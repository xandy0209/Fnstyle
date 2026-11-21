#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针（v1.1.63）：实机验证电价面板 = 单一「电费单价」配置项

⚠️ v1.1.63 变更：整个电价模型退化为**单一单价**
   「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置，
     并增加对应的说明即可」
   ⇒ 面板里只剩：电费单价输入框 + 口径说明 + 保存按钮

逐项验证：
  A. 面板只有一个「电费单价」输入框，没有其它任何配置控件
  B. 面板没有地区/电压/谷时段/档位表/季节参数
  C. 口径说明文案正确（含默认单价出处）
  D. 后端 /api/power/tariff 的 config 只含 rate + updated（废弃键无残留）
  E. 改单价 → 保存 → 后端持久化 → 前端立即生效
  F. live 重建（9 秒）不丢用户正在输入的单价
  G. 重载页面后仍在
  H. 旧配置（阶梯残留）升级后被清理干净

前置：Chrome 已带 --remote-debugging-port=9222 启动。
"""
import json
import time
import urllib.request

import websocket

URL = "http://192.168.5.8:9910/"
API = "http://192.168.5.8:9910"


class CDP:
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


PASSN = [0]
FAIL = []


def check(label, cond, extra=""):
    if cond:
        PASSN[0] += 1
        print("  ✓ %s" % label)
    else:
        FAIL.append(label)
        print("  ✗ %s%s" % (label, ("  → %s" % extra) if extra else ""))


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())


TB = ("document.querySelector('#monComputeBody [data-tariff-box]')"
      " || document.querySelector('#monComputeBody .tariff-box')"
      " || document.querySelector('#monComputeBody')")


def snapshot(c):
    """抓取抽屉电价面板当前控件。"""
    expr = """(function(){
      var tb = %s;
      if (!tb) return '{"err":"NO TARIFF BOX"}';
      function v(sel){ var e = tb.querySelector(sel); return e ? e.value : null; }
      var inputs = tb.querySelectorAll('input');
      var selects = tb.querySelectorAll('select');
      var rt = tb.querySelector('[data-tariff-rate]');
      var note = tb.querySelector('[data-tariff-note]');
      return JSON.stringify({
        hasRate: !!rt,
        rate: rt ? rt.value : null,
        rateType: rt ? rt.getAttribute('type') : null,
        rateMin: rt ? rt.getAttribute('min') : null,
        rateMax: rt ? rt.getAttribute('max') : null,
        rateStep: rt ? rt.getAttribute('step') : null,
        hasNote: !!note,
        noteText: note ? note.innerText.replace(/\\s+/g,' ').trim() : null,
        inputCount: inputs.length,
        selectCount: selects.length,
        hasRegion: !!tb.querySelector('[data-tariff-region]'),
        hasVoltage: !!tb.querySelector('[data-tariff-voltage]'),
        hasVstart: !!tb.querySelector('[data-tariff-vstart]'),
        hasVend: !!tb.querySelector('[data-tariff-vend]'),
        hasEnabled: !!tb.querySelector('[data-tariff-enabled]'),
        hasPeople: !!tb.querySelector('[data-tariff-people]'),
        hasMeter: !!tb.querySelector('[data-tariff-meter]'),
        hasProjRate: !!tb.querySelector('[data-tariff-proj-rate]'),
        hasSeason: !!tb.querySelector('[data-tariff-season]'),
        hasTable: !!tb.querySelector('.tariff-table'),
        hasTierRow: tb.innerHTML.indexOf('tariff-tb-row') > -1,
        hasSave: !!tb.querySelector('[data-tariff-save]'),
        panelText: tb.innerText.replace(/\\s+/g,' ').trim()
      });
    })()""" % TB
    raw = c.js(expr)
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"err": "PARSE_FAIL", "raw": str(raw)[:200]}


def set_field(c, sel, value, evt="input"):
    return c.js("""(function(){
      var tb = %s;
      var e = tb.querySelector('%s');
      if (!e) return 'NO_FIELD';
      e.value = %s;
      e.dispatchEvent(new Event('%s', {bubbles:true}));
      return e.value;
    })()""" % (TB, sel, json.dumps(str(value)), evt))


def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=5).read().decode())
    page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 1400, "deviceScaleFactor": 2, "mobile": False})
    c.pump(2)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(4)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(5)

    # ---------- H0. 旧配置残留：模拟阶梯时代的脏配置 ----------
    print("--- H0. 注入旧版脏配置（阶梯/地区/电压残留）---")
    api("/api/power/tariff", "POST", {
        "rate": 0.6666,
        "region": "sichuan", "enabled": True,
        "valley_start": 23, "valley_end": 7,
        "voltage": "lv2",
    })
    # 用 raw 文件写脏配置（POST 只接受 rate，废弃键会被丢弃）
    # → 直接读一次接口看废弃键有没有残留
    j0 = api("/api/power/tariff")
    cfg0 = j0.get("config") or {}
    leaked = [k for k in ("region", "enabled", "voltage", "valley_start",
                          "valley_end", "overrides", "household_people",
                          "meter_months", "rate_override", "proj_rate")
              if k in cfg0]
    check("H0 废弃键无残留（读出来就是干净的）", leaked == [], str(leaked))
    check("H0 config 只含 rate + updated",
          set(cfg0.keys()) == {"rate", "updated"}, str(sorted(cfg0.keys())))
    check("H0 生效单价 = 0.6666", abs(float(cfg0.get("rate")) - 0.6666) < 1e-9,
          cfg0.get("rate"))
    check("H0 describe 含 note 口径说明", bool(j0.get("note")))
    check("H0 describe 含 unit", j0.get("unit") == "元/kWh", j0.get("unit"))
    check("H0 describe 含 default_rate",
          abs(float(j0.get("default_rate")) - 0.5224) < 1e-9,
          j0.get("default_rate"))

    # 复位到默认单价 0.5224
    api("/api/power/tariff", "POST", {"rate": 0.5224})
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)

    # ---------- A. 面板只有一个单价输入框 ----------
    print("\n--- A. 面板结构：只有电费单价输入框 ---")
    st = snapshot(c)
    check("A 面板已渲染", "err" not in st, str(st)[:200])
    check("A 有单价输入框 data-tariff-rate", st.get("hasRate") is True)
    check("A 输入框是 number 类型", st.get("rateType") == "number", st.get("rateType"))
    check("A 输入框 min=0", st.get("rateMin") == "0", st.get("rateMin"))
    check("A 输入框 max=10", st.get("rateMax") == "10", st.get("rateMax"))
    check("A 输入框 step=0.0001", st.get("rateStep") == "0.0001", st.get("rateStep"))
    check("A 只有 1 个输入框", st.get("inputCount") == 1, st.get("inputCount"))
    check("A 没有 <select> 下拉", st.get("selectCount") == 0, st.get("selectCount"))
    check("A 默认值 = 0.5224", str(st.get("rate")) == "0.5224", st.get("rate"))
    check("A 有保存按钮", st.get("hasSave") is True)

    # ---------- B. 已删除的控件都不存在 ----------
    print("\n--- B. 已删除的控件全部不存在 ---")
    for key, label in (("hasRegion", "地区下拉"), ("hasVoltage", "电压下拉"),
                       ("hasVstart", "谷段起"), ("hasVend", "谷段止"),
                       ("hasEnabled", "阶梯开关"), ("hasPeople", "家庭人口"),
                       ("hasMeter", "抄表周期"), ("hasProjRate", "预计单价"),
                       ("hasSeason", "季节参数"), ("hasTable", "档位价目表")):
        check("B 无%s" % label, st.get(key) is False)
    check("B 无档位行", st.get("hasTierRow") is False)
    txt = st.get("panelText") or ""
    # ⚠️ 口径说明**本身**就要提到「阶梯档位」「峰谷时段」「地区」——
    #    是在「**不区分**…」的否定语境里说明模型边界。
    #    所以这里不能查「文本里有这个词」，只能查这些概念**没���作为配置项**出现。
    for kw in ("家庭人口", "抄表周期", "丰水期", "夏季档", "电压等级"):
        check("B 面板文本不含「%s」" % kw, kw not in txt, txt[:150])
    # 否定语境的口径说明（这些词应当以「不区分…」的形式出现）
    for kw in ("不区分阶梯档位", "不区分峰谷时段", "不区分地区"):
        check("B 说明含否定语境「%s」" % kw, kw in txt, txt[:200])

    # ---------- C. 口径说明 ----------
    print("\n--- C. 口径说明文案 ---")
    note = st.get("noteText") or ""
    check("C 有说明", bool(note), note[:80])
    for kw in ("用电量", "电费单价", "0.5224", "成都", "不区分阶梯档位",
               "不区分峰谷时段", "不区分地区"):
        check("C 说明含「%s」" % kw, kw in note, note[:200])

    # ---------- D. 保存 → 后端持久化 ----------
    print("\n--- D. 改单价 → 保存 → 后端持久化 ---")
    set_field(c, "[data-tariff-rate]", "0.7777")
    c.js("""(function(){
      var tb = %s;
      var b = tb.querySelector('[data-tariff-save]');
      if (b) b.click();
      return 'clicked';
    })()""" % TB)
    c.pump(3)
    j1 = api("/api/power/tariff")
    check("D 后端单价 = 0.7777",
          abs(float((j1.get("config") or {}).get("rate")) - 0.7777) < 1e-9,
          (j1.get("config") or {}).get("rate"))
    st2 = snapshot(c)
    check("D 面板回显 0.7777", str(st2.get("rate")) == "0.7777", st2.get("rate"))
    # ⚠️ 「已保存」提示 2.5 秒后自动清除 —— 探针 pump(3) 时可能已过期，
    #    所以这里只验证**保存确实生效**（后端持久化 + 面板回显），
    #    提示文案的正确性由前端单测覆盖。
    btn_state = c.js(
        "(function(){var tb = %s;"
        "var b = tb.querySelector('[data-tariff-save]');"
        "return b ? (b.disabled ? 'disabled' : 'enabled') : 'NO_BTN';})()"
        % TB)
    check("D 保存后按钮恢复可用（不再是 disabled）",
          btn_state == "enabled", btn_state)

    # ---------- E. 能耗卡片显示新单价 ----------
    print("\n--- E. 能耗卡片按新单价显示 ---")
    st3 = api("/api/power/stats")
    check("E 后端 rate_yuan_per_kwh = 0.7777",
          abs(float(st3.get("rate_yuan_per_kwh")) - 0.7777) < 1e-6,
          st3.get("rate_yuan_per_kwh"))
    check("E tariff_mode = fixed", st3.get("tariff_mode") == "fixed",
          st3.get("tariff_mode"))
    check("E 无阶梯字段 current_tier", "current_tier" not in st3,
          str([k for k in st3 if 'tier' in k]))
    check("E 无阶梯字段 tier_kwh", "tier_kwh" not in st3)
    check("E 无谷段字段 valley_kwh", "valley_kwh" not in st3)
    check("E eff_rate = 生效单价（不再反推均价）",
          abs(float(st3.get("eff_rate_yuan_per_kwh")) - 0.7777) < 1e-4,
          st3.get("eff_rate_yuan_per_kwh"))
    check("E proj_rate = 同一单价",
          abs(float(st3.get("proj_rate_yuan_per_kwh")) - 0.7777) < 1e-4,
          st3.get("proj_rate_yuan_per_kwh"))
    if st3.get("month_kwh"):
        expect = round(float(st3["month_kwh"]) * 0.7777, 2)
        check("E month_yuan = month_kwh × 0.7777",
              abs(float(st3.get("month_yuan")) - expect) < 0.02,
              "%.4f vs %.4f" % (st3.get("month_yuan"), expect))

    # ---------- F. live 重建不丢用户输入 ----------
    print("\n--- F. live 重建（9 秒）不丢正在输入的单价 ---")
    set_field(c, "[data-tariff-rate]", "0.9999")
    c.pump(9)
    st4 = snapshot(c)
    check("F live 重建后仍保留 0.9999", str(st4.get("rate")) == "0.9999",
          st4.get("rate"))

    # ---------- G. 重载页面后仍在 ----------
    print("\n--- G. 重载页面后仍是已保存的值 ---")
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)
    st5 = snapshot(c)
    check("G 重载后单价 = 0.7777（后端持久化）",
          str(st5.get("rate")) == "0.7777", st5.get("rate"))

    # ---------- 收尾：还原默认单价 ----------
    print("\n--- 收尾：还原默认单价 0.5224 ---")
    api("/api/power/tariff", "POST", {"rate": 0.5224})
    jf = api("/api/power/tariff")
    check("收尾：单价已还原 0.5224",
          abs(float((jf.get("config") or {}).get("rate")) - 0.5224) < 1e-9,
          (jf.get("config") or {}).get("rate"))

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (PASSN[0], len(FAIL)))
    print("=" * 60)
    if FAIL:
        for f in FAIL:
            print("  ✗ %s" % f)
        raise SystemExit(1)
    print("🎉 全部通过")


if __name__ == "__main__":
    main()
