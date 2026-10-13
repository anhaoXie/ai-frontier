# -*- coding: utf-8 -*-
"""可复用的采集流水线。

为什么单独抽出来：原来这套逻辑写在 collect.py 的 main 里，
只能从命令行调用，进度只能 print 到控制台。GUI 需要
（a）拿到结构化进度，（b）在同进程里跑。

所以这里做两件事：
  - 把编排逻辑集中到 run_collection()
  - 通过 log 回调把每一条进度吐出去，调用方自己决定
    是打印到控制台还是推给浏览器

collect.py 和 app_server.py 都调这里，不重复实现。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import scoring as sc
from . import sources as srcmod
from .net import HttpClient
from .report import write_markdown_digest, write_xlsx

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUT = ROOT / "out"
STATE_PATH = ROOT / "data" / "state.json"

# 「来源团体」：判断两个来源是否互相独立。
# 同一份 arXiv 预印本常同时挂 cs.CL/cs.LG/cs.AI，被三个 arxiv 源抓到，
# 那是同一份东西的交叉列表；GitHub 的两个查询也会命中同一个仓库。
SOURCE_GROUP = {
    "arxiv_cl": "arxiv", "arxiv_lg": "arxiv", "arxiv_ai": "arxiv",
    "hf_daily": "arxiv",
    "github_new": "github", "github_new_topic": "github",
    "hn_ai": "hn",
}


def load_sources_cfg():
    p = CONFIG_DIR / "sources.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for s in cfg.get("sources", []):
        s.setdefault("group", SOURCE_GROUP.get(s.get("id"), s.get("id")))
    return cfg


def load_topic_py():
    import importlib.util
    p = CONFIG_DIR / "topic.py"
    spec = importlib.util.spec_from_file_location("topic_cfg", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {
        "DOMAIN": getattr(mod, "DOMAIN", {}),
        "TOPICS": getattr(mod, "TOPICS", {}),
        "SALIENCE": getattr(mod, "SALIENCE", {}),
        "SCORING": getattr(mod, "SCORING", {}),
        "DEDUP": getattr(mod, "DEDUP", {}),
    }


def build_scoring_cfg(topic, window=None):
    tc = topic.get("SCORING", {}) or {}
    dd = topic.get("DEDUP", {}) or {}
    cfg = {
        "lookback_hours": (topic.get("DOMAIN", {}) or {}).get("lookback_hours", 24),
        "window_floor_hours": 72,
        "salience": topic.get("SALIENCE", {}) or {},
        "title_similarity": dd.get("title_similarity", 0.82),
        "max_candidates": tc.get("max_candidates", 120),
    }
    for k in ("w_salience", "w_heat", "w_source", "w_fresh",
              "w_novelty", "w_diversity", "w_channel"):
        cfg[k] = tc.get(k, 0.1)
    if window:
        cfg["lookback_hours"] = window
        cfg["window_floor_hours"] = max(window, 24)
    return cfg


def build_jobs(sources):
    """展开成具体抓取任务。flaky 源排最前，保证它们拿到完整重试窗口。"""
    jobs, meta = [], {}
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    for s in sorted(sources, key=lambda x: (not x.get("flaky", False))):
        if not s.get("enabled", True):
            continue
        tpl = s["url"]
        queries = s.get("queries")
        if queries:
            for i, q in enumerate(queries):
                key = f"{s['id']}#{i}"
                jobs.append((key, tpl.replace("{q}", q.replace(" ", "+")), None))
                meta[key] = s
        else:
            key = s["id"]
            jobs.append((key, tpl.replace("{since}", since), None))
            meta[key] = s
    return jobs, meta


def run_collection(window=None, only=None, no_state=False, log=None,
                   cancel=None):
    """跑一轮采集。log 是 callable(str)，cancel 是 callable()->bool。"""
    def say(msg):
        if log:
            log(msg)

    def aborted():
        return bool(cancel and cancel())

    t_start = time.time()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    src_cfg = load_sources_cfg()
    topic = load_topic_py()
    scfg = build_scoring_cfg(topic, window)
    sources = src_cfg.get("sources", [])
    if only:
        want = {x.strip() for x in only.split(",")}
        sources = [s for s in sources if s.get("id") in want]

    say(f"窗口：最近 {scfg['lookback_hours']}h（下限 {scfg['window_floor_hours']}h）")
    say(f"信息源：{len(sources)} 个已启用")

    client = HttpClient(src_cfg.get("fetch", {}))
    jobs, meta = build_jobs(sources)
    deadline = src_cfg.get("fetch", {}).get("deadline_seconds")
    say(f"开始并发抓取 {len(jobs)} 个请求"
        f"（并发 {client.max_workers}，整体时限 {deadline}s）")

    done_count = [0]

    def on_done(key, res):
        done_count[0] += 1
        pct = int(done_count[0] / max(1, len(jobs)) * 100)
        if res.ok:
            say(f"[{pct}%] 抓取成功 {key}  ({res.elapsed_ms}ms)")
        else:
            say(f"[{pct}%] 抓取失败 {key}  ({res.error})")

    results = client.map(jobs, on_done=on_done, deadline_seconds=deadline)
    if aborted():
        raise RuntimeError("已取消")
    missing = [k for k, _u, _a in jobs if k not in results]
    if missing:
        say(f"{len(missing)} 个请求未在时限内返回，已跳过")
    say(f"抓取完成：成功 {client.stats['requests']}，"
        f"失败 {client.stats['failures']}，重试 {client.stats['retries']}")

    items, diag = [], {}
    for s in sources:
        sid = s["id"]
        got, warns, nreq = [], [], 0
        fetched_ok = 0
        for key, res in results.items():
            if key == sid or key.startswith(sid + "#"):
                nreq += 1
                if res.ok:
                    fetched_ok += 1
                its, w = srcmod.parse(res, s, now)
                got.extend(its)
                warns.extend(w)
        # 关键：区分三种"没有内容"，它们的处置方式完全不同。
        #   1) 请求就失败了            -> 网络/站点问题，重试或忽略
        #   2) 请求成功但解析出 0 条    -> 最危险：源静默变空（arXiv RSS 每天
        #      04:00 UTC 之前就是返回 200 + 合法 XML + 0 条 item）
        #   3) 请求成功、解析到条目     -> 正常；条目数少可能只是今天量小
        # 如果不区分，第 2 种会和第 3 种一起显示成"正常"，然后你会在
        # 毫不知情的情况下用一份缺了一大块素材的候选清单。
        if not got:
            if fetched_ok and nreq:
                warns.append("请求成功但解析出 0 条内容 —— 疑似源静默变空，"
                             "而不是网络失败")
            elif warns:
                pass
            else:
                warns.append("没有产出，且未记录到具体原因")
        diag[sid] = {
            "name": s["name"], "kind": s.get("kind"), "requests": nreq,
            "items": len(got), "warnings": warns[:6],
            "weight": s.get("weight", 0.5), "ok": len(got) > 0,
            "fetched_ok": fetched_ok,
        }
        items.extend(got)
    raw_total = len(items)
    say(f"解析出 {raw_total} 条原始条目")
    # 记住每个源过滤前的条数。过滤前的数字才是"源到底给没给东西"，
    # 过滤后的数字受窗口影响 —— 少了它，一份"抓了 100 条但全在窗口外"
    # 的源会和"源坏了"长得一模一样。
    raw_by_source = {k: v["items"] for k, v in diag.items()}

    src_rank = {s["id"]: i for i, s in enumerate(sources)}
    stats = {}
    items = sc.time_filter(items, scfg, now, stats)
    after_filter = len(items)
    say(f"时间过滤后 {after_filter} 条"
        f"（丢弃过期 {stats.get('dropped_stale', 0)}，无日期 {stats.get('undated', 0)}）")

    # 把过滤前的条数补回诊断里，并区分"全被窗口砍掉"和"源没给东西"。
    for sid, d in diag.items():
        d["raw_items"] = raw_by_source.get(sid, 0)
        d["survived_filter"] = sum(1 for it in items if it.source_id == sid)
        if d["ok"] and d["survived_filter"] == 0:
            d["all_filtered"] = True
            d["warnings"] = (d.get("warnings") or [])[:4] + [
                f"抓到 {d['raw_items']} 条，但全部在 {stats.get('window_hours', 72):.0f} "
                f"小时窗口之外，本轮没有贡献候选（源本身是正常的）"]

    items = sc.dedupe_events(items, scfg, src_rank)
    say(f"事件去重后 {len(items)} 条（合并掉 {after_filter - len(items)} 条重复）")

    sc.assign_topics(items, topic.get("TOPICS", {}))
    state = sc.load_state(STATE_PATH)
    seen = set(state.get("seen", {}).keys())
    new_count = sum(1 for it in items if it.uid not in seen)
    items = sc.score_items(items, scfg, now, stats.get("window_hours", 72),
                           seen, src_rank)
    items = sc.rank_and_cap(items, scfg)
    say(f"打分排序后保留 {len(items)} 条候选（其中 {new_count} 条首次出现）")

    OUT.joinpath("candidates").mkdir(parents=True, exist_ok=True)
    OUT.joinpath("digests").mkdir(parents=True, exist_ok=True)
    OUT.joinpath("logs").mkdir(parents=True, exist_ok=True)

    payload = {
        "date": today,
        "generated_at": now.isoformat(),
        "domain": topic.get("DOMAIN", {}),
        "window_hours": stats.get("window_hours"),
        "totals": {
            "raw": raw_total, "after_time_filter": after_filter,
            "candidates": len(items), "new": new_count,
            "merged_duplicates": after_filter - len(items),
        },
        "source_diagnostics": diag,
        "http_stats": client.stats,
        "candidates": [it.to_dict() for it in items],
    }

    cpath = OUT / "candidates" / f"{today}.json"
    cpath.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    (OUT / "candidates" / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    say("已写入候选数据")

    mpath = write_markdown_digest(payload, OUT / "digests")
    xpath = write_xlsx(payload, OUT / "candidates")
    say("已生成候选清单（Markdown + Excel）")

    if not no_state:
        sc.save_state(STATE_PATH, state, today, [it.uid for it in items])

    elapsed = time.time() - t_start
    (OUT / "logs" / f"collect-{today}.json").write_text(
        json.dumps({"date": today, "elapsed_seconds": round(elapsed, 1),
                    "totals": payload["totals"], "http_stats": client.stats,
                    "sources": {k: {"items": v["items"], "ok": v["ok"],
                                    "warnings": v["warnings"]}
                                for k, v in diag.items()}},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    bad = [v["name"] for v in diag.values() if not v["ok"]]
    filtered_out = [v["name"] for v in diag.values() if v.get("all_filtered")]
    say(f"用时 {elapsed:.1f} 秒")
    if bad:
        say(f"注意：{len(bad)} 个源今天没有产出 —— {', '.join(bad)}")
    if filtered_out:
        say(f"另有 {len(filtered_out)} 个源抓到了内容但全在窗口外："
            f"{', '.join(filtered_out)}")

    payload["_paths"] = {"json": str(cpath), "digest": str(mpath),
                         "xlsx": str(xpath)}
    payload["_elapsed"] = round(elapsed, 1)
    return payload
