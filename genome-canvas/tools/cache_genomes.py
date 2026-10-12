#!/usr/bin/env python3
"""Prepare local IGV reference resources and regional RefGene indexes."""

import argparse
import concurrent.futures
import copy
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


GENOMES_URL = "https://igv.org/genomes/genomes3.json"
MAPPINGS_URL = "https://igv.org/data/url_mappings.tsv"
APP_DIR = Path(__file__).resolve().parents[1]
RESOURCE_FIELDS = (
    "twoBitURL", "twoBitBptURL", "chromSizesURL", "cytobandURL", "cytobandBbURL",
    "aliasURL", "chromAliasBbURL", "maneBbURL", "maneTrixURL", "rsdbURL",
)


def download(url, directory):
    basename = Path(urlparse(url).path).name
    name = "{}-{}".format(hashlib.sha256(url.encode()).hexdigest()[:16], basename)
    target = directory / name
    if target.is_file() and target.stat().st_size:
        return name, {"file": name, "source": url, "bytes": target.stat().st_size}
    for attempt in range(3):
        temporary = None
        try:
            request = Request(url, headers={"User-Agent": "GenomeCanvas-ReferenceCache/1.0"})
            with urlopen(request, timeout=120) as response:
                expected = response.headers.get("Content-Length")
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".partial", delete=False) as handle:
                    temporary = Path(handle.name)
                    shutil.copyfileobj(response, handle, length=1024 * 1024)
                if expected is not None and temporary.stat().st_size != int(expected):
                    raise OSError("Incomplete download: {}".format(url))
            temporary.replace(target)
            size = target.stat().st_size
            print("Cached {} ({:.1f} MB)".format(basename, size / 1000000), flush=True)
            return name, {"file": name, "source": url, "bytes": size}
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def index_refgene(source, directory, workers):
    basename = source.name[:-3] if source.name.endswith(".gz") else source.name
    name = basename + ".indexed.refgene.gz"
    target = directory / name
    index = Path(str(target) + ".tbi")
    if target.is_file() and index.is_file():
        return name, index.name
    if not shutil.which("bgzip") or not shutil.which("tabix"):
        raise RuntimeError("Install bgzip and tabix (HTSlib) to index RefGene annotations")
    with tempfile.TemporaryDirectory(dir=directory, prefix="refgene-") as staging:
        staging = Path(staging)
        raw = staging / "refgene.txt"
        with gzip.open(source, "rb") as reader, raw.open("wb") as writer:
            shutil.copyfileobj(reader, writer)
        sorted_path = staging / "sorted.txt"
        environment = dict(os.environ, LC_ALL="C")
        with sorted_path.open("wb") as writer:
            subprocess.run(
                ["sort", "-t", "\t", "-k3,3", "-k5,5n", str(raw)],
                stdout=writer, check=True, env=environment,
            )
        compressed = staging / name
        help_result = subprocess.run(["bgzip", "--help"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        compression_options = ["-@", str(workers)] if b"-@" in help_result.stdout else []
        with compressed.open("wb") as writer:
            subprocess.run(
                ["bgzip"] + compression_options + ["-c", str(sorted_path)],
                stdout=writer, check=True,
            )
        subprocess.run(
            ["tabix", "-0", "-s", "3", "-b", "5", "-e", "6", str(compressed)],
            check=True,
        )
        compressed.replace(target)
        Path(str(compressed) + ".tbi").replace(index)
    print("Indexed {}".format(source.name), flush=True)
    return name, index.name


def prepare(directory, requested, workers):
    directory.mkdir(parents=True, exist_ok=True)
    with urlopen(GENOMES_URL, timeout=30) as response:
        catalog = json.load(response)
    selected = [genome for genome in catalog if genome["id"] in requested]
    missing = set(requested) - {genome["id"] for genome in selected}
    if missing:
        raise ValueError("Unknown genome IDs: {}".format(", ".join(sorted(missing))))
    urls = {MAPPINGS_URL}
    for genome in selected:
        urls.update(genome[key] for key in RESOURCE_FIELDS if genome.get(key))
        for track in genome.get("tracks", []):
            urls.update(track[key] for key in ("url", "indexURL", "trixURL") if track.get(key))
    # IGV searches a .ix trix index together with its companion .ixx.
    urls.update(url + "x" for url in list(urls) if url.endswith(".ix"))
    assets, resources, indexed_tracks = {}, {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download, url, directory): url for url in sorted(urls)}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            name, record = future.result()
            assets[name] = record
            resources[url] = "/reference/" + name
    for url in urls:
        if url.endswith(".ix"):
            trix_name = resources[url].rsplit("/", 1)[-1]
            companion_name = resources[url + "x"].rsplit("/", 1)[-1]
            assets[trix_name + "x"] = assets[companion_name]
    mappings_path = directory / resources[MAPPINGS_URL].rsplit("/", 1)[-1]
    for line in mappings_path.read_text().splitlines():
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) == 2 and fields[1] in resources:
            resources[fields[0]] = resources[fields[1]]
    for genome in selected:
        for track in genome.get("tracks", []):
            if track.get("format") == "refgene" and track.get("indexed") is False:
                source = directory / resources[track["url"]].rsplit("/", 1)[-1]
                name, index_name = index_refgene(source, directory, workers)
                for asset_name in (name, index_name):
                    assets[asset_name] = {"file": asset_name, "bytes": (directory / asset_name).stat().st_size}
                indexed_tracks[track["url"]] = {
                    "url": "/reference/" + name,
                    "indexURL": "/reference/" + index_name,
                    "indexed": True,
                    "format": "refgene",
                }

    def localize(value):
        if isinstance(value, list):
            return [localize(item) for item in value]
        if isinstance(value, dict):
            result = {key: localize(item) for key, item in value.items()}
            if value.get("url") in indexed_tracks:
                result.update(indexed_tracks[value["url"]])
            return result
        return resources.get(value, value) if isinstance(value, str) else value

    definitions = copy.deepcopy(catalog)
    for index, genome in enumerate(definitions):
        if genome["id"] in requested:
            # Prefer 2bit; remove unused FASTA URLs from the cached definition.
            genome.pop("fastaURL", None)
            genome.pop("indexURL", None)
            definitions[index] = localize(genome)
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        previous_genomes = {genome["id"]: genome for genome in previous.get("genomes", [])}
        definitions = [
            previous_genomes.get(genome["id"], genome) if genome["id"] not in requested else genome
            for genome in definitions
        ]
        assets = {**previous.get("assets", {}), **assets}
        resources = {**previous.get("resources", {}), **resources}
        indexed_tracks = {**previous.get("indexedTracks", {}), **indexed_tracks}
        requested = sorted(set(previous.get("cachedGenomes", [])) | set(requested))
    manifest = {
        "version": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "cachedGenomes": requested,
        "genomes": definitions,
        "assets": assets,
        "resources": resources,
        "indexedTracks": indexed_tracks,
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
        json.dump(manifest, handle, separators=(",", ":"))
        temporary = Path(handle.name)
    temporary.replace(manifest_path)
    print("Ready: {} in {}".format(", ".join(requested), directory), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genomes", nargs="+", default=["hg38"])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get(
        "GENOME_CANVAS_REFERENCE_DIR", str(APP_DIR / ".genomecanvas" / "references")
    )))
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    prepare(args.cache_dir.expanduser().resolve(), args.genomes, args.workers)


if __name__ == "__main__":
    main()
