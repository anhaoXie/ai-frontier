# -*- coding: utf-8 -*-
"""① 数据层：把每个源的原始响应解析成统一的 Item。

设计原则：解析器只负责『这个源给我的东西长什么样』，
不负责评分、不负责排序、不负责去重 —— 那些在 scoring.py。

每个解析器都必须容忍结构变化：拿不到就返回空列表并留下 warnings，
绝不让一个源改版把整轮采集炸掉。
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>")

# 这些查询参数只是追踪，不参与资源标识 —— 去掉它们才能真正去重
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source", "spm",
    "_hsenc", "_hsmi", "igshid", "yclid", "wt_mc",
}

# 量化/再打包版本的特征词。开源模型榜上大量条目是同一模型的 4bit 搬运，
# 不过滤的话『开源动向』这一栏就废了。
REPACKAGE_MARKERS = (
    "gguf", "awq", "gptq", "exl2", "exl3", "-4bit", "-8bit", "_4bit", "_8bit",
    "int4", "int8", "fp8", "bnb", "q4_k", "q5_k", "q8_0", "mlx", "onnx",
    "openvino", "tflite", "coreml", "w4a16", "quanto", "hqq", "eetq",
    "-lora", "_lora", "-qlora", "adapter", "peft", "merged", "-ggml",
)

# 标题里出现这些词说明不是"新模型发布"，而是文档/示例/玩具
MODEL_NOISE = ("demo", "test", "tutorial", "example", "docs", "template")


# ------------------------------------------------------------------ 基础工具

def strip_html(s: str) -> str:
    if not s:
        return ""
    s = SCRIPT_RE.sub(" ", s)
    s = TAG_RE.sub(" ", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&apos;", "'"), ("&mdash;", "—"), ("&ndash;", "–"),
                 ("&#x27;", "'"), ("&hellip;", "…")):
        s = s.replace(a, b)
    s = re.sub(r"&#x?[0-9a-fA-F]+;", " ", s)
    return WS_RE.sub(" ", s).strip()


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
             if k.lower() not in TRACKING_PARAMS]
        path = p.path.rstrip("/") or "/"
        return urlunparse((p.scheme.lower() or "https", p.netloc.lower(), path,
                           "", urlencode(q), ""))
    except Exception:
        return url.strip()


def fingerprint(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p or "").encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(s: str):
    """尽力把各种日期格式解成带时区的 datetime。解不出返回 None。"""
    if not s:
        return None
    s = s.strip()
    # RFC 2822（RSS 的标准格式）
    try:
        return to_utc(parsedate_to_datetime(s))
    except Exception:
        pass
    # ISO 8601（Atom / 各种 JSON API）
    iso = s.replace("Z", "+00:00")
    try:
        return to_utc(datetime.fromisoformat(iso))
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y",
                "%Y/%m/%d", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return to_utc(datetime.strptime(s, fmt))
        except Exception:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def slug_to_title(url: str) -> str:
    """把 /news/some-post-title 变成 'Some post title'。保底用。"""
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug)
    slug = slug.replace("-", " ").replace("_", " ").strip()
    if not slug or slug.isdigit():
        return ""
    return slug[0].upper() + slug[1:]


def arxiv_id_from(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(\d{4}\.\d{4,5})", text)
    return m.group(1) if m else ""


def is_repackaged(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return any(mk in mid for mk in REPACKAGE_MARKERS)


# ------------------------------------------------------------------ Item

class Item:
    """统一条目。所有源都产出这个东西，下游只认它。"""

    __slots__ = (
        "title", "url", "body", "published", "source_id", "source_name",
        "weight", "category_hint", "metrics", "announce_type", "canonical_id",
        "topics", "score", "breakdown", "extra", "sources", "corroboration",
        "_raw", "group", "cross_group", "cadence",
    )

    def __init__(self, title="", url="", body="", published=None, source_id="",
                 source_name="", weight=0.5, category_hint="", metrics=None,
                 announce_type="", canonical_id="", extra=None, group="",
                 cadence="news"):
        self.title = WS_RE.sub(" ", (title or "")).strip()
        self.url = normalize_url(url)
        self.body = (body or "").strip()
        self.published = published
        self.source_id = source_id
        self.source_name = source_name
        self.weight = weight
        self.category_hint = category_hint
        self.metrics = metrics or {}
        self.announce_type = announce_type
        self.canonical_id = canonical_id
        self.group = group or source_id
        self.cadence = cadence or "news"
        self.topics = []
        self.score = 0.0
        self.breakdown = {}
        self.extra = extra or {}
        self._raw = 0.0
        # 跨源合并后：见过这一事件的所有来源（按权重降序）
        self.sources = [{"source_id": source_id, "source_name": source_name,
                         "url": self.url, "weight": weight,
                         "group": self.group}]
        self.corroboration = 1
        self.cross_group = 1

    @property
    def uid(self) -> str:
        """条目身份。有规范 ID 就用它，否则用规范化 URL。

        用 URL 而不是标题：同一事件不同媒体的标题不同，但 URL 一定不同 ——
        跨源合并交给相似度去判，不在这里做。
        """
        if self.canonical_id:
            return f"{self.source_id}:{self.canonical_id}"
        return fingerprint(self.url or self.title)

    @property
    def heat(self) -> float:
        m = self.metrics
        for k in ("points", "upvotes", "stars", "likes", "trendingScore",
                  "downloads"):
            v = m.get(k)
            if isinstance(v, (int, float)) and v:
                return float(v)
        return 0.0

    def age_hours(self, now: datetime):
        if not self.published:
            return None
        return (now - self.published).total_seconds() / 3600.0

    def to_dict(self):
        return {
            "uid": self.uid,
            "title": self.title,
            "url": self.url,
            "body": self.body,
            "published": self.published.isoformat() if self.published else None,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "weight": self.weight,
            "category_hint": self.category_hint,
            "announce_type": self.announce_type,
            "canonical_id": self.canonical_id,
            "metrics": self.metrics,
            "topics": self.topics,
            "score": round(self.score, 4),
            "breakdown": {k: round(v, 4) for k, v in (self.breakdown or {}).items()},
            "extra": self.extra,
            "sources": self.sources,
            "corroboration": self.corroboration,
            "cross_group": self.cross_group,
            "group": self.group,
            "cadence": self.cadence,
        }


# ------------------------------------------------------------------ 解析器

def parse_feed(text: str):
    """RSS 2.0 / Atom 通用解析。

    正文取值优先级：content:encoded > content > description/summary。
    实测结论：很多源 description 只有一句话，但 content:encoded 里有全文。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return None, f"XML 解析失败: {e}"
    items = []
    for node in root.iter():
        if localname(node.tag) not in ("item", "entry"):
            continue
        title = link = date = guid = ""
        bodies = {}
        for ch in node:
            ln = localname(ch.tag)
            if ln == "title":
                title = strip_html(ch.text or "")
            elif ln == "link":
                link = link or (ch.text or "").strip() or ch.attrib.get("href", "")
            elif ln == "guid":
                guid = guid or (ch.text or "").strip()
            elif ln in ("pubdate", "published", "updated", "date", "dc:date"):
                date = date or (ch.text or "").strip()
            elif ln in ("encoded", "content", "summary", "description"):
                bodies[ln] = strip_html(ch.text or "")
        body = ""
        for pref in ("encoded", "content", "summary", "description"):
            if bodies.get(pref):
                body = bodies[pref]
                break
        if title or link:
            items.append({"title": title, "link": link, "date": date,
                          "body": body, "guid": guid})
    return items, None


