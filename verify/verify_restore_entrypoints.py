# -*- coding: utf-8 -*-
"""
验证「事后一键还原」的两个用户入口：
  A. restore-pack/cmd/install_callback（装 fpk 时执行）
  B. tools/restore_now.sh（SSH/终端手动执行，自包含单文件）

两者都硬编码了真机路径，这里把 /usr/trim/www 与 /usr/trim/share/.restore
文本替换为沙箱路径后执行（脚本逻辑一行未改），验证调用链能真正把系统还原。
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
S = Path('/tmp/fnstyle_restore_entry')
GUARD = S / 'fnstyle-guard'
WWW = S / 'www'
TOW = S / 'tow'
RAW = S / 'raw'

import importlib.util
spec = importlib.util.spec_from_file_location(
    'vur', ROOT / 'verify/verify_uninstall_restore.py')
V = importlib.util.module_from_spec(spec)
spec.loader.exec_module(V)

results = []


def rec(item, ok, detail=''):
    results.append({'group': 'entrypoints', 'item': item,
                    'status': 'PASS' if ok else 'FAIL', 'detail': str(detail)[:200]})
    print('  %s %-50s %s' % ('✓' if ok else '✗', item, detail if not ok else ''))


def build_state():
    if S.exists():
        shutil.rmtree(str(S))
    for p in (WWW, TOW, WWW / 'assets', WWW / 'deskdata/fnstyle',
              TOW / 'assets', TOW / 'deskdata/fnstyle',
              RAW / 'assets', GUARD):
        p.mkdir(parents=True, exist_ok=True)
    # fnOS 官方出厂副本
    (RAW / 'index.html').write_bytes(V.NATIVE_HTML)
    (RAW / 'assets/index-abc.js').write_bytes(V.NATIVE_BUNDLE)
    (RAW / 'fndesk.js').write_bytes(V.NATIVE_FNDESK)
    # 三处应用写入
    for base in (WWW, TOW):
        (base / 'index.html').write_bytes(V.APPLIED_HTML)
        (base / 'assets/index-abc.js').write_bytes(V.APPLIED_BUNDLE)
        (base / 'fndesk.js').write_bytes(V.APPLIED_FNDESK)
        (base / 'fnstyle-login.js').write_bytes(b'/* injected */')
        (base / 'fnstyle-runtime.js').write_bytes(b'/* injected */')
        (base / 'deskdata/fnstyle.json').write_bytes(V.CFG)
        (base / 'deskdata/fnstyle/bg.png').write_bytes(b'\x89PNG')
    with zipfile.ZipFile(str(S / 'www.zip'), 'w') as z:
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


def sandbox_copy(src, dst):
    """把真机路径替换成沙箱路径，保持脚本其余逻辑不变。"""
    t = src.read_text(encoding='utf-8')
    t = t.replace('/usr/trim/share/.restore', str(S))
    t = t.replace('/usr/trim/www', str(WWW))
    dst.write_text(t, encoding='utf-8')
    os.chmod(str(dst), 0o755)
    return dst


def check(tag):
    with zipfile.ZipFile(str(S / 'www.zip')) as z:
        names = z.namelist()
        rec('%s：zip 剔除应用图片条目' % tag,
            not any(n.startswith('deskdata/fnstyle/') and not n.endswith('/')
                    for n in names))
        rec('%s：zip 注入脚本已中和为 stub' % tag,
            V.is_stub(z.read('fnstyle-login.js')) and
            V.is_stub(z.read('fnstyle-runtime.js')))
        rec('%s：zip fnstyle.json 已中和为空配置' % tag,
            V.is_neutral(z.read('deskdata/fnstyle.json')))
        rec('%s：zip index.html 恢复原生' % tag, z.read('index.html') == V.NATIVE_HTML)
        rec('%s：zip assets 主包恢复原生 R3' % tag,
            V.OLD_R3 in z.read('assets/index-abc.js'))
        rec('%s：zip 保留目录条目' % tag,
            'assets/' in names and 'deskdata/' in names and 'locales/' in names)
    for base, nm in ((WWW, 'www'), (TOW, 'tow')):
        rec('%s：%s/index.html 恢复原生' % (tag, nm),
            (base / 'index.html').read_bytes() == V.NATIVE_HTML)
        rec('%s：%s/assets 主包恢复原生' % (tag, nm),
            V.OLD_R3 in (base / 'assets/index-abc.js').read_bytes())
        rec('%s：%s 注入脚本已中和' % (tag, nm),
            V.is_stub((base / 'fnstyle-login.js').read_bytes()) and
            V.is_stub((base / 'fnstyle-runtime.js').read_bytes()))
        rec('%s：%s 配置已中和' % (tag, nm),
            V.is_neutral((base / 'deskdata/fnstyle.json').read_bytes()))
        rec('%s：%s 自定义图片已清除' % (tag, nm),
            not (base / 'deskdata/fnstyle').exists())
    rec('%s：日志已落盘且自检通过' % tag,
        (GUARD / 'restore_now.log').is_file() and
        'VERIFY: PASS' in (GUARD / 'restore_now.log').read_text(errors='replace'))
    st = GUARD / 'restore_status.json'
    rec('%s：restore_status.json 标记 ok' % tag,
        st.is_file() and json.loads(st.read_text(encoding='utf-8'))['ok'] is True)


def main():
    # ---- A. 一键还原包的 install_callback ----
    print('===== A. 安装包回调 =====')
    build_state()
    cb_dir = S / 'cb'
    cb_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'restore-pack/cmd/restore_system.py',
                 cb_dir / 'restore_system.py')
    shutil.copy2(ROOT / 'restore-pack/cmd/restore_deferred.sh',
                 cb_dir / 'restore_deferred.sh')
    cb = sandbox_copy(ROOT / 'restore-pack/cmd/install_callback',
                     cb_dir / 'install_callback')
    # 生产走「同步中和源 + 后台延迟还原」两段式；沙箱里用同步钩子跑完整链路，
    # 否则回调返回时 www/ 还没被后台进程处理，无法一次性断言最终状态。
    env = os.environ.copy()
    env['FNSTYLE_SYNC_RESTORE'] = '1'
    env['FNSTYLE_SKIP_KILL'] = '1'
    # 自愈逻辑扫的是真机应用目录，沙箱里指到一个不存在的路径确保不会误改
    env['FNSTYLE_TARGET_APP_DIR'] = str(S / 'no-such-app')
    r = subprocess.run(['bash', str(cb)], capture_output=True, text=True,
                        env=env, timeout=90)
    rec('A：回调退出码 0', r.returncode == 0, (r.stdout + r.stderr)[-200:])
    check('A')

    # ---- B. 手动脚本 tools/restore_now.sh ----
    print('\n===== B. 手动脚本 =====')
    build_state()
    sh = sandbox_copy(ROOT / 'tools/restore_now.sh', S / 'restore_now.sh')
    # 脚本要求 root；沙箱里把 id -u 判断临时改成 0 以便执行（仅这一处）
    t = sh.read_text(encoding='utf-8').replace('if [ "$(id -u)" != "0" ]; then',
                                               'if false; then')
    sh.write_text(t, encoding='utf-8')
    r = subprocess.run(['bash', str(sh)], capture_output=True, text=True)
    rec('B：脚本退出码 0', r.returncode == 0, (r.stdout + r.stderr)[-200:])
    check('B')

    passed = sum(1 for x in results if x['status'] == 'PASS')
    print('\n===== 汇总：%d/%d PASS =====' % (passed, len(results)))
    (S / 'result.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
