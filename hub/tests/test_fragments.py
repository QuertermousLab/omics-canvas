"""convert.py finds a Seurat object's fragment files when the object was made on another machine; detect.writable.
Run: python -m unittest discover hub/tests"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converters'))
import convert  # noqa: E402
import detect  # noqa: E402


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


class WritableTests(unittest.TestCase):
    def test_writable_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(detect.writable(tmp))
            self.assertEqual(list(Path(tmp).iterdir()), [])  # the test file is gone
            self.assertFalse(detect.writable(Path(tmp) / 'missing'))


if __name__ == '__main__':
    unittest.main()
