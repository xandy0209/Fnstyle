#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP 探针 v1.1.84：「配置管理」抽屉的三段式结构与量测

验证：
  1. 抽屉打开后能渲染出三段：阈值门限 / 电费设置 / 告警通知
  2. 电价面板**真的**画出来了（不是只挂了个空容器）
  3. 告警通知段有：总开关 / 通道 / Webhook 地址 / 级别 / 指标 / 静默期 / 测试推送
  4. 勾选框、开关、输入框的实际尺寸与对齐（用 getBoundingClientRect 量）
  5. 段与段之间的间距（视觉分段是否成立）
  6. 窄屏下不溢出

⚠️ 量测要点（来自历史教训）：
   · 元素在视口外时鼠标事件不触发 → 派发前 scrollIntoView 并重取 rect
   · 测底层元素交互前先关掉所有抽屉/浮层（遮罩会让 elementFromPoint 返回遮罩）
   · 无窗口 Chrome 冻结 rAF → 用 Emulation.setFocusEmulationEnabled(true)
   · 「元素存在」不等于「元素画出来了」→ 要断言实际尺寸 > 0
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import CDP, Checker, connect  # noqa: E402

URL = "http://127.0.0.1:9911/"


class AllChecker(Checker):
    """跨分段累计的断言计数器。

    ⚠️ `probe_base.Checker` 每段独立计数，`finish()` 只报**最后一段**的数字
       ⇒ 前面段落全失败也会显示「全部通过」（假绿）。
       这里让所有分段共享一份计数，finish() 才是真总数。
    """

    def __init__(self, title="", parent=None):
        super().__init__(title)
        self.parent = parent

    def check(self, label, cond, extra=""):
        super().check(label, cond, extra)
        if self.parent is not None:
            self.parent.passn += 1
            if not cond:
                self.parent.fail.append(label)

    def finish(self):
        if self.parent is not None:
            return self.parent.finish()
        return super().finish()


def new_ck(title, parent):
    return AllChecker(title, parent)



