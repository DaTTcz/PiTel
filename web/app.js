"use strict";
// PiTel – webová správa (bez závislostí)

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const PHASES = {
  idle: "Čeká na hovor",
  negotiating: "Vyjednávání…",
  connected: "Připojeno",
};
const UNIT = { mgetty: "pitel-mgetty.service", nat: "pitel-nat.service", web: "pitel-web.service" };

// ---- API ------------------------------------------------------------------
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { "X-PiTel": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  let data = {};
  try { data = await r.json(); } catch (e) { /* prázdné */ }
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Nepřihlášen"); }
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), 3500);
}

async function act(fn) {
  try {
    const d = await fn();
    if (d && d.message) toast(d.message);
    if (d && d.messages) toast(d.messages.join(" "));
    return d;
  } catch (e) {
    if (e.message !== "Nepřihlášen") toast(e.message, true);
  }
}

// ---- formátování -----------------------------------------------------------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function fBytes(n) {
  n = Number(n || 0);
  const u = ["B", "kB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return i === 0 ? `${n} B` : `${n.toFixed(1)} ${u[i]}`;
}
function fBps(bytesPerSec) {
  const b = Number(bytesPerSec || 0) * 8;
  return b < 1000 ? `${b.toFixed(0)} b/s` : `${(b / 1000).toFixed(1)} kb/s`;
}
function fDur(s) {
  s = Math.max(0, Math.floor(s || 0));
  const p = (x) => String(x).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}
function fTime(ts) {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  return d.toLocaleString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
const svc = (v) => `<span class="badge ${v === "active" ? "ok" : "bad"}">${v === "active" ? "běží" : esc(v)}</span>`;

// ---- login -----------------------------------------------------------------
function showLogin() {
  stopPolling();
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-password").focus();
}
function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  startPolling();
}
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password: $("#login-password").value } });
    $("#login-password").value = "";
    showApp();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});
$("#logout").addEventListener("click", async () => {
  await act(() => api("/api/logout", { method: "POST" }));
  showLogin();
});

// ---- záložky ---------------------------------------------------------------
let currentTab = "overview";
$$("#tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    currentTab = b.dataset.tab;
    $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== currentTab));
    location.hash = currentTab;
    onTab(currentTab);
  })
);
function onTab(t) {
  if (t === "calls") loadCalls();
  if (t === "users") loadUsers();
  if (t === "settings") loadConfig();
  if (t === "logs") loadLogs();
  if (t === "system") loadUpdate();
}

// ---- přehled + graf --------------------------------------------------------
const HISTORY_SEC = 300;
let samples = []; // {t, rxRate, txRate}
let prevCounters = null;
let pollTimer = null;

function startPolling() {
  stopPolling();
  poll();
  pollTimer = setInterval(poll, 2000);
}
function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function poll() {
  let s;
  try { s = await api("/api/status"); } catch (e) { return; }
  renderStatus(s);
  if (currentTab === "logs" && $("#logs-auto").checked) loadLogs();
  if (currentTab === "system") loadUpdate(true);
}

