import { assayTypeForTrack, automaticTrackColor } from "./track-colors.mjs?v=20260821.4";
import { installGradientSignalRenderer } from "./signal-style.mjs?v=20261007.2";
import { installRefSeqAllStyle } from "./annotation-style.mjs?v=20260821.3";
import { configureHicHeatmap, installHicHeatmapRenderer } from "./hic-heatmap.mjs?v=20261008.1";
import { installManhattanRenderer } from "./manhattan-style.mjs?v=20261007.2";
import { DARK_CANVAS, LIGHT_CANVAS, canvasTheme, installDarkCanvasAdapter, setCanvasTheme } from "./canvas-theme.mjs?v=20261007.2";
import { normalizeReferenceResources } from "./reference-resources.mjs?v=20261005.1";
import {
  DEFAULT_HIGHLIGHT_COLOR,
  HIGHLIGHT_FILL_ALPHA,
  HIGHLIGHT_OUTLINE_ALPHA,
  genomicExtentFromPixels,
  hexToRgba,
  normalizeHexColor,
  linkedView,
  normalizeHighlights,
  parseRegion,
  visibleHighlightPixels,
} from "./highlights.mjs?v=20261012.3";
import {
  PUBLIC_HUBS,
  filterHubTracks,
  hubsForGenome,
  inferHubKind,
  normalizeWashUHub,
} from "./public-hubs.mjs?v=20260824.3";

// Must run before IGV.js paints its first canvas.
installDarkCanvasAdapter();

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const appBaseURL = new URL(".", document.baseURI);

function appURL(path = "") {
  return new URL(String(path).replace(/^\/+/, ""), appBaseURL).toString();
}

function localDataURL(url) {
  if (typeof url !== "string") return url;

  let parsed;
  const dataPrefix = `${appBaseURL.pathname.replace(/\/$/, "")}/data/`;
  if (url.startsWith("/data/")) {
    parsed = new URL(appURL(`data/${url.slice("/data/".length)}`));
  } else {
    try {
      parsed = new URL(url, appBaseURL);
    } catch {
      return url;
    }
    if (parsed.origin !== appBaseURL.origin || !parsed.pathname.startsWith(dataPrefix)) return url;
  }

  let dataPath = parsed.pathname.slice(dataPrefix.length);
  const workspaceId = state.config?.workspaceId;
  const hasWorkspaceScope = /^(?:home|manual)-[a-f0-9]{18}\//.test(dataPath);
  if (workspaceId && !hasWorkspaceScope) {
    dataPath = `${encodeURIComponent(workspaceId)}/${dataPath}`;
  }
  parsed.pathname = `${appBaseURL.pathname.replace(/\/$/, "")}/data/${dataPath}`;
  return parsed.toString();
}

function normalizeTrackURLs(config) {
  const normalized = normalizeReferenceResources(config, state.config, appBaseURL);
  if (normalized.url) normalized.url = localDataURL(normalized.url);
  if (normalized.indexURL) normalized.indexURL = localDataURL(normalized.indexURL);
  return normalized;
}

function normalizeReferenceURLs(reference) {
  const normalized = normalizeReferenceResources(reference, state.config, appBaseURL);
  for (const key of ["fastaURL", "indexURL", "twoBitURL", "cytobandURL", "aliasURL", "chromSizesURL", "twoBitBptURL", "chromAliasBbURL", "cytobandBbURL", "maneBbURL", "maneTrixURL", "rsdbURL"]) {
    if (normalized[key]) normalized[key] = localDataURL(normalized[key]);
  }
  return normalized;
}

function normalizeBrowserConfigURLs(config) {
  const normalized = { ...config };
  if (Array.isArray(config?.tracks)) normalized.tracks = config.tracks.map(normalizeTrackURLs);
  if (config?.reference && typeof config.reference === "object") {
    normalized.reference = normalizeReferenceURLs(config.reference);
  }
  return normalized;
}

const ui = {
  appName: $("#app-name"),
  workspace: $("#workspace"),
  viewer: $("#igv-container"),
  loading: $("#viewer-loading"),
  genome: $("#genome-select"),
  locusForm: $("#locus-form"),
  locus: $("#locus-input"),
  currentLocus: $("#current-locus"),
  assembly: $("#assembly-badge"),
  viewSpan: $("#view-span"),
  trackList: $("#track-list"),
  trackEmpty: $("#track-empty"),
  trackCount: $("#track-count"),
  trackSummary: $("#track-summary"),
  statusText: $("#status-text"),
  statusDot: $("#status-dot"),
  rootSelect: $("#root-select"),
  breadcrumbs: $("#breadcrumbs"),
  fileBrowser: $("#file-browser"),
  serverPath: $("#server-path-input"),
  selectionSummary: $("#selection-summary"),
  loadSelected: $("#load-selected-button"),
  selectAllFiles: $("#select-all-files-button"),
  clearSelectedFiles: $("#clear-selected-files-button"),
  sharedBrowser: $("#shared-browser"),
  sharedSearch: $("#shared-search"),
  sharedGenomeOnly: $("#shared-genome-only"),
  sharedNewOnly: $("#shared-new-only"),
  sharedGenomeLabel: $("#shared-genome-label"),
  sharedSelectionSummary: $("#shared-selection-summary"),
  loadShared: $("#load-shared-button"),
  sharedSelectAll: $("#shared-select-all-button"),
  sharedClear: $("#shared-clear-button"),
  shareUrl: $("#share-url"),
  toastRegion: $("#toast-region"),
  hubCatalog: $("#hub-catalog"),
  hubCatalogSearch: $("#hub-catalog-search"),
  hubTrackSearch: $("#hub-track-search"),
  hubTrackEmpty: $("#hub-track-empty"),
  hubTrackBrowser: $("#hub-track-browser"),
  hubTrackList: $("#hub-track-list"),
  hubTrackStatus: $("#hub-track-status"),
  loadHubTracks: $("#load-hub-tracks"),
  hubPrevPage: $("#hub-prev-page"),
  hubNextPage: $("#hub-next-page"),
  profileName: $("#profile-name"),
  profileList: $("#profile-list"),
  igvShell: $(".igv-shell"),
  highlightLayer: $("#highlight-layer"),
  highlightMode: $("#highlight-mode-button"),
  highlightColor: $("#highlight-color-input"),
  clearHighlights: $("#clear-highlights-button"),
  workspaceGate: $("#workspace-gate"),
  workspaceSearch: $("#workspace-search"),
  homeWorkspaceList: $("#home-workspace-list"),
  manualWorkspaceList: $("#manual-workspace-list"),
  hiddenWorkspaceList: $("#hidden-workspace-list"),
  hiddenWorkspaces: $("#hidden-workspaces"),
  workspaceName: $("#workspace-name"),
};

const state = {
  browser: null,
  config: null,
  authenticatedUser: "genome",
  currentGenome: "hg38",
  currentRoot: "",
  currentPath: "",
  selectedFiles: new Map(),
  currentFileEntries: [],
  sharedTracks: [],
  selectedShared: new Map(),
  centerGuide: false,
  syncTimer: null,
  reorderObserver: null,
  publicDataTab: "hubs",
  activeHub: null,
  publicHubTracks: [],
  selectedHubTracks: new Set(),
  selectedHubTrackConfigs: new Map(),
  hubRequestController: null,
  hubRequestToken: 0,
  hubPageOffset: 0,
  hubPageTotal: 0,
  hubPageLimit: 200,
  hubSearchTimer: null,
  highlightMode: false,
  highlights: [],
  highlightDraft: null,
  highlightDrag: null,
  highlightRenderFrame: null,
  highlightResizeObserver: null,
  workspace: null,
  workspaceCatalog: [],
  hiddenHomeWorkspaces: [],
  eventsBound: false,
};

const desktopMode = document.documentElement.classList.contains("desktop-embedded");
let desktopStateTimer = null;

function desktopTrackViews() {
  return trackViews().filter((view) => view.track?.id !== "ideogram");
}

function desktopStateSnapshot() {
  const frames = state.browser?.referenceFrameList;
  const loci = Array.isArray(frames) && frames.length
    ? frames.map((frame) => frame.getLocusString?.() || frame.label).filter(Boolean)
    : state.browser?.currentLoci?.();
  const locus = Array.isArray(loci) ? loci.join(" ") : (loci || ui.locus.value || "");
  return {
    type: "state",
    ready: Boolean(state.browser),
    localMode: Boolean(state.config?.localMode),
    workspace: state.workspace ? { id: state.workspace.id, name: state.workspace.name, kind: state.workspace.kind } : null,
    workspaces: state.workspaceCatalog.map((workspace) => ({
      id: workspace.id,
      name: workspace.name,
      kind: workspace.kind,
    })),
    genome: state.currentGenome,
    locus,
    status: ui.statusText.textContent || "Ready",
    statusMode: ui.statusDot.classList.contains("error") ? "error" : (ui.statusDot.classList.contains("ready") ? "ready" : "busy"),
    highlightMode: state.highlightMode,
    highlightColor: normalizeHexColor(ui.highlightColor.value, DEFAULT_HIGHLIGHT_COLOR),
    highlightCount: state.highlights.length,
    tracks: desktopTrackViews().map(({ track }, index) => ({
      index,
      name: track.name || track.id || "Untitled track",
      format: track.config?.format || track.format || track.type || "track",
      color: trackColor(track),
      height: Number(track.height || track.config?.height || 0),
    })),
  };
}

function postDesktopMessage(message) {
  if (!desktopMode) return;
  try {
    window.webkit?.messageHandlers?.genomeCanvas?.postMessage(message);
  } catch {
    // Running in a regular browser or an older desktop client is harmless.
  }
}

function notifyDesktopHost() {
  if (!desktopMode) return;
  window.clearTimeout(desktopStateTimer);
  desktopStateTimer = window.setTimeout(() => postDesktopMessage(desktopStateSnapshot()), 30);
}

const TRACK_REORDER_COLUMN_WIDTH = 34;
const IGV_TRACK_REORDER_COLUMN_WIDTH = 12;

const TRACK_REORDER_CSS = `
  .igv-track-drag-column {
    box-sizing: border-box !important;
    width: 34px !important;
    min-width: 34px !important;
    flex: 0 0 34px !important;
    padding: 0 5px !important;
    align-items: center !important;
    background: rgb(249, 250, 251) !important;
    border-left: 1px solid rgb(228, 231, 235) !important;
  }

  .igv-track-drag-column > .igv-track-drag-handle {
    box-sizing: border-box !important;
    width: 24px !important;
    min-width: 24px !important;
    position: relative !important;
    cursor: grab !important;
    touch-action: none !important;
    border: 0 !important;
    border-radius: 7px !important;
    background: transparent !important;
    transition: background-color 120ms ease, box-shadow 120ms ease !important;
  }

  .igv-track-drag-column > .igv-track-drag-handle::before {
    content: "";
    position: absolute;
    top: 50%;
    left: 50%;
    width: 4px;
    height: 4px;
    transform: translate(-6px, -50%);
    border-radius: 50%;
    color: rgb(123, 133, 145);
    background: currentColor;
    box-shadow: 0 -8px 0 currentColor, 0 8px 0 currentColor,
      8px -8px 0 currentColor, 8px 0 0 currentColor, 8px 8px 0 currentColor;
    opacity: 0.72;
    transition: opacity 120ms ease, transform 120ms ease;
  }

  .igv-track-drag-column > .igv-track-drag-handle:hover,
  .igv-track-drag-column > .igv-track-drag-handle.igv-track-drag-handle-hover-color {
    background: rgb(241, 243, 245) !important;
    box-shadow: inset 0 0 0 1px rgb(208, 213, 220) !important;
  }

  .igv-track-drag-column > .igv-track-drag-handle:hover::before,
  .igv-track-drag-column > .igv-track-drag-handle.igv-track-drag-handle-hover-color::before {
    opacity: 1;
    transform: translate(-6px, -50%) scale(1.08);
  }

  .igv-track-drag-column > .igv-track-drag-handle:active {
    cursor: grabbing !important;
    background: rgb(223, 231, 252) !important;
    box-shadow: inset 0 0 0 2px rgb(62, 99, 221) !important;
  }

  .igv-track-drag-column > .igv-track-drag-handle.igv-track-drag-handle-selected-color {
    background: rgb(237, 242, 254) !important;
    box-shadow: inset 0 0 0 1px rgb(62, 99, 221) !important;
  }

  .igv-track-drag-column > .igv-track-drag-shim {
    box-sizing: border-box !important;
    width: 24px !important;
    min-width: 24px !important;
    background: transparent !important;
  }

  .igv-container.genome-canvas-highlight-mode .igv-viewport {
    cursor: crosshair !important;
  }

  .igv-container.genome-canvas-dark { color: rgb(216, 222, 233); }
  .igv-container.genome-canvas-dark .igv-track-label {
    border-color: rgb(76, 86, 106) !important;
    background-color: rgba(46, 52, 64, 0.88) !important;
    color: rgb(229, 233, 240) !important;
  }
  .igv-container.genome-canvas-dark .igv-track-label:hover { background-color: rgb(59, 66, 82) !important; }
  .igv-container.genome-canvas-dark .igv-track-drag-column {
    background: rgb(42, 48, 59) !important;
    border-left-color: rgb(59, 66, 82) !important;
  }
  .igv-container.genome-canvas-dark .igv-track-drag-column > .igv-track-drag-handle::before { color: rgb(136, 146, 163); }
  .igv-container.genome-canvas-dark .igv-gear-menu-column > div { background: transparent !important; }
  .igv-container.genome-canvas-dark .igv-gear-menu-column svg { opacity: 0; }
  .igv-container.genome-canvas-dark .igv-track-drag-column > .igv-track-drag-handle:hover,
  .igv-container.genome-canvas-dark .igv-track-drag-column > .igv-track-drag-handle.igv-track-drag-handle-hover-color {
    background: rgb(59, 66, 82) !important;
    box-shadow: inset 0 0 0 1px rgb(76, 86, 106) !important;
  }
  .igv-container.genome-canvas-dark .igv-zoom-in-notice-container,
  .igv-container.genome-canvas-dark .igv-zoom-in-notice div { background-color: rgba(46, 52, 64, 0.9) !important; }
  .igv-container.genome-canvas-dark .igv-zoom-in-notice-container > div,
  .igv-container.genome-canvas-dark .igv-zoom-in-notice div { color: rgb(216, 222, 233) !important; }
  .igv-container.genome-canvas-dark .igv-menu-popup,
  .igv-container.genome-canvas-dark .igv-track-label-popover,
  .igv-container.genome-canvas-dark .igv-track-label-popover__body {
    border-color: rgb(76, 86, 106) !important;
    background: rgb(59, 66, 82) !important;
    color: rgb(236, 239, 244) !important;
  }
  .igv-container.genome-canvas-dark .igv-menu-popup-header,
  .igv-container.genome-canvas-dark .igv-track-label-popover__header {
    border-bottom-color: rgb(76, 86, 106) !important;
    background-color: rgb(67, 76, 94) !important;
  }
  .igv-container.genome-canvas-dark .igv-menu-popup-shim:hover { background-color: rgb(76, 86, 106) !important; }
`;

