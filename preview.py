#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FnStyle 本地预览服务（开发/演示用，仅依赖 Python 3 标准库）

用途：不装到飞牛 NAS 也能完整打开并操作 FnStyle 界面 ——
      「Fnstyle个性化设置」「Fnstyle主题库」两个标签页、五个库源、
      31 个主题、库源管理/编辑/添加弹窗、主题预览、配置保存、图片上传、
      「应用到系统」按钮，全部可点可用。

与真机 CGI 的差别只有一处：
      /api/apply 在真机上会改写 /usr/trim/www/ 让样式生效；
      本地预览不触碰任何系统文件，只保存配置并如实提示「预览模式」。

运行：
      python3 preview.py                # 默认 http://127.0.0.1:8765/
      python3 preview.py --port 9000
      python3 preview.py --reset        # 清空本地预览数据，回到出厂默认

数据落点：.preview-data/（不进 fpk 包）
默认图片与壁纸：优先取 pic/ 目录，其次 app/seed/fnstyle/，两者同源。
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "app" / "ui"
SEED_DIR = ROOT / "app" / "seed"
PIC_DIR = ROOT / "pic"
DATA_DIR = ROOT / ".preview-data"
UPLOAD_DIR = DATA_DIR / "upload"
CACHE_DIR = DATA_DIR / "cache"

CONFIG_FILE = DATA_DIR / "config.json"
LIBS_FILE = DATA_DIR / "theme_libs.json"
THEMES_FILE = DATA_DIR / "themes.json"

SEED_CONFIG = SEED_DIR / "fnstyle.json"
SEED_LIBS = SEED_DIR / "theme_list.json"
SEED_CACHE = SEED_DIR / "theme_cache"   # 出厂内置的主题库快照（离线也能看到 31 个主题）

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


# --------------------------------------------------------------------------
# 本地数据（原子写，避免多线程下写坏 JSON）
# --------------------------------------------------------------------------
def read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(path))


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(str(tmp), str(path))


