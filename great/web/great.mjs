// GREAT page of Omics Canvas: submit regions, browse the enrichment tables (GREAT's views, filters and columns),
// term details with region-gene tables and graphs, exports, the API notes and the admin panel.
// The analysis runs on the GREAT server (great/server.py, /great/api/*); workspaces and server files are Genome
// Canvas's (its API at CANVAS, where the workspace cookie applies).

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
// GREAT's API next to this page; Genome Canvas (workspaces, server files, the genome browser) at CANVAS
const CANVAS = document.querySelector('meta[name="genome-canvas-base"]')?.content.replace(/^@.*@$/, "/genome-canvas/") || "/genome-canvas/";
const api = (path) => new URL(`api/${path}`, new URL(".", location.href)).toString();
const appURL = (path) => new URL(`${CANVAS}${path}`, location.href).toString();

const ASSEMBLY_NAMES = {
  hg38: ["Human", "GRCh38 (hg38)"], hg19: ["Human", "GRCh37 (hg19)"],
  mm10: ["Mouse", "GRCm38 (mm10)"], mm9: ["Mouse", "NCBI37 (mm9)"],
};
const TERM_LINKS = [
  [/^GO:/, (id) => `https://amigo.geneontology.org/amigo/term/${id}`],
  [/^HP:/, (id) => `https://hpo.jax.org/browse/term/${id}`],
  [/^MP:/, (id) => `https://www.informatics.jax.org/vocab/mp_ontology/${id}`],
  [/^ENSG/, (id) => `https://www.ensembl.org/Homo_sapiens/Gene/Summary?g=${id}`],
  [/^ENSMUSG/, (id) => `https://www.ensembl.org/Mus_musculus/Gene/Summary?g=${id}`],
];
// Bars of the association graphs: data colors, the same in both themes and in exports
const SERIES_COLORS = ["#4c7a86", "#d08770"];
const ROW_CHOICES = [20, 50, 100, 500, 0];

const state = {
  status: null,
  workspaceId: null,
  job: null,
  rows: {},
  sort: {},
  shown: {},
  columns: loadColumns(),
  linkedTrack: null,
};

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.innerHTML = `<div>${escapeHTML(message)}</div>`;
  $("#gr-toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 9000 : 4500);
}

async function fetchJSON(url, options = {}) {
  // GREAT's API learns the Genome Canvas workspace from this header (as the hub's pages send it)
  const headers = { ...(options.headers || {}), ...(state.workspaceId && url.startsWith(api("")) ? { "X-Vizhub-Workspace": state.workspaceId } : {}) };
  const response = await fetch(url, { ...options, headers });
  let payload = null;
  try { payload = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const error = new Error(payload?.error || `${response.status} ${response.statusText}`);
    error.retryAfter = payload?.retryAfter;
    throw error;
  }
  return payload;
}

function formatNumber(value, digits = 3) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value !== "number") return String(value);
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude < 1e-3 || magnitude >= 1e6) return value.toExponential(digits - 1).replace("e", "e");
  return Number(value.toPrecision(digits)).toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function formatP(value) {
  if (value === 0) return "0";
  return value < 0.001 ? value.toExponential(2) : value.toPrecision(3);
}

function download(blob, name) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 4000);
}

// ---------------------------------------------------------------- theme

function setupTheme() {
  $("#gr-theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try { localStorage.setItem("genome-canvas:theme", dark ? "dark" : "light"); } catch { /* storage disabled */ }
  });
}

// ---------------------------------------------------------------- the form

function renderAssemblies() {
  const box = $("#gr-assembly");
  const assemblies = state.status.assemblies;
  box.replaceChildren(...assemblies.map((assembly, index) => {
    const [species, build] = ASSEMBLY_NAMES[assembly.name] || [assembly.species, assembly.name];
    const label = document.createElement("label");
    label.innerHTML = `<input type="radio" name="assembly" value="${assembly.name}" ${index === 0 ? "checked" : ""}>
      <span><b>${escapeHTML(species)}</b> ${escapeHTML(build)}<small>${formatNumber(assembly.genes)} genes</small></span>`;
    return label;
  }));
  if (!assemblies.length) {
    box.innerHTML = '<p class="gr-error">No GREAT data on this server yet: run <code>tools/fetch_great_data.py</code> (see genome-canvas/README.md).</p>';
    $("#gr-submit").disabled = true;
  }
  const params = new URLSearchParams(location.search);
  const wanted = params.get("genome");
  const match = assemblies.find((a) => a.name === wanted);
  if (match) $(`input[name="assembly"][value="${match.name}"]`).checked = true;
  const built = assemblies.map((a) => a.built).filter(Boolean).sort().pop();
  const versions = new Map();
  for (const assembly of assemblies) {
    for (const o of assembly.ontologies) {
      const date = /\d{4}-\d{2}-\d{2}/.exec(o.version || "");
      if (date) versions.set(o.name, date[0]);
    }
  }
  $("#gr-data-status").textContent = assemblies.length
    ? `Genes, TSSs and curated regulatory domains: GREAT 4.0.4. Ontologies (built ${built}): ${[...versions].map(([name, date]) => `${name} ${date}`).join(", ")}.`
    : "";
}

function sourcePicker(container) {
  const prefix = container.dataset.prefix;
  const tabs = [["file", "File"], ["paste", "Paste"], ["server", "Server file"], ["url", "URL"]];
  if (prefix === "fg" && state.linkedTrack) tabs.unshift(["track", "Genome Canvas track"]);
  container.innerHTML = `
    <div class="gr-tabs" role="tablist">${tabs.map(([key, label], i) => `<button type="button" role="tab" data-tab="${key}" aria-selected="${i === 0}">${label}</button>`).join("")}</div>
    <div class="gr-tab-panels">
      ${state.linkedTrack && prefix === "fg" ? `<div data-panel="track"><p class="gr-picked">${escapeHTML(state.linkedTrack.name)} <small>${escapeHTML(state.linkedTrack.url)}</small></p></div>` : ""}
      <div data-panel="file"><label class="gr-drop"><input type="file" name="${prefix}File">
        <span><svg class="icon"><use href="#i-upload"/></svg> <b>Choose a file</b> or drop it here</span>
        <small>BED, narrowPeak, CSV/TSV with a header, MACS .xls, region lists; .gz/.zip allowed</small></label></div>
      <div data-panel="paste" hidden><textarea name="${prefix}Text" rows="8" spellcheck="false" placeholder="chr1\t1000\t2000\tpeak1\nchr2\t5000\t5600\nchr7:155,600,000-155,601,000"></textarea></div>
      <div data-panel="server" hidden><div class="gr-browser">
        <div class="gr-browser-bar"><select data-role="root"></select><input data-role="path" type="text" placeholder="folder/file.bed"></div>
        <div class="gr-browser-list" data-role="list"><p class="gr-muted">Select a workspace in Genome Canvas to browse its folders.</p></div>
      </div></div>
      <div data-panel="url" hidden><input type="url" name="${prefix}URL" placeholder="https://…/peaks.bed.gz (a public address)"></div>
    </div>`;
  const select = (key) => {
    for (const tab of $$("[data-tab]", container)) tab.setAttribute("aria-selected", String(tab.dataset.tab === key));
    for (const panel of $$("[data-panel]", container)) panel.hidden = panel.dataset.panel !== key;
    container.dataset.selected = key;
    if (key === "server") void browseServer(container);
  };
  for (const tab of $$("[data-tab]", container)) tab.addEventListener("click", () => select(tab.dataset.tab));
  select(tabs[0][0]);
  const drop = $(".gr-drop", container);
  const fileInput = $('input[type="file"]', container);
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    $("span", drop).innerHTML = file ? `<b>${escapeHTML(file.name)}</b> ${formatNumber(file.size / 1048576, 2)} MB` : "<b>Choose a file</b> or drop it here";
    if (file && prefix === "fg" && !$("#gr-fg-name").value) $("#gr-fg-name").placeholder = file.name;
  });
  drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    if (event.dataTransfer.files.length) { fileInput.files = event.dataTransfer.files; fileInput.dispatchEvent(new Event("change")); }
  });
}

