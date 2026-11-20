#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针（v1.1.59）：实机验证三个新增配置项（电压等级 / 一户多人口 / 抄表周期）

依据官方 PDF（成都居民阶梯电价表 2026-07-01 起执行）新增的三项：
  ① 电压等级：不满1千伏 / 1-10千伏 / 35-110千伏（峰价不同）
  ② 一户多人口：满 5 人每户每月 +100 度，超出 5 人按 30 度/人·月递增（川发改价格〔2024〕668号）
  ③ 抄表周期：两个月一次时，分档电量按同档次月用电量的 2 倍确定（川发改价格〔2012〕560号）

逐项验证：
  A. 四川地区 → 三个控件都出现
  B. 天津/固定单价地区 → 三个控件都**不**出现（地方政策不外溢）
  C. 切电压等级 → 价目表峰价随之变化（0.5224 → 0.5124）
  D. 填家庭人口 → 计费真的按人口加成走（后端接口读回一致）
  E. 填抄表周期 → 门槛翻倍 + 用电窗口翻倍（后端接口读回 bill_months=2）
  F. 保存后后端配置持久化，重载页面仍在
  G. live 重建（9 秒）不丢三个控件的值
  H. 切地区后三项正确回落（不留旧地区残留）

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
        if r.get("exceptionDetails"):
            return None
        return r.get("result", {}).get("value")


FAIL = []
PASSN = [0]


def check(label, ok, extra=""):
    if ok:
        PASSN[0] += 1
        print("  ✓ %s%s" % (label, ("  → " + extra) if extra else ""))
    else:
        print("  ✗ %s  → %s" % (label, extra))
        FAIL.append(label)


def api(path, method="GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


TB = "document.querySelector('#monComputeBody [data-tariff-box]')"


def snapshot(c):
    """抓取抽屉电价面板当前三个控件 + 价目表文本。"""
    expr = """(function(){
      var tb = %s;
      if (!tb) return '{"err":"NO TARIFF BOX"}';
      function v(sel){ var e = tb.querySelector(sel); return e ? e.value : null; }
      var tt = tb.querySelector('.tariff-table');
      var opts = [];
      var vo = tb.querySelector('[data-tariff-voltage]');
      if (vo) {
        for (var i=0;i<vo.options.length;i++){
          opts.push(vo.options[i].value+'|'+vo.options[i].textContent.trim());
        }
      }
      return JSON.stringify({
        region: v('[data-tariff-region]'),
        hasVoltage: !!vo,
        hasPeople: !!tb.querySelector('[data-tariff-people]'),
        hasMeter: !!tb.querySelector('[data-tariff-meter]'),
        voltage: v('[data-tariff-voltage]'),
        people: v('[data-tariff-people]'),
        meter: v('[data-tariff-meter]'),
        voltageOptions: opts,
        hasTable: !!tt,
        tableText: tt ? tt.innerText.replace(/\\s+/g,' ').trim() : null,
        panelText: tb.innerText.replace(/\\s+/g,' ').trim()
      });
    })()""" % TB
    raw = c.js(expr)
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"err": "PARSE_FAIL", "raw": str(raw)[:200]}


