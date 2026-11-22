#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify/verify_service_start.py —— FnStyle 服务启动链路的静态回归测试

背景（v1.1.75 修复的真实故障）：
    桌面入口 502 Bad Gateway，而 9910 直连一切正常。
    根因是网关 unix socket 没建起来：
      · socket 要落在 ${APP_ROOT}/fnstyle.sock
        （= /usr/local/apps/@appcenter/fnstyle/fnstyle.sock），
        而该目录的 ACL 只授权 fnstyle 用户与 root：
            user::rwx / user:fnstyle:rwx / group::r-x / other::r-x
        管理员账号 xandy0209 没有写权限 → bind 直接
        "[Errno 13] Permission denied" → 网关连不上 → 502。
      · 触发原因：手工用普通用户执行 cmd/main start，
        服务就以那个普通用户的身份跑起来了。

    同时还有一个连带问题：CGI 的版本检查日志写死
    /tmp/fnstyle_version_check.log，该文件被别的身份先创建后，
    CGI 追加会报 Permission denied 并刷进 server.log 的 CGI stderr。

本测试做**静态断言**（不真的起服务、不碰 NAS），
锁住：
    A. cmd/main start 必须在非 root 时强制提权 / 明确报错，绝不静默低权限运行
    B. cmd/main stop 同样要能停掉 root 进程
    C. 根因注释必须在位（说明 socket 目录 ACL 与 smartctl 需要 root）
    D. index.cgi 的版本检查日志不得再写死 /tmp
    E. socket_ok() 的"真实连接"探测不能在修复中丢失（冷 socket 防线）
    F. 版本号四处一致（manifest / index.cgi / app.js / monitor_collect.py）

用法：python3 verify/verify_service_start.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "cmd", "main")
CGI = os.path.join(ROOT, "app", "ui", "index.cgi")
MANIFEST = os.path.join(ROOT, "manifest")
APPJS = os.path.join(ROOT, "app", "ui", "static", "js", "app.js")
MONCOL = os.path.join(ROOT, "app", "ui", "monitor_collect.py")

_ok = 0
_fail = 0
_failures = []