def _asdict(v):
    """cdp.js 返回的可能是 str(JSON) 也可能已被解析成 dict ⇒ 统一成 dict。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return {}


def wait_for(cdp, expr, timeout=30.0, step=0.6, want="truthy"):
    """轮询直到 expr 返回真值（或等于 want）。

    ⚠️ 为什么需要它：本机 `/api/version` 会去拉 11 个外网镜像，
       全部超时时该请求要挂 30+ 秒 ⇒ 页面首屏可能迟迟没建好。
       探针**不该**假设「navigate 后 pump(4) 页面就绪」——
       实测同一份代码有时通、有时报 body 为 null。
    ⇒ 改成「等条件成立」，对加载时间不敏感。

    返回 (ok, last_value)。
    """
    import time
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = cdp.js(expr)
        except Exception as e:
            last = repr(e)
        if want == "truthy":
            if last:
                return True, last
        elif last == want:
            return True, last
        cdp.pump(step)
    return False, last


def main():
    cdp = connect(host="127.0.0.1")
    TOTAL = AllChecker("TOTAL")
    ck = new_ck("A. 打开配置管理抽屉", TOTAL)

    cdp.send("Page.navigate", {"url": URL})

    # ⚠️ 等页面真的建好再操作，别靠固定 sleep 猜。
    ok, last = wait_for(cdp, "!!document.getElementById('tbOpenThresholds')")
    ck.check("页面就绪（齿轮按钮已渲染）", ok, str(last))

    # 切到监控标签页（齿轮按钮在监控面板里）
    r = cdp.js("""(() => {
      const t = document.querySelector('.tab[data-tab="monitor"]');
      if (t) { t.click(); return 'clicked-tab'; }
      return 'no-tab';
    })()""")
    cdp.pump(1.5)
    ck.check("切到系统监控标签页", r == "clicked-tab", str(r))

    opened = cdp.js("""(() => {
      const b = document.getElementById('tbOpenThresholds');
      if (!b) return 'no-btn';
      b.click();
      return 'clicked';
    })()""")
    cdp.pump(2.5)
    ck.check("点齿轮按钮打开抽屉", opened == "clicked", str(opened))

    # ⚠️ 抽屉内容是**异步**渲染的（电价面板要等 /api/power/tariff 回来才填值），
    #    告警段也要等 /api/monitor/alerts ⇒ 必须等「电价面板真的画出来」再量测。
    ok2, last2 = wait_for(
        cdp, "!!document.querySelector('#monThresholdBody [data-tariff-rate]')")
    ck.check("抽屉内容已渲染（电价输入框已出现）", ok2, str(last2))

    # ---- 三段结构 ----
    ck = new_ck("B. 抽屉的三段结构", TOTAL)
    d = cdp.js("""(() => {
      const ov = document.getElementById('monThresholdOverlay');
      const body = document.getElementById('monThresholdBody');
      const secs = Array.from(document.querySelectorAll('#monThresholdBody .thr-sec'));
      return JSON.stringify({
        hidden: ov ? ov.hasAttribute('hidden') : null,
        on: ov ? ov.classList.contains('on') : null,
        sections: secs.map(s => ({
          cls: s.className,
          title: (s.querySelector('.thr-sec-t')||{}).textContent || ''
        })),
        tariffBox: !!document.querySelector('#monThresholdBody [data-tariff-box]'),
        tariffRate: !!document.querySelector('#monThresholdBody [data-tariff-rate]'),
        tariffNote: !!document.querySelector('#monThresholdBody [data-tariff-note]'),
        tariffSave: !!document.querySelector('#monThresholdBody [data-tariff-save]'),
        alSec: !!document.querySelector('#monThresholdBody .thr-sec-alerts'),
        alEnabled: !!document.querySelector('#monThresholdBody [data-al-enabled]'),
        alUrl: !!document.querySelector('#monThresholdBody [data-al-url]'),
        alSecret: !!document.querySelector('#monThresholdBody [data-al-secret]'),
        alCooldown: !!document.querySelector('#monThresholdBody [data-al-cooldown]'),
        alSave: !!document.querySelector('#monThresholdBody [data-al-save]'),
        alTest: !!document.querySelector('#monThresholdBody [data-al-test]'),
        alChannels: document.querySelectorAll('#monThresholdBody [data-al-channel]').length,
        alLevels: document.querySelectorAll('#monThresholdBody [data-al-level]').length,
        alMetrics: document.querySelectorAll('#monThresholdBody [data-al-metric]').length,
        sub: (document.getElementById('monThresholdSub')||{}).textContent || ''
      });
    })()""")
    info = _asdict(d)

    ck.check("抽屉已展开", info.get("hidden") is False and info.get("on") is True,
             str(info.get("hidden")))
    titles = [s["title"].strip() for s in info.get("sections", [])]
    # ⚠️ v1.1.84：`.thr-sec` 既有**阈值分组**（处理器/内存/存储/温度/显示着色），
    #    又有**新增的两段**（电费设置 / 告警通知）⇒ 一共 7 个，不能断言「3 个」。
    ck.check("抽屉共 7 个分段（阈值 5 组 + 电费设置 + 告警通知）",
             len(titles) == 7, str(titles))
    ck.check("分段里含「电费设置」与「告警通知」",
             "电费设置" in titles and "告警通知" in titles, str(titles))
    ci = titles.index("电费设置") if "电费设置" in titles else -1
    ai = titles.index("告警通知") if "告警通知" in titles else -1
    ck.check("新加的两段排在阈值分组之后（电费设置在后、告警通知在最后）",
             ci >= 0 and ai >= 0 and ci < ai and ai == len(titles) - 1,
             "ci=%s ai=%s total=%s" % (ci, ai, len(titles)))
    ck.check("副标题涵盖三类配置",
             "阈值门限" in info.get("sub", "") and "电费设置" in info.get("sub", "")
             and "告警通知" in info.get("sub", ""), info.get("sub"))

    # ---- 电价面板真的画出来了 ----
    ck = new_ck("C. 电价面板真的画出来了", TOTAL)
    ck.check("有电价面板容器", info.get("tariffBox") is True)
    ck.check("有单价输入框", info.get("tariffRate") is True)
    ck.check("有后端口径说明", info.get("tariffNote") is True)
    ck.check("有独立保存按钮", info.get("tariffSave") is True)

    # 电价输入框的实际尺寸（断言「画出来了」而不只是「元素存在」）
    d = cdp.js("""(() => {
      const el = document.querySelector('#monThresholdBody [data-tariff-rate]');
      if (!el) return JSON.stringify({missing:true});
      const r = el.getBoundingClientRect();
      const note = document.querySelector('#monThresholdBody [data-tariff-note]');
      const nr = note ? note.getBoundingClientRect() : null;
      return JSON.stringify({
        w: Math.round(r.width), h: Math.round(r.height),
        value: el.value,
        noteText: note ? note.textContent.slice(0, 60) : null,
        noteH: nr ? Math.round(nr.height) : 0
      });
    })()""")
    tj = _asdict(d)
    ck.check("单价输入框有实际尺寸（>0）",
             tj.get("w", 0) > 20 and tj.get("h", 0) > 10, str(tj))
    # ⚠️ v1.1.84 修复过：`.tariff-input` 曾被全局 `input[type="number"]`（特异性 0,1,1）
    #    的 `width:100%` 压掉 ⇒ 实测撑到 437px（撑满整行）。
    #    修复后应回到 140px 附近，且**明显窄于抽屉内容区**。
    ck.check("单价输入框宽度正常（140px 附近，未撑满整行）",
             100 <= tj.get("w", 0) <= 200, "w=%s" % tj.get("w"))
    ck.check("单价输入框有值（后端生效单价）",
             bool(tj.get("value")), str(tj.get("value")))
    ck.check("口径说明有内容（后端下发，非前端写死）",
             bool(tj.get("noteText")) and tj.get("noteH", 0) > 5, str(tj))

    # ---- 告警通知段 ----
    ck = new_ck("D. 告警通知段的控件齐备", TOTAL)
    ck.check("有告警通知分段", info.get("alSec") is True)
    ck.check("有总开关", info.get("alEnabled") is True)
    ck.check("有 Webhook 地址输入框", info.get("alUrl") is True)
    ck.check("有加签密钥输入框", info.get("alSecret") is True)
    ck.check("有静默期输入框", info.get("alCooldown") is True)
    ck.check("有保存按钮", info.get("alSave") is True)
    ck.check("有测试推送按钮", info.get("alTest") is True)
    ck.check("有 2 个通道选项", info.get("alChannels") == 2,
             str(info.get("alChannels")))
    ck.check("有 2 个级别选项", info.get("alLevels") == 2,
             str(info.get("alLevels")))
    ck.check("有 6 个指标选项", info.get("alMetrics") == 6,
             str(info.get("alMetrics")))

    # 密钥框必须是 password（安全）
    d = cdp.js("""(() => {
      const el = document.querySelector('#monThresholdBody [data-al-secret]');
      const url = document.querySelector('#monThresholdBody [data-al-url]');
      const sw = document.querySelector('#monThresholdBody [data-al-enabled]');
      const swTrack = document.querySelector('#monThresholdBody .thr-switch-track');
      const ur = url ? url.getBoundingClientRect() : null;
      const sr = el ? el.getBoundingClientRect() : null;
      return JSON.stringify({
        secretType: el ? el.type : null,
        urlType: url ? url.type : null,
        enabledChecked: sw ? sw.checked : null,
        swW: swTrack ? Math.round(swTrack.getBoundingClientRect().width) : 0,
        swH: swTrack ? Math.round(swTrack.getBoundingClientRect().height) : 0,
        urlW: ur ? Math.round(ur.width) : 0,
        secW: sr ? Math.round(sr.width) : 0
      });
    })()""")
    sj = _asdict(d)
    ck.check("密钥框是 password 类型（不显示明文）",
             sj.get("secretType") == "password", str(sj.get("secretType")))
    ck.check("地址框是 url 类型", sj.get("urlType") == "url", str(sj.get("urlType")))
    # ⚠️ v1.1.85：不能硬编码「默认开关是关闭态」。
    #    告警配置是**持久化**的（存在数据目录的 monitor_alerts.json），
    #    上一轮实机测试留下的 enabled=true 会让这条断言必然假红。
    #    ⇒ 改成断言「开关状态与后端返回的配置一致」—— 这才是真正的契约。
    try:
        import urllib.request as _u
        _op = _u.build_opener(_u.ProxyHandler({}))
        _cfg = json.loads(_op.open(URL.rstrip("/") + "/api/monitor/alerts", timeout=8).read().decode())
        _srvEnabled = bool(_cfg.get("config", {}).get("enabled"))
    except Exception as e:
        _srvEnabled = None
    ck.check("告警总开关状态与后端配置一致",
             _srvEnabled is not None and sj.get("enabledChecked") is _srvEnabled,
             "ui=%s server=%s" % (sj.get("enabledChecked"), _srvEnabled))
    ck.check("开关轨道有实际尺寸",
             sj.get("swW", 0) > 20 and sj.get("swH", 0) > 10, str(sj))
    # ⚠️ v1.1.84 修复过：`.thr-input-wide` 曾被 `input[type="url"]` / `input[type="password"]`
    #    （特异性同为 0,1,1）压掉 240px。修复后应回到 240px 附近。
    ck.check("Webhook 地址框宽度正常（240px 附近）",
             200 <= sj.get("urlW", 0) <= 280, "urlW=%s" % sj.get("urlW"))
    ck.check("加签密钥框宽度与地址框一致（同排对齐）",
             abs(sj.get("secW", 0) - sj.get("urlW", 0)) <= 4,
             "secW=%s urlW=%s" % (sj.get("secW"), sj.get("urlW")))

    # ---- 量测：段间距与对齐 ----
    ck = new_ck("E. 版式量测（段间距 / 控件对齐）", TOTAL)
    d = cdp.js("""(() => {
      const secs = Array.from(document.querySelectorAll('#monThresholdBody .thr-sec'));
      const boxes = secs.map(s => {
        const r = s.getBoundingClientRect();
        return {top: Math.round(r.top), bottom: Math.round(r.bottom),
                h: Math.round(r.height), title: (s.querySelector('.thr-sec-t')||{}).textContent||''};
      });
      const thrRows = document.querySelectorAll('#monThresholdBody .thr-row');
      const rowHeights = Array.from(thrRows).map(r => Math.round(r.getBoundingClientRect().height));
      const chk = document.querySelector('#monThresholdBody [data-al-metric]');
      const lbl = chk ? chk.closest('.thr-check') : null;
      const lblR = lbl ? lbl.getBoundingClientRect() : null;
      const saveBtn = document.querySelector('#monThresholdBody [data-al-save]');
      const saveR = saveBtn ? saveBtn.getBoundingClientRect() : null;
      const secRight = secs.length ? Math.round(secs[secs.length-1].getBoundingClientRect().right) : 0;
      return JSON.stringify({
        boxes: boxes,
        rowHeights: rowHeights.slice(0, 5),
        chkW: lblR ? Math.round(lblR.width) : 0,
        chkH: lblR ? Math.round(lblR.height) : 0,
        saveW: saveR ? Math.round(saveR.width) : 0,
        saveH: saveR ? Math.round(saveR.height) : 0,
        secRight: secRight,
        bodyRight: Math.round(document.getElementById('monThresholdBody').getBoundingClientRect().right)
      });
    })()""")
    m = _asdict(d)
    boxes = m.get("boxes", [])
    ck.check("7 个分段都量到尺寸（>0 高度）",
             len(boxes) == 7 and all(b["h"] > 10 for b in boxes), str(boxes))
    # ⚠️ 段间距要针对**新增的两段**（电费设置 / 告警通知），
    #    不能沿用「boxes[1] / boxes[2]」（那是阈值分组，与视觉分段无关）。
    if len(boxes) == 7:
        names = [b["title"].strip() for b in boxes]
        try:
            ti = names.index("电费设置")
            ai2 = names.index("告警通知")
        except ValueError:
            ti = ai2 = -1
        ck.check("找到「电费设置」与「告警通知」分段下标", ti >= 0 and ai2 >= 0,
                 "ti=%s ai=%s %s" % (ti, ai2, names))
        if ti >= 0 and ai2 >= 0:
            gap_tariff = boxes[ti]["top"] - boxes[ti - 1]["bottom"]
            gap_alert = boxes[ai2]["top"] - boxes[ai2 - 1]["bottom"]
            ck.check("电费段与上一段有间距（>0，形成视觉分段）",
                     gap_tariff > 0, "gap=%s" % gap_tariff)
            ck.check("告警段与上一段有间距（>0，形成视觉分段）",
                     gap_alert > 0, "gap=%s" % gap_alert)
            ck.check("分段不重叠（bottom ≤ 下一段 top）",
                     all(boxes[i]["bottom"] <= boxes[i + 1]["top"]
                         for i in range(len(boxes) - 1)), str(boxes))
            ck.check("告警段在最后（视觉收尾）",
                     ai2 == len(boxes) - 1, "ai=%s total=%s" % (ai2, len(boxes)))
    ck.check("勾选行有实际高度（>12px，可点）",
             m.get("chkH", 0) > 12, str(m.get("chkH")))
    ck.check("保存按钮有实际尺寸",
             m.get("saveW", 0) > 40 and m.get("saveH", 0) > 18,
             "w=%s h=%s" % (m.get("saveW"), m.get("saveH")))
    ck.check("告警段未溢出抽屉右边界",
             m.get("secRight", 0) <= m.get("bodyRight", 0) + 1,
             "sec.right=%s body.right=%s" % (m.get("secRight"), m.get("bodyRight")))

    # ---- 交互：勾一个指标，看是否生效 ----
    ck = new_ck("F. 交互：勾选与开关", TOTAL)
    # 先关掉可能的遮罩
    d = cdp.js("""(() => {
      const cbs = document.querySelectorAll('#monThresholdBody [data-al-metric]');
      const cb = cbs[1];   /* 第二个指标 = 内存 */
      if (!cb) return 'no-cb';
      cb.scrollIntoView({block:'center'});
      const was = cb.checked;
      cb.click();
      return JSON.stringify({was: was, now: cb.checked});
    })()""")
    cdp.pump(0.8)
    try:
        tj2 = _asdict(d)
        ck.check("勾选指标后 checked 翻转",
                 tj2.get("now") != tj2.get("was"), str(tj2))
    except Exception:
        ck.check("勾选指标后 checked 翻转", False, str(d))

    # 开关
    d = cdp.js("""(() => {
      const sw = document.querySelector('#monThresholdBody [data-al-enabled]');
      if (!sw) return 'no-sw';
      sw.scrollIntoView({block:'center'});
      const was = sw.checked;
      sw.click();
      return JSON.stringify({was: was, now: sw.checked});
    })()""")
    cdp.pump(0.8)
    try:
        sj2 = _asdict(d)
        ck.check("点开关后 checked 翻转",
                 sj2.get("now") != sj2.get("was"), str(sj2))
        # ⚠️ 点击开关**不会自动保存**（要按「保存通知配置」）⇒
        #    UI 状态此时必然与后端不同，不能拿它跟后端比。
        #    真正该断言的是：① UI 翻转了 ② 这次点击**没有**偷偷改后端。
        try:
            _op2 = _u.build_opener(_u.ProxyHandler({}))
            _cfg2 = json.loads(_op2.open(URL.rstrip("/") + "/api/monitor/alerts", timeout=8).read().decode())
            _srvEnabled2 = bool(_cfg2.get("config", {}).get("enabled"))
        except Exception:
            _srvEnabled2 = None
        ck.check("点开关不会自动改后端配置（仍需点保存）",
                 _srvEnabled2 is _srvEnabled,
                 "before=%s after=%s" % (_srvEnabled, _srvEnabled2))
    except Exception:
        ck.check("点开关后 checked 翻转", False, str(d))

    # ---- 窄屏不溢出 ----
    ck = new_ck("G. 窄屏不溢出", TOTAL)
    cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": 380, "height": 800, "deviceScaleFactor": 1, "mobile": False})
    cdp.pump(1.2)
    d = cdp.js("""(() => {
      const body = document.getElementById('monThresholdBody');
      if (!body) return JSON.stringify({missing:true});
      const url = document.querySelector('#monThresholdBody [data-al-url]');
      const r = url ? url.getBoundingClientRect() : null;
      const br = body.getBoundingClientRect();
      return JSON.stringify({
        missing: false,
        scrollW: body.scrollWidth, clientW: body.clientWidth,
        urlRight: r ? Math.round(r.right) : 0,
        bodyRight: Math.round(br.right),
        urlW: r ? Math.round(r.width) : 0
      });
    })()""")
    nj = _asdict(d) if isinstance(d, str) else d
    ck.check("窄屏下抽屉内容仍在（未因重绘丢失）",
             nj.get("missing") is False, str(nj.get("missing")))
    ck.check("窄屏下抽屉内容不横向溢出",
             nj.get("scrollW", 0) <= nj.get("clientW", 0) + 2,
             "scrollW=%s clientW=%s" % (nj.get("scrollW"), nj.get("clientW")))
    ck.check("窄屏下地址框未超出抽屉右界",
             nj.get("urlRight", 0) <= nj.get("bodyRight", 0) + 2,
             "url.right=%s body.right=%s" % (nj.get("urlRight"), nj.get("bodyRight")))
    cdp.send("Emulation.clearDeviceMetricsOverride")
    cdp.pump(0.8)

    # ---- 阈值按钮区位置（v1.1.85）----
    # ⚠️ v1.1.85：老板反馈「恢复默认门限 / 保存阈值」位置不对，
    #    要求挪到**阈值配置下方**（阈值分组之后、电费设置之前）。
    #    原先它们在抽屉最底部 ⇒ 离操作的表单隔了两大段，
    #    而且紧挨着「保存通知配置」，两个「保存」很容易点错。
    ck = new_ck("H. 阈值按钮区的位置（v1.1.85）", TOTAL)
    d = cdp.js("""(() => {
      const body = document.getElementById('monThresholdBody');
      if (!body) return JSON.stringify({missing:true});
      const act = body.querySelector('.thr-actions-thresholds');
      const secs = Array.from(body.querySelectorAll('.thr-sec'));
      const tariff = body.querySelector('.thr-sec-tariff');
      const alerts = body.querySelector('.thr-sec-alerts');
      /* ⚠️ 用**子节点下标**判顺序，不碰 compareDocumentPosition。
         那玩意儿的位语义（谁在谁的 FOLLOWING 方向）改错过两次，
         每次都是「条件写反 ⇒ 探针假红」⇒ 产品被冤。
         DOM 顺序用 indexOf 一目了然，且不会搞反。 */
      const acts = body.querySelectorAll('.thr-actions-thresholds');
      const actEl = acts[0];
      const kids = Array.prototype.slice.call(body.children);
      const idxOf = (x) => x ? kids.indexOf(x) : -1;
      const iAct = idxOf(actEl);
      const iTariff = idxOf(tariff);
      const iAlerts = idxOf(alerts);
      const thrSecs = secs.filter(s => !s.classList.contains('thr-sec-tariff')
                                     && !s.classList.contains('thr-sec-alerts'));
      const iThrLast = thrSecs.length ? idxOf(thrSecs[thrSecs.length - 1]) : -1;
      const ar = actEl ? actEl.getBoundingClientRect() : null;
      const tr = tariff ? tariff.getBoundingClientRect() : null;
      const lr = alerts ? alerts.getBoundingClientRect() : null;
      const lastThr = thrSecs.length ? thrSecs[thrSecs.length-1].getBoundingClientRect() : null;
      return JSON.stringify({
        missing: false,
        actCount: acts.length,
        iAct: iAct, iTariff: iTariff, iAlerts: iAlerts, iThrLast: iThrLast,
        childOrder: kids.map(k => (k.className || '').split(' ')[0]),
        hasReset: !!body.querySelector('[data-thr-reset]'),
        hasSave: !!body.querySelector('[data-thr-save]'),
        hasMsg: !!body.querySelector('[data-thr-msg]'),
        actTop: ar ? Math.round(ar.top) : 0,
        actBottom: ar ? Math.round(ar.bottom) : 0,
        actH: ar ? Math.round(ar.height) : 0,
        actRight: ar ? Math.round(ar.right) : 0,
        tariffTop: tr ? Math.round(tr.top) : 0,
        alertsTop: lr ? Math.round(lr.top) : 0,
        lastThrBottom: lastThr ? Math.round(lastThr.bottom) : 0,
        bodyRight: Math.round(body.getBoundingClientRect().right)
      });
    })()""")
    hj = _asdict(d)
    ck.check("阈值按钮区存在（且只有一个）",
             hj.get("missing") is False and hj.get("actCount") == 1,
             "count=%s" % hj.get("actCount"))
    ck.check("按钮区在**所有阈值分组之后**",
             hj.get("iAct", -1) > hj.get("iThrLast", -1),
             "iAct=%s iThrLast=%s order=%s" % (hj.get("iAct"), hj.get("iThrLast"),
                                              hj.get("childOrder")))
    ck.check("按钮区在「电费设置」之前",
             0 < hj.get("iAct", -1) < hj.get("iTariff", -1),
             "iAct=%s iTariff=%s" % (hj.get("iAct"), hj.get("iTariff")))
    ck.check("按钮区在「告警通知」之前",
             0 < hj.get("iAct", -1) < hj.get("iAlerts", -1),
             "iAct=%s iAlerts=%s" % (hj.get("iAct"), hj.get("iAlerts")))
    ck.check("按钮区里有「恢复默认门限」按钮", hj.get("hasReset") is True)
    ck.check("按钮区里有「保存阈值」按钮", hj.get("hasSave") is True)
    ck.check("按钮区里有提示位（data-thr-msg）", hj.get("hasMsg") is True)
    # 几何上也必须紧跟阈值分组（不是被排到别处）
    ck.check("按钮区顶边紧跟最后一个阈值分组底边（间距 < 40px）",
             0 <= hj.get("actTop", 0) - hj.get("lastThrBottom", 0) <= 40,
             "actTop=%s lastThrBottom=%s" % (hj.get("actTop"), hj.get("lastThrBottom")))
    ck.check("按钮区在电价段上方（actBottom ≤ tariffTop）",
             hj.get("actBottom", 10 ** 9) <= hj.get("tariffTop", 0) + 1,
             "actBottom=%s tariffTop=%s" % (hj.get("actBottom"), hj.get("tariffTop")))
    ck.check("按钮区有实际高度（画出来了，不是空容器）",
             hj.get("actH", 0) > 15, "h=%s" % hj.get("actH"))
    ck.check("按钮区未溢出抽屉右界",
             hj.get("actRight", 0) <= hj.get("bodyRight", 0) + 1,
             "act.right=%s body.right=%s" % (hj.get("actRight"), hj.get("bodyRight")))

    # ---- 交互：点「恢复默认门限」后按钮区不能消失 ----
    # ⚠️ v1.1.85 的核心回归点：`renderThresholdsOnly()` 原来按 `.thr-sec` 增删，
    #    而按钮区**不是** .thr-sec ⇒ 重绘后按钮会凭空消失。
    #    这里必须**真的点一次**并断言按钮仍在。
    ck = new_ck("I. 点「恢复默认门限」后按钮区仍在", TOTAL)
    d = cdp.js("""(() => {
      const b = document.querySelector('#monThresholdBody [data-thr-reset]');
      if (!b) return JSON.stringify({missing:true});
      b.scrollIntoView({block:'center'});
      b.click();
      return JSON.stringify({clicked:true});
    })()""")
    cdp.pump(1.8)
    d = cdp.js("""(() => {
      const body = document.getElementById('monThresholdBody');
      const acts = body ? body.querySelectorAll('.thr-actions-thresholds') : [];
      const a = acts[0];
      const r = a ? a.getBoundingClientRect() : null;
      const secs = body ? Array.from(body.querySelectorAll('.thr-sec')) : [];
      const tariff = body ? body.querySelector('.thr-sec-tariff') : null;
      /* 同上：用子节点下标判顺序，不碰 compareDocumentPosition 位语义 */
      const kids = Array.prototype.slice.call(body ? body.children : []);
      const iAct = a ? kids.indexOf(a) : -1;
      const iTariff = tariff ? kids.indexOf(tariff) : -1;
      return JSON.stringify({
        count: acts.length,
        hasReset: !!(body && body.querySelector('[data-thr-reset]')),
        hasSave: !!(body && body.querySelector('[data-thr-save]')),
        hasMsg: !!(body && body.querySelector('[data-thr-msg]')),
        h: r ? Math.round(r.height) : 0,
        secCount: secs.length,
        iAct: iAct, iTariff: iTariff,
        msgText: (body && body.querySelector('[data-thr-msg]')) ?
                 (body.querySelector('[data-thr-msg]').textContent || '') : ''
      });
    })()""")
    rj = _asdict(d)
    ck.check("重绘后按钮区仍在（没有凭空消失）",
             rj.get("count") == 1, "count=%s" % rj.get("count"))
    ck.check("重绘后「恢复默认门限」按钮仍在", rj.get("hasReset") is True)
    ck.check("重绘后「保存阈值」按钮仍在", rj.get("hasSave") is True)
    ck.check("重绘后提示位仍在", rj.get("hasMsg") is True)
    ck.check("重绘后按钮区仍有实际高度", rj.get("h", 0) > 15, "h=%s" % rj.get("h"))
    ck.check("重绘后分段数量不变（阈值 5 + 电费 + 告警 = 7）",
             rj.get("secCount") == 7, "secCount=%s" % rj.get("secCount"))
    ck.check("重绘后按钮区仍在电价段之前（位置没跑掉）",
             0 < rj.get("iAct", -1) < rj.get("iTariff", -1),
             "iAct=%s iTariff=%s" % (rj.get("iAct"), rj.get("iTariff")))
    ck.check("重绘给出了结果提示",
             bool(rj.get("msgText")), str(rj.get("msgText")))

    print()
    ck.finish()


if __name__ == "__main__":
    main()
