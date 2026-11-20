#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针：验证季节参数保存后端到端生效。"""
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


def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=3).read().decode())
    page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.send("Page.navigate", {"url": URL})
    c.pump(6)
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.pump(2)
    c.send("Runtime.evaluate", {"expression":
           "var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'"})
    c.pump(4)
    c.send("Runtime.evaluate", {"expression":
           "window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'"})
    c.pump(5)

    print("=== 修改季节参数：把 11 月加入丰水期，丰水期谷价一档改 0.2 ===")
    r = c.send("Runtime.evaluate", {"expression": r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var season = tb.querySelector('[data-tariff-season]');
  if (!season) return 'NO SEASON BOX';
  /* 1) 点亮 11 月（丰水期） */
  var wetGrp = season.querySelector('[data-tariff-months="wet"]');
  var m11 = wetGrp.querySelector('.tariff-month[data-m="11"]');
  m11.click();
  /* 2) 改丰水期一档谷价 0.175 → 0.2 */
  var p0 = season.querySelector('[data-tariff-season-price="wet"][data-i="0"]');
  p0.value = '0.2';
  /* 3) 改其他月二档上限 280 → 300 */
  var t1 = season.querySelector('[data-tariff-season-tier="other"][data-i="1"]');
  t1.value = '300';
  return 'modified: wet11=on, wet#0=' + p0.value + ', other#1=' + t1.value;
})()
""", "returnByValue": True})
    print(r.get("result", {}).get("value"))
    c.pump(1)

    print("\n=== 点保存 ===")
    r = c.send("Runtime.evaluate", {"expression": r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var btn = tb.querySelector('[data-tariff-save]');
  btn.click();
  return 'clicked save';
})()
""", "returnByValue": True})
    print(r.get("result", {}).get("value"))
    c.pump(4)

    print("\n=== 面板提示 + 后端实际存储 ===")
    r = c.send("Runtime.evaluate", {"expression": r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  var msg = tb.querySelector('[data-tariff-msg]');
  return msg ? msg.textContent : 'NO MSG';
})()
""", "returnByValue": True})
    print("提示:", r.get("result", {}).get("value"))

    r = c.send("Runtime.evaluate", {"expression":
           "fetch('api/power/tariff').then(r=>r.json()).then(j=>JSON.stringify({"
           "overrides:j.overrides, wet_months:j.profile.wet_months, "
           "vwet:j.profile.valley_rates_wet, tother:j.profile.tiers_other}))",
           "awaitPromise": True, "returnByValue": True})
    print("后端:", r.get("result", {}).get("value"))

    print("\n=== 计费是否按新季节参数生效（11 月本应是丰水期） ===")
    r = c.send("Runtime.evaluate", {"expression":
           "fetch('api/power/stats').then(r=>r.json()).then(j=>JSON.stringify({"
           "tariff_region:j.tariff_region, eff:j.eff_rate_yuan_per_kwh, "
           "valley_rate:j.tariff_detail}))",
           "awaitPromise": True, "returnByValue": True})
    print("stats:", str(r.get("result", {}).get("value"))[:400])

    ws.close()


if __name__ == "__main__":
    main()