def ensure_seed_data():
    """首次运行：从 seed 复制出可编辑的预览数据，并预置默认主题库的主题。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        shutil.copyfile(str(SEED_CONFIG), str(CONFIG_FILE))
    if not LIBS_FILE.exists():
        shutil.copyfile(str(SEED_LIBS), str(LIBS_FILE))
    if not THEMES_FILE.exists():
        # 默认勾选「Fndesk 主题库」，首屏直接展示它的 31 个主题
        libs = read_json(LIBS_FILE, [])
        themes = []
        for lib in libs:
            if "fndesk" in str(lib.get("name", "")).lower() and "主题库" in str(lib.get("name", "")):
                themes = fetch_remote_json(lib["url"]) or []
                if isinstance(themes, dict):
                    themes = themes.get("themes", [])
                break
        write_json(THEMES_FILE, themes if isinstance(themes, list) else [])


def cache_key(url):
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def fetch_remote_json(url):
    """运行缓存 → 出厂内置快照 → 联网拉取 → 落缓存；失败返回 None。"""
    clean = re.sub(r"[?&]v=\d+$", "", url)
    cached = CACHE_DIR / (cache_key(clean) + ".json")
    if cached.exists():
        return read_json(cached, None)
    seeded = SEED_CACHE / (cache_key(clean) + ".json")
    if seeded.exists():
        return read_json(seeded, None)
    try:
        req = urllib.request.Request(clean, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        data = json.loads(raw.decode("utf-8"))
        write_bytes(cached, raw)
        return data
    except Exception as exc:  # 离线时保持界面可用
        print("[proxy] %s -> %s" % (clean, exc), file=sys.stderr)
        return None


def ok(msg, **extra):
    out = {"success": True, "message": msg}
    out.update(extra)
    return out


def fail(msg):
    return {"success": False, "message": msg}


def safe_name(name):
    """与真机 CGI 一致：只保留安全字符，防目录穿越。"""
    name = os.path.basename(name or "")
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)
    return name if name and name not in (".", "..") else ""


def find_resource(name):
    """上传文件 → pic/ 默认素材 → seed/fnstyle/ 兜底。"""
    if not name or ".." in name or name.startswith("/"):
        return None
    for cand in (UPLOAD_DIR / name, PIC_DIR / name, SEED_DIR / "fnstyle" / name):
        if cand.is_file():
            return cand
    return None


def lib_meta_for(url):
    """proxy-json：按 URL 去官方清单里匹配 {name,desc,from}。"""
    clean = re.sub(r"[?&]v=\d+$", "", url)
    libs = read_json(LIBS_FILE, [])
    for lib in libs:
        if str(lib.get("url", "")).strip() == clean.strip():
            return {
                "name": lib.get("name", ""),
                "desc": lib.get("desc", ""),
                "from": lib.get("from", ""),
            }
    return None


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "FnStylePreview/1.0"

    def log_message(self, fmt, *args):
        # 只打印 API 与异常，静态资源不刷屏
        if self.path.startswith("/api/") or " 4" in (fmt % args) or " 5" in (fmt % args):
            sys.stderr.write("[preview] %s %s\n" % (self.address_string(), fmt % args))

    # ---------- 响应工具 ----------
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, cache=False):
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "Not Found")
            return
        ctype = MIME.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=600" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def body_bytes(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            return self.handle_api(path[5:], query, "GET")

        rel = path.lstrip("/") or "index.html"
        if rel == "index.html":
            return self.send_file(UI_DIR / "index.html")
        # 静态资源只允许落在 app/ui/ 内
        target = (UI_DIR / rel).resolve()
        if UI_DIR.resolve() not in target.parents or not target.is_file():
            return self.send_error(404, "Not Found")
        return self.send_file(target, cache=True)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if not path.startswith("/api/"):
            return self.send_error(404, "Not Found")
        return self.handle_api(path[5:], query, "POST")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if not path.startswith("/api/"):
            return self.send_error(404, "Not Found")
        return self.handle_api(path[5:], query, "DELETE")

    # ---------- API ----------
    def handle_api(self, name, query, method):
        # ---- 个性化配置 ----
        if name == "config":
            if method == "GET":
                return self.send_json(read_json(CONFIG_FILE, {}))
            try:
                patch = json.loads(self.body_bytes().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(fail("请求体不是合法 JSON"))
            cfg = read_json(CONFIG_FILE, {})
            cfg.update(patch)
            write_json(CONFIG_FILE, cfg)
            return self.send_json(ok("配置已保存"))

        if name == "fndesk-config":
            return self.send_json({})

        if name == "apply":
            # 真机上这一步会改写 /usr/trim/www/；预览模式只如实说明，
            # 不假装已经写入 NAS，避免误导。
            return self.send_json(ok(
                "已应用到系统（本地预览模式：配置已保存，未改写 fnOS 系统文件）"
            ))

        # ---- 主题库源 ----
        if name == "theme-libs":
            if method == "GET":
                return self.send_json(read_json(LIBS_FILE, []))
            try:
                libs = json.loads(self.body_bytes().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(fail("请求体不是合法 JSON"))
            write_json(LIBS_FILE, libs)
            return self.send_json(ok("主题库源列表已保存"))

        # ---- 主题数据缓存 ----
        if name == "themes":
            if method == "GET":
                return self.send_json(read_json(THEMES_FILE, []))
            try:
                themes = json.loads(self.body_bytes().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(fail("请求体不是合法 JSON"))
            write_json(THEMES_FILE, themes)
            return self.send_json(ok("主题数据已保存"))

        # ---- 远程 JSON 代理 ----
        if name == "proxy":
            url = (query.get("url") or [""])[0]
            if not url.startswith(("http://", "https://")):
                return self.send_json(fail("缺少或非法的 url 参数"))
            data = fetch_remote_json(url)
            return self.send_json(data if data is not None else [])

        if name == "proxy-json":
            url = (query.get("url") or [""])[0]
            if not url.startswith(("http://", "https://")):
                return self.send_json(fail("缺少或非法的 url 参数"))
            data = fetch_remote_json(url)
            meta = lib_meta_for(url)
            if isinstance(data, list):
                return self.send_json({"_meta": meta, "themes": data} if meta else data)
            if isinstance(data, dict):
                data = dict(data)
                if meta:
                    data["_meta"] = meta
                return self.send_json(data)
            return self.send_json([])

        # ---- 图片/视频读取与删除 ----
        if name == "file":
            fname = (query.get("name") or [""])[0]
            target = find_resource(fname)
            if method == "DELETE":
                if target is None:
                    return self.send_json(fail("文件不存在：%s" % fname))
                try:
                    (UPLOAD_DIR / fname).unlink(missing_ok=True)
                    return self.send_json(ok("已删除"))
                except OSError as exc:
                    return self.send_json(fail("删除失败：%s" % exc))
            if target is None:
                return self.send_json(fail("文件不存在：%s" % fname))
            return self.send_file(target)

        # ---- 上传（前端用原始二进制 body，不是 multipart）----
        if name == "upload":
            field = (query.get("field") or ["loginBackground"])[0]
            original = (query.get("name") or [""])[0]
            raw = self.body_bytes()
            if not raw:
                return self.send_json(fail("上传内容为空"))
            safe = safe_name(original) or ("upload" + (Path(original).suffix or ".bin"))
            stored = "fnstyle_" + safe
            write_bytes(UPLOAD_DIR / stored, raw)
            url = "/deskdata/fnstyle/" + stored
            cfg = read_json(CONFIG_FILE, {})
            cfg[field] = url
            write_json(CONFIG_FILE, cfg)
            return self.send_json(ok("上传成功：" + stored, data={
                "field": field,
                "name": stored,
                "url": url,
                "preview": "api/file?name=" + urllib.parse.quote(stored),
            }))

        return self.send_json(fail("Not Found：" + name))


def bind_server(host, port):
    """从指定端口起向后找空闲端口，避免和别的本地服务撞车。"""
    last = OSError()
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate), Handler), candidate
        except OSError as exc:
            last = exc
    raise last


def main():
    ap = argparse.ArgumentParser(description="FnStyle 本地预览服务")
    ap.add_argument("--port", type=int, default=8765, help="起始端口（默认 8765，被占用则自动顺延）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--reset", action="store_true", help="清空预览数据，恢复出厂默认")
    args = ap.parse_args()

    if args.reset and DATA_DIR.exists():
        shutil.rmtree(str(DATA_DIR))
        print("[reset] 已清空 .preview-data/")

    ensure_seed_data()
    themes = read_json(THEMES_FILE, [])
    libs = read_json(LIBS_FILE, [])
    server, port = bind_server(args.host, args.port)
    print("FnStyle 本地预览已启动")
    print("  地址      : http://%s:%d/" % (args.host, port))
    print("  库源      : %d 个   预置主题: %d 个" % (len(libs), len(themes)))
    print("  默认素材  : pic/（loginBackground / loginLogo / deviceLogo / webFavicon / desktopWallpaper）")
    print("  预览数据  : .preview-data/   （--reset 可恢复默认）")
    print("  停止      : Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
