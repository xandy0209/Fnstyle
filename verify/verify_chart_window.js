/* chart.js 的 X 轴窗口行为单元测试（v1.1.65 新增）
 *
 * 背景
 * ----
 * 老板反馈「内存抽屉中的趋势图也没有实时更新」。
 * 真根因不是「数据没来」，而是 chart.js 的坐标窗口两端
 *   t0 = min(数据点时间戳)、t1 = max(数据点时间戳)
 * 被历史记录「钉死」→ 曲线虽然延伸（live 点进来了），
 * 但时间轴刻度冻结在打开那一刻 → 视觉上就是「没实时更新」。
 *
 * 修法：窗口右端 = max(数据末点, 现在)，左端 = 右端 - span，
 *       窗口长度恒等于 span，随时间滚动。
 *
 * 本测试直接驱动 chart.js 的 LineChart，断言：
 *   ① 窗口长度恒等于 span（数据不足整窗时**不**被压缩）
 *   ② 时间推进 → t0/t1 同步右移（窗口在滚）
 *   ③ t1 ≥ 当前时刻（窗口右端跟住「现在」）
 *   ④ 数据不足整窗时左侧留白而不是压缩窗口
 *   ⑤ 各档位（10m/6h/24h/7d）窗口长度都等于各自 span
 *   ⑥ span=0 时退回按数据范围（不受 now 影响）
 *
 * 用法: node verify/verify_chart_window.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const CHART = path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'chart.js');
let bad = 0;
function check(label, cond, extra) {
  console.log((cond ? '  ✓ ' : '  ✗ ') + label + (extra ? '  → ' + extra : ''));
  if (!cond) bad++;
}

/* ---------- 极简 DOM stub ----------
   chart.js 只用到：classList / style / innerHTML / firstChild /
   querySelector('.mon-chart-tip' | '.mon-chart-empty') / children /
   addEventListener / getBoundingClientRect / ResizeObserver。
   这里用一个「innerHTML 赋值 → 生成对应子节点」的极简解析器，
   只处理 chart.js 用到的那几个固定标签，足够跑通 render。 */
function mkClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    toggle: (c, on) => (on ? set.add(c) : set.delete(c)),
    contains: (c) => set.has(c),
  };
}
function mkNode(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    style: {},
    classList: mkClassList(),
    children: [],
    firstChild: null,
    _attrs: {},
    _html: '',
    setAttribute(k, v) { this._attrs[k] = String(v); },
    getAttribute(k) { return this._attrs[k] === undefined ? null : this._attrs[k]; },
    addEventListener() {},
    removeEventListener() {},
    appendChild(c) { this.children.push(c); this.firstChild = this.children[0] || null; return c; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 600, height: 180, right: 600, bottom: 180 }; },
    querySelector(sel) { return findByClass(this, sel); },
    querySelectorAll(sel) { return findAllByClass(this, sel); },
  };
  /* innerHTML 是**访问器**：赋值时重建 children 树，
     这样 chart.js 构造函数里 `host.innerHTML = '<svg>…</svg>'`
     之后 `host.firstChild` / `host.querySelector('.mon-chart-tip')`
     才拿得到东西。 */
  Object.defineProperty(node, 'innerHTML', {
    get() { return node._html; },
    set(v) {
      node._html = String(v);
      node.children = [];
      node.firstChild = null;
      parseInto(node, node._html);
    },
    enumerable: true,
  });
  return node;
}
function findByClass(root, sel) {
  const cls = String(sel).replace(/^\./, '');
  const stack = [root];
  while (stack.length) {
    const n = stack.shift();
    for (const c of (n.children || [])) {
      if (c.classList && c.classList.contains(cls)) return c;
      stack.push(c);
    }
  }
  return null;
}
function findAllByClass(root, sel) {
  const cls = String(sel).replace(/^\./, '');
  const out = [];
  const stack = [root];
  while (stack.length) {
    const n = stack.shift();
    for (const c of (n.children || [])) {
      if (c.classList && c.classList.contains(cls)) out.push(c);
      stack.push(c);
    }
  }
  return out;
}
/* 极简 innerHTML 解析：把 <svg>…<g class="gg"></g>…</svg> 这种
   固定结构还原成 children 树。只认标签名 + class 属性，够用。 */
function parseInto(node, html) {
  const re = /<(\/?)([a-zA-Z0-9]+)([^>]*?)(\/?)>/g;
  let m, stack = [node];
  while ((m = re.exec(html)) !== null) {
    const closing = m[1] === '/';
    const tag = m[2];
    const attrs = m[3] || '';
    const selfClose = m[4] === '/';
    if (closing) {
      if (stack.length > 1) stack.pop();
      continue;
    }
    const el = mkNode(tag);
    const cm = /class="([^"]*)"/.exec(attrs);
    if (cm) cm[1].split(/\s+/).filter(Boolean).forEach((c) => el.classList.add(c));
    const hm = /hidden/.test(attrs);
    el.hidden = hm;
    stack[stack.length - 1].appendChild(el);
    if (!selfClose && !/^(line|rect|circle|path|text|br|img|input)$/i.test(tag)) {
      stack.push(el);
    }
  }
}

global.document = {
  createElement(tag) { return mkNode(tag); },
};
global.window = global;
global.ResizeObserver = function () {
  return { observe() {}, unobserve() {}, disconnect() {} };
};

/* 把 chart.js 灌进来 */
const src = fs.readFileSync(CHART, 'utf8');
eval(src);

