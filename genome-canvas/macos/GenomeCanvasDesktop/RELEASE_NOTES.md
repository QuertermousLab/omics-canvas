Native Genome Canvas Desktop builds for Apple Silicon and Intel Macs.

## What's new in 1.10.0

- **LocusCompare**: a button next to **Liftover** (or **File → LocusCompare…**, ⌥⌘C, or a GWAS track's right-click menu, or a SNP's popup) compares a GWAS track in view with GTEx eQTLs of one gene, as locuscompare.com does: each variant's GWAS and eQTL −log₁₀ P against each other and a LocusZoom panel for each, colored by LD with the lead SNP (1000 Genomes EUR, AFR, AMR, EAS, SAS or ALL, or the study's own LD). Pick the tissue, gene and region; click a point to make it the lead; download SVG, PNG or the table. Server mode only
- **Export PNG** (and every exported figure) is set in Arial
- **Liftover**: a button under **Public data** / **Reference** (or **File → Liftover…**, ⌥⌘L) converts pasted regions or the current view between hg19 ↔ hg38 and mm10 ↔ mm39 (click a result to go there, or copy them as BED), and track files in the view — BED, narrowPeak, bedGraph, bigWig, BAM, GTF/GFF, VCF, BEDPE — on the server
- **Genome build check**: a file whose header or name says it is on another build than the view asks before loading — convert it, load it as it is, or skip it
- **Tracksets**: groups of shared files under **Shared tracks**, loaded together as separate tracks or overlaid in one smooth layered track (e.g. **Layered H3K27ac (ENCODE)**, 7 cell lines). The stack button next to the track list's refresh (or **File → Save View as Trackset…**) saves the server tracks in view as one; the native track list shows a layered track's layer count. Server mode only
- **Shared Favorites**: **Share** on a Favorite lists it for the lab; others **Load** it or **Save a copy**

## What's new in 1.9.0

- **Connect to Server…** (⌘,) sets the server address and port (default 171.65.68.140 port 8892) and, optionally, **SSH port forwarding** for servers you cannot reach directly: the SSH host and port, your account and an optional password (kept in the macOS Keychain; empty: your SSH key). The app opens the tunnel itself and reconnects after sleep or a network change; **Use Default** goes back to the lab server

## What's new in 1.8.0

- **Shared tracks**: a sidebar card under **Open server files**, or **File → Shared Tracks…** (⇧⌘L), lists every server file anyone in the lab has loaded (files in Home folders stay private) with its folder, format, genome and who loaded it. Filter, tick and load several at once; **Only hg38** (the genome in view) and **Not in this view** are on by default. Deleted or moved files drop off the list and rewritten ones show their new date. Available when connected to a server (hidden in This Mac mode)

## What's new in 1.7.1

- Fixed: a `genomecanvas://` link that started the app only opened the default view; the region, highlight and linked tracks now show when the link launches the app too

## What's new in 1.7.0

- Opens views from `genomecanvas://open?locus=…&genome=…&highlight=…&track=name|url` links (the same parameters work on the web page): the Omics Canvas app and CRISPR explorer use them to show an enhancer with its enhancer–gene loops. When a canvas is already open the app jumps there in place and keeps the loaded tracks
- Linked BEDPE tracks are drawn as arcs whose height follows the score

## What's new in 1.6.0

- Export PNG now has a transparent background (the light canvas palette is kept), so figures drop onto slides and posters of any color
- Genome Canvas now lives in the [Omics Canvas](https://github.com/QuertermousLab/omics-canvas) repository, next to the new Omics Canvas app for spatial, single-cell and CRISPR data; on the lab server the web page links back to the Omics Canvas home page
- Data roots can pin their id in the server configuration, so share links keep working when a data folder moves

## What's new in 1.5.2

- Export PNG is about five times faster on dense views (for example a 2 Mb region with a Hi-C heatmap: 3.5 s → 0.7 s), with identical output
- Toolbar buttons such as Export PNG, Zoom and Clear no longer stay looking selected after a click

## What's new in 1.5.1

- Native window redesigned to match the web interface: Nord light and dark themes, the same fonts, and a sidebar with the same file, public-data, reference and track-list controls
- The appearance follows macOS automatically; choose Light or Dark with the toolbar button or **View → Appearance** (**Use System Setting** returns to automatic). The genome canvas switches with it
- Fixed **This Mac** mode, which showed a "404 Not found" page in 1.5.0
- Track rows show a remove button on hover; **Edit → Remove Selected Track** (⌘⌫) removes the selected one

## What's new in 1.5

- Redesigned Nord light and dark interface that follows the macOS appearance, with a dark track canvas in dark mode (track colors are unchanged; PNG exports stay white)
- New app icon
- Faster loading of large text tracks (gzip transfer) and server directory listings
- Local reference-genome cache and LD features in the bundled local backend, alongside remote-server mode

## Download

| Mac | File |
| --- | --- |
| Apple Silicon (M1/M2/M3/M4) | `GenomeCanvasDesktop-macOS-arm64.zip` |
| Intel | `GenomeCanvasDesktop-macOS-x86_64.zip` |

Not sure which one? Choose  → **About This Mac**: "Chip: Apple …" means Apple Silicon; "Processor: Intel …" means Intel.

## Install

1. Download the zip for your Mac and double-click it to unzip.
2. Move **Genome Canvas.app** to `/Applications`.
3. Open it. On first launch, enter the server address (for example `http://SERVER_IP:8892/genome-canvas/`) or choose **This Mac** to browse local files.

## If macOS says the app "is damaged" or "cannot be opened"

The app is ad-hoc signed rather than notarized by Apple, so Gatekeeper may block the downloaded copy. First try Control-click (or right-click) the app → **Open** → **Open**.

If macOS still refuses, remove the download quarantine flag in Terminal and open the app again:

```bash
xattr -dr com.apple.quarantine "/Applications/Genome Canvas.app"
```

If you kept the app somewhere else, use that path instead (you can drag the app onto the Terminal window to insert its path). Only run this for the app downloaded from this release page.

## Requirements

- macOS 13 Ventura or later
- Python 3 for **This Mac** mode (installed with the Xcode Command Line Tools: `xcode-select --install`)
- Network access to your Genome Canvas server for **Server** mode