function installTrackReorderUI() {
  const shadowRoot = ui.viewer.shadowRoot;
  if (!shadowRoot) return;

  let style = shadowRoot.getElementById("genome-canvas-track-reorder-style");
  if (!style) {
    style = document.createElement("style");
    style.id = "genome-canvas-track-reorder-style";
    style.textContent = TRACK_REORDER_CSS;
    shadowRoot.append(style);
  }

  const decorateHandles = (root = shadowRoot) => {
    const handles = [];
    if (root.matches?.(".igv-track-drag-handle")) handles.push(root);
    handles.push(...(root.querySelectorAll?.(".igv-track-drag-handle") || []));
    for (const handle of handles) {
      handle.title = "Drag to reorder track";
      handle.setAttribute("aria-label", "Drag to reorder track");
    }
  };
  decorateHandles();

  state.reorderObserver?.disconnect();
  state.reorderObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) decorateHandles(node);
      }
    }
  });
  state.reorderObserver.observe(shadowRoot, { childList: true, subtree: true });

  if (state.browser && !state.browser.__genomeCanvasReorderGeometry) {
    const originalCalculateViewportWidth = state.browser.calculateViewportWidth;
    state.browser.calculateViewportWidth = function calculateGenomeCanvasViewportWidth(columnCount) {
      const width = originalCalculateViewportWidth.call(this, columnCount);
      return Math.max(80, width - (TRACK_REORDER_COLUMN_WIDTH - IGV_TRACK_REORDER_COLUMN_WIDTH));
    };
    state.browser.__genomeCanvasReorderGeometry = true;
  }
}

const HIGHLIGHT_EXCLUDED_TRACK_TYPES = new Set(["ruler", "sequence", "ideogram"]);

function positionTrackSettingsMenu(trackView, event) {
  const popover = trackView?.trackGearPopup?.popover;
  if (!popover || !event) return;
  const margin = 8;
  Object.assign(popover.style, {
    position: "fixed",
    right: "auto",
    left: "0px",
    top: "0px",
    zIndex: "10000",
  });
  const bounds = popover.getBoundingClientRect();
  const left = Math.max(margin, Math.min(event.clientX + 6, window.innerWidth - bounds.width - margin));
  const below = event.clientY + 6;
  const top = below + bounds.height <= window.innerHeight - margin
    ? below
    : Math.max(margin, event.clientY - bounds.height - 6);
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function presentTrackSettings(trackView, event) {
  if (!trackView?.trackGearPopup || !state.browser?.menuUtils) return;
  state.browser.menuPopup?.hide?.();
  for (const otherView of state.browser.trackViews || []) {
    if (otherView !== trackView && otherView.trackGearPopup?.popover) otherView.trackGearPopup.popover.style.display = "none";
  }
  const menuItems = state.browser.menuUtils.trackMenuItemList(trackView);
  trackView.trackGearPopup.presentMenuList(trackView, menuItems, state.browser.config);
  positionTrackSettingsMenu(trackView, event);
}

function viewportMouseX(event, viewportElement) {
  const rect = viewportElement.getBoundingClientRect();
  return Math.max(0, Math.min(rect.width, event.clientX - rect.left));
}

function cancelHighlightDrag() {
  document.removeEventListener("mousemove", moveHighlightDrag, true);
  document.removeEventListener("mouseup", finishHighlightDrag, true);
  state.highlightDrag = null;
  state.highlightDraft = null;
  scheduleHighlightRender();
}

function beginHighlightDrag(event, viewport) {
  if (!state.highlightMode || event.button !== 0 || !viewport?.referenceFrame) return;
  if (String(viewport.referenceFrame.chr).toLowerCase() === "all") {
    toast("Choose a chromosome before creating a highlight", "error");
    return;
  }
  event.preventDefault();
  event.stopImmediatePropagation();
  cancelHighlightDrag();
  const viewportElement = viewport.viewportElement;
  state.highlightDrag = {
    viewport,
    startX: viewportMouseX(event, viewportElement),
    color: normalizeHexColor(ui.highlightColor.value),
  };
  document.addEventListener("mousemove", moveHighlightDrag, true);
  document.addEventListener("mouseup", finishHighlightDrag, true);
}

function moveHighlightDrag(event) {
  const drag = state.highlightDrag;
  if (!drag) return;
  event.preventDefault();
  event.stopPropagation();
  const viewportElement = drag.viewport.viewportElement;
  const currentX = viewportMouseX(event, viewportElement);
  const extent = genomicExtentFromPixels(
    drag.viewport.referenceFrame,
    drag.startX,
    currentX,
    viewportElement.getBoundingClientRect().width,
  );
  state.highlightDraft = extent ? { ...extent, color: drag.color, id: "draft" } : null;
  scheduleHighlightRender();
}

function finishHighlightDrag(event) {
  const drag = state.highlightDrag;
  if (!drag) return;
  event.preventDefault();
  event.stopPropagation();
  const viewportElement = drag.viewport.viewportElement;
  const currentX = viewportMouseX(event, viewportElement);
  const extent = genomicExtentFromPixels(
    drag.viewport.referenceFrame,
    drag.startX,
    currentX,
    viewportElement.getBoundingClientRect().width,
  );
  cancelHighlightDrag();
  if (!extent) return;
  const highlight = {
    ...extent,
    id: `highlight-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    color: drag.color,
  };
  state.highlights.push(highlight);
  scheduleHighlightRender();
  setStatus("Highlight added · drag again or turn Highlight off");
  toast(`Highlighted ${highlight.chr}:${highlight.start.toLocaleString()}-${highlight.end.toLocaleString()}`);
}

function installTrackInteractions() {
  if (!state.browser) return;
  for (const trackView of state.browser.trackViews || []) {
    const type = trackView.track?.type;
    for (const viewport of trackView.viewports || []) {
      const element = viewport.viewportElement;
      if (!element) continue;

      if (!element.__genomeCanvasSettingsMenu) {
        element.__genomeCanvasSettingsMenu = true;
        element.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          event.stopImmediatePropagation();
          presentTrackSettings(trackView, event);
        }, true);
      }

      if (!HIGHLIGHT_EXCLUDED_TRACK_TYPES.has(type) && !element.__genomeCanvasHighlightDrag) {
        element.__genomeCanvasHighlightDrag = true;
        element.addEventListener("mousedown", (event) => beginHighlightDrag(event, viewport), true);
      }
    }
  }
}

function setHighlightMode(enabled) {
  state.highlightMode = Boolean(enabled);
  ui.highlightMode.setAttribute("aria-pressed", String(state.highlightMode));
  state.browser?.root?.classList.toggle("genome-canvas-highlight-mode", state.highlightMode);
  if (!state.highlightMode) cancelHighlightDrag();
  setStatus(state.highlightMode ? "Highlight mode · drag across any track" : "Ready");
  notifyDesktopHost();
}

function clearHighlights() {
  state.highlights = [];
  state.highlightDraft = null;
  scheduleHighlightRender();
  setStatus("Highlights cleared");
  toast("All highlights cleared");
  notifyDesktopHost();
}

function highlightViewportBounds(referenceFrame) {
  const entries = [];
  for (const trackView of state.browser?.trackViews || []) {
    if (HIGHLIGHT_EXCLUDED_TRACK_TYPES.has(trackView.track?.type)) continue;
    for (const viewport of trackView.viewports || []) {
      if (viewport.referenceFrame === referenceFrame && viewport.viewportElement?.isConnected) entries.push(viewport.viewportElement);
    }
  }
  if (!entries.length) return null;
  const rects = entries.map((element) => element.getBoundingClientRect()).filter((rect) => rect.width > 0 && rect.height > 0);
  if (!rects.length) return null;
  return {
    viewport: rects[0],
    top: Math.min(...rects.map((rect) => rect.top)),
    bottom: Math.max(...rects.map((rect) => rect.bottom)),
  };
}

function appendHighlightRegion(highlight, referenceFrame, isDraft = false) {
  const bounds = highlightViewportBounds(referenceFrame);
  if (!bounds) return;
  const pixels = visibleHighlightPixels(highlight, referenceFrame, bounds.viewport.width);
  if (!pixels) return;
  const shellRect = ui.igvShell.getBoundingClientRect();
  const color = normalizeHexColor(highlight.color);
  const region = document.createElement("div");
  region.className = `genome-highlight-region${isDraft ? " draft" : ""}`;
  region.style.left = `${bounds.viewport.left - shellRect.left + ui.igvShell.scrollLeft + pixels.left}px`;
  region.style.top = `${bounds.top - shellRect.top + ui.igvShell.scrollTop}px`;
  region.style.width = `${pixels.width}px`;
  region.style.height = `${Math.max(2, bounds.bottom - bounds.top)}px`;
  region.style.setProperty("--highlight-fill", hexToRgba(color, HIGHLIGHT_FILL_ALPHA));
  region.style.setProperty("--highlight-draft-fill", hexToRgba(color, HIGHLIGHT_FILL_ALPHA / 2));
  region.style.setProperty("--highlight-outline", hexToRgba(color, HIGHLIGHT_OUTLINE_ALPHA));
  region.title = `${highlight.chr}:${Math.floor(highlight.start).toLocaleString()}-${Math.ceil(highlight.end).toLocaleString()}`;
  ui.highlightLayer.append(region);
}

function renderHighlights() {
  state.highlightRenderFrame = null;
  ui.highlightLayer.replaceChildren();
  ui.highlightLayer.style.width = `${Math.max(ui.igvShell.clientWidth, ui.igvShell.scrollWidth)}px`;
  ui.highlightLayer.style.height = `${Math.max(ui.igvShell.clientHeight, ui.igvShell.scrollHeight)}px`;
  for (const referenceFrame of state.browser?.referenceFrameList || []) {
    for (const highlight of state.highlights) appendHighlightRegion(highlight, referenceFrame);
    if (state.highlightDraft) appendHighlightRegion(state.highlightDraft, referenceFrame, true);
  }
  ui.clearHighlights.disabled = state.highlights.length === 0;
}

function scheduleHighlightRender() {
  if (state.highlightRenderFrame !== null) window.cancelAnimationFrame(state.highlightRenderFrame);
  state.highlightRenderFrame = window.requestAnimationFrame(renderHighlights);
}

function installHighlightUI() {
  const swatch = $(".highlight-color span");
  swatch.style.background = normalizeHexColor(ui.highlightColor.value, DEFAULT_HIGHLIGHT_COLOR);
  state.highlightResizeObserver?.disconnect();
  state.highlightResizeObserver = new ResizeObserver(scheduleHighlightRender);
  state.highlightResizeObserver.observe(ui.viewer);
  state.highlightResizeObserver.observe(ui.igvShell);
  if (state.browser?.columnContainer) state.highlightResizeObserver.observe(state.browser.columnContainer);
  installTrackInteractions();
  scheduleHighlightRender();
}

// The canvas palette follows the page theme; DARK_CANVAS.background matches
// --canvas in styles.css.
function syncCanvasTheme() {
  const dark = document.documentElement.dataset.theme === "dark";
  setCanvasTheme(dark ? DARK_CANVAS : LIGHT_CANVAS);
  const root = state.browser?.root;
  if (!root) return;
  root.classList.toggle("genome-canvas-dark", dark);
  for (const view of state.browser.trackViews || []) view.repaintViews?.();
}

function setStatus(message, mode = "ready") {
  ui.statusText.textContent = message;
  ui.statusDot.className = `status-dot ${mode === "busy" ? "" : mode}`.trim();
  notifyDesktopHost();
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`.trim();
  node.textContent = message;
  ui.toastRegion.append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function showDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog.open) dialog.showModal();
  document.body.classList.add("modal-open");
}

function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog.open) dialog.close();
}

function cleanDialogState() {
  if (!$("dialog[open]")) document.body.classList.remove("modal-open");
}

function updatePersistentScrollbar(bar) {
  const scroller = document.getElementById(bar.dataset.scrollTarget);
  const thumb = bar.querySelector("i");
  if (!scroller || !thumb || !bar.clientHeight) return;
  const maximumScroll = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  const trackHeight = bar.clientHeight;
  const thumbHeight = maximumScroll
    ? Math.max(42, Math.round(trackHeight * scroller.clientHeight / scroller.scrollHeight))
    : trackHeight;
  const maximumTop = Math.max(0, trackHeight - thumbHeight);
  const top = maximumScroll ? Math.round(maximumTop * scroller.scrollTop / maximumScroll) : 0;
  thumb.style.height = `${thumbHeight}px`;
  thumb.style.transform = `translateY(${top}px)`;
  bar.classList.toggle("inactive", maximumScroll === 0);
}

function refreshPersistentScrollbars() {
  window.requestAnimationFrame(() => $$('[data-scroll-target]').forEach(updatePersistentScrollbar));
}