def _arxiv_items(res, src):
    raw, err = parse_feed(res.text)
    if raw is None:
        return [], [err]
    out, warns = [], []
    for r in raw:
        aid = arxiv_id_from(r["link"]) or arxiv_id_from(r.get("guid", ""))
        # announce_type: new=首次发布, cross=跨类列表, replace=替换版本
        atype = ""
        m = re.search(r"Announce Type:\s*(\w+)", r["body"] or "")
        if m:
            atype = m.group(1).lower()
        if not atype:
            atype = "new"
        body = re.sub(r"^arXiv:\S+\s*Announce Type:\s*\w+\s*", "", r["body"] or "").strip()
        body = re.sub(r"^Abstract:\s*", "", body).strip()
        out.append(Item(
            title=r["title"], url=r["link"] or f"https://arxiv.org/abs/{aid}",
            body=body, published=parse_date(r["date"]), source_id=src["id"],
            source_name=src["name"], weight=src.get("weight", 1.0),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
            announce_type=atype, canonical_id=aid,
        ))
    return out, warns


def _arxiv_api_items(res, src):
    """arXiv 官方 API（Atom）。作为 RSS 的兜底路线。

    为什么要兜底：rss.arxiv.org 在每天的公告生成之前会返回
    HTTP 200 + 合法 XML + 0 条 item —— 不报错、不超时，就是没有内容。
    而 arXiv 是本项目最大的信息源，静默变空等于当天报告失去一大半素材。
    """
    raw, err = parse_feed(res.text)
    if raw is None:
        return [], [err]
    out = []
    for r in raw:
        aid = arxiv_id_from(r["link"]) or arxiv_id_from(r.get("guid", ""))
        body = re.sub(r"^Abstract:\s*", "", (r["body"] or "")).strip()
        out.append(Item(
            title=r["title"],
            url=r["link"] or f"https://arxiv.org/abs/{aid}",
            body=body, published=parse_date(r["date"]),
            source_id=src["id"], source_name=src["name"],
            weight=src.get("weight", 1.0),
            category_hint=src.get("category_hint", ""),
            announce_type="api-new", canonical_id=aid,
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
        ))
    return out, []


