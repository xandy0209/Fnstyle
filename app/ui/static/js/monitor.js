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
  /* 用电统计（/api/power/stats）与每日用电（/api/power/daily）。
     这两个接口较重（要扫历史库），所以只在能耗板块可见时拉一次，
     拉到的结果缓存在这里供总览四格与柱状图共用。 */
  var pstats = null;           // /api/power/stats 的结果
  var pdaily = null;           // /api/power/daily 的结果
  var pstatsAt = 0;            // 上次拉取时间（毫秒）
  var pdailyAt = 0;
  /* v1.1.50：失败时间戳 —— 请求失败后进入冷却，避免每 3 秒重试打接口 */
  var pstatsFailAt = 0;
  var pdailyFailAt = 0;
  var PSTAT_TTL = 60000;       // 60 秒刷新一次即可（能耗是慢变量）
  var PDaily_TTL = 600000;     // 10 分钟刷新一次（每日用电只按天变）
  /* ⚠️ v1.1.53：每日用电图的请求窗口天数（对齐 fnmonitor 的 DAY_BARS=30）。
     注意：标题不再固定写这个数 —— fnmonitor 的标题是「近 N 天用电」，
     N = **实际有数据的天数**（从第一个有数据的天起算），没有数据时写
     「近 30 天用电」。所以 PDaily_DAYS 只是请求天数，不参与标题计算。 */
  var PDaily_DAYS = 30;

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

  /* 数值格式化（与 compute.js 里的同名函数保持一致的口径）——
     温度和能耗板块要用到一位/两位小数与整数。 */
  function fmtInt(v) {
    var n = Number(v);
    return isNaN(n) ? '—' : String(Math.round(n));
  }
  function fmt1(v) {
    var n = Number(v);
    return isNaN(n) ? '—' : n.toFixed(1);
  }
  function fmt2(v) {
    var n = Number(v);
    return isNaN(n) ? '—' : n.toFixed(2);
  }

  /* 百分比 → 等级（用于条形/温度着色）。warn 从 70% 起，high 从 90% 起。 */
  function level(pct, warnAt, highAt) {
    if (pct === null || pct === undefined || isNaN(pct)) return 'ok';
    if (pct >= highAt) return 'high';
    if (pct >= warnAt) return 'warn';
    return 'ok';
  }

  /* 温度阈值（v1.1.79：改为从统一阈值配置读取）
     ⚠️ 口径与 fnmonitor 对齐（ui-src/33-state.js 的 TH_SPEC）：
       diskTempWarn 50℃ / diskTempCrit 58℃ —— 硬盘的温度余量比 CPU 小得多，
       50℃ 就该留意了；早先这里写死 60/75，与 fnmonitor 差一大截。
       通用传感器（CPU / 风扇位）沿用 60/75 的家用口径。
     ⚠️ 这里**不能**再用 `var TH_TEMP_WARN = 60` 这种常量 ——
        常量在脚本加载时就固化了，后端配置异步回来后永远不生效。
        必须走函数调用（th('tempWarn')），每次用时现取。 */
  var THRESHOLDS = window.FnStyleThresholds || null;

  /* 取阈值；thresholds.js 未就绪时退回内置默认值（保证不崩）。 */
  function th(key, fallback) {
    if (THRESHOLDS && typeof THRESHOLDS.get === 'function') {
      return THRESHOLDS.get(key, fallback);
    }
    return fallback;
  }

  /* 温度等级：返回 'ok' / 'warn' / 'high'。
     ⚠️ 注意 'ok' 也是真值 —— 判「是否超阈值」必须显式比较，
        不能写成 if (lv)，否则任何温度都会被当成超阈。
        要判是否超阈请用 tempExceed() 。 */
  function tempLevel(t) {
    if (t === null || t === undefined || isNaN(t)) return 'ok';
    if (t >= th('tempCrit', 75)) return 'high';
    if (t >= th('tempWarn', 60)) return 'warn';
    return 'ok';
  }

  /* 硬盘温度等级：fnmonitor 的 LV.dtemp 口径（默认 50 / 58） */
  function diskTempLevel(t) {
    if (t === null || t === undefined || isNaN(t)) return 'ok';
    if (t >= th('diskTempCrit', 58)) return 'high';
    if (t >= th('diskTempWarn', 50)) return 'warn';
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
    return '<div class="mon-bar"><div class="mon-bar-fill lv-' +
      level(p, th('barWarn', 70), th('barCrit', 90)) +
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

  /* ⚠️ v1.1.49：温度板块的硬盘显示名 —— 用「存储空间 volN」（fnOS 存储空间 N
     = /volN），没挂载到存储池的盘保留设备名。
     同一存储空间有多块盘（RAID 池）时标签会撞名，diskDisplayLabels()
     对重复标签追加设备名「· sda」以示区分。
     用户反馈：「硬盘名称使用存储空间的名称显示，才好进行区分」。 */
  function diskDisplayName(x) {
    if (!x) return '';
    var v = volOf(x);
    return v ? ('存储空间 vol' + v) : String(x.name || '');
  }

  function diskDisplayLabels(list) {
    var arr = list || [];
    var raw = arr.map(diskDisplayName);
    var seen = {};
    raw.forEach(function (s) { seen[s] = (seen[s] || 0) + 1; });
    return raw.map(function (s, i) {
      var d = arr[i];
      if (seen[s] > 1 && d && d.name) return s + ' · ' + d.name;
      return s;
    });
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

  /* 容量等级：默认 <85 正常，>=85 留意，>=95 故障（与 fnmonitor 阈值一致）
     ⚠️ v1.1.79：门限改为从统一阈值配置读取（capWarn / capCrit）。 */
  function capLevel(pct) {
    var p = Number(pct);
    if (!isFinite(p)) return '';
    if (p >= th('capCrit', 95)) return 'crit';
    if (p >= th('capWarn', 85)) return 'warn';
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
     与 fnmonitor 的 RAMP.pct(capWarn, capCrit) 完全一致。
     ⚠️ v1.1.79：门限改为从统一阈值配置读取。 */
  function capRampStops() {
    var w = th('capWarn', 85), c = th('capCrit', 95);
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
        (tEx === 'high' ? th('diskTempCrit', 58) : th('diskTempWarn', 50)) + '℃）。</span></div>';
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
    // ⚠️ v1.1.65：**不能只靠 requestAnimationFrame**。
    //    标签页切到后台时 Chrome 冻结 rAF（实测 document.hidden=true 时
    //    2 秒内只触发 1 次，Page.bringToFront 也救不回来）→
    //    双层 rAF 不执行 → 硬盘抽屉的读写曲线永远空白。
    //    → 加 setTimeout 兜底。
    var sparkInited = false;
    var drawSparkOnce = function () {
      if (sparkInited) return;
      sparkInited = true;
      drawDrawerSpark();
    };
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { drawSparkOnce(); });
    });
    setTimeout(drawSparkOnce, 60);
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
          (capLv ? ' · 容量占用超 ' +
            (capLv === 'crit' ? th('capCrit', 95) : th('capWarn', 85)) + '%' : '');

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

    /* --- 软 RAID ---
       ⚠️ v1.1.47：按用户要求删除软 RAID 卡片的显示
       （/proc/mdstat 数据后端仍在采集，仅前端不再呈现；
         硬 RAID 卡（storcli）段落保留）。 */
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

  /* ---------------- 2. 处理器 / 内存 / 显卡 ----------------
     渲染委托给 compute.js —— 按 fnmonitor 1:1 还原的三张卡片
     （每核点阵瓦片 / DIMM 内存条 SVG / 显卡引擎明细条）。
     这里只做兜底：compute.js 没加载时给一个提示，不让板块空白。 */
  /* ---------------- 2.5 系统重要内容概览（v1.1.69）----------------
     对齐 fnmonitor 总览页的「核心指标」模块：六张卡片
     （处理器 / 内存 / 存储 / 网络 / 温度 / 功耗），
     每张卡 = 点阵大数字 + 环形仪表 + 副标题 + 迷你趋势线。
     实现在 static/js/overview.js（独立文件，避免 monitor.js 继续膨胀）。 */
  function renderOverview(d, stats) {
    var box = $('#monOverview');
    if (!box) return;
    if (!window.FnStyleOverview || !window.FnStyleOverview.render) {
      /* ⚠️ v1.1.89：不要用带 .spin 的 loading 骨架 ——
         overview.js 加载失败时页面会永远转圈，看起来像「彻底坏掉」。
         纯文字提示更诚实。 */
      box.innerHTML = '<div class="mon-empty">概览模块未加载</div>';
      return;
    }
    /* 把独立的功耗统计挂上去（可能为 null —— 还没拿到 / 接口失败） */
    var payload = d;
    if (stats) {
      payload = {};
      for (var k in d) payload[k] = d[k];
      payload.powerStats = stats;
    }
    window.FnStyleOverview.render(box, payload);
  }

  function renderCore(d) {
    var box = $('#monCore');
    if (!box) return;
    if (window.FnStyleCompute && window.FnStyleCompute.render) {
      window.FnStyleCompute.render(d, box);
    } else {
      box.innerHTML = '<div class="mon-cmp-note">处理器 / 内存 / 显卡模块未加载</div>';
    }
  }

  /* ---------------- 3. 温度和散热 ----------------
     fnmonitor 有两套彼此独立的 UI：
       · 总览卡片（defineModule id:'thermal'）—— 主页面上的两栏摘要卡
       · 详情抽屉（SHEETS.temp）—— 点「详情 ›」后弹出的完整面板
     本文件里两套都有：renderTempOverview / renderPowerOverview 画总览，
     renderTemp / renderPower 画抽屉内容。 */

  /* 温度条刻度：20℃ → 0%，100℃ → 100%（与 fnmonitor 的 T_MIN/T_MAX 一致） */
  var T_MIN = 20, T_MAX = 100;
  function tPos(t) {
    var p = (Number(t) - T_MIN) / (T_MAX - T_MIN) * 100;
    return Math.max(0, Math.min(100, p));
  }

  /* 传感器类型 → 中文标签（对齐 fnmonitor 的 TN 映射） */
  var SENSOR_TN = {
    cpu: 'CPU', pch: '芯片组', board: '主板',
    acpi: 'ACPI', disk: '硬盘', gpu: 'GPU', other: '其它'
  };

  /* 传感器名称 → 可读对象名（⚠️ v1.1.48）
     用户反馈：抽屉里的 acpitz / Package id 0 / x86_pkg_temp 这类
     内核测点名「都不知道是什么」。按名称映射为中文对象名；
     映射后同名的测点（如实机的 3 个 acpitz）由 sensorLabels()
     自动追加序号区分。未认识的名称保留原样。 */
  function sensorLabel(name) {
    var raw = String(name || '').trim();
    var n = raw.toLowerCase();
    /* hwmon 的 coretemp 芯片前缀（coretemp Package id 0 / coretemp Core 0） */
    n = n.replace(/^coretemp[\s_-]*/, '');
    if (/^package id/.test(n)) return 'CPU 封装';
    if (/^x86_pkg_temp/.test(n)) return 'CPU 封装';
    var m = n.match(/^core[\s_-]?(\d+)$/);
    if (m) return 'CPU 核心 ' + m[1];
    if (/^acpitz/.test(n)) return '主板 ACPI 温区';
    if (/^pch/.test(n)) return '芯片组（PCH）';
    if (/iwlwifi|wifi|wlan/.test(n)) return '无线网卡';
    if (/^drivetemp/.test(n)) return 'SATA 硬盘';
    if (/^(nct\d+|it86\d+|w836\d+|lm7\d|atk0110|asus)/.test(n)) return '主板传感器';
    if (/^motherboard|^board/.test(n)) return '主板';
    if (/^gpu/.test(n)) return 'GPU';
    if (/^cpu/.test(n)) return 'CPU';
    /* 其余保留原名 —— 通常是用户认识的盘名（sda / nvme0n1），
       保留反而比「SATA 硬盘」信息量更大（知道是哪块盘）。 */
    return raw;
  }
  /* 风扇名称 → 可读对象名（v1.1.48）：nct6798 fan1 这类同样难懂 */
  function fanLabel(name) {
    var raw = String(name || '').trim();
    var n = raw.toLowerCase();
    var m = n.match(/^(?:nct\d+|it86\d+|w836\d+)[\s_-]*fan[\s_-]*(\d+)$/);
    if (m) return '主板风扇 ' + m[1];
    if (/cpu/.test(n)) return 'CPU 风扇';
    m = n.match(/^fan[\s_-]*(\d+)$/);
    if (m) return '风扇 ' + m[1];
    return raw;
  }
  /* 同名区分：映射后同名 ≥2 个的测点依次追加序号「 1」「 2」…（单个不加）。
     传入的是待渲染的传感器数组，返回等长的可读名称数组。 */
  function sensorLabels(list) {
    var counts = {}, idx = {};
    var labels = (list || []).map(function (s) { return sensorLabel(s && s.name); });
    labels.forEach(function (l) { counts[l] = (counts[l] || 0) + 1; });
    return labels.map(function (l) {
      if (counts[l] <= 1) return l;
      idx[l] = (idx[l] || 0) + 1;
      return l + ' ' + idx[l];
    });
  }

  /* ⚠️ v1.1.49：对象名排序比较器（用户要求温度计与传感器列表按名称排序）。
     规则：西文名（CPU 等）在前，中文名按拼音序（zh-Hans-CN）；
     numeric 让数字后缀「主板风扇 2 / 10」按数值比较，
     不会出现「10 排在 2 前」。
     ⚠️ 不能直接 localeCompare('zh-Hans-CN')：ICU 的中文排序默认把
     拉丁字符排在汉字之后，CPU 会落到列表末尾，观感不对。 */
  function cmpName(a, b) {
    var x = String(a), y = String(b);
    var xa = /^[\x00-\x7F]/.test(x), ya = /^[\x00-\x7F]/.test(y);
    if (xa !== ya) return xa ? -1 : 1;
    return x.localeCompare(y, 'zh-Hans-CN', { numeric: true });
  }

  /* ================= 总览形态：温度与散热 =================
     对齐 fnmonitor defineModule id:'thermal'（源文件 3014 行起）：
       <div class="grid g-thermal">            ← 1.5fr 1fr 两栏
         <div class="card pad tap">             ← 左栏（可点开详情）
           <div class="label">处理器与主板<span class="faint lbl-r">竖线为警告 / 严重阈值</span></div>
           <div data-ref="tl"></div>            ← CPU / 主板温度条
           <div class="label" style="margin:18px 0 6px">硬盘</div>
           <div data-ref="td"></div>            ← 硬盘温度条
           ${scaleHTML}                         ← 20/40/60/80/100 刻度尺
         </div>
         <div class="card pad">                ← 右栏
           <div class="label">风扇</div>
           <div data-ref="fans"></div>
           <div data-ref="vw"><div class="label">电压</div><div class="volts"></div></div>
         </div>
       </div>
     ⚠️ v1.1.48：温度测点顺序调整为（用户要求只显示重要部件）：
       CPU → 主板 → 硬盘单独一段；其余传感器只进详情抽屉。 */

  /* 温度色阶（对齐 fnmonitor 的 RAMP.temp / RAMP.dtemp）
     fnmonitor 的温度条用**渐变**填充（rampGrad + background-size 裁切），
     而不是纯色 —— 这里同样输出渐变，视觉更接近。 */
  function ovTempStops(kind) {
    var w = (kind === 'disk') ? th('diskTempWarn', 50) : th('tempWarn', 60);
    var c = (kind === 'disk') ? th('diskTempCrit', 58) : th('tempCrit', 75);
    var RG = 'var(--r-g)', RY = 'var(--r-y)';
    return (kind === 'disk')
      ? [[Math.min(20, w - 13), RG], [w - 12, RG], [w - 4, RY], [w, 'var(--warning)'], [c, 'var(--danger)']]
      : [[Math.min(30, w - 26), RG], [w - 25, RG], [w - 10, RY], [w, 'var(--warning)'], [c, 'var(--danger)']];
  }

  /* 温度条填充（渐变 + background-size 只铺满已填充段）*/
  function ovTempFill(value, kind) {
    var stops = ovTempStops(kind);
    var grad = 'linear-gradient(90deg,' + stops.map(function (s) {
      return s[1] + ' ' + s[0] + '%';
    }).join(',') + ')';
    var len = tPos(value);
    var big = (100 / Math.max(len, .5) * 100).toFixed(1) + '%';
    return 'background-image:' + grad + ';background-size:' + big + ' 100%;background-position:left';
  }

  /* 温度条一行（对齐 fnmonitor 的 thermoRows）
     DOM：<div class="thermo-row"><span class="t-l">…</span>
           <div class="thermo"><span class="mk"/><i/></div>
           <span class="v">40℃</span></div> */
  function ovThermoRow(label, value, marks, kind, cls) {
    var p = tPos(value);
    var mkHtml = (marks || []).map(function (m) {
      return '<span class="mk" style="left:' + tPos(m).toFixed(1) + '%"></span>';
    }).join('');
    return '<div class="thermo-row">' +
      '<span class="' + (cls ? cls + ' ' : '') + 't-l" title="' + esc(label) + '">' + esc(label) + '</span>' +
      '<div class="thermo">' + mkHtml +
        '<i style="width:' + p.toFixed(1) + '%;' + ovTempFill(value, kind) + '"></i></div>' +
      '<span class="v">' + fmtInt(value) + '℃</span>' +
    '</div>';
  }

  /* 空状态（对齐 fnmonitor 的 emptyHTML） */
  function ovEmpty(title, text) {
    return '<div class="empty-s"><div><b>' + esc(title) + '</b>' +
      (text ? '<span>' + esc(text) + '</span>' : '') + '</div></div>';
  }

  /* 刻度尺（对齐 fnmonitor 的 scaleHTML） */
  var OV_SCALE_HTML = '<div class="thermo-scale"><span></span><div>' +
    [20, 40, 60, 80, 100].map(function (v) {
      return '<span style="left:' + tPos(v) + '%">' + v + '°</span>';
    }).join('') + '</div><span></span></div>';

  /* 温度测点计数（⚠️ v1.1.47 统一口径，全站唯一）
     口径 = 温度计列表的实际行数：
       CPU(有效 1 个) + 主板(有效 1 个)
       + temps 中 type!=='disk' 且有读数的传感器
       + disks 中有读数的硬盘。
     ⚠️ 之前两处口径各算各的，导致「顶部计数与列表行数对不上」
       （用户实机反馈：顶部 16 个，列表只有 15 行）：
       · 抽屉副标题 compute.js tempSubText = temps.length + disks + cpu
         —— temps 里 type='disk' 的 drivetemp 条目（就是硬盘本身，
         列表已单独用 disks 段显示）被重复计入，且漏了「主板」；
       · 总览计数 monitor.js = temps.length + disks.length
         —— 同样重复计 drivetemp。
     修复：计数必须与列表行数一致 —— 「顶部说几个，下面就显示几行」。
     compute.js 的 tempSubText 也调本函数（monitor.js 先于 compute.js 加载）。 */
  function tempPointCount(t) {
    t = t || {};
    var n = 0;
    var cpu = t.cpuTemp, mb = t.mbTemp;
    if (cpu !== null && cpu !== undefined && !isNaN(Number(cpu))) n++;
    if (mb !== null && mb !== undefined && !isNaN(Number(mb))) n++;
    (t.temps || []).forEach(function (s) {
      if (!s || s.type === 'disk') return;
      if (s.value === null || s.value === undefined || s.value === '') return;
      n++;
    });
    (t.disks || []).forEach(function (x) {
      if (!x || x.temp === null || x.temp === undefined || x.temp === '') return;
      n++;
    });
    return n;
  }

  function renderTempOverview(d) {
    var box = $('#monTempOverview');
    if (!box) return;
    var t = d.temp || {};
    var temps = t.temps || [];
    var fans = t.fans || [];
    var volts = t.volts || [];
    var cpuTemp = (t.cpuTemp === null || t.cpuTemp === undefined) ? null : Number(t.cpuTemp);
    var mbTemp = (t.mbTemp === null || t.mbTemp === undefined) ? null : Number(t.mbTemp);

    /* ---- 左栏：处理器与主板 + 硬盘 ----
       ⚠️ v1.1.48：主页只显示重要部件（CPU / 主板 / 硬盘），
       其余传感器全部移到详情抽屉（用户要求），slice(0,6) 上限随之移除。 */
    var rows = '';
    var marks = [th('tempWarn', 60), th('tempCrit', 75)];
    var list = [];
    if (cpuTemp !== null && !isNaN(cpuTemp)) {
      list.push({ label: 'CPU', value: cpuTemp, marks: marks, kind: 'cpu' });
    }
    if (mbTemp !== null && !isNaN(mbTemp)) {
      list.push({ label: '主板', value: mbTemp, marks: [], kind: 'other' });
    }
    /* ⚠️ v1.1.48：按用户要求，主页总览只显示**非常重要的部件**的温度
       （CPU / 主板 / 硬盘），其余传感器（CPU 封装、ACPI 温区、核心、
       PCH、GPU 等）全部只在详情抽屉里显示。
       旧实现还带 slice(0, 6) 的 fnmonitor thermoList 上限，已一并移除。 */
    list.forEach(function (it) {
      rows += ovThermoRow(it.label, it.value, it.marks, it.kind);
    });
    if (!rows) {
      rows = ovEmpty('未读取到 CPU / 主板温度',
        'fnOS 默认未安装 lm-sensors，已直读 thermal zone 与 hwmon；虚拟机或容器内通常无法访问硬件传感器。');
    }

    /* 硬盘单独一段（对齐 fnmonitor 的 diskThermo）
       ⚠️ v1.1.49：标签改用「存储空间 volN」（用户要求以存储空间名区分硬盘），
          同空间多盘自动追加设备名；休眠盘追加「· 休眠」。
          旧实现优先插槽号（fnmonitor 的 iDRAC 口径），FnStyle 无 iDRAC，
          实际始终回退到 sda 之类的设备名，难以分辨是哪块盘。 */
    var diskRows = '';
    var diskLbls = diskDisplayLabels(t.disks);
    (t.disks || []).forEach(function (x, di) {
      if (x && x.temp !== null && x.temp !== undefined && x.temp !== '') {
        var sleeping = !!(x.standby || x.state === 'standby');
        var lbl = diskLbls[di] || String(x.name || '');
        if (sleeping) lbl += ' · 休眠';
        diskRows += ovThermoRow(lbl, Number(x.temp),
          [th('diskTempWarn', 50), th('diskTempCrit', 58)], 'disk', sleeping ? 'faint' : '');
      }
    });
    if (!diskRows) {
      diskRows = ovEmpty('没有可用的硬盘温度',
        (t.disks || []).length ? '硬盘未提供温度（需要 drivetemp 或 smartctl）' : '未检测到物理硬盘');
    }

    /* ---- 右栏：风扇 + 电压 ---- */
    var fanHtml = '';
    if (fans.length) {
      fanHtml = fans.map(function (f) {
        var rpm = Number(f.rpm);
        var has = f.rpm !== null && f.rpm !== undefined && !isNaN(rpm);
        var spin = has && rpm > 0 ? (2400 / Math.max(300, rpm)).toFixed(2) + 's' : '0s';
        return '<div class="fan">' +
          '<span class="fan-ico">' + fanIcon(spin) + '</span>' +
          '<div style="flex:1;min-width:0">' +
            /* v1.1.48：风扇名可读化（与抽屉 kv 一致，nct6798 fan1 → 主板风扇 1） */
            '<div class="sub">' + esc(fanLabel(f.name) || '风扇') + '</div>' +
            '<div class="num"><span>' + (has ? fmtInt(rpm) : '--') + '</span>' +
            '<span class="unit" style="font-size:12px">RPM</span></div>' +
          '</div>' +
        '</div>';
      }).join('');
    } else {
      fanHtml = ovEmpty('未检测到可读取的风扇传感器',
        '已适配标准 hwmon、IPMI/BMC、ThinkPad ACPI 与 0xA20 硬件监控邮箱（只读）；' +
        '仍无数值通常表示该机型固件未向系统开放风扇数据。');
    }

    var voltHtml = '';
    if (volts.length) {
      voltHtml = volts.slice(0, 8).map(function (v) {
        return '<div class="volt">' +
          '<div class="eyebrow">' + esc(v.name) + '</div>' +
          '<div class="num"><span>' + fmt2(v.value) + '</span>' +
          '<span class="unit" style="font-size:12px">V</span></div>' +
        '</div>';
      }).join('');
    }

    box.innerHTML =
      '<div class="label" style="margin-bottom:6px">处理器与主板' +
        '<span class="faint lbl-r">竖线为警告 / 严重阈值</span></div>' +
      rows +
      '<div class="label" style="margin:18px 0 6px">硬盘</div>' +
      diskRows +
      OV_SCALE_HTML;

    /* 右栏是独立一张卡（fnmonitor 的 .g-thermal 第二个 .card.pad） */
    var right = $('#monTempSide');
    if (right) {
      right.innerHTML =
        '<div class="label">风扇</div>' +
        '<div style="margin-top:6px">' + fanHtml + '</div>' +
        (voltHtml
          ? '<div class="label" style="margin:18px 0 12px">电压</div><div class="volts">' + voltHtml + '</div>'
          : '');
    }

    /* ---- 摘要行（对齐 fnmonitor 的 m.r.dek）----
       fnmonitor 口径：CPU 超阈值 → 整行用 warn 样式 + 建议检查风道；
       否则列出 CPU / 硬盘最高 / 风扇平均。硬盘最高排除休眠盘。 */
    var dek = $('#monTempDek');
    if (dek) {
      var hot = null;
      var dekDiskLbls = diskDisplayLabels(t.disks);
      var hotIdx = -1;
      (t.disks || []).forEach(function (x, di) {
        if (!x || x.temp === null || x.temp === undefined || x.temp === '') return;
        if (x.standby || x.state === 'standby') return;   // 休眠盘不参与最高温
        if (!hot || Number(x.temp) > Number(hot.temp)) { hot = x; hotIdx = di; }
      });
      var fanAvg = null;
      var running = fans.filter(function (f) { return Number(f.rpm) > 0; });
      if (running.length) {
        fanAvg = running.reduce(function (s, f) { return s + Number(f.rpm); }, 0) / running.length;
      }
      var cpuLv = (cpuTemp === null || isNaN(cpuTemp)) ? '' : tempLevel(cpuTemp);
      if (cpuLv === 'high' || cpuLv === 'warn') {
        dek.innerHTML = '<span class="warn">CPU ' + fmtInt(cpuTemp) + '℃，温度' +
          (cpuLv === 'high' ? '过高' : '偏高') + '</span>，建议检查风道与灰尘。';
      } else {
        var parts = [];
        if (cpuTemp !== null && !isNaN(cpuTemp)) parts.push('CPU <b>' + fmtInt(cpuTemp) + '℃</b>');
        if (hot) parts.push('硬盘最高 ' + fmtInt(hot.temp) + '℃（' +
          esc(dekDiskLbls[hotIdx] || hot.name) + '）');
        if (fanAvg !== null) parts.push('风扇平均 ' + fmtInt(fanAvg) + ' RPM');
        if (!parts.length) {
          dek.textContent = '未读取到任何温度传感器。';
        } else {
          dek.innerHTML = parts.join('，') + '。';
        }
      }
    }
  }

  /* ================= 总览形态：能耗 =================
     对齐 fnmonitor defineModule id:'energy'（源文件 3107 行起）：
       <div class="energy">                    ← 1.1fr 1fr 两栏
         <div class="card pad energy-live tap">← 左：部件功耗构成
           <div class="label">部件功耗构成</div>
           <div class="stack">…</div>            ← 堆叠条
           <div class="legend">…</div>
           <div class="sub">合计 30.5 W · 硬件 RAPL</div>
         </div>
         <div class="stat-cells">              ← 右：2×2 四格（无额外卡片外壳）
           今日用电 / 本月用电 / 本月预计电费 / 全年估算
         </div>
       </div>
       <div class="card chart-card">           ← 下方：每日用电柱状图
     ⚠️ v1.1.50：左栏的「实时功耗」大数字区已按用户要求删除
        （标题栏新增「本月预估电费」徽标）；#monPowerStats 去掉了
        .ov-card 外壳，.stat-cells 直接作为 .energy 网格项。
     */

  var SRC_TXT = { rapl: '硬件 RAPL', idrac: 'iDRAC 实测', estimated: '模型估算' };

  /* 实时功耗的口径说明行（fnmonitor 的 x.wSub）
     rapl  ：整机 = CPU 封装（RAPL 实测）+ 内存 + 主板底座 + 活动硬盘
     idrac ：iDRAC 实测整机输出（+ 电源容量 / 近 1 分钟区间）
     其它  ：无 RAPL：按 TDP × CPU 占用 + 硬盘 + 网卡 + 主板底座估算 */
  function powerBasisText(p, has) {
    if (!has) return '无法读取 RAPL，也无法估算整机功耗';
    if (p.source === 'idrac') {
      var t = 'iDRAC 实测整机输出';
      if (fnOk(p.capacity)) t += ' · 电源容量 ' + fnInt(p.capacity) + ' W';
      if (fnOk(p.min) && fnOk(p.max)) {
        t += ' · 近 1 分钟 ' + fnInt(p.min) + '–' + fnInt(p.max) + ' W';
      }
      return t;
    }
    if (p.source === 'rapl') {
      return '整机 = CPU 封装（RAPL 实测）+ 内存 + 主板底座 + 活动硬盘';
    }
    return '无 RAPL：按 TDP × CPU 占用 + 硬盘 + 网卡 + 主板底座估算';
  }

  function renderPowerOverview(d) {
    var box = $('#monPowerOverview');
    var statsBox = $('#monPowerStats');
    if (!box || !statsBox) return;
    var p = d.power || {};
    var has = p.ok !== false && p.total !== null && p.total !== undefined;
    /* 电价优先取后端返回的实际费率（用户在设置里改过就是那个值）*/
    var rate = (pstats && pstats.rate_yuan_per_kwh) ? pstats.rate_yuan_per_kwh : 0.6;

    /* ---- 左栏：1:1 对齐 fnmonitor energy 模块的左卡 ----
       fnmonitor 的四段结构：
         ① .label「实时功耗」+ 右侧来源 .pill
         ② .dnum.huge 点阵大数字 + .unit「W」
         ③ .sub 口径说明行
         ④ .stack 堆叠条 + .legend 图例
       ⚠️ v1.1.54：v1.1.50 曾按要求删掉左栏的「实时功耗」大数字区，
          本次按用户附图（fnmonitor 截图）恢复 —— 图里左栏就是这四段。
          堆叠条与图例的结构不变。 */
    var html = '';
    html += '<div class="label">实时功耗<span class="pill" style="margin-left:auto">' +
      esc(has ? (SRC_TXT[p.source] || '来源未知') : '不可用') + '</span></div>';
    html += '<div class="dnum huge" style="margin-top:18px"><span class="dm">' +
      esc(has ? fnD1(p.total) : '--') + '</span><span class="unit">W</span></div>';
    html += '<div class="sub" style="margin-top:6px">' + powerBasisText(p, has) + '</div>';

    /* 堆叠条 + 图例（fnmonitor：堆叠条 12px 高、2px gap；色块 8px）*/
    var parts = has ? powerParts(p) : [];
    if (parts.length) {
      html += '<div class="stack" style="margin-top:22px;height:12px">' +
        parts.map(function (q) {
          var style = q[2] === 'hatch' ? '' : ' style="background:' + q[2] + '"';
          return '<i class="' + (q[2] === 'hatch' ? 'ov-hatch' : '') + '"' + style +
            ' data-grow="' + q[1].toFixed(2) + '"></i>';
        }).join('') + '</div>';
      html += '<div class="legend">' + parts.map(function (q) {
        return '<span>' + ovStackSwatch(q[2]) + esc(q[0]) +
          ' <b>' + fnD1(q[1]) + ' W</b></span>';
      }).join('') + '</div>';
    } else {
      html += '<div class="mon-info" style="margin-top:22px">未读取到部件功耗构成数据</div>';
    }
    box.innerHTML = html;

    /* 堆叠条按部件功耗分配宽度（flex-grow 用部件值本身即可，
       浏览器会按比例归一 —— fnmonitor 也是直接写 q[1]）*/
    if (parts.length) {
      Array.prototype.forEach.call(box.querySelectorAll('.stack > i'), function (el) {
        el.style.flexGrow = (parseFloat(el.getAttribute('data-grow')) || 0).toFixed(2);
      });
    }

    /* ---- ⚠️ v1.1.56：右栏四格 ----
       ⚠️ v1.1.50：#monPowerStats 容器已去掉 .ov-card 外壳，
       .stat-cells 直接作为 .energy 网格项 → 不再有「多了一层卡片」。
       ⚠️ v1.1.55：数字改回普通文本（与其它板块的 .dnum > .dm 同款）；
       数值口径仍是 fnmonitor 的（金额带 ¥、kWh 精度规则）。
       ⚠️ v1.1.64：电价口径行**从右栏格子移到板块标题栏**（#monPowerRate），
          避免同一屏出现 3 处电费数字。 */
    /* ⚠️ v1.1.63：电价模型简化为**单一单价**。
       电费 = 用电量 × 单价，不分阶梯、不分时段、不分地区。
       所以这里不再有「谷/平构成胶囊」与「判档依据行」——
       老板决定：全部删掉，只留单价说明。 */
    var effRate = (pstats && pstats.eff_rate_yuan_per_kwh)
      ? Number(pstats.eff_rate_yuan_per_kwh)
      : Number(rate);
    /* ⚠️ v1.1.63：单价统一显示 **4 位小数**。
       原来这里取 3 位（Math.round(effRate*1000)/1000）→ 0.5224 被截成
       0.522，而同一张卡片里的 .sub-proj-rate 用 4 位显示 0.5224，
       结果**同屏两个胶囊显示不同精度的同一个单价**（实机截图确认）。
       现在两处都用 4 位，且与电价面板输入框的 step（0.0001）一致。 */
    var rateTxt = String(Math.round(effRate * 10000) / 10000);
    /* ⚠️ v1.1.64：单价胶囊从右栏格子里**移到标题栏**。
       原来「本月预计电费」格里并排放了两枚胶囊
       （「按 0.5224 元/kWh 估算」+「电价 0.5224 元/kWh」），
       数值完全相同、语义重复，把那一格撑得很挤。
       现在标题栏放一枚，右栏两枚一并删除（老板确认）。
       ⚠️ 无统计数据时**清空**标题栏胶囊，别显示「电价 0 元/kWh」。
       ⚠️ v1.1.64：老板要求标签里**说明电价本身**（这个电价是什么、
          默认取哪档、能不能改），而不是讲电费的计算规则。
          后端因此把说明拆成两段分别下发：
            rate_desc → 这个电价是什么（标题栏标签用）
            rate_note → 电费怎么算出来（电价设置面板用） */
    var headRate = document.querySelector('#monPowerRate');
    if (headRate) {
      if (!pstats) {
        headRate.innerHTML = '';
        headRate.removeAttribute('title');
      } else {
        /* ⚠️ v1.1.64：老板要求标签里说明**电价本身**是什么，
           不是讲电费的计算规则（那是电价面板 RATE_NOTE 的事）。
           后端把文案分开下发：
             rate_desc_short = 标签里直接显示的短句
             rate_desc       = 完整说明（悬停/聚焦可见）
             rate_note       = 电费怎么算（电价面板用）
           ⚠️ 短句由后端给，不能在前端截断 RATE_DESC ——
              它的第一句就有 46 字（实测铺开后胶囊 708px、占满标题栏 63%）。 */
        var descFull = String((pstats && pstats.rate_desc) || '');
        var descShort = String((pstats && pstats.rate_desc_short) || '');
        headRate.innerHTML =
          '<span class="rate-lbl">电价</span>' + esc(rateTxt) + ' 元/kWh' +
          (descShort ? '<span class="rate-note">' + esc(descShort) + '</span>' : '') +
          (descFull ? '<span class="rate-info" tabindex="0" role="img" ' +
            'aria-label="' + esc(descFull) + '">ⓘ</span>' : '');
        headRate.setAttribute('title',
          descFull || ('电费单价：' + rateTxt + ' 元/kWh'));
        var info = headRate.querySelector('.rate-info');
        if (info) info.setAttribute('title', descFull);
      }
    }

    statsBox.innerHTML = '<div class="stat-cells">' +
      '<div class="stat-cell"><div class="eyebrow">今日用电</div>' +
        '<div class="dnum"><span class="dm">' + esc(fnKwh(pstats && pstats.today_kwh)) +
        '</span><span class="unit">kWh</span></div>' +
        '<div class="sub" title="今日已用电量 × 电价 = ' +
          esc(fnKwh(pstats && pstats.today_kwh)) + ' kWh × ' +
          esc(String(rate)) + ' 元/kWh">已计电费 ' +
          fnYuan(pstats && pstats.today_yuan) + '</div></div>' +
      '<div class="stat-cell"><div class="eyebrow">本月用电</div>' +
        '<div class="dnum"><span class="dm">' + esc(fnKwh(pstats && pstats.month_kwh)) +
        '</span><span class="unit">kWh</span></div>' +
        '<div class="sub" title="本月已用电量 × 电价 = ' +
          esc(fnKwh(pstats && pstats.month_kwh)) + ' kWh × ' +
          esc(String(rate)) + ' 元/kWh">已计电费 ' +
          fnYuan(pstats && pstats.month_yuan) + '</div>' +
        '</div>' +
      '<div class="stat-cell"><div class="eyebrow">本月预计电费</div>' +
        '<div class="dnum"><span class="dm">' + esc(fnYuan(pstats && pstats.month_proj_yuan)) + '</span></div>' +
        '<div class="sub" title="本月已用 + 剩余天数按平均功耗推算">约 ' +
          fnKwh(pstats && pstats.month_proj_kwh) + ' kWh · 剩余天数按' +
          esc(pstats && pstats.avg_basis || '平均') + ' ' +
          fnD1(pstats && pstats.avg_w) + ' W</div>' +
        '</div>' +
      '<div class="stat-cell"><div class="eyebrow">全年估算<span class="pill est-pill">估算</span></div>' +
        '<div class="dnum"><span class="dm">' + esc(fnYuan(pstats && pstats.year_proj_yuan)) + '</span></div>' +
        '<div class="sub" title="平均功耗 × 8760 小时">约 ' +
          fnInt(pstats && pstats.year_proj_kwh) + ' kWh · ' +
          esc(pstats && pstats.avg_basis || '平均') + ' ' +
          fnD1(pstats && pstats.avg_w) + ' W × 8760 小时</div></div>' +
    '</div>';
  }

  /* 每日用电柱状图（1:1 对齐 fnmonitor 的 BarChart —— SVG 实现）
     fnmonitor 的口径与形态（32-bars.js 的 BarChart + 43-modules.js 的 barOpts）：
       · 请求 days=30（DAY_BARS），后端返回完整序列；
       · **从第一个有数据的天起 slice**，中间没数据的天保留空位（不画柱）；
       · 标题 = 「近 N 天用电」，N = 实际渲染天数；无数据时写「近 30 天用电」；
       · 柱高 = 当天 kWh / (max × 1.08)，最大值上浮 8%；
       · 两条水平虚线网格（0.5 / 1.0 位置）；
       · 非今天的数据柱 fill-opacity .42，今天的柱满色 + 柱顶小圆点（live-pt）；
       · X 轴标签：末位固定「今天」，其余按 step 间隔（M/D）；
       · 逐柱悬浮浮层：标题（日期 · 电费 · 数据时长）+「用电 x kWh」。
     ⚠️ v1.1.54：v1.1.53 是 div 实现（无网格线 / 无小圆点 / 无浮层），
        本次按用户附图换成 SVG 实现，细节与 fnmonitor 逐项对齐。 */

  /* 柱状图的当前数据（浮层内容要用）*/
  var pwrBarState = { rows: [], list: [], labels: [] };
  var pwrBarLast = '';        /* 上次绘制的签名，宽度/行数不变就不重画 */
  var pwrBarsRO = null;       /* 容器尺寸变化时重画 */
  var pwrHoverBound = false;  /* 悬浮事件只绑一次 */
  var pwrBarRetry = 0;        /* 首帧没布局时的重试次数上限 */

  function renderPowerDaily(res) {
    var box = $('#monPowerDaily');
    if (!box) return;
    var all = (res && res.days) || [];
    /* 对齐 fnmonitor 的 dailyFromApi：从第一个有数据的天起截取 */
    var first = -1;
    for (var fi = 0; fi < all.length; fi++) {
      if (all[fi] && all[fi].has_data && all[fi].kwh !== null &&
          all[fi].kwh !== undefined) { first = fi; break; }
    }
    var list = first < 0 ? [] : all.slice(first);
    /* 短标签 M/D（fnmonitor 的 barOpts.title / xLabel 用同一个日期）*/
    var labels = list.map(function (d) {
      var parts = String(d.day || '').split('-');
      return (parts.length === 3) ? (Number(parts[1]) + '/' + Number(parts[2])) : (d.day || '');
    });
    var n = list.length;
    var html = '<div class="chart-head"><div class="chart-title">' +
      (n ? ('近 ' + n + ' 天用电') : '近 30 天用电') + '</div>' +
      '<div class="chart-legend"><span><span class="lk"><i class="kb0"></i></span>' +
      '<span>每日 kWh · 功率历史积分</span></span></div></div>';
    if (!n) {
      html += '<div class="chart-empty-s">暂无功率数据：采集满一段时间后这里会显示每天的用电量</div>';
      box.innerHTML = html;
      pwrBarState.rows = [];
      pwrBarLast = '';
      return;
    }
    html += '<div class="pwr-bars" id="monPowerBars"></div>';
    box.innerHTML = html;

    /* rows：中间没数据的天保留空位（kwh 缺省 → 不画柱）*/
    var rows = list.map(function (d, i) {
      var r = { dim: i !== n - 1, day: d.day };
      if (d.kwh !== null && d.kwh !== undefined && !isNaN(Number(d.kwh))) {
        r.kwh = Number(d.kwh);
      }
      return r;
    });
    pwrBarState.rows = rows;
    pwrBarState.list = list;
    pwrBarState.labels = labels;
    pwrBarLast = '';
    drawPowerBars();
    bindPowerBarsHover();
    ensurePowerBarsObserver(box);
  }

  /* 画柱状图（fnmonitor 的 BarChart.render，SVG 版）
     几何与 fnmonitor 一致：T=8 / B=22 / max=×1.08 / bw=clamp(slot-3, 3, 22)。*/
  function drawPowerBars() {
    var host = $('#monPowerBars');
    if (!host) return;
    var rows = pwrBarState.rows || [];
    var w = host.clientWidth || 0;
    var h = 132;                 /* fnmonitor: new BarChart(..., 132) */
    if (!w || !rows.length) {
      /* 首帧容器还没布局（display:none / 宽度为 0）→ 下一帧重试，最多重试 5 次 */
      if (!w && rows.length && pwrBarRetry < 5) {
        pwrBarRetry++;
        requestAnimationFrame(drawPowerBars);
      }
      return;
    }
    pwrBarRetry = 0;
    var last = rows[rows.length - 1] || {};
    var sig = w + '|' + h + '|' + rows.length + '|' + (last.kwh || 0);
    if (sig === pwrBarLast) return;
    pwrBarLast = sig;

    var T = 8, B = 22;
    var PH = h - T - B;
    var n = rows.length;
    var max = 0;
    rows.forEach(function (r) { max = Math.max(max, Number(r.kwh) || 0); });
    max = max * 1.08 || 1e-9;
    var slot = w / n;
    var bw = Math.max(3, Math.min(22, slot - 3));
    var svg = '';
    /* 两条水平虚线网格（0.5 / 1.0）*/
    [0.5, 1].forEach(function (f) {
      var y = Math.round(T + PH * (1 - f / 1.08)) + 0.5;
      svg += '<line class="grid-l" x1="0" x2="' + w + '" y1="' + y + '" y2="' + y + '"/>';
    });
    var hasDim = rows.some(function (r) { return r.dim; });
    var step = n > 14 ? 7 : (n > 7 ? 3 : 1);
    rows.forEach(function (r, i) {
      var x = slot * i + (slot - bw) / 2;
      var hh = (Number(r.kwh) || 0) / max * PH;
      svg += '<g data-i="' + i + '">';
      /* 命中区（整列）*/
      svg += '<rect x="' + (slot * i).toFixed(1) + '" y="0" width="' + slot.toFixed(1) +
        '" height="' + h + '" fill="transparent"/>';
      /* ⚠️ v1.1.72：用量为 0 / null 时**也要画柱**（最小高度 2px）——
         之前用 `if (hh > 0)` 整根跳过 → 当天刚开始还没累计用电时，
         「今天」那根柱子**整根消失**，图上少一列（实测 bar=3 但 group=4），
         且「今天」柱顶的 live 圆点也跟着没了。
         现在统一画：hh=0 → 贴底 2px 的矮柱，表示「有这一天但还没用电」。 */
      var isZero = hh <= 0;
      var bh = isZero ? 2 : hh;
      var y = T + PH - bh;
      svg += '<rect class="b"' + (isZero ? ' zero' : '') +
        ' x="' + x.toFixed(1) +
        '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
        '" height="' + Math.max(1, bh).toFixed(1) +
        '" rx="' + Math.min(3, bw / 2) + '"' +
        (r.dim ? ' style="fill-opacity:.42"' : '') + '/>';
      /* 今天柱顶的小圆点（只在存在历史柱时才画，fnmonitor 同款）*/
      if (!r.dim && hasDim) {
        svg += '<circle class="live-pt" cx="' + (x + bw / 2).toFixed(1) +
          '" cy="' + (y - 6).toFixed(1) + '" r="2.5"/>';
      }
      svg += '</g>';
      /* X 轴标签：末位固定「今天」，其余按 step 间隔 */
      var lb = (i === n - 1) ? '今天'
        : (((n - 1 - i) % step === 0) ? (pwrBarState.labels[i] || '') : '');
      if (lb) {
        svg += '<text class="axis-t" x="' + (x + bw / 2).toFixed(1) + '" y="' + (h - 4) +
          '" text-anchor="middle">' + esc(lb) + '</text>';
      }
    });
    host.innerHTML = '<svg role="img">' + svg + '</svg><div class="pwr-tip"></div>';
  }

  /* 逐柱悬浮浮层（fnmonitor 的 BarChart.hover）
     事件委托绑在 #monPowerDaily 上（只绑一次，不随 innerHTML 重建失效）。*/
  function bindPowerBarsHover() {
    if (pwrHoverBound) return;
    var box = $('#monPowerDaily');
    if (!box) return;
    pwrHoverBound = true;
    box.addEventListener('pointermove', function (e) {
      var g = (e.target && e.target.closest) ? e.target.closest('[data-i]') : null;
      hoverPowerBar(g ? Number(g.getAttribute('data-i')) : -1);
    });
    box.addEventListener('pointerleave', function () { hoverPowerBar(-1); });
  }

  function hoverPowerBar(i) {
    var host = $('#monPowerBars');
    if (!host) return;
    var tip = host.querySelector('.pwr-tip');
    if (!tip) return;
    var rows = pwrBarState.rows || [];
    if (i < 0 || i >= rows.length) {
      host.classList.remove('hovering');
      tip.classList.remove('show');
      Array.prototype.forEach.call(host.querySelectorAll('rect.b'), function (r) {
        r.classList.remove('on');
      });
      return;
    }
    host.classList.add('hovering');
    Array.prototype.forEach.call(host.querySelectorAll('g[data-i] rect.b'), function (r) {
      var g = r.parentNode;
      r.classList.toggle('on', !!(g && Number(g.getAttribute('data-i')) === i));
    });
    var d = (pwrBarState.list || [])[i] || {};
    var r = rows[i];
    var title = (pwrBarState.labels || [])[i] || (d.day || '');
    if (i === rows.length - 1) title += ' · 今天';
    if (d.cost !== null && d.cost !== undefined) title += ' · ' + fnYuan(d.cost);
    if (d.power_h !== null && d.power_h !== undefined && d.power_h < 23.5) {
      title += ' · 数据 ' + fnD1(d.power_h) + ' 小时';
    }
    tip.innerHTML = '<div class="tt">' + esc(title) + '</div>' +
      '<div class="tr"><span><i class="kb0"></i>用电</span><b>' +
      esc(fnKwh(r.kwh) + ' kWh') + '</b></div>';
    /* 定位：水平居中于柱子上方，垂直浮出图表顶部（fnmonitor 同款）*/
    var w = host.clientWidth || 0;
    var slot = w / rows.length;
    var x = slot * (i + 0.5);
    var tw = tip.offsetWidth || 140;
    tip.style.left = clampN(x - tw / 2, 0, Math.max(0, w - tw)) + 'px';
    tip.style.top = '-8px';
    tip.style.transform = 'translateY(-100%)';
    tip.classList.add('show');
  }

  /* 容器宽度变化时重画（fnmonitor 用 ResizeObserver 观察 host）*/
  function ensurePowerBarsObserver(box) {
    if (pwrBarsRO || typeof ResizeObserver === 'undefined') return;
    try {
      pwrBarsRO = new ResizeObserver(function () { drawPowerBars(); });
      pwrBarsRO.observe(box);
    } catch (e) { /* 忽略：不影响主流程 */ }
  }

  /* 风扇图标（对齐 fnmonitor 的 #i-fan symbol：中心轴 + 三片扇叶）
     fnmonitor 的 .fan-ico svg{animation:spin linear infinite} 旋转的是整个 svg，
     所以这里输出一个完整 svg，动画周期通过 --fan-spin 变量传入。 */
  function fanIcon(period) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-linecap="round" style="--fan-spin:' + (period || '0s') + '">' +
        '<circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none"/>' +
        '<path d="M12 10.2C11 7.6 11.4 4 14 3.6c2.4-.4 3.2 2.6 1.4 4.1-1 .9-2.2 1.6-3.4 2.5Z"/>' +
        '<path d="M12 10.2C11 7.6 11.4 4 14 3.6c2.4-.4 3.2 2.6 1.4 4.1-1 .9-2.2 1.6-3.4 2.5Z" transform="rotate(120 12 12)"/>' +
        '<path d="M12 10.2C11 7.6 11.4 4 14 3.6c2.4-.4 3.2 2.6 1.4 4.1-1 .9-2.2 1.6-3.4 2.5Z" transform="rotate(240 12 12)"/>' +
      '</svg>';
  }

  /* 温度抽屉内容（1:1 对齐 fnmonitor 的 SHEETS.temp）
     返回 HTML 字符串，由 compute.js 的 openCompute('temp') 插进抽屉 body。 */
  function tempSheetHtml(d) {
    var t = (d && d.temp) || {};
    var html = '';

    var temps = t.temps || [];
    var fans = t.fans || [];
    var volts = t.volts || [];
    var cpuTemp = (t.cpuTemp === null || t.cpuTemp === undefined) ? null : Number(t.cpuTemp);
    var mbTemp = (t.mbTemp === null || t.mbTemp === undefined) ? null : Number(t.mbTemp);

    /* --- ① 大数字区：CPU 温度 + 阈值 ---
       ⚠️ v1.1.44 对齐 fnmonitor SHEETS.temp.build：
          阈值是 **单行** 字符串（fnmonitor: setText(x.thr, `CPU ${TH.tempWarn} / ${TH.tempCrit}℃ · 硬盘 ${TH.diskTempWarn} / ${TH.diskTempCrit}℃`)），
          之前 FnStyle 写成两行（<br>），观感上右侧被撑高、显得笨重。
          eyebrow 文案也改成 fnmonitor 的「CPU」（不是「CPU 温度」）。 */
    var cpuLv = tempLevel(cpuTemp);
    html += '<div class="mon-hero">' +
      '<div class="mon-hero-left">' +
        '<div class="mon-hero-eyebrow">CPU</div>' +
        '<div class="mon-hero-num"><b class="lv-' + (cpuLv === 'high' ? 'crit' : cpuLv) + '">' +
        (cpuTemp === null ? '—' : fmt1(cpuTemp)) + '</b>' +
        '<span>℃</span></div>' +
      '</div>' +
      '<div class="mon-hero-right">' +
        '<div class="mon-hero-eyebrow">阈值</div>' +
        '<div class="mon-hero-side">CPU ' + th('tempWarn', 60) + ' / ' + th('tempCrit', 75) +
        '℃ · 硬盘 ' + th('diskTempWarn', 50) + ' / ' + th('diskTempCrit', 58) + '℃</div>' +
      '</div>' +
    '</div>';

    /* --- ② 四格统计：CPU / 主板 / 显卡 / 全部测点最高 --- */
    var gpuTemps = [];
    /* 真机 gpu 段结构是 { available, gpus:[...] }，不是数组 */
    var gpuList = (d.gpu && Array.isArray(d.gpu.gpus)) ? d.gpu.gpus
                 : (Array.isArray(d.gpu) ? d.gpu : []);
    gpuList.forEach(function (g) {
      if (g && g.temp !== null && g.temp !== undefined && g.temp !== '') {
        gpuTemps.push(Number(g.temp));
      }
    });
    temps.forEach(function (s) {
      if (s && s.type === 'gpu' && s.value !== null && s.value !== undefined) {
        gpuTemps.push(Number(s.value));
      }
    });
    var allT = temps.map(function (s) { return Number(s.value); })
      .filter(function (v) { return !isNaN(v); })
      .concat([cpuTemp, mbTemp])
      .filter(function (v) { return v !== null && !isNaN(v); });
    (t.disks || []).forEach(function (x) {
      if (x && x.temp !== null && x.temp !== undefined && x.temp !== '') {
        allT.push(Number(x.temp));
      }
    });

    function tv(v) { return (v === null || v === undefined || isNaN(v)) ? '—' : fmt1(v) + ' ℃'; }
    html += '<div class="mon-cells">' +
      '<div class="mon-cell"><div class="mon-cell-eyebrow">CPU</div><b>' + tv(cpuTemp) + '</b></div>' +
      '<div class="mon-cell"><div class="mon-cell-eyebrow">主板</div><b>' + tv(mbTemp) + '</b></div>' +
      '<div class="mon-cell"><div class="mon-cell-eyebrow">显卡</div><b>' +
        (gpuTemps.length ? tv(Math.max.apply(null, gpuTemps)) : '—') + '</b></div>' +
      '<div class="mon-cell"><div class="mon-cell-eyebrow">全部测点最高</div><b>' +
        (allT.length ? tv(Math.max.apply(null, allT)) : '—') + '</b></div>' +
    '</div>';

    /* --- ③ 温度计条形图 --- */
    /* 测点清单：CPU → 主板 → 其它传感器（排除 disk 类型）→ 硬盘 */
    var points = [];
    if (cpuTemp !== null) points.push({ k: 'cpu', label: 'CPU', v: cpuTemp, kind: 'cpu' });
    if (mbTemp !== null) points.push({ k: 'mb', label: '主板', v: mbTemp, kind: 'other' });
    /* 名称映射为可读对象名（v1.1.48），同名测点自动带序号 */
    var drawerSensors = temps.filter(function (s) {
      return s && s.type !== 'disk' &&
        s.value !== null && s.value !== undefined;
    });
    var drawerLabels = sensorLabels(drawerSensors);
    drawerSensors.forEach(function (s, si) {
      points.push({
        k: 's' + si + (s.name || ''), label: drawerLabels[si], v: Number(s.value),
        kind: s.type === 'cpu' ? 'cpu' : 'other',
        max: s.max, crit: s.crit
      });
    });
    /* v1.1.49：硬盘标签改用「存储空间 volN」（同空间多盘追加设备名） */
    var sheetDiskLbls = diskDisplayLabels(t.disks);
    (t.disks || []).forEach(function (x, di) {
      if (!x || x.temp === null || x.temp === undefined || x.temp === '') return;
      points.push({
        k: 'd-' + x.name, label: sheetDiskLbls[di] || String(x.name || ''),
        v: Number(x.temp), kind: 'disk'
      });
    });
    /* ⚠️ v1.1.49：各对象按名称排序显示（用户要求），
       用 cmpName（西文在前 + 中文拼音序 + 数字按数值比较）。 */
    points.sort(function (a, b) { return cmpName(a.label, b.label); });

    if (points.length) {
      var trows = points.map(function (p) {
        /* 阈值刻度：CPU 用 CPU 阈值，硬盘用硬盘阈值，其它用传感器自身 max/crit */
        var marks = [];
        var lv;
        if (p.kind === 'cpu') {
          marks = [th('tempWarn', 60), th('tempCrit', 75)];
          lv = tempLevel(p.v);
        } else if (p.kind === 'disk') {
          marks = [th('diskTempWarn', 50), th('diskTempCrit', 58)];
          lv = diskTempLevel(p.v);
        } else {
          marks = [p.max, p.crit].filter(function (v) {
            return v !== null && v !== undefined && !isNaN(v);
          });
          lv = (p.crit !== null && p.crit !== undefined && p.v >= p.crit) ? 'high'
              : (p.max !== null && p.max !== undefined && p.v >= p.max) ? 'warn' : 'ok';
        }
        var markHtml = marks.map(function (v) {
          return '<span class="mon-thermo-mark" style="left:' + tPos(v).toFixed(1) + '%"></span>';
        }).join('');
        var cls = lv === 'high' ? 'crit' : lv === 'warn' ? 'warn' : '';
        return '<div class="mon-thermo-row">' +
          '<span class="mon-thermo-label" title="' + esc(p.label) + '">' + esc(p.label) + '</span>' +
          '<div class="mon-thermo-bar">' + markHtml +
            '<i class="mon-thermo-fill ' + cls + '" style="width:' + tPos(p.v).toFixed(1) + '%"></i>' +
          '</div>' +
          '<span class="mon-thermo-val">' + fmt1(p.v) + '℃</span>' +
        '</div>';
      }).join('');

      var scaleHtml = '<div class="mon-thermo-scale"><span></span><div>' +
        [20, 40, 60, 80, 100].map(function (v) {
          return '<span style="left:' + tPos(v).toFixed(1) + '%">' + v + '°</span>';
        }).join('') + '</div><span></span></div>';

      html += '<div class="mon-thermo">' + trows + scaleHtml + '</div>';
    } else {
      html += '<div class="mon-thermo"><div class="mon-info">未读取到任何温度传感器' +
        '（fnOS 默认未安装 lm-sensors，已直读 thermal zone 与 hwmon；' +
        '虚拟机或容器内通常无法访问硬件传感器）。</div></div>';
    }

    /* --- ④ 趋势图（对齐 fnmonitor 的 SHEETS.temp：温度计之后、风扇之前）--- */
    if (window.FnStyleCompute && window.FnStyleCompute.chartsHtml) {
      html += window.FnStyleCompute.chartsHtml('temp');
    }

    /* --- ⑤ 分节明细：风扇 / 电压 / 全部传感器 ---
       ⚠️ v1.1.44 对齐 fnmonitor SHEETS.temp.update：
          风扇 / 电压 / 全部传感器 三段在 fnmonitor 里**都用 kvHTML() 键值表**
          （<div class="card pad kv-card"><dl class="kv">…</dl></div>），
          之前 FnStyle 把风扇做成「40px 圆形图标 + 大号转速」的卡片列表、
          电压做成 2 列卡片网格 —— 与 fnmonitor 观感完全不同。
          现在统一改成 kv 键值表（与「全部传感器」段一致）。 */
    /* 风扇 —— fnmonitor: kvHTML(fans.map(f => [esc(fanName(f)), `${F.int(f.rpm)} RPM`])) */
    html += '<div class="mon-cmp-sec"><span>风扇</span>' +
      '<span class="faint">' + fans.length + ' 个</span></div>';
    if (fans.length) {
      html += '<div class="mon-dkv">' + fans.map(function (f) {
        var rpm = Number(f.rpm);
        var has = f.rpm !== null && f.rpm !== undefined && !isNaN(rpm);
        return '<div class="mon-dkv-row">' +
          '<span class="k">' + esc(fanLabel(f.name) || '风扇') + '</span>' +
          '<span class="v">' + (has ? fmtInt(rpm) + ' RPM' : '—') + '</span></div>';
      }).join('') + '</div>';
    } else {
      html += '<div class="mon-info">未检测到可读取的风扇转速传感器' +
        '（已适配标准 hwmon、IPMI/BMC、ThinkPad ACPI 与 0xA20 硬件监控邮箱（只读）；' +
        '仍无数值通常表示该机型固件未向系统开放风扇数据）。</div>';
    }

    /* 电压 —— fnmonitor: kvHTML(volts.map(v => [esc(v.name), `${F.d2(v.value)} V`])) */
    html += '<div class="mon-cmp-sec"><span>电压</span>' +
      '<span class="faint">' + volts.length + ' 个</span></div>';
    if (volts.length) {
      html += '<div class="mon-dkv">' + volts.map(function (v) {
        var val = Number(v.value);
        var has = v.value !== null && v.value !== undefined && !isNaN(val);
        return '<div class="mon-dkv-row">' +
          '<span class="k">' + esc(v.name) + '</span>' +
          '<span class="v">' + (has ? fmt2(val) + ' V' : '—') + '</span></div>';
      }).join('') + '</div>';
    } else {
      html += '<div class="mon-info">未读取到电压数据。</div>';
    }

    html += '<div class="mon-cmp-sec"><span>全部传感器</span>' +
      '<span class="faint">' + temps.length + ' 个</span></div>';
    if (temps.length) {
      /* ⚠️ v1.1.49：按对象名排序显示（用户要求，中文拼音序 + 数字按数值比较），
         先排序再算同名序号，保证「CPU 封装 1 / 2」序号与显示顺序一致。
         type='disk' 的 drivetemp 条目按设备名匹配 t.disks，
         标签改用「存储空间 volN」（v1.1.49 与温度计口径一致）。 */
      var kvList = temps.slice().sort(function (a, b) {
        return cmpName(sensorLabel(a.name), sensorLabel(b.name));
      });
      var kvDiskLbls = diskDisplayLabels(t.disks);
      var kvDiskByName = {};
      (t.disks || []).forEach(function (x, i) {
        if (x && x.name) kvDiskByName[String(x.name).toLowerCase()] = kvDiskLbls[i];
      });
      /* 名称映射为可读对象名（v1.1.48），同名测点自动带序号 */
      var kvLabels = sensorLabels(kvList);
      html += '<div class="mon-dkv">' + kvList.map(function (s, si) {
        var extra = '';
        if (s.max !== null && s.max !== undefined) {
          extra += '<div class="sub">上限 ' + fmt1(s.max) + ' ℃</div>';
        }
        var tag = SENSOR_TN[s.type] || s.type || '其它';
        var kvName = (s.type === 'disk' && kvDiskByName[String(s.name || '').toLowerCase()]) ||
          kvLabels[si];
        return '<div class="mon-dkv-row">' +
          '<span class="k">' + esc(kvName) + '<span class="mon-sensor-tag">' + esc(tag) + '</span></span>' +
          '<span class="v">' + fmt1(s.value) + ' ℃' + extra + '</span></div>';
      }).join('') + '</div>';
    } else {
      html += '<div class="mon-info">sensors 未返回温度' +
        '（未安装 lm-sensors，已使用 thermal zone / hwmon 直读作为替代）。</div>';
    }

    return html;
  }

  /* =====================================================================
     能耗：LED 点阵数字 + fnmonitor 数值口径
     ---------------------------------------------------------------------
     ① 点阵：fnmonitor 的大数字是 5×7 的 LED 点阵（每格一颗圆点，未点亮的点
        留淡灰底），截图里「29.2 / 0.25 / ¥6.37」都是这个形态。
        v1.1.53 之前 FnStyle 用的是普通无衬线粗体数字，视觉上差一档，
        v1.1.54 按用户附图还原为点阵。
        实现差异：fnmonitor 是 DOM API 逐格改 class（复用 cells，点位淡入淡出）；
        FnStyle 总览是 innerHTML 字符串整体重建，这里直接生成 SVG 字符串，
        点位状态用 CSS transition 做过渡。字库只保留能耗面板用得到的字符。
     ② 数值口径：fnmonitor 的 F.int / F.d1 / F.kwh / F.yuan（30-core.js）。
        与 FnStyle 原有的 fmtInt / fmt1 / fmtKwh / fmtYuan 精度不同，
        且 fnmonitor 的金额带 ¥ 前缀。这里另起一套 fnInt / fnD1 / fnKwh /
        fnYuan，能耗卡片统一用 fnmonitor 口径；详情抽屉保持原样不动。
     ===================================================================== */


  /* ---- fnmonitor 数值口径（30-core.js 的 F）---- */
  function fnOk(v) {
    return v !== null && v !== undefined && !isNaN(v);
  }
  function fnInt(v) {
    return fnOk(v) ? String(Math.round(Number(v))) : '--';
  }
  function fnD1(v) {
    if (!fnOk(v)) return '--';
    var n = Number(v);
    return Math.abs(n) >= 100 ? String(Math.round(n)) : n.toFixed(1);
  }
  function fnKwh(v) {
    if (!fnOk(v)) return '--';
    var n = Number(v);
    return n >= 100 ? String(Math.round(n)) : (n >= 10 ? n.toFixed(1) : n.toFixed(2));
  }
  function fnYuan(v) {
    if (!fnOk(v)) return '--';
    var n = Number(v);
    return '¥' + (n >= 100 ? String(Math.round(n)) : n.toFixed(2));
  }

  /* ---------------- 4. 能耗 ---------------- */

  /* ---------------- 4. 能耗 ----------------
     1:1 对齐 fnmonitor 的 SHEETS.power：
       ① 大数字区（实时功耗 + 口径说明）
       ② 功耗堆叠条（按部件分解：CPU 封装 / 内存 / 硬盘 / 主板底座 / 其他）
       ③ 部件明细（CPU 封装 / 核心 / 非核心 / 内存 / 主板底座 / 活动硬盘）
       ④ 用电与电费（今日 / 本月 / 开机以来）
       ⑤ 估算参数
     用电量由 /api/power/stats 提供（历史库 power 指标梯形积分）。 */

  /* 功耗部件分解（对齐 fnmonitor 的 powerParts）
     fnmonitor 的配色是围绕主色的明度渐变：
       CPU 封装   = 主色
       内存       = 主色 55% 混卡片底
       硬盘       = 斜线纹理（hatch）
       主板与其他 = 主色 25% 混卡片底
       剩余       = --fill-3
     FnStyle 用 color-mix 实现同款层次。 */
  function powerParts(p) {
    var parts = [];
    function ok(v) { return v !== null && v !== undefined && !isNaN(v); }
    if (p.source === 'idrac') {
      return [['整机实测', Number(p.total) || 0, 'var(--spark-1)']];
    }
    if (ok(p.package)) parts.push(['CPU 封装', Number(p.package), 'var(--spark-1)']);
    if (ok(p.dram)) {
      parts.push(['内存', Number(p.dram),
        'color-mix(in srgb, var(--spark-1) 55%, var(--bg-elevated))']);
    }
    if (ok(p.disksW)) {
      parts.push(['硬盘' + (p.disksActive ? '（' + p.disksActive + ' 块在转）' : ''),
        Number(p.disksW), 'hatch']);
    }
    if (ok(p.base)) {
      parts.push(['主板与其他', Number(p.base),
        'color-mix(in srgb, var(--spark-1) 25%, var(--bg-elevated))']);
    }
    var sum = parts.reduce(function (a, x) { return a + x[1]; }, 0);
    var rest = (Number(p.total) || 0) - sum;
    if (rest > 0.05) {
      parts.push([p.source === 'estimated' ? 'CPU 与网卡等（估算）' : '其他',
        rest, 'var(--bg-hover)']);
    }
    return parts;
  }

  /* 堆叠条色块（hatch 用斜线纹理，其余直接上色）*/
  function ovStackSwatch(color) {
    return color === 'hatch'
      ? '<i class="ov-hatch"></i>'
      : '<i style="background:' + color + '"></i>';
  }

  /* 能耗抽屉内容（1:1 对齐 fnmonitor 的 SHEETS.power）
     返回 HTML 字符串，由 compute.js 的 openCompute('power') 插进抽屉 body。 */
  function powerSheetHtml(d) {
    var p = (d && d.power) || {};
    var html = '';

    if (!p.ok) {
      html += '<div class="mon-hero">' +
        '<div class="mon-hero-left">' +
          '<div class="mon-hero-eyebrow">实时功耗</div>' +
          '<div class="mon-hero-num"><b>—</b><span>W</span></div>' +
        '</div>' +
        '<div class="mon-hero-right"><div class="mon-hero-eyebrow">口径</div>' +
          '<div class="mon-hero-side">不可用</div></div>' +
      '</div>';
      html += '<div class="mon-info">功耗数据不可用（既无 RAPL 实测，也无法按 TDP 估算）。</div>';
      return html;
    }

    var isRapl = p.source === 'rapl';
    var srcText = isRapl ? 'RAPL 实测 + 底座 / 硬盘估算' : '模型估算（无 RAPL）';

    /* --- ① 大数字区 --- */
    html += '<div class="mon-hero">' +
      '<div class="mon-hero-left">' +
        '<div class="mon-hero-eyebrow">实时功耗</div>' +
        '<div class="mon-hero-num"><b>' +
          (p.total === null || p.total === undefined ? '—' : fmt1(p.total)) +
        '</b><span>W</span></div>' +
      '</div>' +
      '<div class="mon-hero-right">' +
        '<div class="mon-hero-eyebrow">口径</div>' +
        '<div class="mon-hero-side">' + srcText + '</div>' +
      '</div>' +
    '</div>';

    /* --- ② 功耗堆叠条 + 图例 ---
       对齐 fnmonitor：堆叠条与图例同在一个 .card.pad 卡片里 */
    var parts = powerParts(p);
    var totalW = parts.reduce(function (a, x) { return a + x[1]; }, 0) || 1;
    if (parts.length) {
      html += '<div class="mon-stack-wrap">' +
        '<div class="mon-stack">' + parts.map(function (q) {
          var cls = q[2] === 'hatch' ? ' mono-hatch' : '';
          /* flex-grow 按部件功耗占总功耗的比例分配 */
          var bg = q[2] === 'hatch' ? '' : 'background:' + q[2] + ';';
          return '<i class="' + cls.trim() + '" style="' + bg +
            'flex-grow:' + ((q[1] / totalW) * 100).toFixed(2) + '"></i>';
        }).join('') + '</div>' +
        '<div class="mon-legend">' + parts.map(function (q) {
          var sw = q[2] === 'hatch'
            ? '<i class="mono-hatch"></i>'
            : '<i style="background:' + q[2] + '"></i>';
          return '<span>' + sw + esc(q[0]) + ' <b>' + fmt1(q[1]) + ' W</b></span>';
        }).join('') + '</div>' +
      '</div>';
    }

    /* --- ③ 趋势图（对齐 fnmonitor 的 SHEETS.power：堆叠条之后、部件明细之前）--- */
    if (window.FnStyleCompute && window.FnStyleCompute.chartsHtml) {
      html += window.FnStyleCompute.chartsHtml('power');
    }

    /* --- ④ 部件明细 --- */
    function w(v) {
      return (v === null || v === undefined || isNaN(v))
        ? '<span class="faint">—</span>' : fmt1(v) + ' W';
    }
    var detail = [];
    if (isRapl) {
      detail.push(['CPU 封装 Package', w(p.package)]);
      detail.push(['　核心 Core', w(p.core)]);
      detail.push(['　非核心 Uncore', w(p.uncore)]);
      detail.push(['内存 DRAM', w(p.dram)]);
    } else {
      detail.push(['CPU 封装 Package', w(p.package)]);
      detail.push(['　核心 Core', w(p.core)]);
      detail.push(['　非核心 Uncore', w(p.uncore)]);
      detail.push(['内存 DRAM', w(p.dram)]);
    }
    detail.push(['主板 / 其他底座', w(p.base)]);
    detail.push(['活动硬盘' + (p.disksActive ? '（' + p.disksActive + ' 块）' : ''), w(p.disksW)]);
    if (p.nics !== null && p.nics !== undefined) {
      /* ⚠️ v1.1.67：这里显示的是**物理网口数**（后端已改成只数有 PCI 设备
         链接的接口 / bonding 的物理 slave）。
         原来数的是 /sys/class/net 下的**网络接口数** —— bond0 聚合口、
         虚拟桥、容器口都会被算进去 → 显示成「2 口」，用户一看就怀疑
         「我 NAS 真有两个网口？」（老板原话：「我NAS设备有两个网口？？核实一下」）。
         → 数字改准，并在下面附一行接口明细供核对。 */
      detail.push(['网卡（物理口）', p.nics + ' 口 · 约 ' + fmt1(p.nics * 5) + ' W']);
    }
    html += '<div class="mon-cmp-sec"><span>部件明细</span>' +
      '<span class="faint">' + (isRapl ? 'RAPL 实测' : '模型估算') + '</span></div>';
    html += '<div class="mon-dkv">' + detail.map(function (kv) {
      return '<div class="mon-dkv-row"><span class="k">' + esc(kv[0]) + '</span>' +
        '<span class="v">' + kv[1] + '</span></div>';
    }).join('') + '</div>';

    /* --- 部件明细后的网卡接口清单（v1.1.67）---
       老板看到「网卡 2 口」会问「我 NAS 真有两个网口？」——
       这里把后端读到的**每个网络接口**都列出来（名称 / 是否物理口 /
       速率 / 状态），用户可以自己核对到底是几个物理网口。
       有 bonding 聚合口时同时列出它的 slave。 */
    var nd = p.nicDetail;
    if (nd && nd.length) {
      html += '<div class="mon-cmp-sec"><span>网卡接口</span>' +
        '<span class="faint">物理 ' + (p.nics || 0) + ' / 接口 ' +
        (p.nicsIface || nd.length) + '</span></div>';
      html += '<div class="mon-dkv">' + nd.map(function (x) {
        /* 三态：物理口 / 聚合口（bond，成员另列）/ 虚拟口
           隧道口（Tailscale / WireGuard 等）单独标 —— 它们不占物理网口，
           但常被误当成「一个网口」。 */
        var tag = x.kind === 'bond' ? '聚合口'
          : (x.kind === 'phys' || x.physical ? '物理口'
            : (x.tunnel ? '隧道口' : '虚拟口'));
        var bits = [];
        if (x.driver) bits.push(x.driver);
        if (x.speed && x.speed !== '-1' && x.speed) bits.push(x.speed + ' Mbps');
        if (x.state) bits.push(x.state);
        if (x.slaves && x.slaves.length) bits.push('成员 ' + x.slaves.join('/'));
        /* ⚠️ v1.1.68：带上内核接口索引号 —— 有值就证明这是内核登记在册的
           真接口（不是 /sys/class/net 下的内核符号链接），用户可据此自行核对。 */
        if (x.ifindex) bits.push('if#' + x.ifindex);
        return '<div class="mon-dkv-row"><span class="k">' + esc(x.name) +
          ' <span class="faint">' + tag + '</span></span>' +
          '<span class="v">' + esc(bits.join(' · ') || '—') + '</span></div>';
      }).join('') + '</div>';
    }

    /* --- ⑤ 用电与电费（异步加载） --- */
    html += '<div class="mon-cmp-sec"><span>用电与电费</span>' +
      '<span class="faint" data-pwr-rate></span></div>';
    html += '<div class="mon-dkv" data-pwr-energy><div class="mon-dkv-row">' +
      '<span class="k">今日</span><span class="v">统计加载中…</span></div></div>';

    /* --- ⑤-b 电价设置 ---
       ⚠️ v1.1.84：电价设置**已迁到「配置管理」抽屉**（thresholds-ui.js）。
       原来放在这里，是因为「上面就是电费数字，改价方便」；
       老板要求把配置统一收敛到设置抽屉 ⇒ 这里不再重复渲染，
       避免同一份配置出现两个入口、两套口径。
       抽屉里改完单价，这里的数字会在下次刷新时按新单价重算。 */

    /* --- ⑥ 估算参数 --- */
    var ep = p.estimation_params || {};
    if (ep.tdp_w !== undefined || ep.disk_typical_w !== undefined) {
      html += '<div class="mon-cmp-sec"><span>估算参数</span></div>';
      html += '<div class="mon-dkv">' +
        '<div class="mon-dkv-row"><span class="k">CPU TDP（无 RAPL 时）</span><span class="v">' +
          (ep.tdp_w !== undefined ? ep.tdp_w + ' W' : '—') + '</span></div>' +
        '<div class="mon-dkv-row"><span class="k">单盘典型功耗</span><span class="v">' +
          (ep.disk_typical_w !== undefined ? ep.disk_typical_w + ' W' : '—') + '</span></div>' +
        '<div class="mon-dkv-row"><span class="k">网卡固定功耗</span><span class="v">' +
          (ep.nic_fixed_w !== undefined ? ep.nic_fixed_w + ' W' : '—') + '</span></div>' +
        '<div class="mon-dkv-row"><span class="k">主板 / 其他底座</span><span class="v">' +
          (ep.base_w !== undefined ? ep.base_w + ' W' : '—') + '</span></div>' +
      '</div>';
    }

    /* --- 说明条 --- */
    html += '<div class="mon-info">有 RAPL 时它只测得 CPU 封装（与内存）功耗，' +
      '整机值再叠加主板底座与活动硬盘的典型功耗；无 RAPL 时按 TDP × CPU 占用估算。' +
      '休眠硬盘不计入。</div>';

    return html;
  }

  /* 板块趋势图（温度和散热 / 能耗各一组）
     挂在**独立宿主容器**里（#monTempCharts / #monPowerCharts）——
     总览卡片区每 3 秒轮询重建一次，趋势图放里面会被反复清空。
     宿主已建好图表时不重复创建，只刷新数据。 */
  function renderPanelCharts(d) {
    if (!window.FnStyleCompute) return;
    var groups = [
      /* ⚠️ v1.1.44：主页「温度和散热」板块按需求去掉
         CPU 温度 / 主板温度两张趋势图卡片 → onlyKeys 传空数组。
         抽屉内的温度趋势图保留（走 chartsHtml，不经过这里）。 */
      { host: '#monTempCharts', key: 'temp', only: [] },
      /* ⚠️ v1.1.50：主页「能耗」板块按用户要求去掉下方
         「整机功耗 / 累计用电」两张趋势图卡片（能耗详情抽屉里已有），
         同样 onlyKeys 传空数组。 */
      { host: '#monPowerCharts', key: 'power', only: [] }
    ];
    groups.forEach(function (g) {
      var host = $(g.host);
      if (!host) return;
      if (!host.querySelector('[data-pcharts="' + g.key + '"]')) {
        host.innerHTML = window.FnStyleCompute.panelChartsHtml(g.key, g.only);
        /* ⚠️ v1.1.65：rAF 兜底 —— 标签页切后台时 Chrome 冻结 rAF，
           双层 rAF 不执行 → 板块趋势图永远空白。 */
        var panelInited = false;
        var initPanelOnce = function () {
          if (panelInited) return;
          panelInited = true;
          window.FnStyleCompute.initPanelCharts(host, g.key);
        };
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { initPanelOnce(); });
        });
        setTimeout(initPanelOnce, 60);
      } else if (window.FnStyleCompute.refreshPanelCharts) {
        window.FnStyleCompute.refreshPanelCharts(g.key);
      }
    });
  }

  /* --------------------------------------------------------------------------
   用电统计的异步拉取
   -------------------------------------------------------------------------- */
  /* ⚠️ v1.1.50：失败退避 —— 原实现里请求失败时**不写时间戳**，
     pdaily / pstats 始终为 null → 每 3 秒轮询都会重新发起一次请求，
     用户看到「每日用电一直在读取」。
     修法：加 pstatsFailAt / pdailyFailAt，失败后进入 FAIL_BACKOFF
     （30 秒）冷却期，冷却期内直接返回旧值，不再打接口；
     失败时也把 loading 占位换成明确的空态文案，别一直转圈。 */
  var PFAIL_BACKOFF = 30000;

  /* /api/power/stats —— 今日 / 本月 / 本月预计 / 全年估算
     这个接口要扫历史库，比 /api/monitor 重，所以加了 TTL 缓存：
     能耗是慢变量，60 秒刷新一次足够。 */
  function ensurePowerStats() {
    var now = Date.now();
    if (pstats && now - pstatsAt < PSTAT_TTL) return pstats;
    /* 冷却期内不再重试 */
    if (pstatsFailAt && now - pstatsFailAt < PFAIL_BACKOFF) return pstats;
    if (!window.fetch) return pstats;
    /* ⚠️ v1.1.51：必须用相对路径 BASE + 'power/stats'，不能写 '/api/power/stats'。
       绝对路径在 fnOS 的 iframe 嵌入场景下会打到 fnOS 主站根路径（302 / 404），
       而不是本应用 —— 表现为右栏四格全是「--」、标题栏电费徽标也是「--」，
       但直接 curl 9910 端口却完全正常（假象：后端没事，是前端 URL 写错）。
       其余接口（monitor / storage）一直用的是相对路径 BASE + 'monitor'，
       只有这两个 power 接口漏了。 */
    return fetch(BASE + 'power/stats').then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) {
      if (j && j.success) {
        pstats = j;
        pstatsAt = Date.now();
        pstatsFailAt = 0;
        /* 拿到统计后重画总览四格（此时才有电费 / 预计值）*/
        if (lastData) renderPowerOverview(lastData);
        /* ⚠️ v1.1.69：概览六卡片的功耗副标题也依赖这份统计
           （「今日 0.60 kWh · ¥0.36」），拿到后要重画一次。 */
        if (lastData) renderOverview(lastData, pstats);
      } else {
        pstatsFailAt = Date.now();
        renderPowerStatsEmpty('用电统计暂不可用');
      }
      return pstats;
    }).catch(function () {
      pstatsFailAt = Date.now();
      renderPowerStatsEmpty('用电统计暂不可用');
      return pstats;
    });
  }

  /* /api/power/daily —— 每日用电柱状图
     数据按天聚合，一天只变一次，所以 TTL 给到 10 分钟。 */
  function ensurePowerDaily() {
    var now = Date.now();
    if (pdaily && now - pdailyAt < PDaily_TTL) return pdaily;
    /* 冷却期内不再重试（v1.1.50） */
    if (pdailyFailAt && now - pdailyFailAt < PFAIL_BACKOFF) return pdaily;
    if (!window.fetch) return pdaily;
    return fetch(BASE + 'power/daily?days=' + PDaily_DAYS).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) {
      if (j && j.success) {
        pdaily = j;
        pdailyAt = Date.now();
        pdailyFailAt = 0;
        renderPowerDaily(pdaily);
      } else {
        pdailyFailAt = Date.now();
        renderPowerDailyEmpty();
      }
      return pdaily;
    }).catch(function () {
      pdailyFailAt = Date.now();
      renderPowerDailyEmpty();
      return pdaily;
    });
  }

  /* 用电统计空态（v1.1.50：失败时不再一直转圈） */
  function renderPowerStatsEmpty(msg) {
    var box = $('#monPowerStats');
    if (!box) return;
    if (box.querySelector('.stat-cells')) return;   /* 已有数据就不覆盖 */
    box.innerHTML = '<div class="mon-info">' + esc(msg) + '</div>';
  }

  /* 每日用电空态（v1.1.50） */
  function renderPowerDailyEmpty() {
    var box = $('#monPowerDaily');
    if (!box) return;
    if (box.querySelector('.pwr-bars')) return;      /* 已有数据就不覆盖 */
    box.innerHTML = '<div class="chart-head"><div class="chart-title">近 30 天用电</div></div>' +
      '<div class="chart-empty-s">每日用电数据暂不可用，稍后会自动重试</div>';
  }

  /* 拉取用电量与电费统计并回填到能耗板块
     ⚠️ v1.1.48：加 60 秒结果缓存 —— 抽屉 live 更新（compute.js updateLive）
     每 3 秒重渲染能耗抽屉并调用本函数回填，接口要扫历史库较重，
     能耗又是慢变量（与 ensurePowerStats 的 PSTAT_TTL 同款思路）。 */
  var pwrEnergyCache = null, pwrEnergyAt = 0, PWR_ENERGY_TTL = 60000;
  function applyPowerEnergy(box, j) {
    if (!box || !j) return;
    /* ⚠️ v1.1.63：单一单价，不再区分「阶梯/固定」两种模式。 */
    var rateEl = box.querySelector('[data-pwr-rate]');
    if (rateEl) {
      rateEl.textContent = '电价 ' + (j.rate_yuan_per_kwh || 0.5224) + ' 元/kWh';
    }
    var host = box.querySelector('[data-pwr-energy]');
    if (!host) return;
    var rows =
      '<div class="mon-dkv-row"><span class="k">今日</span><span class="v">' +
        fmtKwh(j.today_kwh) + ' kWh · ' + fmtYuan(j.today_yuan) + ' 元</span></div>' +
      '<div class="mon-dkv-row"><span class="k">本月</span><span class="v">' +
        fmtKwh(j.month_kwh) + ' kWh · ' + fmtYuan(j.month_yuan) + ' 元</span></div>';
    /* ⚠️ v1.1.63：谷/平构成行、逐档电量行全部删除 ——
       电价模型已简化为「总量 × 单一单价」，没有分段也没有档位。 */
    rows += '<div class="mon-dkv-row"><span class="k">开机以来（按当前功率估）</span><span class="v">' +
      fmtKwh(j.boot_kwh) + ' kWh</span></div>';
    host.innerHTML = rows;
  }
  function loadPowerEnergy(box) {
    if (!box || !window.fetch) return;
    /* 缓存命中：直接回填，不发请求 */
    if (pwrEnergyCache && Date.now() - pwrEnergyAt < PWR_ENERGY_TTL) {
      applyPowerEnergy(box, pwrEnergyCache);
      return;
    }
    fetch(BASE + 'power/stats').then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) {
      if (!j || !j.success) return;
      pwrEnergyCache = j;
      pwrEnergyAt = Date.now();
      applyPowerEnergy(box, j);
    }).catch(function () { /* 静默失败：保留「统计加载中…」 */ });
  }

  /* --------------------------------------------------------------------------
     电价设置面板（v1.1.57 起）—— v1.1.84 迁到「配置管理」抽屉
     -------------------------------------------------------------------------- */
  /* ⚠️ v1.1.84 变更记录（重要，别改回去）：
       ① 电价面板的**实现**仍然留在 monitor.js（这里是全应用唯一一份），
          但改成**可复用模块**导出（window.FnStyleTariff）——
          之前它是「能耗计算抽屉」的一部分（powerSheetHtml 里硬编码渲染），
          老板要求把配置统一收敛到设置抽屉 ⇒ 渲染位置改了，实现没动。
       ② 为什么实现不搬去 thresholds-ui.js：
          电价属于「监控/能耗」域的数据（/api/power/tariff、power_tariff.py），
          放在 monitor.js 里与「配置管理」抽屉的**编排**职责分离更清晰；
          thresholds-ui.js 只负责「把它画到抽屉里」。
       ③ 旧入口 loadTariff(scope) 保留兼容：能耗抽屉里已经没有电价面板了，
          所以它会直接返回（找不到 [data-tariff-box]），
          但 compute.js 的调用点不必同步删 —— 少改一处少一处风险。 */
  var tariffCache = null, tariffAt = 0, TARIFF_TTL = 60000;

  /* 电价面板 HTML（v1.1.63 简化版）
     ------------------------------------------------------------
     老板决定：「不使用阶梯电价计费方式，根据成都最低档对应的电价进行计算
     （并提供可配置能力），删除阶梯计费和档位信息等，也不区分区域，
     就是一个电费单价的配置，并增加对应的说明即可。」

     → 面板里**只剩一个输入框**：电费单价（元/kWh）
     → 删掉：启用开关 / 地区下拉 / 低谷时段 / 电压等级 /
             档位价目表 / 季节参数 / 自定义单价 / 预计电费单价
     → 下面配一段**口径说明**（后端给的 note），讲清楚这个单价怎么用。 */
  function tariffPanelHtml(d) {
    var cfg = (d && d.config) || {};
    var html = '';

    /* 电费单价（唯一配置项） */
    var rateVal = (d && d.rate !== undefined && d.rate !== null)
      ? d.rate : (cfg.rate === undefined || cfg.rate === null ? 0.5224 : cfg.rate);
    html += '<div class="tariff-field"><span class="tariff-lbl">电费单价</span>' +
      '<input class="tariff-input" type="number" step="0.0001" min="0" max="10" ' +
      'data-tariff-rate value="' + esc(String(rateVal)) + '">' +
      '<span class="tariff-unit">元/kWh</span></div>';

    /* 口径说明（后端给的权威文案，避免前后端各写一遍） */
    var note = (d && d.note) || '';
    if (note) {
      html += '<div class="tariff-note" data-tariff-note>' + esc(note) + '</div>';
    }

    /* 保存按钮 */
    html += '<div class="tariff-actions">' +
      '<button type="button" class="tariff-save" data-tariff-save>保存电价设置</button>' +
      '<span class="tariff-msg" data-tariff-msg></span></div>';
    return html;
  }

  /* 采集电价面板的当前表单状态
     ⚠️ v1.1.63 简化：面板只剩一个「电费单价」输入框。
        v1.1.84：面板已迁到「配置管理」抽屉 —— 那个抽屉**不会**每 3 秒重建，
        所以「重绘吃掉用户输入」的问题自然消失；
        但 capture/restore 仍然保留（保存成功后要重绘面板，
        此时用户的输入值不能被后端旧值覆盖）。
        （v1.1.62 及之前还要抓 enabled / valley_start / valley_end /
          voltage / proj_rate / 季节参数 —— 全部随电价模型简化而废弃。） */
  function captureTariffForm(box) {
    if (!box) return null;
    var rt = box.querySelector('[data-tariff-rate]');
    return {
      rate: (rt && rt.value !== '') ? rt.value : null
    };
  }

  /* 回填采集到的表单状态
     ⚠️ v1.1.63：单价是**全局**配置（不分地区），任何时候都回填。 */
  function restoreTariffForm(box, st) {
    if (!box || !st) return;
    var rt = box.querySelector('[data-tariff-rate]');
    if (!rt) return;
    if (st.rate !== null && st.rate !== undefined) {
      rt.value = st.rate;
      return;
    }
    /* 没有用户输入 → 用后端给的生效单价 */
    var d = box.__tariffData;
    var cfg = (d && d.config) || {};
    var v = (d && d.rate !== undefined && d.rate !== null)
      ? d.rate : cfg.rate;
    rt.value = (v === undefined || v === null) ? '0.5224' : String(v);
  }

  function applyTariff(tbox, d) {
    if (!tbox) return;
    /* 重绘前捕获用户未保存的编辑，重绘后回填 */
    var st = captureTariffForm(tbox);
    tbox.innerHTML = tariffPanelHtml(d);
    bindTariffEvents(tbox);
    /* 挂上本次数据 */
    tbox.__tariffData = d;
    /* 标记已渲染 → 之后 live 重建不再重绘面板（保住用户输入与焦点） */
    tbox.setAttribute('data-tariff-rendered', '1');
    restoreTariffForm(tbox, st);
  }

  function bindTariffEvents(box) {
    var btn = box.querySelector('[data-tariff-save]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var msg = box.querySelector('[data-tariff-msg]');
      var rt = box.querySelector('[data-tariff-rate]');
      /* ⚠️ v1.1.63：只有一个配置项 —— 电费单价。
         空串要当「未填」处理（Number('') 是 0 而不是 NaN），
         兜底用后端给的生效单价。 */
      var payload = {};
      var cfgSave = (box.__tariffData && box.__tariffData.config) || {};
      if (rt) {
        var rv = Number(rt.value);
        payload.rate = (rt.value !== '' && isFinite(rv)
          && rv >= 0 && rv <= 10) ? rv
          : (Number(cfgSave.rate) || 0.5224);
      } else {
        payload.rate = Number(cfgSave.rate) || 0.5224;
      }
      btn.disabled = true;
      if (msg) msg.textContent = '保存中…';
      fetch(BASE + 'power/tariff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, j: j };
        });
      }).then(function (res) {
        if (!res.ok || !res.j || !res.j.success) {
          if (msg) msg.textContent = (res.j && res.j.error) || '保存失败';
          btn.disabled = false;
          return;
        }
        /* 失效统计缓存 → 立刻重画卡片，让新电价生效 */
        pstats = null; pstatsAt = 0;
        pwrEnergyCache = null; pwrEnergyAt = 0;
        tariffCache = res.j; tariffAt = Date.now();
        /* 重渲染面板（会把「已保存」提示一起换掉，所以先渲染再写提示）*/
        applyTariff(box, res.j);
        var msg2 = box.querySelector('[data-tariff-msg]');
        if (msg2) msg2.textContent = '已保存';
        if (lastData) renderPowerOverview(lastData);
        ensurePowerStats();
        setTimeout(function () {
          var m2 = box.querySelector('[data-tariff-msg]');
          if (m2) m2.textContent = '';
        }, 2500);
      }).catch(function () {
        if (msg) msg.textContent = '保存失败（网络错误）';
        btn.disabled = false;
      });
    });
  }

  /* 电价面板加载
     ⚠️ v1.1.57 修复：调用方传进来的是**整个抽屉 body**，
        不是电价面板容器。早期版本直接 box.innerHTML = ...，
        把抽屉里原有的内容（实时功耗、堆叠条、趋势图、部件明细、
        用电与电费、估算参数）**全部覆盖**掉了 —— 用户反馈「抽屉内容没了」。
        现在必须先定位到 [data-tariff-box] 再只更新它。
     ⚠️ v1.1.84：能耗计算抽屉里已不再有电价面板 ⇒ 这个入口基本是空转，
        真正被用的是 FnStyleTariff.mount() （配置管理抽屉调用）。 */
  function loadTariff(scope) {
    if (!scope || !window.fetch) return;
    var tbox = scope.querySelector ? scope.querySelector('[data-tariff-box]') : null;
    if (!tbox) return;
    /* ⚠️ v1.1.58：面板已经渲染过就别再重绘。
       能耗抽屉每 3 秒 live 重建一次 → loadTariff 每轮都会被调用 →
       每次都重绘面板 → 用户正在输入的「自定义单价」被清空、
       光标也被踢回开头。重绘一次就好，之后只在**用户主动操作**
       （切地区 / 点保存）时才重绘。
       缓存过期时仍然后台刷新 tariffCache，但不重绘面板。 */
    if (tbox.getAttribute('data-tariff-rendered') === '1') {
      if (!tariffCache || Date.now() - tariffAt >= TARIFF_TTL) {
        fetch(BASE + 'power/tariff').then(function (r) {
          return r.ok ? r.json() : null;
        }).then(function (j) {
          if (j && j.success) {
            tariffCache = j;
            tariffAt = Date.now();
          }
        }).catch(function () { });
      }
      return;
    }
    if (tariffCache && Date.now() - tariffAt < TARIFF_TTL) {
      applyTariff(tbox, tariffCache);
      return;
    }
    fetch(BASE + 'power/tariff').then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) {
      if (!j || !j.success) {
        tbox.innerHTML = '<div class="mon-info">电价配置读取失败</div>';
        return;
      }
      tariffCache = j;
      tariffAt = Date.now();
      applyTariff(tbox, j);
    }).catch(function () {
      tbox.innerHTML = '<div class="mon-info">电价配置读取失败</div>';
    });
  }

  /* --------------------------------------------------------------------------
     电价配置面板 —— 对外接口（v1.1.84）
     --------------------------------------------------------------------------
     供「配置管理」抽屉（thresholds-ui.js）调用。

     mount(host, opts)
       host : 面板容器元素（会被整体替换内容）
       opts : { onSaved: fn }  保存成功后的回调（可选）
       返回 Promise（数据加载完成后 resolve），便于调用方串联。
     -------------------------------------------------------------------------- */
  function mountTariff(host, opts) {
    if (!host) return Promise.resolve(null);
    opts = opts || {};
    function paint(d) {
      if (!d) return;
      tariffCache = d;
      tariffAt = Date.now();
      applyTariff(host, d);
    }
    if (tariffCache && Date.now() - tariffAt < TARIFF_TTL) {
      paint(tariffCache);
      return Promise.resolve(tariffCache);
    }
    host.innerHTML = '<div class="mon-info">电价配置加载中…</div>';
    return fetch(BASE + 'power/tariff').then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) {
      if (!j || !j.success) {
        host.innerHTML = '<div class="mon-info">电价配置读取失败</div>';
        return null;
      }
      paint(j);
      if (opts.onSaved) opts.onSaved(j);
      return j;
    }).catch(function () {
      host.innerHTML = '<div class="mon-info">电价配置读取失败</div>';
      return null;
    });
  }

  function fmtKwh(v) {
    var n = Number(v);
    if (isNaN(n)) return '—';
    return n >= 10 ? n.toFixed(2) : n >= 0.1 ? n.toFixed(3) : n.toFixed(4);
  }

  function fmtYuan(v) {
    var n = Number(v);
    if (isNaN(n)) return '—';
    return n.toFixed(2);
  }

  /* ---------------- 总渲染 ---------------- */

  function render(d) {
    if (!d || d.success === false) {
      var msg = (d && (d.error || d.message)) || '监控服务不可用';
      /* ⚠️ v1.1.89：统一走 showMonitorUnavailable()，
         保证各区块**全部**被替换，不再有任何区块永远停在 loading 骨架上。 */
      showMonitorUnavailable(msg);
      /* ⚠️⚠️ v1.1.89：概览容器（#monOverview）**不能**只靠 showMonitorUnavailable()。
         它由 overview.js 专门管理（buildCards + _built 缓存）——
         一旦被 innerHTML 清空，后续**没有任何代码会重建它**
         （错误/预热分支都直接 return，不会调用 renderOverview）
         ⇒ 六张卡片永远消失。
         所以这里必须**照样调一次 renderOverview()** ——
         overview.js 的 render() 对缺失数据是安全的（各字段用 okNum 判空），
         会渲染出「-- / 无数据」占位，而不是空白。
         （配套：overview.js 的 render() 已加自愈 —— 容器被清空过就重建。） */
      renderOverview(d && d.success !== false ? d : {}, pstats);
      setStatus('err', msg);
      return;
    }
    if (d.warming) {
      /* ⚠️ v1.1.89：预热分支原来只改状态栏就 return ——
         三处 loading 骨架（「正在采集监控数据…」「汇总关键指标…」「读取存储信息…」）
         原样保留，**看起来像彻底坏掉**。
         现在把骨架换成明确的「预热中」提示，并安排重试，
         服务跑出第一帧后自动重画。 */
      setStatus('', d.message || '监控数据采集中，请稍候…');
      showMonitorUnavailable('监控数据采集中，请稍候…');
      /* 同上：概览容器必须重建，否则预热结束后六卡不会回来。
         ⚠️ 传 {} 而不是 null —— overview.js 内部对缺失字段有 okNum 判空，
            传空对象即可渲染出「-- / 无数据」占位。 */
      renderOverview({}, pstats);
      scheduleRetry();
      return;
    }
    // system（主机名 / 型号）挂在顶层，转给存储板块渲染机箱顶栏
    if (d.storage && typeof d.storage === 'object') {
      d.storage._system = d.system || null;
    }
    renderStorage(d.storage);
    renderCore(d);
    /* 温度和散热 / 能耗：总览卡片形态（对齐 fnmonitor 的 overview 模块）*/
    renderTempOverview(d);
    renderPowerOverview(d);
    /* ⚠️ v1.1.69：系统重要内容概览（六卡片）——
       放在存储卡片**上方**。它需要 powerStats（独立接口异步拿），
       所以把已有的 pstats 一并传进去。 */
    renderOverview(d, pstats);
    /* 用电统计与每日用电是独立接口，异步补齐后回调重画 */
    ensurePowerStats();
    ensurePowerDaily();
    /* 板块趋势图宿主（内容区已换形态，图表挂在独立宿主上）*/
    renderPanelCharts(d);

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

  /* ⚠️ v1.1.89：请求超时保护（修「页面永远停在 loading 骨架」）
     ------------------------------------------------------------
     现象（老板 2026-11-23 实测）：应用重装/服务重启期间打开页面，
     概览、存储、温度、能耗**全部卡在 index.html 的静态 loading 骨架**
     （「正在采集监控数据…」「汇总关键指标…」「读取存储信息…」），
     且**没有任何错误提示**，刷新前一直不动。

     真凶：① fetchMonitor() 的 fetch **既没有 .catch 也没有超时** ——
     ② 服务重启期间，`api/monitor` 的 TCP 连接会**建立但迟迟不返回**
        （后端 Python 服务刚起、采集线程还没跑出第一帧），
        fetch 处于 **pending** 状态，**既不 resolve 也不 reject**；
     ③ refresh() 因此永不进入 render() ⇒ 三处 loading 骨架原样保留；
     ④ 没有超时 ⇒ 永远等下去 ⇒ 用户看到「彻底坏掉」。

     ★ 注意：光加 .catch **不够** —— pending 的请求不会触发 catch。
       必须用 AbortController **主动超时中断**。

     修法：① fetchMonitor() 加 8 秒超时（AbortController）；
          ② 超时/失败统一收敛成 {success:false, error:...}，
             由 render() 的既有分支显示明确的错误文案；
          ③ refresh() 补 .catch 兜底（防止 render() 内部抛错导致整链断掉）；
          ④ 服务不可用时**自动重试**（1s/2s/4s 退避），服务恢复后自动接上。 */
  var MON_TIMEOUT_MS = 8000;
  /* 连续失败次数与重试定时器 */
  var monFailStreak = 0;
  var monRetryTimer = null;
  /* ⚠️ v1.1.89：防并发标记（失败重试与自动刷新可能同时触发） */
  var monInFlight = false;
  /* refresh() 因「已有请求在飞」被跳过时的返回值 */
  var MON_SKIPPED = { skipped: true, success: false, error: '已有刷新请求在进行中' };

  function fetchMonitor() {
    var ctrl = null;
    var timedOut = false;
    try {
      ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    } catch (e) { ctrl = null; }
    /* ⚠️ 变量名不要用 timer —— 模块级已有 `var timer`（自动刷新 setInterval），
       这里用 `to`（timeout）避免遮蔽。 */
    var to = null;
    if (ctrl) {
      to = setTimeout(function () {
        timedOut = true;
        try { ctrl.abort(); } catch (e) {}
      }, MON_TIMEOUT_MS);
    }
    var init = { method: 'GET', headers: {} };
    if (ctrl) init.signal = ctrl.signal;
    return fetch(BASE + 'monitor', init)
      .then(function (r) {
        if (to) clearTimeout(to);
        return r.text().then(function (txt) {
          try { return JSON.parse(txt); }
          catch (e) { return { success: false, error: '响应解析失败：' + txt.slice(0, 120) }; }
        });
      })
      .catch(function (e) {
        if (to) clearTimeout(to);
        if (timedOut) {
          return { success: false, error: '监控数据请求超时（服务可能正在启动），稍后自动重试' };
        }
        return { success: false, error: '后端服务不可用，请确认应用已启动' };
      });
  }

  function refresh() {
    /* ⚠️ v1.1.89：防并发 —— 失败重试（1s 起）与自动刷新（3s）可能同时触发。
       没有这层保护时，服务挂起期间会堆积多个 pending 请求。 */
    if (monInFlight) return Promise.resolve(MON_SKIPPED);
    monInFlight = true;
    return fetchMonitor().then(function (d) {
      monInFlight = false;
      /* ⚠️ v1.1.89：三态判定，不能只看 d.success ——
         后端预热时返回的是 {success:true, available:false, warming:true}，
         若按「success !== false 即恢复」处理，会把失败计数清零、
         撤掉重试定时器 ⇒ 预热期间的退避重试被自己掐掉。 */
      if (d && d.success !== false && !d.warming) {
        /* 完全恢复：清失败计数、撤掉重试定时器 */
        monFailStreak = 0;
        if (monRetryTimer) { clearTimeout(monRetryTimer); monRetryTimer = null; }
      } else {
        /* 失败或预热中：走退避重试（scheduleRetry 内部负责计数） */
        scheduleRetry();
      }
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
      // ⚠️ v1.1.48：计算抽屉（处理器/内存/温度/能耗）开着时同步刷新
      // 此前抽屉只在打开时渲染一次快照，之后 hero/四格/温度计全部冻结
      // （用户反馈「温度与散热抽屉界面打开后其中显示的温度都不变化」）
      if (window.FnStyleCompute && window.FnStyleCompute.isOpen &&
          window.FnStyleCompute.isOpen() && window.FnStyleCompute.updateLive) {
        window.FnStyleCompute.updateLive(d);
      }
      return d;
    })
    /* ⚠️ v1.1.89：refresh() 原本**没有 .catch** ——
       render() 内部任何一处抛错都会变成未捕获的 Promise rejection，
       整条刷新链断掉，页面就永远停在 loading 骨架且无提示。
       这里兜住，并把失败计入退避重试。 */
    .catch(function (e) {
      monInFlight = false;
      /* ⚠️ 失败计数只在 scheduleRetry() 里递增一次 ——
         这里再 ++ 会让退避延迟翻倍（1s→2s→8s→10s），收敛过慢。 */
      var msg = '监控数据刷新失败：' + ((e && e.message) || e);
      setStatus('err', msg);
      showMonitorUnavailable(msg);
      scheduleRetry();
      return null;
    });
  }

  /* ⚠️ v1.1.89：把所有「loading 骨架」统一换成明确提示。
     ★ 容器清单必须与 index.html 的静态骨架**一一对应**：
       monStorage（读取存储信息）/ monCore（读取运行状态）/
       monTempOverview（读取传感器）/ monTempSide（读取风扇）/
       monPowerOverview（读取功耗）/ monPowerStats（读取用电统计）/
       monPowerDaily（读取每日用电）
     —— 漏任何一个，那个区块就会永远停在转圈骨架上（= 老板截图的现象）。

     ⚠️ **monOverview 不在本清单里** —— 它由 renderOverview() 独家管理。
        原因：overview.js 内部有 _built 缓存（buildCards 只建一次），
        容器一旦被 innerHTML 清空且**没有** renderOverview() 重建，
        六张卡片就永远消失（v1.1.89 实测踩过）。
        所以概览容器改由 renderOverview 在**所有三条路径**
        （正常 / 错误 / 预热）都调用 —— 它对缺失数据安全（渲染「-- / 无数据」占位），
        且 overview.js 未加载时 renderOverview 自身有兜底文字（不会转圈）。 */
  var MON_SKELETON_IDS = [
    'monStorage', 'monCore', 'monTempOverview',
    'monTempSide', 'monPowerOverview', 'monPowerStats', 'monPowerDaily'
  ];

  function showMonitorUnavailable(msg) {
    MON_SKELETON_IDS.forEach(function (id) {
      var b = $('#' + id);
      if (b) b.innerHTML = emptyBox(msg);
    });
  }

  /* ⚠️ v1.1.89：服务不可用时的自动重试（1s → 2s → 4s → 8s → 10s 退避）。
     服务重启完成后页面能**自动接上**，不必用户手动刷新。
     ⚠️ 失败计数只在这里递增（refresh() 的 catch 里不要再 ++）。 */
  function scheduleRetry() {
    if (monRetryTimer) return;
    monFailStreak = Math.min(monFailStreak + 1, 5);
    var delay = Math.min(1000 * Math.pow(2, monFailStreak - 1), 10000);
    monRetryTimer = setTimeout(function () {
      monRetryTimer = null;
      /* ⚠️ v1.1.89：本次重试可能被「防并发」跳过（已有请求在飞）。
         若就此结束，重试链会**断掉** —— 页面永远不再自动恢复。
         所以跳过时必须再排一次。 */
      refresh().then(function (r) {
        if (r && r.skipped) scheduleRetry();
      });
    }, delay);
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

  /* --------------------------------------------------------------------------
     温度 / 能耗的详情抽屉入口
     -------------------------------------------------------------------------- */
  /* 两种入口都指向同一个抽屉（fnmonitor 的 SHEETS）：
       ① 卡片头右上角的「详情 ›」按钮（data-sheet="temp" / "power"）
       ② 整张总览卡片可点（fnmonitor 的 .card.pad.tap，data-sheet 同款）
     抽屉内容由 compute.js 的 openCompute 渲染。 */
  function bindSheetDrawer() {
    if (bindSheetDrawer._done) return;
    bindSheetDrawer._done = true;

    function open(kind) {
      if (!window.FnStyleCompute || !window.FnStyleCompute.open) return;
      window.FnStyleCompute.open(kind, lastData);
    }

    /* 按钮点击 */
    document.addEventListener('click', function (e) {
      var el = e.target.closest ? e.target.closest('[data-sheet]') : null;
      if (!el) return;
      /* 卡片本身也可点（.ov-tap），但里面的按钮会冒泡到卡片，
         这里统一处理并阻止卡片再处理一次。 */
      var kind = el.getAttribute('data-sheet');
      if (!kind) return;
      e.preventDefault();
      e.stopPropagation();
      open(kind);
    });

    /* 键盘可达：卡片可聚焦时按 Enter / Space 打开 */
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var el = e.target.closest ? e.target.closest('.ov-tap[data-sheet]') : null;
      if (!el) return;
      e.preventDefault();
      open(el.getAttribute('data-sheet'));
    });
  }

  /* ---------------- 初始化 ---------------- */

  /* --------------------------------------------------------------------------
     概览「存储」卡的点击入口（v1.1.78）
     --------------------------------------------------------------------------
     老板需求：「存储卡片没有点击事件，请添加」。
     实现：**复用已有的硬盘详情抽屉**（monDrawerOverlay）—— 它已经有
     顶部盘位下拉、容量条、SMART 明细、实时读写曲线，没必要另造一个。

     ⚠️ 为什么不走 data-sheet：
        bindSheetDrawer 会把任何 [data-sheet] 交给 compute.js 的 openCompute，
        而 openCompute 只有 cpu/mem/temp/power 四个分支，storage 会掉进
        else 兜底 → **误开「处理器」抽屉**（张冠李戴）。
        所以概览存储卡用独立的 data-ov-action="disk" 属性。

     默认打开哪块盘？按「问题优先」挑：
       ① 有故障分级（crit）的盘
       ② 有 SMART 告警（warn）的盘
       ③ 容量偏满（≥ capWarn）的盘
       ④ 都正常 → 第一块盘（按盘位排序）
     这样点「存储」卡能**直接看到有问题的那块盘**，而不是随便一块。 */
  function pickOverviewDiskDev() {
    var st = lastData && lastData.storage ? lastData.storage : null;
    var disks = (st && st.disks) || [];
    if (!disks.length) return '';

    var sorted = disks.slice().sort(byVol);

    /* ① crit 分级 */
    for (var i = 0; i < sorted.length; i++) {
      if (String(sorted[i].grade || '').toLowerCase() === 'crit') return sorted[i].name;
    }
    /* ② SMART 告警（warn 级计数非零） */
    for (var j = 0; j < sorted.length; j++) {
      if (smartAlerts(sorted[j]).lv === 'warn') return sorted[j].name;
    }
    /* ③ 容量偏满（复用现成的 capLevel：≥85 留意 / ≥95 故障） */
    for (var k = 0; k < sorted.length; k++) {
      if (capLevel(sorted[k].percent)) return sorted[k].name;
    }
    /* ④ 兜底：第一块盘 */
    return sorted[0].name;
  }

  /* 概览卡的动作入口：action → 具体行为 */
  function openOverviewAction(action) {
    if (action === 'disk') {
      var dev = pickOverviewDiskDev();
      if (!dev) return false;
      openDiskDrawer(dev);
      return true;
    }
    return false;
  }

  /* 事件绑定：概览「存储」卡点击 / 键盘可达。
     用**事件委托**挂在 document 上 —— 概览区是懒加载的（切到监控标签才渲染），
     直接给元素绑监听会漏掉后渲染出来的卡片。 */
  function bindOverviewAction() {
    if (bindOverviewAction._done) return;
    bindOverviewAction._done = true;

    document.addEventListener('click', function (e) {
      var el = e.target.closest ? e.target.closest('[data-ov-action]') : null;
      if (!el) return;
      var action = el.getAttribute('data-ov-action');
      if (!action) return;
      e.preventDefault();
      e.stopPropagation();
      openOverviewAction(action);
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var el = e.target.closest ? e.target.closest('.ov-tap[data-ov-action]') : null;
      if (!el) return;
      e.preventDefault();
      openOverviewAction(el.getAttribute('data-ov-action'));
    });
  }

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
    /* 温度 / 能耗的「详情 ›」按钮 + 整张总览卡片可点 */
    bindSheetDrawer();
    /* 概览「存储」卡点击 → 打开硬盘详情抽屉（v1.1.78） */
    bindOverviewAction();
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
    /* ⚠️ v1.1.55：测试钩子 —— 供 verify/verify_monitor_render.js 注入
       /api/power/stats 的结果，验证「标题栏徽标 = 摘要行 = 卡内格」
       三处电费逐字一致。生产路径不调用。 */
    __setPowerStatsForTest: function (s) { pstats = s; pstatsAt = Date.now(); },
    start: function () { refresh(); startAuto(); },
    stop: stopAuto,
    // 供 compute.js 的详情抽屉使用：取最近一次采集数据
    lastData: function () { return lastData; },
    // 温度 / 能耗抽屉的内容（1:1 对齐 fnmonitor 的 SHEETS.temp / SHEETS.power）
    tempSheetHtml: tempSheetHtml,
    tempPointCount: tempPointCount,
    powerSheetHtml: powerSheetHtml,
    // 抽屉里的用电与电费需要异步回填
    loadPowerEnergy: loadPowerEnergy,
    loadTariff: loadTariff,
    // 滚动锁：抽屉打开/关闭时成对调用，避免锁计数泄漏
    lockScroll: lockPageScroll,
    unlockScroll: unlockPageScroll
  };

  /* --------------------------------------------------------------------------
     电价配置面板（v1.1.84 新增对外接口）
     --------------------------------------------------------------------------
     「配置管理」抽屉（thresholds-ui.js）用它把电价面板画出来。
     为什么要独立成一个模块而不是直接在 thresholds-ui.js 里写：
       ① 电价的**实现**（面板 HTML / 读写 / 校验 / 保存 / 口径说明）
          属于监控域，与 /api/power/tariff、power_tariff.py 同源，
          放在 monitor.js 里只有一份，不会出现「两处各写一遍」；
       ② thresholds-ui.js 的职责是「**编排**设置抽屉」，
          不应该再去实现电价业务逻辑；
       ③ 加载顺序无要求 —— 阈值抽屉是用户点了才打开的，
          那时 monitor.js 早已加载完毕。
     -------------------------------------------------------------------------- */
  window.FnStyleTariff = {
    /* 把电价面板挂到 host 里（host 会被整体替换内容）。
       返回 Promise，数据到位后 resolve。 */
    mount: mountTariff,
    /* 面板 HTML 生成（供需要自己拼 DOM 的场景） */
    html: tariffPanelHtml,
    /* 保存成功后让所有「依赖电费数字」的地方重算 */
    invalidateStats: function () {
      pstats = null; pstatsAt = 0;
      pwrEnergyCache = null; pwrEnergyAt = 0;
      if (lastData) renderPowerOverview(lastData);
      ensurePowerStats();
    }
  };
})();
