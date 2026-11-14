# -*- coding: utf-8 -*-
"""
验证卸载还原（v2 中和方案）：构造与 fnOS 真机同构的沙箱
（www / tow / www.zip / raw / guard），模拟「应用到系统」写进样式后跑
cmd/restore_system.py，逐项核对恢复原生。

v2 与 v1 的关键差别：不再「删除」应用文件，而是「中和」
  fnstyle-*.js → 无操作 stub，fnstyle.json → {"__fnstyleDisabled":true}
因为 fnOS 的 ngx_http_trim_recover 会从 www.zip 重建 www/，删掉的文件会复活。

覆盖 8 轮场景：
  1. raw 干净 + guard 完整            → 完整还原（含 assets R3 / fndesk.js）
  2. raw/guard/baseline 全丢           → 反向补丁兜底
  3. guard 基线被旧版污染              → 跳过脏候选，仍用 raw
  4. 应用已卸载、data-share 被删        → 事后补救（仅靠系统级 guard 兜底）
  5. 连一份干净原生副本都没有          → index.html 通用清理必须剥离注入标签
  6. 还原后模拟 nginx 从 www.zip 重建  → 重建出来的仍必须是原生/中和状态
  7. 还原过程中模拟后台 worker 延时写回 → 第二轮必须把样式再中和掉
  8. 出厂副本 raw/ 本身被旧版污染          → 只中和应用条目，不动原生首页

真机证据（2026-11-14 17:31）：/ 返回带 3 处注入脚本的 index.html，
/deskdata/fnstyle.json 200（含原神外链壁纸），两个注入脚本 200；
脚本 Last-Modified 相差 3 秒 = 后台复查 / nginx 重建的延时写回。
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

ROOT = Path('/Volumes/SSD-Data/trae/FnOsApp')
SCRIPT = ROOT / 'cmd/restore_system.py'
S = Path('/tmp/fnstyle_uninstall_v2')

OLD_R3 = (b'R3=e=>{let t=_?`${nk}`:`${XO} ${nk}`;'
          b'e?document.title=`${e} - ${t}`:document.title=t}')
NEW_R3 = (
    b'R3=e=>{let t=_?`${nk}`:`${XO} ${nk}`;'
    b'if(!e){document.title=t;return}'
    b'fetch("deskdata/fnstyle.json?v="+(new Date).getTime())'
    b'.then(r=>r.ok?r.json():null)'
    b'.then(c=>{document.title=(c&&c.appTitle)||(`${e} - ${t}`)})'
    b'.catch(()=>{document.title=`${e} - ${t}`})}'
)
OLD_FD = (
    'e.appTitle&&e.appTitle2&&"飞牛 fnOS"!==e.appTitle&&'
    'document.addEventListener("visibilitychange",function(){'
    'document.hidden?document.title=e.appTitle2:document.title=e.appTitle})'
).encode('utf-8')
NEW_FD = (
    'e.appTitle&&"飞牛 fnOS"!==e.appTitle&&'
    'document.addEventListener("visibilitychange",function(){'
    'document.title=e.appTitle})'
).encode('utf-8')

NATIVE_HTML = (
    '<!doctype html><html><head><title>飞牛 fnOS</title>'
    '<meta name="description" content="飞牛私有云存储系统">'
    '<link rel="icon" href="/favicon.ico">'
    '<script src="/assets/index-abc.js"></script></head>'
    '<body><div id="root"></div></body></html>'
).encode('utf-8')
APPLIED_HTML = (
    '<!doctype html><html><head><title>XandyNs - 飞牛fnOS</title>'
    '<meta name="description" content="我的 NAS">'
    '<link rel="icon" href="/deskdata/fnstyle/webFavicon.png">'
    '<script src="/assets/index-abc.js"></script>'
    '<script src="./fnstyle-runtime.js?v=abc"></script>'
    '<script src="./fnstyle-login.js?v=def"></script>'
    '<script src="./fnstyle-login.js"></script>'
    '</head><body><div id="root"></div></body></html>'
).encode('utf-8')
NATIVE_BUNDLE = b'// fnOS desktop bundle\n' + OLD_R3 + b'\nfunction boot(){}\n'
APPLIED_BUNDLE = NATIVE_BUNDLE.replace(OLD_R3, NEW_R3)
NATIVE_FNDESK = b'// fndesk\n' + OLD_FD + b'\n'
APPLIED_FNDESK = NATIVE_FNDESK.replace(OLD_FD, NEW_FD)
CFG = json.dumps({'appTitle': 'XandyNs - 飞牛fnOS',
                  'loginBackground': 'https://fndesk.imcq.top/images/theme/原神_t2.jpeg',
                  'desktopWallpaper': 'https://fndesk.imcq.top/images/theme/原神_t4.jpg'},
                 ensure_ascii=False, indent=2).encode('utf-8')
STUB_MARK = b'__FNSTYLE_RESTORED_NOOP__'
STYLE_KEYS = ('appTitle', 'appTitle2', 'desktopWallpaper', 'loginBackground',
              'loginLogo', 'deviceLogo', 'webFavicon', 'FndeskGlass',
              'Fndesk_taskbar', 'FndeskIconSort', 'fndeskClickSound',
              'fndeskLoginVW', 'fndeskLoginVH', 'loginOpacity', 'nasNameDisplay')

results = []


def rec(group, item, ok, detail=''):
    results.append({'group': group, 'item': item,
                    'status': 'PASS' if ok else 'FAIL', 'detail': str(detail)[:200]})
    print('  %s %-54s %s' % ('✓' if ok else '✗', item, detail if not ok else ''))


def is_stub(data):
    return STUB_MARK in data


def is_neutral(data):
    try:
        obj = json.loads(data.decode('utf-8', 'replace'))
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    return all(obj.get(k) in (None, '', 0, False) for k in STYLE_KEYS)


def build(round_name, raw=True, guard=True, guard_dirty=False, data_deleted=False,
          raw_polluted=False):
    """构造一套「已被应用改过」的系统目录。"""
    d = S / round_name
    if d.exists():
        shutil.rmtree(str(d))
    www = d / 'www'
    tow = d / 'tow'
    raw_dir = d / 'raw'
    guard_dir = d / 'guard'
    baseline = d / 'data/system_baseline'
    for p in (www, tow, www / 'deskdata/fnstyle', www / 'assets',
              tow / 'deskdata/fnstyle', tow / 'assets'):
        p.mkdir(parents=True, exist_ok=True)
    guard_dir.mkdir(parents=True, exist_ok=True)
    baseline.mkdir(parents=True, exist_ok=True)

    if raw:
        (raw_dir / 'assets').mkdir(parents=True, exist_ok=True)
        (raw_dir / 'index.html').write_bytes(NATIVE_HTML)
        (raw_dir / 'assets/index-abc.js').write_bytes(NATIVE_BUNDLE)
        (raw_dir / 'fndesk.js').write_bytes(NATIVE_FNDESK)
        if raw_polluted:
            (raw_dir / 'deskdata/fnstyle').mkdir(parents=True, exist_ok=True)
            (raw_dir / 'fnstyle-runtime.js').write_bytes(b'/* injected runtime */')
            (raw_dir / 'deskdata/fnstyle.json').write_bytes(CFG)
            (raw_dir / 'deskdata/fnstyle/bg.png').write_bytes(b'\x89PNG')

    if guard:
        shutil.copy2(str(SCRIPT), str(guard_dir / 'restore_system.py'))
        (guard_dir / 'assets').mkdir(exist_ok=True)
        (guard_dir / 'index.html').write_bytes(
            APPLIED_HTML if guard_dirty else NATIVE_HTML)
        (guard_dir / 'assets/index-abc.js').write_bytes(
            APPLIED_BUNDLE if guard_dirty else NATIVE_BUNDLE)
        (guard_dir / 'fndesk.js').write_bytes(
            APPLIED_FNDESK if guard_dirty else NATIVE_FNDESK)
        (guard_dir / 'manifest.json').write_text(
            json.dumps({'index_html': True, 'assets': 1, 'from_raw': raw}),
            encoding='utf-8')

    for base in (www, tow):
        (base / 'index.html').write_bytes(APPLIED_HTML)
        (base / 'assets/index-abc.js').write_bytes(APPLIED_BUNDLE)
        (base / 'fndesk.js').write_bytes(APPLIED_FNDESK)
        (base / 'fnstyle-login.js').write_bytes(b'/* injected login */')
        (base / 'fnstyle-runtime.js').write_bytes(b'/* injected runtime */')
        (base / 'deskdata/fnstyle.json').write_bytes(CFG)
        (base / 'deskdata/fnstyle/loginBackground.png').write_bytes(b'\x89PNG\r\n')
        (base / 'deskdata/fnstyle/desktopWallpaper.jpg').write_bytes(b'\xff\xd8')

    zp = d / 'www.zip'
    with zipfile.ZipFile(str(zp), 'w') as z:
        z.writestr('index.html', APPLIED_HTML)
        z.writestr('assets/', b'')
        z.writestr('assets/index-abc.js', APPLIED_BUNDLE)
        z.writestr('fndesk.js', APPLIED_FNDESK)
        z.writestr('deskdata/', b'')
        z.writestr('fnstyle-login.js', b'/* injected */')
        z.writestr('fnstyle-runtime.js', b'/* injected */')
        z.writestr('deskdata/fnstyle.json', CFG)
        z.writestr('deskdata/fnstyle/', b'')
        z.writestr('deskdata/fnstyle/loginBackground.png', b'\x89PNG')
        z.writestr('locales/zh-CN/os.json', b'{"changeThemeWallpaper":"x"}')

    (baseline / 'manifest.json').write_text(
        json.dumps({'www_zip': True, 'index_html': True, 'from_raw': raw}),
        encoding='utf-8')
    (baseline / 'index.html').write_bytes(
        APPLIED_HTML if guard_dirty else NATIVE_HTML)

    if data_deleted:
        shutil.rmtree(str(d / 'data'))
    return d


def run_restore(d, settle=0.05, verify_only=False):
    cmd = [sys.executable, str(SCRIPT),
           '--data-dir', str(d / 'data'),
           '--www', str(d / 'www'),
           '--restore', str(d),
           '--guard', str(d / 'guard'),
           '--settle', str(settle)]
    if verify_only:
        cmd.append('--verify-only')
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r, (r.stdout + '\n' + r.stderr)


def check_round(name, expect_native_html):
    d = S / name
    zp = d / 'www.zip'
    with zipfile.ZipFile(str(zp)) as z:
        names = z.namelist()
        z_html = z.read('index.html')
        z_bundle = z.read('assets/index-abc.js')
        z_fndesk = z.read('fndesk.js')
        z_login = z.read('fnstyle-login.js')
        z_runtime = z.read('fnstyle-runtime.js')
        z_cfg = z.read('deskdata/fnstyle.json')

    rec(name, 'zip 已剔除应用图片条目',
        not any(n.startswith('deskdata/fnstyle/') and not n.endswith('/')
                for n in names),
        str([n for n in names if 'fnstyle/' in n]))
    rec(name, 'zip 保留目录条目与原生资源',
        'assets/' in names and 'deskdata/' in names and
        'locales/zh-CN/os.json' in names)
    rec(name, 'zip 注入脚本已中和为 stub',
        is_stub(z_login) and is_stub(z_runtime))
    rec(name, 'zip fnstyle.json 已中和为空配置', is_neutral(z_cfg))
    rec(name, 'zip index.html 恢复原生', z_html == NATIVE_HTML if expect_native_html
        else b'fnstyle-' not in z_html)
    rec(name, 'zip assets 主包 R3 恢复原生逻辑',
        OLD_R3 in z_bundle and b'deskdata/fnstyle.json?v=' not in z_bundle)
    rec(name, 'zip fndesk.js 恢复原生双标题', OLD_FD in z_fndesk and NEW_FD not in z_fndesk)

    for base_name in ('tow', 'www'):
        base = d / base_name
        html = (base / 'index.html').read_bytes()
        bundle = (base / 'assets/index-abc.js').read_bytes()
        fd = (base / 'fndesk.js').read_bytes()
        rec(name, '%s/index.html 恢复原生' % base_name,
            html == NATIVE_HTML if expect_native_html else b'fnstyle-' not in html)
        rec(name, '%s 注入 script 标签已剥离' % base_name, b'fnstyle-' not in html)
        rec(name, '%s/assets 主包 R3 恢复' % base_name,
            OLD_R3 in bundle and b'deskdata/fnstyle.json?v=' not in bundle)
        rec(name, '%s/fndesk.js 恢复' % base_name, OLD_FD in fd and NEW_FD not in fd)
        rec(name, '%s 注入脚本已中和为 stub' % base_name,
            is_stub((base / 'fnstyle-login.js').read_bytes()) and
            is_stub((base / 'fnstyle-runtime.js').read_bytes()))
        rec(name, '%s 配置已中和' % base_name,
            is_neutral((base / 'deskdata/fnstyle.json').read_bytes()))
        rec(name, '%s 应用图片目录已清除' % base_name,
            not (base / 'deskdata/fnstyle').exists())

    rec(name, '日志同时写入 guard 与 data-share',
        (d / 'guard/uninstall_restore.log').is_file() and
        (d / 'data/uninstall_restore.log').is_file())
    log = (d / 'guard/uninstall_restore.log').read_text(errors='replace')
    rec(name, '日志含 VERIFY: PASS', 'VERIFY: PASS' in log)
    rec(name, '日志含两轮执行与 nginx 重建触发',
        '第 2 轮' in log and '触发 nginx' in log)
    rec(name, '日志无未捕获异常', '未捕获异常' not in log)
    st = d / 'guard/restore_status.json'
    rec(name, 'restore_status.json 标记 ok', st.is_file() and
        json.loads(st.read_text(encoding='utf-8'))['ok'] is True)
    rv, _ = run_restore(d, verify_only=True)
    rec(name, '--verify-only 自检通过', rv.returncode == 0)


def check_rebuilt_tree(name, base):
    """模拟 ngx_http_trim_recover：把 www.zip 解压到一个全新的 www/。"""
    rec(name, '重建后 index.html 无注入脚本',
        b'fnstyle-' not in (base / 'index.html').read_bytes())
    rec(name, '重建后 assets 主包 R3 为原生',
        OLD_R3 in (base / 'assets/index-abc.js').read_bytes())
    rec(name, '重建后注入脚本是 stub',
        is_stub((base / 'fnstyle-runtime.js').read_bytes()) and
        is_stub((base / 'fnstyle-login.js').read_bytes()))
    rec(name, '重建后配置为空（读不出任何样式）',
        is_neutral((base / 'deskdata/fnstyle.json').read_bytes()))
    # zip 里保留空的目录条目（fnOS 重建依赖显式目录项），所以只要求没有图片文件
    imgdir = base / 'deskdata/fnstyle'
    rec(name, '重建后没有自定义图片',
        not (imgdir.is_dir() and any(p.is_file() for p in imgdir.iterdir())))


def main():
    if S.exists():
        shutil.rmtree(str(S))
    S.mkdir(parents=True)

    scenarios = [
        ('round1_clean_raw_and_guard', dict(raw=True, guard=True)),
        ('round2_no_baseline_reverse_patch', dict(raw=False, guard=False)),
        ('round3_guard_polluted', dict(raw=True, guard=True, guard_dirty=True)),
        ('round4_after_uninstall_data_deleted', dict(raw=True, guard=True, data_deleted=True)),
        ('round5_no_native_copy_generic_sanitize',
         dict(raw=False, guard=False, data_deleted=True)),
    ]
    for name, kw in scenarios:
        print('\n===== %s =====' % name)
        d = build(name, **kw)
        r, out = run_restore(d)
        rec(name, '还原脚本退出码为 0', r.returncode == 0, out[-200:])
        expect_native = kw.get('raw', False) or kw.get('guard', False)
        check_round(name, expect_native)

    # ---- 轮 6：还原后模拟 nginx 从 www.zip 重建整个 www/ ----
    print('\n===== round6_nginx_rebuild_from_zip =====')
    d = build('round6_nginx_rebuild_from_zip', raw=True, guard=True)
    r, out = run_restore(d)
    rec('round6', '还原脚本退出码为 0', r.returncode == 0, out[-200:])
    rebuilt = d / 'www_rebuilt'
    with zipfile.ZipFile(str(d / 'www.zip')) as z:
        z.extractall(str(rebuilt))
    check_rebuilt_tree('round6', rebuilt)

    # ---- 轮 7：还原过程中，后台 worker 延时把样式又写回 www/ ----
    print('\n===== round7_delayed_worker_reapply =====')
    d = build('round7_delayed_worker_reapply', raw=True, guard=True)
    www = d / 'www'

    def reapply():
        try:
            (www / 'index.html').write_bytes(APPLIED_HTML)
            (www / 'assets/index-abc.js').write_bytes(APPLIED_BUNDLE)
            (www / 'fnstyle-runtime.js').write_bytes(b'/* injected runtime */')
            (www / 'fnstyle-login.js').write_bytes(b'/* injected login */')
            (www / 'deskdata/fnstyle.json').write_bytes(CFG)
        except Exception as e:
            print('  ! 模拟写回失败', e)

    timer = threading.Timer(0.45, reapply)
    timer.start()
    cmd = [sys.executable, str(SCRIPT),
           '--data-dir', str(d / 'data'), '--www', str(www),
           '--restore', str(d), '--guard', str(d / 'guard'), '--settle', '1.2']
    r = subprocess.run(cmd, capture_output=True, text=True)
    timer.join()
    rec('round7', '还原脚本退出码为 0', r.returncode == 0,
        (r.stdout + r.stderr)[-200:])
    rec('round7', '延时写回已被第二轮重新中和',
        is_stub((www / 'fnstyle-runtime.js').read_bytes()) and
        is_neutral((www / 'deskdata/fnstyle.json').read_bytes()) and
        b'fnstyle-' not in (www / 'index.html').read_bytes())
    rv, _ = run_restore(d, verify_only=True)
    rec('round7', '延时写回后整体自检仍通过', rv.returncode == 0)

    # ---- 轮 8：出厂副本 raw/ 本身被旧版污染 ----
    print('\n===== round8_factory_raw_polluted =====')
    d8 = build('round8_factory_raw_polluted', raw=True, guard=True,
               raw_polluted=True)
    r, out = run_restore(d8)
    rec('round8', '还原脚本退出码为 0', r.returncode == 0, out[-200:])
    rec('round8', 'raw/ 注入脚本已中和',
        is_stub((d8 / 'raw/fnstyle-runtime.js').read_bytes()))
    rec('round8', 'raw/ 配置已中和',
        is_neutral((d8 / 'raw/deskdata/fnstyle.json').read_bytes()))
    rec('round8', 'raw/ 自定义图片已清除且原生首页未被动',
        not (d8 / 'raw/deskdata/fnstyle').exists() and
        (d8 / 'raw/index.html').read_bytes() == NATIVE_HTML)
    rv, _ = run_restore(d8, verify_only=True)
    rec('round8', 'raw 污染场景整体自检通过', rv.returncode == 0)

    # ---- 静态检查：回调必须两段式（同步不碰 www/ + 后台延迟还原）----
    print('\n===== 回调静态检查 =====')
    uc = (ROOT / 'cmd/uninstall_callback').read_text(encoding='utf-8')
    ic = (ROOT / 'cmd/install_callback').read_text(encoding='utf-8')
    df = (ROOT / 'cmd/restore_deferred.sh').read_text(encoding='utf-8')
    core = (ROOT / 'cmd/restore_system.py').read_text(encoding='utf-8')
    rec('callbacks', 'uninstall 优先挑 v2 中和方案脚本',
        '__FNSTYLE_RESTORED_NOOP__' in uc and 'skip-www' in uc)
    rec('callbacks', 'uninstall 先终止残留后台写入进程', 'pgrep -f' in uc)
    rec('callbacks', 'uninstall 同步阶段带 --skip-www（不触发 nginx 重建）',
        '--skip-www' in uc and '--settle 0' in uc)
    rec('callbacks', 'uninstall 用 setsid+nohup 排程后台还原',
        'setsid' in uc and 'nohup' in uc and 'restore_deferred.sh' in uc)
    rec('callbacks', 'install 投递后台执行器到 guard',
        'restore_deferred.sh' in ic)
    rec('callbacks', '后台执行器含 PYCODE 内联兜底', 'PYCODE' in df)
    rec('callbacks', '后台执行器兜底同样是中和而非删除',
        'NEUTRAL' in df and 'STUB' in df)
    rec('callbacks', '后台执行器完整还原 + 自检',
        '--verify-only' in df and '--settle' in df)
    rec('callbacks', '后台执行器先延迟再写 www/',
        0 < df.find('sleep "$DELAY"') < df.find('python3 "$SCRIPT"'))
    rec('callbacks', 'install 建立系统级 guard', 'install_system_guard' in ic)
    rec('callbacks', '还原核心含两轮执行与延时等待', 'settle' in core and '第 %d 轮' in core)
    rec('callbacks', '还原核心含空配置自禁用标记',
        '__fnstyleDisabled' in core and 'kill_lingering_workers' in core)
    rt = (ROOT / 'app/ui/static/js/fnstyle-runtime.js').read_text(encoding='utf-8')
    lg = (ROOT / 'app/ui/static/js/fnstyle-login.js').read_text(encoding='utf-8')
    rec('callbacks', '桌面运行时支持卸载后自禁用', '__fnstyleDisabled' in rt and 'CFG_TTL' in rt)
    rec('callbacks', '登录运行时支持卸载后自禁用', '__fnstyleDisabled' in lg and 'CFG_TTL' in lg)

    passed = sum(1 for x in results if x['status'] == 'PASS')
    print('\n===== 汇总：%d/%d PASS =====' % (passed, len(results)))
    (ROOT / 'verify/result_uninstall_restore.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
