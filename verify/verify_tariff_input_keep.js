#!/usr/bin/env node
/* ===========================================================================
   v1.1.63 回归测试：电价单价输入不被清空

   历史沿革
   --------
   v1.1.58 用户反馈（截图）：
     ① 「选择固定单价时自定义单价输入后会被清空」
     ② 「都固定单价了为什么还有档位信息？」（截图显示「第1档 0–null」）

   ① 的根因（三重叠加）：
     a. 能耗抽屉每 3 秒 live 重建一次 → body.innerHTML 重置
        → [data-tariff-box] 连同用户正在输入的 input 一起被冲掉；
     b. loadTariff 每轮都重绘面板 → 即使节点还在，内容也被覆盖；
     c. 重绘后光标位置丢失，用户看着像「输入没生效」。

     修法（三处同时改，缺一不可）：
       - compute.js updateLive：把旧 [data-tariff-box] 节点原样搬回
         （参照图表节点的做法）；
       - monitor.js loadTariff：面板已渲染过就**不重绘**
         （只后台刷新缓存），保住用户编辑与焦点；
       - monitor.js applyTariff：重绘前捕获表单状态、重绘后回填
         （兜底，覆盖主动保存等确实需要重绘的场景）。

   ② v1.1.63 已彻底消失 —— 整个模型只剩一个单价输入框，没有档位信息。

   本测试做两件事：
     · 源码级断言：确认上述修法确实写进了源码（防回退）
     · 逻辑级验证：用极简 DOM stub 跑真实的 captureTariffForm /
       restoreTariffForm，验证「输入值能原样恢复」

   真正的浏览器端到端验证在 verify/probe_tariff_input.py（CDP 探针）。
   =========================================================================== */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const UI = path.join(__dirname, '..', 'app', 'ui');
const MON = fs.readFileSync(path.join(UI, 'static', 'js', 'monitor.js'), 'utf8');
const CMP = fs.readFileSync(path.join(UI, 'static', 'js', 'compute.js'), 'utf8');

let PASS = 0, FAIL = 0;
function check(label, ok, extra) {
  if (ok) { PASS++; console.log('  ✓ ' + label); }
  else { FAIL++; console.log('  ✗ ' + label + (extra ? '  → ' + extra : '')); }
}
function section(t) { console.log('\n--- ' + t + ' ---'); }

/* ============ ① 源码级断言 ============ */
section('源码级：电费单价输入不被清空');

check('monitor.js 存在 captureTariffForm',
  /function\s+captureTariffForm\s*\(/.test(MON));
check('monitor.js 存在 restoreTariffForm',
  /function\s+restoreTariffForm\s*\(/.test(MON));
check('applyTariff 里先捕获再回填',
  /function\s+applyTariff[\s\S]{0,600}captureTariffForm\(tbox\)[\s\S]{0,600}restoreTariffForm\(/.test(MON));
check('applyTariff 打上 data-tariff-rendered 标记',
  /data-tariff-rendered/.test(MON));
check('loadTariff 检测已渲染标记并跳过重绘',
  /data-tariff-rendered/.test(MON) &&
  /loadTariff[\s\S]{0,900}data-tariff-rendered[\s\S]{0,900}return;/.test(MON));
check('captureTariffForm 抓取 data-tariff-rate 的值',
  /captureTariffForm[\s\S]{0,1200}data-tariff-rate/.test(MON));
check('restoreTariffForm 回填 data-tariff-rate 的值',
  /restoreTariffForm[\s\S]{0,1600}data-tariff-rate/.test(MON));
check('compute.js updateLive 保留旧电价面板节点',
  /tariffNode\s*=\s*body\.querySelector\('\[data-tariff-box\]'\)/.test(CMP));
check('compute.js updateLive 把旧电价面板放回原位',
  /tslot\.parentNode\.replaceChild\(tariffNode,\s*tslot\)/.test(CMP));

section('源码级：档位/地区等控件已删除（v1.1.63）');

/* v1.1.63：单一单价模型 —— 面板只剩一个输入框，
   「固定单价不显示档位表」这个老问题从根上消失。 */
const gone = [
  'has_tiers', 'data-tariff-flat-rate', 'tariff-table', 'tariff-tb-',
  'tariffTableHtml', 'tariffDerivationHtml', 'tariffSeasonHtml',
  'data-tariff-enabled', 'data-tariff-region', 'data-tariff-voltage',
  'data-tariff-vstart', 'data-tariff-vend', 'data-tariff-proj-rate',
  'data-tariff-season'
];
gone.forEach(w => check('monitor.js 不含 ' + w, MON.indexOf(w) < 0));

/* ============ ② 逻辑级：真实跑 capture/restore ============ */
section('逻辑级：capture / restore 往返');

/* 极简 DOM：能按属性选择、能读写 value / checked / classList */
function makeEl(tag, attrs) {
  const el = {
    tagName: (tag || 'div').toUpperCase(),
    _attrs: attrs || {}, _listeners: {},
    style: {}, children: [],
    textContent: '', innerHTML: '', value: '', checked: false, disabled: false,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, f) {
        if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
        else if (f) this._s.add(c); else this._s.delete(c);
      }
    },
    get className() { return Array.from(this.classList._s).join(' '); },
    set className(v) { this.classList._s = new Set(String(v || '').split(/\s+/).filter(Boolean)); },
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return this._attrs[k] !== undefined ? this._attrs[k] : null; },
    hasAttribute(k) { return this._attrs[k] !== undefined; },
    removeAttribute(k) { delete this._attrs[k]; },
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(e) { (this._listeners[e.type] || []).forEach(fn => fn(e)); return true; },
    appendChild(c) { this.children.push(c); return c; },
    querySelector(sel) { return this._q(sel)[0] || null; },
    querySelectorAll(sel) { return this._q(sel); },
    _q(sel) {
      const out = [];
      const self = this;
      function walk(n) {
        n.children.forEach(function (c) {
          if (match(c, sel)) out.push(c);
          walk(c);
        });
      }
      function match(el, s) {
        /* 支持 [attr="v"] 与 .class 与 tag */
        let m = s.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
        if (m) {
          const v = el.getAttribute(m[1]);
          if (v === null) return false;
          return m[2] === undefined ? true : v === m[2];
        }
        if (s.charAt(0) === '.') return el.classList.contains(s.slice(1));
        return el.tagName === s.toUpperCase();
      }
      walk(self);
      return out;
    }
  };
  return el;
}

