
const I18N={
 de:{discover:"↻ AKTUALISIEREN",alerts:"HEALTH ALERTS",global:"GLOBALSTATUS",instances:"vLLM-INSTANZEN",gpus:"GPU-STATUS",system:"SYSTEM",benchmark:"BENCHMARK",silicon:"SILIZIUMPRÜFUNG",startBench:"▶ BENCHMARK STARTEN",readFuses:"Fuse-Masken lesen",language:"Sprache"},
 en:{discover:"↻ DISCOVER",alerts:"HEALTH ALERTS",global:"GLOBAL STATUS",instances:"vLLM INSTANCES",gpus:"GPU STATUS",system:"SYSTEM",benchmark:"BENCHMARK SUITE",silicon:"SILICON CHECK",startBench:"▶ START BENCHMARK",readFuses:"Read fuse masks",language:"Language"},
 es:{discover:"↻ ACTUALIZAR",alerts:"ALERTAS",global:"ESTADO GLOBAL",instances:"INSTANCIAS vLLM",gpus:"ESTADO GPU",system:"SISTEMA",benchmark:"BANCO DE PRUEBAS",silicon:"REVISIÓN DEL SILICIO",startBench:"▶ INICIAR PRUEBA",readFuses:"Leer máscaras",language:"Idioma"},
 fr:{discover:"↻ ACTUALISER",alerts:"ALERTES",global:"ÉTAT GLOBAL",instances:"INSTANCES vLLM",gpus:"ÉTAT GPU",system:"SYSTÈME",benchmark:"BANC DE TEST",silicon:"CONTRÔLE DU SILICIUM",startBench:"▶ DÉMARRER LE TEST",readFuses:"Lire les masques",language:"Langue"},
 zh:{discover:"↻ 刷新",alerts:"健康警报",global:"全局状态",instances:"vLLM 实例",gpus:"GPU 状态",system:"系统",benchmark:"基准测试",silicon:"芯片检查",startBench:"▶ 开始测试",readFuses:"读取熔丝掩码",language:"语言"}
};
let LANG=localStorage.getItem("dashboard-lang")||"de";
function t(key){return (I18N[LANG]||I18N.de)[key]||key}
function applyLanguage(){document.documentElement.lang=LANG; document.querySelectorAll("[data-i18n]").forEach(el=>{el.textContent=t(el.dataset.i18n)}); const sel=byId("language"); if(sel) sel.value=LANG;}
function initLanguage(){const sel=byId("language"); if(!sel) return; sel.innerHTML=Object.entries({de:"Deutsch",en:"English",es:"Español",fr:"Français",zh:"中文"}).map(([k,v])=>`<option value="${k}">${v}</option>`).join(""); sel.value=LANG; sel.addEventListener("change",()=>{LANG=sel.value; localStorage.setItem("dashboard-lang",LANG); applyLanguage(); applyLanguage(); renderAll();});}
"use strict";

let DATA = null;
let RANGE_SECONDS = 300;
let requestInFlight = false;

const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const finite = value => typeof value === "number" && Number.isFinite(value);
const number = (value, digits = 1) => finite(value) ? value.toFixed(digits).replace(/\.0$/, "") : "N/A";
const integer = value => finite(value) ? String(Math.round(value)) : "N/A";
const percent = value => finite(value) ? `${number(value, 1)} %` : "N/A";
const metricHtml = value => value === "N/A" ? '<span class="na">N/A</span>' : escapeHtml(value);
const ageText = seconds => finite(seconds) ? (seconds < 1 ? "<1s" : `${Math.round(seconds)}s`) : "N/A";
const dateTime = seconds => finite(seconds) ? new Date(seconds * 1000).toLocaleTimeString() : "N/A";
const qValue = (metric, field = "p50") => metric && finite(metric[field]) ? `${number(metric[field], 1)} ms` : "N/A";
const tooltip = text => escapeHtml(text);

function kpi(label, value, unit, help) {
  return `<div class="kpi" title="${tooltip(help)}"><div class="label">${escapeHtml(label)}</div><div class="value">${metricHtml(value)}${value !== "N/A" && unit ? `<span class="unit">${escapeHtml(unit)}</span>` : ""}</div></div>`;
}

