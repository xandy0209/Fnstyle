# -*- coding: utf-8 -*-
"""
验证「卸载回调不再切断应用中心请求」的两段式还原架构。

真机故障（2026-11-14）：卸载 fnstyle 时弹窗「无法卸载 fnstyle / Failed to fetch」。
根因：fnOS nginx 的 ngx_http_trim_recover 用 inotify 监控 /usr/trim/www/，
卸载回调同步写该目录 → stop nginx → 从 www.zip 重建 → start（约 2~10 秒），
应用中心这条卸载请求被切断。

本验证在沙箱里真实执行 cmd/uninstall_callback，断言：
  A. 同步阶段「零 www/ 写入」（字节 + mtime 都不变）且立即返回；
     还原源 www.zip / tow / raw 已被中和，并排程了后台任务。
  B. 后台延迟结束后 www/ 才被处理，完整还原 + 自检通过。
  C. 开关关闭时不启动任何后台任务、不碰任何文件。
  D. FNSTYLE_SYNC_RESTORE=1 同步钩子仍可用（本地验证/排障用）。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
S = Path('/tmp/fnstyle_uninstall_async')
WWW = S / 'www'
RESTORE = S / 'restore'
TOW = RESTORE / 'tow'
RAW = RESTORE / 'raw'
ZIP = RESTORE / 'www.zip'
GUARD = S / 'guard'
SHARE = S / 'share'
DATA = SHARE / 'deskdata'
CB = S / 'cb'

spec = importlib.util.spec_from_file_location(
    'vur', ROOT / 'verify/verify_uninstall_restore.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

results = []


def rec(group, item, ok, detail=''):
    results.append({'group': group, 'item': item,
                    'status': 'PASS' if ok else 'FAIL',
                    'detail': str(detail)[:200]})
    print('  %s [%s] %-48s %s' % (
        '✓' if ok else '✗', group, item, detail if not ok else ''))


def build_state(auto_restore=None):
    if S.exists():
        shutil.rmtree(str(S))
    for p in (WWW, WWW / 'assets', WWW / 'deskdata/fnstyle',
              TOW, TOW / 'assets', TOW / 'deskdata/fnstyle',
              RAW / 'assets', GUARD, DATA, CB,
              S / 'var', S / 'target'):
        p.mkdir(parents=True, exist_ok=True)
    # fnOS 官方出厂副本
    (RAW / 'index.html').write_bytes(V.NATIVE_HTML)
    (RAW / 'assets/index-abc.js').write_bytes(V.NATIVE_BUNDLE)
    (RAW / 'fndesk.js').write_bytes(V.NATIVE_FNDESK)
    # 三处「应用已生效」状态
    for base in (WWW, TOW):
        (base / 'index.html').write_bytes(V.APPLIED_HTML)
        (base / 'assets/index-abc.js').write_bytes(V.APPLIED_BUNDLE)
        (base / 'fndesk.js').write_bytes(V.APPLIED_FNDESK)
        (base / 'fnstyle-login.js').write_bytes(b'/* injected */')
        (base / 'fnstyle-runtime.js').write_bytes(b'/* injected */')
        (base / 'deskdata/fnstyle.json').write_bytes(V.CFG)
        (base / 'deskdata/fnstyle/bg.png').write_bytes(b'\x89PNG')
    with zipfile.ZipFile(str(ZIP), 'w') as z:
        z.writestr('index.html', V.APPLIED_HTML)
        z.writestr('assets/', b'')
        z.writestr('assets/index-abc.js', V.APPLIED_BUNDLE)
        z.writestr('fndesk.js', V.APPLIED_FNDESK)
        z.writestr('deskdata/', b'')
        z.writestr('fnstyle-login.js', b'/* injected */')
        z.writestr('fnstyle-runtime.js', b'/* injected */')
        z.writestr('deskdata/fnstyle.json', V.CFG)
        z.writestr('deskdata/fnstyle/', b'')
        z.writestr('deskdata/fnstyle/bg.png', b'\x89PNG')
        z.writestr('locales/', b'')
    # 回调与还原资产投递到「应用安装目录」
    for name in ('uninstall_callback', 'restore_system.py',
                 'restore_deferred.sh'):
        shutil.copy2(str(ROOT / 'cmd' / name), str(CB / name))
    if auto_restore is not None:
        (DATA / 'auto_restore').write_text(auto_restore, encoding='utf-8')


def env(sync=False, delay='1'):
    e = os.environ.copy()
    e.update({
        'FNSTYLE_GUARD_DIR': str(GUARD),
        'FNSTYLE_WWW': str(WWW),
        'FNSTYLE_RESTORE_ROOT': str(RESTORE),
        'FNSTYLE_DATA_DIR': str(GUARD / 'run-data'),
        'FNSTYLE_RESTORE_DELAY': delay,
        'FNSTYLE_RESTORE_SETTLE': '0.3',
        'FNSTYLE_SKIP_KILL': '1',
        'TRIM_DATA_SHARE_PATHS': str(SHARE),
        'TRIM_PKGVAR': str(S / 'var'),
        'TRIM_APPDEST': str(S / 'target'),
    })
    if sync:
        e['FNSTYLE_SYNC_RESTORE'] = '1'
    return e


def snapshot(base):
    out = {}
    for p in sorted(base.rglob('*')):
        if p.is_file():
            out[str(p.relative_to(base))] = (p.read_bytes(), p.stat().st_mtime_ns)
    return out


def run_callback(e, timeout=40):
    t0 = time.time()
    r = subprocess.run(['bash', str(CB / 'uninstall_callback')],
                       capture_output=True, text=True, env=e, timeout=timeout)
    return r, time.time() - t0


def zip_state():
    with zipfile.ZipFile(str(ZIP)) as z:
        return {
            'cfg': z.read('deskdata/fnstyle.json'),
            'login': z.read('fnstyle-login.js'),
            'runtime': z.read('fnstyle-runtime.js'),
            'html': z.read('index.html'),
        }


def wait_pending(timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if not (GUARD / 'restore_pending').exists():
            return True
        time.sleep(0.15)
    return False


# ------------------------------------------------------------ ① 静态
def static_checks():
    uc = (ROOT / 'cmd/uninstall_callback').read_text(encoding='utf-8')
    core = (ROOT / 'cmd/restore_system.py').read_text(encoding='utf-8')
    df = (ROOT / 'cmd/restore_deferred.sh').read_text(encoding='utf-8')
    rec('static', '卸载回调同步阶段带 --skip-www',
        '--skip-www' in uc and '--settle 0' in uc)
    rec('static', '卸载回调排程 setsid/nohup 后台',
        'setsid' in uc and 'nohup' in uc and 'restore_deferred.sh' in uc)
    rec('static', '还原核心 skip_www 时不写 self.www',
        'if not self.skip_www:' in core and
        'self.neutralize_tree(self.www)' in core)
    rec('static', '后台执行器先 sleep 再写 www/',
        0 < df.find('sleep "$DELAY"') < df.find('python3 "$SCRIPT"'))
    rec('static', '安装回调同步投递后台执行器',
        'restore_deferred.sh' in (ROOT / 'cmd/install_callback').read_text(
            encoding='utf-8'))
    rp = (ROOT / 'restore-pack/cmd/install_callback').read_text(encoding='utf-8')
    rec('static', '还原工具含旧版卸载回调自愈',
        'disarm_legacy_uninstall' in rp and '.fnstyle-orig' in rp)
    rec('static', '还原工具回调同样是两段式',
        '--skip-www' in rp and 'setsid' in rp)


# ------------------------------------------------- ② A 同步阶段零 www 写入
def async_phase_a():
    build_state()
    before = snapshot(WWW)
    z_before = zip_state()
    r, elapsed = run_callback(env())
    rec('A', '卸载回调立即返回（不等待后台还原）',
        r.returncode == 0 and elapsed < 3.0,
        'rc=%s 耗时=%.2fs %s' % (r.returncode, elapsed, (r.stdout + r.stderr)[-120:]))
    after = snapshot(WWW)
    rec('A', '★ 同步阶段零 www/ 写入（字节一致）',
        {k: v[0] for k, v in before.items()} ==
        {k: v[0] for k, v in after.items()},
        '文件数 %d → %d' % (len(before), len(after)))
    rec('A', '★ 同步阶段零 www/ 写入（mtime 一致，不触发 inotify）',
        {k: v[1] for k, v in before.items()} ==
        {k: v[1] for k, v in after.items()})
    z_after = zip_state()
    rec('A', '还原源 www.zip 注入脚本已中和',
        V.is_stub(z_after['login']) and V.is_stub(z_after['runtime']))
    rec('A', '还原源 www.zip 配置已中和', V.is_neutral(z_after['cfg']))
    rec('A', '还原源 www.zip 首页恢复原生', z_after['html'] == V.NATIVE_HTML)
    rec('A', 'tow/ 注入脚本已中和',
        V.is_stub((TOW / 'fnstyle-login.js').read_bytes()))
    rec('A', 'tow/ 配置已中和',
        V.is_neutral((TOW / 'deskdata/fnstyle.json').read_bytes()))
    rec('A', '后台任务已排程（restore_pending 存在）',
        (GUARD / 'restore_pending').is_file())
    log = GUARD / 'uninstall_callback.log'
    rec('A', '回调日志明确记录「www/ 未被触碰」',
        log.is_file() and 'www/ 未被触碰' in log.read_text(errors='replace'))
    return before, z_before


# ------------------------------------------------- ③ B 延迟后完整还原
def async_phase_b(before):
    rec('B', '后台任务在延迟后完成（pending 清除）', wait_pending(45),
        'guard/restore_now.log 尾部: %s' % (
            (GUARD / 'restore_now.log').read_text(errors='replace')[-160:]
            if (GUARD / 'restore_now.log').is_file() else 'MISSING'))
    rec('B', '★ 后台阶段才写 www/（首页恢复原生）',
        (WWW / 'index.html').read_bytes() == V.NATIVE_HTML)
    rec('B', '后台阶段 assets 主包恢复原生 R3',
        V.OLD_R3 in (WWW / 'assets/index-abc.js').read_bytes())
    rec('B', '后台阶段注入脚本变为 no-op stub',
        V.is_stub((WWW / 'fnstyle-login.js').read_bytes()) and
        V.is_stub((WWW / 'fnstyle-runtime.js').read_bytes()))
    rec('B', '后台阶段配置被中和（读不出任何样式）',
        V.is_neutral((WWW / 'deskdata/fnstyle.json').read_bytes()))
    rec('B', '后台阶段自定义图片已清除',
        not (WWW / 'deskdata/fnstyle').exists())
    st = GUARD / 'restore_status.json'
    rec('B', '后台自检 restore_status.json 标记 ok',
        st.is_file() and json.loads(st.read_text(encoding='utf-8'))['ok'] is True)
    rec('B', 'www/ 确实被后台改过（与同步阶段不同）',
        {k: v[0] for k, v in snapshot(WWW).items()} !=
        {k: v[0] for k, v in before.items()})


# ------------------------------------------------- ④ C 开关关闭
def switch_off():
    build_state(auto_restore='0')
    before = snapshot(WWW)
    z_before = zip_state()
    r, elapsed = run_callback(env())
    rec('C', '关闭开关时回调立即返回', r.returncode == 0 and elapsed < 3.0,
        '耗时 %.2fs' % elapsed)
    rec('C', '关闭开关时 www/ 完全不动', snapshot(WWW) == before)
    rec('C', '关闭开关时 www.zip 完全不动', zip_state() == z_before)
    rec('C', '关闭开关时不排程后台任务',
        not (GUARD / 'restore_pending').exists())
    log = GUARD / 'uninstall_callback.log'
    rec('C', '日志记录用户关闭',
        log.is_file() and '用户已关闭自动还原' in log.read_text(errors='replace'))


# ------------------------------------------------- ⑤ D 同步钩子
def sync_hook():
    build_state()
    r, elapsed = run_callback(env(sync=True), timeout=60)
    rec('D', '同步验证钩子回调返回 0', r.returncode == 0,
        (r.stdout + r.stderr)[-160:])
    rec('D', '同步钩子下 www/ 在回调返回时已还原',
        (WWW / 'index.html').read_bytes() == V.NATIVE_HTML and
        V.is_neutral((WWW / 'deskdata/fnstyle.json').read_bytes()))
    rec('D', '同步钩子不排程后台任务',
        not (GUARD / 'restore_pending').exists())


# ------------------------------------------------- ⑥ E 旧版卸载回调自愈
def self_heal():
    build_state()
    legacy = S / 'legacy' / 'cmd'
    legacy.mkdir(parents=True, exist_ok=True)
    old = legacy / 'uninstall_callback'
    old.write_text('#!/bin/bash\n'
                   'python3 /usr/trim/www/restore_system.py --settle 5\nexit 0\n',
                   encoding='utf-8')
    rp = S / 'rp'
    rp.mkdir(exist_ok=True)
    src = (ROOT / 'restore-pack/cmd/install_callback').read_text(encoding='utf-8')
    src = src.replace('/usr/trim/share/.restore', str(RESTORE))
    src = src.replace('/usr/trim/www', str(WWW))
    cb = rp / 'install_callback'
    cb.write_text(src, encoding='utf-8')
    os.chmod(str(cb), 0o755)
    for name in ('restore_system.py', 'restore_deferred.sh'):
        shutil.copy2(str(ROOT / 'restore-pack/cmd' / name), str(rp / name))
    e = env(sync=True)
    e['FNSTYLE_TARGET_APP_DIR'] = str(S / 'legacy')
    e['FNSTYLE_SKIP_KILL'] = '1'
    r = subprocess.run(['bash', str(cb)], capture_output=True, text=True,
                       errors='replace', env=e, timeout=90)
    rec('E', '还原工具安装回调返回 0', r.returncode == 0,
        (r.stdout + r.stderr)[-160:])
    bak = legacy / 'uninstall_callback.fnstyle-orig'
    rec('E', '旧回调已备份为 .fnstyle-orig',
        bak.is_file() and 'python3 /usr/trim/www/' in bak.read_text(encoding='utf-8'))
    new = old.read_text(encoding='utf-8')
    rec('E', '旧回调被替换为安全 no-op',
        'FnStyle 系统还原工具替换为安全 no-op' in new and 'exit 0' in new)
    rec('E', '★ 新回调不再同步写 www/（不含 python3 调用）',
        'python3' not in new)
    log = GUARD / 'restore_now.log'
    rec('E', '日志记录自愈动作',
        log.is_file() and '已解除旧版卸载回调' in log.read_text(errors='replace'))
    # 幂等：再跑一次不能把 .fnstyle-orig 覆盖成 no-op
    subprocess.run(['bash', str(cb)], capture_output=True, text=True,
                  errors='replace', env=e, timeout=90)
    rec('E', '重复安装不再覆盖原始备份',
        bak.is_file() and 'python3 /usr/trim/www/' in bak.read_text(encoding='utf-8'))


def main():
    print('===== 卸载异步还原验证 =====')
    static_checks()
    print('\n===== A. 同步阶段（绝不能碰 www/）=====')
    before, _ = async_phase_a()
    print('\n===== B. 后台延迟阶段 =====')
    async_phase_b(before)
    print('\n===== C. 开关关闭 =====')
    switch_off()
    print('\n===== D. 同步验证钩子 =====')
    sync_hook()
    print('\n===== E. 旧版卸载回调自愈 =====')
    self_heal()
    passed = sum(1 for x in results if x['status'] == 'PASS')
    print('\n===== 汇总：%d/%d PASS =====' % (passed, len(results)))
    (ROOT / 'verify/result_uninstall_async.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