async function browseServer(container, path = null) {
  const rootSelect = $('[data-role="root"]', container);
  const list = $('[data-role="list"]', container);
  if (!rootSelect.options.length) {
    try {
      const config = await fetchJSON(appURL("api/config"));
      rootSelect.replaceChildren(...config.roots.map((root) => new Option(root.label, root.id)));
      rootSelect.value = config.defaultFileRoot || config.roots[0]?.id;
      path = path ?? config.defaultFilePath ?? "";
      rootSelect.addEventListener("change", () => browseServer(container, ""));
    } catch (error) {
      list.innerHTML = `<p class="gr-muted">${escapeHTML(error.message)}: select a workspace in <a href="${appURL("")}">Genome Canvas</a> first.</p>`;
      return;
    }
  }
  const pathInput = $('[data-role="path"]', container);
  if (path === null) path = container.dataset.folder || "";
  try {
    const listing = await fetchJSON(appURL(`api/files?root=${encodeURIComponent(rootSelect.value)}&path=${encodeURIComponent(path)}&regions=1`));
    container.dataset.folder = listing.path;
    const rows = [];
    if (listing.parent !== null) rows.push(`<button type="button" data-dir="${escapeHTML(listing.parent)}">↑ ..</button>`);
    for (const entry of listing.entries) {
      rows.push(entry.kind === "directory"
        ? `<button type="button" data-dir="${escapeHTML(entry.path)}">📁 ${escapeHTML(entry.name)}</button>`
        : `<button type="button" data-file="${escapeHTML(entry.path)}">${escapeHTML(entry.name)} <small>${formatNumber(entry.size / 1048576, 2)} MB</small></button>`);
    }
    list.innerHTML = rows.join("") || '<p class="gr-muted">No region files here.</p>';
    for (const button of $$("[data-dir]", list)) button.addEventListener("click", () => browseServer(container, button.dataset.dir));
    for (const button of $$("[data-file]", list)) button.addEventListener("click", () => {
      pathInput.value = button.dataset.file;
      for (const other of $$("[data-file]", list)) other.classList.toggle("picked", other === button);
    });
  } catch (error) {
    list.innerHTML = `<p class="gr-muted">${escapeHTML(error.message)}</p>`;
  }
}

function appendSource(form, prefix) {
  const container = $(`.gr-source[data-prefix="${prefix}"]`);
  const selected = container.dataset.selected;
  if (selected === "track") form.append(`${prefix}Track`, state.linkedTrack.url);
  else if (selected === "file") {
    const file = $('input[type="file"]', container).files[0];
    if (file) form.append(`${prefix}File`, file, file.name);
  } else if (selected === "paste") form.append(`${prefix}Text`, $("textarea", container).value);
  else if (selected === "server") {
    form.append(`${prefix}Root`, $('[data-role="root"]', container).value);
    form.append(`${prefix}Path`, $('[data-role="path"]', container).value.trim());
  } else if (selected === "url") form.append(`${prefix}URL`, $('input[type="url"]', container).value.trim());
}

async function submitForm(event) {
  event.preventDefault();
  const formElement = $("#gr-form");
  const data = new FormData();
  const value = (name) => formElement.elements[name]?.value;
  data.append("assembly", $('input[name="assembly"]:checked')?.value || "hg38");
  for (const name of ["rule", "upstream", "downstream", "span", "twoDistance", "oneDistance", "coordinates", "anchor"]) {
    const field = formElement.elements[name];
    data.append(name, field instanceof RadioNodeList ? field.value : value(name));
  }
  data.append("includeCuratedRegDoms", formElement.elements.includeCuratedRegDoms.checked ? "1" : "0");
  const bgChoice = formElement.elements.bgChoice.value;
  data.append("bgChoice", bgChoice);
  data.append("fgName", $("#gr-fg-name").value.trim() || (state.linkedTrack && $(".gr-source[data-prefix=fg]").dataset.selected === "track" ? state.linkedTrack.name : ""));
  appendSource(data, "fg");
  if (bgChoice !== "wholeGenome") appendSource(data, "bg");
  const button = $("#gr-submit");
  const status = $("#gr-submit-status");
  button.disabled = true;
  const started = Date.now();
  const timer = setInterval(() => { status.textContent = `Analyzing… ${Math.round((Date.now() - started) / 1000)} s (requests over the server's limits wait in a queue)`; }, 500);
  try {
    const result = await fetchJSON(api("jobs"), { method: "POST", body: data });
    location.hash = `job=${result.job.id}`;
  } catch (error) {
    status.textContent = "";
    toast(error.message, "error");
  } finally {
    clearInterval(timer);
    button.disabled = false;
    status.textContent = "";
  }
}

async function renderRecent() {
  try {
    const { jobs } = await fetchJSON(api("jobs"));
    $("#gr-recent-card").hidden = !jobs.length;
    $("#gr-recent-note").textContent = `kept ${state.status.limits.jobRetentionDays} days`;
    $("#gr-recent").innerHTML = `<thead><tr><th>Analysis</th><th>Assembly</th><th>Regions</th><th>Background</th><th>Submitted</th><th></th></tr></thead><tbody>${jobs.map((job) => `
      <tr><td><a href="#job=${job.id}">${escapeHTML(job.name)}</a></td><td>${job.assembly}</td>
      <td>${formatNumber(job.summary?.regions)}</td><td>${job.background === "regions" ? escapeHTML(job.backgroundName) : "whole genome"}</td>
      <td>${new Date(job.created * 1000).toLocaleString()}</td>
      <td><button class="icon-button ghost small" type="button" data-delete="${job.id}" title="Delete"><svg class="icon"><use href="#i-trash"/></svg></button></td></tr>`).join("")}</tbody>`;
    for (const button of $$("[data-delete]", $("#gr-recent"))) button.addEventListener("click", async () => {
      await fetchJSON(api(`jobs/${button.dataset.delete}`), { method: "DELETE" }).catch((error) => toast(error.message, "error"));
      void renderRecent();
    });
  } catch {
    $("#gr-recent-card").hidden = true;
  }
}

// ---------------------------------------------------------------- results

const BINOM = {
  // whole-genome rows: ID, name, genome fraction, expected, observed, fold, set coverage, binom p, total genes,
  // hyper expected, observed genes, hyper fold, gene set coverage, term gene coverage, hyper p
  id: 0, name: 1, fraction: 2, expected: 3, observed: 4, fold: 5, coverage: 6, p: 7, totalGenes: 8,
  hyperExpected: 9, observedGenes: 10, hyperFold: 11, geneSetCoverage: 12, termCoverage: 13, hyperP: 14,
};
const BACKGROUND = {
  // ID, name, background region hits, expected, observed, fold, set coverage, term coverage, fg genes, bg genes,
  // genes annotated, p
  id: 0, name: 1, background: 2, expected: 3, observed: 4, fold: 5, coverage: 6, termCoverage: 7, fgGenes: 8,
  bgGenes: 9, totalGenes: 10, p: 11,
};

