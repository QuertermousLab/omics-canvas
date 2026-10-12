import gzip
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.precompute_gwas_ld import original_locus
from tools.precompute_gwas_ld_genomewide import (
    group_loci, match_panel_candidates, prepare_genomewide, range_rows,
    scan_significant, select_pair, variant_key, write_annotation,
)


class GenomeWideLDTests(unittest.TestCase):
    def test_numeric_x_coordinates_and_par_ranges(self):
        self.assertEqual(original_locus({"hg19_locus": "23:100"}), ("X", 100))
        self.assertEqual(range_rows("X", 2699400, 2699700), [("PAR1", 2699400, 2699520), ("X", 2699521, 2699700)])

    def test_merges_loci_without_discarding_independent_leads(self):
        leads = [{"chrom": "1", "original_position": position} for position in (100, 200, 1000)]
        leads += [{"chrom": "2", "original_position": 100}]
        loci = group_loci(leads, 100)
        self.assertEqual(len(loci), 3)
        self.assertEqual(loci[0]["end"], 300)
        self.assertEqual(len(loci[0]["leads"]), 2)
        self.assertNotEqual(leads[1]["locus"], leads[2]["locus"])

    def test_pair_assignment_keeps_self_reference_and_strongest_ld(self):
        def pair(score, r2, reference=False):
            return {"self": reference, "r2": r2, "distance": 10, "lead": {"score": score, "variant": str(score)}}
        weak = pair(20, 0.3)
        strong = pair(10, 0.8)
        self_pair = pair(8, 1, True)
        self.assertIs(select_pair(weak, strong), strong)
        self.assertIs(select_pair(self_pair, pair(200, 1)), self_pair)

    def test_significant_scan_handles_missing_rsid_underflow_and_contigs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.gwas.gz"
            with gzip.open(source, "wt") as handle:
                handle.write("chromosome\tposition\trsid\tpvalue\tneg_log_pvalue\tref\talt\thg19_locus\n")
                handle.write("chr1\t110\t.\t0\t350\tA\tG\t1:100\n")
                handle.write("chr1\t130\trsWeak\t0.1\t1\tA\tG\t1:120\n")
                handle.write("chr1_alt\t110\t.\t0\t350\tA\tG\t1:100\n")
                handle.write("chrX\t210\t.\t1e-9\t9\tC\tT\t23:200\n")
            _, candidates, counts, significant, digest = scan_significant(source, 5e-8)
            self.assertEqual(sum(counts.values()), 4)
            self.assertEqual(sum(significant.values()), 3)
            self.assertEqual(len(candidates["1"]), 1)
            self.assertEqual(candidates["X"][0]["original_position"], 200)
            self.assertEqual(len(digest), 64)

    def test_panel_mapping_matches_alleles_and_excludes_ambiguous_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            bfile = Path(temporary) / "panel"
            Path(str(bfile) + ".bim").write_text(
                "1 rs1 0 100 G A\n1 rs2 0 200 T C\n1 rsDuplicate 0 200 T C\n")
            records = [
                {"original_position": 100, "ref": "T", "alt": "C", "score": 20, "rsid": "."},
                {"original_position": 100, "ref": "A", "alt": "C", "score": 30, "rsid": "."},
                {"original_position": 200, "ref": "C", "alt": "T", "score": 10, "rsid": "."},
            ]
            matched, ambiguous = match_panel_candidates(records, bfile, "1")
            self.assertEqual(list(matched), ["1:100:A:G"])
            self.assertEqual(matched["1:100:A:G"]["label"], "rs1")
            self.assertEqual(ambiguous, 1)

    @unittest.skipUnless(all(shutil.which(name) for name in ("plink2", "bgzip", "tabix", "sort")), "LD tools unavailable")
    def test_end_to_end_preserves_all_rows_and_builds_pair_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = root / "panel1"
            subprocess.run(["plink2", "--dummy", "100", "6", "acgt", "--seed", "1729", "--make-bed",
                            "--memory", "1024", "--threads", "1", "--out", str(panel)], check=True, capture_output=True)
            variants = [line.split() for line in Path(str(panel) + ".bim").read_text().splitlines()]
            for variant in variants:
                variant[3] = str(int(variant[3]) + 100000)
            Path(str(panel) + ".bim").write_text("".join("\t".join(variant) + "\n" for variant in variants))
            rows = ["chromosome\tposition\trsid\tpvalue\tneg_log_pvalue\tref\talt\thg19_locus"]
            for index, variant in enumerate(variants):
                chromosome, identifier, _, position, alt, ref = variant
                score = 20 - index if index in (0, 3) else 1
                rows.append("chr1\t{}\t{}\t{}\t{}\t{}\t{}\t1:{}".format(int(position) + 10, identifier, 10 ** -score,
                                                                                      score, ref, alt, position))
            rows.append("chrX\t5000000\trsUncovered\t1e-10\t10\tA\tG\t23:5000000")
            source, output = root / "study.gwas.gz", root / "study.ld.gwas.gz"
            source.write_bytes(subprocess.run(["bgzip", "-c"], input=("\n".join(rows) + "\n").encode(), capture_output=True, check=True).stdout)
            original = source.read_bytes()
            settings = {"label": "Test ALL", "bfileTemplate": str(root / "panel{chrom}")}
            with patch("server.ld_reference_settings", return_value=settings):
                metadata = prepare_genomewide(source, output, workers=1, threads=1)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(metadata["statistics"]["variants"], 7)
            self.assertEqual(len(metadata["leads"]), 2)
            self.assertEqual(metadata["unavailableChromosomes"][0]["chromosome"], "X")
            with gzip.open(output, "rt") as handle:
                output_rows = handle.read().splitlines()
            self.assertEqual(["\t".join(row.split("\t")[:8]) for row in output_rows[1:]], rows[1:])
            self.assertTrue(Path(str(output) + ".tbi").is_file())
            self.assertTrue(Path(str(output) + ".ld-pairs.hg19.tsv.gz.tbi").is_file())
            self.assertEqual(json.loads(Path(str(output) + ".ld.json").read_text())["scope"], "complete GWAS input")
            with patch("server.ld_reference_settings", return_value=settings), self.assertRaises(FileExistsError):
                prepare_genomewide(source, output)
