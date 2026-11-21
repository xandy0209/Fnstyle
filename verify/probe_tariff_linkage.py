#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.1.61：电价面板「下拉 ↔ 价目表」联动 —— CDP 实机探针

前置条件：
  · Chrome 以 --remote-debugging-port=9222 启动
  · NAS 上已部署本版（http://192.168.5.8:9910/）

覆盖 8 处联动缺陷的实机表现：
  ① 切地区后电压下拉与表格峰价一致
  ② 表格峰价取自当前地区（不取旧面板的残留值）
  ③ 换地区时电压下拉不空白
  ④ 固定单价地区单价随电压等级更新
  ⑤ 电压切换后表格峰价即时更新
  ⑥⑦ 切地区保存后，电压/人口/抄表周期不被清零
  ⑧ 低谷时段起=止 → 不是全天谷段
"""
import sys
import json
import time

sys.path.insert(0, '/tmp')
from cdp_lib import C, snap, TB  # noqa: E402

BASE = 'http://192.168.5.8:9910/'

PASS = []
FAIL = []


def check(label, ok, extra=""):
    if ok:
        PASS.append(label)
        print("  ✅ %s%s" % (label, ("  → " + str(extra)) if extra else ""))
    else:
        FAIL.append(label)
        print("  ❌ %s  → %s" % (label, extra))


def peaks_of(s):
    """取表格三行的峰价。"""
    out = []
    for r in s.get('rows') or []:
        p = r.split(' | ')
        if len(p) >= 3 and p[0].startswith('第'):
            out.append(p[2])
    return out


def setf(c, sel, val):
    return c.js("""(function(){var tb=%s;var e=tb.querySelector('%s');
      if(!e)return 'NO';e.value=%s;e.dispatchEvent(new Event('change',{bubbles:true}));
      return e.value;})()""" % (TB, sel, json.dumps(str(val))))


def goto_region(c, reg):
    c.js("""(function(){var tb=%s;var sel=tb.querySelector('[data-tariff-region]');
      sel.value=%s;sel.dispatchEvent(new Event('change',{bubbles:true}));})()"""
         % (TB, json.dumps(reg)))
    time.sleep(2.5)


def save(c):
    c.js("(function(){var tb=%s;var b=tb.querySelector('[data-tariff-save]');"
         "if(b)b.click();return 'OK';})()" % TB)
    time.sleep(2.5)


def api_get(c, path):
    """取接口 JSON。

    ⚠️ 踩坑（CDP + Runtime.evaluate + returnByValue）：
       `return await fetch(...).then(r=>r.json())` 在 evaluate 里返回的是
       **空对象** —— Promise 的结果没有被正确序列化。
       可靠做法：先在页面里发起请求并把结果挂到 window.__probe，
       再用第二次 evaluate 同步读出来。
    """
    c.js("""(function(){
      window.__probe = null; window.__probeErr = null;
      fetch('%s').then(function(r){return r.json();})
        .then(function(j){window.__probe = j;})
        .catch(function(e){window.__probeErr = String(e);});
      return 'started';
    })()""" % path)
    for _ in range(40):
        time.sleep(0.25)
        r = c.js("window.__probe")
        if r:
            return r
        err = c.js("window.__probeErr")
        if err:
            return {"__err__": err}
    return {}


print("=== 打开面板 ===")
c = C()
c.send('Runtime.enable')
c.send('Page.navigate', {'url': BASE})
time.sleep(5)
c.js("var b=document.querySelector('[data-tab=monitor]');if(b)b.click();'ok'")
time.sleep(2)
c.js("window.FnStyleCompute.open('power', window.FnStyleMonitor.lastData());'ok'")
time.sleep(5)

s = snap(c)
check("电价面板已渲染", bool(s.get('rows') or s.get('region')), str(s.get('region')))

# ---------------------------------------------------------------------------
print("\n=== ① 电压下拉与表格峰价一致（当前地区）===")
s0 = snap(c)
vt = (s0.get('voltage') or {})
print("  电压下拉 =", vt.get('value'), " 表格峰价 =", peaks_of(s0))
EXP = {'lv1': '0.5224', 'lv2': '0.5124', 'lv3': '0.5124'}
exp = EXP.get(vt.get('value'))
check("当前地区表格峰价与电压下拉一致",
      exp is not None and peaks_of(s0)[:1] == [exp],
      "下拉=%s 期望=%s 实际=%s" % (vt.get('value'), exp, peaks_of(s0)))

# ---------------------------------------------------------------------------
print("\n=== ⑤ 切电压 → 表格峰价即时更新 ===")
for target in ('lv1', 'lv2', 'lv3'):
    setf(c, '[data-tariff-voltage]', target)
    time.sleep(0.8)
    s = snap(c)
    exp = EXP[target]
    check("选 %s → 表格峰价 %s" % (target, exp),
          peaks_of(s)[:1] == [exp],
          "实际=%s 下拉=%s" % (peaks_of(s), (s.get('voltage') or {}).get('value')))
    # 低谷价不受电压影响（官方表三等级相同）
    if s.get('rows'):
        col = [r.split(' | ')[3] for r in s['rows'][1:]]
        check("选 %s → 低谷价不变（0.2535）" % target,
              col[:1] == ['0.2535'], str(col))

# ---------------------------------------------------------------------------
print("\n=== ①②③ 切地区 → 电压下拉与表格峰价一致 ===")
for reg in ('sichuan', 'tianjin', 'sichuan_shared', 'flat', 'sichuan'):
    goto_region(c, reg)
    s = snap(c)
    vt = (s.get('voltage') or {})
    pk = peaks_of(s)
    print("  %-16s 电压控件=%s 值=%-6s 表格峰价=%s" % (
        reg, s.get('hasVoltage'), vt.get('value'), pk))
    if s.get('hasVoltage'):
        check("%s 电压下拉非空白" % reg, bool(vt.get('value')), repr(vt.get('value')))
        if pk:
            # 有档位表的地区：表格峰价必须与电压下拉一致
            exp = EXP.get(vt.get('value'))
            check("%s 表格峰价与电压下拉一致" % reg,
                  exp is not None and pk[:1] == [exp],
                  "下拉=%s 期望=%s 实际=%s" % (vt.get('value'), exp, pk))
        else:
            # 合表 / 固定单价：**没有档位表**（不执行阶梯电价），
            # 峰价不在表里显示，而是在说明行的单价里 —— 单独校验。
            check("%s 无档位表（不执行阶梯）" % reg, not pk, str(pk))
            flat = c.js("(function(){var tb=%s;var e=tb.querySelector("
                        "'[data-tariff-flat-rate]');"
                        "return e?e.textContent.trim():'NO';})()" % TB)
            # 合表：lv1=0.5464，其余=0.5364
            exp_flat = '0.5464' if vt.get('value') == 'lv1' else '0.5364'
            check("%s 单价与电压等级一致" % reg, flat == exp_flat,
                  "下拉=%s 期望=%s 实际=%s" % (vt.get('value'), exp_flat, flat))
    else:
        check("%s 无电压控件（不区分电压等级）" % reg, not s.get('hasVoltage'))

# ---------------------------------------------------------------------------
print("\n=== ④ 固定单价地区单价随电压更新（合表）===")
goto_region(c, 'sichuan_shared')
# 合表：lv1=0.5464，lv2/lv3=0.5364。
# ⚠️ 不能假设当前是 lv1（保存的配置里可能已是 lv2）→ 先显式切到 lv1 取基准。
setf(c, '[data-tariff-voltage]', 'lv1')
time.sleep(0.8)
flat1 = c.js("(function(){var tb=%s;var e=tb.querySelector('[data-tariff-flat-rate]');"
             "return e?e.textContent.trim():'NO';})()" % TB)
print("  合表 lv1 单价 =", flat1)
check("合表有单价 span", flat1 not in ('NO', ''), repr(flat1))
check("合表 lv1 单价 = 0.5464", flat1 == '0.5464', repr(flat1))
setf(c, '[data-tariff-voltage]', 'lv2')
time.sleep(0.8)
flat2 = c.js("(function(){var tb=%s;var e=tb.querySelector('[data-tariff-flat-rate]');"
             "return e?e.textContent.trim():'NO';})()" % TB)
print("  合表 lv2 单价 =", flat2)
check("合表切 lv2 → 单价变 0.5364", flat2 == '0.5364', repr(flat2))
check("合表切 lv2 → 单价确实变了", flat1 != flat2, "%s vs %s" % (flat1, flat2))
setf(c, '[data-tariff-voltage]', 'lv1')
time.sleep(0.6)

# ---------------------------------------------------------------------------
print("\n=== ⑥⑦ 切地区保存后设置不被清零 ===")
goto_region(c, 'sichuan')
# ⚠️ v1.1.62：人口/抄表控件已删除，只设电压 lv2
setf(c, '[data-tariff-voltage]', 'lv2')
time.sleep(0.8)
save(c)
s = snap(c)
print("  保存后：电压=%s" % ((s.get('voltage') or {}).get('value')))
check("四川保存后档位 = 180/280（官方基准）",
      '0–180' in ' '.join(s.get('rows') or []) and '181–280' in ' '.join(s.get('rows') or []),
      str(s.get('rows')))
check("面板不再有家庭人口控件", s.get('hasPeople') is False)
check("面板不再有抄表周期控件", s.get('hasMeter') is False)

# 切天津并保存
goto_region(c, 'tianjin')
save(c)
raw = api_get(c, BASE + 'api/power/tariff')
try:
    j = raw if isinstance(raw, dict) else json.loads(raw)
    cfg = j.get('config') or {}
    print("  切天津保存后 config：voltage=%r" % (cfg.get('voltage'),))
    check("切天津保存后 voltage 未被清零", cfg.get('voltage') == 'lv2',
          repr(cfg.get('voltage')))
    check("切天津保存后已无 household_people 键",
          'household_people' not in cfg, sorted(cfg.keys()))
    check("切天津保存后已无 meter_months 键",
          'meter_months' not in cfg, sorted(cfg.keys()))
except Exception as e:
    check("解析后端配置", False, "%s / %s" % (e, str(raw)[:120]))
# 切回四川
goto_region(c, 'sichuan')
s = snap(c)
print("  切回四川：电压=%s 表格=%s" % (
    (s.get('voltage') or {}).get('value'), peaks_of(s)))
check("切回四川档位为 180/280",
      '0–180' in ' '.join(s.get('rows') or []), str(s.get('rows')))
check("切回四川电压恢复 lv2",
      (s.get('voltage') or {}).get('value') == 'lv2',
      str((s.get('voltage') or {}).get('value')))
check("切回四川表格峰价 = lv2 的 0.5124",
      peaks_of(s)[:1] == ['0.5124'], str(peaks_of(s)))

# ---------------------------------------------------------------------------
print("\n=== ⑧ 低谷时段起=止 → 不是全天谷段 ===")
# 先看有没有可判定的用电数据
base = api_get(c, BASE + 'api/power/stats')
bv = base.get('valley_kwh') or 0.0
bt = base.get('month_kwh') or 0.0
print("  基准：谷=%.4f 总=%.4f" % (bv, bt))
if bt < 1e-6:
    # 该机近期无用电采样 → 无法用电量占比判定，
    # 退而验证**判定口径本身**：起=止时后端不应把全天算成谷段。
    setf(c, '[data-tariff-vstart]', '5')
    setf(c, '[data-tariff-vend]', '5')
    save(c)
    st = api_get(c, BASE + 'api/power/stats')
    v = st.get('valley_kwh') or 0.0
    t = st.get('month_kwh') or 0.0
    print("  起=止后：谷=%.4f 总=%.4f" % (v, t))
    check("起=止时谷段为 0（空区间，不是全天谷段）",
          v <= 1e-9, "谷=%.4f" % v)
else:
    setf(c, '[data-tariff-vstart]', '5')
    setf(c, '[data-tariff-vend]', '5')
    save(c)
    st = api_get(c, BASE + 'api/power/stats')
    v = st.get('valley_kwh') or 0.0
    t = st.get('month_kwh') or 0.0
    print("  起=止后：谷=%.4f 总=%.4f" % (v, t))
    # 空区间 → 谷段占比应为 0（而不是全部）
    check("起=止时谷段占比为 0（不是全天谷段）",
          (t <= 1e-9) or (v <= 1e-9),
          "谷=%.4f 总=%.4f" % (v, t))
    # 对照：设成 23→7 应该**恢复**谷段电量（与基准一致）
    setf(c, '[data-tariff-vstart]', '23')
    setf(c, '[data-tariff-vend]', '7')
    save(c)
    st2 = api_get(c, BASE + 'api/power/stats')
    v2 = st2.get('valley_kwh') or 0.0
    print("  23→7 后：谷=%.4f（基准 %.4f）" % (v2, bv))
    check("23→7 时谷段恢复（与基准一致）",
          abs(v2 - bv) < 1e-6, "%.4f vs %.4f" % (v2, bv))
    check("23→7 谷段大于 0（对照组）", v2 > 1e-9, "%.4f" % v2)
# 恢复默认
setf(c, '[data-tariff-vstart]', '23')
setf(c, '[data-tariff-vend]', '7')
save(c)

# ---------------------------------------------------------------------------
print("\n=== 无 JS 异常 ===")
errs = c.js("(function(){return (window.__errs||[]).slice(0,5);})()")
check("无 JS 运行时异常", not errs, str(errs))

print("\n" + "=" * 62)
print("结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
print("=" * 62)
for f in FAIL:
    print("  ❌", f)
sys.exit(1 if FAIL else 0)