def _rss_items(res, src):
    raw, err = parse_feed(res.text)
    if raw is None:
        return [], [err]
    out = []
    for r in raw:
        out.append(Item(
            title=r["title"], url=r["link"], body=r["body"],
            published=parse_date(r["date"]), source_id=src["id"],
            source_name=src["name"], weight=src.get("weight", 0.5),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
        ))
    return out, []


def _hf_daily(res, src):
    data = res.json
    if not isinstance(data, list):
        return [], ["HuggingFace daily_papers 返回结构不是 list"]
    out = []
    for e in data:
        paper = e.get("paper") or {}
        aid = paper.get("id") or e.get("id") or ""
        title = paper.get("title") or e.get("title") or ""
        summary = paper.get("summary") or e.get("summary") or ""
        pub = (paper.get("submittedOnDailyAt") or paper.get("publishedAt")
               or e.get("publishedAt"))
        # HF 的 publishedAt 常比 daily 早几天，用 daily 提交时间更贴近"今天的新东西"
        pub = pub or e.get("publishedAt")
        out.append(Item(
            title=title, url=f"https://huggingface.co/papers/{aid}",
            body=summary, published=parse_date(pub), source_id=src["id"],
            source_name=src["name"], weight=src.get("weight", 1.0),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
            metrics={"upvotes": e.get("upvotes") or paper.get("upvotes") or 0,
                     "numComments": e.get("numComments") or 0},
            canonical_id=aid,
            extra={"githubRepo": paper.get("githubRepo") or e.get("githubRepo") or "",
                   "projectPage": paper.get("projectPage") or e.get("projectPage") or ""},
        ))
    return out, []


def _hf_models(res, src):
    data = res.json
    if not isinstance(data, list):
        return [], ["HuggingFace models 返回结构不是 list"]
    out, skipped = [], 0
    for m in data:
        mid = m.get("modelId") or m.get("id") or ""
        if not mid:
            continue
        if is_repackaged(mid):
            skipped += 1
            continue
        pid = (m.get("pipeline_tag") or "").lower()
        if any(n in mid.lower() for n in MODEL_NOISE):
            skipped += 1
            continue
        created = parse_date(m.get("createdAt") or "")
        out.append(Item(
            title=mid, url=f"https://huggingface.co/{mid}",
            body=(f"开源模型 · 类型 {pid or '未知'} · 库 "
                  f"{m.get('library_name') or '未知'} · 创建于 "
                  f"{(m.get('createdAt') or '')[:10]}"),
            published=created, source_id=src["id"], source_name=src["name"],
            weight=src.get("weight", 0.7),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
            metrics={"likes": m.get("likes") or 0,
                     "downloads": m.get("downloads") or 0,
                     "trendingScore": m.get("trendingScore") or 0},
            canonical_id=mid,
            extra={"pipeline_tag": pid, "library": m.get("library_name") or "",
                   "tags": (m.get("tags") or [])[:12]},
        ))
    warns = []
    if skipped:
        warns.append(f"过滤掉 {skipped} 个量化/再打包/示例仓库")
    return out, warns