function renderStatus(s) {
  const u = s.update || {};
  $("#version").textContent = u.current || "v" + s.version;
  $("#upd-badge").classList.toggle("hidden", !u.available);
  $("#upd-badge").textContent = u.available ? `⬆ ${u.latest}` : "";
  const c = s.current || { phase: "idle" };
  $("#phase-dot").className = "dot " + c.phase;
  $("#phase-label").textContent = PHASES[c.phase] || c.phase;
  $("#phase-connect").textContent = c.phase === "idle" ? `/dev/${s.device}` : (c.connect || "") + (c.caller ? ` · volá ${c.caller}` : "");
  $("#btn-hangup").disabled = c.phase === "idle";
  document.title = (c.phase === "connected" ? "● " : "") + "PiTel – " + (PHASES[c.phase] || "");

  $("#st-duration").textContent = c.phase === "idle" ? "–" : fDur(c.duration);
  $("#st-user").textContent = c.user || "–";
  $("#st-ip").textContent = c.remote_ip ? `${c.remote_ip} · ${c.iface}` : "";

  const cnt = c.counters;
  let rxRate = 0, txRate = 0;
  if (cnt) {
    if (prevCounters && cnt.ts > prevCounters.ts && cnt.rx >= prevCounters.rx) {
      const dt = cnt.ts - prevCounters.ts;
      rxRate = (cnt.rx - prevCounters.rx) / dt;
      txRate = (cnt.tx - prevCounters.tx) / dt;
    }
    prevCounters = cnt;
    $("#st-tx").textContent = fBytes(cnt.tx);
    $("#st-rx").textContent = fBytes(cnt.rx);
    $("#st-tx-rate").textContent = fBps(txRate);
    $("#st-rx-rate").textContent = fBps(rxRate);
  } else {
    prevCounters = null;
    ["#st-tx", "#st-rx"].forEach((x) => ($(x).textContent = ""));
    ["#st-tx-rate", "#st-rx-rate"].forEach((x) => ($(x).textContent = "–"));
  }
  const now = Date.now() / 1000;
  samples.push({ t: now, rx: rxRate, tx: txRate });
  samples = samples.filter((p) => p.t > now - HISTORY_SEC);
  drawChart(now);

  $("#sys-modem").innerHTML = `/dev/${esc(s.device)} ` + (s.modem_present ? '<span class="badge ok">připojen</span>' : '<span class="badge bad">nenalezen</span>');
  $("#sys-mgetty").innerHTML = svc(s.services[UNIT.mgetty]);
  $("#sys-nat").innerHTML = svc(s.services[UNIT.nat]);
  $("#sys-web").innerHTML = svc(s.services[UNIT.web]);
  $("#sys-speed").textContent = s.max_speed ? `${s.max_speed} b/s` : "bez omezení";

  const lc = s.last_call, t = s.totals;
  $("#tot-last").innerHTML = lc ? `${fTime(lc.started)} · ${fDur(lc.duration)} · <span class="badge ${lc.connected ? "ok" : "bad"}">${esc(lc.result)}</span>` : "–";
  $("#tot-calls").textContent = `${t.calls} (${t.ok} úspěšných)`;
  $("#tot-today").textContent = t.today;
  $("#tot-bytes").textContent = `↓ ${fBytes(t.bytes_sent)} · ↑ ${fBytes(t.bytes_rcvd)}`;
  $("#tot-time").textContent = fDur(t.seconds);
}

function drawChart(now) {
  const cv = $("#chart");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 180;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const padL = 64, padB = 20, padT = 8, pw = w - padL - 8, ph = h - padB - padT;
  const maxBits = Math.max(2400, ...samples.map((p) => Math.max(p.rx, p.tx) * 8));
  const nice = [2400, 4800, 9600, 14400, 19200, 28800, 33600, 64000, 128000, 1e6, 1e7, 1e8].find((x) => x >= maxBits) || maxBits;
  const css = getComputedStyle(document.documentElement);
  g.font = "11px system-ui";
  g.fillStyle = css.getPropertyValue("--muted");
  g.strokeStyle = css.getPropertyValue("--line");
  g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padT + ph - (ph * i) / 4;
    g.beginPath(); g.moveTo(padL, y + 0.5); g.lineTo(padL + pw, y + 0.5); g.stroke();
    g.textAlign = "right";
    g.fillText(fBps((nice * i) / 4 / 8), padL - 6, y + 4);
  }
  g.textAlign = "center";
  [5, 4, 3, 2, 1, 0].forEach((m) => g.fillText(m ? `-${m} min` : "teď", padL + pw - (pw * m * 60) / HISTORY_SEC, h - 4));
  const X = (t) => padL + pw - ((now - t) / HISTORY_SEC) * pw;
  const Y = (v) => padT + ph - Math.min(1, (v * 8) / nice) * ph;
  [["tx", "--tx"], ["rx", "--rx"]].forEach(([k, col]) => {
    if (samples.length < 2) return;
    g.strokeStyle = css.getPropertyValue(col);
    g.lineWidth = 2;
    g.beginPath();
    samples.forEach((p, i) => (i ? g.lineTo(X(p.t), Y(p[k])) : g.moveTo(X(p.t), Y(p[k]))));
    g.stroke();
  });
}
window.addEventListener("resize", () => drawChart(Date.now() / 1000));

