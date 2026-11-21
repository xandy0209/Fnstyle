#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针（v1.1.62）：实机验证电价面板只剩「电压等级」一个新增配置项

⚠️ v1.1.62 变更：「家庭人口」「抄表周期」两个输入项**已删除**
   · 「一户多人口」（川发改价格〔2024〕668号）需用户**主动申报**才生效，
     软件里填个数字不代表供电企业已批准 → 留着反而误导；
   · 「抄表周期」（川发改价格〔2012〕560号）是两个月抄表场景，
     自动抄表时代已不适用。
   → 档位门槛回到官方基准（四川其他月 180/280）。

逐项验证：
  A. 四川地区 → 只出现电压等级下拉，**没有**人口/抄表控件
  B. 天津/固定单价地区 → 电压下拉也不出现
  C. 切电压等级 → 价目表峰价随之变化（0.5224 → 0.5124）
  D. 保存后后端配置持久化（voltage 生效）
  E. 生效门槛回到官方基准 180/280
  F. live 重建（9 秒）不丢电压等级的值
  G. 重载页面后仍在
  H. 切地区后电压正确回落（不留旧地区残留）

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
    """抓取抽屉电价面板当前控件 + 价目表文本。"""
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
    # ⚠️ v1.1.62：必须显式给 "lv1"，不能给 ""。
    #    后端 save_config 对空电压是「继承旧值」语义（v1.1.59 起），
    #    给 "" 会拿到上一轮跑测残留的电压等级 → C 项起点不确定。
    api("/api/power/tariff", "POST", {
        "region": "sichuan", "enabled": True,
        "valley_start": 23, "valley_end": 7,
        "voltage": "lv1",
    })
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)

    # ---------- A. 四川地区只剩电压下拉 ----------
    print("\n--- A. 四川地区：只剩电压等级下拉 ---")
    st = snapshot(c)
    check("A 四川地区显示电压等级下拉", st.get("hasVoltage") is True)
    check("A 四川地区**不**显示一户多人口输入", st.get("hasPeople") is False)
    check("A 四川地区**不**显示抄表周期下拉", st.get("hasMeter") is False)
    check("A 面板文本不含「家庭人口」", "家庭人口" not in (st.get("panelText") or ""),
          (st.get("panelText") or "")[:120])
    check("A 面板文本不含「抄表周期」", "抄表周期" not in (st.get("panelText") or ""),
          (st.get("panelText") or "")[:120])
    check("A 电压等级下拉有 3 个选项",
          len(st.get("voltageOptions") or []) == 3,
          str(st.get("voltageOptions")))
    for opt in (st.get("voltageOptions") or []):
        if "35-110" in opt:
            check("A 电压选项含「35-110 千伏」", True, opt)
        if "不满 1 千伏" in opt:
            check("A 电压选项含「不满 1 千伏」", True, opt)

    # ---------- B. 其它地区不应出现 ----------
    print("\n--- B. 天津 / 固定单价：电压下拉也不出现 ---")
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
    check("B 切回四川后电压控件恢复", st4.get("hasVoltage") is True)

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

    # ---------- F. live 重建不丢值 ----------
    print("\n--- F. live 重建（9 秒）后电压等级的值不丢 ---")
    set_field(c, "[data-tariff-voltage]", "lv1")
    c.pump(2)
    pre = snapshot(c)
    check("F 设置后：电压 lv1", pre.get("voltage") == "lv1", str(pre.get("voltage")))

    print("  …等待 9 秒（跨过 3 轮 live 重建）…")
    c.pump(9)
    post = snapshot(c)
    check("F 9 秒后电压仍是 lv1", post.get("voltage") == "lv1", str(post.get("voltage")))

    # ---------- D/E. 保存并核验后端 ----------
    print("\n--- D/E. 保存后后端配置与生效门槛应正确 ---")
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
    check("D 后端 region=sichuan", conf.get("region") == "sichuan",
          str(conf.get("region")))
    check("D 后端 voltage=lv1", conf.get("voltage") == "lv1",
          str(conf.get("voltage")))
    check("D 后端已无 household_people 键（v1.1.62）",
          "household_people" not in conf, sorted(conf.keys()))
    check("D 后端已无 meter_months 键（v1.1.62）",
          "meter_months" not in conf, sorted(conf.keys()))

    # ⚠️ v1.1.62：生效门槛回到官方基准（无人口加成、无抄表周期放大）
    tiers = cfg.get("active_tiers") or []
    check("E 一档生效门槛 180（官方基准）",
          abs((tiers[0] if tiers else 0) - 180.0) < 0.01, str(tiers))
    check("E 二档生效门槛 280（官方基准）",
          abs((tiers[1] if len(tiers) > 1 else 0) - 280.0) < 0.01, str(tiers))
    check("E describe 不再返回 multi_person_extra",
          "multi_person_extra" not in cfg, sorted(cfg.keys()))

    # 峰价按电压等级（lv1 = 不满1千伏）
    peak = cfg.get("profile", {}).get("peak_rates")
    check("D 保存后峰价仍为不满1千伏 0.5224",
          peak is not None and abs(peak[0] - 0.5224) < 0.0001, str(peak))

    # ---------- G. 重载后仍在 ----------
    print("\n--- G. 重载页面后配置应持久化 ---")
    c.send("Page.navigate", {"url": URL})
    c.pump(7)
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(4)
    st7 = snapshot(c)
    check("G 重载后电压仍为 lv1", st7.get("voltage") == "lv1", str(st7.get("voltage")))
    check("G 重载后仍无人口控件", st7.get("hasPeople") is False)
    check("G 重载后仍无抄表周期控件", st7.get("hasMeter") is False)

    # ---------- H2. 切地区后回落 ----------
    print("\n--- H2. 切到天津后电压控件应消失且不留残值 ---")
    set_field(c, "[data-tariff-region]", "tianjin")
    c.pump(3)
    st8 = snapshot(c)
    check("H2 切天津后电压控件消失", st8.get("hasVoltage") is False)
    check("H2 切天津后面板文本不含「千伏」",
          "千伏" not in (st8.get("panelText") or ""),
          (st8.get("panelText") or "")[:120])

    set_field(c, "[data-tariff-region]", "sichuan")
    c.pump(3)
    st9 = snapshot(c)
    # ⚠️ 口径说明：voltage 是**全局配置**（不是按地区存），
    #    所以切回四川应**恢复已保存的值**，而不是清空。
    check("H2 切回四川后电压恢复为已保存的 lv1",
          st9.get("voltage") == "lv1", repr(st9.get("voltage")))

    # ---------- 截图 ----------
    set_field(c, "[data-tariff-voltage]", "lv2")
    c.pump(2)
    c.js("""(function(){ var tb = %s;
      if (tb) tb.scrollIntoView({block:'center'}); return 'ok'; })()""" % TB)
    c.pump(1.5)
    shot = c.send("Page.captureScreenshot", {"format": "png"})
    import base64
    with open("/tmp/fnstyle_v1162_tariff_cfg.png", "wb") as f:
        f.write(base64.b64decode(shot["data"]))
    print("\n  截图已保存：/tmp/fnstyle_v1162_tariff_cfg.png")

    # ---------- 还原配置 ----------
    print("\n--- 还原配置 ---")
    # ⚠️ 同样要显式给 "lv1"：空串是「继承旧值」语义，
    #    跑测结束时电压可能已被改成 lv2 → 空串会把 lv2 留下来。
    api("/api/power/tariff", "POST", {
        "region": "sichuan", "enabled": True,
        "valley_start": 23, "valley_end": 7,
        "voltage": "lv1",
    })
    rc = api("/api/power/tariff").get("config", {})
    check("还原成功（voltage=lv1 默认档）",
          rc.get("voltage") == "lv1"
          and "household_people" not in rc
          and "meter_months" not in rc, str(rc))

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
