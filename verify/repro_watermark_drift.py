#!/usr/bin/env python3
"""复现「归档水位线失控漂移 → 用电柱状图断档」。

⚠️ 本脚本对应实机故障（2026-11-23 截图）：
    - /api/power/daily 的 11/22、11/23 返回 kwh=null
    - 但 /api/monitor/history 里 11/22 有 941 个 power 点、11/23 有 468 个
    - storage.db 的 meta.daily_archived_until = 1801152000.0（2027-01-29，未来）

根因：HistoryStore.archive_daily 的「对齐分支」把 target 推到 wm 之后，
      于是**每次调用水位线都 +1 天**，而 cutoff(now-7d) 才 +1 天/24 小时
      ⇒ 水位线以约 24 倍速飞向未来 ⇒
      daily_rows() 的实时尾段 tail_start = wm 被推到未来
      ⇒ 归档水位线之后的「今天/昨天」数据全部被跳过 ⇒ kwh=null。

用法：python3 verify/repro_watermark_drift.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app', 'ui'))

import storage_collect as sc  # noqa: E402


def day_start(ts):
    """与 storage_collect.HistoryStore._day_start 完全一致。"""
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def archive_daily_watermark(wm, cutoff, has=True):
    """按 storage_collect.archive_daily 的真实逻辑推演一次调用后的水位线。

    只复刻「水位线推进」这一段（不碰 SQLite），与线上代码逐行对应：
      1. target 向上取整到下一个自然日 00:00
      2. 老水位线 >= target ⇒ 走对齐分支，target 推到「wm 之后的下一个自然日」
      3. while wm < target: end = min(target, wm + DAILY_CHUNK)
    """
    raw = float(cutoff)
    target = day_start(raw)
    if target <= raw:
        target += 86400
    if has and wm >= target:
        nxt = day_start(wm)
        if nxt <= wm:
            nxt += 86400
        target = nxt
    guard = 0
    while wm < target and guard < 10000:
        guard += 1
        end = min(float(target), wm + sc.HistoryStore.DAILY_CHUNK)
        wm = end
    return wm


def main():
    fmt = lambda t: time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))
    fails = []

    print('=' * 74)
    print('复现：归档水位线失控漂移 → daily_energy 读不到「昨天/今天」')
    print('=' * 74)

    # ---- 场景：应用装在 11/19，之后每小时调一次 cleanup()（= archive_daily）----
    install = time.mktime((2026, 11, 19, 0, 0, 0, 0, 0, -1))
    now = install + 4 * 86400          # 11/23
    wm = day_start(now - sc.HistoryStore.DAILY_KEEP_DAYS * 86400)

    print('\n[1] 首次归档（从未归档过，wm = now - 400 天）')
    wm = archive_daily_watermark(wm, now - 7 * 86400, has=False)
    print('    wm =', fmt(wm))

    print('\n[2] 之后每小时调一次 cleanup() → archive_daily(cutoff=now-7d)')
    print('    %-4s %-22s %-22s' % ('#', 'now', '水位线 wm'))
    for i in range(1, 13):
        now += 3600
        wm = archive_daily_watermark(wm, now - 7 * 86400, has=True)
        print('    %-4d %-22s %-22s' % (i, fmt(now), fmt(wm)))
        if wm > now:
            print('    ★ 水位线已跑到「现在」之前 —— 实时尾段 tail_start = wm')
            print('      ⇒ daily_rows() 会跳过 [wm, now] 的全部点')
            break

    print('\n[3] 检查「水位线是否越过当前时刻」')
    final_now = time.time()
    wm = archive_daily_watermark(wm, final_now - 7 * 86400, has=True)
    print('    最终 wm =', fmt(wm))
    print('    当前 now =', fmt(final_now))
    if wm > final_now:
        print('    ✗✗ 水位线在未来 ⇒ 昨天的/今天的用电数据**必然**读不到')
        fails.append('水位线漂移到未来')
    else:
        print('    ✓ 水位线未越过当前时刻')

    print('\n[4] 实机对照')
    print('    实机 meta.daily_archived_until = 1801152000.0 = 2027-01-29 00:00:00')
    print('    距今约 +67 天 ⇒ 与上面的「每次调用 +1 天」模型一致')

    print('\n' + '=' * 74)
    if fails:
        print('结论：BUG 确认 —— archive_daily 的对齐分支让水位线单调 +1 天/次，')
        print('      最终跑到未来，daily_rows 的实时尾段被整体跳过。')
        print('')
        print('（本脚本是**故障复现脚本**：成功复现出 bug 即视为通过，')
        print('  所以退出码为 0。修复后请跑 verify/verify_watermark_clamp.py 验证已修好。）')
        return 0
    print('结论：未复现 —— 当前逻辑已不会让水位线漂移到未来。')
    print('（若修复生效，这里应该走到这一分支。）')
    return 1


if __name__ == '__main__':
    sys.exit(main())