$("#btn-hangup").addEventListener("click", () => {
  if (confirm("Opravdu ukončit probíhající hovor?")) act(() => api("/api/action", { method: "POST", body: { action: "hangup" } })).then(poll);
});
$("#btn-restart").addEventListener("click", () => {
  if (confirm("Restartovat mgetty? Probíhající hovor se ukončí.")) act(() => api("/api/action", { method: "POST", body: { action: "restart-mgetty" } })).then(poll);
});

// ---- historie ---------------------------------------------------------------
async function loadCalls() {
  const d = await act(() => api("/api/calls?n=200"));
  if (!d) return;
  $("#calls-body").innerHTML = d.calls.length
    ? d.calls.map((r) => `<tr>
        <td>${fTime(r.started)}</td><td class="mono">${fDur(r.duration)}</td><td>${esc(r.user || "–")}</td>
        <td class="mono">${esc(r.connect || "–")}</td><td class="r mono">${fBytes(r.bytes_sent)}</td><td class="r mono">${fBytes(r.bytes_rcvd)}</td>
        <td><span class="badge ${r.connected ? "ok" : "bad"}">${esc(r.result)}</span></td></tr>`).join("")
    : '<tr><td colspan="7" class="muted">Zatím žádné hovory.</td></tr>';
}
$("#calls-refresh").addEventListener("click", loadCalls);

// ---- uživatelé --------------------------------------------------------------
async function loadUsers() {
  const d = await act(() => api("/api/users"));
  if (!d) return;
  $("#users-body").innerHTML = d.users.length
    ? d.users.map((u) => `<tr><td><b>${esc(u.name)}</b></td><td>${fTime(u.created)}</td><td>${fTime(u.changed)}</td>
        <td class="r"><button class="ghost small" data-edit="${esc(u.name)}">Změnit heslo</button>
        <button class="danger small" data-del="${esc(u.name)}">Smazat</button></td></tr>`).join("")
    : '<tr><td colspan="4" class="muted">Žádní uživatelé – bez nich se nikdo nepřipojí.</td></tr>';
}
$("#users-body").addEventListener("click", async (e) => {
  const del = e.target.dataset.del, edit = e.target.dataset.edit;
  if (del && confirm(`Smazat uživatele ${del}?`)) {
    await act(() => api("/api/users/" + encodeURIComponent(del), { method: "DELETE" }));
    loadUsers();
  }
  if (edit) { $("#u-name").value = edit; $("#u-pass").focus(); }
});
$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ok = await act(() => api("/api/users", { method: "POST", body: { name: $("#u-name").value.trim(), password: $("#u-pass").value } }));
  if (ok) { $("#u-name").value = ""; $("#u-pass").value = ""; loadUsers(); }
});

// ---- nastavení --------------------------------------------------------------
let cfgMeta = null;
async function loadConfig() {
  const d = await act(() => api("/api/config"));
  if (!d) return;
  cfgMeta = d;
  const f = $("#cfg-form"), c = d.config;
  f.line_type.innerHTML = Object.entries(d.line_types).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  f.max_speed.innerHTML = d.speeds.map((s) => `<option value="${s}">${s ? s + " b/s" : "bez omezení"}</option>`).join("");
  for (const k of ["device", "line_type", "max_speed", "rings", "server_ip", "client_ip", "wan_iface", "idle_timeout", "web_port", "web_bind", "repo_url", "branch", "update_channel"]) f[k].value = c[k];
  f.extra_init.value = (c.extra_init || []).join(";");
  f.dns.value = (c.dns || []).join(", ");
  f.terminal_test.checked = !!c.terminal_test;
  $("#init-preview").textContent = d.init_preview.join("  ");
}
$("#cfg-form").line_type.addEventListener("change", (e) => {
  if (cfgMeta) $("#cfg-form").max_speed.value = cfgMeta.line_defaults[e.target.value];
});
$("#cfg-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = {
    device: f.device.value.replace("/dev/", "").trim(), line_type: f.line_type.value, max_speed: +f.max_speed.value,
    rings: +f.rings.value, extra_init: f.extra_init.value, terminal_test: f.terminal_test.checked,
    server_ip: f.server_ip.value, client_ip: f.client_ip.value, dns: f.dns.value, wan_iface: f.wan_iface.value.trim(),
    idle_timeout: +f.idle_timeout.value, web_port: +f.web_port.value, web_bind: f.web_bind.value.trim(),
    repo_url: f.repo_url.value.trim(), branch: f.branch.value.trim(), update_channel: f.update_channel.value,
  };
  const d = await act(() => api("/api/config", { method: "POST", body }));
  if (d) $("#init-preview").textContent = d.init_preview.join("  ");
});
$("#pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ok = await act(() => api("/api/admin-password", { method: "POST", body: { old: $("#pw-old").value, new: $("#pw-new").value } }));
  if (ok) e.target.reset();
});

