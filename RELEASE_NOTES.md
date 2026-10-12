Native Mac apps for Apple Silicon and Intel Macs: **Genome Canvas 1.6.0** (genome browser) and the new
**Omics Canvas 1.0.0** (spatial, single-cell and CRISPR viewers). The repository is now
[QuertermousLab/omics-canvas](https://github.com/QuertermousLab/omics-canvas) (formerly genome-canvas).

## Omics Canvas 1.0.0 (new)

- One window for the lab server's **spatial viewer, single-cell viewer and CRISPR screen explorer**: switch
  viewers in the sidebar (⌘1 / ⌘2 / ⌘3), pick your workspace (the same as Genome Canvas), open your datasets,
  and add or convert new ones (⌘O)
- Multiome and scATAC: choose the assay (RNA, peaks, gene activity, chromVAR), chromatin accessibility tracks,
  chromVAR motif logos
- Export PNG with a transparent background (⇧⌘E); light and dark themes follow macOS (⇧⌘T to switch)
- Connects to `http://171.65.68.140:8892` (from outside Stanford, connect the VPN first); **Connect to Server…** (⌘,) for another address

## Genome Canvas 1.6.0

- Export PNG now has a transparent background (the light canvas palette is kept)
- On the lab server the web page links back to the Omics Canvas home page
- Data roots can pin their id, so share links keep working when a data folder moves

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
