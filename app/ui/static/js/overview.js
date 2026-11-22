/* FnStyle —— 系统重要内容概览（六卡片）
 *
 * ⚠️ v1.1.70 返工：对齐 fnmonitor 总览页的「核心指标」模块。
 *
 * 组件来源（结构照搬 fnmonitor，保留 FnStyle 蓝色主题）：
 *   · 卡片结构：m-top(标题+状态词) / m-main(大数字+环形仪表) / m-sub(副标题) / m-foot(趋势线或盘位条)
 *   · 环形仪表：270° 弧线 + 中心 24×24 官方图标（#i-cpu / #i-mem / #i-storage / #i-temp / #i-bolt）
 *   · 迷你趋势线：单调三次插值平滑，深色主线 + 浅灰面积 + 末端实心点
 *   · 盘位指示条：每块盘一条，填充高度 = 已用百分比，按占用率着色
 *
 * ⚠️ v1.1.70 老板指示：**不使用点阵数字**，保持与项目其它板块
 *    （能耗 / 温度 / 存储 / 对比）完全一致的大数字字体 ——
 *    `.dnum > .dm` 普通文本（--font-display，700，tabular-nums）。
 *    v1.1.69 用的 5×7 LED 点阵（SVG circle 阵）已整体下线。
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

  /* HTML 转义（盘位名可能含特殊字符） */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ---------------------------------------------------------------------
     色阶（对齐 fnmonitor 的 RAMP / rampAt / rampInk）
     ---------------------------------------------------------------------
     fnmonitor 的点阵/大数字颜色由 rampInk(v, ramp) 决定：
       低 → 绿，逼近告警线 → 黄绿，越过 → 橙（warn），越过严重线 → 红（crit）。
     FnStyle 保留蓝色主题，所以**绿色段换成我们的 --accent 蓝**，
     告警段仍用 --warning（橙）/ --danger（红）——
     「正常=品牌蓝，越高越橙/红」与 fnmonitor 的语义一致。

     rampAt() 用于**连续填充**（仪表弧、盘位条），
     rampInk() 用于**文字/大数字**（只有 4 档硬色，不做渐变）。
     --------------------------------------------------------------------- */
  var RAMP = {
    /* 百分比型：正常段(蓝) → 接近告警(黄绿→橙) → 越界(红)
       ⚠️ v1.1.71：中间档必须用**实心**色（--ramp-mid / --ramp-mid-2），
          不能用 --warning-soft —— 那是 12% 透明度的背景色，
          当文字色 / 填充色用会与底色相混 → 发灰（实测存储 69% 变米色）。 */
    pct: function (warn, crit) {
      return [
        [0, 'var(--accent)'],
        [warn * 0.5, 'var(--accent)'],
        [warn * 0.8, 'var(--ramp-mid)'],
        [warn, 'var(--warning)'],
        [Math.max(crit, warn), 'var(--danger)']
      ];
    },
    cpu: function () { return RAMP.pct(TH.cpuWarn, TH.cpuCrit); },
    mem: function () { return RAMP.pct(TH.memWarn, TH.memCrit); },
    cap: function () { return RAMP.pct(TH.capWarn, TH.capCrit); },
    gen: function () { return RAMP.pct(70, 90); },
    /* 温度：起点 30℃ 即为正常段末尾，70℃ 起橙，85℃ 起红 */
    temp: function () {
      var w = TH.tempWarn, c = TH.tempCrit;
      return [
        [Math.min(30, w - 26), 'var(--accent)'],
        [w - 25, 'var(--accent)'],
        [w - 10, 'var(--ramp-mid)'],
        [w, 'var(--warning)'],
        [c, 'var(--danger)']
      ];
    }
  };

  /* 阈值（对齐 fnmonitor 的 TH_SPEC 默认值：
     cpuWarn 85 / cpuCrit 95 / memWarn 85 / memCrit 95 /
     capWarn 85 / capCrit 95 / tempWarn 80 / tempCrit 90） */
  var TH = {
    cpuWarn: 85, cpuCrit: 95,
    memWarn: 85, memCrit: 95,
    capWarn: 85, capCrit: 95,
    tempWarn: 80, tempCrit: 90
  };

  function rampStops(k) {
    return Array.isArray(k) ? k : RAMP[k]();
  }

  /* 连续取色：用于仪表弧 / 盘位条填充 */
  function rampAt(v, k) {
    var st = rampStops(k);
    if (v <= st[0][0]) return st[0][1];
    for (var i = 1; i < st.length; i++) {
      if (v <= st[i][0]) {
        var t = clamp((v - st[i - 1][0]) / ((st[i][0] - st[i - 1][0]) || 1), 0, 1);
        if (t < 0.02) return st[i - 1][1];
        if (t > 0.98) return st[i][1];
        return 'color-mix(in oklch, ' + st[i][1] + ' ' + Math.round(t * 100) +
          '%, ' + st[i - 1][1] + ')';
      }
    }
    return st[st.length - 1][1];
  }

  /* 分级取色：用于大数字（文字只能是实色，不能渐变）
     与 fnmonitor rampInk 同构 —— 4 档：正常 / 接近告警 / 告警 / 严重 */
  function rampInk(v, k) {
    var st = rampStops(k);
    if (v >= st[4][0]) return 'var(--danger)';
    if (v >= st[3][0]) return 'var(--warning)';
    if (v >= (st[2][0] + st[3][0]) / 2) return 'var(--ramp-mid-2)';
    var t = clamp((v - st[1][0]) / ((st[2][0] - st[1][0]) || 1), 0, 1);
    if (t < 0.02) return 'var(--accent)';
    if (t > 0.98) return 'var(--ramp-mid)';
    return 'color-mix(in oklch, var(--ramp-mid) ' + Math.round(t * 100) +
      '%, var(--accent))';
  }

  /* 给元素上色（对齐 fnmonitor 的 inkTo，带脏检查避免重复写样式） */
  function inkTo(el, col) {
    if (el && el._ink !== col) {
      el._ink = col;
      el.style.color = col;
    }
  }

  /* ---------------------------------------------------------------------
     官方图标（移植自 fnmonitor 的 <symbol> sprite，24×24 viewBox）
     ---------------------------------------------------------------------
     fnmonitor 用 <use href="#i-bolt"> 引用全局 sprite，
     我们没有那套 sprite → 把 symbol 里的图形**内联**成 SVG 元素。
     元素类型混合（rect / ellipse / path），所以用描述表 + 构建器，
     不能简单当成单个 path。

     注意：图标**始终描边**（fill:none + stroke:currentColor），
     颜色由 CSS 的 .ov-gauge-ico 给 --text-muted（对齐 fnmonitor 的 ink-3）。
     --------------------------------------------------------------------- */
  var ICONS = {
    /* #i-cpu：芯片方框 + 引脚 */
    cpu: [
      { t: 'rect', x: 5, y: 5, width: 14, height: 14, rx: 3 },
      { t: 'rect', x: 9.25, y: 9.25, width: 5.5, height: 5.5, rx: 1 },
      { t: 'path', d: 'M9 2.5V5M15 2.5V5M9 19v2.5M15 19v2.5M2.5 9H5M2.5 15H5M19 9h2.5M19 15h2.5' }
    ],
    /* #i-mem：内存条 */
    mem: [
      { t: 'rect', x: 2.5, y: 6.5, width: 19, height: 10, rx: 2 },
      { t: 'path', d: 'M7 10v3M11 10v3M15 10v3M6 16.5V19M10 16.5V19M14 16.5V19M18 16.5V19' }
    ],
    /* #i-storage：硬盘圆柱 */
    storage: [
      { t: 'ellipse', cx: 12, cy: 6, rx: 8, ry: 3 },
      { t: 'path', d: 'M4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3' }
    ],
    /* #i-temp：温度计 */
    temp: [
      { t: 'path', d: 'M14 14.76V5a2 2 0 1 0-4 0v9.76a4 4 0 1 0 4 0Z' },
      { t: 'path', d: 'M12 9.5v8' }
    ],
    /* #i-bolt：闪电（功耗） */
    power: [
      { t: 'path', d: 'M13 2.5 4.5 13.5H12l-1 8 8.5-11H12l1-8Z' }
    ]
  };

  /* 上下行箭头（移植自 fnmonitor 的 #i-down / #i-up，24×24） */
  var ARROWS = {
    down: [{ t: 'path', d: 'M12 5v14m0 0-5.5-5.5M12 19l5.5-5.5' }],
    up: [{ t: 'path', d: 'M12 19V5m0 0L6.5 10.5M12 5l5.5 5.5' }]
  };

  /* 构建图标组：24×24 坐标系，可平移缩放 */
  function buildIcon(parts, cls) {
    var g = svgEl('g', { class: cls || '' });
    if (!parts) return g;
    parts.forEach(function (p) {
      var e;
      if (p.t === 'rect') {
        e = svgEl('rect', {
          x: p.x, y: p.y, width: p.width, height: p.height, rx: p.rx || 0
        });
      } else if (p.t === 'ellipse') {
        e = svgEl('ellipse', { cx: p.cx, cy: p.cy, rx: p.rx, ry: p.ry });
      } else {
        e = svgEl('path', { d: p.d });
      }
      e.setAttribute('fill', 'none');
      e.setAttribute('stroke', 'currentColor');
      e.setAttribute('stroke-width', '1.6');
      e.setAttribute('stroke-linecap', 'round');
      e.setAttribute('stroke-linejoin', 'round');
      g.appendChild(e);
    });
    return g;
  }

  /* ---------------------------------------------------------------------
     环形仪表（270° 弧 + 中心 24×24 图标）
     ---------------------------------------------------------------------
     对齐 fnmonitor 的 makeGauge：
       <svg class="m-gauge" viewBox="0 0 100 100">
         <g transform="rotate(135 50 50)">
           <circle class="gauge-track" ... stroke-dasharray="188.50 251.33"/>
           <circle class="gauge-val"   ... stroke-dashoffset="188.50"/>
         </g>
         <use href="#i-bolt" x="38" y="38" width="24" height="24" .../>
       </svg>
     图标位置：x=38 y=38 宽高 24 → 正好居中在 100×100 的画布里。
     --------------------------------------------------------------------- */
  function makeGauge(iconParts) {
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
    if (iconParts && iconParts.length) {
      /* ⚠️ v1.1.72：仪表 76 → 64px，图标同步缩到 17×17，
         保持官方比例（24/92 ≈ 26% → 17/64 ≈ 26.6%）。
         偏移 (100-17)/2 = 41.5 → 正好居中；缩放 17/24 = 0.7083。 */
      var ig = svgEl('g', { transform: 'translate(41.5 41.5) scale(0.7083)' });
      ig.appendChild(buildIcon(iconParts, 'ov-gauge-ico'));
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
     配色对齐 fnmonitor：主线深色、面积浅灰、末端实心点。
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
      set: function (s, o) { series = normSeries(s); opt = o || {}; draw(); }
    };
    /* ⚠️ v1.1.72：容错 —— series 允许传裸数组 [1,2,3] 或 {values:[...]}。
       之前只认 {values:[]}，传裸数组就静默不画（趋势线整条消失）。 */
    function normSeries(s) {
      if (!s) return [];
      var arr = Array.isArray(s) ? s : [s];
      return arr.map(function (x) {
        if (x && typeof x === 'object' && Array.isArray(x.values)) return x;
        return { values: Array.isArray(x) ? x : [] };
      });
    }
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
        var xs = vals.map(function (_, i) {
          /* ⚠️ n=1 时 n-1=0 → 除零 → NaN → 圆点画不出来。
             单点时直接落在最右端。 */
          return n > 1 ? i / (n - 1) * w : w;
        });
        var ys = vals.map(function (v) {
          return pad + (1 - (clamp(v, lo, hi) - lo) / (hi - lo)) * (h - pad * 2);
        });
        /* ⚠️ 只有 1 个数据点时画不出曲线（monoPath 需要 ≥2 点）。
           首帧只有 1 点 → 画一个孤零零的圆点占位，
           等第二个采样点到达后自然变成曲线（否则首帧趋势线区域完全空白）。 */
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
    if (Math.abs(v) >= 100) return String(Math.round(v));
    return (Math.round(v * 10) / 10).toFixed(1);
  }

  function fmtBytes(v) {
    if (v == null || !isFinite(v)) return '--';
    var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    var i = 0;
    var n = Math.abs(v);
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    var s = (i === 0) ? String(Math.round(v))
      : (n >= 100 ? String(Math.round(n)) : n.toFixed(1));
    return s + ' ' + units[i];
  }

  function fmtRate(v) {
    if (v == null || !isFinite(v)) return { v: '--', u: '' };
    var p = fmtBytes(v);
    var sp = p.split(' ');
    return { v: sp[0], u: sp[1] ? sp[1] + '/s' : '' };
  }

  function fmtGB(v) {
    if (v == null || !isFinite(v)) return '--';
    return (Math.round(v / 1073741824 * 10) / 10).toFixed(1);
  }

  /* 十进制容量（对齐 fnmonitor 的 F.dsize）
   * 口径：T=1e12, G=1e9, M=1e6 —— 与磁盘厂商标称容量一致。
   * 1.76 TB → v>=10 用一位小数、v<10 用两位小数、v>=100 取整。 */
  function fmtDSize(v) {
    if (v == null || !isFinite(v)) return '--';
    var T = 1e12, G = 1e9, M = 1e6;
    var n = Math.abs(v);
    if (n >= T) {
      var t = n / T;
      return (t >= 100 ? String(Math.round(t)) : (t >= 10 ? t.toFixed(1) : t.toFixed(2))) + ' TB';
    }
    if (n >= G) {
      var g = n / G;
      return (g >= 100 ? String(Math.round(g)) : g.toFixed(1)) + ' GB';
    }
    return String(Math.round(n / M)) + ' MB';
  }

  /* ---------------------------------------------------------------------
     状态阈值（对齐 fnmonitor 的 LV）
     --------------------------------------------------------------------- */
  function lvCPU(p) {
    if (p == null) return '';
    if (p >= TH.cpuCrit) return 'crit';
    if (p >= TH.cpuWarn) return 'warn';
    return '';
  }

  function lvMem(p) {
    if (p == null) return '';
    if (p >= TH.memCrit) return 'crit';
    if (p >= TH.memWarn) return 'warn';
    return '';
  }

  function lvCap(p) {
    if (p == null) return '';
    if (p >= TH.capCrit) return 'crit';
    if (p >= TH.capWarn) return 'warn';
    return '';
  }

  function lvTemp(t) {
    if (t == null) return '';
    if (t >= TH.tempCrit) return 'crit';
    if (t >= TH.tempWarn) return 'warn';
    return '';
  }

  var LV_TXT = { warn: '偏高', crit: '严重' };

  /* ---------------------------------------------------------------------
     六卡片渲染
     ---------------------------------------------------------------------
     卡片结构（对齐 fnmonitor 的 card()）：
       <article class="ov-metric">
         <div class="ov-m-top">标题 / 状态词</div>
         <div class="ov-m-main">大数字(+单位) / 仪表</div>
         <div class="ov-m-sub">副标题</div>
         <div class="ov-m-foot">趋势线 或 盘位条</div>
       </article>
     网络卡无仪表 → 数字区横跨两列（对齐 fnmonitor 的 style="grid-column:1/-1"）。
     --------------------------------------------------------------------- */
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
          '<div class="row"><span class="ov-arrow" data-ov-arrow="down"></span>' +
          '<span class="ov-dnum"><span class="ov-dm" data-ov-val="rxV"></span>' +
          '<span class="ov-unit" data-ov-unit="rxU"></span></span></div>' +
          '<div class="row sec"><span class="ov-arrow" data-ov-arrow="up"></span>' +
          '<span class="ov-dnum"><span class="ov-dm" data-ov-val="txV"></span>' +
          '<span class="ov-unit" data-ov-unit="txU"></span></span></div>' +
          '</div>';
      } else {
        html += '<span class="ov-dnum"><span class="ov-dm" data-ov-val="' + c.k + 'V"></span>' +
          '<span class="ov-unit" data-ov-unit="' + c.k + 'U">' + c.unit + '</span></span>';
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
    /* 网络卡箭头（官方 i-down / i-up 图标） */
    ['down', 'up'].forEach(function (dir) {
      var slot = host.querySelector('[data-ov-arrow="' + dir + '"]');
      if (!slot) return;
      var svg = svgEl('svg', {
        viewBox: '0 0 24 24', class: 'ov-arrow-svg', 'aria-hidden': 'true'
      });
      svg.appendChild(buildIcon(ARROWS[dir], 'ov-arrow-ico'));
      slot.appendChild(svg);
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
    if (cv) {
      cv.textContent = okNum(cp) ? fmtInt(cp) : '--';
      inkTo(cv, okNum(cp) ? rampInk(cp, 'cpu') : '');
    }
    setState(host, 'cpu', lc, !okNum(cp) ? '无数据'
      : (lc === 'crit' ? '高负载' : (lc === 'warn' ? '繁忙'
        : (cp < 35 ? '平稳' : '适中'))));
    var cpuSub = [];
    if (cpu.cores) cpuSub.push(cpu.cores + ' 核');
    if (cpu.freqText) cpuSub.push(cpu.freqText);
    if (cpu.loadavg && cpu.loadavg.length) cpuSub.push('负载 ' + cpu.loadavg[0]);
    setSub(host, 'cpu', cpuSub.join(' · '));
    var cg = findGauge(host, 'cpu');
    if (cg) {
      cg.set(okNum(cp) ? cp / 100 : 0, okNum(cp) ? rampAt(cp, 'cpu') : '');
    }
    pushLive('cpu', cp);

    /* ---- 内存 ---- */
    var mem = (d && d.mem) || {};
    var mp = mem.percent;
    var lm = lvMem(mp);
    var mv = host.querySelector('[data-ov-val="memV"]');
    if (mv) {
      mv.textContent = okNum(mp) ? fmtInt(mp) : '--';
      inkTo(mv, okNum(mp) ? rampInk(mp, 'mem') : '');
    }
    setState(host, 'mem', lm, !okNum(mp) ? '无数据'
      : (lm ? LV_TXT[lm] : '充裕'));
    var memSub = [];
    if (mem.totalText) memSub.push(mem.usedText + ' / ' + mem.totalText);
    if (mem.availText) memSub.push('可用 ' + mem.availText);
    setSub(host, 'mem', memSub.join(' · '));
    var mg = findGauge(host, 'mem');
    if (mg) {
      mg.set(okNum(mp) ? mp / 100 : 0, okNum(mp) ? rampAt(mp, 'mem') : '');
    }
    pushLive('mem', mp);

    /* ---- 存储 ---- */
    var dk = diskOf(d);
    var ld = lvCap(dk.pct);
    var dv = host.querySelector('[data-ov-val="diskV"]');
    if (dv) {
      dv.textContent = okNum(dk.pct) ? fmtInt(dk.pct) : '--';
      inkTo(dv, okNum(dk.pct) ? rampInk(dk.pct, 'cap') : '');
    }
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
      /* ⚠️ 十进制容量（对齐 fnmonitor 的 F.dsize）：
         1.76 TB / 2.56 TB —— 与磁盘厂商标称一致，
         不是二进制口径的 1.6 TiB / 2.3 TiB。 */
      diskSub.push(fmtDSize(dk.used) + ' / ' + fmtDSize(dk.total));
    }
    if (dk.disks.length) diskSub.push(dk.disks.length + ' 块硬盘');
    setSub(host, 'disk', diskSub.join(' · ') || '未检测到存储');
    var dg = findGauge(host, 'disk');
    if (dg) {
      dg.set(okNum(dk.pct) ? dk.pct / 100 : 0,
        okNum(dk.pct) ? rampAt(dk.pct, 'cap') : '');
    }
    /* 盘位指示条（对齐 fnmonitor 的 drive-pips） */
    renderPips(host, dk.disks);

    /* ---- 网络 ---- */
    var net = (d && d.network) || {};
    var rxv = host.querySelector('[data-ov-val="rxV"]');
    var txv = host.querySelector('[data-ov-val="txV"]');
    var rxu = host.querySelector('[data-ov-unit="rxU"]');
    var txu = host.querySelector('[data-ov-unit="txU"]');
    var rr = fmtRate(net.rxRate), tr = fmtRate(net.txRate);
    if (rxv) rxv.textContent = net.available ? rr.v : '--';
    if (txv) txv.textContent = net.available ? tr.v : '--';
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
    if (tv) {
      tv.textContent = okNum(tp.main) ? fmtInt(tp.main) : '--';
      inkTo(tv, okNum(tp.main) ? rampInk(tp.main, 'temp') : '');
    }
    setState(host, 'temp', lt, tp.main == null ? '无传感器'
      : (lt === 'crit' ? '过热' : (lt === 'warn' ? '偏高' : '正常')));
    var tempSub = [];
    if (tp.cpu != null && tp.mb != null) tempSub.push('主板 ' + fmtInt(tp.mb) + '℃');
    if (tp.disk != null) tempSub.push('硬盘最高 ' + fmtInt(tp.disk) + '℃');
    setSub(host, 'temp', tempSub.join(' · ') || 'CPU 封装温度');
    var tg = findGauge(host, 'temp');
    if (tg) {
      tg.set(okNum(tp.main) ? (tp.main - 20) / 80 : 0,
        okNum(tp.main) ? rampAt(tp.main, 'temp') : '');
    }
    pushLive('temp', tp.cpu != null ? tp.cpu : tp.main);

    /* ---- 功耗 ---- */
    var pw = (d && d.power) || {};
    var w = pw.ok ? Number(pw.total) : null;
    var pv = host.querySelector('[data-ov-val="pwrV"]');
    if (pv) {
      pv.textContent = okNum(w) ? fmt1(w) : '--';
      /* 功耗按「占额定容量比例」分级（对齐 fnmonitor 的 w / capW * 100） */
      inkTo(pv, okNum(w) ? rampInk(pwrPct(w, pw), 'gen') : '');
    }
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
    if (pg) {
      pg.set(okNum(w) ? pwrPct(w, pw) / 100 : 0,
        okNum(w) ? rampAt(pwrPct(w, pw), 'gen') : '');
    }
    pushLive('power', w);

    /* ---- 迷你趋势线 ----
       ⚠️ v1.1.72：series 必须是 {values: [...]} 的**对象数组**。
          之前写成 [live.cpu]（裸数组）→ makeSpark.draw() 取 s.values
          得到 undefined → vals=[] → 一条线都不画。
          只有网络那张碰巧用了 {values: live.rx} 才正常 ——
          所以「只有网络卡有趋势线」。 */
    setSpark(host, 'cpu', [{ values: live.cpu }], { min: 0, max: 100 });
    setSpark(host, 'mem', [{ values: live.mem }], { min: 0, max: 100 });
    setSpark(host, 'net', [{ values: live.rx }, { values: live.tx }], { min: 0 });
    setSpark(host, 'temp', [{ values: live.temp }], {});
    setSpark(host, 'pwr', [{ values: live.power }], { min: 0 });
  }

  /* 功耗占额定容量的百分比（对齐 fnmonitor 的 w / capW * 100）
   * 额定容量来源：后端 estimation_params（tdp + base + 硬盘 + 网口），
   * 拿不到时用「当前功耗 × 1.5」兜底。 */
  function pwrPct(w, pw) {
    if (!okNum(w)) return 0;
    var ep = pw && pw.estimation_params;
    var capW = 0;
    if (ep) {
      capW = (Number(ep.tdp_w) || 0) + (Number(ep.base_w) || 0) +
        (Number(ep.disk_typical_w) || 0) * Math.max(1, Number(ep.disks) || 1) +
        (Number(ep.nic_fixed_w) || 0) * Math.max(1, Number(ep.nics) || 1);
    }
    if (!capW) capW = Math.max(60, w * 1.5);
    return clamp(w / capW * 100, 0, 100);
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
     （对齐 fnmonitor 的 drive-pips：条 + 下方设备名）
     ⚠️ 结构必须与 fnmonitor 的 .drive-pips 一致 ——
        外层 flex 容器 → 每块盘一个**列容器**（grid：条 + 名字）→
        条（.ov-pip，position:relative）+ 填充（i，absolute 贴底）。
        少一层列容器 → `inset:auto 0 0 0` 定位失效 → 条变成横铺大圆角块。 */
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
      /* 填充色按占用率分级（对齐 fnmonitor 的 rampAt(+d.percent, 'cap')）：
         占用高的盘转橙/红，其余保持品牌蓝。 */
      var col = rampAt(pct, 'cap');
      html += '<div><div class="ov-pip"><i style="height:' + h + '%;background:' +
        col + '"></i></div>' +
        '<span>' + esc(nm) + '</span></div>';
    });
    box.innerHTML = html;
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
      makeGauge: makeGauge, makeSpark: makeSpark, buildIcon: buildIcon,
      fmtBytes: fmtBytes, fmtRate: fmtRate, fmtInt: fmtInt, fmt1: fmt1,
      fmtGB: fmtGB, fmtDSize: fmtDSize, clamp: clamp, esc: esc,
      lvCPU: lvCPU, lvMem: lvMem, lvCap: lvCap, lvTemp: lvTemp,
      tempOf: tempOf, diskOf: diskOf, monoPath: monoPath,
      okNum: okNum, CARDS: CARDS, SHEET_KINDS: SHEET_KINDS,
      rampAt: rampAt, rampInk: rampInk, inkTo: inkTo,
      rampStops: rampStops, TH: TH, ICONS: ICONS, ARROWS: ARROWS,
      pwrPct: pwrPct, live: live, pushLive: pushLive
    }
  };

  window.FnStyleOverview = API;
})();