def check(name, cond, detail=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print("  PASS  %s" % name)
    else:
        _fail += 1
        _failures.append((name, detail))
        print("  FAIL  %s   %s" % (name, detail))


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def main():
    src = read(MAIN)
    cgi = read(CGI)
    manifest = read(MANIFEST)
    appjs = read(APPJS)
    moncol = read(MONCOL)

    # ---------------- A. start 必须强制 root ----------------
    print("\n[A] cmd/main start 的 root 强制")

    # A1: 存在 id -u 判定
    check("start 里有 id -u 判定", 'id -u' in src,
          "未找到 id -u，可能没做身份判断")

    # A2: 非 root 时走 sudo 提权
    #     注意正则要能跨行（代码里 id -u 与 sudo 不在同一行）
    has_sudo_elevate = bool(re.search(
        r'id\s+-u[\s\S]{0,1200}?\bsudo\b', src))
    check("非 root 分支存在 sudo 提权路径", has_sudo_elevate,
          "没找到 id -u → sudo 的提权链路")

    # A2b: 提权分支用 exec 转发（否则会留下半启动的子进程）
    check("start 的 sudo 分支用 exec 转发",
          re.search(r'exec sudo[\s\S]{0,600}?bash\s+"\$0"\s+start', src)
          is not None,
          "start 的 sudo 分支应 exec 转发")

    # A3: 提权失败时必须**明确报错**而不是静默继续
    #     关键：错误分支要 return 1
    m_err = re.search(
        r'服务必须以 root 运行[\s\S]{0,200}?return 1', src)
    check("提不了权时明确报错并 return 1", m_err is not None,
          "缺少「必须以 root 运行」的错误分支，或该分支没有 return 1")

    # A4: 必须在拉起 python 之前做身份检查
    idx_guard = src.find('服务必须以 root 运行')
    idx_launch = src.find('setsid nohup python3')
    idx_launch2 = src.find('nohup python3 "${SERVER}"')
    idx_launch = min([i for i in (idx_launch, idx_launch2) if i >= 0] or [-1])
    check("身份检查在拉起服务**之前**",
          idx_guard > 0 and idx_launch > 0 and idx_guard < idx_launch,
          "guard=%d launch=%d" % (idx_guard, idx_launch))

    # A5: 根因注释必须写明 socket 目录 ACL
    check("注释写明 socket 目录只授权 fnstyle/root",
          'user:fnstyle:rwx' in src or 'ACL' in src,
          "缺少 ACL 根因说明，后人容易再踩")

    # A6: 注释写明 smartctl 需要 root（降权会丢 SMART 数据）
    check("注释写明 smartctl 需要 root",
          'smartctl' in src and '/dev/sda' in src,
          "缺少 smartctl 需要 root 的说明")

    # ---------------- B. stop 也要能停 root 进程 ----------------
    print("\n[B] cmd/main stop 的提权")

    # ⚠️ 不用正则去抠函数体 —— 函数体里嵌套着 if/fi 的 } 会被提前截断。
    #    改成按行扫描，从 "stop_server() {" 开始到第一个**顶格**的 "}" 为止。
    lines = src.split("\n")
    stop_body = ""
    in_fn = False
    for ln in lines:
        if not in_fn:
            if re.match(r'^\s*stop_server\s*\(\)\s*\{', ln):
                in_fn = True
            continue
        if re.match(r'^\}', ln):          # 顶格 } = 函数结束
            break
        stop_body += ln + "\n"
    check("找到 stop_server 函数体", bool(stop_body.strip()),
          "未找到 stop_server")
    check("stop 里也有 root 提权",
          'id -u' in stop_body and 'sudo' in stop_body,
          "stop 没有提权 → 普通用户停不掉 root 进程")
    check("stop 提权用 exec 转发（避免递归起子进程残留）",
          re.search(r'exec sudo[\s\S]{0,200}?\bstop\b', stop_body) is not None,
          "stop 的 sudo 分支应 exec 转发")

    # ---------------- C. socket_ok 冷 socket 防线不能丢 ----------------
    print("\n[C] 冷 socket 防线（socket_ok 真实连接探测）")
    check("socket_ok 函数仍在", 'socket_ok()' in src, "socket_ok 丢了")
    check("socket_ok 用真实 connect 探测（不是只判 -S）",
          'connect' in src and 'AF_UNIX' in src,
          "冷 socket 防线被削弱")
    check("start 里调用了 socket_ok",
          re.search(r'start_server[\s\S]{0,2500}?socket_ok', src) is not None,
          "start 未调用 socket_ok → 冷 socket 会重现 502")

    # ---------------- D. CGI 日志不再写死 /tmp ----------------
    print("\n[D] index.cgi 版本检查日志落点")

    # D1: 不再有写死 /tmp 的重定向
    hard_tmp = re.search(r'>>\s*"?/tmp/fnstyle_version_check\.log', cgi)
    check("不再写死 >> /tmp/fnstyle_version_check.log",
          hard_tmp is None,
          "仍存在写死的 /tmp 重定向，会因属主不同报 Permission denied")

    # D2: 有可写路径兜底链
    check("有 VLOG_FILE 兜底变量", 'VLOG_FILE' in cgi, "缺少 VLOG_FILE")
    check("兜底优先应用自有数据目录 DATA_DIR",
          'DATA_DIR}/version_check.log' in cgi or
          'DATA_DIR' in cgi and 'version_check.log' in cgi,
          "没有优先写 DATA_DIR")
    check("兜底包含 /var/apps/<app>/var",
          '/var/apps/${APP_NAME}/var' in cgi,
          "缺少 pkgvar 兜底路径")

    # ---------------- E. socket 路径与 ui/config 一致 ----------------
    print("\n[E] socket 路径与 ui/config 声明一致")
    cfg = read(os.path.join(ROOT, "app", "ui", "config"))
    check("ui/config 声明了 gatewaySocket", 'gatewaySocket' in cfg, "缺少")
    check("ui/config 的 gatewayPrefix 是 /app/fnstyle",
          '"gatewayPrefix": "/app/fnstyle"' in cfg,
          "gatewayPrefix 不是 /app/fnstyle → 应用中心会整段丢弃")
    check("cmd/main 的 socket 名是 fnstyle.sock",
          'fnstyle.sock' in src, "cmd/main 未使用 fnstyle.sock")

    # ---------------- F. 版本号四处一致 ----------------
    print("\n[F] 版本号一致性")
    m_ver = re.search(r'^version\s*=\s*(\S+)', manifest, re.M)
    ver_manifest = m_ver.group(1) if m_ver else ""
    m_cgi = re.search(r'APP_VERSION="([^"]+)"', cgi)
    ver_cgi = m_cgi.group(1) if m_cgi else ""
    m_js = re.search(r"APP_VERSION\s*=\s*'([^']+)'", appjs)
    ver_js = m_js.group(1) if m_js else ""
    m_col = re.search(r'COLLECT_VERSION\s*=\s*"([^"]+)"', moncol)
    ver_col = m_col.group(1) if m_col else ""
    check("manifest / index.cgi / app.js / monitor_collect.py 版本号一致",
          ver_manifest == ver_cgi == ver_js == ver_col,
          "manifest=%s cgi=%s js=%s col=%s"
          % (ver_manifest, ver_cgi, ver_js, ver_col))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 62)
    print("verify_service_start: %d 通过 / %d 失败（共 %d 项）"
          % (_ok, _fail, _ok + _fail))
    if _failures:
        print("\n失败明细：")
        for n, d in _failures:
            print("  - %s   %s" % (n, d))
    print("=" * 62)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
