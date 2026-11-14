#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# FN定制化配置 —— 独立 HTTP 服务
#
# 背景（2026-11-11 实测）：
#   fnOS 网关的 /cgi/ThirdParty/<pkg>/index.cgi 路径**强制校验 token**，
#   无 token 一律返回 "invalid token"（连 /app/<prefix> 走 gateway_socket 也一样）。
#   → QLink2Desktop 这类「反代到某个 URL」的桌面入口无法直接指向 fnstyle 的 CGI。
#
# 解法：
#   本服务在独立端口（默认 9910，即 manifest 里的 service_port）上直接跑
#   index.cgi，把 HTTP 请求翻译成标准 CGI 环境变量。
#   index.cgi 本身零鉴权，鉴权完全在 fnOS 网关层，所以这里不需要 token。
#
# 设计要点：
#   1. URI 原样透传给 CGI（保留 /cgi/ThirdParty/fnstyle/index.cgi/ 前缀），
#      CGI 内部自己剥前缀，前端 app.js 的相对路径 'api/' 也就自动对上。
#   2. 静态资源（/static/、/images/、/index.html）由本服务直接提供，
#      不经过 CGI（快，且 CGI 只认 /api/... 路由）。
#   3. 单进程 ThreadingHTTPServer，够用（这是配置工具，不是高并发服务）。
#   4. 不用第三方依赖，NAS 上只有 python3 标准库。
# ---------------------------------------------------------------------------
import io
import os
import posixpath
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------- 路径配置 -----------------------------
UI_DIR = os.environ.get("FNSTYLE_UI_DIR", "/var/apps/fnstyle/target/ui")
CGI_SCRIPT = os.path.join(UI_DIR, "index.cgi")
STATIC_ROOT = UI_DIR

# ------------------------- 网关短地址（/app/fnstyle） -------------------------
# fnOS 允许应用自带 HTTP 服务并注册到系统网关：应用在自己的 target 目录里监听一个
# unix socket，网关把 /app/<appname>/* 整段反代过去（鉴权由网关做）。
# Fndesk 就是这么拿到 https://host:8443/app/fndesk 的，QLink2Desktop 同理。
#
# 不注册 socket 的话只能走 /cgi/ThirdParty/<pkg>/index.cgi/ 这条 CGI 统一入口，
# 地址又长又没法改 —— 因为 /cgi 是分发器独占的前缀，应用无权改成 /fnstyle。
#
# ⚠️ 网关**不会**剥掉前缀，转过来的请求路径仍是 /app/fnstyle/api/config，
#    所以 Handler 里要自己 strip 再路由（见 _strip_gateway_prefix）。
GATEWAY_PREFIX = (os.environ.get("FNSTYLE_GATEWAY_PREFIX") or "/app/fnstyle").rstrip("/")
# 放在 target 根目录，和 Fndesk 的 /var/apps/fndesk/target/fndesk.sock 保持一致
DEFAULT_SOCK = os.path.join(os.path.dirname(UI_DIR.rstrip("/")), "fnstyle.sock")
# 默认**不**开 socket：短地址由独立的壳应用 fnstyleshort 负责（它独占 /app/fnstyle），
# 本应用若也声明同一前缀会与之冲突。只有显式 set FNSTYLE_SOCKET 时才监听。
SOCK_FILE = os.environ.get("FNSTYLE_SOCKET") or None


def _default_log_path():
    """日志默认落点：与 index.cgi 共用同一套 share 目录解析规则。

    ⚠️ 不要硬编码 /vol1 —— 不同设备的系统盘可能是 vol2/vol3。
    fnOS 安装时为 config/resource 声明的 data-share 建了软链
    /var/apps/<app>/shares/<name> -> /volN/@appshare/<app>，这个在任何设备上都成立。
    """
    for cand in ("/var/apps/fnstyle/shares/fnstyle",
                 "/var/apps/fnstyle/share/fnstyle"):
        if os.path.isdir(cand):
            return os.path.join(cand, "deskdata", "server.log")
    paths = os.environ.get("TRIM_DATA_SHARE_PATHS", "")
    if paths:
        first = paths.split(":")[0]
        if first and os.path.isdir(first):
            return os.path.join(first, "deskdata", "server.log")
    return "/vol1/@appshare/fnstyle/deskdata/server.log"


LOG_FILE = os.environ.get("FNSTYLE_SERVER_LOG") or _default_log_path()
PORT = int(os.environ.get("FNSTYLE_PORT", "9910"))
BIND = os.environ.get("FNSTYLE_BIND", "0.0.0.0")
MAX_BODY = 64 * 1024 * 1024  # 64MB，主题包上传可能较大