function columnsFor(background) {
  if (background) {
    return [
      { key: "rank", label: "Rank", get: (r) => r.rank, fmt: String, sort: 1, show: true },
      { key: "p", label: "Raw p-value", get: (r) => r.p, fmt: formatP, sort: 1, show: true, sig: "pSig" },
      { key: "bonf", label: "Bonferroni p", get: (r) => r.bonf, fmt: formatP, sort: 1, show: false },
      { key: "fdr", label: "FDR q", get: (r) => r.fdr, fmt: formatP, sort: 1, show: true, sig: "pSig" },
      { key: "fold", label: "Fold enrichment", get: (r) => r.row[BACKGROUND.fold], fmt: (v) => formatNumber(v, 3), sort: -1, show: true },
      { key: "expected", label: "Expected", get: (r) => r.row[BACKGROUND.expected], fmt: (v) => formatNumber(v, 3), sort: -1, show: false },
      { key: "observed", label: "Observed region hits", get: (r) => r.row[BACKGROUND.observed], fmt: formatNumber, sort: -1, show: true },
      { key: "background", label: "Background region hits", get: (r) => r.row[BACKGROUND.background], fmt: formatNumber, sort: -1, show: true },
      { key: "coverage", label: "Region set coverage", get: (r) => r.row[BACKGROUND.coverage], fmt: percent, sort: -1, show: true },
      { key: "termCoverage", label: "Region term coverage", get: (r) => r.row[BACKGROUND.termCoverage], fmt: percent, sort: -1, show: false },
      { key: "fgGenes", label: "Genes hit by test set", get: (r) => r.row[BACKGROUND.fgGenes], fmt: formatNumber, sort: -1, show: true },
      { key: "bgGenes", label: "Genes hit by background", get: (r) => r.row[BACKGROUND.bgGenes], fmt: formatNumber, sort: -1, show: false },
      { key: "totalGenes", label: "Genes annotated", get: (r) => r.row[BACKGROUND.totalGenes], fmt: formatNumber, sort: -1, show: true },
    ];
  }
  return [
    { key: "rank", label: "Binom rank", get: (r) => r.rank, fmt: String, sort: 1, show: true },
    { key: "p", label: "Binom raw p", get: (r) => r.p, fmt: formatP, sort: 1, show: true, sig: "pSig" },
    { key: "bonf", label: "Binom Bonferroni p", get: (r) => r.bonf, fmt: formatP, sort: 1, show: false },
    { key: "fdr", label: "Binom FDR q", get: (r) => r.fdr, fmt: formatP, sort: 1, show: true, sig: "pSig" },
    { key: "fold", label: "Binom fold enrichment", get: (r) => r.row[BINOM.fold], fmt: (v) => formatNumber(v, 3), sort: -1, show: true },
    { key: "expected", label: "Binom expected", get: (r) => r.row[BINOM.expected], fmt: (v) => formatNumber(v, 3), sort: -1, show: false },
    { key: "observed", label: "Binom observed region hits", get: (r) => r.row[BINOM.observed], fmt: formatNumber, sort: -1, show: true },
    { key: "fraction", label: "Binom genome fraction", get: (r) => r.row[BINOM.fraction], fmt: percent, sort: -1, show: false },
    { key: "coverage", label: "Binom region set coverage", get: (r) => r.row[BINOM.coverage], fmt: percent, sort: -1, show: true },
    { key: "hrank", label: "Hyper rank", get: (r) => r.hrank, fmt: String, sort: 1, show: true },
    { key: "hp", label: "Hyper raw p", get: (r) => r.hp, fmt: formatP, sort: 1, show: false, sig: "hSig" },
    { key: "hbonf", label: "Hyper Bonferroni p", get: (r) => r.hbonf, fmt: formatP, sort: 1, show: false },
    { key: "hfdr", label: "Hyper FDR q", get: (r) => r.hfdr, fmt: formatP, sort: 1, show: true, sig: "hSig" },
    { key: "hfold", label: "Hyper fold enrichment", get: (r) => r.row[BINOM.hyperFold], fmt: (v) => formatNumber(v, 3), sort: -1, show: true },
    { key: "hexpected", label: "Hyper expected", get: (r) => r.row[BINOM.hyperExpected], fmt: (v) => formatNumber(v, 3), sort: -1, show: false },
    { key: "genes", label: "Hyper observed gene hits", get: (r) => r.row[BINOM.observedGenes], fmt: formatNumber, sort: -1, show: true },
    { key: "totalGenes", label: "Hyper total genes", get: (r) => r.row[BINOM.totalGenes], fmt: formatNumber, sort: -1, show: true },
    { key: "geneSetCoverage", label: "Hyper gene set coverage", get: (r) => r.row[BINOM.geneSetCoverage], fmt: percent, sort: -1, show: true },
    { key: "termCoverage", label: "Hyper term gene coverage", get: (r) => r.row[BINOM.termCoverage], fmt: percent, sort: -1, show: false },
  ];
}

function percent(value) {
  return `${formatNumber(value * 100, 3)}%`;
}

function loadColumns() {
  try { return JSON.parse(localStorage.getItem("genome-canvas:great-columns") || "{}"); } catch { return {}; }
}

function saveColumns() {
  try { localStorage.setItem("genome-canvas:great-columns", JSON.stringify(state.columns)); } catch { /* storage disabled */ }
}

function isBackground() {
  return state.job?.background === "regions";
}

/** GREAT's corrections over the tested terms: rank, Bonferroni (m p), FDR q (m p / rank). */
function corrected(entries, key) {
  const order = entries.map((_, i) => i).sort((a, b) => entries[a][key] - entries[b][key] || a - b);
  const m = entries.length;
  order.forEach((index, position) => {
    const entry = entries[index];
    const rank = position + 1;
    entry[`${key}Rank`] = rank;
    entry[`${key}Bonf`] = Math.min(1, entry[key] * m);
    entry[`${key}Fdr`] = Math.min(1, entry[key] * m / rank);
  });
}

function filters() {
  const number = (id, fallback) => {
    const value = Number($(id).value);
    return $(id).value === "" || !Number.isFinite(value) ? fallback : value;
  };
  return {
    view: $("#gr-view-mode").value,
    threshold: number("#gr-threshold", 0.05),
    minFold: number("#gr-min-fold", 0),
    minGenes: number("#gr-min-genes", 0),
    minCount: number("#gr-min-count", 1),
    maxCount: number("#gr-max-count", Infinity),
    text: $("#gr-term-filter").value.trim().toLowerCase(),
  };
}

/** The rows one ontology shows under the current filters, with ranks and corrections over the tested terms. */
function tableEntries(key) {
  const rows = state.rows[key];
  if (!rows) return null;
  const background = isBackground();
  const f = filters();
  const total = background ? BACKGROUND.totalGenes : BINOM.totalGenes;
  const tested = rows.filter((row) => row[total] >= f.minCount && row[total] <= f.maxCount)
    .map((row) => ({ row, p: background ? row[BACKGROUND.p] : row[BINOM.p], hp: background ? null : row[BINOM.hyperP] }));
  corrected(tested, "p");
  if (!background) corrected(tested, "hp");
  for (const entry of tested) {
    Object.assign(entry, { rank: entry.pRank, bonf: entry.pBonf, fdr: entry.pFdr, hrank: entry.hpRank, hbonf: entry.hpBonf, hfdr: entry.hpFdr });
    entry.pSig = entry.fdr <= f.threshold;
    entry.hSig = !background && entry.hfdr <= f.threshold;
    entry.both = entry.pSig && (background || entry.hSig);
  }
  const genes = background ? BACKGROUND.fgGenes : BINOM.observedGenes;
  const shown = tested.filter((entry) => {
    const row = entry.row;
    if (row[BINOM.observed] <= 0) return false;
    if (f.view === "both" && !entry.both) return false;
    if (f.view === "binom" && !entry.pSig) return false;
    if (row[BINOM.fold] < f.minFold || row[genes] < f.minGenes) return false;
    if (f.text && !String(row[0]).toLowerCase().includes(f.text) && !String(row[1]).toLowerCase().includes(f.text)) return false;
    return true;
  });
  const sort = state.sort[key] || { key: "rank", dir: 1 };
  const column = columnsFor(background).find((c) => c.key === sort.key) || columnsFor(background)[0];
  shown.sort((a, b) => ((column.get(a) ?? 0) - (column.get(b) ?? 0)) * sort.dir || a.rank - b.rank);
  return { shown, tested: tested.length };
}

async function showJob(jobId, focus = {}) {
  $("#gr-view-new").hidden = true;
  $("#gr-view-job").hidden = false;
  if (state.job?.id !== jobId) {
    state.job = null;
    state.rows = {};
    $("#gr-ontologies").innerHTML = '<div class="gr-card"><p class="gr-muted">Loading…</p></div>';
    try {
      state.job = (await fetchJSON(api(`jobs/${jobId}`))).job;
    } catch (error) {
      $("#gr-ontologies").innerHTML = `<div class="gr-card"><p class="gr-error">${escapeHTML(error.message)}</p><p><a href="./">Start a new analysis</a></p></div>`;
      return;
    }
    renderJobHead();
    const background = isBackground();
    $("#gr-view-mode").innerHTML = background
      ? '<option value="binom">Significant (hypergeometric over regions)</option><option value="all">All terms (ignore significance)</option>'
      : '<option value="both">Significant by both tests</option><option value="binom">Significant by region-based binomial</option><option value="all">All terms (ignore significance)</option>';
    $("#gr-min-genes").closest("label").firstChild.textContent = background ? "Test-set gene hits ≥ " : "Observed gene hits ≥ ";
    renderOntologySections();
    await Promise.all(state.job.summary.ontologies.map(async (o) => {
      try {
        state.rows[o.key] = (await fetchJSON(api(`jobs/${jobId}/ontologies/${o.key}`))).rows;
      } catch (error) {
        state.rows[o.key] = [];
        toast(`${o.name}: ${error.message}`, "error");
      }
      renderTable(o.key);
    }));
  }
  if (focus.ontology && focus.term) void openTerm(focus.ontology, focus.term);
}

