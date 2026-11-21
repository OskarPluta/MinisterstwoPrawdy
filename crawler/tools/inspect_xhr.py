#!/usr/bin/env python3
"""Narzędzie diagnostyczne (nie część crawlera): ładuje stronę w Playwright z uczciwym UA
(po sprawdzeniu robots.txt) i wypisuje żądania XHR/fetch, które wykonuje jej JavaScript.
Służy do opisania, skąd aplikacje JS (np. gov.pl/web/gov/ostrzezenia) biorą dane.

    CRAWLER_CONTACT=mail@example.pl python tools/inspect_xhr.py URL [URL...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from faktbot.config import load_config  # noqa: E402
from faktbot.fetcher import Fetcher, RobotsBlocked  # noqa: E402
from faktbot.storage import Store  # noqa: E402


def main(urls: list[str]) -> None:
    cfg = load_config(Path(__file__).resolve().parent.parent / "sources.yaml")
    import tempfile
    store = Store(Path(tempfile.mkdtemp(prefix="inspect-")))
    f = Fetcher(cfg.settings, store, cfg.user_agent())
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for url in urls:
            print(f"\n=== {url}")
            try:
                f.check_allowed(url)
            except RobotsBlocked as e:
                print("  robots.txt:", e)
                continue
            f._wait_turn(url.split("/")[2])
            ctx = browser.new_context(user_agent=f.ua, locale="pl-PL")
            page = ctx.new_page()
            page.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "font", "media")
                       else r.continue_())
            seen = []

            def on_response(resp):
                req = resp.request
                if req.resource_type in ("xhr", "fetch", "document", "eventsource", "websocket"):
                    ct = resp.headers.get("content-type", "")
                    size = None
                    sample = ""
                    if "json" in ct:
                        try:
                            body = resp.body()
                            size = len(body)
                            sample = body[:300].decode("utf-8", "replace").replace("\n", " ")
                        except Exception:
                            pass
                    seen.append({"type": req.resource_type, "method": req.method, "status": resp.status,
                                 "url": resp.url, "ct": ct.split(";")[0], "size": size, "sample": sample})
            page.on("response", on_response)
            try:
                page.goto(url, wait_until="networkidle", timeout=40000)
            except Exception as e:
                print("  goto:", type(e).__name__, str(e)[:200])
            time.sleep(2)
            for s in seen:
                print(f"  [{s['type']:8}] {s['method']} {s['status']} {s['ct']:24} {s['url']}")
                if s["sample"]:
                    print(f"             {s['size']} B: {s['sample'][:250]}")
            text = page.inner_text("body")[:1500].replace("\n", " | ")
            print("  TEKST:", text)
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
