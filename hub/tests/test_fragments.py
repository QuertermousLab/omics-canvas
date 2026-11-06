"""convert.py finds a Seurat object's fragment files when the object was made on another machine; detect.writable.
Run: python -m unittest discover hub/tests"""

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converters'))
import convert  # noqa: E402
import detect  # noqa: E402
import atac_to_viewer  # noqa: E402
import pandas as pd  # noqa: E402
from fragments_to_viewer import gene_models  # noqa: E402

GTF = """\
1\tensembl\tgene\t101\t1000\t.\t-\t.\tgene_id "G1"; gene_name "ABC1"; gene_biotype "protein_coding";
1\tensembl\ttranscript\t101\t1000\t.\t-\t.\tgene_id "G1"; transcript_id "T1"; gene_biotype "protein_coding"; tag "Ensembl_canonical";
1\tensembl\texon\t101\t200\t.\t-\t.\tgene_id "G1"; transcript_id "T1"; gene_biotype "protein_coding";
1\tensembl\texon\t901\t1000\t.\t-\t.\tgene_id "G1"; transcript_id "T1"; gene_biotype "protein_coding";
1\tensembl\tgene\t2001\t3000\t.\t+\t.\tgene_id "G2"; gene_biotype "lncRNA";
"""


class LocateFragmentsTests(unittest.TestCase):
    def test_paths_from_another_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'zeus' / 'Projects' / 'run1'
            frag = run / 'aggr' / 'outs' / 'atac_fragments.tsv.gz'
            frag.parent.mkdir(parents=True)
            frag.write_bytes(b'')
            src = run / 'seurat' / 'object.rds'
            src.parent.mkdir()
            src.write_bytes(b'')
            self.assertEqual(convert.locate_fragments(str(frag), src), frag)  # where it says
            # made where the share is mounted as /ZEUS: found under the folders above the object
            self.assertEqual(convert.locate_fragments('/ZEUS/Projects/run1/aggr/outs/atac_fragments.tsv.gz', src), frag)
            self.assertEqual(convert.locate_fragments('/old/disk/aggr/outs/atac_fragments.tsv.gz', src), frag)
            # a bare file name is not enough, nor a folder that is not there
            self.assertIsNone(convert.locate_fragments('/x/atac_fragments.tsv.gz', src))
            self.assertIsNone(convert.locate_fragments('/ZEUS/Projects/run2/outs/atac_fragments.tsv.gz', src))
            self.assertEqual(convert.usable_fragments([('/ZEUS/Projects/run1/aggr/outs/atac_fragments.tsv.gz', {0: 'A-1'}),
                                                       ('/x/y/missing.tsv.gz', {})], src), [(str(frag), {0: 'A-1'})])


class GeneModelTests(unittest.TestCase):
    def test_gene_level_annotation_uses_the_reference_gtf(self):
        # rtracklayer::import(gtf, feature.type = "gene"): one row per gene, no exons (the genes drew as blocks)
        genes_only = pd.DataFrame(dict(seqnames=['1'], start=[101], end=[1000], strand=['-'], gene_name=['ABC1'],
                                       gene_biotype=['protein_coding'], type=['gene'], tx_id=['G1']))
        self.assertFalse(atac_to_viewer.has_exons(genes_only))
        self.assertTrue(atac_to_viewer.has_exons(genes_only.assign(type='exon')))
        self.assertFalse(atac_to_viewer.has_exons(None))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gtf = root / 'refdata' / 'genes' / 'genes.gtf.gz'
            gtf.parent.mkdir(parents=True)
            with gzip.open(gtf, 'wt') as f:
                f.write(GTF)
            # Ensembl GTF: gene_biotype, the canonical transcript's exons, unnamed genes left out
            self.assertEqual(dict(gene_models(gtf)), {'1': [['ABC1', 100, 1000, '-', 'p', [(100, 200), (900, 1000)]]]})
            run = root / 'aggr'
            (run / 'outs').mkdir(parents=True)
            # the reference as the machine that ran Cell Ranger named it: found from the folders above the object
            (run / '_invocation').write_text('    reference_path = "/OTHER/disk/refdata",\n')
            src = root / 'seurat' / 'object.rds'
            src.parent.mkdir()
            self.assertEqual(convert.reference_gtf([(str(run / 'outs' / 'atac_fragments.tsv.gz'), {})], src), gtf)
            self.assertIsNone(convert.reference_gtf([(str(root / 'x' / 'outs' / 'f.tsv.gz'), {})], src))


class WritableTests(unittest.TestCase):
    def test_writable_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(detect.writable(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])  # the test file is gone
            self.assertFalse(detect.writable(Path(tmp) / 'missing'))


if __name__ == '__main__':
    unittest.main()
