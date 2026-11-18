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

  function smartFlagsHtml(d) {
    /* 有告警计数的项标红，无数据的显示 — */
    var out = '';
    SMART_KEYS.forEach(function (k) {
      var v = d[k[0]];
      var txt = (v === null || v === undefined || v === '') ? '—' : String(v);
      var cls = (v !== null && v !== undefined && v !== '' && Number(v) > 0) ? 'crit' : '';
      out += '<span class="mon-smart-flag ' + cls + '" title="' + esc(k[1]) + '">' +
        esc(k[1]) + ' <b>' + esc(txt) + '</b></span>';
    });
    return out;
  }

  /* ==========================================================================
     硬盘详情抽屉（点击盘位卡片从右侧滑出）
     呈现内容对齐 fnmonitor 的「硬盘」详情面板（.sheet disk/<dev>）：
     容量大数字 → 容量条 → 告警/休眠提示 → 实时读写 → SMART 表 → 信息表。
     ========================================================================== */

  var drawerOpen = false;
  var drawerDev = null;

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

  /* 信息表（型号 / 序列号 / 类型 / 容量 / 挂载点 / 所属空间） */
  function dwInfoKv(d) {
    var mounts = (d && d.mounts) || [];
    var kv = [
      ['型号', esc(vendorModel(d) || '未知型号')],
      ['序列号', d && d.serial ? '<span class="mono">' + esc(d.serial) + '</span>' : '<span class="faint">—</span>'],
      ['类型', esc(d && d.type ? d.type : '未知')],
      ['硬盘容量', esc(d && d.sizeText ? d.sizeText : fmtDisk((d && d.size) || 0))],
      ['挂载点', mounts.length
        ? '<span class="mono">' + esc(mounts.join('、')) + '</span>'
        : '<span class="faint">未挂载</span>'],
      ['所属存储空间', d && volOf(d) ? '存储空间 vol' + esc(volOf(d)) : '<span class="faint">—</span>']
    ];
    return '<dl class="mon-dw-kv">' + kv.map(function (p) {
      return dwKvRow(p[0], p[1]);
    }).join('') + '</dl>';
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
    var titleEl = $('#monDrawerTitle');
    var subEl = $('#monDrawerSub');
    if (!body || !titleEl || !subEl) return;

    if (!d) {
      titleEl.textContent = '未找到硬盘';
      subEl.textContent = dev || '';
      body.innerHTML = '<div class="mon-dw-note">它可能已被移除，或尚未被识别。</div>';
      showDrawer();
      return;
    }

    var led = bayLedState(d);
    var hasCap = Number(d.total) > 0;
    var pct = Number(d.percent) || 0;
    var io = d._io;

    /* 标题：物理位置；副标题：设备名 · 型号（fnmonitor 同款结构） */
    titleEl.textContent = '硬盘 ' + volLabel(d);
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
    if (notes) html += notes;

    /* --- 实时读写 --- */
    if (!d.standby) {
      html += '<div class="mon-dw-sec">实时读写<span class="faint">近 5 分钟</span></div>';
      html += '<div class="mon-dw-card">' +
        '<dl class="mon-dw-kv">' +
        dwKvRow('读取速率', io && io.readText ? esc(io.readText) : '<span class="faint">无 IO 数据</span>') +
        dwKvRow('写入速率', io && io.writeText ? esc(io.writeText) : '<span class="faint">无 IO 数据</span>') +
        dwKvRow('硬盘状态', d.state === 'standby' ? '休眠' : esc(d.state || '—')) +
        '</dl></div>';
    }

    /* --- SMART --- */
    html += '<div class="mon-dw-sec">SMART</div>' +
      '<div class="mon-dw-card">' + dwSmartTable(d) + '</div>';

    /* --- 信息 --- */
    html += '<div class="mon-dw-sec">信息</div>' +
      '<div class="mon-dw-card">' + dwInfoKv(d) + '</div>';

    /* --- 巡检 --- */
    var scan = (st && st.scan) || {};
    var ago = scanAgo(scan.last_ts || 0);
    html += '<div class="mon-dw-sec">巡检</div>' +
      '<div class="mon-dw-card"><dl class="mon-dw-kv">' +
      dwKvRow('巡检分级', gradeBadge(led.scanLv)) +
      dwKvRow('巡检时间', ago ? esc(ago) : '<span class="faint">未巡检</span>') +
      (d.detail ? dwKvRow('巡检说明', esc(d.detail)) : '') +
      '</dl></div>';

    body.innerHTML = html;
    drawerDev = dev;
    showDrawer();
  }

  function showDrawer() {
    var ov = monDrawerOverlay();
    if (!ov) return;
    ov.hidden = false;
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
    drawerOpen = false;
    drawerDev = null;
    // 等滑出动画结束再隐藏
    setTimeout(function () {
      if (!drawerOpen) ov.hidden = true;
    }, 320);
  }

  /* 事件绑定：盘位点击 / 关闭 / 遮罩 / ESC */
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
          '<span class="hint">盘位 LED = 硬盘健康与 SMART 告警状态</span>' +
          /* 点阵色阶图例：绿 → 黄绿 → 橙 → 红，与容量占用率一一对应 */
          '<span class="ramp-key"><i class="ramp-bar"></i>点阵与容量行颜色 = 空间占用（绿 → 红）</span>' +
          '<span class="scan-ago">' +
            (lastScanTs ? (ago ? '巡检于 ' + esc(ago) : '巡检中') : '尚未巡检') +
          '</span>' +
          '<span class="hint">盘位按存储空间排列</span>' +
        '</div>' +
      '</div>';

      /* SMART 明细（每盘 5 项告警计数）—— 盘位卡片放不下的精细数据留在这张表里 */
      var smartRows = st.disks.map(function (d) {
        var led = bayLedState(d);
        var smText = smartAlertsText(led.smart);
        /* 分级列显示整合后的状态（与盘位 LED 完全一致）：
           巡检说正常但 SMART 有告警时，这里也要显示「留意」，
           否则表和灯会自相矛盾。 */
        var detailTxt = (d.detail ? d.detail : '') +
          (smText ? (d.detail ? '；' : '') + 'SMART 告警：' + smText : '');
        return '<tr>' +
          '<td class="mono">' + esc(d.name) + '</td>' +
          '<td>' + gradeBadge(led.lv) + '</td>' +
          '<td class="num">' + (detailTxt ? esc(detailTxt) : '—') + '</td>' +
          '<td>' + smartFlagsHtml(d) + '</td>' +
        '</tr>';
      }).join('');
      html += item('SMART 告警计数', '5 项',
        '<div class="mon-table-wrap"><table class="mon-table"><thead><tr>' +
        '<th>设备</th><th>状态</th><th>说明</th><th>计数</th>' +
        '</tr></thead><tbody>' + smartRows + '</tbody></table></div>',
        'mon-item-wide');
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

    /* --- 巡检历史 --- */
    var scan = st.scan || {};
    var scanLog = st.scanLog || [];
    var scanHtml = '';
    if (scan.error) {
      scanHtml = '<div class="mon-note crit">巡检异常：' + esc(scan.error) + '</div>';
    } else if (scanLog.length) {
      var logRows = scanLog.slice(0, 12).map(function (l) {
        var cls = l.changed ? 'warn' : 'ok';
        var when = l.ts ? new Date(l.ts * 1000).toLocaleString('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
        }) : '—';
        return '<div class="mon-row"><span class="mon-row-name mono">' + esc(when) + '</span>' +
          '<span class="mon-badge ' + cls + '">' + (l.changed ? '有变化' : '未见异常') + '</span>' +
          '<span class="mon-row-val faint">' + esc(l.summary || '') + '</span></div>';
      }).join('');
      scanHtml = '<div class="mon-list">' + logRows + '</div>';
    } else {
      scanHtml = '<div class="mon-note faint">尚未巡检（首轮巡检在服务启动约 30 秒后进行）</div>';
    }
    var lastScan = scan.last_ts
      ? new Date(scan.last_ts * 1000).toLocaleString('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
        })
      : '尚未巡检';
    html += item('硬盘巡检', esc(lastScan), scanHtml, 'mon-item-wide');

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
      render(d);
      return d;
    });
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
