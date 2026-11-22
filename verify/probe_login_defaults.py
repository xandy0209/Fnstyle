#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：个性化设置「登录框」三项的出厂默认值（v1.1.81 复查）

背景
----
老板单独重申了一次：「个性化设置中登录框水平位置默认为60%」。
v1.1.81 已在代码层实现并通过 verify_default_values.py（30 项静态断言），
本探针补上**实机 UI 层**的证据链：

  A 段  页面读数   —— 个性化设置面板里三个控件的实际值
  B 段  「默认」按钮 —— 把值改脏后点「默认」，必须回到出厂值
  C 段  后端落盘   —— 点完「默认」后，API 里的值必须同步
  D 段  存量还原   —— 探针结束前把原值写回，不留残留

⚠️ 本探针会**真实写入**配置。跑完必须还原。
   （「默认」按钮会触发 saveField → POST api/config）

前置：Chrome 已带 --remote-debugging-port=9222 启动，
      NAS 上已部署 v1.1.81 及以上。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_base import CDP, Checker, api, connect  # noqa: E402

# 出厂默认值（v1.1.81）
EXP_VW = '60'        # 登录框水平位置 %
EXP_NAME = '2'       # 登录框名字 2=隐藏 1=显示
EXP_GLASS = '8'      # 窗口毛玻璃效果 px

# 目标控件的 data-key
K_VW = 'fndeskLoginVW'
K_NAME = 'nasNameDisplay'
K_GLASS = 'FndeskGlass'


def read_controls(c):
    """读个性化设置面板里三个控件的当前显示值。"""
    raw = c.js("""(function(){
      var out = {};
      ['fndeskLoginVW','nasNameDisplay','FndeskGlass'].forEach(function(k){
        var el = document.querySelector('#personalizeBody [data-key="'+k+'"]');
        if (!el) { out[k] = null; return; }
        out[k] = (el.type === 'checkbox') ? (el.checked ? '1' : '2') : el.value;
      });
      /* 滑块右侧的实时数值标签 */
      var v = document.querySelector('#v_fndeskLoginVW');
      out['vwLabel'] = v ? v.textContent.trim() : null;
      var t = document.querySelector('#t_nasNameDisplay');
      out['nameLabel'] = t ? t.textContent.trim() : null;
      return JSON.stringify(out);
    })()""")
    return json.loads(raw or "{}")


def click_default(c, key):
    """点某个字段旁边的「默认」按钮。

    ⚠️ 用占位符替换而不是 Python 的 % 格式化 —— JS 里带 '%' 字符
       （如滑块单位 %）会被 % 格式化吃掉，报 "not enough arguments"。
    """
    js = """(function(){
      var btn = document.querySelector('#personalizeBody [data-default="__KEY__"]');
      if (!btn) return 'NO_BTN';
      btn.click();
      return 'OK';
    })()""".replace("__KEY__", key)
    return c.js(js)


def set_value(c, key, value):
    """改控件的值并触发 change（range/checkbox 走 change 才落盘）。"""
    if key == K_NAME:
        checked = 'true' if str(value) == '1' else 'false'
    else:
        checked = 'false'
    js = """(function(){
      var el = document.querySelector('#personalizeBody [data-key="__KEY__"]');
      if (!el) return 'NO_EL';
      if (el.type === 'checkbox') {
        el.checked = __CHECKED__;
      } else {
        el.value = __VALUE__;
      }
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
      return String(el.value);
    })()"""
    js = (js.replace("__KEY__", key)
            .replace("__CHECKED__", checked)
            .replace("__VALUE__", json.dumps(str(value))))
    return c.js(js)


