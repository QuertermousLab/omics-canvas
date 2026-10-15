Native Mac apps for Apple Silicon and Intel Macs: **Omics Canvas 1.4.0** (spatial, single-cell and CRISPR viewers)
and **Genome Canvas 1.9.0** (genome browser).

## Omics Canvas 1.4.0

- **Server and port**: **Server…** (⌘,) sets the server address and port (default THOR, 171.65.68.140 port 8892)
- **SSH port forwarding** for servers you cannot reach directly — off campus without the VPN, or a server that only
  listens on 127.0.0.1: tick **Connect through SSH port forwarding** and give the SSH host (and port), your account and,
  optionally, your password (kept in the macOS Keychain; leave it empty to use your SSH key). The app opens the tunnel
  itself and reconnects after sleep or a network change; **Use Default** goes back to THOR
- The sidebar shows the connection, e.g. `171.65.68.140:8892 · SSH you@171.65.68.140`

## Genome Canvas 1.9.0

- **Server and port**: **Connect to Server…** (⌘,) — or the **Server** button — sets the server address and port (default THOR, 171.65.68.140 port 8892)
- **SSH port forwarding** for servers you cannot reach directly — off campus without the VPN, or a server that only
  listens on 127.0.0.1: tick **Connect through SSH port forwarding** and give the SSH host (and port), your account and,
  optionally, your password (kept in the macOS Keychain; leave it empty to use your SSH key). The app opens the tunnel
  itself and reconnects after sleep or a network change; **Use Default** goes back to THOR

## Omics Canvas 1.3.0

- **Shared datasets** (CRISPR explorer: **Shared screens**): the button under the dataset list, or **File → Shared
  Datasets…** (⇧⌘L), opens the lab's shared list: every dataset anyone has loaded, converted or combined in this
  viewer, with who shared it. Tick some and add them to your workspace (in a viewer: add & open); nothing is copied or
  converted again. **Not in my workspace** (on by default) leaves out what you already have
- The list follows the files: datasets whose converted files were deleted drop off; ones converted again or renamed by
  the person who added them are updated; a conversion whose source changed is marked *source changed*.
  **Shared by you** lists your own, with **Unshare**
- The same button is on the web pages: next to *Datasets*, under the viewers' dataset picker and next to the CRISPR
  explorer's screen picker

## Genome Canvas 1.8.0

- **Shared tracks**: a card under **Open server files**, or **File → Shared Tracks…** (⇧⌘L), lists every server file
  anyone in the lab has loaded (files in Home folders stay private) with its folder, format, genome and who loaded it.
  Filter, tick and **Load selected tracks**; **Only hg38** (the genome in view) and **Not in this view** are on by
  default. Deleted or moved files drop off the list, rewritten ones show their new date. Server mode only

## Omics Canvas 1.2.1

- The CRISPR explorer combines too: several screens in one dataset with a screen picker (⧉ button or right-click →
  **Combine Screens…**); each screen keeps its Genome Canvas links with enhancer–gene loops

## Omics Canvas 1.2.0

- **Combined datasets**: put samples of several datasets — time points, conditions, sections — into one dataset with
  one link and a sample picker (sidebar ⧉ button or right-click → **Combine Datasets…**). Nothing is copied
- The sidebar lists combined datasets and multi-sample projects as groups: click the arrow to show their samples,
  click a sample to open it. Datasets that went into a combined one are listed inside it, not on their own
- Right-click a dataset: **Edit Samples…** (add, remove, reorder and rename samples, also of existing projects such as
  Mouse Xenium 5K — the folder is not changed and the samples can be restored), **Rename…**, **Remove…**

## Genome Canvas 1.7.1

- Fixed: a Genome Canvas link (for example from the Omics Canvas CRISPR explorer) that started the app only opened
  the default view; the region, highlight and enhancer–gene loops now show when the link launches the app too

## Omics Canvas 1.1.1

- Fixed: links to Genome Canvas (CRISPR explorer: enhancer and gene buttons) opened the browser even with
  Genome Canvas.app installed. They now open in Genome Canvas.app (1.7.0 or later), also right after it was copied
  to Applications; with an older Genome Canvas.app they still open in the browser and the status line says to update it

## Omics Canvas 1.1.0

- **Tissue images** in the spatial viewer: H&E, IHC and other stains (and grey scans) under the spots, with black /
  white / gamma and opacity controls. Read from Seurat Visium objects, SpatialExperiment, h5ad `uns["spatial"]`,
  **Space Ranger output folders** (new: select `outs/` or a Visium HD bin folder) and post-Xenium H&E aligned in
  Xenium Explorer
- Seurat Visium (VisiumV2) spots are no longer transposed — convert such datasets again to fix older conversions
- CRISPR explorer → Genome Canvas: enhancer and gene links open the region, highlight the element and add the
  screen's enhancer → gene loops (significant and all tested links). In the app they open in **Genome Canvas.app**
  when it is installed, otherwise in the browser

## Genome Canvas 1.7.0

- Opens views from links: `genomecanvas://open?locus=…&genome=…&highlight=…&track=…` (and the same parameters on the
  web page); when a canvas is already open it jumps there in place and keeps your tracks
- Linked BEDPE tracks draw enhancer–gene loops with arc height from the score

## Download

| App | Apple Silicon (M1–M4) | Intel |
| --- | --- | --- |
| Genome Canvas | `GenomeCanvasDesktop-macOS-arm64.zip` | `GenomeCanvasDesktop-macOS-x86_64.zip` |
| Omics Canvas | `OmicsCanvasDesktop-macOS-arm64.zip` | `OmicsCanvasDesktop-macOS-x86_64.zip` |

Not sure which one? Choose  → **About This Mac**: "Chip: Apple …" means Apple Silicon; "Processor: Intel …" means Intel.

## First launch

The apps are ad-hoc signed, not notarized. After unzipping and moving an app to Applications, run once in Terminal:

```bash
xattr -dr com.apple.quarantine "/Applications/Genome Canvas.app"
xattr -dr com.apple.quarantine "/Applications/Omics Canvas.app"
```
