/* FnStyle —— 系统重要内容概览（六卡片）
 *
 * ⚠️ v1.1.69 新增。对齐 fnmonitor 总览页的「核心指标」模块：
 *   处理器 / 内存 / 存储 / 网络 / 温度 / 功耗 六张卡片，
 *   每张卡 = 标题 + 状态词 + 点阵大数字 + 环形仪表 + 副标题 + 迷你趋势线。
 *
 * 组件来源：
 *   · 点阵数字（Dot）—— 字模移植自 fnmonitor（MIT License，
 *     Copyright (c) 2026 Misite齊 / iqiuzixuan），渲染方式对齐其 Dot 类。
 *   · 环形仪表（makeGauge）—— 270° 弧线 + 中间图标，移植自 fnmonitor。
 *   · 迷你趋势线（makeSpark）—— 单调三次插值平滑，移植自 fnmonitor。
 *
 * 口径与 fnmonitor 的差异（有意为之）：
 *   · **网络卡片统计的接口范围**与「物理网口数」不同 ——
 *     后端 collect_network() 排除了容器虚拟桥（会重复统计），
 *     但**包含** tailscale0 等 VPN 隧道口（用户确实在用它传数据）。
 *   · 温度卡片优先取 CPU 封装温度，取不到时退回主板温度 / 硬盘最高温。
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  function svgEl(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    attrs = attrs || {};
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function clamp(v, a, b) {
    v = Number(v);
    if (!isFinite(v)) return a;
    return v < a ? a : (v > b ? b : v);
  }

  /* 数值是否有效（对齐 fnmonitor 的 ok()）
   * ⚠️ v1.1.69 修正：原来用 `x.available === false` 判「无数据」，
   *    但 available 字段缺失时（undefined）会走到 else 分支，
   *    把「没数据」显示成「适中 / 充裕」—— 假数据。
   *    正解：**只看数值本身**（非 null / 非空 / 有限）。
   */
  function okNum(v) {
    return v != null && v !== '' && isFinite(v);
  }

  /* ---------------------------------------------------------------------
     点阵数字（Dot）
     ---------------------------------------------------------------------
     字模为 7 行点阵，每个字符宽度不一（5 或 3 或 2）。
     '.' = 熄灭，'#' = 点亮。渲染成 SVG <circle>，
     用 CSS 变量控制「亮/灭」两色。
     --------------------------------------------------------------------- */
  var GLYPH_SRC = {
    '0': ['.###.', '#...#', '#..##', '#.#.#', '##..#', '#...#', '.###.'],
    '1': ['..#..', '.##..', '..#..', '..#..', '..#..', '..#..', '.###.'],
    '2': ['.###.', '#...#', '....#', '...#.', '..#..', '.#...', '#####'],
    '3': ['#####', '...#.', '..#..', '...#.', '....#', '#...#', '.###.'],
    '4': ['...#.', '..##.', '.#.#.', '#..#.', '#####', '...#.', '...#.'],
    '5': ['#####', '#....', '####.', '....#', '....#', '#...#', '.###.'],
    '6': ['..##.', '.#...', '#....', '####.', '#...#', '#...#', '.###.'],
    '7': ['#####', '....#', '...#.', '..#..', '.#...', '.#...', '.#...'],
    '8': ['.###.', '#...#', '#...#', '.###.', '#...#', '#...#', '.###.'],
    '9': ['.###.', '#...#', '#...#', '.####', '....#', '...#.', '.##..'],
    '.': ['.', '.', '.', '.', '.', '.', '#'],
    ',': ['..', '..', '..', '..', '..', '.#', '#.'],
    '-': ['....', '....', '....', '####', '....', '....', '....'],
    ' ': ['..', '..', '..', '..', '..', '..', '..'],
    '%': ['##...', '##..#', '...#.', '..#..', '.#...', '#..##', '...##'],
    '/': ['....#', '....#', '...#.', '..#..', '.#...', '#....', '#....'],
    '°': ['.#.', '#.#', '.#.', '...', '...', '...', '...'],
    '℃': ['.#.', '#.#', '.#.', '...', '...', '...', '...'],
    '↓': ['..#..', '..#..', '..#..', '#.#.#', '.###.', '..#..', '.....'],
    '↑': ['.....', '..#..', '.###.', '#.#.#', '..#..', '..#..', '..#..']
  };

  var GL = {};
  for (var gch in GLYPH_SRC) {
    GL[gch] = { w: GLYPH_SRC[gch][0].length, rows: GLYPH_SRC[gch] };
  }

  function Dot(el) {
    this.el = el;
    this.str = null;
    this.sig = null;
    this.cells = null;
    el.classList.add('ov-dm');
    el.setAttribute('role', 'img');
    if (el.getAttribute('data-dm') != null) this.set(el.getAttribute('data-dm'));
  }

  Dot.prototype.set = function (v) {
    var raw = String(v == null ? '--' : v);
    if (raw === this.str) return;
    this.str = raw;
    this.el.setAttribute('aria-label', raw);
    var chars = Array.from(raw);
    var gl = chars.map(function (c) {
      return GL[c] || GL[c.toUpperCase()] || GL['?'] || GL[' '];
    });
    var sig = gl.map(function (x) { return x.w; }).join(',');
    if (sig !== this.sig) {
      this.build(gl);
      this.sig = sig;
    }
    var k = 0;
    for (var i = 0; i < gl.length; i++) {
      var g = gl[i];
      for (var r = 0; r < 7; r++) {
        var row = g.rows[r] || '';
        for (var c = 0; c < g.w; c++) {
          var lit = row[c] === '#';
          var ci = this.cells[k++];
          if (!ci) continue;
          var cls = lit ? 'on' : '';
          if (ci.getAttribute('class') !== cls) ci.setAttribute('class', cls);
        }
      }
    }
  };

  Dot.prototype.build = function (gl) {
    var U = gl.reduce(function (a, x) { return a + x.w; }, 0) +
      Math.max(0, gl.length - 1);
    var svg = svgEl('svg', {
      viewBox: '0 0 ' + U + ' 7',
      'aria-hidden': 'true',
      preserveAspectRatio: 'xMinYMid meet'
    });
    svg.style.setProperty('--ov-dm-u', U);
    var cells = [];
    var x = 0;
    for (var i = 0; i < gl.length; i++) {
      var g = gl[i];
      for (var r = 0; r < 7; r++) {
        for (var c = 0; c < g.w; c++) {
          var ci = svgEl('circle', {
            cx: x + c + 0.5, cy: r + 0.5, r: 0.39
          });
          svg.appendChild(ci);
          cells.push(ci);
        }
      }
      x += g.w + 1;
    }
    this.el.replaceChildren(svg);
    this.cells = cells;
  };

  var dotMap = new WeakMap();

  function dot(el) {
    if (!el) return null;
    var d = dotMap.get(el);
    if (!d) { d = new Dot(el); dotMap.set(el, d); }
    return d;
  }

  /* ---------------------------------------------------------------------
     环形仪表（270° 弧）
     --------------------------------------------------------------------- */
  /* 环形仪表（对齐 fnmonitor 的 makeGauge）
   * ⚠️ v1.1.69 修正：fnmonitor 的仪表**是有中心图标的** ——
   *    <use href="#i-bolt" x="38" y="38" width="24" height="24" .../>
   *    即 24×24 的图标居中在 100×100 的 viewBox 里。
   *    我之前直接塞了个 16×16 坐标系的 path（没居中也没放大），
   *    缩到 92px 后只剩左上角一个小点 → 看着像渲染缺陷。
   *    正解：把 16×16 的 path 装进 <g transform="translate(38,38) scale(1.5)">，
   *    正好变成 24×24 且居中。 */
  function makeGauge(iconPath) {
    var r = 40, C = 2 * Math.PI * r, A = C * 0.75;
    var svg = svgEl('svg', {
      viewBox: '0 0 100 100', 'aria-hidden': 'true', class: 'ov-gauge'
    });
    var g = svgEl('g', { transform: 'rotate(135 50 50)' });
    svg.appendChild(g);
    var track = svgEl('circle', {
      class: 'ov-gauge-track', cx: 50, cy: 50, r: r, fill: 'none',
      'stroke-width': 7, 'stroke-linecap': 'round',
      'stroke-dasharray': A.toFixed(2) + ' ' + C.toFixed(2)
    });
    g.appendChild(track);
    var val = svgEl('circle', {
      class: 'ov-gauge-val', cx: 50, cy: 50, r: r, fill: 'none',
      'stroke-width': 7, 'stroke-linecap': 'round',
      'stroke-dasharray': A.toFixed(2) + ' ' + C.toFixed(2),
      'stroke-dashoffset': A.toFixed(2)
    });
    g.appendChild(val);
    if (iconPath) {
      /* 16×16 的 path → translate(38,38) scale(1.5) → 24×24 且居中 */
      var ig = svgEl('g', {
        transform: 'translate(38 38) scale(1.5)'
      });
      var p = svgEl('path', {
        d: iconPath, fill: 'none', stroke: 'currentColor',
        'stroke-width': 1.6, 'stroke-linecap': 'round',
        'stroke-linejoin': 'round'
      });
      p.setAttribute('class', 'ov-gauge-ico');
      ig.appendChild(p);
      svg.appendChild(ig);
    }
    return {
      el: svg,
      set: function (f, tone) {
        var off = (A * (1 - clamp(f || 0, 0, 1))).toFixed(2);
        if (val.style.strokeDashoffset !== off + 'px') {
          val.style.strokeDashoffset = off + 'px';
        }
        if (tone != null && val.style.stroke !== tone) {
          val.style.stroke = tone;
        }
      }
    };
  }

  /* ---------------------------------------------------------------------
     迷你趋势线（makeSpark）
     单调三次插值，避免普通三次样条在数据抖动时产生回折。
     --------------------------------------------------------------------- */
  function monoPath(xs, ys) {
    var n = xs.length;
    if (!n) return '';
    var f = function (v) { return Math.round(v * 10) / 10; };
    if (n === 1) return 'M' + f(xs[0]) + ',' + f(ys[0]);
    var dx = [], m = [], t = new Array(n);
    var i;
    for (i = 0; i < n - 1; i++) {
      dx[i] = xs[i + 1] - xs[i] || 1e-6;
      m[i] = (ys[i + 1] - ys[i]) / dx[i];
    }
    t[0] = m[0];
    t[n - 1] = m[n - 2];
    for (i = 1; i < n - 1; i++) {
      t[i] = (m[i - 1] * m[i] <= 0) ? 0 :
        3 * (dx[i - 1] + dx[i]) /
        ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
    }
    var d = 'M' + f(xs[0]) + ',' + f(ys[0]);
    for (i = 0; i < n - 1; i++) {
      var h = dx[i] / 3;
      d += 'C' + f(xs[i] + h) + ',' + f(ys[i] + t[i] * h) + ' ' +
        f(xs[i + 1] - h) + ',' + f(ys[i + 1] - t[i + 1] * h) + ' ' +
        f(xs[i + 1]) + ',' + f(ys[i + 1]);
    }
    return d;
  }

  function makeSpark() {
    var svg = svgEl('svg', { class: 'ov-spark', 'aria-hidden': 'true' });
    var g = svgEl('g', {});
    svg.appendChild(g);
    var series = [];
    var opt = {};
    var obj = {
      el: svg,
      set: function (s, o) { series = s || []; opt = o || {}; draw(); }
    };
    function draw() {
      var w = svg.clientWidth || 240;
      var h = svg.clientHeight || 40;
      if (!w || !h || !series.length) return;
      var lo = opt.min, hi = opt.max;
      var all = [];
      series.forEach(function (s) { all = all.concat(s.values || []); });
      if (!all.length) { g.innerHTML = ''; return; }
      if (lo == null) lo = Math.min.apply(null, all);
      if (hi == null) hi = Math.max.apply(null, all);
      if (hi - lo < 1e-9) hi = lo + Math.max(1, Math.abs(lo) * 0.5);
      if (opt.max == null) hi += (hi - lo) * 0.18;
      var pad = 3;
      var html = '';
      series.forEach(function (s, si) {
        var vals = s.values || [];
        var n = vals.length;
        if (!n) return;
        var xs = vals.map(function (_, i) { return i / (n - 1) * w; });
        var ys = vals.map(function (v) {
          return pad + (1 - (clamp(v, lo, hi) - lo) / (hi - lo)) * (h - pad * 2);
        });
        /* ⚠️ v1.1.69：只有 1 个数据点时画不出曲线（monoPath 需要 ≥2 点）。
           fnmonitor 靠历史数据天然多点；我们首帧只有 1 点 →
           画一个孤零零的圆点占位，等第二个采样点到达后自然变成曲线。
           （否则首帧趋势线区域完全空白，看着像没渲染。） */
        if (n === 1) {
          if (si === 0) {
            html += '<circle class="ov-spark-dot" cx="' + w +
              '" cy="' + ys[0].toFixed(1) + '" r="2.5"/>';
          }
          return;
        }
        var d = monoPath(xs, ys);
        if (s.area !== false && si === 0) {
          html += '<path class="ov-spark-area" d="' + d + 'L' + w + ',' + h +
            'L0,' + h + 'Z"/>';
        }
        html += '<path class="ov-spark-line' + (si ? ' l2' : '') + '" d="' + d + '"/>';
        if (si === 0) {
          html += '<circle class="ov-spark-dot" cx="' + w + '" cy="' +
            ys[n - 1].toFixed(1) + '" r="2.5"/>';
        }
      });
      g.innerHTML = html;
    }
    /* 容器尺寸变化时重画（栅格宽度会随窗口变化） */
    if (typeof ResizeObserver !== 'undefined') {
      try {
        new ResizeObserver(function () { draw(); }).observe(svg);
      } catch (e) { /* 忽略：没有 ResizeObserver 时靠下一次 set 触发重画 */ }
    }
    return obj;
  }

  /* ---------------------------------------------------------------------
     格式化
     --------------------------------------------------------------------- */
  function fmtInt(v) {
    if (v == null || !isFinite(v)) return '--';
    return String(Math.round(v));
  }

  function fmt1(v) {
    if (v == null || !isFinite(v)) return '--';
    return (Math.round(v * 10) / 10).toFixed(1);
  }

  function fmtBytes(v) {
    if (v == null || !isFinite(v)) return '--';
    var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    var i = 0;
    var n = Math.abs(v);
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    var s = (i === 0) ? String(Math.round(v)) : (Math.round(n * 10) / 10).toFixed(1);
    return s + ' ' + units[i];
  }

  function fmtRate(v) {
    if (v == null || !isFinite(v)) return { v: '--', u: '' };
    var units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
    var i = 0;
    var n = Math.abs(v);
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    var num = (i === 0) ? String(Math.round(v)) : (Math.round(n * 10) / 10).toFixed(1);
    return { v: num, u: units[i] };
  }

  function fmtGB(v) {
    if (v == null || !isFinite(v)) return '--';
    return (Math.round(v / 1073741824 * 10) / 10).toFixed(1);
  }

  /* ---------------------------------------------------------------------
     状态阈值（对齐 fnmonitor 的 LV）
     --------------------------------------------------------------------- */
  function lvCPU(p) {
    if (p == null) return '';
    if (p >= 90) return 'crit';
    if (p >= 70) return 'warn';
    return '';
  }

  function lvMem(p) {
    if (p == null) return '';
    if (p >= 90) return 'crit';
    if (p >= 80) return 'warn';
    return '';
  }

  function lvCap(p) {
    if (p == null) return '';
    if (p >= 90) return 'crit';
    if (p >= 80) return 'warn';
    return '';
  }

  function lvTemp(t) {
    if (t == null) return '';
    if (t >= 85) return 'crit';
    if (t >= 70) return 'warn';
    return '';
  }

  var LV_TXT = { warn: '偏高', crit: '严重' };

  /* 阈值 → 颜色档（对应 CSS 的 .is-warn / .is-crit） */
  function toneOf(lv) {
    if (lv === 'crit') return 'var(--danger)';
    if (lv === 'warn') return 'var(--warning)';
    return '';
  }

  /* ---------------------------------------------------------------------
     六卡片渲染
     --------------------------------------------------------------------- */
  var ICONS = {
    cpu: 'M8 2v3M8 11v3M2 8h3M11 8h3M4.2 4.2l2.1 2.1M9.7 9.7l2.1 2.1M11.8 4.2l-2.1 2.1M6.3 9.7l-2.1 2.1',
    mem: 'M2 5h12M2 8h12M2 11h12M4 3v10M8 3v10M12 3v10',
    storage: 'M8 2.2c3.2 0 5.8.8 5.8 1.8S11.2 5.8 8 5.8 2.2 5 2.2 4 4.8 2.2 8 2.2zM2.2 4v8c0 1 2.6 1.8 5.8 1.8s5.8-.8 5.8-1.8V4',
    net: 'M2 8.5h3l2-5 2 9 2-6 1.5 2H14',
    temp: 'M8 5.5a1.5 1.5 0 011.5 1.5v3.2a3 3 0 11-3 0V7A1.5 1.5 0 018 5.5z',
    power: 'M8 2v7M4.6 4.2a5 5 0 106.8 0'
  };

  /* 可打开详情抽屉的卡片 kind —— 与 compute.js 的 openCompute 支持的一致
     （cpu / mem / temp / power 四种）。
     ⚠️ 存储与网络**没有**对应的计算抽屉：若硬塞 data-sheet，
        openCompute 会掉进 else 分支打开「处理器」抽屉 —— 属于张冠李戴。
        这两张卡改为可点击滚动到下方对应板块。 */
  var SHEET_KINDS = { cpu: 1, mem: 1, temp: 1, power: 1 };

  var CARDS = [
    { k: 'cpu', label: '处理器', unit: '%', gauge: 'cpu', sheet: 'cpu' },
    { k: 'mem', label: '内存', unit: '%', gauge: 'mem', sheet: 'mem' },
    { k: 'disk', label: '存储', unit: '%', gauge: 'storage', sheet: '' },
    { k: 'net', label: '网络', unit: '', gauge: '', sheet: '' },
    { k: 'temp', label: '温度', unit: '℃', gauge: 'temp', sheet: 'temp' },
    { k: 'pwr', label: '功耗', unit: 'W', gauge: 'power', sheet: 'power' }
  ];

  var live = {
    cpu: [], mem: [], rx: [], tx: [], temp: [], power: []
  };
  var LIVE_MAX = 60;

  function pushLive(key, v) {
    if (v == null || !isFinite(v)) return;
    var a = live[key];
    a.push(v);
    if (a.length > LIVE_MAX) a.shift();
  }

  function buildCards(host) {
    var html = '<div class="ov-metrics">';
    CARDS.forEach(function (c) {
      /* 只有真正有详情抽屉的卡片才可点（ov-tap + data-sheet）；
         存储 / 网络没有对应抽屉，不加 data-sheet —— 否则会误开处理器抽屉。 */
      var canOpen = !!SHEET_KINDS[c.sheet];
      html += '<article class="ov-metric' + (canOpen ? ' ov-tap' : '') +
        '" data-ov="' + c.k + '"' +
        (canOpen ? ' data-sheet="' + c.sheet + '" tabindex="0" role="button" ' +
          'aria-label="' + c.label + '，查看详情"' : '') + '>' +
        '<div class="ov-m-top"><span class="ov-m-label">' + c.label + '</span>' +
        '<span class="ov-m-state"><span data-ov-state="' + c.k + '"></span></span></div>' +
        '<div class="ov-m-main">';
      if (c.k === 'net') {
        /* 网络卡：上下行两行（对齐 fnmonitor 的 net-vals）
           ⚠️ 网络卡**没有仪表** → 数字区要横跨两列，
           否则右侧会空出一列 92px 的白区。 */
        html += '<div class="ov-net-vals">' +
          '<div class="row"><span class="ov-arrow">↓</span>' +
          '<span class="ov-dnum"><span class="ov-dm" data-ov-val="rxV"></span>' +
          '<span class="ov-unit" data-ov-unit="rxU"></span></span></div>' +
          '<div class="row sec"><span class="ov-arrow">↑</span>' +
          '<span class="ov-dnum"><span class="ov-dm" data-ov-val="txV"></span>' +
          '<span class="ov-unit" data-ov-unit="txU"></span></span></div>' +
          '</div>';
      } else {
        html += '<span class="ov-dm" data-ov-val="' + c.k + 'V"></span>' +
          '<span class="ov-unit" data-ov-unit="' + c.k + 'U">' + c.unit + '</span>';
      }
      html += '</div>';
      if (c.gauge) {
        html += '<div class="ov-m-gauge" data-ov-gauge="' + c.k + '"></div>';
      }
      html += '<div class="ov-m-sub" data-ov-sub="' + c.k + '"></div>' +
        '<div class="ov-m-foot" data-ov-foot="' + c.k + '"></div>' +
        '</article>';
    });
    html += '</div>';
    host.innerHTML = html;

    /* 挂载 gauge 与 spark */
    CARDS.forEach(function (c) {
      if (!c.gauge) return;
      var slot = host.querySelector('[data-ov-gauge="' + c.k + '"]');
      if (!slot) return;
      var g = makeGauge(ICONS[c.gauge]);
      slot.appendChild(g.el);
      g.el.setAttribute('data-ov-gauge-el', c.k);
      /* 实例挂在 DOM 节点上（render 时按 data 属性回查） */
      g.el._ovGauge = g;
    });
    ['cpu', 'mem', 'net', 'temp', 'pwr'].forEach(function (k) {
      var slot = host.querySelector('[data-ov-foot="' + k + '"]');
      if (!slot) return;
      var sp = makeSpark();
      slot.appendChild(sp.el);
      sp.el.setAttribute('data-ov-spark', k);
      sp.el._ovSpark = sp;
    });
  }

  function setState(host, k, lv, txt) {
    var el = host.querySelector('[data-ov-state="' + k + '"]');
    if (!el) return;
    el.textContent = txt || '';
    var card = host.querySelector('[data-ov="' + k + '"]');
    if (!card) return;
    card.classList.remove('is-warn', 'is-crit');
    if (lv) card.classList.add('is-' + lv);
  }

  function setSub(host, k, txt) {
    var el = host.querySelector('[data-ov-sub="' + k + '"]');
    if (el) el.textContent = txt || '';
  }

  /* 温度：取 CPU 封装 → 主板 → 硬盘最高
     ⚠️ 后端字段名是 temps（不是 sensors），
        且 cpuTemp / mbTemp 已由后端算好，这里优先用。 */
  function tempOf(d) {
    var t = (d && d.temp) || {};
    var list = t.temps || t.sensors || t.items || [];
    var cpu = null, mb = null, disk = null;
    for (var i = 0; i < list.length; i++) {
      var x = list[i] || {};
      var type = String(x.type || '').toLowerCase();
      var val = x.value != null ? Number(x.value) : null;
      if (val == null || !isFinite(val)) continue;
      if (type === 'cpu' && (cpu == null || val > cpu)) cpu = val;
      else if (type === 'board' && (mb == null || val > mb)) mb = val;
      else if (type === 'disk' && (disk == null || val > disk)) disk = val;
    }
    if (cpu == null && t.cpuTemp != null) cpu = Number(t.cpuTemp);
    if (mb == null && t.mbTemp != null) mb = Number(t.mbTemp);
    /* 硬盘最高温：后端 temp.disks 里带 temperature */
    var dks = t.disks || [];
    for (var j = 0; j < dks.length; j++) {
      var dv = Number((dks[j] || {}).temperature);
      if (isFinite(dv) && (disk == null || dv > disk)) disk = dv;
    }
    var main = (cpu != null) ? cpu : ((mb != null) ? mb : disk);
    return { main: main, cpu: cpu, mb: mb, disk: disk };
  }

  /* 存储池汇总
     ⚠️ 后端字段：storage.summary（含 total/used/percent/poolCount/diskCount）
        + storage.volumes（逐池）+ storage.disks（逐盘）。
        这里优先用 summary（与存储板块同一口径，避免两处数字对不上）。 */
  function diskOf(d) {
    var st = (d && d.storage) || {};
    var sum = st.summary || {};
    var volumes = st.volumes || [];
    var total = Number(sum.total) || 0;
    var used = Number(sum.used) || 0;
    if (!total) {
      volumes.forEach(function (v) {
        total += Number(v.total) || 0;
        used += Number(v.used) || 0;
      });
    }
    var pct = sum.percent != null ? Number(sum.percent)
      : (total > 0 ? used / total * 100 : null);
    if (pct != null && !isFinite(pct)) pct = null;
    return {
      total: total, used: used, pct: pct,
      poolCount: sum.poolCount != null ? sum.poolCount : volumes.length,
      disks: st.disks || []
    };
  }

  function render(host, d) {
    if (!host) return;
    if (!host._built) { buildCards(host); host._built = true; }

    /* ---- 处理器 ---- */
    var cpu = (d && d.cpu) || {};
    var cp = cpu.percent;
    var lc = lvCPU(cp);
    var cv = host.querySelector('[data-ov-val="cpuV"]');
    if (cv) dot(cv).set(fmtInt(cp));
    setState(host, 'cpu', lc, !okNum(cp) ? '无数据'
      : (lc === 'crit' ? '高负载' : (lc === 'warn' ? '繁忙'
        : (cp < 35 ? '平稳' : '适中'))));
    var cpuSub = [];
    if (cpu.cores) cpuSub.push(cpu.cores + ' 核');
    if (cpu.freqText) cpuSub.push(cpu.freqText);
    if (cpu.loadavg && cpu.loadavg.length) cpuSub.push('负载 ' + cpu.loadavg[0]);
    setSub(host, 'cpu', cpuSub.join(' · '));
    var cg = findGauge(host, 'cpu');
    if (cg) cg.set(cp == null ? 0 : cp / 100, toneOf(lc));
    pushLive('cpu', cp);

    /* ---- 内存 ---- */
    var mem = (d && d.mem) || {};
    var mp = mem.percent;
    var lm = lvMem(mp);
    var mv = host.querySelector('[data-ov-val="memV"]');
    if (mv) dot(mv).set(fmtInt(mp));
    setState(host, 'mem', lm, !okNum(mp) ? '无数据'
      : (lm ? LV_TXT[lm] : '充裕'));
    var memSub = [];
    if (mem.totalText) memSub.push(mem.usedText + ' / ' + mem.totalText);
    if (mem.availText) memSub.push('可用 ' + mem.availText);
    setSub(host, 'mem', memSub.join(' · '));
    var mg = findGauge(host, 'mem');
    if (mg) mg.set(mp == null ? 0 : mp / 100, toneOf(lm));
    pushLive('mem', mp);

    /* ---- 存储 ---- */
    var dk = diskOf(d);
    var ld = lvCap(dk.pct);
    var dv = host.querySelector('[data-ov-val="diskV"]');
    if (dv) dot(dv).set(dk.pct == null ? '--' : fmtInt(dk.pct));
    var smartBad = 0;
    dk.disks.forEach(function (x) {
      var g = String((x && (x.grade || x.health || x.smartStatus)) || '');
      if (/warn|crit|bad|fail/i.test(g)) smartBad++;
    });
    setState(host, 'disk', ld || (smartBad ? 'warn' : ''),
      dk.pct == null ? '无存储池'
        : (ld === 'crit' ? '将满' : (ld === 'warn' ? '偏满'
          : (smartBad ? smartBad + ' 块需留意' : '充足'))));
    var diskSub = [];
    if (dk.total > 0) {
      diskSub.push(fmtBytes(dk.used) + ' / ' + fmtBytes(dk.total));
    }
    if (dk.disks.length) diskSub.push(dk.disks.length + ' 块硬盘');
    setSub(host, 'disk', diskSub.join(' · ') || '未检测到存储');
    var dg = findGauge(host, 'disk');
    if (dg) dg.set(dk.pct == null ? 0 : dk.pct / 100, toneOf(ld));
    /* 盘位指示条（对齐 fnmonitor 的 drive-pips） */
    renderPips(host, dk.disks);

    /* ---- 网络 ---- */
    var net = (d && d.network) || {};
    var rxv = host.querySelector('[data-ov-val="rxV"]');
    var txv = host.querySelector('[data-ov-val="txV"]');
    var rxu = host.querySelector('[data-ov-unit="rxU"]');
    var txu = host.querySelector('[data-ov-unit="txU"]');
    var rr = fmtRate(net.rxRate), tr = fmtRate(net.txRate);
    if (rxv) dot(rxv).set(net.available ? rr.v : '--');
    if (txv) dot(txv).set(net.available ? tr.v : '--');
    if (rxu) rxu.textContent = net.available ? rr.u : '';
    if (txu) txu.textContent = net.available ? tr.u : '';
    var rxT = (net.rxRate || 0) + (net.txRate || 0);
    setState(host, 'net', '', !net.available ? '无网卡'
      : (rxT < 10240 ? '空闲' : '活跃'));
    var netSub = [];
    if (net.todayRx != null) {
      netSub.push('今日 ↓ ' + fmtBytes(net.todayRx));
      netSub.push('↑ ' + fmtBytes(net.todayTx));
    } else if (net.ifaceCount) {
      netSub.push(net.ifaceCount + ' 个网口');
    }
    setSub(host, 'net', netSub.join(' · '));
    pushLive('rx', net.rxRate);
    pushLive('tx', net.txRate);

    /* ---- 温度 ---- */
    var tp = tempOf(d);
    var lt = lvTemp(tp.main);
    var tv = host.querySelector('[data-ov-val="tempV"]');
    if (tv) dot(tv).set(tp.main == null ? '--' : fmtInt(tp.main));
    setState(host, 'temp', lt, tp.main == null ? '无传感器'
      : (lt === 'crit' ? '过热' : (lt === 'warn' ? '偏高' : '正常')));
    var tempSub = [];
    if (tp.cpu != null && tp.mb != null) tempSub.push('主板 ' + fmtInt(tp.mb) + '℃');
    if (tp.disk != null) tempSub.push('硬盘最高 ' + fmtInt(tp.disk) + '℃');
    setSub(host, 'temp', tempSub.join(' · ') || 'CPU 封装温度');
    var tg = findGauge(host, 'temp');
    if (tg) {
      tg.set(tp.main == null ? 0 : (tp.main - 20) / 80, toneOf(lt));
    }
    pushLive('temp', tp.cpu != null ? tp.cpu : tp.main);

    /* ---- 功耗 ---- */
    var pw = (d && d.power) || {};
    var w = pw.ok ? Number(pw.total) : null;
    var pv = host.querySelector('[data-ov-val="pwrV"]');
    if (pv) dot(pv).set(w == null ? '--' : fmt1(w));
    setState(host, 'pwr', '', !pw.ok ? '不可用'
      : (pw.source === 'rapl' ? '实测 + 估算' : '估算'));
    var pwrSub = [];
    /* ⚠️ 功耗统计是**独立接口**（/api/power/stats，异步加载），
       字段是扁平的：today_kwh / today_yuan（不是嵌套的 energy 对象）。
       拿不到时退回到「整机功耗」文案，不显示 '--'。 */
    var ps = (d && d.powerStats) || null;
    if (ps && ps.today_kwh != null) {
      pwrSub.push('今日 ' + (Math.round(ps.today_kwh * 1000) / 1000) + ' kWh');
      if (ps.today_yuan != null) {
        pwrSub.push('¥ ' + (Math.round(ps.today_yuan * 100) / 100));
      }
    } else if (pw.source === 'rapl') {
      pwrSub.push('硬件 RAPL 读数');
    } else if (pw.source === 'estimated') {
      pwrSub.push('按部件模型估算');
    }
    setSub(host, 'pwr', pwrSub.join(' · ') || '整机功耗');
    var pg = findGauge(host, 'pwr');
    if (pg) pg.set(0, '');
    pushLive('power', w);

    /* ---- 迷你趋势线 ---- */
    setSpark(host, 'cpu', [live.cpu], { min: 0, max: 100 });
    setSpark(host, 'mem', [live.mem], { min: 0, max: 100 });
    setSpark(host, 'net', [{ values: live.rx }, { values: live.tx }], { min: 0 });
    setSpark(host, 'temp', [live.temp], {});
    setSpark(host, 'pwr', [live.power], { min: 0 });
  }

  function findGauge(host, k) {
    var el = host.querySelector('[data-ov-gauge-el="' + k + '"]');
    if (!el) return null;
    /* gauge 实例挂在 DOM 节点上 */
    return el._ovGauge || null;
  }

  function setSpark(host, k, series, opt) {
    var el = host.querySelector('[data-ov-spark="' + k + '"]');
    if (!el) return;
    var sp = el._ovSpark;
    if (!sp) return;
    sp.set(series, opt);
  }

  /* 盘位指示条：每块盘一条，填充高度 = 已用百分比
     （对齐 fnmonitor 的 drive-pips：条 + 下方设备名） */
  function renderPips(host, disks) {
    var foot = host.querySelector('[data-ov-foot="disk"]');
    if (!foot) return;
    var box = foot.querySelector('.ov-pips');
    if (!box) {
      box = document.createElement('div');
      box.className = 'ov-pips';
      foot.appendChild(box);
    }
    if (!disks || !disks.length) {
      box.innerHTML = '';
      box.style.display = 'none';
      return;
    }
    box.style.display = '';
    var html = '';
    disks.slice(0, 12).forEach(function (x) {
      var nm = String((x && (x.name || x.dev || x.device)) || '');
      var pct = Number((x && (x.percent != null ? x.percent : x.usage)) || 0);
      if (!isFinite(pct)) pct = 0;
      var h = clamp(pct, 0, 100).toFixed(1);
      /* ⚠️ v1.1.69：结构必须与 fnmonitor 的 .drive-pips 一致 ——
         外层 flex 容器 → 每块盘一个**列容器**（grid：条 + 名字）→
         条（.ov-pip，position:relative）+ 填充（i，absolute 贴底）。
         之前少了一层列容器，直接把 .ov-pip 当列容器 →
         `inset:auto 0 0 0` 定位失效 → 条变成横铺的大圆角块。
         与 fnmonitor 的 <div><div class="pip"><i></i></div><span>…</span></div> 对齐。 */
      html += '<div><div class="ov-pip"><i style="height:' + h + '%"></i></div>' +
        '<span>' + esc(nm) + '</span></div>';
    });
    box.innerHTML = html;
  }

  /* HTML 转义（盘位名可能含特殊字符） */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ---------------------------------------------------------------------
     对外接口
     --------------------------------------------------------------------- */
  var API = {
    render: function (host, d) {
      try { render(host, d); } catch (e) {
        if (window.console) console.error('[overview] render 失败', e);
      }
    },
    reset: function () {
      live.cpu = []; live.mem = []; live.rx = []; live.tx = [];
      live.temp = []; live.power = [];
    },
    /* 供测试用：暴露内部工具 */
    _internal: {
      dot: dot, makeGauge: makeGauge, makeSpark: makeSpark,
      fmtBytes: fmtBytes, fmtRate: fmtRate, fmtInt: fmtInt, fmt1: fmt1,
      fmtGB: fmtGB, clamp: clamp, esc: esc,
      lvCPU: lvCPU, lvMem: lvMem, lvCap: lvCap, lvTemp: lvTemp,
      tempOf: tempOf, diskOf: diskOf, monoPath: monoPath,
      okNum: okNum, CARDS: CARDS, SHEET_KINDS: SHEET_KINDS,
      live: live, pushLive: pushLive
    }
  };

  window.FnStyleOverview = API;
})();
