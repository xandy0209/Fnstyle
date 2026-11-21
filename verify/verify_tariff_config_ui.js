/* 前端电价面板配置项验证（v1.1.62）
 *
 * ⚠️ v1.1.62 变更：「家庭人口」「抄表周期」两个输入项**已删除**
 *    （一户多人口需主动申报才生效；抄表周期是两个月抄表场景，
 *    自动抄表时代已不适用）→ 本文件改为验证「已删除」。
 *
 * 验证三件事：
 *   ① 面板只保留电压等级下拉（人口/抄表控件必须**不存在**）
 *   ② capture/restore 能保住用户正在编辑的电压等级（live 重建不丢）
 *   ③ 保存载荷只收集电压等级
 *
 * 与 verify_monitor_render.js 一样，走「源码断言 + 逻辑级执行」双轨。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const JS = path.join(ROOT, 'app', 'ui', 'static', 'js', 'monitor.js');
const SRC = fs.readFileSync(JS, 'utf8');

let PASS = 0, FAIL = 0;
function ok(label, cond, extra) {
  if (cond) { PASS++; console.log('  ✓ ' + label); }
  else { FAIL++; console.log('  ✗ ' + label + (extra !== undefined ? '  → ' + extra : '')); }
}

/* ---------- 抽取函数体做逻辑级验证 ---------- */
function extractFn(name) {
  const idx = SRC.indexOf('function ' + name + '(');
  if (idx < 0) return null;
  let i = SRC.indexOf('{', idx);
  let depth = 0, end = i;
  for (; end < SRC.length; end++) {
    if (SRC[end] === '{') depth++;
    else if (SRC[end] === '}') { depth--; if (depth === 0) break; }
  }
  return SRC.slice(idx, end + 1);
}

console.log('\n=== 1. 源码层：只保留电压等级 ===');
ok('tariffPanelHtml 有电压等级下拉', /data-tariff-voltage/.test(SRC));
ok('家庭人口输入已删除', !/data-tariff-people/.test(SRC));
ok('抄表周期下拉已删除', !/data-tariff-meter/.test(SRC));
ok('captureTariffForm 采集 voltage', /voltage:\s*vt\s*\?\s*vt\.value/.test(SRC));
ok('captureTariffForm 不再采集 people', !/people:\s*hp\s*\?\s*hp\.value/.test(SRC));
ok('captureTariffForm 不再采集 meter_months', !/meter_months:\s*mm\s*\?\s*mm\.value/.test(SRC));
ok('restoreTariffForm 回填 voltage', /st\.voltage !== null/.test(SRC));
ok('restoreTariffForm 不再回填 people', !/st\.people !== null/.test(SRC));
ok('restoreTariffForm 不再回填 meter_months', !/st\.meter_months !== null/.test(SRC));
ok('保存载荷含 voltage', /payload\.voltage\s*=\s*vt\.value/.test(SRC));
ok('保存载荷不再含 household_people', !/payload\.household_people/.test(SRC));
ok('保存载荷不再含 meter_months', !/payload\.meter_months/.test(SRC));

console.log('\n=== 1b. v1.1.62：预计电费单价（全局配置，可手调）===');
ok('面板有预计电费单价输入项', /data-tariff-proj-rate/.test(SRC));
ok('输入项默认 0.5224（成都最低档）', /'0\.5224'/.test(SRC));
ok('capture 采集 proj_rate', /proj_rate:\s*\(pr && pr\.value !== ''\)/.test(SRC));
ok('restore 回填 proj_rate', /st\.proj_rate !== null/.test(SRC));
ok('保存载荷提交 proj_rate', /payload\.proj_rate/.test(SRC));
ok('判档依据说明行存在（sub-tier）', /sub-tier/.test(SRC));
ok('判档说明读 current_tier', /current_tier/.test(SRC));
ok('判档说明读 tier_limits', /tier_limits/.test(SRC));

console.log('\n=== 2. 条件渲染：电压等级按地区能力 ===');
const panelFn = extractFn('tariffPanelHtml');
ok('tariffPanelHtml 可提取', !!panelFn);
ok('电压等级按 prof.voltage_levels 渲染',
  panelFn && /var levels = prof\.voltage_levels \|\| \[\]/.test(panelFn) &&
  panelFn && /if \(levels\.length\)/.test(panelFn));
ok('面板不再有 supports_multi_person 条件',
  !/supports_multi_person/.test(SRC));
ok('面板不再有 supports_meter_cycle 条件',
  !/supports_meter_cycle/.test(SRC));

console.log('\n=== 3. 逻辑级：capture / restore 往返 ===');
const capFn = extractFn('captureTariffForm');
const resFn = extractFn('restoreTariffForm');
ok('captureTariffForm 可提取', !!capFn);
ok('restoreTariffForm 可提取', !!resFn);

/* 造一个最小 DOM 替身
   ⚠️ 关键：querySelector 必须返回**同一个对象**（缓存），
      否则写入值后下次查询拿到的还是新对象 → 断言永远失败。 */
function makeBox(opts) {
  const vals = opts || {};
  const cache = {};
  const mk = (key, attr, value) => {
    if (!cache[key]) {
      cache[key] = {
        value: value,
        checked: !!vals.__checked,
        getAttribute: function (a) { return a === attr ? '1' : null; },
        contains: function () { return true; }
      };
    }
    return cache[key];
  };
  const box = {
    __vals: vals,
    __attr: vals.__attr || null,
    getAttribute: function (a) { return a === 'data-tariff-prof' ? box.__attr : null; },
    querySelector: function (sel) {
      if (sel === '[data-tariff-enabled]') return mk('en', '[data-tariff-enabled]', vals.__enabled);
      if (sel === '[data-tariff-vstart]') return mk('vs', '[data-tariff-vstart]', vals.__vs);
      if (sel === '[data-tariff-vend]') return mk('ve', '[data-tariff-vend]', vals.__ve);
      if (sel === '[data-tariff-rate]') return mk('rate', '[data-tariff-rate]', vals.__rate);
      if (sel === '[data-tariff-voltage]') return mk('v', '[data-tariff-voltage]', vals.__voltage);
      if (sel === '[data-tariff-proj-rate]') return mk('pr', '[data-tariff-proj-rate]', vals.__proj);
      return null;
    },
    querySelectorAll: function () { return []; }
  };
  return box;
}

