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

  function renderStorage(st) {
    var box = $('#monStorage');
    if (!box) return;
    if (!st || !st.available || !st.volumes || !st.volumes.length) {
      box.innerHTML = emptyBox('未检测到存储池（/vol* 挂载点不可见）');
      $('#monStoreCount').textContent = '不可用';
      return;
    }
    var html = '';
    st.volumes.forEach(function (v) {
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

    // 软 RAID 状态
    if (st.raid && st.raid.length) {
      var rows = st.raid.map(function (r) {
        var cls = (r.state && r.state.indexOf('active') === 0) ? 'ok' : 'warn';
        return '<div class="mon-row">' +
          '<span class="mon-row-name">' + esc(r.name) + '</span>' +
          '<span class="mon-badge ' + cls + '">' + esc(r.state || '未知') + '</span>' +
          '<span class="mon-row-val">' + r.active + '/' + r.total + '</span>' +
        '</div>';
      }).join('');
      html += item('软 RAID', st.raid.length + ' 组',
        '<div class="mon-list">' + rows + '</div>');
    }

    // 物理磁盘清单
    if (st.disks && st.disks.length) {
      var trows = st.disks.map(function (d) {
        var t = d.temp;
        var tHtml = (t === null || t === undefined)
          ? '—'
          : '<span class="mon-temp"><span class="mon-temp-dot lv-' + tempLevel(t) +
            '"></span>' + t + '°C</span>';
        return '<tr><td>' + esc(d.name) + '</td><td>' + esc(d.model || '') + '</td>' +
          '<td>' + esc(d.type || '—') + '</td>' +
          '<td class="num">' + esc(d.sizeText || '—') + '</td>' +
          '<td class="num">' + tHtml + '</td></tr>';
      }).join('');
      html += item('物理磁盘', st.disks.length + ' 块',
        '<div class="mon-table-wrap"><table class="mon-table"><thead><tr>' +
        '<th>设备</th><th>型号</th><th>类型</th><th>容量</th><th>温度</th>' +
        '</tr></thead><tbody>' + trows + '</tbody></table></div>',
        'mon-item-wide');
    }

    box.innerHTML = html;
    $('#monStoreCount').textContent = st.volumes.length + ' 个存储池';
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
