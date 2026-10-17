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
import json
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


def _already_fresh(now):
    """今天已经成功采过一轮就返回 (候选数, 路径)，否则 None。

    定时任务现在每 2 小时重试到 23:00。不设这道闸的话，一天会重跑七八次：
    既把 novelty（"首次出现"）压成 0 —— 那正是打分里判断新意的依据 ——
    又反复覆盖同一份数据。

    判断依据刻意用「候选数 > 0」而不是「文件存在」：整个网络挂掉时
    管线照样会写一个候选为 0 的文件（10-16 就是这样），那种日子恰恰
    最需要后面几轮重试。
    """
    path = ROOT / "out" / "candidates" / f"{now.strftime('%Y-%m-%d')}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    n = (data.get("totals") or {}).get("candidates") or 0
    return (n, path) if n > 0 else None


def run(args):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    print(f"ai-frontier 采集  {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if args.only:
        print(f"[only] 只跑: {args.only}")
    print()

    if getattr(args, "if_fresh", False) and not args.only:
        hit = _already_fresh(now)
        if hit:
            print(f"今天已成功采集过（候选 {hit[0]} 条，{hit[1].name}），跳过本轮。")
            print("要强制重跑，去掉 --if-fresh 即可。")
            return 0

    def log(msg):
        print(msg, flush=True)

    payload = pipeline_run.run_collection(
        window=args.window, only=args.only, no_state=args.no_state, log=log)

    print(f"\n写入 {payload['_paths']['json']}")
    if payload["_paths"].get("digest"):
        print(f"写入 {payload['_paths']['digest']}")
    if payload["_paths"].get("xlsx"):
        print(f"写入 {payload['_paths']['xlsx']}")
    hs = payload.get("http_stats", {})
    print(f"\n完成，用时 {payload['_elapsed']}s")
    print(f"  HTTP: {hs.get('requests')} 成功请求 / "
          f"{hs.get('failures')} 失败 / {hs.get('retries')} 次重试")

    diag = payload.get("source_diagnostics", {})
    # 先摘出「源自带发布日历、今天本来就不发布」的（如 arXiv 的 skipDays 周末）。
    # 把它们混进告警，等于每天制造一条误报；误报多了，真故障就没人看了。
    expected = [(v["name"], v.get("skip_day")) for v in diag.values()
                if v.get("skip_day")]
    bad = [(v["name"], v.get("warnings") or ["未知"]) for v in diag.values()
           if not v["ok"] and not v.get("skip_day")]
    if expected:
        print(f"\n  · 按源自身日历今天不发布（非故障，{len(expected)} 个）：")
        for name, day in expected:
            print(f"     - {name}（skipDays 含 {day}）")
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
    ap.add_argument("--if-fresh", action="store_true",
                    help="今天已成功采过就跳过（定时任务用，避免重复覆盖）")
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
