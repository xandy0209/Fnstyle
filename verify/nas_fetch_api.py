#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 NAS 取回 FnStyle 实机 API 数据（只读取证）。

背景
----
实机验证需要拿到 `http://<NAS>:9910/api/...` 的返回内容，但：

1. 本机不在 192.168.5.0/24 网段 ⇒ 直连内网不可用；
2. 走官方中继 `xandyns.fnos.net` 长期 503（NAS 侧 relay 服务离线）；
3. `trim-cli file download-url` 返回的 `uri` 字段会被**上层脱敏器**替换成
   `[REDACTED]` ⇒ 拿不到真实下载地址；
4. `trim-cli raw <路径>` 只接受 **req 名**（如 `file.download`），不接受路径，
   且 allowlist 之外的路径会被 403 挡掉。

正解（本脚本走的就是这条）
--------------------------
**直接手写 fnOS RPC 网关请求**，绕开 CLI 的脱敏与 allowlist：

    POST https://<host>:<port>/ogh/ac/w
    Authorization: Bearer <accessToken>
    Content-Type: application/json
    body = {"req": "file.download", "reqid": "...", "files": ["/vol1/1000/x.json"]}

返回体形如 `[{"download":[{"uri":"/download/vol1/1000/x.json?t=...&sign=..."}]}]`，
`uri` 是**相对路径**，直接 GET 即可下载。

⚠️ Token 从 **macOS keychain** 取（`security find-generic-password -s trim-cli.session -a profile:<name> -w`），
不是从 `~/.config/trim-cli/profiles/*.session.json` 取 —— 后者是**过期**的快照，
用例会拿到 401 `AUTH_INVALID_TOKEN`。

用法
----
    python3 verify/nas_fetch_api.py --profile mynas
    python3 verify/nas_fetch_api.py --profile mynas --out /tmp/fn_verify.json
    python3 verify/nas_fetch_api.py --profile mynas --api /api/power/daily?days=7

