# Omics Canvas (macOS)

A native Mac window for Omics Canvas's **spatial viewer, single-cell viewer and CRISPR explorer**
(http://171.65.68.140:8892). Built like Genome Canvas Desktop (`../../genome-canvas/macos/GenomeCanvasDesktop`):
AppKit + WKWebView, the same Nord light/dark palette, fonts and buttons. Genome Canvas stays its own app;
links to it (and to the hub home page or JupyterLab) open in the web browser.

- **Sidebar**: the three viewers, your workspace (the same workspaces as Genome Canvas and the web pages),
  your datasets of the chosen viewer (click to open), **+** to add / convert / manage datasets (the viewer's
  web data page), **Shared datasets** / **Shared screens** (the pages' shared-library dialog, ⇧⌘L), the server status and address.
- **Toolbar**: Back, Datasets, the page title, Export PNG (transparent background), Open in Browser, theme, Reload.
- **Menus**: ⌘1 / ⌘2 / ⌘3 switch viewers, ⌘O datasets, ⇧⌘L shared datasets, ⇧⌘E export PNG, ⇧⌘B open in browser, ⌘R reload,
  ⇧⌘T toggle light/dark, ⌘, connect to another server address.
- The pages are opened with `?desktop=1`: they hide their own top bar and take the theme from the window
  (`window.VizHubDesktop` in `hub/web/src/common.ts` is the bridge; the page posts `page` / `workspace`
  messages to the `vizhub` script handler).
- There is no local mode: conversions and data stay on THOR. From outside Stanford, connect the VPN.

## Download

From the [latest release](https://github.com/QuertermousLab/omics-canvas/releases/latest):
[Apple Silicon](https://github.com/QuertermousLab/omics-canvas/releases/latest/download/OmicsCanvasDesktop-macOS-arm64.zip) ·
[Intel](https://github.com/QuertermousLab/omics-canvas/releases/latest/download/OmicsCanvasDesktop-macOS-x86_64.zip).
Unzip, move **Omics Canvas.app** to Applications and, because the app is ad-hoc signed (not notarized), run once:

```bash
xattr -dr com.apple.quarantine "/Applications/Omics Canvas.app"
```

## Build (on a Mac)

Needs Xcode Command Line Tools (`xcode-select --install`), macOS 13 or newer.

```bash
cd desktop/OmicsCanvasDesktop
./build.sh            # → dist/Omics Canvas.app and dist/OmicsCanvasDesktop-macOS.zip (ad-hoc signed)
open "dist/Omics Canvas.app"
```

GitHub Actions builds it too: a `mac-ui-*` branch gets window screenshots (`.github/workflows/macos-preview.yml`;
the runner cannot reach the lab server, so the window shows "Server unavailable"), and a `v*` tag releases
both Mac apps (`release-macos.yml`). `OMICS_CANVAS_SNAPSHOT_DIR=<dir>` makes the app save
`window-light.png` / `window-dark.png` after loading and quit.