function renderJobHead() {
  const job = state.job;
  const s = job.summary;
  const [species, build] = ASSEMBLY_NAMES[job.assembly] || ["", job.assembly];
  $("#gr-job-kicker").textContent = `GREAT ${job.version} · ${species} ${build} · ${new Date(job.created * 1000).toLocaleString()}`;
  $("#gr-job-title").textContent = job.name;
  $("#gr-job-rule").textContent = `${job.rule}. Background: ${job.background === "regions" ? `${job.backgroundName} (${formatNumber(s.backgroundRegions)} regions)` : "whole genome"}.`;
  const stats = [
    [formatNumber(s.regions), "regions"],
    [formatNumber(s.associatedRegions), `associated with genes (${percent(s.associatedRegions / Math.max(1, s.regions))})`],
    [formatNumber(s.genesHit), `of ${formatNumber(s.genes)} genes hit`],
    [`${job.seconds ?? "–"} s`, "analysis"],
  ];
  $("#gr-job-stats").innerHTML = stats.map(([value, label]) => `<div><b>${value}</b><span>${escapeHTML(label)}</span></div>`).join("");
  $("#gr-job-warnings").innerHTML = (job.warnings || []).map((w) => `<p class="gr-warning">${escapeHTML(w)}</p>`).join("");
  renderReport();
  const canvas = new URL(appURL(""));
  canvas.searchParams.set("genome", job.assembly);
  canvas.searchParams.append("track", `${job.name} (GREAT)|${new URL(api(`jobs/${job.id}/regions.bed`)).pathname}`);
  $("#gr-open-canvas").href = canvas.toString();
  $("#gr-graphs").innerHTML = "";
  const g = s.graphs;
  const associations = g.absoluteDistance.reduce((a, b) => a + b, 0);
  $("#gr-graphs").append(
    chartBox("great-genes-per-region", barChart({ title: "Number of associated genes per region", categories: ["0", "1", "2", "> 2"],
      xLabel: "Genes per region", yLabel: "Regions (%)", series: [{ name: "Regions", values: g.genesPerRegion, total: s.regions }] })),
    chartBox("great-distance-orientation", barChart({ title: "Region-gene associations by orientation and distance to TSS",
      categories: ["≤ −500", "−500 to −50", "−50 to −5", "−5 to 0", "0 to 5", "5 to 50", "50 to 500", "> 500"],
      xLabel: "Distance to TSS (kb)", yLabel: "Region-gene associations (%)", series: [{ name: "Associations", values: g.signedDistance, total: associations }] })),
    chartBox("great-distance", barChart({ title: "Region-gene associations by absolute distance to TSS",
      categories: ["0 to 5", "5 to 50", "50 to 500", "> 500"], xLabel: "Absolute distance to TSS (kb)",
      yLabel: "Region-gene associations (%)", series: [{ name: "Associations", values: g.absoluteDistance, total: associations }] })),
  );
}

function renderReport() {
  const job = state.job;
  const part = (title, report) => {
    if (!report) return "";
    const issues = report.issues.map((issue) => `<li><b>${formatNumber(issue.count)}</b> ${escapeHTML(issue.text)}${issue.examples.length ? `<code>${issue.examples.map(escapeHTML).join("<br>")}</code>` : ""}</li>`).join("");
    const renamed = report.renamed.length ? `<li>Chromosome names changed: ${report.renamed.map(([a, b]) => `<code>${escapeHTML(a)}</code> → <code>${escapeHTML(b)}</code>`).join(", ")}</li>` : "";
    const columns = Object.entries(report.columns || {}).map(([k, v]) => `${k}: <code>${escapeHTML(v)}</code>`).join(", ");
    return `<div class="gr-report-part"><h3>${title}</h3><ul>
      ${report.notes.map((n) => `<li>${escapeHTML(n)}</li>`).join("")}
      <li>Columns used: ${columns}</li>
      <li><b>${formatNumber(report.regions)}</b> regions from ${formatNumber(report.lines)} data lines${report.duplicates ? `; ${formatNumber(report.duplicates)} repeat a region already listed (kept, as GREAT does)` : ""}${report.summits ? `; ${formatNumber(report.summits)} with a summit` : ""}</li>
      ${renamed}${issues}</ul></div>`;
  };
  $("#gr-report-body").innerHTML = part("Test regions", job.report) + part("Background regions", job.backgroundReport);
  $("#gr-report").open = Boolean(job.report?.issues?.length || job.backgroundReport?.issues?.length);
}

function renderOntologySections() {
  const container = $("#gr-ontologies");
  container.innerHTML = "";
  let category = null;
  for (const o of state.job.summary.ontologies) {
    if (o.category !== category) {
      category = o.category;
      const heading = document.createElement("h2");
      heading.className = "gr-category";
      heading.textContent = category;
      container.append(heading);
    }
    const section = document.createElement("section");
    section.className = "gr-card gr-ontology";
    section.dataset.ontology = o.key;
    section.innerHTML = `
      <div class="gr-card-head"><h2>${escapeHTML(o.name)}</h2><span class="gr-muted" data-role="count">Loading…</span>
        <div class="gr-table-tools">
          <label>Rows <select data-role="rows">${ROW_CHOICES.map((n) => `<option value="${n}">${n || "All"}</option>`).join("")}</select></label>
          <details class="gr-columns"><summary>Columns</summary><div data-role="columns"></div></details>
          <button class="quiet-button small" type="button" data-role="export"><svg class="icon"><use href="#i-download"/></svg>Shown .tsv</button>
          <a class="quiet-button small" data-role="all" href="${api(`jobs/${state.job.id}/export.tsv?ontology=${o.key}`)}"><svg class="icon"><use href="#i-download"/></svg>All .tsv</a>
        </div></div>
      <div class="gr-table-wrap"><table class="gr-table gr-enrichment"></table></div>`;
    container.append(section);
    $('[data-role="rows"]', section).value = String(state.shown[o.key] ?? 20);
    $('[data-role="rows"]', section).addEventListener("change", (event) => { state.shown[o.key] = Number(event.target.value); renderTable(o.key); });
    $('[data-role="export"]', section).addEventListener("click", () => exportShown("tsv", [o.key]));
  }
}

function visibleColumns() {
  const background = isBackground();
  const saved = state.columns[background ? "background" : "wholeGenome"] || {};
  return columnsFor(background).filter((c) => saved[c.key] ?? c.show);
}