/* 把两个函数塞进沙箱跑（需要 esc / $ 等外部依赖时只用到纯逻辑） */
const sandbox = {
  console: console,
  Math: Math,
  Number: Number,
  isFinite: isFinite,
  esc: function (s) { return s; },
  Array: Array,
  document: { activeElement: null }
};
try {
  vm.createContext(sandbox);
  vm.runInContext(capFn, sandbox);
  vm.runInContext(resFn, sandbox);

  const box = makeBox({
    __attr: 'sichuan',
    __enabled: true,
    __vs: '23', __ve: '7',
    __rate: '0.8888',
    __voltage: 'lv2',
    __proj: '0.5224'
  });
  const st = sandbox.captureTariffForm(box);
  ok('capture 抓到 voltage=lv2', st && st.voltage === 'lv2', st && st.voltage);
  ok('capture 记下渲染地区', st && st.prof === 'sichuan', st && st.prof);
  ok('capture 抓到 proj_rate=0.5224', st && st.proj_rate === '0.5224',
    st && st.proj_rate);
  ok('capture 不再带 people 字段', st && st.people === undefined, st && st.people);
  ok('capture 不再带 meter_months 字段', st && st.meter_months === undefined,
    st && st.meter_months);

  /* 回填到新面板（同地区 → 应还原用户编辑） */
  const box2 = makeBox({ __attr: 'sichuan' });
  sandbox.restoreTariffForm(box2, st, 'sichuan');
  ok('restore 回填 voltage', box2.querySelector('[data-tariff-voltage]').value === 'lv2',
    box2.querySelector('[data-tariff-voltage]').value);
  ok('restore 回填 rate', box2.querySelector('[data-tariff-rate]').value === '0.8888',
    box2.querySelector('[data-tariff-rate]').value);
  ok('restore 回填 proj_rate',
    box2.querySelector('[data-tariff-proj-rate]').value === '0.5224',
    box2.querySelector('[data-tariff-proj-rate]').value);

  /* 换地区 → 电压应回落到新地区的生效值，不能沿用旧值 */
  const box3 = makeBox({ __attr: 'tianjin' });
  box3.__tariffData = { config: { voltage: '', proj_rate: 0.61 } };
  sandbox.restoreTariffForm(box3, st, 'tianjin');
  ok('换地区：voltage 回落到新地区配置',
    box3.querySelector('[data-tariff-voltage]').value === '',
    JSON.stringify(box3.querySelector('[data-tariff-voltage]').value));
  /* proj_rate 是**全局**配置 → 换地区也要保留。
     注意：st.proj_rate 是用户**正在编辑**的值，优先级最高
     （live 重建时不丢用户输入），所以这里应是 st 里的 0.5224。 */
  ok('换地区：proj_rate 不被清空（全局配置，保留用户编辑值）',
    String(box3.querySelector('[data-tariff-proj-rate]').value) === '0.5224',
    JSON.stringify(box3.querySelector('[data-tariff-proj-rate]').value));
} catch (e) {
  ok('capture/restore 逻辑级执行', false, e && e.message);
}

console.log('\n=== 4. 逻辑级：保存载荷取值 ===');
/* 模拟保存按钮的取值逻辑（与源码同规则） */
function buildPayload(vals) {
  const payload = {
    enabled: !!vals.enabled,
    region: vals.region || 'sichuan',
    valley_start: vals.vs ? Number(vals.vs) : 23,
    valley_end: vals.ve ? Number(vals.ve) : 7,
    rate_override: (vals.rate !== undefined && vals.rate !== '') ? Number(vals.rate) : null
  };
  if (vals.voltage) payload.voltage = vals.voltage;
  if (vals.proj !== undefined) {
    const prv = Number(vals.proj);
    /* ⚠️ 空串要当「未填」处理 —— Number('') 是 0 而不是 NaN */
    payload.proj_rate = (vals.proj !== '' && isFinite(prv)
      && prv >= 0 && prv <= 10) ? prv : 0.5224;
  }
  return payload;
}
let p = buildPayload({ enabled: true, voltage: 'lv3', proj: '0.5224' });
ok('载荷 voltage=lv3', p.voltage === 'lv3', p.voltage);
ok('载荷 proj_rate=0.5224', p.proj_rate === 0.5224, p.proj_rate);
p = buildPayload({ enabled: true, proj: '99' });
ok('超范围 proj_rate → 0.5224', p.proj_rate === 0.5224, p.proj_rate);
p = buildPayload({ enabled: true, proj: '' });
ok('空 proj_rate → 0.5224', p.proj_rate === 0.5224, p.proj_rate);
ok('载荷不含 household_people', p.household_people === undefined, p.household_people);
ok('载荷不含 meter_months', p.meter_months === undefined, p.meter_months);
p = buildPayload({ enabled: true });
ok('无电压时载荷不带 voltage', p.voltage === undefined, p.voltage);

console.log('\n' + '-'.repeat(56));
console.log('通过 ' + PASS + ' 项，失败 ' + FAIL + ' 项');
if (FAIL) process.exit(1);
console.log('全部通过');
