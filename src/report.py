# -*- coding: utf-8 -*-
"""④ 分发层：把候选清单渲染成人能看的 Markdown 和能排序筛选的 Excel。

这一层不做判断，只做呈现。所有分数都来自 scoring.py，
这里连"哪个更重要"都不重新决定。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# 主题展示顺序：越靠前越先入眼
TOPIC_ORDER = [
    "模型与训练", "推理与效率", "智能体与工具", "多模态",
    "评测与安全", "开源生态", "产品与商业", "其他",
]

SOURCE_KIND_LABEL = {
    "arxiv_rss": "论文", "rss": "订阅", "hf_daily": "论文",
    "hf_models": "开源", "github_search": "开源",
    "hn_search": "社区", "html_links": "官方",
}


def _fmt_score(s):
    return f"{s:.1f}"


def _truncate(s, n):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _topics_of(c):
    return c.get("topics") or ["其他"]


def write_markdown_digest(payload, out_dir: Path) -> Path:
    """候选清单（不是最终报告）。

    这份文件给两拨人看：
      - 人：快速扫一遍今天有什么
      - 技能层（AI）：作为分析素材，所以每条都带分数明细和原始链接
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    date = payload["date"]
    cands = payload.get("candidates", [])
    tot = payload.get("totals", {})
    diag = payload.get("source_diagnostics", {})

    L = []
    L.append(f"# AI 前沿候选清单 · {date}")
    L.append("")
    L.append(f"> 覆盖窗口：最近 {payload.get('window_hours', 0):.0f} 小时 ｜ "
             f"生成时间：{payload.get('generated_at', '')[:19]} UTC")
    L.append(f"> 原始 {tot.get('raw', 0)} 条 → 时间过滤 {tot.get('after_time_filter', 0)} 条 "
             f"→ 事件合并 + 打分 → **{tot.get('candidates', 0)} 条候选**"
             f"（其中 {tot.get('new', 0)} 条首次出现）")
    L.append("")

    # ---------------- 来源体检：这一节很重要，它让"源悄悄死掉"变得可见
    L.append("## 来源体检")
    L.append("")
    L.append("| 源 | 类型 | 抓到 | 状态 | 备注 |")
    L.append("|---|---|---:|---|---|")
    for sid, d in diag.items():
        label = SOURCE_KIND_LABEL.get(d.get("kind", ""), d.get("kind", ""))
        status = "✅" if d.get("ok") else "❌ 无产出"
        note = "；".join(d.get("warnings", [])[:2]) or ""
        L.append(f"| {d['name']} | {label} | {d['items']} | {status} | {note} |")
    L.append("")

    # ---------------- 按主题分组
    groups = {}
    for c in cands:
        groups.setdefault(_topics_of(c)[0], []).append(c)
    order = [t for t in TOPIC_ORDER if t in groups]
    order += [t for t in groups if t not in order]

    for topic in order:
        g = groups[topic]
        L.append(f"## {topic}（{len(g)} 条）")
        L.append("")
        for c in g:
            srcs = c.get("sources") or []
            src_txt = " + ".join(s["source_name"] for s in srcs[:3]) or c.get("source_name", "")
            corr = c.get("corroboration", 1)
            chan = c.get("cross_group", 1) or len(srcs)
            badge = ""
            if corr > 1:
                badge += f" ｜**{corr} 个独立来源印证**"
            if chan > 1:
                badge += f" ｜{chan} 个渠道收录"
            L.append(f"### [{_fmt_score(c['score'])}] {c['title']}")
            L.append("")
            if c.get("body"):
                L.append(f"{_truncate(c['body'], 420)}")
                L.append("")
            meta = [f"来源：{src_txt}"]
            if c.get("published"):
                meta.append(f"时间：{c['published'][:16].replace('T', ' ')}")
            if c.get("announce_type"):
                meta.append(f"类型：{c['announce_type']}")
            m = c.get("metrics") or {}
            hot = [f"{k}={v}" for k, v in m.items() if v]
            if hot:
                meta.append("热度：" + ", ".join(hot[:4]))
            L.append(" ｜ ".join(meta) + badge)
            L.append("")
            comp = (c.get("extra") or {}).get("components") or {}
            if comp:
                L.append("分数构成：" + " ".join(f"{k} {v}" for k, v in comp.items()))
            hits = (c.get("extra") or {}).get("salience_hits") or []
            if hits:
                L.append(f"命中信号：{', '.join(hits)}")
            if len(srcs) > 1:
                for s in srcs[1:]:
                    L.append(f"  - 另见 {s['source_name']}：{s['url']}")
            L.append("")
            L.append(f"链接：{c['url']}")
            L.append("")
        L.append("")

    L.append("---")
    L.append("")
    L.append("## 怎么读这份清单")
    L.append("")
    L.append("- **分数**是机器算的相关性/显著性，不是真相。它只用来排序，不用来下结论。")
    L.append("- 分数是**本轮相对分位**（最高分=100），不是绝对质量分。"
             "跨天比较没有意义，同一天内排序才有意义。"
             "之所以这样定标：各分项都有地板值，直接加权求和会让绝大多数条目挤在"
             "同一区间，排序就看不出差别了。")
    L.append("- **分数构成**里 `显著性` 命中越具体的技术/产品信号越高；"
             "`热度` 是对数压缩过的（100 分和 200 分的差距不等于 1000 分和 1100 分）。")
    L.append("- **独立来源印证**：同一件事被几个【互相独立】的来源报道。"
             "这是最有价值的信号 —— 单一来源的爆料经常是错的。"
             "注意：一篇 arXiv 论文同时挂 cs.CL/cs.LG/cs.AI 只算 1 个，"
             "因为那是同一份预印本。它常常是 0，这是**实测结果而不是故障**："
             "现阶段大多数条目只出现在 arXiv 一条线上。")
    L.append("- **渠道收录**：同一生态内被几个渠道收到（例如 arXiv 收录 + "
             "HuggingFace Daily Papers 人工选中）。它不等于独立印证，"
             "但被人工挑选本身是质量信号。")
    L.append("- 一条都没有的源会在『来源体检』里显示 ❌。**源会悄悄死掉，但不会自己报告。**")
    L.append("")

    p = out_dir / f"{date}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    (out_dir / "latest.md").write_text("\n".join(L), encoding="utf-8")
    return p


