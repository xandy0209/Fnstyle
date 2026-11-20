/* 前端电价面板新配置项验证（v1.1.59）
 *
 * 验证三件事：
 *   ① 面板按地区条件渲染新控件（电压等级 / 家庭人口 / 抄表周期）
 *   ② capture/restore 能保住用户正在编辑的新字段（live 重建不丢）
 *   ③ 保存载荷正确收集三个新字段
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

console.log('\n=== 1. 源码层：新控件存在 ===');
ok('tariffPanelHtml 有电压等级下拉', /data-tariff-voltage/.test(SRC));
ok('tariffPanelHtml 有家庭人口输入', /data-tariff-people/.test(SRC));
ok('tariffPanelHtml 有抄表周期下拉', /data-tariff-meter/.test(SRC));
ok('captureTariffForm 采集 voltage', /voltage:\s*vt\s*\?\s*vt\.value/.test(SRC));
ok('captureTariffForm 采集 people', /people:\s*hp\s*\?\s*hp\.value/.test(SRC));
ok('captureTariffForm 采集 meter_months', /meter_months:\s*mm\s*\?\s*mm\.value/.test(SRC));
ok('restoreTariffForm 回填 voltage', /st\.voltage !== null/.test(SRC));
ok('restoreTariffForm 回填 people', /st\.people !== null/.test(SRC));
ok('restoreTariffForm 回填 meter_months', /st\.meter_months !== null/.test(SRC));
ok('保存载荷含 voltage', /payload\.voltage\s*=\s*vt\.value/.test(SRC));
ok('保存载荷含 household_people', /payload\.household_people/.test(SRC));
ok('保存载荷含 meter_months', /payload\.meter_months/.test(SRC));

console.log('\n=== 2. 条件渲染：只在支持该政策的地区显示 ===');
const panelFn = extractFn('tariffPanelHtml');
ok('tariffPanelHtml 可提取', !!panelFn);
ok('电压等级按 prof.voltage_levels 渲染',
  panelFn && /var levels = prof\.voltage_levels \|\| \[\]/.test(panelFn) &&
  panelFn && /if \(levels\.length\)/.test(panelFn));
ok('家庭人口按 prof.supports_multi_person 渲染',
  panelFn && /if \(prof\.supports_multi_person\)/.test(panelFn));
ok('抄表周期按 prof.supports_meter_cycle 渲染',
  panelFn && /if \(prof\.supports_meter_cycle\)/.test(panelFn));

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
      if (sel === '[data-tariff-people]') return mk('p', '[data-tariff-people]', vals.__people);
      if (sel === '[data-tariff-meter]') return mk('m', '[data-tariff-meter]', vals.__meter);
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
    __people: '6',
    __meter: '2'
  });
  const st = sandbox.captureTariffForm(box);
  ok('capture 抓到 voltage=lv2', st && st.voltage === 'lv2', st && st.voltage);
  ok('capture 抓到 people=6', st && st.people === '6', st && st.people);
  ok('capture 抓到 meter_months=2', st && st.meter_months === '2', st && st.meter_months);
  ok('capture 记下渲染地区', st && st.prof === 'sichuan', st && st.prof);

  /* 回填到新面板（同地区 → 应还原用户编辑） */
  const box2 = makeBox({ __attr: 'sichuan' });
  sandbox.restoreTariffForm(box2, st, 'sichuan');
  ok('restore 回填 voltage', box2.querySelector('[data-tariff-voltage]').value === 'lv2',
    box2.querySelector('[data-tariff-voltage]').value);
  ok('restore 回填 people', box2.querySelector('[data-tariff-people]').value === '6',
    box2.querySelector('[data-tariff-people]').value);
  ok('restore 回填 meter_months', box2.querySelector('[data-tariff-meter]').value === '2',
    box2.querySelector('[data-tariff-meter]').value);
  ok('restore 回填 rate', box2.querySelector('[data-tariff-rate]').value === '0.8888',
    box2.querySelector('[data-tariff-rate]').value);

  /* 换地区 → 新字段应回落到新地区的配置，不能沿用旧值 */
  const box3 = makeBox({ __attr: 'tianjin' });
  box3.__tariffData = { config: { voltage: '', household_people: 0, meter_months: 1 } };
  sandbox.restoreTariffForm(box3, st, 'tianjin');
  ok('换地区：voltage 回落到新地区配置',
    box3.querySelector('[data-tariff-voltage]').value === '',
    JSON.stringify(box3.querySelector('[data-tariff-voltage]').value));
  ok('换地区：people 回落到新地区配置',
    String(box3.querySelector('[data-tariff-people]').value) === '0',
    JSON.stringify(box3.querySelector('[data-tariff-people]').value));
  ok('换地区：meter_months 回落到新地区配置',
    box3.querySelector('[data-tariff-meter]').value === '1',
    JSON.stringify(box3.querySelector('[data-tariff-meter]').value));
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
  if (vals.people !== undefined) {
    const ppl = Number(vals.people);
    payload.household_people = (isFinite(ppl) && ppl >= 0 && ppl <= 30) ? Math.floor(ppl) : 0;
  }
  if (vals.meter !== undefined) {
    const mv = Number(vals.meter);
    payload.meter_months = (mv === 2) ? 2 : 1;
  }
  return payload;
}
let p = buildPayload({ enabled: true, voltage: 'lv3', people: '5', meter: '2' });
ok('载荷 voltage=lv3', p.voltage === 'lv3', p.voltage);
ok('载荷 household_people=5', p.household_people === 5, p.household_people);
ok('载荷 meter_months=2', p.meter_months === 2, p.meter_months);
p = buildPayload({ enabled: true, people: '', meter: '1' });
ok('空人口数 → 0', p.household_people === 0, p.household_people);
p = buildPayload({ enabled: true, people: '99', meter: '3' });
ok('超范围人口数 → 0', p.household_people === 0, p.household_people);
ok('非法抄表周期 → 1', p.meter_months === 1, p.meter_months);
p = buildPayload({ enabled: true, people: '7.9' });
ok('小数人口数取整', p.household_people === 7, p.household_people);

console.log('\n' + '-'.repeat(56));
console.log('通过 ' + PASS + ' 项，失败 ' + FAIL + ' 项');
if (FAIL) process.exit(1);
console.log('全部通过');
