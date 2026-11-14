#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「主题库默认进行加载」。

三层验证：
  ① 静态链路：前端去掉一次性闸门 / 串行加载 / 增量合并，后端有出厂快照与离线兜底。
  ② 真实 CGI：/api/proxy 在远程失败、返回 HTML 错误页时回退出厂快照；
     远程正常时不回退；播种的 theme.json 能被 /api/themes 读出。
  ③ Playwright：不点任何按钮，打开软件主题库就有内容；旧版遗留标记不阻止加载；
     远程全失败不清空本地主题；增量合并；用户显式全不选被尊重。
"""
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import threading
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'app/ui'
SEED = ROOT / 'app/seed'
SANDBOX = ROOT / 'verify/.sandbox_theme'
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

results = []


def rec(group, name, ok, extra=''):
    results.append(dict(group=group, name=name, status='PASS' if ok else 'FAIL',
                        extra=extra))
    print('%s [%s] %s%s' % (' ✓' if ok else ' ✗', group, name,
                           ('  → ' + str(extra)) if (extra and not ok) else ''))


def text(p):
    return Path(p).read_text(encoding='utf-8')


# ------------------------------------------------------------ ① 静态链路
def static_checks():
    js = text(UI / 'static/js/app.js')
    cgi = text(UI / 'index.cgi')
    ins = text(ROOT / 'cmd/install_callback')

    rec('static', '已移除「只做一次」的永久闸门',
        'AUTOLOAD_KEY' not in js and 'fnstyle_theme_autoload_v1' not in js)
    rec('static', '启动时默认加载且带重试语义',
        'autoLoadStarted' in js and 'loadSelectedLibs(true, { merge: true })' in js)
    rec('static', '先渲染本地主题，再读库源，最后静默更新（串行）',
        bool(re.search(r'loadThemes\(\)\s*\n\s*\.then\(loadLibs\)\s*\n\s*\.then\(autoLoadDefaultLibs\)', js)))
    rec('static', '默认加载有轻量状态提示且不遮挡网格',
        'setAutoLoadHint' in js and js.count('setAutoLoadHint') >= 3)
    rec('static', '默认加载走增量合并、失败不清空',
        'if (!incoming.length) return false;' in js and 'merged = state.themes.slice();' in js)
    rec('static', '无勾选记录时默认勾上库源',
        'state.selectedLibs[list[0].url] = true' in js)
    rec('static', '用户显式全不选被尊重',
        'DEFAULT_LIB_EXPLICIT_KEY' in js and 'saveLibSelection(true)' in js)

    rec('static', '安装回调播种默认主题到 theme.json',
        'default_themes.json' in ins and 'seed_themes_if_empty' in ins)
    rec('static', '安装回调播种各库源出厂快照',
        'fnstyle_theme_cache' in ins and 'theme_cache' in ins)
    rec('static', 'CGI 代理有非 JSON 校验与快照回退',
        'SNAP_KEY' in cgi and 'fnstyle_theme_cache' in cgi and '5b|7b|ef' in cgi)

    d = json.loads(text(SEED / 'default_themes.json'))
    rec('static', '出厂默认主题是合法数组且非空', isinstance(d, list) and len(d) >= 20,
        'len=%d' % (len(d) if isinstance(d, list) else -1))
    libs = json.loads(text(SEED / 'theme_list.json'))
    default_url = next((l['url'] for l in libs if 'fndesk' in l.get('name', '').lower()
                        and '主题库' in l.get('name', '')), libs[0]['url'])
    key = hashlib.md5(default_url.encode('utf-8')).hexdigest()
    snap = SEED / 'theme_cache' / (key + '.json')
    rec('static', '默认库快照与出厂主题一致',
        snap.is_file() and snap.read_bytes() == (SEED / 'default_themes.json').read_bytes(),
        key)
    rec('static', '全部库源都有出厂快照',
        all((SEED / 'theme_cache' / (hashlib.md5(l['url'].encode('utf-8')).hexdigest() + '.json')).is_file()
            for l in libs))


# ---------------------------------------------------- ② 真实 CGI 动态
def cgi_run(cgi, share_root, path, query='', method='GET'):
    env = os.environ.copy()
    env.update({
        'FNSTYLE_SANDBOX': '1',
        'TRIM_DATA_SHARE_PATHS': str(share_root),
        'REQUEST_METHOD': method,
        'REQUEST_URI': '/cgi/ThirdParty/fnstyle/index.cgi' + path,
        'SCRIPT_NAME': '/cgi/ThirdParty/fnstyle/index.cgi',
        'PATH_INFO': path,
        'QUERY_STRING': query,
        'CONTENT_LENGTH': '0',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'HTTP_HOST': '127.0.0.1:8443',
        'REMOTE_ADDR': '127.0.0.1',
        'no_proxy': '*', 'NO_PROXY': '*',
    })
    r = subprocess.run(['bash', str(cgi)], input=b'', env=env,
                       capture_output=True, timeout=60)
    out = r.stdout.decode('utf-8', 'replace')
    body = re.split(r'\r?\n\r?\n', out, maxsplit=1)[-1].strip()
    return r, body


def cgi_checks():
    if SANDBOX.exists():
        shutil.rmtree(str(SANDBOX))
    ui = SANDBOX / 'var/apps/fnstyle/target/ui'
    shutil.copytree(str(UI), str(ui), dirs_exist_ok=True)
    cgi = ui / 'index.cgi'
    t = cgi.read_text(encoding='utf-8')
    t = t.replace('APP_ROOT="/var/apps/${APP_NAME}/target"',
                  'APP_ROOT="%s"' % ui.parent)
    t = t.replace('if ! is_root; then',
                  'if [ "${FNSTYLE_SANDBOX:-0}" != "1" ] && ! is_root; then')
    cgi.write_text(t, encoding='utf-8')

    share = SANDBOX / 'shareRoot'
    data_dir = share / 'deskdata'
    snap_dir = data_dir / 'lib/fnstyle_theme_cache'
    snap_dir.mkdir(parents=True)
    # 模拟安装回调播种的默认主题
    shutil.copyfile(str(SEED / 'default_themes.json'), str(data_dir / 'theme.json'))

    r, body = cgi_run(cgi, share, '/api/themes')
    try:
        themes = json.loads(body)
    except ValueError:
        themes = None
    rec('cgi', '播种后 /api/themes 直接读到 31 个主题',
        isinstance(themes, list) and len(themes) == 31,
        'rc=%d body=%s' % (r.returncode, body[:120]))

    def put_snapshot(url, payload):
        k = hashlib.md5(url.encode('utf-8')).hexdigest()
        (snap_dir / (k + '.json')).write_text(
            json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    # A. 远程连接失败（端口 1 必然 refused）→ 回退快照
    url_a = 'http://127.0.0.1:1/never.json'
    put_snapshot(url_a, [{'ID': 'offline-1', '标题': '离线主题'}])
    r, body = cgi_run(cgi, share, '/api/proxy',
                      'url=' + urllib.parse.quote(url_a, safe=''))
    rec('cgi', '远程失败时回退出厂快照',
        'offline-1' in body and r.returncode == 0,
        'rc=%d body=%s' % (r.returncode, body[:160]))

    # B/C. 用一个临时 HTTP 服务分别返回 HTML 错误页 / 新 JSON
    class Probe(http.server.BaseHTTPRequestHandler):
        payload = b'<html>502 Bad Gateway</html>'

        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)

    srv = socketserver.TCPServer(('127.0.0.1', 0), Probe)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url_bc = 'http://127.0.0.1:%d/theme.json' % port
        put_snapshot(url_bc, [{'ID': 'offline-2', '标题': '快照主题'}])
        q = 'url=' + urllib.parse.quote(url_bc, safe='')

        Probe.payload = b'<html>502 Bad Gateway</html>'
        r, body = cgi_run(cgi, share, '/api/proxy', q)
        rec('cgi', '远程返回 HTML 错误页时回退快照',
            'offline-2' in body and 'Bad Gateway' not in body,
            'body=%s' % body[:160])

        Probe.payload = b'[{"ID":"fresh-1","\xe6\xa0\x87\xe9\xa2\x98":"\xe6\x96\xb0\xe4\xb8\xbb\xe9\xa2\x98"}]'
        r, body = cgi_run(cgi, share, '/api/proxy', q)
        rec('cgi', '远程正常 JSON 时不回退（保留最新数据）',
            'fresh-1' in body and 'offline-2' not in body,
            'body=%s' % body[:160])
    finally:
        srv.shutdown()


# ------------------------------------------------- ③ 浏览器：不点按钮就加载
CONFIG = json.loads(text(SEED / 'fnstyle.json'))


def theme(i, tag='T'):
    return {'ID': '%s%d' % (tag, i), '标题': '%s主题 %d' % (tag, i),
            't0': '', 't1': '', 't4': ''}


def serve(initial_themes, libs, proxy_data, proxy_status=200, storage=None):
    state = {'themes': list(initial_themes), 'posted': []}
    calls = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = urllib.parse.urlparse(self.path).path
            calls.append(('GET', p))
            if p == '/api/config':
                return self._json(CONFIG)
            if p == '/api/theme-libs':
                return self._json(libs)
            if p == '/api/themes':
                return self._json(state['themes'])
            if p == '/api/proxy':
                calls.append(('proxy', urllib.parse.urlparse(self.path).query))
                if proxy_status != 200:
                    return self._json({'success': False, 'message': '远程不可用'}, proxy_status)
                return self._json(proxy_data)
            if p.startswith('/api/'):
                return self._json({'success': False, 'message': 'not mocked: ' + p})
            return super().do_GET()

        def do_POST(self):
            p = urllib.parse.urlparse(self.path).path
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            calls.append(('POST', p))
            if p == '/api/themes':
                try:
                    state['themes'] = json.loads(raw.decode('utf-8'))
                    state['posted'].append(state['themes'])
                except ValueError:
                    pass
            self._json({'success': True, 'message': 'ok'})

    h = functools.partial(Handler, directory=str(UI))
    httpd = socketserver.ThreadingTCPServer(('127.0.0.1', 0), h)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d/' % httpd.server_address[1], calls, state


DEFAULT_LIBS = [{'name': 'Fndesk 主题库', 'desc': '', 'from': '',
                 'url': 'http://127.0.0.1:1/default.json'}]
OTHER_LIBS = [{'name': '自定义库', 'desc': '', 'from': '',
               'url': 'http://127.0.0.1:1/custom.json'}]


def browser_case(pw, label, *, themes, libs=DEFAULT_LIBS, proxy_data, proxy_status=200,
                 storage=None, expect_count, expect_proxy, expect_cards=None):
    httpd, base, calls, state = serve(themes, libs, proxy_data, proxy_status, storage)
    browser = pw.chromium.launch(executable_path=CHROME, headless=True,
                                 args=['--no-sandbox', '--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width': 1400, 'height': 900})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    if storage:
        script = ';'.join("try{localStorage.setItem(%s,%s)}catch(e){}" %
                           (json.dumps(k), json.dumps(v))
                           for k, v in storage.items())
        page.add_init_script(script)
    try:
        page.goto(base + 'index.html', wait_until='domcontentloaded', timeout=20000)
        expect_text = '%d 个主题' % expect_count
        try:
            page.wait_for_function(
                "document.querySelector('#themeCount') && "
                "document.querySelector('#themeCount').textContent.trim() === %s && "
                "!document.querySelector('#themeCount').textContent.includes('正在')" %
                json.dumps(expect_text), timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(600)   # 让静默加载与后续 POST 全部落地
        count_text = page.locator('#themeCount').inner_text().strip()
        cards = page.locator('#themeGrid .theme-card').count()
        proxy_hits = sum(1 for c in calls if c[0] == 'proxy')
        rec('browser/' + label, '主题数标签正确', count_text == expect_text,
            'got=%r want=%r' % (count_text, expect_text))
        rec('browser/' + label, '未点任何按钮即渲染主题卡片',
            cards == (expect_cards if expect_cards is not None else min(expect_count, 10)),
            'cards=%d' % cards)
        rec('browser/' + label, '默认加载请求次数符合预期',
            proxy_hits == expect_proxy, 'proxy=%d want=%d' % (proxy_hits, expect_proxy))
        rec('browser/' + label, '无 JS 异常', not errors, '; '.join(errors[:2]))
        return state, calls
    finally:
        browser.close()
        httpd.shutdown()


def browser_checks():
    with sync_playwright() as pw:
        # A. 全新安装：本地主题为空，远程能拉到 3 个 → 打开即有内容
        st, _ = browser_case(
            pw, '首次打开', themes=[],
            proxy_data=[theme(1), theme(2), theme(3)],
            expect_count=3, expect_proxy=1)
        rec('browser/首次打开', '默认加载结果已写回后端',
            bool(st['posted']) and len(st['posted'][-1]) == 3,
            'posted=%s' % ([len(x) for x in st['posted']]))

        # B. 旧版遗留的「已自动加载过」标记不能阻止这次加载
        browser_case(
            pw, '遗留标记', themes=[],
            proxy_data=[theme(1, 'R')],
            storage={'fnstyle_theme_autoload_v1': '1'},
            expect_count=1, expect_proxy=1)

        # C. 远程全失败：保留本地主题，不清空
        st, _ = browser_case(
            pw, '远程失败', themes=[theme(i, 'L') for i in range(1, 6)],
            proxy_data=[], proxy_status=502,
            expect_count=5, expect_proxy=1)
        rec('browser/远程失败', '失败时不覆盖本地主题', not st['posted'],
            'posted=%s' % ([len(x) for x in st['posted']]))

        # D. 增量合并：本地 2 + 远程 3（1 个 ID 重复）= 4
        st, _ = browser_case(
            pw, '增量合并', themes=[theme(1, 'L'), theme(2, 'L')],
            proxy_data=[theme(2, 'L'), theme(3, 'R'), theme(4, 'R')],
            expect_count=4, expect_proxy=1)
        rec('browser/增量合并', '合并后的完整主题写回后端',
            bool(st['posted']) and len(st['posted'][-1]) == 4,
            'posted=%s' % ([len(x) for x in st['posted']]))

        # E. 用户显式「全不选」：尊重选择，不自动加载
        browser_case(
            pw, '显式全不选', themes=[],
            proxy_data=[theme(1)],
            storage={'fnstyle_theme_selection_v1': '[]',
                      'fnstyle_theme_selection_explicit_v1': '1'},
            expect_count=0, expect_proxy=0, expect_cards=0)

        # F. 库源名称不叫「Fndesk 主题库」时也能默认加载（退回第一个库源）
        browser_case(
            pw, '非默认名库源', themes=[], libs=OTHER_LIBS,
            proxy_data=[theme(1, 'O'), theme(2, 'O')],
            expect_count=2, expect_proxy=1)


def main():
    print('===== 主题库默认加载验证 =====')
    static_checks()
    print('\n----- 真实 CGI -----')
    cgi_checks()
    print('\n----- 浏览器（不点任何按钮）-----')
    browser_checks()
    passed = sum(1 for x in results if x['status'] == 'PASS')
    print('\n合计：%d/%d 通过' % (passed, len(results)))
    for x in results:
        if x['status'] == 'FAIL':
            print('  FAIL [%s] %s %s' % (x['group'], x['name'], x['extra']))
    print('RESULT %d/%d' % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