function installPersistentScrollbars() {
  for (const bar of $$('[data-scroll-target]')) {
    if (bar.__genomeCanvasScrollbar) continue;
    bar.__genomeCanvasScrollbar = true;
    const scroller = document.getElementById(bar.dataset.scrollTarget);
    const thumb = bar.querySelector("i");
    if (!scroller || !thumb) continue;

    scroller.addEventListener("scroll", () => updatePersistentScrollbar(bar), { passive: true });
    new ResizeObserver(() => updatePersistentScrollbar(bar)).observe(scroller);
    new MutationObserver(() => updatePersistentScrollbar(bar)).observe(scroller, { childList: true, subtree: true });

    bar.addEventListener("pointerdown", (event) => {
      if (event.target === thumb) return;
      event.preventDefault();
      const bounds = bar.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (event.clientY - bounds.top) / Math.max(1, bounds.height)));
      scroller.scrollTop = ratio * Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    });

    thumb.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const startY = event.clientY;
      const startScroll = scroller.scrollTop;
      const availableTrack = Math.max(1, bar.clientHeight - thumb.getBoundingClientRect().height);
      const maximumScroll = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
      const move = (moveEvent) => {
        moveEvent.preventDefault();
        scroller.scrollTop = startScroll + (moveEvent.clientY - startY) * maximumScroll / availableTrack;
      };
      const stop = () => {
        document.removeEventListener("pointermove", move, true);
        document.removeEventListener("pointerup", stop, true);
      };
      document.addEventListener("pointermove", move, true);
      document.addEventListener("pointerup", stop, true);
    });
    updatePersistentScrollbar(bar);
  }
}

function humanSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

function humanDate(timestamp) {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(timestamp * 1000));
}

function formatSpan(basePairs) {
  if (!Number.isFinite(basePairs)) return "—";
  if (basePairs >= 1_000_000) return `${(basePairs / 1_000_000).toFixed(basePairs >= 10_000_000 ? 1 : 2)} Mb`;
  if (basePairs >= 1_000) return `${(basePairs / 1_000).toFixed(basePairs >= 10_000 ? 1 : 2)} kb`;
  return `${Math.max(1, Math.round(basePairs))} bp`;
}

function inferType(format = "") {
  const value = format.toLowerCase();
  if (["bam", "cram"].includes(value)) return "alignment";
  if (["vcf", "bcf"].includes(value)) return "variant";
  if (["bigwig", "bw", "wig", "bedgraph", "tdf"].includes(value)) return "wig";
  if (["seg"].includes(value)) return "seg";
  if (["maf", "mut"].includes(value)) return "mut";
  if (["bedpe", "interact", "biginteract"].includes(value)) return "interact";
  if (value === "hic") return "interact";
  if (["gwas"].includes(value)) return "gwas";
  if (["qtl"].includes(value)) return "qtl";
  return "annotation";
}

