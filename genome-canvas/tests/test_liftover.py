import gzip
import struct
import tempfile
import time
import unittest
from pathlib import Path

import liftover


def bigwig_bytes(chromosomes):
    """A bigWig header and chromosome B+ tree (one leaf), nothing else."""
    key_size = max(len(name) for name, _ in chromosomes)
    header = struct.pack("<IHHQ", 0x888FFC26, 4, 0, 64).ljust(64, b"\0")
    tree = struct.pack("<IIIIQQ", 0x78CA8C91, 256, key_size, 8, len(chromosomes), 0)
    leaf = struct.pack("<BBH", 1, 0, len(chromosomes))
    for index, (name, size) in enumerate(chromosomes):
        leaf += name.encode().ljust(key_size, b"\0") + struct.pack("<II", index, size)
    return header + tree + leaf


def bam_bytes(chromosomes):
    text = b"@HD\tVN:1.6\n"
    body = b"BAM\1" + struct.pack("<i", len(text)) + text + struct.pack("<i", len(chromosomes))
    for name, size in chromosomes:
        encoded = name.encode() + b"\0"
        body += struct.pack("<i", len(encoded)) + encoded + struct.pack("<i", size)
    return gzip.compress(body)


class DetectBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_bigwig_chromosome_sizes(self):
        path = self.root / "signal.bw"
        path.write_bytes(bigwig_bytes([("chr1", 249250621), ("chr2", 243199373)]))
        self.assertEqual(liftover.detect_build(path, "bigwig")["build"], "hg19")
        path.write_bytes(bigwig_bytes([("chrX", 169476592)]))
        self.assertEqual(liftover.detect_build(path, "bigwig")["build"], "mm39")

    def test_bam_header_with_prefixed_names(self):
        path = self.root / "reads.bam"
        path.write_bytes(bam_bytes([("GRCh38_chr1", 248956422), ("GRCh38_chr2", 242193529)]))
        found = liftover.detect_build(path, "bam")
        self.assertEqual(found["build"], "hg38")
        self.assertIn("248,956,422", found["evidence"])

    def test_vcf_contigs_then_name(self):
        path = self.root / "calls.vcf.gz"
        path.write_bytes(gzip.compress(b"##fileformat=VCFv4.2\n##contig=<ID=1,length=195471971>\n#CHROM\tPOS\n"))
        self.assertEqual(liftover.detect_build(path, "vcf")["build"], "mm10")
        named = self.root / "GRCh37" / "peaks.bed"
        named.parent.mkdir()
        named.write_text("chr1\t1\t2\n")
        found = liftover.detect_build(named, "bed")
        self.assertEqual((found["build"], found["evidence"]), ("hg19", "“GRCh37” in its folder name"))
        self.assertIsNone(liftover.detect_build(self.root / "missing.bed", "bed"))

    def test_header_wins_over_the_name(self):
        path = self.root / "signal.hg38.bw"
        path.write_bytes(bigwig_bytes([("chr1", 249250621)]))
        self.assertEqual(liftover.detect_build(path, "bigwig")["build"], "hg19")

    def test_detection_is_fast(self):
        path = self.root / "signal.bw"
        path.write_bytes(bigwig_bytes([("chr{}".format(i), 1000 + i) for i in range(1, 23)] + [("chr1x", 5)]))
        started = time.monotonic()
        for _ in range(50):
            liftover.detect_build(path, "bigwig")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_canonical_names(self):
        self.assertEqual([liftover.canonical_build(name) for name in ("GRCh38", "hg19", "GRCm39.p6", "b37", "dm6")],
                         ["hg38", "hg19", "mm39", "hg19", None])


class LiftoverRulesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.chains = Path(self.temporary.name) / "chains"
        self.chains.mkdir()
        (self.chains / "hg19ToHg38.over.chain.gz").write_bytes(gzip.compress(b""))
        self.tools = liftover.Liftover(self.chains, Path(self.temporary.name) / "out")

    def tearDown(self):
        self.temporary.cleanup()

    def test_pairs_come_from_chain_files(self):
        self.assertEqual(self.tools.pairs(), [["hg19", "hg38"]])

    def test_reasons(self):
        self.assertIn("No chain file", self.tools.why_not("bed", "hg38", "hg19"))
        self.assertIn("binned", self.tools.why_not("hic", "hg19", "hg38"))
        self.assertIn("already", self.tools.why_not("bed", "hg38", "hg38"))
        if liftover.tool("CrossMap"):
            self.assertIsNone(self.tools.why_not("bigwig", "hg19", "hg38"))
            self.assertIn("FASTA", self.tools.why_not("vcf", "hg19", "hg38"))


if __name__ == "__main__":
    unittest.main()