# CGI 前缀：CGI 内部靠它剥前缀，必须原样保留
CGI_PREFIX = "/cgi/ThirdParty/fnstyle/index.cgi"

_log_lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with _log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def guess_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".webp": "image/webp",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".txt": "text/plain; charset=utf-8",
    }.get(ext, "application/octet-stream")


def safe_join(root, url_path):
    """把 URL 路径安全地映射到 root 下的文件路径，防目录穿越。"""
    p = urllib.parse.urlsplit(url_path).path
    p = urllib.parse.unquote(p)
    p = posixpath.normpath(p)
    if p.startswith(".."):
        return None
    # normpath 之后 leading slash 已被去掉
    full = os.path.join(root, p.lstrip("/"))
    real_root = os.path.realpath(root)
    real_full = os.path.realpath(full)
    if real_full != real_root and not real_full.startswith(real_root + os.sep):
        return None
    return full


class Handler(BaseHTTPRequestHandler):
    server_version = "fnstyle/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- 基础工具 ----------------
    def log_message(self, fmt, *args):
        pass  # 自己记录

    def _strip_gateway_prefix(self):
        """剥掉网关前缀，让同一个 Handler 既伺候 9910 直连、也伺候 /app/fnstyle。

        实测 fnOS 网关转发时不剥前缀：浏览器请求 /app/fnstyle/api/config，
        应用收到的是 /app/fnstyle/api/config（可对照 Fndesk 的 access.log）。
        所以这里自己剥，下游所有路由逻辑完全不用改。
        """
        p = self.path or "/"
        pre = GATEWAY_PREFIX
        if pre and (p == pre or p.startswith(pre + "/") or p.startswith(pre + "?")):
            p = p[len(pre):] or "/"
            if not p.startswith("/"):
                p = "/" + p
            self.path = p

    def _redirect_bare_prefix(self):
        """把不带尾斜杠的 /app/fnstyle 跳到 /app/fnstyle/。

        ⚠️ 这一跳是必需的：页面用**相对路径**引资源（static/css/app.css）。
        浏览器把「/app/fnstyle」当作文件，相对路径会解析成 /app/static/...；
        只有以斜杠结尾才会解析成 /app/fnstyle/static/...。
        少了这一跳，手输短地址会打开一个没有任何样式的空壳页。
        """
        if (self.path or "") == GATEWAY_PREFIX:
            self.send_response(302)
            self.send_header("Location", GATEWAY_PREFIX + "/")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return True
        return False

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8", "replace")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > MAX_BODY:
            n = MAX_BODY
        buf = io.BytesIO()
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            buf.write(chunk)
            remaining -= len(chunk)
        return buf.getvalue()

    # ---------------- 静态文件 ----------------
    def _serve_static(self):
        """返回 True 表示已处理。"""
        path = self.path
        # 根路径 → index.html
        if path in ("/", ""):
            path = "/index.html"
        # 去掉查询串
        path = path.split("?", 1)[0]

        # QLink2Desktop 抓图标时固定请求 /favicon.ico，
        # 而本页面的图标是 images/icon_64.png —— 这里做个映射。
        if path == "/favicon.ico":
            path = "/images/icon_64.png"

        # /api/... 一律走 CGI
        if path.startswith("/api/") or path.startswith(CGI_PREFIX):
            return False

        # 兼容前端可能用的 /static/... 以及裸路径
        full = safe_join(STATIC_ROOT, path)
        if not full or not os.path.isfile(full):
            return False

        ctype = guess_type(full)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception as e:
            self._send(500, "read failed: %s" % e)
            return True

        self._send(200, data, ctype)
        return True

    # ---------------- CGI ----------------
    @staticmethod
    def _base_cgi_env():
        """CGI 进程的基础环境变量（宿主环境透传白名单）。

        ⚠️ 这里必须从 os.environ 透传，不能凭空造一个 env：
          1. index.cgi 靠 TRIM_DATA_SHARE_PATHS / shares 软链定位数据目录。
             环境被清空后它只能退化到 /vol1 —— 系统盘不是 vol1 的设备上
             独立 HTTP 服务(9910)会「桌面内嵌能用、反代入口全空」。
          2. CGI 内部要调用 python3 / cat / chmod / cp，没有 PATH 会找不到命令。
          3. LANG/TZ 影响中文输出与时间戳。
        """
        env = {}
        for k, v in os.environ.items():
            if k.startswith("TRIM_") or k.startswith("FNSTYLE_"):
                env[k] = v
        for k in ("PATH", "LANG", "LC_ALL", "LANGUAGE", "HOME", "TZ", "TMPDIR"):
            if k in os.environ:
                env[k] = os.environ[k]
        env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        return env

    def _run_cgi(self):
        body = self._read_body()
        uri = self.path
        env = self._base_cgi_env()
        env.update({
            "GATEWAY_INTERFACE": "CGI/1.1",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "SERVER_SOFTWARE": self.server_version,
            "SERVER_NAME": self.headers.get("Host", "localhost"),
            "SERVER_PORT": str(PORT),
            "REQUEST_METHOD": self.command,
            "REQUEST_URI": uri,
            "SCRIPT_NAME": CGI_PREFIX,
            "SCRIPT_FILENAME": CGI_SCRIPT,
            "PATH_INFO": "",
            "QUERY_STRING": urllib.parse.urlsplit(uri).query,
            "DOCUMENT_ROOT": STATIC_ROOT,
            "REMOTE_ADDR": self.client_address[0],
            "REMOTE_USER": os.environ.get("TRIM_USERNAME", "admin"),
            "HTTPS": "on",
            "HTTP_HOST": self.headers.get("Host", "localhost"),
            "CONTENT_LENGTH": str(len(body)),
        })
        ct = self.headers.get("Content-Type")
        if ct:
            env["CONTENT_TYPE"] = ct
        for k, v in self.headers.items():
            if k.lower() in ("cookie", "user-agent", "accept", "accept-language",
                             "referer", "origin", "x-requested-with",
                             "x-access-code", "x-device-id", "x-access-source"):
                env["HTTP_" + k.upper().replace("-", "_")] = v

        try:
            proc = subprocess.run(
                ["/bin/bash", CGI_SCRIPT],
                input=body,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            self._send(504, "CGI timeout")
            return
        except Exception as e:
            self._send(500, "CGI failed: %s" % e)
            return

        out = proc.stdout or b""
        err = proc.stderr or b""
        if err:
            log("CGI stderr: %s" % err.decode("utf-8", "replace")[:800])

        # CGI 自己输出完整 HTTP 头（Status: 200 OK\r\n...）还是裸 body？
        head, sep, rest = out.partition(b"\r\n\r\n")
        if not sep:
            head, sep, rest = out.partition(b"\n\n")
        status = 200
        headers = {}
        if sep:
            # 逐行解析所有头，不假设首行一定是 Status 或 HTTP/。
            # CGI 的头格式有三种，本次修复前只认第三种：
            #   1) "HTTP/1.1 200 OK"  -> 状态行在首行
            #   2) "Status: 200 OK"   -> 状态行在首行（CGI 规范写法）
            #   3) "Content-Type: ..." -> 首行就是普通头（index.cgi 的 / 分支就是这种）
            # 只解析第 3 种时会把首行当普通头丢掉，后面所有头也都解析不到，
            # 最终 Content-Type 丢失 -> 被降级成 text/plain。
            for line in head.split(b"\n"):
                line = line.decode("latin-1").strip()
                if not line or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k.lower() == "status":
                    # "Status: 200 OK" -> 取状态码
                    try:
                        status = int(v.split()[0])
                    except Exception:
                        status = 200
                    continue
                if k.lower() == "content-type":
                    headers[k] = v
                    continue
                if k.lower() == "content-length":
                    continue
                headers[k] = v
        else:
            rest = out

        # CGI 已经给了 Content-Length 就别重复（由 _send 统一按字节数算）
        headers.pop("Content-Length", None)

        # Content-Type 必须尊重 CGI 自己给的值。
        # 历史 bug：这里无条件 pop 掉 Content-Type，再靠 body 首字符是不是 { 或 [ 猜类型
        #   -> HTML / CSS / JS 全被降级成 text/plain，配合 nosniff 头，
        #      浏览器直接拒绝解析（页面显示成纯文本源码，CSS/JS 完全不执行）。
        #   2026-11-12 修复。
        ctype = headers.pop("Content-Type", "").strip()
        if not ctype:
            # CGI 没给 Content-Type 才兜底猜：JSON -> json，其余 -> 纯文本
            ctype = "application/json; charset=utf-8"
            try:
                txt = rest[:1].decode("utf-8")
                if txt not in ("{", "["):
                    ctype = "text/plain; charset=utf-8"
            except Exception:
                ctype = "text/plain; charset=utf-8"

        # CGI 的 Status 行形如 "Status: 200 OK"，status 已单独解析，
        # headers 里残留的 Status 不能原样透传给浏览器。
        headers.pop("Status", None)

        self._send(status, rest, ctype, headers)

    # ---------------- 动词分发 ----------------
    # 每个动词入口先剥网关前缀（/app/fnstyle），下游 _serve_static / _run_cgi 无感。
    # GET/HEAD 先做一次「裸前缀 → 带尾斜杠」的 302（见 _redirect_bare_prefix）。
    def do_GET(self):
        if self._redirect_bare_prefix():
            return
        self._strip_gateway_prefix()
        if self._serve_static():
            return
        self._run_cgi()

    def do_HEAD(self):
        if self._redirect_bare_prefix():
            return
        self._strip_gateway_prefix()
        if self._serve_static():
            return
        self._run_cgi()

    def do_POST(self):
        self._strip_gateway_prefix()
        self._run_cgi()

    def do_PUT(self):
        self._strip_gateway_prefix()
        self._run_cgi()

    def do_DELETE(self):
        self._strip_gateway_prefix()
        self._run_cgi()

    def do_OPTIONS(self):
        self._strip_gateway_prefix()
        self._send(204, b"", "text/plain",
                   {"Allow": "GET,POST,PUT,DELETE,OPTIONS"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET


class UnixServer(socketserver.ThreadingUnixStreamServer):
    """同一套 Handler 再挂到一个 unix socket 上（供 fnOS 网关反代）。

    ⚠️ 不能直接继承 http.server.HTTPServer：它的 server_bind() 里硬编码了
       AF_INET，绑不上 unix socket。所以走 socketserver 的 UnixStreamServer。
    """

    daemon_threads = True

    def get_request(self):
        req, _ = super().get_request()
        # BaseHTTPRequestHandler 会读 client_address[0]（日志/地址串），补个占位
        return req, ("127.0.0.1", 0)


def main():
    if not os.path.isfile(CGI_SCRIPT):
        log("❌ CGI 不存在: %s" % CGI_SCRIPT)
        sys.exit(1)

    srv = Server((BIND, PORT), Handler)
    log("FnStyle HTTP 服务已启动 http://%s:%d/" % (BIND, PORT))
    log("  静态根目录 : %s" % STATIC_ROOT)
    log("  CGI 脚本   : %s" % CGI_SCRIPT)

    # ---- 网关 socket（可选；默认关闭，见 SOCK_FILE 说明）----
    unix_srv = None
    if not SOCK_FILE:
        log("  网关 socket : 未启用（短地址由 fnstyleshort 壳应用提供）")
    else:
        try:
            parent = os.path.dirname(SOCK_FILE) or "."
            if not os.path.isdir(parent):
                raise OSError("父目录不存在: %s" % parent)
            # 上次异常退出会留下 socket 文件，bind 会报 Address already in use
            if os.path.exists(SOCK_FILE):
                try:
                    os.unlink(SOCK_FILE)
                except OSError:
                    pass
            unix_srv = UnixServer(SOCK_FILE, Handler)
            try:
                # 网关 worker 不是 root，必须让所有人可连（Fndesk 的 sock 也是 777）
                os.chmod(SOCK_FILE, 0o777)
            except OSError:
                pass
            threading.Thread(target=unix_srv.serve_forever, daemon=True).start()
            log("  网关 socket : %s  (前缀 %s)" % (SOCK_FILE, GATEWAY_PREFIX))
        except OSError as e:
            # 起不来不影响 9910 直连与 CGI 长地址，只是用不上短地址
            unix_srv = None
            log("  ⚠️ 网关 socket 监听失败（短地址不可用，长地址照常）: %s" % e)

    # SIGTERM 默认直接终止进程，finally 不会跑 → socket 文件会留在磁盘上，
    # 网关就会连到一个死 socket（表现为桌面入口打不开）。
    # cmd/main stop 发的正是 SIGTERM，所以必须自己转成正常退出。
    def _graceful(signum, frame):
        log("收到信号 %d，准备退出" % signum)
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _graceful)
        except (ValueError, OSError):
            pass

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log("服务已停止")
        for s in (srv, unix_srv):
            if s is not None:
                try:
                    s.server_close()
                except Exception:
                    pass
        # 进程退出后 socket 文件必须删掉，否则网关会连到一个死 socket 上
        if unix_srv is not None:
            try:
                os.unlink(SOCK_FILE)
            except OSError:
                pass


if __name__ == "__main__":
    main()
