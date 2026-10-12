# Omics Canvas

Self-hosted viewers for a lab's genomics data, in one place:

- **Genome Canvas** (`genome-canvas/`) — IGV.js-based genome and epigenome browser with a Python
  backend and a native macOS app (unchanged history; see [genome-canvas/README.md](genome-canvas/README.md)).
- **Omics Canvas hub** (`hub/`) — spatial viewer, single-cell viewer and CRISPR screen explorer
  (deck.gl viewers adapted from the lab's ATHENA Co-Lab), a home page, and a data manager that finds files
  on the server and converts `.h5ad`, `.h5mu`, Seurat / Signac / SingleCellExperiment `.rds`, 10x `.h5`,
  Xenium bundles and SCEPTRE / IGVF result tables into the viewer format — including multiome, scATAC and
  spatial ATAC (peaks, gene activity, chromVAR motifs, accessibility tracks from fragment files).
- **Omics Canvas for Mac** (`desktop/OmicsCanvasDesktop/`) — a native window for the three viewers,
  built like Genome Canvas Desktop.

Downloads (Apple Silicon / Intel): [Genome Canvas](https://github.com/QuertermousLab/omics-canvas/releases/latest/download/GenomeCanvasDesktop-macOS-arm64.zip)
([Intel](https://github.com/QuertermousLab/omics-canvas/releases/latest/download/GenomeCanvasDesktop-macOS-x86_64.zip)) ·
[Omics Canvas](https://github.com/QuertermousLab/omics-canvas/releases/latest/download/OmicsCanvasDesktop-macOS-arm64.zip)
([Intel](https://github.com/QuertermousLab/omics-canvas/releases/latest/download/OmicsCanvasDesktop-macOS-x86_64.zip)).
The apps are ad-hoc signed: after unzipping, run `xattr -dr com.apple.quarantine "/Applications/<App>.app"` once.

## Lab server deployment (THOR)

One address for everything: **http://171.65.68.140:8892/** (redirects to `/hub/`). The checkout
`/nfs/baldar/quanyiz/vizhub` is the live deployment.

| URL | What | Served by |
| --- | --- | --- |
| `/hub/` | Home page: the four tools | `hub/server.py` (127.0.0.1:8893) |
| `/spatial/` · `/spatial/<dataset>/` | Your spatial datasets (manage, add, convert) · the spatial viewer | hub |
| `/single-cell/` · `/single-cell/<dataset>/` | Your single-cell datasets · the single-cell viewer | hub |
| `/crispr/` · `/crispr/<screen>/` | Your CRISPR screens · the CRISPR screen explorer | hub |
| `/genome-canvas/` | Genome Canvas | `genome-canvas/server.py` (127.0.0.1:8000) |
| `/lab` and everything else | JupyterLab | `jupyter-lab.service` (127.0.0.1:8891) |

Viewer links take `?s=<slide>&assay=<assay>&gene=<gene>`; the explorer takes `?t=<target>&g=<gene>`.

Setting up a server: `cp hub/config.example.json hub/config.json` (and Genome Canvas's own config),
create `hub/venv` from `hub/requirements.txt`, `./vizhubctl build` (web pages), `./vizhubctl install`
(systemd user units in `deploy/systemd/`), `loginctl enable-linger $USER`.

## Layout

```
omics-canvas/            (on THOR: /nfs/baldar/quanyiz/vizhub)
  vizhubctl              status | start | stop | restart [hub|canvas|gateway] | logs | access | install | build
  gateway/apache.conf    the port-8892 Apache gateway (logs in gateway/logs/)
  deploy/systemd/        vizhub-hub, genome-canvas, vizhub-gateway (alias genome-canvas-gateway) + JupyterLab drop-in
  genome-canvas/         Genome Canvas (web app, server, macOS app; on THOR ~/baldar/IGV links here)
  hub/
    server.py config.json  backend (FastAPI) and its settings (config.example.json in git; roots, Rscript, jobs)
    converters/           convert.py (dispatch), detect.py, rds_export.R, crispr_to_viewer.py,
                          xenium_to_viewer.py / h5ad_to_viewer.py / igvf_to_viewer.py (from ATHENA)
    web/                  page sources (TypeScript; viewer code from QuertermousLab/athena) -> static/ via `./vizhubctl build`
    venv/                 Python 3.12 environment from requirements.txt (not in git)
  state/                 registry.json (datasets), jobs.json + jobs/<id>.log, converted/ (not in git)
  desktop/               Omics Canvas macOS app (spatial, single-cell, CRISPR; see desktop/OmicsCanvasDesktop/README.md)
```

## Look and workspaces

- All pages use Genome Canvas's Nord light / dark theme, fonts and controls (`hub/web/src/theme.css`), and share its theme
  setting (`localStorage` key `genome-canvas:theme`, same origin): the sun/moon button switches every tool.
- The viewers' plot background follows the theme (Genome Canvas's canvas color: white / `#2e3440`) unless a color is set
  under Display → Background (kept per browser; "Follow theme" goes back). PNG exports are always transparent; morphology
  image tiles carry alpha, so they look the same as before on black and stay transparent elsewhere.
- Datasets and conversions belong to a **workspace** — Genome Canvas's password-free workspaces (one per /home user,
  plus named ones). The chip in the top bar reads and sets Genome Canvas's selection through its API, so choosing a
  workspace in either app chooses it in both. Each workspace sees, renames and removes only its own datasets; a dataset
  link opens for anyone (marked "shared by …" in the viewer's list). Like Genome Canvas this is not access control.
- The hub backend stores `owner` / `ownerName` on every dataset and job (`state/registry.json`, `state/jobs.json`) and
  takes the workspace from the `X-Vizhub-Workspace` header, checked against Genome Canvas's `/api/workspaces`.

## Adding data

Open a viewer's own page (`/spatial/`, `/single-cell/`, `/crispr/`), browse `/nfs` (or paste a path) and select a file or
folder; the page only offers what that viewer reads and points to the right one otherwise:

- **Ready** (loaded as is, nothing written): a viewer slide folder (`slide.json`), a project folder (`project.json` + slides),
  a CRISPR screen folder, or a folder of those (e.g. `~/baldar/app/athena/*/data/view`).
- **Converted** into `<file>.vizhub/` next to the source, with progress shown under *Conversions*:
  `.h5ad` (spatial if `obsm["spatial"]`), Seurat v3–v5 / SingleCellExperiment `.rds` (Visium, Visium HD, Slide-seq and
  FOV/Xenium images become spatial), 10x `.h5`, `.loom`, Xenium output folders, and CRISPR results
  (`.tsv/.csv/.parquet` or an `.rds` sceptre_object / data.frame: SCEPTRE, IGVF, generic target/gene/log2FC/p).
  Raw integer counts are used when present (`layers["counts"]`, `raw.X`, `X`); otherwise values are shown as stored.
  A UMAP (+ Leiden clusters if there is no annotation) is computed when a single-cell dataset has no embedding.
  Selecting a source again reuses a finished conversion unless the source changed.
- Removing a dataset only unregisters it; the hub can also delete the `.vizhub` folder it generated (not while another
  workspace still uses it), never source data.

### Multiome and scATAC

- A Seurat object with several assays (RNA + Signac ATAC, SCT, gene activity, chromVAR, …) or a MuData `.h5mu`
  becomes one single-cell dataset with an **Assay** selector. Each assay is stored like the RNA matrix
  (`assays/<name>/expr.bin|expr.idx|features.json`, per-assay totals for log-normalization).
- **Every assay with a genomic position** (RNA, SCT, gene activity, peaks; not chromVAR): the ATHENA "Chromatin accessibility" panel (coverage per cell group, gene models,
  the object's peak set, "Color cells by this region"). Its data is built from the Signac fragment files
  (`Fragment@cells` maps cells to barcodes; several files are merged) and the object's gene annotation (or a GTF).
  This reads every fragment once — expect a long conversion for 50–100 GB aggregate files; it can be switched off
  under Advanced. Picking a peak moves the tracks to it and marks it; typing a gene opens its region.
- **chromVAR**: deviation z-scores on a diverging scale (`rdbu`, symmetric range), motifs listed by name
  (e.g. `CTCF (MA0139.1)`), and the motif's sequence logo from the Signac motif PWMs.
- R is the `sceptre` env's (SeuratObject 5); Signac does not need to be installed there (slots are read as attributes).

Command line (same as the hub runs): `hub/venv/bin/python hub/converters/convert.py SRC OUT --id ID [--kind spatial|singlecell|crispr]`.

### Conversion queue and resuming

- At most **8 conversions** run at once (`maxJobs` in `hub/config.json`); the others wait first-in, first-out and
  show their place in the queue. Each uses up to 8 processes for the fragment files (`fragmentWorkers`).
- Conversions run on THOR (`vizhub-hub` service, linger on): closing the page, the laptop sleeping or the VPN
  dropping does not stop them.
- **Checkpoints** in `<output>/.checkpoint/` and `.atac-work/`: the R export, the viewer files, and every finished
  chromosome of the accessibility tracks. If the hub or THOR restarts, interrupted conversions are queued again and
  continue from the last finished step (the card shows "resumed"). "Continue & load" on an unfinished conversion
  resumes it; "Convert again" / "Start over" (`--fresh`) and a changed source start from scratch.
- Long steps (more than ~30 min, e.g. 50–100 GB of fragments) show a notice with a rough time estimate and this
  resume behaviour.

## Notes

- Port 8892 has no login, as before (Genome Canvas already browsed `/nfs`): anyone who reaches it can view registered
  data and start conversions of files the `quanyiz` account can read. Writes go only to `<file>.vizhub/` and `state/`.
- R objects are read with the `sceptre` conda env's Rscript (SeuratObject 5, reads v4 and v5 objects), then system R
  (`config.json` → `rscript`).
- Genome Canvas's `./data` root pins `"id": "8b314e678286"` in `genomecanvas.config.json` (the id of its old path),
  so share links made before the move keep working (`server.py` reads the optional `id`).
- After editing `hub/web/src`: `./vizhubctl build` (no restart). After editing `hub/server.py` or `converters/`:
  `./vizhubctl restart hub` — running conversions are interrupted and then resume from their checkpoints.
- Every page has a small **Omics Canvas** button back to `/hub/` (Genome Canvas too, when served through the gateway).