// ---- modem -------------------------------------------------------------------
$$(".chip[data-cmd]").forEach((b) => b.addEventListener("click", () => { $("#modem-cmds").value = b.dataset.cmd; }));
$("#modem-run").addEventListener("click", async () => {
  const btn = $("#modem-run");
  btn.disabled = true;
  $("#modem-out").textContent = "Čekám na modem…";
  const d = await act(() => api("/api/modem", { method: "POST", body: { commands: $("#modem-cmds").value } }));
  $("#modem-out").textContent = d ? d.output : "Chyba – viz hláška dole.";
  btn.disabled = false;
});

// ---- logy ----------------------------------------------------------------------
async function loadLogs() {
  const d = await act(() => api("/api/logs?n=300"));
  if (!d) return;
  const o = $("#logs-out");
  const atBottom = o.scrollTop + o.clientHeight >= o.scrollHeight - 20;
  o.textContent = d.text;
  if (atBottom) o.scrollTop = o.scrollHeight;
}
$("#logs-refresh").addEventListener("click", loadLogs);

// ---- systém / aktualizace ----------------------------------------------------
async function loadUpdate(quiet, check) {
  let d;
  try { d = await api("/api/update" + (check ? "?check=1" : "")); } catch (e) { if (!quiet) toast(e.message, true); return; }
  $("#upd-version").textContent = d.current.version;
  $("#upd-latest").innerHTML = d.latest
    ? esc(d.latest) + (d.available ? ' <span class="badge warn">nová verze</span>' : ' <span class="badge ok">aktuální</span>')
    : `<span class="muted">${esc(d.error || "–")}</span>`;
  $("#upd-commit").textContent = d.current.commit ? `${d.current.commit} (${d.current.date})` : "– (není git checkout)";
  $("#upd-repo").textContent = d.repo_url ? `${d.repo_url} · ${d.channel === "branch" ? "větev " + d.branch : "tagy"}` : "nenastaven – doplň v Nastavení";
  $("#upd-checked").textContent = fTime(d.checked);
  $("#upd-run").disabled = d.running || !d.repo_url;
  $("#upd-run").textContent = d.running ? "Aktualizace běží…" : (d.available ? `Nainstalovat ${d.latest}` : "Přeinstalovat aktuální verzi");
  if (d.log) $("#upd-log").textContent = d.log;
  if (check) toast(d.error ? d.error : d.available ? `Je dostupná verze ${d.latest}.` : "Máš nejnovější verzi.", !!d.error);
}
$("#upd-check").addEventListener("click", () => loadUpdate(false, true));
$("#upd-badge").addEventListener("click", () => $('#tabs button[data-tab="system"]').click());
$("#upd-run").addEventListener("click", async () => {
  if (!confirm("Stáhnout aktualizaci z GitHubu a nainstalovat? Web se na chvíli restartuje.")) return;
  await act(() => api("/api/update", { method: "POST" }));
  setTimeout(() => loadUpdate(true), 1500);
});
$("#btn-apply").addEventListener("click", () => act(() => api("/api/action", { method: "POST", body: { action: "apply" } })));

// ---- start --------------------------------------------------------------------
(async () => {
  try {
    const s = await (await fetch("/api/session", { headers: { "X-PiTel": "1" } })).json();
    if (s.authenticated) {
      showApp();
      const t = location.hash.slice(1);
      const b = $(`#tabs button[data-tab="${t}"]`);
      if (b) b.click();
    } else showLogin();
  } catch (e) {
    showLogin();
  }
})();
