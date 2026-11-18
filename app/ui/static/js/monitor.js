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

  /* 百分比 → 等级（用于条形/温度着色）。warn 从 70% 起，high 从 90% 起。 */
  function level(pct, warnAt, highAt) {
    if (pct === null || pct === undefined || isNaN(pct)) return 'ok';
    if (pct >= highAt) return 'high';
    if (pct >= warnAt) return 'warn';
    return 'ok';
  }

  /* 温度等级：<60 正常，60~75 偏高，>=75 过高（家用/服务器通用阈值） */
  function tempLevel(t) {
    if (t === null || t === undefined || isNaN(t)) return 'ok';
    if (t >= 75) return 'high';
    if (t >= 60) return 'warn';
    return 'ok';
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

  function gradeBadge(g) {
    var m = GRADE_BADGE[g] || GRADE_BADGE.unknown;
    return '<span class="mon-badge ' + m.cls + '">' + m.text + '</span>';
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

  function diskIoHtml(d) {
    var io = (d._io) || null;
    if (!io) return '<span class="faint">—</span>';
    return '<span class="mono">↓ ' + esc(io.readText || '—') + '</span> ' +
      '<span class="mono">↑ ' + esc(io.writeText || '—') + '</span>';
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

    /* --- 存储总览 --- */
    var gradeCounts = sum.grades || {};
    // summary 由后端聚合；缺失时直接数数组兜底，避免显示成「0 个存储池」
    var poolN = (sum.poolCount !== undefined && sum.poolCount !== null)
      ? sum.poolCount : (st.volumes || []).length;
    var diskN = (sum.diskCount !== undefined && sum.diskCount !== null)
      ? sum.diskCount : (st.disks || []).length;
    var overviewBits = [];
    if (poolN) overviewBits.push(poolN + ' 个存储池');
    if (diskN) overviewBits.push(diskN + ' 块硬盘');
    if (sum.sleepingCount) overviewBits.push(sum.sleepingCount + ' 块休眠');
    if (gradeCounts.warn) overviewBits.push(gradeCounts.warn + ' 块需留意');
    if (gradeCounts.crit) overviewBits.push(gradeCounts.crit + ' 块故障');
    if (overviewBits.length) {
      var ovCls = gradeCounts.crit ? 'crit' : (gradeCounts.warn ? 'warn' : 'ok');
      // 容量合计：优先用后端 summary，缺失时从存储池累加
      var totText = sum.totalText, usedText = sum.usedText, freeText = sum.freeText;
      if (!totText) {
        var tSum = 0, uSum = 0, fSum = 0;
        (st.volumes || []).forEach(function (v) {
          tSum += v.total || 0; uSum += v.used || 0; fSum += v.free || 0;
        });
        if (tSum) {
          totText = fmtBytes(tSum); usedText = fmtBytes(uSum); freeText = fmtBytes(fSum);
        }
      }
      html += item('存储总览',
        '<span class="mon-lv-' + ovCls + '">' + esc(overviewBits.join(' · ')) + '</span>',
        (totText && totText !== '—')
          ? '<div class="mon-item-sub">总容量 <span>' + esc(totText) + '</span>，已用 ' +
            '<span>' + esc(usedText) + '</span>，剩余 <span>' + esc(freeText) + '</span></div>'
          : '', 'mon-item-wide');
    }

    /* --- 存储池容量 --- */
    (st.volumes || []).forEach(function (v) {
      var sub = '';
      if (v.children && v.children.length) {
        var rows = v.children.slice(0, 6).map(function (c) {
          return '<div class="mon-child">' +
            '<span class="mon-child-name" title="' + esc(c.name) + '">' + esc(c.name) + '</span>' +
            bar(c.percent) +
            '<span class="mon-child-val">' + fmtBytes(c.used) + ' / ' + fmtBytes(c.total) + '</span>' +
          '</div>';
        }).join('');
        sub = '<div class="mon-children">' + rows + '</div>';
      }
      html += item(
        '存储池 ' + v.name,
        v.percent + '%',
        bar(v.percent) +
        '<div class="mon-item-sub">已用 <span>' + esc(v.usedText) + '</span> / 共 ' +
        esc(v.totalText) + '，剩余 <span>' + esc(v.freeText) + '</span></div>' + sub
      );
    });

    /* --- 物理磁盘（SMART + 分级 + IO） --- */
    if (st.disks && st.disks.length) {
      var rows = st.disks.map(function (d) {
        var t = d.temp;
        var tHtml = (t === null || t === undefined)
          ? '—'
          : '<span class="mon-temp"><span class="mon-temp-dot lv-' + tempLevel(t) +
            '"></span>' + t + '°C</span>';
        var standby = d.standby
          ? '<span class="mon-badge unknown">休眠</span>' : '';
        var proto = d.proto ? '<span class="mon-badge unknown">' + esc(d.proto) + '</span>' : '';
        return '<tr>' +
          '<td class="mono">' + esc(d.name) + (standby ? ' ' + standby : '') + '</td>' +
          '<td>' + esc(d.model || '') + (proto ? ' ' + proto : '') + '</td>' +
          '<td>' + esc(d.type || '—') + '</td>' +
          '<td class="num">' + esc(d.sizeText || '—') + '</td>' +
          '<td class="num">' + tHtml + '</td>' +
          '<td class="num">' + esc(d.health || '—') + '</td>' +
          '<td class="num">' + gradeBadge(d.grade) + '</td>' +
          '<td class="num">' + diskIoHtml(d) + '</td>' +
        '</tr>';
      }).join('');
      html += item('物理磁盘', st.disks.length + ' 块',
        '<div class="mon-table-wrap"><table class="mon-table"><thead><tr>' +
        '<th>设备</th><th>型号</th><th>类型</th><th class="num">容量</th>' +
        '<th class="num">温度</th><th class="num">SMART</th><th class="num">分级</th>' +
        '<th class="num">实时读写</th>' +
        '</tr></thead><tbody>' + rows + '</tbody></table></div>',
        'mon-item-wide');

      /* SMART 明细（每盘 5 项告警计数） */
      var smartRows = st.disks.map(function (d) {
        return '<tr>' +
          '<td class="mono">' + esc(d.name) + '</td>' +
          '<td>' + gradeBadge(d.grade) + '</td>' +
          '<td class="num">' + (d.detail ? esc(d.detail) : '—') + '</td>' +
          '<td>' + smartFlagsHtml(d) + '</td>' +
        '</tr>';
      }).join('');
      html += item('SMART 告警计数', '5 项',
        '<div class="mon-table-wrap"><table class="mon-table"><thead><tr>' +
        '<th>设备</th><th>分级</th><th>说明</th><th>计数</th>' +
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
            '<span class="mon-temp"><span class="mon-temp-dot lv-' + tempLevel(x.temp) + '"></span>' +
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