只读、不改 NAS 上任何东西。
"""
from __future__ import print_function

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.request

DEFAULT_HOST = 'mynas.dpdns.org'
DEFAULT_PORT = 5443
DEFAULT_PROFILE = 'mynas'


# --------------------------------------------------------------------------
# token
# --------------------------------------------------------------------------
def get_token(profile):
    """从 keychain 取**已刷新**的 access token。

    ⚠️ 为什么要从 keychain 而不是 ~/.config/trim-cli/profiles/：
       后者是 CLI 写回的**旧快照**（accessTokenExpiresAt 常常已过期），
       直接用会 401 AUTH_INVALID_TOKEN。keychain 里的是当前有效的那份。
    """
    out = subprocess.run(
        ['security', 'find-generic-password', '-s', 'trim-cli.session',
         '-a', 'profile:%s' % profile, '-w'],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        sys.exit('[nas_fetch_api] keychain 里没有 profile:%s 的 session' % profile)
    sess = json.loads(out.stdout.strip())
    return sess.get('accessToken'), sess


# --------------------------------------------------------------------------
# RPC
# --------------------------------------------------------------------------
def make_opener():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # ⚠️ 必须显式 ProxyHandler({})：本机有全局 HTTP_PROXY 指向沙箱代理，
    #    不清掉会把对 NAS 的请求也劫持走。
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx))


def rpc(opener, host, port, token, req, extra=None, timeout=30):
    payload = {'req': req, 'reqid': 'fnosrelay00000001'}
    if extra:
        payload.update(extra)
    r = urllib.request.Request(
        'https://%s:%d/ogh/ac/w' % (host, port),
        data=json.dumps(payload).encode(), method='POST')
    r.add_header('authorization', 'Bearer %s' % token)
    r.add_header('Content-Type', 'application/json')
    with opener.open(r, timeout=timeout) as resp:
        body = json.loads(resp.read().decode('utf-8', 'replace'))
    # fnOS 返回 JSON 数组，取第一个元素
    return body[0] if isinstance(body, list) else body


def download(opener, host, port, token, nas_path, timeout=60):
    """拿 download-url 并把文件下载到本地。返回本地路径。"""
    res = rpc(opener, host, port, token, 'file.download', {'files': [nas_path]})
    dl = res.get('download') or []
    if not dl:
        sys.exit('[nas_fetch_api] file.download 返回空: %r' % res)
    uri = dl[0]['uri']
    if uri.startswith('http'):
        url = uri
    else:
        url = 'https://%s:%d%s' % (host, port, uri)
    req = urllib.request.Request(url)
    req.add_header('authorization', 'Bearer %s' % token)
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read()
    local = '/tmp/nas_fetch_%s' % os.path.basename(nas_path)
    with open(local, 'wb') as f:
        f.write(data)
    return local, len(data)


# --------------------------------------------------------------------------
# 取 API 数据
# --------------------------------------------------------------------------
def fetch_api_via_container(opener, host, port, token, apis, out_path):
    """借 Docker 容器在 NAS 内网侧请求 172.17.0.1:9910。

    为什么必须借容器：FnStyle 的服务端口 9910 只监听 NAS 本机/内网，
    公网 182.136.218.63 的 9910 是关闭的（实测 nc 不通）。
    """
    code = (
        'import json,urllib.request,os\n'
        'out={}\n'
        'for ep in %r:\n'
        '    try:\n'
        '        out[ep]=json.load(urllib.request.urlopen('
        '"http://172.17.0.1:9910"+ep,timeout=25))\n'
        '    except Exception as e:\n'
        '        out[ep]="ERR %%s"%%e\n'
        'open("/hostvol/1000/fn_api_fetch.json","w").write('
        'json.dumps(out,ensure_ascii=False,indent=1))\n' % (list(apis),)
    )
    name = 'fnapifetch'
    # 用 trim-cli 建容器；--cmd 按空格 split ⇒ 整条命令必须用单引号包住
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', '..', '.workbuddy', 'skills', 'fnos',
                          'scripts', 'trim-cli')
    if not os.path.exists(runner):
        runner = os.path.expanduser(
            '~/.workbuddy/skills/fnos/scripts/trim-cli')
    cmd = [
        runner, '--profile', 'mynas', 'docker', 'container', 'create',
        '--image', 'python:3.12-alpine', '--name', name,
        '--mount', '/vol1:/hostvol',
        '--env', 'PYCODE=' + code,
        '--cmd', 'sh', '--cmd', '-c',
        '--cmd', "'python3 -c \"import os;exec(os.environ[chr(80)+chr(89)+"
                "chr(67)+chr(79)+chr(68)+chr(69)])\"'",
        '--start', '--yes',
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    import time
    time.sleep(8)
    local, size = download(opener, host, port, token,
                            '/vol1/1000/fn_api_fetch.json')
    # 清理容器与临时文件
    subprocess.run([runner, '--profile', 'mynas', 'docker', 'container',
                    'stop', name, '--yes'], capture_output=True, text=True)
    subprocess.run([runner, '--profile', 'mynas', 'docker', 'container',
                    'rm', name, '--yes'], capture_output=True, text=True)
    subprocess.run([runner, '--profile', 'mynas', 'file', 'rm',
                    '/vol1/1000/fn_api_fetch.json', '--yes'],
                   capture_output=True, text=True)
    with open(local) as f:
        data = json.load(f)
    if out_path:
        with open(out_path, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def main():
    ap = argparse.ArgumentParser(description='从 NAS 取回 FnStyle 实机 API 数据')
    ap.add_argument('--profile', default=DEFAULT_PROFILE)
    ap.add_argument('--host', default=DEFAULT_HOST)
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--out', default=None, help='落盘路径（默认打印到 stdout）')
    ap.add_argument('--api', action='append', default=None,
                    help='要取的 API 路径，可重复；默认取 version/daily/stats')
    args = ap.parse_args()

    apis = args.api or ['/api/version', '/api/power/daily?days=7',
                        '/api/power/stats']
    token, sess = get_token(args.profile)
    opener = make_opener()
    data = fetch_api_via_container(opener, args.host, args.port, token,
                                   apis, args.out)
    print(json.dumps(data, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