function decorateTrack(config) {
  const candidate = { ...config };
  if (!candidate.format && typeof candidate.url === "string" && /\.hic(?:[?#]|$)/i.test(candidate.url)) {
    candidate.format = "hic";
  }
  const track = configureHicHeatmap(candidate);
  const type = track.type || inferType(track.format);
  track.type = type;
  if (!track.color || /^hsla?\(/i.test(track.color) || track.genomeCanvasAutoColor === true) {
    track.color = automaticTrackColor(track);
    track.genomeCanvasAutoColor = true;
  }
  if (!track.height) {
    if (type === "alignment") track.height = 180;
    else if (type === "wig") track.height = 110;
    else if (type === "interact") track.height = 130;
    else if (type === "gwas") track.height = 220;
    else track.height = 90;
  }
  if (type === "gwas") {
    // Backfill new Manhattan/LD settings when restoring profiles that were
    // saved before these fields were introduced.
    track.significancePValue = 5e-8;
    track.ldEndpoint ||= "api/gwas-ld";
    track.ldLabel ||= "1000 Genomes Phase 3 ALL";
    track.ldMaxWindow ||= 2_000_000;
    if (track.height === 180) track.height = 220;
    if (track.dotSize === 4) track.dotSize = 6;
  }
  if (type === "variant") track.visibilityWindow = track.visibilityWindow || 1_000_000;
  return track;
}

function isHicConfig(config) {
  if (String(config?.format || "").toLowerCase() === "hic") return true;
  return typeof config?.url === "string" && /\.hic(?:[?#]|$)/i.test(config.url);
}

function requiresCustomRenderer(config) {
  return isHicConfig(config) || config?.type === "gwas" || config?.format === "gwas"
    || /\.gwas(?:\.(?:gz|bgz))?(?:[?#]|$)/i.test(String(config?.url || ""));
}

async function loadConfiguredTrack(config) {
  const decorated = decorateTrack(normalizeTrackURLs(config));
  if (requiresCustomRenderer(decorated)) {
    const track = await state.browser.createTrack(decorated);
    if (!track) throw new Error("IGV could not create the track");
    // Install the LD loader before the first feature request, not after the
    // viewport cache has already been populated by loadTrack().
    applyTrackVisualStyle(track, false);
    await state.browser.addTrack(track);
    state.browser.reorderTracks?.();
    await state.browser.layoutChange?.();
    return track;
  }

  const track = await state.browser.loadTrack(decorated);
  applyTrackVisualStyle(track);
  return track;
}

function trackViews() {
  return (state.browser?.trackViews || []).filter((view) => {
    const type = view.track?.type;
    return view.track && type !== "ruler" && type !== "sequence";
  });
}

function trackColor(track) {
  if (typeof track.color === "string") return track.color;
  const configured = track.config?.color;
  if (typeof configured === "string") return configured;
  return automaticTrackColor(track);
}

function applyTrackVisualStyle(track, repaint = true) {
  let changed = installGradientSignalRenderer(track);
  changed = installHicHeatmapRenderer(track) || changed;
  changed = installManhattanRenderer(track) || changed;
  changed = installRefSeqAllStyle(track) || changed;
  if (assayTypeForTrack(track) === "gene" && !track.__genomeCanvasRefSeqStyle) {
    const color = automaticTrackColor(track);
    track.color = color;
    if (track.config) track.config.color = color;
    changed = true;
  }
  if (changed && repaint) track.trackView?.repaintViews?.();
}

function applyLoadedTrackStyles() {
  for (const view of trackViews()) applyTrackVisualStyle(view.track, false);
  for (const view of trackViews()) view.repaintViews?.();
}

function syncTracks() {
  window.clearTimeout(state.syncTimer);
  state.syncTimer = window.setTimeout(() => {
    const views = trackViews();
    installTrackInteractions();
    scheduleHighlightRender();
    ui.trackCount.textContent = String(views.length);
    ui.trackSummary.textContent = `${views.length} track${views.length === 1 ? "" : "s"}`;
    ui.trackList.replaceChildren();

    if (!views.length) {
      const empty = document.createElement("div");
      empty.className = "track-empty";
      empty.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-layers"/></svg><p>Tracks loaded from server files or public URLs appear here.</p>';
      ui.trackList.append(empty);
      notifyDesktopHost();
      return;
    }

    views.forEach(({ track }) => {
      const item = document.createElement("article");
      item.className = "track-item";

      const swatch = document.createElement("span");
      swatch.className = "track-color";
      swatch.style.background = trackColor(track);

      const copy = document.createElement("div");
      copy.className = "track-item-copy";
      const name = document.createElement("strong");
      name.textContent = track.name || track.id || "Untitled track";
      name.title = name.textContent;
      const meta = document.createElement("small");
      meta.textContent = track.config?.format || track.format || track.type || "track";
      copy.append(name, meta);

      const remove = document.createElement("button");
      remove.className = "remove-track";
      remove.type = "button";
      remove.textContent = "×";
      remove.title = `Remove ${name.textContent}`;
      remove.setAttribute("aria-label", remove.title);
      remove.addEventListener("click", () => {
        state.browser.removeTrack(track);
        syncTracks();
        toast(`Removed ${name.textContent}`);
      });

      item.append(swatch, copy, remove);
      ui.trackList.append(item);
    });
    notifyDesktopHost();
  }, 60);
}

function updateLocus(referenceFrames) {
  // Without an event payload, read IGV's frames rather than currentLoci(),
  // which returns unformatted fractional coordinates.
  if (!Array.isArray(referenceFrames) && Array.isArray(state.browser?.referenceFrameList)) {
    referenceFrames = state.browser.referenceFrameList;
  }
  let loci = [];
  if (Array.isArray(referenceFrames)) {
    loci = referenceFrames.map((frame) => typeof frame.getLocusString === "function" ? frame.getLocusString() : frame.label).filter(Boolean);
  }
  if (!loci.length && state.browser) {
    const current = state.browser.currentLoci();
    loci = Array.isArray(current) ? current : [current];
  }
  if (!loci.length) return;

  const label = loci.join(" · ");
  ui.locus.value = loci.join(" ");
  ui.currentLocus.textContent = label;

  if (Array.isArray(referenceFrames) && referenceFrames.length === 1) {
    const frame = referenceFrames[0];
    const span = Number(frame.end) - Number(frame.start);
    ui.viewSpan.textContent = formatSpan(span);
  } else {
    ui.viewSpan.textContent = loci.length > 1 ? `${loci.length} loci` : "—";
  }
  scheduleHighlightRender();
  notifyDesktopHost();
}

function setCurrentGenome(genome) {
  state.currentGenome = genome;
  ui.assembly.textContent = String(genome || "CUSTOM").toUpperCase();
  $("#hub-current-genome").textContent = genome;
  const existing = [...ui.genome.options].find((option) => option.value === genome);
  if (existing) ui.genome.value = genome;
  else {
    const option = document.createElement("option");
    option.value = genome;
    option.textContent = `${genome} · custom`;
    ui.genome.insertBefore(option, ui.genome.querySelector('[value="__custom__"]'));
    ui.genome.value = genome;
  }
  renderHubCatalog();
  if (state.activeHub && !state.activeHub.assemblies?.includes(genome)) resetHubTrackBrowser();
  notifyDesktopHost();
}

async function loadGenome(genome, name = genome) {
  setStatus(`Loading ${name}...`, "busy");
  await state.browser.loadGenome(genome);
  state.highlights = [];
  state.highlightDraft = null;
  setCurrentGenome(typeof genome === "string" ? genome : genome.id || "custom");
  applyLoadedTrackStyles();
  syncTracks();
  setStatus(`${name} is ready`);
  scheduleHighlightRender();
}

function workspaceKindLabel(workspace) {
  return workspace.kind === "home" ? "SERVER HOME" : "SHARED /NFS";
}

function workspaceMatches(workspace, query) {
  return !query || `${workspace.name} ${workspace.kind}`.toLocaleLowerCase().includes(query);
}

function emptyWorkspaceList(message) {
  const empty = document.createElement("div");
  empty.className = "workspace-list-empty";
  empty.textContent = message;
  return empty;
}

function workspaceChoice(workspace, { hidden = false } = {}) {
  const row = document.createElement("div");
  row.className = "workspace-choice";
  if (state.workspace?.id === workspace.id) row.classList.add("current");
  row.tabIndex = hidden ? -1 : 0;
  row.setAttribute("role", hidden ? "group" : "button");

  const icon = document.createElement("span");
  icon.className = "workspace-choice-icon";
  icon.textContent = workspace.kind === "home" ? "H" : "W";
  const copy = document.createElement("span");
  copy.className = "workspace-choice-copy";
  const name = document.createElement("strong");
  name.textContent = workspace.name;
  const kind = document.createElement("small");
  kind.textContent = hidden ? "HIDDEN SERVER HOME" : workspaceKindLabel(workspace);
  copy.append(name, kind);
  const action = document.createElement("button");
  action.className = `workspace-choice-action${hidden ? " restore" : ""}`;
  action.type = "button";
  action.textContent = hidden ? "Restore" : (workspace.kind === "home" ? "Hide" : "Delete");
  action.title = hidden ? "Restore this Home workspace" : `${action.textContent} workspace`;
  action.addEventListener("click", (event) => {
    event.stopPropagation();
    if (hidden) restoreHomeWorkspace(workspace);
    else removeWorkspace(workspace);
  });
  row.append(icon, copy, action);

  if (!hidden) {
    const enter = () => enterWorkspace(workspace);
    row.addEventListener("click", (event) => {
      if (!event.target.closest("button")) enter();
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        enter();
      }
    });
  }
  return row;
}

function renderWorkspaceCatalog() {
  const query = ui.workspaceSearch.value.trim().toLocaleLowerCase();
  const visible = state.workspaceCatalog.filter((workspace) => workspaceMatches(workspace, query));
  const homes = visible.filter((workspace) => workspace.kind === "home");
  const manual = visible.filter((workspace) => workspace.kind === "manual");
  ui.homeWorkspaceList.replaceChildren(...(homes.length ? homes.map((item) => workspaceChoice(item)) : [emptyWorkspaceList("No matching Home workspaces") ]));
  ui.manualWorkspaceList.replaceChildren(...(manual.length ? manual.map((item) => workspaceChoice(item)) : [emptyWorkspaceList("No manual workspaces yet") ]));

  const hidden = state.hiddenHomeWorkspaces.filter((workspace) => workspaceMatches(workspace, query));
  ui.hiddenWorkspaces.hidden = state.hiddenHomeWorkspaces.length === 0;
  $("#hidden-workspace-count").textContent = state.hiddenHomeWorkspaces.length;
  ui.hiddenWorkspaceList.replaceChildren(...(hidden.length ? hidden.map((item) => workspaceChoice(item, { hidden: true })) : [emptyWorkspaceList("No matching hidden workspaces") ]));
}

async function loadWorkspaceCatalog() {
  const payload = await fetchJSON(appURL("api/workspaces"));
  state.workspace = payload.selected || state.workspace;
  state.workspaceCatalog = Array.isArray(payload.workspaces) ? payload.workspaces : [];
  state.hiddenHomeWorkspaces = Array.isArray(payload.hiddenHomeWorkspaces) ? payload.hiddenHomeWorkspaces : [];
  renderWorkspaceCatalog();
  notifyDesktopHost();
  return payload;
}

function showWorkspaceGate() {
  if (state.config?.localMode) return;
  document.body.classList.remove("workspace-active", "workspace-pending");
  document.body.classList.add("workspace-selecting");
  $("#close-workspace-gate").hidden = !state.workspace;
  $("#workspace-gate-status").textContent = state.workspace
    ? `Current workspace: ${state.workspace.name}`
    : "Select a Home workspace or create a shared workspace.";
  loadWorkspaceCatalog().catch((error) => {
    $("#workspace-gate-status").textContent = error.message;
  });
  window.setTimeout(() => ui.workspaceSearch.focus(), 0);
}

function closeWorkspaceGate() {
  if (!state.workspace) return;
  document.body.classList.remove("workspace-selecting", "workspace-pending");
  document.body.classList.add("workspace-active");
}

async function enterWorkspace(workspace) {
  $("#workspace-gate-status").textContent = `Opening ${workspace.name}...`;
  try {
    await fetchJSON(appURL("api/workspaces/select"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: workspace.id }),
    });
    window.location.reload();
  } catch (error) {
    $("#workspace-gate-status").textContent = error.message;
  }
}

async function createWorkspace(event) {
  event.preventDefault();
  const name = $("#workspace-create-name").value.trim();
  if (!name) return;
  $("#workspace-gate-status").textContent = `Creating ${name}...`;
  try {
    await fetchJSON(appURL("api/workspaces"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    window.location.reload();
  } catch (error) {
    $("#workspace-gate-status").textContent = error.message;
  }
}

async function removeWorkspace(workspace) {
  const verb = workspace.kind === "home" ? "hide" : "delete";
  const detail = workspace.kind === "home" ? "The Home directory and files will not be deleted." : "Its saved Favorites will remain on disk but the workspace entry will be removed.";
  if (!window.confirm(`${verb[0].toUpperCase()}${verb.slice(1)} workspace “${workspace.name}”?\n\n${detail}`)) return;
  try {
    await fetchJSON(appURL(`api/workspaces/${encodeURIComponent(workspace.id)}`), { method: "DELETE" });
    if (state.workspace?.id === workspace.id) {
      state.workspace = null;
      document.body.classList.remove("workspace-active");
      document.body.classList.add("workspace-selecting");
    }
    await loadWorkspaceCatalog();
  } catch (error) {
    $("#workspace-gate-status").textContent = error.message;
  }
}

async function restoreHomeWorkspace(workspace) {
  try {
    await fetchJSON(appURL("api/workspaces/restore"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: workspace.id }),
    });
    await loadWorkspaceCatalog();
  } catch (error) {
    $("#workspace-gate-status").textContent = error.message;
  }
}

function bindWorkspaceEvents() {
  ui.workspaceSearch.addEventListener("input", renderWorkspaceCatalog);
  $("#workspace-create-form").addEventListener("submit", createWorkspace);
  $("#close-workspace-gate").addEventListener("click", closeWorkspaceGate);
  $("#workspace-button").addEventListener("click", showWorkspaceGate);
}

async function initializeApplication() {
  bindWorkspaceEvents();
  // The workspace cookie is HttpOnly, so request the configuration alongside
  // the catalog instead of waiting a round trip; it is discarded (409) when no
  // workspace has been selected yet.
  const configRequest = fetchJSON(appURL("api/config"));
  configRequest.catch(() => {});
  const payload = await loadWorkspaceCatalog();
  if (!payload.selected) {
    state.workspace = null;
    showWorkspaceGate();
    return;
  }
  state.workspace = payload.selected;
  ui.workspaceName.textContent = state.workspace.name;
  document.body.classList.remove("workspace-pending", "workspace-selecting");
  document.body.classList.add("workspace-active");
  bindEvents();
  state.eventsBound = true;
  await initializeBrowser(configRequest);
}

async function initializeBrowser(configRequest) {
  setStatus("Reading local configuration...", "busy");
  state.config = await (configRequest || fetchJSON(appURL("api/config"))).catch(() => fetchJSON(appURL("api/config")));
  document.documentElement.classList.toggle("local-backend", Boolean(state.config.localMode));
  if (state.config.localMode) {
    $("#workspace-button").hidden = true;
    $("#shared-tracks-button").hidden = true; // the lab list lives on the server
    $("#file-source-title").textContent = "Open local files";
    $("#file-source-detail").textContent = "Browse files on this Mac without uploading";
    $("#file-dialog-kicker").textContent = "This Mac";
  }
  state.authenticatedUser = state.config.workspaceId || state.config.user || "workspace";
  $("#profile-list-owner").textContent = `SAVED FOR ${(state.config.user || "WORKSPACE").toLocaleUpperCase()}`;
  ui.appName.textContent = state.config.appName || "Genome Canvas";
  document.title = `${ui.appName.textContent} — Local genome browser`;

  ui.rootSelect.replaceChildren(...state.config.roots.map((root) => {
    const option = document.createElement("option");
    option.value = root.id;
    option.textContent = root.label;
    return option;
  }));
  const initialFileLocation = defaultFileLocation();
  state.currentRoot = initialFileLocation.root;
  state.currentPath = initialFileLocation.path;
  ui.rootSelect.value = initialFileLocation.root;

  const params = new URLSearchParams(window.location.search);
  let browserConfig;
  let initialGenome = state.config.defaultGenome || "hg38";
  if (params.has("session")) {
    setStatus("Restoring shared view...", "busy");
    try {
      browserConfig = await fetchJSON(appURL(`api/sessions/${encodeURIComponent(params.get("session"))}`));
      initialGenome = browserConfig.genome || browserConfig.reference?.id || initialGenome;
    } catch (error) {
      toast(`Could not restore shared view: ${error.message}`, "error");
    }
  }

  // ?locus=&genome=&highlight= links from the Omics Canvas pages (and genomecanvas:// links in the Mac app)
  const link = linkedView(params);
  if (!browserConfig && link.genome && [...ui.genome.options].some((option) => option.value === link.genome)) {
    initialGenome = link.genome;
  }
  if (!browserConfig) {
    browserConfig = {
      genome: initialGenome,
      locus: state.config.defaultLocus || "chr8:127,728,000-127,742,000",
      tracks: [],
    };
  }
  // a region opens directly; a gene name or rsID is searched once the browser is up
  const linkedLocusSearch = link.locus && !parseRegion(link.locus) ? link.locus : null;
  if (link.locus && !linkedLocusSearch) browserConfig.locus = link.locus;
  browserConfig = normalizeBrowserConfigURLs(browserConfig);
  const restoredHighlights = [...normalizeHighlights(browserConfig.genomeCanvasHighlights), ...link.highlights];
  const restoredCenterGuide = browserConfig.genomeCanvasCenterGuide === true;
  const restoredHighlightColor = normalizeHexColor(browserConfig.genomeCanvasHighlightColor, DEFAULT_HIGHLIGHT_COLOR);
  delete browserConfig.genomeCanvasHighlights;
  delete browserConfig.genomeCanvasCenterGuide;
  delete browserConfig.genomeCanvasHighlightColor;
  const deferredTracks = (browserConfig.tracks || []).filter(requiresCustomRenderer);
  if (deferredTracks.length) {
    browserConfig.tracks = browserConfig.tracks.filter((track) => !requiresCustomRenderer(track));
  }
  Object.assign(browserConfig, {
    ...(state.config.genomeList?.length ? {
      loadDefaultGenomes: false,
      genomeList: normalizeReferenceResources(state.config.genomeList, state.config, appBaseURL),
    } : {}),
    showNavigation: false,
    showIdeogram: true,
    showRuler: true,
    showSVGButton: false,
    showCenterGuideButton: false,
    showCenterGuide: false,
    showCursorTrackGuide: true,
    showTrackLabels: true,
    showSampleNames: true,
    showGearColumn: false,
    queryParametersSupported: false,
  });

  if (!window.igv?.createBrowser) throw new Error("IGV.js browser engine did not load");
  state.browser = await window.igv.createBrowser(ui.viewer, browserConfig);
  installTrackReorderUI();
  syncCanvasTheme();
  await state.browser.layoutChange?.();
  for (const trackConfig of deferredTracks) await loadConfiguredTrack(trackConfig);
  await loadLinkedTracks(link.tracks);
  state.highlights = restoredHighlights;
  state.centerGuide = restoredCenterGuide;
  state.browser.doShowCenterLine = restoredCenterGuide;
  state.browser.setCenterLineVisibility?.(restoredCenterGuide);
  $("#center-guide-button").setAttribute("aria-pressed", String(restoredCenterGuide));
  ui.highlightColor.value = restoredHighlightColor;
  $(".highlight-color span").style.background = restoredHighlightColor;
  setCurrentGenome(initialGenome);
  state.browser.on("locuschange", updateLocus);
  state.browser.on("trackremoved", syncTracks);
  state.browser.on("trackorderchanged", syncTracks);
  state.browser.on("trackdragend", syncTracks);
  applyLoadedTrackStyles();
  installHighlightUI();
  updateLocus();
  syncTracks();
  ui.loading.classList.add("hidden");
  if (linkedLocusSearch) await searchLocation(linkedLocusSearch);
  setStatus("Ready");
}

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) throw new Error(payload?.error || `${response.status} ${response.statusText}`);
  return payload;
}

const FILE_LOCATION_HISTORY_PREFIX = "genome-canvas:last-server-location:";
const RAIL_COLLAPSED_KEY = "genome-canvas:rail-collapsed";
const THEME_KEY = "genome-canvas:theme";
// Set by bindEvents; the native macOS client also drives it through the bridge.
let applyThemeFromHost = null;

function fileLocationHistoryKey() {
  return `${FILE_LOCATION_HISTORY_PREFIX}${state.authenticatedUser || "genome"}`;
}

function readFileLocationHistory() {
  try {
    const location = JSON.parse(window.localStorage.getItem(fileLocationHistoryKey()));
    const rootExists = state.config.roots.some((root) => root.id === location?.root);
    if (rootExists && typeof location.path === "string") return location;
  } catch {
    // Ignore disabled storage and malformed values.
  }
  return null;
}

function rememberFileLocation(root, path) {
  try {
    window.localStorage.setItem(fileLocationHistoryKey(), JSON.stringify({ root, path }));
  } catch {
    // File browsing still works when browser storage is unavailable.
  }
}

function clearFileLocationHistory() {
  try {
    window.localStorage.removeItem(fileLocationHistoryKey());
  } catch {
    // Ignore disabled browser storage.
  }
}

function defaultFileLocation() {
  const configuredRoot = state.config.roots.some((root) => root.id === state.config.defaultFileRoot)
    ? state.config.defaultFileRoot
    : state.config.roots[0]?.id || "";
  return { root: configuredRoot, path: state.config.defaultFilePath || "" };
}

async function openFileBrowser() {
  state.selectedFiles.clear();
  updateSelectionSummary();
  showDialog("file-dialog");
  const remembered = readFileLocationHistory();
  const fallback = defaultFileLocation();
  const location = remembered || fallback;
  ui.rootSelect.value = location.root;
  const loaded = await loadDirectory(location.root, location.path);
  if (!loaded && remembered) {
    clearFileLocationHistory();
    ui.rootSelect.value = fallback.root;
    await loadDirectory(fallback.root, fallback.path);
  }
}

async function loadDirectory(root, path) {
  state.currentRoot = root;
  state.currentPath = path;
  state.currentFileEntries = [];
  updateSelectionSummary();
  ui.fileBrowser.innerHTML = `<div class="browser-message"><div><b>G</b><span>Reading ${state.config.localMode ? "local" : "server"} directory...</span></div></div>`;
  try {
    const payload = await fetchJSON(`${appURL("api/files")}?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`);
    state.currentPath = payload.path;
    ui.rootSelect.value = root;
    rememberFileLocation(root, payload.path);
    renderBreadcrumbs(payload.path);
    renderFileRows(payload.entries);
    return true;
  } catch (error) {
    state.currentFileEntries = [];
    updateSelectionSummary();
    ui.fileBrowser.innerHTML = `<div class="browser-message"><div><b>!</b><span>${escapeHTML(error.message)}</span></div></div>`;
    return false;
  }
}

function renderBreadcrumbs(path) {
  ui.breadcrumbs.replaceChildren();
  const root = state.config.roots.find((item) => item.id === state.currentRoot);
  ui.serverPath.value = path ? `/${path.replace(/^\/+/, "")}` : "/";
  const parts = path ? path.split("/") : [];
  const labels = [root?.label || "Data", ...parts];
  labels.forEach((label, index) => {
    if (index > 0) {
      const divider = document.createElement("i");
      divider.textContent = "/";
      ui.breadcrumbs.append(divider);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => loadDirectory(state.currentRoot, parts.slice(0, index).join("/")));
    ui.breadcrumbs.append(button);
  });
}

function renderFileRows(entries) {
  state.currentFileEntries = [...entries];
  ui.fileBrowser.replaceChildren();
  const head = document.createElement("div");
  head.className = "file-table-head";
  head.innerHTML = "<span></span><span>NAME</span><span>FORMAT</span><span>SIZE</span><span>MODIFIED</span>";
  ui.fileBrowser.append(head);

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "browser-message";
    empty.innerHTML = "<div><b>∅</b><span>No supported track files were found in this directory</span></div>";
    ui.fileBrowser.append(empty);
    updateSelectionSummary();
    return;
  }

  entries.forEach((entry) => {
    const row = document.createElement("div");
    row.className = "file-row";
    if (entry.kind === "file" && state.selectedFiles.has(`${state.currentRoot}:${entry.path}`)) row.classList.add("selected");

    const checkSlot = document.createElement("span");
    if (entry.kind === "file") {
      const check = document.createElement("input");
      check.className = "file-check";
      check.type = "checkbox";
      check.checked = state.selectedFiles.has(`${state.currentRoot}:${entry.path}`);
      check.setAttribute("aria-label", `Select ${entry.name}`);
      check.addEventListener("change", () => toggleFile(entry, check.checked, row));
      checkSlot.append(check);
    }

    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "file-name-button";
    const icon = document.createElement("span");
    icon.className = `file-icon ${entry.kind === "directory" ? "folder" : ""} ${entry.symlink ? "symlink" : ""}`.trim();
    icon.textContent = entry.symlink ? "LINK" : entry.kind === "directory" ? "DIR" : entry.format.toUpperCase().slice(0, 5);
    const name = document.createElement("strong");
    name.textContent = entry.name;
    nameButton.append(icon, name);
    if (entry.kind === "directory") nameButton.addEventListener("click", () => loadDirectory(state.currentRoot, entry.path));
    else nameButton.addEventListener("click", () => {
      const check = $(".file-check", row);
      check.checked = !check.checked;
      toggleFile(entry, check.checked, row);
    });

    const format = document.createElement("small");
    format.textContent = entry.kind === "directory"
      ? entry.symlink ? "Symlink folder" : "Folder"
      : [entry.format, entry.indexed ? "indexed" : "", entry.symlink ? "symlink" : ""].filter(Boolean).join(" · ");
    if (entry.indexed) format.className = "file-index";
    const size = document.createElement("small");
    size.textContent = entry.kind === "directory" ? "—" : humanSize(entry.size);
    const modified = document.createElement("small");
    modified.textContent = humanDate(entry.modified);
    row.append(checkSlot, nameButton, format, size, modified);
    ui.fileBrowser.append(row);
  });
  updateSelectionSummary();
}

function toggleFile(entry, checked, row) {
  const key = `${state.currentRoot}:${entry.path}`;
  if (checked) state.selectedFiles.set(key, { root: state.currentRoot, ...entry });
  else state.selectedFiles.delete(key);
  row.classList.toggle("selected", checked);
  updateSelectionSummary();
}

function updateSelectionSummary() {
  const count = state.selectedFiles.size;
  const visibleFiles = state.currentFileEntries.filter((entry) => entry.kind === "file");
  const selectedVisible = visibleFiles.filter((entry) => state.selectedFiles.has(`${state.currentRoot}:${entry.path}`)).length;
  ui.selectionSummary.textContent = count ? `${count} file${count === 1 ? "" : "s"} selected` : "No files selected";
  ui.loadSelected.disabled = count === 0;
  ui.selectAllFiles.disabled = visibleFiles.length === 0 || selectedVisible === visibleFiles.length;
  ui.clearSelectedFiles.disabled = count === 0;
}

function selectAllVisibleFiles() {
  for (const entry of state.currentFileEntries) {
    if (entry.kind !== "file") continue;
    state.selectedFiles.set(`${state.currentRoot}:${entry.path}`, { root: state.currentRoot, ...entry });
  }
  renderFileRows(state.currentFileEntries);
}

function clearSelectedFiles() {
  state.selectedFiles.clear();
  renderFileRows(state.currentFileEntries);
}

async function loadSelectedFiles() {
  await loadServerFiles([...state.selectedFiles.values()], ui.loadSelected, "file-dialog");
}

// Server files ({root, path, name}) from the file browser or the shared track list. The genome in view is
// recorded with each file on the shared list.
async function loadServerFiles(selected, button, dialogId) {
  if (!selected.length) return;
  button.disabled = true;
  setStatus(`Loading ${selected.length} server file${selected.length === 1 ? "" : "s"}...`, "busy");
  let loaded = 0;
  const failures = [];

  for (const file of selected) {
    try {
      const payload = await fetchJSON(`${appURL("api/track")}?root=${encodeURIComponent(file.root)}&path=${encodeURIComponent(file.path)}&genome=${encodeURIComponent(state.currentGenome)}`);
      if (payload.kind === "reference") {
        const reference = normalizeReferenceURLs(payload.reference);
        await loadGenome(reference, reference.name);
      } else {
        await loadConfiguredTrack(payload.track);
      }
      loaded += 1;
    } catch (error) {
      failures.push(`${file.name}: ${error.message}`);
    }
  }
  closeDialog(dialogId);
  syncTracks();
  if (failures.length) {
    toast(`Loaded ${loaded}; ${failures.length} failed. ${failures[0]}`, "error");
    setStatus("Some tracks failed to load", "error");
  } else {
    toast(`Loaded ${loaded} server file${loaded === 1 ? "" : "s"}`);
    setStatus("Ready");
  }
}

// ---------------------------------------------------------------- shared tracks
// Every server file any workspace has loaded from a shared data root (server: /api/shared-tracks), so others
// load it without looking for its path. The server drops files that are gone and dates rewritten ones.

const sharedKey = (item) => `${item.root}:${item.path}`;

async function openSharedTracks() {
  state.selectedShared.clear();
  ui.sharedGenomeLabel.textContent = state.currentGenome;
  updateSharedSelection();
  showDialog("shared-dialog");
  await loadSharedTracks(false);
}

async function loadSharedTracks(refresh) {
  ui.sharedBrowser.innerHTML = '<div class="browser-message"><div><b>G</b><span>Reading the shared track list...</span></div></div>';
  try {
    const payload = await fetchJSON(`${appURL("api/shared-tracks")}${refresh ? "?refresh=1" : ""}`);
    state.sharedTracks = payload.tracks || [];
    const known = new Set(state.sharedTracks.map(sharedKey));
    for (const key of [...state.selectedShared.keys()]) if (!known.has(key)) state.selectedShared.delete(key);
    renderSharedTracks();
  } catch (error) {
    state.sharedTracks = [];
    ui.sharedBrowser.innerHTML = `<div class="browser-message"><div><b>!</b><span>${escapeHTML(error.message)}</span></div></div>`;
    updateSharedSelection();
  }
}

// root:path of the server files shown now, from their /data/[<workspace>/]<root>/<path> URLs
function serverFilesInView() {
  const keys = new Set();
  for (const view of trackViews()) {
    const url = view.track?.config?.url || view.track?.url;
    if (typeof url !== "string") continue;
    let path;
    try { path = new URL(url, location.href).pathname; } catch { continue; }
    const at = path.indexOf("/data/");
    if (at < 0) continue;
    const parts = path.slice(at + 6).split("/").filter(Boolean).map((part) => {
      try { return decodeURIComponent(part); } catch { return part; }
    });
    if (/^(home|manual)-[0-9a-f]{18}$|^local$/.test(parts[0] || "")) parts.shift();
    if (parts.length > 1) keys.add(`${parts[0]}:${parts.slice(1).join("/")}`);
  }
  return keys;
}

function visibleSharedTracks() {
  const query = ui.sharedSearch.value.trim().toLowerCase();
  const genomeOnly = ui.sharedGenomeOnly.checked;
  const inView = ui.sharedNewOnly.checked ? serverFilesInView() : new Set();
  return state.sharedTracks.filter((item) => {
    if (inView.has(sharedKey(item))) return false;
    if (genomeOnly && item.kind !== "reference" && item.genomes.length && !item.genomes.includes(state.currentGenome)) return false;
    if (!query) return true;
    return [item.name, item.path, item.format, item.rootLabel, item.ownerName, ...item.users, ...item.genomes]
      .join(" ").toLowerCase().includes(query);
  });
}

function sharedFolder(item) {
  const folder = item.path.split("/").slice(0, -1).join("/");
  return `${item.rootLabel} · /${folder}`;
}

function renderSharedTracks() {
  const shown = visibleSharedTracks();
  ui.sharedBrowser.replaceChildren();
  const head = document.createElement("div");
  head.className = "file-table-head shared-row";
  head.innerHTML = "<span></span><span>NAME</span><span>FORMAT</span><span>LOADED BY</span><span>MODIFIED</span>";
  ui.sharedBrowser.append(head);
  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "browser-message";
    const filtered = state.sharedTracks.length > 0;
    empty.innerHTML = `<div><b>∅</b><span>${filtered
      ? `No shared track matches${ui.sharedGenomeOnly.checked ? ` for ${escapeHTML(state.currentGenome)}` : ""}${ui.sharedNewOnly.checked ? " that is not in this view already" : ""}.`
      : "Nothing shared yet: server files that anyone loads show up here."}</span></div>`;
    ui.sharedBrowser.append(empty);
    updateSharedSelection();
    return;
  }
  for (const item of shown) {
    const key = sharedKey(item);
    const row = document.createElement("div");
    row.className = "file-row shared-row";
    row.classList.toggle("selected", state.selectedShared.has(key));
    const checkSlot = document.createElement("span");
    const check = document.createElement("input");
    check.className = "file-check";
    check.type = "checkbox";
    check.checked = state.selectedShared.has(key);
    check.setAttribute("aria-label", `Select ${item.name}`);
    const toggle = (checked) => {
      if (checked) state.selectedShared.set(key, item);
      else state.selectedShared.delete(key);
      check.checked = checked;
      row.classList.toggle("selected", checked);
      updateSharedSelection();
    };
    check.addEventListener("change", () => toggle(check.checked));
    checkSlot.append(check);

    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "file-name-button";
    nameButton.title = `${item.rootLabel}: /${item.path}`;
    const icon = document.createElement("span");
    icon.className = "file-icon";
    icon.textContent = item.kind === "reference" ? "REF" : item.format.toUpperCase().slice(0, 5);
    const label = document.createElement("span");
    label.className = "shared-name";
    const name = document.createElement("strong");
    name.textContent = item.name;
    const folder = document.createElement("small");
    folder.textContent = sharedFolder(item);
    label.append(name, folder);
    nameButton.append(icon, label);
    nameButton.addEventListener("click", () => toggle(!check.checked));

    const format = document.createElement("small");
    format.textContent = [item.kind === "reference" ? "reference" : item.format, ...item.genomes].join(" · ");
    const by = document.createElement("small");
    const others = item.users.filter((user) => user !== item.ownerName);
    by.textContent = `${item.mine ? "you" : item.ownerName || "saved view"}${others.length ? ` + ${others.length}` : ""}`;
    by.title = item.users.length ? `Loaded by ${item.users.join(", ")} (${item.loads} time${item.loads === 1 ? "" : "s"})` : "";
    const modifiedCell = document.createElement("span");
    modifiedCell.className = "shared-modified";
    const modified = document.createElement("small");
    modified.textContent = humanDate(item.modified);
    modified.title = `${humanSize(item.size)} · first shared ${item.createdAt.slice(0, 10)}`;
    modifiedCell.append(modified);
    if (item.mine) {
      const unshare = document.createElement("button");
      unshare.type = "button";
      unshare.className = "icon-button ghost small";
      unshare.title = "Take it off the shared list (shared again when you load it again)";
      unshare.setAttribute("aria-label", `Stop sharing ${item.name}`);
      unshare.innerHTML = '<svg class="icon"><use href="#i-x"/></svg>';
      unshare.addEventListener("click", () => unshareTrack(item));
      modifiedCell.append(unshare);
    }
    row.append(checkSlot, nameButton, format, by, modifiedCell);
    ui.sharedBrowser.append(row);
  }
  updateSharedSelection();
}

