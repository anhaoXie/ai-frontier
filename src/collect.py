# -*- coding: utf-8 -*-
"""ai-frontier 采集器入口（命令行）。

用法：
    python src/collect.py                 # 按配置跑一轮完整采集
    python src/collect.py --window 24      # 覆盖时间窗口
    python src/collect.py --only arxiv_cl  # 只跑某个源（调试用）
    python src/collect.py --no-state       # 不更新"已见过"状态（补跑历史用）

产出（全部在 out/ 下）：
    candidates/YYYY-MM-DD.json   当日候选全量（含分数明细，供技能层分析）
    candidates/latest.json       指向最近一次
    digests/YYYY-MM-DD.md        人读的候选清单（按主题分组）
    candidates/YYYY-MM-DD.xlsx   可排序筛选的表格
    logs/collect-*.json          本次运行日志（哪个源失败、失败原因）

实现说明：真正的编排逻辑在 pipeline_run.py，这里只负责命令行参数和
把进度打印到控制台。之所以抽出去，是因为图形界面也要跑同一套流程 ——
两边各写一份的结果就是"修了一个忘了另一个"，这个坑本项目已经踩过一次
（arXiv 静默返回 0 条的修复只补在了其中一条路径上）。
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src import pipeline_run  # noqa: E402


def run(args):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    print(f"ai-frontier 采集  {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if args.only:
        print(f"[only] 只跑: {args.only}")
    print()

    def log(msg):
        print(msg, flush=True)

    payload = pipeline_run.run_collection(
        window=args.window, only=args.only, no_state=args.no_state, log=log)

    print(f"\n写入 {payload['_paths']['json']}")
    print(f"写入 {payload['_paths']['digest']}")
    print(f"写入 {payload['_paths']['xlsx']}")
    hs = payload.get("http_stats", {})
    print(f"\n完成，用时 {payload['_elapsed']}s")
    print(f"  HTTP: {hs.get('requests')} 成功请求 / "
          f"{hs.get('failures')} 失败 / {hs.get('retries')} 次重试")

    diag = payload.get("source_diagnostics", {})
    bad = [(v["name"], v.get("warnings") or ["未知"]) for v in diag.values()
           if not v["ok"]]
    if bad:
        print(f"\n  ⚠ 无产出的源（{len(bad)}）—— 必须看清原因，"
              f"有些是网络问题，有些是源静默变空：")
        for name, warns in bad:
            print(f"     - {name}: {'；'.join(warns[:2])}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="ai-frontier 每日采集")
    ap.add_argument("--window", type=int, default=None,
                    help="覆盖时间窗口（小时）")
    ap.add_argument("--only", type=str, default=None,
                    help="只跑指定源，逗号分隔")
    ap.add_argument("--no-state", action="store_true",
                    help="不更新已见状态")
    ap.add_argument("--json-only", action="store_true",
                    help="（已废弃，忽略；Excel 始终生成）")
    args = ap.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n中断")
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