function metric(label, value, help = "") {
  return `<div class="metric"${help ? ` title="${tooltip(help)}"` : ""}><div class="label">${escapeHtml(label)}</div><div class="value">${metricHtml(value)}</div></div>`;
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function fmtAlertValue(value) {
  if (value === null || value === undefined) return "N/A";
  if (typeof value === "number") {
    if (!Number.isFinite(value) || Number.isNaN(value)) return "N/A";
    return String(Math.round(value * 1000) / 1000);
  }
  const text = String(value).trim();
  return text === "" ? "N/A" : text;
}

function alertScopeText(alert) {
  const container = alert && alert.container ? String(alert.container).trim() : "";
  const instanceId = alert && alert.instance_id !== undefined && alert.instance_id !== null ? String(alert.instance_id).trim() : "";
  const gpuIndex = alert && alert.gpu_index !== undefined && alert.gpu_index !== null && String(alert.gpu_index).trim() !== "" ? String(alert.gpu_index).trim() : "";
  if (gpuIndex) return `GPU ${gpuIndex}`;
  const scope = container || instanceId;
  return scope ? (container ? `Container ${scope}` : `Instance ${scope}`) : "Global";
}

function renderAlerts() {
  const rawAlerts = Array.isArray(DATA.alerts) ? DATA.alerts : [];
  const list = byId("alerts-list");
  const empty = byId("alerts-empty");
  const summary = byId("alerts-summary");
  let critical = 0;
  let warning = 0;
  const cards = [];
  for (const alert of rawAlerts) {
    if (!alert || typeof alert !== "object") continue;
    const severity = alert.severity === "critical" || alert.severity === "warning" ? alert.severity : "warning";
    if (severity === "critical") critical += 1; else warning += 1;
    const code = alert.code !== undefined && alert.code !== null ? String(alert.code) : "";
    const message = alert.message !== undefined && alert.message !== null ? String(alert.message) : "(no message)";
    const hasValue = Object.prototype.hasOwnProperty.call(alert, "value") && alert.value !== undefined;
    const valueText = hasValue ? fmtAlertValue(alert.value) : "";
    cards.push(
      `<article class="alert-card alert-${escapeHtml(severity)}" role="group" aria-label="${escapeHtml(severity)} alert">
        <span class="alert-sev">${escapeHtml(severity.toUpperCase())}</span>
        <div class="alert-msg">${code ? `<span class="alert-code">${escapeHtml(code)}</span>` : ""}${escapeHtml(message)}</div>
        <div class="alert-meta">${escapeHtml(alertScopeText(alert))}${hasValue ? `<span class="alert-value">${escapeHtml(valueText)}</span>` : ""}</div>
      </article>`
    );
  }
  if (cards.length === 0) {
    list.hidden = true;
    list.innerHTML = "";
    empty.hidden = false;
    summary.textContent = "0 alerts · system nominal";
  } else {
    list.hidden = false;
    empty.hidden = true;
    list.innerHTML = cards.join("");
    summary.textContent = `${critical} critical · ${warning} warning`;
  }
  return { critical, warning };
}

function renderHeader() {
  const host = byId("host-status");
  host.textContent = DATA.connected ? "HOST CONNECTED" : "DISCOVERY ERROR";
  host.className = `chip ${DATA.connected ? "ok" : "err"}`;

  const active = DATA.aggregate && Number.isInteger(DATA.aggregate.active_instances)
    ? DATA.aggregate.active_instances
    : (DATA.instances || []).filter(item => item.state === "online" && item.discovery_present).length;
  const vllm = byId("vllm-status");
  vllm.textContent = active > 0 ? `vLLM ACTIVE ${active}` : "vLLM IDLE 0";
  vllm.className = `chip ${active > 0 ? "ok" : "idle"}`;

  const ages = (DATA.instances || []).filter(item => item.discovery_present && finite(item.sample_age)).map(item => item.sample_age);
  const freshest = ages.length ? Math.min(...ages) : null;
  const update = byId("last-update");
  update.textContent = `UPDATE ${ageText(freshest)}`;
  update.className = `chip ${freshest != null && freshest <= 10 ? "ok" : "idle"}`;
  byId("host-label").textContent = `${DATA.label || "local host"} · ${DATA.host || "N/A"}`;
  byId("discover-info").textContent = DATA.discover_msg === "OK" ? "" : (DATA.discover_msg || "");
}

function renderGlobal() {
  const a = DATA.aggregate || {};
  const cards = [
    ["Decode", number(a.decode_tps), "tok/s", "Aggregierter Output-Token-Server-Throughput aller aktiven vLLM-Endpunkte."],
    ["Prefill", number(a.prefill_tps), "tok/s", "Aggregierter Prompt-Token-Server-Throughput aller aktiven vLLM-Endpunkte."],
    ["Total", number(a.total_tps), "tok/s", "Decode plus Prefill; kein Einzelrequest-Messwert."],
    ["Running", integer(a.running), "", "Summe aktuell laufender Requests."],
    ["Waiting", integer(a.waiting), "", "Summe aktuell wartender Requests."],
    ["KV max", percent(a.kv_cache_usage_max), "", "Maximale KV-Cache-Auslastung einer aktiven Instanz; nicht summiert."],
    ["TTFT p50 worst", finite(a.ttft_p50_max) ? number(a.ttft_p50_max) : "N/A", "ms", "Höchster aktueller TTFT-p50 einer aktiven Instanz."],
    ["TPOT p50 worst", finite(a.tpot_p50_max) ? number(a.tpot_p50_max) : "N/A", "ms", "Höchster aktueller TPOT-p50 einer aktiven Instanz."],
  ];
  byId("global-kpis").innerHTML = cards.map(args => kpi(...args)).join("");
}

function latencySummary(value) {
  if (!value) return "N/A";
  return ["avg", "p50", "p95", "p99"]
    .filter(key => finite(value[key]))
    .map(key => `${key} ${number(value[key], 1)}ms`)
    .join(" · ") || "N/A";
}

function instanceState(item) {
  if (!item.discovery_present || item.state === "offline") return ["offline", "OFFLINE"];
  if (item.stale) return ["stale", "STALE"];
  if (item.state === "degraded") return ["degraded", "DEGRADED"];
  return ["online", "ONLINE"];
}

function renderInstances() {
  const items = DATA.instances || [];
  byId("empty").style.display = items.length ? "none" : "block";
  byId("instance-summary").textContent = `${items.length} registriert · ${(DATA.aggregate || {}).active_instances || 0} aktiv`;
  const wrap = byId("instances");
  wrap.innerHTML = items.map(item => {
    const [stateClass, stateLabel] = instanceState(item);
    const m = item.metrics || {};
    const live = stateClass === "online" || stateClass === "stale";
    const show = (value, formatter = number) => live ? formatter(value) : "N/A";
    const error = item.last_error ? `<div class="error">${escapeHtml(item.last_error)}</div>` : "";
    return `<article class="card ${stateClass}" data-instance="${escapeHtml(item.instance_id)}">
      <div class="instance-head">
        <span class="model">${escapeHtml(item.model || "Unbekanntes Modell")}</span>
        <span class="state ${stateClass}">${stateLabel}</span>
        <span class="tag">${escapeHtml(item.container || item.instance_id || "N/A")}</span>
        <span class="tag">${escapeHtml(item.endpoint || "kein Endpoint")}</span>
        <span class="tag">ctx ${escapeHtml(item.ctx_len || "N/A")}</span>
        <span class="instance-meta">sample ${ageText(item.sample_age)} · last seen ${dateTime(item.last_seen)}</span>
      </div>
      <div class="kpi-grid">
        ${kpi("Decode", show(m.decode_tps), "tok/s", "Output-Token-Server-Throughput dieser Instanz im letzten Sample-Intervall.")}
        ${kpi("Prefill", show(m.prefill_tps), "tok/s", "Prompt-Token-Server-Throughput dieser Instanz im letzten Sample-Intervall.")}
        ${kpi("Total", show(m.total_tps), "tok/s", "Decode plus Prefill dieser Instanz.")}
        ${kpi("Running", show(m.running, integer), "", "Aktuell laufende Requests.")}
        ${kpi("Waiting", show(m.waiting, integer), "", "Aktuell wartende Requests.")}
        ${kpi("KV cache", show(m.kv_cache_usage, percent), "", "Von vLLM gemeldete KV-Cache-Auslastung.")}
      </div>
      <div class="metrics" style="margin-top:8px">
        ${metric("TTFT", live ? latencySummary(m.ttft) : "N/A", "Bucket-basierte Intervall-Schätzung.")}
        ${metric("TPOT", live ? latencySummary(m.tpot) : "N/A", "Zeit pro Output-Token.")}
        ${metric("ITL", live ? latencySummary(m.itl) : "N/A", "Inter-Token-Latenz.")}
        ${metric("Queue time", live ? latencySummary(m.queue_time) : "N/A")}
        ${metric("E2E", live ? latencySummary(m.e2e) : "N/A")}
        ${metric("Requests/s", show(m.requests_s))}
        ${metric("Success Δ", show(m.req_success, integer))}
        ${metric("Failed Δ", show(m.req_failed, integer))}
        ${metric("Wait capacity", show(m.waiting_capacity, integer))}
        ${metric("Wait deferred", show(m.waiting_deferred, integer))}
        ${metric("Generation total", show(m.generation_tokens_total, integer))}
        ${metric("Prompt total", show(m.prompt_tokens_total, integer))}
        ${metric("Sample interval", live && finite(m.sample_interval) ? `${number(m.sample_interval, 3)}s` : "N/A")}
        ${metric("Counter reset", live ? (m.counter_reset ? "YES" : "no") : "N/A")}
        ${metric("Prefix hit", show(m.prefix_hit_rate, percent))}
        ${metric("Preemptions Δ", show(m.preemptions, integer))}
      </div>
      ${error}
      <div class="charts" data-charts></div>
    </article>`;
  }).join("");

  items.forEach(item => {
    const card = wrap.querySelector(`[data-instance="${CSS.escape(String(item.instance_id))}"]`);
    if (card) drawCharts(card, (item.tail || []).filter(point => !DATA.now || DATA.now - point.ts <= RANGE_SECONDS));
  });
}

const CHARTS = [
  ["decode_t", "Throughput · Decode tok/s"],
  ["prefill_t", "Throughput · Prefill tok/s"],
  ["total_t", "Throughput · Total tok/s"],
  ["running", "Requests · Running"],
  ["waiting", "Requests · Waiting"],
  ["kv", "Cache · KV %"],
  ["ttft", "Latency · TTFT p50 ms"],
  ["tpot", "Latency · TPOT p50 ms"],
];

function drawCharts(card, tail) {
  const wrap = card.querySelector("[data-charts]");
  wrap.innerHTML = CHARTS.map(([key, label]) => `<div class="chart"><div class="chart-title">${label}</div><canvas data-series="${key}"></canvas></div>`).join("");
  wrap.querySelectorAll("canvas").forEach(canvas => drawLine(canvas, tail, canvas.dataset.series));
}

function drawLine(canvas, tail, key) {
  const points = tail.filter(row => finite(row[key])).map(row => [row.ts, row[key]]);
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(220, canvas.clientWidth);
  const height = canvas.clientHeight || 105;
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  ctx.fillStyle = "#080f18"; ctx.fillRect(0, 0, width, height);
  if (points.length < 2) { ctx.fillStyle = "#556575"; ctx.font = "11px sans-serif"; ctx.fillText("warte auf Daten …", 8, 18); return; }
  const values = points.map(point => point[1]);
  const low = Math.min(0, ...values); let high = Math.max(...values); if (high <= low) high = low + 1;
  const pad = 8;
  ctx.strokeStyle = "#48cae4"; ctx.lineWidth = 1.6; ctx.beginPath();
  points.forEach((point, index) => {
    const x = pad + index / (points.length - 1) * (width - pad * 2);
    const y = height - pad - (point[1] - low) / (high - low) * (height - pad * 2);
    if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = "#8195aa"; ctx.font = "10px ui-monospace";
  ctx.fillText(number(high), 3, 11); ctx.fillText(number(values.at(-1)), Math.max(3, width - 55), 11);
}

function renderGpus() {
  const snapshot = DATA.gpus || {};
  const rows = snapshot.gpus || [];
  const grid = byId("gpu-grid");
  const open = new Set([...grid.querySelectorAll("details[open]")].map(el => el.dataset.gpu));
  byId("gpu-time").textContent = snapshot.error ? snapshot.error : `sample ${dateTime(snapshot.ts)}`;
  grid.innerHTML = rows.length ? rows.map(gpu => {
    const memWidth = finite(gpu.mem_pct) ? Math.max(0, Math.min(100, gpu.mem_pct)) : 0;
    return `<article class="card">
      <div class="gpu-name">GPU ${escapeHtml(gpu.index)} · ${escapeHtml(gpu.name)}</div>
      <div class="gpu-bar"><span style="width:${memWidth}%"></span></div>
      <div class="metrics">
        ${metric("GPU util", percent(gpu.util_gpu))}
        ${metric("Memory util", percent(gpu.util_mem))}
        ${metric("VRAM", finite(gpu.mem_used) && finite(gpu.mem_total) ? `${integer(gpu.mem_used)} / ${integer(gpu.mem_total)} MiB · ${percent(gpu.mem_pct)}` : "N/A")}
        ${metric("Temperature", finite(gpu.temp) ? `${number(gpu.temp)} °C` : "N/A")}
        ${metric("Power", finite(gpu.power) ? `${number(gpu.power)} / ${number(gpu.power_limit)} W · ${percent(gpu.power_pct)}` : "N/A")}
        ${metric("Clocks", `G ${number(gpu.clock_graphics)} · M ${number(gpu.clock_memory)} · SM ${number(gpu.clock_sm)} MHz`)}
        ${metric("PCIe", `Gen ${escapeHtml(gpu.pcie_gen || "N/A")}/${escapeHtml(gpu.pcie_gen_max || "N/A")} · x${escapeHtml(gpu.pcie_width || "N/A")}/x${escapeHtml(gpu.pcie_width_max || "N/A")}`)}
        ${metric("ECC corr/uncorr", `${integer(gpu.ecc_corrected)} / ${integer(gpu.ecc_uncorrected)}`)}
        ${metric("UUID", escapeHtml(gpu.uuid || "N/A"))}
      </div>
      <details data-gpu="${escapeHtml(gpu.index)}" ${open.has(String(gpu.index)) ? "open" : ""}><summary>Unlock / SM readout</summary><div class="metrics">
        ${metric("PCI ID", escapeHtml(gpu.pci_device_id || "N/A"))}
        ${metric("Unlock", String(gpu.pci_device_id || "").includes("20C2") && finite(gpu.mem_total) && gpu.mem_total >= 60000 ? "64 GB profile active" : "not proven")}
        ${metric("Active SMs", finite(gpu.active_sms) && finite(gpu.full_ga100_sms) ? `${integer(gpu.active_sms)} / ${integer(gpu.full_ga100_sms)}` : "N/A")}
        ${metric("SM clock max", finite(gpu.max_sm_clock) ? `${integer(gpu.max_sm_clock)} MHz` : "N/A")}
        ${metric("Fuse", fuseSummary((DATA.fuse_scan?.rows||[]).find(row => String(row.gpu)===String(gpu.index))))}
      </div></details><div>
      </div>
    </article>`;
  }).join("") : '<div class="empty">Keine GPU-Daten verfügbar.</div>';
}

// --- Benchmark suite --------------------------------------------------------

const BENCH_NEUTRAL = new Set(["READY", "PLANNED"]);
const BENCH_AMBER = new Set(["RUNNING"]);
const BENCH_GREEN = new Set(["COMPLETE", "MAX_TESTED"]);
const BENCH_RED = new Set(["ABORTED", "FAILED", "BLOCKED"]);

function benchStateColor(state) {
  if (BENCH_GREEN.has(state)) return "green";
  if (BENCH_AMBER.has(state)) return "amber";
  if (BENCH_RED.has(state)) return "red";
  return "neutral";
}

function benchNonEmpty(value) {
  if (value === null || value === undefined) return false;
  return String(value).trim() !== "";
}

function benchValue(value) {
  return benchNonEmpty(value) ? escapeHtml(String(value)) : '<span class="na">N/A</span>';
}

function benchField(value) {
  return benchNonEmpty(value) ? escapeHtml(String(value)) : "N/A";
}

function renderBenchmark() {
  const data = DATA && DATA.benchmark && typeof DATA.benchmark === "object" ? DATA.benchmark : {};
  const state = benchNonEmpty(data.state) ? String(data.state).toUpperCase() : "READY";
  const stateEl = byId("benchmark-state");
  stateEl.textContent = state;
  stateEl.className = `bench-state ${benchStateColor(state)}`;

  // Target container / model / concurrency
  const targetWrap = byId("benchmark-target");
  const targetTags = [];
  if (benchNonEmpty(data.target_container)) {
    targetTags.push(`<span class="tag">Container<strong>${escapeHtml(String(data.target_container))}</strong></span>`);
  } else {
    targetTags.push(`<span class="tag">Container<strong>N/A</strong></span>`);
  }
  if (benchNonEmpty(data.target_model)) {
    targetTags.push(`<span class="tag">Model<strong>${escapeHtml(String(data.target_model))}</strong></span>`);
  } else {
    targetTags.push(`<span class="tag">Model<strong>N/A</strong></span>`);
  }
  if (finite(data.max_concurrency)) {
    targetTags.push(`<span class="tag">Max Concurrency<strong>${integer(data.max_concurrency)}</strong></span>`);
  } else {
    targetTags.push(`<span class="tag">Max Concurrency<strong>N/A</strong></span>`);
  }
  if (benchNonEmpty(data.run_id)) {
    targetTags.push(`<span class="tag">Run-ID<strong>${escapeHtml(String(data.run_id))}</strong></span>`);
  }
  targetWrap.innerHTML = targetTags.join("");

  // Reason (only when set)
  const reasonEl = byId("benchmark-reason");
  if (benchNonEmpty(data.reason)) {
    reasonEl.hidden = false;
    reasonEl.innerHTML = `<strong>Reason:</strong> ${escapeHtml(String(data.reason))}`;
  } else {
    reasonEl.hidden = true;
    reasonEl.textContent = "";
  }

  // Timing info
  const infoParts = [];
  infoParts.push(`<span>started<strong>${benchField(data.started_at)}</strong></span>`);
  infoParts.push(`<span>finished<strong>${benchField(data.finished_at)}</strong></span>`);
  byId("benchmark-info").innerHTML = infoParts.join("");

  // Phases table — render whatever the backend provides, N/A for missing values.
  const phases = Array.isArray(data.phases) ? data.phases : [];
  const tbody = byId("benchmark-phases");
  const table = byId("benchmark-table");
  if (phases.length === 0) {
    table.hidden = true;
    tbody.innerHTML = "";
  } else {
    table.hidden = false;
    tbody.innerHTML = phases.map(phase => {
      if (!phase || typeof phase !== "object") {
        return `<tr><td colspan="3"><span class="na">N/A</span></td></tr>`;
      }
      const concurrency = benchValue(phase.concurrency);
      const phaseState = benchValue(phase.state);
      // Result is a generic summary string from the backend. We try a few common
      // keys but always fall back to a generic JSON-ish view of remaining fields
      // so future fields appear without code changes — missing keys render N/A.
      const resultParts = [];
      for (const key of ["result", "summary", "status", "metric", "error", "tokens_s", "e2e_aggregate_tps", "completion_tokens", "ttft_p50_ms", "tpot_p50_ms", "p99_ms", "duration_s", "e2e_s", "completed", "failed"]) {
        if (Object.prototype.hasOwnProperty.call(phase, key)) {
          const raw = phase[key];
          if (raw === null || raw === undefined || raw === "") {
            resultParts.push(`${escapeHtml(key)}: <span class="na">N/A</span>`);
          } else if (typeof raw === "object") {
            resultParts.push(`${escapeHtml(key)}: ${escapeHtml(JSON.stringify(raw))}`);
          } else {
            resultParts.push(`${escapeHtml(key)}: ${escapeHtml(String(raw))}`);
          }
        }
      }
      const result = resultParts.length ? resultParts.join(" · ") : '<span class="na">N/A</span>';
      return `<tr>
        <td class="mono">${concurrency}</td>
        <td>${phaseState}</td>
        <td>${result}</td>
      </tr>`;
    }).join("");
  }

  // Cancel button only for PLANNED / RUNNING
  const cancelBtn = byId("benchmark-cancel");
  cancelBtn.hidden = !(state === "PLANNED" || state === "RUNNING");

  // Honest messaging: backend only plans/cancels and does not yet generate load.
  const feedback = byId("benchmark-feedback");
  if (state === "READY") {
    feedback.className = "bench-feedback";
    feedback.textContent = "Hinweis: Backend plant aktuell nur und erzeugt noch keine Last — Klick auf START legt einen Plan an.";
  } else if (state === "PLANNED") {
    feedback.className = "bench-feedback";
    feedback.textContent = "Plan erstellt. Last wird im Backend noch nicht erzeugt.";
  } else if (state === "RUNNING") {
    feedback.className = "bench-feedback";
    feedback.textContent = "Geplant. Backend erzeugt aktuell noch keine echte Last.";
  } else if (state === "COMPLETE" || state === "MAX_TESTED") {
    feedback.className = "bench-feedback ok";
    feedback.textContent = "Lauf beendet. Ergebniswerte stammen aus den gemessenen Phasen.";
  } else if (state === "ABORTED" || state === "FAILED" || state === "BLOCKED") {
    feedback.className = "bench-feedback err";
    feedback.textContent = `Lauf ${state}.`;
  } else {
    feedback.className = "bench-feedback";
    feedback.textContent = "";
  }
}

async function benchmarkStart() {
  const button = byId("benchmark-start");
  const ok = window.confirm(
    "Benchmark starten?\n\n" +
    "Das Backend plant den Lauf aktuell nur — es wird (noch) keine Last erzeugt.\n" +
    "Der Lauf bricht automatisch bei fremder Aktivität ab (foreign traffic)."
  );
  if (!ok) return;
  button.disabled = true;
  const feedback = byId("benchmark-feedback");
  feedback.className = "bench-feedback";
  feedback.textContent = "Starte …";
  try {
    const response = await fetch("/api/benchmark/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: true}),
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok && body && body.ok !== false) {
      feedback.className = "bench-feedback ok";
      feedback.textContent = "Plan-Anfrage gesendet.";
      await tick();
    } else {
      const err = (body && (body.err || body.error)) || `HTTP ${response.status}`;
      feedback.className = "bench-feedback err";
      feedback.textContent = `Start fehlgeschlagen: ${err}`;
    }
  } catch (error) {
    feedback.className = "bench-feedback err";
    feedback.textContent = `Netzwerkfehler: ${error}`;
  } finally {
    button.disabled = false;
  }
}

async function benchmarkCancel() {
  const button = byId("benchmark-cancel");
  button.disabled = true;
  const feedback = byId("benchmark-feedback");
  feedback.className = "bench-feedback";
  feedback.textContent = "Abbruch wird gesendet …";
  try {
    const response = await fetch("/api/benchmark/cancel", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({}),
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok) {
      feedback.className = "bench-feedback ok";
      feedback.textContent = "Abbruch gesendet.";
      await tick();
    } else {
      const err = (body && (body.err || body.error)) || `HTTP ${response.status}`;
      feedback.className = "bench-feedback err";
      feedback.textContent = `Abbruch fehlgeschlagen: ${err}`;
    }
  } catch (error) {
    feedback.className = "bench-feedback err";
    feedback.textContent = `Netzwerkfehler: ${error}`;
  } finally {
    button.disabled = false;
  }
}

function renderAll() {
  if (!DATA) return;
  renderHeader(); renderAlerts(); renderGlobal(); renderInstances(); renderGpus(); renderSystem();
  renderBenchmark();
}

byId("range").addEventListener("click", event => {
  const button = event.target.closest("button[data-seconds]"); if (!button) return;
  RANGE_SECONDS = Number(button.dataset.seconds);
  byId("range").querySelectorAll("button").forEach(item => item.classList.toggle("active", item === button));
  renderInstances();
});

byId("discover").addEventListener("click", async () => {
  const button = byId("discover"); button.disabled = true;
  try {
    const response = await fetch("/api/discover", {method: "POST"});
    const body = await response.json();
    byId("discover-info").textContent = body.err ? `ERROR ${body.err}` : `DISCOVERED ${body.discovered}`;
  } catch (error) {
    byId("discover-info").textContent = `NETWORK ERROR ${error}`;
  } finally {
    button.disabled = false; await tick();
  }
});

byId("benchmark-start").addEventListener("click", benchmarkStart);
byId("benchmark-cancel").addEventListener("click", benchmarkCancel);

async function tick() {
  if (requestInFlight) return;
  requestInFlight = true;
  try {
    const response = await fetch("/api/state", {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    DATA = await response.json(); renderAll();
  } catch (error) {
    const host = byId("host-status"); host.textContent = `DASHBOARD ERROR ${error}`; host.className = "chip err";
  } finally { requestInFlight = false; }
}

setInterval(tick, 1500);
tick();

function renderSystem() {
  const data = DATA && DATA.system && typeof DATA.system === "object" ? DATA.system : {};
  const cards = [
    ["Mainboard", data.mainboard],
    ["CPU", data.cpu],
    ["RAM", data.ram],
    ["NVMe", data.nvme],
    ["PLX", data.plx],
  ];
  byId("system-grid").innerHTML = cards.map(([label, value]) => `<article class="kpi"><div class="label">${escapeHtml(label)}</div><div class="value" style="font-size:15px">${benchValue(value)}</div></article>`).join("");
}

function fuseSummary(row){
  if(!row) return "";
  const gpc=row.gpc||{}, fbp=row.fbp||{};
  const liveGpc=8-(gpc.defective?.length||0);
  return `${liveGpc*14} SMs möglich · 80 GB theoretisch · defekte Rechenblöcke ${gpc.defective?.length||0}`;
}
function renderFuseScan() {
  const data = DATA.fuse_scan || {state:"READY", rows:[], error:null};
  const status = byId("fuse-status");
  if (status) status.textContent = data.error || (data.rows?.length ? "Gelesen. Details stehen in den GPU-Karten." : "Noch nicht gelesen.");
  const wrap = byId("fuse-cards");
  if (!wrap) return;
  wrap.innerHTML = (data.rows||[]).map(row => {
    const lines = [
      ["Rechenblöcke", row.gpc, n => `${n*14} SMs`],
      ["Speicher", row.fbp, n => n===10 ? "80 GB" : `${n}/12`],
    ];
    const body = lines.map(([label,item,max]) => {
      const defective=item?.defective?.length||0, blocked=item?.blocked_only?.length||0, width=label==="Speicher"?12:8;
      return `<div class="metric"><div class="label">${label}</div><div class="value">${defective} defekt · ${blocked} gesperrt · max ${max(width-defective)}</div></div>`;
    }).join("");
    return `<article class="card"><div class="gpu-name">GPU ${row.gpu}</div><div class="metrics">${body}</div></article>`;
  }).join("");
}
async function fuseStart(){ byId("fuse-start").disabled=true; const r=await fetch("/api/fuse-scan/start",{method:"POST"}); DATA.fuse_scan=(await r.json()).fuse_scan; renderFuseScan(); byId("fuse-start").disabled=false; }

byId("fuse-start").addEventListener("click", fuseStart);

initLanguage();
applyLanguage();
