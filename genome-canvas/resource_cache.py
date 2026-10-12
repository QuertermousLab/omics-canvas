"""Bounded memory caching for immutable references and frontend assets."""

import gzip
import json
from collections import OrderedDict
from pathlib import Path
from threading import Lock


class FileMemoryCache:
    def __init__(self, capacity=256 * 1024 * 1024, item_limit=32 * 1024 * 1024):
        self.capacity = capacity
        self.item_limit = item_limit
        self.entries = OrderedDict()
        self.size = 0
        self.lock = Lock()

    def get(self, path, stat, compressed=False, replacements=()):
        if stat.st_size > self.item_limit:
            return None
        key = (str(path), stat.st_mtime_ns, stat.st_size, compressed, replacements)
        with self.lock:
            cached = self.entries.get(key)
            if cached is not None:
                self.entries.move_to_end(key)
                return cached
        # Read and compress outside the lock so one large cold file does not
        # stall every other cached asset request. Concurrent misses for the
        # same key may both build the body; the result is identical.
        body = path.read_bytes()
        for original, replacement in replacements:
            body = body.replace(original, replacement)
        if compressed:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        if len(body) > self.capacity:
            return body
        with self.lock:
            existing = self.entries.get(key)
            if existing is not None:
                self.entries.move_to_end(key)
                return existing
            while self.entries and self.size + len(body) > self.capacity:
                _, removed = self.entries.popitem(last=False)
                self.size -= len(removed)
            self.entries[key] = body
            self.size += len(body)
            return body


class ReferenceCache:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.manifest_path = self.directory / "manifest.json"
        self.signature = None
        self.manifest = {}
        self.lock = Lock()

    def snapshot(self):
        with self.lock:
            try:
                stat = self.manifest_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if signature != self.signature:
                    manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(manifest, dict):
                        return {}
                    self.manifest = manifest
                    self.signature = signature
            except (OSError, ValueError):
                return {}
            return self.manifest

    def resolve(self, name):
        manifest = self.snapshot()
        relative = manifest.get("assets", {}).get(name, {}).get("file")
        if not relative:
            raise FileNotFoundError("Reference resource not found")
        path = (self.directory / relative).resolve()
        try:
            path.relative_to(self.directory)
        except ValueError:
            raise FileNotFoundError("Reference resource not found")
        if not path.is_file():
            raise FileNotFoundError("Reference resource not found")
        return path
