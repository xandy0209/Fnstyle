#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针 v1.1.57-b：滚动能耗抽屉，逐屏截图（验证电价面板与估算参数都在）。"""
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

    # 找抽屉滚动容器
    r = c.send("Runtime.evaluate", {"expression": r"""
(function () {
  var body = document.querySelector('#monComputeBody');
  var ov = document.querySelector('#monComputeOverlay');
  var out = [];
  Array.prototype.forEach.call(document.querySelectorAll('#monComputeOverlay, #monComputeOverlay *'), function (e) {
    if (e.scrollHeight > e.clientHeight + 20) {
      out.push({ id: e.id || e.className, sh: e.scrollHeight, ch: e.clientHeight });
    }
  });
  return JSON.stringify({ bodySH: body ? body.scrollHeight : 0,
    bodyCH: body ? body.clientHeight : 0, scrollables: out }, null, 1);
})()
"""})
    print("[scrollable] " + str(r.get("result", {}).get("value")))

    # 逐屏滚动截图
    for i, top in enumerate([0, 700, 1400, 2100]):
        c.send("Runtime.evaluate", {"expression": r"""
(function (t) {
  var body = document.querySelector('#monComputeBody');
  var ov = document.querySelector('#monComputeOverlay');
  if (body) body.scrollTop = t;
  if (ov && ov.scrollHeight > ov.clientHeight + 20) ov.scrollTop = t;
  return 'scroll ' + t;
})(%d)
""" % top})
        c.pump(1)
        shot = c.send("Page.captureScreenshot", {"format": "png"})
        path = "/tmp/drawer_scroll_%d.png" % i
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("screenshot -> " + path)
    ws.close()


if __name__ == "__main__":
    main()
