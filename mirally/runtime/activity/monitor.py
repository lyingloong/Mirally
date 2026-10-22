"""Local Activity data-layer monitor.

The monitor is deliberately a small, loopback-only diagnostic service.  It
uses the same ``ObservationStore`` and ``ActivitySensor`` as the product, so
what it displays is the data that downstream consumers will actually see.
The default API response redacts OCR text; a token-protected explicit reveal
is required for content and image downloads.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from mirally.core.activity.models import CollectionLevel
from mirally.core.activity.knowledge import ActivityOrganizer, ContextQuery, KnowledgeStore
from mirally.core.activity.policy import ActivityPolicy
from mirally.core.activity.store import CursorExpiredError, ObservationStore

from .config import ActivityConfig
from .lock import ActivityProcessLock
from .service import ActivitySensor


_THREAD_JOIN_TIMEOUT_SECONDS = 5.0
_HTTP_THREAD_JOIN_TIMEOUT_SECONDS = 3.0


MONITOR_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirally Activity Monitor</title>
<style>
:root { color-scheme: light; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  --ink:#152536; --muted:#657487; --line:#dbe3ea; --paper:#f5f7fa; --blue:#087eac;
  --good:#18794e; --warn:#a05a00; --bad:#b42318; --surface:#fff; }
* { box-sizing:border-box; } body { margin:0; color:var(--ink); background:var(--paper); }
header { position:sticky; top:0; z-index:2; background:var(--surface); border-bottom:1px solid var(--line); padding:12px 22px; display:flex; gap:14px; align-items:center; }
h1 { font-size:18px; margin:0; letter-spacing:0; } header small { color:var(--muted); }
button, input, select { font:inherit; } button { border:1px solid #b9c8d4; background:var(--surface); color:var(--ink); padding:6px 10px; border-radius:5px; cursor:pointer; }
button:hover { border-color:var(--blue); color:var(--blue); } button.danger { color:var(--bad); } button:disabled { opacity:.5; cursor:default; }
.actions { margin-left:auto; display:flex; gap:7px; align-items:center; } .actions label { color:var(--muted); display:flex; gap:5px; align-items:center; }
main { width:min(100%,1500px); margin:0 auto; padding:clamp(12px,2vw,18px) clamp(12px,2.2vw,22px) 36px; } .banner { padding:9px 12px; border-left:3px solid var(--warn); background:#fff8eb; margin-bottom:14px; display:none; overflow-wrap:anywhere; }
.statusbar { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; margin-bottom:16px; }
.stat { background:var(--surface); border:1px solid var(--line); padding:9px 11px; min-height:64px; } .stat b { display:block; font-size:16px; margin-top:3px; overflow-wrap:anywhere; } .stat span { color:var(--muted); font-size:12px; }
.layout { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(0,1fr); gap:16px; align-items:start; } .layout > * { min-width:0; }
section { background:var(--surface); border:1px solid var(--line); min-width:0; } section > h2 { font-size:14px; margin:0; padding:10px 13px; border-bottom:1px solid var(--line); overflow-wrap:anywhere; }
.toolbar { padding:9px 12px; border-bottom:1px solid var(--line); display:flex; gap:7px; flex-wrap:wrap; align-items:center; } .toolbar input { min-width:0; width:min(180px,100%); border:1px solid var(--line); padding:6px 8px; }
.timeline { height:clamp(300px,62vh,590px); overflow:auto; } table { width:100%; border-collapse:collapse; table-layout:fixed; } th,td { padding:8px 10px; text-align:left; border-bottom:1px solid #edf1f4; vertical-align:top; overflow-wrap:anywhere; } th { color:var(--muted); font-size:12px; position:sticky; top:0; background:var(--surface); } .timeline th:nth-child(1) { width:16%; } .timeline th:nth-child(2) { width:25%; } .timeline th:nth-child(3) { width:24%; } .timeline th:nth-child(4) { width:17%; } .timeline th:nth-child(5) { width:18%; }
tr.event { cursor:pointer; } tr.event:hover { background:#f3f8fb; } .kind { font-weight:600; } .muted { color:var(--muted); } .ok { color:var(--good); } .warn { color:var(--warn); } .bad { color:var(--bad); }
.detail { height:clamp(200px,34vh,430px); overflow:auto; padding:12px; background:#fbfcfd; } pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:0; font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace; }
.diag { max-height:300px; overflow:auto; } .diag table { font-size:13px; } .diag td:first-child { color:var(--muted); width:48%; } .diag th { position:sticky; top:0; background:var(--surface); } .note { padding:10px 12px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); overflow-wrap:anywhere; }
.right { display:grid; gap:16px; } .pill { display:inline-block; border:1px solid var(--line); padding:2px 6px; border-radius:10px; font-size:11px; color:var(--muted); }
.status-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--muted); margin-right:5px; } .status-dot.ok { background:var(--good); } .status-dot.warn { background:var(--warn); } .status-dot.bad { background:var(--bad); }
.toolbar .spacer { flex:1; } .toolbar label { color:var(--muted); display:flex; gap:5px; align-items:center; white-space:nowrap; }
.source-status { font-weight:600; } .source-status.ok { color:var(--good); } .source-status.warn { color:var(--warn); } .source-status.bad { color:var(--bad); }
.frame-preview { display:none; width:100%; max-height:260px; object-fit:contain; background:#eef1f3; margin-bottom:10px; }
@media (max-width:980px) { .statusbar { grid-template-columns:repeat(3,minmax(0,1fr)); } .layout { grid-template-columns:1fr; } .right { grid-template-columns:repeat(2,minmax(0,1fr)); } .right > section:first-child { grid-column:1 / -1; } }
@media (max-width:620px) { header { padding:11px 12px; flex-wrap:wrap; } header small { flex:1 1 100%; order:3; } main { padding:12px; } .statusbar { grid-template-columns:repeat(2,minmax(0,1fr)); } .actions { margin-left:0; flex-wrap:wrap; } .right { grid-template-columns:1fr; } .right > section:first-child { grid-column:auto; } .timeline { height:52vh; } .toolbar .spacer { display:none; } }
@media (max-width:390px) { .statusbar { grid-template-columns:1fr; } .actions button { flex:1 1 auto; } .toolbar input { width:100%; } }
@media (max-width:620px) { .timeline table { min-width:600px; } }
</style>
</head>
<body>
<header><h1>Activity 数据层监控</h1><small id="endpoint">本机诊断 · loopback</small><div class="actions">
<button id="pause">暂停采集</button><button id="resume">恢复采集</button><button id="stop" class="danger">停止监控</button><button id="browse">开发者浏览</button>
<label><input id="reveal" type="checkbox"> 显示内容</label><button id="refresh">立即刷新</button>
</div></header>
<main><div id="error" class="banner"></div>
<div class="statusbar">
<div class="stat"><span>采集器</span><b id="collector">-</b></div><div class="stat"><span>策略 / 采集级别</span><b id="policy">-</b></div>
<div class="stat"><span>存储</span><b id="store">-</b></div><div class="stat"><span>最新事件序号</span><b id="events">0</b></div>
<div class="stat"><span>最近事件</span><b id="last">-</b></div><div class="stat"><span>内容层</span><b id="content">off</b></div><div class="stat"><span>组织索引</span><b id="knowledge">-</b></div>
</div>
<div class="layout"><section><h2>事件时间线 <span class="pill">最新优先</span></h2><div class="toolbar"><input id="kind" placeholder="事件类型"><input id="app" placeholder="应用标识"><button id="apply">应用筛选</button><span class="spacer"></span><label><input id="freeze" type="checkbox"> 冻结视图</label><span class="pill" id="poll">自动刷新</span></div>
<div class="timeline"><table><thead><tr><th>时间</th><th>事件</th><th>应用</th><th>级别</th><th>质量</th></tr></thead><tbody id="rows"></tbody></table></div><div class="note">事件来自 append-only store，列表按最新事件置顶；默认隐藏 OCR 文本和文件路径。</div></section>
<div class="right"><section><h2>选中事件</h2><div class="detail"><img id="frame" class="frame-preview" alt="采集帧"><pre id="detail">点击事件查看安全摘要。</pre></div></section>
<section><h2>采集源诊断</h2><div class="diag"><table><thead><tr><th>来源</th><th>状态</th><th>调用</th><th>结果 / 最近耗时</th></tr></thead><tbody id="sources"></tbody></table></div><div class="note" id="sourceNote"></div></section>
<section><h2>采集范围</h2><div class="diag"><table><tbody id="coverage"></tbody></table></div></section>
<section><h2>内容采集调度</h2><div class="diag"><table><tbody id="evidence"></tbody></table></div></section>
<section><h2>存储健康</h2><div class="diag"><table><tbody id="health"></tbody></table></div></section></div></div>
</main>
<script>
const token = __TOKEN__;
const viewOnly = window.__mirallyActivityViewOnly === true;
let cursor = 0, reveal = false, timer = null, stopped = false, frozen = false, frameUrl = null, selection = 0, ticking = false;
const $ = id => document.getElementById(id);
function esc(value) { return String(value ?? ""); }
function escHtml(value) { return esc(value).replace(/[&<>\"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch])); }
function api(path, options={}) { const headers = Object.assign({"Authorization":"Bearer "+token}, options.headers||{}); return fetch(path, Object.assign({}, options, {headers})).then(async r => { const data = await r.json().catch(()=>({})); if (!r.ok) throw new Error(data.error || r.statusText); return data; }); }
function when(ms) { return ms ? new Date(ms).toLocaleTimeString() : "-"; }
function setError(message) { const el=$("error"); el.textContent=message||""; el.style.display=message?"block":"none"; }
function cell(text, cls) { const td=document.createElement("td"); td.textContent=esc(text); if(cls) td.className=cls; return td; }
function addRows(events, reset) { const body=$("rows"); if(reset) body.textContent=""; const ordered=reset?events.slice().reverse():events; for(const event of ordered) { const tr=document.createElement("tr"); tr.className="event"; tr.append(cell(when(event.observed_at_utc_ms),"muted")); tr.append(cell(event.kind,"kind")); const app=cell(event.app_id||"-"); const context=event.payload||{}; for(const value of [context.window_title,context.browser_url]) { if(value) { const line=document.createElement("div"); line.className="muted"; line.textContent=value; line.title=value; app.append(line); } } tr.append(app); tr.append(cell(event.collection_level||"-")); tr.append(cell(event.quality||"-", event.quality==="complete"?"ok":event.quality==="unavailable"?"bad":"warn")); tr.onclick=()=>showEvent(event); if(reset) body.append(tr); else body.insertBefore(tr,body.firstChild); } while(body.children.length>250) body.removeChild(body.lastChild); }
async function showEvent(event) {
  const version=++selection; $("detail").textContent=JSON.stringify(event,null,2);
  const frame=$("frame"); frame.style.display="none"; frame.removeAttribute("src");
  if(frameUrl) { URL.revokeObjectURL(frameUrl); frameUrl=null; }
  if(!reveal || !event.asset_refs?.length) return;
  try {
    const response=await fetch("/api/assets/"+encodeURIComponent(event.asset_refs[0])+"?download=1&include_content=1",{headers:{"Authorization":"Bearer "+token}});
    if(!response.ok || version!==selection || !reveal) return;
    const blob=await response.blob(); if(version!==selection || !reveal) return;
    frameUrl=URL.createObjectURL(blob); frame.src=frameUrl; frame.style.display="block";
  } catch(error) { if(version===selection) setError(error.message); }
}
function rows(obj) { return Object.entries(obj||{}).map(([k,v])=>`<tr><td>${escHtml(k)}</td><td>${escHtml(typeof v === "object" ? JSON.stringify(v) : v)}</td></tr>`).join(""); }
function stateClass(status) { return status==="ok"||status==="running"||status==="healthy"||status==="available"?"ok":status==="degraded"||status==="partial"||status==="paused"?"warn":"bad"; }
function statusLabel(status) { return ({available:"可用",unavailable:"不可用",running:"运行中",degraded:"降级",paused:"已暂停",stopped:"已停止",healthy:"健康",error:"错误",idle:"未启动",on:"已开启",off:"未开启"})[status] || status; }
function sourceRow(label, source, calls, results, duration) { const available=!!source?.available; const status=available?"available":"unavailable"; const resultText=Object.entries(results||{}).map(([key,value])=>key+"="+value).join(" · ")||"-"; const reason=source?.reason==="helper_missing"?"缺少截图程序：在仓库根目录运行 .\\tools\\build-activity-wgc.ps1 后重启采集器":source?.reason; return `<tr><td>${escHtml(label)}</td><td class="source-status ${stateClass(status)}">${escHtml(statusLabel(status))}</td><td>${escHtml(calls ?? "-")}</td><td>${escHtml(resultText)}${duration==null?"":" · "+escHtml(duration)+" ms"}${reason?"<div class=\"warn\">"+escHtml(reason)+"</div>":""}</td></tr>`; }
function coverageRows(coverage) { const baseline=coverage?.baseline||[], optional=coverage?.optional||[], missing=coverage?.not_collected||[]; const rows=[]; baseline.forEach(item=>rows.push(`<tr><td>${escHtml(item.name)}</td><td class="source-status ${item.available?"ok":"warn"}">${item.available?"已接入":"未接入"}</td></tr>`)); optional.forEach(item=>rows.push(`<tr><td>${escHtml(item.name)}</td><td class="source-status ${item.enabled?"ok":"warn"}">${item.enabled?"已授权":"默认关闭"}</td></tr>`)); if(missing.length) rows.push(`<tr><td>明确不采集</td><td>${escHtml(missing.join("、"))}</td></tr>`); return rows.join(""); }
async function status() { const s=await api("/api/status"); const c=s.collector||{}; const h=s.health||{}; const metrics=s.metrics||{}; const k=s.knowledge||{}; const collectorClass=stateClass(c.status||"idle"); $("collector").innerHTML=`<span class="status-dot ${collectorClass}"></span>${viewOnly?"记录状态 · ":""}${escHtml(statusLabel(c.status||"idle"))}`; $("policy").textContent=(s.policy_version||"-")+" / "+(s.collection_level||"-"); $("store").innerHTML=`<span class="status-dot ${h.available?"ok":"bad"}"></span>${h.available?"健康":"错误"}`; $("events").textContent=h.newest_ingest_seq==null?"0":String(h.newest_ingest_seq); $("last").textContent=when(metrics.last_event_at_utc_ms); $("content").textContent=s.content_enabled?"已开启":"未开启"; const lag=k.organizer_lag||0; $("knowledge").innerHTML=k.knowledge?.available===false?"未准备":`<span class="status-dot ${lag?"warn":"ok"}"></span>${lag?"积压 "+lag:"已追平"}`; $("sources").innerHTML=sourceRow("Foreground",s.sources?.foreground,metrics.foreground_events,{events:metrics.events_by_kind?.foreground_changed||0},null)+sourceRow("Window context",s.sources?.window_context,metrics.events_by_kind?.window_context||0,{events:metrics.events_by_kind?.window_context||0},null)+sourceRow("Browser URL",s.sources?.browser_url,metrics.browser_url_calls,metrics.browser_url_by_status,metrics.browser_url_last_duration_ms)+sourceRow("Input",s.sources?.input,metrics.input_batches,{},null)+sourceRow("UIA",s.sources?.uia,metrics.uia_calls,metrics.uia_by_status,metrics.uia_last_duration_ms)+sourceRow("WGC",s.sources?.visual,metrics.capture_calls,metrics.capture_by_status,metrics.capture_last_duration_ms)+sourceRow("OCR",s.sources?.ocr,metrics.ocr_calls,metrics.ocr_by_status,metrics.ocr_last_duration_ms); $("sourceNote").textContent=viewOnly?"这里只查看已保存的采集诊断，更新时间："+(s.collector_diagnostics?.updated_at_utc_ms?new Date(s.collector_diagnostics.updated_at_utc_ms).toLocaleString():"未知")+"。记录状态不代表采集进程仍在运行。":"采集器指标为当前进程累计值；重启后从 0 开始，事件记录仍保留在 SQLite。"; $("coverage").innerHTML=coverageRows(s.coverage); $("health").innerHTML=rows({available:h.available,schema_version:h.schema_version,queue_size:h.queue_size,oldest_ingest_seq:h.oldest_ingest_seq,newest_ingest_seq:h.newest_ingest_seq,pending_assets:h.pending_assets,db_bytes:h.db_bytes,wal_bytes:h.wal_bytes,error:h.error||"-",knowledge:k}); }
async function events(reset=false) { if(reset) cursor=0; const params=new URLSearchParams({after:String(cursor),limit:"100"}); const kind=$("kind").value.trim(), app=$("app").value.trim(); if(kind) params.set("kind",kind); if(app) params.set("app",app); if(reveal) params.set("include_content","1"); const result=await api("/api/events?"+params); if(result.cursor_expired && !reset) { cursor=0; return events(true); } addRows(result.events||[],reset); cursor=result.cursor||cursor; if((result.events||[]).length) showEvent(result.events[result.events.length-1]); }
async function control(action) { if(viewOnly) throw new Error("此页面只查看已保存诊断，不控制采集器。"); await api("/api/control/"+action,{method:"POST"}); await status(); if(action==="stop") { stopped=true; $("poll").textContent="stopped"; } }
async function tick() { if(stopped || ticking) return; ticking=true; try { await status(); const m=await api("/api/metrics"); $("evidence").innerHTML=rows({queue:m.scheduler,triggers:m.last_triggers,wgc_session:m.capture_runtime||{},black_frame_skips:m.black_frame_skips||0,black_filter_errors:m.black_filter_errors||0,input_text:m.events_by_kind?.input_text||0,clipboard:m.clipboard_by_status||{},stale_jobs:m.stale_jobs||0,stale_results:m.stale_results||0,errors:m.evidence_errors||0,low_disk_skips:m.low_disk_skips||0}); if(!frozen) await events(false); $("poll").textContent=frozen?"视图已冻结":"自动刷新"; setError(""); } catch(e) { setError(e.message); } finally { ticking=false; } }
 ["pause","resume","stop"].forEach(id=>{ $(id).hidden=viewOnly; $(id).disabled=viewOnly; }); $("endpoint").textContent=location.host+(viewOnly?" · 只读诊断 · 每秒刷新":" · 每秒刷新"); $("browse").onclick=()=>window.open("/browse?token="+encodeURIComponent(token),"_blank"); $("pause").onclick=()=>control("pause").catch(e=>setError(e.message)); $("resume").onclick=()=>control("resume").catch(e=>setError(e.message)); $("stop").onclick=()=>control("stop").catch(e=>setError(e.message)); $("refresh").onclick=()=>tick(); $("apply").onclick=()=>events(true).catch(e=>setError(e.message)); $("reveal").onchange=e=>{ reveal=e.target.checked; events(true).catch(err=>setError(err.message)); }; $("freeze").onchange=e=>{ frozen=e.target.checked; $("poll").textContent=frozen?"视图已冻结":"自动刷新"; }; tick(); timer=setInterval(tick,1000);
</script>
</body></html>"""


