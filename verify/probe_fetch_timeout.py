#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.89 实机探针 —— 「请求挂起时页面不得停在 loading 骨架」

背景
----
老板 2026-11-23 反馈：「改版本出问题了，请核查」—— 截图里系统监控页
**所有区块卡在转圈骨架**（「正在采集监控数据…」「汇总关键指标…」「读取存储信息…」），
且没有任何错误提示，刷新前一直不动。

根因：前端 fetchMonitor() 的 fetch **既没有 .catch 也没有超时**。
服务重启期间 TCP 连接会**建立但迟迟不返回** ⇒ fetch 处于 **pending**，
既不 resolve 也不 reject ⇒ **光加 .catch 没用**，必须 AbortController 主动超时中断。

本探针覆盖三段：
  A. 静态检查（源码层面）—— 超时 / catch / 重试 / 骨架清单是否齐全
  B. 实机行为（注入拦截）—— 让 monitor 请求挂起，验证：
       · 页面**不再**停在 loading 骨架
       · 出现明确的错误文案
       · 请求在超时窗口内被**主动 abort**（不是无限等）
  C. 自动恢复 —— 解除拦截后，页面能**自己**接上（退避重试链有效）

用法：
    python3 verify/probe_fetch_timeout.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署 v1.1.89。
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import CDP, Checker, connect  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MON_JS = os.path.join(ROOT, "app/ui/static/js/monitor.js")
APP_JS = os.path.join(ROOT, "app/ui/static/js/app.js")
INDEX_HTML = os.path.join(ROOT, "app/ui/index.html")


def strip_js_comments(src):
    """剥掉注释 —— 解释性注释里会引用原代码字符串，直接 grep 会误判。"""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    out = []
    for line in src.split("\n"):
        # 不破坏字符串里的 //（极少见，这里保守处理：只剥行首缩进后的 //）
        out.append(re.sub(r"(^|\s)//.*$", r"\1", line))
    return "\n".join(out)


def check_source(chk):
    """A 段：静态检查。"""
    mon = strip_js_comments(open(MON_JS, encoding="utf-8").read())
    app = strip_js_comments(open(APP_JS, encoding="utf-8").read())

    # A1 monitor.js 必须有 AbortController 超时
    chk.check("A1 monitor.js 使用 AbortController",
              "AbortController" in mon)
    # A2 有明确的超时时长常量
    chk.check("A2 定义了请求超时时长（MON_TIMEOUT_MS）",
              re.search(r"MON_TIMEOUT_MS\s*=\s*\d+", mon) is not None)
    # A3 fetch 必须挂 signal
    chk.check("A3 fetch 带 signal（可被主动中断）",
              re.search(r"init\.signal\s*=\s*ctrl\.signal", mon) is not None)
    # A4 fetchMonitor 有 catch
    chk.check("A4 fetchMonitor 有 .catch 兜底",
              re.search(r"function fetchMonitor\(\)\s*\{[\s\S]{0,2000}?\.catch\(", mon)
              is not None)
    # A5 refresh 有 catch
    chk.check("A5 refresh() 有 .catch 兜底（原来没有）",
              re.search(r"function refresh\(\)\s*\{[\s\S]{0,3000}?\.catch\(", mon)
              is not None)
    # A6 有退避重试
    chk.check("A6 有退避重试机制 scheduleRetry()",
              "function scheduleRetry()" in mon
              and "Math.pow(2" in mon)
    # A7 重试链不会因「被跳过」而断掉
    chk.check("A7 重试被跳过时会续排（不会断链）",
              "skipped" in mon and "scheduleRetry()" in mon)
    # A8 app.js 的 api() 有超时
    chk.check("A8 app.js api() 有超时保护",
              re.search(r"function api\([^)]*\)[\s\S]{0,2000}?AbortController", app)
              is not None)
    # A9 超时文案要让用户知道该做什么
    chk.check("A9 超时文案明确（提示服务可能正在启动）",
              "服务可能正在启动" in mon or "服务可能正在启动" in app)

    # A10 骨架容器清单必须与 index.html 的静态骨架一一对应
    html = open(INDEX_HTML, encoding="utf-8").read()
    # index.html 里带 loading 骨架的容器 id
    ids = set()
    for m in re.finditer(r'id="(mon[A-Za-z]*)"[^>]*>\s*<div class="loading"', html):
        ids.add(m.group(1))
    # monOverview 在 .ov-wrap 里
    m = re.search(r'id="(monOverview)"[^>]*>\s*<div class="loading"', html)
    if m:
        ids.add(m.group(1))
    # ⚠️ monStatus 是**状态条**（<span class="mon-status">），不是内容区块容器 ——
    #    它的骨架由 setStatus() 直接改写，不走 showMonitorUnavailable()。
    #    把它算进「区块清单」会永远判红。
    ids = {i for i in ids if i != "monStatus"}
    # monTempOverview / monTempSide 是 .ov-card，本身就是卡片不是骨架容器
    ids = {i for i in ids if i not in ("monTempOverview", "monTempSide")}
    # ⚠️ monOverview 由 renderOverview() 独家管理（overview.js 有 _built 缓存，
    #    容器被清空后必须由 renderOverview 重建）⇒ 不在 MON_SKELETON_IDS 里。
    ids = {i for i in ids if i != "monOverview"}
    listed = set(re.findall(r"'(mon[A-Za-z]+)'", mon))
    missing = sorted(ids - listed)
    chk.check("A10 骨架容器清单齐全（漏一个就会永远转圈）：%s"
              % (",".join(missing) if missing else "OK"),
              not missing)
    # A10b：renderOverview 必须在正常 / 错误 / 预热三条路径都被调用 ——
    #     漏掉错误或预热路径 ⇒ 概览容器被清空后六卡永远消失。
    r_fn = re.search(r"function render\(d\)\s*\{[\s\S]{0,2500}?\n  \}", mon)
    r_body = r_fn.group(0) if r_fn else ""
    chk.check("A10b 错误/预热分支都调用了 renderOverview（否则概览六卡永远消失）",
              r_body.count("renderOverview(") >= 3,
              extra="render() 内调用 %d 次" % r_body.count("renderOverview("))
    return mon


