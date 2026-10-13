# -*- coding: utf-8 -*-
"""② 模型层之外的那一层：网络访问。

这里只做一件事：把『从网上拿一份东西』变得可重试、可并发、可观测。
不做解析，不做业务判断。

为什么单独一层：这台机器上 VPN 会做 TLS 中间人，导致证书校验失败；
而且路径抖动频繁（同一网址这次超时、下次就好）。这类"环境脾气"必须
集中在一处处理，否则每个解析器都要各自写一遍重试。
"""
from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as _wait

__all__ = ["HttpClient", "FetchResult"]

_LOCK = threading.Lock()


class FetchResult:
    """一次抓取的结果。失败不抛异常，而是带着 error 返回 ——
    这样单个源失败不会炸掉整轮采集。"""

    __slots__ = ("url", "ok", "status", "body", "error", "attempts",
                 "elapsed_ms", "content_type")

    def __init__(self, url, ok, status=None, body=None, error=None,
                 attempts=0, elapsed_ms=0, content_type=""):
        self.url = url
        self.ok = ok
        self.status = status
        self.body = body
        self.error = error
        self.attempts = attempts
        self.elapsed_ms = elapsed_ms
        self.content_type = content_type or ""

    @property
    def text(self) -> str:
        if self.body is None:
            return ""
        if isinstance(self.body, str):
            return self.body
        for enc in ("utf-8", "gb18030", "latin-1"):
            try:
                return self.body.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.body.decode("utf-8", "replace")

    @property
    def json(self):
        return json.loads(self.body)

    def __repr__(self):
        state = "OK" if self.ok else f"FAIL({self.error})"
        return f"<FetchResult {state} {self.status} {self.elapsed_ms}ms {self.url[:60]}>"


class HttpClient:
    """带重试的并发抓取器。

    重试策略是刻意的：
      - 超时 / SSL 中断 / 连接重置 → 重试（VPN 路径抖动，换个时机往往就好）
      - 429 → 重试并退避（被限速）
      - 404 / 403 → 不重试（重试也没用，纯浪费时间）
    """

    RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}

    def __init__(self, cfg: dict):
        self.timeout = cfg.get("timeout_seconds", 30)
        self.retries = cfg.get("retries", 2)
        self.backoff = cfg.get("retry_backoff_seconds", 1.5)
        self.delay = cfg.get("delay_between_requests", 0.0)
        self.max_workers = cfg.get("max_workers", 8)
        self.ua = cfg.get("user_agent", "Mozilla/5.0")
        if cfg.get("insecure_tls", False):
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
            self.insecure = True
        else:
            self.ctx = ssl.create_default_context()
            self.insecure = False
        self.stats = {"requests": 0, "retries": 0, "failures": 0, "bytes": 0}

    def get(self, url, accept=None, timeout=None):
        t0 = time.time()
        last_err = None
        attempts = 0
        for i in range(self.retries + 1):
            attempts = i + 1
            if self.delay:
                time.sleep(self.delay)
            try:
                headers = {"User-Agent": self.ua,
                           "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"}
                if accept:
                    headers["Accept"] = accept
                else:
                    headers["Accept"] = ("application/rss+xml, application/atom+xml, "
                                         "application/xml, text/xml, application/json, */*")
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout or self.timeout,
                                            context=self.ctx) as r:
                    body = r.read()
                with _LOCK:
                    self.stats["requests"] += 1
                    self.stats["bytes"] += len(body)
                return FetchResult(url, True, r.status, body, None, attempts,
                                   int((time.time() - t0) * 1000),
                                   r.headers.get("Content-Type", ""))
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code not in self.RETRY_STATUS:
                    break
            except Exception as e:
                last_err = type(e).__name__
                if isinstance(e, (ValueError,)):
                    break
            if i < self.retries:
                with _LOCK:
                    self.stats["retries"] += 1
                time.sleep(self.backoff * (i + 1))
        with _LOCK:
            self.stats["failures"] += 1
        return FetchResult(url, False, None, None, last_err, attempts,
                           int((time.time() - t0) * 1000))

    def map(self, jobs, on_done=None, deadline_seconds=None):
        """jobs: [(key, url, accept)] -> {key: FetchResult}

        并发抓取。网络等待是这里最大的成本，串行跑 20 个源要几分钟，
        并发 16 个大约十几秒。

        deadline_seconds 是硬闸门：整体墙钟时间超过它，就不再等剩下的请求。
        为什么必须有：VPN 路径抖动时，个别站点会一路卡到超时，
        串行等待会把一轮采集拖成好几分钟。这里宁可丢掉一两个慢源，
        也不能让当天日报跑不出来 —— 慢源第二天自然会回来。
        """
        out = {}
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {}
            for key, url, accept in jobs:
                futs[ex.submit(self.get, url, accept)] = key
            pending = set(futs)
            while pending:
                if deadline_seconds is not None:
                    remain = deadline_seconds - (time.time() - t0)
                    if remain <= 0:
                        for fut in pending:
                            fut.cancel()
                            out[futs[fut]] = FetchResult(
                                futs[fut], False,
                                error=f"超过整体时限 {deadline_seconds}s 未完成")
                        break
                    wait = min(0.5, remain)
                else:
                    wait = 0.5
                try:
                    done, pending = _wait(pending, timeout=wait)
                except Exception:
                    break
                for fut in done:
                    key = futs[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        res = FetchResult(key, False,
                                          error=f"{type(e).__name__}: {e}")
                    out[key] = res
                    if on_done:
                        on_done(key, res)
        return out
