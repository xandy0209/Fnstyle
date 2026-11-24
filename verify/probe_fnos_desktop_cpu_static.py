#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
探针（静态取证）：从 fnOS 前端 bundle 中定位「运行状态」卡片 CPU 利用率的
**数据刷新间隔**，并与后端缓存 TTL 实测交叉验证。

背景
----
老板反馈：FnDesk 桌面「运行状态」卡片的 CPU 利用率「好长时间才变一次」。
本探针把结论固化成可回归的自动断言。

方法
----
A. 静态：从 fnOS Web 前端 bundle（/assets/index-*.js）里解出
   ① 桌面「运行状态」面板的 CPU 数据源钩子（resmoCpu）
   ② 该钩子使用的轮询间隔常量（V3）
   ③ 状态栏小组件用的另一套间隔（KZe，走 resmoGen）
   ④ 渲染 CPU 百分比文本的组件（E1e → H2）
B. 动态：直连 trim-cli 实测后端 resmon 缓存 TTL 与并发快照一致性

判定
----
S1 bundle 中存在 resmoCpu 轮询钩子，且间隔常量 = 5000 ms
S2 状态栏小组件间隔常量 = 3000 ms（走 resmoGen）
S3 CPU 百分比文本由 latestCpuData（busy.all）渲染
S4 后端：并发同刻请求返回逐位相同的快照（存在服务端缓存）
S5 后端：Δ 扫描存在分界（Δ 小同率高、Δ 大同率低）⇒ 缓存 TTL ≈ 0.5~1 秒
S6 resmoGen.cpuBusy 与 resmoCpu.busy.all 同源同值

用法
----
python3 verify/probe_fnos_desktop_cpu_static.py [--bundle /tmp/fnos_index-*.js]
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

SKILL = os.path.expanduser("~/.workbuddy/skills/fnos")
TC = os.path.join(SKILL, "scripts", "trim-cli")
PROFILE = "relay"


# ---------------- 静态分析 ----------------

def find_bundle(explicit=""):
    if explicit:
        return explicit
    cands = sorted(glob.glob("/tmp/fnos_index-*.js")) + \
        sorted(glob.glob("/tmp/fnos_assets_index-*.js"))
    cands = [c for c in cands if os.path.getsize(c) > 500000]
    if cands:
        return cands[-1]
    return ""


