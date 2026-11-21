#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在已启动的 Chrome CDP 上新建一个 page target，供 probe_* 脚本使用。

背景
----
探针脚本假设 /json/list 里已经有可用的 page target，
但有时浏览器开着却没有标签页（空列表）→ IndexError。

⚠️ v1.1.62 实测坑：`PUT /json/new?<url>` 会返回 200 并给出 targetId，
   但这个 target **不会出现在 `/json/list` 里**（它是无窗口 target），
   所以探针后面照样 IndexError。
   → 正确做法是走 **browser 级 WebSocket**：
       Target.createTarget  →  Target.attachToTarget{flatten:true}
   之后 `/json/list` 里才会出现可用的 page。

用法：
    python3 verify/cdp_open_tab.py                 # 新开一个空白 tab
    python3 verify/cdp_open_tab.py --url <url>     # 新开并导航到指定地址
"""
import argparse
import json
import urllib.parse
import urllib.request


def _browser_ws(port):
    info = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port, timeout=10).read().decode())
    return info.get("webSocketDebuggerUrl")


def _ws_call(ws_url, method, params, timeout=15):
    """用标准库实现最小 WebSocket 客户端（CDP 用的是文本帧）。"""
    import base64
    import os
    import socket
    import struct

    u = urllib.parse.urlparse(ws_url)
    host, port = u.hostname, u.port or 80
    path = u.path or "/"
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("WebSocket 握手失败")
        buf += chunk
    if b"101" not in buf.split(b"\r\n")[0]:
        raise RuntimeError("WebSocket 握手被拒: %s" % buf.split(b"\r\n")[0])

    def send_text(msg):
        data = msg.encode()
        hdr = bytearray([0x81])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        hdr += mask
        hdr += bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        sock.sendall(bytes(hdr))

    def recv_text():
        h = sock.recv(2)
        if not h:
            raise RuntimeError("WebSocket 关闭")
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", sock.recv(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", sock.recv(8))[0]
        payload = b""
        while len(payload) < ln:
            payload += sock.recv(ln - len(payload))
        return payload.decode(errors="replace")

    send_text(json.dumps({"id": 1, "method": method, "params": params}))
    while True:
        msg = json.loads(recv_text())
        if msg.get("id") == 1:
            sock.close()
            return msg
    sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="about:blank")
    ap.add_argument("--port", type=int, default=9222)
    args = ap.parse_args()

    base = "http://127.0.0.1:%d" % args.port

    # 首选：browser 级 WS → Target.createTarget（能被 /json/list 看到）
    try:
        ws = _browser_ws(args.port)
        res = _ws_call(ws, "Target.createTarget", {"url": args.url})
        tid = (res.get("result") or {}).get("targetId")
        if tid:
            import time
            for _ in range(20):
                time.sleep(0.3)
                try:
                    lst = json.loads(urllib.request.urlopen(
                        base + "/json/list", timeout=8).read().decode())
                except Exception:
                    continue
                hit = [t for t in lst
                       if t.get("id") == tid and t.get("type") == "page"]
                if hit:
                    print(json.dumps({"id": tid, "type": "page",
                                      "url": hit[0].get("url"),
                                      "via": "browser-ws"}, ensure_ascii=False))
                    return 0
            print(json.dumps({"id": tid, "type": "page",
                              "url": args.url,
                              "warn": "已创建但 /json/list 未出现"}, ensure_ascii=False))
            return 0
    except Exception as e:
        print("browser-ws 失败，尝试 HTTP 端点: %s" % e, file=__import__("sys").stderr)

    # 回退：HTTP /json/new（老版本 Chrome 只支持 GET）
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(args.url, safe=""),
        method="PUT")
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except Exception:
        req = urllib.request.Request(
            base + "/json/new?" + urllib.parse.quote(args.url, safe=""))
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    print(json.dumps({"id": data.get("id"), "type": data.get("type"),
                      "url": data.get("url"), "via": "http"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