function renderTable(key) {
  const section = $(`.gr-ontology[data-ontology="${key}"]`);
  if (!section) return;
  const result = tableEntries(key);
  if (!result) return;
  const { shown, tested } = result;
  const info = state.job.summary.ontologies.find((o) => o.key === key);
  const limit = state.shown[key] ?? 20;
  const visible = limit ? shown.slice(0, limit) : shown;
  $('[data-role="count"]', section).textContent = `${formatNumber(shown.length)} terms shown of ${formatNumber(tested)} tested${/\d{4}-\d{2}-\d{2}/.test(info.version || "") ? ` · release ${/\d{4}-\d{2}-\d{2}/.exec(info.version)[0]}` : ""}`;
  const columns = visibleColumns();
  const sort = state.sort[key] || { key: "rank", dir: 1 };
  const background = isBackground();
  $('[data-role="columns"]', section).innerHTML = columnsFor(background).map((c) => `<label><input type="checkbox" data-column="${c.key}" ${columns.includes(c) ? "checked" : ""}> ${escapeHTML(c.label)}</label>`).join("");
  for (const box of $$("[data-column]", section)) box.addEventListener("change", () => {
    const group = background ? "background" : "wholeGenome";
    state.columns[group] = { ...(state.columns[group] || {}), [box.dataset.column]: box.checked };
    saveColumns();
    for (const o of state.job.summary.ontologies) renderTable(o.key);
  });
  const table = $(".gr-enrichment", section);
  table.innerHTML = `<thead><tr><th class="gr-term-col">Term</th>${columns.map((c) => `<th data-sort="${c.key}" class="${sort.key === c.key ? (sort.dir > 0 ? "asc" : "desc") : ""}">${escapeHTML(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${visible.map((entry) => `<tr>
      <td class="gr-term-col"><a href="#" data-term="${escapeHTML(entry.row[0])}" class="${entry.both ? "gr-both" : ""}">${escapeHTML(entry.row[1])}</a><small>${escapeHTML(entry.row[0])}</small></td>
      ${columns.map((c) => `<td class="${c.sig && entry[c.sig] ? "gr-sig" : ""}">${c.fmt(c.get(entry))}</td>`).join("")}</tr>`).join("")
      || `<tr><td colspan="${columns.length + 1}" class="gr-muted gr-empty">No terms pass the filters${filters().view !== "all" ? "; try “All terms” or a larger FDR threshold" : ""}.</td></tr>`}</tbody>`;
  for (const th of $$("th[data-sort]", table)) th.addEventListener("click", () => {
    const column = columnsFor(background).find((c) => c.key === th.dataset.sort);
    const current = state.sort[key];
    state.sort[key] = { key: column.key, dir: current?.key === column.key ? -current.dir : column.sort };
    renderTable(key);
  });
  for (const link of $$("[data-term]", table)) link.addEventListener("click", (event) => {
    event.preventDefault();
    void openTerm(key, link.dataset.term);
  });
}

function renderAllTables() {
  for (const o of state.job?.summary?.ontologies || []) renderTable(o.key);
}

// ---------------------------------------------------------------- exports

function exportShown(kind, keys = null) {
  const columns = visibleColumns();
  const sections = [];
  for (const o of state.job.summary.ontologies) {
    if (keys && !keys.includes(o.key)) continue;
    const result = tableEntries(o.key);
    if (!result) continue;
    const limit = state.shown[o.key] ?? 20;
    sections.push({ name: o.name, entries: limit ? result.shown.slice(0, limit) : result.shown });
  }
  const job = state.job;
  const stamp = `great-${job.id}${keys?.length === 1 ? `-${keys[0]}` : ""}-shown`;
  if (kind === "tsv") {
    const lines = [`# GREAT ${job.version} (Genome Canvas)\t${job.assembly}\t${job.rule}\t${job.name}`,
      ["Ontology", "ID", "Term", ...columns.map((c) => c.label)].join("\t")];
    for (const section of sections) for (const entry of section.entries) {
      lines.push([section.name, entry.row[0], entry.row[1], ...columns.map((c) => c.get(entry))].join("\t"));
    }
    download(new Blob([lines.join("\n") + "\n"], { type: "text/tab-separated-values" }), `${stamp}.tsv`);
    return;
  }
  const htmlTables = sections.map((section) => `<h2>${escapeHTML(section.name)}</h2><table><thead><tr><th>Term</th><th>ID</th>${columns.map((c) => `<th>${escapeHTML(c.label)}</th>`).join("")}</tr></thead><tbody>
    ${section.entries.map((entry) => `<tr><td>${entry.both ? "<b>" : ""}${escapeHTML(entry.row[1])}${entry.both ? "</b>" : ""}</td><td>${escapeHTML(entry.row[0])}</td>${columns.map((c) => `<td>${c.sig && entry[c.sig] ? "<b>" : ""}${c.fmt(c.get(entry))}${c.sig && entry[c.sig] ? "</b>" : ""}</td>`).join("")}</tr>`).join("")}</tbody></table>`).join("");
  const page = `<!doctype html><html><head><meta charset="utf-8"><title>GREAT ${escapeHTML(job.name)}</title><style>
    body{font:13px Arial,Helvetica,sans-serif;margin:24px;color:#2e3440}table{border-collapse:collapse;margin-bottom:24px}
    th,td{border:1px solid #d8dee9;padding:4px 7px;text-align:right}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
    th{background:#eceff4}h1{font-size:18px}h2{font-size:15px;margin-top:28px}</style></head><body>
    <h1>GREAT: ${escapeHTML(job.name)}</h1><p>${escapeHTML(job.assembly)} · ${escapeHTML(job.rule)} · ${formatNumber(job.summary.regions)} regions · view: ${escapeHTML($("#gr-view-mode").selectedOptions[0].textContent)}, FDR ≤ ${filters().threshold}, fold ≥ ${filters().minFold}</p>
    ${htmlTables}</body></html>`;
  download(new Blob([page], { type: "text/html" }), `${stamp}.html`);
}

// ---------------------------------------------------------------- graphs (SVG, Arial)

