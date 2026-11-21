#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针（v1.1.60）：实机验证「推导解释行」与「季节参数折叠」

改动背景
--------
用户反馈电价面板上方档位表（0–490 / 491–690）与下面两个档位输入框
看着像重复。核查结论：上方是**结果**、下方是**参数**，不是重复；
但表格下方那行说明文字（7–9 月档位 260/460、6–10 月低谷价 0.175）
**全是下方输入框已有的数字** → 纯重复；
而真正该说的「490/690 怎么来的」反而缺失。

本轮改动：
  ① 表格下方重复说明 → 换成**推导解释行**（讲清生效门槛的来源）
  ② 季节参数区块 → 默认折叠

逐项验证：
  A. 推导解释行存在，且含生效档位数字
  B. 旧重复说明文字已消失
  C. 6 人 + 双月抄表 → 解释行含 490/690 与「×2」「130 度」
  D. 季节参数默认折叠，展开后参数可见
  E. 切地区后解释行同步更新（不残留旧地区）
  F. 天津等固定单价地区不显示推导行/季节参数
  G. 保存后重载页面，推导行仍在
  H. 电压等级切换后峰价与解释行都正常

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


def snap(c):
    """抓抽屉电价面板的推导行、折叠态、价目表文本。"""
    expr = """(function(){
      var tb = %s;
      if (!tb) return '{"err":"NO TARIFF BOX"}';
      var derive = tb.querySelector('.tariff-tb-derive');
      var season = tb.querySelector('[data-tariff-season]');
      var body = tb.querySelector('.tariff-season-body');
      var toggle = tb.querySelector('[data-tariff-season-toggle]');
      var tt = tb.querySelector('.tariff-table');
      function v(sel){ var e = tb.querySelector(sel); return e ? e.value : null; }
      return JSON.stringify({
        region: v('[data-tariff-region]'),
        people: v('[data-tariff-people]'),
        meter: v('[data-tariff-meter]'),
        voltage: v('[data-tariff-voltage]'),
        hasDerive: !!derive,
        deriveText: derive ? derive.innerText.replace(/\\s+/g,' ').trim() : null,
        hasSeason: !!season,
        seasonCollapsed: season ? season.classList.contains('collapsed') : null,
        hasToggle: !!toggle,
        bodyVisible: body ? (body.offsetParent !== null || getComputedStyle(body).display !== 'none') : null,
        tableText: tt ? tt.innerText.replace(/\\s+/g,' ').trim() : null,
        panelText: tb.innerText.replace(/\\s+/g,' ').trim()
      });
    })()""" % TB
    raw = c.js(expr)
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {"err": "PARSE_FAIL", "raw": str(raw)[:300]}


def set_field(c, sel, value, evt="change"):
    return c.js("""(function(){
      var tb = %s;
      var e = tb.querySelector('%s');
      if (!e) return 'NO_FIELD';
      e.value = %s;
      e.dispatchEvent(new Event('%s', {bubbles:true}));
      return e.value;
    })()""" % (TB, sel, json.dumps(str(value)), evt))


def click(c, sel):
    return c.js("""(function(){
      var tb = %s;
      var e = tb.querySelector('%s');
      if (!e) return 'NO_EL';
      e.click();
      return 'CLICKED';
    })()""" % (TB, sel))


def open_panel(c):
    """确保抽屉与系统监控页打开，能耗抽屉可见（沿用已有探针的做法）。"""
    c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
    c.pump(3)
    c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
    c.pump(6)


