/* ==========================================================================
   FnStyle —— 趋势图组件（LineChart）
   --------------------------------------------------------------------------
   移植自「飞牛监控 fnmonitor」（MIT License）的 LineChart 类，
   用于处理器详情抽屉的「CPU 使用率」「系统负载（1 分钟）」两张趋势图。

   组件能力（与 fnmonitor 一致）：
     · 自适应 Y 轴（niceStep 取整刻度）、左轴刻度标签、底部时间刻度
     · 单调三次样条（monoPath）平滑折线 + 首序列面积填充
     · 阈值虚线（警告 / 严重）+ 文字标注
     · 峰值标注（圆点 + 「峰值 xx」文字）
     · 实时点（呼吸光晕）
     · 悬停：竖直参考线 + 数据点 + 悬浮提示框 + 图例实时数值

   与 fnmonitor 的差异：
     · 不依赖 fnmonitor 的 Bus 事件总线（本地只有两张图，无需跨图对齐）
     · 颜色用本项目的 CSS 变量（--spark-1 / --warning / --danger）

   致谢与许可：
     本组件移植自开源项目 fnmonitor
     （https://github.com/MisiteQ/fnmonitor ，MIT License，
       Copyright (c) 2026 fnmonitor contributors）。
     MIT 许可全文见 app/ui/monitor_collect.py 顶部的许可声明。
   ========================================================================== */
