# -*- coding: utf-8 -*-
"""把候选清单里的头部条目导出成便于阅读的文本，供写报告时参考。

用法：
    python tools/dump_candidates.py            # 前 20 条，正文 700 字
    python tools/dump_candidates.py -n 40 -c 400
    python tools/dump_candidates.py --topic 智能体与工具
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20, help="输出条数")
    ap.add_argument("-c", type=int, default=700, help="每条正文截断字数")
    ap.add_argument("--topic", default=None, help="只看某个主题")
    ap.add_argument("--file", default=None, help="指定候选 JSON")
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()

    p = Path(args.file) if args.file else ROOT / "out" / "candidates" / "latest.json"
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    cs = d["candidates"]
    if args.topic:
        cs = [c for c in cs if args.topic in (c.get("topics") or [])]
    cs = cs[args.offset:args.offset + args.n]

    print(f"# 候选导出 · {d.get('date')} · 共 {len(cs)} 条")
    print(f"# 口径 {d.get('totals')}")
    print(f"# 源体检: " + "; ".join(
        f"{v['name']}={'OK' if v.get('ok') else 'FAIL:' + (v.get('warnings') or ['?'])[0]}"
        for v in (d.get("source_diagnostics") or {}).values() if not v.get("ok")))
    print()
    for i, c in enumerate(cs, 1 + args.offset):
        comp = (c.get("extra") or {}).get("components") or {}
        print("=" * 78)
        print(f"[{i}] score={c['score']:.1f}  {c['title']}")
        print(f"    源: {c['source_name']}  主题: {c.get('topics')}  "
              f"独立印证={c.get('corroboration')} 渠道={c.get('cross_group')}")
        print(f"    公布: {(c.get('published') or '')[:19]}  类型: {c.get('announce_type') or '-'}")
        if c.get("metrics"):
            print(f"    热度: {c['metrics']}")
        print(f"    构成: {comp}")
        hits = (c.get("extra") or {}).get("salience_hits") or []
        if hits:
            print(f"    命中信号: {hits}")
        if len(c.get("sources") or []) > 1:
            print(f"    全部来源: {[s['source_name'] for s in c['sources']]}")
        print(f"    链接: {c['url']}")
        body = (c.get("body") or "").strip()
        print(f"    正文({len(body)}字): {body[:args.c]}"
              + ("…" if len(body) > args.c else ""))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
