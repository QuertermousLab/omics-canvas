import gzip
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.precompute_gwas_ld import annotate_records, choose_lead, parse_region, prepare


class PrecomputedLDTests(unittest.TestCase):
    def payload(self):
        return {
            "available": True, "chromosome": "9", "label": "Test EUR", "build": "GRCh37",
            "reference": {"position": 100, "id": "rsLead", "ref": "A", "alt": "G"},
            "variants": [
                {"position": 100, "id": "rsLead", "ref": "A", "alt": "G", "r2": 1},
                {"position": 125, "id": "rsOther", "ref": "C", "alt": "T", "r2": 0.625},
            ],
        }

    def test_regions_and_fixed_lead_selection(self):
        self.assertEqual(parse_region("9:1,000-2,000"), ("chr9", 1000, 2000))
        with self.assertRaises(ValueError):
            parse_region("chr9:2000-1000")
        records = [
            {"rsid": "rsWeak", "pvalue": "1e-8", "hg19_locus": "9:125", "ref": "C", "alt": "T"},
            {"rsid": "rsLead", "pvalue": "0", "neg_log_pvalue": "400", "hg19_locus": "9:100", "ref": "A", "alt": "G"},
        ]
        self.assertEqual(choose_lead(records)["rsid"], "rsLead")
        self.assertEqual(choose_lead(records, "rsWeak")["rsid"], "rsWeak")
        with self.assertRaises(ValueError):
            choose_lead(records, "rsAbsent")

    def test_allele_matching_missing_values_and_provenance(self):
        records = [
            {"hg19_locus": "9:100", "ref": "A", "alt": "G"},
            {"hg19_locus": "9:125", "ref": "T", "alt": "C"},
            {"hg19_locus": "9:125", "ref": "A", "alt": "C"},
            {"hg19_locus": "9:125", "ref": "C", "alt": "C"},
            {"hg19_locus": "8:100", "ref": "A", "alt": "G"},
            {"hg19_locus": ".", "ref": "A", "alt": "G"},
        ]
        self.assertEqual(annotate_records(records, self.payload()), 2)
        self.assertEqual([record["ld_r2"] for record in records], ["1", "0.625", ".", ".", ".", "."])
        self.assertEqual([record["ld_is_reference"] for record in records], ["1", "0", "0", "0", "0", "0"])
        self.assertTrue(all(record["ld_population"] == "Test EUR" for record in records))
        self.assertTrue(all(record["ld_reference_locus"] == "9:100" for record in records))

    @unittest.skipUnless(shutil.which("bgzip") and shutil.which("tabix"), "bgzip/tabix are not installed")
    def test_indexed_output_preserves_source_and_existing_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "input.gwas.gz", root / "output.gwas.gz"
            original = (
                "chromosome\tposition\trsid\tpvalue\tref\talt\thg19_locus\n"
                "chr9\t101\trsLead\t1e-20\tA\tG\t9:100\n"
                "chr9\t126\trsOther\t1e-10\tC\tT\t9:125\n"
            )
            source.write_bytes(subprocess.run(["bgzip", "-c"], input=original.encode(), capture_output=True, check=True).stdout)
            subprocess.run(["tabix", "-S", "1", "-s", "1", "-b", "2", "-e", "2", str(source)], check=True)
            original_bytes = source.read_bytes()
            with patch("server.calculate_local_ld", return_value=self.payload()) as calculate:
                metadata = prepare(source, output, ["chr9:1-200"])
            calculate.assert_called_once_with("9", 100, 125, 100, "A", "G")
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertEqual(metadata["scope"], "selected regions only")
            with gzip.open(output, "rt") as handle:
                lines = handle.read().splitlines()
            self.assertTrue(lines[0].endswith("ld_is_reference"))
            self.assertIn("\t0.625\trsLead\t9:100\tTest EUR\t", lines[2])
            result = subprocess.run(["tabix", str(output), "chr9:126-126"], capture_output=True, text=True, check=True)
            self.assertIn("rsOther", result.stdout)
            self.assertTrue(json.loads(Path(str(output) + ".ld.json").read_text())["fixedReference"])
            with self.assertRaises(FileExistsError):
                prepare(source, output, ["chr9:1-200"])
            with self.assertRaises(ValueError):
                prepare(source, source, ["chr9:1-200"])
            with self.assertRaises(ValueError):
                prepare(source, root / "overlap.gwas.gz", ["chr9:1-100", "chr9:100-200"])
