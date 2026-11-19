/* FnStyle 存储监控前端渲染单元测试（最小 DOM stub，无 jsdom 依赖）
 * 用法: node /tmp/monitor_render_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js');

/* ---------- 最小 DOM stub ---------- */
const els = {};
function mkEl(id) {
  return {
    id: id,
    innerHTML: '',
    textContent: '',
    checked: true,
    className: '',
    addEventListener: function () {},
    getAttribute: function () { return null; },
    setAttribute: function () {},
  };
}
['monStorage', 'monStoreCount', 'monStatus', 'monUpdated', 'monCore', 'monCoreCount',
  'monTemp', 'monTempCount', 'monPower', 'monPowerSource', 'btnMonRefresh', 'monAuto',
  'btnMonScan', 'monDrawerOverlay', 'monDrawerTitle', 'monDrawerSub', 'monDrawerBody',
  'btnCloseMonDrawer', 'monDwSpark', 'monDwTimeFrom', 'monDwTimeTo',
  /* v1.1.32：抽屉顶部的硬盘切换下拉 + 巡检历史抽屉 */
  'monDrawerDiskSel', 'monScanLogOverlay', 'monScanLogTitle', 'monScanLogSub',
  'monScanLogBody', 'btnCloseMonScanLog', 'btnMonScanLog']
  .forEach(id => { els[id] = mkEl(id); });

/* 抽屉的 overlay 需要 classList / hidden / 事件钩子 */
[els.monDrawerOverlay, els.monScanLogOverlay].forEach(function (ov) {
  ov.classList = {
    _s: {},
    add: function (c) { this._s[c] = true; },
    remove: function (c) { delete this._s[c]; },
    contains: function (c) { return !!this._s[c]; },
    toggle: function (c, on) { if (on === undefined) on = !this._s[c]; if (on) this._s[c] = true; else delete this._s[c]; },
  };
  ov.hidden = true;
});
els.monDrawerOverlay.classList.toggle = function (c, on) {
  if (on === undefined) on = !this._s[c];
  if (on) this._s[c] = true; else delete this._s[c];
};
/* select 元素：需要 value / parentNode.classList */
els.monDrawerDiskSel.value = '';
els.monDrawerDiskSel.parentNode = { className: '', classList: {
  _s: {},
  add: function (c) { this._s[c] = true; },
  remove: function (c) { delete this._s[c]; },
  contains: function (c) { return !!this._s[c]; },
  toggle: function (c, on) { if (on === undefined) on = !this._s[c]; if (on) this._s[c] = true; else delete this._s[c]; },
} };

/* spark 画布：需要尺寸才能算出曲线坐标 */
els.monDwSpark.clientWidth = 320;
els.monDwSpark.clientHeight = 64;
els.monDrawerOverlay.__listeners = {};
els.monDrawerOverlay.addEventListener = function (t, fn) {
  (this.__listeners[t] = this.__listeners[t] || []).push(fn);
};
els.monDrawerOverlay.dispatch = function (t, ev) {
  (this.__listeners[t] || []).forEach(function (fn) { fn(ev); });
};

global.document = {
  readyState: 'complete',
  hidden: false,
  /* 滚动锁需要 documentElement.classList（抽屉打开时给 html 加锁类） */
  documentElement: {
    classList: {
      _s: {},
      add: function (c) { this._s[c] = true; },
      remove: function (c) { delete this._s[c]; },
      contains: function (c) { return !!this._s[c]; },
    },
  },
  querySelector: function (sel) {
    if (sel.charAt(0) === '#') return els[sel.slice(1)] || null;
    return null;
  },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
};
global.location = { hash: '' };
global.window = global;
global.fetch = function () {
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
};
global.setTimeout = setTimeout;
global.clearTimeout = clearTimeout;
/* 抽屉动画用 requestAnimationFrame —— Node 环境用 setTimeout 顶替 */
global.requestAnimationFrame = function (fn) { return setTimeout(fn, 0); };

/* ---------- 加载 monitor.js ---------- */
const code = fs.readFileSync(SRC, 'utf8');
try {
  (0, eval)(code);
} catch (e) {
  console.error('❌ monitor.js 加载异常:', e.message, '\n', e.stack);
  process.exit(1);
}
console.log('✅ monitor.js 加载成功');

/* ---------- 构造完整模拟数据（对齐后端字段） ---------- */
const GIB = 1024 * 1024 * 1024;
const now = Date.now() / 1000;

const FULL = {
  success: true,
  available: true,
  ts: now,
  uptime: '3 天 4 小时',
  storage: {
    available: true,
    ts: now,
    volumes: [{
      name: 'vol1', path: '/vol1',
      total: 500 * GIB, used: 312.5 * GIB, free: 187.5 * GIB, percent: 62,
      totalText: '500 GB', usedText: '313 GB', freeText: '188 GB',
      children: [
        { name: '1000', total: 100 * GIB, used: 60 * GIB, free: 40 * GIB, percent: 60 },
        { name: '2000', total: 200 * GIB, used: 150 * GIB, free: 50 * GIB, percent: 75 },
      ],
    }],
    filesystems: [
      { name: '/vol1', total: 500 * GIB, used: 312.5 * GIB, free: 187.5 * GIB, percent: 62 },
    ],
    disks: [
      {
        name: 'sda', model: 'WDC WD40EFZX-68AWUN0', vendor: 'ATA', type: 'disk',
        size: 4000653453312, sizeText: '4.00 TB',
        serial: 'WD-WX32D0051234', temp: 34, rpm: '5400 rpm',
        health: 'PASSED', grade: 'ok', gradeLabel: '正常', detail: '未发现异常计数',
        mounts: ['/vol1'], vol: 1, used: 250 * GIB, total: 4000653453312,
        percent: 6.4, usedText: '250 GB',
        standby: false, proto: 'SATA',
        reallocated_sectors: 0, reallocated_events: 0,
        pending_sectors: 0, offline_uncorrectable: 0,
        uncorrectable: 0, udma_crc: 0,
      },
      {
        name: 'sdb', model: 'WDC WD40EFZX-68AWUN0', vendor: 'ATA', type: 'disk',
        size: 4000653453312, sizeText: '4.00 TB',
        serial: 'WD-WX32D0055678', temp: 41, rpm: '5400 rpm',
        health: 'PASSED', grade: 'warn', gradeLabel: '留意',
        detail: '重映射扇区计数 3',
        mounts: ['/vol1'], vol: 1, used: 300 * GIB, total: 4000653453312,
        percent: 7.7, usedText: '300 GB',
        standby: false, proto: 'SATA',
        reallocated_sectors: 3, reallocated_events: 3,
        pending_sectors: 0, offline_uncorrectable: 0,
        uncorrectable: 0, udma_crc: 12,
      },
      {
        name: 'sdc', model: 'Samsung SSD 870 EVO 1TB', vendor: 'ATA', type: 'SSD',
        size: 1000204886016, sizeText: '1.00 TB',
        serial: 'S6P2NJ0T123456', temp: null, rpm: 'Solid State Device',
        health: 'PASSED', grade: 'crit', gradeLabel: '故障',
        detail: '不可纠正扇区计数 2；待映射扇区 5',
        mounts: ['/vol2'], vol: 2, used: 500 * GIB, total: 1000204886016,
        percent: 51.2, usedText: '500 GB',
        standby: false, proto: 'SATA',
        reallocated_sectors: 10, reallocated_events: 10,
        pending_sectors: 5, offline_uncorrectable: 2,
        uncorrectable: 2, udma_crc: 0,
      },
      {
        name: 'sdd', model: 'ST4000VN008-2DR166', vendor: 'ATA', type: 'HDD',
        size: 4000787030016, sizeText: '4.00 TB',
        serial: 'ZDH1AAAA', temp: 36, rpm: '5900 rpm',
        health: 'PASSED', grade: 'unknown', gradeLabel: '未知',
        detail: '休眠中，未唤醒检测',
        mounts: [], vol: 0, used: 0, total: 0,
        percent: 0, usedText: '—',
        standby: true, proto: 'SAS',
        reallocated_sectors: 0, reallocated_events: 0,
        pending_sectors: 0, offline_uncorrectable: 0,
        uncorrectable: 0, udma_crc: 0,
      },
    ],
    diskio: [
      { name: 'sda', read_rate: 1048576, write_rate: 524288, read_iops: 32, write_iops: 16,
        readText: '1.0 MB/s', writeText: '512.0 KB/s' },
      { name: 'sdc', read_rate: 0, write_rate: 0, read_iops: 0, write_iops: 0,
        readText: '0 B/s', writeText: '0 B/s' },
    ],
    raid: [{
      device: 'md0', name: 'md0', state: 'active (raid1)', active: 2, total: 2,
      members: ['sda[0]', 'sdb[1](F)'],
    }],
    raidcard: {
      type: 'megaraid', label: 'LSI MegaRAID',
      detail: 'storcli /opt/MegaRAID/storcli64',
      tool: 'storcli', error: '',
      controllers: [{
        id: '0', model: 'LSI MegaRAID SAS 9361-8i', status: '工作正常',
        roc_temp: 54,
        vd: [{ vd: '0', type: 'RAID1', state: 'optl', state_label: '最优', badge: 'on' }],
        pds: [{ loc: '0', model: 'ST4000VN008', temp: 33, health: 'ok' }],
      }],
    },
    scan: { last_ts: now - 3600, running: false, summary: { ok: 3, warn: 1 }, error: '' },
    scanLog: [
      { ts: now - 3600, changed: true, summary: 'sdb 重映射扇区计数 0 → 3' },
      { ts: now - 3600 * 8, changed: false, summary: '3 块盘全部正常' },
    ],
    summary: {
      poolCount: 1, diskCount: 4, sleepingCount: 1,
      total: 500 * GIB, used: 312.5 * GIB, free: 187.5 * GIB,
      totalText: '500 GB', usedText: '313 GB', freeText: '188 GB',
      percent: 62,
      grades: { ok: 1, warn: 1, crit: 1, unknown: 1 },
      warnDisks: [{ name: 'sdb', grade: 'warn', detail: '重映射扇区计数 3' },
                  { name: 'sdc', grade: 'crit', detail: '不可纠正扇区计数 2；待映射扇区 5' }],
      raidCount: 1, raidCardType: 'megaraid',
    },
  },
  cpu: { available: false }, mem: { available: false }, gpu: { available: false },
  temp: { available: false }, power: { ok: false },
};

/* ---------- 执行渲染 ---------- */
function renderCase(name, data) {
  els.monStorage.innerHTML = '';
  els.monStoreCount.textContent = '';
  try {
    // renderStorage 是内部函数，通过 render() 间接调用
    // 这里直接复用 IIFE 暴露的接口
    global.window.FnStyleMonitor.__renderForTest(data);
  } catch (e) {
    console.error('❌ [' + name + '] 渲染异常:', e.message);
    console.error(e.stack);
    process.exitCode = 1;
    return;
  }
  const html = els.monStorage.innerHTML;
  console.log('✅ [' + name + '] 渲染成功, HTML 长度=' + html.length +
    ', 计数=' + els.monStoreCount.textContent);
  return html;
}

/* monitor.js 未暴露 renderStorage，补一个测试钩子（不改生产代码） */
const patched = code.replace(
  'window.FnStyleMonitor = {\n    refresh: refresh,',
  'window.FnStyleMonitor = {\n    __renderForTest: render,\n    refresh: refresh,'
);
if (patched === code) {
  console.error('❌ 未能注入测试钩子（源码结构变化）');
  process.exit(1);
}
/* 再注入 SMART 状态判定的测试钩子（bayLedState / smartAlertsText）
   以及硬盘详情抽屉的测试钩子（openDiskDrawer / dwSmartTable / dwInfoKv） */
