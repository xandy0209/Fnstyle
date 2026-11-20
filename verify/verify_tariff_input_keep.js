#!/usr/bin/env node
/* ===========================================================================
   v1.1.58 回归测试：电价设置面板的两个用户反馈 bug

   用户反馈（截图）：
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
         （兜底，覆盖切地区 / 主动保存等确实需要重绘的场景）。

   ② 的根因：flat 地区 tiers_* 全是 [None,None,None]，前端仍渲染三行
     档位表，bandLabel 把 None 拼成「不限 / null」。
     修法：profile 增加 has_tiers 标记，前端据此整块隐藏价目表。

   本测试做两件事：
     · 源码级断言：确认上述修法确实写进了源码（防回退）
     · 逻辑级验证：用极简 DOM stub 跑真实的 captureTariffForm /
       restoreTariffForm，验证「输入值能原样恢复」

   真正的浏览器端到端验证在 verify/probe_tariff_input.js（CDP 探针）。
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
section('源码级：自定义单价输入不被清空');

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

section('源码级：固定单价不显示档位表');

check('monitor.js 判断 prof.has_tiers',
  /has_tiers\s*!==\s*false/.test(MON));
check('has_tiers 为 false 时不渲染 .tariff-table',
  /if\s*\(prof\.has_tiers\s*!==\s*false\)\s*\{[\s\S]{0,900}tariff-table/.test(MON));
check('has_tiers 为 false 时把单价并入说明行（不重复渲染两行说明）',
  /flatRate\s*!==\s*null\s*\?\s*'（'\s*\+\s*flatRate/.test(MON) &&
  /flatRate\s*!==\s*null[\s\S]{0,80}元\/kWh/.test(MON));

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
  /* --- 构造一个「用户正在编辑」的固定单价面板 --- */
  const box = makeEl('div');
  box.setAttribute('data-tariff-prof', 'flat');
  box.setAttribute('data-tariff-rendered', '1');

  const en = makeEl('input'); en.setAttribute('data-tariff-enabled', '');
  en.checked = true;
  const rg = makeEl('select'); rg.setAttribute('data-tariff-region', '');
  rg.value = 'flat';
  const rt = makeEl('input'); rt.setAttribute('data-tariff-rate', '');
  rt.value = '0.8888';               /* ← 用户刚输入的自定义单价 */
  const season = makeEl('div'); season.setAttribute('data-tariff-season', '');

  box.appendChild(en); box.appendChild(rg); box.appendChild(rt); box.appendChild(season);

  const st = sandbox.captureTariffForm(box);
  check('captureTariffForm 抓到自定义单价 0.8888',
    st && st.rate === '0.8888', JSON.stringify(st && st.rate));
  check('captureTariffForm 记录渲染地区 flat',
    st && st.prof === 'flat');
  check('captureTariffForm 抓到启用状态 true',
    st && st.enabled === true);

  /* --- 模拟重绘：清空后重建一个新面板 --- */
  const box2 = makeEl('div');
  box2.setAttribute('data-tariff-prof', 'flat');
  box2.setAttribute('data-tariff-rendered', '1');
  const en2 = makeEl('input'); en2.setAttribute('data-tariff-enabled', '');
  const rt2 = makeEl('input'); rt2.setAttribute('data-tariff-rate', '');
  rt2.value = '';                     /* 重绘后是空的 */
  const season2 = makeEl('div'); season2.setAttribute('data-tariff-season', '');
  box2.appendChild(en2); box2.appendChild(rt2); box2.appendChild(season2);

  sandbox.restoreTariffForm(box2, st, 'flat');
  check('restoreTariffForm 把自定义单价还原成 0.8888',
    rt2.value === '0.8888', '实际 "' + rt2.value + '"');
  check('restoreTariffForm 还原启用状态',
    en2.checked === true);

  /* --- 切地区（flat → tianjin）不应把 flat 的自定义单价带过去 --- */
  const box3 = makeEl('div');
  box3.setAttribute('data-tariff-prof', 'tianjin');
  const rt3 = makeEl('input'); rt3.setAttribute('data-tariff-rate', '');
  rt3.value = '';
  box3.appendChild(rt3);
  sandbox.restoreTariffForm(box3, st, 'tianjin');
  check('切地区时自定义单价被清空（不把 flat 的值带到 tianjin）',
    rt3.value === '', '实际 "' + rt3.value + '"');
}

console.log('\n' + '='.repeat(60));
console.log('verify_tariff_input_keep: PASS ' + PASS + ' / FAIL ' + FAIL);
if (FAIL) process.exit(1);
console.log('全部通过');