function updateSharedSelection() {
  const count = state.selectedShared.size;
  const shown = visibleSharedTracks();
  ui.sharedSelectionSummary.textContent = count ? `${count} track${count === 1 ? "" : "s"} selected` : "No tracks selected";
  ui.loadShared.disabled = count === 0;
  ui.sharedSelectAll.disabled = !shown.length || shown.every((item) => state.selectedShared.has(sharedKey(item)));
  ui.sharedClear.disabled = count === 0;
}

async function unshareTrack(item) {
  if (!window.confirm(`Take ${item.name} off the shared list? The file is not changed.`)) return;
  try {
    await fetchJSON(appURL("api/shared-tracks/hide"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root: item.root, path: item.path }),
    });
    state.selectedShared.delete(sharedKey(item));
    state.sharedTracks = state.sharedTracks.filter((entry) => sharedKey(entry) !== sharedKey(item));
    renderSharedTracks();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadSelectedSharedTracks() {
  await loadServerFiles([...state.selectedShared.values()], ui.loadShared, "shared-dialog");
}

async function loadRemoteTrack(event) {
  event.preventDefault();
  const url = $("#track-url").value.trim();
  const indexURL = $("#track-index-url").value.trim();
  const name = $("#track-name").value.trim();
  const format = $("#track-format").value.trim();
  if (!url) return;

  const config = { url };
  if (indexURL) config.indexURL = indexURL;
  if (name) config.name = name;
  else {
    try { config.name = decodeURIComponent(new URL(url, location.href).pathname.split("/").pop()); }
    catch { config.name = "Remote track"; }
  }
  if (format) config.format = format;
  setStatus(`Connecting ${config.name}...`, "busy");
  try {
    await loadConfiguredTrack(config);
    closeDialog("url-dialog");
    $("#url-form").reset();
    syncTracks();
    setStatus("Ready");
    toast(`Connected ${config.name}`);
  } catch (error) {
    setStatus("Public track failed to load", "error");
    toast(`Could not load public track: ${error.message}`, "error");
  }
}

const HUB_TRACK_RENDER_LIMIT = 200;
const HUB_REQUEST_TIMEOUT_MS = 45000;

function switchPublicDataTab(tab) {
  state.publicDataTab = tab === "track" ? "track" : "hubs";
  $$('[data-public-tab]').forEach((button) => {
    button.setAttribute("aria-selected", String(button.dataset.publicTab === state.publicDataTab));
  });
  $$('[data-public-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.publicPanel !== state.publicDataTab;
  });
  const trackMode = state.publicDataTab === "track";
  $("#track-url").required = trackMode;
  $("#connect-track-button").hidden = !trackMode;
  ui.loadHubTracks.hidden = trackMode;
  $("#public-data-footer-note").textContent = trackMode
    ? "Supports HTTPS and LAN HTTP data sources"
    : "Choose a catalog entry, then select only the tracks you need";
  if (!trackMode) renderHubCatalog();
}

function openPublicDataDialog() {
  switchPublicDataTab("hubs");
  showDialog("url-dialog");
  refreshPersistentScrollbars();
}