def build_test_config(root: Path, *, content: bool = True, persist_images: bool = True) -> ActivityConfig:
    """Build an isolated, enabled monitor configuration with no credentials."""

    data_root = (Path(root) / ".artifacts" / "activity-monitor").resolve()
    policy = ActivityPolicy(
        enabled=True,
        default_level=CollectionLevel.CONTENT if content else CollectionLevel.STRUCTURE,
        policy_version=1,
        exclude_self=True,
        deny_content_on_unknown_desktop=True,
    )
    return ActivityConfig(
        enabled=True,
        database_path=data_root / "observations.sqlite3",
        asset_directory=data_root / "assets",
        poll_interval_seconds=1.0,
        policy=policy,
        observation_retention_days=2,
        image_retention_hours=6,
        image_quota_mib=128,
        min_free_space_mib=128,
        input_enabled=True,
        ui_structure_enabled=True,
        uia_timeout_ms=5000,
        uia_max_depth=16 if content else 6,
        visual_enabled=content,
        visual_persist_images=bool(persist_images and content),
        ocr_enabled=content,
        ocr_languages=("zh-CN", "en-US"),
        uia_content_enabled=content,
        input_text_enabled=content,
        clipboard_enabled=content,
        visual_max_per_minute=120,
        visual_timeout_ms=2500,
        visual_scope="monitors" if content else "foreground",
    )


