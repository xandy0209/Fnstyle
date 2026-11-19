/* ==========================================================================
   FnStyle —— 处理器 / 内存 / 显卡 卡片与详情抽屉
   --------------------------------------------------------------------------
   按「飞牛监控 fnmonitor」（MIT License）的处理器·内存·显卡板块 1:1 还原。

   三张卡片：
     ① 每核使用率 —— 每个核心一个 5×5 点阵瓦片（25 点 = 0~100%）
     ② 内存构成   —— DIMM 内存条 SVG（PCB + 8 芯片位 + 金手指 + 斜纹缓存区）
     ③ 显卡       —— 利用率大数字 + 引擎明细条（渲染/视频/视频增强/复制/计算）

   详情抽屉：处理器（每核点阵 + 进程 + 规格 + 显卡）/ 内存（构成 + 进程 + 交换分区）

   致谢与许可：
     本模块的视觉与实现方式参考了开源项目 fnmonitor
     （https://github.com/MisiteQ/fnmonitor ，MIT License，
       Copyright (c) 2026 fnmonitor contributors）。
     MIT 许可全文见 app/ui/monitor_collect.py 顶部的许可声明。
   ========================================================================== */
(function () {
  'use strict';

  function $(sel) { return document.querySelector(sel); }

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ---------------------------------------------------------------------
     格式化
     --------------------------------------------------------------------- */
  function isNum(v) { return v !== null && v !== undefined && v !== '' && isFinite(v); }

  function fmtInt(v) {
    if (!isNum(v)) return '—';
    return String(Math.round(v));
  }

  function fmt1(v) {
    if (!isNum(v)) return '—';
    return (Math.round(v * 10) / 10).toFixed(1);
  }

  function fmt2(v) {
    if (!isNum(v)) return '—';
    return (Math.round(v * 100) / 100).toFixed(2);
  }

  /* kWh（fnmonitor 口径：>=10 两位小数，>=0.1 三位小数，更小四位） */
  function fmtKwh(v) {
    if (!isNum(v)) return '—';
    return v >= 10 ? v.toFixed(2) : v >= 0.1 ? v.toFixed(3) : v.toFixed(4);
  }

  var GiB = 1024 * 1024 * 1024;

  /* GB（十进制取整，fnmonitor 口径：>=10 取整，否则一位小数） */
  function gb(v) {
    if (!isNum(v)) return '—';
    var g = v / GiB;
    return g >= 10 ? String(Math.round(g)) : g.toFixed(1);
  }

  function fmtBytes(n) {
    if (!isNum(n)) return '—';
    var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    var i = 0;
    var v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)) + ' ' + units[i];
  }

  /* ---------------------------------------------------------------------
     色阶（对齐 fnmonitor 的 rampAt / rampInk）
     —— 用 CSS 变量做插值，避免在 JS 里硬编码色值。
     --------------------------------------------------------------------- */
  function rampAt(v, kind) {
    var pct = Math.max(0, Math.min(100, Number(v) || 0));
    var stops = kind === 'mem' ? [70, 90] : kind === 'cpu' ? [70, 90] : [70, 90];
    var warn = stops[0];
    var crit = stops[1];
    if (pct <= warn * 0.5) return 'var(--success)';
    if (pct <= warn) return 'color-mix(in srgb, var(--success) 55%, var(--warning))';
    if (pct <= (warn + crit) / 2) return 'var(--warning)';
    if (pct <= crit) return 'color-mix(in srgb, var(--warning) 60%, var(--danger))';
    return 'var(--danger)';
  }

  function rampInk(v, kind) {
    var pct = Math.max(0, Math.min(100, Number(v) || 0));
    if (pct >= 90) return 'var(--danger)';
    if (pct >= 70) return 'var(--warning)';
    if (pct >= 35) return 'color-mix(in srgb, var(--success) 55%, var(--warning))';
    return 'var(--success)';
  }

  function inkTo(el, col) {
    if (el && el._ink !== col) { el._ink = col; el.style.color = col; }
  }

  /* ---------------------------------------------------------------------
     核心点阵：5×5 = 25 点，填满的点数 = round(占用率 / 100 × 25)
     （fnmonitor 的 CORE_DOTS：viewBox 0 0 5 5，25 个 r=.36 的圆）
     --------------------------------------------------------------------- */
  var CORE_DOTS = (function () {
    var h = '';
    for (var y = 0; y < 5; y++) {
      for (var x = 0; x < 5; x++) {
        h += '<circle cx="' + (x + 0.5) + '" cy="' + (y + 0.5) + '" r=".36"/>';
      }
    }
    return h;
  })();

  /* 点阵填充顺序：从上往下逐行填（与 fnmonitor 的 order 一致） */
  function coreOrder(svg) {
    var cs = svg.querySelectorAll('circle');
    var order = [];
    for (var y = 4; y >= 0; y--) {
      for (var c = 0; c < 5; c++) order.push(cs[y * 5 + c]);
    }
    return order;
  }

  function coreTilesHtml(n) {
    var h = '';
    for (var i = 0; i < n; i++) {
      h += '<div class="mon-hw-core" data-core="' + i + '">' +
        '<svg viewBox="0 0 5 5" aria-hidden="true">' + CORE_DOTS + '</svg>' +
        '<div class="mon-hw-core-f"><span>C' + i + '</span><b data-cv="' + i + '">--</b></div>' +
        '</div>';
    }
    return h;
  }

  function setCores(host, pc) {
    var tiles = host.querySelectorAll('.mon-hw-core');
    for (var i = 0; i < tiles.length; i++) {
      var v = pc[i];
      if (!isNum(v)) continue;
      var t = tiles[i];
      var svg = t.querySelector('svg');
      var order = coreOrder(svg);
      var k = Math.round(Math.max(0, Math.min(100, v)) / 100 * 25);
      for (var j = 0; j < order.length; j++) {
        var on = j < k;
        var col = on ? rampAt((Math.floor(j / 5) + 1) * 20, 'cpu') : '';
        var c = order[j];
        if (c._on !== on) { c._on = on; c.setAttribute('class', on ? 'on' : ''); }
        if (c._col !== col) { c._col = col; c.style.fill = col; }
      }
      var b = t.querySelector('[data-cv="' + i + '"]');
      if (b) {
        b.textContent = fmtInt(v);
        inkTo(b, rampInk(v, 'cpu'));
      }
    }
  }

  /* ---------------------------------------------------------------------
     DIMM 内存条 SVG
     —— PCB 轮廓 + 8 个芯片位 + 金手指 + 斜纹缓存区（fnmonitor 同款）
     --------------------------------------------------------------------- */
  var DIMM = { x0: 12, p: 47.5, w: 42, y: 12, h: 48 };

  function dimmX(f) {
    f = Math.max(0, Math.min(1, f));
    var k = Math.min(7, Math.floor(f * 8));
    return DIMM.x0 + k * DIMM.p + (f * 8 - k) * DIMM.w;
  }

  function dimmSvgHtml() {
    var h = '<svg class="mon-hw-dimm" viewBox="0 0 400 100" role="img" ' +
      'aria-label="内存条占用示意">' +
      '<defs>' +
      '<pattern id="monDimmHatch" width="3.2" height="3.2" patternUnits="userSpaceOnUse" ' +
      'patternTransform="rotate(45)"><rect width="1.3" height="3.2" ' +
      'style="fill:var(--text-muted)"/></pattern>' +
      '<clipPath id="monDimmChips">';
    for (var i = 0; i < 8; i++) {
      h += '<rect x="' + (DIMM.x0 + i * DIMM.p) + '" y="' + DIMM.y +
        '" width="' + DIMM.w + '" height="' + DIMM.h + '" rx="3"/>';
    }
    h += '</clipPath></defs>' +
      '<path class="pcb" d="M7 0H393A7 7 0 0 1 400 7V100H241V92H229V100H0V7A7 7 0 0 1 7 0Z"/>';
    for (var j = 0; j < 8; j++) {
      h += '<rect class="chip-bg" x="' + (DIMM.x0 + j * DIMM.p) + '" y="' + DIMM.y +
        '" width="' + DIMM.w + '" height="' + DIMM.h + '" rx="3"/>';
    }
    h += '<g clip-path="url(#monDimmChips)">' +
      '<rect class="seg seg-app" y="' + DIMM.y + '" height="' + DIMM.h +
      '" x="' + DIMM.x0 + '" width="0"/>' +
      '<rect class="seg seg-cache" y="' + DIMM.y + '" height="' + DIMM.h +
      '" x="' + DIMM.x0 + '" width="0"/>' +
      '<rect class="seg seg-buf" y="' + DIMM.y + '" height="' + DIMM.h +
      '" x="' + DIMM.x0 + '" width="0"/>' +
      '</g>';
    h += '<text class="dimm-txt" x="' + DIMM.x0 + '" y="74" data-dimm-t></text>' +
      '<rect x="362" y="66" width="22" height="9" rx="1.5" class="chip-bg"/>';
    for (var x = 8; x < 394; x += 5) {
      if (x < 225 || x > 242) {
        h += '<rect class="finger" x="' + x + '" y="82" width="3" height="14" rx=".8"/>';
      }
    }
    h += '</svg>';
    return h;
  }

  /* 内存构成四份（对应 fnmonitor 的 memParts） */
  function memParts(m) {
    var T = Number(m && m.total) || 0;
    var app = Number(m && m.used) || 0;
    var cache = Number(m && m.cached) || 0;
    var buf = Number(m && m.buffers) || 0;
    return { app: app, cache: cache, buf: buf, free: Math.max(0, T - app - cache - buf) };
  }

  /* 内存插槽列表（后端 dmidecode 采集，对应 fnmonitor 的 memItems） */
  function memSlots(d) {
    var m = (d && d.memory) || {};
    var it = m.items || [];
    if (!Array.isArray(it)) return [];
    return it;
  }

  function memSlotsPresent(d) {
    return memSlots(d).filter(function (i) { return i && i.present; });
  }

  /* DIMM 图形旁的标签（fnmonitor 的 dimmLabel）：
     有插槽信息 → 「DDR4-3200 · 8 GB · SAMSUNG M378A1K43 · × 2」
     没有       → 「8 GB 物理内存 · 未读取到插槽信息」 */
  function dimmLabelText(d) {
    var it = memSlotsPresent(d);
    var mem = (d && d.mem) || {};
    if (!it.length) {
      var T = Number(mem.total) || 0;
      if (T) return gb(T) + ' GB 物理内存 · 未读取到插槽信息';
      return '未读取到插槽信息';
    }
    var a = it[0] || {};
    var parts = [];
    if (a.type || a.speed) {
      parts.push([a.type, a.speed].filter(Boolean).join('-'));
    }
    if (a.size) parts.push(a.size);
    var sub = [a.manufacturer, a.part].filter(Boolean).join(' ');
    if (sub) parts.push(sub);
    if (it.length > 1) parts.push('× ' + it.length);
    return parts.join(' · ').toUpperCase();
  }

  function memLegendHtml(mp, firstColor) {
    var items = [
      ['应用', mp.app, firstColor],
      ['缓存', mp.cache, 'hatch'],
      ['缓冲', mp.buf, 'var(--text-muted)'],
      ['空闲', mp.free, 'var(--bg-hover)']
    ];
    return items.map(function (it, i) {
      var color = it[2];
      var sw = color === 'hatch'
        ? '<i class="hatch"></i>'
        : '<i style="background:' + (i === 0 && firstColor ? firstColor : color) + '"></i>';
      return '<span>' + sw + it[0] + ' <b>' + gb(it[1]) + ' GB</b></span>';
    }).join('');
  }

  /* ---------------------------------------------------------------------
     显卡
     --------------------------------------------------------------------- */
  var GPU_TAG = { igpu: '核显', nvidia: 'NVIDIA', amd: 'AMD' };
  var GPU_ENG = [
    ['render', '渲染'],
    ['video', '视频'],
    ['video_enhance', '视频增强'],
    ['blitter', '复制'],
    ['compute', '计算']
  ];

  function gpuIntel(g) { return g && g.vendor === 'Intel'; }

  function gpuEngines(g) {
    if (!g || !g.engines) return [];
    return GPU_ENG.filter(function (e) { return isNum(g.engines[e[0]]); });
  }

  /* 占用率来源判定（与 fnmonitor 的 gpuMode 一致）
     pmu  → PMU 引擎实测（最准）
     rc6  → RC6 驻留估算（整体，不分引擎）
     warm → 正在采样
     none → 读不到
     drv  → 驱动直接给的利用率 */
  function gpuMode(g) {
    if (!g) return 'none';
    if (!gpuIntel(g)) return 'drv';
    if (g.warming) return 'warm';
    if (g.utilSource === 'pmu' && gpuEngines(g).length) return 'pmu';
    if (g.utilSource === 'rc6' && isNum(g.util)) return 'rc6';
    return 'none';
  }

  var GPU_NOTE = {
    pmu: 'i915 PMU 实测（与 intel_gpu_top 同源），约 10 秒均值。',
    rc6: '按 GPU 省电态（RC6）驻留时间估算整体活跃度，不区分引擎；读取各引擎占用需要 i915 PMU。',
    warm: '正在采样，约 10 秒后显示。',
    none: '未能读取核显占用（i915 PMU 与 RC6 均不可用）。'
  };

  /* ---------------------------------------------------------------------
     渲染三张卡片
     --------------------------------------------------------------------- */
  function renderCoreCards(d, host) {
    if (!host) return;
    var cpu = d.cpu || {}, mem = d.mem || {}, gpu = d.gpu || {};
    var gpus = gpu.gpus || [];
    var g = gpus[0] || null;
    var html = '';

    /* ---------- ① 每核使用率 ---------- */
    var pc = cpu.coresPercent || [];
    var cpuHtml = '';
    if (cpu.available) {
      var model = cpu.model || '未知型号';
      var freq = isNum(cpu.freqMhz) && cpu.freqMhz > 0
        ? (cpu.freqMhz / 1000).toFixed(2) + ' GHz'
        : (cpu.freqText || '--');
      var la = (cpu.loadavg && cpu.loadavg.length)
        ? cpu.loadavg.map(function (v) { return fmt2(parseFloat(v)); }).join(' / ')
        : '--';

      cpuHtml = '<div class="mon-cmp-label"><span class="lbl-t">每核使用率</span>' +
        '<span class="faint">' + esc(model) + '</span></div>' +
        '<div class="mon-cmp-split">' +
        '<div><span class="mon-cmp-eyebrow">整体</span>' +
        '<span class="mon-cmp-big" style="margin-top:0"><span class="dm" data-cpu-avg>' +
        fmtInt(cpu.percent) + '</span><span class="unit">%</span></span></div>' +
        '<div><span class="mon-cmp-eyebrow">频率</span><b>' + esc(freq) + '</b></div>' +
        '<div><span class="mon-cmp-eyebrow">负载 1 / 5 / 15 分钟</span><b>' + esc(la) + '</b></div>' +
        '</div>';
      if (pc.length) {
        cpuHtml += '<div class="mon-hw-cores" data-cores style="--cols:' +
          (pc.length <= 4 ? pc.length : pc.length <= 8 ? 4 : pc.length <= 18 ? 6 : 8) + '">' +
          coreTilesHtml(pc.length) + '</div>';
      } else {
        cpuHtml += '<div class="mon-cmp-note">每核数据不可用</div>';
      }
    } else {
      cpuHtml = '<div class="mon-cmp-label"><span class="lbl-t">每核使用率</span></div>' +
        '<div class="mon-cmp-note">无法读取 /proc/stat</div>';
    }
    html += '<div class="mon-cmp-card" data-cmp-open="cpu" tabindex="0" role="button" ' +
      'aria-label="查看处理器详情">' + cpuHtml + '</div>';

    /* ---------- ② 内存构成 ---------- */
    var memHtml = '';
    if (mem.available) {
      var mp = memParts(mem);
      var T = Number(mem.total) || 0;
      memHtml = '<div class="mon-cmp-label"><span class="lbl-t">内存构成</span></div>' +
        '<div class="mon-cmp-big"><span class="dm" data-mem-used>' +
        (T ? gb(mp.app) : '—') + '</span>' +
        '<span class="unit">' + (T ? '/ ' + gb(T) + ' GB 已用' : '') + '</span></div>' +
        dimmSvgHtml() +
        '<div class="mon-legend" data-mem-leg></div>' +
        '<hr class="mon-hair">' +
        '<div class="mon-cmp-label"><span class="lbl-t">交换分区</span>' +
        '<span class="faint tnum" data-mem-swap>' +
        (mem.swapTotal ? gb(mem.swapUsed) + ' / ' + gb(mem.swapTotal) + ' GB' : '未启用') +
        '</span></div>' +
        '<div class="mon-engine" style="grid-template-columns:1fr;margin-top:.4rem">' +
        '<div class="bar"><i data-mem-swap-bar style="width:0"></i></div></div>';
    } else {
      memHtml = '<div class="mon-cmp-label"><span class="lbl-t">内存构成</span></div>' +
        '<div class="mon-cmp-note">无法读取 /proc/meminfo</div>';
    }
    html += '<div class="mon-cmp-card" data-cmp-open="mem" tabindex="0" role="button" ' +
      'aria-label="查看内存详情">' + memHtml + '</div>';

    /* ---------- ③ 显卡 ----------
       fnmonitor 的 compute 板块里，显卡卡片不可点击（GPU 详情放在
       「处理器」详情抽屉底部的「显卡」段落），这里保持一致。 */
    html += '<div class="mon-cmp-card mon-cmp-card-static">' + gpuCardHtml(g, gpus) + '</div>';

    host.innerHTML = html;

    /* 渲染后填充动态部分 */
    if (cpu.available && pc.length) {
      var cores = host.querySelector('[data-cores]');
      if (cores) setCores(cores, pc);
      var avg = host.querySelector('[data-cpu-avg]');
      if (avg) inkTo(avg, rampInk(cpu.percent, 'cpu'));
    }
    if (mem.available) {
      var mp2 = memParts(mem);
      var T2 = Number(mem.total) || 0;
      if (T2) {
        var a = mp2.app / T2, c = mp2.cache / T2, b = mp2.buf / T2;
        var segApp = host.querySelector('.seg-app');
        var segCache = host.querySelector('.seg-cache');
        var segBuf = host.querySelector('.seg-buf');
        if (segApp) {
          var x0 = dimmX(0), x1 = dimmX(a);
          segApp.setAttribute('x', x0.toFixed(2));
          segApp.setAttribute('width', Math.max(0, x1 - x0).toFixed(2));
          segApp.style.fill = rampAt(mem.percent, 'mem');
        }
        if (segCache) {
          var x2 = dimmX(a), x3 = dimmX(a + c);
          segCache.setAttribute('x', x2.toFixed(2));
          segCache.setAttribute('width', Math.max(0, x3 - x2).toFixed(2));
        }
        if (segBuf) {
          var x4 = dimmX(a + c), x5 = dimmX(a + c + b);
          segBuf.setAttribute('x', x4.toFixed(2));
          segBuf.setAttribute('width', Math.max(0, x5 - x4).toFixed(2));
        }
        var leg = host.querySelector('[data-mem-leg]');
        if (leg) leg.innerHTML = memLegendHtml(mp2, rampAt(mem.percent, 'mem'));
        var txt = host.querySelector('[data-dimm-t]');
        if (txt) txt.textContent = dimmLabelText(d);
      }
      var usedEl = host.querySelector('[data-mem-used]');
      if (usedEl) inkTo(usedEl, rampInk(mem.percent, 'mem'));
      if (mem.swapTotal) {
        var bar = host.querySelector('[data-mem-swap-bar]');
        if (bar) bar.style.width =
          (Math.max(0, Math.min(100, mem.swapUsed * 100 / mem.swapTotal))).toFixed(1) + '%';
      }
    }
    /* 显卡引擎条填充
       注意：data-eng 挂在**行容器**上（.mon-engine），填充条与数值是它的子元素。
       若用 [data-eng] 直接选，会选到填充条 <i> 本身 —— 取不到 .v 数值，
       且 <i> 没有初始宽度会让进度条一直满格。 */
    if (g) {
      var mode = gpuMode(g);
      var rows = host.querySelectorAll('.mon-engine[data-eng]');
      for (var bi = 0; bi < rows.length; bi++) {
        var key = rows[bi].getAttribute('data-eng');
        var v;
        if (mode === 'pmu') {
          v = (g.engines && g.engines[key]);
        } else if (key === 'mem') {
          v = g.memPct;
        } else {
          v = g.util;
        }
        var fill = rows[bi].querySelector('.bar > i');
        var val = rows[bi].querySelector('.v');
        if (isNum(v)) {
          if (fill) fill.style.width = Math.max(0, Math.min(100, v)).toFixed(1) + '%';
          if (val) val.textContent = fmtInt(v) + '%';
        } else {
          if (fill) fill.style.width = '0%';
          if (val) val.textContent = '--';
        }
      }
      var gu = host.querySelector('[data-gpu-util]');
      if (gu) {
        var has = g.utilAvailable && isNum(g.util);
        gu.textContent = has ? fmt1(g.util) : '--';
        inkTo(gu, has ? rampInk(g.util, 'gen') : '');
      }
    }
  }

  function gpuCardHtml(g, gpus) {
    if (!g) {
      return '<div class="mon-cmp-label"><span class="lbl-t">显卡</span></div>' +
        '<div class="mon-cmp-note">未检测到可监控的显卡。' +
        '支持 Intel 核显 / NVIDIA（nvidia-smi）/ AMD（amdgpu）。</div>';
    }
    var mode = gpuMode(g);
    var engs = gpuEngines(g);
    var rows = '';
    if (mode === 'pmu') {
      rows = engs.map(function (e) {
        return '<div class="mon-engine" data-eng="' + e[0] + '"><span>' + e[1] + '</span>' +
          '<div class="bar"><i style="width:0"></i></div>' +
          '<span class="v">--</span></div>';
      }).join('');
    } else if (mode === 'rc6') {
      rows = '<div class="mon-engine wide" data-eng="util"><span>活跃度（RC6 估算）</span>' +
        '<div class="bar"><i style="width:0"></i></div>' +
        '<span class="v">--</span></div>';
    } else if (mode === 'drv') {
      rows = '<div class="mon-engine" data-eng="util"><span>利用率</span>' +
        '<div class="bar"><i style="width:0"></i></div>' +
        '<span class="v">--</span></div>';
      if (Number(g.memTotal) > 0) {
        rows += '<div class="mon-engine" data-eng="mem"><span>显存</span>' +
          '<div class="bar"><i style="width:0"></i></div>' +
          '<span class="v">--</span></div>';
      }
    }

    var name = String(g.name || '').replace(/ \(.*\)/, '');
    var sub = [];
    if (isNum(g.freqMhz)) sub.push(fmtInt(g.freqMhz) + ' MHz');
    if (isNum(g.temp)) sub.push(fmtInt(g.temp) + '℃');

    var note = mode === 'drv'
      ? (!g.utilAvailable ? '该显卡驱动未开放利用率读数。'
        : (Number(g.memTotal) > 0
          ? '显存 ' + fmtBytes(g.memUsed) + ' / ' + fmtBytes(g.memTotal)
          : (gpus.length > 1 ? '另有 ' + (gpus.length - 1) + ' 块显卡，详见处理器详情。' : '')))
      : (GPU_NOTE[mode] || '');

    return '<div class="mon-cmp-label"><span class="lbl-t">' +
      esc(GPU_TAG[g.type] || '显卡') + '</span>' +
      '<span class="faint">' + esc(name) + '</span></div>' +
      '<div class="mon-cmp-big"><span class="dm" data-gpu-util>--</span>' +
      '<span class="unit">%</span>' +
      (sub.length ? '<span class="sub">' + esc(sub.join(' · ')) + '</span>' : '') +
      '</div>' +
      (rows ? '<div style="margin-top:.95rem">' + rows + '</div>' : '') +
      (note ? '<p class="mon-cmp-note" style="margin-top:.85rem">' + esc(note) + '</p>' : '');
  }

  /* ---------------------------------------------------------------------
     详情抽屉
     --------------------------------------------------------------------- */
  function secHtml(title, extra) {
    return '<div class="mon-cmp-sec"><span>' + esc(title) + '</span>' +
      (extra ? '<span class="faint">' + esc(extra) + '</span>' : '') + '</div>';
  }

  /* ---------------------------------------------------------------------
     处理器抽屉的趋势图（1:1 对齐 fnmonitor 的 sheetCharts）
     ---------------------------------------------------------------------
     fnmonitor 在处理器抽屉里放两张图：
       ① 「CPU 使用率」      0~100%，带警告/严重阈值虚线 + 峰值标注
       ② 「系统负载（1 分钟）」起点 0，无阈值
     两张图共用一个范围切换控件（10分钟/1小时/6小时/24小时/7天/全部），
     切换后一次性拉取两个指标的历史序列。

     数据来自 GET api/monitor/history?metrics=cpu,load1&range=xxx
     —— 后台每 60 秒写一条，首次打开时通常还没有历史，
        后端会附加当前实时值兜底，曲线不会空着。
     --------------------------------------------------------------------- */

  /* 阈值：与 fnmonitor 的 TH_SPEC 默认值一致（cpuWarn 85 / cpuCrit 95） */
  var CPU_WARN = 85;
  var CPU_CRIT = 95;

  /* 图表定义（与 fnmonitor 的 TREND_DEFS[0] 及 SHEETS.cpu 对应） */
  var CPU_CHARTS = [
    {
      key: 'cpu',
      title: 'CPU 使用率',
      yMin: 0, yMax: 100,
      yTick: function (v) { return v + '%'; },
      peak: true,
      thresholds: function () {
        return [{ v: CPU_WARN, lv: 'warn' }, { v: CPU_CRIT, lv: 'crit' }];
      },
      series: [{
        m: 'cpu', label: 'CPU', color: 'var(--spark-1)',
        fmt: function (v) { return [fmtInt(v), '%']; }
      }]
    },
    {
      key: 'load1',
      title: '系统负载（1 分钟）',
      yMin: 0,
      yTick: function (v) { return String(v); },
      series: [{
        m: 'load1', label: '负载', color: 'var(--spark-1)',
        fmt: function (v) { return [fmt2(v), '']; }
      }]
    }
  ];

  /* 内存抽屉的趋势图（1:1 对齐 fnmonitor 的 SHEETS.mem）
     fnmonitor 的内存抽屉有一张「内存使用率」图，指标名就是 mem。 */
  var MEM_CHARTS = [
    {
      key: 'mem',
      title: '内存使用率',
      yMin: 0, yMax: 100,
      yTick: function (v) { return v + '%'; },
      series: [{
        m: 'mem', label: '内存', color: 'var(--spark-2)',
        fmt: function (v) { return [fmtInt(v), '%']; }
      }]
    }
  ];

  /* 温度和散热板块的趋势图（1:1 对齐 fnmonitor 的 SHEETS.temp）
     fnmonitor 的温度抽屉有两张图：
       ① 「CPU 温度」    带警告/严重阈值虚线 + 峰值标注
       ② 「主板温度」    无阈值
     指标名分别是 temp 与 temp_mb。 */
  var TEMP_CHARTS = [
    {
      key: 'temp',
      title: 'CPU 温度',
      yMin: 0,
      yTick: function (v) { return v + '°'; },
      peak: true,
      thresholds: function () {
        return [{ v: 60, lv: 'warn' }, { v: 75, lv: 'crit' }];
      },
      series: [{
        m: 'temp', label: 'CPU', color: 'var(--spark-1)',
        fmt: function (v) { return [fmt1(v), '℃']; }
      }]
    },
    {
      key: 'temp_mb',
      title: '主板温度',
      yMin: 0,
      yTick: function (v) { return v + '°'; },
      series: [{
        m: 'temp_mb', label: '主板', color: 'var(--spark-2)',
        fmt: function (v) { return [fmt1(v), '℃']; }
      }]
    }
  ];

  /* 能耗板块的趋势图（1:1 对齐 fnmonitor 的 SHEETS.power）
     fnmonitor 的能耗抽屉有两张图：
       ① 整机功耗（TREND_DEFS[4]，指标 power，单位 W）
       ② 累计用电（所选范围）（指标同样是 power，前端梯形积分换算成 kWh） */
  var POWER_CHARTS = [
    {
      key: 'power',
      title: '整机功耗',
      yMin: 0,
      yTick: function (v) { return v + 'W'; },
      peak: true,
      series: [{
        m: 'power', label: '功耗', color: 'var(--spark-2)',
        fmt: function (v) { return [fmt1(v), 'W']; }
      }]
    },
    {
      key: 'power',
      title: '累计用电（所选范围）',
      yMin: 0,
      yTick: function (v) {
        return v >= 10 ? fmtKwh(v) : v >= 0.1 ? v.toFixed(2) : v.toFixed(3);
      },
      tf: cumKwh,
      series: [{
        m: 'power', label: '累计', color: 'var(--spark-2)',
        fmt: function (v) { return [fmtKwh(v), 'kWh']; }
      }]
    }
  ];

  /* 各抽屉的图表组：key = 抽屉种类，defs = 图表定义数组 */
  var CHART_GROUPS = {
    cpu: { defs: CPU_CHARTS, metrics: 'cpu,load1' },
    mem: { defs: MEM_CHARTS, metrics: 'mem' },
    temp: { defs: TEMP_CHARTS, metrics: 'temp,temp_mb' },
    power: { defs: POWER_CHARTS, metrics: 'power' }
  };

  /* 图表组的 HTML。
     ⚠️ 范围切换段控件放在**图表组顶部、所有卡片之前**，水平居中 ——
        处理器抽屉里它正好落在第一张「CPU 使用率」卡片的上方中间；
        内存抽屉只有一张图，同样在该卡片上方居中。
        多张图时段控件只出现一次（放在组顶部，不重复到每张卡片里）。 */
  function chartsHtml(groupKey) {
    var grp = CHART_GROUPS[groupKey] || CHART_GROUPS.cpu;
    var defs = grp.defs;
    var cards = defs.map(function (c, i) {
      return '<div class="mon-cmp-chart-card">' +
        '<div class="mon-cmp-chart-head">' +
        '<div class="mon-cmp-chart-title">' + esc(c.title) + '</div>' +
        '<div class="mon-cmp-chart-legend" data-cleg="' + i + '"></div>' +
        '</div>' +
        '<div data-cch="' + i + '"></div>' +
        '</div>';
    }).join('');
    return '<div class="mon-cmp-charts" data-charts="' + groupKey + '">' +
      '<div class="mon-cmp-chart-seg" data-cseg></div>' + cards + '</div>';
  }

  /* ---------------------------------------------------------------------
     板块级趋势图（温度和散热 / 能耗板块用）
     ---------------------------------------------------------------------
     与抽屉里的 chartsHtml 同构，但挂在**板块容器**上而不是抽屉 body 上：
     温度/能耗在 FnStyle 是直接展开的板块（没有抽屉），趋势图就跟在
     板块内容后面，用独立的数据属性避免和抽屉里的 [data-charts] 冲突。

     段控件放在图表组顶部居中 —— 与处理器/内存抽屉保持一致的观感。 */
  function panelChartsHtml(groupKey) {
    var grp = CHART_GROUPS[groupKey] || CHART_GROUPS.cpu;
    var defs = grp.defs;
    var cards = defs.map(function (c, i) {
      return '<div class="mon-cmp-chart-card">' +
        '<div class="mon-cmp-chart-head">' +
        '<div class="mon-cmp-chart-title">' + esc(c.title) + '</div>' +
        '<div class="mon-cmp-chart-legend" data-pleg="' + i + '"></div>' +
        '</div>' +
        '<div data-pch="' + i + '"></div>' +
        '</div>';
    }).join('');
    return '<div class="mon-cmp-charts mon-panel-charts" data-pcharts="' + groupKey + '">' +
      '<div class="mon-cmp-chart-seg" data-pseg></div>' + cards + '</div>';
  }

  /* 板块级图表实例（与抽屉的 chartState 分开管理）
     按板块分组保存 —— 温度板块和能耗板块各自有独立的图表组，
     互相销毁会导致先渲染的那块图表被后渲染的清掉。 */
  var panelChartStates = {};

  function destroyPanelCharts(groupKey) {
    var st = panelChartStates[groupKey];
    if (st) {
      st.charts.forEach(function (c) { try { c.destroy(); } catch (e) {} });
      delete panelChartStates[groupKey];
    }
  }

  /* 初始化某个板块内的趋势图（box = 板块容器，groupKey = temp / power）
     注意：panelChartsHtml() 返回的根元素自身就带 data-pcharts 属性，
     querySelector 只搜索后代、查不到自身，所以这里要一并判断 box 本身。 */
  function initPanelCharts(box, groupKey) {
    if (!box) return;
    var wrap = (box.getAttribute && box.getAttribute('data-pcharts') === groupKey)
      ? box
      : box.querySelector('[data-pcharts="' + groupKey + '"]');
    if (!wrap || !window.FnStyleChart) return;

    var grp = CHART_GROUPS[groupKey] || CHART_GROUPS.cpu;
    var defs = grp.defs;

    var segHost = wrap.querySelector('[data-pseg]');
    var charts = defs.map(function (d, i) {
      var host = wrap.querySelector('[data-pch="' + i + '"]');
      var legend = wrap.querySelector('[data-pleg="' + i + '"]');
      if (!host) return null;
      return new window.FnStyleChart.LineChart(host, {
        series: d.series, yMin: d.yMin, yMax: d.yMax, yTick: d.yTick,
        peak: d.peak, thresholds: d.thresholds,
        height: 180, legend: legend,
        empty: function () {
          return '暂无数据 · 后台每 60 秒记录一条历史，稍后自动出现';
        }
      });
    }).filter(Boolean);

    var range = '1h';
    var st = {
      charts: charts, range: range, res: null,
      box: box, group: groupKey, defs: defs, metrics: grp.metrics
    };
    panelChartStates[groupKey] = st;

    if (segHost) {
      segHost.appendChild(window.FnStyleChart.makeRangeSeg(range, function (r) {
        loadPanelCharts(st, r);
      }));
    }
    loadPanelCharts(st, range);
  }

  function loadPanelCharts(st, range) {
    if (!st) return;
    st.range = range;
    fetchHistory(st.metrics, range).then(function (res) {
      /* 期间可能已经重建（轮询刷新）或切了范围，丢弃过期响应 */
      if (panelChartStates[st.group] !== st || st.range !== range) return;
      st.res = res;
      drawPanelCharts(st);
    });
  }

  /* 板块图表数据刷新（图表实例已存在时用，避免反复重建实例） */
  function refreshPanelCharts(groupKey) {
    var st = panelChartStates[groupKey];
    if (!st) return;
    loadPanelCharts(st, st.range);
  }

  /* 累计用电：把瓦数序列按时间梯形积分成 kWh（对齐 fnmonitor 的 cumKwh） */
  function cumKwh(pts) {
    var out = [];
    if (!pts || pts.length < 2) return out;
    /* 用相邻点的中位间隔做上限，剔除跨度过大的断档 */
    var gaps = [];
    for (var i = 1; i < pts.length; i++) {
      var g = pts[i][0] - pts[i - 1][0];
      if (g > 0) gaps.push(g);
    }
    gaps.sort(function (a, b) { return a - b; });
    var cap = (gaps.length ? gaps[Math.floor(gaps.length / 2)] : 60) * 3;
    var cum = 0;
    for (var j = 1; j < pts.length; j++) {
      var dt = pts[j][0] - pts[j - 1][0];
      if (dt > 0 && dt <= cap) cum += pts[j - 1][1] * dt / 3.6e6;
      out.push([pts[j][0], cum]);
    }
    return out;
  }

  function drawPanelCharts(st) {
    if (!st || !st.res) return;
    var res = st.res;
    var span = window.FnStyleChart.RANGE_SEC[st.range] || 0;
    st.defs.forEach(function (d, i) {
      var c = st.charts[i];
      if (!c) return;
      var pts = (res.series && res.series[d.key]) || [];
      /* tf 存在则先对数据做变换（如累计用电：瓦 → kWh） */
      var data = d.tf ? [d.tf(pts)] : [pts];
      c.setData(data, span);
    });
  }

  /* 图表实例管理：抽屉每次打开重建，关闭时销毁 */
  var chartState = null;

  function destroyCharts() {
    if (chartState) {
      chartState.charts.forEach(function (c) { try { c.destroy(); } catch (e) {} });
      chartState = null;
    }
  }

  function initCharts(body) {
    destroyCharts();
    var wrap = body.querySelector('[data-charts]');
    if (!wrap || !window.FnStyleChart) return;

    var groupKey = wrap.getAttribute('data-charts') || 'cpu';
    var grp = CHART_GROUPS[groupKey] || CHART_GROUPS.cpu;
    var defs = grp.defs;

    var segHost = wrap.querySelector('[data-cseg]');
    var charts = defs.map(function (d, i) {
      var host = body.querySelector('[data-cch="' + i + '"]');
      var legend = body.querySelector('[data-cleg="' + i + '"]');
      if (!host) return null;
      return new window.FnStyleChart.LineChart(host, {
        series: d.series,
        yMin: d.yMin,
        yMax: d.yMax,
        yTick: d.yTick,
        peak: d.peak,
        thresholds: d.thresholds,
        height: 180,
        legend: legend,
        empty: function () {
          return '暂无数据 · 后台每 60 秒记录一条历史，稍后自动出现';
        }
      });
    }).filter(Boolean);

    var range = '1h';
    chartState = {
      charts: charts, range: range, res: null, body: body,
      group: groupKey, defs: defs, metrics: grp.metrics
    };

    if (segHost) {
      segHost.appendChild(window.FnStyleChart.makeRangeSeg(range, function (r) {
        loadCharts(r);
      }));
    }
    loadCharts(range);
  }

  /* 拉取历史序列并绘制 */
  function loadCharts(range) {
    if (!chartState) return;
    var st = chartState;
    st.range = range;
    fetchHistory(st.metrics, range).then(function (res) {
      /* 期间用户可能又切了范围 —— 丢弃过期响应 */
      if (!chartState || chartState !== st || st.range !== range) return;
      st.res = res;
      drawCharts();
    });
  }

  function drawCharts() {
    if (!chartState || !chartState.res) return;
    var res = chartState.res;
    var span = window.FnStyleChart.RANGE_SEC[chartState.range] || 0;
    chartState.defs.forEach(function (d, i) {
      var c = chartState.charts[i];
      if (!c) return;
      var pts = (res.series && res.series[d.key]) || [];
      c.setData([pts], span);
    });
  }

  /* 拉取历史序列（返回 {range, series:{metric:[[ts,v],...]}}） */
  function fetchHistory(metrics, range) {
    var base = 'api/monitor/history';
    var url = base + '?metrics=' + encodeURIComponent(metrics) +
      '&range=' + encodeURIComponent(range);
    return fetch(url, { method: 'GET', headers: {} })
      .then(function (r) {
        return r.text().then(function (txt) {
          try { return JSON.parse(txt); }
          catch (e) { return { success: false, series: {} }; }
        });
      })
      .catch(function () { return { success: false, series: {} }; });
  }

  function kvHtml(rows) {
    return '<div class="mon-dkv">' + rows.map(function (r) {
      var v = r[1];
      var dim = (v === '<span class="dim">—</span>') ? ' dim' : '';
      return '<div class="mon-dkv-row"><span class="k">' + esc(r[0]) + '</span>' +
        '<span class="v' + dim + '">' + v + '</span></div>';
    }).join('') + '</div>';
  }

  function dash(v) {
    if (v === null || v === undefined || v === '') return '<span class="dim">—</span>';
    return esc(v);
  }

  function procRowsHtml(list, key) {
    if (!list || !list.length) {
      return '<div class="mon-cmp-note">暂无进程数据</div>';
    }
    var max = key === 'mem' ? 6 : 8;
    return list.slice(0, max).map(function (p) {
      var right = key === 'cpu'
        ? fmt1(p.cpu) + '%'
        : fmtBytes(p.rss);
      return '<div class="mon-proc">' +
        '<div style="min-width:0"><span class="nm">' + esc(p.comm || '?') + '</span>' +
        '<span class="pid">' + esc(p.pid) + '</span>' +
        (p.args ? '<div class="cmd">' + esc(p.args) + '</div>' : '') +
        '</div>' +
        '<div class="val">' + right + '</div></div>';
    }).join('');
  }

  /* 内存抽屉的进程列表：按常驻内存（RSS）降序取前 6
     —— 对齐 fnmonitor 内存抽屉的口径（CPU 抽屉则是按 CPU 降序取前 8）。 */
  function memProcTop(d) {
    var list = (d && d.procs) || [];
    if (!list.length) return [];
    return list.slice().sort(function (a, b) {
      return (Number(b.rss) || 0) - (Number(a.rss) || 0);
    }).slice(0, 6);
  }

  var computeOpen = false;
  var computeKind = 'cpu';
  var computeData = null;

  function computeOverlay() { return $('#monComputeOverlay'); }

  function openCompute(kind, d) {
    var ov = computeOverlay();
    if (!ov) return;
    computeKind = kind;
    computeData = d;
    var body = $('#monComputeBody');
    var title = $('#monComputeTitle');
    var sub = $('#monComputeSub');
    if (!body || !title || !sub) return;

    body.innerHTML = (kind === 'mem') ? memSheetHtml(d) : cpuSheetHtml(d);
    if (kind === 'mem') {
      title.textContent = '内存';
      sub.textContent = memSubText(d);
    } else {
      title.textContent = '处理器';
      sub.textContent = cpuSubText(d);
    }

    ov.hidden = false;
    if (!computeOpen) {
      if (window.FnStyleMonitor && window.FnStyleMonitor.lockScroll) {
        window.FnStyleMonitor.lockScroll();
      }
    }
    computeOpen = true;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { ov.classList.add('on'); });
    });

    /* 详情里的每核点阵也要填色 */
    if (kind !== 'mem') {
      var pc = (d.cpu && d.cpu.coresPercent) || [];
      var host = body.querySelector('[data-dcores]');
      if (host && pc.length) setDetailCores(host, pc);
    }
    /* 抽屉里的趋势图（处理器 2 张 + 内存 1 张）
       等抽屉滑入后才有实际宽度，所以延后两帧再初始化。 */
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { initCharts(body); });
    });
  }

  function closeCompute() {
    var ov = computeOverlay();
    if (!ov) return;
    ov.classList.remove('on');
    if (computeOpen) {
      if (window.FnStyleMonitor && window.FnStyleMonitor.unlockScroll) {
        window.FnStyleMonitor.unlockScroll();
      }
    }
    computeOpen = false;
    /* 图表实例要显式销毁：ResizeObserver 持有元素引用，
       不清理的话抽屉反复开关会累积监听。 */
    destroyCharts();
    setTimeout(function () {
      if (!computeOpen) ov.hidden = true;
    }, 320);
  }

  function cpuSubText(d) {
    var c = (d && d.cpu) || {};
    var parts = [];
    if (c.model) parts.push(c.model);
    if (c.cores) {
      parts.push(c.cores + ' 核' +
        (c.threads && c.threads !== c.cores ? ' ' + c.threads + ' 线程' : ''));
    }
    return parts.join(' · ');
  }

  function memSubText(d) {
    var m = (d && d.mem) || {};
    return m.total ? gb(m.total) + ' GB 物理内存' : '';
  }

  function cpuSheetHtml(d) {
    var cpu = (d && d.cpu) || {};
    var gpus = (d.gpu && d.gpu.gpus) || [];
    var pc = cpu.coresPercent || [];
    var la = (cpu.loadavg && cpu.loadavg.length)
      ? cpu.loadavg.map(function (v) { return fmt2(parseFloat(v)); }).join(' / ')
      : '--';

    var cores = '';
    if (pc.length) {
      var cols = Math.min(8, pc.length);
      var rows = detailCoreRows(pc.length);
      var h = '';
      for (var i = 0; i < pc.length; i++) {
        h += '<div class="mon-dcore">' +
          dotGaugeSvg(3, rows).replace('<svg ', '<svg data-rows="' + rows + '" ') +
          '<div class="mon-dcore-f"><span>C' + i + '</span>' +
          '<b data-dcv="' + i + '">--</b></div></div>';
      }
      cores = '<div class="mon-dcores" data-dcores style="--cols:' + cols + '">' + h + '</div>';
    } else {
      cores = '<div class="mon-cmp-note">每核数据不可用</div>';
    }

    var gpuHtml = '';
    if (gpus.length) {
      gpuHtml = gpus.map(function (g) {
        var mode = gpuMode(g);
        var iu = gpuIntel(g);
        var util;
        if (mode === 'pmu') {
          util = fmt1(g.util) + '%<div class="faint">' +
            gpuEngines(g).map(function (e) {
              return e[1] + ' ' + fmtInt(g.engines[e[0]]) + '%';
            }).join(' · ') + '</div>';
        } else if (mode === 'rc6') {
          util = fmt1(g.util) + '%（RC6 活跃度估算）';
        } else if (mode === 'warm') {
          util = '<span class="faint">采样中…</span>';
        } else if (g.utilAvailable && isNum(g.util)) {
          util = fmt1(g.util) + '%';
        } else {
          util = iu ? '<span class="faint">未能读取</span>' : dash(null);
        }
        var memTxt = Number(g.memTotal) > 0
          ? fmtBytes(g.memUsed) + ' / ' + fmtBytes(g.memTotal) +
            '（' + fmt1(g.memPct) + '%）'
          : (g.sharedMemory ? '<span class="faint">与系统共享内存</span>' : dash(null));
        var freqTxt = isNum(g.freqMhz)
          ? fmtInt(g.freqMhz) + ' MHz' +
            (isNum(g.freqReqMhz) ? '<div class="faint">请求 ' + fmtInt(g.freqReqMhz) + ' MHz</div>' : '')
          : dash(null);
        return kvHtml([
          ['型号', esc(g.name || '—') +
            (GPU_TAG[g.type] ? ' <span class="mon-chip">' + GPU_TAG[g.type] + '</span>' : '')],
          ['利用率', util],
          ['温度', isNum(g.temp) ? fmt1(g.temp) + ' ℃'
            : (iu ? '<span class="faint">核显无独立温度读数（见 CPU 温度）</span>' : dash(null))],
          ['频率', freqTxt],
          ['显存', memTxt]
        ]);
      }).join('');
    } else {
      gpuHtml = '<div class="mon-cmp-note">未检测到可监控的显卡</div>';
    }

    return '<div class="mon-cmp-hero">' +
      '<div><span class="mon-cmp-eyebrow">CPU 使用率</span>' +
      '<span class="dm">' + fmtInt(cpu.percent) + '</span><span class="unit">%</span></div>' +
      '<div class="right"><span class="mon-cmp-eyebrow">负载 1 / 5 / 15</span>' +
      '<b class="tnum">' + esc(la) + '</b></div></div>' +
      secHtml('每核使用率') + cores +
      chartsHtml('cpu') +
      secHtml('占用最高的进程', '每 15 秒刷新') +
      '<div class="mon-dkv" data-procs>' +
      procRowsHtml((d && d.procs) || [], 'cpu') + '</div>' +
      secHtml('规格') +
      kvHtml([
        ['型号', dash(cpu.model)],
        ['核心 / 线程', dash(cpu.cores) + ' / ' + dash(cpu.threads)],
        ['插槽', dash(cpu.sockets)],
        ['当前频率', isNum(cpu.freqMhz) && cpu.freqMhz > 0
          ? (cpu.freqMhz / 1000).toFixed(2) + ' GHz' : dash(null)],
        ['架构', dash((d && d.system && d.system.arch) || null)]
      ]) +
      secHtml('显卡') + gpuHtml;
  }

  function memSheetHtml(d) {
    var mem = (d && d.mem) || {};
    var mp = memParts(mem);
    var T = Number(mem.total) || 0;
    var a = T ? mp.app / T : 0;
    var c = T ? mp.cache / T : 0;
    var b = T ? mp.buf / T : 0;
    var f = T ? mp.free / T : 0;

    var stack = '<div class="mon-dstack">' +
      '<i style="width:' + (a * 100).toFixed(1) + '%;background:' + rampAt(mem.percent, 'mem') + '"></i>' +
      '<i class="hatch" style="width:' + (c * 100).toFixed(1) + '%"></i>' +
      '<i style="width:' + (b * 100).toFixed(1) + '%;background:var(--text-muted)"></i>' +
      '<i style="width:' + (f * 100).toFixed(1) + '%;background:var(--bg-hover)"></i>' +
      '</div>' +
      '<div class="mon-legend">' +
      '<span><i style="background:' + rampAt(mem.percent, 'mem') + '"></i>应用 <b>' +
      gb(mp.app) + ' GB</b></span>' +
      '<span><i class="hatch"></i>缓存 <b>' + gb(mp.cache) + ' GB</b></span>' +
      '<span><i style="background:var(--text-muted)"></i>缓冲 <b>' + gb(mp.buf) + ' GB</b></span>' +
      '<span><i style="background:var(--bg-hover)"></i>空闲 <b>' + gb(mp.free) + ' GB</b></span>' +
      '</div>';

    var swapHtml = '';
    if (mem.swapTotal) {
      var sp = Math.max(0, Math.min(100, mem.swapUsed * 100 / mem.swapTotal));
      swapHtml = kvHtml([
        ['总量', gb(mem.swapTotal) + ' GB'],
        ['已用', gb(mem.swapUsed) + ' GB（' + sp.toFixed(1) + '%）'],
        ['说明', '<span class="faint">交换分区是物理内存不足时的溢出区，' +
          '频繁读写说明内存压力偏大</span>']
      ]);
    } else {
      swapHtml = '<div class="mon-cmp-note">未启用交换分区</div>';
    }

    var slotsHtml = '';
    var items = memSlots(d);
    if (items.length) {
      slotsHtml = kvHtml(items.map(function (i) {
        var loc = esc(i.locator || '插槽');
        var v = i.present
          ? esc([i.manufacturer, i.size, [i.type, i.speed].filter(Boolean).join('-'), i.part]
            .filter(Boolean).join(' · '))
          : '<span class="faint">空</span>';
        return [loc, v];
      }));
    } else {
      slotsHtml = '<div class="mon-cmp-note">未读取到内存插槽信息' +
        '<div class="faint" style="margin-top:.35rem">需要 root 权限运行 dmidecode</div></div>';
    }

    return '<div class="mon-cmp-hero">' +
      '<div><span class="mon-cmp-eyebrow">已用</span>' +
      '<span class="dm">' + fmtInt(mem.percent) + '</span><span class="unit">%</span></div>' +
      '<div class="right"><span class="mon-cmp-eyebrow">可用</span>' +
      '<b class="tnum">' + (mem.availableBytes ? gb(mem.availableBytes) + ' GB' : '—') + '</b></div>' +
      '</div>' +
      '<div class="mon-cmp-card" style="cursor:default">' + stack + '</div>' +
      '<p class="mon-cmp-note">“已用”为扣除可回收缓存后的实际占用；' +
      '“缓存”是 Linux 用空闲内存加速读盘的部分，应用需要时会立即让出。</p>' +
      chartsHtml('mem') +
      secHtml('内存占用较多的进程', '取自 CPU 前 20 的进程') +
      '<div class="mon-dkv">' + procRowsHtml(memProcTop(d), 'mem') + '</div>' +
      secHtml('交换分区') + swapHtml +
      secHtml('内存插槽') + slotsHtml;
  }

  /* ---------------------------------------------------------------------
     详情抽屉里的每核点阵仪表
     —— 1:1 对齐 fnmonitor 的 dotGauge()：竖向点阵柱，3 列 × N 行，
        核心多时行数收窄（n>8 → 8 行，否则 14 行）。
        填充顺序：从下往上逐行；着色按「该点所在行」的百分比取色阶。
     --------------------------------------------------------------------- */
  function dotGaugeSvg(cols, rows) {
    var h = '<svg viewBox="0 0 ' + cols + ' ' + rows + '" ' +
      'preserveAspectRatio="xMidYMid meet" aria-hidden="true">';
    for (var r = rows - 1; r >= 0; r--) {
      for (var c = 0; c < cols; c++) {
        h += '<circle cx="' + (c + 0.5) + '" cy="' + (r + 0.5) + '" r="0.34"/>';
      }
    }
    return h + '</svg>';
  }

  /* 每核仪表：3 列，核心数 > 8 时 8 行，否则 14 行 */
  function detailCoreRows(n) { return n > 8 ? 8 : 14; }

  function setDetailCores(host, pc) {
    var tiles = host.querySelectorAll('.mon-dcore');
    for (var i = 0; i < tiles.length; i++) {
      var v = pc[i];
      if (!isNum(v)) continue;
      var t = tiles[i];
      var svg = t.querySelector('svg');
      if (!svg) continue;
      var cols = 3;
      var rows = parseInt(svg.getAttribute('data-rows'), 10) || detailCoreRows(pc.length);
      var cs = svg.querySelectorAll('circle');
      var total = cols * rows;
      var k = Math.round(Math.max(0, Math.min(100, v)) / 100 * total);
      for (var j = 0; j < cs.length; j++) {
        var on = j < k;
        var col = on
          ? rampAt((Math.floor(j / cols) + 1) / rows * 100, 'cpu')
          : '';
        var c = cs[j];
        if (c._on !== on) { c._on = on; c.setAttribute('class', on ? 'on' : ''); }
        if (c._col !== col) { c._col = col; c.style.fill = col; }
      }
      var b = t.querySelector('[data-dcv="' + i + '"]');
      if (b) {
        b.textContent = fmtInt(v);
        inkTo(b, rampInk(v, 'cpu'));
      }
    }
  }

  /* ---------------------------------------------------------------------
     事件绑定
     --------------------------------------------------------------------- */
  function bind() {
    if (bind._done) return;
    bind._done = true;

    /* 卡片点击 → 打开详情抽屉 */
    document.addEventListener('click', function (e) {
      var card = e.target.closest ? e.target.closest('[data-cmp-open]') : null;
      if (!card) return;
      var kind = card.getAttribute('data-cmp-open');
      if (!kind) return;
      openCompute(kind, window.FnStyleMonitor ? window.FnStyleMonitor.lastData() : null);
    });

    /* 卡片键盘可达（Enter / Space） */
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var card = e.target.closest ? e.target.closest('[data-cmp-open]') : null;
      if (!card) return;
      e.preventDefault();
      var kind = card.getAttribute('data-cmp-open');
      openCompute(kind, window.FnStyleMonitor ? window.FnStyleMonitor.lastData() : null);
    });

    var ov = computeOverlay();
    if (ov) {
      ov.addEventListener('click', function (e) {
        if (e.target === ov) closeCompute();
      });
      var btn = $('#btnCloseMonCompute');
      if (btn) btn.addEventListener('click', closeCompute);
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && computeOpen) closeCompute();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }

  window.FnStyleCompute = {
    render: renderCoreCards,
    open: openCompute,
    close: closeCompute,
    isOpen: function () { return computeOpen; },
    /* 板块级趋势图（温度和散热 / 能耗板块）*/
    panelChartsHtml: panelChartsHtml,
    initPanelCharts: initPanelCharts,
    refreshPanelCharts: refreshPanelCharts,
    destroyPanelCharts: destroyPanelCharts
  };
})();