(function () {
  'use strict';

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(v); }

  /* ---------------- 时间格式化（对齐 fnmonitor 的 F） ---------------- */

  function p2(n) { return String(n).padStart(2, '0'); }

  function hm(ts) {
    var d = new Date(ts * 1000);
    return p2(d.getHours()) + ':' + p2(d.getMinutes());
  }

  function hms(ts) {
    var d = new Date(ts * 1000);
    return hm(ts) + ':' + p2(d.getSeconds());
  }

  function md(ts) {
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + '/' + d.getDate();
  }

  function mdhm(ts) {
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + hm(ts);
  }

  /* ---------------- 数学辅助（对齐 fnmonitor） ---------------- */

  /* 单调三次样条：折线平滑但不会过冲（不会画出负值或超过峰值的假点） */
  function monoPath(xs, ys) {
    var n = xs.length;
    if (!n) return '';
    var f = function (v) { return Math.round(v * 10) / 10; };
    if (n === 1) return 'M' + f(xs[0]) + ',' + f(ys[0]);
    var dx = [], m = [], t = new Array(n);
    for (var i = 0; i < n - 1; i++) {
      dx[i] = xs[i + 1] - xs[i] || 1e-6;
      m[i] = (ys[i + 1] - ys[i]) / dx[i];
    }
    t[0] = m[0];
    t[n - 1] = m[n - 2];
    for (i = 1; i < n - 1; i++) {
      t[i] = m[i - 1] * m[i] <= 0 ? 0
        : 3 * (dx[i - 1] + dx[i]) /
          ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
    }
    var d = 'M' + f(xs[0]) + ',' + f(ys[0]);
    for (i = 0; i < n - 1; i++) {
      var h = dx[i] / 3;
      d += 'C' + f(xs[i] + h) + ',' + f(ys[i] + t[i] * h) +
        ' ' + f(xs[i + 1] - h) + ',' + f(ys[i + 1] - t[i + 1] * h) +
        ' ' + f(xs[i + 1]) + ',' + f(ys[i + 1]);
    }
    return d;
  }

  /* 取「好看」的刻度步长：1 / 2 / 2.5 / 5 / 10 × 10^n */
  function niceStep(raw) {
    var p = Math.pow(10, Math.floor(Math.log10(raw)));
    var n = raw / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  /* 二分查找最接近 ts 的点下标 */
  function bsearch(a, ts) {
    var lo = 0, hi = a.length - 1;
    while (hi - lo > 1) {
      var m = (lo + hi) >> 1;
      if (a[m][0] < ts) lo = m; else hi = m;
    }
    return Math.abs(a[lo][0] - ts) <= Math.abs(a[hi][0] - ts) ? lo : hi;
  }

  /* 相邻点间隔的中位数（秒） */
  function medianDt(pts) {
    if (pts.length < 2) return 60;
    var d = [];
    for (var i = 1; i < pts.length; i++) d.push(pts[i][0] - pts[i - 1][0]);
    d.sort(function (a, b) { return a - b; });
    return d[d.length >> 1] || 60;
  }

  /* 按时间断层切段：采样中断（服务重启）时不要把两段连成一条直线 */
  function segments(pts) {
    if (!pts.length) return [];
    var gap = Math.max(medianDt(pts) * 3, 150), out = [[pts[0]]];
    for (var i = 1; i < pts.length; i++) {
      if (pts[i][0] - pts[i - 1][0] > gap) out.push([]);
      out[out.length - 1].push(pts[i]);
    }
    return out;
  }

  /* 把 [值, 单位] 拼成字符串（单位以字母开头时加空格） */
  function joinU(p) {
    if (!p) return '--';
    return p[1] && /^[A-Za-z]/.test(p[1]) ? p[0] + ' ' + p[1] : p[0] + (p[1] || '');
  }

  /* ---------------- 尺寸观察（容器宽度变化时重绘） ---------------- */

  var sizeObs = (typeof ResizeObserver !== 'undefined')
    ? new ResizeObserver(function (entries) {
      entries.forEach(function (en) {
        var t = en.target;
        var w = Math.round(en.contentRect.width);
        var h = Math.round(en.contentRect.height);
        if (w !== t._w || h !== t._h) {
          t._w = w; t._h = h;
          if (t._onResize) t._onResize();
        }
      });
    })
    : null;

  function observe(el, fn) {
    el._onResize = fn;
    if (sizeObs) sizeObs.observe(el);
  }

  /* ---------------- LineChart ---------------- */

  function LineChart(host, opt) {
    this.host = host;
    this.opt = opt || {};
    this.cols = [];
    this.hoverTs = null;
    this.span = 0;

    host.classList.add('mon-chart');
    if (this.opt.height) host.style.height = this.opt.height + 'px';
    host.innerHTML =
      '<svg role="img" aria-label="' + esc(this.opt.series.map(function (s) { return s.label; }).join(' / ')) + ' 趋势">' +
      '<g class="gg"></g><g class="gs"></g><g class="gp"></g>' +
      '<line class="xh" y1="0"/><g class="gd"></g><rect class="hit"/></svg>' +
      '<div class="mon-chart-tip"></div>' +
      '<div class="mon-chart-empty" hidden></div>';

    this.svg = host.firstChild;
    this.tip = host.querySelector('.mon-chart-tip');
    this.emptyEl = host.querySelector('.mon-chart-empty');
    this.gGrid = this.svg.children[0];
    this.gSer = this.svg.children[1];
    this.gPeak = this.svg.children[2];
    this.xh = this.svg.children[3];
    this.gDots = this.svg.children[4];
    this.hit = this.svg.children[5];

    if (this.opt.legend) this.buildLegend();

    var self = this;
    var move = function (e) {
      if (!self.X) return;
      var r = self.svg.getBoundingClientRect();
      var x = clamp((e.clientX - r.left - self.L) / self.PW, 0, 1);
      var ts = self.t0 + x * (self.t1 - self.t0);
      self.showAt(ts, true);
    };
    var leave = function () { self.showAt(null, true); };

    this.hit.addEventListener('pointermove', move);
    this.hit.addEventListener('pointerdown', move);
    this.hit.addEventListener('pointerleave', leave);
    this.hit.addEventListener('pointercancel', leave);

    observe(host, function () { self.render(); });
  }

  LineChart.prototype.destroy = function () {
    /* ResizeObserver 持有元素引用，不解除观察的话，
       抽屉反复开关会累积监听器 */
    if (sizeObs && this.host) {
      try { sizeObs.unobserve(this.host); } catch (e) {}
    }
    this.host.innerHTML = '';
    this.cols = [];
    this.X = null;
  };

  LineChart.prototype.buildLegend = function () {
    var self = this;
    this.opt.legend.innerHTML = this.opt.series.map(function (s, i) {
      return '<span><span class="lk"><i class="k' + (i ? 1 : 0) + '" style="--k:' + s.color + '"></i>' +
        esc(s.label) + '</span><span class="lv">--<small></small></span></span>';
    }).join('');
    this.lv = this.opt.legend.querySelectorAll('.lv');
  };

  LineChart.prototype.setData = function (cols, span) {
    /* 过滤掉 null / NaN 的点，否则 Y 轴会被污染 */
    var clean = (cols || []).map(function (c) {
      return (c || []).filter(function (p) { return p && p[1] != null && isFinite(p[1]); });
    });
    /* 数据没变就不重绘（避免每次刷新都闪一下） */
    var sig = clean.map(function (c) {
      return c.length + ':' + (c.length
        ? c[c.length - 1][0] + ',' + c[c.length - 1][1] + ',' + c[0][0] : '');
    }).join('|') + '|' + span;
    if (sig === this._sig) return;
    this._sig = sig;
    this.cols = clean;
    this.span = span || 0;
    this.render();
  };

  LineChart.prototype.render = function () {
    var w = this.host._w || this.host.clientWidth;
    var h = this.host._h || this.host.clientHeight;
    var o = this.opt;
    var all = this.cols.flat();
    var empty = !all.length;

    this.emptyEl.hidden = !empty;
    this.svg.style.visibility = empty ? 'hidden' : '';
    if (empty) {
      this.emptyEl.textContent = this._sig === undefined
        ? '正在加载历史…'
        : ((o.empty && o.empty()) || '暂无数据');
      this.X = null;
      for (var i0 = 0; i0 < this.cols.length; i0++) this.setLegend(i0, null);
      return;
    }
    if (!w || !h) return;

    var L = this.L = o.yTick ? 40 : 8, R = 4, T = 10, B = 22;
    var PW = this.PW = w - L - R, PH = this.PH = h - T - B;
    var vals = all.map(function (p) { return p[1]; });

    var lo = o.yMin != null ? o.yMin : Math.min.apply(null, vals);
    var hi = o.yMax != null ? o.yMax : Math.max.apply(null, vals);
    if (o.yMin == null) {
      lo = Math.max(o.floor0 ? 0 : -Infinity, lo - (hi - lo) * 0.15);
    }
    var step = niceStep(((hi - lo) || Math.abs(hi) || 1) / 3);
    if (o.yMax == null) hi = Math.ceil(hi / step) * step; else step = (hi - lo) / 4;
    if (o.yMin == null) lo = Math.floor(lo / step) * step;
    if (hi <= lo) hi = lo + step;
    this.lo = lo; this.hi = hi;

    var tsAll = all.map(function (p) { return p[0]; });
    var t0 = Math.min.apply(null, tsAll), t1 = Math.max.apply(null, tsAll);
    /* 数据不足一个完整窗口时，把右端拉到当前时刻，左端对齐窗口起点 */
    if (this.span && t1 - t0 < this.span * 0.8) {
      t1 = Math.max(t1, Date.now() / 1000);
      t0 = t1 - this.span;
    }
    if (t1 - t0 < 1) t0 = t1 - 60;
    this.t0 = t0; this.t1 = t1;

    var self = this;
    var X = this.X = function (ts) { return L + (ts - t0) / (t1 - t0) * PW; };
    var Y = this.Y = function (v) {
      return T + (1 - (clamp(v, lo, hi) - lo) / (hi - lo)) * PH;
    };

    /* 横向网格线 + 左轴刻度 */
    var g = '';
    for (var v = lo; v <= hi + step * 0.01; v += step) {
      var y = Math.round(Y(v)) + 0.5;
      g += '<line class="grid-l" x1="' + L + '" x2="' + (L + PW) + '" y1="' + y + '" y2="' + y + '"/>';
      if (o.yTick) {
        g += '<text class="axis-t" x="' + (L - 8) + '" y="' + (y + 3.5) +
          '" text-anchor="end">' + esc(o.yTick(+v.toFixed(6))) + '</text>';
      }
    }

    /* 底部时间刻度 */
    var span = t1 - t0, nT = w < 420 ? 3 : 5;
    for (var i = 0; i < nT; i++) {
      var tsx = t0 + span * (i + 0.5) / nT;
      g += '<text class="axis-t" x="' + X(tsx).toFixed(1) + '" y="' + (h - 4) +
        '" text-anchor="middle">' +
        esc(span > 86400 * 1.5 ? md(tsx) : hm(tsx)) + '</text>';
    }

    /* 折线 + 面积填充 */
    var s = '', d = '', g2 = '', p0 = '';
    this.cols.forEach(function (col, ci) {
      var c = (o.series[ci] && o.series[ci].color) || 'var(--spark-1)';
      var path = '', area = '';
      segments(col).forEach(function (seg) {
        var xs = seg.map(function (p) { return X(p[0]); });
        var ys = seg.map(function (p) { return Y(p[1]); });
        var mp = seg.length === 1
          ? 'M' + xs[0].toFixed(1) + ',' + ys[0].toFixed(1) + 'h1'
          : monoPath(xs, ys);
        path += mp;
        if (ci === 0 && seg.length > 1) {
          area += mp + 'L' + xs[xs.length - 1].toFixed(1) + ',' + (T + PH) +
            'L' + xs[0].toFixed(1) + ',' + (T + PH) + 'Z';
        }
      });
      if (ci === 0) {
        p0 = path;
        if (area) s += '<path class="ar" d="' + area + '" style="fill:' + c + ';fill-opacity:.1"/>';
      }
      s += '<path class="ln' + (ci ? ' l2' : '') + '" d="' + path + '" style="stroke:' + c + '"/>';
      d += '<circle class="dot" r="3.5" style="stroke:' + c + '"/>';
    });

    /* 实时点（最新值） */
    var c0 = this.cols[0];
    if (c0 && c0.length) {
      var lp = c0[c0.length - 1], lx = X(lp[0]), ly = Y(lp[1]);
      s += '<g class="live-pt" style="color:' + (o.series[0].color || 'var(--spark-1)') + '">' +
        '<circle class="h" cx="' + lx.toFixed(1) + '" cy="' + ly.toFixed(1) + '" r="6"/>' +
        '<circle class="c" cx="' + lx.toFixed(1) + '" cy="' + ly.toFixed(1) + '" r="3"/></g>';
    }

    /* 阈值线（警告 / 严重）
       ⚠️ 两条阈值线挨得近（85% 与 95% 在 0~100 的 Y 轴上只差 10%），
          文字如果都贴着线画会叠在一起 —— 这里把「警告」画在线下方、
          「严重」画在线上方，并保证各自的文字不越过线的 y 坐标，
          两条线之间的空隙足够放下两行 9.5px 文字。 */
    var ths = (o.thresholds ? o.thresholds() : []).filter(function (t) {
      return t.v > lo && t.v < hi;
    });
    if (ths.length && p0) {
      ths.forEach(function (t) {
        var y = Y(t.v);
        var id = 'th' + (LineChart.uid = (LineChart.uid || 0) + 1);
        var col = t.lv === 'crit' ? 'var(--danger)' : 'var(--warning)';
        s += '<clipPath id="' + id + '"><rect x="' + L + '" y="' + (T - 4) +
          '" width="' + (PW + 8) + '" height="' + Math.max(0, y - T + 4).toFixed(1) +
          '"/></clipPath>' +
          '<path class="ln th-seg" d="' + p0 + '" clip-path="url(#' + id + ')" style="stroke:' + col + '"/>';
        /* 文字锚在绘图区右侧内缘；严重线在上、警告线在下，
           两者相差 10% 的 Y 高度，字号 9.5px 不会撞在一起。 */
        var label = (t.lv === 'crit' ? '严重' : '警告') + ' ' +
          (o.yTick ? o.yTick(t.v) : t.v);
        var ty = t.lv === 'crit' ? (y - 5) : (y + 11);
        /* 文字不能超出绘图区上下边界 */
        ty = Math.max(T + 9, Math.min(T + PH - 2, ty));
        g2 += '<line class="th-l" x1="' + L + '" x2="' + (L + PW) + '" y1="' + y.toFixed(1) +
          '" y2="' + y.toFixed(1) + '" style="stroke:' + col + '"/>' +
          '<text class="th-t" x="' + (L + PW - 2) + '" y="' + ty.toFixed(1) +
          '" text-anchor="end" style="fill:' + col + '">' + esc(label) + '</text>';
      });
    }

    this.gGrid.innerHTML = g + g2;
    this.gSer.innerHTML = s;
    this.gDots.innerHTML = d;

    /* 峰值标注
       ⚠️ 阈值文字锚在绘图区**右端**（x = L+PW），峰值标注若也落在右端
          就会与「严重 95%」叠字。这里把峰值标注的锚点限制在左 65% 区域内，
          超出时改为左对齐 —— 保证两者永远不撞。 */
    var pk = '';
    if (o.peak && c0 && c0.length > 2) {
      var mi = 0;
      c0.forEach(function (p, pi) { if (p[1] > c0[mi][1]) mi = pi; });
      var px = X(c0[mi][0]), py = Y(c0[mi][1]);
      var lab = '峰值 ' + joinU(o.series[0].fmt(c0[mi][1]));
      var safeRight = L + PW - 78;   // 右侧留出阈值文字的位置
      var anchor = 'middle';
      var lx = px;
      if (px > safeRight) { anchor = 'end'; lx = safeRight; }
      else if (px < L + 40) { anchor = 'start'; lx = L + 2; }
      pk = '<circle cx="' + px.toFixed(1) + '" cy="' + py.toFixed(1) + '" r="3" style="fill:' +
        (o.series[0].color || 'var(--spark-1)') + ';stroke:var(--bg-card);stroke-width:2"/>' +
        '<text class="peak-t" x="' + lx.toFixed(1) + '" y="' + Math.max(10, py - 9).toFixed(1) +
        '" text-anchor="' + anchor + '">' + esc(lab) + '</text>';
    }
    this.gPeak.innerHTML = pk;

    this.xh.setAttribute('y1', T);
    this.xh.setAttribute('y2', T + PH);
    this.hit.setAttribute('x', L);
    this.hit.setAttribute('y', 0);
    this.hit.setAttribute('width', Math.max(0, PW));
    this.hit.setAttribute('height', h);

    this.showAt(this.hoverTs, this._src);
  };

  /* 取某条序列在 ts 附近的值（超出容忍范围返回 null） */
  LineChart.prototype.valAt = function (col, ts) {
    if (!col.length) return null;
    var i = bsearch(col, ts), p = col[i];
    return Math.abs(p[0] - ts) <= Math.max(medianDt(col) * 2, 90) ? p : null;
  };

  LineChart.prototype.showAt = function (ts, isSrc) {
    this.hoverTs = ts;
    this._src = isSrc;
    var o = this.opt;

    if (ts == null || !this.X || ts < this.t0 - 1 || ts > this.t1 + 1) {
      this.host.classList.remove('hovering');
      this.tip.classList.remove('show');
      for (var i = 0; i < this.cols.length; i++) {
        this.setLegend(i, this.cols[i].length ? this.cols[i][this.cols[i].length - 1][1] : null);
      }
      return;
    }

    var ref = null;
    for (var k = 0; k < this.cols.length; k++) {
      if (this.cols[k].length) { ref = this.cols[k]; break; }
    }
    if (!ref) return;

    var rp = ref[bsearch(ref, ts)], x = this.X(rp[0]);
    this.host.classList.add('hovering');
    this.xh.setAttribute('x1', x);
    this.xh.setAttribute('x2', x);

    var dots = this.gDots.children;
    var vals = this.cols.map(function (c) { return this.valAt(c, rp[0]); }, this);

    vals.forEach(function (p, ki) {
      if (dots[ki]) {
        if (p) {
          dots[ki].setAttribute('cx', this.X(p[0]));
          dots[ki].setAttribute('cy', this.Y(p[1]));
          dots[ki].style.display = '';
        } else {
          dots[ki].style.display = 'none';
        }
      }
      this.setLegend(ki, p ? p[1] : null);
    }, this);

    if (isSrc) {
      var span = this.t1 - this.t0;
      var html = '<div class="tt">' +
        esc(span > 86400 * 1.5 ? mdhm(rp[0]) : hms(rp[0])) + '</div>';
      vals.forEach(function (p, ki) {
        html += '<div class="tr"><span><i class="k' + (ki ? 1 : 0) + '" style="--k:' +
          o.series[ki].color + '"></i>' + esc(o.series[ki].label) +
          '</span><b>' + esc(p ? joinU(o.series[ki].fmt(p[1])) : '--') + '</b></div>';
      });
      this.tip.innerHTML = html;
      var w = this.host._w || this.host.clientWidth;
      var tw = this.tip.offsetWidth || 140;
      this.tip.style.left = (x + 14 + tw > w ? x - 14 - tw : x + 14) + 'px';
      this.tip.style.top = '4px';
      this.tip.classList.add('show');
    } else {
      this.tip.classList.remove('show');
    }
  };

  LineChart.prototype.setLegend = function (i, v) {
    if (!this.lv || !this.lv[i]) return;
    var p = v == null ? ['--', ''] : this.opt.series[i].fmt(v);
    var el = this.lv[i];
    var k = p.join('|');
    if (el._k === k) return;
    el._k = k;
    el.innerHTML = esc(p[0]) + '<small>' + esc(p[1] || '') + '</small>';
  };

  /* ---------------- 范围切换段控件 ---------------- */

  var RANGES = [
    ['10m', '10分钟'], ['1h', '1小时'], ['6h', '6小时'],
    ['24h', '24小时'], ['7d', '7天'], ['all', '全部']
  ];

  var RANGE_SEC = {
    '10m': 600, '1h': 3600, '6h': 21600,
    '24h': 86400, '7d': 604800, all: 0
  };

  /* 构造一个范围切换控件（fnmonitor 的 makeSeg 同款交互：
     滑块指示 + 左右方向键切换） */
  function makeRangeSeg(value, onChange) {
    var el = document.createElement('div');
    el.className = 'mon-seg';
    el.setAttribute('role', 'radiogroup');
    el.innerHTML = '<span class="thumb"></span>' + RANGES.map(function (r) {
      return '<button type="button" role="radio" data-v="' + esc(r[0]) +
        '" aria-checked="false">' + esc(r[1]) + '</button>';
    }).join('');

    var thumb = el.querySelector('.thumb');
    function place() {
      var b = el.querySelector('[aria-checked="true"]');
      if (!b || !b.offsetWidth) return;
      thumb.style.width = b.offsetWidth + 'px';
      thumb.style.transform = 'translateX(' + b.offsetLeft + 'px)';
    }
    el.set = function (v) {
      el.value = v;
      var bs = el.querySelectorAll('button');
      for (var i = 0; i < bs.length; i++) {
        bs[i].setAttribute('aria-checked', String(bs[i].getAttribute('data-v') === String(v)));
      }
      place();
    };
    el.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button') : null;
      if (!b) return;
      var opt = null;
      for (var i = 0; i < RANGES.length; i++) {
        if (String(RANGES[i][0]) === b.getAttribute('data-v')) opt = RANGES[i];
      }
      if (!opt) return;
      if (String(el.value) === String(opt[0])) return;
      el.set(opt[0]);
      if (onChange) onChange(opt[0]);
    });
    el.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      var idx = 0;
      for (var i = 0; i < RANGES.length; i++) {
        if (String(RANGES[i][0]) === String(el.value)) idx = i;
      }
      var ni = clamp(idx + (e.key === 'ArrowRight' ? 1 : -1), 0, RANGES.length - 1);
      if (String(RANGES[ni][0]) !== String(el.value)) {
        el.set(RANGES[ni][0]);
        if (onChange) onChange(RANGES[ni][0]);
        var cur = el.querySelector('[aria-checked="true"]');
        if (cur) cur.focus();
      }
      e.preventDefault();
    });
    observe(el, place);
    el.set(value);
    return el;
  }

  window.FnStyleChart = {
    LineChart: LineChart,
    makeRangeSeg: makeRangeSeg,
    RANGES: RANGES,
    RANGE_SEC: RANGE_SEC,
    esc: esc
  };
})();