def main():
    # 找页面
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read().decode())
    pages = [p for p in tabs if p.get("type") == "page"]
    if not pages:
        print("没有可用的 Chrome 页面")
        return 1
    target = None
    for p in pages:
        if "192.168.5.8" in (p.get("url") or ""):
            target = p
            break
    if not target:
        target = pages[0]

    ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30)
    c = CDP(ws)
    c.send("Page.enable")
    c.send("Runtime.enable")

    print("=== 加载实机页面 ===")
    c.send("Page.navigate", {"url": URL})
    time.sleep(4)
    c.pump(2)

    # 备份原配置（GET /api/power/tariff 返回 {config:{...}}）
    orig = api("/api/power/tariff").get("config", {})
    print("原配置:", json.dumps(orig, ensure_ascii=False)[:200])

    try:
        open_panel(c)
        s = snap(c)
        print("\n面板快照:", json.dumps(s, ensure_ascii=False)[:600])

        # ── A. 推导解释行存在 ──────────────────────────────────
        print("\n[A] 推导解释行")
        check("推导解释行存在", s.get("hasDerive") is True, str(s.get("hasDerive")))
        dt = s.get("deriveText") or ""
        check("解释行非空", len(dt) > 5, dt[:120])
        check("解释行含「生效口径」", "生效口径" in dt, dt[:80])

        # ── B. 旧重复说明消失 ──────────────────────────────────
        print("\n[B] 旧重复说明已移除")
        pt = s.get("panelText") or ""
        check("不再出现「7–9 月档位 0–260 / 261–460 / 460+」",
              "261–460" not in pt, pt[:160])
        check("不再出现「6–10 月低谷价 0.175」",
              "6–10 月低谷价" not in pt, pt[:160])

        # ── C. 6 人 + 双月 → 490/690 ───────────────────────────
        print("\n[C] 6 人 + 两个月抄表 → 解释行显示推导链")
        set_field(c, "[data-tariff-people]", "6")
        set_field(c, "[data-tariff-meter]", "2")
        time.sleep(0.6)
        click(c, "[data-tariff-save]")
        time.sleep(2.5)
        s2 = snap(c)
        dt2 = s2.get("deriveText") or ""
        print("  解释行:", dt2)
        check("解释行含生效一档 490", "490" in dt2, dt2[:160])
        check("解释行含生效二档 690", "690" in dt2, dt2[:160])
        check("解释行含抄表周期 ×2", "×2" in dt2 or "x2" in dt2, dt2[:160])
        check("解释行含人口加成 130", "130" in dt2, dt2[:160])
        check("表格电量列为 0–490",
              "0–490" in (s2.get("tableText") or ""), (s2.get("tableText") or "")[:120])
        check("表格电量列为 491–690",
              "491–690" in (s2.get("tableText") or ""), (s2.get("tableText") or "")[:120])

        # ── D. 季节参数折叠 ────────────────────────────────────
        print("\n[D] 季节参数默认折叠")
        check("季节参数区块存在", s2.get("hasSeason") is True)
        check("有折叠开关", s2.get("hasToggle") is True)
        check("默认折叠", s2.get("seasonCollapsed") is True, str(s2.get("seasonCollapsed")))
        check("折叠时参数区不可见", s2.get("bodyVisible") is False, str(s2.get("bodyVisible")))

        print("  → 点击展开")
        click(c, "[data-tariff-season-toggle]")
        time.sleep(0.8)
        s3 = snap(c)
        check("展开后不再是 collapsed", s3.get("seasonCollapsed") is False,
              str(s3.get("seasonCollapsed")))
        check("展开后参数区可见", s3.get("bodyVisible") is True,
              str(s3.get("bodyVisible")))
        pt3 = s3.get("panelText") or ""
        check("展开后可见「其他月档位」", "其他月档位" in pt3, pt3[:200])
        check("展开后可见「枯平期谷价」", "枯平期谷价" in pt3, pt3[:200])
        print("  → 再次点击折叠")
        click(c, "[data-tariff-season-toggle]")
        time.sleep(0.6)
        s3b = snap(c)
        check("可再次折叠", s3b.get("seasonCollapsed") is True,
              str(s3b.get("seasonCollapsed")))

        # ── E. 切地区 → 解释行同步 ────────────────────────────
        print("\n[E] 切换地区后解释行同步更新")
        api("/api/power/tariff", "POST",
            {"enabled": True, "region": "sichuan",
             "household_people": 6, "meter_months": 2,
             "valley_start": 23, "valley_end": 7})
        time.sleep(1.0)
        c.send("Page.reload", {"ignoreCache": True})
        time.sleep(4)
        c.pump(1.5)
        open_panel(c)
        s4 = snap(c)
        dt4 = s4.get("deriveText") or ""
        print("  四川解释行:", dt4)
        check("四川解释行仍含 490", "490" in dt4, dt4[:160])

        # 切到天津
        c.js("""(function(){
          var tb = %s;
          var sel = tb.querySelector('[data-tariff-region]');
          if (!sel) return;
          sel.value = 'tianjin';
          sel.dispatchEvent(new Event('change', {bubbles:true}));
        })()""" % TB)
        time.sleep(2.5)
        s5 = snap(c)
        dt5 = s5.get("deriveText") or ""
        print("  天津解释行:", dt5)
        check("切天津后解释行不含 490", "490" not in dt5, dt5[:160])
        check("切天津后解释行不含 690", "690" not in dt5, dt5[:160])
        check("天津不显示季节参数区块", s5.get("hasSeason") is False,
              str(s5.get("hasSeason")))

        # 切回四川
        c.js("""(function(){
          var tb = %s;
          var sel = tb.querySelector('[data-tariff-region]');
          if (!sel) return;
          sel.value = 'sichuan';
          sel.dispatchEvent(new Event('change', {bubbles:true}));
        })()""" % TB)
        time.sleep(2.5)
        s6 = snap(c)
        dt6 = s6.get("deriveText") or ""
        print("  切回四川:", dt6)
        check("切回四川后解释行恢复 490", "490" in dt6, dt6[:160])

        # ── F. 电压等级切换 ────────────────────────────────────
        print("\n[F] 电压等级切换 → 峰价与解释行")
        set_field(c, "[data-tariff-voltage]", "lv2")
        time.sleep(1.0)
        s7 = snap(c)
        tt7 = s7.get("tableText") or ""
        check("切 1-10 千伏后峰价变 0.5124", "0.5124" in tt7, tt7[:160])
        check("切电压后解释行仍在", bool(s7.get("deriveText")), (s7.get("deriveText") or "")[:80])

        # ── G. 重载后仍在 ──────────────────────────────────────
        print("\n[G] 重载页面后推导行仍在")
        c.send("Page.reload", {"ignoreCache": True})
        time.sleep(4)
        c.pump(1.5)
        open_panel(c)
        s8 = snap(c)
        check("重载后解释行存在", s8.get("hasDerive") is True)
        check("重载后解释行含 490",
              "490" in (s8.get("deriveText") or ""), (s8.get("deriveText") or "")[:160])
        check("重载后季节参数仍折叠", s8.get("seasonCollapsed") is True,
              str(s8.get("seasonCollapsed")))

        # ── H. 无 JS 异常 ──────────────────────────────────────
        print("\n[H] 页面健康")
        errs = [e for e in c.events
                if e.get("method") == "Runtime.exceptionThrown"]
        check("无 JS 异常", len(errs) == 0, "%d 个" % len(errs))

    finally:
        # 还原配置
        try:
            payload = dict(orig)
            payload.setdefault("valley_start", 23)
            payload.setdefault("valley_end", 7)
            api("/api/power/tariff", "POST", payload)
            print("\n已还原原配置:", json.dumps(payload, ensure_ascii=False)[:160])
        except Exception as e:
            print("\n⚠ 还原配置失败:", e)

    print("\n" + "=" * 60)
    print("结果：%d 通过 / %d 失败" % (PASSN[0], len(FAIL)))
    print("=" * 60)
    if FAIL:
        for f in FAIL:
            print("  ✗", f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
