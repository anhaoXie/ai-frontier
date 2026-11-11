# -*- coding: utf-8 -*-
"""sources.py 纯函数的回归测试（不联网、不读文件）。"""
from __future__ import annotations

from datetime import datetime, timezone

from src.sources import (
    Item,
    arxiv_id_from,
    fingerprint,
    is_repackaged,
    normalize_url,
    parse_date,
    parse_feed,
    slug_to_title,
    strip_html,
    to_utc,
)


class TestNormalizeUrl:
    def test_strips_tracking_params_and_fragment(self):
        got = normalize_url("https://Example.com/Post/?utm_source=x&id=7#top")
        assert got == "https://example.com/Post?id=7"

    def test_bare_domain_gets_scheme_and_slashes(self):
        # 回归：旧实现会输出 "https:example.com/post"（缺少 //，打不开）
        assert normalize_url("example.com/post") == "https://example.com/post"

    def test_bare_domain_with_query(self):
        assert normalize_url("example.com/a/b?utm_campaign=fp&id=3") == "https://example.com/a/b?id=3"

    def test_protocol_relative_url(self):
        assert normalize_url("//example.com/post") == "https://example.com/post"

    def test_relative_path_left_untouched(self):
        assert normalize_url("/relative/path") == "/relative/path"

    def test_empty(self):
        assert normalize_url("") == ""


class TestStripHtml:
    def test_tags_entities_and_whitespace(self):
        got = strip_html("<p>Hello&nbsp;<b>World</b> &amp;lt;tag&amp;gt;</p>")
        assert got == "Hello World <tag>"

    def test_script_and_style_removed(self):
        got = strip_html("<style>.x{}</style><script>bad()</script><p>ok</p>")
        assert got == "ok"


class TestParseDate:
    def test_iso_with_z(self):
        assert parse_date("2026-11-10T08:00:00Z") == datetime(2026, 11, 10, 8, tzinfo=timezone.utc)

    def test_rfc2822(self):
        assert parse_date("Tue, 10 Nov 2026 08:00:00 GMT") == datetime(2026, 11, 10, 8, tzinfo=timezone.utc)

    def test_offset_is_converted_to_utc(self):
        assert parse_date("2026-11-10 08:00:00+08:00") == datetime(2026, 11, 10, 0, tzinfo=timezone.utc)

    def test_date_only(self):
        assert parse_date("2026-11-10") == datetime(2026, 11, 10, tzinfo=timezone.utc)

    def test_invalid_and_empty(self):
        assert parse_date("not a date") is None
        assert parse_date("") is None


class TestMiscHelpers:
    def test_to_utc_assumes_utc_for_naive(self):
        got = to_utc(datetime(2026, 1, 1, 8, 0))
        assert got.tzinfo == timezone.utc
        assert got.hour == 8

    def test_slug_to_title(self):
        assert slug_to_title("https://example.com/news/some-post-title") == "Some post title"
        assert slug_to_title("https://example.com/12345") == ""

    def test_arxiv_id_from(self):
        assert arxiv_id_from("https://arxiv.org/abs/2410.12345v2") == "2410.12345"
        assert arxiv_id_from("no id here") == ""

    def test_is_repackaged(self):
        assert is_repackaged("Qwen2.5-7B-Instruct-GGUF") is True
        assert is_repackaged("Qwen2.5-7B-Instruct") is False

    def test_fingerprint_is_stable_and_distinct(self):
        a = fingerprint("https://example.com/a")
        assert a == fingerprint("https://example.com/a")
        assert len(a) == 16
        assert a != fingerprint("https://example.com/b")


class TestItem:
    def test_heat_uses_first_known_metric(self):
        it = Item(title="x", url="https://example.com/x", metrics={"points": 120, "stars": 9})
        assert it.heat == 120.0

    def test_heat_without_metrics(self):
        assert Item(title="x", url="https://example.com/x").heat == 0.0

    def test_age_hours(self):
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        it = Item(title="x", url="https://example.com/x",
                  published=datetime(2026, 11, 10, 10, tzinfo=timezone.utc))
        assert it.age_hours(now) == 2.0

    def test_uid_prefers_canonical_id(self):
        it = Item(title="x", url="https://example.com/x", source_id="arxiv", canonical_id="2410.1")
        assert it.uid == "arxiv:2410.1"

    def test_uid_falls_back_to_fingerprint(self):
        it = Item(title="x", url="https://example.com/x")
        assert it.uid == fingerprint("https://example.com/x")


class TestParseFeed:
    RSS = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Hello &amp; World</title>
        <link>https://example.com/post</link>
        <description><![CDATA[<p>Body text</p>]]></description>
        <pubDate>Tue, 10 Nov 2026 08:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    def test_rss_item_fields(self):
        items, err = parse_feed(self.RSS)
        assert err is None
        assert len(items) == 1
        assert items[0]["title"] == "Hello & World"
        assert items[0]["link"] == "https://example.com/post"
        assert items[0]["body"] == "Body text"
        assert items[0]["date"] == "Tue, 10 Nov 2026 08:00:00 GMT"

    def test_broken_xml_returns_error(self):
        items, err = parse_feed("<rss><channel>")
        assert items is None
        assert err
