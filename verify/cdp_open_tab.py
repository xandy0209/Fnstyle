#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在已启动的 Chrome CDP 上新建一个 page target，供 probe_* 脚本使用。

背景：探针脚本假设 /json/list 里已经有可用的 page target，
但有时浏览器开着却没有标签页（空列表）→ IndexError。

用法：
    python3 verify/cdp_open_tab.py                 # 新开一个空白 tab
    python3 verify/cdp_open_tab.py --url <url>     # 新开并导航到指定地址
"""
import argparse
import json
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="about:blank")
    ap.add_argument("--port", type=int, default=9222)
    args = ap.parse_args()

    base = "http://127.0.0.1:%d" % args.port
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(args.url, safe=""),
        method="PUT")
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except Exception:
        # 老版本 Chrome 只支持 GET /json/new
        req = urllib.request.Request(
            base + "/json/new?" + urllib.parse.quote(args.url, safe=""))
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    print(json.dumps({"id": data.get("id"), "type": data.get("type"),
                      "url": data.get("url")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import urllib.parse
    raise SystemExit(main())
