/* ==========================================================================
   FnStyle —— 系统监控前端逻辑
   独立成文件（不塞进 app.js）：app.js 已 2000+ 行，监控是独立功能域，
   单独放便于后续独立演进，也避免动 app.js 核心逻辑引入回归。

   数据来源：GET api/monitor（后端后台线程采集，这里只读缓存）
   四大板块：存储 / 处理器内存显卡 / 温度散热 / 能耗
   ========================================================================== */
(function () {
  'use strict';

  var BASE = 'api/';
  var REFRESH_MS = 3000;       // 自动刷新周期，与后端采集周期一致
  var timer = null;
  var lastData = null;
  var inited = false;

  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); }

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtBytes(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    var v = Number(n), i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + ' ' + units[i];
  }

  /* 速率格式化（字节/秒 → 人类可读），对齐 fnmonitor 的 F.rateStr。
     趋势图的图例数值与曲线必须同源：都从 read_rate / write_rate 现算，
     不能一个用后端下发的 readText、另一个用 read_rate —— 两者可能不同步
     （后端 readText 由上一轮采集算好，前端 read_rate 是本次原始值）。 */
  function fmtRate(bps) {
    if (bps === null || bps === undefined || isNaN(bps)) return '--';
    var v = Number(bps);
    if (v < 0) v = 0;
    var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    var i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    var num = i === 0 ? String(Math.round(v)) : (v >= 100 ? String(Math.round(v)) : v.toFixed(1));
    return num + ' ' + units[i] + '/s';
  }

  /* 硬盘 / 存储池容量格式化（十进制，与 fnmonitor F.dsize 和 fnOS 存储页一致）。
     磁盘厂商标称 1 TB = 10^12 字节，若用 1024 进制会显示成 0.91 TB，
     与系统存储页、fnmonitor 的数字都对不上。
     舍入规则照搬 fnmonitor：≥100 取整、≥10 取 1 位小数、否则 2 位小数。 */
  function fmtDisk(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var b = Number(n);
    if (b <= 0) return '0 MB';
    var T = 1e12, G = 1e9, M = 1e6;
    if (b >= T) {
      var vt = b / T;
      return (vt >= 100 ? vt.toFixed(0) : vt >= 10 ? vt.toFixed(1) : vt.toFixed(2)) + ' TB';
    }
    if (b >= G) {
      var vg = b / G;
      return (vg >= 100 ? vg.toFixed(0) : vg.toFixed(1)) + ' GB';
    }
    return Math.round(b / M) + ' MB';
  }

  /* 百分比 → 等级（用于条形/温度着色）。warn 从 70% 起，high 从 90% 起。 */
  function level(pct, warnAt, highAt) {
    if (pct === null || pct === undefined || isNaN(pct)) return 'ok';
    if (pct >= highAt) return 'high';
    if (pct >= warnAt) return 'warn';
    return 'ok';
  }

  /* 温度阈值。
     ⚠️ 口径与 fnmonitor 对齐（ui-src/33-state.js 的 TH_SPEC）：
       diskTempWarn 50℃ / diskTempCrit 58℃ —— 硬盘的温度余量比 CPU 小得多，
       50℃ 就该留意了；早先这里写死 60/75，与 fnmonitor 差一大截。
       通用传感器（CPU / 风扇位）沿用 60/75 的家用口径。 */
  var TH_DISK_TEMP_WARN = 50;
  var TH_DISK_TEMP_CRIT = 58;
  var TH_TEMP_WARN = 60;
  var TH_TEMP_CRIT = 75;

  /* 温度等级：返回 'ok' / 'warn' / 'high'。
     ⚠️ 注意 'ok' 也是真值 —— 判「是否超阈值」必须显式比较，
        不能写成 if (lv)，否则任何温度都会被当成超阈。
        要判是否超阈请用 tempExceed() 。 */
  function tempLevel(t) {
    if (t === null || t === undefined || isNaN(t)) return 'ok';
    if (t >= TH_TEMP_CRIT) return 'high';
    if (t >= TH_TEMP_WARN) return 'warn';
    return 'ok';
  }

  /* 硬盘温度等级：fnmonitor 的 LV.dtemp 口径（50 / 58） */
  function diskTempLevel(t) {
    if (t === null || t === undefined || isNaN(t)) return 'ok';
    if (t >= TH_DISK_TEMP_CRIT) return 'high';
    if (t >= TH_DISK_TEMP_WARN) return 'warn';
    return 'ok';
  }

  /* 是否真的超过硬盘温度阈值（返回 '' / 'warn' / 'high'）。
     这是判「要不要弹温度告警条」的唯一依据。 */
  function tempExceed(t) {
    var lv = diskTempLevel(t);
    return (lv === 'warn' || lv === 'high') ? lv : '';
  }

  /* ---------------- 通用块渲染 ---------------- */

  function item(title, valueHtml, subHtml, extraCls) {
    return '<div class="mon-item ' + (extraCls || '') + '">' +
      '<div class="mon-item-head">' +
        '<span class="mon-item-title">' + esc(title) + '</span>' +
        '<span class="mon-item-value">' + valueHtml + '</span>' +
      '</div>' +
      (subHtml || '') +
    '</div>';
  }

  function bar(pct) {
    var p = Math.max(0, Math.min(100, Number(pct) || 0));
    return '<div class="mon-bar"><div class="mon-bar-fill lv-' + level(p, 70, 90) +
           '" style="width:' + p + '%"></div></div>';
  }

  function emptyBox(text) {
    return '<div class="mon-empty">' + esc(text) + '</div>';
  }

  /* ---------------- 1. 存储 ---------------- */
  /* 能力对齐 fnmonitor 存储板块：存储池容量 / 物理盘 SMART 与分级 /
     磁盘 IO 速率 / 休眠状态 / 软 RAID / 硬 RAID 卡 / 巡检历史。 */

  /* SMART 分级 → 徽标样式 */
  var GRADE_BADGE = {
    ok: { cls: 'ok', text: '正常' },
    warn: { cls: 'warn', text: '留意' },
    crit: { cls: 'crit', text: '故障' },
    standby: { cls: 'unknown', text: '休眠' },
    unknown: { cls: 'unknown', text: '未知' }
  };

  /* SMART 告警计数列定义（顺序与后端 SMART_COUNTER_KEYS 一致） */
  var SMART_KEYS = [
    ['reallocated_sectors', '重映射扇区'],
    ['reallocated_events', '重映射事件'],
    ['pending_sectors', '待映射扇区'],
    ['uncorrectable', '无法纠正错误'],
    ['udma_crc', 'UDMA CRC 错误']
  ];

  /* SMART 告警项 → 严重等级。
     crit = 数据已不可恢复地损坏（必须立刻处理）；
     warn = 已有异常征兆但尚未致命（需留意，安排换盘）。
     判据与后端 grade_disk 一致：无法纠正错误 → crit，
     其余非零计数项 → warn；SMART 自检未通过一律 crit。 */
  var SMART_CRIT_KEYS = ['uncorrectable'];
  var SMART_WARN_KEYS = ['reallocated_sectors', 'reallocated_events',
    'pending_sectors', 'udma_crc'];

  /* 取某项告警计数值（非数字 / 缺失 → null） */
  function smartCount(d, key) {
    if (!d) return null;
    var v = d[key];
    if (v === null || v === undefined || v === '' || isNaN(v)) return null;
    return Number(v);
  }

  /* 汇总单盘的 SMART 告警：返回 { lv, items }。
     lv 取 'crit' / 'warn' / ''（无告警）；items 为 [{label, value, lv}]。 */
  function smartAlerts(d) {
    var items = [];
    var lv = '';
    // SMART 自检未通过 —— 直接判故障
    if (/FAIL/i.test(String((d && d.health) || ''))) {
      items.push({ label: 'SMART 自检未通过', value: null, lv: 'crit' });
      lv = 'crit';
    }
    SMART_KEYS.forEach(function (k) {
      var v = smartCount(d, k[0]);
      if (v === null || v <= 0) return;
      var itemLv = SMART_CRIT_KEYS.indexOf(k[0]) >= 0 ? 'crit' : 'warn';
      items.push({ label: k[1], value: v, lv: itemLv });
      // crit 优先于 warn
      if (itemLv === 'crit' || !lv) lv = itemLv;
    });
    return { lv: lv, items: items };
  }

  /* 盘位 LED 的最终状态 = 巡检分级 与 SMART 告警 取更严重者。
     - 休眠 > 故障 > 留意 > 正常 > 未巡检
     这样「巡检说正常但 SMART 计数不为 0」的情况不会被绿灯掩盖。 */
  var LED_RANK = { standby: 4, crit: 3, warn: 2, ok: 1, unknown: 0 };
  function bayLedState(d) {
    var scanLv = d.standby ? 'standby' : (d.grade || 'unknown');
    var sm = smartAlerts(d);
    var lv = scanLv;
    if (sm.lv && LED_RANK[sm.lv] > LED_RANK[lv]) lv = sm.lv;
    return { lv: lv, scanLv: scanLv, smart: sm };
  }

  /* SMART 告警的可读描述（用于 LED 的 title 说明） */
  function smartAlertsText(sm) {
    if (!sm || !sm.items.length) return '';
    return sm.items.map(function (it) {
      return it.value === null ? it.label : (it.label + ' ' + it.value);
    }).join('、');
  }

  /* 分级文案（与后端 GRADE_LABEL 一致；fnmonitor 用「未巡检」，这里保持一致） */
  var GRADE_TXT = { ok: '正常', warn: '留意', crit: '故障', standby: '休眠中', unknown: '未巡检' };

  function gradeBadge(g) {
    var m = GRADE_BADGE[g] || GRADE_BADGE.unknown;
    return '<span class="mon-badge ' + m.cls + '">' + m.text + '</span>';
  }

  /* ---- 盘位卡片（参照 fnmonitor 的 NAS 前面板）---- */

  /* 盘位标签：优先「存储空间 N」（fnOS 存储空间 N = /volN），没挂载则显式标出 */
  function volOf(d) {
    if (d && d.vol) return Number(d.vol) || 0;
    var ms = (d && d.mounts) || [];
    for (var i = 0; i < ms.length; i++) {
      var m = /^\/vol(\d+)(?:\/|$)/.exec(String(ms[i]));
      if (m) return Number(m[1]);
    }
    return 0;
  }

  function volLabel(d) {
    var v = volOf(d);
    return v ? '存储空间 vol' + v : '未挂载';
  }

  /* 盘位排序：按存储空间号升序，同一空间内按设备名；未挂载排最后 */
  function byVol(a, b) {
    var va = volOf(a), vb = volOf(b);
    if (va !== vb) return (va || 999) - (vb || 999);
    return String(a.name).localeCompare(String(b.name));
  }

  /* 厂商 + 型号（fnmonitor 同口径：厂商名已含在型号里时不重复） */
  function vendorModel(d) {
    var v = String((d && d.vendor) || '').trim();
    var mo = String((d && d.model) || '').trim();
    if (!v) return mo;
    if (/^0x/i.test(v) || /^ata$/i.test(v)) return mo;
    if (mo.toLowerCase().indexOf(v.toLowerCase()) === 0) return mo;
    return v + ' ' + mo;
  }

  /* 介质类型：决定容量点阵仪表的列数（NVMe 4 列，其余 5 列） */
  function diskKind(d) {
    var nm = String((d && d.name) || '');
    if (/^nvme/.test(nm)) return 'nvme';
    var rpm = String((d && d.rpm) || '');
    if (/solid state/i.test(rpm)) return 'ssd';
    if (/rpm/i.test(rpm)) return 'hdd';
    if (String((d && d.type) || '').toUpperCase() === 'SSD') return 'ssd';
    if (String((d && d.type) || '').toUpperCase() === 'HDD') return 'hdd';
    return 'disk';
  }

  /* 容量等级：<85 正常，>=85 留意，>=95 故障（与 fnmonitor 阈值一致） */
  function capLevel(pct) {
    var p = Number(pct);
    if (!isFinite(p)) return '';
    if (p >= 95) return 'crit';
    if (p >= 85) return 'warn';
    return '';
  }

  /* ---- 负载色阶（移植 fnmonitor「负载色阶」规范）----
     容量类控件按数值着色：绿 → 黄 → 橙 → 红，越满越"热"。
     色标 [数值, 颜色]，容量档用 fnmonitor 的 capWarn=85 / capCrit=95。 */
  var RAMP_G = 'var(--r-g)';     /* 绿 */
  var RAMP_Y = 'var(--r-y)';     /* 黄绿 */
  var RAMP_A = 'var(--warning)'; /* 橙 */
  var RAMP_C = 'var(--danger)';  /* 红 */

  /* 容量色阶色标：0 绿 → 85%×0.8 绿 → 85%×0.5 绿 → 85% 黄绿 → 85% 橙 → 95% 红
     与 fnmonitor 的 RAMP.pct(capWarn, capCrit) 完全一致。 */
  function capRampStops() {
    var w = 85, c = 95;
    return [[0, RAMP_G], [w * .5, RAMP_G], [w * .8, RAMP_Y], [w, RAMP_A], [Math.max(c, w), RAMP_C]];
  }

  function clampN(v, lo, hi) {
    v = Number(v);
    if (!isFinite(v)) return lo;
    return v < lo ? lo : (v > hi ? hi : v);
  }

  /* 数值 v 在色阶上的颜色（fnmonitor rampAt 的等价实现）。
     两色之间用 color-mix(in oklch, ...) 做连续插值。 */
  function rampAt(v, stops) {
    var st = stops || capRampStops();
    v = Number(v);
    if (!isFinite(v)) return st[0][1];
    if (v <= st[0][0]) return st[0][1];
    for (var i = 1; i < st.length; i++) {
      if (v <= st[i][0]) {
        var span = (st[i][0] - st[i - 1][0]) || 1;
        var t = clampN((v - st[i - 1][0]) / span, 0, 1);
        if (t < .02) return st[i - 1][1];
        if (t > .98) return st[i][1];
        return 'color-mix(in oklch, ' + st[i][1] + ' ' + Math.round(t * 100) + '%, ' + st[i - 1][1] + ')';
      }
    }
    return st[st.length - 1][1];
  }

  /* 整条轨道的色阶渐变（fnmonitor rampGrad 的等价实现）。 */
  function rampGrad(stops, dir) {
    var st = stops || capRampStops();
    return 'linear-gradient(' + (dir || '90deg') + ', ' + st.map(function (s) {
      return s[1] + ' ' + clampN(s[0], 0, 100).toFixed(1) + '%';
    }).join(', ') + ')';
  }

  /* 巡检时间描述：「刚刚 / X 分钟前 / X 小时前 / X 天前」 */
  function scanAgo(ts) {
    if (!ts) return '';
    var s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 90) return '刚刚';
    if (s < 3600) return Math.round(s / 60) + ' 分钟前';
    if (s < 86400) return Math.round(s / 3600) + ' 小时前';
    return Math.round(s / 86400) + ' 天前';
  }

  /* 容量点阵仪表：cols 列 × rows 行的 SVG 圆点，自下而上按占用比例点亮。
     点亮顺序与 fnmonitor 一致：从最底下一行开始，左→右逐行往上。
     颜色：每个点按「自身在柱中的高度位置」取容量色阶色 → 底部绿、顶部红，
     标识空间使用越多越需要注意（fnmonitor dotGauge 的 ramp='cap' 语义）。

     ⚠️ fnmonitor 里内联 fill 恒由 ramp 决定 —— 内联样式优先级高于 CSS 的
     [data-l="warn"]/[data-l="crit"] 规则，所以它的点阵**始终**是容量色阶渐变，
     健康分级另外走 LED 与 title。这里保持同样语义。 */
  function gaugeSvg(cols, rows, frac) {
    var total = cols * rows;
    var k = Math.round(Math.max(0, Math.min(1, frac || 0)) * total);
    var stops = capRampStops();
    var out = '';
    for (var i = 0; i < total; i++) {
      var on = i < k;
      var fill = '';
      if (on) {
        /* 点在柱中的高度位置（0=底部, 100=顶部）→ 色阶取值 */
        var row = Math.floor(i / cols);
        fill = rampAt((row + 1) / rows * 100, stops);
      }
      out += '<circle class="' + (on ? 'on' : '') + '"' +
        (fill ? ' style="fill:' + fill + '"' : '') +
        ' cx="' + ((i % cols) + 0.5) +
        '" cy="' + (rows - Math.floor(i / cols) - 0.5) + '" r="0.34"></circle>';
    }
    return '<svg viewBox="0 0 ' + cols + ' ' + rows + '" preserveAspectRatio="xMidYMid meet"' +
      ' aria-hidden="true">' + out + '</svg>';
  }

  /* 与控件联动的数字：同一色阶的文字版（浅色模式压暗以保证对比度）。
     移植 fnmonitor rampInk：>=95 纯红、>=85 橙、>=76.5 橙，其余绿→黄绿插值。 */
  function rampInk(v, stops) {
    var st = stops || capRampStops();
    v = Number(v);
    if (!isFinite(v)) return '';
    if (v >= st[4][0]) return 'var(--danger)';
    if (v >= st[3][0]) return 'var(--warning)';
    /* 黄绿 → 橙 这一段：填充色过了中点就已明显偏橙，数字在同一点切换为橙 */
    if (v >= (st[2][0] + st[3][0]) / 2) return 'var(--warning)';
    var t = clampN((v - st[1][0]) / ((st[2][0] - st[1][0]) || 1), 0, 1);
    if (t < .02) return 'var(--rt-g)';
    if (t > .98) return 'var(--rt-l)';
    return 'color-mix(in oklch, var(--rt-l) ' + Math.round(t * 100) + '%, var(--rt-g))';
  }

  /* smartFlagsHtml 已移除：
     原「SMART 告警计数」卡片用的徽章式 5 项计数，与硬盘详情抽屉里
     dwSmartTable() 的 SMART 表（同样的 5 项，且带状态列）完全重复。
     卡片删掉后这个函数失去唯一调用点，一并清理，避免留下死代码。 */

  /* ==========================================================================
     巡检历史抽屉
     巡检记录原先是一张独立的「硬盘巡检」卡片，占掉监控面板一整块位置，
     但它的信息密度低（时间 + 一句话结论），又和盘位卡片的「巡检于 …」重复。
     现在改由存储标题栏的「巡检历史」按钮打开右抽屉查看。
     ========================================================================== */

  /* --------------------------------------------------------------------------
     页面滚动锁
     抽屉是 position:fixed 的全屏遮罩，但**挡不住页面本身的滚动条** ——
     抽屉内容长的时候抽屉自己有滚动条，页面也还有一个，
     屏幕上就并排出现两条滚动条（用户实机反馈）。

     做法：抽屉打开时给 <html> 加 .mon-scroll-lock，
     CSS 里把它设为 overflow:hidden —— 页面不再滚动，
     滚动条随之消失，只剩抽屉自己那一条。
     ⚠️ 用 overflow:hidden 而不是 body{position:fixed}：
        后者会让页面在关闭抽屉后跳回顶部，体验更差。
     ⚠️ overflow:hidden 会让滚动条消失后内容横向挪动几像素，
        所以用 scrollbar-gutter:stable 预留滚动条宽度，
        避免抽屉弹出/收起时页面左右抖动。
     -------------------------------------------------------------------------- */
  var scrollLockCount = 0;

  function lockPageScroll() {
    scrollLockCount++;
    if (scrollLockCount === 1) {
      var de = document.documentElement;
      if (de) de.classList.add('mon-scroll-lock');
    }
  }

  function unlockPageScroll() {
    scrollLockCount = Math.max(0, scrollLockCount - 1);
    if (scrollLockCount === 0) {
      var de = document.documentElement;
      if (de) de.classList.remove('mon-scroll-lock');
    }
  }

  var scanLogOpen = false;

  function monScanLogOverlay() { return $('#monScanLogOverlay'); }

  /* 巡检流水渲染：
     scan     = { last_ts, running, summary, error }
     scanLog  = [{ ts, total, changed, summary }]（新→旧）
     异常轮次永久保留，「未见异常」只留 7 天（后端策略）。 */
  function renderScanLogHtml(scan, scanLog) {
    scan = scan || {};
    scanLog = scanLog || [];
    var html = '';

    /* 顶部概览：最近一次巡检时间 + 本轮盘数 + 状态 */
    var lastTs = scan.last_ts || 0;
    var lastText = lastTs
      ? new Date(lastTs * 1000).toLocaleString('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
        })
      : '尚未巡检';
    var ago = scanAgo(lastTs);
    var counts = (scan.summary && scan.summary.counts) || {};

    html += '<div class="mon-dw-hero"><div>' +
      '<div class="mon-dw-eyebrow">最近巡检</div>' +
      '<div class="mon-dw-big" style="font-size:1.25rem">' + esc(lastText) + '</div>' +
      '</div>' +
      '<div class="mon-dw-right">' +
      '<div class="mon-dw-eyebrow">状态</div>' +
      '<b>' + (scan.running ? '巡检中…' : (lastTs ? (ago || '刚刚') : '未巡检')) + '</b>' +
      '</div></div>';

    /* 本轮分级统计（后端 summary.counts）
       ⚠️ 口径说明必须写在这里 —— 这套统计只反映**盘体健康**
       （重映射扇区 / 待映射扇区 / 不可纠正错误 / 缺陷增长），
       不含 UDMA CRC 错误等**链路类**告警。
       所以「留意 0」不代表盘位 LED 全绿：
       盘位 LED 是「巡检分级 + SMART 告警取更严重者」，
       CRC 这类线缆/供电问题的告警只在 LED 上体现。
       不写清楚就会出现「LED 橙着、巡检说留意 0」的自相矛盾。 */
    var cntHtml = '';
    if (counts.ok || counts.warn || counts.crit || counts.unknown) {
      cntHtml = '<span class="mon-badge ok">正常 ' + (counts.ok || 0) + '</span>' +
        '<span class="mon-badge warn">留意 ' + (counts.warn || 0) + '</span>' +
        '<span class="mon-badge crit">故障 ' + (counts.crit || 0) + '</span>' +
        '<span class="mon-badge unknown">未知 ' + (counts.unknown || 0) + '</span>';
      html += '<div class="mon-dw-card"><div class="mon-chips">' + cntHtml + '</div>' +
        '<div class="mon-slog-text empty mon-slog-scope">统计口径：盘体健康（坏道、待映射扇区、不可纠正错误、缺陷增长）。' +
        '线缆与供电类告警（如 UDMA CRC 错误）不计入本表，以盘位 LED 为准。</div></div>';
    }

    /* 巡检异常：置顶红色提示 */
    if (scan.error) {
      html += '<div class="mon-dw-note crit">⚠ <span>巡检异常：' + esc(scan.error) + '</span></div>';
    }

    /* 流水列表 */
    html += dwSec('巡检流水', scanLog.length ? ('共 ' + scanLog.length + ' 条') : '');
    if (scanLog.length) {
      html += '<div class="mon-slog">' + scanLog.map(function (l) {
        var cls = l.changed ? 'changed' : 'ok';
        var when = l.ts ? new Date(l.ts * 1000).toLocaleString('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
        }) : '—';
        var ag = scanAgo(l.ts);
        var txt = l.summary || '';
        return '<div class="mon-slog-item ' + cls + '">' +
          '<div class="mon-slog-head">' +
            '<span class="mon-slog-time">' + esc(when) + '</span>' +
            (ag ? '<span class="mon-slog-ago">' + esc(ag) + '</span>' : '') +
            '<span class="mon-slog-badge ' + cls + '">' +
              (l.changed ? '有变化' : '未见异常') + '</span>' +
          '</div>' +
          '<div class="mon-slog-text' + (txt ? '' : ' empty') + '">' +
            (txt ? esc(txt) : '无结论') + '</div>' +
          '</div>';
      }).join('') + '</div>';
    } else {
      html += '<div class="mon-dw-card"><div class="mon-slog-text empty">' +
        '尚未巡检（首轮巡检在服务启动约 30 秒后进行，之后每 6 小时一轮）</div></div>';
    }

    return html;
  }

  function openScanLogDrawer() {
    var ov = monScanLogOverlay();
    if (!ov) return;
    var body = $('#monScanLogBody');
    var sub = $('#monScanLogSub');
    if (!body) return;
    var st = lastData && lastData.storage ? lastData.storage : null;
    body.innerHTML = renderScanLogHtml((st && st.scan) || {}, (st && st.scanLog) || []);
    if (sub) {
      var n = ((st && st.scanLog) || []).length;
      sub.textContent = n ? ('最近 ' + n + ' 条记录') : '暂无巡检记录';
    }
    ov.hidden = false;
    /* 同样用「从关到开才加锁」的守卫 ——
       抽屉开着时再点一次「巡检历史」按钮会重走这里，
       不加守卫的话锁计数会只增不减，关闭时解不干净，
       页面就一直处于锁滚动状态。 */
    if (!scanLogOpen) lockPageScroll();
    scanLogOpen = true;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { ov.classList.add('on'); });
    });
  }

  function closeScanLogDrawer() {
    var ov = monScanLogOverlay();
    if (!ov) return;
    ov.classList.remove('on');
    if (scanLogOpen) unlockPageScroll();
    scanLogOpen = false;
    setTimeout(function () {
      if (!scanLogOpen) ov.hidden = true;
    }, 320);
  }

  function bindScanLogDrawer() {
    var ov = monScanLogOverlay();
    if (!ov || ov.__bound) return;
    ov.__bound = true;
    ov.addEventListener('click', function (e) {
      if (e.target === ov) closeScanLogDrawer();
    });
    var btn = $('#btnCloseMonScanLog');
    if (btn) btn.addEventListener('click', closeScanLogDrawer);
    var openBtn = $('#btnMonScanLog');
    if (openBtn) openBtn.addEventListener('click', openScanLogDrawer);
    document.addEventListener('keydown', function (e) {
      if ((e.key === 'Escape' || e.key === 'Esc') && scanLogOpen) {
        e.preventDefault();
        closeScanLogDrawer();
      }
    });
  }

  /* ==========================================================================
     硬盘详情抽屉（点击盘位卡片从右侧滑出）
     呈现内容对齐 fnmonitor 的「硬盘」详情面板（.sheet disk/<dev>）：
     容量大数字 → 容量条 → 告警/休眠提示 → 实时读写趋势图 → SMART 表 → 信息表。
     ========================================================================== */

  var drawerOpen = false;
  var drawerDev = null;

  /* --------------------------------------------------------------------------
     实时读写趋势图（对齐 fnmonitor 的 .chart-card + .spark）
     fnmonitor 的「实时读写」不是键值表，而是一张带图例的双线趋势图：
       标题「读写速率」 + 读取/写入两条线色标 + 实时速率数字 + 64px 高的 spark。
     数据来自前端内存里的滑动缓冲 S.diskBuf（每次轮询 push 一次读写速率）。
     窗口长度对齐 fnmonitor 的「近 5 分钟」：fnmonitor 是 5s×60 点，
     FnStyle 轮询 3s，所以取 100 点同样是 5 分钟（见 BUF_N）。

     缓冲区额外记一路时间戳（t），用于在曲线下方标出时间锚点
     （窗口起止两端），让用户知道这条曲线对应哪段时间。
     -------------------------------------------------------------------------- */
  var BUF_N = 100;                      // 缓冲点数：3s × 100 = 5 分钟窗口
  var diskBuf = {};                     // { dev: { r: [], w: [], t: [] } }
  var diskBufTs = 0;                    // 上次采纳的后端快照时间戳（秒）

  function bufPush(arr, v) {
    arr.push(Number(v) || 0);
    if (arr.length > BUF_N) arr.shift();
  }

  /* 每次刷新把当前读写速率推进缓冲（fnmonitor 的 feed() 同款位置）
     ⚠️ 必须做 ts 去重（对齐 fnmonitor feed() 的 `if (S._lastTs === ts) return`）：
     前端每 3s 拉一次、后端每 3s 采一次，两个时钟异步，实测约 15% 的请求
     拿到的是同一份后端缓存（ts 未变）。不去重就会往曲线上灌重复值，
     表现为「平台 + 突然跳变」的锯齿，还把有效窗口挤短。
     ⚠️ ts 在快照顶层（monitor_collect 的 data["ts"]）或 storage 子对象
     （storage_collect 采集时刻的浮点时间戳，唯一性更强），必须由调用方传进来。 */
  function feedDiskBuf(st, ts) {
    if (!st) return;
    ts = ts ? Number(ts) : 0;
    if (ts && diskBufTs && ts === diskBufTs) return;   // 同一份快照，不重复采纳
    if (ts) diskBufTs = ts;
    var io = st.diskio;
    if (!io || !io.length) return;
    var now = Date.now();
    io.forEach(function (z) {
      var b = diskBuf[z.name];
      if (!b) { b = diskBuf[z.name] = { r: [], w: [], t: [] }; }
      bufPush(b.r, z.read_rate);
      bufPush(b.w, z.write_rate);
      bufPush(b.t, now);
    });
  }

  /* 本地时间 HH:MM（对齐 fnmonitor 的 F.hm：零填充小时分钟） */
  function fmtHM(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    var hh = d.getHours(), mm = d.getMinutes();
    return (hh < 10 ? '0' : '') + hh + ':' + (mm < 10 ? '0' : '') + mm;
  }

  /* 平滑曲线：单调三次插值（monotone cubic），避免普通样条在极值处过冲。
     实现同 fnmonitor 的 monoPath()。 */
  function monoPath(xs, ys) {
    var n = xs.length;
    if (!n) return '';
    var f = function (v) { return Math.round(v * 10) / 10; };
    if (n === 1) return 'M' + f(xs[0]) + ',' + f(ys[0]);
    var dx = [], m = [], t = new Array(n), i;
    for (i = 0; i < n - 1; i++) {
      dx[i] = xs[i + 1] - xs[i] || 1e-6;
      m[i] = (ys[i + 1] - ys[i]) / dx[i];
    }
    t[0] = m[0];
    t[n - 1] = m[n - 2];
    for (i = 1; i < n - 1; i++) {
      t[i] = (m[i - 1] * m[i] <= 0) ? 0
        : 3 * (dx[i - 1] + dx[i]) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
    }
    var d = 'M' + f(xs[0]) + ',' + f(ys[0]);
    for (i = 0; i < n - 1; i++) {
      var h = dx[i] / 3;
      d += 'C' + f(xs[i] + h) + ',' + f(ys[i] + t[i] * h) + ' '
        + f(xs[i + 1] - h) + ',' + f(ys[i + 1] - t[i + 1] * h) + ' '
        + f(xs[i + 1]) + ',' + f(ys[i + 1]);
    }
    return d;
  }

  /* 渲染双线 spark 图到指定的 <svg> 元素。
     series: [{values:[...]}, {values:[...]}]，对齐 fnmonitor 的 spark.set()。
     min 固定 0（速率没有负值），max 自适应并留 18% 余量。 */
  function drawSpark(svg, series) {
    if (!svg) return;
    var w = svg.clientWidth || 320;
    var h = svg.clientHeight || 64;
    if (!w || !h) return;
    var all = [];
    series.forEach(function (s) { all = all.concat(s.values); });
    if (all.length < 2) {
      svg.innerHTML = '';
      return;
    }
    var lo = 0;
    var hi = Math.max.apply(null, all);
    if (hi - lo < 1e-9) hi = lo + 1;
    hi += (hi - lo) * 0.18;              // 顶部留白，避免贴边
    var pad = 3;
    var html = '';
    series.forEach(function (s, si) {
      var vals = s.values;
      var n = vals.length;
      if (n < 2) return;
      var xs = vals.map(function (_, i) { return i / (n - 1) * w; });
      var ys = vals.map(function (v) {
        var c = Math.max(lo, Math.min(hi, v));
        return pad + (1 - (c - lo) / (hi - lo)) * (h - pad * 2);
      });
      var d = monoPath(xs, ys);
      // 第一条线带面积填充（fnmonitor 同款）
      if (si === 0) html += '<path class="area" d="' + d + 'L' + w + ',' + h + 'L0,' + h + 'Z"/>';
      html += '<path class="line' + (si ? ' l2' : '') + '" d="' + d + '"/>';
      // 末端活动光点
      html += '<circle class="halo" cx="' + w + '" cy="' + ys[n - 1].toFixed(1) + '" r="5"/>'
        + '<circle cx="' + w + '" cy="' + ys[n - 1].toFixed(1) + '" r="2.5"/>';
    });
    svg.innerHTML = html;
  }

  function monDrawerOverlay() { return $('#monDrawerOverlay'); }

  /* 数值 → 千分位字符串；非数字返回 null */
  function dwNum(v) {
    if (v === null || v === undefined || v === '' || isNaN(v)) return null;
    return Number(v).toLocaleString('zh-CN');
  }

  /* 单盘 SMART 表行：与 fnmonitor 一致 —— 有计数的标状态，无数据的显示 — */
  function dwSmartRows(d) {
    var rows = [];
    var health = String((d && d.health) || '');
    var healthTxt = health
      ? (/PASS/i.test(health) ? '通过' : health)
      : '<span class="faint">未读取到</span>';
    var healthSt = /FAIL/i.test(health) ? 'crit' : (/PASS|OK/i.test(health) ? 'ok' : '');
    rows.push({
      k: '整体评估',
      v: healthTxt,
      st: healthSt,
      // 休眠盘的扇区计数只有休眠前的值，用「休眠前」标注（fnmonitor 同款）
      note: (d && d.standby) ? '休眠前' : ''
    });

    SMART_KEYS.forEach(function (k) {
      var v = smartCount(d, k[0]);
      var txt = (v === null) ? '<span class="faint">—</span>' : esc(String(v));
      /* 分级与盘位 LED 保持同一套告警分类：
         无法纠正错误 = 故障（crit），其余非零计数 = 留意（warn）。 */
      var st = (v === null) ? '' : (v > 0
        ? (SMART_CRIT_KEYS.indexOf(k[0]) >= 0 ? 'crit' : 'warn')
        : 'ok');
      rows.push({ k: k[1], v: txt, st: st, note: (d && d.standby) ? '休眠前' : '' });
    });

    rows.push({
      k: '通电时间',
      v: (d && d.power_on_hours)
        ? esc(dwNum(d.power_on_hours) || d.power_on_hours) + ' 小时'
        : '<span class="faint">—</span>',
      st: '', note: ''
    });
    var t = (d && d.temp !== null && d.temp !== undefined && d.temp !== '') ? Number(d.temp) : null;
    rows.push({
      k: '温度',
      v: (t === null) ? '<span class="faint">—</span>' : (esc(String(t)) + '℃'),
      st: (t === null || !d || d.standby) ? '' : diskTempLevel(t),
      note: ''
    });
    rows.push({
      k: '转速',
      // SSD 的 smartctl rpm 字段是 "Solid State Device" 这种描述文本，
      // 不是转速数字 —— 拼上 " RPM" 会变成 "Solid State Device RPM"，很怪。
      v: (d && d.rpm)
        ? (/solid state/i.test(String(d.rpm))
            ? esc(String(d.rpm))
            : esc(String(d.rpm)) + ' RPM')
        : '<span class="faint">—</span>',
      st: '', note: ''
    });
    return rows;
  }

  /* SMART 表的 HTML */
  function dwSmartTable(d) {
    var rows = dwSmartRows(d);
    var body = rows.map(function (r) {
      var stCell;
      if (r.note) stCell = '<span class="faint">' + esc(r.note) + '</span>';
      else if (r.st === 'ok') stCell = '<span class="st-ok">✓</span>';
      else if (r.st === 'warn') stCell = '<span class="pill warn">留意</span>';
      else if (r.st === 'crit') stCell = '<span class="pill crit">严重</span>';
      else stCell = '<span class="faint">—</span>';
      var vCls = (r.st === 'warn' || r.st === 'crit') ? (' st-' + r.st) : '';
      return '<tr><td>' + esc(r.k) + '</td>' +
        '<td class="r num' + vCls + '">' + r.v + '</td>' +
        '<td class="r">' + stCell + '</td></tr>';
    }).join('');

    /* 没有任何计数可读时给一句提示（fnmonitor 同款口径） */
    var anyCount = SMART_KEYS.some(function (k) { return smartCount(d, k[0]) !== null; });
    var hint = '';
    if (!anyCount) {
      hint = d && d.standby
        ? '休眠中，扇区计数待硬盘唤醒后读取'
        : '该硬盘未提供重映射 / 待映射扇区等计数（NVMe 或 smartctl 不可用）';
    }
    return '<table class="mon-dw-table"><thead><tr><th>属性</th>' +
      '<th class="r">值</th><th class="r">状态</th></tr></thead><tbody>' + body +
      (hint ? '<tr><td colspan="3" class="faint mon-dw-hint">' + esc(hint) + '</td></tr>' : '') +
      '</tbody></table>';
  }

  /* 键值行：fnmonitor 的 .kv 用 <div> 包裹一对 dt/dd（CSS 里 display:contents），
     这样「每行一条上边框、首行无线」的规则才能正确命中第一行。 */
  function dwKvRow(k, v) {
    return '<div><dt>' + esc(k) + '</dt><dd>' + (v === undefined || v === null ? '' : v) + '</dd></div>';
  }

  /* 信息表（型号 / 序列号 / 类型 / 容量 / 挂载点 / 所属空间）
     注：「硬盘状态」原本挂在实时读写的键值表里；改成趋势图后那一栏没了，
     这里补回信息表，信息不丢。 */
  function dwInfoKv(d) {
    var mounts = (d && d.mounts) || [];
    var kv = [
      ['型号', esc(vendorModel(d) || '未知型号')],
      ['序列号', d && d.serial ? '<span class="mono">' + esc(d.serial) + '</span>' : '<span class="faint">—</span>'],
      ['类型', esc(d && d.type ? d.type : '未知')],
      ['硬盘容量', esc(d && d.sizeText ? d.sizeText : fmtDisk((d && d.size) || 0))],
      ['硬盘状态', d && d.state === 'standby' ? '休眠' : esc(d && d.state ? d.state : '—')],
      ['挂载点', mounts.length
        ? '<span class="mono">' + esc(mounts.join('、')) + '</span>'
        : '<span class="faint">未挂载</span>'],
      ['所属存储空间', d && volOf(d) ? '存储空间 vol' + esc(volOf(d)) : '<span class="faint">—</span>']
    ];
    return '<dl class="mon-dw-kv">' + kv.map(function (p) {
      return dwKvRow(p[0], p[1]);
    }).join('') + '</dl>';
  }

  /* 分节标题（FnStyle 自有风格）：左侧 accent 竖条 + 标题文字，
     右侧可挂一行淡色补充说明（如「近 5 分钟」）。
     与主题库分组标题（.fgroup-title）同一套语言：竖条 + 字重 + 字距。 */
  function dwSec(title, extra) {
    return '<div class="mon-dw-sec">' +
      '<span class="mon-dw-sec-main"><i class="mon-dw-sec-bar"></i>' + esc(title) + '</span>' +
      (extra ? '<span class="faint">' + extra + '</span>' : '') +
      '</div>';
  }

  /* 打开抽屉：dev = 设备名（sda / sdc …） */
  function openDiskDrawer(dev) {
    var ov = monDrawerOverlay();
    if (!ov) return;
    var st = lastData && lastData.storage ? lastData.storage : null;
    var disks = (st && st.disks) || [];
    var d = null;
    for (var i = 0; i < disks.length; i++) {
      if (disks[i].name === dev) { d = disks[i]; break; }
    }
    var body = $('#monDrawerBody');
    var subEl = $('#monDrawerSub');
    var selEl = $('#monDrawerDiskSel');
    if (!body || !subEl || !selEl) return;

    /* 顶部下拉：列出所有硬盘，切换即换盘。
       选项文字用「存储空间 volN · 设备名」—— 物理位置 + 设备名都带上，
       避免同名盘（都是 vol1）分不清。 */
    selEl.innerHTML = disks.length
      ? disks.slice().sort(byVol).map(function (x) {
          return '<option value="' + esc(x.name) + '">' +
            esc(volLabel(x) + ' · ' + x.name) + '</option>';
        }).join('')
      : '<option value="">（无硬盘）</option>';

    /* 只有一块盘时不必给「可切换」的错觉 —— 触发器淡出边框 */
    var picker = selEl.parentNode;
    if (picker && picker.classList) {
      picker.classList.toggle('single', disks.length <= 1);
    }

    if (!d) {
      selEl.value = '';
      subEl.textContent = '未找到硬盘' + (dev ? ' · ' + dev : '');
      body.innerHTML = '<div class="mon-dw-note">它可能已被移除，或尚未被识别。</div>';
      showDrawer();
      return;
    }

    var led = bayLedState(d);
    var hasCap = Number(d.total) > 0;
    var pct = Number(d.percent) || 0;
    var io = d._io;

    /* 下拉选中当前盘；标题不再重复写「硬盘 …」，位置信息已在下拉里 */
    selEl.value = d.name;
    /* 副标题：设备名 · 型号（fnmonitor 同款结构） */
    subEl.textContent = [d.name, vendorModel(d)].filter(Boolean).join(' · ');

    var html = '';

    /* --- 容量大数字（fnmonitor 的 sheet-hero） --- */
    html += '<div class="mon-dw-hero"><div>' +
      '<div class="mon-dw-eyebrow">已用</div>' +
      '<div class="mon-dw-big"' +
      (hasCap && !d.standby ? ' style="color:' + rampInk(pct) + '"' : '') + '>' +
      (hasCap ? esc(String(Math.round(pct))) : '—') +
      '<span class="unit">%</span></div></div>' +
      '<div class="mon-dw-right">' +
      '<div class="mon-dw-eyebrow">可用</div>' +
      '<b>' + (hasCap ? esc(fmtDisk(Math.max(0, (Number(d.total) || 0) - (Number(d.used) || 0)))) : '—') + '</b>' +
      '</div></div>';

    /* --- 容量条：分段格条，每段按容量色阶单独着色（fnmonitor 的 .dbar） --- */
    var BAR_N = 40;
    var barHtml = '<div class="mon-dw-bar">';
    var lit = hasCap ? Math.round(Math.max(0, Math.min(1, pct / 100)) * BAR_N) : 0;
    for (var bi = 0; bi < BAR_N; bi++) {
      var on = bi < lit;
      var col = '';
      if (on) {
        col = d.standby ? 'var(--led-off)' : rampAt(((bi + .5) / BAR_N) * 100, capRampStops());
      }
      barHtml += '<i class="' + (on ? 'on' : '') + '"' +
        (col ? ' style="background:' + col + '"' : '') + '></i>';
    }
    barHtml += '</div>';
    html += '<div class="mon-dw-card">' + barHtml +
      '<div class="mon-dw-legend">' + (hasCap
        ? '<span>已用 <b>' + esc(d.usedText || fmtDisk(d.used || 0)) + '</b></span>' +
          '<span>可用 <b>' + esc(fmtDisk(Math.max(0, (Number(d.total) || 0) - (Number(d.used) || 0)))) + '</b></span>' +
          '<span>文件系统容量 <b>' + esc(d.totalText || fmtDisk(d.total || 0)) + '</b></span>'
        : '<span>未挂载或无用量数据</span><span>硬盘容量 <b>' + esc(d.sizeText || fmtDisk(d.size || 0)) + '</b></span>') +
      '</div></div>';

    /* --- 提示条：休眠 / 告警 / 温度（fnmonitor 的 .note，无边框纯底色） --- */
    var notes = '';
    if (d.standby) {
      notes += '<div class="mon-dw-note sleep">💤 <span>这块硬盘已停转休眠。' +
        'FnStyle 不会主动唤醒它：容量与温度为休眠前的最后读数，SMART 会在它下次被系统唤醒时更新。</span></div>';
    }
    if (led.lv === 'crit' || led.lv === 'warn') {
      var smText = smartAlertsText(led.smart);
      notes += '<div class="mon-dw-note ' + led.lv + '">⚠ <span><b>' +
        esc(GRADE_TXT[led.lv] || '告警') + '。</b>' +
        (smText ? 'SMART 告警：' + esc(smText) + '。' : '') +
        '建议确认 ' + esc(((d.mounts || []).join('、')) || '该盘上的数据') +
        ' 有备份，并留意数值是否继续增长。</span></div>';
    }
    /* 温度告警：必须用 tempExceed() 判定 —— tempLevel() 正常时返回 'ok'，
       而 'ok' 是真值，直接用 if (lv) 判断会让任何温度都弹这条提示。 */
    var t = (d.temp !== null && d.temp !== undefined && d.temp !== '') ? Number(d.temp) : null;
    var tEx = (d.standby || t === null) ? '' : tempExceed(t);
    if (tEx) {
      notes += '<div class="mon-dw-note warn">🌡 <span>温度 ' + esc(String(t)) +
        '℃，已超过' + (tEx === 'high' ? '严重' : '警告') + '阈值（' +
        (tEx === 'high' ? TH_DISK_TEMP_CRIT : TH_DISK_TEMP_WARN) + '℃）。</span></div>';
    }
    if (notes) html += '<div class="mon-dw-stack">' + notes + '</div>';

    /* --- 实时读写：对齐 fnmonitor —— 趋势图卡片，不是键值表 ---
       fnmonitor 的结构是 .chart-card：标题「读写速率」+ 读取/写入色标与实时数值
       + 64px 高的双线 spark。曲线数据来自前端滑动缓冲 diskBuf。
       底部时间锚点标出窗口起止（方案 A）：只占一行，不改 spark 高度，
       既能回答「这条曲线对应哪段时间」，又不破坏 fnmonitor 的 spark 观感。 */
    if (!d.standby) {
      var rTxt = io ? fmtRate(io.read_rate) : '--';
      var wTxt = io ? fmtRate(io.write_rate) : '--';
      html += dwSec('实时读写', '近 ' +
        Math.round(BUF_N * REFRESH_MS / 60000) + ' 分钟');
      html += '<div class="mon-dw-card mon-dw-chart-card">' +
        '<div class="mon-dw-chart-head">' +
        '<div class="mon-dw-chart-title">读写速率</div>' +
        '<div class="mon-dw-chart-legend">' +
        '<span><span class="lk"><i class="k0"></i>读取</span><span class="lv">' + esc(rTxt) + '</span></span>' +
        '<span><span class="lk"><i class="k1"></i>写入</span><span class="lv">' + esc(wTxt) + '</span></span>' +
        '</div></div>' +
        '<div class="mon-dw-spark"><svg class="mon-spark" id="monDwSpark" aria-hidden="true"></svg></div>' +
        '<div class="mon-dw-timeaxis">' +
        '<span id="monDwTimeFrom">--</span>' +
        '<span class="mon-dw-timearrow" aria-hidden="true">→</span>' +
        '<span id="monDwTimeTo">--</span>' +
        '</div>' +
        '</div>';
    }

    /* --- SMART --- */
    html += dwSec('SMART') +
      '<div class="mon-dw-card">' + dwSmartTable(d) + '</div>';

    /* --- 信息 --- */
    html += dwSec('信息') +
      '<div class="mon-dw-card">' + dwInfoKv(d) + '</div>';

    /* --- 巡检 --- */
    var scan = (st && st.scan) || {};
    var ago = scanAgo(scan.last_ts || 0);
    html += dwSec('巡检') +
      '<div class="mon-dw-card"><dl class="mon-dw-kv">' +
      dwKvRow('巡检分级', gradeBadge(led.scanLv)) +
      dwKvRow('巡检时间', ago ? esc(ago) : '<span class="faint">未巡检</span>') +
      (d.detail ? dwKvRow('巡检说明', esc(d.detail)) : '') +
      '</dl></div>';

    body.innerHTML = html;
    drawerDev = dev;
    showDrawer();
    // 抽屉滑入后再画 spark：此时容器已有实际宽度，画出来的曲线才准
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { drawDrawerSpark(); });
    });
  }

  /* 绘制抽屉内的读写趋势图（数据来自 diskBuf 滑动缓冲）
     同时更新底部的时间锚点（窗口起止两端）。 */
  function drawDrawerSpark() {
    var svg = $('#monDwSpark');
    if (!svg || !drawerDev) return;
    var buf = diskBuf[drawerDev];
    if (!buf || buf.r.length < 2) {
      svg.innerHTML = '<text class="mon-dw-spark-empty">正在积累读写速率数据…</text>';
      drawDrawerTimeAxis(buf);
      return;
    }
    drawSpark(svg, [{ values: buf.r }, { values: buf.w }]);
    drawDrawerTimeAxis(buf);
  }

  /* 时间锚点：标出曲线窗口的起止时刻。
     只有两个刻度（左端起点、右端终点），避免在 64px 高的 spark 下方
     塞满刻度导致拥挤 —— 这也正是 fnmonitor 带轴图表用 180px 的原因。 */
  function drawDrawerTimeAxis(buf) {
    var fromEl = $('#monDwTimeFrom');
    var toEl = $('#monDwTimeTo');
    if (!fromEl || !toEl) return;
    if (!buf || !buf.t || !buf.t.length) {
      fromEl.textContent = '--';
      toEl.textContent = '--';
      return;
    }
    fromEl.textContent = fmtHM(buf.t[0]);
    toEl.textContent = fmtHM(buf.t[buf.t.length - 1]);
  }

  function showDrawer() {
    var ov = monDrawerOverlay();
    if (!ov) return;
    ov.hidden = false;
    /* 只在「从关到开」时加锁 —— 下拉切换硬盘会重走 openDiskDrawer，
       那里也会调 showDrawer，不能重复加锁。 */
    if (!drawerOpen) lockPageScroll();
    drawerOpen = true;
    // 下一帧再加 .on，确保过渡动画能触发
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { ov.classList.add('on'); });
    });
  }

  function closeDiskDrawer() {
    var ov = monDrawerOverlay();
    if (!ov) return;
    ov.classList.remove('on');
    if (drawerOpen) unlockPageScroll();
    drawerOpen = false;
    drawerDev = null;
    // 等滑出动画结束再隐藏
    setTimeout(function () {
      if (!drawerOpen) ov.hidden = true;
    }, 320);
  }

  /* 事件绑定：盘位点击 / 关闭 / 遮罩 / ESC / 顶部下拉切换硬盘 */
  function bindDrawer() {
    var ov = monDrawerOverlay();
    if (!ov || ov.__bound) return;
    ov.__bound = true;

    // 点遮罩关闭（点在抽屉内部不关）
    ov.addEventListener('click', function (e) {
      if (e.target === ov) closeDiskDrawer();
    });
    var btn = $('#btnCloseMonDrawer');
    if (btn) btn.addEventListener('click', closeDiskDrawer);

    /* 顶部下拉换盘：切到另一块盘就重绘整个抽屉。
       重绘而不是局部更新 —— 容量/分级/SMART 三块内容都要跟着换，
       局部更新比全量重绘更容易漏改。 */
    var sel = $('#monDrawerDiskSel');
    if (sel) {
      sel.addEventListener('change', function () {
        var v = sel.value;
        if (!v || v === drawerDev) return;
        openDiskDrawer(v);
      });
    }

    document.addEventListener('keydown', function (e) {
      if ((e.key === 'Escape' || e.key === 'Esc') && drawerOpen) {
        e.preventDefault();
        closeDiskDrawer();
      }
    });
  }

  function renderStorage(st) {
    var box = $('#monStorage');
    if (!box) return;
    var cntEl = $('#monStoreCount');
    if (!st || !st.available || (!st.volumes || !st.volumes.length) &&
        (!st.disks || !st.disks.length)) {
      box.innerHTML = emptyBox('未检测到存储池（/vol* 挂载点不可见）');
      if (cntEl) cntEl.textContent = '不可用';
      return;
    }

    // 建立设备名 → IO 速率 的映射
    var ioMap = {};
    (st.diskio || []).forEach(function (z) { ioMap[z.name] = z; });
    (st.disks || []).forEach(function (d) { d._io = ioMap[d.name] || null; });

    var sum = st.summary || {};
    var html = '';

    /* --- 存储池使用率总览（参照 fnmonitor 的 .card.pool）---
       左：使用率大数字；中：进度条 + 图例（已用 / 总容量 / 池数）；
       右：可用空间大数字。 */
    var gradeCounts = sum.grades || {};
    // summary 由后端聚合；缺失时直接数数组兜底，避免显示成「0 个存储池」
    var poolN = (sum.poolCount !== undefined && sum.poolCount !== null)
      ? sum.poolCount : (st.volumes || []).length;
    var diskN = (sum.diskCount !== undefined && sum.diskCount !== null)
      ? sum.diskCount : (st.disks || []).length;

    // 容量合计：优先用后端 summary，缺失时从存储池累加
    var totText = sum.totalText, usedText = sum.usedText, freeText = sum.freeText;
    var totBytes = sum.total, usedBytes = sum.used, freeBytes = sum.free;
    if (!totText) {
      var tSum = 0, uSum = 0, fSum = 0;
      (st.volumes || []).forEach(function (v) {
        tSum += v.total || 0; uSum += v.used || 0; fSum += v.free || 0;
      });
      if (tSum) {
        totBytes = tSum; usedBytes = uSum; freeBytes = fSum;
        totText = fmtDisk(tSum); usedText = fmtDisk(uSum); freeText = fmtDisk(fSum);
      }
    }
    var poolPct = (sum.percent !== undefined && sum.percent !== null)
      ? Number(sum.percent) : (totBytes ? Math.round(usedBytes / totBytes * 100) : 0);
    var poolLv = capLevel(poolPct);

    /* 进度条：填充段按容量色阶从绿渐变到当前值位置（越满越"热"）。
       实现同 fnmonitor 的 rampBar —— 渐变铺满整条轨道（色标位置 = 数值百分比），
       再用 background-size 把「已填充的那一段」露出来。
       关键：big = 100/len×100%，使渐变宽度恒等于整条轨道宽度，
       于是填充段自身就是「从绿到当前使用率对应色」的连续渐变。 */
    var poolLen = Math.max(0, Math.min(100, poolPct));
    var poolBig = (100 / Math.max(poolLen, .5) * 100).toFixed(1) + '%';
    var poolBarStyle = 'width:' + poolLen + '%;background-image:' + rampGrad() +
      ';background-size:' + poolBig + ' 100%;background-position:left center';

    html += '<div class="mon-pool">' +
      '<div>' +
        '<div class="mon-pool-eyebrow">存储池使用率</div>' +
        '<div class="mon-pool-num" style="color:' + rampInk(poolPct) + '">' +
          esc(poolPct) + '<span class="unit">%</span></div>' +
      '</div>' +
      '<div>' +
        '<div class="mon-pool-bar"><i style="' + poolBarStyle + '"></i></div>' +
        '<div class="mon-pool-legend">' +
          '<span>已用 <b>' + esc(usedText || '—') + '</b></span>' +
          '<span>总容量 <b>' + esc(totText || '—') + '</b></span>' +
          (poolN ? '<span>' + esc(poolN) + ' 个存储池</span>' : '') +
          (diskN ? '<span>' + esc(diskN) + ' 块硬盘</span>' : '') +
          (sum.sleepingCount ? '<span>' + esc(sum.sleepingCount) + ' 块休眠</span>' : '') +
          (gradeCounts.warn ? '<span class="mon-lv-warn">' + esc(gradeCounts.warn) + ' 块需留意</span>' : '') +
          (gradeCounts.crit ? '<span class="mon-lv-crit">' + esc(gradeCounts.crit) + ' 块故障</span>' : '') +
        '</div>' +
      '</div>' +
      '<div class="mon-pool-free">' +
        '<div class="mon-pool-eyebrow">可用空间</div>' +
        '<div class="mon-pool-num sm">' + esc(freeText || '—') + '</div>' +
      '</div>' +
    '</div>';

    /* --- NAS 前面板：每块物理盘一个盘位卡片 --- */
    if (st.disks && st.disks.length) {
      var scan = st.scan || {};
      var lastScanTs = scan.last_ts || 0;
      var ago = scanAgo(lastScanTs);

      var bays = st.disks.slice().sort(byVol).map(function (d) {
        var kind = diskKind(d);
        /* LED 状态 = 巡检分级 与 SMART 告警取更严重者：
           巡检说「正常」但 SMART 有非零计数时，不能让绿灯把告警盖住。 */
        var led = bayLedState(d);
        var lv = led.lv;
        var hasCap = Number(d.total) > 0;
        var pct = Number(d.percent) || 0;
        var gv = hasCap ? pct / 100 : 0;
        /* 容量等级与健康分级是两件事：容量满（要留意容量）≠ 盘坏了（健康故障）。
           角标与容量行只看容量等级；LED 与 title 看健康分级。 */
        var capLv = capLevel(pct);
        var cols = (kind === 'nvme') ? 4 : 5;

        var t = d.temp;
        var tTxt = (d.standby || t === null || t === undefined || t === '') ? '--' : t;
        var tLv = (d.standby || t === null || t === undefined) ? '' : diskTempLevel(t);

        // 容量行：已挂载显示占用率 + 已用/总量；未挂载显示裸容量
        var capTxt = hasCap
          ? Math.round(pct) + '% · ' + (d.usedText || fmtDisk(d.used || 0)) +
            ' / ' + (d.totalText || fmtDisk(d.total || 0))
          : (d.sizeText || '—') + ' · 未挂载';

        // 实时 IO：整合原「物理磁盘」表里的读写列
        var io = d._io;
        var ioTxt = io
          ? '↓' + (io.readText || '—') + ' ↑' + (io.writeText || '—')
          : '';

        // SMART 告警明细（若有），直接拼进 title —— 悬停 LED/盘位即知是哪一项
        var smText = smartAlertsText(led.smart);
        var title = d.name + '（' + volLabel(d) + '） · ' +
          (GRADE_TXT[led.scanLv] || '未巡检') +
          (d.detail ? '：' + d.detail : '') +
          (smText ? ' · SMART 告警：' + smText : '') +
          (hasCap ? ' · 已用 ' + Math.round(pct) + '%' : '') +
          (ago ? '（巡检于 ' + ago + '）' : '') +
          (capLv ? ' · 容量占用超 ' + (capLv === 'crit' ? 95 : 85) + '%' : '');

        return '<div class="mon-bay" data-st="' + esc(lv) + '" data-kind="' + esc(kind) +
          '" data-dev="' + esc(d.name) + '" data-clickable="1" tabindex="0"' +
          ' role="button" aria-label="' + esc(d.name + ' ' + volLabel(d) + ' 详情') + '"' +
          ' title="' + esc(title) + '">' +
          '<div class="mon-bay-top">' +
            '<span>' + esc(volLabel(d)) + '</span>' +
            '<i class="mon-bay-led"' +
              (smText ? ' title="' + esc(smText) + '"' : '') + '></i>' +
          '</div>' +
          '<div class="mon-bay-body">' +
            '<span class="mon-bay-gauge">' +
              gaugeSvg(cols, 16, gv) +
            '</span>' +
            '<span class="mon-bay-zz" aria-hidden="true">Zz</span>' +
            '<span class="mon-bay-info">' +
              '<span class="mon-bay-temp' + (tLv ? ' lv-' + tLv : '') + '">' +
                esc(tTxt) + '<small>℃</small></span>' +
              '<span class="mon-bay-name">' + esc(d.name) + '</span>' +
              '<span class="mon-bay-model">' + esc(vendorModel(d) || '未知型号') + '</span>' +
              '<span class="mon-bay-cap' + (capLv ? ' lv-' + capLv : '') + '"' +
                (capLv ? ' style="color:' + rampInk(pct) + '"' : '') + '>' + esc(capTxt) + '</span>' +
              (ioTxt ? '<span class="mon-bay-io">' + esc(ioTxt) + '</span>' : '') +
            '</span>' +
          '</div>' +
          '<div class="mon-bay-grip"></div>' +
        '</div>';
      }).join('');

      var sys = st._system || null;
      var chName = '', chModel = 'NAS';
      if (sys) {
        chName = sys.hostname || '';
        // productName 可能是 DMI 占位串（后端已过滤），这里再兜一层
        chModel = (sys.productName || sys.boardName || 'NAS').slice(0, 24) || 'NAS';
      }

      html += '<div class="mon-nas">' +
        '<div class="mon-chassis">' +
          '<div class="mon-ch-top">' +
            '<span>' + esc(chName) + '</span>' +
            '<span class="vent"></span>' +
            '<span>' + esc(chModel) + ' · ' + esc(st.disks.length) + ' 盘</span>' +
          '</div>' +
          '<div class="mon-bays" style="--bay-cols:' + Math.min(Math.max(st.disks.length, 1), 4) + '">' +
            bays +
          '</div>' +
        '</div>' +
        '<div class="mon-nas-foot">' +
          '<span><i class="led-key"></i>正常</span>' +
          '<span><i class="led-key warn"></i>留意</span>' +
          '<span><i class="led-key crit"></i>故障</span>' +
          '<span><i class="led-key off"></i>休眠 / 未巡检</span>' +
          '<span class="scan-ago">' +
            (lastScanTs ? (ago ? '巡检于 ' + esc(ago) : '巡检中') : '尚未巡检') +
          '</span>' +
          '<span class="hint">盘位按存储空间排列</span>' +
        '</div>' +
      '</div>';

      /* SMART 明细（每盘 5 项告警计数）—— 已移除独立的「SMART 告警计数」卡片：
         盘位卡片的 LED 已反映每盘健康状态，硬盘详情抽屉里有完整的 SMART 明细表，
         单独再列一张表属于重复呈现。 */
    }

    /* --- 软 RAID --- */
    if (st.raid && st.raid.length) {
      var rrows = st.raid.map(function (r) {
        var cls = (r.state && r.state.indexOf('active') === 0) ? 'ok' : 'warn';
        var members = (r.members || []).map(function (m) {
          var bad = m.indexOf('(F)') >= 0;
          return '<span class="mon-chip ' + (bad ? 'crit' : 'ok') + '">' + esc(m) + '</span>';
        }).join('');
        return '<div class="mon-row">' +
          '<span class="mon-row-name">' + esc(r.device || r.name || 'md') + '</span>' +
          '<span class="mon-badge ' + cls + '">' + esc(r.state || '未知') + '</span>' +
          '<span class="mon-row-val">' + r.active + '/' + r.total + '</span>' +
          (members ? '<div class="mon-chips">' + members + '</div>' : '') +
        '</div>';
      }).join('');
      html += item('软 RAID', st.raid.length + ' 组',
        '<div class="mon-list">' + rrows + '</div>');
    }

    /* --- 硬 RAID 卡（storcli） --- */
    var rc = st.raidcard || {};
    if (rc.type && rc.type !== 'none') {
      var rcHtml = '';
      if (rc.type === 'megaraid') {
        (rc.controllers || []).forEach(function (c) {
          var vds = (c.vd || []).map(function (v) {
            var cls = v.badge === 'off' ? 'crit' : (v.badge === 'on' ? 'ok' : 'unknown');
            return '<span class="mon-chip ' + cls + '">VD' + esc(v.vd) + ' ' +
              esc(v.type || '') + ' · ' + esc(v.state_label || v.state || '--') + '</span>';
          }).join('');
          var pds = (c.pds || []).map(function (p) {
            var cls = p.health === 'ok' ? 'ok' : (p.health === 'warn' ? 'warn' : 'crit');
            return '<span class="mon-chip ' + cls + '">插槽 ' + esc(p.loc) + ' ' +
              esc(p.model || '') + (p.temp != null ? ' ' + p.temp + '°C' : '') + '</span>';
          }).join('');
          rcHtml += '<div class="mon-row"><span class="mon-row-name">' +
            esc(c.model || 'Controller ' + esc(c.id)) + '</span>' +
            '<span class="mon-row-val">' + esc(c.status || '') +
            (c.roc_temp != null ? ' · 芯片 ' + c.roc_temp + '℃' : '') + '</span>' +
            (vds ? '<div class="mon-chips">' + vds + '</div>' : '') +
            (pds ? '<div class="mon-chips">' + pds + '</div>' : '') +
            '</div>';
        });
      }
      if (!rcHtml && rc.detail) {
        rcHtml = '<div class="mon-note">' + esc(rc.detail) + '</div>';
      }
      html += item('阵列卡', esc(rc.label || rc.type),
        rcHtml ? '<div class="mon-list">' + rcHtml + '</div>' : '',
        rc.error ? 'mon-item-warn' : '');
    }

    /* --- 巡检历史 ---
       已移除独立的「硬盘巡检」卡片：巡检记录改由标题栏的
       「🗂 巡检历史」按钮打开右抽屉查看（renderScanLogHtml），
       监控面板只保留盘位卡片与「巡检于 …」的时间提示。 */

    box.innerHTML = html;
    if (cntEl) {
      // summary 是后端聚合的，但兜底直接数数组 —— summary 缺失时不能显示成「0 个」
      var poolN = (sum.poolCount !== undefined && sum.poolCount !== null)
        ? sum.poolCount : (st.volumes || []).length;
      var diskN = (sum.diskCount !== undefined && sum.diskCount !== null)
        ? sum.diskCount : (st.disks || []).length;
      cntEl.textContent = poolN + ' 个存储池 · ' + diskN + ' 块硬盘';
    }

    /* 盘位点击 → 打开右侧详情抽屉。
       用事件委托绑在容器上：存储面板每次轮询都会重绘 innerHTML，
       直接给每个盘位绑监听会反复叠加。
       closest 在个别老内核上对 SVG 元素不生效（盘位里有 gauge SVG），
       所以找不到时退回手动向上找 .mon-bay。 */
    if (!box.__bayBound) {
      box.__bayBound = true;
      var findBay = function (el) {
        while (el && el !== box) {
          if (el.classList && el.classList.contains('mon-bay') && el.getAttribute('data-dev')) {
            return el;
          }
          el = el.parentNode;
        }
        return null;
      };
      box.addEventListener('click', function (e) {
        var bay = (e.target.closest ? e.target.closest('.mon-bay[data-dev]') : null) || findBay(e.target);
        if (!bay) return;
        openDiskDrawer(bay.getAttribute('data-dev'));
      });
      box.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var bay = (e.target.closest ? e.target.closest('.mon-bay[data-dev]') : null) || findBay(e.target);
        if (!bay) return;
        e.preventDefault();
        openDiskDrawer(bay.getAttribute('data-dev'));
      });
    }
    bindDrawer();
    bindScanLogDrawer();
  }

  /* ---------------- 2. 处理器 / 内存 / 显卡 ---------------- */

  function renderCore(d) {
    var box = $('#monCore');
    if (!box) return;
    var cpu = d.cpu || {}, mem = d.mem || {}, gpu = d.gpu || {};
    var html = '';

    /* CPU */
    if (cpu.available) {
      var sub = '';
      if (cpu.coresPercent && cpu.coresPercent.length) {
        var cores = cpu.coresPercent.map(function (p) {
          return '<div class="mon-core" title="核心负载 ' + p + '%">' +
            '<div class="mon-core-fill" style="height:' + Math.min(100, p) + '%"></div></div>';
        }).join('');
        sub = '<div class="mon-cores">' + cores + '</div>';
      }
      var la = (cpu.loadavg && cpu.loadavg.length) ? cpu.loadavg.join(' / ') : '';
      html += item(
        'CPU',
        cpu.percent + '%',
        bar(cpu.percent) + sub +
        '<div class="mon-item-sub">' + esc(cpu.model || '未知型号') +
        ' · ' + (cpu.cores || '?') + ' 核' +
        (cpu.freqText ? ' · ' + esc(cpu.freqText) : '') +
        (la ? ' · 负载 ' + esc(la) : '') + '</div>'
      );
    } else {
      html += item('CPU', '—', '<div class="mon-item-sub">无法读取 /proc/stat</div>');
    }

    /* 内存 */
    if (mem.available) {
      html += item(
        '内存',
        mem.percent + '%',
        bar(mem.percent) +
        '<div class="mon-item-sub">已用 <span>' + esc(mem.usedText) + '</span> / 共 ' +
        esc(mem.totalText) + '，可用 <span>' + esc(mem.availText) + '</span>' +
        (mem.swapTotal ? ' · Swap ' + fmtBytes(mem.swapUsed) + '/' + fmtBytes(mem.swapTotal) : '') +
        '</div>'
      );
    } else {
      html += item('内存', '—', '<div class="mon-item-sub">无法读取 /proc/meminfo</div>');
    }

    /* 显卡 */
    if (gpu.available && gpu.gpus && gpu.gpus.length) {
      gpu.gpus.forEach(function (g) {
        var detail = [];
        if (g.util !== null && g.util !== undefined) {
          detail.push('负载 ' + g.util + '%');
        }
        if (g.temp !== null && g.temp !== undefined) {
          detail.push('温度 ' + g.temp + '°C');
        }
        if (g.memTotal) {
          detail.push('显存 ' + fmtBytes(g.memUsed) + ' / ' + fmtBytes(g.memTotal));
        }
        if (g.power !== null && g.power !== undefined) {
          detail.push('功耗 ' + g.power + ' W');
        }
        var val = (g.util !== null && g.util !== undefined) ? g.util + '%' : '—';
        var head = g.vendor && g.vendor !== 'GPU' ? g.vendor + ' 显卡' : '显卡';
        html += item(head, val,
          (g.utilAvailable ? bar(g.util) : '') +
          '<div class="mon-item-sub">' + esc(g.name || '') +
          (detail.length ? '<br>' + esc(detail.join(' · ')) : '') + '</div>');
      });
    } else {
      html += item('显卡', '—', '<div class="mon-item-sub">未检测到独立显卡（核显或虚拟机环境可能无此类设备）</div>');
    }

    box.innerHTML = html;
    var n = (cpu.available ? 1 : 0) + (mem.available ? 1 : 0) + (gpu.available ? gpu.gpus.length : 0);
    $('#monCoreCount').textContent = n ? n + ' 项可用' : '不可用';
  }

  /* ---------------- 3. 温度和散热 ---------------- */

  function renderTemp(d) {
    var box = $('#monTemp');
    if (!box) return;
    var t = d.temp || {};
    var html = '';

    if (t.temps && t.temps.length) {
      var rows = t.temps.map(function (s) {
        var lv = tempLevel(s.value);
        var extra = '';
        if (s.max) extra += ' · 上限 ' + s.max + '°C';
        if (s.crit) extra += ' · 临界 ' + s.crit + '°C';
        return '<div class="mon-row">' +
          '<span class="mon-row-name" title="' + esc(s.chip || '') + '">' + esc(s.name) + '</span>' +
          '<span class="mon-temp"><span class="mon-temp-dot lv-' + lv + '"></span>' +
          '<span class="mon-row-val">' + s.value + '°C</span></span>' +
        '</div>';
      }).join('');
      html += item('温度传感器', t.temps.length + ' 个',
        '<div class="mon-list">' + rows + '</div>');
    } else {
      html += item('温度传感器', '—',
        '<div class="mon-item-sub">未检测到温度传感器（<span>/sys/class/thermal</span> 与 <span>hwmon</span> 均无数据）</div>');
    }

    if (t.fans && t.fans.length) {
      var frows = t.fans.map(function (f) {
        return '<div class="mon-row">' +
          '<span class="mon-row-name">' + esc(f.name) + '</span>' +
          '<span class="mon-row-val">' + f.rpm + ' RPM</span>' +
        '</div>';
      }).join('');
      html += item('风扇', t.fans.length + ' 个',
        '<div class="mon-list">' + frows + '</div>');
    } else {
      html += item('风扇', '—',
        '<div class="mon-item-sub">未检测到风扇转速数据</div>');
    }

    // 硬盘温度
    if (t.disks && t.disks.length) {
      var withTemp = t.disks.filter(function (x) { return x.temp !== null && x.temp !== undefined; });
      if (withTemp.length) {
        var drows = withTemp.map(function (x) {
          return '<div class="mon-row">' +
            '<span class="mon-row-name">' + esc(x.name) + '</span>' +
            '<span class="mon-temp"><span class="mon-temp-dot lv-' + diskTempLevel(x.temp) + '"></span>' +
            '<span class="mon-row-val">' + x.temp + '°C</span></span>' +
          '</div>';
        }).join('');
        html += item('硬盘温度', withTemp.length + ' 块',
          '<div class="mon-list">' + drows + '</div>');
      }
    }

    box.innerHTML = html;
    $('#monTempCount').textContent = t.available ? '传感器在线' : '不可用';
  }

  /* ---------------- 4. 能耗 ---------------- */

  function renderPower(d) {
    var box = $('#monPower');
    if (!box) return;
    var p = d.power || {};
    var html = '';

    if (!p.ok) {
      html += item('整机功耗', '—',
        '<div class="mon-item-sub">功耗数据不可用（既无 RAPL 实测，也无法按 TDP 估算）</div>');
      $('#monPowerSource').textContent = '不可用';
      box.innerHTML = html;
      return;
    }

    var isRapl = p.source === 'rapl';
    html += item(
      '整机功耗',
      (p.total === null || p.total === undefined ? '—' : p.total + ' W'),
      '<div class="mon-item-sub">' +
        (isRapl
          ? '来源：<span>RAPL 实测</span>（package ' + (p.package === null || p.package === undefined ? '—' : p.package + ' W') +
            (p.core !== null && p.core !== undefined ? ' · core ' + p.core + ' W' : '') +
            (p.dram !== null && p.dram !== undefined ? ' · dram ' + p.dram + ' W' : '') + '）'
          : '来源：<span>TDP 模型估算</span>（基准 ' + (p.tdp || '—') + ' W）') +
        '<br>主板基线 <span>' + (p.base === null || p.base === undefined ? '—' : p.base + ' W') + '</span>' +
        ' · 硬盘 <span>' + (p.disksW === null || p.disksW === undefined ? '—' : p.disksW + ' W') + '</span>' +
        (p.disksActive ? '（' + p.disksActive + ' 块）' : '') +
        (p.nics !== undefined && p.nics !== null ? ' · 网卡 ' + p.nics + ' 口' : '') +
      '</div>'
    );

    if (isRapl) {
      // RAPL 各域明细
      var rows = [];
      [['package', '封装 (Package)'], ['core', '核心 (Core)'], ['uncore', '非核心 (Uncore)'], ['dram', '内存 (DRAM)']]
        .forEach(function (pair) {
          var v = p[pair[0]];
          if (v === null || v === undefined) return;
          rows.push('<div class="mon-row"><span class="mon-row-name">' + pair[1] +
            '</span><span class="mon-row-val">' + v + ' W</span></div>');
        });
      if (rows.length) {
        html += item('RAPL 功耗明细', '—',
          '<div class="mon-list">' + rows.join('') + '</div>');
      }
    }

    box.innerHTML = html;
    $('#monPowerSource').textContent = isRapl ? 'RAPL 实测' : 'TDP 估算';
  }

  /* ---------------- 总渲染 ---------------- */

  function render(d) {
    if (!d || d.success === false) {
      var msg = (d && (d.error || d.message)) || '监控服务不可用';
      ['monStorage', 'monCore', 'monTemp', 'monPower'].forEach(function (id) {
        var b = $('#' + id);
        if (b) b.innerHTML = emptyBox(msg);
      });
      setStatus('err', msg);
      return;
    }
    if (d.warming) {
      setStatus('', d.message || '监控数据采集中，请稍候…');
      return;
    }
    // system（主机名 / 型号）挂在顶层，转给存储板块渲染机箱顶栏
    if (d.storage && typeof d.storage === 'object') {
      d.storage._system = d.system || null;
    }
    renderStorage(d.storage);
    renderCore(d);
    renderTemp(d);
    renderPower(d);

    var st = $('#monStatus');
    if (st) {
      var okCount = 0;
      if (d.cpu && d.cpu.available) okCount++;
      if (d.mem && d.mem.available) okCount++;
      if (d.storage && d.storage.available) okCount++;
      if (d.temp && d.temp.available) okCount++;
      if (d.gpu && d.gpu.available) okCount++;
      if (d.power && d.power.ok) okCount++;
      setStatus('ok', '监控运行中 · ' + okCount + ' 项数据可用');
    }
    var up = $('#monUpdated');
    if (up) {
      var ts = d.ts ? new Date(d.ts * 1000) : null;
      var timeStr = ts ? ts.toLocaleTimeString('zh-CN', { hour12: false }) : '';
      var uptime = d.uptime && d.uptime !== '—' ? (' · 运行 ' + d.uptime) : '';
      up.textContent = (timeStr ? '更新于 ' + timeStr : '') + uptime;
    }
  }

  function setStatus(cls, text) {
    var el = $('#monStatus');
    if (!el) return;
    el.className = 'mon-status' + (cls ? ' ' + cls : '');
    el.textContent = text;
  }

  /* ---------------- 数据拉取 ---------------- */

  function fetchMonitor() {
    return fetch(BASE + 'monitor', { method: 'GET', headers: {} })
      .then(function (r) {
        return r.text().then(function (txt) {
          try { return JSON.parse(txt); }
          catch (e) { return { success: false, error: '响应解析失败：' + txt.slice(0, 120) }; }
        });
      })
      .catch(function () {
        return { success: false, error: '后端服务不可用，请确认应用已启动' };
      });
  }

  function refresh() {
    return fetchMonitor().then(function (d) {
      lastData = d;
      // 把本次采样的读写速率推进趋势图缓冲（fnmonitor 的 feed() 同款位置）
      // 去重时间戳优先用 storage.ts（采集时刻的浮点值，唯一性最强），
      // 顶层 ts（整秒）作兜底
      feedDiskBuf(d && d.storage, (d && d.storage && d.storage.ts) || (d && d.ts));
      render(d);
      // 抽屉开着时同步刷新趋势图与实时速率数字
      if (drawerOpen) refreshDrawerLive();
      // 巡检历史抽屉开着时同步刷新流水
      if (scanLogOpen) refreshScanLogLive();
      return d;
    });
  }

  /* 巡检历史抽屉开着时，只重绘内容区（保持滚动位置）。 */
  function refreshScanLogLive() {
    var body = $('#monScanLogBody');
    if (!body) return;
    var st = lastData && lastData.storage ? lastData.storage : null;
    body.innerHTML = renderScanLogHtml((st && st.scan) || {}, (st && st.scanLog) || []);
  }

  /* 抽屉开着时，只更新「实时读写」这一段（趋势图 + 实时速率），不重建整个抽屉
     —— 重建会把滚动位置和动画都打断，体验很差。 */
  function refreshDrawerLive() {
    var body = $('#monDrawerBody');
    if (!body || !drawerDev) return;
    var st = lastData && lastData.storage ? lastData.storage : null;
    var disks = (st && st.disks) || [];
    var d = null;
    for (var i = 0; i < disks.length; i++) {
      if (disks[i].name === drawerDev) { d = disks[i]; break; }
    }
    if (!d) return;
    var io = d._io;
    var lvs = $$('.mon-dw-chart-legend .lv');
    if (lvs.length >= 2) {
      lvs[0].textContent = io ? fmtRate(io.read_rate) : '--';
      lvs[1].textContent = io ? fmtRate(io.write_rate) : '--';
    }
    drawDrawerSpark();
  }

  /* 手动触发硬盘巡检：POST /api/storage/scan
     巡检要跑 smartctl，十几秒起步，所以期间按钮置灰并显示进度。 */
  function runDiskScan() {
    var btn = $('#btnMonScan');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ 巡检中…';
    }
    setStatus('', '正在巡检硬盘 SMART…');
    return fetch(BASE + 'storage/scan', { method: 'POST', headers: {} })
      .then(function (r) {
        return r.text().then(function (txt) {
          try { return JSON.parse(txt); }
          catch (e) { return { success: false, error: '响应解析失败：' + txt.slice(0, 120) }; }
        });
      })
      .then(function (d) {
        if (btn) {
          btn.disabled = false;
          btn.textContent = '🔍 立即巡检';
        }
        if (d && d.success) {
          var r = d.result || {};
          var c = r.counts || {};
          setStatus('ok', '巡检完成：正常 ' + (c.ok || 0) + ' · 留意 ' + (c.warn || 0) +
            ' · 故障 ' + (c.crit || 0) + ' · 未知 ' + (c.unknown || 0));
        } else {
          setStatus('err', '巡检失败：' + ((d && d.error) || '未知原因'));
        }
        refresh();
        return d;
      })
      .catch(function () {
        if (btn) {
          btn.disabled = false;
          btn.textContent = '🔍 立即巡检';
        }
        setStatus('err', '巡检请求失败：后端服务不可用');
      });
  }

  function startAuto() {
    stopAuto();
    timer = setInterval(function () {
      // 页面在后台时不必频繁请求（浏览器本身会节流，但仍避免无谓开销）
      if (document.hidden) return;
      refresh();
    }, REFRESH_MS);
  }

  function stopAuto() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  /* ---------------- 初始化 ---------------- */

  function init() {
    if (inited) return;
    inited = true;

    var btn = $('#btnMonRefresh');
    if (btn) {
      btn.addEventListener('click', function () {
        setStatus('', '正在刷新…');
        refresh();
      });
    }
    var scanBtn = $('#btnMonScan');
    if (scanBtn) {
      scanBtn.addEventListener('click', function () {
        runDiskScan();
      });
    }
    /* 巡检历史抽屉：即使还没拉到数据也要能打开（显示"尚未巡检"占位） */
    bindScanLogDrawer();
    var auto = $('#monAuto');
    if (auto) {
      auto.addEventListener('change', function () {
        if (auto.checked) { refresh(); startAuto(); }
        else { stopAuto(); setStatus('ok', '自动刷新已暂停'); }
      });
    }

    /* 切到监控标签时开始轮询；切走时停掉（离开标签就没必要继续拉数据）。
       ⚠️ 这里自己监听 .tab 的点击，而不是让 app.js 的 switchTab 来回调 ——
          两个脚本都是普通 <script>，app.js 先加载，若 app.js 在 init() 里
          直接调用 monitor 的接口，会撞上 monitor.js 尚未定义 window.FnStyleMonitor
          的时序问题。自己监听彻底避开这个坑。 */
    $$('.tab').forEach(function (t) {
      t.addEventListener('click', function () {
        if (t.getAttribute('data-tab') === 'monitor') {
          if (auto && auto.checked) { refresh(); startAuto(); }
        } else {
          stopAuto();
        }
      });
    });

    // 支持 #monitor 直达
    if (location.hash === '#monitor') {
      refresh();
      if (auto && auto.checked) startAuto();
    }

    // 页面隐藏时暂停轮询，可见时恢复 —— 省电也避免后台堆积
    document.addEventListener('visibilitychange', function () {
      var auto = $('#monAuto');
      if (!auto || !auto.checked) return;
      if (document.hidden) stopAuto();
      else { refresh(); startAuto(); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 暴露给外部（如 app.js）手动触发刷新
  window.FnStyleMonitor = {
    refresh: refresh,
    start: function () { refresh(); startAuto(); },
    stop: stopAuto
  };
})();
