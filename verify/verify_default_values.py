#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出厂默认值一致性测试（v1.1.81）

背景
----
老板 2026-11-22 需求：
  「个性化设置中登录酷水平位置默认为 60%，登录框名字默认为隐藏，
    窗口毛玻璃效果默认为 8」

⚠️ 这三个「默认」在项目里**散落在 7 个地方**，必须全部一致：

  层                        文件                            位置
  ─────────────────────────────────────────────────────────────
  ① 前端 UI 回填（点"默认"） app/ui/static/js/app.js       DEFAULTS
  ② 前端 UI null 兜底        app/ui/static/js/app.js       nullValue
  ③ 前端登录页运行时         app/ui/static/js/fnstyle-login.js  num()
  ④ 前端桌面运行时           app/ui/static/js/fnstyle-runtime.js num()
  ⑤ 后端样式覆盖             app/ui/apply_style_overrides.py    map_values()
  ⑥ 后端 null 归一化         app/ui/index.cgi                SWITCH_NULL_DEFAULTS
  ⑦ 首次安装播种             app/seed/fnstyle.json

  漏改任何一处 → 「界面显示的默认」与「实际生效的默认」脱节，
  典型症状：界面显示 60% 但登录框没右移（界面骗人）。

用法：
    python3 verify/verify_default_values.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Checker:
    """分段断言计数器。

    ⚠️ 计数**跨段共享**（类属性）—— 早期版本把 passed/failed 放在
    __init__ 里，导致每个分段各自计数、summary() 反复打印，
    最后一行显示的"通过 N 项"只反映最后一段，误导性极强。
    """

    passed = 0
    failed = []

    def __init__(self, title):
        self.title = title
        print("\n=== %s ===" % title)

    def check(self, name, cond, detail=""):
        if cond:
            Checker.passed += 1
            print("  ✅ %s" % name)
        else:
            Checker.failed.append((name, detail))
            print("  ❌ %s   %s" % (name, detail))
        return cond

    def summary(self):
        total = Checker.passed + len(Checker.failed)
        print("\n" + "=" * 60)
        print("通过 %d 项，失败 %d 项" % (Checker.passed, len(Checker.failed)))
        if Checker.failed:
            print("\n失败明细：")
            for n, d in Checker.failed:
                print("  ✗ %s   %s" % (n, d))
            return 1
        print("🎉 全部通过")
        return 0


EXPECT = {
    "fndeskLoginVW": "60",
    "nasNameDisplay": "2",
    "FndeskGlass": "8",
}

LABELS = {
    "fndeskLoginVW": "登录框水平位置",
    "nasNameDisplay": "登录框名字",
    "FndeskGlass": "窗口毛玻璃效果",
}


