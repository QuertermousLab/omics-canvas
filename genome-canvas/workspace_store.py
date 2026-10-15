"""Persistent password-free workspace names for Genome Canvas, and the lab's shared track list."""

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


class WorkspaceError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_workspace_name(value):
    name = " ".join(str(value or "").split())
    if not name:
        raise WorkspaceError("Workspace name is required")
    if len(name) > 48:
        raise WorkspaceError("Workspace name must be 48 characters or fewer")
    if any(ord(character) < 32 or character in "/\\" for character in name):
        raise WorkspaceError("Workspace name contains unsupported characters")
    return name


def manual_workspace_id(name):
    digest = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()[:18]
    return "manual-{}".format(digest)


class WorkspaceStore:
    def __init__(self, path):
        self.path = Path(path)
        self.catalog_lock = Lock()
        self.cached_catalog = None
        self.catalog_expires = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hidden_home_workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    hidden_at TEXT NOT NULL
                );
                -- every server file a workspace has loaded from a shared data root
                CREATE TABLE IF NOT EXISTS shared_tracks (
                    root_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    format TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER,
                    mtime INTEGER,
                    genomes TEXT NOT NULL DEFAULT '[]',
                    workspace_id TEXT,
                    workspace_name TEXT,
                    users TEXT NOT NULL DEFAULT '[]',
                    loads INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    loaded_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (root_id, path)
                );
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def public_workspace(row):
        return {
            "id": row["id"],
            "name": row["name"],
            "kind": "manual",
            "createdAt": row["created_at"],
        }

    def list_manual(self):
        return self.catalog()[0]

    def catalog(self):
        with self.catalog_lock:
            if self.cached_catalog is None or time.monotonic() >= self.catalog_expires:
                with self.connect() as connection:
                    manual = connection.execute(
                        "SELECT id, name, created_at FROM workspaces ORDER BY name COLLATE NOCASE"
                    ).fetchall()
                    hidden = connection.execute(
                        "SELECT id, name, hidden_at FROM hidden_home_workspaces ORDER BY name COLLATE NOCASE"
                    ).fetchall()
                self.cached_catalog = (
                    [self.public_workspace(row) for row in manual],
                    [{"id": row["id"], "name": row["name"], "kind": "home", "hiddenAt": row["hidden_at"]}
                     for row in hidden],
                )
                self.catalog_expires = time.monotonic() + 10
            return tuple([dict(item) for item in entries] for entries in self.cached_catalog)

    def invalidate_catalog(self):
        with self.catalog_lock:
            self.cached_catalog = None

    def get_manual(self, workspace_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, created_at FROM workspaces WHERE id = ?",
                (str(workspace_id or ""),),
            ).fetchone()
        return self.public_workspace(row) if row else None

    def create(self, value):
        name = normalize_workspace_name(value)
        workspace_id = manual_workspace_id(name)
        now = utc_now()
        try:
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO workspaces(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (workspace_id, name, now, now),
                )
        except sqlite3.IntegrityError:
            raise WorkspaceError("A manual workspace with that name already exists")
        self.invalidate_catalog()
        return self.get_manual(workspace_id)

    def delete(self, workspace_id):
        with self.connect() as connection:
            result = connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        if not result.rowcount:
            raise WorkspaceError("Manual workspace was not found")
        self.invalidate_catalog()

    def hidden_homes(self):
        return self.catalog()[1]

    def hide_home(self, workspace_id, name):
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO hidden_home_workspaces(id, name, hidden_at) VALUES (?, ?, ?)",
                (workspace_id, name, utc_now()),
            )
        self.invalidate_catalog()

    def restore_home(self, workspace_id):
        with self.connect() as connection:
            result = connection.execute("DELETE FROM hidden_home_workspaces WHERE id = ?", (workspace_id,))
        if not result.rowcount:
            raise WorkspaceError("Hidden Home workspace was not found")
        self.invalidate_catalog()

    # Shared tracks: one row per server file (data root id + root-relative path) loaded by any workspace.
    # The first workspace is its owner (it alone may take it off the list); `users` names everyone who loaded it.

    @staticmethod
    def public_shared_track(row):
        return {
            "root": row["root_id"],
            "path": row["path"],
            "name": row["name"],
            "format": row["format"],
            "kind": row["kind"],
            "size": row["size"],
            "modified": row["mtime"],
            "genomes": json.loads(row["genomes"] or "[]"),
            "ownerId": row["workspace_id"],
            "ownerName": row["workspace_name"],
            "users": json.loads(row["users"] or "[]"),
            "loads": row["loads"],
            "createdAt": row["created_at"],
            "loadedAt": row["loaded_at"],
            "updatedAt": row["updated_at"],
        }

    def record_shared_track(self, root_id, path, name, file_format, kind, size, mtime, genome, workspace):
        now = utc_now()
        workspace_name = (workspace or {}).get("name") or ""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM shared_tracks WHERE root_id = ? AND path = ?", (root_id, path)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO shared_tracks(root_id, path, name, format, kind, size, mtime, genomes, workspace_id,"
                    " workspace_name, users, loads, created_at, loaded_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (root_id, path, name, file_format, kind, size, mtime, json.dumps([genome] if genome else []),
                     (workspace or {}).get("id"), workspace_name, json.dumps([workspace_name] if workspace_name else []),
                     now, now, now),
                )
                return
            genomes = json.loads(row["genomes"] or "[]")
            if genome and genome not in genomes:
                genomes.append(genome)
            users = json.loads(row["users"] or "[]")
            if workspace_name and workspace_name not in users:
                users.append(workspace_name)
            changed = row["size"] != size or row["mtime"] != mtime
            hidden = row["hidden"]
            if hidden and (workspace or {}).get("id") == row["workspace_id"]:
                hidden = 0  # its owner loaded it again: shared again
            connection.execute(
                "UPDATE shared_tracks SET name = ?, format = ?, kind = ?, size = ?, mtime = ?, genomes = ?, users = ?,"
                " loads = loads + 1, hidden = ?, loaded_at = ?, updated_at = ? WHERE root_id = ? AND path = ?",
                (name, file_format, kind, size, mtime, json.dumps(genomes), json.dumps(users), hidden, now,
                 now if changed else row["updated_at"], root_id, path),
            )

    def seed_shared_track(self, root_id, path, name, file_format, kind, size, mtime, genome, workspace, created_at):
        """A file from a view saved before the list existed (share link, Favorite); kept as is when already listed."""
        workspace_name = (workspace or {}).get("name") or ""
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO shared_tracks(root_id, path, name, format, kind, size, mtime, genomes, workspace_id,"
                " workspace_name, users, loads, created_at, loaded_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (root_id, path, name, file_format, kind, size, mtime, json.dumps([genome] if genome else []),
                 (workspace or {}).get("id"), workspace_name, json.dumps([workspace_name] if workspace_name else []),
                 created_at, created_at, created_at),
            )

    def list_shared_tracks(self, include_hidden=False):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM shared_tracks{} ORDER BY loaded_at DESC".format("" if include_hidden else " WHERE hidden = 0")
            ).fetchall()
        return [self.public_shared_track(row) for row in rows]

    def update_shared_track_file(self, root_id, path, size, mtime):
        """The file was rewritten since it was recorded (its author updated it)."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE shared_tracks SET size = ?, mtime = ?, updated_at = ? WHERE root_id = ? AND path = ?",
                (size, mtime, utc_now(), root_id, path),
            )

    def delete_shared_tracks(self, keys):
        """Rows whose files are gone: [(root_id, path), ...]."""
        if not keys:
            return
        with self.connect() as connection:
            connection.executemany("DELETE FROM shared_tracks WHERE root_id = ? AND path = ?", list(keys))

    def hide_shared_track(self, root_id, path, workspace_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT workspace_id, workspace_name FROM shared_tracks WHERE root_id = ? AND path = ? AND hidden = 0",
                (root_id, path),
            ).fetchone()
            if row is None:
                raise WorkspaceError("This track is no longer on the shared list")
            if row["workspace_id"] != workspace_id:
                raise PermissionError("Only {} can take this track off the shared list".format(row["workspace_name"] or "its owner"))
            connection.execute("UPDATE shared_tracks SET hidden = 1 WHERE root_id = ? AND path = ?", (root_id, path))
