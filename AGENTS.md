# Omics Canvas — guide for agents and new sessions

Read this before changing anything. User documentation: [README.md](README.md). Genome Canvas has its
own detailed guide, [genome-canvas/AGENTS.md](genome-canvas/AGENTS.md) — follow it for anything in
`genome-canvas/` (cache-busting rules, decisions such as "track colors never change", macOS client).

## Layout and where it runs

- Repository https://github.com/QuertermousLab/omics-canvas (public; formerly `genome-canvas`), branch `main`.
- THOR (`quanyiz@171.65.68.140`): the checkout `/nfs/baldar/quanyiz/vizhub` **is the live deployment** of
  everything on port 8892 (gateway `gateway/apache.conf`, systemd user units `deploy/systemd/`, managed with
  `./vizhubctl`). `state/`, `hub/venv/`, `hub/static/`, `hub/config.json` and Genome Canvas's
  `genomecanvas.config.json` / `.genomecanvas/` are local and never committed.
- `hub/server.py` (FastAPI, 127.0.0.1:8893): home page, viewer pages, API, conversion queue.
  `hub/converters/` turn source files into the ATHENA viewer format (`convert.py` dispatches;
  `rds_export.R` reads R objects; `atac_to_viewer.py` builds accessibility tracks).
  `hub/web/` is the page source (TypeScript, esbuild): `./vizhubctl build` → `hub/static/`.
- `desktop/OmicsCanvasDesktop/`: Objective-C AppKit + WKWebView app (only compiles on macOS; CI builds it).

## Rules

- **Users' conversions run inside `vizhub-hub`.** Before `./vizhubctl restart hub`, check
  `GET /hub/api/jobs` (with the user's `X-Vizhub-Workspace`) — a restart interrupts them; they resume from
  their checkpoints, but the step in progress is redone. Web-only changes need no restart (`./vizhubctl build`).
- Datasets and jobs belong to a Genome Canvas workspace (`owner`); never list, rename or delete another
  workspace's datasets. Removing a dataset deletes files only in folders the hub generated (`*.vizhub/`).
- Never delete or overwrite source data on `/nfs`; conversions write to `<source>.vizhub/` next to it.
- Data colors in the viewers are data: change the interface theme tokens (`hub/web/src/theme.css`,
  Genome Canvas's Nord palette), not dataset colors.
- Commit identity (repo config): `Genome Canvas Contributors <genome-canvas@users.noreply.github.com>`.
  CI (`.github/workflows/ci.yml`) runs Genome Canvas's `npm test` and builds/type-checks the hub.

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
