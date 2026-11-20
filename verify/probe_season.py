#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针：验证季节参数区块在实机渲染正常，并截图。"""
import base64
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

    probe = r"""
(function () {
  var tb = document.querySelector('#monComputeBody [data-tariff-box]');
  if (!tb) return JSON.stringify({ err: 'NO TARIFF BOX' });
  var season = tb.querySelector('[data-tariff-season]');
  var out = {
    hasSeason: !!season,
    seasonHead: season ? season.innerText.split('\n')[0] : null,
    seasonText: season ? season.innerText.replace(/\s+/g, ' ').trim() : null,
    monthGroups: Array.prototype.map.call(
      tb.querySelectorAll('[data-tariff-months]'), function (g) {
        return { kind: g.getAttribute('data-tariff-months'),
          on: Array.prototype.map.call(g.querySelectorAll('.tariff-month.on'),
            function (b) { return b.getAttribute('data-m'); }) };
      }),
    priceInputs: Array.prototype.map.call(
      tb.querySelectorAll('[data-tariff-season-price]'), function (i) {
        return i.getAttribute('data-tariff-season-price') + '#' +
          i.getAttribute('data-i') + '=' + i.value;
      }),
    tierInputs: Array.prototype.map.call(
      tb.querySelectorAll('[data-tariff-season-tier]'), function (i) {
        return i.getAttribute('data-tariff-season-tier') + '#' +
          i.getAttribute('data-i') + '=' + i.value;
      }),
    seasonRect: season ? (function () {
      var r = season.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height) };
    })() : null,
    tableText: (function () {
      var t = tb.querySelector('.tariff-table');
      return t ? t.innerText.replace(/\s+/g, ' ').trim() : null;
    })()
  };
  return JSON.stringify(out, null, 1);
})()
"""
    r = c.send("Runtime.evaluate", {"expression": probe, "returnByValue": True})
    print("--- 季节参数区块 ---")
    print(r.get("result", {}).get("value"))

    # 滚到季节参数区块并截图
    c.send("Runtime.evaluate", {"expression": r"""
(function () {
  var s = document.querySelector('#monComputeBody [data-tariff-season]');
  if (s) { s.scrollIntoView({ block: 'center' }); return 'scrolled'; }
  return 'NO SEASON';
})()
"""})
    c.pump(1.5)
    shot = c.send("Page.captureScreenshot", {"format": "png"})
    with open("/tmp/fnstyle_season.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("\nscreenshot -> /tmp/fnstyle_season.png")

    print("\n--- 错误检查 ---")
    errs = 0
    for e in c.events:
        if e.get("method") == "Runtime.exceptionThrown":
            print("[exception]", str(e["params"].get("exceptionDetails", {}).get("text"))[:200])
            errs += 1
    print("exceptions=%d" % errs)
    ws.close()


if __name__ == "__main__":
    main()
