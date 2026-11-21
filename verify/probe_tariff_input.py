#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针（v1.1.63）：实机验证「电费单价输入不被清空」

历史沿革
--------
v1.1.58 用户反馈：「选择固定单价时自定义单价输入后会被清空」
   根因：能耗抽屉每 3 秒 live 重建一次 → body.innerHTML 重置
        → 用户正在输入的 input 被冲掉；重绘后光标位置也丢了。

   修法（三处同时改）：compute.js updateLive 搬回旧节点 +
        monitor.js loadTariff 已渲染则跳过重绘 +
        monitor.js applyTariff 捕获/回填表单状态。

v1.1.63：电价模型退化为**单一单价** —— 面板只剩一个「电费单价」输入框，
   上述「输入被清空」的修复**依然必须成立**（这是本轮唯一保留的验证点）。
   「固定单价地区不显示档位表」的老问题随地区概念一起消失。

前置：Chrome 已带 --remote-debugging-port=9222 启动。
"""
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

    def js(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")


FAIL = []


def check(label, ok, extra=""):
    if ok:
        print("  ✓ %s%s" % (label, ("  → " + extra) if extra else ""))
    else:
        print("  ✗ %s  → %s" % (label, extra))
        FAIL.append(label)


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
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(4)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(5)

    tb = "document.querySelector('#monComputeBody [data-tariff-box]')"
    print("--- 初始状态（单一单价面板） ---")
    info = json.loads(c.js("""(function(){
      var tb = %s;
      if (!tb) return '{"err":"NO TARIFF BOX"}';
      var rt = tb.querySelector('[data-tariff-rate]');
      var note = tb.querySelector('[data-tariff-note]');
      return JSON.stringify({
        hasRate: !!rt,
        rate: rt ? rt.value : null,
        hasNote: !!note,
        noteText: note ? note.innerText.replace(/\\s+/g,' ').trim() : null,
        hasTable: !!tb.querySelector('.tariff-table'),
        inputCount: tb.querySelectorAll('input').length,
        selectCount: tb.querySelectorAll('select').length,
        rendered: tb.getAttribute('data-tariff-rendered')
      });
    })()""" % tb) or "{}")
    check("面板已渲染", "err" not in info, str(info)[:200])
    check("有单价输入框", info.get("hasRate") is True)
    check("单价默认值 = 0.5224", str(info.get("rate")) == "0.5224",
          str(info.get("rate")))
    check("有口径说明", info.get("hasNote") is True)
    check("口径说明含默认单价出处",
          "0.5224" in (info.get("noteText") or "") and
          "成都" in (info.get("noteText") or ""),
          (info.get("noteText") or "")[:120])
    check("没有档位价目表", info.get("hasTable") is False)
    check("只有 1 个输入框", info.get("inputCount") == 1, str(info.get("inputCount")))
    check("没有 <select> 下拉", info.get("selectCount") == 0, str(info.get("selectCount")))

    # ---------- 输入自定义单价，等 live 重建 ----------
    print("\n--- 输入自定义单价，等待 live 重建 ---")
    c.js("""(function(){
      var tb = %s;
      var rt = tb.querySelector('[data-tariff-rate]');
      rt.focus();
      rt.value = '0.8888';
      rt.dispatchEvent(new Event('input', {bubbles:true}));
      return rt.value;
    })()""" % tb)
    c.pump(1)
    v0 = c.js("""(function(){
      var tb = %s;
      return (tb.querySelector('[data-tariff-rate]')||{}).value;
    })()""" % tb)
    check("输入后立刻读到 0.8888", v0 == "0.8888", str(v0))

    print("  ...等待 9 秒（跨过 3 轮 live 重建）...")
    c.pump(9)
    v1 = c.js("""(function(){
      var tb = %s;
      var rt = tb.querySelector('[data-tariff-rate]');
      return JSON.stringify({
        value: rt ? rt.value : null,
        focused: document.activeElement === rt,
        rendered: tb.getAttribute('data-tariff-rendered')
      });
    })()""" % tb)
    st = json.loads(v1 or "{}")
    check("9 秒后自定义单价仍是 0.8888（没被清空）",
          st.get("value") == "0.8888", str(st.get("value")))
    check("输入框仍持有焦点（重绘没有踢掉焦点）",
          st.get("focused") is True)

    # 再等一轮
    c.pump(4)
    v2 = c.js("""(function(){
      var tb = %s;
      var rt = tb.querySelector('[data-tariff-rate]');
      return rt ? rt.value : null;
    })()""" % tb)
    check("再等 4 秒后仍是 0.8888", v2 == "0.8888", str(v2))

    # ---------- 截图 ----------
    c.js("""(function(){
      var tb = %s;
      tb.scrollIntoView({block:'center'});
      return 'ok';
    })()""" % tb)
    c.pump(1.5)
    shot = c.send("Page.captureScreenshot", {"format": "png"})
    with open("/tmp/fnstyle_v1163_tariff.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("\nscreenshot -> /tmp/fnstyle_v1163_tariff.png")

    print("\n--- 错误检查 ---")
    errs = 0
    for e in c.events:
        if e.get("method") == "Runtime.exceptionThrown":
            print("[exception]",
                  str(e["params"].get("exceptionDetails", {}).get("text"))[:200])
            errs += 1
    print("exceptions=%d" % errs)
    if errs:
        FAIL.append("控制台有 %d 个异常" % errs)

    ws.close()
    print("\n" + "=" * 60)
    if FAIL:
        print("❌ 失败 %d 项: %s" % (len(FAIL), "; ".join(FAIL)))
        raise SystemExit(1)
    print("✅ 全部通过")


if __name__ == "__main__":
    main()