function barChart({ title, categories, series, xLabel, yLabel, width = 420, height = 270 }) {
  const margin = { top: 34, right: 12, bottom: 66, left: 50 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const percents = series.map((s) => s.values.map((v) => (s.total ? (100 * v) / s.total : 0)));
  const maximum = Math.max(1, ...percents.flat());
  const step = [1, 2, 5, 10, 20, 25].find((candidate) => maximum / candidate <= 5) || 25;
  const top = Math.min(100, Math.ceil(maximum / step) * step);
  const band = plotWidth / categories.length;
  const barWidth = Math.min(34, (band * 0.72) / series.length);
  const y = (value) => margin.top + plotHeight - (value / top) * plotHeight;
  const parts = [];
  for (let tick = 0; tick <= top + 1e-9; tick += step) {
    parts.push(`<line x1="${margin.left}" x2="${width - margin.right}" y1="${y(tick)}" y2="${y(tick)}" stroke="#d8dee9" stroke-width="1"/>`,
      `<text x="${margin.left - 6}" y="${y(tick) + 4}" text-anchor="end" font-size="10">${Math.round(tick)}</text>`);
  }
  categories.forEach((category, i) => {
    const center = margin.left + band * (i + 0.5);
    series.forEach((s, j) => {
      const x = center - (barWidth * series.length) / 2 + j * barWidth;
      const value = percents[j][i];
      parts.push(`<rect x="${x.toFixed(1)}" y="${y(value).toFixed(1)}" width="${(barWidth - 2).toFixed(1)}" height="${(margin.top + plotHeight - y(value)).toFixed(1)}" fill="${SERIES_COLORS[j % SERIES_COLORS.length]}"><title>${escapeHTML(s.name)}: ${formatNumber(s.values[i])} (${value.toFixed(1)}%)</title></rect>`);
      parts.push(`<text x="${(x + barWidth / 2 - 1).toFixed(1)}" y="${(y(value) - 4).toFixed(1)}" text-anchor="middle" font-size="9">${formatNumber(Math.round(s.values[i] * 2) / 2)}</text>`);
    });
    const [first, second] = category.split(" to ");  // "−500 to −50" on two lines
    parts.push(`<text x="${center}" y="${margin.top + plotHeight + 14}" text-anchor="middle" font-size="10">${escapeHTML(second ? `${first} to` : first)}${second ? `<tspan x="${center}" dy="11">${escapeHTML(second)}</tspan>` : ""}</text>`);
  });
  const legend = series.length > 1 ? series.map((s, j) => `<rect x="${margin.left + j * 130}" y="${height - 16}" width="10" height="10" fill="${SERIES_COLORS[j]}"/><text x="${margin.left + 14 + j * 130}" y="${height - 7}" font-size="10">${escapeHTML(s.name)}</text>`).join("") : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" font-family="Arial, Helvetica, sans-serif" fill="currentColor">
    <text x="${width / 2}" y="16" text-anchor="middle" font-size="12" font-weight="bold">${escapeHTML(title)}</text>
    ${parts.join("")}
    <line x1="${margin.left}" x2="${width - margin.right}" y1="${margin.top + plotHeight}" y2="${margin.top + plotHeight}" stroke="currentColor"/>
    <text x="${margin.left + plotWidth / 2}" y="${margin.top + plotHeight + 40}" text-anchor="middle" font-size="10.5">${escapeHTML(xLabel)}</text>
    <text transform="translate(13 ${margin.top + plotHeight / 2}) rotate(-90)" text-anchor="middle" font-size="10.5">${escapeHTML(yLabel)}</text>
    ${legend}</svg>`;
}

function chartBox(name, svg) {
  const box = document.createElement("figure");
  box.className = "gr-chart";
  box.innerHTML = `${svg}<figcaption><button type="button" class="quiet-button small" data-kind="svg">SVG</button><button type="button" class="quiet-button small" data-kind="png">PNG</button></figcaption>`;
  for (const button of $$("button", box)) button.addEventListener("click", () => exportChart($("svg", box), name, button.dataset.kind));
  return box;
}

function exportChart(svg, name, kind) {
  // exports use the light palette on a transparent background, as every Genome Canvas figure
  const text = new XMLSerializer().serializeToString(svg).replace('fill="currentColor"', 'fill="#2e3440"').replaceAll('stroke="currentColor"', 'stroke="#2e3440"');
  if (kind === "svg") { download(new Blob([text], { type: "image/svg+xml" }), `${name}.svg`); return; }
  const image = new Image();
  const width = Number(svg.getAttribute("width"));
  const height = Number(svg.getAttribute("height"));
  image.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = width * 3;
    canvas.height = height * 3;
    const context = canvas.getContext("2d");
    context.scale(3, 3);
    context.drawImage(image, 0, 0, width, height);
    canvas.toBlob((blob) => blob && download(blob, `${name}.png`), "image/png");
  };
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;
}

// ---------------------------------------------------------------- term details

async function openTerm(ontology, term) {
  const dialog = $("#gr-term-dialog");
  const job = state.job;
  const info = job.summary.ontologies.find((o) => o.key === ontology);
  $("#gr-term-kicker").textContent = info?.name || ontology;
  $("#gr-term-title").textContent = term;
  $("#gr-term-body").innerHTML = '<p class="gr-muted">Loading…</p>';
  if (!dialog.open) dialog.showModal();
  history.replaceState(null, "", `#job=${job.id}&ontology=${encodeURIComponent(ontology)}&term=${encodeURIComponent(term)}`);
  let details;
  try {
    details = await fetchJSON(api(`jobs/${job.id}/term?ontology=${encodeURIComponent(ontology)}&term=${encodeURIComponent(term)}`));
  } catch (error) {
    $("#gr-term-body").innerHTML = `<p class="gr-error">${escapeHTML(error.message)}</p>`;
    return;
  }
  $("#gr-term-title").textContent = details.name;
  const entry = tableEntries(ontology)?.shown.find((e) => e.row[0] === term) || null;
  const row = state.rows[ontology]?.find((r) => r[0] === term);
  const link = TERM_LINKS.find(([pattern]) => pattern.test(term));
  const background = isBackground();
  const stat = (label, value) => `<div><span>${escapeHTML(label)}</span><b>${value}</b></div>`;
  const stats = row ? (background
    ? [stat("Raw p", formatP(row[BACKGROUND.p])), stat("FDR q", entry ? formatP(entry.fdr) : "–"), stat("Fold enrichment", formatNumber(row[BACKGROUND.fold], 3)),
      stat("Test regions", formatNumber(row[BACKGROUND.observed])), stat("Background regions", formatNumber(row[BACKGROUND.background])), stat("Genes annotated", formatNumber(row[BACKGROUND.totalGenes]))]
    : [stat("Binom raw p", formatP(row[BINOM.p])), stat("Binom FDR q", entry ? formatP(entry.fdr) : "–"), stat("Region fold", formatNumber(row[BINOM.fold], 3)),
      stat("Regions", `${formatNumber(row[BINOM.observed])} <small>exp. ${formatNumber(row[BINOM.expected], 3)}</small>`), stat("Genome fraction", percent(row[BINOM.fraction])),
      stat("Hyper raw p", formatP(row[BINOM.hyperP])), stat("Genes hit", `${formatNumber(row[BINOM.observedGenes])} / ${formatNumber(row[BINOM.totalGenes])}`)]).join("") : "";
  const all = job.summary.graphs;
  const totalAll = all.absoluteDistance.reduce((a, b) => a + b, 0);
  const totalTerm = details.graphs.absoluteDistance.reduce((a, b) => a + b, 0);
  const region = (r) => `${r.chrom}:${(r.start + 1).toLocaleString("en-US")}-${r.end.toLocaleString("en-US")}`;
  const canvasLink = (locus) => {
    const url = new URL(appURL(""));
    url.searchParams.set("genome", job.assembly);
    url.searchParams.set("locus", locus);
    url.searchParams.append("track", `${details.name} (GREAT)|${new URL(api(`jobs/${job.id}/term.bed?ontology=${encodeURIComponent(ontology)}&term=${encodeURIComponent(term)}`)).pathname}${new URL(api(`jobs/${job.id}/term.bed?ontology=${encodeURIComponent(ontology)}&term=${encodeURIComponent(term)}`)).search}`);
    return url.toString();
  };
  $("#gr-term-body").innerHTML = `
    <p class="gr-term-meta"><code>${escapeHTML(term)}</code> · ${formatNumber(details.annotatedGenes)} genes annotated${link ? ` · <a href="${link[1](term)}" target="_blank" rel="noopener">term page ↗</a>` : ""}</p>
    <div class="gr-term-stats">${stats}</div>
    <div class="gr-graphs gr-term-graphs"></div>
    <div class="gr-tabs" role="tablist"><button type="button" data-tab="region" aria-selected="true">Region → genes (${formatNumber(details.byRegion.length)})</button><button type="button" data-tab="gene" aria-selected="false">Gene → regions (${formatNumber(details.byGene.length)})</button></div>
    <div class="gr-table-wrap gr-term-table" data-panel="region"><table class="gr-table"><thead><tr><th>Region</th><th>Genes of this term (distance to TSS)</th></tr></thead><tbody>
      ${details.byRegion.map((r) => `<tr><td><a href="${canvasLink(region(r))}" target="_blank" rel="noopener">${escapeHTML(r.region)}</a>${r.region.replaceAll(",", "") === region(r).replaceAll(",", "") ? "" : ` <small>${region(r)}</small>`}</td><td>${r.genes.map(([gene, d]) => `${escapeHTML(gene)} <small>(${d > 0 ? "+" : ""}${d.toLocaleString("en-US")})</small>`).join(", ")}</td></tr>`).join("")}</tbody></table></div>
    <div class="gr-table-wrap gr-term-table" data-panel="gene" hidden><table class="gr-table"><thead><tr><th>Gene</th><th>Regulatory domain</th><th>Regions</th><th>Regions (distance to TSS)</th></tr></thead><tbody>
      ${details.byGene.map((g) => `<tr><td><b>${escapeHTML(g.gene)}</b> <small>${escapeHTML(g.id)}</small></td><td><a href="${canvasLink(`${g.chrom}:${g.domain[0] + 1}-${g.domain[1]}`)}" target="_blank" rel="noopener">${g.chrom}:${(g.domain[0] + 1).toLocaleString("en-US")}-${g.domain[1].toLocaleString("en-US")}</a></td><td>${g.regions.length}</td><td>${g.regions.slice(0, 50).map(([r, d]) => `${escapeHTML(r)} <small>(${d > 0 ? "+" : ""}${d.toLocaleString("en-US")})</small>`).join(", ")}${g.regions.length > 50 ? ` … ${g.regions.length - 50} more` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  $(".gr-term-graphs").append(
    chartBox(`great-${term}-regions-per-gene`, barChart({ title: "Number of associated regions per gene", categories: ["1", "2", "3", "4", "≥ 5"],
      xLabel: "Regions per gene", yLabel: "Genes of the term (%)", series: [{ name: "Genes", values: details.graphs.regionsPerGene, total: details.byGene.length }] })),
    chartBox(`great-${term}-distance-orientation`, barChart({ title: "Binned by orientation and distance to TSS",
      categories: ["≤ −500", "−500 to −50", "−50 to −5", "−5 to 0", "0 to 5", "5 to 50", "50 to 500", "> 500"], xLabel: "Distance to TSS (kb)",
      yLabel: "Region-gene associations (%)", series: [{ name: "All regions", values: all.signedDistance, total: totalAll }, { name: "This term", values: details.graphs.signedDistance, total: totalTerm }] })),
    chartBox(`great-${term}-distance`, barChart({ title: "Binned by absolute distance to TSS", categories: ["0 to 5", "5 to 50", "50 to 500", "> 500"],
      xLabel: "Absolute distance to TSS (kb)", yLabel: "Region-gene associations (%)",
      series: [{ name: "All regions", values: all.absoluteDistance, total: totalAll }, { name: "This term", values: details.graphs.absoluteDistance, total: totalTerm }] })),
  );
  for (const tab of $$("[data-tab]", $("#gr-term-body"))) tab.addEventListener("click", () => {
    for (const other of $$("[data-tab]", $("#gr-term-body"))) other.setAttribute("aria-selected", String(other === tab));
    for (const panel of $$("[data-panel]", $("#gr-term-body"))) panel.hidden = panel.dataset.panel !== tab.dataset.tab;
  });
  const query = `ontology=${encodeURIComponent(ontology)}&term=${encodeURIComponent(term)}`;
  $("#gr-term-region-tsv").href = api(`jobs/${job.id}/associations.tsv?table=region&${query}`);
  $("#gr-term-gene-tsv").href = api(`jobs/${job.id}/associations.tsv?table=gene&${query}`);
  $("#gr-term-bed").href = api(`jobs/${job.id}/term.bed?${query}`);
  $("#gr-term-bed").download = `great-${term.replace(/[^A-Za-z0-9]+/g, "_")}.bed`;
  const first = details.byRegion[0];
  $("#gr-term-canvas").href = first ? canvasLink(region({ ...first, start: Math.max(0, first.start - 50000), end: first.end + 50000 })) : "#";
  $("#gr-term-note").textContent = `${formatNumber(details.byRegion.length)} regions near ${formatNumber(details.byGene.length)} genes of this term`;
}

// ---------------------------------------------------------------- API notes

function renderAPI() {
  const base = state.status.greatBase;
  const limits = state.status.limits;
  $("#gr-api-r").textContent = `library(rGREAT)
source("${base}/rgreat.R")      # points rGREAT at this server
job <- submitGreatJob(gr, genome = "hg38", request_interval = 0)
tables <- getEnrichmentTables(job, category = "GO")
regions <- getRegionGeneAssociations(job)
plotRegionGeneAssociations(job)`;
  $("#gr-api-batch").textContent = `# results of every ontology as a table (GREAT's batch mode)
curl "${base}/public/cgi-bin/greatStart.php?outputType=batch&requestSpecies=hg38&requestURL=https%3A%2F%2Fexample.org%2Fpeaks.bed"
# or post the file itself
curl -F outputType=batch -F requestSpecies=hg38 -F requestFile=@peaks.bed.gz \\
     "${base}/public/cgi-bin/greatStart.php" > great.tsv`;
  $("#gr-api-python").textContent = `import requests, io, pandas as pd
r = requests.post("${base}/public/cgi-bin/greatStart.php",
                  data={"outputType": "batch", "requestSpecies": "hg38",
                        "rule": "basalPlusExt", "adv_upstream": 5, "adv_downstream": 1, "adv_span": 1000},
                  files={"requestFile": open("peaks.bed", "rb")})
r.raise_for_status()
lines = r.text.splitlines()
table = pd.read_csv(io.StringIO("\\n".join([lines[3].lstrip("# ")] + [l for l in lines[4:] if not l.startswith("#")])), sep="\\t")`;
  $("#gr-limits").textContent = `This server: ${limits.minIntervalSeconds ? `one analysis per ${limits.minIntervalSeconds} s per client` : "no minimum interval"}${limits.maxJobsPerHour ? `, ${limits.maxJobsPerHour} per hour` : ""}; up to ${formatNumber(limits.maxRegions)} regions, ${limits.maxUploadMB} MB; results kept ${limits.jobRetentionDays} days.${limits.apiEnabled ? "" : " The batch interface is turned off."}`;
  $("#gr-footer").innerHTML = `GREAT ${state.status.version} reimplemented in Genome Canvas from GREAT's published code and documentation (McLean CY et al., <i>Nat Biotechnol</i> 28:495, 2010; Tanigawa Y et al., <i>Hum Genet</i> 2022). Please cite GREAT when you use these results.`;
}

// ---------------------------------------------------------------- admin

const SETTING_HELP = {
  minIntervalSeconds: ["Minimum interval between analyses from one client (s)", "GREAT asked for 60 s. rGREAT also waits request_interval (60 s) itself unless told 0."],
  maxJobsPerHour: ["Analyses per client per hour", "0 = no limit"],
  maxConcurrentJobs: ["Analyses running at once", "each runs in its own worker process"],
  maxWaitSeconds: ["Queue wait before refusing (s)", "requests over a limit wait this long, then get HTTP 429 with Retry-After"],
  maxRegions: ["Test regions per analysis", "GREAT: 500,000"],
  maxBackgroundRegions: ["Background regions", "GREAT: 1,000,000"],
  maxUploadMB: ["File size limit (MB, decompressed)", "GREAT: 50 MB"],
  jobRetentionDays: ["Keep results (days)", "GREAT: 1 day; older ones are deleted every 10 minutes"],
  maxStorageMB: ["Storage limit for results (MB)", "above it the oldest analyses are deleted; 0 = no limit"],
  exportTermLimit: ["Terms per ontology in batch exports", "0 = all; GREAT returned the top 500"],
  allowRemoteURLs: ["Fetch requestURL / URL inputs", "public addresses only"],
  apiEnabled: ["GREAT's programming interfaces (rGREAT, greatStart.php)", ""],
  exemptClients: ["Clients never rate limited", "IP addresses, one per line"],
  blockedClients: ["Blocked clients", "IP addresses, one per line"],
};

async function openAdmin() {
  const dialog = $("#gr-admin-dialog");
  let data;
  try { data = await fetchJSON(api("settings")); } catch (error) { toast(error.message, "error"); return; }
  renderAdminFields(data.settings, data.defaults);
  dialog.showModal();
  await Promise.all([refreshAdminStatus(), refreshStoredJobs()]);
  const timer = setInterval(() => (dialog.open ? refreshAdminStatus() : clearInterval(timer)), 3000);
}

function renderAdminFields(settings, defaults) {
  $("#gr-admin-fields").innerHTML = Object.entries(settings).map(([key, value]) => {
    const [label, help] = SETTING_HELP[key] || [key, ""];
    const def = defaults?.[key];
    let control;
    if (typeof value === "boolean") control = `<input type="checkbox" name="${key}" ${value ? "checked" : ""}>`;
    else if (Array.isArray(value)) control = `<textarea name="${key}" rows="3" spellcheck="false">${escapeHTML(value.join("\n"))}</textarea>`;
    else control = `<input type="number" name="${key}" value="${value}" min="0" step="any">`;
    return `<label class="gr-setting"><span>${escapeHTML(label)}<small>${escapeHTML(help)}${def !== undefined && !Array.isArray(def) ? ` · default ${def}` : ""}</small></span>${control}</label>`;
  }).join("");
  $("#gr-admin-fields").dataset.defaults = JSON.stringify(defaults || {});
}

function formatBytes(bytes) {
  if (bytes >= 1073741824) return `${formatNumber(bytes / 1073741824, 3)} GB`;
  if (bytes >= 1048576) return `${formatNumber(bytes / 1048576, 3)} MB`;
  return `${formatNumber(Math.round(bytes / 1024))} kB`;
}

async function refreshStoredJobs() {
  let data;
  try { data = await fetchJSON(api("admin/jobs")); } catch (error) { $("#gr-store-summary").textContent = error.message; return; }
  const limit = data.maxStorageMB ? ` of the ${formatNumber(data.maxStorageMB)} MB limit` : " (no storage limit)";
  $("#gr-store-summary").textContent = `${formatNumber(data.jobs.length)} analyses, ${formatBytes(data.totalBytes)}${limit}; results are kept ${data.retentionDays} days. Everyone's analyses are listed; only the running ones are never deleted.`;
  const shown = data.jobs.slice(0, 500);
  $("#gr-store-jobs").innerHTML = `<thead><tr><th><input type="checkbox" id="gr-store-all" aria-label="Select all"></th><th>Analysis</th><th>Workspace</th><th>Assembly</th><th>Regions</th><th>Size</th><th>Age (days)</th><th>From</th></tr></thead><tbody>
    ${shown.map((job) => `<tr><td><input type="checkbox" data-job="${job.id}" ${job.running ? "disabled" : ""}></td>
      <td><a href="#job=${job.id}" data-open-job>${escapeHTML(job.name || job.id)}</a> <small>${job.id}</small></td>
      <td>${escapeHTML(job.ownerName || (job.owner ? job.owner : `API · ${job.client}`))}</td><td>${escapeHTML(job.assembly)}</td>
      <td>${formatNumber(job.regions)}</td><td>${formatBytes(job.bytes)}</td><td>${formatNumber(job.ageDays, 3)}</td>
      <td>${escapeHTML(job.running ? "running" : job.source)}</td></tr>`).join("") || '<tr><td colspan="8" class="gr-muted gr-empty">No stored analyses.</td></tr>'}</tbody>`
    + (data.jobs.length > shown.length ? `<caption class="gr-muted">The newest 500 of ${formatNumber(data.jobs.length)}</caption>` : "");
  const update = () => { $("#gr-store-selected").disabled = !$$("[data-job]:checked", $("#gr-store-jobs")).length; };
  for (const box of $$("[data-job]", $("#gr-store-jobs"))) box.addEventListener("change", update);
  $("#gr-store-all")?.addEventListener("change", (event) => {
    for (const box of $$("[data-job]:not(:disabled)", $("#gr-store-jobs"))) box.checked = event.target.checked;
    update();
  });
  for (const link of $$("[data-open-job]", $("#gr-store-jobs"))) link.addEventListener("click", () => $("#gr-admin-dialog").close());
  update();
}

async function runCleanup(action) {
  const body = { action };
  let question;
  if (action === "olderThan") {
    body.days = Number($("#gr-store-days").value);
    question = `Delete every analysis older than ${body.days} days?`;
  } else if (action === "jobs") {
    body.jobs = $$("[data-job]:checked", $("#gr-store-jobs")).map((box) => box.dataset.job);
    question = `Delete the ${body.jobs.length} selected analyses?`;
  } else if (action === "all") {
    question = "Delete ALL stored GREAT analyses (except running ones)? Their links stop working.";
  }
  if (question && !window.confirm(question)) return;
  try {
    const result = await fetchJSON(api("admin/cleanup"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("#gr-admin-message").textContent = `${formatNumber(result.removed)} analyses deleted, ${formatBytes(result.freedBytes)} freed`;
    await Promise.all([refreshStoredJobs(), refreshAdminStatus()]);
    void renderRecent();
  } catch (error) {
    $("#gr-admin-message").textContent = error.message;
  }
}

async function refreshAdminStatus() {
  try {
    const s = await fetchJSON(api("admin"));
    const rows = (items, columns) => items.length ? `<table class="gr-table"><thead><tr>${columns.map(([label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${items.map((item) => `<tr>${columns.map(([, get]) => `<td>${escapeHTML(get(item))}</td>`).join("")}</tr>`).join("")}</tbody></table>` : '<p class="gr-muted">none</p>';
    $("#gr-admin-status").innerHTML = `
      <p>${formatNumber(s.storedJobs)} stored analyses (${formatBytes(s.storedBytes)}) in <code>${escapeHTML(s.jobsDir)}</code>${s.lastCleanup ? ` · last cleanup ${new Date(s.lastCleanup.time * 1000).toLocaleString()} (${escapeHTML(s.lastCleanup.reason)}): ${formatNumber(s.lastCleanup.removed)} deleted, ${formatBytes(s.lastCleanup.freedBytes)}` : ""}${s.nextCleanup ? ` · next automatic cleanup ${new Date(s.nextCleanup * 1000).toLocaleTimeString()}` : ""}</p>
      <p>${s.workers.length} worker process${s.workers.length === 1 ? "" : "es"} (${s.workers.map((w) => `${w.busy ? "busy" : "idle"}${w.assemblies.length ? ` ${w.assemblies.join("/")}` : ""}`).join(", ") || "none yet"}) · data <code>${escapeHTML(s.dataDir)}</code></p>
      <h4>Running</h4>${rows(s.running, [["Job", (r) => r.id], ["Client", (r) => r.client], ["Assembly", (r) => r.assembly], ["Regions", (r) => formatNumber(r.regions)], ["From", (r) => r.source], ["Seconds", (r) => r.seconds]])}
      <h4>Waiting</h4>${rows(s.waiting, [["Client", (r) => r.client], ["Waiting (s)", (r) => r.seconds]])}
      <h4>Clients in the last hour</h4>${rows(s.clientsLastHour, [["Client", (r) => r.client], ["Analyses", (r) => r.jobs]])}
      <h4>Recent</h4>${rows(s.recent.slice(0, 15), [["Job", (r) => r.id], ["Client", (r) => r.client], ["Assembly", (r) => r.assembly], ["Regions", (r) => formatNumber(r.regions)], ["From", (r) => r.source], ["Seconds", (r) => r.seconds]])}`;
  } catch (error) {
    $("#gr-admin-status").innerHTML = `<p class="gr-error">${escapeHTML(error.message)}</p>`;
  }
}

async function saveAdmin(event) {
  event.preventDefault();
  const changes = {};
  for (const field of $$("#gr-admin-fields [name]")) {
    if (field.type === "checkbox") changes[field.name] = field.checked;
    else if (field.tagName === "TEXTAREA") changes[field.name] = field.value.split(/[\s,]+/).filter(Boolean);
    else changes[field.name] = Number(field.value);
  }
  try {
    const data = await fetchJSON(api("settings"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
    renderAdminFields(data.settings, data.defaults);
    $("#gr-admin-message").textContent = `Saved ${new Date().toLocaleTimeString()}`;
    state.status = await fetchJSON(api("status"));
    renderAPI();
  } catch (error) {
    $("#gr-admin-message").textContent = error.message;
  }
}

// ---------------------------------------------------------------- start

function route() {
  const hash = new URLSearchParams(location.hash.replace(/^#/, ""));
  if (hash.get("job")) {
    void showJob(hash.get("job"), { ontology: hash.get("ontology"), term: hash.get("term") });
  } else {
    $("#gr-view-job").hidden = true;
    $("#gr-view-new").hidden = false;
    if (location.hash === "#api") $("#api").scrollIntoView();
    void renderRecent();
  }
}

async function start() {
  setupTheme();
  const params = new URLSearchParams(location.search);
  if (params.get("track")) {
    const url = params.get("track");
    state.linkedTrack = { url, name: params.get("name") || decodeURIComponent(url.split(/[?#]/)[0].split("/").pop()) };
  }
  try {
    state.workspaceId = (await fetchJSON(appURL("api/workspaces")))?.selected?.id || null;
  } catch { /* Genome Canvas unreachable: GREAT works without a workspace */ }
  try {
    state.status = await fetchJSON(api("status"));
  } catch (error) {
    $(".gr-intro").insertAdjacentHTML("beforeend", `<p class="gr-error">GREAT is not available: ${escapeHTML(error.message)}</p>`);
    return;
  }
  renderAssemblies();
  for (const container of $$(".gr-source")) sourcePicker(container);
  if (state.linkedTrack) $("#gr-fg-name").value = state.linkedTrack.name;
  renderAPI();
  $("#gr-admin-button").hidden = !state.status.admin;
  $("#gr-admin-button").addEventListener("click", openAdmin);
  $("#gr-admin-form").addEventListener("submit", saveAdmin);
  $("#gr-admin-defaults").addEventListener("click", () => {
    const defaults = JSON.parse($("#gr-admin-fields").dataset.defaults || "{}");
    renderAdminFields(defaults, defaults);
  });
  for (const button of $$("[data-cleanup]")) button.addEventListener("click", () => runCleanup(button.dataset.cleanup));
  $("#gr-form").addEventListener("submit", submitForm);
  for (const radio of $$('input[name="bgChoice"]')) radio.addEventListener("change", () => {
    $('.gr-source[data-prefix="bg"]').hidden = $('input[name="bgChoice"]:checked').value === "wholeGenome";
  });
  for (const id of ["#gr-view-mode", "#gr-threshold", "#gr-min-fold", "#gr-min-genes", "#gr-min-count", "#gr-max-count", "#gr-term-filter"]) {
    $(id).addEventListener("input", renderAllTables);
  }
  for (const button of $$("[data-close]")) button.addEventListener("click", () => $(`#${button.dataset.close}`).close());
  $("#gr-term-dialog").addEventListener("close", () => {
    if (state.job) history.replaceState(null, "", `#job=${state.job.id}`);
  });
  for (const button of $$("[data-menu]")) button.addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $(`#${button.dataset.menu}`);
    const open = menu.hidden;
    for (const other of $$(".gr-menu-list")) other.hidden = true;
    menu.hidden = !open;
  });
  document.addEventListener("click", () => { for (const menu of $$(".gr-menu-list")) menu.hidden = true; });
  for (const item of $$("[data-export]")) item.addEventListener("click", () => {
    const kind = item.dataset.export;
    if (kind === "all") location.href = api(`jobs/${state.job.id}/export.tsv`);
    else if (kind === "regions-bed") {
      fetch(api(`jobs/${state.job.id}/regions.bed`)).then((r) => r.blob()).then((blob) => download(blob, `great-${state.job.id}-regions.bed`));
    } else exportShown(kind === "shown-html" ? "html" : "tsv");
  });
  for (const item of $$("[data-assoc]")) item.addEventListener("click", () => { location.href = api(`jobs/${state.job.id}/associations.tsv?table=${item.dataset.assoc}`); });
  $("#gr-copy-link").addEventListener("click", async () => {
    const link = `${state.status.greatBase}/#job=${state.job.id}`;
    try { await navigator.clipboard.writeText(link); toast("Link copied"); } catch { prompt("Link to these results", link); }
  });
  window.addEventListener("hashchange", route);
  route();
}

void start();