def main():
    ck = Checker("A. 各层默认值一致（出厂 = 60 / 隐藏 / 8）")

    # ① app.js DEFAULTS
    appjs = read("app/ui/static/js/app.js")
    m = re.search(r"var DEFAULTS = \{(.*?)\n  \};", appjs, re.S)
    ck.check("app.js 找到 DEFAULTS 块", m is not None)
    if m:
        blk = m.group(1)
        for k, v in EXPECT.items():
            mm = re.search(r"\b%s:\s*'([^']*)'" % re.escape(k), blk)
            ck.check("① app.js DEFAULTS.%s = %s（%s）"
                     % (k, v, LABELS[k]),
                     mm is not None and mm.group(1) == v,
                     "实际=%s" % (mm.group(1) if mm else "未找到"))

    # ② app.js nullValue
    mm = re.search(r"key:\s*'nasNameDisplay'.{0,400}?nullValue:\s*'([^']*)'",
                   appjs, re.S)
    ck.check("② app.js nasNameDisplay.nullValue = 2（隐藏）",
             mm is not None and mm.group(1) == "2",
             "实际=%s" % (mm.group(1) if mm else "未找到"))

    # ③ fnstyle-login.js
    loginjs = read("app/ui/static/js/fnstyle-login.js")
    mm = re.search(r"num\(cfg\.FndeskGlass,\s*(-?\d+)\)", loginjs)
    ck.check("③ login.js num(cfg.FndeskGlass, 8)",
             mm is not None and mm.group(1) == "8",
             "实际=%s" % (mm.group(1) if mm else "未找到"))
    mm = re.search(r"num\(cfg\.fndeskLoginVW,\s*(-?\d+)\)", loginjs)
    ck.check("③ login.js num(cfg.fndeskLoginVW, 60)",
             mm is not None and mm.group(1) == "60",
             "实际=%s" % (mm.group(1) if mm else "未找到"))

    # ④ fnstyle-runtime.js
    rtjs = read("app/ui/static/js/fnstyle-runtime.js")
    mm = re.search(r"num\(cfg\.nasNameDisplay,\s*(-?\d+)\)", rtjs)
    ck.check("④ runtime.js num(cfg.nasNameDisplay, 2) → 隐藏",
             mm is not None and mm.group(1) == "2",
             "实际=%s" % (mm.group(1) if mm else "未找到"))

    # ⑤ apply_style_overrides.py
    py = read("app/ui/apply_style_overrides.py")
    mm = re.search(r"num\(cfg,\s*'FndeskGlass',\s*(-?\d+)\)", py)
    ck.check("⑤ apply_style_overrides num(cfg,'FndeskGlass', 8)",
             mm is not None and mm.group(1) == "8",
             "实际=%s" % (mm.group(1) if mm else "未找到"))
    mm = re.search(r"num\(cfg,\s*'fndeskLoginVW',\s*(-?\d+)\)", py)
    ck.check("⑤ apply_style_overrides num(cfg,'fndeskLoginVW', 60)",
             mm is not None and mm.group(1) == "60",
             "实际=%s" % (mm.group(1) if mm else "未找到"))

    # ⑥ index.cgi SWITCH_NULL_DEFAULTS
    cgi = read("app/ui/index.cgi")
    m = re.search(r'SWITCH_NULL_DEFAULTS="(.*?)"', cgi, re.S)
    ck.check("index.cgi 找到 SWITCH_NULL_DEFAULTS", m is not None)
    if m:
        tbl = m.group(1)
        mm = re.search(r"nasNameDisplay\t(\S+)", tbl)
        ck.check("⑥ index.cgi SWITCH_NULL_DEFAULTS nasNameDisplay = 2",
                 mm is not None and mm.group(1) == "2",
                 "实际=%s" % (mm.group(1) if mm else "未找到"))

    # ⑦ seed/fnstyle.json
    seed = json.loads(read("app/seed/fnstyle.json"))
    ck.check("⑦ seed FndeskGlass = 8", seed.get("FndeskGlass") == 8,
             "实际=%s" % seed.get("FndeskGlass"))
    ck.check("⑦ seed fndeskLoginVW = '60'",
             str(seed.get("fndeskLoginVW")) == "60",
             "实际=%s" % seed.get("fndeskLoginVW"))
    ck.check("⑦ seed nasNameDisplay = 2", seed.get("nasNameDisplay") == 2,
             "实际=%s" % seed.get("nasNameDisplay"))

    # ---- 行为层：后端 map_values 的实际输出 ----
    ck = Checker("B. 后端 map_values 实际输出")
    sys.path.insert(0, os.path.join(ROOT, "app", "ui"))
    try:
        import importlib
        aso = importlib.import_module("apply_style_overrides")
        v = aso.map_values({})
        ck.check("空配置 → blur = 8", v["blur"] == 8, "实际=%s" % v["blur"])
        ck.check("空配置 → vw = 60", v["vw"] == 60, "实际=%s" % v["vw"])
        ck.check("空配置 → alpha = 0.1", abs(v["alpha"] - 0.1) < 1e-9,
                 "实际=%s" % v["alpha"])
        # 有明确值时以用户值为准（不被缺省覆盖）
        v2 = aso.map_values({"FndeskGlass": "20", "fndeskLoginVW": "10"})
        ck.check("用户显式配置时以用户值为准（glass=20）",
                 v2["blur"] == 20, "实际=%s" % v2["blur"])
        ck.check("用户显式配置时以用户值为准（vw=10）",
                 v2["vw"] == 10, "实际=%s" % v2["vw"])
    except Exception as e:
        ck.check("import apply_style_overrides 成功", False, str(e))

    # ---- 行为层：前端 JS 的实际缺省（用 node 跑一遍） ----
    ck = Checker("C. 前端 JS 实际缺省（node 实跑）")
    import subprocess
    node_script = r"""
const fs = require('fs');
// 从 app.js 里抽出 DEFAULTS
const appjs = fs.readFileSync(process.argv[1], 'utf8');
const m = appjs.match(/var DEFAULTS = \{([\s\S]*?)\n  \};/);
const DEFAULTS = {};
m[1].replace(/(\w+):\s*'([^']*)'/g, (_, k, v) => { DEFAULTS[k] = v; });
// 从 login.js 里抽出 num() 兜底
const loginjs = fs.readFileSync(process.argv[2], 'utf8');
const g = loginjs.match(/num\(cfg\.FndeskGlass,\s*(-?\d+)\)/)[1];
const vw = loginjs.match(/num\(cfg\.fndeskLoginVW,\s*(-?\d+)\)/)[1];
// 从 runtime.js 里抽出 nasNameDisplay 兜底
const rtjs = fs.readFileSync(process.argv[3], 'utf8');
const nd = rtjs.match(/num\(cfg\.nasNameDisplay,\s*(-?\d+)\)/)[1];
console.log(JSON.stringify({
  def_vw: DEFAULTS['fndeskLoginVW'],
  def_nd: DEFAULTS['nasNameDisplay'],
  def_glass: DEFAULTS['FndeskGlass'],
  num_glass: g, num_vw: vw, num_nd: nd
}));
"""
    try:
        r = subprocess.run(
            ["node", "-e", node_script,
             os.path.join(ROOT, "app/ui/static/js/app.js"),
             os.path.join(ROOT, "app/ui/static/js/fnstyle-login.js"),
             os.path.join(ROOT, "app/ui/static/js/fnstyle-runtime.js")],
            capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        ck.check("DEFAULTS.fndeskLoginVW = 60", d["def_vw"] == "60", str(d))
        ck.check("DEFAULTS.nasNameDisplay = 2（隐藏）", d["def_nd"] == "2", str(d))
        ck.check("DEFAULTS.FndeskGlass = 8", d["def_glass"] == "8", str(d))
        ck.check("login.js 兜底 glass = 8", d["num_glass"] == "8", str(d))
        ck.check("login.js 兜底 vw = 60", d["num_vw"] == "60", str(d))
        ck.check("runtime.js 兜底 nasNameDisplay = 2", d["num_nd"] == "2", str(d))
    except Exception as e:
        ck.check("node 实跑抽值", False, str(e))

    # ---- 关键行为：login.js 不再跳过注入 ----
    ck = Checker("D. 关键行为：默认配置也会真正注入（不跳过）")
    loginjs = read("app/ui/static/js/fnstyle-login.js")
    # ⚠️ 必须**剥掉注释**再判断 —— 解释性注释里会引用原代码字符串
    #    （"glass === -1 && opacity === 10 && vw === 0 && vh === 0"），
    #    直接全文 grep 会误判成"短路还在"。
    code = re.sub(r"/\*.*?\*/", " ", loginjs, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    ck.check("login.js 已移除「全默认不注入」的短路（剥注释后判定）",
             "glass === -1 && opacity === 10 && vw === 0 && vh === 0" not in code,
             "短路逻辑仍在代码里 → 界面显示 60% 但实际没右移")
    ck.check("login.js 仍会写 margin-left（位置照常注入）",
             "margin-left:" in code, "找不到 margin-left 注入")

    # ---- UI 字段定义与新默认是否自洽 ----
    ck = Checker("E. UI 字段定义自洽")
    mm = re.search(r"key:\s*'fndeskLoginVW'.*?min:\s*(-?\d+),\s*max:\s*(-?\d+)",
                   appjs, re.S)
    ck.check("fndeskLoginVW 的取值范围包含 60",
             mm is not None and int(mm.group(1)) <= 60 <= int(mm.group(2)),
             "范围=%s..%s" % (mm.group(1) if mm else "?", mm.group(2) if mm else "?"))
    mm = re.search(r"key:\s*'FndeskGlass'.*?min:\s*(-?\d+),\s*max:\s*(-?\d+)",
                   appjs, re.S)
    ck.check("FndeskGlass 的取值范围包含 8",
             mm is not None and int(mm.group(1)) <= 8 <= int(mm.group(2)),
             "范围=%s..%s" % (mm.group(1) if mm else "?", mm.group(2) if mm else "?"))

    return ck.summary()


if __name__ == "__main__":
    sys.exit(main())
