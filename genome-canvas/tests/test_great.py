import gzip
import http.client
import io
import json
import math
import re
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

import great
import great_service
import server
from workspace_store import WorkspaceStore

# A small assembly worked out by hand. chrA (100 kb, a gap at 90-100 kb) holds g1 (+, TSS 10 kb), g2 (-, 30 kb) and
# g3 (+, 60 kb); chrB (50 kb) holds g4 (-, 20 kb) with a curated domain over all of chrB.
GENES = [("G1", "GENE1", "chrA", 10000, "+"), ("G2", "GENE2", "chrA", 30000, "-"),
         ("G3", "GENE3", "chrA", 60000, "+"), ("G4", "GENE4", "chrB", 20000, "-")]
SIZES = {"chrA": 100000, "chrB": 50000}


def write_assembly(folder, name="hg38"):
    folder = Path(folder) / name
    folder.mkdir(parents=True)
    (folder / "genes.tsv").write_text("#id\tchrom\ttss\tstrand\tsymbol\n" + "".join(
        "{}\t{}\t{}\t{}\t{}\n".format(g[0], g[2], g[3], g[4], g[1]) for g in GENES))
    (folder / "chrom.sizes").write_text("".join("{}\t{}\n".format(c, s) for c, s in SIZES.items()))
    (folder / "gaps.bed").write_text("chrA\t90000\t100000\n")
    (folder / "curated.tsv").write_text("#chrom\tstart\tend\tgene_id\tnote\nchrB\t0\t50000\tG4\ttest\n")
    ontologies = [("GOBiologicalProcess", "GO Biological Process", "GO",
                   [["T:1", "term one"], ["T:2", "term two"], ["T:3", "term three"]], [[0, 1], [2], [3]]),
                  ("EnsemblGenes", "Ensembl Genes", "Genes", [[g[0], g[1]] for g in GENES], [[0], [1], [2], [3]])]
    for key, title, category, terms, genes in ontologies:
        with gzip.open(str(folder / (key + ".json.gz")), "wt") as handle:
            json.dump({"key": key, "name": title, "category": category, "version": "test", "terms": terms,
                       "genes": genes}, handle)
    (folder / "manifest.json").write_text(json.dumps({
        "assembly": name, "built": "2026-11-05", "genes": len(GENES),
        "ontologies": [{"key": o[0], "name": o[1], "category": o[2], "terms": len(o[3]), "version": "test"}
                       for o in ontologies]}))
    return folder.parent


