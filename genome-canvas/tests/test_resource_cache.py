import gzip
import json
import tempfile
import unittest
from pathlib import Path

from resource_cache import FileMemoryCache, ReferenceCache
from workspace_store import WorkspaceStore


class ResourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_memory_cache_evicts_and_refreshes_changed_files(self):
        cache = FileMemoryCache(capacity=8, item_limit=8)
        first = self.directory / "first"
        second = self.directory / "second"
        first.write_bytes(b"first")
        second.write_bytes(b"other")
        self.assertEqual(cache.get(first, first.stat()), b"first")
        self.assertEqual(cache.get(second, second.stat()), b"other")
        self.assertLessEqual(cache.size, 8)
        self.assertEqual(len(cache.entries), 1)
        first.write_bytes(b"new")
        self.assertEqual(cache.get(first, first.stat()), b"new")
        first.write_bytes(b"too-large-for-cache")
        self.assertIsNone(cache.get(first, first.stat()))

    def test_compressed_and_rewritten_variants_are_separate(self):
        cache = FileMemoryCache()
        path = self.directory / "asset.js"
        path.write_bytes(b"remote resource")
        self.assertEqual(cache.get(path, path.stat()), b"remote resource")
        compressed = cache.get(path, path.stat(), True, ((b"remote", b"local"),))
        self.assertEqual(gzip.decompress(compressed), b"local resource")
        self.assertEqual(cache.get(path, path.stat()), b"remote resource")

    def test_reference_manifest_reloads_and_restricts_paths(self):
        manifest = self.directory / "manifest.json"
        path = self.directory / "reference.2bit"
        path.write_bytes(b"reference")
        manifest.write_text(json.dumps({"assets": {"reference.2bit": {"file": path.name}}}))
        cache = ReferenceCache(self.directory)
        self.assertEqual(cache.resolve(path.name), path)
        with self.assertRaises(FileNotFoundError):
            cache.resolve("../private")
        manifest.write_text(json.dumps({"assets": {"escape": {"file": "../private"}}, "version": 2}))
        self.assertEqual(cache.snapshot()["version"], 2)
        with self.assertRaises(FileNotFoundError):
            cache.resolve("escape")
        manifest.write_text("incomplete-json")
        self.assertEqual(cache.snapshot(), {})

    def test_workspace_catalog_cache_invalidates_after_changes(self):
        store = WorkspaceStore(self.directory / "workspaces.sqlite3")
        self.assertEqual(store.list_manual(), [])
        created = store.create("Project")
        self.assertEqual(store.list_manual()[0]["id"], created["id"])
        store.hide_home("home-test", "Test")
        self.assertEqual(store.hidden_homes()[0]["id"], "home-test")
        store.restore_home("home-test")
        self.assertEqual(store.hidden_homes(), [])
        store.delete(created["id"])
        self.assertEqual(store.list_manual(), [])
