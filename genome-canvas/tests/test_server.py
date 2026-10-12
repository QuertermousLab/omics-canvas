import http.client
import hashlib
import gzip
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import server
from resource_cache import ReferenceCache
from workspace_store import WorkspaceStore


class GenomeCanvasServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.external_temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.external_root = Path(self.external_temporary.name)
        self.home_root = self.root / "homes"
        self.home_root.mkdir()
        self.alice_home = self.home_root / "alice"
        self.bob_home = self.home_root / "bob"
        self.alice_home.mkdir()
        self.bob_home.mkdir()
        self.home_sample = self.alice_home / "private.bed"
        self.home_sample.write_bytes(b"chr2\t30\t40\tprivate\n")
        self.nfs_root = self.root / "nfs"
        self.nfs_root.mkdir()
        self.sample = self.root / "sample.bed"
        self.sample.write_bytes(b"chr1\t10\t20\tfeature\n")
        self.indexed = self.root / "variants.vcf.gz"
        self.indexed.write_bytes(b"compressed-placeholder")
        Path(str(self.indexed) + ".tbi").write_bytes(b"index")
        self.hic = self.root / "contacts.hic"
        self.hic.write_bytes(b"HIC-placeholder")
        self.gwas = self.root / "study.hg38.gwas.gz"
        self.gwas.write_bytes(b"bgzf-placeholder")
        Path(str(self.gwas) + ".tbi").write_bytes(b"index")

        self.linked_directory = self.external_root / "linked-directory"
        self.linked_directory.mkdir()
        (self.linked_directory / "signal.bigWig").write_bytes(b"bigwig-placeholder")
        (self.root / "linked-data").symlink_to(self.linked_directory, target_is_directory=True)

        self.linked_bam_target = self.external_root / "reads.bam"
        self.linked_bam_target.write_bytes(b"bam-placeholder")
        Path(str(self.linked_bam_target) + ".bai").write_bytes(b"bam-index")
        (self.root / "linked-reads.bam").symlink_to(self.linked_bam_target)

        self.previous_roots = server.DATA_ROOTS
        self.previous_sessions = server.SESSION_DIR
        self.previous_profiles = server.PROFILE_DIR
        server.DATA_ROOTS = {
            "nfs": {"path": self.nfs_root, "label": "Shared NFS"},
            "test": {"path": self.root, "label": "Test data"},
            "external": {"path": self.external_root, "label": "External data"},
        }
        server.SESSION_DIR = self.root / "sessions"
        server.PROFILE_DIR = self.root / "profiles"

        self.workspace_store = WorkspaceStore(self.root / "workspaces.sqlite3")
        self.httpd = server.GenomeCanvasHTTPServer(
            ("127.0.0.1", 0), server.GenomeCanvasHandler, self.workspace_store, self.home_root
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.workspace_cookie = ""
        self.alice_workspace_id = server.home_workspace_id(self.alice_home)
        self.select_workspace(self.alice_workspace_id)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.DATA_ROOTS = self.previous_roots
        server.SESSION_DIR = self.previous_sessions
        server.PROFILE_DIR = self.previous_profiles
        self.temporary.cleanup()
        self.external_temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        headers = dict(headers or {})
        if self.workspace_cookie and "Cookie" not in headers:
            headers["Cookie"] = self.workspace_cookie
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def select_workspace(self, workspace_id):
        body = json.dumps({"id": workspace_id}).encode("utf-8")
        status, headers, payload = self.request(
            "POST",
            "/api/workspaces/select",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body)), "Cookie": ""},
        )
        self.assertEqual(status, 200, payload)
        self.workspace_cookie = headers["Set-Cookie"].split(";", 1)[0]
        return json.loads(payload)["workspace"]

    def test_file_listing_and_index_pairing(self):
        status, _, body = self.request("GET", "/api/files?root=test&path=")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        variants = next(item for item in payload["entries"] if item["name"] == "variants.vcf.gz")
        self.assertTrue(variants["indexed"])
        self.assertEqual(variants["indexName"], "variants.vcf.gz.tbi")

        status, _, body = self.request("GET", "/api/track?root=test&path=variants.vcf.gz")
        self.assertEqual(status, 200)
        track = json.loads(body)["track"]
        self.assertEqual(track["format"], "vcf")
        self.assertTrue(track["indexURL"].endswith("variants.vcf.gz.tbi"))

    def test_data_endpoint_supports_byte_ranges(self):
        status, headers, body = self.request("GET", "/data/test/sample.bed", headers={"Range": "bytes=5-9"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 5-9/19")
        self.assertEqual(body, b"10\t20")

    def test_whole_text_track_is_gzip_encoded_but_ranges_are_not(self):
        large = self.root / "contacts.bedpe"
        content = b"".join(b"chr1\t%d\t%d\tchr1\t%d\t%d\t0.5\n" % (i, i + 10, i + 500, i + 510) for i in range(400))
        large.write_bytes(content)
        status, headers, body = self.request("GET", "/data/test/contacts.bedpe", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        self.assertEqual(gzip.decompress(body), content)
        self.assertLess(len(body), len(content))
        status, _, body = self.request("GET", "/data/test/contacts.bedpe", headers={
            "Accept-Encoding": "gzip", "If-None-Match": headers["ETag"],
        })
        self.assertEqual(status, 304)
        status, headers, body = self.request("GET", "/data/test/contacts.bedpe", headers={
            "Accept-Encoding": "gzip", "Range": "bytes=0-9",
        })
        self.assertEqual(status, 206)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, content[:10])
        status, headers, body = self.request("GET", "/data/test/contacts.bedpe")
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, content)
        # Already-compressed binary formats are never re-encoded.
        status, headers, _ = self.request("GET", "/data/test/variants.vcf.gz", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)

    def test_bundled_fonts_are_served_immutable_and_confined(self):
        status, headers, body = self.request("GET", "/vendor/fonts/manrope-latin-wght-normal.woff2", headers={"Cookie": ""})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "font/woff2")
        self.assertIn("immutable", headers["Cache-Control"])
        self.assertEqual(body[:4], b"wOF2")
        for path in ("/vendor/fonts/missing.woff2", "/vendor/fonts/..%2F..%2Fserver.py", "/vendor/fonts/manrope.LICENSE"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_large_json_responses_are_gzip_encoded(self):
        for index in range(60):
            (self.root / "track-{:03d}.bed".format(index)).write_bytes(b"chr1\t1\t2\n")
        status, headers, body = self.request("GET", "/api/files?root=test&path=", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        names = [item["name"] for item in json.loads(gzip.decompress(body))["entries"]]
        self.assertIn("track-059.bed", names)
        status, headers, body = self.request("GET", "/api/health", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", headers)
        self.assertTrue(json.loads(body)["ok"])

    def test_static_compression_and_conditional_requests(self):
        status, headers, body = self.request("GET", "/app.js?v=test", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(gzip.decompress(body), (server.APP_DIR / "app.js").read_bytes())
        self.assertIn("immutable", headers["Cache-Control"])
        status, _, body = self.request("GET", "/app.js?v=test", headers={
            "Accept-Encoding": "gzip", "If-None-Match": headers["ETag"],
        })
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        status, headers, body = self.request("GET", "/app.js", headers={"Accept-Encoding": "gzip;q=0"})
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_cached_reference_range_does_not_require_workspace(self):
        reference_root = self.root / "references"
        reference_root.mkdir()
        (reference_root / "sequence.2bit").write_bytes(b"0123456789")
        (reference_root / "manifest.json").write_text(json.dumps({
            "assets": {"sequence.2bit": {"file": "sequence.2bit"}},
        }))
        previous_cache = server.REFERENCE_CACHE
        try:
            server.REFERENCE_CACHE = ReferenceCache(reference_root)
            status, headers, body = self.request("GET", "/reference/sequence.2bit", headers={
                "Range": "bytes=3-6", "Cookie": "",
            })
            self.assertEqual(status, 206)
            self.assertEqual(headers["Content-Range"], "bytes 3-6/10")
            self.assertEqual(body, b"3456")
            status, _, _ = self.request("GET", "/reference/../workspaces.sqlite3", headers={"Cookie": ""})
            self.assertEqual(status, 404)
        finally:
            server.REFERENCE_CACHE = previous_cache

    def test_workspace_scoped_data_range_does_not_require_cookie(self):
        path = "/data/{}/test/sample.bed".format(self.alice_workspace_id)
        status, headers, body = self.request(
            "GET",
            path,
            headers={"Range": "bytes=5-9", "Cookie": ""},
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 5-9/19")
        self.assertEqual(body, b"10\t20")

    def test_deleted_workspace_scoped_shared_data_still_supports_ranges(self):
        path = "/data/manual-000000000000000000/test/sample.bed"
        status, headers, body = self.request(
            "GET",
            path,
            headers={"Range": "bytes=5-9", "Cookie": ""},
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 5-9/19")
        self.assertEqual(body, b"10\t20")

    def test_legacy_shared_data_range_does_not_require_cookie(self):
        status, _, body = self.request(
            "GET",
            "/data/test/sample.bed",
            headers={"Range": "bytes=0-2", "Cookie": ""},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"chr")

    def test_legacy_home_data_range_without_cookie_requires_workspace(self):
        home_root_id = hashlib.sha256(str(self.alice_home).encode("utf-8")).hexdigest()[:12]
        status, _, body = self.request(
            "GET",
            "/data/{}/private.bed".format(home_root_id),
            headers={"Range": "bytes=0-2", "Cookie": ""},
        )
        self.assertEqual(status, 409)
        self.assertTrue(json.loads(body)["workspaceRequired"])

    def test_hic_file_is_listed_and_configured_as_an_interaction_track(self):
        status, _, body = self.request("GET", "/api/files?root=test&path=")
        self.assertEqual(status, 200)
        hic = next(item for item in json.loads(body)["entries"] if item["name"] == "contacts.hic")
        self.assertEqual(hic["format"], "hic")
        self.assertEqual(hic["trackType"], "interact")

        status, _, body = self.request("GET", "/api/track?root=test&path=contacts.hic")
        self.assertEqual(status, 200)
        track = json.loads(body)["track"]
        self.assertEqual(track["format"], "hic")
        self.assertEqual(track["type"], "interact")

    def test_indexed_gwas_is_configured_as_regional_manhattan_track(self):
        status, _, body = self.request("GET", "/api/files?root=test&path=")
        self.assertEqual(status, 200)
        gwas = next(item for item in json.loads(body)["entries"] if item["name"] == "study.hg38.gwas.gz")
        self.assertEqual(gwas["format"], "gwas")
        self.assertTrue(gwas["indexed"])

        # LD wiring depends on the deployment's private ldReferenceHg19 setting.
        ld_settings = {"ldReferenceHg19": {"label": "Test panel", "maxWindow": 1000000}}
        with mock.patch.dict(server.SETTINGS, ld_settings):
            status, _, body = self.request("GET", "/api/track?root=test&path=study.hg38.gwas.gz")
        self.assertEqual(status, 200)
        track = json.loads(body)["track"]
        self.assertEqual(track["ldLabel"], "Test panel")
        self.assertEqual(track["type"], "gwas")
        self.assertEqual(track["format"], "gwas")
        self.assertEqual(track["height"], 220)
        self.assertEqual(track["visibilityWindow"], 50000000)
        self.assertTrue(track["genomeCanvasManhattan"])
        self.assertEqual(track["significancePValue"], 5e-8)
        self.assertEqual(track["ldEndpoint"], "api/gwas-ld")
        self.assertTrue(track["indexURL"].endswith("study.hg38.gwas.gz.tbi"))

    def test_plink_ld_output_is_parsed_and_clamped(self):
        text = (
            "#CHROM_A\tPOS_A\tID_A\tREF_A\tALT1_A\tCHROM_B\tPOS_B\tID_B\tREF_B\tALT1_B\tUNPHASED_R2\n"
            "6\t100\trsLead\tA\tG\t6\t100\trsLead\tA\tG\t1\n"
            "6\t100\trsLead\tA\tG\t6\t125\trsOther\tC\tT\t0.625\n"
            "6\t100\trsLead\tA\tG\t6\t250\trsOutside\tC\tG\t1.2\n"
        )
        records = server.parse_plink_ld_output(text, 90, 200)
        self.assertEqual(records, [
            {"position": 100, "id": "rsLead", "ref": "A", "alt": "G", "r2": 1.0},
            {"position": 125, "id": "rsOther", "ref": "C", "alt": "T", "r2": 0.625},
        ])

    def test_ucsc_trackdb_is_normalized_without_browser_side_parsing(self):
        text = """
track parent
shortLabel Signal group
compositeTrack on

track sampleSignal
parent parent on
shortLabel Sample signal
longLabel Sample bigWig signal
type bigWig
bigDataUrl files/sample.bigWig
subGroups biosample=Heart assay=RNA_seq
"""
        records = server.parse_ucsc_records(text, "track")
        for record in records:
            record["_base_url"] = "https://example.org/hg38/trackDb.txt"
        payload = server.normalize_ucsc_trackdb(records, "https://example.org/hub.txt")
        self.assertEqual(len(payload["tracks"]), 1)
        track = payload["tracks"][0]
        self.assertEqual(track["url"], "https://example.org/hg38/files/sample.bigWig")
        self.assertEqual(track["format"], "bigwig")
        self.assertEqual(track["hubGroup"], "Signal group")
        self.assertEqual(track["sample"], "Heart")
        self.assertEqual(track["assay"], "RNA seq")

    def test_public_hub_proxy_rejects_private_network_targets(self):
        with self.assertRaises(ValueError):
            server.validate_public_url("http://127.0.0.1:8000/private-hub.json")

    def test_share_session_round_trip(self):
        session = {"genome": "hg38", "locus": "chr1:1-100", "tracks": []}
        body = json.dumps(session).encode("utf-8")
        status, _, created = self.request("POST", "/api/sessions", body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        self.assertEqual(status, 201)
        session_id = json.loads(created)["id"]

        status, _, restored = self.request("GET", "/api/sessions/{}".format(session_id))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(restored), session)

    def test_favorite_profile_save_list_load_and_delete(self):
        profile = {
            "name": "Smooth signal view",
            "state": {
                "reference": {"id": "hg38"},
                "locus": "chr1:10-100",
                "tracks": [{"name": "Signal", "type": "wig", "height": 120, "color": "#778899"}],
                "genomeCanvasHighlights": [{"chr": "chr1", "start": 20, "end": 30, "color": "#f2c94c"}],
            },
        }
        body = json.dumps(profile).encode("utf-8")
        status, _, created = self.request("POST", "/api/profiles", body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        self.assertEqual(status, 201)
        profile_id = json.loads(created)["profile"]["id"]

        status, _, listing = self.request("GET", "/api/profiles")
        self.assertEqual(status, 200)
        summary = json.loads(listing)["profiles"][0]
        self.assertEqual(summary["name"], "Smooth signal view")
        self.assertEqual(summary["trackCount"], 1)
        self.assertEqual(summary["highlightCount"], 1)

        status, _, loaded = self.request("GET", "/api/profiles/{}".format(profile_id))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(loaded)["state"]["tracks"][0]["height"], 120)

        status, _, _ = self.request("DELETE", "/api/profiles/{}".format(profile_id))
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/profiles/{}".format(profile_id))
        self.assertEqual(status, 404)

    def test_favorite_profiles_are_isolated_by_workspace(self):
        def save(workspace_id, locus):
            self.select_workspace(workspace_id)
            body = json.dumps({
                "name": "My view",
                "state": {"genome": "hg38", "locus": locus, "tracks": []},
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }
            return self.request("POST", "/api/profiles", body=body, headers=headers)

        alice_id = server.home_workspace_id(self.alice_home)
        bob_id = server.home_workspace_id(self.bob_home)
        self.assertEqual(save(alice_id, "chr1:1-10")[0], 201)
        self.assertEqual(save(bob_id, "chr2:1-10")[0], 201)

        self.select_workspace(alice_id)
        status, _, alice_listing = self.request("GET", "/api/profiles")
        self.assertEqual(status, 200)
        alice_payload = json.loads(alice_listing)
        self.assertEqual(alice_payload["user"], "alice")
        self.assertEqual([item["locus"] for item in alice_payload["profiles"]], ["chr1:1-10"])

        self.select_workspace(bob_id)
        status, _, bob_listing = self.request("GET", "/api/profiles")
        self.assertEqual(status, 200)
        bob_payload = json.loads(bob_listing)
        self.assertEqual(bob_payload["user"], "bob")
        self.assertEqual([item["locus"] for item in bob_payload["profiles"]], ["chr2:1-10"])

        profile_id = bob_payload["profiles"][0]["id"]
        self.select_workspace(alice_id)
        status, _, alice_profile = self.request("GET", "/api/profiles/{}".format(profile_id))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(alice_profile)["state"]["locus"], "chr1:1-10")

    def test_home_workspace_uses_its_home_as_default(self):
        status, _, config = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        payload = json.loads(config)
        self.assertEqual(payload["user"], "alice")
        roots = {item["id"]: item["label"] for item in payload["roots"]}
        self.assertEqual(roots[payload["defaultFileRoot"]], "alice Home")
        self.assertEqual(payload["defaultFilePath"], "")
        self.assertIn("Shared NFS", roots.values())
        self.assertIn("Test data", roots.values())
        self.assertIn("External data", roots.values())

    def test_manual_workspace_defaults_to_shared_nfs(self):
        body = json.dumps({"name": "Shared project"}).encode("utf-8")
        status, headers, created = self.request(
            "POST", "/api/workspaces", body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        self.assertEqual(status, 201)
        self.workspace_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, config = self.request("GET", "/api/config")
        payload = json.loads(config)
        roots = {item["id"]: item["label"] for item in payload["roots"]}
        self.assertEqual(payload["user"], "Shared project")
        self.assertEqual(roots[payload["defaultFileRoot"]], "Shared NFS")

    def test_config_requires_a_workspace_cookie(self):
        status, _, body = self.request("GET", "/api/config", headers={"Cookie": ""})
        self.assertEqual(status, 409)
        self.assertTrue(json.loads(body)["workspaceRequired"])

    def test_workspace_switch_takes_effect_on_same_keep_alive_connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request("GET", "/api/config", headers={"Cookie": self.workspace_cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["workspaceId"], self.alice_workspace_id)

        bob_id = server.home_workspace_id(self.bob_home)
        body = json.dumps({"id": bob_id}).encode("utf-8")
        connection.request(
            "POST",
            "/api/workspaces/select",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Cookie": self.workspace_cookie,
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        bob_cookie = response.getheader("Set-Cookie").split(";", 1)[0]

        connection.request("GET", "/api/config", headers={"Cookie": bob_cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["workspaceId"], bob_id)
        connection.close()

    def test_rejected_post_body_does_not_corrupt_keep_alive_connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        body = json.dumps({"name": "Not saved", "state": {"tracks": []}}).encode("utf-8")
        connection.request(
            "POST",
            "/api/profiles",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Cookie": "",
            },
        )
        response = connection.getresponse()
        self.assertEqual(response.status, 409)
        self.assertTrue(json.loads(response.read())["workspaceRequired"])

        connection.request("GET", "/api/workspaces")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("workspaces", json.loads(response.read()))
        connection.close()

    def test_refsnp_json_resolves_hg38_and_hg19_coordinates(self):
        record = {
            "primary_snapshot_data": {
                "placements_with_allele": [
                    {
                        "seq_id": "NC_000008.11",
                        "placement_annot": {
                            "seq_type": "refseq_chromosome",
                            "seq_id_traits_by_assembly": [{"assembly_name": "GRCh38.p14"}],
                        },
                        "alleles": [{"allele": {"spdi": {"position": 19962212, "deleted_sequence": "C"}}}],
                    },
                    {
                        "seq_id": "NC_000008.10",
                        "placement_annot": {
                            "seq_type": "refseq_chromosome",
                            "seq_id_traits_by_assembly": [{"assembly_name": "GRCh37.p13"}],
                        },
                        "alleles": [{"allele": {"spdi": {"position": 19819723, "deleted_sequence": "C"}}}],
                    },
                ]
            }
        }
        self.assertEqual(server.refsnp_location(record, "hg38")["position"], 19962213)
        self.assertEqual(server.refsnp_location(record, "hg19")["position"], 19819724)

    def test_home_workspace_can_be_hidden_and_restored_without_deleting_home(self):
        bob_id = server.home_workspace_id(self.bob_home)
        status, _, _ = self.request("DELETE", "/api/workspaces/{}".format(bob_id))
        self.assertEqual(status, 200)
        self.assertTrue(self.bob_home.is_dir())
        status, _, listing = self.request("GET", "/api/workspaces")
        payload = json.loads(listing)
        self.assertNotIn(bob_id, [item["id"] for item in payload["workspaces"]])
        self.assertIn(bob_id, [item["id"] for item in payload["hiddenHomeWorkspaces"]])

        body = json.dumps({"id": bob_id}).encode("utf-8")
        status, _, _ = self.request(
            "POST", "/api/workspaces/restore", body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        self.assertEqual(status, 200)
        status, _, listing = self.request("GET", "/api/workspaces")
        self.assertIn(bob_id, [item["id"] for item in json.loads(listing)["workspaces"]])

    def test_public_hub_tracks_are_filtered_and_paginated(self):
        result = {
            "kind": "ucsc",
            "tracks": [
                {"name": "Heart RNA", "assay": "RNA-seq"},
                {"name": "Liver ATAC", "assay": "ATAC-seq"},
                {"name": "Heart ATAC", "assay": "ATAC-seq"},
            ],
            "skipped": 4,
        }
        page = server.paginate_public_hub(result, "heart", 0, 20)
        self.assertEqual(page["total"], 2)
        self.assertEqual([track["name"] for track in page["tracks"]], ["Heart RNA", "Heart ATAC"])
        self.assertFalse(page["hasMore"])

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(PermissionError):
            server.resolve_data_path("test", "../outside.bam")

    def test_filesystem_root_contains_absolute_paths(self):
        self.assertTrue(server.within_root(Path("/home/quanyiz"), Path("/")))

    def test_display_path_with_leading_slash_is_root_relative(self):
        self.assertEqual(server.resolve_data_path("test", "/sample.bed"), self.sample)

    def test_igv_shadow_host_is_not_hidden_as_empty(self):
        stylesheet = (server.APP_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("#igv-container:empty", stylesheet)

    def test_hidden_attribute_always_removes_dialog_states_from_layout(self):
        stylesheet = (server.APP_DIR / "styles.css").read_text(encoding="utf-8")
        self.assertIn("[hidden] { display: none !important; }", stylesheet)

    def test_symbolic_link_directory_can_be_browsed(self):
        status, _, body = self.request("GET", "/api/files?root=test&path=")
        self.assertEqual(status, 200)
        linked = next(item for item in json.loads(body)["entries"] if item["name"] == "linked-data")
        self.assertEqual(linked["kind"], "directory")
        self.assertTrue(linked["symlink"])

        status, _, body = self.request("GET", "/api/files?root=test&path=linked-data")
        self.assertEqual(status, 200)
        self.assertIn("signal.bigWig", [item["name"] for item in json.loads(body)["entries"]])

    def test_symbolic_link_track_uses_target_index(self):
        status, _, body = self.request("GET", "/api/track?root=test&path=linked-reads.bam")
        self.assertEqual(status, 200)
        track = json.loads(body)["track"]
        self.assertEqual(track["format"], "bam")
        self.assertEqual(track["url"], "/data/{}/test/linked-reads.bam".format(self.alice_workspace_id))
        self.assertEqual(track["indexURL"], "/data/{}/external/reads.bam.bai".format(self.alice_workspace_id))

    def test_external_symlink_and_target_index_work_with_only_one_visible_root(self):
        server.DATA_ROOTS = {"test": {"path": self.root, "label": "Test data"}}

        status, _, body = self.request("GET", "/api/files?root=test&path=linked-data")
        self.assertEqual(status, 200)
        self.assertIn("signal.bigWig", [item["name"] for item in json.loads(body)["entries"]])

        status, _, body = self.request("GET", "/api/track?root=test&path=linked-reads.bam")
        self.assertEqual(status, 200)
        index_url = json.loads(body)["track"]["indexURL"]
        self.assertEqual(
            index_url,
            "/data/{}/test/linked-reads.bam?companion=reads.bam.bai".format(self.alice_workspace_id),
        )

        status, _, body = self.request("GET", index_url, headers={"Range": "bytes=0-2"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"bam")


class LocalModeServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "local.bed").write_bytes(b"chr1\t5\t15\tlocal\n")
        self.previous_local_mode = server.LOCAL_MODE
        self.previous_roots = server.DATA_ROOTS
        self.previous_sessions = server.SESSION_DIR
        self.previous_profiles = server.PROFILE_DIR
        server.LOCAL_MODE = True
        server.DATA_ROOTS = {"local-root": {"path": self.root, "label": "This Mac"}}
        server.SESSION_DIR = self.root / "sessions"
        server.PROFILE_DIR = self.root / "profiles"
        self.workspace_store = WorkspaceStore(self.root / "workspaces.sqlite3")
        self.httpd = server.GenomeCanvasHTTPServer(
            ("127.0.0.1", 0), server.GenomeCanvasHandler, self.workspace_store, self.root / "missing-homes"
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.LOCAL_MODE = self.previous_local_mode
        server.DATA_ROOTS = self.previous_roots
        server.SESSION_DIR = self.previous_sessions
        server.PROFILE_DIR = self.previous_profiles
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_local_mode_auto_selects_one_workspace_and_serves_local_files(self):
        status, _, body = self.request("GET", "/api/workspaces")
        self.assertEqual(status, 200)
        workspaces = json.loads(body)
        self.assertTrue(workspaces["localMode"])
        self.assertEqual(workspaces["selected"], {"id": "local", "name": "This Mac", "kind": "local"})
        self.assertEqual(len(workspaces["workspaces"]), 1)

        status, _, body = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        config = json.loads(body)
        self.assertTrue(config["localMode"])
        self.assertEqual(config["workspaceKind"], "local")
        self.assertEqual(config["roots"], [{"id": "local-root", "label": "This Mac"}])

        status, _, body = self.request("GET", "/api/track?root=local-root&path=local.bed")
        self.assertEqual(status, 200)
        track = json.loads(body)["track"]
        self.assertEqual(track["url"], "/data/local/local-root/local.bed")
        status, _, body = self.request("GET", track["url"])
        self.assertEqual(status, 200)
        self.assertEqual(body, b"chr1\t5\t15\tlocal\n")

    def test_local_mode_rejects_workspace_creation(self):
        body = json.dumps({"name": "unused"}).encode("utf-8")
        status, _, payload = self.request(
            "POST",
            "/api/workspaces",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        self.assertEqual(status, 403, payload)


if __name__ == "__main__":
    unittest.main()
