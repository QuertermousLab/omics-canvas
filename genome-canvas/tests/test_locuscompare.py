"""LocusCompare (locuscompare.py) on small tabix files written here: matching, the lead SNP, genes, study LD."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import locuscompare  # noqa: E402

HAVE_TABIX = bool(shutil.which("tabix") and shutil.which("bgzip"))


def write_tabix(path, header, rows, seq, pos):
    rows = sorted(rows, key=lambda r: r[pos - 1])  # tabix needs position order
    text = "\t".join(header) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in rows)
    plain = path.with_suffix("")
    plain.write_text(text)
    subprocess.run(["bgzip", "-f", str(plain)], check=True)
    subprocess.run(["tabix", "-f", "-S", "1", "-s", str(seq), "-b", str(pos), "-e", str(pos), str(path)], check=True)


@unittest.skipUnless(HAVE_TABIX, "needs tabix and bgzip")
class LocusCompareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # GWAS: no rsid for one variant, its own LD to a signal, two ancestry groups
        self.gwas = root / "study.hg38.gwas.gz"
        header = ["chromosome", "position", "rsid", "pvalue", "ref", "alt", "beta", "GROUP", "SIGNAL", "R2"]
        rows = [
            ("chr6", 1000, "rs1", 1e-10, "A", "G", 0.1, "EUR", "6:1000:A:G", 1),
            ("chr6", 2000, ".", 1e-6, "C", "T", 0.05, "EUR", "6:1000:A:G", 0.7),
            ("chr6", 3000, "rs3", 0.2, "G", "A", -0.01, "EUR", "6:1000:A:G", 0.1),
            ("chr6", 4000, "rs4", 1e-3, "T", "C", 0.02, "EUR", "6:1000:A:G", 0.3),
            ("chr6", 1000, "rs1", 1e-4, "A", "G", 0.1, "EAS", "6:1000:A:G", 1),
        ]
        write_tabix(self.gwas, header, rows, 1, 2)
        # eQTL: catalogue columns; GENE1 colocalizes, GENE2 does not; variant 2000 on the other strand
        eqtl_dir = root / "eqtl"
        eqtl_dir.mkdir()
        cols = ["gene_id", "chromosome", "position", "ref", "alt", "pvalue", "beta", "se", "rsid"]
        erows = [
            ("ENSG1", 6, 1000, "A", "G", 1e-8, 0.5, 0.1, "rs1"),
            ("ENSG2", 6, 1000, "A", "G", 0.5, 0.0, 0.1, "rs1"),
            ("ENSG1", 6, 2000, "G", "A", 1e-5, 0.3, 0.1, "rs2"),
            ("ENSG2", 6, 2000, "G", "A", 0.6, 0.0, 0.1, "rs2"),
            ("ENSG1", 6, 3000, "G", "A", 0.3, 0.1, 0.1, "rs3"),
            ("ENSG2", 6, 3000, "G", "A", 1e-9, 0.9, 0.1, "rs3"),
            ("ENSG1", 6, 5000, "A", "C", 0.01, 0.1, 0.1, "rs5"),
        ]
        write_tabix(eqtl_dir / "Artery_Coronary.tsv.gz", cols, erows, 2, 3)
        (eqtl_dir / "manifest.json").write_text(json.dumps({"tissues": [{"key": "Artery_Coronary", "label": "Artery - Coronary",
                                                                       "file": "Artery_Coronary.tsv.gz"}]}))
        genes = root / "genes.tsv"
        genes.write_text("gene_id\tsymbol\tchrom\tstart\tend\tstrand\ttype\nENSG1\tGENE1\tchr6\t500\t2500\t+\tprotein_coding\n"
                         "ENSG2\tGENE2\tchr6\t2600\t4500\t-\tprotein_coding\n")
        settings = {"locusCompare": {"genes": str(genes), "eqtl": [{"id": "test", "label": "Test eQTL", "dir": str(eqtl_dir)}]}}
        self.lc = locuscompare.LocusCompare(settings, root, root / "state")

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_lists_the_tissue_and_no_ld_panel(self):
        catalog = self.lc.catalog()
        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["sources"][0]["tissues"][0]["key"], "Artery_Coronary")
        self.assertFalse(catalog["ld"]["available"])

    def test_matches_alleles_on_either_strand_and_picks_the_colocalizing_gene(self):
        result = self.lc.compare(self.gwas, "Study", "chr6", 1, 10000, population="study")
        self.assertEqual(result["eqtl"]["symbol"], "GENE1")  # the shared signal, not GENE2's stronger lone eQTL
        self.assertEqual(result["gwas"]["subset"], "EUR")    # EUR rows by default, EAS left out
        self.assertEqual(result["gwas"]["subsets"], ["EUR", "EAS"])
        by_pos = {s["pos"]: s for s in result["snps"]}
        self.assertEqual(sorted(by_pos), [1000, 2000, 3000])  # 4000 has no eQTL, 5000 no GWAS
        self.assertEqual(by_pos[2000]["rsid"], "rs2")  # C/T in the GWAS = G/A in GTEx; rsid from the eQTL side
        self.assertEqual(result["lead"], "rs1")  # smallest p1 + p2
        self.assertEqual([g["symbol"] for g in result["egenes"]], ["GENE2", "GENE1"])  # by best eQTL p
        self.assertEqual({g["symbol"] for g in result["genes"]}, {"GENE1", "GENE2"})

    def test_study_ld_and_a_chosen_lead(self):
        result = self.lc.compare(self.gwas, "Study", "chr6", 1, 10000, population="study", gene="GENE1")
        self.assertTrue(result["ld"]["available"])
        self.assertEqual({s["pos"]: s["r2"] for s in result["snps"]}, {1000: 1.0, 2000: 0.7, 3000: 0.1})
        chosen = self.lc.compare(self.gwas, "Study", "chr6", 1, 10000, population="EUR", gene="GENE1", lead="rs3")
        self.assertEqual(chosen["lead"], "rs3")
        self.assertFalse(chosen["ld"]["available"])  # no 1000 Genomes panel configured here
        other = self.lc.compare(self.gwas, "Study", "chr6", 1, 10000, population="study", subset="EAS", gene="GENE1")
        self.assertEqual(len(other["snps"]), 1)

    def test_problems_are_reported(self):
        with self.assertRaises(locuscompare.Problem):
            self.lc.compare(self.gwas, "Study", "chr6", 1, 5_000_000)
        with self.assertRaises(locuscompare.Problem):
            self.lc.compare(self.gwas, "Study", "chr6", 1, 10000, gene="NOPE")
        with self.assertRaises(locuscompare.Problem):
            self.lc.compare(self.gwas, "Study", "chr6", 1, 10000, lead="rs999")
        with self.assertRaises(locuscompare.Problem):
            self.lc.compare(self.gwas, "Study", "chr7", 1, 10000)

    def test_allele_keys(self):
        self.assertEqual(locuscompare.allele_keys("A", "G"), locuscompare.allele_keys("G", "A"))
        self.assertIn(locuscompare.allele_keys("C", "T")[0], locuscompare.allele_keys("G", "A"))
        self.assertEqual(locuscompare.allele_keys("", "A"), ())

    def test_gtex_tissues_offered(self):
        root = Path(self.tmp.name) / "gtex"
        root.mkdir()
        for qtd in ("QTD000136", "QTD000266"):  # artery_coronary, liver
            (root / "{}.all.tsv.gz".format(qtd)).write_bytes(b"")
            (root / "{}.all.tsv.gz.tbi".format(qtd)).write_bytes(b"")
        source = {"id": "v8", "dir": root, "catalogue": "gtex_v8", "tissues": ["artery_coronary", "artery_aorta"]}
        self.assertEqual([t["key"] for t in self.lc.tissues(source)], ["artery_coronary"])  # aorta: neither local nor remote
        source["remote"] = True
        self.assertEqual([(t["key"], t["local"]) for t in self.lc.tissues(source)],
                         [("artery_aorta", False), ("artery_coronary", True)])
        source["offerDownloaded"] = True  # liver is downloaded: offered too, never remote tissues outside the list
        self.assertEqual([t["key"] for t in self.lc.tissues(source)], ["artery_aorta", "artery_coronary", "liver"])

    def test_gtex_tissue_table(self):
        keys = [t[1] for t in locuscompare.GTEX_V8]
        self.assertEqual(len(keys), 49)
        self.assertIn("artery_coronary", keys)


if __name__ == "__main__":
    unittest.main()
