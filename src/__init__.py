# -*- coding: utf-8 -*-
"""ai-frontier：AI 前沿每日雷达。

分层（改任务时看这个）：
    config/topic.py     ⓞ 任务定义 —— 追踪什么、什么算重要
    config/sources.json ① 数据层 —— 从哪来
    src/net.py          网络访问 —— 重试、并发、TLS
    src/sources.py      解析 —— 原始响应 → 统一 Item
    src/scoring.py      ⓪ 评估 —— 时间过滤、事件去重、打分
    src/collect.py      ③ 工作流 —— 编排
    src/report.py       ④ 分发 —— Markdown / Excel

README.md 里有怎么换领域的完整说明。
"""
__version__ = "1.0.0"