def binomial_exact(n, k, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def hypergeometric_exact(total, successes, draws, k):
    return sum(math.comb(successes, i) * math.comb(total - successes, draws - i)
               for i in range(k, min(successes, draws) + 1)) / math.comb(total, draws)


class StatisticsTests(unittest.TestCase):
    def test_binomial_tail_matches_the_exact_sum(self):
        for n, k, p in ((4, 2, 55 / 140), (100, 10, 0.03), (1000, 40, 0.02), (20, 20, 0.5), (50, 1, 0.001)):
            self.assertAlmostEqual(great.binomial_upper(n, k, p) / binomial_exact(n, k, p), 1.0, places=5)
        self.assertEqual(great.binomial_upper(10, 0, 0.3), 1.0)
        self.assertEqual(great.binomial_upper(10, 3, 0.0), 0.0)

    def test_hypergeometric_tail_matches_the_exact_sum(self):
        for args in ((18549, 13, 1418, 5), (18549, 400, 3000, 30), (100, 20, 30, 2), (100, 20, 30, 15), (50, 50, 10, 10)):
            self.assertAlmostEqual(great.hypergeometric_upper(*args), hypergeometric_exact(*args), places=12)
        self.assertEqual(great.hypergeometric_upper(100, 5, 10, 6), 0.0)
        self.assertEqual(great.hypergeometric_upper(100, 5, 10, 0), 1.0)

    def test_corrections_are_great_style(self):
        ranks, bonferroni, fdr = great.corrections([0.01, 0.001, 0.5])
        self.assertEqual(ranks, [2, 1, 3])
        self.assertAlmostEqual(bonferroni[0], 0.03)
        self.assertAlmostEqual(fdr[0], 0.015)  # m p / rank, not forced monotone
        self.assertEqual(bonferroni[2], 1.0)


class DomainTests(unittest.TestCase):
    def test_basal_plus_extension_follows_create_regulatory_domains(self):
        domains = great.regulatory_domains(GENES, SIZES, "basalPlusExt", 5000, 1000, 1000000)
        # g1 stops at g2's basal domain (g2 is minus: 30 kb - 1 kb); g2 starts after g1's (10 kb + 1 kb) and stops
        # at g3's (60 kb - 5 kb); g3 starts after g2's basal domain (30 kb + 5 kb upstream of a minus gene)
        self.assertEqual(domains, [(0, 29000), (11000, 55000), (35000, 100000), (0, 50000)])

    def test_two_nearest_and_single_nearest(self):
        self.assertEqual(great.regulatory_domains(GENES, SIZES, "twoClosest", extension=1000000),
                         [(0, 30000), (10000, 60000), (30000, 100000), (0, 50000)])
        self.assertEqual(great.regulatory_domains(GENES, SIZES, "oneClosest", extension=1000000),
                         [(0, 20000), (20000, 45000), (45000, 100000), (0, 50000)])
        self.assertEqual(great.regulatory_domains(GENES, SIZES, "oneClosest", extension=4000)[0], (6000, 14000))

    def test_parameters_take_rgreat_kilobases(self):
        params = great.normalized_params({"rule": "basalPlusExt", "adv_upstream": "10", "adv_downstream": "2.5",
                                          "adv_span": "500", "includeCuratedRegDoms": "0"})
        self.assertEqual((params["upstream"], params["downstream"], params["extension"]), (10000, 2500, 500000))
        self.assertFalse(params["curated"])
        with self.assertRaises(great.InputError):
            great.normalized_params({"rule": "closest"})


class ReadRegionsTests(unittest.TestCase):
    sizes = {"chr1": 1000000, "chr2": 500000, "chrX": 300000, "chrM": 16569}

    def read(self, text, **options):
        data = text if isinstance(text, bytes) else text.encode()
        return great.read_regions(data, self.sizes, **options)

    def test_bed_with_track_lines_comments_and_dot_names(self):
        regions, report = self.read("track name=x\n# comment\nchr1\t100\t200\t.\t0\t+\nchr2\t5\t9\tpeak2\t3\t-\n")
        self.assertEqual(regions, [("chr1", 100, 200, "", None), ("chr2", 5, 9, "peak2", None)])
        self.assertIn("BED6", report["notes"][0])

    def test_chromosome_names_contigs_and_bad_lines_are_reported(self):
        regions, report = self.read("1\t10\t20\nMT\t1\t5\nchrUn_gl000220\t1\t5\nx\t30\t20\nchr1\tten\t20\nchr2\t400000\t600000\n")
        self.assertEqual([r[:3] for r in regions], [("chr1", 10, 20), ("chrM", 1, 5), ("chrX", 20, 30), ("chr2", 400000, 500000)])
        kinds = {issue["kind"]: issue["count"] for issue in report["issues"]}
        self.assertEqual(kinds, {"unknownChromosome": 1, "swapped": 1, "badCoordinates": 1, "clipped": 1})
        self.assertIn(["1", "chr1"], report["renamed"])

    def test_granges_csv_with_header_is_one_based(self):
        regions, report = self.read('seqnames,start,end,width,strand,peak\n"chr1",1001,2000,1000,*,p1\n2,"1,001","1,500",500,*,p2\n')
        self.assertEqual(regions, [("chr1", 1000, 2000, "p1", None), ("chr2", 1000, 1500, "p2", None)])
        self.assertEqual(report["columns"]["chrom"], "seqnames")

    def test_columns_found_by_name_anywhere(self):
        regions, _ = self.read("gene\tscore\tChromosome\tStart\tEnd\nABC\t1.5\tchr1\t100\t200\n")
        self.assertEqual(regions[0][:3], ("chr1", 100, 200))

    def test_narrowpeak_summit_and_macs_xls(self):
        regions, report = self.read("chr1\t100\t300\tp1\t50\t.\t5.1\t3.2\t2.1\t40\n")
        self.assertEqual(regions[0][4], 140)
        self.assertEqual(report["summits"], 1)
        xls = ("# This file is generated by MACS\n\nchr\tstart\tend\tlength\tabs_summit\tpileup\t-log10(pvalue)\t"
               "fold_enrichment\t-log10(qvalue)\tname\nchr1\t101\t300\t200\t150\t12\t8\t4\t6\tpeak_1\n")
        regions, report = self.read(xls)
        self.assertEqual(regions, [("chr1", 100, 300, "peak_1", 149)])
        self.assertIn("MACS", report["notes"][0])

    def test_region_strings_and_positions(self):
        regions, _ = self.read("chr1:1,001-2,000\nchr2-501-600 myname\n")
        self.assertEqual([r[:4] for r in regions], [("chr1", 1000, 2000, ""), ("chr2", 500, 600, "myname")])
        regions, _ = self.read("chr1 12345\nchrX 99\n")
        self.assertEqual([r[:3] for r in regions], [("chr1", 12344, 12345), ("chrX", 98, 99)])

    def test_scientific_notation_and_compressed_files(self):
        regions, _ = self.read(gzip.compress(b"chr1\t1e+05\t100500\n"))
        self.assertEqual(regions[0][:3], ("chr1", 100000, 100500))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("peaks.bed", "chr2\t10\t20\n")
        self.assertEqual(self.read(archive.getvalue())[0][0][:3], ("chr2", 10, 20))

    def test_coordinates_can_be_forced(self):
        self.assertEqual(self.read("chr1\t100\t200\n", coordinates="one")[0][0][1], 99)
        self.assertEqual(self.read("seqnames,start,end\nchr1,100,200\n", coordinates="bed")[0][0][1], 100)

    def test_nothing_usable_is_an_input_error(self):
        with self.assertRaises(great.InputError):
            self.read("# only a comment\n")
        with self.assertRaises(great.InputError):
            self.read("chrZ\t1\t10\n")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = write_assembly(self.temporary.name)
        self.engine = great.Engine(self.data)

    def tearDown(self):
        self.temporary.cleanup()

    def job(self, regions, params=None):
        folder = Path(self.temporary.name) / "job"
        folder.mkdir(exist_ok=True)
        (folder / "job.json").write_text(json.dumps({"assembly": "hg38", "params": great.normalized_params(params or {})}))
        great.write_job_regions(folder, "fg.tsv.gz", regions)
        return folder

    def test_whole_genome_test_by_hand(self):
        regions = [("chrA", 5000, 5010, "r1", None), ("chrA", 20000, 20002, "r2", None),
                   ("chrA", 70000, 70001, "r3", None), ("chrB", 100, 200, "r4", None)]
        folder = self.job(regions)
        summary = great.analyze(self.engine, folder)
        self.assertEqual(summary["genesHit"], 4)
        self.assertEqual(summary["graphs"]["genesPerRegion"], [0, 3, 1, 0])
        rows = {r[0]: r for r in great.read_json_gz(folder / "GOBiologicalProcess.json.gz")}
        non_gap = 140000
        term_one = rows["T:1"]
        self.assertAlmostEqual(term_one[2], 55000 / non_gap)  # [0, 55 kb) of chrA
        self.assertEqual(term_one[4], 2)
        self.assertAlmostEqual(term_one[7] / binomial_exact(4, 2, 55000 / non_gap), 1.0, places=5)
        self.assertAlmostEqual(rows["T:2"][2], 55000 / non_gap)  # [35, 100 kb) minus the 10 kb gap
        self.assertEqual(len(term_one), 15)  # rGREAT's column count
        associations = great.read_associations(folder)
        self.assertEqual(associations[1], ("r2", [(0, 10000), (1, 9999)]))  # minus strand: TSS - 1 - midpoint
        details = great.term_details(self.engine, folder, "GOBiologicalProcess", "T:1")
        self.assertEqual([r["region"] for r in details["byRegion"]], ["r1", "r2"])
        self.assertEqual(details["graphs"]["regionsPerGene"], [1, 1, 0, 0, 0])

    def test_without_curated_domains_and_with_another_rule(self):
        regions = [("chrB", 45000, 45010, "far", None)]
        with_curated = great.analyze(self.engine, self.job(regions))
        self.assertEqual(with_curated["associatedRegions"], 1)
        rows = {r[0]: r for r in great.read_json_gz(Path(self.temporary.name) / "job" / "GOBiologicalProcess.json.gz")}
        self.assertEqual(rows["T:3"][4], 1)
        summary = great.analyze(self.engine, self.job(regions, {"rule": "oneClosest", "oneDistance": "10"}))
        self.assertEqual(summary["associatedRegions"], 1)  # the curated domain still covers chrB
        summary = great.analyze(self.engine, self.job(regions, {"rule": "oneClosest", "oneDistance": "10",
                                                                "includeCuratedRegDoms": "0"}))
        self.assertEqual(summary["associatedRegions"], 0)  # 10 kb around g4's TSS ends at 30 kb


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data = write_assembly(root / "data")
        self.service = great_service.GreatService({"great": {"dataDir": str(self.data)}}, root, root / "state")

    def tearDown(self):
        self.service.pool.stop()
        self.temporary.cleanup()

    def test_submit_export_and_associations(self):
        fg = b"chrA\t5000\t5010\tr1\nchrA\t20000\t20002\tr2\nchrA\t70000\t70001\tr3\nchrB\t100\t200\tr4\n"
        job = self.service.submit("hg38", fg, client="10.0.0.1", name="test regions")
        self.assertEqual(job["status"], "done")
        tsv = great_service.export_tsv(self.service, job).splitlines()
        self.assertTrue(tsv[3].startswith("# Ontology\tID\tDesc\tBinomRank"))  # rGREAT drops 3 lines, then a header
        body = [line.split("\t") for line in tsv[4:] if not line.startswith("#")]
        term_one = next(f for f in body if f[1] == "T:1")
        self.assertEqual(len(term_one), 24)
        self.assertEqual(term_one[22], "r1,r2")
        self.assertEqual(term_one[23], "GENE1,GENE2")
        table = great_service.association_table(self.service, job).splitlines()
        self.assertEqual(table[1:3], ["r1\tGENE1 (-4996)", "r2\tGENE1 (+10000), GENE2 (+9999)"])
        by_gene = great_service.association_table(self.service, job, "gene").splitlines()
        self.assertIn("GENE1\tr1 (-4996), r2 (+10000)", by_gene)

    def test_background_set_must_hold_the_test_set(self):
        job = self.service.submit("hg38", b"chrA\t5000\t5010\n", b"chrA\t70000\t70010\nchrB\t10\t20\n", client="10.0.0.2")
        self.assertEqual(job["background"], "regions")
        self.assertEqual(job["summary"]["backgroundRegions"], 3)
        self.assertIn("added", job["warnings"][0])
        rows = self.service.rows(job["id"], "GOBiologicalProcess")
        self.assertEqual(len(rows[0]), 12)  # rGREAT's foreground/background columns

    def test_limits_change_live(self):
        self.service.update_settings({"minIntervalSeconds": 60, "maxWaitSeconds": 0})
        self.service.submit("hg38", b"chrA\t5000\t5010\n", client="10.0.0.3")
        with self.assertRaises(great_service.LimitError) as raised:
            self.service.submit("hg38", b"chrA\t5000\t5010\n", client="10.0.0.3")
        self.assertEqual(raised.exception.status, 429)
        self.service.submit("hg38", b"chrA\t5000\t5010\n", client="127.0.0.1")  # exempt
        self.service.update_settings({"minIntervalSeconds": 0})
        self.service.submit("hg38", b"chrA\t5000\t5010\n", client="10.0.0.3")
        self.service.update_settings({"blockedClients": "10.0.0.9"})
        with self.assertRaises(great_service.LimitError):
            self.service.submit("hg38", b"chrA\t5000\t5010\n", client="10.0.0.9")
        with self.assertRaises(ValueError):
            self.service.update_settings({"maxConcurrentJobs": 0})
        with self.assertRaises(great.InputError):
            self.service.update_settings({"maxRegions": 2})
            self.service.submit("hg38", b"chrA\t1\t2\nchrA\t3\t4\nchrA\t5\t6\n", client="10.0.0.4")

    def test_session_page_reads_like_rgreat(self):
        job = self.service.submit("hg38", b"chrA\t5000\t5010\n", client="10.0.0.5")
        page = great_service.session_page(job, "http://server/great/#job=" + job["id"])
        self.assertEqual(re.search(r'var _sessionName = "(.*?)";', page).group(1), job["id"])
        categories = [re.sub(r"<.*?>", "", m) for m in re.findall(r'<div class="gc_header gc_column">.*?</div>', page)]
        items = re.findall(r'<div class="gc_info_item gc_column">.*?</div>', page)
        names = [re.sub(r"<.*?>", "", m).strip() for m in items]
        # rGREAT gives the i-th item (1-based) to the category with the same index modulo their number
        assigned = {c: [n for i, n in enumerate(names, 1) if i % len(categories) == j % len(categories) and n]
                    for j, c in enumerate(categories, 1)}
        self.assertEqual(assigned, {"GO": ["GO Biological Process"], "Genes": ["Ensembl Genes"]})
        self.assertIn('value="GOBiologicalProcess"', page)

    def test_multipart_forms_without_the_cgi_module(self):
        body, content_type = multipart({"species": "hg38", "fgFile": ("x.bed.gz", gzip.compress(b"chrA\t1\t2\n"))})
        fields = great_service.parse_form(content_type, body)
        self.assertEqual(fields["species"], "hg38")
        self.assertEqual(gzip.decompress(fields["fgFile"]), b"chrA\t1\t2\n")
        self.assertEqual(fields["fgFile__filename"], "x.bed.gz")
        self.assertEqual(great_service.parse_form("application/x-www-form-urlencoded", b"a=1&b=x%20y"), {"a": "1", "b": "x y"})


def multipart(fields):
    boundary = "----greattest"
    parts = []
    for name, value in fields.items():
        if isinstance(value, tuple):
            parts.append('--{}\r\nContent-Disposition: form-data; name="{}"; filename="{}"\r\nContent-Type: '
                         'application/octet-stream\r\n\r\n'.format(boundary, name, value[0]).encode() + value[1] + b"\r\n")
        else:
            parts.append('--{}\r\nContent-Disposition: form-data; name="{}"\r\n\r\n{}\r\n'.format(boundary, name, value).encode())
    return b"".join(parts) + "--{}--\r\n".format(boundary).encode(), "multipart/form-data; boundary=" + boundary


class InterfaceTests(unittest.TestCase):
    """GREAT's .php interfaces over HTTP, as rGREAT and scripts call them."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.previous = server.GREAT
        server.GREAT = great_service.GreatService({"great": {"dataDir": str(write_assembly(root / "data"))}}, root,
                                                  root / "state")
        self.httpd = server.GenomeCanvasHTTPServer(("127.0.0.1", 0), server.GenomeCanvasHandler,
                                                   WorkspaceStore(root / "workspaces.sqlite3"), root / "homes")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.GREAT.pool.stop()
        server.GREAT = self.previous
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=60)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type", ""), data.decode("utf-8")

    def test_rgreat_calls(self):
        bed = gzip.compress(b"chrA\t5000\t5010\t/chrA:5000-5010\nchrA\t20000\t20002\t/chrA:20000-20002\n")
        body, content_type = multipart({"species": "hg38", "rule": "basalPlusExt", "span": "1000", "upstream": "5",
                                        "downstream": "1", "includeCuratedRegDoms": "1", "fgChoice": "file",
                                        "fgFile": ("file1.gz", bed), "bgChoice": "wholeGenome"})
        status, _, page = self.request("POST", "/great/public/cgi-bin/greatWeb.php", body, {"Content-Type": content_type})
        self.assertEqual(status, 200)
        job = re.search(r'var _sessionName = "(.*?)";', page).group(1)
        status, _, text = self.request("GET", "/great/public-4.0.4/cgi-bin/readJsFromFile.php?path=/scratch/great/tmp/"
                                              "results/{}.d/GOBiologicalProcess.js".format(job))
        rows = json.loads(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), 15)
        form = "outputDir=/scratch/great/tmp/results/{}.d/&ontoName=GOBiologicalProcess&species=hg38&" \
               "ontoList=GOBiologicalProcess@1-Inf&binom=true".format(job)
        status, _, tsv = self.request("POST", "/great/public/cgi-bin/downloadAllTSV.php", form,
                                      {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertTrue(tsv.splitlines()[3].startswith("# Ontology"))
        status, _, table = self.request("GET", "/great/public/cgi-bin/downloadAssociations.php?sessionName={}&species="
                                               "hg38&foreName=file1.gz&backName=&table=region".format(job))
        self.assertIn("/chrA:20000-20002\tGENE1 (+10000), GENE2 (+9999)", table)
        status, _, table = self.request("GET", "/great/public/cgi-bin/downloadAssociations.php?termId=T:2&ontoName="
                                               "GOBiologicalProcess&sessionName={}&table=region".format(job))
        self.assertEqual([line for line in table.splitlines() if not line.startswith("#")], [])
        status, _, text = self.request("GET", "/great/public/cgi-bin/readJsFromFile.php?path=/scratch/great/tmp/"
                                              "results/20200101-000000-aaaaaaaa.d/GOBiologicalProcess.js")
        self.assertEqual((status, text), (200, ""))  # rGREAT: an expired job
        status, _, script = self.request("GET", "/great/rgreat.R")
        self.assertIn('sub("^https?://great\\\\.stanford\\\\.edu", server, urls)', script)

    def test_user_errors_and_the_batch_interface(self):
        body, content_type = multipart({"species": "hg38", "fgFile": ("x.bed", b"chrZ\t1\t5\n")})
        status, _, page = self.request("POST", "/great/public/cgi-bin/greatWeb.php", body, {"Content-Type": content_type})
        self.assertEqual(status, 200)
        self.assertIn("GREAT encountered a user error", page)
        self.assertIn("<blockquote><b>Error details:</b>", page)
        body, content_type = multipart({"outputType": "batch", "requestSpecies": "hg38",
                                        "requestFile": ("x.bed", b"chrA\t5000\t5010\n")})
        status, _, tsv = self.request("POST", "/great/public/cgi-bin/greatStart.php", body, {"Content-Type": content_type})
        self.assertEqual(status, 200)
        self.assertIn("# Ontology\tTerms tested", tsv)
        status, _, _ = self.request("GET", "/great/public/cgi-bin/greatStart.php?outputType=batch&requestSpecies=hg38"
                                           "&requestURL=http%3A%2F%2F127.0.0.1%3A1%2Fx.bed")
        self.assertEqual(status, 400)  # private addresses are never fetched

    def test_page_and_its_address(self):
        status, content_type, page = self.request("GET", "/great/")
        self.assertIn('<meta name="genome-canvas-base" content="/">', page)
        status, content_type, _ = self.request("GET", "/great/styles.css")
        self.assertEqual((status, content_type.split(";")[0]), (200, "text/css"))
        gateway = {"X-Forwarded-Prefix": "/genome-canvas", "X-Great-Prefix": "/great", "Host": "lab:8892"}
        status, _, page = self.request("GET", "/great/", headers=gateway)
        self.assertIn('content="/genome-canvas/"', page)
        status, _, script = self.request("GET", "/great/rgreat.R", headers=gateway)
        self.assertIn('server <- "http://lab:8892/great"', script)
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        connection.request("GET", "/great/?track=x", headers={"X-Forwarded-Prefix": "/genome-canvas", "Host": "lab:8892"})
        response = connection.getresponse()
        self.assertEqual((response.status, response.getheader("Location")), (301, "http://lab:8892/great/?track=x"))
        connection.close()
        status, _, text = self.request("GET", "/api/great/status", headers={"X-Forwarded-Prefix": "/genome-canvas", "Host": "lab:8892"})
        self.assertEqual(json.loads(text)["greatBase"], "http://lab:8892/great")

    def test_page_api(self):
        status, _, text = self.request("GET", "/api/great/status")
        status_payload = json.loads(text)
        self.assertEqual([a["name"] for a in status_payload["assemblies"]], ["hg38"])
        self.assertFalse(status_payload["admin"])
        body, content_type = multipart({"assembly": "hg38", "fgText": "chrA:5,001-5,010\n", "fgName": "pasted"})
        status, _, text = self.request("POST", "/api/great/jobs", body, {"Content-Type": content_type})
        job = json.loads(text)["job"]
        self.assertEqual((job["name"], job["summary"]["regions"]), ("pasted", 1))
        self.assertNotIn("client", job)
        status, _, text = self.request("GET", "/api/great/jobs/{}/term?ontology=GOBiologicalProcess&term=T:1".format(job["id"]))
        self.assertEqual(json.loads(text)["byGene"][0]["gene"], "GENE1")
        status, _, _ = self.request("GET", "/api/great/settings")
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/great/jobs", body, {"Content-Type": content_type, "Origin": "http://evil"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
