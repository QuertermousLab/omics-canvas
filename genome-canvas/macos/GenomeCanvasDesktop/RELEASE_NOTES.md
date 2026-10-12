Native Genome Canvas Desktop builds for Apple Silicon and Intel Macs.

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
