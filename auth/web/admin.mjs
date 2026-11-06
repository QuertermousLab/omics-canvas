// The admin page: sign-in (access passwords, sessions, settings, log; /admin/api/) and GREAT (its settings and
// stored analyses; GREAT's own admin API under /great/api/, which checks the same session).

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const when = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString() : "–");
const day = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleDateString() : "never");
const number = (value, digits = 3) => (value === null || value === undefined ? "" : Number.isInteger(value) ? value.toLocaleString("en-US")
  : Number(value.toPrecision(digits)).toLocaleString("en-US"));

async function call(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

function browser(agent) {
  const a = agent || "";
  const name = /Edg\//.test(a) ? "Edge" : /Chrome\//.test(a) ? "Chrome" : /Firefox\//.test(a) ? "Firefox" : /Safari\//.test(a) ? "Safari"
    : /OmicsCanvas|GenomeCanvas|AppleWebKit/.test(a) ? "Mac app" : a.split(" ")[0] || "?";
  const os = /Mac OS X/.test(a) ? "macOS" : /Windows/.test(a) ? "Windows" : /Linux/.test(a) ? "Linux" : "";
  return `${name}${os ? ` · ${os}` : ""}`;
}

// ---------------------------------------------------------------- sign-in

const SIGNIN_HELP = {
  admins: ["Administrators (THOR accounts)", "they see this page and GREAT's admin settings"],
  rememberDays: ["Keep me signed in (days)", "counted from the last use"],
  idleHours: ["Without “keep me signed in”: idle limit (hours)", "also ends when the browser closes"],
  lockoutAttempts: ["Failed sign-ins before a lockout", "per address and per account"],
  lockoutMinutes: ["Lockout window and length (minutes)", ""],
  minUid: ["Lowest account uid that may sign in", "system accounts are below 1000"],
  allowedAccounts: ["Only these THOR accounts", "empty: every account"],
};

async function loadSignin() {
  const data = await call("/admin/api/overview");
  $("#ad-me").textContent = `Signed in as ${data.me.user}`;
  $("#ad-method").innerHTML = data.verifier === "pam"
    ? "THOR passwords are checked with PAM (the root-owned helper <code>/usr/local/lib/omics-canvas/pam_check</code>)."
    : "THOR passwords are checked by a local SSH login. For PAM, run once on THOR: <code>sudo auth/install-pam-helper.sh</code> (see auth/README.md).";
  renderPasswords(data.passwords);
  renderSessions(data.sessions, data.me.session);
  renderLog(data.log);
  renderSettings(data.settings);
}

function renderPasswords(passwords) {
  $("#ad-passwords").innerHTML = `<thead><tr><th>Name</th><th>State</th><th>Valid until</th><th>Created</th><th>Last used</th><th class="num">Uses</th><th class="num">Signed in</th><th>Note</th><th></th></tr></thead><tbody>
    ${passwords.map((p) => `<tr data-id="${p.id}"><td><b>${esc(p.label)}</b></td><td><span class="ad-state ${p.state}">${p.state}</span></td>
      <td><input type="date" data-expires value="${p.expires ? new Date(p.expires * 1000).toISOString().slice(0, 10) : ""}" title="Change the expiry (empty: never)"></td>
      <td>${day(p.created)}</td><td>${when(p.last_used)}</td><td class="num">${p.uses}</td><td class="num">${p.sessions}</td><td>${esc(p.note)}</td>
      <td><button class="quiet-button small" data-toggle>${p.disabled ? "Enable" : "Disable"}</button><button class="quiet-button small ad-danger" data-delete>Delete</button></td></tr>`).join("")
      || '<tr><td colspan="9" class="ad-muted">No access passwords yet.</td></tr>'}</tbody>`;
  for (const row of $$("#ad-passwords tr[data-id]")) {
    const id = row.dataset.id;
    const update = (body) => call(`/admin/api/passwords/${id}`, { method: "POST", body: JSON.stringify(body) }).then(loadSignin).catch((e) => alert(e.message));
    $("[data-toggle]", row).addEventListener("click", () => update({ disabled: $("[data-toggle]", row).textContent === "Disable" }));
    $("[data-expires]", row).addEventListener("change", (event) => update({ expires: event.target.value || null }));
    $("[data-delete]", row).addEventListener("click", () => {
      if (!confirm("Delete this access password? Everyone signed in with it is signed out.")) return;
      call(`/admin/api/passwords/${id}`, { method: "DELETE" }).then(loadSignin).catch((e) => alert(e.message));
    });
  }
}

function renderSessions(sessions, own) {
  $("#ad-sessions").innerHTML = `<thead><tr><th>Who</th><th>How</th><th>From</th><th>Browser</th><th>Signed in</th><th>Last used</th><th>Until</th><th></th></tr></thead><tbody>
    ${sessions.map((s) => `<tr><td><b>${esc(s.user)}</b>${s.id === own ? " <small>(you)</small>" : ""}</td>
      <td>${s.method === "password" ? `access password${s.password_label ? ` · ${esc(s.password_label)}` : ""}` : "THOR account"}${s.remember ? " · kept" : ""}</td>
      <td>${esc(s.ip)}</td><td>${esc(browser(s.agent))}</td><td>${when(s.created)}</td><td>${when(s.last_seen)}</td><td>${day(s.expires)}</td>
      <td>${s.id === own ? "" : `<button class="quiet-button small ad-danger" data-session="${s.id}">Sign out</button>`}</td></tr>`).join("")}</tbody>`;
  for (const button of $$("[data-session]")) button.addEventListener("click", () =>
    call(`/admin/api/sessions/${button.dataset.session}`, { method: "DELETE" }).then(loadSignin).catch((e) => alert(e.message)));
}

function renderLog(log) {
  $("#ad-log").innerHTML = `<thead><tr><th>Time</th><th>From</th><th>Who</th><th>How</th><th>Result</th></tr></thead><tbody>
    ${log.map((l) => `<tr><td>${when(l.time)}</td><td>${esc(l.ip)}</td><td>${esc(l.user || "–")}</td><td>${l.method === "password" ? "access password" : "THOR account"}</td>
      <td>${l.ok ? '<span class="ad-state active">signed in</span>' : `<span class="ad-state failed">failed</span> <small>${esc(l.detail)}</small>`}</td></tr>`).join("")}</tbody>`;
}

function settingsFields(container, values, help) {
  container.innerHTML = Object.entries(values).map(([key, value]) => {
    const [label, note] = help[key] || [key, ""];
    const control = typeof value === "boolean" ? `<input type="checkbox" name="${key}" ${value ? "checked" : ""}>`
      : Array.isArray(value) ? `<textarea name="${key}" rows="2" spellcheck="false">${esc(value.join("\n"))}</textarea>`
        : `<input type="number" name="${key}" value="${value}" min="0" step="any">`;
    return `<label class="ad-setting"><span>${esc(label)}<small>${esc(note)}</small></span>${control}</label>`;
  }).join("");
}

function settingsValues(container) {
  const changes = {};
  for (const field of $$("[name]", container)) {
    if (field.type === "checkbox") changes[field.name] = field.checked;
    else if (field.tagName === "TEXTAREA") changes[field.name] = field.value.split(/[\s,]+/).filter(Boolean);
    else changes[field.name] = Number(field.value);
  }
  return changes;
}

function renderSettings(settings) {
  settingsFields($("#ad-settings-fields"), settings, SIGNIN_HELP);
}

// ---------------------------------------------------------------- GREAT

const GREAT_HELP = {
  minIntervalSeconds: ["Minimum interval between analyses from one client (s)", "GREAT asked for 60 s; rGREAT also waits request_interval (60 s) unless told 0"],
  maxJobsPerHour: ["Analyses per client per hour", "0 = no limit"],
  maxConcurrentJobs: ["Analyses running at once", "each runs in its own worker process"],
  maxWaitSeconds: ["Queue wait before refusing (s)", "then HTTP 429 with Retry-After"],
  maxRegions: ["Test regions per analysis", "GREAT: 500,000"],
  maxBackgroundRegions: ["Background regions", "GREAT: 1,000,000"],
  maxUploadMB: ["File size limit (MB, decompressed)", "GREAT: 50 MB"],
  jobRetentionDays: ["Keep results (days)", "older ones are deleted every 10 minutes; GREAT: 1 day"],
  maxStorageMB: ["Storage limit for results (MB)", "above it the oldest analyses go; 0 = no limit"],
  exportTermLimit: ["Terms per ontology in batch exports", "0 = all; GREAT returned the top 500"],
  allowRemoteURLs: ["Fetch requestURL / URL inputs", "public addresses only"],
  apiEnabled: ["GREAT's programming interfaces (rGREAT, greatStart.php)", "open without signing in; they never read server files"],
  exemptClients: ["Clients never rate limited", "IP addresses, one per line"],
  blockedClients: ["Blocked clients", "IP addresses, one per line"],
};
let greatDefaults = {};

const bytes = (b) => (b >= 1073741824 ? `${number(b / 1073741824)} GB` : b >= 1048576 ? `${number(b / 1048576)} MB` : `${number(Math.round(b / 1024))} kB`);

async function loadGreat() {
  try {
    const data = await call("/great/api/settings");
    greatDefaults = data.defaults;
    settingsFields($("#gr-fields"), data.settings, GREAT_HELP);
  } catch (error) {
    $("#gr-fields").innerHTML = `<p class="ad-danger">GREAT is not reachable: ${esc(error.message)}</p>`;
    return;
  }
  await Promise.all([loadStoredJobs(), loadGreatStatus()]);
}

async function loadStoredJobs() {
  const data = await call("/great/api/admin/jobs");
  const limit = data.maxStorageMB ? ` of the ${number(data.maxStorageMB)} MB limit` : " (no storage limit)";
  $("#gr-store-summary").textContent = `${number(data.jobs.length)} analyses, ${bytes(data.totalBytes)}${limit}; results are kept ${data.retentionDays} days. Running analyses are never deleted.`;
  const shown = data.jobs.slice(0, 500);
  $("#gr-store-jobs").innerHTML = `<thead><tr><th><input type="checkbox" id="gr-store-all" aria-label="Select all"></th><th>Analysis</th><th>Workspace</th><th>Assembly</th><th class="num">Regions</th><th class="num">Size</th><th class="num">Age (days)</th><th>From</th></tr></thead><tbody>
    ${shown.map((job) => `<tr><td><input type="checkbox" data-job="${job.id}" ${job.running ? "disabled" : ""}></td>
      <td><a href="/great/#job=${job.id}" target="_blank" rel="noopener">${esc(job.name || job.id)}</a> <small>${job.id}</small></td>
      <td>${esc(job.ownerName || (job.owner ? job.owner : `API · ${job.client}`))}</td><td>${esc(job.assembly)}</td>
      <td class="num">${number(job.regions)}</td><td class="num">${bytes(job.bytes)}</td><td class="num">${number(job.ageDays)}</td>
      <td>${esc(job.running ? "running" : job.source)}</td></tr>`).join("") || '<tr><td colspan="8" class="ad-muted">No stored analyses.</td></tr>'}</tbody>`;
  const update = () => { $("#gr-store-selected").disabled = !$$("[data-job]:checked").length; };
  for (const box of $$("[data-job]")) box.addEventListener("change", update);
  $("#gr-store-all")?.addEventListener("change", (event) => { for (const box of $$("[data-job]:not(:disabled)")) box.checked = event.target.checked; update(); });
  update();
}

async function loadGreatStatus() {
  const s = await call("/great/api/admin");
  const table = (items, columns) => (items.length ? `<div class="ad-table-wrap"><table class="ad-table"><thead><tr>${columns.map(([c]) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${items.map((i) => `<tr>${columns.map(([, get]) => `<td>${esc(get(i))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : '<p class="ad-muted">none</p>');
  $("#gr-status").innerHTML = `<p class="ad-muted">${number(s.storedJobs)} stored analyses (${bytes(s.storedBytes)}) in <code>${esc(s.jobsDir)}</code>${s.lastCleanup ? ` · last cleanup ${when(s.lastCleanup.time)} (${esc(s.lastCleanup.reason)}): ${number(s.lastCleanup.removed)} deleted, ${bytes(s.lastCleanup.freedBytes)}` : ""}${s.nextCleanup ? ` · next automatic cleanup ${new Date(s.nextCleanup * 1000).toLocaleTimeString()}` : ""}</p>
    <p class="ad-muted">${s.workers.length} worker process${s.workers.length === 1 ? "" : "es"} (${s.workers.map((w) => `${w.busy ? "busy" : "idle"}${w.assemblies.length ? ` ${w.assemblies.join("/")}` : ""}`).join(", ") || "none yet"}) · data <code>${esc(s.dataDir)}</code></p>
    <h3>Running</h3>${table(s.running, [["Job", (r) => r.id], ["Client", (r) => r.client], ["Assembly", (r) => r.assembly], ["Regions", (r) => number(r.regions)], ["From", (r) => r.source], ["Seconds", (r) => r.seconds]])}
    <h3>Waiting</h3>${table(s.waiting, [["Client", (r) => r.client], ["Waiting (s)", (r) => r.seconds]])}
    <h3>Clients in the last hour</h3>${table(s.clientsLastHour, [["Client", (r) => r.client], ["Analyses", (r) => r.jobs]])}
    <h3>Recent</h3>${table(s.recent.slice(0, 20), [["Job", (r) => r.id], ["Client", (r) => r.client], ["Assembly", (r) => r.assembly], ["Regions", (r) => number(r.regions)], ["From", (r) => r.source], ["Seconds", (r) => r.seconds]])}`;
}

async function cleanup(action) {
  const body = { action };
  let question;
  if (action === "olderThan") { body.days = Number($("#gr-store-days").value); question = `Delete every GREAT analysis older than ${body.days} days?`; }
  if (action === "jobs") { body.jobs = $$("[data-job]:checked").map((b) => b.dataset.job); question = `Delete the ${body.jobs.length} selected analyses?`; }
  if (action === "all") question = "Delete ALL stored GREAT analyses (except running ones)? Their links stop working.";
  if (question && !confirm(question)) return;
  try {
    const result = await call("/great/api/admin/cleanup", { method: "POST", body: JSON.stringify(body) });
    $("#gr-store-message").textContent = `${number(result.removed)} deleted, ${bytes(result.freedBytes)} freed`;
    await Promise.all([loadStoredJobs(), loadGreatStatus()]);
  } catch (error) { $("#gr-store-message").textContent = error.message; }
}

// ---------------------------------------------------------------- start

function selectTab(name) {
  for (const tab of $$("[data-tab]")) tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  for (const panel of $$("[data-panel]")) panel.hidden = panel.dataset.panel !== name;
  if (name === "great") void loadGreat();
  history.replaceState(null, "", `#${name}`);
}

$("#ad-new-password").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const result = await call("/admin/api/passwords", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
    $("#ad-created").hidden = false;
    $("#ad-created").innerHTML = `Access password for <b>${esc(form.get("label"))}</b>: <code>${esc(result.password)}</code> — copy it now; it is stored only as a hash.`;
    event.target.reset();
    await loadSignin();
  } catch (error) { alert(error.message); }
});
$("#ad-settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await call("/admin/api/settings", { method: "POST", body: JSON.stringify(settingsValues($("#ad-settings-fields"))) });
    renderSettings(result.settings);
    $("#ad-settings-message").textContent = `Saved ${new Date().toLocaleTimeString()}`;
  } catch (error) { $("#ad-settings-message").textContent = error.message; }
});
$("#gr-settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await call("/great/api/settings", { method: "POST", body: JSON.stringify(settingsValues($("#gr-fields"))) });
    settingsFields($("#gr-fields"), result.settings, GREAT_HELP);
    $("#gr-message").textContent = `Saved ${new Date().toLocaleTimeString()}`;
  } catch (error) { $("#gr-message").textContent = error.message; }
});
$("#gr-defaults").addEventListener("click", () => settingsFields($("#gr-fields"), greatDefaults, GREAT_HELP));
for (const button of $$("[data-cleanup]")) button.addEventListener("click", () => cleanup(button.dataset.cleanup));
for (const tab of $$("[data-tab]")) tab.addEventListener("click", () => selectTab(tab.dataset.tab));
setInterval(() => { if (!$('[data-panel="great"]').hidden) void loadGreatStatus().catch(() => {}); }, 5000);
await loadSignin().catch((error) => { document.querySelector(".ad-main").insertAdjacentHTML("afterbegin", `<p class="ad-danger">${esc(error.message)}</p>`); });
selectTab(location.hash === "#great" ? "great" : "signin");
