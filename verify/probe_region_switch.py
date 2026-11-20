#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针：复现「地区下拉选择后总是恢复到成都」的问题。"""
import json
import time
import urllib.request

import websocket

URL = "http://192.168.5.8:9910/"


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.events = []

    def send(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def pump(self, seconds):
        end = time.time() + seconds
        self.ws.settimeout(1.0)
        while time.time() < end:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if "method" in msg:
                self.events.append(msg)
        self.ws.settimeout(60)


def ev(c, expr, wait=1.5):
    r = c.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    val = r.get("result", {}).get("value")
    if wait:
        c.pump(wait)
    return val


def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=3).read().decode())
    page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Network.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.send("Page.navigate", {"url": URL})
    c.pump(6)
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.pump(2)
    ev(c, "var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'", 3)
    ev(c, "window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'", 4)

    print("=== 初始状态 ===")
    print(ev(c, r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  if (!tb) return 'NO TARIFF BOX';
  var reg = tb.querySelector('[data-tariff-region]');
  return JSON.stringify({
    regionValue: reg ? reg.value : null,
    options: reg ? Array.prototype.map.call(reg.options, function (o) {
      return o.value + '|' + o.textContent; }) : [],
    enabled: (function () { var e = tb.querySelector('[data-tariff-enabled]');
      return e ? e.checked : null; })()
  }, null, 1);
})()
""", 0))

    print("\n=== 模拟用户选择「天津市居民」并触发 change ===")
    print(ev(c, r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var reg = tb.querySelector('[data-tariff-region]');
  reg.value = 'tianjin';
  reg.dispatchEvent(new Event('change', { bubbles: true }));
  return 'dispatched change → ' + reg.value;
})()
""", 2.5))

    print("\n=== change 之后的下拉值 ===")
    print(ev(c, r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var reg = tb.querySelector('[data-tariff-region]');
  var tbl = tb.querySelector('.tariff-table');
  return JSON.stringify({
    regionValue: reg ? reg.value : null,
    hasTable: !!tbl,
    tableText: tbl ? tbl.innerText.replace(/\s+/g, ' ').trim().slice(0, 200) : null
  }, null, 1);
})()
""", 0))

    print("\n=== 点击「保存电价设置」 ===")
    print(ev(c, r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var btn = tb.querySelector('[data-tariff-save]');
  if (!btn) return 'NO SAVE BTN';
  btn.click();
  return 'clicked save';
})()
""", 3))

    print("\n=== 保存后的下拉值 + 后端实际配置 ===")
    print(ev(c, r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var reg = tb.querySelector('[data-tariff-region]');
  var msg = tb.querySelector('[data-tariff-msg]');
  return JSON.stringify({
    regionValue: reg ? reg.value : null,
    msg: msg ? msg.textContent : null
  }, null, 1);
})()
""", 0))

    print("\n=== 后端 /api/power/tariff 实际返回的 region ===")
    r = c.send("Runtime.evaluate", {"expression":
           "fetch('api/power/tariff').then(r=>r.json()).then(j=>JSON.stringify({region:j.config.region,enabled:j.config.enabled}))",
           "awaitPromise": True, "returnByValue": True})
    print(r.get("result", {}).get("value"))

    ws.close()


if __name__ == "__main__":
    main()
