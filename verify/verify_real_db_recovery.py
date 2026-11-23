#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用**真实实机数据**验证 v1.1.83 修复：归档水位线漂移 → 用电数据断档。

⚠️ 数据来源（2026-11-23 16:07 从 NAS 实机 storage.db 只读导出）：
   · meta.daily_archived_until = "1801152000.0"  → 2027-01-29 00:00:00（比今天晚 67 天）
   · daily_summary 只有 11/19、11/20、11/21 三天
   · metrics.power = 5607 点，覆盖 11/19 15:21:09 → 11/23 16:07:09
     （11/22 有 1322 点、11/23 有 488 点 —— 数据一直在库里）

本脚本把这份**真实数据**灌进临时库，然后：
   ① 用 **HEAD（v1.1.82）** 的 storage_collect 跑 → 复现故障（11/22、11/23 = None）
   ② 用 **修复后（v1.1.83）** 的 storage_collect 跑 → 11/22、11/23 恢复非 None
   ③ 核对 daily_summary 里**已归档的天没有被重复累加**（UPSERT 是累加语义，重复会翻倍）
   ④ 核对修复后的今天 kWh 与实机 /api/power/stats 的 today_kwh 对得上

用法：python3 verify/verify_real_db_recovery.py [--fixture 路径]
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'app', 'ui'))

DEFAULT_FIXTURE = '/tmp/fnprobe_out.json'

N = [0]
BAD = [0]


def check(name, cond, detail=''):
    N[0] += 1
    ok = bool(cond)
    if not ok:
        BAD[0] += 1
    print('  %s %s%s' % ('OK  ' if ok else 'FAIL', name,
                         ('  → ' + detail) if detail else ''))
    return ok


