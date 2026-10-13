# -*- coding: utf-8 -*-
"""本地报告生成：按你选的条件，把候选清单整理成一份结构化报告。

## 这份报告能做什么、不能做什么（重要）

**能做的**：确定性的整理与统计 —— 筛选、排序、分组、来源体检、
分发统计、逐条列出标题+摘要+热度+命中信号+原文链接。

**不能做的**：它**不会联网核实，也不会判断重要性**。为什么：
  - 联网核实需要在开放网络上检索、辨别来源可信度、发现互相矛盾的说法
  - 重要性判断需要读懂内容，知道"这篇论文的方法是否真的新"
这两件事都需要语言模型主动做判断，本脚本做不到。

所以这份报告是**数据摘要**，不是**带判断的情报分析**。
两者差别很大：摘要会告诉你"今天有 30 条关于智能体的内容"，
判断会告诉你"其中 28 条不值得看"。

要拿到带判断的版本：在 DSH 会话说「跑一下今天的 AI 前沿日报」，
由 ai-frontier-daily 技能完成（会联网搜证 + 写"为什么重要/不确定的地方"）。

报告开头会自动写明这一点，避免以后误把它当成分析报告用。
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

# 用户能选的分组方式
GROUP_MODES = {
    "topic": "按主题分组",
    "source": "按来源分组",
    "score": "按分数降序（不分组）",
}


def _truncate(s, n):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def select_candidates(payload, topics=None, min_score=0.0, limit=30,
                      sources=None, only_high_heat=False):
    """按用户条件筛选候选。全部条件都是可选的。"""
    cs = payload.get("candidates", [])
    out = []
    for c in cs:
        if c.get("score", 0) < min_score:
            continue
        if topics:
            ct = set(c.get("topics") or [])
            if not (ct & set(topics)):
                continue
        if sources:
            sn = {s.get("source_name") for s in (c.get("sources") or [])}
            sn.add(c.get("source_name"))
            if not (sn & set(sources)):
                continue
        if only_high_heat:
            m = c.get("metrics") or {}
            if not any(v for v in m.values()):
                continue
        out.append(c)
    return out[:limit] if limit else out


def build_markdown(payload, sel, opts, now=None):
    now = now or datetime.now(timezone.utc)
    date = payload.get("date") or now.strftime("%Y-%m-%d")
    tot = payload.get("totals") or {}
    diag = payload.get("source_diagnostics") or {}
    allc = payload.get("candidates") or []
    group_mode = opts.get("group", "topic")

    L = []
    L.append(f"# AI 前沿数据摘要 · {date}")
    L.append("")
    L.append("> ⚠️ **这份报告是自动整理的数据摘要，不含联网核实和重要性判断。**")
    L.append(">")
    L.append("> 生成方式：按你设定的条件对候选清单做筛选、排序和分组。"
             "里面的每一条都是采集器抓回来的原始信息（标题/摘要/热度/链接），"
             "**没有经过查证，也没有经过领域筛选**。")
    L.append(">")
    L.append("> 想要带判断的版本（会联网搜证、写清「为什么重要」和"
             "「不确定的地方」），在 DSH 会话说一句：`跑一下今天的 AI 前沿日报`。")
    L.append("")

    # ---------------- 生成条件，必须写清楚，否则以后看不懂这份报告是怎么来的
    L.append("## 生成条件")
    L.append("")
    L.append(f"- 数据日期：{date}")
    L.append(f"- 数据生成时间：{(payload.get('generated_at') or '')[:19]} UTC")
    L.append(f"- 报告生成时间：{now.strftime('%Y-%m-%d %H:%M')} UTC")
    L.append(f"- 覆盖窗口：最近 {payload.get('window_hours', 0):.0f} 小时")
    L.append(f"- 分组方式：{GROUP_MODES.get(group_mode, group_mode)}")
    L.append(f"- 分数下限：{opts.get('min_score', 0)}")
    if opts.get("topics"):
        L.append(f"- 只看主题：{'、'.join(opts['topics'])}")
    if opts.get("sources"):
        L.append(f"- 只看来源：{'、'.join(opts['sources'])}")
    if opts.get("only_high_heat"):
        L.append("- 只要带热度数据的条目")
    L.append(f"- 选取条数：{len(sel)} 条（共 {len(allc)} 条候选）")
    L.append(f"- 用户备注：{opts.get('note') or '（无）'}")
    L.append("")

    # ---------------- 概况
    L.append("## 本次数据概况")
    L.append("")
    L.append(f"原始 {tot.get('raw', 0)} 条 → 时间过滤后 {tot.get('after_time_filter', 0)} 条 "
             f"→ 事件去重合并 {tot.get('merged_duplicates', 0)} 条 "
             f"→ 候选 {tot.get('candidates', 0)} 条，其中 {tot.get('new', 0)} 条首次出现。")
    L.append("")
    ok = [v for v in diag.values() if v.get("ok")]
    bad = [v for v in diag.values() if not v.get("ok")]
    L.append(f"来源：{len(ok)} 个有产出，{len(bad)} 个无产出。")
    if bad:
        L.append("")
        L.append("**今天没有产出的源**（源会悄悄失效而不报错，所以必须列出来）：")
        L.append("")
        for v in bad:
            why = "；".join(v.get("warnings", [])) or "未知"
            L.append(f"- {v['name']} —— {why}")
    L.append("")

    # ---------------- 分发统计
    from collections import Counter
    tc = Counter((c.get("topics") or ["其他"])[0] for c in sel)
    sc_ = Counter(c.get("source_name", "?") for c in sel)
    L.append("## 入选条目的分发")
    L.append("")
    L.append("| 主题 | 条数 |")
    L.append("|---|---:|")
    for k, v in tc.most_common():
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("| 来源 | 条数 |")
    L.append("|---|---:|")
    for k, v in sc_.most_common():
        L.append(f"| {k} | {v} |")
    L.append("")

    # ---------------- 正文
    if not sel:
        L.append("## 条目")
        L.append("")
        L.append("**按当前条件没有筛选出任何条目。** 把分数下限调低，"
                 "或减少主题限制再试一次。")
        L.append("")
    else:
        L.append(f"## 条目（{len(sel)} 条）")
        L.append("")
        groups = {}
        for c in sel:
            if group_mode == "topic":
                key = (c.get("topics") or ["其他"])[0]
            elif group_mode == "source":
                key = c.get("source_name", "未知来源")
            else:
                key = "全部条目"
            groups.setdefault(key, []).append(c)

        for gname, gitems in groups.items():
            if group_mode != "score":
                L.append(f"### {gname}（{len(gitems)} 条）")
                L.append("")
            for i, c in enumerate(gitems, 1):
                comp = (c.get("extra") or {}).get("components") or {}
                L.append(f"**{i}. [{c.get('score', 0):.1f} 分] {c.get('title', '')}**")
                L.append("")
                body = c.get("body") or ""
                if body:
                    L.append(_truncate(body, 320))
                    L.append("")
                meta = []
                if c.get("published"):
                    meta.append(f"时间 {c['published'][:16].replace('T', ' ')}")
                meta.append(f"来源 {c.get('source_name', '?')}")
                m = c.get("metrics") or {}
                hot = [f"{k}={v}" for k, v in m.items() if v]
                if hot:
                    meta.append("热度 " + ", ".join(hot[:3]))
                if (c.get("cross_group") or 1) > 1:
                    meta.append(f"{(c['cross_group'])} 个渠道收录")
                L.append(" ｜ ".join(meta))
                hits = (c.get("extra") or {}).get("salience_hits") or []
                if hits:
                    L.append(f"命中信号：{', '.join(hits)}")
                L.append(f"<{c.get('url', '')}>")
                L.append("")

    L.append("---")
    L.append("")
    L.append("## 怎么用这份报告")
    L.append("")
    L.append("- 它是**候选池的切片**，用来快速扫一遍和二次筛选，不是结论。")
    L.append("- 分数是**本轮相对分位**（最高分=100），跨天不可比 —— "
             "它表达的是「在今天的候选里排第几档」，不是绝对质量。")
    L.append("- **独立印证**：几个互相独立的来源团体报道了同一件事。"
             "一篇 arXiv 论文同时挂 cs.CL/cs.LG/cs.AI 只算 1 个。")
    L.append("- 每条都带原文链接，**要看结论请点进原文**。")
    L.append("")
    return "\n".join(L)


def generate(payload=None, topics=None, min_score=0.0, limit=30, sources=None,
             group="topic", note="", only_high_heat=False, make_pdf=False,
             log=None):
    """生成报告文件，返回路径信息。"""
    def say(m):
        if log:
            log(m)

    if payload is None:
        p = OUT / "candidates" / "latest.json"
        if not p.exists():
            raise RuntimeError("还没有候选数据，请先点『获取今天信息』。")
        import json
        payload = json.loads(p.read_text(encoding="utf-8"))

    opts = {"topics": topics, "min_score": min_score, "sources": sources,
            "group": group, "note": note, "only_high_heat": only_high_heat}
    sel = select_candidates(payload, topics=topics, min_score=min_score,
                            limit=limit, sources=sources,
                            only_high_heat=only_high_heat)
    say(f"筛选出 {len(sel)} 条（候选共 {len(payload.get('candidates', []))} 条）")
    if not sel:
        say("警告：按当前条件没有匹配条目，报告里会说明这一点")

    md = build_markdown(payload, sel, opts)
    rdir = OUT / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    date = payload.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%H%M%S")
    # 本地摘要和历史报告分开命名，避免和技能生成的正式日报混淆
    md_path = rdir / f"{date}-摘要-{stamp}.md"
    md_path.write_text(md, encoding="utf-8")
    (rdir / "latest_local.md").write_text(md, encoding="utf-8")
    say(f"已写入 {md_path.name}")

    result = {"md": str(md_path), "count": len(sel), "date": date}
    if make_pdf:
        try:
            from .make_docx import md_to_docx, docx_to_pdf
            docx = md_to_docx(md_path, rdir / (md_path.stem + ".docx"))
            pdf = docx_to_pdf(docx, rdir)
            result["docx"] = str(docx)
            result["pdf"] = str(pdf)
            say("已生成 Word 和 PDF")
        except Exception as e:
            say(f"Word/PDF 生成失败（Markdown 已保留）：{e}")
    return result