function renderHubCatalog() {
  if (!ui.hubCatalog) return;
  const hubs = hubsForGenome(state.currentGenome, ui.hubCatalogSearch.value);
  ui.hubCatalog.replaceChildren();
  if (!hubs.length) {
    const empty = document.createElement("div");
    empty.className = "hub-catalog-empty";
    empty.textContent = `No catalog hubs match ${state.currentGenome}. You can still inspect a compatible hub URL above.`;
    ui.hubCatalog.append(empty);
    refreshPersistentScrollbars();
    return;
  }

  for (const hub of hubs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "hub-card";
    button.classList.toggle("selected", state.activeHub?.id === hub.id);

    const sourceRow = document.createElement("div");
    sourceRow.className = "hub-card-source";
    const source = document.createElement("span");
    source.className = hub.source.toLowerCase();
    source.textContent = hub.source;
    const count = document.createElement("small");
    count.textContent = hub.trackCount ? `${hub.trackCount.toLocaleString()} tracks` : hub.kind === "ucsc" ? "UCSC hub.txt" : "JSON data hub";
    sourceRow.append(source, count);

    const name = document.createElement("strong");
    name.textContent = hub.name;
    const description = document.createElement("small");
    description.textContent = hub.description;
    button.append(sourceRow, name, description);
    button.addEventListener("click", () => inspectPublicHub(hub));
    ui.hubCatalog.append(button);
  }
  refreshPersistentScrollbars();
}

function resetHubTrackBrowser() {
  state.hubRequestToken += 1;
  state.hubRequestController?.abort();
  state.hubRequestController = null;
  state.activeHub = null;
  state.publicHubTracks = [];
  state.selectedHubTracks.clear();
  state.selectedHubTrackConfigs.clear();
  state.hubPageOffset = 0;
  state.hubPageTotal = 0;
  ui.hubPrevPage.disabled = true;
  ui.hubNextPage.disabled = true;
  ui.hubTrackBrowser.hidden = true;
  ui.hubTrackEmpty.hidden = false;
  ui.hubTrackEmpty.className = "hub-track-empty";
  ui.hubTrackEmpty.replaceChildren();
  const icon = document.createElement("span");
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "≋";
  const title = document.createElement("strong");
  title.textContent = "Select a public hub";
  const detail = document.createElement("small");
  detail.textContent = "Its compatible tracks will appear here for search and selection.";
  ui.hubTrackEmpty.append(icon, title, detail);
  updateHubLoadButton();
  renderHubCatalog();
}

function showHubTrackMessage(title, detail, mode = "loading") {
  ui.hubTrackBrowser.hidden = true;
  ui.hubTrackEmpty.hidden = false;
  ui.hubTrackEmpty.className = `hub-track-empty ${mode}`.trim();
  ui.hubTrackEmpty.replaceChildren();
  const icon = document.createElement("span");
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = mode === "error" ? "!" : "G";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const copy = document.createElement("small");
  copy.textContent = detail;
  ui.hubTrackEmpty.append(icon, heading, copy);
}

async function inspectPublicHub(hub, options = {}) {
  const switchingHub = state.activeHub?.id !== hub.id;
  const preserveSelection = options.preserveSelection === true && !switchingHub;
  const offset = Math.max(0, Number(options.offset) || 0);
  const search = switchingHub ? "" : String(options.search ?? ui.hubTrackSearch.value).trim();
  state.hubRequestController?.abort();
  const controller = new AbortController();
  const requestToken = ++state.hubRequestToken;
  state.hubRequestController = controller;
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, HUB_REQUEST_TIMEOUT_MS);
  state.activeHub = hub;
  state.publicHubTracks = [];
  if (!preserveSelection) {
    state.selectedHubTracks.clear();
    state.selectedHubTrackConfigs.clear();
  }
  updateHubLoadButton();
  renderHubCatalog();
  if (switchingHub) ui.hubTrackSearch.value = "";
  showHubTrackMessage(search ? `Searching ${hub.name}...` : `Reading ${hub.name}...`, "The server is returning one lightweight page of tracks.");
  setStatus(`Reading ${hub.name}...`, "busy");

  try {
    let tracks;
    let skipped = 0;
    const query = new URLSearchParams({
      url: hub.url,
      genome: state.currentGenome,
      kind: hub.kind,
      q: search,
      offset: String(offset),
      limit: String(state.hubPageLimit),
    });
    const result = await fetchJSON(`${appURL("api/public-hub")}?${query}`, { signal: controller.signal });
    if (requestToken !== state.hubRequestToken) return;
    let total;
    let pageOffset;
    if (result.kind === "washu") {
      let normalized;
      ({ tracks: normalized, skipped } = normalizeWashUHub(result.payload, hub));
      normalized = filterHubTracks(normalized, search);
      total = normalized.length;
      pageOffset = Math.min(offset, Math.max(0, total - 1));
      tracks = normalized.slice(pageOffset, pageOffset + state.hubPageLimit);
    } else {
      tracks = Array.isArray(result.tracks) ? result.tracks : [];
      skipped = Number(result.skipped) || 0;
      total = Number(result.total) || 0;
      pageOffset = Number(result.offset) || 0;
    }

    if (!tracks.length && !search) throw new Error(`No supported tracks were found for ${state.currentGenome}`);
    state.publicHubTracks = tracks;
    state.hubPageOffset = pageOffset;
    state.hubPageTotal = total;
    ui.hubTrackEmpty.hidden = true;
    ui.hubTrackBrowser.hidden = false;
    $("#hub-source-label").textContent = `${hub.source} · ${hub.kind === "ucsc" ? "UCSC TRACK HUB" : "JSON DATA HUB"}`;
    $("#hub-name-label").textContent = hub.name;
    const truncated = result.truncated ? " · catalog limit reached" : "";
    $("#hub-track-count-label").textContent = `${total.toLocaleString()} compatible tracks${skipped ? ` · ${skipped.toLocaleString()} non-data entries omitted` : ""}${truncated}`;
    $("#hub-source-link").href = hub.url;
    renderHubTrackRows();
    refreshPersistentScrollbars();
    setStatus("Ready");
  } catch (error) {
    if (requestToken !== state.hubRequestToken || (error.name === "AbortError" && !timedOut)) return;
    state.publicHubTracks = [];
    const message = timedOut ? "The public hub did not respond within 45 seconds" : error.message;
    showHubTrackMessage("Hub could not be read", `${message}. Verify that the source is online and contains ${state.currentGenome}.`, "error");
    setStatus("Public hub failed to load", "error");
    toast(`Could not read public hub: ${message}`, "error");
  } finally {
    window.clearTimeout(timeout);
    if (requestToken === state.hubRequestToken) state.hubRequestController = null;
  }
}

function inspectCustomHub() {
  const value = $("#custom-hub-url").value.trim();
  if (!value) return;
  let url;
  try {
    url = new URL(value).toString();
    if (!/^https?:$/.test(new URL(url).protocol)) throw new Error("Only HTTP and HTTPS URLs are supported");
  } catch (error) {
    toast(`Invalid track hub URL: ${error.message}`, "error");
    return;
  }
  const hub = {
    id: `custom-${url}`,
    source: "Custom",
    kind: inferHubKind(url),
    name: url.split(/[/?#]/).filter(Boolean).at(-1) || "Custom track hub",
    description: "Custom public track hub",
    assemblies: [state.currentGenome],
    url,
  };
  inspectPublicHub(hub);
}

function visibleHubTracks() {
  return [...state.publicHubTracks];
}

function updateHubLoadButton() {
  const count = state.selectedHubTracks.size;
  ui.loadHubTracks.disabled = count === 0;
  ui.loadHubTracks.textContent = count ? `Load ${count.toLocaleString()} selected` : "Load selected tracks";
}

function renderHubTrackRows() {
  const matches = visibleHubTracks();
  const shown = matches.slice(0, HUB_TRACK_RENDER_LIMIT);
  ui.hubTrackList.replaceChildren();
  const start = state.hubPageTotal ? state.hubPageOffset + 1 : 0;
  const end = state.hubPageOffset + shown.length;
  ui.hubTrackStatus.textContent = `Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${state.hubPageTotal.toLocaleString()} · ${state.selectedHubTracks.size.toLocaleString()} selected`;
  ui.hubPrevPage.disabled = state.hubPageOffset <= 0;
  ui.hubNextPage.disabled = end >= state.hubPageTotal;

  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "hub-catalog-empty";
    empty.textContent = "No tracks match this filter.";
    ui.hubTrackList.append(empty);
    refreshPersistentScrollbars();
    return;
  }

  for (const track of shown) {
    const row = document.createElement("label");
    row.className = "hub-track-row";
    row.classList.toggle("selected", state.selectedHubTracks.has(track.id));
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedHubTracks.has(track.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedHubTracks.add(track.id);
      else state.selectedHubTracks.delete(track.id);
      if (checkbox.checked) state.selectedHubTrackConfigs.set(track.id, track);
      else state.selectedHubTrackConfigs.delete(track.id);
      row.classList.toggle("selected", checkbox.checked);
      updateHubLoadButton();
      ui.hubTrackStatus.textContent = `Showing ${start.toLocaleString()}–${end.toLocaleString()} of ${state.hubPageTotal.toLocaleString()} · ${state.selectedHubTracks.size.toLocaleString()} selected`;
    });

    const copy = document.createElement("span");
    copy.className = "hub-track-copy";
    const name = document.createElement("strong");
    name.textContent = track.name;
    const metadata = document.createElement("small");
    metadata.textContent = [track.sample, track.assay, track.hubGroup].filter(Boolean).join(" · ") || track.url;
    copy.append(name, metadata);
    const format = document.createElement("span");
    format.className = "hub-format-badge";
    format.textContent = track.format || "track";
    row.append(checkbox, copy, format);
    ui.hubTrackList.append(row);
  }
  refreshPersistentScrollbars();
}

function selectShownHubTracks() {
  for (const track of visibleHubTracks().slice(0, HUB_TRACK_RENDER_LIMIT)) {
    state.selectedHubTracks.add(track.id);
    state.selectedHubTrackConfigs.set(track.id, track);
  }
  updateHubLoadButton();
  renderHubTrackRows();
}

function clearSelectedHubTracks() {
  state.selectedHubTracks.clear();
  state.selectedHubTrackConfigs.clear();
  updateHubLoadButton();
  renderHubTrackRows();
}

function searchActiveHubTracks() {
  window.clearTimeout(state.hubSearchTimer);
  state.hubSearchTimer = window.setTimeout(() => {
    if (!state.activeHub) return;
    inspectPublicHub(state.activeHub, {
      offset: 0,
      search: ui.hubTrackSearch.value,
      preserveSelection: true,
    });
  }, 320);
}

function changeHubPage(direction) {
  if (!state.activeHub) return;
  const offset = Math.max(0, state.hubPageOffset + direction * state.hubPageLimit);
  inspectPublicHub(state.activeHub, {
    offset,
    search: ui.hubTrackSearch.value,
    preserveSelection: true,
  });
}

async function loadSelectedHubTracks() {
  const selected = [...state.selectedHubTrackConfigs.values()];
  if (!selected.length) return;
  ui.loadHubTracks.disabled = true;
  setStatus(`Loading ${selected.length} public track${selected.length === 1 ? "" : "s"}...`, "busy");
  let loaded = 0;
  const failures = [];
  for (const track of selected) {
    try {
      await loadConfiguredTrack(track);
      loaded += 1;
    } catch (error) {
      failures.push(`${track.name}: ${error.message}`);
    }
  }
  syncTracks();
  if (loaded) closeDialog("url-dialog");
  if (failures.length) {
    setStatus("Some public tracks failed to load", "error");
    toast(`Loaded ${loaded}; ${failures.length} failed. ${failures[0]}`, "error");
  } else {
    setStatus("Ready");
    toast(`Loaded ${loaded} public track${loaded === 1 ? "" : "s"}`);
  }
  updateHubLoadButton();
}

async function loadCustomReference(event) {
  event.preventDefault();
  const id = $("#reference-id").value.trim();
  const name = $("#reference-name").value.trim() || id;
  const url = $("#reference-url").value.trim();
  const indexURL = $("#reference-index-url").value.trim();
  const cytobandURL = $("#reference-cytoband-url").value.trim();
  if (!id || !url) return;

  const reference = { id, name };
  const normalizedURL = localDataURL(url);
  if (/\.2bit(?:\?|$)/i.test(url)) reference.twoBitURL = normalizedURL;
  else {
    reference.fastaURL = normalizedURL;
    reference.indexURL = localDataURL(indexURL || `${url}.fai`);
  }
  if (cytobandURL) reference.cytobandURL = localDataURL(cytobandURL);

  try {
    await loadGenome(reference, name);
    closeDialog("reference-dialog");
    $("#reference-form").reset();
    toast(`Loaded custom reference ${name}`);
  } catch (error) {
    setStatus("Custom reference failed to load", "error");
    toast(`Could not load custom reference: ${error.message}`, "error");
  }
}

function currentProfileState() {
  if (!state.browser) throw new Error("Genome browser is not ready");
  const session = state.browser.toJSON();
  session.genomeCanvasHighlights = state.highlights.map(({ id, chr, start, end, color }) => ({ id, chr, start, end, color }));
  session.genomeCanvasCenterGuide = state.centerGuide;
  session.genomeCanvasHighlightColor = normalizeHexColor(ui.highlightColor.value, DEFAULT_HIGHLIGHT_COLOR);
  return session;
}

function profileDocument(name, profileState) {
  const now = new Date().toISOString();
  return {
    schema: "genome-canvas-profile",
    version: 1,
    name,
    createdAt: now,
    updatedAt: now,
    state: profileState,
  };
}

function safeDownloadName(value) {
  return String(value || "genome-canvas-profile")
    .trim()
    .replace(/[^a-z0-9._-]+/gi, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 90) || "genome-canvas-profile";
}

