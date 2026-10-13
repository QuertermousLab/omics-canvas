Native Mac apps for Apple Silicon and Intel Macs: **Omics Canvas 1.2.0** (spatial, single-cell and CRISPR viewers)
and **Genome Canvas 1.7.1** (genome browser).

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
