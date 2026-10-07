from pathlib import Path
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vLLM Observability Dashboard (Surface-PC)
- SSH-Discovery laufender vLLM/OpenAI-Container auf dem local host (read-only)
- Native Prometheus-Metriken via /metrics, dynamisch gemappt (nichts hardcodiert)
- Abgeleitete Kennzahlen aus Counter-/Histogramm-Deltas
- In-Memory-Ringbuffer, keine DB, keine Cloud
- Nur Python-stdlib
"""
import concurrent.futures, copy, json, os, re, subprocess, threading, time, urllib.request, uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------- Config ----------------
DEFAULTS = {
    "AIPC_HOST": "127.0.0.1",
    "AIPC_LABEL": "local host",
    "SSH_USER": "ai",
    "SSH_KEY": "/home/ai/.ssh/id_ed25519_cmp170hx",
    "SSH_PORT": "22",
    "DISCOVER_TIMEOUT": 12,
    "POLL_INTERVAL": 2.0,
    "PROBE_TIMEOUT": 3.0,
    "LISTEN_PORT": 8080,
    "SERIES_LEN": 1800,
    "SERIES_TAIL": 1800,
    "OFFLINE_RETENTION": 30.0,
    "MAX_SAMPLE_GAP": 10.0,
    "STALE_AFTER": 10.0,
    # Alert thresholds are deliberately conservative: long prefill must not
    # be called a stall after a few seconds without decode tokens.
    "STALL_AFTER": 60.0,
    "KV_WARN_PCT": 95.0,
    "KV_CRIT_PCT": 98.0,
    "GPU_MEM_WARN_PCT": 95.0,
    "GPU_TEMP_WARN_C": 85.0,
    "GPU_TEMP_CRIT_C": 95.0,
}

def load_cfg():
    cfg = dict(DEFAULTS)
    ep = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(ep):
        with open(ep, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, _, v = ln.partition("=")
                k, v = k.strip(), v.strip()
                if k in cfg and isinstance(cfg[k], str):
                    cfg[k] = v
                elif k in cfg:
                    try:
                        cfg[k] = type(cfg[k])(v)
                    except Exception:
                        pass
    return cfg

CFG = load_cfg()

# ---------------- Metric-Map (Schluessel -> Metrik + Herkunft) ----------------
# kind: gauge | counter | rate | q | prefill_rate
METRIC_MAP = [
    ("decode_tps",        "vllm:generation_tokens_total",            "rate",  "Δ tokens / Δ Zeit (kumulativ, Intervall)"),
    ("prefill_tps",       "vllm:prompt_tokens_total",                "prefill_rate", "Δ prompt tokens / Δ wall time"),
    ("total_tps",         "vllm:prompt_tokens_total+vllm:generation_tokens_total", "total_rate", "decode_tps + prefill_tps"),
    ("ttft",              "vllm:time_to_first_token_seconds",        "q",     "Quantile aus Δ-Buckets des TTFT-Histogramms"),
    ("tpot",              "vllm:request_time_per_output_token_seconds", "q",  "Quantile aus Δ-Buckets"),
    ("itl",               "vllm:inter_token_latency_seconds",        "q",     "Quantile aus Δ-Buckets"),
    ("e2e",               "vllm:e2e_request_latency_seconds",        "q",     "Quantile aus Δ-Buckets"),
    ("queue_time",        "vllm:request_queue_time_seconds",         "q",     "Quantile aus Δ-Buckets"),
    ("decode_time",       "vllm:request_decode_time_seconds",        "q",     "Quantile aus Δ-Buckets"),
    ("prefill_kv_computed","vllm:request_prefill_kv_computed_tokens","q",     "Δ-Buckets (tokens)"),
    ("iter_tokens",       "vllm:iteration_tokens_total",             "q",     "Δ-Buckets (tokens)"),
    ("req_gen_tokens",    "vllm:request_generation_tokens",          "q",     "Δ-Buckets (tokens)"),
    ("req_params_max_tokens","vllm:request_params_max_tokens",       "q",     "Δ-Buckets (tokens)"),
    ("running",           "vllm:num_requests_running",               "gauge", "Gauge (aktive Requests)"),
    ("waiting",           "vllm:num_requests_waiting",               "gauge", "Gauge (wartende Requests)"),
    ("kv_cache_usage",    "vllm:kv_cache_usage_perc",                "gauge_pct", "Gauge 0..1 -> %"),
    ("requests_s",        "vllm:request_success_total",              "rate",  "Δ Requests / Δ Zeit"),
    ("req_success",       "vllm:request_success_total",              "counter", "Δ erfolgreiche Requests"),
    ("http_requests",     "http_requests_total",                     "counter", "Δ HTTP-Requests"),
    ("prefix_queries",    "vllm:prefix_cache_queries_total",         "counter", "Δ Queries"),
    ("prefix_hits",       "vllm:prefix_cache_hits_total",            "counter", "Δ Hits"),
    ("ext_queries",       "vllm:external_prefix_cache_queries_total","counter", "Δ Queries (P/D-Xfer)"),
    ("ext_hits",          "vllm:external_prefix_cache_hits_total",   "counter", "Δ Hits (P/D-Xfer)"),
    ("mm_queries",        "vllm:mm_cache_queries_total",             "counter", "Δ Queries (Multimodal)"),
    ("mm_hits",           "vllm:mm_cache_hits_total",                "counter", "Δ Hits (Multimodal)"),
    ("preemptions",       "vllm:num_preemptions_total",              "counter", "Δ Verdraengungen"),
    ("prompt_tokens",     "vllm:prompt_tokens_total",                "counter", "Δ Prompt-Tokens"),
    ("gen_tokens",        "vllm:generation_tokens_total",            "counter", "Δ Generierte Tokens"),
    ("spec_drafts",       "vllm:spec_decode_num_drafts_total",       "counter", "Δ Drafts"),
    ("spec_draft_tokens", "vllm:spec_decode_num_draft_tokens_total", "counter", "Δ Draft-Tokens"),
    ("spec_accepted",     "vllm:spec_decode_num_accepted_tokens_total", "counter", "Δ akzeptierte Tokens"),
    ("flops_gpu",         "vllm:estimated_flops_per_gpu_total",      "rate",  "Δ FLOPs / Δ Zeit"),
    ("read_bytes_gpu",    "vllm:estimated_read_bytes_per_gpu_total", "rate",  "Δ Bytes / Δ Zeit"),
    ("write_bytes_gpu",   "vllm:estimated_write_bytes_per_gpu_total","rate",  "Δ Bytes / Δ Zeit"),
]
DERIVED_KEYS = {"prefill_tps"}
QKEYS = ("ttft", "tpot", "itl", "e2e", "queue_time", "decode_time",
         "prefill_kv_computed", "iter_tokens", "req_gen_tokens", "req_params_max_tokens")
TOKEN_Q_KEYS = {"prefill_kv_computed", "iter_tokens", "req_gen_tokens"}  # s-> tokens (keine s)

LINE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(\S+)$")
LE_RE = re.compile(r'le="([^"]*)"')
LABEL_PAIR_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"')
WANTED = {n for _, n, _, _ in METRIC_MAP if "+" not in n}
for _n in [n for _, n, _, _ in METRIC_MAP if "+" in n]:
    for piece in _n.split("+"):
        WANTED.add(piece)
WANTED.add("vllm:num_requests_waiting_by_reason")

# ---------------- Parsing ----------------
def parse_metrics(text):
    """Zurueck: name -> {val:float, sum:float, count:float, buckets:{le:float}} (ueber Labels summiert)."""
    vals, sums, counts, buckets, series = {}, {}, {}, {}, {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = LINE_RE.match(ln)
        if not m:
            continue
        name = m.group(1)
        labels = m.group(2) or ""
        label_values = {
            key: value.replace('\\"', '"').replace('\\\\', '\\')
            for key, value in LABEL_PAIR_RE.findall(labels)
        }
        sval = m.group(3)
        try:
            fv = float(sval)
        except ValueError:
            continue
        if name.endswith("_bucket"):
            base = name[:-7]
            lms = LE_RE.search(labels)
            try:
                k = float(lms.group(1)) if lms else float("inf")
            except ValueError:
                k = float("inf")
            buckets.setdefault(base, {})
            buckets[base][k] = buckets[base].get(k, 0.0) + fv
        elif name.endswith("_sum"):
            base = name[:-4]
            if base in WANTED:
                sums[base] = sums.get(base, 0.0) + fv
        elif name.endswith("_count"):
            base = name[:-6]
            if base in WANTED:
                counts[base] = counts.get(base, 0.0) + fv
        elif name.endswith("_created"):
            continue
        else:
            if name in WANTED:
                vals[name] = vals.get(name, 0.0) + fv
                series.setdefault(name, []).append({"labels": label_values, "value": fv})
    return {"vals": vals, "sums": sums, "counts": counts, "buckets": buckets, "series": series}


def labeled_totals(parsed, name, label_key):
    totals = {}
    for item in parsed.get("series", {}).get(name, []):
        label_value = item.get("labels", {}).get(label_key)
        if label_value is not None:
            totals[label_value] = totals.get(label_value, 0.0) + item["value"]
    return totals

def quantile(deltas, q):
    """deltas: {le: Δ_cum} — kumulative Histogramm-Inkremente zwischen zwei Polls.
    Frequenzen = Differenzen der kumulativen Werte; +Inf als oberer Rand."""
    les = sorted(k for k in deltas.keys() if k != float("inf"))
    INF = float("inf")
    total = deltas.get(INF, 0.0)
    if total <= 0:
        return None
    target = q * total
    cum_v = 0.0   # verbrauchte kumulative Frequenz
    lo = 0.0      # unterer Rand des aktuellen Buckets
    val = None
    for le in les:
        v = deltas.get(le, 0.0)
        if v < 0:
            v = 0.0
        f = v - cum_v  # Frequenz des Buckets
        if f < 0:
            f = 0.0
        if f > 0 and cum_v + f >= target:
            val = lo + (target - cum_v) / f * (le - lo)
            break
        cum_v += f
        lo = le
    if val is None:
        # target im +Inf-Bereich (sehr langsame Requests) -> nicht als Infinity ausliefern
        return None
    return val

def hist_quantiles(cur, prev):
    if not prev:
        prev = {"vals": {}, "sums": {}, "counts": {}, "buckets": {}}
    deltas = {}
    for le, v in cur.items():
        d = v - prev.get(le, 0.0)
        if d < 0:  # counter reset
            d = v
        deltas[le] = d
    cnt = cur.get(float("inf"), 0.0) - prev.get(float("inf"), 0.0)
    if cnt < 0:
        cnt = cur.get(float("inf"), 0.0)
    if cnt <= 0:
        return None
    def q(f):
        return quantile(deltas, f)
    return {"current": None, "avg": None, "p50": q(0.50), "p95": q(0.95), "p99": q(0.99)}

def hist_full(cur, prev, key):
    """Vollständige Latenz-Ableitung: current+avg aus _sum/_count-Deltas,
    p50/p95/p99 aus Δ-Buckets."""
    cur_b, prev_b = cur["buckets"].get(key), prev["buckets"].get(key)
    q = hist_quantiles(cur_b, prev_b) if cur_b else None
    cs, ps = cur["sums"].get(key), prev["sums"].get(key)
    cc, pc = cur["counts"].get(key), prev["counts"].get(key)
    avg = None
    if cs is not None and ps is not None and cc is not None and pc is not None:
        ds, dc = cs - ps, cc - pc
        if ds < 0 or dc < 0:  # counter reset
            ds, dc = cs, cc
        if dc > 0:
            avg = ds / dc
    if q:
        q["avg"] = avg
    elif avg is not None:
        q = {"current": None, "avg": avg, "p50": None, "p95": None, "p99": None}
    return q

# ---------------- Registry ----------------
class Registry:
    def __init__(self):
        self.lock = threading.Lock()
        self.instances = {}   # id -> dict (mit _prev/_prev_t internal)
        self.anchor = {}      # id -> (ts, parsed) Prev-Anker fuer Quantile
        self.series = {}      # id -> deque
        self.last_discover = 0.0
        self.discover_msg = "noch nicht gestartet"
        self.connected = False
        self.stats = {"discovered": 0, "poll_ok": 0, "poll_err": 0}
        # The worker is added only after API/UI safety paths are validated.
        # This state alone cannot generate benchmark traffic.
        self.benchmark = {
            "state": "READY", "run_id": None, "phases": [], "reason": None,
            "started_at": None, "finished_at": None, "max_concurrency": 16,
        }

REG = Registry()

# ---------------- Discovery ----------------
def ssh_run(cmd, timeout):
    args = ["ssh", "-i", CFG["SSH_KEY"], "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=4", "-p", str(CFG["SSH_PORT"]),
            "%s@%s" % (CFG["SSH_USER"], CFG["AIPC_HOST"]), cmd]
    p = subprocess_run(args, timeout)
    return p

def subprocess_run(args, timeout):
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), p.returncode

DOCKER_PS_FMT = "{{.ID}}|{{.Names}}|{{.Image}}|{{.Ports}}|{{.Status}}"

def http_get(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "text/plain, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def configured_host():
    import os
    for line in (os.environ.get("VLLM_HOST",""),):
        if line: return line
    try:
        for raw in open(".env",encoding="utf-8"):
            if raw.startswith("VLLM_HOST="): return raw.split("=",1)[1].strip()
    except Exception: pass
    return ""

def discover():
    cmd = "docker ps --format '%s'" % DOCKER_PS_FMT
    try:
        out, err, rc = ssh_run(cmd, CFG["DISCOVER_TIMEOUT"])
    except subprocess.TimeoutExpired:
        return [], "ssh-timeout"
    except Exception as e:
        return [], "ssh-exfail %s" % e
    if rc != 0 and not out:
        return [], "ssh-rc=%d %s" % (rc, " ".join((err or "").split())[:150])
    insts, seen = [], set()
    for ln in out.splitlines():
        parts = ln.split("|")
        if len(parts) < 4 or not parts[0]:
            continue
        cid, name, image, ports = parts[0], parts[1], parts[2], parts[3]
        status = parts[4] if len(parts) > 4 else ""
        cip = ""
        pid = parts[6] if len(parts) > 6 else ""
        # Port-Mapping: host:container, mehrere Kandidaten, none hardcodiert
        endpoint, model, ctx_len = "", "", 0
        for m in re.finditer(r"(?:\S+:)?(\d+)->(\d+)/tcp", ports):
            ep = "http://%s:%s" % (CFG["AIPC_HOST"], m.group(1))
            try:
                body = http_get(ep + "/v1/models", CFG["PROBE_TIMEOUT"])
                j = json.loads(body)
                ms = [x.get("id") for x in j.get("data", []) if x.get("id")]
            except Exception:
                continue
            if not ms:
                continue
            endpoint, model = ep, ms[0]
            # ctx_len: max_model_len aus /v1/models nicht standard -> try /v1/models payload
            break
        if not endpoint and "vllm" in (image + " " + name).lower():
            # vllm-Image, aber kein /v1/models erreichbar
            insts.append({"instance_id": cid[:12], "container": name, "image": image,
                          "model": "", "endpoint": "", "container_ip": cip, "pid": pid,
                          "started_at": status, "ctx_len": 0, "tp_size": None, "pp_size": None,
                          "state": "degraded", "created": time.time(), "last_ok": 0.0,
                          "consecutive_failures": 1})
            continue
        if not endpoint:
            continue  # kein vLLM-Server
        insts.append({"instance_id": cid[:12], "container": name, "image": image,
                      "model": model, "endpoint": endpoint, "container_ip": cip, "pid": pid,
                      "started_at": status, "ctx_len": 0, "tp_size": None, "pp_size": None,
                      "state": "degraded", "created": time.time(), "last_ok": 0.0,
                      "consecutive_failures": 0})
        seen.add(cid[:12])
    return insts, None

def apply_discover(insts, err=None):
    with REG.lock:
        now = time.time()
        # Global discovery transport state
        if err is None:
            REG.connected = True
            REG.discover_msg = "OK"
        else:
            REG.connected = False
            REG.discover_msg = "Discovery-Fehler: %s" % err
        REG.last_discover = now
        seen_ids = set()
        for i in insts:
            iid = i["instance_id"]
            seen_ids.add(iid)
            old = REG.instances.get(iid)
            identity_changed = bool(old) and (
                old.get("endpoint") != i.get("endpoint")
                or (
                    old.get("model")
                    and i.get("model")
                    and old.get("model") != i.get("model")
                )
            )
            if old and not identity_changed:
                i["prev"] = old.get("prev")
                i["prev_t"] = old.get("prev_t")
                i["_vals"] = old.get("_vals")
                i["sample_ts"] = old.get("sample_ts")
                i["consecutive_failures"] = old.get("consecutive_failures", 0)
                i["created"] = old.get("created", i.get("created", now))
                i["last_ok"] = old.get("last_ok", 0.0)
                if not i.get("ctx_len") and old.get("ctx_len"):
                    i["ctx_len"] = old.get("ctx_len")
                if not i.get("model") and old.get("model"):
                    i["model"] = old.get("model")
                if (
                    old.get("state") == "online"
                    and old.get("last_ok")
                    and now - old["last_ok"] <= float(CFG.get("STALE_AFTER", 10.0))
                ):
                    i["state"] = "online"
            else:
                i.setdefault("created", now)
                i.setdefault("last_ok", 0.0)
                i.setdefault("prev", None)
                i.setdefault("prev_t", 0.0)
                i.setdefault("_vals", None)
                i.setdefault("sample_ts", None)
                i.setdefault("consecutive_failures", 0)
                if identity_changed:
                    REG.anchor.pop(iid, None)
                    REG.series.pop(iid, None)
            i["discovery_present"] = True
            i["last_seen"] = now
            i["stale"] = False
            i["offline_since"] = None
            i["last_error"] = None
            REG.instances[iid] = i
        # Missing old ids (only when discovery was successful transport-wise)
        if err is None:
            retention = float(CFG.get("OFFLINE_RETENTION", 30.0))
            for iid in list(REG.instances.keys()):
                if iid in seen_ids:
                    continue
                old = REG.instances[iid]
                # Already offline -> apply retention and prune
                if old.get("state") == "offline":
                    offline_since = old.get("offline_since")
                    if offline_since and (now - offline_since) > retention:
                        del REG.instances[iid]
                    continue
                # First time we notice it's gone
                old["state"] = "offline"
                old["stale"] = True
                old["discovery_present"] = False
                old["last_error"] = "not present in latest discovery"
                old["offline_since"] = now
                # Preserve last_known_metrics from _vals, clear live metrics
                if old.get("_vals") is not None and old.get("last_known_metrics") is None:
                    old["last_known_metrics"] = old.get("_vals")
                old["_vals"] = None
                old["sample_ts"] = None
                old["metrics"] = None
        # Stats
        if err is None:
            REG.stats["discovered"] = len([x for x in insts if x.get("endpoint")])
        else:
            REG.stats["discovered"] = 0
    return insts

# Registry.prev helpers
def _r_get(self, iid):
    return self.instances.get(iid)
Registry.prev_set = lambda self, iid, i: None  # placeholder

# ---------------- Collector ----------------
def collect(iid):
    inst = REG.instances.get(iid)
    if not inst or not inst.get("endpoint") or not inst.get("discovery_present"):
        return None
    try:
        raw = http_get(inst["endpoint"] + "/metrics", CFG["PROBE_TIMEOUT"])
    except Exception as exc:
        inst["consecutive_failures"] = inst.get("consecutive_failures", 0) + 1
        inst["last_error"] = "metrics: %s: %s" % (type(exc).__name__, exc)
        REG.stats["poll_err"] += 1
        if inst["consecutive_failures"] >= 3:
            inst["state"] = "offline"
            inst["stale"] = True
        return None
    cur = parse_metrics(raw)
    empty = {"vals": {}, "sums": {}, "counts": {}, "buckets": {}}
    previous_sample = inst.get("prev")
    prev = previous_sample or empty
    prev_t = inst.get("prev_t") or 0.0
    now = time.time()
    dt = now - prev_t if prev_t and now > prev_t else 0.0
    window_valid = bool(previous_sample) and 0.0 < dt <= float(CFG.get("MAX_SAMPLE_GAP", 10.0))
    vals = {}
    generation_name = "vllm:generation_tokens_total"
    prompt_name = "vllm:prompt_tokens_total"
    current_generation = cur["vals"].get(generation_name)
    previous_generation = prev["vals"].get(generation_name)
    current_prompt = cur["vals"].get(prompt_name)
    previous_prompt = prev["vals"].get(prompt_name)
    counter_reset = bool(
        window_valid
        and (
            (
                current_generation is not None
                and previous_generation is not None
                and current_generation < previous_generation
            )
            or (
                current_prompt is not None
                and previous_prompt is not None
                and current_prompt < previous_prompt
            )
        )
    )
    inst["counter_reset"] = counter_reset

    # Track concrete token-counter progress separately from request liveness.
    # A running request with no prompt or generation counter movement for a
    # conservative interval is actionable; idle instances never trigger this.
    if not inst.get("last_token_progress_ts"):
        inst["last_token_progress_ts"] = now
    elif (
        window_valid
        and not counter_reset
        and (
            (current_generation is not None and previous_generation is not None
             and current_generation > previous_generation)
            or (current_prompt is not None and previous_prompt is not None
                and current_prompt > previous_prompt)
        )
    ):
        inst["last_token_progress_ts"] = now

    def wall_rate(current_value, previous_value):
        if (
            not window_valid
            or counter_reset
            or current_value is None
            or previous_value is None
            or current_value < previous_value
        ):
            return None
        return round((current_value - previous_value) / dt, 1)

    vals["decode_tps"] = wall_rate(current_generation, previous_generation)
    vals["prefill_tps"] = wall_rate(current_prompt, previous_prompt)
    if vals["decode_tps"] is not None and vals["prefill_tps"] is not None:
        vals["total_tps"] = round(vals["decode_tps"] + vals["prefill_tps"], 1)
    else:
        vals["total_tps"] = None
    vals["generation_tokens_total"] = current_generation
    vals["prompt_tokens_total"] = current_prompt
    vals["sample_interval"] = round(dt, 3) if window_valid else None
    vals["counter_reset"] = counter_reset

    # Prev-Anker (~60s-Fenster) fuer Quantile: verhindert Flackern,
    # weil vLLM-Histogramme (TTFT u.a.) erst bei Request-Ende gezaehlt werden
    anc = REG.anchor.get(iid)
    prev_q = anc[1] if anc and window_valid and not counter_reset else None
    histogram_reset = False
    if prev_q:
        histogram_reset = any(
            cur["counts"].get(name, 0.0) < prev_q["counts"].get(name, 0.0)
            for name in (
                "vllm:time_to_first_token_seconds",
                "vllm:request_time_per_output_token_seconds",
                "vllm:inter_token_latency_seconds",
                "vllm:e2e_request_latency_seconds",
                "vllm:request_queue_time_seconds",
                "vllm:request_decode_time_seconds",
            )
        )
    if histogram_reset:
        prev_q = None
    if anc is None or not window_valid or counter_reset or histogram_reset or (now - anc[0]) > 60.0:
        REG.anchor[iid] = (now, cur)
    for key, name, kind, _note in METRIC_MAP:
        if key in ("decode_tps", "prefill_tps", "total_tps"):
            continue
        if kind == "gauge" or kind == "gauge_pct":
            v = cur["vals"].get(name)
            if v is None:
                vals[key] = None
            else:
                vals[key] = round(v * 100.0, 2) if kind == "gauge_pct" else v
            continue
        if kind == "counter":
            cv, pv = cur["vals"].get(name), prev["vals"].get(name)
            if cv is None or pv is None or not window_valid or cv < pv:
                vals[key] = None
            else:
                vals[key] = round(cv - pv, 1)
            continue
        if kind == "rate":
            cv, pv = cur["vals"].get(name), prev["vals"].get(name)
            if cv is None or pv is None or not window_valid or cv < pv:
                vals[key] = None
            else:
                vals[key] = round((cv - pv) / dt, 1)
            continue
        if kind == "q":
            vals[key] = hist_full(cur, prev_q, name) if prev_q else None
            continue
        vals[key] = None
    # Labeled request outcomes: vLLM uses one metric name with finished_reason.
    current_outcomes = labeled_totals(
        cur, "vllm:request_success_total", "finished_reason"
    )
    previous_outcomes = labeled_totals(
        prev, "vllm:request_success_total", "finished_reason"
    )
    if current_outcomes and window_valid and not counter_reset:
        outcome_deltas = {}
        outcome_reset = False
        for reason, current_value in current_outcomes.items():
            previous_value = previous_outcomes.get(reason, 0.0)
            if current_value < previous_value:
                outcome_reset = True
                break
            outcome_deltas[reason] = current_value - previous_value
        if not outcome_reset:
            success_reasons = ("stop", "length", "repetition")
            failure_reasons = ("abort", "error")
            vals["req_success"] = round(
                sum(outcome_deltas.get(reason, 0.0) for reason in success_reasons), 1
            )
            vals["req_failed"] = round(
                sum(outcome_deltas.get(reason, 0.0) for reason in failure_reasons), 1
            )
            vals["requests_s"] = round(
                (vals["req_success"] + vals["req_failed"]) / dt, 3
            )
        else:
            vals["req_success"] = None
            vals["req_failed"] = None
            vals["requests_s"] = None
    else:
        vals["req_failed"] = None
        if vals.get("req_success") is not None and dt > 0:
            vals["requests_s"] = round(vals["req_success"] / dt, 3)

    waiting_reasons = labeled_totals(
        cur, "vllm:num_requests_waiting_by_reason", "reason"
    )
    vals["waiting_capacity"] = waiting_reasons.get("capacity")
    vals["waiting_deferred"] = waiting_reasons.get("deferred")
    # prefix hit rate
    pq, ph = vals.get("prefix_queries"), vals.get("prefix_hits")
    if pq and pq > 0 and ph is not None:
        vals["prefix_hit_rate"] = round(min(1.0, ph / pq) * 100.0, 1)
    else:
        vals["prefix_hit_rate"] = None
    # s -> ms fuer Hist-Quantile (current: nicht aus kumulativem Hist ableitbar -> N/A)
    for k in ("ttft", "tpot", "itl", "e2e", "queue_time", "decode_time"):
        q = vals.get(k)
        if q:
            if q.get("current") is not None:
                q["current"] = round(q["current"] * 1000.0, 1)
            if q.get("avg") is not None:
                q["avg"] = round(q["avg"] * 1000.0, 1)
            for kk in ("p50", "p95", "p99"):
                if q.get(kk) is not None:
                    q[kk] = round(q[kk] * 1000.0, 1)
            vals[k] = q
    # token-count quantiles: tokens, keine ms
    for k in TOKEN_Q_KEYS:
        q = vals.get(k)
        if q:
            for kk in ("p50", "p95", "p99"):
                if q.get(kk) is not None:
                    q[kk] = round(q[kk], 1)
            q["current"] = None
            q["avg"] = None
    inst["prev"] = cur
    inst["prev_t"] = now
    inst["last_ok"] = now
    inst["sample_ts"] = now
    inst["last_error"] = None
    inst["stale"] = False
    inst["state"] = "online"
    inst["consecutive_failures"] = 0
    REG.stats["poll_ok"] += 1
    return vals

def series_row(vals, ts):
    return {
        "ts": round(ts, 3),
        "decode_t": vals.get("decode_tps"),
        "prefill_t": vals.get("prefill_tps"),
        "total_t": vals.get("total_tps"),
        "ttft": (vals.get("ttft") or {}).get("p50"),
        "tpot": (vals.get("tpot") or {}).get("p50"),
        "itl": (vals.get("itl") or {}).get("p50"),
        "running": vals.get("running"),
        "waiting": vals.get("waiting"),
        "kv": vals.get("kv_cache_usage"),
        "req_s": vals.get("requests_s"),
    }

# ---------------- Loop ----------------
def discovery_loop():
    do_full_discover()
    while True:
        time.sleep(60.0)
        do_full_discover()

def do_full_discover():
    insts, err = discover()
    apply_discover(insts, err)
    return insts, err

def fill_initial_values(inst):
    """Wenn noch keine prev: /metrics holen, prev setzen, N/A fuer rate-Kennzahlen vermeiden."""
    try:
        raw = http_get(inst["endpoint"] + "/metrics", CFG["PROBE_TIMEOUT"])
        cur = parse_metrics(raw)
        inst["prev"] = cur
        inst["prev_t"] = time.time()
        # ctx_len
        body = http_get(inst["endpoint"] + "/v1/models", CFG["PROBE_TIMEOUT"])
        j = json.loads(body)
        dat = j.get("data") or []
        if dat:
            inst["model"] = dat[0].get("id", inst.get("model", ""))
            inst["ctx_len"] = int(dat[0].get("max_model_len", 0) or 0)
    except Exception:
        pass

def collect_loop():
    while True:
        t0 = time.time()
        with REG.lock:
            ids = [iid for iid, i in REG.instances.items()
                   if i.get("endpoint") and i.get("discovery_present")]
        for iid in ids:
            inst = REG.instances.get(iid)
            if not inst:
                continue
            if inst.get("prev") is None:
                fill_initial_values(inst)
            vals = collect(iid)
            if vals is not None:
                inst["_vals"] = vals
                ser = REG.series.setdefault(iid, deque(maxlen=int(CFG["SERIES_LEN"])))
                ser.append(series_row(vals, time.time()))
        took = time.time() - t0
        time.sleep(max(0.0, float(CFG["POLL_INTERVAL"]) - took))

def _number_or_none(value):
    value = str(value).strip().strip("[]")
    if not value or value.upper() in ("N/A", "NA", "NOT SUPPORTED"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_gpu_csv(text):
    gpus = []
    for line in text.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) < 19:
            continue
        mem_used = _number_or_none(fields[5])
        mem_total = _number_or_none(fields[6])
        power = _number_or_none(fields[8])
        power_limit = _number_or_none(fields[9])
        gpus.append({
            "index": fields[0],
            "name": fields[1],
            "uuid": fields[2],
            "util_gpu": _number_or_none(fields[3]),
            "util_mem": _number_or_none(fields[4]),
            "mem_used": mem_used,
            "mem_total": mem_total,
            "mem_pct": round(mem_used / mem_total * 100.0, 2) if mem_used is not None and mem_total else None,
            "temp": _number_or_none(fields[7]),
            "power": power,
            "power_limit": power_limit,
            "power_pct": round(power / power_limit * 100.0, 2) if power is not None and power_limit else None,
            "clock_graphics": _number_or_none(fields[10]),
            "clock_memory": _number_or_none(fields[11]),
            "clock_sm": _number_or_none(fields[12]),
            "pcie_gen": fields[13],
            "pcie_gen_max": fields[14],
            "pcie_width": fields[15],
            "pcie_width_max": fields[16],
            "ecc_uncorrected": _number_or_none(fields[17]),
            "ecc_corrected": _number_or_none(fields[18]),
            "pci_device_id": fields[19] if len(fields) > 19 else None,
            "max_sm_clock": _number_or_none(fields[20]) if len(fields) > 20 else None,
            "active_sms": 70,
            "full_ga100_sms": 108,
        })
    return gpus


def gpu_loop():
    query = (
        "index,name,uuid,utilization.gpu,utilization.memory,memory.used,memory.total,"
        "temperature.gpu,power.draw,power.limit,clocks.gr,clocks.mem,clocks.sm,"
        "pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,"
        "pcie.link.width.max,ecc.errors.uncorrected.volatile.total,"
        "ecc.errors.corrected.volatile.total,pci.device_id,clocks.max.sm"
    )
    command = (
        "printf '__GPUS__\\n'; nvidia-smi --query-gpu=" + query
        + " --format=csv,noheader,nounits; printf '__APPS__\\n'; "
        "nvidia-smi --query-compute-apps=pid,gpu_uuid,used_gpu_memory "
        "--format=csv,noheader,nounits"
    )
    while True:
        try:
            out, err, rc = ssh_run(command, 8)
        except Exception as exc:
            REG.gpu = {"ts": time.time(), "gpus": [], "apps": [], "error": str(exc)}
            time.sleep(5)
            continue
        if rc == 0 and "__GPUS__" in out and "__APPS__" in out:
            gpu_text, app_text = out.split("__APPS__", 1)
            gpu_text = gpu_text.split("__GPUS__", 1)[1]
            gpus = parse_gpu_csv(gpu_text)
            app_list = []
            if app_text.strip():
                for ln in app_text.splitlines():
                    f = [x.strip() for x in ln.split(",")]
                    if len(f) >= 3:
                        app_list.append({
                            "pid": f[0], "gpu_uuid": f[1],
                            "mem_mb": _number_or_none(f[2]),
                        })
            REG.gpu = {"ts": time.time(), "gpus": gpus, "apps": app_list, "error": None}
        else:
            REG.gpu = {
                "ts": time.time(), "gpus": [], "apps": [],
                "error": "nvidia-smi rc=%s %s" % (rc, " ".join(err.split())[:160]),
            }
        time.sleep(5)

# registry: gpu slot
REG.gpu = {"ts": 0.0, "gpus": [], "apps": []}

# ---------------- JSON + Public ----------------
def build_aggregate(instances):
    live = [
        inst for inst in instances
        if inst.get("state") == "online" and inst.get("discovery_present")
    ]

    def numeric_values(key):
        return [
            inst.get("_vals", {}).get(key)
            for inst in live
            if isinstance(inst.get("_vals", {}).get(key), (int, float))
        ]

    def summed(key):
        values = numeric_values(key)
        return round(sum(values), 3) if values else None

    def maximum(key):
        values = numeric_values(key)
        return max(values) if values else None

    def nested_maximum(key, field):
        values = []
        for inst in live:
            metric = inst.get("_vals", {}).get(key)
            value = metric.get(field) if isinstance(metric, dict) else None
            if isinstance(value, (int, float)):
                values.append(value)
        return max(values) if values else None

    return {
        "active_instances": len(live),
        "decode_tps": summed("decode_tps"),
        "prefill_tps": summed("prefill_tps"),
        "total_tps": summed("total_tps"),
        "running": summed("running"),
        "waiting": summed("waiting"),
        "requests_s": summed("requests_s"),
        "kv_cache_usage_max": maximum("kv_cache_usage"),
        "ttft_p50_max": nested_maximum("ttft", "p50"),
        "tpot_p50_max": nested_maximum("tpot", "p50"),
    }


def build_alerts(instances, gpus, now=None):
    """Return only evidenced, actionable health alerts.

    Queue, cache and counter values are never inferred: absent metrics simply
    create no alert. Alert evaluation is pure so it can be regression-tested
    without a running vLLM server.
    """
    now = time.time() if now is None else now
    alerts = []

    def add(severity, code, message, inst=None, gpu=None, value=None):
        item = {"severity": severity, "code": code, "message": message}
        if inst is not None:
            item["instance_id"] = inst.get("instance_id")
            item["container"] = inst.get("container", "")
        if gpu is not None:
            item["gpu_index"] = gpu.get("index")
        if value is not None:
            item["value"] = value
        alerts.append(item)

    for inst in instances:
        if not inst.get("discovery_present") or inst.get("state") == "offline":
            add("critical", "instance_offline", "Instance is not in the latest discovery", inst)
            continue
        sample_ts = inst.get("sample_ts")
        sample_age = now - sample_ts if isinstance(sample_ts, (int, float)) else None
        if inst.get("stale") or (sample_age is not None and sample_age > float(CFG["STALE_AFTER"])):
            add("warning", "metrics_stale", "Metrics sample is older than the stale threshold", inst, value=round(sample_age or 0.0, 1))
        failures = inst.get("consecutive_failures", 0)
        if failures:
            add("warning", "metrics_poll_failed", "Metrics polling has failed", inst, value=failures)
        vals = inst.get("_vals") or {}
        waiting = vals.get("waiting")
        if isinstance(waiting, (int, float)) and waiting > 0:
            add("warning", "queue_waiting", "Requests are waiting in the vLLM queue", inst, value=waiting)
        kv = vals.get("kv_cache_usage")
        if isinstance(kv, (int, float)):
            if kv >= float(CFG["KV_CRIT_PCT"]):
                add("critical", "kv_cache_critical", "KV cache is critically full", inst, value=kv)
            elif kv >= float(CFG["KV_WARN_PCT"]):
                add("warning", "kv_cache_pressure", "KV cache pressure is high", inst, value=kv)
        preemptions = vals.get("preemptions")
        if isinstance(preemptions, (int, float)) and preemptions > 0:
            add("warning", "preemptions_detected", "vLLM preempted one or more requests", inst, value=preemptions)
        running = vals.get("running")
        progress_ts = inst.get("last_token_progress_ts")
        progress_age = now - progress_ts if isinstance(progress_ts, (int, float)) else None
        if (
            isinstance(running, (int, float)) and running > 0
            and progress_age is not None and progress_age >= float(CFG["STALL_AFTER"])
        ):
            add("critical", "token_progress_stalled", "Running request has no token-counter progress", inst, value=round(progress_age, 1))

    for gpu in gpus or []:
        ecc = gpu.get("ecc_uncorrected")
        if isinstance(ecc, (int, float)) and ecc > 0:
            add("critical", "gpu_ecc_uncorrected", "GPU reports uncorrected volatile ECC errors", gpu=gpu, value=ecc)
        temp = gpu.get("temp")
        if isinstance(temp, (int, float)):
            if temp >= float(CFG["GPU_TEMP_CRIT_C"]):
                add("critical", "gpu_temperature_critical", "GPU temperature is critical", gpu=gpu, value=temp)
            elif temp >= float(CFG["GPU_TEMP_WARN_C"]):
                add("warning", "gpu_temperature_high", "GPU temperature is high", gpu=gpu, value=temp)
        mem_pct = gpu.get("mem_pct")
        if isinstance(mem_pct, (int, float)) and mem_pct >= float(CFG["GPU_MEM_WARN_PCT"]):
            add("warning", "gpu_memory_pressure", "GPU memory pressure is high", gpu=gpu, value=mem_pct)
    return alerts


def instance_public(iid, inst, tail):
    now = time.time()
    sample_ts = inst.get("sample_ts")
    is_live = inst.get("state") == "online" and bool(inst.get("discovery_present"))
    sample_age = round(now - sample_ts, 3) if sample_ts else None
    is_stale = bool(inst.get("stale", not is_live)) or (
        sample_age is not None and sample_age > float(CFG.get("STALE_AFTER", 10.0))
    )
    return {
        "instance_id": iid, "container": inst.get("container", ""),
        "image": inst.get("image", ""), "model": inst.get("model", ""),
        "endpoint": inst.get("endpoint", ""), "container_ip": inst.get("container_ip", ""),
        "pid": inst.get("pid", ""), "started_at": inst.get("started_at", ""),
        "ctx_len": inst.get("ctx_len", 0), "tp_size": inst.get("tp_size"),
        "pp_size": inst.get("pp_size"), "state": inst.get("state", "unknown"),
        "last_ok": inst.get("last_ok", 0.0),
        "discovery_present": bool(inst.get("discovery_present")),
        "last_seen": inst.get("last_seen"),
        "sample_ts": sample_ts,
        "sample_age": sample_age,
        "stale": is_stale,
        "last_error": inst.get("last_error"),
        "offline_since": inst.get("offline_since"),
        "last_token_progress_ts": inst.get("last_token_progress_ts"),
        "last_known_metrics": inst.get("last_known_metrics"),
        "metrics": inst.get("_vals") if is_live else None,
        "tail": tail,
    }

# ---------------- HTTP ----------------
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif p == "/app.js":
            self._send(200, APP_JS, "application/javascript; charset=utf-8")
        elif p == "/api/state":
            tail_n = int(CFG["SERIES_TAIL"])
            insts = []
            with REG.lock:
                pub = {"host": CFG["AIPC_HOST"], "label": CFG["AIPC_LABEL"],
                       "connected": REG.connected, "discover_msg": REG.discover_msg,
                       "last_discover": REG.last_discover, "now": time.time(),
                       "gpus": REG.gpu, "stats": REG.stats}
                for iid, inst in REG.instances.items():
                    ser = REG.series.get(iid)
                    tail = []
                    if ser:
                        arr = list(ser)
                        tail = arr[-tail_n:] if len(arr) > tail_n else arr
                    insts.append(instance_public(iid, inst, tail))
                pub["instances"] = insts
                raw_instances = list(REG.instances.values())
                pub["aggregate"] = build_aggregate(raw_instances)
                pub["alerts"] = build_alerts(raw_instances, REG.gpu.get("gpus", []), now=pub["now"])
                pub["alert_summary"] = {
                    "critical": sum(1 for alert in pub["alerts"] if alert["severity"] == "critical"),
                    "warning": sum(1 for alert in pub["alerts"] if alert["severity"] == "warning"),
                }
                pub["system"] = system_public()
                pub["fuse_scan"] = getattr(REG, "fuse_scan", {"state":"READY","rows":[],"findings":[],"error":None})
                pub["benchmark"] = benchmark_public()
            self._send(200, json.dumps(pub, ensure_ascii=False), "application/json; charset=utf-8")
        elif p == "/api/health":
            self._send(200, json.dumps({"ok": True}), "application/json")
        elif p == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
        else:
            self._send(404, "not found")

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/discover":
            insts, err = do_full_discover()
            self._send(200, json.dumps({"discovered": len([x for x in insts if x.get("endpoint")]), "err": err}, ensure_ascii=False),
                       "application/json; charset=utf-8")
            return
        
        if p == "/api/fuse-scan/start":
            try:
                result = read_fuse_scan()
                REG.fuse_scan = result
                self._send(200, json.dumps({"ok": True, "fuse_scan": result}, ensure_ascii=False), "application/json")
            except Exception as exc:
                result = {"state": "FAILED", "rows": [], "findings": [], "error": str(exc)}
                REG.fuse_scan = result
                self._send(500, json.dumps({"ok": False, "fuse_scan": result}, ensure_ascii=False), "application/json")
            return
        if p == "/api/benchmark/start":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(max(0, min(length, 4096))).decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, json.dumps({"ok": False, "code": "invalid_json"}), "application/json")
                return
            with REG.lock:
                result = benchmark_start_plan(payload.get("confirm") is True)
            self._send(200 if result["ok"] else 409, json.dumps(result, ensure_ascii=False), "application/json")
            return
        if p == "/api/benchmark/history/clear":
            with REG.lock:
                try: Path(HISTORY_PATH).write_text("[]", encoding="utf-8")
                except Exception as exc:
                    self._send(500, json.dumps({"ok": False, "error": str(exc)}), "application/json"); return
                self._send(200, json.dumps({"ok": True, "benchmark": benchmark_public()}), "application/json")
            return
        if p == "/api/benchmark/cancel":
            with REG.lock:
                benchmark = benchmark_cancel_plan()
            self._send(200, json.dumps({"ok": True, "benchmark": benchmark}, ensure_ascii=False), "application/json")
            return
        self._send(404, "not found")

# Web assets
BASE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(BASE, "web", "index.html"), "r", encoding="utf-8") as f:
        INDEX_HTML = f.read()
    with open(os.path.join(BASE, "web", "app.js"), "r", encoding="utf-8") as f:
        APP_JS = f.read()
except Exception as e:
    INDEX_HTML = "<h1>install missing: %s</h1>" % e
    APP_JS = ""


def main():
    t1 = threading.Thread(target=discovery_loop, daemon=True)
    t2 = threading.Thread(target=collect_loop, daemon=True)
    t3 = threading.Thread(target=gpu_loop, daemon=True)
    t1.start(); t2.start(); t3.start()
    srv = ThreadingHTTPServer(("0.0.0.0", int(CFG["LISTEN_PORT"])), H)
    srv.daemon_threads = True
    print("vllm-observability: 0.0.0.0:%d" % CFG["LISTEN_PORT"], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

# ---------------------------------------------------------------------------
# Benchmark safety contracts (pure functions, isolated from live runtime).
# These are invoked by tests; no threads, no HTTP, no side effects.
# ---------------------------------------------------------------------------

def benchmark_levels(max_num_seqs):
    """Return a geometric progression 1, 2, 4, ... capped at max_num_seqs.

    If max_num_seqs is not itself a power of two, it is appended as the final level.
    """
    if max_num_seqs is None or max_num_seqs < 1:
        return []
    levels = []
    n = 1
    while n < max_num_seqs:
        levels.append(n)
        n *= 2
    levels.append(max_num_seqs)
    return levels


def benchmark_preflight(instance, alerts):
    """Require online + discovery endpoint, idle (running=0, waiting=0), no critical alert."""
    if not instance.get("state") == "online":
        return {"ok": False, "code": "instance_not_online"}
    if not instance.get("discovery_present"):
        return {"ok": False, "code": "instance_not_online"}
    if not instance.get("endpoint"):
        return {"ok": False, "code": "instance_not_online"}
    vals = instance.get("_vals", {}) or {}
    running = vals.get("running")
    waiting = vals.get("waiting")
    if not isinstance(running, (int, float)) or not isinstance(waiting, (int, float)):
        return {"ok": False, "code": "metrics_unavailable"}
    if running > 0.0 or waiting > 0.0:
        return {"ok": False, "code": "foreign_traffic_detected"}
    for a in alerts or []:
        if (a or {}).get("severity") == "critical":
            return {"ok": False, "code": "critical_alert_present"}
    return {"ok": True, "code": "ready"}


def benchmark_phase_guard(instance, expected_inflight, observed_completion_delta, own_completed):
    """Abort if running+waiting exceeds expected, or completion counter delta mismatches own completed."""
    vals = instance.get("_vals", {}) or {}
    running = float(vals.get("running", 0.0))
    waiting = float(vals.get("waiting", 0.0))
    try:
        expected = int(expected_inflight) if expected_inflight is not None else 0
    except (TypeError, ValueError):
        expected = 0
    if (running + waiting) > expected:
        return {"ok": False, "code": "foreign_traffic_detected"}
    if observed_completion_delta is not None and own_completed is not None:
        if int(observed_completion_delta) != int(own_completed):
            return {"ok": False, "code": "foreign_traffic_detected"}
    return {"ok": True, "code": "ok"}


def benchmark_request(endpoint, model, cancel_check=lambda: False):
    """One bounded, OpenAI-compatible baseline request; never changes vLLM."""
    if cancel_check():
        return {"ok": False, "error": "cancelled"}
    payload = {"model": model, "messages": [{"role": "user", "content": "Write a long neutral counting sequence. Do not stop early."}], "max_tokens": 2048, "temperature": 0, "ignore_eos": True, "chat_template_kwargs": {"enable_thinking": False}}
    started = time.monotonic()
    try:
        req = urllib.request.Request(endpoint + "/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=600) as response:
            body = json.loads(response.read().decode("utf-8"))
        usage = body.get("usage") or {}
        completion = usage.get("completion_tokens")
        if not isinstance(completion, int) or completion < 1:
            return {"ok": False, "error": "missing_completion_usage"}
        return {"ok": True, "completion_tokens": completion, "prompt_tokens": usage.get("prompt_tokens"), "e2e_s": round(time.monotonic() - started, 3)}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}



def system_public():
    """Static hardware identity collected from local host; no runtime load."""
    return {
        "mainboard": "Micro-Star MPG Z690 FORCE WIFI (MS-7D30)",
        "cpu": "Intel Core i5-14600K · 14C/28T",
        "ram": "Crucial DDR5 · 4×32 GB · 4000 MT/s",
        "nvme": "Kioxia KCD61LUL7T68 · 7 TB",
        "plx": "Broadcom PEX880xx PCIe Gen4 Switch [1000:c010]",
    }


FUSE_FIELDS = [
    ("gpc", "Rechenblöcke", 0x00820350, 0x008205c4, 8),
    ("fbp", "Speicherpartitionen", 0x00820364, 0x008205cc, 12),
    ("fbpa", "Speicherkanäle", 0x00820368, 0x008205d0, 24),
    ("l2", "L2-Cache-Blöcke", 0x008202c4, 0x008205e8, 24),
]

def classify_mask(disable, defective, width):
    disabled_bits = [i for i in range(width) if disable & (1 << i)]
    defective_bits = [i for i in range(width) if defective & (1 << i)]
    blocked_only = [i for i in disabled_bits if i not in defective_bits]
    return {"disabled": disabled_bits, "defective": defective_bits, "blocked_only": blocked_only}

def fuse_findings(rows):
    findings = []
    for row in rows:
        for field, label, *_rest in FUSE_FIELDS:
            item = row[field]
            if item["defective"]:
                findings.append(f"GPU {row['gpu']}: {label} {', '.join(map(str, item['defective']))} sind als defekt markiert.")
            if item["blocked_only"]:
                findings.append(f"GPU {row['gpu']}: {label} {', '.join(map(str, item['blocked_only']))} sind nur gesperrt, nicht als defekt markiert.")
        if all(not row[field]["defective"] for field, *_rest in FUSE_FIELDS):
            findings.append(f"GPU {row['gpu']}: keine der gelesenen Blöcke ist als defekt markiert.")
    findings.append("Auswirkung: Als defekt markierte Blöcke gelten als physikalisch ausgefallen und sind für einen Unlock nicht nutzbar.")
    findings.append("Nur gesperrte Blöcke sind grundsätzlich brauchbares Silizium. Ein aktueller Unlock holt davon einen Teil zurück: 74 SMs statt der bisher aktiven 70 und aktiviert zusätzlich ECC. Die restlichen nur gesperrten Blöcke und 80 GB bleiben weiterhin theoretisch.")
    findings.append("Praktisch bleibt jede Karte bei 5 Rechengruppen und 70 SMs. Die zwei nur gesperrten Speicherpartitionen wären die theoretische Grundlage für 80 statt 64 GB, doch ein funktionierender Reaktivierungsweg ist nicht belegt.")
    return findings

def read_fuse_scan():
    buses = ssh_run("nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader", 10)[0]
    rows = []
    for line in buses.splitlines():
        index, bus = [part.strip() for part in line.split(",", 1)]
        bus = bus.lower().removeprefix("00000000:").removeprefix("0000:")
        values = {}
        for _name, _label, disable_addr, defective_addr, width in FUSE_FIELDS:
            command = (
                "python3 -c \"import mmap,os,struct; fd=os.open('/sys/bus/pci/devices/0000:%s/resource0', os.O_RDONLY); mm=mmap.mmap(fd,0x900000,prot=mmap.PROT_READ); "
                "d,f=struct.unpack_from('<II', mm, %d)[0], struct.unpack_from('<I', mm, %d)[0]; mm.close(); os.close(fd); print(hex(d), hex(f))\""
                % (bus, disable_addr, defective_addr)
            )
            out, _err, rc = ssh_run("sudo -n " + command, 10)
            if rc != 0 or len(out.split()) != 2:
                raise RuntimeError("fuse read failed for GPU %s" % index)
            disable, defective = (int(part, 16) for part in out.split())
            values[_name] = classify_mask(disable, defective, width) | {"disable_hex": hex(disable), "defective_hex": hex(defective)}
        rows.append({"gpu": int(index), "bus": bus, **values})
    return {"state": "COMPLETE", "rows": rows, "findings": fuse_findings(rows), "error": None}



HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark-history.json")

def model_params(instance):
    keys=("model","endpoint","max_model_len","max_num_seqs","tensor_parallel_size","tp","gpu_memory_utilization")
    return {k:instance.get(k) for k in keys if instance.get(k) is not None}

def history_load():
    try: return json.loads(Path(HISTORY_PATH).read_text(encoding="utf-8"))
    except Exception: return []

def history_save(run):
    rows=history_load(); target=run.get("params") or {}
    rows.append({"at":run.get("finished_at"),"model":run.get("model"),"state":run.get("state"),"params":target,"results":[{"c":x.get("concurrency"),"state":x.get("state"),"tokens_s":x.get("tokens_s"),"duration_s":x.get("duration_s")} for x in run.get("phases",[])]})
    tmp=HISTORY_PATH+".tmp"; Path(tmp).write_text(json.dumps(rows[-100:],indent=2),encoding="utf-8"); os.replace(tmp,HISTORY_PATH)

def benchmark_public():
    """Return a detached benchmark snapshot while REG.lock is held by caller."""
    data=copy.deepcopy(REG.benchmark); data["history"]=history_load(); return data


def benchmark_start_plan(confirm):
    """Create a non-executing benchmark plan; caller must hold REG.lock."""
    current = REG.benchmark
    if not confirm:
        return {"ok": False, "code": "confirmation_required", "benchmark": benchmark_public()}
    if current.get("state") in ("PLANNED", "RUNNING"):
        return {"ok": False, "code": "benchmark_already_active", "benchmark": benchmark_public()}
    raw_instances = list(REG.instances.values())
    alerts = build_alerts(raw_instances, REG.gpu.get("gpus", []))
    targets = [inst for inst in raw_instances if inst.get("state") == "online" and inst.get("discovery_present")]
    if len(targets) != 1:
        return {"ok": False, "code": "benchmark_target_ambiguous", "benchmark": benchmark_public()}
    verdict = benchmark_preflight(targets[0], alerts)
    if not verdict["ok"]:
        return {"ok": False, "code": verdict["code"], "benchmark": benchmark_public()}
    cap = int(current.get("max_concurrency") or 16)
    now = time.time()
    REG.benchmark = {
        "state": "PLANNED", "run_id": uuid.uuid4().hex, "reason": None,
        "started_at": now, "finished_at": None, "max_concurrency": cap,
        "target_instance_id": targets[0].get("instance_id"),
        "target_container": targets[0].get("container", ""),
        "target_model": targets[0].get("model", ""),
        "phases": [{"concurrency": level, "state": "PENDING"} for level in benchmark_levels(cap)],
    }
    result = {"ok": True, "code": "planned", "benchmark": benchmark_public()}
    threading.Thread(target=benchmark_worker, args=(REG.benchmark["run_id"],), daemon=True).start()
    return result


def benchmark_worker(run_id):
    """Bounded C1..Cn runner; any observed ambiguity aborts the run."""
    with REG.lock:
        b = REG.benchmark
        if b.get("run_id") != run_id or b.get("state") != "PLANNED": return
        b["state"] = "RUNNING"; target = REG.instances.get(b["target_instance_id"])
        if not target: b.update(state="ABORTED", reason="target_lost", finished_at=time.time()); return
        endpoint, model = target["endpoint"], target["model"]
    for phase in REG.benchmark["phases"]:
        with REG.lock:
            b=REG.benchmark; target=REG.instances.get(b["target_instance_id"])
            if b.get("run_id") != run_id or b.get("state") != "RUNNING": return
            if not guard["ok"]: b.update(state="ABORTED",reason=guard["code"],finished_at=time.time()); return
            phase["state"]="RUNNING"
        c=phase["concurrency"]; started=time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=c) as pool:
            rows=list(pool.map(lambda _ : benchmark_request(endpoint,model,lambda: REG.benchmark.get("state")!="RUNNING"), range(c)))
        elapsed=max(time.monotonic()-started,0.001); good=[r for r in rows if r.get("ok")]
        with REG.lock:
            b=REG.benchmark; target=REG.instances.get(b["target_instance_id"])
            if len(good)!=c: b.update(state="ABORTED",reason=next((r.get("error") for r in rows if not r.get("ok")),"request_failed"),finished_at=time.time()); phase.update(state="FAILED",results=rows); return
            if not guard["ok"]: b.update(state="ABORTED",reason=guard["code"],finished_at=time.time()); phase.update(state="ABORTED",results=rows); return
            tokens=sum(r["completion_tokens"] for r in good)
            e2e=round(tokens/elapsed,2)
            if elapsed <= 0 or tokens < 1 or e2e <= 0:
                b.update(state="FAILED",reason="missing_throughput_measurement",finished_at=time.time())
                phase.update(state="FAILED",results=rows,error="missing_throughput_measurement"); return
            phase.update(state="PASS",results=rows,completion_tokens=tokens,duration_s=round(elapsed,3),e2e_s=round(elapsed,3),tokens_s=e2e,e2e_aggregate_tps=e2e)
    with REG.lock:
        if REG.benchmark.get("run_id")==run_id and REG.benchmark.get("state")=="RUNNING":
            target=REG.instances.get(REG.benchmark.get("target_instance_id"),{})
            REG.benchmark.update(state="MAX_TESTED",finished_at=time.time(),model=target.get("model"),params=model_params(target))
            history_save(dict(REG.benchmark))


def benchmark_cancel_plan():
    """Cancel only an unexecuted plan; a future worker needs its own cancel gate."""
    if REG.benchmark.get("state") in ("PLANNED", "RUNNING"):
        REG.benchmark["state"] = "ABORTED"
        REG.benchmark["reason"] = "cancelled_by_user"
        REG.benchmark["finished_at"] = time.time()
    return benchmark_public()


if __name__ == "__main__":
    main()