const LineChart = (global.FnStyleChart || {}).LineChart;
if (typeof LineChart !== 'function') {
  console.error('❌ chart.js 未导出 FnStyleChart.LineChart');
  process.exit(1);
}

/* 造一张图 */
function makeChart(span) {
  const host = mkNode('div');
  host._w = 600; host._h = 180;
  host.clientWidth = 600; host.clientHeight = 180;
  const chart = new LineChart(host, {
    series: [{ color: 'red', label: '测试', fmt: (v) => [String(v), ''] }],
    span: span,
    yMin: 0,
    yMax: 100,
    yTick: (v) => String(v),
    legend: mkNode('div'),
  });
  return chart;
}

/* 造数据点：从 now-A 到 now，每 3 秒一个 */
function mkData(nowS, count, gap) {
  const out = [];
  for (let i = 0; i < count; i++) {
    out.push([nowS - (count - 1 - i) * (gap || 3), 40 + (i % 5)]);
  }
  return out;
}

console.log('--- chart.js X 轴窗口行为（v1.1.65） ---');

/* ① 窗口长度恒等于 span —— 数据不足整窗时不压缩 */
{
  const nowS = Date.now() / 1000;
  const ch = makeChart(3600);           /* 1 小时窗口 */
  ch.setData([mkData(nowS, 10)], 3600);  /* 只有 10 个点、跨度 27 秒 */
  const span = ch.t1 - ch.t0;
  check('数据不足整窗时窗口长度仍 = span（3600s）',
    Math.abs(span - 3600) < 1.5, '实际 %.1f s' % span);
  check('窗口右端 t1 ≥ 当前时刻',
    ch.t1 >= nowS - 1, 't1=%.1f now=%.1f' % (ch.t1, nowS));
  check('窗口左端在数据起点之前（左侧留白，不压缩）',
    ch.t0 < nowS - 27, 't0=%.1f 数据起点≈%.1f' % (ch.t0, nowS - 27));
}

/* ② 时间推进 → t0/t1 同步右移（窗口在滚） */
{
  const nowS = Date.now() / 1000;
  const ch = makeChart(3600);
  ch.setData([mkData(nowS, 20)], 3600);
  const t0a = ch.t0, t1a = ch.t1;
  /* 模拟 10 秒后刷新（Date.now 前移）*/
  const realNow = Date.now;
  Date.now = function () { return realNow.call(Date) + 10000; };
  try {
    ch.setData([mkData(nowS + 10, 21)], 3600);
  } finally {
    Date.now = realNow;
  }
  const t0b = ch.t0, t1b = ch.t1;
  check('时间推进 10s 后 t1 右移 ≈10s',
    Math.abs((t1b - t1a) - 10) < 2, 'Δt1=%.1f' % (t1b - t1a));
  check('时间推进 10s 后 t0 同步右移（整窗滚动，不是钉死）',
    Math.abs((t0b - t0a) - 10) < 2, 'Δt0=%.1f' % (t0b - t0a));
  check('窗口长度保持恒定', Math.abs((t1b - t0b) - (t1a - t0a)) < 1,
    '%.1f → %.1f' % (t1a - t0a, t1b - t0b));
}

/* ③ 数据末点晚于「现在」时，窗口右端应取数据末点（不能倒退） */
{
  const nowS = Date.now() / 1000;
  const ch = makeChart(600);
  const future = nowS + 120;
  ch.setData([[[future, 50]]], 600);
  check('数据末点比现在还晚时 t1 = 数据末点',
    Math.abs(ch.t1 - future) < 2, 't1=%.1f future=%.1f' % (ch.t1, future));
  check('此时窗口长度仍 = span',
    Math.abs((ch.t1 - ch.t0) - 600) < 1.5, '%.1f' % (ch.t1 - ch.t0));
}

/* ④ 不同档位窗口长度都等于各自 span */
{
  const nowS = Date.now() / 1000;
  const cases = [[600, '10m'], [21600, '6h'], [86400, '24h'], [604800, '7d']];
  cases.forEach(([sp, name]) => {
    const ch = makeChart(sp);
    ch.setData([mkData(nowS, 30)], sp);
    const got = ch.t1 - ch.t0;
    /* ⚠️ 别写成 '%s ... %ds' % (name, sp) —— '%' 只有一个占位符时
       会把整个数组当成**一个**参数传进去，第二个占位符拿不到值 → 打印 NaN。
       → 用字符串拼接，或显式包一层参数数组。 */
    check(name + ' 档窗口长度 = ' + sp + 's', Math.abs(got - sp) < 1.5,
      '实际 ' + got.toFixed(1) + ' s');
  });
}

/* ⑤ 无 span（span=0）时退回按数据范围，且不受 now 影响 */
{
  const nowS = Date.now() / 1000;
  const ch = makeChart(0);
  ch.setData([mkData(nowS, 12)], 0);
  const dataSpan = 33;                  /* 11 段 × 3s */
  check('span=0 时窗口 = 数据自身范围',
    Math.abs((ch.t1 - ch.t0) - dataSpan) < 1.5,
    '实际 %.1f s' % (ch.t1 - ch.t0));
  check('span=0 时 t0 = 数据起点',
    Math.abs(ch.t0 - (nowS - 33)) < 2, 't0=%.1f' % ch.t0);
}

console.log('');
if (bad) {
  console.error('❌ chart.js 窗口断言失败 ' + bad + ' 项');
  process.exitCode = 1;
} else {
  console.log('🎉 chart.js 窗口断言全部通过');
}