def main():
    ck = Checker("个性化设置 · 登录框三项出厂默认值（v1.1.81）")

    # ---------- 记录原值（用于收尾还原） ----------
    cfg0 = api("/api/config")
    orig = {
        K_VW: cfg0.get(K_VW),
        K_NAME: cfg0.get(K_NAME),
        K_GLASS: cfg0.get(K_GLASS),
    }
    print("原始配置：%s" % json.dumps(orig, ensure_ascii=False))

    c = connect()
    try:
        # 切到「Fnstyle个性化设置」标签
        c.js("(function(){var b=document.querySelector('[data-tab=personalize]');"
             "if(b)b.click();return 'ok';})()")
        c.pump(2.5)

        # ---------------- A 段：页面读数 ----------------
        ck.section("A 段 · 个性化设置面板的实际值")
        cur = read_controls(c)
        print("  读数：%s" % json.dumps(cur, ensure_ascii=False))

        ck.check("水平位置控件存在", cur.get(K_VW) is not None)
        ck.check("登录框名字控件存在", cur.get(K_NAME) is not None)
        ck.check("毛玻璃控件存在", cur.get(K_GLASS) is not None)

        ck.check("登录框水平位置 = %s%%" % EXP_VW,
                 str(cur.get(K_VW)) == EXP_VW, "实际 %s" % cur.get(K_VW))
        ck.check("滑块数值标签显示 %s%%" % EXP_VW,
                 (cur.get('vwLabel') or '').replace(' ', '') == EXP_VW + '%',
                 "实际 %r" % cur.get('vwLabel'))
        ck.check("登录框名字 = 隐藏（2）",
                 str(cur.get(K_NAME)) == EXP_NAME, "实际 %s" % cur.get(K_NAME))
        ck.check("名字标签显示「隐藏」",
                 (cur.get('nameLabel') or '').strip() == '隐藏',
                 "实际 %r" % cur.get('nameLabel'))
        ck.check("窗口毛玻璃效果 = %s" % EXP_GLASS,
                 str(cur.get(K_GLASS)) == EXP_GLASS, "实际 %s" % cur.get(K_GLASS))

        # ---------------- B 段：点「默认」回到出厂值 ----------------
        ck.section("B 段 · 点「默认」按钮回到出厂值")
        # 先把水平位置改成 20（脏值）
        set_value(c, K_VW, '20')
        c.pump(1.2)
        dirty = read_controls(c)
        ck.check("已把水平位置改成 20（前置条件）",
                 str(dirty.get(K_VW)) == '20', "实际 %s" % dirty.get(K_VW))

        r = click_default(c, K_VW)
        ck.check("「默认」按钮存在且可点", r == 'OK', r)
        c.pump(1.5)
        after = read_controls(c)
        ck.check("点「默认」后水平位置回到 %s" % EXP_VW,
                 str(after.get(K_VW)) == EXP_VW, "实际 %s" % after.get(K_VW))
        ck.check("数值标签同步为 %s%%" % EXP_VW,
                 (after.get('vwLabel') or '').replace(' ', '') == EXP_VW + '%',
                 "实际 %r" % after.get('vwLabel'))

        # 名字：先改成「显示」(1)，再点默认 → 必须是「隐藏」(2)
        set_value(c, K_NAME, '1')
        c.pump(1.2)
        d2 = read_controls(c)
        ck.check("已把登录框名字改成「显示」（前置条件）",
                 str(d2.get(K_NAME)) == '1', "实际 %s" % d2.get(K_NAME))
        click_default(c, K_NAME)
        c.pump(1.5)
        a2 = read_controls(c)
        ck.check("点「默认」后名字回到「隐藏」",
                 str(a2.get(K_NAME)) == EXP_NAME, "实际 %s" % a2.get(K_NAME))

        # 毛玻璃：先改成 30，再点默认 → 必须是 8
        set_value(c, K_GLASS, '30')
        c.pump(1.2)
        d3 = read_controls(c)
        ck.check("已把毛玻璃改成 30（前置条件）",
                 str(d3.get(K_GLASS)) == '30', "实际 %s" % d3.get(K_GLASS))
        click_default(c, K_GLASS)
        c.pump(1.5)
        a3 = read_controls(c)
        ck.check("点「默认」后毛玻璃回到 %s" % EXP_GLASS,
                 str(a3.get(K_GLASS)) == EXP_GLASS, "实际 %s" % a3.get(K_GLASS))

        # ---------------- C 段：后端落盘 ----------------
        ck.section("C 段 · 后端落盘同步")
        cfg1 = api("/api/config")
        ck.check("后端 fndeskLoginVW = %s" % EXP_VW,
                 str(cfg1.get(K_VW)) == EXP_VW, "实际 %s" % cfg1.get(K_VW))
        ck.check("后端 nasNameDisplay = %s（隐藏）" % EXP_NAME,
                 str(cfg1.get(K_NAME)) == EXP_NAME, "实际 %s" % cfg1.get(K_NAME))
        ck.check("后端 FndeskGlass = %s" % EXP_GLASS,
                 str(cfg1.get(K_GLASS)) == EXP_GLASS, "实际 %s" % cfg1.get(K_GLASS))

        # ---------------- D 段：无 JS 异常 ----------------
        ck.section("D 段 · 运行时无异常")
        errs = [e for e in c.events
                if e.get("method") == "Runtime.exceptionThrown"]
        ck.check("页面无未捕获异常", not errs,
                 ("%d 条" % len(errs)) if errs else "")

    finally:
        # ---------------- 还原 ----------------
        print("\n--- 还原原值 ---")
        api("/api/config", "POST", {
            K_VW: orig[K_VW] if orig[K_VW] is not None else EXP_VW,
            K_NAME: orig[K_NAME] if orig[K_NAME] is not None else EXP_NAME,
            K_GLASS: orig[K_GLASS] if orig[K_GLASS] is not None else EXP_GLASS,
        })
        time.sleep(0.8)
        cfg2 = api("/api/config")
        ok = (str(cfg2.get(K_VW)) == str(orig[K_VW])
              and str(cfg2.get(K_NAME)) == str(orig[K_NAME])
              and str(cfg2.get(K_GLASS)) == str(orig[K_GLASS]))
        print("还原后：vw=%s name=%s glass=%s  → %s" %
              (cfg2.get(K_VW), cfg2.get(K_NAME), cfg2.get(K_GLASS),
               "OK" if ok else "不一致！"))
        if not ok:
            ck.check("存量配置已还原", False,
                     "vw=%s name=%s glass=%s" %
                     (cfg2.get(K_VW), cfg2.get(K_NAME), cfg2.get(K_GLASS)))
        else:
            ck.check("存量配置已还原", True)

    ck.finish()


if __name__ == "__main__":
    main()
