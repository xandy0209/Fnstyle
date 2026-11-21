/* 前端电价面板配置项验证（v1.1.63）
 *
 * ⚠️ v1.1.63 变更：整个电价模型退化为**单一单价** ——
 *    「删除阶梯计费和档位信息等，也不区分区域，就是一个电费单价的配置，
 *      并增加对应的说明即可」
 *    ⇒ 面板里**只剩一个「电费单价」输入框** + 一段口径说明
 *    ⇒ 地区 / 电压等级 / 谷时段 / 阶梯开关 / 季节参数 / 预计单价 全部删除
 *
 * 验证四件事：
 *   ① 面板结构：只有一个单价输入框，没有其它任何配置控件
 *   ② capture / restore 能保住用户正在编辑的单价（live 重建不丢）
 *   ③ 保存载荷只收集 rate
 *   ④ 已删除的控件确实不存在（防回归）
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

console.log('\n=== 1. 面板结构：只有一个单价输入框 ===');
ok('tariffPanelHtml 有电费单价输入框', /data-tariff-rate/.test(SRC));
ok('单价输入框是 number 类型', /type="number"/.test(SRC));
ok('单价输入框有 step=0.0001', /step="0\.0001"/.test(SRC));
ok('单价输入框有 min=0 / max=10', /min="0"/.test(SRC) && /max="10"/.test(SRC));
ok('输入框旁有单位「元/kWh」', /tariff-unit/.test(SRC) && /元\/kWh/.test(SRC));
ok('面板有口径说明容器 data-tariff-note', /data-tariff-note/.test(SRC));
ok('面板有保存按钮', /data-tariff-save/.test(SRC));

console.log('\n=== 1b. v1.1.63：已删除的配置项必须不存在 ===');
const gone = [
  'data-tariff-enabled', 'data-tariff-vstart', 'data-tariff-vend',
  'data-tariff-voltage', 'data-tariff-region', 'data-tariff-meter',
  'data-tariff-people', 'data-tariff-proj-rate', 'data-tariff-season'
];
gone.forEach(t => ok('monitor.js 不含 ' + t, !SRC.includes(t)));

/* 已删除的概念（不是属性名，而是标识符 / class） */
const goneWords = [
  'tariffTableHtml', 'tariffDerivationHtml', 'tariffSeasonHtml',
  'syncTariffVoltageTable', 'syncTariffFlatRate', 'has_tiers',
  'current_tier', 'tier_limits', 'tier_kwh', 'tariff-table', 'tariff-tb-'
];
goneWords.forEach(w => ok('monitor.js 不含 ' + w, !SRC.includes(w)));

ok('面板不再有 supports_multi_person 条件', !/supports_multi_person/.test(SRC));
ok('面板不再有 supports_meter_cycle 条件', !/supports_meter_cycle/.test(SRC));

console.log('\n=== 2. 条件渲染：面板里不再有下拉/开关 ===');
const panelFn = extractFn('tariffPanelHtml');
ok('tariffPanelHtml 可提取', !!panelFn);
ok('面板里没有 <select>', panelFn && !panelFn.includes('<select'));
ok('面板里没有 <button> 以外的开关控件',
  panelFn && !panelFn.includes('type="checkbox"'));
ok('面板只渲染一个 input',
  panelFn && (panelFn.match(/<input/g) || []).length === 1,
  panelFn ? String((panelFn.match(/<input/g) || []).length) : 'n/a');
ok('面板渲染后端下发的 note',
  panelFn && /d && d\.note/.test(panelFn));
ok('面板默认值兜底 0.5224', panelFn && /0\.5224/.test(panelFn));

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
  const mk = (key, value) => {
    if (!cache[key]) {
      cache[key] = {
        value: value,
        checked: false,
        getAttribute: function () { return null; },
        contains: function () { return true; }
      };
    }
    return cache[key];
  };
  const box = {
    __vals: vals,
    querySelector: function (sel) {
      if (sel === '[data-tariff-rate]') return mk('rate', vals.__rate);
      return null;
    },
    querySelectorAll: function () { return []; }
  };
  return box;
}

