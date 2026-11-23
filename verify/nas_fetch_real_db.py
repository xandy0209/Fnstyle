#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 NAS 实机只读导出 storage.db 的关键内容（取证用）。

⚠️ 这是 v1.1.83 排查「昨天/今天用电数据缺失」时用的取证通道，
   沉淀下来是为了让故障**可复现**、让结论**可核对**。

通道（全部经过 trim-cli 的 relay 通道，不依赖直连）：
   ① 用 `trim-cli docker container create` 起一个 python:3.12-alpine 容器，
      挂载宿主机的 /usr/local 与 /vol1 —— 容器内以 root 身份**只读**打开
      /usr/local/apps/@appdata/fnstyle/storage.db；
   ② 容器把导出结果写到 /vol1/1000/<文件>；
   ③ `trim-cli file download-url` + relay 代理 dump 取真实 URI 下载回本地。

用法：
    python3 verify/nas_fetch_real_db.py            # 导出到 /tmp/fnprobe_out.json
    python3 verify/nas_fetch_real_db.py --out xxx.json
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

TRIM = os.path.expanduser('~/.workbuddy/skills/fnos/scripts/trim-cli')
DB_ON_NAS = '/usr_local/apps/@appdata/fnstyle/storage.db'
WORKDIR = '/vol1/1000'
HOST_FILE = WORKDIR + '/fnprobe_out.json'

# 容器内执行的导出脚本（只读打开 db，绝不写入）
PROBE = r'''
import sqlite3, json, time, os
db = "%s"
out = {"db_path": db, "db_exists": os.path.exists(db),
       "db_size": os.path.getsize(db) if os.path.exists(db) else 0}
con = sqlite3.connect("file:%%s?mode=ro" %% db, uri=True)
cur = con.cursor()
out["meta"] = {r[0]: r[1] for r in cur.execute("SELECT key,value FROM meta").fetchall()}
out["daily_summary"] = [{"day": r[0], "metric": r[1], "value": r[2]} for r in
    cur.execute("SELECT day,metric,value FROM daily_summary ORDER BY day,metric").fetchall()]
rows = cur.execute("SELECT ts,value FROM metrics WHERE metric='power' ORDER BY ts").fetchall()
per = {}
for t, v in rows:
    d = time.strftime("%%Y-%%m-%%d", time.localtime(t))
    e = per.setdefault(d, {"n": 0, "first": t, "last": t, "sum_w": 0.0})
    e["n"] += 1
    e["last"] = t
    e["sum_w"] += (v or 0.0)
out["power_per_day"] = per
out["power_total"] = len(rows)
out["power_min_ts"] = rows[0][0] if rows else None
out["power_max_ts"] = rows[-1][0] if rows else None
pts = []
for t, v in rows:
    d = time.strftime("%%Y-%%m-%%d", time.localtime(t))
    if d in ("2026-11-22", "2026-11-23"):
        pts.append([t, v])
out["pts_1122_1123"] = pts
con.close()
open("%s", "w").write(json.dumps(out))
print("OK rows=%%d pts=%%d" %% (len(rows), len(pts)))
''' % (DB_ON_NAS, HOST_FILE)


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def download(nas_path, local_path):
    """经 relay 下载 NAS 文件（从代理 dump 取真实 URI）。"""
    dump = '/tmp/relay-dump.log'
    before = 0
    if os.path.exists(dump):
        before = os.path.getsize(dump)
    r = sh(['bash', TRIM, 'file', 'download-url', nas_path, '--profile', 'relay'])
    txt = ''
    if os.path.exists(dump):
        with open(dump, 'r', errors='replace') as f:
            f.seek(before)
            txt = f.read()
    import re
    resp = re.findall(r'=== POST /ogh/ac/w -> \d+\n(.+)', txt)
    uri = None
    for line in reversed(resp):
        m = re.findall(r'"uri":"(/download/[^"]+)"', line)
        if m:
            uri = m[-1].replace('\\u0026', '&')
            break
    if not uri:
        return False, 'relay dump 里没找到下载 URI（代理需带 dump 参数启动）'
    import urllib.request
    ctx = __import__('ssl')._create_unverified_context()
    req = urllib.request.Request('http://127.0.0.1:18087' + uri)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            data = resp.read()
    except Exception as e:
        return False, '下载失败: %s' % e
    with open(local_path, 'wb') as f:
        f.write(data)
    return True, '%d 字节' % len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='/tmp/fnprobe_out.json')
    args = ap.parse_args()

    print('① 起容器（python:3.12-alpine，挂载 /usr/local + /vol1）只读导出 db')
    b64 = base64.b64encode(PROBE.encode('utf-8')).decode()
    inner = ("echo '%s' | base64 -d > %s/fn_probe.py && "
             "python3 %s/fn_probe.py" % (b64, WORKDIR, WORKDIR))
    r = sh(['bash', TRIM, 'docker', 'container', 'create',
            '--image', 'python:3.12-alpine', '--name', 'fnprobe_cli',
            '--mount', '/usr/local:/usr_local', '--mount', '/vol1:/hostvol',
            '--cmd', 'sh', '--cmd', '-c', '--cmd', "'%s'" % inner,
            '--start', '--yes', '--profile', 'relay'])
    if 'created' not in (r.stdout or '').lower():
        print('  ✗ 容器创建失败: %s %s' % (r.stdout[-300:], r.stderr[-300:]))
        return 2
    print('  ✓ 容器已启动，等待导出…')
    time.sleep(8)

    print('② 下载导出结果')
    ok, msg = download(HOST_FILE, args.out)
    if not ok:
        print('  ✗ %s' % msg)
        return 2
    print('  ✓ 已保存 %s（%s）' % (args.out, msg))

    data = json.load(open(args.out))
    wm = data['meta'].get('daily_archived_until')
    print('\n--- 实机现状 ---')
    print('  db 大小            : %d' % data['db_size'])
    print('  daily_archived_until: %s%s' % (
        wm,
        ('  (%s)' % time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(wm))))
        if wm else ''))
    print('  daily_summary 天数 : %s'
          % sorted(set(x['day'] for x in data['daily_summary'])))
    print('  metrics.power 点数 : %d' % data['power_total'])
    print('\n接下来可跑：python3 verify/verify_real_db_recovery.py --fixture %s' % args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
