#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 FnStyle 自动更新检查（GET /api/version）。

直接驱动 app/ui/index.cgi，用本地 HTTP 服务模拟三种场景：
  ① 远程版本高于本地   → updateAvailable=true，latest 与 downloadUrl/changelog 透传
  ② 远程版本等于本地   → updateAvailable=false
  ③ 远程不可达（网络失败）→ 静默降级，updateAvailable=false，不报错、不崩溃
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CGI = os.path.join(ROOT, "app", "ui", "index.cgi")

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (("  —— " + detail) if detail else ""))


class Srv(BaseHTTPRequestHandler):
    payload = b"{}"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(Srv.payload)

    def log_message(self, *a, **k):
        pass


def free_port():
    s = HTTPServer(("127.0.0.1", 0), Srv)
    port = s.server_address[1]
    s.server_close()
    return port


def run_cgi(version_url, api_url):
    """调用 index.cgi 的 /api/version，返回解析后的 JSON（失败返回 None）。"""
    env = dict(os.environ)
    env.update({
        "REQUEST_METHOD": "GET",
        "REQUEST_URI": "/api/version",
        "QUERY_STRING": "",
        "PATH_INFO": "/api/version",
        "SCRIPT_NAME": "/cgi/ThirdParty/fnstyle/index.cgi",
        "TRIM_DATA_SHARE_PATHS": "/tmp/fnstyle_vercheck_share",
        "FNSTYLE_VERSION_URL": version_url,
        "FNSTYLE_VERSION_API": api_url,
    })
    # 保证 curl/python3 在 PATH 里
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    os.makedirs("/tmp/fnstyle_vercheck_share/deskdata", exist_ok=True)
    p = subprocess.run(
        ["/bin/bash", CGI],
        env=env,
        capture_output=True,
        timeout=60,
    )
    out = p.stdout.decode("utf-8", "replace")
    # 取最后一个空行之后的内容作为 body
    if "\r\n\r\n" in out:
        body = out.split("\r\n\r\n", 1)[1]
    elif "\n\n" in out:
        body = out.split("\n\n", 1)[1]
    else:
        body = out
    body = body.strip()
    try:
        return json.loads(body)
    except Exception:
        return None


def main():
    if not os.path.isfile(CGI):
        print("✗ 找不到 CGI: " + CGI)
        sys.exit(1)

    # 读取本地当前版本（从 CGI 的 APP_VERSION 常量）
    cur = "unknown"
    with open(CGI, encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith('APP_VERSION='):
                cur = line.strip().split("=", 1)[1].strip().strip('"')
                break
    print("本地安装版本 APP_VERSION = %s\n" % cur)

    # ---------- 场景 ①：远程版本更高 ----------
    print("[场景1] 远程版本 1.2.0（高于本地）")
    remote = {
        "version": "1.2.0",
        "releaseDate": "2026-12-01",
        "downloadUrl": "https://example.com/fnstyle_1.2.0_all.fpk",
        "changelog": "测试变更说明",
    }
    Srv.payload = json.dumps(remote).encode("utf-8")
    port = free_port()
    httpd = HTTPServer(("127.0.0.1", port), Srv)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        r = run_cgi("http://127.0.0.1:%d/version.json" % port,
                    "http://127.0.0.1:9/")
    finally:
        httpd.shutdown()
    rec("返回合法 JSON 且含 updateAvailable", isinstance(r, dict) and "updateAvailable" in r,
        str(r)[:120] if r is not None else "解析失败")
    rec("updateAvailable == true", bool(r and r.get("updateAvailable")))
    rec("latest == 1.2.0", bool(r and r.get("latest") == "1.2.0"),
        "latest=%s" % (r or {}).get("latest"))
    rec("current == %s" % cur, bool(r and r.get("current") == cur),
        "current=%s" % (r or {}).get("current"))
    rec("downloadUrl 透传", bool(r and r.get("downloadUrl") == remote["downloadUrl"]))
    rec("changelog 透传", bool(r and r.get("changelog") == remote["changelog"]))
    rec("releaseDate 透传", bool(r and r.get("releaseDate") == remote["releaseDate"]))

    # ---------- 场景 ②：远程版本等于本地 ----------
    print("\n[场景2] 远程版本 %s（等于本地）" % cur)
    Srv.payload = json.dumps({
        "version": cur,
        "downloadUrl": "https://example.com/x.fpk",
        "changelog": "noop",
    }).encode("utf-8")
    port = free_port()
    httpd = HTTPServer(("127.0.0.1", port), Srv)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = run_cgi("http://127.0.0.1:%d/version.json" % port, "http://127.0.0.1:9/")
    finally:
        httpd.shutdown()
    rec("updateAvailable == false", bool(r and r.get("updateAvailable") is False))
    rec("latest == %s" % cur, bool(r and r.get("latest") == cur))

    # ---------- 场景 ③：网络失败（不可达） ----------
    print("\n[场景3] 远程不可达（连接被拒端口 + API 也不可达）")
    r = run_cgi("http://127.0.0.1:9/version.json", "http://127.0.0.1:9/")
    rec("未崩溃且返回合法 JSON", isinstance(r, dict), str(r)[:120] if r is not None else "解析失败")
    rec("updateAvailable == false（静默降级）", bool(r and r.get("updateAvailable") is False))
    rec("current 仍回显本地版本 %s" % cur, bool(r and r.get("current") == cur))

    # ---------- 场景 ④：GitHub Releases API 形状兜底 ----------
    print("\n[场景4] 远程为 GitHub Releases API 形状（tag_name + assets）")
    api_body = {
        "tag_name": "v1.3.0",
        "published_at": "2026-12-10T00:00:00Z",
        "body": "通过 Releases API 下发",
        "assets": [
            {"name": "readme.txt", "browser_download_url": "https://example.com/readme.txt"},
            {"name": "fnstyle_1.3.0_all.fpk", "browser_download_url": "https://example.com/fnstyle_1.3.0_all.fpk"},
        ],
    }
    Srv.payload = json.dumps(api_body).encode("utf-8")
    port = free_port()
    httpd = HTTPServer(("127.0.0.1", port), Srv)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = run_cgi("http://127.0.0.1:9/never",
                    "http://127.0.0.1:%d/releases/latest" % port)
    finally:
        httpd.shutdown()
    rec("updateAvailable == true", bool(r and r.get("updateAvailable")))
    rec("latest 解析为 1.3.0（去 v）", bool(r and r.get("latest") == "1.3.0"),
        "latest=%s" % (r or {}).get("latest"))
    rec("downloadUrl 取 .fpk 附件", bool(r and r.get("downloadUrl") ==
                                          "https://example.com/fnstyle_1.3.0_all.fpk"))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n===== 自动更新检查验证：%d/%d 通过 =====" % (passed, total))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