def _github_search(res, src, now):
    data = res.json
    items = data.get("items") if isinstance(data, dict) else None
    if items is None:
        return [], ["GitHub search 返回结构异常"]
    out = []
    for r in items:
        try:
            stars = int(r.get("stargazers_count") or 0)
        except Exception:
            stars = 0
        full = r.get("full_name") or ""
        desc = (r.get("description") or "").strip()
        lang = r.get("language") or ""
        topics = r.get("topics") or []
        body = desc
        meta = [f"★{stars}"]
        if lang:
            meta.append(lang)
        if topics:
            meta.append("标签 " + ", ".join(topics[:6]))
        if meta:
            body = (body + " · " if body else "") + " · ".join(meta)
        out.append(Item(
            title=full, url=r.get("html_url") or f"https://github.com/{full}",
            body=body, published=parse_date(r.get("created_at") or ""),
            source_id=src["id"], source_name=src["name"],
            weight=src.get("weight", 0.7),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
            metrics={"stars": stars, "forks": r.get("forks_count") or 0},
            canonical_id=full,
            extra={"language": lang, "topics": topics[:10],
                   "pushed_at": r.get("pushed_at") or ""},
        ))
    return out, []


def _hn_search(res, src):
    data = res.json
    hits = data.get("hits") if isinstance(data, dict) else None
    if hits is None:
        return [], ["HN 返回结构异常"]
    out = []
    for h in hits:
        title = strip_html(h.get("title") or "")
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        pts = h.get("points") or 0
        nc = h.get("num_comments") or 0
        body = f"Hacker News 讨论 · {pts} 分 · {nc} 条评论"
        out.append(Item(
            title=title, url=url, body=body,
            published=parse_date(h.get("created_at") or ""),
            source_id=src["id"], source_name=src["name"],
            weight=src.get("weight", 0.6),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
            metrics={"points": pts, "num_comments": nc},
            canonical_id=str(h.get("objectID") or ""),
            extra={"hn_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                   "author": h.get("author") or ""},
        ))
    return out, []


def _html_links(res, src):
    html = res.text
    pat = src.get("link_pattern")
    if not pat:
        return [], ["html_links 源缺少 link_pattern"]
    try:
        found = re.findall(pat, html)
    except re.error as e:
        return [], [f"link_pattern 非法: {e}"]
    base = src.get("base", "")
    seen, out = set(), []
    for m in found:
        href = m if isinstance(m, str) else (m[0] if m else "")
        if not href:
            continue
        url = href if href.startswith("http") else base.rstrip("/") + "/" + href.lstrip("/")
        url = normalize_url(url)
        if url in seen:
            continue
        seen.add(url)
        title = slug_to_title(url)
        if not title:
            continue
        out.append(Item(
            title=title, url=url, body="",
            published=None, source_id=src["id"], source_name=src["name"],
            weight=src.get("weight", 1.0),
            category_hint=src.get("category_hint", ""),
            group=src.get("group", ""),
            cadence=src.get("cadence", "news"),
            canonical_id=url,
        ))
    return out, []


# ------------------------------------------------------------------ 调度

PARSERS = {
    "arxiv_rss": _arxiv_items,
    "arxiv_api": _arxiv_api_items,
    "rss": _rss_items,
    "hf_daily": _hf_daily,
    "hf_models": _hf_models,
    "html_links": _html_links,
}

NEEDS_NOW = {"github_search", "hn_search"}


def parse(res, src, now: datetime):
    """把一次抓取结果变成 (items, warnings)。任何异常都转成 warning。"""
    if not res.ok:
        return [], [f"抓取失败: {res.error}"]
    kind = src.get("kind")
    try:
        if kind == "github_search":
            return _github_search(res, src, now)
        if kind == "hn_search":
            return _hn_search(res, src)
        fn = PARSERS.get(kind)
        if fn is None:
            return [], [f"未知源类型: {kind}"]
        return fn(res, src)
    except json.JSONDecodeError as e:
        return [], [f"JSON 解析失败: {e}"]
    except ET.ParseError as e:
        # 实测：这台机器的 VPN 做 TLS 中间人，偶尔会把响应体截断或塞入脏字节，
        # 表现为 "not well-formed (invalid token): line 1, column 0"。
        # 同一个网址重试一次通常就好 —— 这不是源坏了，是链路坏了。
        # 所以报错文案必须区分这两种情况，否则会误判成"源失效"而错删一个好源。
        head = (res.text or "")[:80].replace("\n", " ")
        return [], [f"XML 解析失败（疑似链路损坏，非源失效）: {e}；响应开头={head!r}"]
    except Exception as e:
        return [], [f"解析异常 {type(e).__name__}: {e}"]
