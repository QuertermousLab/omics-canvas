# Genome Canvas

Genome Canvas is a local-network genome browser for laboratories and research teams. It uses [IGV.js 3.8.5](https://github.com/igvteam/igv.js) as its browsing engine and provides secure server-directory browsing with HTTP Range support, so large BAM, CRAM, VCF, and Hi-C files can be viewed without uploading or copying them.

[Migration guide](docs/MIGRATION.md) · [Security](SECURITY.md)

## Features

- Common reference genomes including hg38, hg19, mm39, mm10, rn7, danRer11, dm6, and sacCer3
- Custom FASTA + FAI and 2bit reference genomes
- Direct access to configured server directories without file uploads
- Automatic discovery of `.bai`, `.crai`, `.tbi`, `.csi`, and `.fai` indexes
- Major formats including BAM, CRAM, VCF, BCF, BigWig, BigBed, BED, BEDGraph, WIG, GFF/GFF3, GTF, Hi-C contact heatmaps, BEDPE, SEG, MAF, GWAS, and QTL
- LocusZoom-style regional association plots for indexed GWAS summary statistics, with p-value guides, lead-variant labels, and complete variant tooltips
- Searchable public track-hub catalog with UCSC `hub.txt` and WashU JSON data-hub support
- Selective loading from ENCODE, GTEx, Roadmap Epigenomics, and 4DN hubs, plus custom hub URLs
- Automatic assay color families with deterministic sample-specific shades
- Wide UCSC/WashU-style track reorder grips with full-height drag targets
- PNG export of the current canvas
- LAN share links stored on this server
- Responsive layout, keyboard search (`/` or `Ctrl/⌘ K`), and zoom controls
- Nord light ("Snow Storm") and dark ("Polar Night") interface themes that follow the operating system, with a manual toggle in the top bar

## Screenshots

### Web browser

![Genome Canvas web interface in the Nord light theme with signal, association, Hi-C and gene tracks](docs/images/web-browser.png)

![Genome Canvas web interface in the Nord dark theme with the dark track canvas](docs/images/web-browser-dark.png)

### Native macOS app

![Genome Canvas native macOS application in the Nord light theme](docs/images/macos-app.png)

### Exported PNG

![Genome Canvas PNG export with Hi-C heatmap, interaction arcs and LD-colored association plot](docs/images/exported-view.png)

## Quick start

Requirements: Python 3.8 or newer. IGV.js is vendored, so the application does
not need npm or internet access at runtime.

```bash
git clone https://github.com/QuertermousLab/genome-canvas.git
cd genome-canvas
cp genomecanvas.config.example.json genomecanvas.config.json
./start.sh --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000` locally. Other devices on the same LAN can use:

```text
http://SERVER_IP:8000
```

The deployed file browser exposes the signed-in user's home directory, the shared `/nfs` namespace, and the project's `data` directory. Paths entered in the `SERVER PATH` field remain beneath the selected root; direct traversal outside it is rejected. Existing symbolic links located beneath an exposed root may point to shared storage elsewhere on the server, and linked track indexes are served through the same controlled namespace.

### Access Through the 8892 Gateway

The current deployment runs behind a user-level Apache gateway and reuses the already permitted `8892` port:

```text
http://SERVER_IP:8892/genome-canvas/
```

JupyterLab remains at `/lab` with its existing login. Genome Canvas uses a password-free workspace selector. Both backends listen only on `127.0.0.1` and are exposed through the gateway.

### Native macOS App

`macos/GenomeCanvasDesktop` contains a native AppKit desktop workbench for
macOS 13 or later. AppKit owns the toolbar, workspace and track sidebar,
drag-to-reorder list, navigation, highlights, sharing, export and connection
status. WebKit is used only for the IGV genome canvas and server-backed dialogs.
The native window uses the same Nord light/dark themes, fonts and sidebar layout
as the web interface; it follows the macOS appearance, with a manual choice in
the toolbar and **View → Appearance**.

The desktop app has two data-source modes:

- **This Mac** starts the backend bundled inside the app on a random loopback
  port. **Open Local Files** browses files readable by the current macOS user;
  no `/nfs` mount, upload, server installation, or workspace switching is
  required. Local Favorites are stored under `~/Library/Application Support/Genome Canvas`.
- **Server** connects to an existing Genome Canvas LAN deployment and retains
  its per-user/per-project workspace behavior.

Download a prebuilt app for your Mac:

- [Apple Silicon (arm64)](https://github.com/QuertermousLab/genome-canvas/releases/latest/download/GenomeCanvasDesktop-macOS-arm64.zip)
- [Intel (x86_64)](https://github.com/QuertermousLab/genome-canvas/releases/latest/download/GenomeCanvasDesktop-macOS-x86_64.zip)

The release archives are ad-hoc signed rather than Apple-notarized. If
Gatekeeper requests confirmation, Control-click the app and choose **Open**.
If macOS still blocks the app after you move it to `/Applications`, remove the
download quarantine attribute and open it again:

```bash
xattr -dr com.apple.quarantine "/Applications/Genome Canvas.app"
```

Only use this command for the app downloaded from this repository.

To build from source on a Mac:

```bash
cd macos/GenomeCanvasDesktop
./build.sh
open "dist/Genome Canvas.app"
```

Every launch defaults to **Server**. Enter its IP/port once and the app remembers
it for later launches. Choose **This Mac** or **Server** in the native
sidebar, or use **Genome Canvas → Use Local Backend** and **Genome Canvas →
Connect to Server…**. Entering remote mode automatically stops the local
backend. A server address may be a LAN hostname or a reverse-proxy path such as
`http://genome-canvas.example.internal/genome-canvas/`.

Home workspaces are discovered from the immediate subdirectories of `/home`. Selecting one adds that Home as the default file root. Manual workspaces can be created in the browser and default to the shared `/nfs` root. Each workspace has independent Favorites and file-browser history. Hiding an automatically discovered Home workspace only removes it from the selector; it never deletes the directory or its files, and it can be restored from the hidden-workspace section.

The locus search accepts genomic coordinates, gene symbols, and human rsIDs. `hg38` rsIDs are verified against the configured local dbSNP 156 VCF after their assembly placement is resolved. Configure the local file with `dbSnpHg38` in `genomecanvas.config.json`.

### Run as a Persistent User Service

The repository includes a user-level systemd service that continues running after terminal logout and starts with the user service manager:

```bash
systemctl --user link "$PWD/deploy/genome-canvas.service"
systemctl --user enable --now genome-canvas.service
```

Common management commands:

```bash
./genome-canvasctl start
./genome-canvasctl stop
./genome-canvasctl restart
./genome-canvasctl status
./genome-canvasctl monitor
./genome-canvasctl logs
```

`stop` only stops Genome Canvas and leaves the shared port-8892 gateway running
for JupyterLab. Use `gateway-stop` only when both sites may be interrupted. Run
`./genome-canvasctl help` for autostart, Apache access-log, and monitor options.

### Running Temporarily on Another Server

The project directory lives on shared NFS, so another lab server can run the
same checkout while the main server is unavailable. Start only the backend,
bound to localhost (the port-8892 Apache gateway is not required):

```bash
cd ~/baldar/IGV
./start.sh --host 127.0.0.1 --port 8000
```

Then forward the port from your own computer and open `http://localhost:8892/`:

```bash
ssh -N -L 8892:127.0.0.1:8000 quanyiz@TEMPORARY_SERVER
```

Share links created this way point at `localhost`, so they only work for people
using the same tunnel. Workspaces, Favorites and share links are stored in
`.genomecanvas/` on NFS and are visible to both servers; stop the temporary
instance before the main deployment resumes so only one process writes the
workspace database. No unit files, ports or paths need to change for the main
server at `http://171.65.68.140:8892/genome-canvas/`.

## Interface

The UI is plain HTML/CSS/ES modules with no build step. Interface colors are
CSS custom properties in `styles.css`: `:root` holds the light theme and
`html[data-theme="dark"]` the dark theme. Track colors are owned by
`track-colors.mjs` and the renderer modules and are identical in both themes.
In the dark theme the track canvas is Nord `#2e3440`: `canvas-theme.mjs` swaps
only canvas chrome (ruler, axes, labels, legends, empty Hi-C cells) and draws
neutral dark-gray features such as RefSeq genes in light gray so they stay
visible; colored tracks are unchanged. Export PNG always renders with the
light palette on a white background, whichever theme is active. Manrope, Fraunces and JetBrains Mono (SIL
OFL 1.1, licenses alongside the files) are bundled in `vendor/fonts/`, so no
font service is contacted. `public/favicon.svg` is the application logo; the
macOS client icon `macos/GenomeCanvasDesktop/Resources/GenomeCanvas-1024.png`
is rendered from the same design.

## Configure Server Data Directories

Copy the example configuration and edit its paths:

```bash
cp genomecanvas.config.example.json genomecanvas.config.json
```

`dataRoots` accepts multiple directories. The web application exposes only supported files within those roots. Directories can also be supplied temporarily through an environment variable; separate multiple Linux or macOS paths with colons:

```bash
GENOME_DATA_ROOTS=/data/project-a:/mnt/sequencing ./start.sh
```

Optional environment variables:

- `GENOME_CANVAS_HOST`: listening address; default `0.0.0.0`
- `GENOME_CANVAS_PORT`: listening port; default `8000`
- `GENOME_DATA_ROOTS`: permitted data roots
- `GENOME_CANVAS_REFERENCE_DIR`: local reference cache; default `.genomecanvas/references`

### Faster Reference Loading

Prepare the common assemblies once on the server:

```bash
export GENOME_CANVAS_REFERENCE_DIR="$HOME/.cache/genome-canvas/references"
python3 tools/cache_genomes.py --genomes hg38 hg19 mm10 mm39 --workers 8
./start.sh
```

Python 3.8+, `bgzip`, `tabix`, and `sort` are required for cache preparation. Downloads
run concurrently; completed downloads are reused on later runs. The four reference packs use about 3 GB;
the manifest is published only after all requested resources and indexes are ready.
The included user service uses the same cache path in the deployment user's Home.

Cached assemblies load reference sequences, chromosome sizes, aliases, cytobands,
and annotations from this server. RefGene files preserve the upstream annotation
records but are sorted, BGZF-compressed, and Tabix-indexed, so regional views load
only overlapping genes. Existing Favorites and shared views also use the cached
reference resources. Uncached genomes and arbitrary public tracks retain their
original URLs; this cache is not a general URL proxy.

Small reference files and compressed frontend assets use a bounded 256 MB memory
cache. Larger sequence files use local storage and the operating system's page
cache. Versioned frontend assets have browser caching; unversioned assets use
ETags. JSON responses and whole-file plain-text tracks (for example BED or
BEDPE without an index) are gzip encoded when the browser accepts it, and the
compressed copy is cached in memory; byte-range requests are never re-encoded.
Directory listings use `scandir` so large NFS folders only stat folders and
track files. PNG export embeds the Hi-C heatmap as a single raster image
instead of one SVG shape per contact, which makes exporting dense views about
five times faster with identical output. The page preloads IGV.js, its ES modules and the bundled fonts in
parallel, and requests the workspace configuration alongside the catalog. Workspace catalog reads are cached for ten seconds, with immediate
invalidation after application-managed workspace changes. Gene/rsID search,
BLAT, and loading new public hubs can still require public services.

## Docker Deployment

Edit the read-only volume in `docker-compose.yml` so `/data` points to the track directory, then run:

```bash
docker compose up -d --build
```

`.genomecanvas/sessions` stores shared views. Docker Compose persists it in the project directory by default.

## Index File Naming

Use these common naming conventions for automatic pairing:

| Data file | Automatically detected index |
| --- | --- |
| `sample.bam` | `sample.bam.bai` or `sample.bai` |
| `sample.cram` | `sample.cram.crai` or `sample.crai` |
| `sample.vcf.gz` | `sample.vcf.gz.tbi` or `.csi` |
| `sample.bed.gz` / GFF / GTF | matching `.tbi` or `.csi` |
| `study.gwas.gz` | `study.gwas.gz.tbi` or `.csi` |
| `genome.fa` | `genome.fa.fai` |

BigWig, BigBed, TDF, and `.hic` files are internally indexed. Hi-C contacts are rendered as a triangular, log-scaled heatmap at a resolution chosen for the current locus. Unindexed text tracks can also be loaded, but the first read of a large file may be significantly slower.

GWAS files are assumed to already match the selected reference assembly. Large
summary-statistics files should be coordinate-sorted, BGZF-compressed, and
Tabix-indexed; see `data/README.md` for the expected columns and commands. GWAS
tracks always mark the genome-wide threshold at `P = 5e-8`. When
`ldReferenceHg19` is configured and the input retains an `hg19_locus` column,
Genome Canvas calculates local unphased LD r² from the configured PLINK panel,
colors points by LD, and uses point shape to show beta direction.
For faster repeat viewing with a fixed lead SNP, `tools/precompute_gwas_ld.py`
writes a new indexed regional file with embedded r², lead and population
annotations. Embedded LD is read directly, without running PLINK. See
`data/README.md` for preparation commands and the fixed-reference limitations.
`tools/precompute_gwas_ld_genomewide.py` additionally discovers reference-panel
LD-clumped leads across the full input, computes lead-oriented LD windows,
and writes a complete annotated table, lead/locus catalogs, and an indexed
pair table. It preserves all original GWAS rows, including regions without LD.

## Public Hubs and URL Requirements

Open **Public Data** and choose **Public Track Hubs** to browse catalog entries compatible with the current assembly. Select a hub, filter its track index by sample, assay, or format, and load only the selected tracks. The custom hub field accepts a UCSC `hub.txt` URL or a WashU-style JSON data-hub URL. The **Single Track URL** tab remains available for direct BigWig, BAM, CRAM, VCF, Hi-C, and other supported files.

Remote descriptors and track files must allow cross-origin browser access with CORS. Large indexed files must also support HTTP Range requests. Genome Canvas does not proxy external URLs, and authorization tokens are not written to this server.

## Security Boundary

This is a LAN tool, not a multi-user account system. The backend only serves files within `dataRoots` and blocks path traversal. Any authenticated web user can read supported tracks visible in those roots. Do not expose the service directly to the public internet; use a VPN, reverse-proxy authentication, or firewall controls in sensitive environments.

## Verification

```bash
npm test
```

The tests use Python's standard library and Node.js built-in test runner and do not require internet access. CI runs them on every push. Native macOS interface changes can be previewed on a GitHub-hosted Mac by pushing a `mac-ui-*` branch, and pushing a `v*` tag publishes the macOS release; see the [macOS client README](macos/GenomeCanvasDesktop/README.md). IGV.js is distributed under the MIT License; its license is stored at `vendor/IGV-LICENSE.txt`. See the [official IGV.js documentation](https://igv.org/doc/igvjs/) for additional browser options and format details.