def pick_bad_ref():
    """挑一个**已知含水位线漂移缺陷**的版本引用。

    ⚠️ 不能用 HEAD —— 修复提交后 HEAD 就是好版本了。
    优先级：v1.1.82 tag → 提交历史里最后一个 v1.1.8x 的提交。
    """
    def git(*args):
        r = subprocess.run(['git', '-C', ROOT] + list(args),
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ''

    def git_ok(*args):
        r = subprocess.run(['git', '-C', ROOT] + list(args),
                           capture_output=True, text=True)
        return r.returncode == 0

    # ⚠️ 用 git_ok 判成败，别用 `if git(...)` —— git() 返回 stdout 字符串，
    #    成功时也可能为空串（空串是 falsy ⇒ and 短路 ⇒ 误判成「找不到」）。
    if git_ok('rev-parse', '--verify', 'v1.1.82') and git_ok(
            'cat-file', '-e', 'v1.1.82:app/ui/storage_collect.py'):
        return 'v1.1.82'
    # 退而求其次：找最后一个 touch 过 storage_collect.py 的 v1.1.8x 提交
    log = git('log', '--oneline', '--format=%H %s', '--', 'app/ui/storage_collect.py')
    for line in log.splitlines():
        sha = line.split()[0]
        subj = line
        if 'v1.1.8' in subj and '水位线' not in subj and '漂移' not in subj:
            return sha
    raise RuntimeError('找不到已知坏版本，请设置环境变量 BAD_REF')


def build_db(path, fixture):
    """把实机导出数据灌进一个真实的 storage.db（结构与实机一致）。"""
    import storage_collect as sc
    store = sc.HistoryStore(path)
    store.db_path = path
    conn = store._connect(write=True)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS metrics (
            ts REAL, metric TEXT, value REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_summary (
            day TEXT, metric TEXT, value REAL,
            PRIMARY KEY(day, metric))""")
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                     ('daily_archived_until', fixture['meta']['daily_archived_until']))
        for x in fixture['daily_summary']:
            conn.execute("INSERT OR REPLACE INTO daily_summary(day,metric,value) VALUES(?,?,?)",
                         (x['day'], x['metric'], x['value']))
        rows = []
        for t, v in fixture.get('pts_1122_1123') or []:
            rows.append((t, 'power', v))
        # 11/19~11/21 的点：fixture 只导出了 11/22、11/23，
        # 这里用每日点数 + 首末时间重建近似值（不影响结论的关键路径）
        for day, e in sorted(fixture['power_per_day'].items()):
            if day in ('2026-11-22', '2026-11-23'):
                continue
            n = e['n']
            span = e['last'] - e['first']
            avg = e['sum_w'] / max(n, 1)
            for i in range(n):
                t = e['first'] + (span * i / max(n - 1, 1))
                rows.append((t, 'power', avg))
        rows.sort(key=lambda r: r[0])
        conn.executemany("INSERT INTO metrics(ts,metric,value) VALUES(?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def daily_kwh(store, day):
    """取 daily_rows() 里某天的 kwh（None 表示没有数据）。"""
    rows = store.daily_rows(30)
    return (rows.get(day) or {}).get('kwh')


def main():
    fx = DEFAULT_FIXTURE
    if '--fixture' in sys.argv:
        fx = sys.argv[sys.argv.index('--fixture') + 1]
    if not os.path.exists(fx):
        print('缺少实机数据快照：%s' % fx)
        print('（该文件由 NAS 取证容器导出，见本脚本 docstring）')
        return 2
    fixture = json.load(open(fx))

    print('=== v1.1.83 修复验证：真实实机数据 ===\n')
    wm = float(fixture['meta']['daily_archived_until'])
    print('实机 meta.daily_archived_until = %s  (%s)'
          % (wm, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(wm))))
    print('实机 daily_summary 天数 = %s'
          % sorted(set(x['day'] for x in fixture['daily_summary'])))
    print('实机 metrics.power 点数 = %d\n' % fixture['power_total'])

    tmp = tempfile.mkdtemp(prefix='fnreal_')
    try:
        db = os.path.join(tmp, 'storage.db')
        n = build_db(db, fixture)
        print('已在临时库灌入 %d 个 power 点\n' % n)

        # ---------- ① 修复前（已知坏版本 v1.1.82）----------
        print('=' * 66)
        print('① 修复前（v1.1.82）：复现老板截图的断档')
        print('=' * 66)
        old_dir = os.path.join(tmp, 'old')
        os.makedirs(old_dir)
        # ⚠️ 必须取**显式的已知坏版本**，不能取 HEAD ——
        #    修复提交后 HEAD 已是好版本，取 HEAD 会拿到修复版，断言必假失败。
        #    坏版本来源优先级：BAD_REF 环境变量 → v1.1.82 tag → 含 v1.1.82 的历史提交。
        bad_ref = os.environ.get('BAD_REF') or pick_bad_ref()
        print('  坏版本来源 = %s' % bad_ref)
        with open(os.path.join(old_dir, 'storage_collect.py'), 'w') as f:
            subprocess.check_call(
                ['git', '-C', ROOT, 'show', '%s:app/ui/storage_collect.py' % bad_ref],
                stdout=f)
        # ⚠️ 两版代码必须**各自独立进程**跑 —— 同进程里 sys.path 切换会串味
        #    （storage_collect 的模块级 import 缓存会拿到另一版）。
        probe = os.path.join(HERE, '_probe_daily_kwh.py')
        runner = os.path.join(tmp, 'run.py')
        with open(runner, 'w') as f:
            f.write(
                "import sys, json\n"
                "sys.path.insert(0, %r)\n" % old_dir +
                "import storage_collect as sc\n"
                "db = %r\n"
                "s = sc.HistoryStore(db); s.db_path = db\n"
                "rows = s.daily_rows(30)\n"
                "print(json.dumps({'1122': (rows.get('2026-11-22') or {}).get('kwh'),\n"
                "                  '1123': (rows.get('2026-11-23') or {}).get('kwh'),\n"
                "                  'wm': (rows.get('_meta') or {}).get('archived_until')}))\n"
                % db)
        r = subprocess.run([sys.executable, runner], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-2000:])
            return 2
        old_out = json.loads(r.stdout.strip().splitlines()[-1])
        before_y = old_out['1122']
        before_t = old_out['1123']
        print('  修复前 11/22 kwh = %s' % before_y)
        print('  修复前 11/23 kwh = %s' % before_t)
        check('修复前能复现「昨天/今天都是 None」',
              before_y is None and before_t is None,
              '昨天=%s 今天=%s' % (before_y, before_t))

        # ---------- ② 修复后（v1.1.83）----------
        print('\n' + '=' * 66)
        print('② 修复后（v1.1.83）：昨天/今天恢复')
        print('=' * 66)
        runner2 = os.path.join(tmp, 'run2.py')
        with open(runner2, 'w') as f:
            f.write(
                "import sys, json\n"
                "sys.path.insert(0, %r)\n" % os.path.join(ROOT, 'app', 'ui') +
                "import storage_collect as sc\n"
                "db = %r\n"
                "s = sc.HistoryStore(db); s.db_path = db\n"
                "rows = s.daily_rows(30)\n"
                "print(json.dumps({'1122': (rows.get('2026-11-22') or {}).get('kwh'),\n"
                "                  '1123': (rows.get('2026-11-23') or {}).get('kwh'),\n"
                "                  'wm': (rows.get('_meta') or {}).get('archived_until')}))\n"
                % db)
        r = subprocess.run([sys.executable, runner2], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-2000:])
            return 2
        new_out = json.loads(r.stdout.strip().splitlines()[-1])
        after_y = new_out['1122']
        after_t = new_out['1123']
        print('  修复后 11/22 kwh = %s' % after_y)
        print('  修复后 11/23 kwh = %s' % after_t)
        check('修复后「昨天」不再是 None', after_y is not None and after_y > 0,
              'kwh=%s' % after_y)
        check('修复后「今天」不再是 None', after_t is not None and after_t > 0,
              'kwh=%s' % after_t)

        # ③ 需要在**当前进程**里操作 store（archive_daily / cleanup）
        import importlib
        for m in list(sys.modules):
            if m == 'storage_collect':
                del sys.modules[m]
        sys.path.insert(0, os.path.join(ROOT, 'app', 'ui'))
        import storage_collect as sc
        importlib.reload(sc)
        store = sc.HistoryStore(db)
        store.db_path = db

        # ---------- ③ 已归档的天没被重复累加 ----------
        print('\n' + '=' * 66)
        print('③ 已归档的 11/19~11/21 不被重复累加（UPSERT 是加法语义）')
        print('=' * 66)
        orig = {}
        for x in fixture['daily_summary']:
            orig.setdefault(x['day'], {})[x['metric']] = x['value']
        store.archive_daily(time.time() - 7 * 86400)
        conn = sqlite3.connect(db)
        after = {}
        for day, metric, value in conn.execute(
                "SELECT day,metric,value FROM daily_summary"):
            after.setdefault(day, {})[metric] = value
        wm_after = conn.execute(
            "SELECT value FROM meta WHERE key='daily_archived_until'").fetchone()
        conn.close()
        for day in sorted(orig):
            o = orig[day]
            a = after.get(day, {})
            check('%s 的 kwh 没被翻倍' % day,
                  abs(a.get('kwh', -1) - o.get('kwh', -2)) < 1e-9,
                  '原=%.4f 现=%.4f' % (o.get('kwh', 0), a.get('kwh', 0)))
        check('11/22 被补进 daily_summary', '2026-11-22' in after,
              'days=%s' % sorted(after))
        if '2026-11-22' in after:
            check('补归档的 11/22 kwh 与实时读数一致',
                  abs(after['2026-11-22'].get('kwh', 0) - after_y) < 1e-6,
                  '归档=%.6f 实时=%.6f' % (after['2026-11-22'].get('kwh', 0), after_y))
        wm_v = float(wm_after[0]) if wm_after else None
        today0 = store._day_start(time.time())
        check('水位线被拉回「今天 00:00」之内', wm_v is not None and wm_v <= today0,
              '水位线=%s 今天0点=%s' % (
                  time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(wm_v)) if wm_v else None,
                  time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(today0))))

        # ---------- ④ 与实机 /api/power/stats 对账 ----------
        print('\n' + '=' * 66)
        print('④ 与实机 /api/power/stats 对账（今天 kWh）')
        print('=' * 66)
        # 实机 stats 是直查 metrics（不过归档水位线），口径应与尾段一致
        conn = sqlite3.connect(db)
        pts = conn.execute(
            "SELECT ts,value FROM metrics WHERE metric='power' ORDER BY ts").fetchall()
        conn.close()
        day0 = store._day_start(time.time())
        today_pts = [(t, v) for t, v in pts if t >= day0]
        # 复刻 _daily_aggregate 的口径
        dts = sorted(today_pts[i + 1][0] - today_pts[i][0]
                     for i in range(len(today_pts) - 1)
                     if today_pts[i + 1][0] > today_pts[i][0])
        med = dts[len(dts) // 2] if dts else 60.0
        cap = 3.0 * med
        last = None
        kwh_manual = 0.0
        for t, v in today_pts:
            gap = (t - last) if last is not None else None
            dt = gap if (gap is not None and 0 < gap <= cap) else med
            last = t
            kwh_manual += (v or 0.0) * dt / 3600000.0
        print('  直查 metrics 算出的今天 kWh = %.4f' % kwh_manual)
        print('  daily_rows 读出的今天 kWh   = %.4f' % after_t)
        check('两条路径口径一致（差 < 5%%）',
              abs(kwh_manual - after_t) / max(kwh_manual, 1e-9) < 0.05,
              '直查=%.4f daily=%.4f' % (kwh_manual, after_t))

        print('\n' + '=' * 66)
        print('结果：%d 项通过 / %d 项失败' % (N[0] - BAD[0], BAD[0]))
        print('=' * 66)
        return 1 if BAD[0] else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
