#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.89 实机探针 —— 「真实重启服务时页面不卡死、并能自动恢复」

这是 probe_fetch_timeout.py 的**真实场景版**：
前者用拦截模拟「请求挂起」，本脚本**真的把 NAS 上的 fnstyle 服务停掉再启动**，
验证老板遇到的那一幕：

    应用重装/服务重启 → 打开系统监控页 → 页面卡在 loading 骨架不动 → 只能手动刷新

流程：
  1. 打开监控页并确认正常（骨架已清空、概览六卡已渲染）
  2. trim-cli app stop fnstyle —— 服务真的停掉
  3. 等待页面上的请求超时（>8s）+ 退避重试若干轮
  4. 断言：页面**不再**停在 loading 骨架、有明确错误提示
  5. trim-cli app start fnstyle —— 服务起来
  6. 等待退避重试命中，断言页面**自动恢复**（无需手动刷新）

用法：
    python3 verify/probe_restart_recover.py
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import Checker, connect  # noqa: E402

TRIM = os.path.expanduser(
    "~/.workbuddy/skills/fnos/scripts/trim-cli")
PROFILE = "mynas"


def trim(*args):
    return subprocess.run(
        [TRIM, "--profile", PROFILE] + list(args),
        capture_output=True, text=True, timeout=120).stdout


SNAP_JS = """(function(){
  var ids = ['monStorage','monCore','monTempOverview',
             'monTempSide','monPowerOverview','monPowerStats','monPowerDaily'];
  var stuck = [];
  ids.forEach(function(id){
    var b = document.getElementById(id);
    if(!b) { stuck.push(id+':MISSING'); return; }
    if(b.querySelector('.spin') || b.querySelector('.loading')) stuck.push(id);
  });
  var ov = document.getElementById('monOverview');
  var ovStuck = ov ? !!(ov.querySelector('.spin') || ov.querySelector('.loading')) : true;
  var stt = document.getElementById('monStatus');
  var bodies = [];
  ['monStorage','monCore'].forEach(function(id){
    var b = document.getElementById(id);
    if(b) bodies.push((b.innerText||'').replace(/\\s+/g,' ').trim());
  });
  return JSON.stringify({
    stuck: stuck,
    ovStuck: ovStuck,
    hasOvMetric: !!document.querySelector('#monOverview .ov-metric'),
    status: stt ? (stt.innerText||'').replace(/\\s+/g,' ').trim() : '',
    statusClass: stt ? stt.className : '',
    bodies: bodies.join(' | ')
  });
})()"""


def snap(c):
    r = c.js(SNAP_JS)
    return json.loads(r) if isinstance(r, str) else (r or {})


def main():
    chk = Checker("v1.1.89 真实重启恢复探针")
    print("=" * 66)
    print("v1.1.89 探针 —— 真实重启服务时页面不卡死并自动恢复")
    print("=" * 66)

    c = connect()
    # 切到监控页
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');"
          "if(b)b.click();return 'ok';})()")
    c.pump(6)
    s0 = snap(c)
    chk.check("1 初始状态正常（概览已渲染、无骨架）",
              s0.get("hasOvMetric") is True and not s0.get("stuck")
              and s0.get("ovStuck") is False,
              extra="stuck=%s ovStuck=%s" % (",".join(s0.get("stuck") or []) or "无",
                                             s0.get("ovStuck")))

    # ---- 真的把服务停掉 ----
    # ⚠️ app stop 是写操作，必须带 --yes，否则只会打印一句提示、
    #    服务根本没停 —— 实测踩过：探针看到「状态条仍 ok」以为产品有问题，
    #    其实是服务压根没停。
    print("\n>>> trim-cli app stop fnstyle --yes")
    trim("app", "stop", "fnstyle", "--yes")
    time.sleep(4)
    # 确认服务真的停了（9910 端口不可达）
    import urllib.request
    stopped = False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open("http://192.168.5.8:9910/api/version", timeout=5).read()
    except Exception:
        stopped = True
    chk.check("0 服务确已停掉（9910 不可达）", stopped)
    # 再点一次刷新，确保发出一个注定失败的请求
    c.js("(function(){if(window.FnStyleMonitor)window.FnStyleMonitor.refresh();"
          "return 'ok';})()")
    # 等超时（8s）+ 几轮退避重试
    c.pump(22)
    s1 = snap(c)
    chk.check("2 服务停掉后页面**不再**停在 loading 骨架",
              not s1.get("stuck"),
              extra="仍转圈: %s" % (",".join(s1.get("stuck") or []) or "无"))
    for sk_text in ("正在采集监控数据", "汇总关键指标", "读取存储信息"):
        chk.check("3 原始骨架文案已消失：%s" % sk_text,
                  sk_text not in (s1.get("bodies") or ""))
    chk.check("4 出现明确的服务不可用提示",
              ("不可用" in (s1.get("bodies") or "")
               or "超时" in (s1.get("bodies") or "")
               or "失败" in (s1.get("bodies") or "")),
              extra=(s1.get("bodies") or "")[:120])
    chk.check("5 状态条标记为错误态",
              "err" in (s1.get("statusClass") or ""),
              extra="%s / %s" % (s1.get("statusClass"), s1.get("status")))

    # ---- 把服务起回来 ----
    print("\n>>> trim-cli app start fnstyle --yes")
    trim("app", "start", "fnstyle", "--yes")
    # 等待服务起来 + 退避重试命中（退避封顶 10s）
    ok = False
    for i in range(14):
        c.pump(5)
        s2 = snap(c)
        if s2.get("hasOvMetric") and "运行中" in (s2.get("status") or ""):
            ok = True
            print("    第 %d 轮（%.0fs）已恢复" % (i + 1, (i + 1) * 5))
            break
    chk.check("6 服务恢复后页面**自动接上**（无需手动刷新）", ok,
              extra="status=%s" % s2.get("status"))
    chk.check("7 恢复后无残留骨架",
              not s2.get("stuck"),
              extra="仍转圈: %s" % (",".join(s2.get("stuck") or []) or "无"))
    chk.check("8 概览六卡重新渲染", s2.get("hasOvMetric") is True)
    return chk.finish()


if __name__ == "__main__":
    sys.exit(main())
