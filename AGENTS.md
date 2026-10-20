# Omics Canvas — guide for agents and new sessions

Read this before changing anything. User documentation: [README.md](README.md). Genome Canvas has its
own detailed guide, [genome-canvas/AGENTS.md](genome-canvas/AGENTS.md) — follow it for anything in
`genome-canvas/` (cache-busting rules, decisions such as "track colors never change", macOS client).

## Layout and where it runs

- Repository https://github.com/QuertermousLab/omics-canvas (public; formerly `genome-canvas`), branch `main`.
- THOR (`quanyiz@171.65.68.140`): the checkout `/nfs/baldar/quanyiz/vizhub` **is the live deployment** of
  everything on port 8892 (gateway `gateway/apache.conf`, systemd user units, managed with `./vizhubctl`). Server
  settings live in `deploy/site.env` (local; defaults `deploy/site.env.example` = THOR): edit the templates
  `gateway/apache.conf.in` and `deploy/systemd/*.in`, never the generated `gateway/apache.conf` or
  `deploy/generated/`; `./vizhubctl configure --yes` regenerates, `./vizhubctl install` relinks the units. `state/`, `hub/venv/`, `hub/static/`, `hub/config.json` and Genome Canvas's
  `genomecanvas.config.json` / `.genomecanvas/` are local and never committed.
- `hub/server.py` (FastAPI, 127.0.0.1:8893): home page, viewer pages, API, conversion queue.
  `hub/converters/` turn source files into the ATHENA viewer format (`convert.py` dispatches;
  `rds_export.R` reads R objects; `atac_to_viewer.py` builds accessibility tracks).
  `hub/web/` is the page source (TypeScript, esbuild): `./vizhubctl build` → `hub/static/`.
- `desktop/OmicsCanvasDesktop/`: Objective-C AppKit + WKWebView app (only compiles on macOS; CI builds it).
  `desktop/Shared/OCConnection.{h,m}` (compiled into both Mac apps): server, port and optional SSH port forwarding
  (password in the Keychain, handed to ssh by the app itself as `SSH_ASKPASS`); `macos-connection.yml` tests it
  against the runner's own SSH server.

## Rules

- **Users' conversions run inside `vizhub-hub`.** Before `./vizhubctl restart hub`, check
  `GET /hub/api/jobs` (with the user's `X-Vizhub-Workspace`) — a restart interrupts them; they resume from
  their checkpoints, but the step in progress is redone. Web-only changes need no restart (`./vizhubctl build`).
- Datasets and jobs belong to a Genome Canvas workspace (`owner`); never list, rename or delete another
  workspace's datasets. Removing a dataset deletes files only in folders the hub generated (`*.vizhub/`).
  The exception by design is the shared library (`state/library.json`, the "Shared datasets" dialog in
  `hub/web/src/shared.ts`, opened from every viewer and dataset page; `/<viewer>/#shared` opens it): every dataset any
  workspace loaded, converted or combined, keyed by folder (`combined:<id>` for combined ones), listed for everyone
  and added to a workspace with `/hub/api/library/add` (a combined one becomes a copy with `fromLibrary`). Its entries
  follow the files (`library_sync`): call `record_library(d)` whenever a dataset is created or changed. Genome
  Canvas's counterpart is the `shared_tracks` table (files loaded from shared data roots, never Home folders).
- Combined datasets (`combined: true`, `root: "combined:<id>"`) exist only in `state/registry.json`: each slide has its
  own `root` (a slide folder of another dataset). Use `slide_dir()` for slide folders, never `root / dir`.
- CRISPR screen reports (HTML pages opened from the explorer) are kept per data folder in `state/reports.json`
  (`add` / `hide` lists) and also found next to a screen's source (`found_reports`); `/hub/api/reports/<id>/…` serves
  a report's own folder only.
- Never delete or overwrite source data on `/nfs`; conversions write to `<source>.vizhub/` next to it.
- Data colors in the viewers are data: change the interface theme tokens (`hub/web/src/theme.css`,
  Genome Canvas's Nord palette), not dataset colors.
- Commit identity (repo config): `Genome Canvas Contributors <genome-canvas@users.noreply.github.com>`.
  CI (`.github/workflows/ci.yml`) runs Genome Canvas's `npm test` and builds/type-checks the hub.

## Documentation

[docs/TUTORIAL.md](docs/TUTORIAL.md) is the user guide; its screenshots (`docs/images/`) show **public data only**
(the repository is public). Web shots: a temporary workspace on THOR with public datasets, headless Chromium. Mac app
shots: `.github/workflows/docs-screenshots.yml` (push a `docs-shots-*` branch) runs a test server on the macOS runner
with public 10x data (`gateway/dev_gateway.py` stands in for Apache) and uploads them as an artifact.

## Releases

A `v*` tag builds both Mac apps for arm64 and x86_64 (`.github/workflows/release-macos.yml`) and publishes
`GenomeCanvasDesktop-macOS-<arch>.zip` and `OmicsCanvasDesktop-macOS-<arch>.zip` (fixed names: README
download links use `releases/latest/download/`). Before tagging: bump `CFBundleShortVersionString` /
`CFBundleVersion` in both `Resources/Info.plist` files (Genome Canvas's version is also asserted in
`genome-canvas/tests/test_macos_desktop.py`), and update `RELEASE_NOTES.md` (root, the release text) and
`genome-canvas/macos/GenomeCanvasDesktop/RELEASE_NOTES.md`. Push a `mac-ui-*` branch to get window
screenshots of both apps (`macos-preview.yml`) before releasing interface changes; it also starts Genome Canvas
from a `genomecanvas://` link (`link/` screenshots). The apps only compile on macOS: push the branch before tagging
any change to `main.m`. Note `NSURL.path` drops a trailing slash (match `NSURLComponents.path`), and a link that
launches an app arrives in `application:openURLs:` before `applicationDidFinishLaunching:`.