/* 把两个函数塞进沙箱跑 */
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

  const box = makeBox({ __rate: '0.8888' });
  const st = sandbox.captureTariffForm(box);
  ok('capture 抓到 rate=0.8888', st && st.rate === '0.8888', st && st.rate);
  ok('capture 只有 rate 一个字段',
    st && Object.keys(st).length === 1 && Object.keys(st)[0] === 'rate',
    st ? JSON.stringify(Object.keys(st)) : 'null');

  /* 回填到新面板 → 应还原用户编辑 */
  const box2 = makeBox({});
  sandbox.restoreTariffForm(box2, st);
  ok('restore 回填 rate=0.8888',
    box2.querySelector('[data-tariff-rate]').value === '0.8888',
    box2.querySelector('[data-tariff-rate]').value);

  /* 空值 → 回填后端给的生效单价 */
  const box3 = makeBox({});
  box3.__tariffData = { rate: 0.61, config: { rate: 0.61 } };
  sandbox.restoreTariffForm(box3, { rate: null });
  ok('restore 无用户输入时用后端生效单价 0.61',
    String(box3.querySelector('[data-tariff-rate]').value) === '0.61',
    JSON.stringify(box3.querySelector('[data-tariff-rate]').value));

  /* 后端也没给 → 兜底 0.5224 */
  const box4 = makeBox({});
  box4.__tariffData = {};
  sandbox.restoreTariffForm(box4, { rate: null });
  ok('restore 后端也没给时兜底 0.5224',
    String(box4.querySelector('[data-tariff-rate]').value) === '0.5224',
    JSON.stringify(box4.querySelector('[data-tariff-rate]').value));
} catch (e) {
  ok('capture/restore 逻辑级执行', false, e && e.message);
}

console.log('\n=== 4. 逻辑级：保存载荷取值 ===');
/* 模拟保存按钮的取值逻辑（与源码同规则） */
function buildPayload(vals) {
  const payload = {};
  const cfgSave = (vals.__cfg || {});
  if (vals.rate !== undefined) {
    const rv = Number(vals.rate);
    /* ⚠️ 空串要当「未填」处理 —— Number('') 是 0 而不是 NaN */
    payload.rate = (vals.rate !== '' && isFinite(rv)
      && rv >= 0 && rv <= 10) ? rv : (Number(cfgSave.rate) || 0.5224);
  } else {
    payload.rate = Number(cfgSave.rate) || 0.5224;
  }
  return payload;
}
let p = buildPayload({ rate: '0.7', __cfg: { rate: 0.5224 } });
ok('载荷 rate=0.7', p.rate === 0.7, p.rate);
p = buildPayload({ rate: '99', __cfg: { rate: 0.5224 } });
ok('超范围 rate → 兜底 0.5224', p.rate === 0.5224, p.rate);
p = buildPayload({ rate: '', __cfg: { rate: 0.5224 } });
ok('空 rate → 兜底 0.5224', p.rate === 0.5224, p.rate);
p = buildPayload({ rate: '-1', __cfg: { rate: 0.5224 } });
ok('负 rate → 兜底 0.5224', p.rate === 0.5224, p.rate);
p = buildPayload({ __cfg: { rate: 0.61 } });
ok('无输入时用配置里的 rate', p.rate === 0.61, p.rate);
ok('载荷只有 rate 一个键', Object.keys(p).length === 1, JSON.stringify(Object.keys(p)));

console.log('\n=== 5. 源码层：capture / restore / 提交 ===');
ok('capture 采集 rate', /rate:\s*\(rt && rt\.value !== ''\)/.test(SRC));
ok('capture 不再采集 people', !/people:/.test(SRC));
ok('capture 不再采集 meter_months', !/meter_months:/.test(SRC));
ok('capture 不再采集 voltage', !/voltage:/.test(SRC));
ok('capture 不再采集 region', !/region:/.test(SRC));
ok('restore 回填 rate', /st\.rate !== null/.test(SRC));
ok('restore 不再回填 proj_rate', !/st\.proj_rate/.test(SRC));
ok('保存载荷提交 rate', /payload\.rate/.test(SRC));
ok('保存载荷不再提交 proj_rate', !/payload\.proj_rate/.test(SRC));
ok('保存载荷不再提交 region', !/payload\.region/.test(SRC));
ok('保存载荷不再提交 voltage', !/payload\.voltage/.test(SRC));

console.log('\n' + '-'.repeat(56));
console.log('通过 ' + PASS + ' 项，失败 ' + FAIL + ' 项');
if (FAIL) process.exit(1);
console.log('全部通过');
