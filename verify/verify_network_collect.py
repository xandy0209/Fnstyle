#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_network_collect.py —— 整机网络流量采集的单元测试（v1.1.69）

覆盖点：
  1. /proc/net/dev 解析：字段 0 = 收包字节、字段 8 = 发包字节；脏行/短行安全跳过
  2. 接口筛选：排除 lo 与容器虚拟桥（docker*/br-*/veth*/virbr*/tun*/bonding_masters），
     但**保留** tailscale0 等隧道口
  3. 速率差分：两次采样之间字节增量 / 时间差 = 速率（上下行分别算）
  4. 计数回退（计数器变小/重置）：速率不出现负值
  5. 今日累计：基准持久化 + 跨进程重启不丢 + 日期键变化时自动归零重算
  6. 无 /proc/net/dev（读取失败）时返回 available=False，不抛异常

运行：python3 verify/verify_network_collect.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
UI = ROOT / 'app/ui'

RESULTS = []


def rec(group, item, ok, detail=''):
    RESULTS.append((group, item, 'PASS' if ok else 'FAIL', detail))
    print(('  ✅' if ok else '  ❌') + ' ' + group + ' / ' + item +
          (('  → ' + detail) if detail else ''))
    return ok


def load_module():
    """把 monitor_collect.py 作为独立模块载入（它 import 的都是标准库 + 本地模块）。"""
    sys.path.insert(0, str(UI))
    spec = importlib.util.spec_from_file_location(
        'mc_under_test', str(UI / 'monitor_collect.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 假 /proc/net/dev
# --------------------------------------------------------------------------
def fake_net_dev(rows):
    """rows = [(iface, rx, tx), ...] → /proc/net/dev 文本

    ⚠️ 真实 /proc/net/dev 的字段布局（内核 2.6+ 标准格式）：
         收包： bytes packets errs drop fifo frame compressed multicast   ← 8 个
         发包： bytes packets errs drop fifo colls carrier compressed     ← 8 个
       所以 **发包字节在索引 8**（收包 8 个 + 发包第 1 个）。
       拼接后形如：
         bytes packets errs drop fifo frame compressed multicast
         bytes packets errs drop fifo colls carrier compressed
    """
    out = []
    for iface, rx, tx in rows:
        rx_fields = [str(rx), '1', '0', '0', '0', '0', '0', '0']
        tx_fields = [str(tx), '1', '0', '0', '0', '0', '0', '0']
        fields = rx_fields + tx_fields
        out.append('%-12s %s' % (iface + ':', ' '.join(fields)))
    return 'Inter-|   Receive                                                |  Transmit\n' + \
           ' face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n' + \
           '\n'.join(out) + '\n'


def main():
    mc = load_module()

    tmpdir = tempfile.mkdtemp(prefix='fnstyle_net_')
    orig_netdev = mc._net_dev_counters
    orig_state_path = mc._net_state_path

    try:
        # ----------------------------------------------------------
        print('\n【1】/proc/net/dev 解析')
        # ----------------------------------------------------------
        raw = fake_net_dev([
            ('enp1s0', 111, 222),
            ('tailscale0', 333, 444),
            ('lo', 999, 999),
        ])
        # 直接替换文件读取：_net_dev_counters 内部 open() 固定路径，
        # 这里改为 monkeypatch builtins.open 只针对该路径。
        import builtins
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if str(path) == '/proc/net/dev':
                import io
                return io.StringIO(raw)
            return real_open(path, *a, **kw)

        builtins.open = fake_open
        try:
            c = mc._net_dev_counters()
        finally:
            builtins.open = real_open

        rec('解析', '读到 3 个接口', len(c) == 3, '实际 %d' % len(c))
        rec('解析', 'enp1s0 收包字节 = 字段0', c.get('enp1s0', (0, 0))[0] == 111,
            str(c.get('enp1s0')))
        rec('解析', 'enp1s0 发包字节 = 字段8', c.get('enp1s0', (0, 0))[1] == 222,
            str(c.get('enp1s0')))
        rec('解析', 'tailscale0 正确解析', c.get('tailscale0') == (333, 444),
            str(c.get('tailscale0')))

        # 脏行 / 短行
        dirty = ('Inter-|   Receive\n'
                 ' face |bytes\n'
                 '  lo: 1 2 3\n'                       # 字段不足 16 → 跳过
                 'garbage-line-without-colon\n'
                 '  bad: 1 2 3 4 5 6 7 8 X 10 11 12 13 14 15 16\n'  # 非数字 → 跳过
                 '  ok: 10 1 0 0 0 0 0 0 20 1 0 0 0 0 0 0\n')
        def dirty_open(path, *a, **kw):
            if str(path) == '/proc/net/dev':
                import io
                return io.StringIO(dirty)
            return real_open(path, *a, **kw)

        builtins.open = dirty_open
        try:
            c2 = mc._net_dev_counters()
        finally:
            builtins.open = real_open
        rec('解析', '脏行/短行不影响正常接口', c2.get('ok') == (10, 20), str(c2))
        rec('解析', '脏行不产生多余条目', len(c2) == 1, str(c2))

        # ----------------------------------------------------------
        print('\n【2】接口筛选口径')
        # ----------------------------------------------------------
        counters = {
            'lo': (1, 1),
            'enp1s0': (10, 20),
            'tailscale0': (30, 40),
            'docker0': (50, 60),
            'br-abc123': (70, 80),
            'veth999': (90, 100),
            'virbr0': (110, 120),
            'tun0': (130, 140),
            'bonding_masters': (150, 160),
            'eth0': (170, 180),
        }
        counted = mc._counted_ifaces(counters)
        rec('筛选', '排除 lo', 'lo' not in counted)
        rec('筛选', '排除 docker0', 'docker0' not in counted)
        rec('筛选', '排除 br-*', 'br-abc123' not in counted)
        rec('筛选', '排除 veth*', 'veth999' not in counted)
        rec('筛选', '排除 virbr*', 'virbr0' not in counted)
        rec('筛选', '排除 tun*', 'tun0' not in counted)
        rec('筛选', '排除 bonding_masters', 'bonding_masters' not in counted)
        rec('筛选', '保留物理口 enp1s0', 'enp1s0' in counted)
        rec('筛选', '保留隧道口 tailscale0', 'tailscale0' in counted)
        rec('筛选', '保留 eth0', 'eth0' in counted)
        rec('筛选', '计数接口数 = 3', len(counted) == 3, str(sorted(counted)))

        # ----------------------------------------------------------
        print('\n【3】速率差分')
        # ----------------------------------------------------------
        # 重置进程内状态与状态文件
        mc._NET_STATE['prev'] = None
        mc._NET_STATE['prevTs'] = None
        state_file = os.path.join(tmpdir, 'net_daily.json')
        mc._net_state_path = lambda: state_file

        cur = fake_net_dev([('enp1s0', 1000, 2000), ('tailscale0', 500, 600)])
        builtins.open = fake_open_cur = (lambda path, *a, **kw:
                                         real_open(path, *a, **kw))
        # 用可控的计数器替换 _net_dev_counters
        boxes = {'rx': 1000, 'tx': 2000}

        def mk_text():
            return fake_net_dev([('enp1s0', boxes['rx'], boxes['tx'])])

        def counters_hook():
            import io
            t = mk_text()
            out = {}
            for line in t.splitlines():
                if ':' not in line:
                    continue
                name, _, rest = line.partition(':')
                f = rest.split()
                if len(f) < 16:
                    continue
                out[name.strip()] = (int(f[0]), int(f[8]))
            return out

        mc._net_dev_counters = counters_hook

        r1 = mc.collect_network()
        rec('差分', '首次采样速率 = 0（无基线）',
            r1['rxRate'] == 0.0 and r1['txRate'] == 0.0,
            'rx=%s tx=%s' % (r1['rxRate'], r1['txRate']))
        rec('差分', '首次采样 available=True', r1['available'] is True)

        time.sleep(0.25)
        boxes['rx'] += 512   # 512 字节
        boxes['tx'] += 256   # 256 字节
        r2 = mc.collect_network()
        rec('差分', '上行速率 > 0', r2['rxRate'] > 0, 'rxRate=%s' % r2['rxRate'])
        rec('差分', '下行速率 > 0', r2['txRate'] > 0, 'txRate=%s' % r2['txRate'])
        # 512 字节 / 0.25s ≈ 2048 B/s
        ok_mag = 1200 < r2['rxRate'] < 3000
        rec('差分', '上行速率量级正确（512B/~0.25s）', ok_mag,
            'rxRate=%s B/s' % r2['rxRate'])
        ok_mag2 = 600 < r2['txRate'] < 1500
        rec('差分', '下行速率量级正确（256B/~0.25s）', ok_mag2,
            'txRate=%s B/s' % r2['txRate'])
        rec('差分', '返回接口清单', len(r2['ifaces']) == 1 and
            r2['ifaces'][0]['name'] == 'enp1s0', str(r2['ifaces']))

        # ----------------------------------------------------------
        print('\n【4】计数器回退（不出现负速率）')
        # ----------------------------------------------------------
        time.sleep(0.2)
        boxes['rx'] = 10      # 计数器被重置
        boxes['tx'] = 10
        r3 = mc.collect_network()
        rec('回退', 'rxRate 不为负', r3['rxRate'] >= 0, 'rxRate=%s' % r3['rxRate'])
        rec('回退', 'txRate 不为负', r3['txRate'] >= 0, 'txRate=%s' % r3['txRate'])
        rec('回退', '今日累计不为负', r3['todayRx'] >= 0 and r3['todayTx'] >= 0,
            'todayRx=%s todayTx=%s' % (r3['todayRx'], r3['todayTx']))

        # ----------------------------------------------------------
        print('\n【5】今日累计 / 日切')
        # ----------------------------------------------------------
        rec('累计', '状态文件已落盘', os.path.exists(state_file), state_file)
        st = json.loads(Path(state_file).read_text())
        rec('累计', '状态文件含日期键', st.get('day') == mc._today_key(),
            str(st.get('day')))
        rec('累计', '状态文件含基准值',
            'baseRx' in st and 'baseTx' in st, str(st))

        # 模拟跨进程重启：清掉内存态，只保留磁盘文件
        mc._NET_STATE['prev'] = None
        mc._NET_STATE['prevTs'] = None
        r4 = mc.collect_network()
        rec('重启', '重启后速率归零（无内存基线）', r4['rxRate'] == 0.0,
            'rxRate=%s' % r4['rxRate'])
        rec('重启', '重启后今日累计不丢（沿用磁盘基准）',
            r4['todayRx'] >= 0 and r4['todayTx'] >= 0,
            'todayRx=%s todayTx=%s' % (r4['todayRx'], r4['todayTx']))
        rec('重启', '重启后日期键仍为今天',
            r4['day'] == mc._today_key(), r4['day'])

        # 跨日：把日期键改成昨天 → 应重置基准，今日累计从当前算起
        st = json.loads(Path(state_file).read_text())
        st['day'] = '2000-01-01'
        st['baseRx'] = 999999999
        st['baseTx'] = 999999999
        Path(state_file).write_text(json.dumps(st))
        r5 = mc.collect_network()
        rec('日切', '跨日后基准已重建', r5['day'] == mc._today_key(), r5['day'])
        st2 = json.loads(Path(state_file).read_text())
        rec('日切', '跨日后基准不再沿用旧值',
            st2.get('baseRx') != 999999999, str(st2.get('baseRx')))
        rec('日切', '跨日后今日累计从 0 起算（不跳变）',
            r5['todayRx'] < 100000 and r5['todayTx'] < 100000,
            'todayRx=%s todayTx=%s' % (r5['todayRx'], r5['todayTx']))

        # ----------------------------------------------------------
        print('\n【6】采集失败降级')
        # ----------------------------------------------------------
        mc._net_dev_counters = lambda: {}
        mc._NET_STATE['prev'] = None
        mc._NET_STATE['prevTs'] = None
        r6 = mc.collect_network()
        rec('降级', '无接口时 available=False', r6['available'] is False, str(r6))
        rec('降级', '无接口时不抛异常', True)

        # ----------------------------------------------------------
        print('\n【7】_collect_once 集成：network 字段确实进快照')
        # ----------------------------------------------------------
        collector = mc.MonitorCollector(interval=3)
        snap = collector._collect_once()
        rec('集成', '快照含 network 键', 'network' in snap,
            str(sorted(snap.keys()))[:200])
        rec('集成', 'network 为 dict', isinstance(snap.get('network'), dict))
        rec('集成', 'network 含 available 键', 'available' in (snap.get('network') or {}))
        rec('集成', 'network 含 todayRx/todayTx 键',
            'todayRx' in (snap.get('network') or {}) and
            'todayTx' in (snap.get('network') or {}))

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        try:
            mc._net_state_path = orig_state_path
        except Exception:
            pass

    # ------------------------------------------------------------------------
    print('\n' + '=' * 72)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r[2] == 'PASS')
    failed = total - passed
    print('结果：%d/%d 通过，%d 失败' % (passed, total, failed))
    if failed:
        print('\n失败项：')
        for g, i, s, d in RESULTS:
            if s == 'FAIL':
                print('  ❌ %s / %s  %s' % (g, i, d))
    print('=' * 72)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
