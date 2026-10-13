# -*- coding: utf-8 -*-
"""一眼看清今天采到了什么。

为什么需要它：采集器逐源打印，跑完就滚过去了。这个命令把
『今天到底有没有料』压缩成几十行，用来日常确认和排查。
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def bar(v, mx, width=22):
    n = int(round(v / mx * width)) if mx else 0
    return "█" * n + "·" * (width - n)


def main():
    p = ROOT / "out" / "candidates" / "latest.json"
    if not p.exists():
        print("还没有采集结果。先跑：run.cmd collect")
        return 1
    d = json.loads(p.read_text(encoding="utf-8"))
    cs = d.get("candidates", [])
    tot = d.get("totals", {})
    diag = d.get("source_diagnostics", {})
    gen = d.get("generated_at", "")[:19]
    age_h = None
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(d["generated_at"])).total_seconds() / 3600
    except Exception:
        pass

    print("=" * 66)
    print(f"  ai-frontier 状态   数据日期 {d.get('date')}   生成于 {gen} UTC")
    if age_h is not None:
        flag = "（数据偏旧，建议重跑）" if age_h > 26 else ""
        print(f"  距今 {age_h:.1f} 小时 {flag}")
    print("=" * 66)

    print(f"\n口径：原始 {tot.get('raw')} → 时间过滤 {tot.get('after_time_filter')} "
          f"→ 事件去重 {tot.get('after_time_filter')}-{tot.get('merged_duplicates')} "
          f"→ 候选 {tot.get('candidates')}")
    hs = d.get("http_stats", {})
    print(f"网络：成功 {hs.get('requests')} / 失败 {hs.get('failures')} "
          f"/ 重试 {hs.get('retries')}，{hs.get('bytes', 0)/1e6:.1f} MB")

    ok = [v for v in diag.values() if v.get("ok")]
    bad = [v for v in diag.values() if not v.get("ok")]
    print(f"\n来源：{len(ok)} 个有产出，{len(bad)} 个无产出")
    for v in bad:
        why = "；".join(v.get("warnings", [])) or "未知"
        print(f"   ❌ {v['name']}  —  {why}")

    print("\n各源抓取量：")
    mx = max((v["items"] for v in diag.values()), default=1)
    for sid, v in sorted(diag.items(), key=lambda kv: -kv[1]["items"]):
        if v["items"]:
            print(f"   {v['items']:5d} {bar(v['items'], mx)} {v['name']}")

    if not cs:
        print("\n候选为 0 —— 检查上面的失败源。")
        return 0

    scores = sorted((c["score"] for c in cs), reverse=True)
    print(f"\n分数：最高 {scores[0]:.1f} / 中位 {scores[len(scores)//2]:.1f} "
          f"/ 最低 {scores[-1]:.1f}")

    print("\n主题分布：")
    tc = Counter(c["topics"][0] if c["topics"] else "其他" for c in cs)
    mt = max(tc.values()) if tc else 1
    for k, v in tc.most_common():
        print(f"   {v:4d} {bar(v, mt)} {k}")

    multi = [c for c in cs if c.get("corroboration", 1) > 1]
    chan = [c for c in cs if (c.get("cross_group") or 1) > 1]
    print(f"\n信号：独立印证>1 的 {len(multi)} 条；多渠道收录的 {len(chan)} 条")

    print("\n前 12 条：")
    for i, c in enumerate(cs[:12], 1):
        t = c["title"]
        t = t if len(t) <= 58 else t[:57] + "…"
        print(f"   {i:2d}. [{c['score']:5.1f}] {t}")
        print(f"       {c['source_name'][:30]}  "
              f"{'/'.join(c['topics'][:2])}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