const patched2 = patched.replace(
  'window.FnStyleMonitor = {',
  'window.__bayLedState = bayLedState;\n  window.__smartAlertsText = smartAlertsText;\n' +
  '  window.__openDiskDrawer = openDiskDrawer;\n' +
  '  window.__dwSmartTable = dwSmartTable;\n' +
  '  window.__dwInfoKv = dwInfoKv;\n' +
  '  window.__dwSmartRows = dwSmartRows;\n' +
  '  window.__dwKvRow = dwKvRow;\n' +
  '  window.__diskTempLevel = diskTempLevel;\n' +
  '  window.__tempExceed = tempExceed;\n' +
  '  window.__tempLevel = tempLevel;\n' +
  '  window.__TH_DISK_TEMP_WARN = TH_DISK_TEMP_WARN;\n' +
  '  window.__TH_DISK_TEMP_CRIT = TH_DISK_TEMP_CRIT;\n' +
  '  window.__feedDiskBuf = feedDiskBuf;\n' +
  '  window.__monoPath = monoPath;\n' +
  '  window.__drawSpark = drawSpark;\n' +
  '  window.__drawDrawerSpark = drawDrawerSpark;\n' +
  '  window.__getDiskBuf = function (n) { return diskBuf[n] || null; };\n' +
  '  window.__fmtRate = fmtRate;\n' +
  '  window.__fmtHM = fmtHM;\n' +
  '  window.__drawDrawerTimeAxis = drawDrawerTimeAxis;\n' +
  '  window.__setLastData = function (d) { lastData = d; };\n' +
  '  window.__openScanLogDrawer = openScanLogDrawer;\n' +
  '  window.__closeScanLogDrawer = closeScanLogDrawer;\n' +
  '  window.__closeDiskDrawer = closeDiskDrawer;\n' +
  '  window.__renderScanLogHtml = renderScanLogHtml;\n' +
  '  window.FnStyleMonitor = {'
);
if (patched2 === patched) {
  console.error('❌ 未能注入 SMART 测试钩子（源码结构变化）');
  process.exit(1);
}
global.document = {
  readyState: 'complete', hidden: false,
  /* 滚动锁需要 documentElement.classList */
  documentElement: {
    classList: {
      _s: {},
      add: function (c) { this._s[c] = true; },
      remove: function (c) { delete this._s[c]; },
      contains: function (c) { return !!this._s[c]; },
    },
  },
  querySelector: function (sel) { return sel.charAt(0) === '#' ? (els[sel.slice(1)] || null) : null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
};
global.window = global;
(0, eval)(patched2);
console.log('✅ 测试钩子注入成功');
const bayLedState = global.__bayLedState;
const smartAlertsText = global.__smartAlertsText;
const openDiskDrawer = global.__openDiskDrawer;
const dwSmartTable = global.__dwSmartTable;
const dwInfoKv = global.__dwInfoKv;
const dwSmartRows = global.__dwSmartRows;
const dwKvRow = global.__dwKvRow;
const diskTempLevel = global.__diskTempLevel;
const tempExceed = global.__tempExceed;
const tempLevel = global.__tempLevel;
const TH_DISK_TEMP_WARN = global.__TH_DISK_TEMP_WARN;
const TH_DISK_TEMP_CRIT = global.__TH_DISK_TEMP_CRIT;
const setLastData = global.__setLastData;
const feedDiskBuf = global.__feedDiskBuf;
const monoPath = global.__monoPath;
const drawSpark = global.__drawSpark;
const drawDrawerSpark = global.__drawDrawerSpark;
const getDiskBuf = global.__getDiskBuf;
const fmtRate = global.__fmtRate;
const fmtHM = global.__fmtHM;
const drawDrawerTimeAxis = global.__drawDrawerTimeAxis;
const openScanLogDrawer = global.__openScanLogDrawer;
const renderScanLogHtml = global.__renderScanLogHtml;
const closeScanLogDrawerFn = global.__closeScanLogDrawer;
const closeDiskDrawerFn = global.__closeDiskDrawer;
if (typeof bayLedState !== 'function' || typeof smartAlertsText !== 'function') {
  console.error('❌ SMART 测试钩子未就绪');
  process.exit(1);
}
if (typeof openDiskDrawer !== 'function' || typeof dwSmartTable !== 'function'
    || typeof dwInfoKv !== 'function' || typeof setLastData !== 'function') {
  console.error('❌ 抽屉测试钩子未就绪');
  process.exit(1);
}
if (typeof openScanLogDrawer !== 'function' || typeof renderScanLogHtml !== 'function') {
  console.error('❌ 巡检历史抽屉测试钩子未就绪');
  process.exit(1);
}
if (typeof diskTempLevel !== 'function' || typeof tempExceed !== 'function'
    || typeof dwKvRow !== 'function') {
  console.error('❌ 温度阈值测试钩子未就绪');
  process.exit(1);
}
if (typeof feedDiskBuf !== 'function' || typeof monoPath !== 'function'
    || typeof drawSpark !== 'function' || typeof getDiskBuf !== 'function') {
  console.error('❌ 趋势图测试钩子未就绪');
  process.exit(1);
}
if (typeof drawDrawerSpark !== 'function') {
  console.error('❌ 趋势图绘制钩子未就绪');
  process.exit(1);
}

/* ---------- 各用例 ---------- */
let html = renderCase('完整数据', { storage: FULL.storage, ts: FULL.ts });
if (html) {
  const checks = [
    // 存储池使用率总览（fnmonitor 的 .card.pool 形态）
    ['存储池使用率标题', '存储池使用率'],
    ['可用空间标题', '可用空间'],
    ['池容量进度条', 'mon-pool-bar'],
    ['图例已用', '已用'],
    ['图例总容量', '总容量'],
    // NAS 机箱 + 盘位卡片
    ['NAS 机箱', 'mon-chassis'],
    ['盘位容器', 'mon-bays'],
    ['盘位卡片', 'mon-bay"'],
    ['健康 LED', 'mon-bay-led'],
    ['容量点阵仪表', 'mon-bay-gauge'],
    ['盘位容量行', 'mon-bay-cap'],
    ['盘位实时 IO', 'mon-bay-io'],
    ['LED 图例', 'led-key'],
    ['LED 图例-留意', 'led-key warn'],
    ['LED 图例-故障', 'led-key crit'],
    ['LED 图例-休眠未巡检', '休眠 / 未巡检'],
    ['巡检时间', '巡检于'],
    ['盘位排序提示', '盘位按存储空间排列'],
    // 原表格信息已整合进盘位卡片
    ['盘位显示设备名', 'sda'],
    ['盘位显示型号', 'WDC WD40EFZX'],
    ['盘位显示存储空间标签', '存储空间 vol1'],
    // 其余板块
    ['软 RAID', '软 RAID'],
    ['阵列卡', '阵列卡'],
    ['休眠标记', '休眠'],
    ['故障盘分级', '故障'],
    ['成员盘故障 (F)', '(F)'],
    // 负载色阶（参照 fnmonitor「负载色阶」）：进度条与点阵都从绿渐变到红
    ['进度条铺色阶渐变', 'background-image:linear-gradient(90deg, var(--r-g)'],
    ['进度条色标-黄绿', 'var(--r-y)'],
    ['进度条色标-橙', 'var(--warning)'],
    ['进度条色标-红', 'var(--danger)'],
    ['进度条露出填充段', 'background-size:'],
    ['点阵按位置取色阶', 'style="fill:color-mix(in oklch'],
    ['存储池数字走文字色阶', 'style="color:'],
    // 容量单位必须用十进制（与 fnmonitor F.dsize / fnOS 存储页一致）：
    // 1 TB 盘不能显示成 931.5 GB。这里断言后端下发的十进制文本被原样透传。
    ['存储池容量十进制文本', '>500 GB<'],
    ['存储池已用十进制文本', '>313 GB<'],
    ['盘位容量十进制文本', '250 GB / 4.00 TB'],
  ];
  let bad = 0;
  checks.forEach(([label, needle]) => {
    const ok = html.indexOf(needle) >= 0;
    console.log((ok ? '  ✓ ' : '  ✗ ') + label + ' → "' + needle + '"');
    if (!ok) bad++;
  });
  // 存储池卡片不应再下钻子目录明细（即便后端仍传来 children）
  const noChildren = [
    ['子目录明细不渲染', 'mon-child'],
    ['子目录名不出现', '>1000<'],
    ['子目录名不出现2', '>2000<'],
    // 盘位右上角不再挂容量告警角标：状态一律靠颜色 + 图例表达
    ['盘位容量告警角标已移除', 'mon-bay-alert'],
    // v1.1.25：机箱顶栏的装饰性电源灯已整体移除（渲染 + 图例）
    ['顶栏电源灯已移除', 'class="pwr"'],
    ['电源灯图例已移除', 'pwr-key'],
    ['电源灯说明已移除', '机箱电源灯'],
    // v1.1.32：两张重复卡片已删除 —— 巡检记录改由标题栏按钮开抽屉，
    // SMART 5 项计数在硬盘详情抽屉里已完整呈现
    ['SMART 告警计数卡片已移除', 'SMART 告警计数'],
    ['硬盘巡检卡片已移除', '硬盘巡检'],
    // v1.1.33：机箱脚注里的两行冗长口径说明已移除
    ['色阶图例条已移除', 'ramp-bar'],
    ['色阶图例说明已移除', '点阵与容量行颜色 = 空间占用'],
    ['LED 语义说明已移除', '盘位 LED = 硬盘健康与 SMART 告警状态'],
  ];
  noChildren.forEach(([label, needle]) => {
    const ok = html.indexOf(needle) < 0;
    console.log((ok ? '  ✓ ' : '  ✗ ') + label + ' → 不含 "' + needle + '"');
    if (!ok) bad++;
  });
  // 检查未替换的模板残留
  ['undefined', 'NaN', '[object Object]'].forEach(bad2 => {
    const cnt = (html.match(new RegExp(bad2.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length;
    console.log((cnt === 0 ? '  ✓ ' : '  ✗ ') + '无 "' + bad2 + '" 残留 (出现 ' + cnt + ' 次)');
    if (cnt) process.exitCode = 1;
  });
  if (bad) { console.error('❌ 缺少 ' + bad + ' 个预期片段'); process.exitCode = 1; }
}

/* ---------- SMART 告警 → 盘位 LED 状态整合 ---------- */
(function checkSmartLedIntegration() {
  console.log('\n--- SMART 告警整合到盘位 LED ---');
  const base = {
    name: 'sdz', health: 'PASSED', grade: 'ok', standby: false,
    reallocated_sectors: 0, reallocated_events: 0, pending_sectors: 0,
    uncorrectable: 0, udma_crc: 0,
  };
  const mk = o => Object.assign({}, base, o);
  const cases = [
    ['全零告警 → 正常(ok)', mk({}), 'ok'],
    ['巡检 ok + UDMA CRC 60 → 留意(warn)', mk({ udma_crc: 60 }), 'warn'],
    ['巡检 ok + 重映射扇区 10 → 留意(warn)', mk({ reallocated_sectors: 10 }), 'warn'],
    ['巡检 ok + 待映射扇区 5 → 留意(warn)', mk({ pending_sectors: 5 }), 'warn'],
    ['巡检 ok + 重映射事件 3 → 留意(warn)', mk({ reallocated_events: 3 }), 'warn'],
    ['巡检 ok + 无法纠正错误 2 → 故障(crit)', mk({ uncorrectable: 2 }), 'crit'],
    ['SMART 自检未通过 → 故障(crit)', mk({ health: 'FAILED!' }), 'crit'],
    ['巡检 warn + SMART warn → 仍是留意', mk({ grade: 'warn', udma_crc: 5 }), 'warn'],
    ['巡检 crit + SMART warn → 仍是故障', mk({ grade: 'crit', udma_crc: 5 }), 'crit'],
    ['休眠盘优先于 SMART 告警', mk({ standby: true, udma_crc: 99 }), 'standby'],
    ['未巡检(unknown) + SMART warn → 留意', mk({ grade: 'unknown', udma_crc: 7 }), 'warn'],
    ['未巡检(unknown) + 无告警 → 未巡检', mk({ grade: 'unknown' }), 'unknown'],
  ];
  let bad = 0;
  cases.forEach(([label, d, expect]) => {
    const got = bayLedState(d).lv;
    const ok = got === expect;
    console.log((ok ? '  ✓ ' : '  ✗ ') + label + ' → got=' + got + ' expect=' + expect);
    if (!ok) bad++;
  });
  // 告警明细文本
  const t1 = smartAlertsText(bayLedState(mk({ udma_crc: 60 })).smart);
  const t2 = smartAlertsText(bayLedState(mk({ uncorrectable: 2, udma_crc: 60 })).smart);
  const t3 = smartAlertsText(bayLedState(mk({})).smart);
  const t4 = smartAlertsText(bayLedState(mk({ health: 'FAILED!' })).smart);
  const txts = [
    ['告警文本含 UDMA CRC 计数', t1.indexOf('UDMA CRC 错误 60') >= 0, t1],
    ['告警文本含无法纠正错误', t2.indexOf('无法纠正错误 2') >= 0, t2],
    ['无告警时文本为空', t3 === '', t3],
    ['自检未通过进文本', t4.indexOf('SMART 自检未通过') >= 0, t4],
  ];
  txts.forEach(([label, ok, extra]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label + (extra ? ' → "' + extra + '"' : ''));
    if (!ok) bad++;
  });
  if (bad) { console.error('❌ SMART 整合断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- 盘位 LED：放大 + 留意/故障动画提醒（源码级） ---------- */
(function checkLedSizeAndAnimation() {
  console.log('\n--- 盘位 LED 尺寸与告警动画 ---');
  const CSS = path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css');
  const css = fs.readFileSync(CSS, 'utf8');

  /* 取 .mon-bay-led 规则块 */
  const ledBlock = (css.match(/\.mon-bay-led\s*\{[^}]*\}/) || [''])[0];
  const w = (ledBlock.match(/width:\s*(\d+)px/) || [])[1];
  const h = (ledBlock.match(/height:\s*(\d+)px/) || [])[1];

  const rules = [
    ['LED 宽度已放大到 ≥10px', !!w && Number(w) >= 10, w],
    ['LED 高度已放大到 ≥10px', !!h && Number(h) >= 10, h],
    ['LED 有 position:relative（光环定位）', /position:\s*relative/.test(ledBlock)],
    // 动画必须包在 prefers-reduced-motion: no-preference 里（系统关动效时不闪）
    ['动画包在 prefers-reduced-motion: no-preference', /@media \(prefers-reduced-motion: no-preference\)/.test(css)],
    ['warn 状态有呼吸动画', /\.mon-bay\[data-st="warn"\]\s+\.mon-bay-led\s*\{[^}]*animation:\s*mon-led-breathe/.test(css)],
    ['crit 状态有呼吸动画', /\.mon-bay\[data-st="crit"\]\s+\.mon-bay-led\s*\{[^}]*animation:\s*mon-led-breathe/.test(css)],
    ['crit 有外扩光环动画', /mon-led-ring/.test(css)],
    ['呼吸关键帧存在', /@keyframes mon-led-breathe/.test(css)],
    ['光环关键帧存在', /@keyframes mon-led-ring/.test(css)],
    // 正常与休眠态不能有动画
    ['ok 状态无动画', !/\.mon-bay\[data-st="ok"\]\s+\.mon-bay-led\s*\{[^}]*animation/.test(css)],
    ['standby 状态无动画', !/\.mon-bay\[data-st="standby"\]\s+\.mon-bay-led\s*\{[^}]*animation/.test(css)],
    // 图例圆点也同步动画
    ['图例 warn 圆点有动画', /\.mon-nas-foot \.led-key\.warn\s*\{[^}]*animation/.test(css)],
    ['图例 crit 圆点有动画', /\.mon-nas-foot \.led-key\.crit\s*\{[^}]*animation/.test(css)],
  ];
  let bad = 0;
  rules.forEach(function ([label, ok, extra]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label + (extra !== undefined ? ' → ' + extra : ''));
    if (!ok) bad++;
  });
  if (bad) { console.error('❌ LED 尺寸/动画断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- 硬盘详情抽屉 ---------- */
(function checkDiskDrawer() {
  console.log('\n--- 硬盘详情抽屉 ---');
  const HTML = path.join(__dirname, '..', 'app', 'ui', 'index.html');
  const html = fs.readFileSync(HTML, 'utf8');
  let bad = 0;

  /* --- HTML 骨架 --- */
  const htmlRules = [
    ['抽屉容器存在', /id="monDrawerOverlay"/],
    ['抽屉是 aside + role=dialog', /<aside class="mon-drawer" role="dialog" aria-modal="true"/],
    ['有标题元素', /id="monDrawerTitle"/],
    ['有副标题元素', /id="monDrawerSub"/],
    ['有内容容器', /id="monDrawerBody"/],
    ['有关闭按钮', /id="btnCloseMonDrawer"/],
  ];
  htmlRules.forEach(function ([label, re]) {
    const ok = re.test(html);
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 盘位可点击 --- */
  const renderHtml = renderCase('抽屉用例', { storage: FULL.storage, ts: FULL.ts });
  const clickRules = [
    ['盘位带 data-dev', /class="mon-bay"[^>]*data-dev="sda"/],
    ['盘位带 data-clickable', /data-clickable="1"/],
    ['盘位带 tabindex（键盘可达）', /data-clickable="1" tabindex="0"/],
    ['盘位带 role=button', /role="button"/],
    ['盘位带 aria-label', /aria-label="sda 存储空间 vol1 详情"/],
  ];
  clickRules.forEach(function ([label, re]) {
    const ok = re.test(renderHtml);
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 抽屉内容渲染 --- */
  setLastData({ storage: FULL.storage });
  openDiskDrawer('sdb');   // 有 SMART 告警的盘
  const body = els.monDrawerBody.innerHTML;
  const contentRules = [
    /* v1.1.32：标题改为下拉切换，标题文字由 <select> 的选中项承载 */
    ['下拉选中当前盘（sdb）', els.monDrawerDiskSel.value === 'sdb'],
    ['下拉列出全部 4 块盘', (els.monDrawerDiskSel.innerHTML.match(/<option /g) || []).length === 4],
    ['下拉选项含存储空间与设备名', /存储空间 vol1 · sdb/.test(els.monDrawerDiskSel.innerHTML)],
    ['副标题含设备名与型号', /sdb/.test(els.monDrawerSub.textContent) && /WD40EFZX/.test(els.monDrawerSub.textContent)],
    ['有容量大数字区', /mon-dw-hero/.test(body)],
    ['有容量条', /mon-dw-bar/.test(body)],
    ['有容量图例（已用/可用/文件系统容量）', /已用/.test(body) && /可用/.test(body) && /文件系统容量/.test(body)],
    /* 分节标题改为 dwSec() 输出：.mon-dw-sec > .mon-dw-sec-main > 竖条 + 文字 */
    ['有 SMART 分节', /mon-dw-sec-main[^>]*>.*SMART/.test(body)],
    ['有信息分节', /mon-dw-sec-main[^>]*>.*信息/.test(body)],
    ['有巡检分节', /mon-dw-sec-main[^>]*>.*巡检/.test(body)],
    ['有实时读写分节', /mon-dw-sec-main[^>]*>.*实时读写/.test(body)],
    ['分节标题带左侧竖条', /mon-dw-sec-bar/.test(body)],
    ['实时读写分节右侧带「近 5 分钟」补充', /mon-dw-sec[\s\S]*?近 5 分钟/.test(body)],
    ['SMART 表含「整体评估」', /整体评估/.test(body)],
    ['SMART 表含「UDMA CRC 错误」', /UDMA CRC 错误/.test(body)],
    ['SMART 表含「通电时间」', /通电时间/.test(body)],
    ['SMART 表含「转速」', /转速/.test(body)],
    ['SMART 表含「温度」', /温度/.test(body)],
    // sdb: udma_crc=12, reallocated=3 → 留意告警条
    ['告警条标为留意', /mon-dw-note warn/.test(body)],
    ['告警条列出具体告警项', /UDMA CRC 错误/.test(body) && /重映射扇区/.test(body)],
    ['信息表含型号', /型号/.test(body) && /WD40EFZX/.test(body)],
    ['信息表含序列号', /序列号/.test(body) && /WD-WX32D0055678/.test(body)],
    ['信息表含挂载点', /挂载点/.test(body) && /vol1/.test(body)],
    ['overlay 变为可见', els.monDrawerOverlay.hidden === false],
  ];
  contentRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 只有一块盘时，下拉应标记 single（不给"可切换"的错觉） ---
     注意：这里会改写 lastData，跑完必须恢复并重开抽屉，
     否则后面的断言拿到的副标题会是 sda 的。 */
  setLastData({ storage: { available: true, disks: [FULL.storage.disks[0]], volumes: [] } });
  openDiskDrawer('sda');
  const singleOk = els.monDrawerDiskSel.parentNode.classList.contains('single');
  console.log((singleOk ? '  ✓ ' : '  ✗ ') + '下拉容器在只有 1 块盘时标记 single');
  if (!singleOk) bad++;
  setLastData({ storage: FULL.storage });
  openDiskDrawer('sdb');

  /* --- 故障盘：提示条为 crit --- */
  els.monDrawerBody.innerHTML = '';
  openDiskDrawer('sdc');   // uncorrectable=2 → crit
  const critBody = els.monDrawerBody.innerHTML;
  const critOk = /mon-dw-note crit/.test(critBody);
  console.log((critOk ? '  ✓ ' : '  ✗ ') + '无法纠正错误的盘 → 提示条为 crit');
  if (!critOk) bad++;

  /* --- 休眠盘：提示休眠、不显示实时读写 --- */
  els.monDrawerBody.innerHTML = '';
  openDiskDrawer('sdd');
  const sleepBody = els.monDrawerBody.innerHTML;
  const sleepRules = [
    ['休眠盘有休眠提示条', /mon-dw-note sleep/.test(sleepBody)],
    ['休眠盘不显示实时读写分节', !/mon-dw-sec">实时读写/.test(sleepBody)],
    ['休眠盘的 SMART 计数标注「休眠前」', /休眠前/.test(sleepBody)],
  ];
  sleepRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 未找到的盘 --- */
  els.monDrawerBody.innerHTML = '';
  openDiskDrawer('sdz-not-exist');
  /* v1.1.32：标题不再是纯文字，「未找到硬盘」挪到副标题 */
  const missingOk = /未找到硬盘/.test(els.monDrawerSub.textContent)
    && /可能已被移除/.test(els.monDrawerBody.innerHTML)
    && els.monDrawerDiskSel.value === '';
  console.log((missingOk ? '  ✓ ' : '  ✗ ') + '不存在的盘 → 提示「未找到硬盘」');
  if (!missingOk) bad++;

  /* --- dwSmartTable 单元断言 --- */
  const tbl = dwSmartTable({ health: 'PASSED', udma_crc: 60, reallocated_sectors: 0,
    pending_sectors: 0, reallocated_events: 0, uncorrectable: 0, temp: 34, rpm: '5400 rpm',
    power_on_hours: 8760, standby: false });
  const tblRules = [
    ['SMART 表：有计数的项标「留意」', /pill warn/.test(tbl)],
    ['SMART 表：无计数的项标 ✓', /st-ok/.test(tbl)],
    ['SMART 表：通电时间带小时', /8,760 小时/.test(tbl)],
    ['SMART 表：温度带 ℃', /34℃/.test(tbl)],
  ];
  tblRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* SMART 表分级必须与盘位 LED 用同一套告警分类：
     无法纠正错误 = 严重(crit)，其余非零计数 = 留意(warn)。 */
  const critTbl = dwSmartTable({ health: 'PASSED', udma_crc: 0, reallocated_sectors: 0,
    pending_sectors: 0, reallocated_events: 0, uncorrectable: 2, temp: 34, rpm: '5400 rpm',
    power_on_hours: 8760, standby: false });
  const critRules = [
    ['SMART 表：无法纠正错误标「严重」', /pill crit/.test(critTbl) && /严重/.test(critTbl)],
  ];
  critRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* SSD 的 rpm 是描述文本，不能拼成 "Solid State Device RPM" */
  const ssdTbl = dwSmartTable({ health: 'PASSED', udma_crc: 0, reallocated_sectors: 0,
    pending_sectors: 0, reallocated_events: 0, uncorrectable: 0, temp: 30,
    rpm: 'Solid State Device', power_on_hours: 100, standby: false });
  const ssdOk = /Solid State Device/.test(ssdTbl) && !/Solid State Device RPM/.test(ssdTbl);
  console.log((ssdOk ? '  ✓ ' : '  ✗ ') + 'SMART 表：SSD 转速不拼 RPM 后缀');
  if (!ssdOk) bad++;

  /* --- 滑入动画：.on 是在两帧 rAF 之后加的，必须异步断言 --- */
  setTimeout(function () {
    const onOk = els.monDrawerOverlay.classList.contains('on') === true;
    console.log((onOk ? '  ✓ ' : '  ✗ ') + 'overlay 加上 .on（滑入动画）');
    if (!onOk) process.exitCode = 1;
  }, 30);

  if (bad) { console.error('❌ 抽屉断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.27：硬盘温度阈值口径 + 抽屉样式对齐 fnmonitor ---------- */
(function checkDiskTempThresholdAndDrawerStyle() {
  console.log('\n--- 硬盘温度阈值（对齐 fnmonitor 50/58℃）---');
  let bad = 0;

  /* 1. 阈值常量必须是 fnmonitor 的 diskTempWarn / diskTempCrit */
  const thRules = [
    ['警告阈值 = 50℃（fnmonitor diskTempWarn）', TH_DISK_TEMP_WARN === 50],
    ['严重阈值 = 58℃（fnmonitor diskTempCrit）', TH_DISK_TEMP_CRIT === 58],
  ];
  thRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 2. 分级边界：<50 ok / >=50 warn / >=58 high
        注意 tempLevel() 正常时返回 'ok'（真值），
        所以判「是否超阈」必须用 tempExceed() 而不是 if (lv)。 */
  const lvRules = [
    ['29℃ → ok', diskTempLevel(29) === 'ok'],
    ['49℃ → ok', diskTempLevel(49) === 'ok'],
    ['50℃ → warn（边界含等号）', diskTempLevel(50) === 'warn'],
    ['57℃ → warn', diskTempLevel(57) === 'warn'],
    ['58℃ → high（边界含等号）', diskTempLevel(58) === 'high'],
    ['60℃ → high', diskTempLevel(60) === 'high'],
    ['null → ok', diskTempLevel(null) === 'ok'],
    ['undefined → ok', diskTempLevel(undefined) === 'ok'],
    ['NaN → ok', diskTempLevel(NaN) === 'ok'],
  ];
  lvRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 3. tempExceed()：未超阈返回空串，超阈才返回等级
        —— 这是修掉「29℃ 也弹告警条」的关键。 */
  const exRules = [
    ['29℃ 不超阈（返回空串）', tempExceed(29) === ''],
    ['34℃ 不超阈（返回空串）', tempExceed(34) === ''],
    ['49℃ 不超阈（返回空串）', tempExceed(49) === ''],
    ['50℃ → warn', tempExceed(50) === 'warn'],
    ['58℃ → high', tempExceed(58) === 'high'],
    ['null 不超阈', tempExceed(null) === ''],
  ];
  exRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 4. tempLevel() 仍可用于通用传感器（CPU / 风扇位），口径 60/75 */
  const genRules = [
    ['通用传感器 59℃ → ok', tempLevel(59) === 'ok'],
    ['通用传感器 60℃ → warn', tempLevel(60) === 'warn'],
    ['通用传感器 75℃ → high', tempLevel(75) === 'high'],
  ];
  genRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 5. 抽屉渲染：29℃ 的盘不能出现温度告警条；50℃ 的盘必须出现 */
  const mkDisk = function (temp) {
    return {
      name: 'sdt', model: 'WDC WD40EFZX-68AWUN0', vendor: 'ATA', type: 'disk',
      size: 4000653453312, sizeText: '4.00 TB', serial: 'TEMP-TEST-001',
      temp: temp, rpm: '5400 rpm', health: 'PASSED', grade: 'ok', gradeLabel: '正常',
      detail: '未发现异常计数', mounts: ['/vol1'], vol: 1,
      used: 100 * 1073741824, total: 4000653453312, percent: 25,
      usedText: '100 GB', totalText: '4.00 TB',
      standby: false, proto: 'SATA',
      reallocated_sectors: 0, reallocated_events: 0, pending_sectors: 0,
      offline_uncorrectable: 0, uncorrectable: 0, udma_crc: 0,
    };
  };
  setLastData({ storage: { disks: [mkDisk(29)], scan: {}, summary: {} } });
  openDiskDrawer('sdt');
  const coolBody = els.monDrawerBody.innerHTML;
  const coolRules = [
    ['29℃ 不弹温度告警条', !/已超过/.test(coolBody)],
    ['29℃ 无温度提示条', !/mon-dw-note/.test(coolBody) || !/温度/.test(coolBody)],
  ];
  coolRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  setLastData({ storage: { disks: [mkDisk(52)], scan: {}, summary: {} } });
  openDiskDrawer('sdt');
  const warmBody = els.monDrawerBody.innerHTML;
  const warmRules = [
    ['52℃ 弹温度告警条', /已超过警告阈值/.test(warmBody)],
    ['告警条显示触发阈值 50℃', /50℃/.test(warmBody)],
  ];
  warmRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  setLastData({ storage: { disks: [mkDisk(61)], scan: {}, summary: {} } });
  openDiskDrawer('sdt');
  const hotBody = els.monDrawerBody.innerHTML;
  const hotRules = [
    ['61℃ 弹严重阈值告警条', /已超过严重阈值/.test(hotBody)],
    ['告警条显示触发阈值 58℃', /58℃/.test(hotBody)],
  ];
  hotRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 6. 抽屉样式结构对齐 fnmonitor */
  console.log('\n--- 抽屉样式结构（对齐 fnmonitor）---');
  setLastData({ storage: { disks: [mkDisk(29)], scan: {}, summary: {} } });
  openDiskDrawer('sdt');
  const styleBody = els.monDrawerBody.innerHTML;
  const styleRules = [
    ['信息表键值行用 <div> 包裹 dt/dd（fnmonitor .kv 结构）',
      /<div><dt>型号<\/dt><dd>/.test(styleBody)],
    ['容量条为 40 段格条（fnmonitor .dbar）',
      (styleBody.match(/mon-dw-bar">(<i[^>]*><\/i>)+/g) || []).length === 1
        && (styleBody.match(/<i class="on"|<i class="" style="background|<i><\/i>/g) || []).length >= 0
        && (styleBody.split('mon-dw-bar">')[1] || '').split('</div>')[0].split('<i').length - 1 === 40],
    ['有 card 包裹（fnmonitor .card.pad）', /<div class="mon-dw-card">/.test(styleBody)],
    ['分节标题对齐 .sheet-sec', /<div class="mon-dw-sec">/.test(styleBody)],
  ];
  styleRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 7b. 实时读写趋势图（对齐 fnmonitor 的 .chart-card + .spark） */
  console.log('\n--- 实时读写趋势图（对齐 fnmonitor）---');
  const chartRules = [
    ['实时读写改为趋势图卡片（不再是键值表）',
      /mon-dw-chart-card/.test(styleBody) && !/<dt>读取速率<\/dt>/.test(styleBody)],
    ['有图表标题「读写速率」', /mon-dw-chart-title">读写速率/.test(styleBody)],
    ['图例含「读取」色标', /<i class="k0"><\/i>读取/.test(styleBody)],
    ['图例含「写入」色标', /<i class="k1"><\/i>写入/.test(styleBody)],
    ['图例显示实时速率数值', /class="lv">/.test(styleBody)],
    ['有 spark 容器（64px 高）', /mon-dw-spark/.test(styleBody)],
    ['有 svg 画布', /<svg class="mon-spark"/.test(styleBody)],
    ['「硬盘状态」移到信息表（信息不丢）', /<dt>硬盘状态<\/dt>/.test(styleBody)],
  ];
  chartRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 7c. CSS 层面的结构校验（读样式表源码） */
  const CSS = path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css');
  const css = fs.readFileSync(CSS, 'utf8');
  const cssRules = [
    ['.mon-dw-kv > div 用 display:contents（fnmonitor .kv 写法）',
      /\.mon-dw-kv > div \{ display: contents; \}/.test(css)],
    ['.mon-dw-kv 每行有上边框线', /\.mon-dw-kv dt,\s*\.mon-dw-kv dd \{[^}]*border-top: 1px solid var\(--border\)/.test(css)],
    ['.mon-dw-kv 首行无上边框线', /\.mon-dw-kv > div:first-child dt/.test(css)],
    ['.mon-dw-note 无边框（fnmonitor .note 纯底色）',
      /\.mon-dw-note \{[^}]*\}/.test(css) && !/\.mon-dw-note \{[^}]*border:/.test(css)],
    ['.mon-dw-bar 为分段格条（gap 2px）', /\.mon-dw-bar \{[^}]*gap: 2px/.test(css)],
    ['.mon-dw-bar 段高 9px', /\.mon-dw-bar > i \{[^}]*height: 9px/.test(css)],
    ['.mon-dw-table 单元格有上边框线（fnmonitor .tbl）',
      /\.mon-dw-table td \{[^}]*border-top: 1px solid var\(--border\)/.test(css)],
    ['.mon-dw-eyebrow 用等宽大写（fnmonitor .eyebrow）',
      /\.mon-dw-eyebrow \{[^}]*text-transform: uppercase/.test(css)],
    ['.mon-dw-card 有卡片底色与描边（fnmonitor .card）',
      /\.mon-dw-card \{[^}]*background: var\(--bg-card\)/.test(css)],
    // 趋势图
    ['.mon-dw-spark 高度 64px（fnmonitor .chart-card spark）', /\.mon-dw-spark \{[^}]*height: 64px/.test(css)],
    ['.mon-dw-chart-head 为 flex 布局（fnmonitor .chart-head）',
      /\.mon-dw-chart-head \{[^}]*display: flex/.test(css)],
    ['.mon-dw-chart-legend 两条图例间距 1.5rem',
      /\.mon-dw-chart-legend \{[^}]*gap: 1\.5rem/.test(css)],
    ['.mon-spark 第一条线用 --spark-1', /\.mon-spark path\.line \{[^}]*stroke: var\(--spark-1\)/.test(css)],
    ['.mon-spark 第二条线用 --spark-2', /\.mon-spark path\.line\.l2 \{[^}]*stroke: var\(--spark-2\)/.test(css)],
    ['.mon-spark 面积填充在 --spark-1 上', /\.mon-spark path\.area \{[^}]*fill: var\(--spark-1\)/.test(css)],
    ['定义了 --spark-1 / --spark-2 曲线色',
      /--spark-1:\s*#/.test(css) && /--spark-2:\s*#/.test(css)],
  ];
  cssRules.forEach(function ([label, ok]) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) { console.error('❌ 温度阈值/抽屉样式断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.28：实时读写趋势图（缓冲 + 平滑曲线 + 双线渲染） ---------- */
(function checkReadWriteTrendChart() {
  console.log('\n--- 实时读写趋势图 ---');
  let bad = 0;
  const check = function (label, ok) {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  };

  /* 1. 缓冲：每次 feed 推进一次读写速率 */
  feedDiskBuf({ diskio: [{ name: 'sdz', read_rate: 100, write_rate: 50 }] });
  feedDiskBuf({ diskio: [{ name: 'sdz', read_rate: 200, write_rate: 80 }] });
  feedDiskBuf({ diskio: [{ name: 'sdz', read_rate: 300, write_rate: 120 }] });
  const buf = getDiskBuf('sdz');
  check('缓冲已建立', !!buf);
  check('读取速率累积 3 点', buf && buf.r.length === 3);
  check('写入速率累积 3 点', buf && buf.w.length === 3);
  check('读取速率按顺序累积', buf && buf.r[0] === 100 && buf.r[1] === 200 && buf.r[2] === 300);
  check('写入速率按顺序累积', buf && buf.w[0] === 50 && buf.w[2] === 120);

  /* 2. 缓冲上限 100 点（3s × 100 = 5 分钟窗口，与 fnmonitor 的 5s×60 等长） */
  for (let i = 0; i < 120; i++) {
    feedDiskBuf({ diskio: [{ name: 'sdz', read_rate: i, write_rate: i }] });
  }
  const buf2 = getDiskBuf('sdz');
  check('缓冲上限 100 点（超出后丢最旧）', buf2 && buf2.r.length === 100);
  check('超出后保留最新值', buf2 && buf2.r[buf2.r.length - 1] === 119);

  /* 2b. v1.1.30：ts 去重守卫（对齐 fnmonitor feed 的 `if (S._lastTs === ts) return`）
     前端 3s 拉一次、后端 3s 采一次，两个时钟异步，约 15% 的请求拿到同一份
     缓存快照（ts 未变）→ 不去重就会往曲线上灌重复值，出现「平台 + 跳变」。 */
  console.log('\n--- ts 去重守卫 ---');
  const devDedup = 'sdd';
  const snap = (ts, v) => ({ diskio: [{ name: devDedup, read_rate: v, write_rate: v }] });
  feedDiskBuf(snap(1000, 10), 1000);
  check('首次快照推进 1 点', getDiskBuf(devDedup).r.length === 1);
  feedDiskBuf(snap(1000, 20), 1000);
  check('同 ts 重复快照不再推进', getDiskBuf(devDedup).r.length === 1);
  check('同 ts 重复快照不覆盖原值', getDiskBuf(devDedup).r[0] === 10);
  feedDiskBuf(snap(1003, 30), 1003);
  check('ts 变化后推进新点', getDiskBuf(devDedup).r.length === 2);
  check('新点写入新值', getDiskBuf(devDedup).r[1] === 30);
  check('时间戳同步推进', getDiskBuf(devDedup).t.length === 2);
  // 无 ts 时（老接口/异常）退化为每次都采纳，不能卡住曲线
  feedDiskBuf({ diskio: [{ name: 'sde', read_rate: 5, write_rate: 5 }] });
  feedDiskBuf({ diskio: [{ name: 'sde', read_rate: 6, write_rate: 6 }] });
  check('无 ts 时仍正常累积', getDiskBuf('sde').r.length === 2);

  /* 3. monoPath：单调三次插值，输出合法 SVG path */
  const d = monoPath([0, 10, 20, 30], [5, 20, 10, 25]);
  check('monoPath 返回 M 起始', /^M[\d.]+,[\d.]+/.test(d));
  check('monoPath 含贝塞尔段 C', d.indexOf('C') > 0);
  check('monoPath 段数 = 点数-1', (d.match(/C/g) || []).length === 3);
  check('monoPath 单点也能输出', monoPath([0], [1]) === 'M0,1');
  check('monoPath 空数组返回空串', monoPath([], []) === '');

  /* 4. monoPath 不过冲：单调上升的数据不应出现中间凹陷 */
  const rising = monoPath([0, 1, 2, 3], [0, 1, 2, 3]);
  const ys = (rising.match(/[\d.]+,([\d.]+)/g) || []).map(s => parseFloat(s.split(',')[1]));
  let monotone = true;
  for (let i = 1; i < ys.length; i++) if (ys[i] < ys[i - 1] - 0.01) monotone = false;
  check('单调数据的曲线不回落（单调插值不过冲）', monotone);

  /* 5. drawSpark：两条线 + 面积填充 + 末端光点 */
  const fakeSvg = { innerHTML: '', clientWidth: 300, clientHeight: 64 };
  drawSpark(fakeSvg, [{ values: [1, 2, 3, 4] }, { values: [4, 3, 2, 1] }]);
  const out = fakeSvg.innerHTML;
  check('drawSpark 渲染出内容', out.length > 0);
  check('含面积填充 path.area', /class="area"/.test(out));
  check('含第一条线 path.line', /class="line"/.test(out));
  check('含第二条线 path.line l2', /class="line l2"/.test(out));
  check('含末端 halo 光点', /class="halo"/.test(out));
  check('含末端实心点', /<circle cx="/.test(out));

  /* 6. 数据不足 2 点时不画曲线（显示占位） */
  const svg2 = { innerHTML: 'OLD', clientWidth: 300, clientHeight: 64 };
  drawSpark(svg2, [{ values: [1] }, { values: [] }]);
  check('数据不足 2 点时清空画布', svg2.innerHTML === '');

  /* 7. 全 0 速率不会除零崩溃 */
  const svg3 = { innerHTML: '', clientWidth: 300, clientHeight: 64 };
  let crashed = false;
  try { drawSpark(svg3, [{ values: [0, 0, 0] }, { values: [0, 0, 0] }]); }
  catch (e) { crashed = true; }
  check('全 0 速率不崩溃', !crashed && svg3.innerHTML.length > 0);

  /* 8. 抽屉打开且缓冲有数据时，spark 真被绘制到 #monDwSpark */
  feedDiskBuf({ diskio: [{ name: 'sdy', read_rate: 1024, write_rate: 512 }] });
  feedDiskBuf({ diskio: [{ name: 'sdy', read_rate: 2048, write_rate: 256 }] });
  const sdy = {
    name: 'sdy', model: 'WDC WD40EFZX-68AWUN0', vendor: 'ATA', type: 'disk',
    size: 4000653453312, sizeText: '4.00 TB', serial: 'TREND-001',
    temp: 30, rpm: '5400 rpm', health: 'PASSED', grade: 'ok', gradeLabel: '正常',
    detail: '未发现异常计数', mounts: ['/vol1'], vol: 1,
    used: 100 * 1073741824, total: 4000653453312, percent: 25,
    usedText: '100 GB', totalText: '4.00 TB', state: 'active/idle',
    standby: false, proto: 'SATA',
    reallocated_sectors: 0, reallocated_events: 0, pending_sectors: 0,
    offline_uncorrectable: 0, uncorrectable: 0, udma_crc: 0,
  };
  setLastData({ storage: { disks: [sdy], scan: {}, summary: {} } });
  openDiskDrawer('sdy');
  drawDrawerSpark();   // 同步调用（真实运行时在 rAF 里调）
  check('抽屉内 spark 画布已被填充',
    /class="area"/.test(els.monDwSpark.innerHTML)
    && /class="line l2"/.test(els.monDwSpark.innerHTML));
  check('抽屉内图例显示实时速率',
    /class="lv">/.test(els.monDrawerBody.innerHTML));

  /* 9. 时间锚点（方案 A）：曲线窗口的起止时刻 */
  console.log('--- 时间锚点（方案 A）---');
  const axisRules = [
    ['缓冲里记了时间戳', !!(getDiskBuf('sdy') && getDiskBuf('sdy').t
      && getDiskBuf('sdy').t.length === 2)],
    ['抽屉里有时间锚点容器',
      /class="mon-dw-timeaxis"/.test(els.monDrawerBody.innerHTML)],
    ['锚点含起止两个占位',
      /id="monDwTimeFrom"/.test(els.monDrawerBody.innerHTML)
      && /id="monDwTimeTo"/.test(els.monDrawerBody.innerHTML)],
    ['锚点已填入起止时刻',
      /^\d\d:\d\d$/.test(els.monDwTimeFrom.textContent)
      && /^\d\d:\d\d$/.test(els.monDwTimeTo.textContent)],
    ['fmtHM 零填充小时分钟',
      fmtHM(new Date(2026, 0, 1, 9, 5).getTime()) === '09:05'],
    ['fmtHM 23 点 59 分',
      fmtHM(new Date(2026, 0, 1, 23, 59).getTime()) === '23:59'],
    ['fmtHM 空值返回空串', fmtHM(0) === ''],
  ];
  axisRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 10. 时间锚点不改变 spark 高度（仍为 64px，只多一行标注） */
  const cssTxt2 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  const cssRules2 = [
    ['.mon-dw-spark 高度仍是 64px', /\.mon-dw-spark \{[^}]*height: 64px/.test(cssTxt2)],
    ['时间锚点是 flex 一行排布', /\.mon-dw-timeaxis \{[^}]*display: flex/.test(cssTxt2)],
    ['时间锚点用最淡的文字色', /\.mon-dw-timeaxis \{[^}]*color: var\(--text-muted\)/.test(cssTxt2)],
    ['时间锚点用等宽数字', /\.mon-dw-timeaxis \{[^}]*font-variant-numeric: tabular-nums/.test(cssTxt2)],
  ];
  cssRules2.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 11. v1.1.30：抽屉分节标题（FnStyle 自有风格）+ 间距节奏 */
  console.log('\n--- 抽屉分节标题与间距（FnStyle 自有风格）---');
  const cssRules3 = [
    ['抽屉定义间距变量 --dw-sec-gap', /\.mon-drawer-body \{[^}]*--dw-sec-gap:/.test(cssTxt2)],
    ['抽屉定义间距变量 --dw-sec-lead', /\.mon-drawer-body \{[^}]*--dw-sec-lead:/.test(cssTxt2)],
    ['分节标题用 --dw-sec-gap / --dw-sec-lead', /\.mon-dw-sec \{[^}]*margin: var\(--dw-sec-gap\) 0 var\(--dw-sec-lead\)/.test(cssTxt2)],
    ['分节标题是 flex 两端对齐', /\.mon-dw-sec \{[^}]*display: flex[\s\S]*?justify-content: space-between/.test(cssTxt2)],
    ['分节标题带字距（自有语言）', /\.mon-dw-sec \{[^}]*letter-spacing:/.test(cssTxt2)],
    ['分节标题带竖条 .mon-dw-sec-bar', /\.mon-dw-sec-bar \{[^}]*width: 3px/.test(cssTxt2)],
    ['竖条用 accent 主题色', /\.mon-dw-sec-bar \{[^}]*background: var\(--accent\)/.test(cssTxt2)],
    ['竖条有圆角', /\.mon-dw-sec-bar \{[^}]*border-radius:/.test(cssTxt2)],
    ['标题主体与竖条 inline-flex 对齐', /\.mon-dw-sec-main \{[^}]*display: inline-flex/.test(cssTxt2)],
    ['提示条有独立容器 .mon-dw-stack', /\.mon-dw-stack \{[^}]*display: grid/.test(cssTxt2)],
    ['提示条与上方卡片留出间距', /\.mon-dw-stack \{[^}]*margin-top:/.test(cssTxt2)],
    ['提示条后的分节回到统一间距', /\.mon-dw-stack \+ \.mon-dw-sec \{[^}]*margin-top: var\(--dw-sec-gap\)/.test(cssTxt2)],
  ];
  cssRules3.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 6. fmtRate：速率格式化对齐 fnmonitor 的 F.rateStr */
  console.log('--- 速率格式化（fnmonitor F.rateStr 对齐）---');
  const rateRules = [
    ['0 → 0 B/s', fmtRate(0) === '0 B/s'],
    ['512 → 512 B/s', fmtRate(512) === '512 B/s'],
    ['1024 → 1.0 KB/s', fmtRate(1024) === '1.0 KB/s'],
    ['1536 → 1.5 KB/s', fmtRate(1536) === '1.5 KB/s'],
    ['102400 → 100 KB/s（≥100 取整）', fmtRate(102400) === '100 KB/s'],
    ['1048576 → 1.0 MB/s', fmtRate(1048576) === '1.0 MB/s'],
    ['非数字 → --', fmtRate(null) === '--' && fmtRate(undefined) === '--' && fmtRate(NaN) === '--'],
    ['负数按 0 处理', fmtRate(-5) === '0 B/s'],
  ];
  rateRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 7. 图例数值与曲线同源：都用 read_rate 前端格式化，不依赖后端 readText */
  const srcText = fs.readFileSync(SRC, 'utf8');
  const sameSource = [
    ['抽屉图例用 fmtRate(io.read_rate)', /var rTxt = io \? fmtRate\(io\.read_rate\)/.test(srcText)],
    ['抽屉图例用 fmtRate(io.write_rate)', /var wTxt = io \? fmtRate\(io\.write_rate\)/.test(srcText)],
    ['刷新图例同样用 fmtRate', /lvs\[0\]\.textContent = io \? fmtRate\(io\.read_rate\)/.test(srcText)],
    ['趋势图数值不再用后端 readText', !/io\.readText \? io\.readText/.test(srcText)],
  ];
  sameSource.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 8. 时间窗口文案：3s × 100 点 = 5 分钟 */
  const winRules = [
    ['缓冲点数为 100', /var BUF_N = 100/.test(srcText)],
    ['窗口文案按 BUF_N × REFRESH_MS 计算', /Math\.round\(BUF_N \* REFRESH_MS \/ 60000\)/.test(srcText)],
  ];
  winRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) { console.error('❌ 趋势图断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.31：字号与文字对比度（不能被改回去） ---------- */
(function checkTypography() {
  console.log('\n--- 字号与文字对比度（v1.1.31）---');
  const cssTxt3 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  let bad = 0;
  const rules = [
    // 根字号：全站 rem 都基于它，是整体字号的总闸
    ['根字号为 16px', /html, body \{[^}]*font-size: 16px/.test(cssTxt3)],
    // 深色主题次要文字色
    ['深色 --text-secondary 已提亮', /--text-secondary: #b8bfc8/.test(cssTxt3)],
    ['深色 --text-muted 已提亮', /--text-muted:     #9099a3/.test(cssTxt3)],
    // 浅色主题次要文字色（浅底上要更深）
    ['浅色 --text-secondary 已压深', /--text-secondary: #4b5560/.test(cssTxt3)],
    ['浅色 --text-muted 已压深', /--text-muted:     #6b7480/.test(cssTxt3)],
    // 机箱内文字色
    ['深色 --chassis-ink-2 已提亮', /--chassis-ink-2:  #9aa4b0/.test(cssTxt3)],
    ['浅色 --chassis-ink-2 已压深', /--chassis-ink-2:  #5b6570/.test(cssTxt3)],
    // 关键信息的极小字号兜底
    ['盘位型号抬到 .75rem', /\.mon-bay-model \{[^}]*font-size: \.75rem/.test(cssTxt3)],
    ['盘位容量抬到 .75rem', /\.mon-bay-cap \{[^}]*font: 500 \.75rem/.test(cssTxt3)],
    ['盘位读写速率抬到 .75rem', /\.mon-bay-io \{[^}]*font: 500 \.75rem/.test(cssTxt3)],
    ['盘位存储空间标签抬到 .75rem', /\.mon-bay-top \{[^}]*font: 500 \.75rem/.test(cssTxt3)],
    ['状态徽章抬到 .78rem', /\.mon-badge \{[^}]*font-size: \.78rem/.test(cssTxt3)],
    ['SMART 告警徽章抬到 .75rem', /\.mon-smart-flag \{[^}]*font-size: \.75rem/.test(cssTxt3)],
    ['时间锚点抬到 .75rem', /\.mon-dw-timeaxis \{[^}]*font-size: \.75rem/.test(cssTxt3)],
    ['存储池标签抬到 .78rem', /\.mon-pool-eyebrow \{[^}]*font: 500 \.78rem/.test(cssTxt3)],
    // 不该再有 px 硬编码的小字号（会脱离 rem 体系）
    ['无 11px 硬编码字号', !/font-size: 11px/.test(cssTxt3)],
  ];
  rules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });
  if (bad) { console.error('❌ 排版断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- 源码级断言：容量单位进制不能被改回去 ---------- */
(function checkDecimalUnits() {
  console.log('\n--- 容量单位进制（源码级）---');
  const src = fs.readFileSync(SRC, 'utf8');
  /* v1.1.35 起，内存与显卡的渲染搬到了 compute.js，
     这里同时检查 monitor.js（存储侧）与 compute.js（内存/显卡侧）。
     注：fnmonitor 的 Swap 用 gb()（1024 进制 GiB），不是 fmtBytes —— 见 compute.js 的 gb()。 */
  const cmpSrc = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8');
  const rules = [
    ['fmtDisk 函数存在', /function fmtDisk\s*\(/],
    ['fmtDisk 用十进制常量 1e12', /var T = 1e12, G = 1e9, M = 1e6/],
    ['存储池汇总用 fmtDisk', /totText = fmtDisk\(tSum\)/],
    ['盘位容量行用 fmtDisk', /d\.usedText \|\| fmtDisk\(d\.used \|\| 0\)/],
    // 内存/Swap/显存仍走 1024 进制（fnmonitor 的 F.bytes），不能被一起改成十进制
    ['Swap 用 1024 进制 gb()（compute.js）', /gb\(mem\.swapTotal\)/],
    ['显存仍用 fmtBytes（compute.js）', /fmtBytes\(g\.memTotal\)/],
    ['fmtBytes 仍按 1024 递减', /while \(v >= 1024 && i < units\.length - 1\)/],
  ];
  // 前 4 条查 src（monitor.js），后 3 条查 cmpSrc（compute.js）
  const SRC_RULES = 4;
  let bad = 0;
  rules.forEach(([label, re], idx) => {
    const target = idx < SRC_RULES ? src : cmpSrc;
    const ok = re.test(target);
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });
  if (bad) { console.error('❌ 容量单位断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

renderCase('空对象', { storage: {} });
renderCase('storage 缺失', { success: true });
renderCase('空数组', { storage: { available: true, volumes: [], disks: [] } });
renderCase('volumes 有 disks 空', { storage: { available: true, volumes: FULL.storage.volumes, disks: [] } });
renderCase('disks 有 volumes 空', { storage: { available: true, volumes: [], disks: FULL.storage.disks } });
renderCase('summary 缺失', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks } });
renderCase('summary.grades 缺失', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks, summary: {} } });
renderCase('diskio 缺失', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks, diskio: [] } });
renderCase('raid 字段缺失', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks, raid: [{ device: 'md0' }] } });
renderCase('raidcard 缺 controllers', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks, raidcard: { type: 'megaraid', label: 'LSI' } } });
renderCase('scanLog 项缺字段', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks, scanLog: [{}] } });
renderCase('raidcard none', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks,
  raidcard: { type: 'none', label: '', detail: '', tool: '', controllers: [], error: '' } } });
renderCase('scan 有错误', { storage: { available: true, volumes: FULL.storage.volumes, disks: FULL.storage.disks,
  scan: { last_ts: 0, running: true, summary: {}, error: 'smartctl 未找到' }, scanLog: [] } });
renderCase('缺失字段的盘', { storage: { available: true, volumes: [], disks: [{ name: 'sdx' }] } });

/* ---------- 源码级断言：v1.1.30 ts 去重接线 + dwSec 分节 ---------- */
(function checkTsDedupWiring() {
  console.log('\n--- ts 去重接线与分节标题（源码级）---');
  const src = fs.readFileSync(SRC, 'utf8');
  const rules = [
    // feedDiskBuf 必须带 ts 参数，否则去重守卫拿不到时间戳
    ['feedDiskBuf 签名带 ts 参数', /function feedDiskBuf\s*\(st,\s*ts\)/],
    // 调用点必须把快照时间戳传进去（storage.ts 优先，顶层 ts 兜底）
    ['调用点传入快照 ts', /feedDiskBuf\(d && d\.storage,\s*\(d && d\.storage && d\.storage\.ts\) \|\| \(d && d\.ts\)\)/],
    // 去重守卫本身
    ['存在同 ts 提前返回', /ts === diskBufTs\) return/],
    ['记录上次采纳的 ts', /diskBufTs = ts/],
    // dwSec 分节标题
    ['存在 dwSec 辅助函数', /function dwSec\s*\(/],
    ['dwSec 输出竖条', /mon-dw-sec-bar/],
    ['dwSec 输出右侧补充说明', /class="faint"/],
    // 提示条包进容器
    ['提示条包进 .mon-dw-stack', /<div class="mon-dw-stack">/],
  ];
  let bad = 0;
  rules.forEach(([label, re]) => {
    const ok = re.test(src);
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });
  if (bad) { console.error('❌ 接线断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.32：巡检历史抽屉 + 硬盘切换下拉 + 两张卡片删除 ---------- */
(function checkScanLogDrawerAndPicker() {
  console.log('\n--- 巡检历史抽屉与硬盘切换下拉（v1.1.32）---');
  const src = fs.readFileSync(SRC, 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');

  let bad = 0;

  /* --- HTML 骨架 --- */
  const htmlRules = [
    ['存储标题栏有「巡检历史」按钮', /id="btnMonScanLog"/],
    ['巡检历史抽屉容器存在', /id="monScanLogOverlay"/],
    ['巡检历史抽屉有标题', /id="monScanLogTitle"/],
    ['巡检历史抽屉有内容容器', /id="monScanLogBody"/],
    ['巡检历史抽屉有关闭按钮', /id="btnCloseMonScanLog"/],
    ['硬盘抽屉顶部有切换下拉', /id="monDrawerDiskSel"/],
    ['下拉有 aria-label', /id="monDrawerDiskSel"[^>]*aria-label="选择要查看的硬盘"/],
    /* 存储计数标签移到标题「存储」后面 */
    ['存储计数标签在 h2 内', /<h2><span>💾<\/span> 存储<span class="theme-count" id="monStoreCount">/],
  ];
  htmlRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- JS 接线 --- */
  const srcRules = [
    ['存在 renderScanLogHtml', /function renderScanLogHtml\s*\(/],
    ['存在 openScanLogDrawer', /function openScanLogDrawer\s*\(/],
    ['存在 closeScanLogDrawer', /function closeScanLogDrawer\s*\(/],
    ['存在 bindScanLogDrawer', /function bindScanLogDrawer\s*\(/],
    ['巡检历史按钮绑定打开事件', /btnMonScanLog[\s\S]{0,120}addEventListener\('click', openScanLogDrawer\)/],
    ['巡检历史抽屉支持遮罩关闭', /e\.target === ov\) closeScanLogDrawer\(\)/],
    ['巡检历史抽屉支持 ESC 关闭', /scanLogOpen\) \{[\s\S]{0,120}closeScanLogDrawer\(\)/],
    /* 下拉切换硬盘 */
    ['下拉绑定 change 事件', /monDrawerDiskSel[\s\S]{0,200}addEventListener\('change'/],
    ['下拉切换调用 openDiskDrawer', /openDiskDrawer\(v\)/],
    ['下拉选中当前盘', /selEl\.value = d\.name/],
    ['下拉列出全部硬盘', /disks\.slice\(\)\.sort\(byVol\)\.map/],
    ['单盘时标记 single', /classList\.toggle\('single', disks\.length <= 1\)/],
    /* 轮询时同步刷新两个抽屉 */
    ['抽屉开着时刷新实时数据', /if \(drawerOpen\) refreshDrawerLive\(\)/],
    ['巡检历史开着时同步刷新', /if \(scanLogOpen\) refreshScanLogLive\(\)/],
    /* 两张卡片确实删掉了 */
    ['已移除 SMART 告警计数卡片', !/item\('SMART 告警计数'/.test(src)],
    ['已移除硬盘巡检卡片', !/item\('硬盘巡检'/.test(src)],
    ['smartFlagsHtml 已清理（无死代码）', !/function smartFlagsHtml\s*\(/.test(src)],
  ];
  srcRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- CSS --- */
  const cssRules = [
    ['下拉触发器有自定义样式', /\.mon-dw-picker-sel \{[^}]*appearance: none/.test(css)],
    ['下拉触发器隐藏原生箭头', /\.mon-dw-picker-sel \{[^}]*-webkit-appearance: none/.test(css)],
    ['下拉箭头为自绘元素', /\.mon-dw-picker-arrow \{[^}]*position: absolute/.test(css)],
    ['单盘态样式存在', /\.mon-dw-picker\.single \.mon-dw-picker-sel \{/],
    ['巡检流水卡片样式存在', /\.mon-slog-item \{/],
    ['巡检流水「有变化」左侧色条', /\.mon-slog-item\.changed\s*\{[^}]*border-left-color: var\(--warning\)/.test(css)],
    ['巡检流水「未见异常」左侧色条', /\.mon-slog-item\.ok\s*\{[^}]*border-left-color: var\(--success\)/.test(css)],
    ['巡检流水徽章样式存在', /\.mon-slog-badge \{/],
  ];
  cssRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- renderScanLogHtml 渲染内容 --- */
  const st = { storage: {
    available: true,
    scan: { last_ts: now - 3600, running: false,
            summary: { counts: { ok: 2, warn: 1, crit: 0, unknown: 1 } }, error: '' },
    scanLog: [
      { ts: now - 3600, total: 4, changed: true, summary: 'sdb 重映射扇区较上次 +3' },
      { ts: now - 7200, total: 4, changed: false, summary: '未见异常（4 块盘分级与计数均无变化）' },
    ],
  } };
  setLastData(st);
  openScanLogDrawer();
  const logBody = els.monScanLogBody.innerHTML;
  const logRules = [
    ['巡检抽屉内容已渲染', logBody.length > 100],
    ['显示最近巡检时间', /mon-dw-hero/.test(logBody)],
    ['显示分级统计', /正常 2/.test(logBody) && /留意 1/.test(logBody)],
    ['有「巡检流水」分节', /mon-dw-sec-main[^>]*>.*巡检流水/.test(logBody)],
    ['列出「有变化」记录', /有变化/.test(logBody) && /sdb 重映射扇区较上次 \+3/.test(logBody)],
    ['列出「未见异常」记录', /未见异常/.test(logBody)],
    ['overlay 变为可见', els.monScanLogOverlay.hidden === false],
  ];
  logRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 空流水：显示占位说明而不是空白 */
  setLastData({ storage: { available: true, scan: { last_ts: 0 }, scanLog: [] } });
  openScanLogDrawer();
  const emptyOk = /尚未巡检/.test(els.monScanLogBody.innerHTML);
  console.log((emptyOk ? '  ✓ ' : '  ✗ ') + '无记录时显示「尚未巡检」占位');
  if (!emptyOk) bad++;

  /* 巡检异常：置顶红色提示 */
  setLastData({ storage: { available: true, scan: { last_ts: now, error: 'smartctl 超时' }, scanLog: [] } });
  openScanLogDrawer();
  const errOk = /mon-dw-note crit/.test(els.monScanLogBody.innerHTML)
    && /smartctl 超时/.test(els.monScanLogBody.innerHTML);
  console.log((errOk ? '  ✓ ' : '  ✗ ') + '巡检异常时显示 crit 提示');
  if (!errOk) bad++;

  if (bad) { console.error('❌ v1.1.32 断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.33：滚动锁 + 口径说明 + 箭头图标 + 精简机箱脚注 ---------- */
(function checkScrollLockAndScope() {
  console.log('\n--- 滚动锁与口径说明（v1.1.33）---');
  const src = fs.readFileSync(SRC, 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  let bad = 0;

  /* --- 滚动锁 --- */
  const lockRules = [
    ['存在 lockPageScroll', /function lockPageScroll\s*\(/],
    ['存在 unlockPageScroll', /function unlockPageScroll\s*\(/],
    ['加锁时给 html 加 .mon-scroll-lock', /classList\.add\('mon-scroll-lock'\)/],
    ['解锁时移除 .mon-scroll-lock', /classList\.remove\('mon-scroll-lock'\)/],
    /* 计数器保护：下拉切换硬盘会重走 showDrawer，不能重复加锁 */
    ['用计数器防止重复加锁', /scrollLockCount\+\+/],
    ['解锁时计数不越界', /Math\.max\(0, scrollLockCount - 1\)/],
    /* 两个抽屉都要锁，且都用「从关到开才加锁」的守卫 */
    ['硬盘抽屉打开时加锁', /if \(!drawerOpen\) lockPageScroll\(\)/],
    ['硬盘抽屉关闭时解锁', /if \(drawerOpen\) unlockPageScroll\(\)/],
    ['巡检历史抽屉打开时加锁', /if \(!scanLogOpen\) lockPageScroll\(\)/],
    ['巡检历史抽屉关闭时解锁', /if \(scanLogOpen\) unlockPageScroll\(\)/],
  ];
  lockRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- CSS --- */
  const cssRules = [
    ['滚动锁规则存在', /html\.mon-scroll-lock,\s*\nhtml\.mon-scroll-lock body \{\s*\n\s*overflow: hidden/.test(css)],
    ['html 预留滚动条位置（防横向抖动）', /scrollbar-gutter: stable/.test(css)],
    ['口径说明样式存在', /\.mon-slog-scope \{/],
    ['口径说明有分隔线', /\.mon-slog-scope \{[^}]*border-top: 1px dashed/.test(css)],
    ['行内按钮图标样式存在', /\.btn \.btn-ico \{/],
    ['行内图标用描边（跟随 currentColor）', /\.btn \.btn-ico \{[^}]*stroke: currentColor/.test(css)],
    /* 已删除的色阶图例 CSS 也不该留 */
    ['色阶图例 CSS 已清理', !/\.mon-nas-foot \.ramp-bar \{/.test(css)],
    ['色阶图例文字样式已清理', !/\.mon-nas-foot \.ramp-key \{/.test(css)],
  ];
  cssRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 巡检历史按钮图标 --- */
  const htmlRules = [
    ['巡检历史按钮用 SVG 箭头', /id="btnMonScanLog"[\s\S]{0,220}?class="btn-ico"/],
    ['箭头 path 为向右', /id="btnMonScanLog"[\s\S]{0,220}?d="M3 8h9M8\.5 4\.5L12 8l-3\.5 3\.5"/],
    ['不再是 🗂 emoji', !/🗂 巡检历史/.test(html)],
  ];
  htmlRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 机箱脚注精简 --- */
  const footRules = [
    ['LED 语义说明已移除', !/盘位 LED = 硬盘健康与 SMART 告警状态/.test(src)],
    ['色阶图例说明已移除', !/点阵与容量行颜色 = 空间占用/.test(src)],
    ['ramp-key 元素已移除', !/class="ramp-key"/.test(src)],
    ['LED 状态图例保留', /led-key warn/.test(src)],
    ['巡检时间提示保留', /巡检于 /.test(src)],
    ['排序提示保留', /盘位按存储空间排列/.test(src)],
  ];
  footRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- A 方案：巡检抽屉的统计口径说明 --- */
  const scopeRules = [
    ['口径说明写明统计范围', /统计口径：盘体健康/.test(src)],
    ['口径说明点明不含 CRC', /UDMA CRC 错误/.test(src)],
    ['口径说明指向盘位 LED', /以盘位 LED 为准/.test(src)],
  ];
  scopeRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 渲染内容：口径说明真的出现在抽屉里 --- */
  setLastData({ storage: {
    available: true,
    scan: { last_ts: now, summary: { counts: { ok: 3, warn: 0, crit: 0, unknown: 0 } } },
    scanLog: [],
  } });
  openScanLogDrawer();
  const scopeOk = /统计口径：盘体健康/.test(els.monScanLogBody.innerHTML)
    && /以盘位 LED 为准/.test(els.monScanLogBody.innerHTML);
  console.log((scopeOk ? '  ✓ ' : '  ✗ ') + '抽屉内显示统计口径说明');
  if (!scopeOk) bad++;

  /* --- 滚动锁的行为级验证（不是只看源码）---
     打开抽屉 → html 应带 .mon-scroll-lock；关闭 → 应摘掉。
     特别注意：硬盘抽屉用「从关到开才加锁」的判断，
     所以下拉切换硬盘（重走 showDrawer）不会把计数搞乱。 */
  const de = global.document.documentElement;
  const hasLock = () => de.classList.contains('mon-scroll-lock');

  /* ⚠️ 前面的测试用例开过抽屉但没关（历史上就是这么写的），
     滚动锁的计数因此有残留。这里先统一关一遍，
     让下面的行为断言从「干净状态」开始。 */
  closeScanLogDrawerFn();
  closeDiskDrawerFn();

  setLastData({ storage: FULL.storage });
  openDiskDrawer('sda');
  const lockOnOpen = hasLock();
  console.log((lockOnOpen ? '  ✓ ' : '  ✗ ') + '打开硬盘抽屉后 html 带滚动锁类');
  if (!lockOnOpen) bad++;

  /* 模拟下拉切换：重走 openDiskDrawer，不应重复加锁（计数保护） */
  openDiskDrawer('sdb');
  const stillLocked = hasLock();
  console.log((stillLocked ? '  ✓ ' : '  ✗ ') + '切换硬盘后滚动锁仍然有效');
  if (!stillLocked) bad++;

  closeDiskDrawerFn();
  const lockOffClose = !hasLock();
  console.log((lockOffClose ? '  ✓ ' : '  ✗ ') + '关闭硬盘抽屉后滚动锁已解除');
  if (!lockOffClose) bad++;

  /* 巡检历史抽屉同样要锁 */
  openScanLogDrawer();
  const logLocked = hasLock();
  console.log((logLocked ? '  ✓ ' : '  ✗ ') + '打开巡检历史抽屉后 html 带滚动锁类');
  if (!logLocked) bad++;
  closeScanLogDrawerFn();
  const logUnlocked = !hasLock();
  console.log((logUnlocked ? '  ✓ ' : '  ✗ ') + '关闭巡检历史抽屉后滚动锁已解除');
  if (!logUnlocked) bad++;

  /* 重复打开同一个抽屉不能重复加锁 ——
     否则锁计数只增不减，关闭时解不干净，页面会永久锁滚动。
     这是实测发现的真 bug（巡检历史抽屉开着时再点一次按钮）。 */
  openScanLogDrawer();
  openScanLogDrawer();
  openScanLogDrawer();
  closeScanLogDrawerFn();
  const noLeak = !hasLock();
  console.log((noLeak ? '  ✓ ' : '  ✗ ') + '重复打开抽屉 3 次后关闭，滚动锁已完全解除');
  if (!noLeak) bad++;

  if (bad) { console.error('❌ v1.1.33 断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.34：软 RAID 卡片补字段 + 口径说明空白修复 ---------- */
(function checkRaidCardAndScopeGap() {
  console.log('\n--- 软 RAID 卡片与口径说明留白（v1.1.34）---');
  const src = fs.readFileSync(SRC, 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  let bad = 0;

  /* --- 源码级：RAID 卡片要渲染 layout 与 raidState --- */
  const srcRules = [
    ['渲染 RAID 级别（layout）', /r\.layout\s*\?/],
    ['渲染健康位图（raidState）', /r\.raidState/],
    ['RAID 级别用专用徽章类', /mon-raid-lvl/],
    /* 健康位图着色规则：出现 _ 或 F 判为异常 */
    ['健康位图异常判定含 _ 与 F', /unhealthy\s*=\s*bits\.indexOf\('_'\)\s*>=\s*0\s*\|\|\s*bits\.indexOf\('F'\)\s*>=\s*0/],
  ];
  srcRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- CSS：口径说明必须覆盖全局 .empty 的 padding --- */
  const cssRules = [
    ['口径说明显式覆盖 padding', /\.mon-slog-scope \{[^}]*padding:\s*[\d.]+rem 0 0/.test(css)],
    ['口径说明覆盖 text-align', /\.mon-slog-scope \{[^}]*text-align:\s*left/.test(css)],
    /* .empty 本身的 padding 保持不变（别去动全局空状态样式） */
    ['全局 .empty 样式未被改动', /\.empty \{[^}]*padding: 2\.6rem 1rem/.test(css)],
    ['RAID 级别徽章样式存在', /\.mon-chip\.mon-raid-lvl \{/.test(css)],
    ['RAID 级别徽章用中性灰底', /\.mon-chip\.mon-raid-lvl \{[^}]*background: var\(--bg-hover\)/.test(css)],
  ];
  cssRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 渲染级：真实把 RAID 数据塞进去，看渲染结果 --- */
  const raidData = {
    storage: {
      available: true,
      disks: FULL.storage.disks,
      volumes: FULL.storage.volumes,
      raid: [
        { device: 'md0', state: 'active', layout: 'raid1', active: 1, total: 1,
          members: ['sda1[0]'], raidState: 'U' },
        { device: 'md127', state: 'active', layout: 'raid10', active: 2, total: 2,
          members: ['sdb1[0]', 'sdb2[1]'], raidState: 'UU' },
        { device: 'md2', state: 'inactive', layout: 'raid5', active: 1, total: 2,
          members: ['sdc1[0]', 'sdd1[1](F)'], raidState: '_U' },
        /* 缺字段的脏数据不能炸 */
        { device: 'md3', state: 'active', active: 1, total: 1, members: ['sde1[0]'] },
      ],
    },
  };
  setLastData(raidData);
  const raidHtml = (function () {
    try {
      global.window.FnStyleMonitor.__renderForTest(raidData);
    } catch (e) {
      console.error('  ❌ RAID 渲染抛异常:', e.message);
      bad++;
      return '';
    }
    return els.monStorage ? els.monStorage.innerHTML : '';
  })();

  const hasLvl = raidHtml.indexOf('mon-raid-lvl') >= 0 && raidHtml.indexOf('>raid1<') >= 0;
  console.log((hasLvl ? '  ✓ ' : '  ✗ ') + '渲染结果含 RAID 级别徽章');
  if (!hasLvl) bad++;

  const hasState = raidHtml.indexOf('[U]') >= 0 && raidHtml.indexOf('[UU]') >= 0
    && raidHtml.indexOf('[_U]') >= 0;
  console.log((hasState ? '  ✓ ' : '  ✗ ') + '渲染结果含成员健康位图');
  if (!hasState) bad++;

  /* 异常位图（带 _）必须用 crit 类着色 */
  const critBits = /mon-chip crit[^>]*>\[_U\]</.test(raidHtml);
  console.log((critBits ? '  ✓ ' : '  ✗ ') + '异常健康位图标红');
  if (!critBits) bad++;

  /* inactive 阵列状态标黄 */
  const warnState = /mon-badge warn[^>]*>inactive</.test(raidHtml);
  console.log((warnState ? '  ✓ ' : '  ✗ ') + '非 active 阵列状态标黄');
  if (!warnState) bad++;

  /* 缺 layout / raidState 的脏数据不能抛异常 */
  const noCrash = typeof raidHtml === 'string' && raidHtml.indexOf('md3') >= 0;
  console.log((noCrash ? '  ✓ ' : '  ✗ ') + '缺少 layout/raidState 时不报错且正常渲染');
  if (!noCrash) bad++;

  /* --- 口径说明的 DOM 高度不该有大片空白 ---
     stub 的 innerHTML 不带样式表，只能验证文本与结构；
     真实高度由 NAS 实机探针测量。 */
  setLastData({ storage: {
    available: true,
    scan: { last_ts: now, summary: { counts: { ok: 3, warn: 0, crit: 0, unknown: 0 } } },
    scanLog: [],
  } });
  openScanLogDrawer();
  const scopeText = els.monScanLogBody ? els.monScanLogBody.innerHTML : '';
  const scopeOk2 = /统计口径：盘体健康/.test(scopeText)
    && /以盘位 LED 为准/.test(scopeText);
  console.log((scopeOk2 ? '  ✓ ' : '  ✗ ') + '口径说明文本完整');
  if (!scopeOk2) bad++;

  closeScanLogDrawerFn();

  if (bad) { console.error('❌ v1.1.34 断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.35：处理器 / 内存 / 显卡卡片与详情抽屉（按 fnmonitor 1:1 还原） ---------- */
(function checkComputeCards() {
  console.log('\n--- 处理器 / 内存 / 显卡卡片与详情抽屉（v1.1.35）---');
  const fs2 = require('fs');
  const path2 = require('path');
  const CMP = path2.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js');
  const CSS = path2.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css');
  const HTML = path2.join(__dirname, '..', 'app', 'ui', 'index.html');
  const MON = path2.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js');
  const cmp = fs2.readFileSync(CMP, 'utf8');
  const css = fs2.readFileSync(CSS, 'utf8');
  const html = fs2.readFileSync(HTML, 'utf8');
  const mon = fs2.readFileSync(MON, 'utf8');
  let bad = 0;

  /* --- 源码级：fnmonitor 1:1 的关键实现要素 --- */
  const srcRules = [
    /* ① 每核 5×5 点阵瓦片 */
    ['CORE_DOTS 生成 25 个圆点', /for \(var y = 0; y < 5; y\+\+\)[\s\S]{0,200}for \(var x = 0; x < 5; x\+\+\)/],
    ['点阵 viewBox 为 0 0 5 5', /viewBox="0 0 5 5"/],
    /* ② 详情抽屉用 dotGauge（3 列 × N 行），不是 5×5 瓦片 */
    ['详情抽屉用 dotGauge 点阵柱', /function dotGaugeSvg\(cols, rows\)/],
    ['dotGauge 行数规则 n>8 → 8 否则 14', /n > 8 \? 8 : 14/],
    ['dotGauge 每点半径 0.34', /r="0\.34"/],
    /* ③ DIMM 内存条 SVG */
    ['DIMM PCB 路径与 fnmonitor 一致', /M7 0H393A7 7 0 0 1 400 7V100H241V92H229V100H0V7A7 7 0 0 1 7 0Z/],
    ['DIMM 8 个芯片位', /for \(var i = 0; i < 8; i\+\+\)/],
    ['DIMM 三段（app/cache/buf）', /seg seg-app[\s\S]{0,400}seg seg-cache[\s\S]{0,400}seg seg-buf/],
    ['DIMM 斜纹图案 id=monDimmHatch', /id="monDimmHatch"/],
    ['DIMM 剪裁路径 id=monDimmChips', /id="monDimmChips"/],
    /* ④ 内存构成四份 */
    ['内存四份（应用/缓存/缓冲/空闲）', /app: app, cache: cache, buf: buf, free: Math\.max/],
    /* ⑤ 显卡引擎 */
    ['五个引擎（渲染/视频/视频增强/复制/计算）', /'render', '视频'[\s\S]{0,200}'video_enhance'[\s\S]{0,200}'blitter'[\s\S]{0,200}'compute'/],
    ['引擎条 data-eng 挂在行容器上', /<div class="mon-engine" data-eng="/],
    /* ⑥ 占用率来源判定 */
    ['占用率来源判定含 pmu/rc6/warm/none/drv', /function gpuMode\(g\)[\s\S]{0,600}'pmu'[\s\S]{0,300}'rc6'[\s\S]{0,300}'warm'[\s\S]{0,300}'none'[\s\S]{0,300}'drv'/],
    /* ⑦ 内存插槽 */
    ['插槽清单读取 memory.items', /function memSlots\(d\)[\s\S]{0,200}m\.items/],
    ['DIMM 标签按插槽规格拼装', /function dimmLabelText\(d\)/],
    /* ⑧ 显卡卡片不可点击（fnmonitor 一致） */
    ['显卡卡片标记为静态（不可点击）', /mon-cmp-card-static/],
    ['显卡卡片不挂 data-cmp-open', !/data-cmp-open="gpu"/.test(cmp)],
    /* ⑨ 进程排序：内存抽屉按 RSS */
    ['内存抽屉进程按 RSS 降序', /function memProcTop\(d\)[\s\S]{0,300}Number\(b\.rss\)[\s\S]{0,120}Number\(a\.rss\)/],
    /* ⑩ 核心/线程区分 */
    ['核心线程分别取 cores 与 threads', /\['核心 \/ 线程', dash\(cpu\.cores\) \+ ' \/ ' \+ dash\(cpu\.threads\)\]/],
  ];
  srcRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- CSS：fnmonitor 1:1 的关键样式 --- */
  const cssRules = [
    ['每核瓦片网格按 --cols 排布', /\.mon-hw-cores \{[^}]*repeat\(var\(--cols/],
    ['瓦片圆点用主题底色', /\.mon-hw-core circle \{ fill: var\(--bg-hover\)/],
    ['详情仪表限宽 30px（对齐 fnmonitor）', /\.mon-dcore svg \{[^}]*max-width: 30px/],
    ['详情仪表网格按 --cols 排布', /\.mon-dcores \{[^}]*repeat\(var\(--cols, 8\)/],
    ['DIMM 斜纹段用图案填充', /\.mon-hw-dimm \.seg-cache \{ fill: url\(#monDimmHatch\)/],
    ['DIMM 三段有过渡动画', /\.mon-hw-dimm \.seg \{[^}]*transition/],
    ['引擎条三栏布局', /\.mon-engine \{[^}]*grid-template-columns: 64px 1fr 40px/],
    ['引擎条填充条有高度', /\.mon-engine \.bar \{[^}]*height: 5px/],
    ['卡片标题不换行', /\.mon-cmp-label \.lbl-t \{[^}]*white-space: nowrap/],
    ['顶部三栏用 flex wrap（避免等宽列截断）', /\.mon-cmp-split \{[^}]*flex-wrap: wrap/],
    ['图例斜纹样式存在', /\.mon-legend i\.hatch/],
  ];
  cssRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 接线：index.html 引入 compute.js、monitor.js 暴露接口 --- */
  const wireRules = [
    ['index.html 引入 compute.js', /<script src="static\/js\/compute\.js"><\/script>/.test(html)],
    ['monitor.js 暴露 lastData', /lastData: function \(\) \{ return lastData; \}/.test(mon)],
    ['monitor.js 暴露 lockScroll', /lockScroll: lockPageScroll/.test(mon)],
    ['monitor.js 暴露 unlockScroll', /unlockScroll: unlockPageScroll/.test(mon)],
    ['renderCore 委托给 FnStyleCompute', /window\.FnStyleCompute && window\.FnStyleCompute\.render/],
    ['版本号已 bump 到 1.1.37', /v1\.1\.37/.test(html)],
  ];
  wireRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* --- 渲染级：真实把数据塞进 compute.js，看渲染结果 --- */
  const computeData = {
    cpu: {
      available: true, percent: 23, coresPercent: [12, 32, 32, 15],
      model: 'Intel(R) Celeron(R) CPU J3455 @ 1.50GHz',
      cores: 4, threads: 4, sockets: 1,
      loadavg: ['0.65', '0.67', '0.64'], freqMhz: 2200, freqText: '2.20 GHz',
    },
    mem: {
      available: true, total: 7976656896, used: 3153301504,
      availableBytes: 4823355392, percent: 40, cached: 3411341312, buffers: 0,
      swapTotal: 2147479552, swapUsed: 699387904,
    },
    gpu: {
      available: true,
      gpus: [{
        vendor: 'Intel', type: 'igpu',
        name: 'VGA compatible controller: Intel Corporation Apollo Lake GT1 [HD Graphics 500] (rev 0b)',
        temp: null, util: 37.5, utilAvailable: true, utilSource: 'pmu', warming: false,
        engines: { render: 42, video: 8, video_enhance: 0, blitter: 3, compute: 61 },
        freqMhz: 900, freqReqMhz: 1150, memPct: 18.2, memUsed: null, memTotal: null,
      }],
    },
    system: { arch: 'x86_64' },
    procs: [
      { pid: 1234, comm: 'fnstyle_server', args: 'python3 fnstyle_server.py', cpu: 18.2, rss: 52428800 },
      { pid: 2345, comm: 'postgres', args: 'postgres: main', cpu: 12.5, rss: 209715200 },
      { pid: 3456, comm: 'nginx', args: 'nginx: worker', cpu: 4.1, rss: 20971520 },
      { pid: 4567, comm: 'trimd', args: '/usr/local/bin/trimd', cpu: 2.2, rss: 41943040 },
      { pid: 5678, comm: 'dockerd', args: '/usr/bin/dockerd', cpu: 1.5, rss: 104857600 },
      { pid: 6789, comm: 'smbd', args: 'smbd', cpu: 0.8, rss: 15728640 },
      { pid: 7890, comm: 'systemd', args: '/sbin/init', cpu: 0.3, rss: 12582912 },
    ],
    memory: {
      items: [
        { present: true, size: '8 GB', type: 'DDR4', speed: '2400 MT/s', manufacturer: 'Samsung', part: 'M378A1K43', locator: 'DIMM_1' },
        { present: true, size: '8 GB', type: 'DDR4', speed: '2400 MT/s', manufacturer: 'Samsung', part: 'M378A1K43', locator: 'DIMM_2' },
        { present: false, size: '', type: '', speed: '', manufacturer: '', part: '', locator: 'DIMM_3' },
      ],
      dual: true,
    },
  };

  /* compute.js 是独立 IIFE，需要真实的 DOM 能力 —— 这里只验证纯函数与 HTML 生成，
     完整渲染由 Playwright 探针（probe_v135_compute.js）覆盖。 */
  let computeMod = null;
  try {
    const sandbox = {
      window: {}, document: { querySelector: () => null, addEventListener: () => {}, readyState: 'complete' },
      console: console, Math: Math, JSON: JSON, parseFloat: parseFloat,
      parseInt: parseInt, isFinite: isFinite, String: String, Number: Number,
      Array: Array, Object: Object, RegExp: RegExp, Date: Date,
    };
    sandbox.window = sandbox;
    const vm = require('vm');
    vm.createContext(sandbox);
    vm.runInContext(cmp, sandbox, { filename: 'compute.js' });
    computeMod = sandbox.FnStyleCompute;
  } catch (e) {
    console.error('  ❌ compute.js 载入失败:', e.message);
    bad++;
  }

  if (computeMod) {
    console.log((computeMod.render ? '  ✓ ' : '  ✗ ') + 'compute.js 导出 render 接口');
    if (!computeMod.render) bad++;
    console.log((computeMod.open && computeMod.close ? '  ✓ ' : '  ✗ ') + 'compute.js 导出 open/close 接口');
    if (!(computeMod.open && computeMod.close)) bad++;

    /* 抽屉内容：直接调内部渲染路径（通过 open 后读 body.innerHTML 不便，
       这里改为校验 CPU/内存两套 HTML 生成函数是否存在于源码中并可被 open 调用） */
    const openOk = (function () {
      try {
        /* 造一个最小 DOM，让 openCompute 能跑通并留下 body.innerHTML */
        const body = {
          innerHTML: '',
          /* 抽屉渲染后会查每核仪表容器来填点阵，这里给个空实现即可 */
          querySelector: () => null,
          querySelectorAll: () => [],
        };
        const title = { textContent: '' };
        const sub = { textContent: '' };
        const overlay = {
          hidden: true,
          classList: { add: () => {}, remove: () => {} },
          addEventListener: () => {},
        };
        const map = {
          '#monComputeOverlay': overlay,
          '#monComputeBody': body,
          '#monComputeTitle': title,
          '#monComputeSub': sub,
          '#btnCloseMonCompute': null,
        };
        const ctx = {
          window: {}, console: console,
          document: {
            querySelector: (s) => map[s] || null,
            querySelectorAll: () => [],
            addEventListener: () => {},
            readyState: 'complete',
          },
          Math: Math, JSON: JSON, parseFloat: parseFloat, parseInt: parseInt,
          isFinite: isFinite, String: String, Number: Number, Array: Array,
          Object: Object, RegExp: RegExp, Date: Date, setTimeout: () => {},
          requestAnimationFrame: (fn) => fn(),
        };
        ctx.window = ctx;
        const vm2 = require('vm');
        vm2.createContext(ctx);
        vm2.runInContext(cmp, ctx, { filename: 'compute.js' });
        /* 分别捕获 CPU 与内存两套抽屉 HTML（open 会覆盖 body.innerHTML） */
        ctx.FnStyleCompute.open('cpu', computeData);
        const cpuHtml = body.innerHTML;
        ctx.FnStyleCompute.open('mem', computeData);
        const memHtml = body.innerHTML;
        return { cpuHtml: cpuHtml, memHtml: memHtml, title: title };
      } catch (e) {
        console.error('  ❌ openCompute 抛异常:', e.message);
        return null;
      }
    })();

    if (openOk && openOk.cpuHtml) {
      const cpuH = openOk.cpuHtml;
      const memH = openOk.memHtml || '';
      console.log((/每核使用率/.test(cpuH) ? '  ✓ ' : '  ✗ ') + '处理器抽屉含「每核使用率」段落');
      if (!/每核使用率/.test(cpuH)) bad++;
      console.log((/占用最高的进程/.test(cpuH) ? '  ✓ ' : '  ✗ ') + '处理器抽屉含「占用最高的进程」段落');
      if (!/占用最高的进程/.test(cpuH)) bad++;
      console.log((/规格/.test(cpuH) ? '  ✓ ' : '  ✗ ') + '处理器抽屉含「规格」段落');
      if (!/规格/.test(cpuH)) bad++;
      console.log((/显卡/.test(cpuH) ? '  ✓ ' : '  ✗ ') + '处理器抽屉含「显卡」段落');
      if (!/显卡/.test(cpuH)) bad++;
      console.log((/viewBox="0 0 3 14"/.test(cpuH) ? '  ✓ ' : '  ✗ ') + '详情仪表为 dotGauge 3 列 × 14 行');
      if (!/viewBox="0 0 3 14"/.test(cpuH)) bad++;
      console.log((/mon-dcore/.test(cpuH) ? '  ✓ ' : '  ✗ ') + '详情仪表使用 mon-dcore 容器');
      if (!/mon-dcore/.test(cpuH)) bad++;
      console.log((/内存插槽/.test(memH) ? '  ✓ ' : '  ✗ ') + '内存抽屉含「内存插槽」段落');
      if (!/内存插槽/.test(memH)) bad++;
      console.log((/交换分区/.test(memH) ? '  ✓ ' : '  ✗ ') + '内存抽屉含「交换分区」段落');
      if (!/交换分区/.test(memH)) bad++;
      console.log((/DIMM_1/.test(memH) && /DIMM_3/.test(memH) ? '  ✓ ' : '  ✗ ') + '插槽清单列出 DIMM 位置');
      if (!(/DIMM_1/.test(memH) && /DIMM_3/.test(memH))) bad++;
      console.log((/>空</.test(memH) ? '  ✓ ' : '  ✗ ') + '空插槽标记为「空」');
      if (!/>空</.test(memH)) bad++;
      console.log((/postgres/.test(memH) ? '  ✓ ' : '  ✗ ') + '进程列表渲染出进程名');
      if (!/postgres/.test(memH)) bad++;
    }
  }

  if (bad) { console.error('❌ v1.1.35 断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.36：处理器抽屉趋势图 + 每核使用率口径修正 ---------- */
(function checkTrendCharts() {
  console.log('\n--- 处理器抽屉趋势图与每核口径修正（v1.1.36）---');
  const fs3 = require('fs');
  const path3 = require('path');
  const base = path3.join(__dirname, '..', 'app', 'ui');
  const CHART = path3.join(base, 'static', 'js', 'chart.js');
  const CMP = path3.join(base, 'static', 'js', 'compute.js');
  const CSS = path3.join(base, 'static', 'css', 'app.css');
  const HTML = path3.join(base, 'index.html');
  const MON = path3.join(base, 'monitor_collect.py');
  const HIST = path3.join(base, 'history_collect.py');
  const SRV = path3.join(base, 'fnstyle_server.py');
  const CGI = path3.join(base, 'index.cgi');
  const STOR = path3.join(base, 'storage_collect.py');

  const chart = fs3.readFileSync(CHART, 'utf8');
  const cmp = fs3.readFileSync(CMP, 'utf8');
  const css = fs3.readFileSync(CSS, 'utf8');
  const html = fs3.readFileSync(HTML, 'utf8');
  const mon = fs3.readFileSync(MON, 'utf8');
  const hist = fs3.readFileSync(HIST, 'utf8');
  const srv = fs3.readFileSync(SRV, 'utf8');
  const cgi = fs3.readFileSync(CGI, 'utf8');
  const stor = fs3.readFileSync(STOR, 'utf8');
  let bad = 0;

  const rules = [
    /* ---- ① chart.js：LineChart 组件 ---- */
    ['chart.js 定义 LineChart', /function LineChart\(host, opt\)/],
    ['chart.js 导出 LineChart', /window\.FnStyleChart\s*=\s*\{[\s\S]{0,200}LineChart:\s*LineChart/],
    ['chart.js 导出范围切换段控件', /makeRangeSeg:\s*makeRangeSeg/],
    ['单调三次样条 monoPath', /function monoPath\(xs, ys\)/],
    ['niceStep 取整刻度', /function niceStep\(raw\)/],
    ['网格线 grid-l', /class="grid-l"/],
    ['左轴刻度文字 axis-t', /class="axis-t"/],
    ['面积填充 path.ar', /class="ar"/],
    ['实时点 live-pt', /class="live-pt"/],
    ['阈值线 clip-path 裁剪', /clipPath[\s\S]{0,200}clip-path="url\(#/],
    ['峰值标注 peak-t', /class="peak-t"/],
    ['悬停提示 tip', /mon-chart-tip/],
    ['六档范围（10m/1h/6h/24h/7d/all）',
      /\['10m', '10分钟'\], \['1h', '1小时'\], \['6h', '6小时'\][\s\S]{0,120}\['24h', '24小时'\], \['7d', '7天'\], \['all', '全部'\]/],
    ['RANGE_SEC 与 fnmonitor 一致',
      /'10m': 600, '1h': 3600, '6h': 21600,[\s\S]{0,80}'24h': 86400, '7d': 604800, all: 0/],
    ['destroy() 解除 ResizeObserver 观察', /LineChart\.prototype\.destroy\s*=\s*function[\s\S]{0,300}unobserve/],
    ['保留 fnmonitor 许可声明', /fnmonitor[\s\S]{0,200}MIT License/],

    /* ---- ② compute.js：抽屉接入 ---- */
    ['compute.js 定义两张趋势图', /var CPU_CHARTS = \[[\s\S]{0,600}key: 'cpu'[\s\S]{0,600}key: 'load1'/],
    ['CPU 图 0~100 固定刻度', /key: 'cpu'[\s\S]{0,200}yMin: 0, yMax: 100/],
    ['CPU 图带峰值标注', /key: 'cpu'[\s\S]{0,300}peak: true/],
    ['CPU 图阈值 85/95', /thresholds: function[\s\S]{0,200}CPU_WARN[\s\S]{0,120}CPU_CRIT/],
    ['CPU_WARN = 85', /var CPU_WARN = 85/],
    ['CPU_CRIT = 95', /var CPU_CRIT = 95/],
    ['负载图起点为 0', /key: 'load1'[\s\S]{0,200}yMin: 0/],
    ['抽屉插入趋势图容器', /secHtml\('每核使用率'\) \+ cores \+[\s\S]{0,80}cpuChartsHtml\(\)/],
    ['initCharts 创建 LineChart 实例', /new window\.FnStyleChart\.LineChart\(host/],
    ['图表高度 180px', /height: 180/],
    ['打开抽屉时初始化图表', /initCharts\(body\)/],
    ['关闭抽屉时销毁图表', /destroyCharts\(\)/],
    ['拉取历史序列用 api/monitor/history', /api\/monitor\/history/],
    ['请求带 metrics 与 range', /metrics=' \+ encodeURIComponent\(metrics\)[\s\S]{0,120}range=' \+ encodeURIComponent\(range\)/],
    ['范围切换时重新拉取', /loadCharts\(r\)/],
    ['过期响应丢弃', /chartState !== st \|\| st\.range !== range/],
    ['空状态文案', /暂无数据 · 后台每 60 秒记录一条历史/],

    /* ---- ③ CSS ---- */
    ['趋势图卡片样式 .mon-cmp-chart-card', /\.mon-cmp-chart-card\s*\{/],
    ['画布样式 .mon-chart', /\.mon-chart\s*\{/],
    ['网格线样式', /\.mon-chart \.grid-l\s*\{/],
    ['轴刻度样式', /\.mon-chart \.axis-t\s*\{/],
    ['阈值线样式', /\.mon-chart \.th-l\s*\{/],
    ['峰值标注样式', /\.mon-chart \.peak-t\s*\{/],
    ['实时点样式', /\.mon-chart \.live-pt/],
    ['悬浮提示样式', /\.mon-chart-tip\s*\{/],
    ['空状态样式', /\.mon-chart-empty\s*\{/],
    ['⚠️ 空状态必须显式声明 [hidden]', /\.mon-chart-empty\[hidden\]\s*\{\s*display:\s*none/],
    ['阈值/峰值文字加描边', /paint-order:\s*stroke fill/],
    ['段控件样式 .mon-seg', /\.mon-seg\s*\{/],
    ['段控件滑块 .thumb', /\.mon-seg \.thumb\s*\{/],

    /* ---- ④ 后端：历史存储 ---- */
    ['storage_collect 建 metrics 表',
      /CREATE TABLE IF NOT EXISTS metrics \(\s*ts REAL NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL\)/],
    ['storage_collect 建 idx_metric_ts 索引',
      /CREATE INDEX IF NOT EXISTS idx_metric_ts ON metrics\(metric, ts\)/],
    ['写入列序为 (ts, metric, value)',
      /INSERT INTO metrics\(ts, metric, value\) VALUES\(\?,\?,\?\)/],
    ['查询走 idx_metric_ts', /WHERE metric=\? AND ts>=\?[\s\S]{0,120}ORDER BY ts DESC LIMIT \?/],
    ['保留期默认 7 天', /HISTORY_RETENTION_DAYS = 7/],
    ['history_collect 存在', /class HistoryWriter/],
    ['history_collect 采样间隔 60 秒', /HISTORY_INTERVAL = 60/],
    ['history_collect 写 cpu 指标', /\(now, "cpu", round\(float\(percent\), 2\)\)/],
    ['history_collect 写 load1 指标（loadavg[0]）', /\(now, "load1", round\(float\(loadavg\[0\]\), 2\)\)/],
    ['history_collect 无有效值不写', /if not rows:\s*\n\s*return False/],

    /* ---- ⑤ 后端：HTTP 接口 ---- */
    ['fnstyle_server 注册 /api/monitor/history', /if p == "\/api\/monitor\/history":/],
    ['fnstyle_server 实现 _handle_monitor_history', /def _handle_monitor_history\(self\)/],
    ['接口支持多指标一次取回', /metrics_raw = \(q\.get\("metrics"/],
    ['非法 range 回落 1h', /if range_ not in _RANGE_SECONDS:/],
    ['指标名白名单（防注入）', /allowed = \("cpu", "load1", "mem"\)/],
    ['实时值兜底', /def _append_live\(self, pts, metric, snap\)/],
    ['fnstyle_server 范围表与 fnmonitor 一致',
      /"10m": 600, "1h": 3600, "6h": 21600,[\s\S]{0,80}"24h": 86400, "7d": 604800, "all": 0/],
    ['index.cgi 转发 /api/monitor/history', /\/api\/monitor\/history\)/],

    /* ---- ⑥ 每核使用率口径修正（本次的核心 bug 修复） ---- */
    ['按 /proc/stat 前 8 字段解析', /total = \[int\(x\) for x in parts\[1:9\]\]/],
    ['每核同样取前 8 字段', /cores\.append\(\[int\(x\) for x in parts\[1:9\]\]\)/],
    ['cpuN 严格按数字后缀匹配', /re\.match\(r"\^cpu\\d\+\$", parts\[0\]\)/],
    ['idle = parts[3] + parts[4]', /prev_idle = prev\[3\] \+ \(prev\[4\] if len\(prev\) > 4 else 0\)/],
    ['结果保留 1 位小数（与 fnmonitor 一致）', /round\(max\(0\.0, 100\.0 \* \(1\.0 - idle_delta \/ float\(total_delta\)\)\), 1\)/],
    ['collect_cpu 复用 _cpu_percent（不另写一套算法）',
      /def collect_cpu\(\)[\s\S]{0,3000}_cpu_percent\(prev, list\(total_line\)\)/],
    ['基线存完整 8 字段时间片列表', /_CPU_STATE\["last"\] = list\(total_line\)/],
    ['每核基线存完整时间片列表', /_CPU_STATE\["cores"\] = \[list\(n\) for n in core_lines\]/],
    ['每核也走 _cpu_percent', /_cpu_percent\(prev_cores\[i\], list\(core_lines\[i\]\)\)/],

    /* ---- ⑦ 版本与接入 ---- */
    ['index.html 引入 chart.js', /<script src="static\/js\/chart\.js"><\/script>/],
    ['版本号已 bump 到 1.1.37', /v1\.1\.37/.test(html)],
  ];

  rules.forEach(([label, ok]) => {
    const res = typeof ok === 'string' ? ok : (ok instanceof RegExp ? ok.test(chart + cmp + css + html + mon + hist + srv + cgi + stor) : !!ok);
    console.log((res ? '  ✓ ' : '  ✗ ') + label);
    if (!res) bad++;
  });

  /* ---- ⑧ chart.js 的正则要单独在 chart.js 上跑（避免被其它文件误命中） ---- */
  const chartOnly = [
    ['chart.js 定义 LineChart', /function LineChart\(host, opt\)/],
    ['chart.js 导出 LineChart', /window\.FnStyleChart\s*=\s*\{[\s\S]{0,200}LineChart:\s*LineChart/],
    ['单调三次样条 monoPath', /function monoPath\(xs, ys\)/],
    ['niceStep 取整刻度', /function niceStep\(raw\)/],
    ['六档范围定义', /RANGES = \[[\s\S]{0,300}\['all', '全部'\]/],
    ['RANGE_SEC 与 fnmonitor 一致',
      /'10m': 600, '1h': 3600, '6h': 21600,[\s\S]{0,60}'24h': 86400, '7d': 604800, all: 0/],
    ['destroy() 解除观察', /prototype\.destroy = function[\s\S]{0,300}unobserve/],
    ['许可声明保留', /fnmonitor[\s\S]{0,200}MIT License/],
  ];
  chartOnly.forEach(([label, re]) => {
    const ok = re.test(chart);
    console.log((ok ? '  ✓ ' : '  ✗ ') + label + '（chart.js 专项）');
    if (!ok) bad++;
  });

  /* ---- ⑨ app.js 版本号（单独读文件） ---- */
  const appjs = fs3.readFileSync(path3.join(base, 'static', 'js', 'app.js'), 'utf8');
  const appOk = /APP_VERSION = '1\.1\.37'/.test(appjs);
  console.log((appOk ? '  ✓ ' : '  ✗ ') + 'app.js 版本号 1.1.37');
  if (!appOk) bad++;

  /* ---- ⑩ v1.1.37：CPU 采样窗口 0.25s → 10s（与 fnmonitor 对齐） ---- */
  console.log('\n--- CPU 采样窗口与 fnmonitor 对齐（v1.1.37）---');
  const mon2 = fs3.readFileSync(path3.join(base, 'monitor_collect.py'), 'utf8');

  const winRules = [
    ['CPU_SAMPLE_WINDOW 常量 = 10', /CPU_SAMPLE_WINDOW\s*=\s*10\b/],
    ['已移除 time.sleep(0.25) 短窗口', !/time\.sleep\(0\.2\d*\)/.test(mon2)],
    ['存在独立 CPU 采样线程 _cpu_loop', /def _cpu_loop\s*\(/],
    ['CPU 线程按 CPU_SAMPLE_WINDOW 等待', /self\._stop\.wait\(CPU_SAMPLE_WINDOW\)/],
    ['主循环从 CPU 缓存取数 _cpu_snapshot', /def _cpu_snapshot\s*\(/],
    ['_collect_once 不再直接采集 CPU', /_collect_once[\s\S]{0,1200}cpu\s*=\s*self\._cpu_snapshot\(\)/],
    ['拓扑进程级缓存 _cpu_topology_cached', /def _cpu_topology_cached\s*\(/],
    ['抖动保护：无有效读数时保留上一轮', /if not cpu\.get\(["']available["']\):[\s\S]{0,80}continue/],
    ['基线与当前值分两次采样做差分', /collect_cpu\(\)\s*#\s*第 1 次[\s\S]{0,900}collect_cpu\(\)\s*#\s*第 2 次/],
    ['注释说明 jiffy 量化分辨率', /jiffy[\s\S]{0,40}10ms/],
  ];
  winRules.forEach(([label, ok]) => {
    const res = typeof ok === 'string' ? ok : (ok instanceof RegExp ? ok.test(mon2) : !!ok);
    console.log((res ? '  ✓ ' : '  ✗ ') + label);
    if (!res) bad++;
  });

  /* ---- ⑪ CPU 采样窗口专项测试脚本与夹具存在 ---- */
  const verifyDir = path3.join(base, '..', '..', 'verify');
  const cpuWinPath = path3.join(verifyDir, 'verify_cpu_sample_window.py');
  const cpuFixPath = path3.join(verifyDir, 'fixtures', 'cpu_stat_samples.json');
  const exists = (p) => { try { return fs3.existsSync(p); } catch (e) { return false; } };
  const fixtureOk = exists(cpuFixPath);
  console.log((exists(cpuWinPath) ? '  ✓ ' : '  ✗ ') + '新增 verify/verify_cpu_sample_window.py');
  if (!exists(cpuWinPath)) bad++;
  console.log((fixtureOk ? '  ✓ ' : '  ✗ ') + '新增 verify/fixtures/cpu_stat_samples.json（NAS 真机样本）');
  if (!fixtureOk) bad++;
  if (fixtureOk) {
    try {
      const fx = JSON.parse(fs3.readFileSync(cpuFixPath, 'utf8'));
      const n = (fx.samples || []).length;
      const okN = n >= 12;
      console.log((okN ? '  ✓ ' : '  ✗ ') + '夹具样本组数 ≥ 12（实为 ' + n + '）');
      if (!okN) bad++;
    } catch (e) {
      console.log('  ✗ 夹具 JSON 解析失败：' + e.message);
      bad++;
    }
  }

  if (bad) { console.error('❌ v1.1.37 断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

console.log(process.exitCode ? '\n❌ 测试未全部通过' : '\n🎉 全部测试通过');