def set_field(c, sel, value, evt="change"):
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

    # 干净起点：把配置复位成四川 + 默认值
    # ⚠️ 后端 save_config 强制校验 valley_start/valley_end 必须是 0-23 的整数，
    #    缺这两个字段会直接 400（前端保存时是带上的，这里补齐）。
    print("--- 复位配置到四川/默认 ---")
    api("/api/power/tariff", "POST", {
        "region": "sichuan", "enabled": True,
        "valley_start": 23, "valley_end": 7,
        "voltage": "", "household_people": 0, "meter_months": 1,
    })
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)

    # ---------- A. 四川地区三个控件都在 ----------
    print("\n--- A. 四川地区：三个新控件应全部出现 ---")
    st = snapshot(c)
    check("A 四川地区显示电压等级下拉", st.get("hasVoltage") is True)
    check("A 四川地区显示一户多人口输入", st.get("hasPeople") is True)
    check("A 四川地区显示抄表周期下拉", st.get("hasMeter") is True)
    check("A 电压等级下拉有 3 个选项",
          len(st.get("voltageOptions") or []) == 3,
          str(st.get("voltageOptions")))
    for opt in (st.get("voltageOptions") or []):
        if "35-110" in opt:
            check("A 电压选项含「35-110 千伏」", True, opt)
        if "不满 1 千伏" in opt:
            check("A 电压选项含「不满 1 千伏」", True, opt)

    # ---------- B. 其它地区不应出现 ----------
    print("\n--- B. 天津 / 固定单价：地方政策控件不应出现 ---")
    set_field(c, "[data-tariff-region]", "tianjin")
    c.pump(3)
    st2 = snapshot(c)
    check("B 天津地区不显示电压等级", st2.get("hasVoltage") is False)
    check("B 天津地区不显示一户多人口", st2.get("hasPeople") is False)
    check("B 天津地区不显示抄表周期", st2.get("hasMeter") is False)

    set_field(c, "[data-tariff-region]", "flat")
    c.pump(3)
    st3 = snapshot(c)
    check("B 固定单价地区不显示电压等级", st3.get("hasVoltage") is False)
    check("B 固定单价地区不显示一户多人口", st3.get("hasPeople") is False)
    check("B 固定单价地区不显示抄表周期", st3.get("hasMeter") is False)

    # 切回四川
    set_field(c, "[data-tariff-region]", "sichuan")
    c.pump(3)
    st4 = snapshot(c)
    check("B 切回四川后三个控件恢复", st4.get("hasVoltage") is True
          and st4.get("hasPeople") is True and st4.get("hasMeter") is True)

    # ---------- C. 电压等级切换 → 峰价变化 ----------
    print("\n--- C. 电压等级切换应改变峰价 ---")
    t_before = st4.get("tableText") or ""
    check("C 默认（不满1千伏）峰价 0.5224 出现", "0.5224" in t_before,
          t_before[:90])
    set_field(c, "[data-tariff-voltage]", "lv2")
    c.pump(3)
    st5 = snapshot(c)
    t_after = st5.get("tableText") or ""
    check("C 切到 1-10 千伏后峰价变为 0.5124", "0.5124" in t_after,
          t_after[:90])
    check("C 切换后不再显示 0.5224", "0.5224" not in t_after,
          t_after[:90])
    check("C 电压下拉值已回显 lv2", st5.get("voltage") == "lv2",
          str(st5.get("voltage")))

    # 切到第三档
    set_field(c, "[data-tariff-voltage]", "lv3")
    c.pump(3)
    st6 = snapshot(c)
    check("C 切到 35-110 千伏后仍是 0.5124（同 1-10 千伏）",
          "0.5124" in (st6.get("tableText") or ""),
          (st6.get("tableText") or "")[:90])

    # ---------- G. live 重建不丢值 ----------
    print("\n--- G. live 重建（9 秒）后三个控件的值不丢 ---")
    set_field(c, "[data-tariff-voltage]", "lv1")
    c.pump(2)
    set_field(c, "[data-tariff-people]", "6", evt="input")
    c.pump(2)
    set_field(c, "[data-tariff-meter]", "2")
    c.pump(2)
    pre = snapshot(c)
    check("G 设置后：电压 lv1", pre.get("voltage") == "lv1", str(pre.get("voltage")))
    check("G 设置后：人口 6", pre.get("people") == "6", str(pre.get("people")))
    check("G 设置后：抄表周期 2", pre.get("meter") == "2", str(pre.get("meter")))

    print("  …等待 9 秒（跨过 3 轮 live 重建）…")
    c.pump(9)
    post = snapshot(c)
    check("G 9 秒后电压仍是 lv1", post.get("voltage") == "lv1", str(post.get("voltage")))
    check("G 9 秒后人口仍是 6", post.get("people") == "6", str(post.get("people")))
    check("G 9 秒后抄表周期仍是 2", post.get("meter") == "2", str(post.get("meter")))

    # ---------- D/E/F. 保存并核验后端 ----------
    print("\n--- D/E/F. 保存后后端配置与计费应按新参数走 ---")
    saved = c.js("""(function(){
      var btn = document.querySelector('#monComputeBody [data-tariff-save]')
             || document.querySelector('#monComputeBody .tariff-save');
      if (!btn) return 'NO_SAVE_BTN';
      btn.click();
      return 'clicked';
    })()""")
    check("找到并点击了保存按钮", saved == "clicked", str(saved))
    c.pump(4)

    cfg = api("/api/power/tariff")
    conf = cfg.get("config", {})
    check("F 后端 region=sichuan", conf.get("region") == "sichuan",
          str(conf.get("region")))
    check("F 后端 voltage=lv1", conf.get("voltage") == "lv1",
          str(conf.get("voltage")))
    check("F 后端 household_people=6", conf.get("household_people") == 6,
          str(conf.get("household_people")))
    check("F 后端 meter_months=2", conf.get("meter_months") == 2,
          str(conf.get("meter_months")))

    # 抄表周期 ×2 → 门槛翻倍
    # ⚠️ 口径修正（v1.1.59 修复后）：active_tiers 现在是**本月实际生效**的门槛，
    #    含抄表周期 ×2 + 一户多人口加成 + 结转。
    #    本次保存的是 6 人 + 双月 → 一档 = 180×2 + 130 = 490；二档累计 = 280×2 + 130 = 690。
    tiers = cfg.get("active_tiers") or []
    check("E 抄表周期2月 + 6人 → 一档生效门槛 490（360+130）",
          abs((tiers[0] if tiers else 0) - 490.0) < 0.01, str(tiers))
    check("E 抄表周期2月 + 6人 → 二档生效门槛 690（560+130）",
          abs((tiers[1] if len(tiers) > 1 else 0) - 690.0) < 0.01, str(tiers))

    # 一户多人口：满 5 人 → +100 度；第 6 人 → +30 度/人 → 130 度
    extra = cfg.get("multi_person_extra")
    check("D 6 人 → 一户多人口加 130 度（100+30）",
          extra is not None and abs(float(extra) - 130.0) < 0.01, str(extra))

    # 峰价按电压等级（lv1 = 不满1千伏）
    peak = cfg.get("profile", {}).get("peak_rates")
    check("D 保存后峰价仍为不满1千伏 0.5224",
          peak is not None and abs(peak[0] - 0.5224) < 0.0001, str(peak))

    # ---------- H. 重载后仍在 ----------
    print("\n--- H. 重载页面后配置应持久化 ---")
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)
    st7 = snapshot(c)
    check("H 重载后电压仍为 lv1", st7.get("voltage") == "lv1", str(st7.get("voltage")))
    check("H 重载后人口仍为 6", st7.get("people") == "6", str(st7.get("people")))
    check("H 重载后抄表周期仍为 2", st7.get("meter") == "2", str(st7.get("meter")))

    # ---------- H2. 切地区后回落 ----------
    print("\n--- H2. 切到天津后三个控件应回落消失且不留残值 ---")
    set_field(c, "[data-tariff-region]", "tianjin")
    c.pump(3)
    st8 = snapshot(c)
    check("H2 切天津后电压控件消失", st8.get("hasVoltage") is False)
    check("H2 切天津后人口控件消失", st8.get("hasPeople") is False)
    check("H2 切天津后抄表周期控件消失", st8.get("hasMeter") is False)
    check("H2 切天津后面板文本不含「千伏」",
          "千伏" not in (st8.get("panelText") or ""),
          (st8.get("panelText") or "")[:120])

    set_field(c, "[data-tariff-region]", "sichuan")
    c.pump(3)
    st9 = snapshot(c)
    # ⚠️ 口径说明：voltage / household_people / meter_months 是**全局配置**
    #    （不是按地区存），所以切回四川应**恢复已保存的值**，
    #    而不是清空。天津不支持这些项 → 控件直接不渲染（上面已验证）。
    check("H2 切回四川后电压恢复为已保存的 lv1",
          st9.get("voltage") == "lv1", repr(st9.get("voltage")))
    check("H2 切回四川后人口恢复为已保存的 6",
          st9.get("people") == "6", repr(st9.get("people")))
    check("H2 切回四川后抄表周期恢复为已保存的 2",
          st9.get("meter") == "2", repr(st9.get("meter")))

    # ---------- 截图 ----------
    set_field(c, "[data-tariff-voltage]", "lv2")
    c.pump(2)
    set_field(c, "[data-tariff-people]", "6", evt="input")
    c.pump(2)
    c.js("""(function(){ var tb = %s;
      if (tb) tb.scrollIntoView({block:'center'}); return 'ok'; })()""" % TB)
    c.pump(1.5)
    shot = c.send("Page.captureScreenshot", {"format": "png"})
    import base64
    with open("/tmp/fnstyle_v1159_tariff_cfg.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("\n  截图已保存：/tmp/fnstyle_v1159_tariff_cfg.png")

    # ---------- 还原配置 ----------
    print("\n--- 还原配置 ---")
    api("/api/power/tariff", "POST", {
        "region": "sichuan", "enabled": True,
        "valley_start": 23, "valley_end": 7,
        "voltage": "", "household_people": 0, "meter_months": 1,
    })
    rc = api("/api/power/tariff").get("config", {})
    # ⚠️ 后端 save_config 会把空 voltage **规整成该地区默认档**（lv1 不满1千伏），
    #    这是设计如此（避免存一个无效值），所以这里断言 lv1 而不是空串。
    check("还原成功（voltage=lv1 默认档 / people 0 / meter 1）",
          rc.get("voltage") == "lv1" and rc.get("household_people") == 0
          and rc.get("meter_months") == 1, str(rc))

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (PASSN[0], len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        print("实机验证未通过")
        return 1
    print("实机验证全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
