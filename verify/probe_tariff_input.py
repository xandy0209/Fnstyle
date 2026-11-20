#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针（v1.1.58）：实机验证两个用户反馈 bug

① 「选择固定单价时自定义单价输入后会被清空」
   验证：切到「固定单价」地区 → 输入自定义单价 → 等 8 秒（跨过 2 轮
   live 重建）→ 断言输入框里的值仍在。

② 「都固定单价了为什么还有档位信息？」
   验证：切到「固定单价」地区 → 断言面板里**没有** .tariff-table，
        且不含「第 1 档」字样；切回四川 → 断言档位表又回来了。

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
    print("--- 初始状态（成都 / 四川） ---")
    info = json.loads(c.js("""(function(){
      var tb = %s;
      if (!tb) return '{"err":"NO TARIFF BOX"}';
      return JSON.stringify({
        region: (tb.querySelector('[data-tariff-region]')||{}).value,
        hasTable: !!tb.querySelector('.tariff-table'),
        tableText: tb.querySelector('.tariff-table')
          ? tb.querySelector('.tariff-table').innerText.replace(/\\s+/g,' ').trim() : null,
        rendered: tb.getAttribute('data-tariff-rendered')
      });
    })()""" % tb) or "{}")
    check("初始为四川地区", info.get("region") == "sichuan", str(info.get("region")))
    check("四川地区显示档位价目表", info.get("hasTable") is True)
    check("四川档位表显示第一档区间",
          "第 1 档" in (info.get("tableText") or ""), (info.get("tableText") or "")[:60])

    # ---------- 切到「固定单价」 ----------
    print("\n--- 切到固定单价地区 ---")
    c.js("""(function(){
      var tb = %s;
      var rg = tb.querySelector('[data-tariff-region]');
      rg.value = 'flat';
      rg.dispatchEvent(new Event('change', {bubbles:true}));
      return 'ok';
    })()""" % tb)
    c.pump(3)

    info2 = json.loads(c.js("""(function(){
      var tb = %s;
      return JSON.stringify({
        region: (tb.querySelector('[data-tariff-region]')||{}).value,
        hasTable: !!tb.querySelector('.tariff-table'),
        text: tb.innerText.replace(/\\s+/g,' ').trim(),
        rate: (tb.querySelector('[data-tariff-rate]')||{}).value,
        rendered: tb.getAttribute('data-tariff-rendered')
      });
    })()""" % tb) or "{}")
    check("地区已切到 flat（固定单价）",
          info2.get("region") == "flat", str(info2.get("region")))
    check("② 固定单价地区**没有**档位价目表",
          info2.get("hasTable") is False,
          "panel text: " + (info2.get("text") or "")[:120])
    check("② 面板不含「第 1 档」字样",
          "第 1 档" not in (info2.get("text") or ""),
          (info2.get("text") or "")[:120])
    check("② 面板含「不区分时段与阶梯」说明",
          "不区分时段与阶梯" in (info2.get("text") or ""),
          (info2.get("text") or "")[:160])
    check("② 固定单价数值出现在说明里（0.6 元/kWh）",
          "0.6 元/kWh" in (info2.get("text") or ""),
          (info2.get("text") or "")[:160])
    check("② 说明只出现一次（不重复渲染两行）",
          (info2.get("text") or "").count("按单一固定单价计费") == 1,
          "出现 %d 次" % (info2.get("text") or "").count("按单一固定单价计费"))

    # ---------- ① 输入自定义单价，等 live 重建 ----------
    print("\n--- ① 输入自定义单价，等待 live 重建 ---")
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

    print("  …等待 9 秒（跨过 3 轮 live 重建）…")
    c.pump(9)
    v1 = c.js("""(function(){
      var tb = %s;
      var rt = tb.querySelector('[data-tariff-rate]');
      return JSON.stringify({
        value: rt ? rt.value : null,
        focused: document.activeElement === rt,
        rendered: tb.getAttribute('data-tariff-rendered'),
        hasTable: !!tb.querySelector('.tariff-table')
      });
    })()""" % tb)
    st = json.loads(v1 or "{}")
    check("① 9 秒后自定义单价仍是 0.8888（没被清空）",
          st.get("value") == "0.8888", str(st.get("value")))
    check("① 输入框仍持有焦点（重绘没有踢掉焦点）",
          st.get("focused") is True)

    # 再等一轮
    c.pump(4)
    v2 = c.js("""(function(){
      var tb = %s;
      var rt = tb.querySelector('[data-tariff-rate]');
      return rt ? rt.value : null;
    })()""" % tb)
    check("① 再等 4 秒后仍是 0.8888", v2 == "0.8888", str(v2))

    # ---------- 截图 ----------
    c.js("""(function(){
      var tb = %s;
      tb.scrollIntoView({block:'center'});
      return 'ok';
    })()""" % tb)
    c.pump(1.5)
    shot = c.send("Page.captureScreenshot", {"format": "png"})
    with open("/tmp/fnstyle_v1158_flat.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("\nscreenshot -> /tmp/fnstyle_v1158_flat.png")

    # ---------- 切回四川，确认档位表恢复 ----------
    print("\n--- 切回四川（档位表应恢复） ---")
    c.js("""(function(){
      var tb = %s;
      var rg = tb.querySelector('[data-tariff-region]');
      rg.value = 'sichuan';
      rg.dispatchEvent(new Event('change', {bubbles:true}));
      return 'ok';
    })()""" % tb)
    c.pump(3)
    info3 = json.loads(c.js("""(function(){
      var tb = %s;
      return JSON.stringify({
        region: (tb.querySelector('[data-tariff-region]')||{}).value,
        hasTable: !!tb.querySelector('.tariff-table'),
        rate: (tb.querySelector('[data-tariff-rate]')||{}).value
      });
    })()""" % tb) or "{}")
    check("切回四川成功", info3.get("region") == "sichuan", str(info3.get("region")))
    check("切回四川后档位表恢复", info3.get("hasTable") is True)
    check("切地区后自定义单价被清空（不带 flat 的值过去）",
          (info3.get("rate") or "") == "", repr(info3.get("rate")))

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
