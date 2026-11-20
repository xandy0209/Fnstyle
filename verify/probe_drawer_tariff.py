#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针 v1.1.57：打开实机能耗抽屉，断言抽屉原有内容 + 电价面板同时存在，并截图。"""
import base64
import json
import time
import urllib.request

import websocket

URL = "http://192.168.5.8:9910/"

PROBE = r"""
(function () {
  var out = {};
  var ov = document.querySelector('#monComputeOverlay');
  var body = document.querySelector('#monComputeBody');
  out.overlayExists = !!ov;
  out.overlayHidden = ov ? ov.hasAttribute('hidden') : null;
  out.bodyExists = !!body;
  if (!body) return JSON.stringify(out, null, 1);
  var h = body.innerHTML;
  out.bodyLen = h.length;
  /* 抽屉原有内容 —— 必须全部还在 */
  out.keep = {
    '实时功耗 hero': h.indexOf('mon-hero') > -1,
    '功耗堆叠条': h.indexOf('mon-stack-wrap') > -1,
    '趋势图容器': h.indexOf('data-charts') > -1 || h.indexOf('chart-') > -1,
    '部件明细': h.indexOf('部件明细') > -1,
    '用电与电费区': h.indexOf('data-pwr-energy') > -1,
    '估算参数': h.indexOf('估算参数') > -1,
    '电价设置区块': h.indexOf('data-tariff-box') > -1
  };
  /* 电价面板是否已渲染 */
  var tb = body.querySelector('[data-tariff-box]');
  out.tariffBoxFound = !!tb;
  out.tariffPanelRendered = !!(tb && tb.innerHTML.indexOf('tariff-field') > -1);
  out.tariffPanelLen = tb ? tb.innerHTML.length : 0;
  /* 抽屉里不能出现「面板把整个 body 覆盖」的迹象：
     面板内容必须只出现在 tariffBox 内，不能出现在 body 顶层 */
  out.panelLeakedToBody = h.indexOf('tariff-field') > -1 &&
    (!tb || tb.innerHTML.indexOf('tariff-field') === -1);
  /* 电价面板是否可见（未被 display:none / 高度 0 隐藏） */
  if (tb) {
    var r = tb.getBoundingClientRect();
    out.tariffBoxRect = { w: Math.round(r.width), h: Math.round(r.height),
      top: Math.round(r.top) };
    var st = getComputedStyle(tb);
    out.tariffBoxDisplay = st.display;
    out.tariffBoxVisibility = st.visibility;
  }
  /* 面板里的关键控件 */
  if (tb) {
    out.panelControls = {
      switch: !!tb.querySelector('[data-tariff-enabled]'),
      region: !!tb.querySelector('[data-tariff-region]'),
      valleyStart: !!tb.querySelector('[data-tariff-vstart]'),
      valleyEnd: !!tb.querySelector('[data-tariff-vend]'),
      rate: !!tb.querySelector('[data-tariff-rate]'),
      save: !!tb.querySelector('[data-tariff-save]')
    };
    var reg = tb.querySelector('[data-tariff-region]');
    out.regionValue = reg ? reg.value : null;
    out.regionOptions = reg ? Array.prototype.map.call(reg.options, function (o) {
      return o.value + '=' + o.textContent; }) : [];
    var tbl = tb.querySelector('.tariff-table');
    out.tableText = tbl ? tbl.innerText.replace(/\s+/g, ' ').trim() : null;
  }
  /* 用电与电费区是否已异步回填 */
  var en = body.querySelector('[data-pwr-energy]');
  out.energyText = en ? en.innerText.replace(/\s+/g, ' ').trim().slice(0, 300) : null;
  /* 部件明细行数 */
  out.dkvRows = body.querySelectorAll('.mon-dkv-row').length;
  return JSON.stringify(out, null, 1);
})()
"""


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
    for _ in range(40):
        try:
            tabs = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:9222/json/list", timeout=2).read().decode())
            if tabs:
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("Chrome 调试端口没起来")
    page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Network.enable")
    c.send("Log.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.send("Page.navigate", {"url": URL})
    c.pump(6)
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.pump(2)

    # 切到「系统监控」面板
    r = c.send("Runtime.evaluate",
               {"expression": "var b=document.querySelector('[data-tab=monitor]');"
                              "if(b){b.click();'clicked'}else{'NO BTN'}"})
    print("[tab] " + str(r.get("result", {}).get("value")))
    c.pump(5)

    # 打开能耗抽屉：直接调 window.FnStyleCompute.open('power', lastData())
    r = c.send("Runtime.evaluate", {"expression": r"""
(function () {
  if (!window.FnStyleCompute || !window.FnStyleCompute.open) return 'NO FnStyleCompute.open';
  var d = window.FnStyleMonitor ? window.FnStyleMonitor.lastData() : null;
  window.FnStyleCompute.open('power', d);
  return 'opened power, data=' + (d ? 'yes' : 'null');
})()
"""})
    print("[open] " + str(r.get("result", {}).get("value")))
    c.pump(5)

    res = c.send("Runtime.evaluate", {"expression": PROBE, "returnByValue": True})
    val = res.get("result", {}).get("value")
    print("\n--- PROBE ---")
    print(val)

    print("\n--- CONSOLE / NETWORK ---")
    errs = 0
    for e in c.events:
        m = e.get("method")
        if m == "Runtime.exceptionThrown":
            d = e["params"].get("exceptionDetails", {})
            print("[exception] %s" % str(d.get("text"))[:300])
            errs += 1
        elif m == "Network.loadingFailed":
            print("[netfail] %s %s" % (e["params"].get("type"), e["params"].get("errorText")))
            errs += 1
        elif m == "Network.responseReceived":
            rr = e["params"]["response"]
            if rr["status"] >= 400:
                print("[http%d] %s" % (rr["status"], rr["url"]))
                errs += 1
        elif m == "Log.entryAdded":
            en = e["params"]["entry"]
            if en.get("level") == "error":
                print("[log.error] %s %s" % (en.get("text", "")[:200], en.get("url", "")[:120]))
    print("errors=%d" % errs)

    # 抽屉内截图
    c.send("Runtime.evaluate",
           {"expression": "var e=document.querySelector('#monComputeOverlay');"
                          "if(e){e.scrollTop=0; var b=document.querySelector('#monComputeBody');"
                          "if(b) b.scrollTop=0;} 'ok'"})
    c.pump(1)
    shot = c.send("Page.captureScreenshot", {"format": "png"})
    with open("/tmp/fnstyle_drawer_v1157.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("\nscreenshot -> /tmp/fnstyle_drawer_v1157.png")
    ws.close()


if __name__ == "__main__":
    main()