def analyze_bundle(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    r = {}

    # ① 桌面「运行状态」面板的 CPU 钩子：y1e 用 resmoCpu + 间隔 V3
    #    形如： Mj(()=>{ P2.resmoCpu({},{timeout:1e3}).then(...), ...}, V3, {immediate:!0})
    #    ⚠️ 正则必须**非贪婪且不跨过 `})` 边界**，否则会一路吞到下一个钩子去。
    #    用「从 resmoCpu 起最多 600 字符」限制跨度。
    r["has_resmoCpu_hook"] = bool(re.search(
        r'resmoCpu\(\{\},\s*\{timeout:\s*1e3\}\)', src))
    # 真实结构： ...})})},V3,{immediate:!0});
    # ⚠️ 直接在 resmoCpu 调用点**之后的小窗口**内找 `},<VAR>,{immediate:!0}`，
    #    比跨整段正则稳得多（压缩代码里 `})` 嵌套层次无法可靠配平）。
    def hook_var_after(anchor_re, span=700):
        mm = re.search(anchor_re, src)
        if not mm:
            return ""
        win = src[mm.end():mm.end() + span]
        m2 = re.search(r'\},([A-Za-z_$][\w$]*),\{immediate:!0\}\)', win)
        return m2.group(1) if m2 else ""

    r["cpu_hook_interval_var"] = hook_var_after(
        r'resmoCpu\(\{\},\s*\{timeout:\s*1e3\}\)')
    r["cpu_hook_is_resmoCpu"] = bool(r["cpu_hook_interval_var"])

    # ② 间隔常量数值
    def const_of(var):
        mm = re.search(re.escape(var) + r'\s*=\s*(\d+)\s*\*\s*1e3', src)
        if mm:
            return int(mm.group(1)) * 1000
        mm = re.search(re.escape(var) + r'\s*=\s*(\d+)\*(\d+)', src)
        if mm:
            return int(mm.group(1)) * int(mm.group(2))
        return None

    iv = r.get("cpu_hook_interval_var") or ""
    r["cpu_hook_interval_ms"] = const_of(iv) if iv else None

    # ③ 状态栏小组件：qZe 用 resmoGen + KZe
    r["gen_hook_interval_var"] = hook_var_after(
        r'resmoGen\(\{item:\[`storeSpeed`,`netSpeed`,`cpuBusy`,`memPercent`\]\}\)', span=900)
    r["gen_hook_interval_ms"] = const_of(r["gen_hook_interval_var"]) \
        if r["gen_hook_interval_var"] else None

    # ④ CPU 百分比文本渲染：E1e 用 latestCpuData → H2
    r["has_latestCpuData"] = "latestCpuData" in src
    r["cpu_text_from_busy_all"] = bool(re.search(
        r'latestCpuData:\w+.*?busy:\w+\}\s*=\w+\[.*?\].*?\.all', src, re.S)) \
        or bool(re.search(r'\{busy:\w+\}=\w+\[\w+\.length-1\].*?\.all\|\|0', src, re.S))
    # ④ CPU 百分比文本渲染：E1e 里 `H2,{label:`CPU`,percent:e||0,text:p}`
    #    而 e 来自 `latestCpuData`（= busy.all）
    r["has_cpu_render_component"] = bool(re.search(
        r'latestCpuData:\w+.*?H2,\{label:`CPU`,percent:\w+', src, re.S))
    r["cpu_pct_text_template"] = bool(re.search(
        r'e\?e<0\?`-`:e>150\?`150%`:`\$\{e\}%`', src))

    # ⑤ 系统状态面板标题（对应截图上的「运行状态」）
    r["has_systemStatus_title"] = "statusBar.system.systemStatus" in src

    return r


# ---------------- 动态分析 ----------------

def fetch_cpu():
    p = subprocess.run([TC, "--profile", PROFILE, "monitor", "cpu"],
                       capture_output=True, text=True, timeout=30)
    try:
        c = json.loads(p.stdout).get("cpu", {})
        return ((c.get("busy") or {}).get("all"),
                (c.get("loadavg") or {}).get("avg1min"))
    except Exception:
        return (None, None)


def fetch_gen():
    p = subprocess.run([TC, "--profile", PROFILE, "monitor", "gen",
                        "--item", "cpuBusy,memPercent"],
                       capture_output=True, text=True, timeout=30)
    try:
        it = json.loads(p.stdout).get("item", {})
        return it.get("cpuBusy"), it.get("memPercent")
    except Exception:
        return (None, None)


def dynamic_checks():
    out = {}

    # S4 并发同刻
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(6) as ex:
        res = list(ex.map(lambda _: fetch_cpu(), range(6)))
    busy = [r[0] for r in res]
    load = [r[1] for r in res]
    out["concurrent_busy"] = busy
    out["concurrent_load"] = load
    out["concurrent_identical"] = len(set(load)) == 1 and len(set(busy)) == 1

    # S5 Δ 扫描
    trials = 6
    scan = []
    for d in [0.0, 0.3, 0.6, 0.9, 1.2, 1.8]:
        same = 0
        for _ in range(trials):
            a_b, a_l = fetch_cpu()
            time.sleep(d)
            b_b, b_l = fetch_cpu()
            if a_l is not None and b_l is not None and a_l == b_l and a_b == b_b:
                same += 1
        scan.append((d, same / float(trials)))
    out["delta_scan"] = scan
    lo = [r for d, r in scan if d <= 0.6]
    hi = [r for d, r in scan if d >= 1.2]
    out["delta_separated"] = (sum(lo) / max(len(lo), 1)) > (sum(hi) / max(len(hi), 1))
    ttl = None
    for d, r in scan:
        if r < 0.5:
            ttl = d
            break
    out["ttl_est"] = ttl

    # S6 交叉验证
    gb, gm = fetch_gen()
    _b, _l = fetch_cpu()
    out["gen_cpuBusy"] = gb
    out["cpu_busy_all"] = _b
    out["gen_matches_cpu"] = (gb is not None and _b is not None and
                               abs(float(gb) - float(_b)) <= 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default="")
    ap.add_argument("--skip-dynamic", action="store_true")
    args = ap.parse_args()

    print("=" * 76)
    print("fnOS 桌面「运行状态」CPU 刷新间隔 —— 静态取证 + 动态交叉验证")
    print("=" * 76)

    bundle = find_bundle(args.bundle)
    if not bundle:
        print("⚠ 未找到 bundle。请先下载：")
        print("  curl -x http://127.0.0.1:18087 -k "
              "https://xandyns.fnos.net/assets/index-<hash>.js -o /tmp/fnos_index-<hash>.js")
        return 2
    print("bundle : %s (%.1f MB)" % (bundle, os.path.getsize(bundle) / 1048576.0))
    st = analyze_bundle(bundle)

    print()
    print("-" * 76)
    print("静态：前端 bundle 解包")
    print("-" * 76)
    for k, v in st.items():
        print("  %-28s %s" % (k, v))

    dyn = {}
    if not args.skip_dynamic:
        print()
        print("-" * 76)
        print("动态：后端 resmon 实测")
        print("-" * 76)
        dyn = dynamic_checks()
        print("  并发 6 次 busy   : %s" % dyn["concurrent_busy"])
        print("  并发 6 次 load1m : %s" % dyn["concurrent_load"])
        print("  逐位全同         : %s" % dyn["concurrent_identical"])
        for d, r in dyn["delta_scan"]:
            print("  Δ=%4.1fs 两次相同率 %.0f%%" % (d, r * 100))
        print("  TTL 估计         : %s 秒" % dyn["ttl_est"])
        print("  gen.cpuBusy=%s  cpu.busy.all=%s  一致=%s" %
              (dyn["gen_cpuBusy"], dyn["cpu_busy_all"], dyn["gen_matches_cpu"]))

    print()
    print("-" * 76)
    print("判定")
    print("-" * 76)
    checks = [
        ("S1 桌面系统状态面板用 resmoCpu 轮询，间隔 5000ms",
         st.get("cpu_hook_is_resmoCpu") and st.get("cpu_hook_interval_ms") == 5000,
         "间隔变量=%s 值=%s ms" % (st.get("cpu_hook_interval_var"),
                                   st.get("cpu_hook_interval_ms"))),
        ("S2 状态栏小组件用 resmoGen 轮询，间隔 3000ms",
         st.get("gen_hook_interval_ms") == 3000,
         "间隔变量=%s 值=%s ms" % (st.get("gen_hook_interval_var"),
                                   st.get("gen_hook_interval_ms"))),
        ("S3 CPU 百分比文本来自 busy.all（latestCpuData）",
         st.get("has_latestCpuData") and
         (st.get("cpu_text_from_busy_all") or st.get("has_cpu_render_component")),
         "latestCpuData=%s 渲染=%s" % (st.get("has_latestCpuData"),
                                       st.get("has_cpu_render_component"))),
        ("S4 桌面卡片标题为「运行状态」(statusBar.system.systemStatus)",
         st.get("has_systemStatus_title"), ""),
    ]
    if not args.skip_dynamic:
        checks += [
            ("S5 后端并发同刻返回逐位相同快照（存在服务端缓存）",
             dyn.get("concurrent_identical"), ""),
            ("S6 Δ 扫描存在分界 ⇒ 缓存 TTL 可估（≈0.3~1s）",
             dyn.get("delta_separated"), "TTL≈%s s" % dyn.get("ttl_est")),
            ("S7 resmoGen.cpuBusy 与 resmoCpu.busy.all 同源同值",
             dyn.get("gen_matches_cpu"),
             "gen=%s cpu=%s" % (dyn.get("gen_cpuBusy"), dyn.get("cpu_busy_all"))),
        ]

    allok = True
    for n, ok, d in checks:
        allok = allok and bool(ok)
        print("  %s %-52s %s" % ("✅" if ok else "❌", n, d))

    print()
    print("=" * 76)
    print("结论")
    print("=" * 76)
    print("  桌面「运行状态」卡片 CPU%%  : 每 %s ms 拉一次 resmoCpu，DOM 每 %s ms 刷新"
          % (st.get("cpu_hook_interval_ms"), st.get("cpu_hook_interval_ms")))
    print("  状态栏 CPU/RAM 小组件      : 每 %s ms 拉一次 resmoGen"
          % (st.get("gen_hook_interval_ms"),))
    if dyn:
        print("  后端 resmon 缓存 TTL       : ≈ %s 秒（同一份快照被复用）" % dyn.get("ttl_est"))
    print("  ⇒ 桌面卡片上看到的 CPU%% 最多滞后 ≈ 5 秒（轮询间隔）")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
