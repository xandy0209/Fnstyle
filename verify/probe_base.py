#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 实机探针的公共骨架（v1.1.63 新增）

背景
----
v1.1.63 之前，verify/ 下的每个 probe_*.py 都各自抄了一份
CDP 客户端 + 「找 tab / 开抽屉 / 抓面板」的样板代码，
改一处要改 N 处，且各家的快照字段互不一致。

这里抽出一份公共骨架：
  · CDP 客户端（send / pump / js）
  · 找 NAS 应用页的 tab
  · 打开能耗抽屉
  · 抓电价面板快照（**单一单价形态**的字段集）
  · 断言计数器（check + 汇总退出码）

用法：
    from probe_base import CDP, open_panel, snap_panel, api, Checker

前置：Chrome 已带 --remote-debugging-port=9222 启动，
      且 NAS 上已部署对应版本（默认 http://192.168.5.8:9910/）。
"""
import json
import time
import urllib.request

import websocket

URL = "http://192.168.5.8:9910/"
API = "http://192.168.5.8:9910"

# 电价面板容器（抽屉 body 里的面板子容器）
TB = ("document.querySelector('#monComputeBody [data-tariff-box]')"
      " || document.querySelector('#monComputeBody .tariff-box')")


class CDP:
    """极简 CDP 客户端。"""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.events = []

    def send(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
            self.events.append(msg)

    def pump(self, seconds):
        """空转等待（同时收集事件）。"""
        end = time.time() + seconds
        self.ws.settimeout(0.4)
        while time.time() < end:
            try:
                self.events.append(json.loads(self.ws.recv()))
            except Exception:
                pass
        self.ws.settimeout(60)

    def js(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True})
        res = (r.get("result") or {})
        if res.get("subtype") == "error":
            raise RuntimeError(str(res.get("description")))
        return res.get("value")


def api(path, method="GET", body=None):
    """调 NAS 上的 API。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


def find_tab(host="192.168.5.8", port=9222):
    """找一个可用的 page target（优先 NAS 应用页）。"""
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/list" % port, timeout=10).read().decode())
    pages = [t for t in tabs if t.get("type") == "page"]
    if not pages:
        raise RuntimeError("没有可用的 Chrome 页面（先跑 verify/cdp_open_tab.py）")
    for p in pages:
        if host in (p.get("url") or ""):
            return p
    return pages[0]


def connect(host="192.168.5.8", port=9222):
    """连接 Chrome，返回 CDP 实例。"""
    target = find_tab(host, port)
    ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=60)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")
    # ⚠️ 必须关掉缓存 —— 否则改了 CSS/JS 后页面仍用旧版，
    #    实测表现为「代码明明改了但尺寸一点没变」，极难判断。
    #    preview.py 的静态资源带 `Cache-Control: max-age=600`，
    #    所以除了 setCacheDisabled 还要主动 clearBrowserCache。
    try:
        c.send("Network.enable")
        c.send("Network.setCacheDisabled", {"cacheDisabled": True})
        c.send("Network.clearBrowserCache")
    except Exception:
        pass
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2,
            "mobile": False})
    return c


def open_drawer(c, settle=5.0):
    """打开能耗抽屉（切到系统监控 → 调 FnStyleCompute.open）。"""
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(4)
    c.js("window.FnStyleCompute.open('power', "
         "window.FnStyleMonitor.lastData());'ok'")
    c.pump(settle)


def reload_and_open(c, settle=5.0):
    """重载页面（**硬刷新**，绕过 HTTP 缓存）→ 切监控 → 开抽屉。"""
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    try:
        # ignoreCache=True → 强制重新拉取 CSS/JS，改了样式才能立刻看到效果
        c.send("Page.reload", {"ignoreCache": True})
        c.pump(5)
    except Exception:
        pass
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2,
            "mobile": False})
    c.pump(2)
    open_drawer(c, settle)


# 面板快照脚本（单一单价形态）
_SNAP_JS = """(function(){
  var tb = %s;
  if (!tb) return '{"err":"NO TARIFF BOX"}';
  function v(sel){ var e = tb.querySelector(sel); return e ? e.value : null; }
  var rt = tb.querySelector('[data-tariff-rate]');
  var note = tb.querySelector('[data-tariff-note]');
  var tt = tb.querySelector('.tariff-table');
  return JSON.stringify({
    /* 存在性 */
    hasRate: !!rt,
    hasNote: !!note,
    hasSave: !!tb.querySelector('[data-tariff-save]'),
    hasTable: !!tt,
    /* 值 */
    rate: rt ? rt.value : null,
    noteText: note ? note.innerText.replace(/\\s+/g,' ').trim() : null,
    /* 已删除的概念 —— 以下应全部为 false */
    hasRegion: !!tb.querySelector('[data-tariff-region]'),
    hasVoltage: !!tb.querySelector('[data-tariff-voltage]'),
    hasVstart: !!tb.querySelector('[data-tariff-vstart]'),
    hasVend: !!tb.querySelector('[data-tariff-vend]'),
    hasEnabled: !!tb.querySelector('[data-tariff-enabled]'),
    hasPeople: !!tb.querySelector('[data-tariff-people]'),
    hasMeter: !!tb.querySelector('[data-tariff-meter]'),
    hasProjRate: !!tb.querySelector('[data-tariff-proj-rate]'),
    hasSeason: !!tb.querySelector('[data-tariff-season]'),
    hasDerive: !!tb.querySelector('.tariff-tb-derive'),
    hasTierRow: tb.innerHTML.indexOf('tariff-tb-row') > -1,
    /* 结构计数 */
    inputCount: tb.querySelectorAll('input').length,
    selectCount: tb.querySelectorAll('select').length,
    /* 文本 */
    panelText: tb.innerText.replace(/\\s+/g,' ').trim(),
    rendered: tb.getAttribute('data-tariff-rendered')
  });
})()""" % TB


def snap_panel(c):
    """抓电价面板快照（单一单价形态的字段集）。"""
    raw = c.js(_SNAP_JS)
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"err": "PARSE_FAIL", "raw": str(raw)[:300]}


def set_rate(c, value, evt="input"):
    """设置单价输入框的值。"""
    return c.js("""(function(){
      var tb = %s;
      var e = tb.querySelector('[data-tariff-rate]');
      if (!e) return 'NO_FIELD';
      e.value = %s;
      e.dispatchEvent(new Event('%s', {bubbles:true}));
      return e.value;
    })()""" % (TB, json.dumps(str(value)), evt))


class Checker:
    """断言计数器。"""

    def __init__(self, title=""):
        self.title = title
        self.passn = 0
        self.fail = []

    def check(self, label, cond, extra=""):
        if cond:
            self.passn += 1
            print("  ✓ %s%s" % (label, ("  → %s" % extra) if extra else ""))
        else:
            self.fail.append(label)
            print("  ✗ %s%s" % (label, ("  → %s" % extra) if extra else ""))

    def section(self, t):
        print("\n--- %s ---" % t)

    def finish(self):
        print("\n" + "=" * 60)
        print("通过 %d 项，失败 %d 项" % (self.passn, len(self.fail)))
        print("=" * 60)
        if self.fail:
            for f in self.fail:
                print("  ✗ %s" % f)
            raise SystemExit(1)
        print("🎉 全部通过")