def write_xlsx(payload, out_dir: Path) -> Path:
    """可排序筛选的候选表格。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    out_dir.mkdir(parents=True, exist_ok=True)
    date = payload["date"]
    cands = payload.get("candidates", [])

    wb = Workbook()

    # ============================ Sheet 1: 候选清单
    ws = wb.active
    ws.title = "候选清单"
    headers = ["排名", "分数", "标题", "主主题", "全部主题", "来源", "独立印证",
               "渠道数", "发布时间(UTC)", "类型", "热度指标", "显著性", "热度",
               "源权重", "新鲜度", "首次出现", "独立印证分", "命中信号",
               "摘要", "链接"]
    ws.append(headers)

    head_fill = PatternFill("solid", fgColor="1F3864")
    head_font = Font(color="FFFFFF", bold=True, size=11)
    for i in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=i)
        c.fill = head_fill
        c.font = head_font
        c.alignment = Alignment(vertical="center", horizontal="center")

    for rank, c in enumerate(cands, 1):
        comp = (c.get("extra") or {}).get("components") or {}
        m = c.get("metrics") or {}
        hot = ", ".join(f"{k}={v}" for k, v in m.items() if v)
        dt = None
        if c.get("published"):
            try:
                dt = datetime.fromisoformat(c["published"]).replace(tzinfo=None)
            except Exception:
                dt = c["published"][:19]
        srcs = c.get("sources") or []
        ws.append([
            rank,
            round(c["score"], 2),
            c["title"],
            _topics_of(c)[0],
            ", ".join(_topics_of(c)),
            " + ".join(s["source_name"] for s in srcs[:4]),
            c.get("corroboration", 1),
            c.get("cross_group", 1) or len(srcs),
            dt,
            c.get("announce_type", ""),
            hot,
            comp.get("显著性", ""),
            comp.get("热度", ""),
            comp.get("源权重", ""),
            comp.get("新鲜度", ""),
            comp.get("首次出现", ""),
            comp.get("独立印证", ""),
            ", ".join((c.get("extra") or {}).get("salience_hits") or []),
            _truncate(c.get("body", ""), 900),
            c["url"],
        ])

    widths = [6, 7, 58, 12, 20, 26, 9, 8, 18, 9, 26, 8, 8, 8, 8, 8, 11, 26, 80, 46]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        row[2].alignment = Alignment(vertical="top", wrap_text=False)
        row[18].alignment = Alignment(vertical="top", wrap_text=False)
        url_cell = row[19]
        if isinstance(url_cell.value, str) and url_cell.value.startswith("http"):
            url_cell.hyperlink = url_cell.value
            url_cell.font = Font(color="0563C1", underline="single")
        row[8].alignment = Alignment(vertical="top")
        if isinstance(row[8].value, datetime):
            row[8].number_format = "yyyy-mm-dd hh:mm"

    # 分数越高，绿色越深 —— 扫一眼就能看出头部
    for r in range(2, ws.max_row + 1):
        v = ws.cell(row=r, column=2).value or 0
        if v >= 60:
            ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor="C6EFCE")
        elif v >= 45:
            ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor="FFEB9C")
        elif v < 30:
            ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor="F2F2F2")

    # ============================ Sheet 2: 主题汇总
    ws2 = wb.create_sheet("主题汇总")
    ws2.append(["主题", "条数", "平均分", "最高分", "最高分条目"])
    for i in range(1, 6):
        c = ws2.cell(row=1, column=i)
        c.fill = head_fill
        c.font = head_font
    buckets = {}
    for c in cands:
        for t in _topics_of(c):
            buckets.setdefault(t, []).append(c)
    for t, g in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        scores = [x["score"] for x in g]
        top = max(g, key=lambda x: x["score"])
        ws2.append([t, len(g), round(sum(scores) / len(scores), 2),
                    round(max(scores), 2), top["title"]])
    for i, w in enumerate([16, 8, 10, 10, 70], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = "A2"

    # ============================ Sheet 3: 来源体检
    ws3 = wb.create_sheet("来源体检")
    ws3.append(["源", "类型", "请求数", "条目数", "状态", "警告"])
    for i in range(1, 7):
        c = ws3.cell(row=1, column=i)
        c.fill = head_fill
        c.font = head_font
    for sid, d in (payload.get("source_diagnostics") or {}).items():
        ws3.append([d.get("name", sid), SOURCE_KIND_LABEL.get(d.get("kind", ""), ""),
                    d.get("requests", 0), d.get("items", 0),
                    "正常" if d.get("ok") else "无产出",
                    "；".join(d.get("warnings", []))])
    for i, w in enumerate([30, 10, 9, 9, 10, 80], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    ws3.freeze_panes = "A2"

    # ============================ Sheet 4: 运行说明
    ws4 = wb.create_sheet("运行说明")
    info = [
        ["项目", "ai-frontier · AI 前沿每日雷达"],
        ["日期", date],
        ["生成时间(UTC)", payload.get("generated_at", "")],
        ["覆盖窗口(小时)", payload.get("window_hours")],
        ["原始条目", (payload.get("totals") or {}).get("raw")],
        ["时间过滤后", (payload.get("totals") or {}).get("after_time_filter")],
        ["事件合并掉", (payload.get("totals") or {}).get("merged_duplicates")],
        ["最终候选", (payload.get("totals") or {}).get("candidates")],
        ["首次出现", (payload.get("totals") or {}).get("new")],
        ["HTTP 成功请求", (payload.get("http_stats") or {}).get("requests")],
        ["HTTP 失败", (payload.get("http_stats") or {}).get("failures")],
        ["HTTP 重试", (payload.get("http_stats") or {}).get("retries")],
        ["", ""],
        ["分数含义", "0-100，本轮相对分位（最高分=100）。用于同一天内排序，跨天比较无意义"],
        ["显著性", "命中越具体的技术/产品信号越高，标题命中权重是正文的 2 倍"],
        ["热度", "对数压缩：log1p(热度)/log1p(上限)，100 与 200 的差距≠1000 与 1100"],
        ["独立印证", "几个【互相独立】的来源团体报道了同一件事。一篇 arXiv 论文同时挂 cs.CL/cs.LG/cs.AI 只算 1"],
        ["渠道数", "同一生态内被几个渠道收录（arXiv 收录 + HF Daily 人工选中 = 2）"],
        ["首次出现", "与过去 30 天状态文件比对，第一次出现给满分"],
    ]
    for row in info:
        ws4.append(row)
    ws4.column_dimensions["A"].width = 18
    ws4.column_dimensions["B"].width = 100

    p = out_dir / f"{date}.xlsx"
    wb.save(p)
    wb.save(out_dir / "latest.xlsx")
    return p