def _safe_observation(value: Any, *, include_content: bool = False) -> dict[str, Any]:
    result = value.to_dict()
    if not include_content:
        ocr = dict(result.get("ocr_summary") or {})
        if "text" in ocr:
            text = str(ocr.pop("text") or "")
            ocr["text_available"] = bool(text)
            ocr["text_char_count"] = len(text)
        lines = ocr.get("lines")
        if isinstance(lines, list):
            ocr["line_count"] = len(lines)
            ocr.pop("lines", None)
        result["ocr_summary"] = ocr
        visual = dict(result.get("visual_summary") or {})
        visual.pop("path", None)
        result["visual_summary"] = visual
        def redact_content(mapping):
            clean = dict(mapping or {})
            for key in ("name", "value", "text", "inserted_text", "automation_id", "class_name"):
                if key in clean:
                    text = str(clean.pop(key) or "")
                    clean[key + "_char_count"] = len(text)
            return clean
        uia = dict(result.get("uia_summary") or {})
        uia["nodes"] = [redact_content(node) for node in uia.get("nodes", ())]
        uia["focused_control"] = redact_content(uia.get("focused_control"))
        result["uia_summary"] = uia
        if result.get("kind") in {"input_text", "clipboard_content"}:
            result["payload"] = redact_content(result.get("payload"))
    return result