/* 从 monitor.js 里抠出 captureTariffForm / restoreTariffForm 两个函数，
   在带 document stub 的沙箱里跑真实源码（不做正则模拟，是真执行）。 */
function extractFn(src, name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const m = re.exec(src);
  if (!m) return null;
  const start = m.index;
  let i = src.indexOf('{', start);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') { depth--; if (depth === 0) { i = j; break; } }
  }
  return src.slice(start, i + 1);
}

const captureSrc = extractFn(MON, 'captureTariffForm');
const restoreSrc = extractFn(MON, 'restoreTariffForm');
check('能从 monitor.js 抽出 captureTariffForm 源码', !!captureSrc);
check('能从 monitor.js 抽出 restoreTariffForm 源码', !!restoreSrc);

const sandbox = {
  console: console,
  esc: function (s) { return s; },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
try {
  vm.runInContext(captureSrc + '\n' + restoreSrc, sandbox);
} catch (e) {
  check('capture/restore 源码可在沙箱中执行', false, e.message);
}

if (sandbox.captureTariffForm && sandbox.restoreTariffForm) {
  /* --- 构造一个「用户正在编辑」的单价面板 --- */
  const box = makeEl('div');
  box.setAttribute('data-tariff-rendered', '1');

  const rt = makeEl('input'); rt.setAttribute('data-tariff-rate', '');
  rt.value = '0.8888';               /* ← 用户刚输入的单价 */
  box.appendChild(rt);

  const st = sandbox.captureTariffForm(box);
  check('captureTariffForm 抓到单价 0.8888',
    st && st.rate === '0.8888', JSON.stringify(st && st.rate));
  check('captureTariffForm 只采集 rate 一个字段',
    st && Object.keys(st).length === 1 && st.rate === '0.8888',
    st ? JSON.stringify(Object.keys(st)) : 'null');
  check('captureTariffForm 不再采集 prof（无地区）',
    st && st.prof === undefined);
  check('captureTariffForm 不再采集 enabled（无开关）',
    st && st.enabled === undefined);

  /* --- 模拟重绘：清空后重建一个新面板 --- */
  const box2 = makeEl('div');
  box2.setAttribute('data-tariff-rendered', '1');
  const rt2 = makeEl('input'); rt2.setAttribute('data-tariff-rate', '');
  rt2.value = '';                     /* 重绘后是空的 */
  box2.appendChild(rt2);

  sandbox.restoreTariffForm(box2, st);
  check('restoreTariffForm 把单价还原成 0.8888',
    rt2.value === '0.8888', '实际 "' + rt2.value + '"');

  /* --- 空值场景：回填后端给的生效单价 --- */
  const box3 = makeEl('div');
  box3.__tariffData = { rate: 0.61, config: { rate: 0.61 } };
  const rt3 = makeEl('input'); rt3.setAttribute('data-tariff-rate', '');
  box3.appendChild(rt3);
  sandbox.restoreTariffForm(box3, { rate: null });
  check('无用户输入时回填后端生效单价 0.61',
    String(rt3.value) === '0.61', '实际 "' + rt3.value + '"');

  /* --- 后端也没给 → 兜底 0.5224 --- */
  const box4 = makeEl('div');
  box4.__tariffData = {};
  const rt4 = makeEl('input'); rt4.setAttribute('data-tariff-rate', '');
  box4.appendChild(rt4);
  sandbox.restoreTariffForm(box4, { rate: null });
  check('后端也没给时兜底 0.5224',
    String(rt4.value) === '0.5224', '实际 "' + rt4.value + '"');
}

console.log('\n' + '='.repeat(60));
console.log('verify_tariff_input_keep: PASS ' + PASS + ' / FAIL ' + FAIL);
if (FAIL) process.exit(1);
console.log('全部通过');