function downloadJSON(documentValue, filename) {
  const blob = new Blob([`${JSON.stringify(documentValue, null, 2)}\n`], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function profileMetadataText(profile) {
  const updated = profile.updatedAt ? new Date(profile.updatedAt).toLocaleString() : "Unknown date";
  return [
    profile.genome,
    profile.locus,
    `${profile.trackCount || 0} tracks`,
    `${profile.highlightCount || 0} highlights`,
    updated,
  ].filter(Boolean).join(" · ");
}

async function fetchProfile(profileId) {
  return fetchJSON(appURL(`api/profiles/${encodeURIComponent(profileId)}`));
}

async function renderFavoriteProfiles() {
  ui.profileList.innerHTML = '<div class="profile-empty"><b>☆</b><span>Reading saved profiles...</span></div>';
  try {
    const payload = await fetchJSON(appURL("api/profiles"));
    const profiles = Array.isArray(payload.profiles) ? payload.profiles : [];
    ui.profileList.replaceChildren();
    if (!profiles.length) {
      const empty = document.createElement("div");
      empty.className = "profile-empty";
      empty.innerHTML = "<b>☆</b><span>No favorite profiles saved yet.</span>";
      ui.profileList.append(empty);
      return;
    }

    for (const profile of profiles) {
      const row = document.createElement("article");
      row.className = "profile-row";
      const copy = document.createElement("div");
      copy.className = "profile-row-copy";
      const name = document.createElement("strong");
      name.textContent = profile.name;
      const metadata = document.createElement("small");
      metadata.textContent = profileMetadataText(profile);
      copy.append(name, metadata);

      const actions = document.createElement("div");
      actions.className = "profile-row-actions";
      const load = document.createElement("button");
      load.type = "button";
      load.textContent = "Load";
      load.addEventListener("click", () => loadFavoriteProfile(profile));
      const download = document.createElement("button");
      download.type = "button";
      download.textContent = "Download";
      download.addEventListener("click", async () => {
        try {
          const record = await fetchProfile(profile.id);
          downloadJSON(record, `${safeDownloadName(profile.name)}.genome-canvas.json`);
        } catch (error) {
          toast(`Could not download profile: ${error.message}`, "error");
        }
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "profile-delete";
      remove.textContent = "Delete";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Delete favorite profile “${profile.name}”?`)) return;
        try {
          await fetchJSON(appURL(`api/profiles/${encodeURIComponent(profile.id)}`), { method: "DELETE" });
          await renderFavoriteProfiles();
          toast(`Deleted ${profile.name}`);
        } catch (error) {
          toast(`Could not delete profile: ${error.message}`, "error");
        }
      });
      actions.append(load, download, remove);
      row.append(copy, actions);
      ui.profileList.append(row);
    }
  } catch (error) {
    ui.profileList.innerHTML = `<div class="profile-empty"><b>!</b><span>${escapeHTML(error.message)}</span></div>`;
  }
}

async function openFavoriteProfiles() {
  if (!ui.profileName.value) ui.profileName.value = `${state.currentGenome} favorite`;
  showDialog("profile-dialog");
  await renderFavoriteProfiles();
}

async function saveFavoriteProfile() {
  const name = ui.profileName.value.trim();
  if (!name) {
    ui.profileName.focus();
    toast("Enter a profile name", "error");
    return;
  }
  setStatus(`Saving favorite ${name}...`, "busy");
  try {
    await fetchJSON(appURL("api/profiles"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, state: currentProfileState() }),
    });
    await renderFavoriteProfiles();
    setStatus("Favorite profile saved");
    toast(`Saved favorite ${name}`);
  } catch (error) {
    setStatus("Favorite profile could not be saved", "error");
    toast(`Could not save profile: ${error.message}`, "error");
  }
}

async function applyProfileState(profileState, profileName = "Favorite profile") {
  const browserConfig = normalizeBrowserConfigURLs(JSON.parse(JSON.stringify(profileState)));
  const restoredHighlights = normalizeHighlights(browserConfig.genomeCanvasHighlights);
  const restoredCenterGuide = browserConfig.genomeCanvasCenterGuide === true;
  const restoredHighlightColor = normalizeHexColor(browserConfig.genomeCanvasHighlightColor, DEFAULT_HIGHLIGHT_COLOR);
  delete browserConfig.genomeCanvasHighlights;
  delete browserConfig.genomeCanvasCenterGuide;
  delete browserConfig.genomeCanvasHighlightColor;
  const deferredTracks = (browserConfig.tracks || []).filter(requiresCustomRenderer);
  if (deferredTracks.length) browserConfig.tracks = browserConfig.tracks.filter((track) => !requiresCustomRenderer(track));
  Object.assign(browserConfig, {
    showNavigation: false,
    showIdeogram: true,
    showRuler: true,
    showSVGButton: false,
    showCenterGuideButton: false,
    showCenterGuide: false,
    showCursorTrackGuide: true,
    showTrackLabels: true,
    showSampleNames: true,
    showGearColumn: false,
    queryParametersSupported: false,
  });

  setStatus(`Loading ${profileName}...`, "busy");
  await state.browser.loadSessionObject(browserConfig);
  installTrackReorderUI();
  await state.browser.layoutChange?.();
  for (const trackConfig of deferredTracks) await loadConfiguredTrack(trackConfig);
  state.highlights = restoredHighlights;
  state.highlightDraft = null;
  state.centerGuide = restoredCenterGuide;
  state.browser.doShowCenterLine = restoredCenterGuide;
  state.browser.setCenterLineVisibility?.(restoredCenterGuide);
  $("#center-guide-button").setAttribute("aria-pressed", String(restoredCenterGuide));
  ui.highlightColor.value = restoredHighlightColor;
  $(".highlight-color span").style.background = restoredHighlightColor;
  setCurrentGenome(state.browser.genome?.id || browserConfig.genome || browserConfig.reference?.id || state.currentGenome);
  applyLoadedTrackStyles();
  updateLocus();
  syncTracks();
  scheduleHighlightRender();
  setStatus(`${profileName} loaded`);
}

async function loadFavoriteProfile(profile) {
  if (!window.confirm(`Replace the current view with “${profile.name}”?`)) return;
  try {
    const record = await fetchProfile(profile.id);
    await applyProfileState(record.state, profile.name);
    closeDialog("profile-dialog");
    toast(`Loaded favorite ${profile.name}`);
  } catch (error) {
    setStatus("Favorite profile failed to load", "error");
    toast(`Could not load profile: ${error.message}`, "error");
  }
}

function downloadCurrentProfile() {
  const name = ui.profileName.value.trim() || `${state.currentGenome} favorite`;
  downloadJSON(profileDocument(name, currentProfileState()), `${safeDownloadName(name)}.genome-canvas.json`);
  toast("Current profile downloaded");
}

async function importFavoriteProfile(file) {
  if (!file) return;
  try {
    const parsed = JSON.parse(await file.text());
    const importedState = parsed?.schema === "genome-canvas-profile" ? parsed.state : (parsed.state || parsed);
    if (!importedState || !Array.isArray(importedState.tracks)) throw new Error("File is not a Genome Canvas or IGV session profile");
    const fallbackName = file.name.replace(/(?:\.genome-canvas)?\.json$/i, "") || "Imported profile";
    const name = ui.profileName.value.trim() || parsed.name || fallbackName;
    await fetchJSON(appURL("api/profiles"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, state: importedState }),
    });
    ui.profileName.value = name;
    await renderFavoriteProfiles();
    toast(`Imported favorite ${name}`);
  } catch (error) {
    toast(`Could not import profile: ${error.message}`, "error");
  } finally {
    $("#import-profile-file").value = "";
  }
}

async function shareCurrentView() {
  if (!state.browser) return;
  setStatus("Saving shared view...", "busy");
  try {
    const session = currentProfileState();
    const payload = await fetchJSON(appURL("api/sessions"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(session),
    });
    const url = new URL(window.location.origin + window.location.pathname);
    url.searchParams.set("session", payload.id);
    ui.shareUrl.value = url.toString();
    $("#share-created-label").textContent = `View ${payload.id}`;
    if (desktopMode) postDesktopMessage({ type: "share", url: url.toString(), id: payload.id });
    else showDialog("share-dialog");
    setStatus("Shared view saved");
  } catch (error) {
    setStatus("Shared view could not be saved", "error");
    toast(`Could not create share link: ${error.message}`, "error");
  }
}

async function copyShareLink() {
  try {
    await navigator.clipboard.writeText(ui.shareUrl.value);
    toast("Share link copied");
    $("#copy-share-button").textContent = "Copied";
    window.setTimeout(() => { $("#copy-share-button").textContent = "Copy"; }, 1500);
  } catch {
    ui.shareUrl.select();
    document.execCommand("copy");
    toast("Share link copied");
  }
}

// Reads the root <svg> size without parsing the whole document, which can be
// several megabytes for dense views.
function svgRootSize(svgText) {
  const root = svgText.slice(0, svgText.indexOf(">", svgText.indexOf("<svg")) + 1);
  const attribute = (name) => Number.parseFloat((root.match(new RegExp(`\\s${name}="([\\d.]+)`)) || [])[1]);
  const viewBox = ((root.match(/\sviewBox="([^"]+)"/) || [])[1] || "").split(/[\s,]+/).map(Number);
  return {
    width: attribute("width") || viewBox[2] || ui.viewer.clientWidth,
    height: attribute("height") || viewBox[3] || ui.viewer.scrollHeight,
  };
}

let exportInProgress = false;

// PNG exports have a transparent background: the SVG's backdrop and the full-width white rectangles IGV
// clears each track with become fill="none". Small white boxes (label halos, the track-name box) stay, so
// names remain readable where they cross features.
function transparentBackground(svgText, width) {
  const white = /fill="(?:white|#fff|#ffffff|rgb\(\s*255,\s*255,\s*255\s*\))"/i;
  return svgText.replace(/<rect\b[^>]*>/g, (tag) => {
    if (!white.test(tag)) return tag;
    const w = tag.match(/\bwidth="([\d.]+)(%?)"/);
    const full = /id="svg_output_backdrop"/.test(tag) || (w && (w[2] === "%" || Number(w[1]) >= width * 0.5));
    return full ? tag.replace(white, 'fill="none"') : tag;
  });
}

async function exportPNG() {
  if (!state.browser || exportInProgress) return;
  exportInProgress = true;
  const exportButton = $("#export-button");
  if (exportButton) exportButton.disabled = true;
  setStatus("Generating PNG...", "busy");
  // Let the status and disabled button paint before the synchronous SVG render.
  await new Promise((resolve) => window.requestAnimationFrame(() => window.setTimeout(resolve, 0)));
  try {
    // Exported figures always use the light (publication) canvas palette on a
    // transparent background. The export renders into its own SVG context, so
    // the screen needs no repaint.
    let svgText;
    const screenPalette = canvasTheme();
    setCanvasTheme(LIGHT_CANVAS);
    try {
      svgText = await state.browser.toSVG();
    } finally {
      setCanvasTheme(screenPalette);
    }
    const { width, height } = svgRootSize(svgText);
    svgText = transparentBackground(svgText, width);
    const scale = Math.min(2, 8192 / Math.max(width, height));
    const blob = new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
    const objectURL = URL.createObjectURL(blob);
    const image = new Image();
    image.decoding = "async";
    image.src = objectURL;
    await image.decode();

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(objectURL);

    const png = await new Promise((resolve, reject) => canvas.toBlob((result) => result ? resolve(result) : reject(new Error("PNG encoder returned no data")), "image/png"));
    const downloadURL = URL.createObjectURL(png);
    const anchor = document.createElement("a");
    const locus = String(state.browser.currentLoci()).replace(/[^a-z0-9_-]+/gi, "_").slice(0, 80);
    anchor.href = downloadURL;
    anchor.download = `genome-canvas_${state.currentGenome}_${locus}.png`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(downloadURL), 1000);
    setStatus("PNG exported");
    toast("PNG screenshot exported");
  } catch (error) {
    setStatus("PNG export failed", "error");
    toast(`Could not export PNG: ${error.message}`, "error");
  } finally {
    exportInProgress = false;
    if (exportButton) exportButton.disabled = false;
  }
}

async function searchLocation(queryValue) {
  const query = String(queryValue || "").trim();
  if (!query || !state.browser) return;
  setStatus(`Searching for ${query}...`, "busy");
  try {
    if (/^rs\d+$/i.test(query)) {
      const parameters = new URLSearchParams({ q: query, genome: state.currentGenome });
      const variant = await fetchJSON(`${appURL("api/variant-search")}?${parameters}`);
      await state.browser.search(variant.locus);
      ui.locus.value = variant.rsid;
      toast(`${variant.rsid} · ${variant.chromosome}:${variant.position.toLocaleString()} · ${variant.source}`);
    } else {
      await state.browser.search(query);
    }
    setStatus("Ready");
  } catch (error) {
    setStatus("Location not found", "error");
    toast(`Could not locate "${query}": ${error.message}`, "error");
  }
}

// Tracks named in a link (e.g. enhancer-gene loops from the CRISPR explorer); one already open is not added again.
async function loadLinkedTracks(tracks) {
  const open = new Set(trackViews().map((view) => view.track?.url || view.track?.config?.url).filter(Boolean));
  let failed = 0;
  for (const config of tracks) {
    if (open.has(config.url)) continue;
    try {
      await loadConfiguredTrack(config);
    } catch (error) {
      failed += 1;
      toast(`Could not load ${config.name}: ${error.message}`, "error");
    }
  }
  if (tracks.length) syncTracks();
  return failed;
}

// A genomecanvas:// link (or ?locus= query) arriving while the browser is open: jump there in place, keeping the tracks.
async function openLinkedLocation(search) {
  const link = linkedView(search);
  if (link.genome && link.genome !== state.currentGenome && [...ui.genome.options].some((option) => option.value === link.genome)) {
    await selectDesktopGenome(link.genome);
  }
  await loadLinkedTracks(link.tracks);
  if (link.locus) await searchLocation(link.locus);
  if (link.highlights.length) {
    const stamp = Date.now();
    state.highlights.push(...link.highlights.map((highlight, index) => ({ ...highlight, id: `link-highlight-${stamp}-${index}` })));
    scheduleHighlightRender();
    notifyDesktopHost();
  }
}

async function selectDesktopGenome(genome) {
  if (!state.browser || !genome || genome === state.currentGenome) return;
  const option = [...ui.genome.options].find((item) => item.value === genome);
  try {
    await loadGenome(genome, option?.textContent || genome);
  } catch (error) {
    setStatus("Reference genome failed to load", "error");
    toast(`Could not load reference genome: ${error.message}`, "error");
  }
}

function selectDesktopWorkspace(workspaceId) {
  const workspace = state.workspaceCatalog.find((item) => item.id === workspaceId);
  if (workspace && workspace.id !== state.workspace?.id) enterWorkspace(workspace);
}

async function createDesktopWorkspace(nameValue) {
  const name = String(nameValue || "").trim();
  if (!name) return false;
  setStatus(`Creating project ${name}...`, "busy");
  try {
    await fetchJSON(appURL("api/workspaces"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    window.location.reload();
    return true;
  } catch (error) {
    setStatus("Project could not be created", "error");
    toast(`Could not create project: ${error.message}`, "error");
    return false;
  }
}

async function deleteDesktopWorkspace(workspaceId) {
  const workspace = state.workspaceCatalog.find((item) => item.id === workspaceId);
  if (!workspace || workspace.kind !== "manual") return false;
  setStatus(`Deleting project ${workspace.name}...`, "busy");
  try {
    await fetchJSON(appURL(`api/workspaces/${encodeURIComponent(workspace.id)}`), { method: "DELETE" });
    if (state.workspace?.id === workspace.id) {
      state.workspace = null;
      window.location.reload();
    } else {
      await loadWorkspaceCatalog();
      setStatus("Project deleted");
    }
    return true;
  } catch (error) {
    setStatus("Project could not be deleted", "error");
    toast(`Could not delete project: ${error.message}`, "error");
    return false;
  }
}

function removeDesktopTrack(index) {
  const view = desktopTrackViews()[Number(index)];
  if (!view?.track || !state.browser) return false;
  state.browser.removeTrack(view.track);
  syncTracks();
  return true;
}

function moveDesktopTrack(fromIndex, toIndex) {
  if (!state.browser) return false;
  const views = desktopTrackViews();
  const from = Number(fromIndex);
  const to = Math.max(0, Math.min(views.length - 1, Number(toIndex)));
  if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || from >= views.length || from === to) return false;
  const tracks = views.map((view) => view.track);
  const [moved] = tracks.splice(from, 1);
  tracks.splice(to, 0, moved);
  tracks.forEach((track, index) => { track.order = (index + 1) * 10; });
  state.browser.reorderTracks?.();
  state.browser.layoutChange?.();
  syncTracks();
  return true;
}

function setDesktopHighlightColor(colorValue) {
  const color = normalizeHexColor(colorValue, DEFAULT_HIGHLIGHT_COLOR);
  ui.highlightColor.value = color;
  $(".highlight-color span").style.background = color;
  if (state.highlightDraft) {
    state.highlightDraft.color = color;
    scheduleHighlightRender();
  }
  notifyDesktopHost();
}

window.GenomeCanvasDesktop = Object.freeze({
  snapshot: desktopStateSnapshot,
  search(query) { void searchLocation(query); return true; },
  // false while the browser is still starting, so the host loads the link URL instead
  openLink(query) { if (!state.browser) return false; void openLinkedLocation(query); return true; },
  selectGenome(genome) { void selectDesktopGenome(genome); return true; },
  selectWorkspace(workspaceId) { selectDesktopWorkspace(workspaceId); return true; },
  createWorkspace(name) { void createDesktopWorkspace(name); return true; },
  deleteWorkspace(workspaceId) { void deleteDesktopWorkspace(workspaceId); return true; },
  zoomIn() { state.browser?.zoomIn(); return true; },
  zoomOut() { state.browser?.zoomOut(); return true; },
  openServerFiles() { void openFileBrowser(); return true; },
  openSharedTracks() { void openSharedTracks(); return true; },
  openPublicData() { openPublicDataDialog(); return true; },
  openCustomReference() { showDialog("reference-dialog"); return true; },
  openFavorites() { void openFavoriteProfiles(); return true; },
  openWorkspace() { showWorkspaceGate(); return true; },
  share() { void shareCurrentView(); return true; },
  exportPNG() { void exportPNG(); return true; },
  reload() { window.location.reload(); return true; },
  // The native client owns the theme in desktop mode; this applies it without
  // saving a browser-side override.
  setTheme(theme) {
    if (theme !== "light" && theme !== "dark") return false;
    if (applyThemeFromHost) applyThemeFromHost(theme);
    else {
      document.documentElement.dataset.theme = theme;
      syncCanvasTheme();
    }
    return true;
  },
  setHighlight(enabled) { setHighlightMode(Boolean(enabled)); return true; },
  setHighlightColor(color) { setDesktopHighlightColor(color); return true; },
  clearHighlights() { clearHighlights(); return true; },
  removeTrack(index) { return removeDesktopTrack(index); },
  moveTrack(from, to) { return moveDesktopTrack(from, to); },
});

function escapeHTML(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function bindEvents() {
  installPersistentScrollbars();
  $$("dialog").forEach((dialog) => {
    dialog.addEventListener("close", cleanDialogState);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  $$('[data-close-dialog]').forEach((button) => button.addEventListener("click", () => closeDialog(button.dataset.closeDialog)));

  $("#server-files-button").addEventListener("click", openFileBrowser);
  $("#shared-tracks-button").addEventListener("click", openSharedTracks);
  ui.sharedSearch.addEventListener("input", renderSharedTracks);
  ui.sharedGenomeOnly.addEventListener("change", renderSharedTracks);
  ui.sharedNewOnly.addEventListener("change", renderSharedTracks);
  $("#shared-refresh-button").addEventListener("click", () => loadSharedTracks(true));
  ui.sharedSelectAll.addEventListener("click", () => {
    for (const item of visibleSharedTracks()) state.selectedShared.set(sharedKey(item), item);
    renderSharedTracks();
  });
  ui.sharedClear.addEventListener("click", () => {
    state.selectedShared.clear();
    renderSharedTracks();
  });
  ui.loadShared.addEventListener("click", loadSelectedSharedTracks);
  $("#url-track-button").addEventListener("click", openPublicDataDialog);
  $("#custom-reference-button").addEventListener("click", () => showDialog("reference-dialog"));
  $("#favorites-button").addEventListener("click", openFavoriteProfiles);
  $("#help-button").addEventListener("click", () => showDialog("help-dialog"));
  $("#share-button").addEventListener("click", shareCurrentView);
  $("#copy-share-button").addEventListener("click", copyShareLink);
  $("#export-button").addEventListener("click", exportPNG);
  $("#refresh-tracks").addEventListener("click", syncTracks);
  $("#load-selected-button").addEventListener("click", loadSelectedFiles);
  ui.selectAllFiles.addEventListener("click", selectAllVisibleFiles);
  ui.clearSelectedFiles.addEventListener("click", clearSelectedFiles);
  $("#url-form").addEventListener("submit", loadRemoteTrack);
  $("#reference-form").addEventListener("submit", loadCustomReference);
  $("#profile-form").addEventListener("submit", (event) => {
    event.preventDefault();
    saveFavoriteProfile();
  });
  $$("[data-public-tab]").forEach((button) => button.addEventListener("click", () => switchPublicDataTab(button.dataset.publicTab)));
  ui.hubCatalogSearch.addEventListener("input", renderHubCatalog);
  ui.hubTrackSearch.addEventListener("input", searchActiveHubTracks);
  $("#inspect-custom-hub").addEventListener("click", inspectCustomHub);
  $("#custom-hub-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      inspectCustomHub();
    }
  });
  $("#select-visible-hub-tracks").addEventListener("click", selectShownHubTracks);
  $("#clear-hub-tracks").addEventListener("click", clearSelectedHubTracks);
  ui.hubPrevPage.addEventListener("click", () => changeHubPage(-1));
  ui.hubNextPage.addEventListener("click", () => changeHubPage(1));
  ui.loadHubTracks.addEventListener("click", loadSelectedHubTracks);
  $("#save-profile-button").addEventListener("click", saveFavoriteProfile);
  $("#download-current-profile").addEventListener("click", downloadCurrentProfile);
  $("#import-profile-button").addEventListener("click", () => $("#import-profile-file").click());
  $("#import-profile-file").addEventListener("change", (event) => importFavoriteProfile(event.target.files?.[0]));

  ui.rootSelect.addEventListener("change", () => {
    state.selectedFiles.clear();
    updateSelectionSummary();
    loadDirectory(ui.rootSelect.value, "");
  });
  const jumpToPath = () => {
    const requested = ui.serverPath.value.trim();
    loadDirectory(ui.rootSelect.value, requested === "/" ? "" : requested);
  };
  $("#path-jump-button").addEventListener("click", jumpToPath);
  ui.serverPath.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      jumpToPath();
    }
  });
  ui.genome.addEventListener("change", async () => {
    if (ui.genome.value === "__custom__") {
      ui.genome.value = state.currentGenome;
      showDialog("reference-dialog");
      return;
    }
    try {
      await loadGenome(ui.genome.value, ui.genome.selectedOptions[0].textContent);
    } catch (error) {
      ui.genome.value = state.currentGenome;
      setStatus("Reference genome failed to load", "error");
      toast(`Could not load reference genome: ${error.message}`, "error");
    }
  });
  ui.locusForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void searchLocation(ui.locus.value);
  });
  $("#zoom-in").addEventListener("click", () => state.browser?.zoomIn());
  $("#zoom-out").addEventListener("click", () => state.browser?.zoomOut());
  $("#center-guide-button").addEventListener("click", (event) => {
    state.centerGuide = !state.centerGuide;
    state.browser.doShowCenterLine = state.centerGuide;
    state.browser.setCenterLineVisibility?.(state.centerGuide);
    event.currentTarget.setAttribute("aria-pressed", String(state.centerGuide));
  });
  ui.highlightMode.addEventListener("click", () => setHighlightMode(!state.highlightMode));
  ui.highlightColor.addEventListener("input", () => {
    const color = normalizeHexColor(ui.highlightColor.value, DEFAULT_HIGHLIGHT_COLOR);
    $(".highlight-color span").style.background = color;
    if (state.highlightDraft) {
      state.highlightDraft.color = color;
      scheduleHighlightRender();
    }
    notifyDesktopHost();
  });
  ui.clearHighlights.addEventListener("click", clearHighlights);

  // Wide layouts collapse the rail in place (remembered per browser); compact
  // layouts slide it over the canvas.
  const compactLayout = window.matchMedia("(max-width: 900px)");
  const railToggle = $("#rail-toggle");
  const railIsOpen = () => (compactLayout.matches
    ? ui.workspace.classList.contains("rail-open")
    : !ui.workspace.classList.contains("rail-collapsed"));
  const updateRailToggle = () => railToggle.setAttribute("aria-expanded", String(railIsOpen()));
  const setRail = (open) => {
    if (compactLayout.matches) {
      ui.workspace.classList.toggle("rail-open", open);
    } else {
      ui.workspace.classList.toggle("rail-collapsed", !open);
      try { window.localStorage.setItem(RAIL_COLLAPSED_KEY, open ? "0" : "1"); } catch { /* storage disabled */ }
      // IGV.js sizes its viewports from a window resize listener.
      window.dispatchEvent(new Event("resize"));
    }
    updateRailToggle();
  };
  try {
    ui.workspace.classList.toggle("rail-collapsed", window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1");
  } catch { /* storage disabled */ }
  compactLayout.addEventListener?.("change", () => {
    ui.workspace.classList.remove("rail-open");
    updateRailToggle();
  });
  updateRailToggle();
  railToggle.addEventListener("click", () => setRail(!railIsOpen()));
  $("#close-rail").addEventListener("click", () => setRail(false));

  // Theme: follow the operating system until the user picks one. Picking the
  // theme the system already uses clears the override again.
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const themeToggle = $("#theme-toggle");
  const applyTheme = (theme) => {
    document.documentElement.dataset.theme = theme;
    syncCanvasTheme();
    const next = theme === "dark" ? "light" : "dark";
    themeToggle.title = `Switch to ${next} theme`;
    themeToggle.setAttribute("aria-label", themeToggle.title);
    $('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#2e3440" : "#f8f9fb");
  };
  const savedTheme = () => {
    try { return window.localStorage.getItem(THEME_KEY); } catch { return null; }
  };
  applyThemeFromHost = applyTheme;
  applyTheme(savedTheme() || (systemDark.matches ? "dark" : "light"));
  systemDark.addEventListener?.("change", (event) => {
    if (!savedTheme()) applyTheme(event.matches ? "dark" : "light");
  });
  themeToggle.addEventListener("click", () => {
    const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try {
      if (theme === (systemDark.matches ? "dark" : "light")) window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, theme);
    } catch { /* storage disabled */ }
    applyTheme(theme);
  });

  $("#rail-scrim").addEventListener("click", () => setRail(false));

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && !$("dialog[open]")) {
      event.preventDefault();
      ui.locus.focus();
      ui.locus.select();
      return;
    }
    if ($("dialog[open]") || /input|select|textarea/i.test(document.activeElement?.tagName)) return;
    if (event.key === "/") {
      event.preventDefault();
      ui.locus.focus();
      ui.locus.select();
    } else if (event.key === "+" || event.key === "=") state.browser?.zoomIn();
    else if (event.key === "-") state.browser?.zoomOut();
    else if (event.key === "Escape" && state.highlightMode) setHighlightMode(false);
  });
}

initializeApplication().catch((error) => {
  document.body.classList.remove("workspace-pending");
  document.body.classList.add("workspace-selecting");
  ui.loading.innerHTML = `<span class="loader-mark">!</span><div><strong>Genome browser could not start</strong><small>${escapeHTML(error.message)}</small></div>`;
  $("#workspace-gate-status").textContent = error.message;
  setStatus("Startup failed", "error");
  toast(`Startup failed: ${error.message}`, "error");
});
