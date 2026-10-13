# -*- coding: utf-8 -*-
"""AI 前沿雷达 · 本地图形界面（浏览器 UI）。

## 为什么用本地网页而不是 tkinter

两个原因：
  1. **能做好看**，而且能用截图验证。tkinter 的观感很难做现代，
     而且我无法截图确认它长什么样 —— 那是"无法验证的交付"。
  2. **无黑框**。tkinter 由 pythonw 启动也是无控制台的，但网页方案
     可以顺便让你在浏览器里直接看报告，缩放、搜索、打印都免费获得。

## 它是怎么跑起来的

    pywebview? 不用。
    app_server.py 起一个只监听 127.0.0.1 的 HTTP 服务，
    然后用默认浏览器打开它。关掉窗口时进程自己退出。

## 安全说明

只绑定 127.0.0.1（不是 0.0.0.0），所以同网段的其他机器访问不到。
另外做了随机 token 校验和路径越界检查 —— 即使本机有别的程序
在扫端口，也不能通过它读到你项目目录以外的文件。

用法：
    python src/app_server.py              # 起服务并自动开浏览器
    python src/app_server.py --no-browser # 只起服务
    python src/app_server.py --port 8321
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = ROOT / "out"
TOKEN = secrets.token_urlsafe(16)

mimetypes.add_type("text/markdown; charset=utf-8", ".md")

# ------------------------------------------------------------------ 任务状态

JOB = {
    "kind": None,        # None | "collect" | "report"
    "state": "idle",     # idle | running | done | error | cancelled
    "progress": 0,
    "logs": [],
    "result": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "cancel": False,
}
_LOCK = threading.Lock()


def _log(msg):
    with _LOCK:
        ts = datetime.now().strftime("%H:%M:%S")
        JOB["logs"].append(f"{ts}  {msg}")
        if len(JOB["logs"]) > 400:
            JOB["logs"] = JOB["logs"][-300:]


# 日志文本 → 进度百分比。不是精确进度，是"阶段感"：
# 网络阶段本来就没有可预期的完成时间，给个大致阶段比假装精确好。
_PROGRESS_HINTS = [
    ("窗口：", 4), ("信息源：", 6), ("开始并发抓取", 8),
    ("抓取完成", 62), ("解析出", 68), ("时间过滤后", 74),
    ("事件去重后", 80), ("打分排序后", 88), ("已写入候选数据", 94),
    ("已生成候选清单", 98), ("用时", 99),
]


def _progress_from(msg):
    for key, pct in _PROGRESS_HINTS:
        if key in msg:
            return pct
    return None


def _run_collect(window=None):
    from src import pipeline_run
    with _LOCK:
        JOB.update(kind="collect", state="running", progress=3, logs=[],
                   result=None, error=None, cancel=False,
                   started_at=time.time(), finished_at=None)

    def log(m):
        _log(m)
        p = _progress_from(m)
        if p is not None:
            with _LOCK:
                JOB["progress"] = max(JOB["progress"], p)

    try:
        payload = pipeline_run.run_collection(window=window, log=log)
        with _LOCK:
            JOB["progress"] = 100
            JOB["state"] = "done"
            JOB["result"] = {
                "date": payload["date"],
                "totals": payload["totals"],
                "elapsed": payload.get("_elapsed"),
                "paths": payload.get("_paths"),
                "bad_sources": [v["name"] for v in
                                payload["source_diagnostics"].values()
                                if not v["ok"]],
            }
            JOB["finished_at"] = time.time()
    except Exception as e:
        _log(f"出错：{type(e).__name__}: {e}")
        _log(traceback.format_exc(limit=3))
        with _LOCK:
            JOB["state"] = "error"
            JOB["error"] = f"{type(e).__name__}: {e}"
            JOB["finished_at"] = time.time()


def _run_report(opts):
    from src import local_report
    # 把收到的筛选条件原样存下来。出过一次"界面显示 20 条、接口说 0 条"的
    # 怪事，光看代码两种猜测都说得通 —— 所以直接把服务端实际收到的参数
    # 暴露出来，让下一次排查有证据可看，而不是靠推理。
    received = {
        "topics": opts.get("topics"),
        "min_score": opts.get("min_score"),
        "limit": opts.get("limit"),
        "group": opts.get("group"),
    }
    with _LOCK:
        JOB.update(kind="report", state="running", progress=10, logs=[],
                   result=None, error=None, cancel=False,
                   started_at=time.time(), finished_at=None)
        JOB["received"] = received
    _log(f"收到筛选条件：主题={received['topics'] or '全部'}，"
         f"分数下限={received['min_score']}，条数上限={received['limit']}")

    def log(m):
        _log(m)

    try:
        res = local_report.generate(
            topics=opts.get("topics") or None,
            min_score=float(opts.get("min_score") or 0),
            limit=int(opts.get("limit") or 30),
            sources=opts.get("sources") or None,
            group=opts.get("group") or "topic",
            note=opts.get("note") or "",
            only_high_heat=bool(opts.get("only_high_heat")),
            make_pdf=bool(opts.get("make_pdf")),
            log=log,
        )
        with _LOCK:
            JOB["progress"] = 100
            JOB["state"] = "done"
            JOB["result"] = res
            JOB["finished_at"] = time.time()
    except Exception as e:
        _log(f"出错：{type(e).__name__}: {e}")
        with _LOCK:
            JOB["state"] = "error"
            JOB["error"] = f"{type(e).__name__}: {e}"
            JOB["finished_at"] = time.time()


# ------------------------------------------------------------------ 数据整理

def latest_payload():
    p = OUT / "candidates" / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def data_summary():
    """给首页用的概览：今天有什么、多旧、有哪些维度可选。"""
    d = latest_payload()
    if not d:
        return {"has_data": False}
    cs = d.get("candidates", [])
    age_h = None
    try:
        age_h = round((datetime.now(timezone.utc)
                       - datetime.fromisoformat(d["generated_at"])
                       ).total_seconds() / 3600, 1)
    except Exception:
        pass
    from collections import Counter
    topics = Counter()
    sources = Counter()
    for c in cs:
        for t in (c.get("topics") or []):
            topics[t] += 1
        for s in (c.get("sources") or [])[:1]:
            sources[s.get("source_name", "?")] += 1
    diag = d.get("source_diagnostics") or {}

    # 三个数字必须分开，否则"多少个源能用"是句废话：
    #   抓到的     = 请求成功且解析出内容（源没坏）
    #   贡献候选的 = 实际有内容进入最终清单
    #   被窗口砍掉 = 抓到了但全在时间窗口外（源正常，只是今天没有新内容）
    n_total = len(diag)
    n_fetched = sum(1 for v in diag.values() if v.get("ok"))
    n_survived = sum(1 for v in diag.values() if v.get("survived_filter"))
    n_bad = n_total - n_fetched

    return {
        "has_data": True,
        "date": d.get("date"),
        "generated_at": d.get("generated_at", "")[:19],
        "age_hours": age_h,
        "stale": bool(age_h is not None and age_h > 26),
        "window_hours": d.get("window_hours"),
        "totals": d.get("totals"),
        "http": d.get("http_stats"),
        "count": len(cs),
        "topics": topics.most_common(),
        "sources": sources.most_common(12),
        "source_counts": {"total": n_total, "fetched": n_fetched,
                          "survived": n_survived, "bad": n_bad},
        # 三种"没有候选"必须分开显示，处置方式完全不同：
        #   没产出     -> 源坏了或网络不通，要去查
        #   全被窗口砍 -> 源正常，只是今天没有窗口内的新内容
        #   正常       -> 有贡献
        "bad_sources": [{"name": v.get("name"),
                         "why": "；".join(v.get("warnings", [])) or "未知"}
                        for v in diag.values() if not v.get("ok")],
        "filtered_sources": [{"name": v.get("name"),
                              "raw": v.get("raw_items", 0)}
                             for v in diag.values() if v.get("all_filtered")],
        "ok_source_count": n_fetched,
        "scores": {
            "max": max((c.get("score", 0) for c in cs), default=0),
            "min": min((c.get("score", 0) for c in cs), default=0),
        },
        "top": [{"title": c.get("title"), "score": round(c.get("score", 0), 1),
                 "source": c.get("source_name")} for c in cs[:8]],
        "files": {
            "xlsx": (OUT / "candidates" / "latest.xlsx").exists(),
            "digest": (OUT / "digests" / "latest.md").exists(),
        },
    }


def history(limit_days=7):
    """过去 N 天的报告与清单。这是用户要的『记忆』。"""
    rep_dir = OUT / "reports"
    dig_dir = OUT / "digests"
    cand_dir = OUT / "candidates"
    days = {}

    def day_key(name):
        # 从 2026-10-12[-摘要-123456] 里取日期部分
        return name[:10] if len(name) >= 10 and name[4] == "-" else None

    for p in sorted(rep_dir.glob("*.md")) if rep_dir.exists() else []:
        k = day_key(p.name)
        if not k:
            continue
        d = days.setdefault(k, {"date": k, "reports": [], "digests": [],
                                "xlsx": [], "pdf": [], "docx": []})
        kind = "摘要" if "摘要" in p.name else "日报"
        d["reports"].append({"name": p.name, "kind": kind,
                             "size": p.stat().st_size,
                             "mtime": p.stat().st_mtime})
    for p in sorted(rep_dir.glob("*.pdf")) if rep_dir.exists() else []:
        k = day_key(p.name)
        if k and k in days:
            days[k]["pdf"].append(p.name)
    for p in sorted(rep_dir.glob("*.docx")) if rep_dir.exists() else []:
        k = day_key(p.name)
        if k and k in days:
            days[k]["docx"].append(p.name)
    for p in sorted(dig_dir.glob("*.md")) if dig_dir.exists() else []:
        k = day_key(p.name)
        if k and p.name.startswith("latest"):
            continue
        if k:
            days.setdefault(k, {"date": k, "reports": [], "digests": [],
                                "xlsx": [], "pdf": [], "docx": []})
            days[k]["digests"].append(p.name)
    for p in sorted(cand_dir.glob("*.xlsx")) if cand_dir.exists() else []:
        k = day_key(p.name)
        if k and not p.name.startswith("latest"):
            if k in days:
                days[k]["xlsx"].append(p.name)

    lst = sorted(days.values(), key=lambda x: x["date"], reverse=True)
    for d in lst:
        d["reports"].sort(key=lambda r: -r["mtime"])
    return lst[:limit_days]


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "AIFrontier/1.0"

    def log_message(self, fmt, *args):
        pass    # 静音：控制台输出没有意义，日志都在 JOB["logs"] 里

    # ---------- 工具
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, code=200, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, q):
        return q.get("token", [""])[0] == TOKEN

    def _safe_path(self, rel):
        """把请求路径限制在项目目录内。

        这一步不能省：如果直接拼接用户给的路径，
        ../../../ 就能读到项目外的任意文件。
        """
        rel = urllib.parse.unquote(rel)
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            return None
        return target if target.is_file() else None

    # ---------- GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path

        if path in ("/", "/index.html"):
            return self._html(PAGE.replace("__TOKEN__", TOKEN))

        if not self._authed(q):
            return self._json({"error": "token 无效"}, 403)

        if path == "/api/summary":
            return self._json(data_summary())
        if path == "/api/status":
            with _LOCK:
                snap = dict(JOB)
            return self._json(snap)
        if path == "/api/history":
            return self._json({"days": history()})
        if path == "/api/file":
            rel = q.get("path", [""])[0]
            t = self._safe_path(rel)
            if not t:
                return self._json({"error": "路径不允许", "path": rel}, 403)
            ctype = mimetypes.guess_type(t.name)[0] or "application/octet-stream"
            if t.suffix.lower() == ".md":
                return self._text(t.read_text(encoding="utf-8", errors="replace"),
                                  200, "text/plain; charset=utf-8")
            data = t.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/quit":
            self._json({"ok": True})
            threading.Timer(0.4, lambda: os._exit(0)).start()
            return
        return self._json({"error": "未知接口"}, 404)

    # ---------- POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._authed(q):
            return self._json({"error": "token 无效"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}

        with _LOCK:
            busy = JOB["state"] == "running"

        if u.path == "/api/collect":
            if busy:
                return self._json({"error": "已有任务在运行"}, 409)
            threading.Thread(target=_run_collect, daemon=True,
                             args=(body.get("window"),)).start()
            return self._json({"ok": True})
        if u.path == "/api/report":
            if busy:
                return self._json({"error": "已有任务在运行"}, 409)
            threading.Thread(target=_run_report, daemon=True,
                             args=(body,)).start()
            return self._json({"ok": True})
        if u.path == "/api/cancel":
            with _LOCK:
                JOB["cancel"] = True
                JOB["state"] = "cancelled"
                JOB["finished_at"] = time.time()
            return self._json({"ok": True})
        if u.path == "/api/reset":
            with _LOCK:
                JOB.update(kind=None, state="idle", progress=0, logs=[],
                           result=None, error=None, cancel=False)
            return self._json({"ok": True})
        if u.path == "/api/open":
            rel = (body or {}).get("path", "")
            t = self._safe_path(rel)
            if not t:
                return self._json({"error": "路径不允许"}, 403)
            try:
                os.startfile(str(t))          # Windows 专用，本项目只跑 Windows
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        return self._json({"error": "未知接口"}, 404)


# ------------------------------------------------------------------ 前端页面

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 前沿雷达</title>
<style>
  :root{
    --bg:#f5f6f8; --panel:#fff; --line:#e3e6ea; --txt:#1c2430;
    --muted:#6b7684; --accent:#2563eb; --accent-d:#1d4ed8;
    --ok:#059669; --warn:#d97706; --err:#dc2626; --soft:#eef2f7;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--txt);font-size:14px;line-height:1.55}
  .wrap{max-width:1080px;margin:0 auto;padding:22px 20px 60px}
  header{display:flex;align-items:center;gap:14px;margin-bottom:20px}
  .logo{width:42px;height:42px;border-radius:11px;
        background:linear-gradient(135deg,#2563eb,#7c3aed);
        display:flex;align-items:center;justify-content:center;
        color:#fff;font-size:20px;font-weight:700;flex:0 0 auto}
  h1{font-size:19px;margin:0;font-weight:650}
  .sub{color:var(--muted);font-size:12.5px;margin-top:2px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:13px;
        padding:18px 20px;margin-bottom:16px}
  .card h2{font-size:15px;margin:0 0 4px;font-weight:650}
  .card .hint{color:var(--muted);font-size:12.5px;margin-bottom:14px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  button{font-family:inherit;font-size:14px;border-radius:9px;cursor:pointer;
         border:1px solid transparent;padding:11px 18px;transition:.15s;
         font-weight:550;white-space:nowrap}
  button:disabled{opacity:.45;cursor:not-allowed}
  .btn-primary{background:var(--accent);color:#fff}
  .btn-primary:hover:not(:disabled){background:var(--accent-d)}
  .btn-ghost{background:#fff;border-color:var(--line);color:var(--txt)}
  .btn-ghost:hover:not(:disabled){background:var(--soft)}
  .btn-lg{padding:14px 26px;font-size:15px}
  .badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;
         background:var(--soft);color:var(--muted)}
  .badge.ok{background:#e7f6ef;color:var(--ok)}
  .badge.warn{background:#fdf3e3;color:var(--warn)}
  .badge.err{background:#fdeaea;color:var(--err)}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
         gap:10px;margin:14px 0 4px}
  .stat{background:var(--soft);border-radius:10px;padding:11px 13px}
  .stat .k{color:var(--muted);font-size:12px}
  .stat .v{font-size:19px;font-weight:650;margin-top:2px}
  .progress-wrap{margin-top:14px}
  .bar{height:9px;background:#e6eaf0;border-radius:6px;overflow:hidden}
  .bar > i{display:block;height:100%;width:0;border-radius:6px;
           background:linear-gradient(90deg,#2563eb,#7c3aed);
           transition:width .35s ease}
  .bar.indet > i{width:35%;animation:slide 1.15s ease-in-out infinite}
  @keyframes slide{0%{margin-left:-35%}100%{margin-left:100%}}
  .log{margin-top:12px;background:#0f172a;color:#c7d2e0;border-radius:10px;
       padding:12px 14px;font-family:"Cascadia Mono",Consolas,monospace;
       font-size:12px;height:190px;overflow-y:auto;white-space:pre-wrap;
       line-height:1.65}
  .log .l-err{color:#fca5a5}
  .log .l-ok{color:#86efac}
  .pct{font-variant-numeric:tabular-nums;font-weight:600}
  .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
          gap:14px;margin:6px 0 4px}
  label.f{display:block;font-size:12.5px;color:var(--muted);margin-bottom:5px}
  input[type=number],input[type=text],select,textarea{
    width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:8px;
    font-family:inherit;font-size:13.5px;background:#fff;color:var(--txt)}
  input:focus,select:focus,textarea:focus{outline:2px solid #bfdbfe;
    outline-offset:-1px;border-color:var(--accent)}
  .chips{display:flex;flex-wrap:wrap;gap:7px}
  .chip{border:1px solid var(--line);background:#fff;border-radius:20px;
        padding:6px 13px;font-size:12.5px;cursor:pointer;user-select:none;
        transition:.12s}
  .chip:hover{background:var(--soft)}
  .chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:600;font-size:12.5px}
  tr:last-child td{border-bottom:none}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .hidden{display:none!important}
  .notice{background:#fff8e6;border:1px solid #f5e0a8;border-radius:10px;
          padding:12px 14px;font-size:13px;color:#7a5c12;margin-top:12px}
  .notice b{color:#5f4708}
  .result{background:#eefbf4;border:1px solid #bfe8d4;border-radius:10px;
          padding:13px 15px;margin-top:12px;font-size:13.5px}
  .result.err{background:#fdeaea;border-color:#f5c2c2;color:#8a1f1f}
  pre.md{background:#fbfbfc;border:1px solid var(--line);border-radius:10px;
         padding:16px;max-height:430px;overflow:auto;font-size:13px;
         white-space:pre-wrap;font-family:"Cascadia Mono",Consolas,monospace}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:13px}
  .kv .k{color:var(--muted)}
  ul.tight{margin:6px 0 0;padding-left:20px}
  ul.tight li{margin:3px 0}
  .foot{color:var(--muted);font-size:12px;text-align:center;margin-top:26px}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="logo">AI</div>
    <div>
      <h1>AI 前沿雷达</h1>
      <div class="sub">每天把 AI / 大模型领域值得知道的新东西找出来</div>
    </div>
    <div style="margin-left:auto;text-align:right">
      <div id="dataBadge" class="badge">检查中…</div>
    </div>
  </header>

  <!-- ============ 数据概览 ============ -->
  <div class="card" id="cardData">
    <h2>今天的信息</h2>
    <div class="hint" id="dataHint">正在读取…</div>
    <div id="dataBody"></div>
  </div>

  <!-- ============ 步骤 1 ============ -->
  <div class="card">
    <h2>第 1 步 · 获取今天信息</h2>
    <div class="hint">从 20 个公开来源抓取（arXiv / HuggingFace / GitHub / Hacker News / 一线实验室博客）。约 45 秒，无需任何密钥。</div>
    <div class="row">
      <button class="btn-primary btn-lg" id="btnCollect">获取今天信息</button>
      <span class="badge" id="collectHint">上次检查：—</span>
    </div>
    <div class="progress-wrap hidden" id="progWrap">
      <div class="bar" id="bar"><i></i></div>
      <div class="row" style="justify-content:space-between;margin-top:8px">
        <span class="pct" id="pct">0%</span>
        <button class="btn-ghost" id="btnCancel" style="padding:5px 12px;font-size:12.5px">取消</button>
      </div>
      <div class="log" id="log"></div>
    </div>
    <div id="collectResult"></div>
  </div>

  <!-- ============ 步骤 2 ============ -->
  <div class="card" id="cardStep2">
    <h2>第 2 步 · 查看清单</h2>
    <div class="hint">候选清单是 Excel 表格和可读清单。表格里有分数明细、来源体检、主题汇总，可以自己排序筛选。</div>
    <div class="row">
      <button class="btn-ghost btn-lg" id="btnXlsx" disabled>打开 Excel 清单</button>
      <button class="btn-ghost btn-lg" id="btnDigest" disabled>打开可读清单</button>
    </div>
  </div>

  <!-- ============ 步骤 3 ============ -->
  <div class="card">
    <h2>第 3 步 · 生成报告</h2>
    <div class="hint">选择要总结的范围，自动生成一份结构化报告。</div>

    <div class="notice">
      <b>先说清楚这份报告的性质。</b>
      下面这个按钮做的是<b>本地的筛选、排序和统计整理</b>：把候选清单按你的条件切片，
      逐条列出标题、摘要、热度、命中信号和原文链接。<br>
      它<b>不会联网核实，也不会判断重要性</b> —— 那需要读懂内容后再去搜索验证，
      只有 AI 能做。所以如果你要的是带「为什么重要」「不确定的地方」的分析，
      请在 DSH 会话说一句 <b>「跑一下今天的 AI 前沿日报」</b>。
    </div>

    <div style="height:14px"></div>

    <label class="f">想总结哪些主题（不选=全部）</label>
    <div class="chips" id="topicChips"></div>

    <div style="height:16px"></div>
    <div class="fields">
      <div>
        <label class="f">分数下限</label>
        <input type="number" id="fMinScore" value="0" min="0" max="100" step="1">
      </div>
      <div>
        <label class="f">最多条目数</label>
        <input type="number" id="fLimit" value="30" min="1" max="200" step="5">
      </div>
      <div>
        <label class="f">分组方式</label>
        <select id="fGroup">
          <option value="topic">按主题分组</option>
          <option value="source">按来源分组</option>
          <option value="score">按分数降序</option>
        </select>
      </div>
      <div>
        <label class="f">只看来源（不选=全部）</label>
        <select id="fSource"><option value="">全部来源</option></select>
      </div>
    </div>

    <div style="height:10px"></div>
    <div class="fields">
      <div>
        <label class="f">备注（会写进报告，方便以后看懂当时为什么这么筛）</label>
        <input type="text" id="fNote" placeholder="例如：只看推理效率相关的">
      </div>
    </div>

    <div style="height:12px"></div>
    <div class="row">
      <label class="chip" id="chipHeat"><input type="checkbox" id="fHeat" style="margin-right:6px;vertical-align:-1px">只要带热度数据的</label>
      <label class="chip" id="chipPdf"><input type="checkbox" id="fPdf" style="margin-right:6px;vertical-align:-1px">同时生成 Word / PDF</label>
    </div>

    <div style="height:14px"></div>
    <div class="row">
      <button class="btn-primary btn-lg" id="btnReport">生成报告</button>
      <span class="badge" id="reportHint">—</span>
    </div>

    <div class="progress-wrap hidden" id="progWrap2">
      <div class="bar" id="bar2"><i></i></div>
      <div class="row" style="justify-content:space-between;margin-top:8px">
        <span class="pct" id="pct2">0%</span>
      </div>
      <div class="log" id="log2"></div>
    </div>
    <div id="reportResult"></div>
  </div>

  <!-- ============ 历史 ============ -->
  <div class="card">
    <h2>过去一周</h2>
    <div class="hint">每天的清单和报告都按日期存档在这里，不会互相覆盖。</div>
    <div id="historyBody">读取中…</div>
  </div>

  <div class="foot">
    数据只存在本机 · 服务只监听 127.0.0.1 · <a href="#" id="lnkQuit">退出程序</a>
  </div>
</div>

<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
const api = (p, opt) => fetch(p + (p.includes("?") ? "&" : "?") + "token=" + TOKEN, opt)
  .then(r => r.json());
const openFile = rel => api("/api/open", {
  method:"POST", headers:{"Content-Type":"application/json"},
  body: JSON.stringify({path: rel})
});

let busy = false;
let selectedTopics = new Set();
let allTopics = [];
let currentJob = null;

/* ---------------- 概览 ---------------- */
async function loadSummary(){
  const s = await api("/api/summary");
  const badge = $("dataBadge");
  if(!s.has_data){
    badge.className = "badge warn";
    badge.textContent = "还没有数据";
    $("dataHint").textContent = "还没有采集过。点下面的「获取今天信息」开始。";
    $("dataBody").innerHTML = "";
    $("btnXlsx").disabled = $("btnDigest").disabled = true;
    return;
  }
  badge.className = "badge " + (s.stale ? "warn" : "ok");
  badge.textContent = s.stale ? `数据已 ${s.age_hours} 小时` : "数据是新的";

  $("dataHint").textContent =
    `${s.date} 采集 · ${s.generated_at} UTC · 覆盖最近 ${Math.round(s.window_hours)} 小时`;

  const t = s.totals || {};
  const sc = s.source_counts || {};
  // 把"源体检"折起来：它是诊断信息，不该一打开就占掉半屏。
  const issues = [];
  if(s.bad_sources.length){
    issues.push(`<div class="notice"><b>${s.bad_sources.length} 个源没有产出</b>（源会悄悄失效而不报错，所以必须列出来）：
      <ul class="tight">${s.bad_sources.map(b=>`<li>${esc(b.name)} —— ${esc(b.why)}</li>`).join("")}</ul></div>`);
  }
  if(s.filtered_sources && s.filtered_sources.length){
    issues.push(`<div class="notice" style="background:#eef4ff;border-color:#c7d9f7;color:#2a4a80">
      <b>${s.filtered_sources.length} 个源抓到内容，但全在时间窗口外</b>（源本身正常，不用修）：
      <ul class="tight">${s.filtered_sources.map(b=>`<li>${esc(b.name)} —— 抓到 ${b.raw} 条，0 条落在窗口内</li>`).join("")}</ul></div>`);
  }

  $("dataBody").innerHTML = `
    <div class="stats">
      <div class="stat"><div class="k">最终候选</div><div class="v">${s.count}</div></div>
      <div class="stat"><div class="k">原始抓取</div><div class="v">${t.raw||0}</div></div>
      <div class="stat"><div class="k">首次出现</div><div class="v">${t.new||0}</div></div>
      <div class="stat"><div class="k">源：抓到内容</div><div class="v">${sc.fetched}/${sc.total}</div></div>
      <div class="stat"><div class="k">源：贡献了候选</div><div class="v">${sc.survived}</div></div>
      <div class="stat"><div class="k">分数区间</div><div class="v">${s.scores.min.toFixed(0)}–${s.scores.max.toFixed(0)}</div></div>
    </div>
    <div style="margin-top:12px">
      <div class="kv"><div class="k">主题分布</div><div>${s.topics.slice(0,8).map(x=>`${esc(x[0])} ${x[1]}`).join(" · ")}</div></div>
    </div>
    ${issues.length ? `
      <details style="margin-top:12px">
        <summary style="cursor:pointer;font-size:13px;color:var(--muted)">
          来源体检：${sc.bad} 个无产出 · ${(s.filtered_sources||[]).length} 个全在窗口外（点开看详情）
        </summary>
        <div style="margin-top:10px">${issues.join("")}</div>
      </details>` : ""}
  `;

  // 主题 chips
  allTopics = s.topics.map(x => x[0]);
  const holder = $("topicChips");
  holder.innerHTML = "";
  allTopics.forEach(name => {
    const d = document.createElement("div");
    d.className = "chip";
    d.textContent = name;
    d.onclick = () => {
      if(selectedTopics.has(name)) selectedTopics.delete(name);
      else selectedTopics.add(name);
      d.classList.toggle("on");
    };
    holder.appendChild(d);
  });

  // 来源下拉
  const sel = $("fSource");
  sel.innerHTML = '<option value="">全部来源</option>';
  s.sources.forEach(x => {
    const o = document.createElement("option");
    o.value = x[0]; o.textContent = `${x[0]}（${x[1]}）`;
    sel.appendChild(o);
  });

  $("btnXlsx").disabled = !s.files.xlsx;
  $("btnDigest").disabled = !s.files.digest;
}

/* ---------------- 进度轮询 ---------------- */
function showLogs(el, logs){
  el.innerHTML = logs.map(l => {
    const cls = /失败|出错|错误|Traceback/.test(l) ? "l-err"
              : /成功|完成|用时/.test(l) ? "l-ok" : "";
    return `<div class="${cls}">${esc(l)}</div>`;
  }).join("");
  el.scrollTop = el.scrollHeight;
}

function poll(barId, pctId, wrapId, logId, onDone){
  const timer = setInterval(async () => {
    const j = await api("/api/status");
    const wrap = $(wrapId);
    if(j.state === "running"){
      wrap.classList.remove("hidden");
      const p = j.progress || 0;
      // 抓取阶段没有可预期的完成时间，进度条先走"不确定"动画，
      // 拿到真实的阶段进度后再切成确定进度条 —— 比一个卡在 3% 的
      // 静止条诚实得多。
      if(p <= 8){
        $(barId).classList.add("indet");
        $(pctId).textContent = "抓取中…";
      } else {
        $(barId).classList.remove("indet");
        $(barId).firstElementChild.style.width = p + "%";
        $(pctId).textContent = p + "%";
      }
      showLogs($(logId), j.logs || []);
    } else {
      clearInterval(timer);
      $(barId).classList.remove("indet");
      const p = j.state === "done" ? 100 : (j.progress || 0);
      $(barId).firstElementChild.style.width = p + "%";
      $(pctId).textContent = j.state === "done" ? "完成" : (j.state === "error" ? "出错" : "已取消");
      showLogs($(logId), j.logs || []);
      busy = false; currentJob = j;
      onDone(j);
    }
  }, 600);
  return timer;
}

/* ---------------- 步骤 1 ---------------- */
$("btnCollect").onclick = async () => {
  if(busy) return;
  busy = true;
  $("btnCollect").disabled = true;
  $("collectResult").innerHTML = "";
  $("progWrap").classList.remove("hidden");
  $("pct").textContent = "0%";
  $("bar").firstElementChild.style.width = "0%";
  $("log").innerHTML = "";
  await api("/api/collect", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify({})});
  poll("bar", "pct", "progWrap", "log", j => {
    $("btnCollect").disabled = false;
    if(j.state === "done"){
      const t = (j.result && j.result.totals) || {};
      $("collectResult").innerHTML = `<div class="result">
        采集完成，用时 ${j.result.elapsed} 秒。原始 ${t.raw} 条 →
        去重后候选 <b>${t.candidates}</b> 条，其中 ${t.new} 条首次出现。
        ${j.result.bad_sources.length ? `<br>无产出的源：${j.result.bad_sources.map(esc).join("、")}` : ""}
      </div>`;
    } else if(j.state === "error"){
      $("collectResult").innerHTML = `<div class="result err">采集失败：${esc(j.error||"")}</div>`;
    }
    loadSummary(); loadHistory();
  });
};

$("btnCancel").onclick = () => api("/api/cancel", {method:"POST"});

/* ---------------- 步骤 2 ---------------- */
$("btnXlsx").onclick = () => openFile("out/candidates/latest.xlsx");
$("btnDigest").onclick = () => openFile("out/digests/latest.md");

/* ---------------- 步骤 3 ---------------- */
$("fHeat").onchange = e => $("chipHeat").classList.toggle("on", e.target.checked);
$("fPdf").onchange  = e => $("chipPdf").classList.toggle("on", e.target.checked);

$("btnReport").onclick = async () => {
  if(busy) return;
  const topics = [...selectedTopics];
  if(!allTopics.length){
    $("reportHint").textContent = "还没有数据，请先获取今天信息";
    return;
  }
  busy = true;
  $("btnReport").disabled = true;
  $("reportResult").innerHTML = "";
  $("progWrap2").classList.remove("hidden");
  $("bar2").firstElementChild.style.width = "0%";
  $("log2").innerHTML = "";
  const payload = {
    topics,
    min_score: $("fMinScore").value,
    limit: $("fLimit").value,
    group: $("fGroup").value,
    sources: $("fSource").value ? [$("fSource").value] : [],
    note: $("fNote").value,
    only_high_heat: $("fHeat").checked,
    make_pdf: $("fPdf").checked
  };
  await api("/api/report", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(payload)});
  poll("bar2", "pct2", "progWrap2", "log2", async j => {
    $("btnReport").disabled = false;
    if(j.state === "done"){
      const r = j.result;
      const md = await api("/api/file?path=" + encodeURIComponent(r.md));
      $("reportResult").innerHTML = `
        <div class="result">
          报告已生成：<b>${esc(r.md.split(/[\\/]/).pop())}</b>（${r.count} 条）
          <div class="row" style="margin-top:10px">
            <button class="btn-ghost" id="btnOpenRep">用默认程序打开</button>
            ${r.pdf ? `<button class="btn-ghost" id="btnOpenPdf">打开 PDF</button>` : ""}
          </div>
        </div>
        <div style="height:12px"></div>
        <pre class="md">${esc(md)}</pre>`;
      $("btnOpenRep").onclick = () => openFile(r.md);
      if(r.pdf) $("btnOpenPdf").onclick = () => openFile(r.pdf);
      loadHistory();
    } else if(j.state === "error"){
      $("reportResult").innerHTML = `<div class="result err">生成失败：${esc(j.error||"")}</div>`;
    }
  });
};

/* ---------------- 历史 ---------------- */
async function loadHistory(){
  const h = await api("/api/history");
  const days = h.days || [];
  if(!days.length){
    $("historyBody").innerHTML = '<div class="hint">还没有历史文件。</div>';
    return;
  }
  let html = `<table><tr><th>日期</th><th>报告</th><th>清单</th><th>表格</th></tr>`;
  days.forEach(d => {
    const reps = d.reports.map(r =>
      `<a href="#" data-p="${esc(r.name)}" class="rep">${r.kind}</a>`).join(" · ") || "—";
    const digs = d.digests.length
      ? `<a href="#" data-p="${esc(d.digests[0])}" class="dig">候选清单</a>` : "—";
    const xls = d.xlsx.length
      ? `<a href="#" data-p="${esc(d.xlsx[0])}" class="xls">Excel</a>` : "—";
    html += `<tr><td><b>${esc(d.date)}</b></td><td>${reps}</td><td>${digs}</td><td>${xls}</td></tr>`;
  });
  html += "</table>";
  $("historyBody").innerHTML = html;
  $("historyBody").querySelectorAll("a[data-p]").forEach(a => {
    a.onclick = async ev => {
      ev.preventDefault();
      const name = a.dataset.p;
      if(a.classList.contains("rep"))      openFile("out/reports/" + name);
      else if(a.classList.contains("xls")) openFile("out/candidates/" + name);
      else                                 openFile("out/digests/" + name);
    };
  });
}

function esc(s){
  return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

$("lnkQuit").onclick = async ev => {
  ev.preventDefault();
  if(confirm("退出 AI 前沿雷达？\n（关掉这个网页标签页不会退出程序）")){
    await api("/api/quit", {method:"POST"});
    document.body.innerHTML =
      '<div style="padding:60px;text-align:center;font-family:sans-serif">' +
      '<h2>已退出</h2><p>可以关闭这个标签页了。</p></div>';
  }
};

/* 首次加载，以及每 60 秒刷新一次概览（数据可能在别处被更新） */
loadSummary(); loadHistory();
setInterval(() => { if(!busy) { loadSummary(); loadHistory(); } }, 60000);
(async () => {
  const j = await api("/api/status");
  if(j.state === "running"){ busy = true; $("btnCollect").disabled = true;
    poll("bar","pct","progWrap","log", () => { $("btnCollect").disabled=false; }); }
})();
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ 启动

def pick_port(preferred=8321):
    for p in [preferred] + list(range(preferred + 1, preferred + 12)):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def main():
    ap = argparse.ArgumentParser(description="AI 前沿雷达 · 本地图形界面")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}/?token={TOKEN}"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True

    print(f"AI 前沿雷达已启动：http://127.0.0.1:{port}")
    print("（这个窗口可以最小化；关掉它程序就退出了）")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
