# -*- coding: utf-8 -*-
"""查 arXiv 官方 API 返回的 120 条为什么一条都没进候选。

两个可能：
  A) 这些论文本来就在 72 小时窗口之外（正常，只是今天 arXiv 没新公告）
  B) 日期解析失败，导致 published=None 或被误判成很旧（bug）
必须分清 —— 前者不用管，后者会让整个论文源长期静默失效。
"""
import io
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src import sources as srcmod          # noqa: E402
from src import pipeline_run as P          # noqa: E402
from src import scoring as sc              # noqa: E402
from src.net import HttpClient             # noqa: E402

cfg = P.load_sources_cfg()
src = next(s for s in cfg["sources"] if s["id"] == "arxiv_api")
client = HttpClient(cfg.get("fetch", {}))
now = datetime.now(timezone.utc)
print("现在(UTC):", now.isoformat())
print("cutoff(72h):", (now - timedelta(hours=72)).isoformat())
print()

res = client.get(src["url"])
print("抓取:", res.ok, res.status, len(res.body or b""), "bytes")
items, warns = srcmod.parse(res, src, now)
print(f"解析出 {len(items)} 条   warns={warns}")
if not items:
    sys.exit(0)

undated = sum(1 for it in items if it.published is None)
print(f"其中无日期: {undated}")
ages = [(it, it.age_hours(now)) for it in items if it.published]
ages.sort(key=lambda x: x[1])
print(f"有日期的 {len(ages)} 条，年龄(小时) 分布:")
buckets = Counter()
for _, a in ages:
    if a is None:
        buckets["None"] += 1
    elif a <= 24:
        buckets["<=24h"] += 1
    elif a <= 72:
        buckets["24-72h"] += 1
    elif a <= 168:
        buckets["3-7天"] += 1
    else:
        buckets[">7天"] += 1
print("  ", dict(buckets))
print("\n最新 5 条:")
for it, a in ages[:5]:
    print(f"   {it.published.isoformat()}  age={a:7.1f}h  {it.title[:64]}")
print("\n最旧 3 条:")
for it, a in ages[-3:]:
    print(f"   {it.published.isoformat()}  age={a:7.1f}h  {it.title[:64]}")

scfg = P.build_scoring_cfg(P.load_topic_py())
stats = {}
kept = sc.time_filter(items, scfg, now, stats)
print(f"\ntime_filter 之后: {len(kept)} 条   stats={stats}")
