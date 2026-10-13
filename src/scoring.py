# -*- coding: utf-8 -*-
"""⓪ 评估 + ③ 工作流的一部分：时间过滤、事件去重、打分排序。

这里做的是『哪些值得看』和『哪几条其实是同一件事』。
打分必须可解释：每个条目的 breakdown 里留着每一项的贡献，
这样报告里说"这条排第一"是有依据的，不是感觉。
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .sources import WS_RE, normalize_url

# ------------------------------------------------------------------ 文本规整

_PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_STOP = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with",
    "at", "by", "from", "is", "are", "was", "were", "be", "as", "it", "its",
    "this", "that", "these", "those", "we", "our", "you", "your", "how",
    "what", "why", "when", "new", "via", "using", "towards", "toward",
    "into", "can", "will", "not", "but", "has", "have", "been",
}


def norm_title(t: str) -> str:
    return WS_RE.sub(" ", _PUNCT_RE.sub(" ", (t or "").lower())).strip()


def sig_tokens(t: str):
    return {w for w in norm_title(t).split() if len(w) > 2 and w not in _STOP}


def dice_sim(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return 2.0 * inter / (len(a) + len(b))


def jaccard_sim(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def title_similarity(t1: str, t2: str) -> float:
    """两种相似度取大者。

    Dice 对『长度悬殊但核心词一致』更宽容（arXiv 标题 vs 媒体改写标题），
    Jaccard 更严格。取大者可以少漏掉同一事件。
    """
    a, b = sig_tokens(t1), sig_tokens(t2)
    return max(dice_sim(a, b), jaccard_sim(a, b))


# ------------------------------------------------------------------ 时间过滤

def time_filter(items, cfg, now: datetime, stats=None):
    """砍掉窗口外的东西。

    这里有个必须解释的取舍：
      - 24 小时是用户要的窗口，但 arXiv / 实验室博客是【按天公告】的。
        纯粹按 24 小时切，很容易一条论文都不剩（取决于几点跑）。
      - 所以引入 floor：窗口至少 72 小时，但对 24 小时之外的条目扣新鲜度分，
        让它们排在最后，而不是直接丢弃。
      - 真正做到『只看过去 24 小时』的办法是每天定时跑（见 run.cmd daily）。

    但 72 小时这个全局下限还不够，实测又发现了一个更严重的漏洞：
    2026-10-13（周二）上午，arXiv 里最新的一篇论文是 10-09 17:59，
    也就是 **86.5 小时之前** —— 因为 arXiv 只在工作日公告，周末不更新。
    周二早上看，上一篇公告已经是上周五的了。

    结论：对"周末不看就不产出"的论文源套用 72 小时窗口，会让每周一、
    周二的报告里**一篇论文都没有**。而论文是本项目最核心的内容类型。

    ## 所以改成按源区分窗口

    给源标 `cadence`：
      - "news"（默认）：快节奏源，窗口 = max(lookback, floor)，即 24/72 小时
      - "daily"：按天/按工作日节奏，放宽到 168 小时（一周），
        这样周末过完还能捞到上周五的论文

    放宽不会让旧内容霸榜：新鲜度分项照常扣分。关键区别是
    **"不进候选"和"排得靠后"是两件事，之前用错了那个更狠的。**
    """
    lookback = timedelta(hours=cfg.get("lookback_hours", 24))
    floor = timedelta(hours=cfg.get("window_floor_hours", 72))
    news_window = max(lookback, floor)
    daily_window = timedelta(hours=cfg.get("daily_window_hours", 168))

    kept, dropped, undated = [], 0, 0
    for it in items:
        if it.published is None:
            undated += 1
            kept.append(it)
            continue
        window = daily_window if it.cadence == "daily" else news_window
        if it.published >= now - window:
            kept.append(it)
        else:
            dropped += 1
    if stats is not None:
        stats["window_hours"] = news_window.total_seconds() / 3600
        stats["daily_window_hours"] = daily_window.total_seconds() / 3600
        stats["dropped_stale"] = dropped
        stats["undated"] = undated
    return kept


# ------------------------------------------------------------------ 事件去重

# 聚合器只提供"链接+热度"，不该成为事件的主链接
AGGREGATOR_SOURCES = {"hn_ai"}


def _primary_score(it, src_rank):
    """选谁当这条事件的『主链接』。"""
    url_pref = 0.0 if it.source_id not in AGGREGATOR_SOURCES else -1.0
    return (it.weight, url_pref, len(it.body), src_rank.get(it.source_id, 0))


def dedupe_events(items, cfg, src_rank):
    """把描述同一件事的条目合并成一条。

    三级判定，从可靠到宽松：
      1. canonical_id 相同 —— arXiv 编号 / HF 模型名 / GitHub 全名，这是硬身份
      2. 规范化标题完全相同
      3. 标题词集相似度超过阈值（同一事件被不同媒体改写）
    """
    thr = cfg.get("title_similarity", 0.82)

    # --- 第 1、2 级：哈希分组
    groups = defaultdict(list)
    for it in items:
        if it.canonical_id:
            key = ("cid", it.canonical_id.lower())
        else:
            key = ("title", norm_title(it.title))
        groups[key].append(it)

    merged = []
    for key, g in groups.items():
        merged.append(_merge(g, src_rank))

    # --- 第 3 级：在候选桶内做相似度合并
    # 分桶依据是『最重要的两个词』，避免两两比较 O(n^2)
    buckets = defaultdict(list)
    for it in merged:
        toks = sorted(sig_tokens(it.title))
        bkey = (toks[0] if toks else "", toks[1] if len(toks) > 1 else "")
        buckets[bkey].append(it)

    out = []
    used = set()
    for bkey, g in buckets.items():
        g = sorted(g, key=lambda x: -x.weight)
        for i, a in enumerate(g):
            if id(a) in used:
                continue
            cluster = [a]
            for b in g[i + 1:]:
                if id(b) in used:
                    continue
                if title_similarity(a.title, b.title) >= thr:
                    cluster.append(b)
                    used.add(id(b))
            used.add(id(a))
            out.append(_merge(cluster, src_rank))
    return out


def _merge(group, src_rank):
    """把一组条目合成一个事件。

    关键点：合并必须【可叠加】。第二轮去重会把第一轮已经合并过的条目
    再合一次，如果这里把 corroboration 重新按"组内条数"算，第一轮的结果
    就被抹平了 —— 实测这个 bug 会让所有条目的印证数都变成 1。
    所以这里每次都从各方已有的 sources 记录里重新聚合，而不是重算。
    """
    if len(group) == 1:
        it = group[0]
        it.corroboration = len({s.get("group") or s["source_id"]
                                for s in it.sources})
        it.cross_group = len({s["source_id"] for s in it.sources})
        return it

    group = sorted(group, key=lambda x: _primary_score(x, src_rank), reverse=True)
    primary = group[0]

    # 聚合所有来源记录（含各方已合并的），按 source_id 去重
    by_id = {}
    for it in group:
        for s in (it.sources or [{"source_id": it.source_id,
                                  "source_name": it.source_name,
                                  "url": it.url, "weight": it.weight}]):
            by_id.setdefault(s["source_id"], s)

    # 这里刻意区分两个概念，它们回答的是不同问题：
    #   corroboration = 有几个【不同渠道】报道了这件事
    #   cross_group   = 有几个【互相独立的团体】报道了这件事
    # 为什么必须分开：一篇 arXiv 论文常同时挂 cs.CL/cs.LG/cs.AI，三个 arxiv 源
    # 都抓到它 —— 渠道数是 3，但它是同一份预印本，独立印证只有 1 个。
    # 反过来，一篇论文同时被 arXiv 收录、又被 HuggingFace Daily Papers 选中，
    # 渠道数是 2 而团体数仍是 1（都是 arXiv 生态），但"被人工挑选 + 有社区投票"
    # 本身就是额外的质量信号，不该被丢掉。
    groups = {s.get("group") or s["source_id"] for s in by_id.values()}

    primary.sources = sorted(by_id.values(), key=lambda s: -s["weight"])
    primary.corroboration = max(1, len(groups))
    primary.cross_group = len(by_id)

    # 合并热度信号：取各源最大值，而不是相加（相加会让多源提及虚高）
    merged_metrics = dict(primary.metrics)
    for it in group[1:]:
        for k, v in it.metrics.items():
            if isinstance(v, (int, float)):
                merged_metrics[k] = max(merged_metrics.get(k, 0) or 0, v)
    primary.metrics = merged_metrics
    # 正文取最长的（信息最多）
    longest = max(group, key=lambda x: len(x.body))
    if len(longest.body) > len(primary.body):
        primary.body = longest.body
    primary.extra = dict(primary.extra or {})
    primary.extra["also_reported_by"] = [s["source_name"]
                                         for s in primary.sources[1:]]
    return primary


# ------------------------------------------------------------------ 主题与打分

def assign_topics(items, topics_cfg):
    """给每条打主题标签：命中标题/正文里的主题关键词。"""
    compiled = []
    for name, kws in topics_cfg.items():
        compiled.append((name, [k.lower() for k in kws]))
    for it in items:
        hay_title = it.title.lower()
        hay_body = it.body[:1500].lower()
        hits = []
        for name, kws in compiled:
            score = 0
            for k in kws:
                if k in hay_title:
                    score += 3
                elif k in hay_body:
                    score += 1
            if score:
                hits.append((name, score))
        hits.sort(key=lambda x: -x[1])
        it.topics = [h[0] for h in hits[:3]] or ["其他"]


def _kw_pattern(kw: str):
    """关键词匹配模式。

    纯 ASCII 词必须按词边界匹配 —— 否则 'qwen' 会命中 'sequence'，
    'ban' 会命中 'urban'。这类假命中会让你以为信号很强，其实全是噪声。
    含中文的词没有词边界概念，退回普通子串匹配。
    """
    if re.fullmatch(r"[a-z0-9][a-z0-9 .\-+]*", kw):
        return re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")
    return re.compile(re.escape(kw))


_SALIENCE_PATTERNS = {}


def _pattern_for(kw: str):
    p = _SALIENCE_PATTERNS.get(kw)
    if p is None:
        p = _kw_pattern(kw)
        _SALIENCE_PATTERNS[kw] = p
    return p


def salience_score(it, salience_cfg):
    """显著性：命中多少个"重要的具体信号"。

    标题命中权重是正文的 2 倍 —— 标题提到意味着文章主旨就是它。
    负权重词（webinar/sponsored 之类）用来压制公关稿和教程。
    """
    title = it.title.lower()
    body = it.body[:1200].lower()
    raw = 0.0
    matched = []
    for kw, w in salience_cfg.items():
        pat = _pattern_for(kw)
        if pat.search(title):
            raw += w * 2.0
            matched.append(kw)
        elif pat.search(body):
            raw += w
            matched.append(kw)
    it.extra = dict(it.extra or {})
    it.extra["salience_hits"] = matched[:10]
    # 用 tanh 压到 0-1：命中很多词不再线性加分，避免堆关键词的稿子霸榜
    return max(0.0, math.tanh(raw / 4.0))


HEAT_CAPS = {
    "hn_search": 500.0,
    "github_search": 3000.0,
    "hf_daily": 60.0,
    "hf_models": 800.0,
}


def heat_score(it):
    """热度：对数压缩。100 分和 200 分的差距，不该等于 1000 分和 1100 分。"""
    h = it.heat
    if h <= 0:
        return 0.0
    cap = HEAT_CAPS.get(it.source_id, HEAT_CAPS.get(it.category_hint, 200.0))
    return min(1.0, math.log1p(h) / math.log1p(cap))


def fresh_score(it, now, window_hours):
    age = it.age_hours(now)
    if age is None:
        return 0.45          # 没日期：给中等分，既不奖励也不惩罚
    if age <= 24:
        return 1.0
    return max(0.0, 1.0 - (age - 24.0) / max(1.0, window_hours - 24.0))


def score_items(items, cfg, now, window_hours, seen_uids, src_rank):
    for it in items:
        w = cfg
        s_sal = salience_score(it, cfg["salience"])
        s_heat = heat_score(it)
        s_src = min(1.0, it.weight)
        s_fresh = fresh_score(it, now, window_hours)
        s_novel = 0.0 if it.uid in seen_uids else 1.0
        # 独立印证：2 个互相独立的团体提到同一件事给满分。
        # 门槛定在 2（而不是 3）是因为同源重复已在 _merge 里排掉了，
        # 能凑到 2 个独立团体（比如媒体 + 官方，或论文 + 社区）已是强信号。
        s_corr = min(1.0, max(0, it.corroboration - 1) / 1.0)
        # 渠道丰富度：即便都在 arXiv 生态内，"被 HF Daily 人工选中"也算加分
        s_chan = min(1.0, max(0, it.cross_group - 1) / 2.0)

        parts = {
            "salience": w["w_salience"] * s_sal,
            "heat": w["w_heat"] * s_heat,
            "source": w["w_source"] * s_src,
            "fresh": w["w_fresh"] * s_fresh,
            "novelty": w["w_novelty"] * s_novel,
            "corroboration": w["w_diversity"] * s_corr,
            "channel": w.get("w_channel", 0.06) * s_chan,
        }
        it.breakdown = parts
        it.extra = dict(it.extra or {})
        it.extra["components"] = {
            "显著性": round(s_sal, 3), "热度": round(s_heat, 3),
            "源权重": round(s_src, 3), "新鲜度": round(s_fresh, 3),
            "首次出现": round(s_novel, 3), "独立印证": round(s_corr, 3),
            "渠道数": it.cross_group,
        }
        it._raw = sum(parts.values())

    # --- 相对分位拉伸
    # 各分项都有地板值（源权重、新鲜度、首次出现几乎条条满分），
    # 直接加权求和会让绝大多数条目挤在 60-70 区间，排序看不出差别。
    # 所以按本轮最高分归一化：分数表达的是"在今天的候选中排第几档"，
    # 而不是"绝对质量"。报告里必须把这层含义讲清楚，否则会误导。
    mx = max((it._raw for it in items), default=0.0)
    for it in items:
        it.score = (it._raw / mx) * 100.0 if mx > 0 else 0.0
        it.breakdown["raw_total"] = it._raw
    return items


def rank_and_cap(items, cfg):
    items.sort(key=lambda x: (-x.score, x.title))
    mx = cfg.get("max_candidates", 120)
    return items[:mx]


# ------------------------------------------------------------------ 状态

def load_state(path: Path):
    if not path.exists():
        return {"seen": {}, "runs": []}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        d.setdefault("seen", {})
        d.setdefault("runs", [])
        return d
    except Exception:
        return {"seen": {}, "runs": []}


def save_state(path: Path, state, today: str, uids):
    seen = state.get("seen", {})
    for u in uids:
        seen.setdefault(u, today)
    # 只保留 30 天，避免状态文件无限膨胀
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    state["seen"] = seen
    runs = state.get("runs", [])
    runs.append({"date": today, "count": len(uids)})
    state["runs"] = runs[-60:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                    encoding="utf-8")
