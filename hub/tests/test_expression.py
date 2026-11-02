"""expression.py on a small slide folder written here: matrices (CSV / TSV / gzip / Matrix Market), cell picking and
distributions. Run: python -m unittest discover hub/tests"""

import base64
import csv
import gzip
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import expression as E  # noqa: E402
import module_score  # noqa: E402

GENES = ['Myh11', 'Acta2', 'Cnn1', 'ENSMUSG00000000001.4', 'Empty']


def write_slide(folder: Path, dense: np.ndarray, groups: list, codes: np.ndarray, ids: list):
    """A slide folder as the converters write it: gene-wise expr.bin (uint32 cells, uint16 counts), cells.bin obs."""
    n, g = dense.shape
    index, blob = [], bytearray()
    for j in range(g):
        cells = np.flatnonzero(dense[:, j]).astype('<u4')
        index.append((len(blob), len(cells)))
        blob += cells.tobytes() + dense[cells, j].astype('<u2').tobytes()
    (folder / 'expr.bin').write_bytes(bytes(blob))
    np.asarray(index, '<f8').tofile(folder / 'expr.idx')
    total = dense.sum(axis=1).astype('<f4')
    cells_bin = total.tobytes() + codes.astype('<u1').tobytes()
    (folder / 'cells.bin').write_bytes(cells_bin)
    (folder / 'cell_ids.txt').write_text('\n'.join(ids) + '\n')
    obs = [dict(key='total_counts', label='Total counts', type='number', dtype='float32', offset=0, hidden=True),
           dict(key='cluster', label='Cluster', type='category', dtype='uint8', offset=4 * n, categories=groups)]
    (folder / 'slide.json').write_text(json.dumps(dict(nCells=n, genes=GENES, obs=obs, exprDtype='uint16',
                                                       normalization='log1p(counts / total_counts * 10000)')))


class ExpressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        rng = np.random.default_rng(3)
        self.n = 300
        self.dense = rng.poisson(2, (self.n, len(GENES))) * (rng.random((self.n, len(GENES))) < 0.6)
        self.dense[:, 4] = 0
        self.dense[:, 0] += 1  # every cell has counts
        self.codes = rng.integers(0, 3, self.n)
        self.ids = [f'CELL{i:04d}-1' for i in range(self.n)]
        write_slide(self.folder, self.dense, ['SMC', 'EC, aortic', 'FB'], self.codes, self.ids)
        self.m = module_score.Matrix(self.folder, '', genes_only=False)
        self.cells = E.Cells(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def table(self, text, sep=','):
        lines = list(csv.reader(io.StringIO(text), delimiter=sep))
        return lines[0], lines[1:]

    def test_genes_matched_by_case_and_ensembl_version(self):
        idx, info = E.genes_of(self.m, '"myh11", CNN1; ENSMUSG00000000001 nope')
        self.assertEqual([GENES[i] for i in idx], ['Myh11', 'Cnn1', 'ENSMUSG00000000001.4'])
        self.assertEqual(info['missing'], ['nope'])
        self.assertEqual(info['converted']['myh11'], 'Myh11')
        all_idx, _ = E.genes_of(self.m, '')
        self.assertEqual(all_idx, list(range(len(GENES))))

    def test_counts_and_normalized_tables(self):
        genes, _ = E.genes_of(self.m, 'Acta2 Myh11')
        rows = np.arange(self.n)
        head, body = self.table(''.join(E.dense_text(self.m, self.cells, genes, rows, 'counts', ',', 'cells', ['cluster'])))
        self.assertEqual(head, ['cell', 'cluster', 'Acta2', 'Myh11'])
        self.assertEqual(len(body), self.n)
        for i in (0, 7, 299):
            self.assertEqual(body[i][0], self.ids[i])
            self.assertEqual(int(body[i][2]), self.dense[i, 1])
            self.assertEqual(int(body[i][3]), self.dense[i, 0])
        # "EC, aortic" has a comma: quoted
        ec = next(i for i in range(self.n) if self.codes[i] == 1)
        self.assertIn('"EC, aortic"', ''.join(E.dense_text(self.m, self.cells, genes, np.array([ec]), 'counts', ',', 'cells', ['cluster'])))
        _, body = self.table(''.join(E.dense_text(self.m, self.cells, genes, rows, 'normalized', '\t')), '\t')
        total = self.dense.sum(axis=1)
        for i in (0, 11):
            self.assertAlmostEqual(float(body[i][1]), np.log1p(self.dense[i, 1] / total[i] * 1e4), places=3)

    def test_genes_as_rows(self):
        genes, _ = E.genes_of(self.m, 'Cnn1')
        head, body = self.table(''.join(E.dense_text(self.m, self.cells, genes, np.arange(10), 'counts', ',', 'genes')))
        self.assertEqual(head, ['gene'] + self.ids[:10])
        self.assertEqual([int(x) for x in body[0][1:]], self.dense[:10, 2].tolist())

    def test_cell_picking(self):
        rows, _ = E.select(self.cells, where={'Cluster': ['smc', 'EC, aortic']})
        self.assertEqual(rows.tolist(), np.flatnonzero(np.isin(self.codes, [0, 1])).tolist())
        rows, _ = E.select(self.cells, where={'cluster': 'SMC,FB'})  # split at commas (GET links)
        self.assertEqual(rows.tolist(), np.flatnonzero(np.isin(self.codes, [0, 2])).tolist())
        rows, info = E.select(self.cells, ids='CELL0005-1, cell0002-1 nope')
        self.assertEqual(rows.tolist(), [2, 5])
        self.assertEqual(info['missingCells'], 1)
        bits = np.zeros(self.n, np.uint8)
        bits[[3, 40, 299]] = 1
        mask = base64.b64encode(np.packbits(bits, bitorder='little')).decode()
        rows, _ = E.select(self.cells, mask=mask)
        self.assertEqual(rows.tolist(), [3, 40, 299])
        rows, _ = E.select(self.cells, indices=[1, 3, 40], mask=mask)  # every criterion
        self.assertEqual(rows.tolist(), [3, 40])
        with self.assertRaises(E.Problem):
            E.select(self.cells, where={'cluster': ['nope']})
        with self.assertRaises(E.Problem):
            E.select(self.cells, where={'unknown': ['x']})

    def test_matrix_market_zip_and_gzip(self):
        genes, _ = E.genes_of(self.m, 'Myh11 Acta2 Empty')
        rows = np.flatnonzero(self.codes == 0)
        z = zipfile.ZipFile(io.BytesIO(b''.join(E.market_zip(self.m, self.cells, genes, rows, 'counts', ['cluster'], 'x'))))
        self.assertEqual(sorted(z.namelist()), ['x/barcodes.tsv', 'x/features.tsv', 'x/matrix.mtx', 'x/metadata.tsv'])
        lines = z.read('x/matrix.mtx').decode().strip().split('\n')
        n_genes, n_cells, nnz = map(int, lines[2].split())
        self.assertEqual((n_genes, n_cells), (3, len(rows)))
        got = np.zeros((3, len(rows)), int)
        for line in lines[3:]:
            i, j, x = line.split()
            got[int(i) - 1, int(j) - 1] = int(x)
        self.assertEqual(nnz, len(lines) - 3)
        self.assertTrue((got == self.dense[rows][:, [0, 1, 4]].T).all())
        self.assertEqual(z.read('x/barcodes.tsv').decode().split(), [self.ids[i] for i in rows])
        text = ''.join(E.dense_text(self.m, self.cells, genes, rows, 'counts'))
        self.assertEqual(gzip.decompress(b''.join(E.gzipped(E.dense_text(self.m, self.cells, genes, rows, 'counts')))).decode(), text)

    def test_limits_and_counts(self):
        old = E.DENSE_MAX
        E.DENSE_MAX = 100
        try:
            with self.assertRaises(E.Problem):
                E.dense_text(self.m, self.cells, list(range(5)), np.arange(self.n), 'counts')
        finally:
            E.DENSE_MAX = old
        self.m.float = True
        with self.assertRaises(E.Problem):
            E.check_value(self.m, 'counts')

    def test_distribution(self):
        values = np.zeros(self.n)
        c, v = E.gene_values(self.m, 1, 'normalized')
        values[c] = v
        d = E.distribution(values, np.arange(self.n), self.codes, ['SMC', 'EC, aortic', 'FB'])
        self.assertEqual([g['name'] for g in d['groups']], ['SMC', 'EC, aortic', 'FB'])
        for g in d['groups']:
            x = values[self.codes == g['code']]
            self.assertEqual(g['cells'], len(x))
            self.assertEqual(g['expressing'], int((x > 0).sum()))
            self.assertAlmostEqual(g['median'], float(np.median(x)))
            self.assertEqual(len(g['density']), d['grid'])
        # a density: integrates to ~1 on continuous data, Gaussian bandwidth as R's bw.nrd0
        x = np.random.default_rng(1).normal(5, 1, 5000)
        y = E.density(x, x.min(), x.max())
        self.assertAlmostEqual(y.sum() * (x.max() - x.min()) / (len(y) - 1), 1, delta=0.03)
        self.assertAlmostEqual(E.bw_nrd0(x), 0.9 * min(x.std(ddof=1), np.subtract(*np.percentile(x, [75, 25])) / 1.34) * 5000 ** -0.2)
        above = E.distribution(values, np.arange(self.n), None, nonzero=True)
        self.assertEqual(above['groups'][0]['n'], int((values > 0).sum()))
        self.assertEqual(above['groups'][0]['cells'], self.n)


if __name__ == '__main__':
    unittest.main()