class ActivityMonitorRuntime:
    """Own one sensor, one loopback HTTP server and its lifecycle."""

    def __init__(self, config: ActivityConfig, *, root: Optional[Path] = None) -> None:
        self.config = config
        self.root = Path(root or Path(__file__).resolve().parents[3])
        self.store = ObservationStore(config.database_path, asset_directory=config.asset_directory)
        self.sensor = ActivitySensor(config, store=self.store)
        knowledge_path = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
        self.knowledge_store = KnowledgeStore(knowledge_path)
        self.organizer = ActivityOrganizer(
            self.store,
            self.knowledge_store,
            idle_timeout_ms=int(config.knowledge_idle_timeout_seconds * 1000),
            allowed_lateness_ms=int(config.knowledge_allowed_lateness_seconds * 1000),
        )
        self.token = secrets.token_urlsafe(24)
        self.stop_event = threading.Event()
        self.sensor_thread: Optional[threading.Thread] = None
        self.organizer_thread: Optional[threading.Thread] = None
        self.http_thread: Optional[threading.Thread] = None
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.lock = ActivityProcessLock(fallback_path=config.database_path.with_suffix(".lock"))
        self.started_at = time.time()
        self.thread_error: Optional[str] = None
        self._closed = False
        self._close_lock = threading.RLock()

    @property
    def url(self) -> str:
        if self.httpd is None:
            return ""
        port = int(self.httpd.server_address[1])
        return f"http://127.0.0.1:{port}/?token={urllib.parse.quote(self.token)}"

    def start(self, *, port: int = 0) -> str:
        if not self.lock.acquire():
            raise RuntimeError("another Mirally activity sensor or monitor is already running")
        try:
            self.stop_event.clear()
            self.sensor_thread = threading.Thread(target=self._run_sensor, name="mirally-activity-monitor-sensor", daemon=True)
            self.sensor_thread.start()
            self.organizer_thread = threading.Thread(target=self._run_organizer, name="mirally-activity-monitor-organizer", daemon=True)
            self.organizer_thread.start()
            handler = self._handler_factory()
            self.httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), handler)
            self.httpd.daemon_threads = True
            self.http_thread = threading.Thread(
                target=self.httpd.serve_forever,
                kwargs={"poll_interval": 0.1},
                name="mirally-activity-monitor-http",
                daemon=True,
            )
            self.http_thread.start()
            return self.url
        except Exception:
            self.close()
            raise

    def _run_sensor(self) -> None:
        try:
            self.sensor.run(stop_event=self.stop_event)
        except Exception as error:
            self.thread_error = f"{type(error).__name__}: {error}"
            try:
                self.store.set_state("collector", {"status": "error", "error": self.thread_error, "at": int(time.time() * 1000)})
            except Exception:
                pass

    def _run_organizer(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.organizer.process_once(limit=self.config.knowledge_batch_size)
            except Exception as error:
                if self.stop_event.is_set():
                    break
                try:
                    self.store.set_state("organizer", {"status": "error", "error": f"{type(error).__name__}: {error}", "at": int(time.time() * 1000)})
                except Exception:
                    break
            self.stop_event.wait(self.config.knowledge_poll_interval_seconds)

    def request_stop(self) -> None:
        self.stop_event.set()
        self.sensor.stop()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self.request_stop()
            if self.httpd is not None:
                self.httpd.shutdown()
                self.httpd.server_close()
                self.httpd = None
            for attribute, timeout in (
                ("http_thread", _HTTP_THREAD_JOIN_TIMEOUT_SECONDS),
                ("sensor_thread", _THREAD_JOIN_TIMEOUT_SECONDS),
                ("organizer_thread", _THREAD_JOIN_TIMEOUT_SECONDS),
            ):
                worker = getattr(self, attribute)
                if worker is not None and worker.is_alive():
                    if worker is not threading.current_thread():
                        worker.join(timeout=timeout)
                    if worker.is_alive():
                        self.thread_error = f"{attribute} did not stop; activity stores and process lock remain open"
                        raise RuntimeError(self.thread_error)
                setattr(self, attribute, None)
            # A failed sensor close can mean that an evidence worker still owns
            # these stores. Retain them and the instance lock so shutdown can be
            # retried after the worker ends, without another sensor starting.
            self.sensor.close()
            try:
                # Sensor shutdown can flush the last UIA/input record after the
                # polling organizer has stopped. The sensor owns closing its raw
                # store, so reopen it strictly read-only for the final bounded drain.
                if self.config.database_path.is_file():
                    with ObservationStore(self.config.database_path, asset_directory=self.config.asset_directory, read_only=True) as source:
                        previous_source = self.organizer.source
                        self.organizer.source = source
                        try:
                            stats = self.organizer.catch_up(batch_size=self.config.knowledge_batch_size, max_batches=100)
                            if stats.errors or stats.lag:
                                raise RuntimeError(
                                    f"activity final projection incomplete (errors={stats.errors}, lag={stats.lag}); "
                                    "raw observations retained for retry"
                                )
                        finally:
                            self.organizer.source = previous_source
            finally:
                self.knowledge_store.close()
                self.store.close()
                self.lock.release()
                self._closed = True

    def serve(self, *, port: int = 0, open_browser: bool = True, duration: Optional[float] = None) -> int:
        url = self.start(port=port)
        print(f"Activity Monitor: {url}")
        print(f"Database: {self.config.database_path}")
        print("Press Ctrl+C to stop the monitor.")
        if open_browser:
            webbrowser.open(url)
        deadline = time.monotonic() + duration if duration else None
        try:
            while not self.stop_event.is_set() and (deadline is None or time.monotonic() < deadline):
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
        return 0

    def _handler_factory(self) -> Callable[..., BaseHTTPRequestHandler]:
        runtime = self

        class Handler(ActivityMonitorRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(runtime, *args, **kwargs)

        return Handler

    def status(self) -> dict[str, Any]:
        health = self.store.health()
        collector = self.store.get_state("collector", {})
        diagnostics = self.store.get_state("collector_diagnostics", {})
        if collector.get("status") == "running" and diagnostics.get("status") == "degraded":
            collector = {**collector, "status": "degraded"}
        pause = self.store.get_state("collector_pause", {})
        if pause.get("paused") and collector.get("status") not in {"stopped", "disabled"}:
            collector = {**collector, "status": "paused"}
        return {
            "monitor": {"uptime_seconds": round(max(0.0, time.time() - self.started_at), 2), "thread_error": self.thread_error},
            "enabled": self.config.enabled,
            "content_enabled": bool(self.sensor.policy.default_level == CollectionLevel.CONTENT and
                                    (self.config.visual_enabled or self.config.uia_content_enabled or
                                     self.config.input_text_enabled or self.config.clipboard_enabled)),
            "database": str(self.config.database_path),
            "asset_directory": str(self.config.asset_directory),
            "policy_version": self.sensor.policy.policy_version,
            "collection_level": self.sensor.policy.default_level,
            "collector": collector,
            "collector_diagnostics": diagnostics,
            "pause": pause,
            "health": asdict(health),
            "knowledge": self.organizer.health(),
            "metrics": self.sensor.metrics_snapshot(),
            "sources": {
                "foreground": {"available": bool(getattr(self.sensor.source, "available", False))},
                "window_context": {"available": bool(self.config.window_titles_enabled or self.config.browser_urls_enabled)},
                "browser_url": {"available": bool(getattr(self.sensor.browser_source, "available", False)) if self.sensor.browser_source else False},
                "input": {"available": bool(getattr(self.sensor.input_source, "available", False)) if self.sensor.input_source else False},
                "uia": {"available": bool(getattr(self.sensor.uia_source, "available", False)) if self.sensor.uia_source else False},
                "visual": self.sensor.metrics_snapshot().get("capture_runtime") or {"available": bool(getattr(self.sensor.visual_source, "available", False)) if self.sensor.visual_source else False},
                "ocr": {"available": bool(getattr(self.sensor.ocr_source, "available", False)) if self.sensor.ocr_source else False},
                "clipboard": {"available": bool(self.config.clipboard_enabled and self.sensor.clipboard_source.available)},
            },
            "coverage": {
                "baseline": [
                    {"name": "前台窗口元数据", "available": bool(getattr(self.sensor.source, "available", False))},
                    {"name": "窗口标题", "available": bool(self.config.window_titles_enabled and getattr(self.sensor.source, "available", False))},
                    {"name": "浏览器 URL", "available": bool(self.config.browser_urls_enabled and getattr(self.sensor.browser_source, "available", False))},
                    {"name": "输入元数据聚合", "available": bool(self.config.input_enabled and getattr(self.sensor.input_source, "available", False))},
                    {"name": "UIA 结构摘要", "available": bool(self.config.ui_structure_enabled and getattr(self.sensor.uia_source, "available", False))},
                ],
                "optional": [
                    {"name": "持续屏幕 / " + self.config.visual_scope, "enabled": bool(self.config.visual_enabled)},
                    {"name": "UIA 正文和属性", "enabled": bool(self.config.uia_content_enabled and self.config.policy.default_level == CollectionLevel.CONTENT)},
                    {"name": "已提交输入文本变化", "enabled": bool(self.config.input_text_enabled and self.config.policy.default_level == CollectionLevel.CONTENT)},
                    {"name": "剪贴板正文 / 复制粘贴", "enabled": bool(self.config.clipboard_enabled and self.config.policy.default_level == CollectionLevel.CONTENT)},
                    {"name": "Windows 原生 OCR", "enabled": bool(self.config.visual_enabled and self.config.ocr_enabled)},
                    {"name": "图片资产持久化", "enabled": bool(self.config.visual_persist_images)},
                ],
                "not_collected": [
                    "原始逐键字符流",
                    "音频与持续录音",
                ],
            },
        }

    def browser_observations(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        """Return the raw source newest-first for the developer browser."""
        limit = max(1, min(200, int((params.get("limit") or ["100"])[0])))
        before_value = (params.get("before") or [None])[0]
        before = None if before_value in (None, "") else int(before_value)
        start_value = (params.get("start") or [None])[0]
        end_value = (params.get("end") or [None])[0]
        rows = self.store.query(
            start_utc_ms=None if start_value in (None, "") else int(start_value),
            end_utc_ms=None if end_value in (None, "") else int(end_value),
            app_ids=[value for value in params.get("app", ()) if value],
            kinds=[value for value in params.get("kind", ()) if value],
            query=(params.get("q") or [None])[0] or None,
            before_ingest_seq=before,
            descending=True,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        cursor = selected[-1].ingest_seq if selected and has_more else None
        include_content = (params.get("include_content") or [""])[0].lower() in {"1", "true", "yes"}
        return {
            "data_status": "ok" if selected else "empty",
            "observations": [_safe_observation(row, include_content=include_content) for row in selected],
            "cursor": cursor,
            "has_more": has_more,
        }

    def events(self, params: Mapping[str, list[str]]) -> dict[str, Any]:
        after = max(0, int((params.get("after") or ["0"])[0]))
        limit = max(1, min(200, int((params.get("limit") or ["100"])[0])))
        include_content = (params.get("include_content") or [""])[0].lower() in {"1", "true", "yes"}
        kinds = [value for value in params.get("kind", []) if value]
        apps = [value for value in params.get("app", []) if value]
        try:
            if kinds or apps:
                rows = self.store.query(
                    after_ingest_seq=after, kinds=kinds or None, app_ids=apps or None, limit=limit + 1
                )
                has_more = len(rows) > limit
                selected = rows[:limit]
                cursor = selected[-1].ingest_seq if selected and selected[-1].ingest_seq is not None else after
            else:
                rows, cursor, has_more = self.store.read_since(after, limit=limit)
        except CursorExpiredError as error:
            return {"events": [], "cursor": after, "has_more": False, "cursor_expired": True, "error": str(error)}
        selected = rows[:limit] if (kinds or apps) else rows
        return {"events": [_safe_observation(row, include_content=include_content) for row in selected], "cursor": cursor, "has_more": has_more}


class ActivityMonitorRequestHandler(BaseHTTPRequestHandler):
    server_version = "MirallyActivityMonitor/1"

    def __init__(self, runtime: ActivityMonitorRuntime, *args: Any, **kwargs: Any) -> None:
        self.runtime = runtime
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorized(self, query: Mapping[str, list[str]]) -> bool:
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else (query.get("token") or [""])[0]
        return bool(supplied) and secrets.compare_digest(str(supplied), self.runtime.token)

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            return

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _path(self) -> tuple[str, Mapping[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._path()
        if path in {"/", "/index.html", "/browse", "/monitor"}:
            if not self._authorized(query):
                self._error(HTTPStatus.UNAUTHORIZED, "monitor token required")
                return
            browser_only = getattr(self.runtime, "browser_only", False)
            if path == "/browse" or browser_only and path != "/monitor":
                from .browser_page import BROWSER_HTML
                page = BROWSER_HTML
            else:
                page = MONITOR_HTML
                if browser_only:
                    page = page.replace("<script>",
                        "<script>window.__mirallyActivityViewOnly=true;</script><script>", 1)
            body = page.replace("__TOKEN__", json.dumps(self.runtime.token)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not path.startswith("/api/") or not self._authorized(query):
            self._error(HTTPStatus.UNAUTHORIZED, "monitor token required")
            return
        try:
            if (path.startswith("/api/knowledge/") and getattr(self.runtime, "browser_only", False)
                    and self.runtime.organizer is None):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE,
                    "活动知识索引尚未准备；原始记录和诊断仍可查看。打开此页不会创建或重建索引。")
                return
            if path in {"/api/health", "/api/status"}:
                self._json(self.runtime.status())
            elif path == "/api/metrics":
                self._json(self.runtime.sensor.metrics_snapshot())
            elif path == "/api/events":
                self._json(self.runtime.events(query))
            elif path == "/api/browser/observations":
                if hasattr(self.runtime, "browser_observations"):
                    self._json(self.runtime.browser_observations(query))
                else:
                    self._json(self.runtime.events(query))
            elif path == "/api/knowledge/context":
                self._knowledge_context(query)
            elif path == "/api/knowledge/events":
                self._knowledge_events(query)
            elif path == "/api/knowledge/timeline":
                self._knowledge_timeline(query)
            elif path == "/api/knowledge/items":
                self._knowledge_items(query)
            elif path == "/api/knowledge/episodes":
                self._knowledge_episodes(query)
            elif path == "/api/knowledge/summary":
                self._knowledge_summary(query)
            elif path == "/api/knowledge/health":
                self._json(self.runtime.organizer.health())
            elif path == "/api/knowledge/memory/candidates":
                status = (query.get("status") or [None])[0]
                limit = max(1, min(500, int((query.get("limit") or ["100"])[0])))
                values = self.runtime.knowledge_store.list_memory_candidates(status=status, limit=limit)
                self._json({"candidates": [value.to_dict() for value in values]})
            elif path == "/api/knowledge/memory/facts":
                status = (query.get("status") or [None])[0]
                limit = max(1, min(500, int((query.get("limit") or ["100"])[0])))
                values = self.runtime.knowledge_store.list_memory_facts(status=status, limit=limit)
                self._json({"facts": [value.to_dict() for value in values]})
            elif path.startswith("/api/knowledge/episodes/"):
                episode_id = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                episode = self.runtime.organizer.get_episode(episode_id)
                if episode is None:
                    self._error(HTTPStatus.NOT_FOUND, "episode not found")
                else:
                    self._json(episode.to_dict())
            elif path == "/api/knowledge/evidence":
                ids = [value for value in query.get("id", ()) if value]
                if not ids:
                    if hasattr(self.runtime, "evidence_query"):
                        include_assets = (query.get("include_assets") or [""])[0].lower() in {"1", "true", "yes"}
                        self._json(self.runtime.evidence_query(query, include_assets=include_assets))
                    else:
                        context = self._context_query(query, mode="evidence", purpose="diagnostics", include_uia=True)
                        self._json({"evidence": [value.to_dict() for value in self.runtime.organizer.context(context).evidence]})
                else:
                    include_assets = (query.get("include_assets") or [""])[0].lower() in {"1", "true", "yes"}
                    self._json({"evidence": [value.to_dict() for value in self.runtime.organizer.get_evidence(ids, include_assets=include_assets)]})
            elif path.startswith("/api/knowledge/items/"):
                item_id = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                item = self.runtime.knowledge_store.get_item(item_id)
                if item is None:
                    self._error(HTTPStatus.NOT_FOUND, "item not found")
                else:
                    self._json(item.to_dict())
            elif path.startswith("/api/events/"):
                observation_id = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                value = self.runtime.store.get(observation_id)
                if value is None:
                    self._error(HTTPStatus.NOT_FOUND, "event not found")
                else:
                    include = (query.get("include_content") or [""])[0].lower() in {"1", "true", "yes"}
                    self._json(_safe_observation(value, include_content=include))
            elif path.startswith("/api/assets/"):
                self._asset(path.rsplit("/", 1)[-1], query)
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except (ValueError, TypeError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except Exception as error:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")

    @staticmethod
    def _query_one(query: Mapping[str, list[str]], name: str) -> Optional[str]:
        values = query.get(name) or []
        return values[0] if values else None

    @classmethod
    def _context_query(cls, query: Mapping[str, list[str]], *, mode: Optional[str] = None,
                       purpose: Optional[str] = None, include_uia: Optional[bool] = None) -> ContextQuery:
        def integer(name: str) -> Optional[int]:
            value = cls._query_one(query, name)
            return None if value in (None, "") else int(value)

        selected_mode = mode or cls._query_one(query, "mode") or "outline"
        selected_purpose = purpose or cls._query_one(query, "purpose") or "agent_context"
        uia = include_uia if include_uia is not None else cls._query_one(query, "include_uia") in {"1", "true", "yes"}
        return ContextQuery(
            query=cls._query_one(query, "q"), start_utc_ms=integer("start"), end_utc_ms=integer("end"),
            apps=tuple(query.get("app", ())), windows=tuple(query.get("window", ())),
            kinds=tuple(query.get("kind", ())), mode=selected_mode, purpose=selected_purpose,
            max_items=int(cls._query_one(query, "limit") or 30),
            max_episodes=int(cls._query_one(query, "episodes") or 10),
            token_budget=int(cls._query_one(query, "tokens") or 4000),
            episode_id=cls._query_one(query, "episode"),
            include_assets=cls._query_one(query, "include_assets") in {"1", "true", "yes"},
            include_uia=uia, include_ocr=cls._query_one(query, "include_ocr") not in {"0", "false", "no"},
            after_item_id=cls._query_one(query, "after_item"),
        )

    def _knowledge_context(self, query: Mapping[str, list[str]]) -> None:
        context = self.runtime.organizer.context(self._context_query(query))
        self._json(context.to_dict())

    def _knowledge_events(self, query: Mapping[str, list[str]]) -> None:
        context = self._context_query(query, mode="evidence", purpose="diagnostics", include_uia=True)
        self._json(self.runtime.organizer.activity_events(context))

    def _knowledge_timeline(self, query: Mapping[str, list[str]]) -> None:
        context = self._context_query(query, mode="structured", purpose="diagnostics", include_uia=True)
        self._json(self.runtime.organizer.activity_timeline(context))

    def _knowledge_items(self, query: Mapping[str, list[str]]) -> None:
        context = self._context_query(query, mode="structured", purpose="diagnostics", include_uia=True)
        # Fetch one extra item so callers can distinguish a full page from a
        # query that happened to contain exactly ``limit`` records.
        limit = max(1, min(500, int(context.max_items)))
        values = self.runtime.knowledge_store.query_items(
            query=context.query, start_utc_ms=context.start_utc_ms, end_utc_ms=context.end_utc_ms,
            apps=context.apps, windows=context.windows, kinds=context.kinds, episode_id=context.episode_id,
            after_item_id=context.after_item_id, limit=min(500, limit + 1),
        )
        truncated = len(values) > limit
        selected = values[:limit]
        self._json({
            "data_status": "ok" if selected else "empty",
            "items": [value.to_dict() for value in selected],
            "truncated": truncated,
            "next_cursor": selected[-1].item_id if truncated and selected else None,
        })

    def _knowledge_episodes(self, query: Mapping[str, list[str]]) -> None:
        start = self._query_one(query, "start")
        end = self._query_one(query, "end")
        limit = max(1, min(100, int(self._query_one(query, "limit") or 50)))
        values = self.runtime.knowledge_store.query_episodes(
            start_utc_ms=None if start in (None, "") else int(start),
            end_utc_ms=None if end in (None, "") else int(end),
            query=self._query_one(query, "q"),
            apps=tuple(query.get("app", ())), windows=tuple(query.get("window", ())),
            kinds=tuple(query.get("kind", ())),
            after_episode_id=self._query_one(query, "after_episode"),
            limit=limit + 1,
        )
        truncated = len(values) > limit
        selected = values[:limit]
        self._json({
            "data_status": "ok" if selected else "empty", "episodes": [value.to_dict() for value in selected],
            "truncated": truncated, "next_cursor": selected[-1].episode_id if truncated else None,
        })

    def _knowledge_summary(self, query: Mapping[str, list[str]]) -> None:
        context = self._context_query(query, mode="outline", purpose="diagnostics")
        self._json(self.runtime.organizer.activity_summary(context))

    def _asset(self, asset_id: str, query: Mapping[str, list[str]]) -> None:
        asset_id = urllib.parse.unquote(asset_id)
        include = (query.get("include_content") or [""])[0].lower() in {"1", "true", "yes"}
        record = self.runtime.store.resolve_asset(asset_id, scope="activity-content")
        if (query.get("download") or [""])[0] != "1":
            self._json({key: value for key, value in record.items() if key != "path"})
            return
        if not include:
            self._error(HTTPStatus.FORBIDDEN, "explicit content reveal is required")
            return
        path_value = record.get("path")
        if record.get("status") != "available" or not path_value:
            self._error(HTTPStatus.NOT_FOUND, "asset is not available")
            return
        path = Path(str(path_value))
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "asset file is missing")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path, query = self._path()
        if not path.startswith("/api/control/") or not self._authorized(query):
            self._error(HTTPStatus.UNAUTHORIZED, "monitor token required")
            return
        if getattr(self.runtime, "browser_only", False):
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "read-only activity browser")
            return
        action = path.rsplit("/", 1)[-1]
        if action == "pause":
            self.runtime.sensor.pause("monitor")
        elif action == "resume":
            self.runtime.sensor.resume()
        elif action == "stop":
            self.runtime.request_stop()
        else:
            self._error(HTTPStatus.NOT_FOUND, "unknown control")
            return
        self._json(self.runtime.status())


__all__ = ["ActivityMonitorRuntime", "build_test_config"]