def check_behavior(chk, c):
    """B + C 段：实机行为。"""
    # ---- B1：注入拦截，让 monitor 请求永久挂起 ----
    c.send("Page.addScriptToEvaluateOnNewDocument", {"source": """
      (function(){
        window.__hang = true;
        window.__hangCount = 0;
        window.__abortSeen = false;
        var origFetch = window.fetch;
        window.fetch = function(input, init){
          var url = (typeof input === 'string') ? input : (input && input.url) || '';
          if (window.__hang && /api\\/monitor(\\?|\\/|$)/.test(url)) {
            window.__hangCount++;
            /* 返回一个永不 settle 的 Promise —— 模拟 TCP 建立但迟迟不返回 */
            return new Promise(function(res, rej){
              if (init && init.signal) {
                init.signal.addEventListener('abort', function(){
                  window.__abortSeen = true;
                  var e = new Error('Aborted');
                  e.name = 'AbortError';
                  rej(e);
                });
              }
            });
          }
          return origFetch.apply(this, arguments);
        };
      })();
    """})
    # 强制硬刷新，让注入脚本生效
    c.send("Page.reload", {"ignoreCache": True})
    c.pump(6)
    # 切到监控页
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');"
          "if(b)b.click();return 'ok';})()")
    c.pump(3)
    # 手动触发刷新（确保请求发出）
    c.js("(function(){ if(window.FnStyleMonitor) window.FnStyleMonitor.refresh();"
          "return 'ok';})()")
    # 等超过超时窗口（8s）+ 一点余量
    c.pump(13)

    # B2：请求确实被发出且被 abort 了
    r = c.js("JSON.stringify({hang:window.__hangCount,abort:window.__abortSeen})")
    st = json.loads(r) if isinstance(r, str) else (r or {})
    chk.check("B1 拦截生效（monitor 请求已发出）", (st.get("hang") or 0) > 0)
    chk.check("B2 挂起的请求被**主动 abort**（不是无限等待）",
              st.get("abort") is True)

    # B3：页面不得停在 loading 骨架
    sk = c.js("""(function(){
      var ids = ['monStorage','monCore','monTempOverview',
                 'monTempSide','monPowerOverview','monPowerStats','monPowerDaily'];
      var out = {stuck: [], empties: []};
      ids.forEach(function(id){
        var b = document.getElementById(id);
        if(!b) { out.empties.push(id + ':MISSING'); return; }
        var hasSpin = !!b.querySelector('.spin');
        var hasLoading = !!b.querySelector('.loading');
        if(hasSpin || hasLoading) out.stuck.push(id);
      });
      /* 概览容器：不能停在骨架上，也不能是空白 */
      var ov = document.getElementById('monOverview');
      out.ovStuck = ov ? !!(ov.querySelector('.spin') || ov.querySelector('.loading')) : true;
      out.ovEmpty = ov ? (ov.innerText||'').trim().length === 0 : true;
      out.ovMetrics = ov ? ov.querySelectorAll('.ov-metric').length : 0;
      return JSON.stringify(out);
    })()""")
    st = json.loads(sk) if isinstance(sk, str) else (sk or {})
    chk.check("B3 无区块仍停在 loading 骨架（修前=8 个）",
              not st.get("stuck"),
              extra="仍转圈: %s" % (",".join(st.get("stuck") or []) or "无"))
    chk.check("B3b 概览区未停在骨架上", st.get("ovStuck") is False)
    chk.check("B3c 概览区非空白（要么有占位卡，要么有明确文字）",
              st.get("ovEmpty") is False,
              extra="ov-metric 数 = %s" % st.get("ovMetrics"))

    # B4：出现明确的错误/提示文案（不能是 index.html 的原始骨架文案）
    txt = c.js("""(function(){
      var ids = ['monOverview','monStorage','monCore','monPowerOverview',
                 'monPowerStats','monPowerDaily'];
      var all = [];
      ids.forEach(function(id){
        var b = document.getElementById(id);
        if(b) all.push((b.innerText||'').replace(/\\s+/g,' ').trim());
      });
      var stt = document.getElementById('monStatus');
      return JSON.stringify({
        bodies: all,
        status: stt ? (stt.innerText||'').replace(/\\s+/g,' ').trim() : '',
        statusClass: stt ? stt.className : ''
      });
    })()""")
    st = json.loads(txt) if isinstance(txt, str) else (txt or {})
    bodies = " | ".join(st.get("bodies") or [])
    # 原始骨架文案必须消失
    for sk_text in ("正在采集监控数据", "汇总关键指标", "读取存储信息",
                    "读取运行状态", "读取传感器", "读取风扇", "读取功耗",
                    "读取用电统计", "读取每日用电"):
        chk.check("B4 原始骨架文案已消失：%s" % sk_text,
                  sk_text not in bodies)
    chk.check("B5 出现了明确的后端不可用提示",
              ("不可用" in bodies or "超时" in bodies or "失败" in bodies
               or "启动" in bodies),
              extra=bodies[:160])
    chk.check("B6 状态条标记为错误态（mon-status err）",
              "err" in (st.get("statusClass") or ""),
              extra="%s / %s" % (st.get("statusClass"), st.get("status")))

    # ---- C：解除拦截，验证自动恢复 ----
    c.js("(function(){window.__hang=false;return 'ok';})()")
    c.pump(16)
    rec = c.js("""(function(){
      var ids = ['monOverview','monStorage','monCore'];
      var out = {stuck: [], hasOvCard: false};
      ids.forEach(function(id){
        var b = document.getElementById(id);
        if(!b) return;
        if(b.querySelector('.spin') || b.querySelector('.loading')) out.stuck.push(id);
      });
      out.hasOvCard = !!document.querySelector('#monOverview .ov-metric');
      var stt = document.getElementById('monStatus');
      out.status = stt ? (stt.innerText||'').replace(/\\s+/g,' ').trim() : '';
      return JSON.stringify(out);
    })()""")
    st = json.loads(rec) if isinstance(rec, str) else (rec or {})
    chk.check("C1 解除故障后自动恢复（骨架已清空）",
              not st.get("stuck"),
              extra="仍转圈: %s" % (",".join(st.get("stuck") or []) or "无"))
    chk.check("C2 概览六卡已重新渲染",
              st.get("hasOvCard") is True)
    chk.check("C3 状态条回到正常态（监控运行中）",
              "运行中" in (st.get("status") or ""),
              extra=st.get("status"))


def main():
    chk = Checker()
    print("=" * 66)
    print("v1.1.89 探针 —— 请求挂起时页面不得停在 loading 骨架")
    print("=" * 66)
    try:
        check_source(chk)
    except Exception as e:
        chk.check("A 段静态检查执行", False, extra=str(e))
    try:
        c = connect()
        check_behavior(chk, c)
    except Exception as e:
        chk.check("B/C 段实机检查执行", False, extra=str(e))
    return chk.finish()


if __name__ == "__main__":
    sys.exit(main())
