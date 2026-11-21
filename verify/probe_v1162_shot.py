#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 截图（v1.1.62）：实机抓「本月用电」格下方的判档说明行 + 预计电费单价行

背景
----
v1.1.62 新增两处前端说明：
  ① 「本月用电」格下方 → 「判档：本月 X kWh → 第 N 档（区间）」
     —— 回答用户的根本质疑「我没输入电量，你怎么知道按哪一档计费」
  ② 「本月预计电费」格下方 → 「0.5224 元/kWh」
     —— 说明预计电费用的是可调的单一估算单价，不是按阶梯算

前置：Chrome 已带 --remote-debugging-port=9222 启动。
"""
import base64
import json
import sys
import time
import urllib.request

import websocket

URL = "http://192.168.5.8:9910/"
OUT = "/tmp/fnstyle_v1162_energy_tier.png"


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

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

    def js(self, expr):
        r = self.send("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            time.sleep(0.12)


def main():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    tabs = json.loads(op.open("http://127.0.0.1:9222/json/list",
                              timeout=5).read().decode())
    pages = [t for t in tabs if t.get("type") == "page"
             and t.get("webSocketDebuggerUrl")]
    if not pages:
        print("!! 没有可用 page target，先跑 verify/cdp_open_tab.py")
        return 2
    page = pages[0]
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1500, "deviceScaleFactor": 2,
            "mobile": False})
    c.send("Page.navigate", {"url": URL})
    c.pump(8)
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1500, "deviceScaleFactor": 2,
            "mobile": False})
    c.pump(2)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(5)

    # 能耗卡片区域
    # ⚠️ 必须先 scrollIntoView —— 卡片在文档流很靠下的位置，
    #    直接取 getBoundingClientRect 得到的 y 可能超出视口 → 截出空白。
    c.js("""
    (function(){
      var els = document.querySelectorAll('.stat-cell');
      for (var i=0;i<els.length;i++){
        if ((els[i].textContent||'').indexOf('本月用电')>=0){
          els[i].scrollIntoView({block:'center'});
          return 'scrolled';
        }
      }
      return 'not-found';
    })()
    """)
    c.pump(2)

    box = c.js("""
    (function(){
      var els = document.querySelectorAll('.stat-cell');
      for (var i=0;i<els.length;i++){
        if ((els[i].textContent||'').indexOf('本月用电')>=0){
          var r = els[i].getBoundingClientRect();
          return JSON.stringify({x:Math.max(0,r.x-14),y:Math.max(0,r.y-14),
                                  w:r.width+28,h:r.height+28});
        }
      }
      return '';
    })()
    """)
    print("能耗卡片 bbox:", box)

    if box:
        b = json.loads(box)
        # 用 clip 坐标是**文档坐标**，而 getBoundingClientRect 是**视口坐标**
        # → 加上滚动偏移量才是正确的文档坐标。
        scroll = c.js("JSON.stringify({x:window.scrollX,y:window.scrollY})")
        off = json.loads(scroll or '{"x":0,"y":0}')
        b["x"] += off["x"]
        b["y"] += off["y"]
        print("文档坐标:", b)
        c.send("Emulation.setDeviceMetricsOverride",
               {"width": 1440, "height": 1500, "deviceScaleFactor": 3,
                "mobile": False})
        c.pump(1)
        shot = c.send("Page.captureScreenshot",
                      {"format": "png",
                       "captureBeyondViewport": True,
                       "clip": {"x": b["x"], "y": b["y"],
                                "width": b["w"], "height": b["h"],
                                "scale": 3}})
        with open(OUT, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("截图已保存：", OUT)
    else:
        shot = c.send("Page.captureScreenshot", {"format": "png"})
        with open(OUT, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("未定位到卡片，截全页：", OUT)

    # 打印两处说明行的实际文本
    txt = c.js("""
    (function(){
      var out = [];
      document.querySelectorAll('.stat-cell').forEach(function(el){
        var t = (el.textContent||'').replace(/\\s+/g,' ').trim();
        if (t.indexOf('本月用电')>=0 || t.indexOf('本月预计电费')>=0){
          out.push(t);
        }
      });
      return JSON.stringify(out);
    })()
    """)
    print("\n--- 能耗卡片文本 ---")
    try:
        for line in json.loads(txt or "[]"):
            print("  ", line)
    except Exception:
        print("  ", txt)

    tier = c.js("""
    (function(){
      var e = document.querySelector('.sub-tier');
      return e ? (e.textContent||'').trim() : '(无 .sub-tier 元素)';
    })()
    """)
    rate = c.js("""
    (function(){
      var e = document.querySelector('.sub-proj-rate');
      return e ? (e.textContent||'').trim() : '(无 .sub-proj-rate 元素)';
    })()
    """)
    print("\n.sub-tier       :", tier)
    print(".sub-proj-rate  :", rate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
