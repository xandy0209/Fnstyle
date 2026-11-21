/* FnStyle 存储监控前端渲染单元测试（最小 DOM stub，无 jsdom 依赖）
 * 用法: node /tmp/monitor_render_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js');

/* 当前版本号：直接从 manifest 读取，避免每版都要改断言里的硬编码版本 */
function currentVersion() {
  try {
    const mf = fs.readFileSync(path.join(__dirname, '..', 'manifest'), 'utf8');
    const m = mf.match(/^version\s*=\s*(\S+)/m);
    return m ? m[1] : '';
  } catch (e) { return ''; }
}
const CUR_VER = currentVersion();

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
    /* v1.1.41：板块内容区与趋势图宿主分离，
       renderTemp/renderPower 会 querySelector 宿主里的图表组 */
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    insertAdjacentHTML: function () {},
  };
}
['monStorage', 'monStoreCount', 'monStatus', 'monUpdated', 'monCore', 'monCoreCount',
  /* v1.1.43：温度/能耗改为「总览卡片」形态，内容进容器 #monTempOverview /
     #monTempSide / #monPowerOverview / #monPowerStats / #monPowerDaily，
     旧的 monTemp / monPower 容器已移除（内容移到抽屉） */
  'monTempOverview', 'monTempSide', 'monTempDek',
  'monPowerOverview', 'monPowerStats', 'monPowerDaily',
  /* v1.1.49：#monTempCount 统计标签已按用户要求移除，不再是页面元素 */
  'btnMonRefresh', 'monAuto',
  /* ⚠️ v1.1.56：能耗板块的三个元素按用户要求删除 ——
     #monPowerDek（标题下摘要行「此刻 X W，按电价 …」）、
     #monPowerSource（标题栏「硬件 RAPL」来源徽标）、
     #monPowerProjCost（标题栏「本月预估电费」徽标）。
     电价说明改到右栏「本月预计电费」格内。 */
  /* v1.1.41：趋势图宿主容器（独立于内容区，避免每 3 秒轮询被清空） */
  'monTempCharts', 'monPowerCharts',
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
    /* ⚠️ v1.1.47：软 RAID 卡片已按用户要求删除，
       「软 RAID」标题不再出现在渲染结果里（见 v1.1.40→v1.1.47 区块） */
    ['阵列卡', '阵列卡'],
    ['休眠标记', '休眠'],
    ['故障盘分级', '故障'],
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

  /* --- 滑入动画：.on 是在两帧 rAF 之后加的，必须异步断言 ---
     注意：要打开一个「存在的盘」才会走 showDrawer()；
     「未找到硬盘」分支会提前 return，不会加 .on。 */
  /* 重新打开抽屉再断言：后面的 v1.1.34 块会同步调用 closeDiskDrawer()，
     把 .on 移除掉 —— 必须在 setTimeout 回调里重开（此时同步代码已跑完）。 */
  setTimeout(function () {
    openDiskDrawer('sda');
    setTimeout(function () {
      const onOk = els.monDrawerOverlay.classList.contains('on') === true;
      console.log((onOk ? '  ✓ ' : '  ✗ ') + 'overlay 加上 .on（滑入动画）');
      if (!onOk) process.exitCode = 1;
    }, 20);
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

  /* --- 源码级 / 渲染级：软 RAID 卡片已删除（v1.1.47）---
     用户要求「删除软RAID卡片显示」。
     渲染级用带 raid 数据的 fixture 验证：不渲染卡片、也不抛异常。 */
  const raidDelRules = [
    ['monitor.js 不再存在软 RAID 渲染块（st.raid 分支）',
      /if \(st\.raid && st\.raid\.length\)/.test(src) === false],
    ['monitor.js 不再引用 mon-raid 系列类名',
      /mon-raid/.test(src) === false],
    ['CSS 不再存在 .mon-raid 系列规则定义',
      /^\s*\.mon-raid[^\s{]*\s*\{/m.test(css) === false],
    ['CSS 不再存在 RAID 级别徽章规则',
      /^\s*\.mon-chip\.mon-raid-lvl \{/m.test(css) === false],
  ];
  raidDelRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 渲染级：塞入 4 组 RAID 脏数据 → 卡片不出现、渲染不炸 */
  const raidData = {
    storage: {
      available: true,
      disks: FULL.storage.disks,
      volumes: FULL.storage.volumes,
      raid: [
        { device: 'md0', state: 'active', layout: 'raid1', active: 1, total: 1,
          members: ['sda1[0]'], raidState: 'U' },
        { device: 'md2', state: 'inactive', layout: 'raid5', active: 1, total: 2,
          members: ['sdc1[0]', 'sdd1[1](F)'], raidState: '_U' },
        { device: 'md3', state: 'active', active: 1, total: 1, members: ['sde1[0]'] },
      ],
    },
  };
  setLastData(raidData);
  const raidHtml = (function () {
    try {
      global.window.FnStyleMonitor.__renderForTest(raidData);
    } catch (e) {
      console.error('  ❌ 带 RAID 数据渲染抛异常:', e.message);
      bad++;
      return '';
    }
    return els.monStorage ? els.monStorage.innerHTML : '';
  })();
  const noRaidCard = typeof raidHtml === 'string'
    && raidHtml.indexOf('软 RAID') < 0 && raidHtml.indexOf('mon-raid') < 0;
  console.log((noRaidCard ? '  ✓ ' : '  ✗ ') + '带 RAID 数据渲染时不出现软 RAID 卡片');
  if (!noRaidCard) bad++;
  const noCrash = typeof raidHtml === 'string' && raidHtml.length > 0;
  console.log((noCrash ? '  ✓ ' : '  ✗ ') + '带 RAID 脏数据渲染不报错');
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
    ['版本号与 manifest 一致（v' + CUR_VER + '）',
      CUR_VER !== '' && html.indexOf('v' + CUR_VER) >= 0],
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
    ['抽屉插入趋势图容器', /secHtml\('每核使用率'\) \+ cores \+[\s\S]{0,80}chartsHtml\('cpu'\)/],
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
    ['指标名白名单（防注入）',
      /allowed = \("cpu", "load1", "mem", "temp", "temp_mb", "power"\)/],
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
    ['版本号与 manifest 一致（v' + CUR_VER + '）',
      CUR_VER !== '' && html.indexOf('v' + CUR_VER) >= 0],
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
  const appOk = CUR_VER !== '' && appjs.indexOf("APP_VERSION = '" + CUR_VER + "'") >= 0;
  console.log((appOk ? '  ✓ ' : '  ✗ ') + 'app.js 版本号 ' + CUR_VER);
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

  /* ---- ⑫ v1.1.38/1.1.39：三卡片铺满 + 段控件定位 + 内存趋势图 ---- */
  console.log('\n--- 三卡片铺满 / 段控件定位 / 内存趋势图（v1.1.38~1.1.39）---');
  const cmp3 = fs3.readFileSync(path3.join(base, 'static', 'js', 'compute.js'), 'utf8');
  const css3 = fs3.readFileSync(path3.join(base, 'static', 'css', 'app.css'), 'utf8');
  const hist3 = fs3.readFileSync(path3.join(base, 'history_collect.py'), 'utf8');

  const v38 = [
    /* --- ① 三卡片铺满整行 --- */
    ['mon-grid-3 固定 3 列（不再 auto-fill）',
      /\.mon-grid-3\s*\{\s*grid-template-columns:\s*repeat\(3,\s*1fr\)/.test(css3)],
    ['mon-grid-3 已无 auto-fill',
      !/\.mon-grid-3\s*\{[^}]*auto-fill/.test(css3)],
    ['mon-grid-3 仅用于处理器板块',
      /class="mon-grid mon-grid-3" id="monCore"/.test(html)],

    /* --- ② 段控件位置（v1.1.39：移到图表组顶部居中） --- */
    ['chartsHtml 通用图表组函数', /function chartsHtml\(groupKey\)/.test(cmp3)],
    ['段控件在图表组顶部（卡片之前）',
      /'<div class="mon-cmp-chart-seg" data-cseg><\/div>' \+ cards/.test(cmp3)],
    ['段控件不再挂在任何卡片内部',
      !/mon-cmp-chart-card[\s\S]{0,400}?><div class="mon-cmp-chart-seg"/.test(cmp3)
        && !/<div class="mon-cmp-chart-card">[\s\S]{0,300}mon-cmp-chart-seg/.test(cmp3)],
    ['段控件样式为居中（justify-content:center）',
      /\.mon-cmp-chart-seg\s*\{[^}]*justify-content:\s*center/.test(css3)],
    ['段控件下留白交给网格 gap（margin 简写底部为 0）',
      /\.mon-cmp-chart-seg\s*\{[^}]*margin:\s*\.85rem 0 0/.test(css3)],
    ['段控件上方留白用 margin-top 下移（v1.1.40）',
      /\.mon-cmp-chart-seg\s*\{[^}]*margin:\s*\.85rem 0 0/.test(css3)],
    ['段控件上下留白对称（上下各 .85rem）',
      /\.mon-cmp-chart-seg\s*\{[^}]*margin:\s*\.85rem 0 0/.test(css3)
        && /\.mon-cmp-charts\s*\{[^}]*gap:\s*\.85rem/.test(css3)],
    ['图表组不再外挂 margin-top（改由段控件 margin-top 承担）',
      /\.mon-cmp-charts\s*\{[^}]*margin-top:\s*0/.test(css3)],
    ['段控件不再有卡片内部分隔线（border-top）',
      !/\.mon-cmp-chart-seg\s*\{[^}]*border-top/.test(css3)],
    ['段控件收缩到内容宽度（fit-content）',
      /\.mon-cmp-chart-seg \.mon-seg\s*\{\s*width:\s*fit-content/.test(css3)],
    ['已移除 :has() 卡片底部留白规则',
      !/\.mon-cmp-chart-card:has\(/.test(css3)],

    /* --- ③ 内存抽屉补「内存使用率」趋势图 --- */
    ['定义 MEM_CHARTS 内存使用率图表',
      /var MEM_CHARTS = \[[\s\S]{0,200}title: '内存使用率'/.test(cmp3)],
    ['内存图表指标键为 mem', /key: 'mem'/.test(cmp3)],
    ['内存图表组登记在 CHART_GROUPS',
      /CHART_GROUPS = \{[\s\S]{0,200}mem:\s*\{\s*defs:\s*MEM_CHARTS,\s*metrics:\s*'mem'/.test(cmp3)],
    ['内存抽屉插入 chartsHtml(\'mem\')',
      /chartsHtml\('mem'\)/.test(cmp3)],
    ['内存抽屉图表位置在堆叠条之后、进程列表之前',
      /mon-cmp-note[\s\S]{0,300}chartsHtml\('mem'\)[\s\S]{0,200}内存占用较多的进程/.test(cmp3)],
    ['内存抽屉也初始化图表（不再只限 cpu）',
      /requestAnimationFrame\(function \(\) \{ initCharts\(body\); \}\);/.test(cmp3)],
    ['drawCharts 按当前图表组定义绘制',
      /chartState\.defs\.forEach/.test(cmp3)],
    ['loadCharts 按当前图表组指标拉取',
      /fetchHistory\(st\.metrics, range\)/.test(cmp3)],
    ['后端写入 mem 历史指标',
      /rows\.append\(\(now,\s*"mem"/.test(hist3)],
    ['后端 mem 指标取自 mem.percent',
      /mem_percent = mem\.get\("percent"\)/.test(hist3)],

    /* --- ④ 移除处理器板块标题栏的「N 项可用」 --- */
    ['index.html 已删除 monCoreCount 元素',
      !/id="monCoreCount"/.test(html)],
    ['index.html 处理器板块标题栏无 theme-count',
      !/处理器 \/ 内存 \/ 显卡<\/h2>\s*<div class="spacer"><\/div>\s*<span class="theme-count"/.test(html)],
    ['monitor.js 已删除计数更新逻辑',
      !/monCoreCount/.test(mon)],
    ['renderCore 不再统计可用项数',
      !/if \(d\.cpu && d\.cpu\.available\) n\+\+/.test(mon)],
  ];
  v38.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) { console.error('❌ v1.1.40 断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* ---------- v1.1.40→v1.1.47：软 RAID 卡片已按用户要求删除 ----------
   v1.1.40 曾把软 RAID 改成通栏区块；v1.1.47 用户要求「删除软RAID卡片显示」，
   渲染块与整套 CSS（.mon-raid*）已物理删除，本块改为锁定删除后的形态：
   ① 源码不再含渲染块与类名引用；② CSS 规则定义清零；
   ③ 带 RAID 数据渲染不出现卡片、不报错（脏数据容错保留）。 */
(function checkRaidRemoved() {
  console.log('\n--- 软 RAID 卡片删除（v1.1.40 历史断言已随 v1.1.47 重写）---');
  const mon = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const css3 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  let bad = 0;

  const rules = [
    ['源码不再含 <div class="mon-raid"> 渲染', /<div class="mon-raid">/.test(mon) === false],
    ['源码不再含 mon-raid-card / mon-raid-grid / mon-raid-member',
      /mon-raid-card|mon-raid-grid|mon-raid-member/.test(mon) === false],
    ['源码不再含 raidCols 列数逻辑', /raidCols/.test(mon) === false],
    ['CSS 不再存在任何 .mon-raid* 规则定义',
      /^\s*\.mon-raid[^\s{]*\s*\{/m.test(css3) === false],
    ['CSS 保留删除说明注释（防误判为漏改）', /软 RAID 区块的整套 CSS/.test(css3)],
  ];
  rules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* 渲染级：4 组阵列数据 → 0 张卡片、不抛异常 */
  const raidData = {
    storage: {
      available: true,
      disks: FULL.storage.disks,
      volumes: FULL.storage.volumes,
      raid: [
        { device: 'md0', state: 'active', layout: 'raid1', active: 1, total: 1,
          members: ['sda1[0]'], raidState: 'U' },
        { device: 'md127', state: 'active', layout: 'raid1', active: 1, total: 1,
          members: ['sdb1[0]'], raidState: 'U' },
        { device: 'md1', state: 'active', layout: 'raid1', active: 1, total: 1,
          members: ['sdc3[0]'], raidState: 'U' },
        { device: 'md2', state: 'inactive', layout: 'raid5', active: 1, total: 2,
          members: ['sdd1[0]', 'sde1[1](F)'], raidState: '_U' },
      ],
    },
  };
  setLastData(raidData);
  let html = '';
  try {
    global.window.FnStyleMonitor.__renderForTest(raidData);
    html = els.monStorage ? els.monStorage.innerHTML : '';
  } catch (e) {
    console.error('  ❌ 带 RAID 数据渲染抛异常:', e.message);
    bad++;
  }
  const cardCount = (html.match(/class="mon-raid-card"/g) || []).length;
  console.log((cardCount === 0 ? '  ✓ ' : '  ✗ ') + '带 4 组阵列数据渲染出 0 张 RAID 卡片（实为 ' + cardCount + '）');
  if (cardCount !== 0) bad++;

  const raidTextGone = html.indexOf('软 RAID') < 0;
  console.log((raidTextGone ? '  ✓ ' : '  ✗ ') + '渲染结果不再含「软 RAID」文字');
  if (!raidTextGone) bad++;

  if (bad) { console.error('❌ v1.1.40(v1.1.47 重写) 软 RAID 删除断言失败 ' + bad + ' 项'); process.exitCode = 1; }
})();

/* =====================================================================
   v1.1.41：温度和散热 / 能耗 板块对齐 fnmonitor
   ---------------------------------------------------------------------
   fnmonitor 的 SHEETS.temp 结构：
     hero（CPU 大数字 + 阈值）→ 4 格统计 → 温度计条形图 → 趋势图
     → 分节：风扇 / 电压 / 全部传感器
   fnmonitor 的 SHEETS.power 结构：
     hero（实时功耗 + 口径）→ 功耗堆叠条 + 图例 → 部件明细 → 趋势图
     → 分节：用电与电费 / 估算参数
   ===================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- 温度和散热 / 能耗 对齐 fnmonitor（v1.1.41）---');

  const monSrc = fs.readFileSync(SRC, 'utf8');
  const compSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'static', 'js', 'compute.js'), 'utf8');
  const cssSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'static', 'css', 'app.css'), 'utf8');
  const srvSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'fnstyle_server.py'), 'utf8');
  const monPy = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'monitor_collect.py'), 'utf8');
  const histPy = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'history_collect.py'), 'utf8');
  const htmlSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'index.html'), 'utf8');

  /* ---------- 源码级：温度板块 ---------- */
  const srcRules = [
    /* v1.1.43：renderTemp 改为纯函数 tempSheetHtml(d)（返回 HTML 字符串，
       由抽屉层注入）；renderTempOverview 负责总览卡片。 */
    ['tempSheetHtml 已重写（大数字 + 四格 + 温度计 + 分节）',
      /function tempSheetHtml\(d\)/.test(monSrc)],
    ['温度 hero 区使用 mon-hero',
      /mon-hero/.test(monSrc)],
    ['四格统计使用 mon-cells',
      /mon-cells/.test(monSrc)],
    ['温度计条形图使用 mon-thermo',
      /mon-thermo/.test(monSrc)],
    ['阈值刻度使用 mon-thermo-mark',
      /mon-thermo-mark/.test(monSrc)],
    ['刻度尺使用 mon-thermo-scale',
      /mon-thermo-scale/.test(monSrc)],
    ['分节标题统一使用 mon-cmp-sec（与处理器抽屉一致）',
      /mon-cmp-sec/.test(monSrc) && !/mon-sub-sec/.test(monSrc)],
    ['风扇分节存在', /<span>风扇<\/span>/.test(monSrc)],
    ['电压分节存在', /<span>电压<\/span>/.test(monSrc)],
    ['全部传感器分节存在', /<span>全部传感器<\/span>/.test(monSrc)],
    ['传感器类型标签使用 mon-sensor-tag',
      /mon-sensor-tag/.test(monSrc)],
    ['温度条刻度范围 20~100℃（对齐 fnmonitor T_MIN/T_MAX）',
      /T_MIN = 20, T_MAX = 100/.test(monSrc)],
    ['传感器类型映射含 cpu/pch/board/acpi/disk/gpu',
      /cpu: 'CPU', pch: '芯片组', board: '主板'/.test(monSrc) &&
      /acpi: 'ACPI', disk: '硬盘', gpu: 'GPU'/.test(monSrc)],
    ['四格统计：CPU / 主板 / 显卡 / 全部测点最高',
      /<div class="mon-cell-eyebrow">全部测点最高<\/div>/.test(monSrc) &&
      /<div class="mon-cell-eyebrow">显卡<\/div>/.test(monSrc)],
    ['GPU 温度从 gpu.gpus 数组取（真机结构是对象不是数组）',
      /d\.gpu\.gpus/.test(monSrc)],
  ];
  srcRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 源码级：能耗板块 ---------- */
  const pwrRules = [
    ['powerSheetHtml 已重写（hero + 堆叠条 + 明细 + 电费）',
      /function powerSheetHtml\(d\)/.test(monSrc)],
    ['功耗堆叠条使用 mon-stack',
      /mon-stack/.test(monSrc)],
    ['图例使用 mon-legend',
      /mon-legend/.test(monSrc)],
    ['部件明细使用 mon-dkv（与处理器抽屉同一套键值表）',
      /mon-dkv/.test(monSrc)],
    ['部件分解函数 powerParts 存在',
      /function powerParts\(p\)/.test(monSrc)],
    ['部件含 CPU 封装 / 内存 / 硬盘 / 主板与其他',
      /'CPU 封装'/.test(monSrc) && /'内存'/.test(monSrc) &&
      /'主板与其他'/.test(monSrc)],
    ['用电与电费分节存在', /<span>用电与电费<\/span>/.test(monSrc)],
    ['估算参数分节存在', /<span>估算参数<\/span>/.test(monSrc)],
    ['用电统计走 /api/power/stats',
      /\/api\/power\/stats/.test(monSrc)],
    ['能耗统计有今日 / 本月 / 开机以来三行',
      /<span class="k">今日<\/span>/.test(monSrc) &&
      /<span class="k">本月<\/span>/.test(monSrc) &&
      /开机以来/.test(monSrc)],
    ['口径区分 RAPL 实测与 TDP 估算',
      /RAPL 实测/.test(monSrc) && /TDP 估算/.test(monSrc)],
    ['堆叠条按部件占比归一 flex-grow',
      /flexGrow/.test(monSrc)],
  ];
  pwrRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 源码级：趋势图 ---------- */
  const chartRules = [
    ['定义 TEMP_CHARTS 温度趋势图组',
      /var TEMP_CHARTS = \[/.test(compSrc)],
    ['定义 POWER_CHARTS 能耗趋势图组',
      /var POWER_CHARTS = \[/.test(compSrc)],
    ['温度趋势图把 CPU 与主板合并为一张（fnmonitor TREND_DEFS[3]）',
      /title: '温度'/.test(compSrc) &&
      /m: 'temp', label: 'CPU'/.test(compSrc) &&
      /m: 'temp_mb', label: '主板'/.test(compSrc) &&
      !/title: 'CPU 温度'/.test(compSrc) && !/title: '主板温度'/.test(compSrc)],
    ['能耗趋势图含整机功耗与累计用电两张',
      /title: '整机功耗'/.test(compSrc) &&
      /title: '累计用电（所选范围）'/.test(compSrc)],
    ['累计用电用梯形积分变换 cumKwh',
      /function cumKwh\(pts\)/.test(compSrc) && /tf: cumKwh/.test(compSrc)],
    ['chartGroups 登记 temp 与 power',
      /temp:\s*\{\s*defs: TEMP_CHARTS/.test(compSrc) &&
      /power:\s*\{\s*defs: POWER_CHARTS/.test(compSrc)],
    ['板块图表实例按板块分组保存',
      /var panelChartStates = \{\}/.test(compSrc)],
    ['提供 refreshPanelCharts 供轮询刷新',
      /function refreshPanelCharts\(groupKey\)/.test(compSrc)],
    ['提供 destroyPanelCharts 按板块销毁',
      /function destroyPanelCharts\(groupKey\)/.test(compSrc)],
  ];
  chartRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 源码级：趋势图宿主与轮询不冲突 ---------- */
  const hostRules = [
    ['index.html 有独立的温度趋势图宿主',
      /id="monTempCharts"/.test(htmlSrc)],
    ['index.html 有独立的能耗趋势图宿主',
      /id="monPowerCharts"/.test(htmlSrc)],
    ['温度板块挂到独立宿主而非内容区',
      /host: '#monTempCharts'/.test(monSrc)],
    ['能耗板块挂到独立宿主而非内容区',
      /host: '#monPowerCharts'/.test(monSrc)],
    ['宿主已有图表时不重复创建实例',
      /host\.querySelector\('\[data-pcharts="' \+ g\.key \+ '"\]'\)/.test(monSrc)],
  ];
  hostRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- CSS 级 ---------- */
  const cssRules = [
    ['.mon-hero 通栏大数字区样式存在',
      /\.mon-hero \{[^}]*grid-column:\s*1 \/ -1/.test(cssSrc)],
    ['.mon-cells 四格统计为一行 4 列（fnmonitor .temp-cells repeat(4,minmax(0,1fr))）',
      /\.mon-cells \{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/.test(cssSrc)],
    ['.mon-thermo 温度计容器样式存在',
      /\.mon-thermo \{/.test(cssSrc)],
    ['.mon-thermo-row 三列栅格（标签/条/值）',
      /\.mon-thermo-row \{[^}]*grid-template-columns:\s*112px 1fr 64px/.test(cssSrc)],
    ['.mon-thermo-fill 条形填充样式存在',
      /\.mon-thermo-fill \{/.test(cssSrc)],
    ['阈值刻度线样式存在',
      /\.mon-thermo-mark \{/.test(cssSrc)],
    ['刻度尺样式存在',
      /\.mon-thermo-scale \{/.test(cssSrc)],
    ['.mon-stack 功耗堆叠条样式存在',
      /\.mon-stack \{/.test(cssSrc)],
    ['.mon-legend 图例样式存在',
      /\.mon-legend \{/.test(cssSrc)],
    ['.mon-cmp-sec 分节标题样式存在（全站唯一）',
      /\.mon-cmp-sec \{/.test(cssSrc) && !/\.mon-sub-sec \{/.test(cssSrc)],
    ['.mon-dkv 键值明细样式存在（全站唯一）',
      /\.mon-dkv \{/.test(cssSrc) && !/^\s*\.mon-kv\s*\{/m.test(cssSrc)],
    ['.mon-info 说明条样式存在',
      /\.mon-info \{/.test(cssSrc)],
    ['.mon-sensor-tag 传感器类型标签样式存在',
      /\.mon-sensor-tag \{/.test(cssSrc)],
    ['趋势图宿主容器样式存在',
      /\.mon-panel-chart-host \{/.test(cssSrc)],
    ['窄屏时四格统计降为两列（fnmonitor 640px .temp-cells repeat(2,minmax(0,1fr))）',
      /@media \(max-width: 760px\)[\s\S]*?\.mon-cells \{[^}]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(cssSrc)],
  ];
  cssRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 后端级 ---------- */
  const beRules = [
    ['后端白名单含 temp / temp_mb / power',
      /allowed = \("cpu", "load1", "mem", "temp", "temp_mb", "power"\)/.test(srvSrc)],
    ['_live_value 映射 temp（CPU 温度）',
      /metric == "temp"/.test(srvSrc)],
    ['_live_value 映射 temp_mb（主板温度）',
      /metric == "temp_mb"/.test(srvSrc)],
    ['_live_value 映射 power（整机功耗）',
      /metric == "power"/.test(srvSrc)],
    ['提供 /api/power/stats 接口',
      /\/api\/power\/stats/.test(srvSrc)],
    ['用电统计按历史 power 指标积分',
      /def energy_stats\(/.test(histPy) && /3\.6e6/.test(histPy)],
    ['历史库写入 temp 指标',
      /"temp"/.test(histPy)],
    ['历史库写入 temp_mb 指标',
      /"temp_mb"/.test(histPy)],
    ['历史库写入 power 指标',
      /"power"/.test(histPy)],
    ['采集端带电压传感器',
      /volts/.test(monPy)],
    ['采集端识别传感器类型',
      /def _sensor_type\(/.test(monPy)],
    ['采集端返回主板温度 mbTemp',
      /mbTemp/.test(monPy)],
    ['采集端返回估算参数 estimation_params',
      /estimation_params/.test(monPy)],
    ['采集端返回开机时长 uptimeSeconds',
      /uptimeSeconds/.test(monPy)],
  ];
  beRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 渲染级 ---------- */
  const TEMP_POWER_DATA = {
    success: true, available: true, ts: now, uptime: '3 天 4 小时',
    cpu: { available: true, percent: 20, cores: 4, coresPercent: [1, 2, 3, 4] },
    mem: { available: true, percent: 50 },
    gpu: { available: true, gpus: [{ name: 'RTX 3060', temp: 52 }] },
    temp: {
      available: true, source: 'thermal+hwmon', cpuTemp: 47, mbTemp: 33,
      temps: [
        { name: 'x86_pkg_temp', chip: 'thermal', value: 47, max: null, crit: null, type: 'cpu' },
        { name: 'acpitz', chip: 'thermal', value: 39, max: null, crit: null, type: 'acpi' },
        { name: 'coretemp Package id 0', chip: 'coretemp', value: 47, max: 80, crit: 100, type: 'cpu' },
        { name: 'nvme composite', chip: 'nvme', value: 38, max: 70, crit: 80, type: 'disk' },
        { name: 'motherboard', chip: 'nct6798', value: 33, max: null, crit: null, type: 'board' },
      ],
      fans: [{ name: 'cpu fan', rpm: 1420 }],
      volts: [{ name: 'Vcore', value: 1.28 }, { name: '+12V', value: 12.06 }],
      /* v1.1.49：硬盘带 vol/mounts（后端 storage_collect 下发），
         两块盘同属存储空间 vol1 → 标签去歧义为「存储空间 vol1 · sda/sdb」 */
      disks: [
        { name: 'sda', temp: 36, vol: 1, mounts: ['/vol1'] },
        { name: 'sdb', temp: 41, vol: 1, mounts: ['/vol1'] },
      ],
    },
    power: {
      ok: true, source: 'rapl', total: 68.4,
      package: 42.1, core: 28.6, uncore: 6.2, dram: 7.3,
      base: 8, disksW: 13, disksActive: 3, nics: 1,
      estimation_params: {
        tdp_w: 65, disk_typical_w: 8, nic_fixed_w: 5, base_w: 8,
      },
    },
    storage: FULL.storage,
  };

  let tempHtml = '', powerHtml = '';
  /* v1.1.43：总览卡片进新容器；抽屉内容用导出接口单独取 */
  let tempOverviewHtml = '', tempSideHtml = '';
  let powerOverviewHtml = '', powerStatsHtml = '';
  try {
    global.window.FnStyleMonitor.__renderForTest(TEMP_POWER_DATA);
    tempOverviewHtml = els.monTempOverview ? els.monTempOverview.innerHTML : '';
    tempSideHtml = els.monTempSide ? els.monTempSide.innerHTML : '';
    powerOverviewHtml = els.monPowerOverview ? els.monPowerOverview.innerHTML : '';
    powerStatsHtml = els.monPowerStats ? els.monPowerStats.innerHTML : '';
    /* v1.1.56：能耗摘要行 #monPowerDek 已删除，不再取其内容 */
    /* 抽屉内容（v1.1.42 成果，v1.1.43 保持）*/
    tempHtml = global.window.FnStyleMonitor.tempSheetHtml
      ? global.window.FnStyleMonitor.tempSheetHtml(TEMP_POWER_DATA) : '';
    powerHtml = global.window.FnStyleMonitor.powerSheetHtml
      ? global.window.FnStyleMonitor.powerSheetHtml(TEMP_POWER_DATA) : '';
  } catch (e) {
    console.error('  ❌ 温度/能耗渲染抛异常:', e.message);
    bad++;
  }

  const renderRules = [
    ['温度 hero 显示 CPU 温度大数字（带色阶 class）',
      /mon-hero-num"><b class="lv-/.test(tempHtml) && /47\.0<\/b>/.test(tempHtml)],
    ['温度 hero 阈值单行显示（fnmonitor 单条字符串，不换行）',
      /CPU 60 \/ 75℃ · 硬盘 50 \/ 58℃/.test(tempHtml) &&
      !/<br>/.test(tempHtml.split('mon-hero-side')[1].split('</div>')[0])],
    ['温度 hero 显示阈值（CPU / 硬盘）',
      /CPU 60 \/ 75℃/.test(tempHtml) && /硬盘 50 \/ 58℃/.test(tempHtml)],
    ['四格统计齐全（CPU / 主板 / 显卡 / 全部测点最高）',
      /mon-cells/.test(tempHtml) &&
      /<div class="mon-cell-eyebrow">CPU<\/div>/.test(tempHtml) &&
      /<div class="mon-cell-eyebrow">主板<\/div>/.test(tempHtml) &&
      /<div class="mon-cell-eyebrow">显卡<\/div>/.test(tempHtml) &&
      /<div class="mon-cell-eyebrow">全部测点最高<\/div>/.test(tempHtml)],
    ['显卡温度取自 gpu.gpus（52℃）',
      /52\.0 ℃/.test(tempHtml)],
    ['温度计条形图渲染出多行',
      (tempHtml.match(/mon-thermo-row/g) || []).length >= 5],
    ['温度计带阈值刻度线',
      /mon-thermo-mark/.test(tempHtml)],
    ['温度计刻度尺 20°~100°',
      /20°/.test(tempHtml) && /100°/.test(tempHtml)],
    ['风扇分节渲染（kv 键值表，对齐 fnmonitor kvHTML）',
      /<span>风扇<\/span>/.test(tempHtml) &&
      /* v1.1.48：风扇名经 fanLabel 映射，'cpu fan' → 'CPU 风扇' */
      /class="mon-dkv-row"><span class="k">CPU 风扇<\/span><span class="v">1420 RPM<\/span>/.test(tempHtml)],
    ['电压分节渲染（kv 键值表，对齐 fnmonitor kvHTML）',
      /<span>电压<\/span>/.test(tempHtml) &&
      /class="mon-dkv-row"><span class="k">Vcore<\/span><span class="v">1\.28 V<\/span>/.test(tempHtml)],
    ['全部传感器分节渲染',
      /<span>全部传感器<\/span>/.test(tempHtml)],
    ['传感器带类型标签',
      /mon-sensor-tag">CPU</.test(tempHtml)],
    ['能耗 hero 显示实时功耗',
      /mon-hero-num"><b>68\.4<\/b><span>W<\/span>/.test(powerHtml)],
    ['能耗 hero 显示口径',
      /RAPL 实测 \+ 底座 \/ 硬盘估算/.test(powerHtml)],
    ['功耗堆叠条渲染出分段',
      /mon-stack/.test(powerHtml) &&
      (powerHtml.match(/flex-grow:/g) || []).length >= 4],
    ['堆叠条含硬盘的斜纹样式',
      /mono-hatch/.test(powerHtml)],
    ['图例渲染（含部件与瓦数）',
      /mon-legend/.test(powerHtml) &&
      /CPU 封装/.test(powerHtml) && /42\.1 W/.test(powerHtml)],
    ['部件明细渲染（Package / Core / DRAM / 硬盘 / 网卡）',
      /CPU 封装 Package/.test(powerHtml) &&
      /核心 Core/.test(powerHtml) &&
      /内存 DRAM/.test(powerHtml) &&
      /活动硬盘（3 块）/.test(powerHtml) &&
      /网卡/.test(powerHtml)],
    ['用电与电费分节渲染',
      /<span>用电与电费<\/span>/.test(powerHtml)],
    ['估算参数分节渲染',
      /<span>估算参数<\/span>/.test(powerHtml)],
    ['估算参数含 TDP / 单盘 / 网卡 / 底座',
      /CPU TDP（无 RAPL 时）/.test(powerHtml) &&
      /单盘典型功耗/.test(powerHtml) &&
      /网卡固定功耗/.test(powerHtml)],
  ];
  renderRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.41 温度/能耗对齐断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* =====================================================================
   v1.1.42：真正的 1:1 还原（逐项对照 fnmonitor 真实 CSS / DOM）
   ---------------------------------------------------------------------
   v1.1.41 只是「结构类似」，在布局、字号、容器形态上有多处实质偏差。
   v1.1.42 从 fnmonitor 源码（/tmp/fnm_ref/app/www/index.html）扒出权威值，
   逐项对齐：
     .sheet-hero   → .mon-hero       无边框无背景 / padding 8px 0 0 / 64px / 22px
     .stat-cells   → .mon-cells      2 列 2×2 / padding 20px 24px / 数字 32px
     .card.pad     → .mon-thermo     容器内缩 24px / 行 padding 10px / 条高 6px
     .volts/.volt  → .mon-volts      2 列网格 / card-sunk 底 + inset 1px 描边 / 20px
     .fan          → .mon-fan        40px 圆形图标 + 名称 + 24px RPM
     .stack        → .mon-stack      gap 2px / 高 12px / min-width 2px / 首尾圆角
     .legend       → .mon-legend     gap 6px 18px / 色块 8px
     .kv           → .mon-dkv        与处理器抽屉同一套键值表
     .sheet-sec    → .mon-cmp-sec    3px 竖条（全站统一）
     .sheet-seg    → 段控件          板块级右对齐（抽屉级仍居中）
   ===================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- 温度和散热 / 能耗 真正 1:1 还原（v1.1.42）---');

  const monSrc = fs.readFileSync(SRC, 'utf8');
  const compSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'static', 'js', 'compute.js'), 'utf8');
  const cssSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'static', 'css', 'app.css'), 'utf8');

  /* ---------- CSS 级：hero（对齐 fnmonitor .sheet-hero）---------- */
  const heroRules = [
    ['.mon-hero 无边框（fnmonitor .sheet-hero 不画盒子）',
      /\.mon-hero \{[^}]*border[^}]*\}/.test(cssSrc) === false &&
      /\.mon-hero \{[^}]*\}\s*\n?/.test(cssSrc)],
    ['.mon-hero 用 padding .5rem 0 0（= 8px 0 0）',
      /\.mon-hero \{[^}]*padding:\s*\.5rem 0 0/.test(cssSrc)],
    ['.mon-hero 底部对齐 + 两端对齐（align-items:flex-end）',
      /\.mon-hero \{[^}]*align-items:\s*flex-end[^}]*justify-content:\s*space-between/.test(cssSrc)],
    ['.mon-hero 大数字由 --dm-h 驱动（fnmonitor .sheet-hero .dnum{--dm-h:50px}）',
      /\.mon-hero-num \{[^}]*--dm-h:\s*50px/.test(cssSrc) &&
      /\.mon-hero-num b \{[^}]*font:\s*700 calc\(var\(--dm-h\) \* 1\.08\)/.test(cssSrc)],
    ['.mon-hero 单位由 --dm-h 派生（fnmonitor .dnum>.unit max(11px,--dm-h*.32)）',
      /\.mon-hero-num span \{[^}]*max\(11px, calc\(var\(--dm-h\) \* \.32\)\)/.test(cssSrc)],
    ['.mon-hero 大数字带温度色阶 class（lv-ok / lv-warn / lv-crit）',
      /\.mon-hero-num b\.lv-warn \{/.test(cssSrc) &&
      /\.mon-hero-num b\.lv-crit \{/.test(cssSrc)],
    ['.mon-hero 右侧区块右对齐',
      /\.mon-hero-right \{[^}]*text-align:\s*right/.test(cssSrc)],
  ];
  heroRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- CSS 级：统计格（对齐 fnmonitor .stat-cells）---------- */
  const cellRules = [
    ['.mon-cells 为一行 4 列（fnmonitor .temp-cells repeat(4,minmax(0,1fr))）',
      /\.mon-cells \{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/.test(cssSrc)],
    ['.mon-cells 用 1px gap + 边框底色做分隔线（fnmonitor 手法）',
      /\.mon-cells \{[^}]*gap:\s*1px[^}]*background:\s*var\(--border\)/.test(cssSrc)],
    ['.mon-cell padding 12px 16px（v1.1.45 按需求调小，高度 91px → 74px）',
      /\.mon-cell \{[^}]*padding:\s*12px 16px/.test(cssSrc)],
    ['.mon-cell 数字 1.125rem = 18px（fnmonitor .temp-cells b font-size:18px）',
      /\.mon-cell b \{[^}]*font-size:\s*1\.125rem/.test(cssSrc)],
    ['.mon-cell 数字 tabular-nums 等宽',
      /\.mon-cell b \{[^}]*font-variant-numeric:\s*tabular-nums/.test(cssSrc)],
  ];
  cellRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- CSS 级：温度计 / 电压 / 风扇 ---------- */
  const bodyRules = [
    ['.mon-thermo 容器内缩 1.5rem = 24px（fnmonitor .card.pad）',
      /\.mon-thermo \{[^}]*padding:\s*1\.5rem/.test(cssSrc)],
    ['.mon-thermo-row 三列栅格 112px 1fr 64px',
      /\.mon-thermo-row \{[^}]*grid-template-columns:\s*112px 1fr 64px/.test(cssSrc)],
    ['.mon-thermo-row 行距 .625rem = 10px（fnmonitor padding:10px 0）',
      /\.mon-thermo-row \{[^}]*padding:\s*\.625rem 0/.test(cssSrc)],
    ['.mon-thermo-row 字号 .8125rem = 13px（fnmonitor --fs-sm）',
      /\.mon-thermo-row \{[^}]*font-size:\s*\.8125rem/.test(cssSrc)],
    ['温度条高度 6px（fnmonitor .thermo height:6px）',
      /\.mon-thermo-fill \{[^}]*height:\s*6px/.test(cssSrc) ||
      /\.mon-thermo-bar \{[^}]*height:\s*6px/.test(cssSrc)],
    ['.mon-volts 为 2 列网格（fnmonitor .volts repeat(2,1fr)）',
      /\.mon-volts \{[^}]*grid-template-columns:\s*repeat\(2,\s*1fr\)/.test(cssSrc)],
    ['.mon-volt 用 inset 描边（fnmonitor box-shadow:inset 0 0 0 1px）',
      /\.mon-volt \{[^}]*box-shadow:\s*inset 0 0 0 1px/.test(cssSrc)],
    ['.mon-volt 数字 1.25rem = 20px（fnmonitor .volt .num）',
      /\.mon-volt b \{[^}]*font-size:\s*1\.25rem/.test(cssSrc)],
    ['.mon-fan-ico 为 40px 圆形（fnmonitor .fan-ico）',
      /\.mon-fan-ico \{[^}]*width:\s*40px[^}]*height:\s*40px[^}]*border-radius:\s*50%/.test(cssSrc)],
    ['.mon-fan-ico svg 24px + 旋转动画（fnmonitor animation:spin）',
      /\.mon-fan-ico svg \{[^}]*width:\s*24px[^}]*animation:\s*mon-fan-spin linear infinite/.test(cssSrc)],
    ['风扇转速 1.5rem = 24px（fnmonitor .fan .num）',
      /\.mon-fan-rpm \{[^}]*font-size:\s*1\.5rem/.test(cssSrc)],
    ['风扇动画尊重 prefers-reduced-motion',
      /@media \(prefers-reduced-motion: reduce\)[^{]*\{\s*\.mon-fan-ico svg \{ animation: none/.test(cssSrc)],
  ];
  bodyRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- CSS 级：堆叠条 / 图例 / 分节 / 明细 ---------- */
  const tailRules = [
    ['.mon-stack gap 2px（fnmonitor .stack gap:2px）',
      /\.mon-stack \{[^}]*gap:\s*2px/.test(cssSrc)],
    ['.mon-stack 高 12px（fnmonitor 能耗板块 stack height:12px）',
      /\.mon-stack \{[^}]*height:\s*12px/.test(cssSrc)],
    ['.mon-stack > i 最小宽 2px（fnmonitor min-width:2px）',
      /\.mon-stack > i \{[^}]*min-width:\s*2px/.test(cssSrc)],
    ['.mon-stack 首尾圆角（fnmonitor :first-child/:last-child）',
      /\.mon-stack > i:first-child \{ border-radius: 6px 0 0 6px; \}/.test(cssSrc) &&
      /\.mon-stack > i:last-child \{ border-radius: 0 6px 6px 0; \}/.test(cssSrc)],
    ['.mon-stack-wrap 为卡片容器（堆叠条与图例同卡，fnmonitor .card.pad）',
      /\.mon-stack-wrap \{[^}]*background:\s*var\(--bg-card\)[^}]*padding:\s*1\.5rem/.test(cssSrc)],
    ['.mon-legend gap 6px 18px（fnmonitor .legend）',
      /\.mon-legend \{[^}]*gap:\s*6px 18px/.test(cssSrc)],
    ['.mon-legend 色块 8px（fnmonitor .legend i 8px）',
      /\.mon-legend > span > i \{[^}]*width:\s*8px[^}]*height:\s*8px/.test(cssSrc)],
    ['分节标题全站统一 .mon-cmp-sec（.mon-sub-sec 已彻底删除）',
      /\.mon-cmp-sec \{[^}]*border-left:\s*3px solid var\(--accent\)/.test(cssSrc) &&
      !/\.mon-sub-sec \{/.test(cssSrc)],
    ['.mon-dkv 行分隔用 border-bottom（末行无分隔线）',
      /\.mon-dkv-row \{[^}]*border-bottom:\s*1px solid var\(--border\)/.test(cssSrc) &&
      /\.mon-dkv-row:last-child \{ border-bottom: none; \}/.test(cssSrc)],
    ['.mon-dkv 行内距 .5rem 0',
      /\.mon-dkv-row \{[^}]*padding:\s*\.5rem 0/.test(cssSrc)],
    ['.mon-dkv 右侧值 tabular-nums 右对齐',
      /\.mon-dkv-row \.v \{[^}]*text-align:\s*right[^}]*font-variant-numeric:\s*tabular-nums/.test(cssSrc)],
    ['.mon-info 对齐 fnmonitor .note.info（fill-1 底 + ink-2 字）',
      /\.mon-info \{[^}]*background:\s*var\(--bg-hover\)/.test(cssSrc)],
    ['.mon-sensor-tag 对齐 fnmonitor .pill（24px 高 + 999px 圆角）',
      /\.mon-sensor-tag \{[^}]*height:\s*24px[^}]*border-radius:\s*999px/.test(cssSrc)],
    ['板块级段控件右对齐（fnmonitor .sheet-seg justify-content:flex-end）',
      /\.mon-panel-charts \.mon-cmp-chart-seg \{[^}]*justify-content:\s*flex-end/.test(cssSrc)],
  ];
  tailRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 源码级：类名冲突隔离 ---------- */
  const isoRules = [
    ['抽屉堆叠条改用 .mon-dstack（避免与板块 .mon-stack 冲突）',
      /class="mon-dstack"/.test(compSrc) && /\.mon-dstack \{/.test(cssSrc)],
    ['抽屉键值表统一使用 .mon-dkv（compute.js 与 monitor.js 共用）',
      /class="mon-dkv"/.test(compSrc) && /class="mon-dkv"/.test(monSrc) &&
      /\.mon-dkv \{/.test(cssSrc)],
    ['CSS 中不再存在会覆盖板块样式的旧 .mon-stack 定义',
      /\.mon-stack \{[^}]*height:\s*14px/.test(cssSrc) === false],
    ['CSS 中不再存在会覆盖样式的旧 .mon-kv 定义（v1.1.45 已彻底合并）',
      /^\s*\.mon-kv\s*\{/m.test(cssSrc) === false],
  ];
  isoRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---------- 源码级：JS 结构 ---------- */
  const jsRules = [
    ['风扇渲染为 kv 键值表（fnmonitor kvHTML）',
      /class="mon-dkv-row"/.test(monSrc) &&
      /* v1.1.48：风扇名经 fanLabel 可读化 */
      /esc\(fanLabel\(f\.name\) \|\| '风扇'\)/.test(monSrc) &&
      /fmtInt\(rpm\) \+ ' RPM'/.test(monSrc)],
    ['风扇转速显示为「N RPM」（fnmonitor F.int(rpm) + RPM）',
      /fmtInt\(rpm\) \+ ' RPM'/.test(monSrc)],
    ['电压渲染为 kv 键值表（fnmonitor kvHTML）',
      /esc\(v\.name\)/.test(monSrc) &&
      /fmt2\(val\) \+ ' V'/.test(monSrc)],
    ['电压值显示为「N V」（fnmonitor F.d2 + V）',
      /fmt2\(val\) \+ ' V'/.test(monSrc)],
    ['堆叠条与图例同在 .mon-stack-wrap 卡片内',
      /class="mon-stack-wrap"/.test(monSrc)],
    ['板块级趋势图初始化兼容宿主自身即带 data-pcharts',
      /box\.getAttribute\('data-pcharts'\) === groupKey/.test(compSrc)],
  ];
  jsRules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.42 1:1 还原断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();


/* =====================================================================
   v1.1.43：总览卡片 1:1 还原（fnmonitor 的 thermal / energy overview 模块）
   ---------------------------------------------------------------------
   v1.1.42 只还原了「详情抽屉」（SHEETS.temp / SHEETS.power），
   用户拿 fnmonitor 截图比对后发现：截图里的其实是「总览卡片」
   （defineModule id:'thermal' 3014 行 / id:'energy' 3107 行）。
   v1.1.43 两套都做，并补齐数据缺口（每日用电量 / 月年推算 / 柱状图）。

   本块断言总览侧的结构与 CSS 关键值：
     .g-thermal  1.5fr 1fr（fnmonitor .grid.g-thermal）
     .energy     1.1fr 1fr（fnmonitor .energy）
     .thermo-row 112px 1fr 64px / 条高 6px
     .stat-cells 2 列 × 2 行 / .stat-cell .dnum --dm-h:24px
     .energy-live .dnum.huge --dm-h:60px（大数字）
     .stack 高 12px gap 2px / .legend gap 6px 18px 色块 8px
     .volts 2 列
   ===================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- 温度和散热 / 能耗 总览卡片 1:1 还原（v1.1.43）---');

  const monSrc2 = fs.readFileSync(SRC, 'utf8');
  const compSrc2 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'static', 'js', 'compute.js'), 'utf8');
  const cssTxt2 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'static', 'css', 'app.css'), 'utf8');
  const htmlTxt2 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui',
    'index.html'), 'utf8');

  /* ---------- 总览 DOM 结构（index.html）---------- */
  const domRules = [
    ['温度总览左栏容器 #monTempOverview（可点开抽屉）',
      /id="monTempOverview"[^>]*data-sheet="temp"/.test(htmlTxt2)],
    ['温度总览右栏容器 #monTempSide（风扇 + 电压）',
      /id="monTempSide"/.test(htmlTxt2)],
    ['能耗总览左栏容器 #monPowerOverview（可点开抽屉）',
      /id="monPowerOverview"[^>]*data-sheet="power"/.test(htmlTxt2)],
    ['能耗四格统计容器 #monPowerStats',
      /id="monPowerStats"/.test(htmlTxt2)],
    ['每日用电柱状图容器 #monPowerDaily',
      /id="monPowerDaily"/.test(htmlTxt2)],
    ['能耗左栏带 energy-live 类（决定大数字字号）',
      /class="[^"]*energy-live[^"]*" id="monPowerOverview"/.test(htmlTxt2)],
    ['温度摘要行 #monTempDek',
      /id="monTempDek"/.test(htmlTxt2)],
    /* ⚠️ v1.1.56：能耗摘要行 #monPowerDek 已按用户要求删除 */
    ['能耗摘要行 #monPowerDek 已移除（v1.1.56）',
      !/id="monPowerDek"/.test(htmlTxt2)],
    ['旧容器 #monTemp / #monPower 已移除（内容进抽屉）',
      !/id="monTemp"/.test(htmlTxt2) && !/id="monPower"/.test(htmlTxt2)],
  ];

  /* ---------- 总览 CSS 关键值 ---------- */
  const cssRules = [
    ['.g-thermal 为 1.5fr 1fr（fnmonitor .grid.g-thermal）',
      /\.g-thermal \{[^}]*grid-template-columns: 1\.5fr 1fr/.test(cssTxt2)],
    ['.energy 为 1.1fr 1fr（fnmonitor .energy）',
      /\.energy \{[^}]*grid-template-columns: 1\.1fr 1fr/.test(cssTxt2)],
    ['.thermo-row 三列栅格 112px 1fr 64px',
      /\.thermo-row \{[^}]*grid-template-columns: 112px 1fr 64px/.test(cssTxt2)],
    ['温度条高 6px（fnmonitor .thermo height:6px）',
      /\.thermo \{[^}]*height: 6px/.test(cssTxt2)],
    ['.stat-cells 为 2 列（fnmonitor repeat(2,1fr)）',
      /\.stat-cells \{[^}]*grid-template-columns: repeat\(2, 1fr\)/.test(cssTxt2)],
    ['.stat-cell .dnum 用 --dm-h: 24px（fnmonitor .stat-cell .dnum）',
      /\.stat-cell \.dnum \{[^}]*--dm-h: 24px/.test(cssTxt2)],
    ['.energy-live .dnum.huge 用 --dm-h: 60px（fnmonitor）',
      /\.energy-live \.dnum\.huge \{[^}]*--dm-h: 60px/.test(cssTxt2)],
    ['.dnum 由 --dm-h 驱动字号（不是裸 font-size）',
      /\.dnum \{[^}]*--dm-h: 40px/.test(cssTxt2)],
    ['.dnum 数字本体用 .dm 且带 display 字体',
      /\.dnum > \.dm \{[^}]*var\(--font-display\)/.test(cssTxt2)],
    ['堆叠条 gap 2px（fnmonitor .stack gap:2px）',
      /\.stack \{[^}]*gap: 2px/.test(cssTxt2)],
    ['图例 gap 6px 18px（fnmonitor .legend）',
      /\.legend \{[^}]*gap: 6px 18px/.test(cssTxt2)],
    ['.volts 为 2 列网格',
      /\.volts \{[^}]*grid-template-columns: repeat\(2, 1fr\)/.test(cssTxt2)],
    ['640px 断点：.energy-live .dnum.huge --dm-h:46px',
      /@media \(max-width: 640px\)[\s\S]{0,400}?\.energy-live \.dnum\.huge \{ --dm-h: 46px/.test(cssTxt2)],
    ['640px 断点：.stat-cell .dnum --dm-h:20px',
      /@media \(max-width: 640px\)[\s\S]{0,500}?\.stat-cell \.dnum \{ --dm-h: 20px/.test(cssTxt2)],
    ['960px 断点：.g-thermal / .energy 单列',
      /@media \(max-width: 960px\)[\s\S]{0,200}?\.g-thermal \{ grid-template-columns: 1fr/.test(cssTxt2) &&
      /@media \(max-width: 960px\)[\s\S]{0,300}?\.energy \{ grid-template-columns: 1fr/.test(cssTxt2)],
  ];

  /* ---------- 总览 JS 实现 ---------- */
  const jsRules = [
    ['renderTempOverview 存在（温度总览）',
      /function renderTempOverview\(d\)/.test(monSrc2)],
    ['renderPowerOverview 存在（能耗总览）',
      /function renderPowerOverview\(d\)/.test(monSrc2)],
    ['renderPowerDaily 存在（每日用电柱状图）',
      /function renderPowerDaily\(res\)/.test(monSrc2)],
    /* ⚠️ v1.1.48：主页只显示重要部件（CPU/主板/硬盘），slice(0,6) 上限已移除 */
    ['主页总览不再渲染其它传感器（只 CPU/主板/硬盘）',
      /var visLabels = sensorLabels\(visSensors\);/.test(monSrc2) === false &&
      /list\.slice\(0, 6\)/.test(monSrc2) === false],
    ['硬盘段落独立，不受 6 条上限约束',
      /diskRows \+=/.test(monSrc2)],
    ['温度色阶按阈值分 5 段（fnmonitor RAMP.temp / dtemp）',
      /function ovTempStops\(kind\)/.test(monSrc2) &&
      /function ovTempFill\(value, kind\)/.test(monSrc2)],
    ['刻度尺 20/40/60/80/100（fnmonitor scaleHTML）',
      /\[20, 40, 60, 80, 100\]/.test(monSrc2)],
    ['功率部件分解对齐 fnmonitor powerParts（含 iDRAC 分支）',
      /if \(p\.source === 'idrac'\)/.test(monSrc2) &&
      /CPU 封装/.test(monSrc2) && /内存/.test(monSrc2) &&
      /主板与其他/.test(monSrc2)],
    ['硬盘部件用 hatch 斜纹（fnmonitor）',
      /'hatch'/.test(monSrc2)],
    ['柱状图最大值上浮 8%（fnmonitor barOpts max×1.08）',
      /1\.08/.test(monSrc2)],
    ['X 轴标签步进 n>14?7:n>7?3:1 且末位「今天」（fnmonitor）',
      /今天/.test(monSrc2)],
    ['总览卡片可点击打开抽屉（.ov-tap[data-sheet]）',
      /\.ov-tap\[data-sheet\]/.test(monSrc2)],
    ['能耗统计走 /api/power/stats',
      /\/api\/power\/stats/.test(monSrc2)],
    ['每日用电走 /api/power/daily',
      /\/api\/power\/daily/.test(monSrc2)],
    ['抽屉支持 temp / power 两种（compute.js openCompute）',
      /kind === 'temp'/.test(compSrc2) && /kind === 'power'/.test(compSrc2)],
    ['抽屉调用 monitor 导出的 tempSheetHtml / powerSheetHtml',
      /window\.FnStyleMonitor\.tempSheetHtml/.test(compSrc2) &&
      /window\.FnStyleMonitor\.powerSheetHtml/.test(compSrc2)],
  ];

  const all = domRules.concat(cssRules, jsRules);
  all.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.43 总览卡片 1:1 还原断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();


/* =====================================================================
   v1.1.43：版本号一致性（4 处必须同步）
   ---------------------------------------------------------------------
   历史教训：v1.1.36 ~ v1.1.42 每次 bump 只改了 manifest / app.js /
   index.html，漏了 app/ui/index.cgi 的 APP_VERSION —— 实机 9910 端口
   的 /api/version 一直返回 1.1.35（落后 8 个版本），用户完全看不到。
   本块把 4 处版本号锁在一起，任何一处漏改即失败。
   ===================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- 版本号一致性（4 处同步）---');

  const mf = fs.readFileSync(path.join(__dirname, '..', 'manifest'), 'utf8');
  const m = mf.match(/^version\s*=\s*(\S+)/m);
  const ver = m ? m[1] : '';
  const htmlTxtV = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const appJsV = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'app.js'), 'utf8');
  const cgiV = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'index.cgi'), 'utf8');

  const rules = [
    ['manifest 版本号已读到（v' + ver + '）', ver !== ''],
    ['index.html 的 ver-pill 与 manifest 一致',
      new RegExp('ver-pill[^>]*>v' + ver + '<').test(htmlTxtV)],
    ['app.js 的 APP_VERSION 与 manifest 一致',
      new RegExp("APP_VERSION = '" + ver + "'").test(appJsV)],
    ['index.cgi 的 APP_VERSION 与 manifest 一致',
      new RegExp('APP_VERSION="' + ver + '"').test(cgiV)],
  ];
  rules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ 版本号一致性断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.44 —— 抽屉红框区域 1:1 对齐 fnmonitor + 主页温度趋势图移除
   ----------------------------------------------------------------------
   用户反馈：「你现在给我实现的好丑啊」，并给出 fnmonitor 截图对照。
   本块把本轮 5 处结构性改动锁死，防止后续又被改回错误形态：
     ① 抽屉四格 = 一行 4 列（不是 2×2）+ 数字 18px（不是 32px）
     ② hero 阈值单行（不是 <br> 两行）
     ③ hero 大数字由 --dm-h 驱动（50px → 实际 54px）
     ④ 风扇 / 电压 / 全部传感器统一为 kv 键值表
     ⑤ 主页温度板块不再渲染 CPU / 主板温度趋势图（抽屉保留）
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.44 抽屉红框对齐 fnmonitor + 主页趋势图移除 ---');

  const cssSrc44 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  const monSrc44 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const compSrc44 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8');

  const rules44 = [
    /* ---- ① 抽屉四格：一行 4 列 + 18px ---- */
    ['抽屉四格为一行 4 列（fnmonitor .temp-cells）',
      /\.mon-cells \{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/.test(cssSrc44)],
    ['抽屉四格数字 18px（fnmonitor .temp-cells b font-size:18px）',
      /\.mon-cell b \{[^}]*font-size:\s*1\.125rem/.test(cssSrc44)],
    ['窄屏降为 2 列（fnmonitor 640px 断点）',
      /@media \(max-width: 760px\)[\s\S]*?\.mon-cells \{[^}]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/.test(cssSrc44)],
    /* ---- ② hero 阈值单行 ---- */
    ['hero 阈值单行（无 <br>）',
      /mon-hero-side">CPU ' \+ TH_TEMP_WARN \+ ' \/ ' \+ TH_TEMP_CRIT \+[\s\S]{0,60}'℃ · 硬盘 '/.test(monSrc44) &&
      !/TH_TEMP_CRIT \+ '℃<br>'/.test(monSrc44)],
    ['hero eyebrow 文案为「CPU」（fnmonitor 原文）',
      /<div class="mon-hero-eyebrow">CPU<\/div>/.test(monSrc44)],
    /* ---- ③ hero 大数字 --dm-h 驱动 ---- */
    ['hero 数字 --dm-h: 50px（fnmonitor .sheet-hero .dnum）',
      /\.mon-hero-num \{[^}]*--dm-h:\s*50px/.test(cssSrc44)],
    ['hero 数字色阶 class（lv-ok / lv-warn / lv-crit）',
      /\.mon-hero-num b\.lv-warn \{/.test(cssSrc44) &&
      /\.mon-hero-num b\.lv-crit \{/.test(cssSrc44)],
    ['hero 数字按温度取色（tempLevel）',
      /var cpuLv = tempLevel\(cpuTemp\)/.test(monSrc44)],
    /* ---- ④ 风扇 / 电压 / 传感器统一 kv 表 ---- */
    ['风扇改为 kv 键值表（不再是卡片列表）',
      !/class="mon-fan-ico"/.test(monSrc44) &&
      /fmtInt\(rpm\) \+ ' RPM'/.test(monSrc44)],
    ['电压改为 kv 键值表（不再是 2 列卡片）',
      !/class="mon-volt"/.test(monSrc44) &&
      /fmt2\(val\) \+ ' V'/.test(monSrc44)],
    ['全部传感器沿用 kv 表',
      /class="mon-dkv-row"/.test(monSrc44)],
    ['分节标题去掉自加的 ::before 竖条（改由 .mon-cmp-sec 统一提供）',
      !/\.mon-sub-sec::before \{/.test(cssSrc44)],
    /* ---- ⑤ 主页温度趋势图移除 ---- */
    ['panelChartsHtml 支持 onlyKeys 白名单',
      /function panelChartsHtml\(groupKey, onlyKeys\)/.test(compSrc44)],
    ['过滤后的图表 key 写到 data-pdefs（索引对齐）',
      /data-pdefs=/.test(compSrc44)],
    ['initPanelCharts 优先读 data-pdefs 还原 defs',
      /getAttribute\('data-pdefs'\)/.test(compSrc44)],
    ['主页温度板块传空数组 → 不渲染趋势图卡片',
      /host: '#monTempCharts', key: 'temp', only: \[\]/.test(monSrc44)],
    ['抽屉温度趋势图保留（走 chartsHtml，不受 onlyKeys 影响）',
      /window\.FnStyleCompute\.chartsHtml\('temp'\)/.test(monSrc44)],
    /* ---- 额外：抽屉底色层次 ---- */
    ['抽屉底色用 var(--bg)（fnmonitor .sheet{background:var(--bg)}）',
      /\.mon-drawer \{[^}]*background:\s*var\(--bg\)/.test(cssSrc44)],
    ['键值表已并入 .mon-dkv（v1.1.45 起全站唯一，无独立 .mon-kv）',
      /\.mon-dkv \{/.test(cssSrc44) && !/^\s*\.mon-kv\s*\{/m.test(cssSrc44)],
  ];
  rules44.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.44 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.45 —— 抽屉卡片样式统一（圆角 / 高度 / 间距 / 分节标题）
   ----------------------------------------------------------------------
   用户反馈：
     「如图温度与散热抽屉中如图红框中的卡片调整为圆角（与下方卡片的一致），
       同时调整卡片高度调小，目前的太高了不协调，
       而且调整与下方卡片和上方信息的间距，目前挨太紧了；
       如图卡片的标题显示的不对，请参考处理器抽屉中的卡片的标题样式调整，
       所有的抽屉中的卡片的样式保持一致嘛，不能乱」

   本块把「一致性」锁死 —— 关键点不是把两套样式改成同样的数值，
   而是**合并成一套**，防止下一轮改样式又长歪：
     ① 四格统计卡圆角 = var(--radius)（10px），与下方卡片一致
     ② 四格统计卡高度调小（padding 12px 16px）
     ③ 区块间距 14px（不再与 hero / 温度计贴死）
     ④ 分节标题全站统一 .mon-cmp-sec（含 3px 竖条）
     ⑤ 键值表全站统一 .mon-dkv（删除 .mon-kv 整套，JS 全部改类名）
     ⑥ 抽屉内所有卡片圆角统一 var(--radius)
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.45 抽屉卡片样式统一 ---');

  const cssSrc45 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  const monSrc45 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const compSrc45 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8');

  const rules45 = [
    /* ---- ① 四格统计卡圆角统一 ---- */
    ['四格统计卡圆角 = var(--radius)（与下方卡片一致）',
      /\.mon-cells \{[^}]*border-radius:\s*var\(--radius\)/.test(cssSrc45)],
    ['温度计卡片圆角 = var(--radius)',
      /\.mon-thermo \{[^}]*border-radius:\s*var\(--radius\)/.test(cssSrc45)],
    ['趋势图卡片圆角 = var(--radius)',
      /\.mon-cmp-chart-card \{[^}]*border-radius:\s*var\(--radius\)/.test(cssSrc45)],
    ['功耗堆叠条卡片圆角 = var(--radius)',
      /\.mon-stack-wrap \{[^}]*border-radius:\s*var\(--radius\)/.test(cssSrc45)],
    ['抽屉通用卡片 .mon-dw-card 圆角 = var(--radius)',
      /\.mon-dw-card \{[^}]*border-radius:\s*var\(--radius\)/.test(cssSrc45)],
    ['抽屉内不再有卡片使用 --radius-sm（6px）作圆角',
      !/\.mon-(cells|thermo|stack-wrap|cmp-chart-card|dw-card) \{[^}]*border-radius:\s*var\(--radius-sm\)/.test(cssSrc45)],
    /* ---- ② 四格统计卡高度调小 ---- */
    ['四格统计卡内边距 = 12px 16px（实测高度 91px → 74px）',
      /\.mon-cell \{[^}]*padding:\s*12px 16px/.test(cssSrc45)],
    ['四格统计卡数字 18px 不变（fnmonitor .temp-cells b）',
      /\.mon-cell b \{[^}]*font-size:\s*1\.125rem/.test(cssSrc45)],
    /* ---- ③ 区块间距 14px ---- */
    ['四格统计卡与上方 hero 间距 14px',
      /\.mon-drawer-body > \.mon-cells \{ margin-top: 14px; \}/.test(cssSrc45)],
    ['温度计卡片间距 14px',
      /\.mon-drawer-body > \.mon-thermo \{ margin-top: 14px; \}/.test(cssSrc45)],
    ['趋势图容器间距 14px',
      /\.mon-drawer-body > \.mon-cmp-charts \{ margin-top: 14px; \}/.test(cssSrc45)],
    ['功耗堆叠条间距 14px',
      /\.mon-drawer-body > \.mon-stack-wrap \{ margin-top: 14px; \}/.test(cssSrc45)],
    ['分节标题上方间距 20px（与处理器抽屉节奏一致）',
      /\.mon-drawer-body > \.mon-cmp-sec \{ margin-top: 20px; \}/.test(cssSrc45)],
    /* ---- ④ 分节标题统一 .mon-cmp-sec ---- */
    ['CSS 中 .mon-sub-sec 整套已删除',
      !/\.mon-sub-sec\s*\{/.test(cssSrc45) && !/\.mon-sub-sec::before\s*\{/.test(cssSrc45)],
    ['JS 中不再出现 mon-sub-sec',
      !/mon-sub-sec/.test(monSrc45)],
    ['温度/能耗抽屉改用 .mon-cmp-sec',
      /class="mon-cmp-sec"/.test(monSrc45)],
    ['.mon-cmp-sec 保留 3px 竖条（处理器抽屉基准）',
      /\.mon-cmp-sec \{[^}]*border-left:\s*3px solid var\(--accent\)/.test(cssSrc45)],
    ['.mon-cmp-sec 标题为 primary 色 + 600 字重',
      /\.mon-cmp-sec \{[^}]*font-weight:\s*600[^}]*color:\s*var\(--text-primary\)/.test(cssSrc45)],
    /* ---- ⑤ 键值表统一 .mon-dkv ---- */
    ['CSS 中 .mon-kv 整套已删除（真正合并，不是改数值）',
      !/^\s*\.mon-kv\s*\{/m.test(cssSrc45) && !/^\s*\.mon-kv-row\s*\{/m.test(cssSrc45)],
    ['JS 中不再出现 mon-kv',
      !/mon-kv/.test(monSrc45)],
    ['温度抽屉风扇段改用 .mon-dkv',
      /class="mon-dkv"/.test(monSrc45)],
    ['抽屉键值行结构统一为 .k / .v 子类',
      /<span class="k">/.test(monSrc45) && /<span class="v">/.test(monSrc45)],
    ['compute.js 与 monitor.js 共用 .mon-dkv',
      /class="mon-dkv"/.test(compSrc45) && /class="mon-dkv"/.test(monSrc45)],
    ['.mon-dkv 无卡片外框（无 background / border-radius）',
      /\.mon-dkv \{[^}]*display:\s*flex[^}]*flex-direction:\s*column/.test(cssSrc45) &&
      !/\.mon-dkv \{[^}]*background:/.test(cssSrc45)],
    ['.mon-dkv 行内距 .5rem 0',
      /\.mon-dkv-row \{[^}]*padding:\s*\.5rem 0/.test(cssSrc45)],
    ['.mon-dkv 行分隔用 border-bottom（末行无）',
      /\.mon-dkv-row \{[^}]*border-bottom:\s*1px solid var\(--border\)/.test(cssSrc45) &&
      /\.mon-dkv-row:last-child \{ border-bottom: none; \}/.test(cssSrc45)],
    ['.mon-dkv 键列 .k 为 muted 色',
      /\.mon-dkv-row \.k \{[^}]*color:\s*var\(--text-muted\)/.test(cssSrc45)],
    ['.mon-dkv 值列 .v 为 primary 色 + 右对齐',
      /\.mon-dkv-row \.v \{[^}]*color:\s*var\(--text-primary\)[^}]*text-align:\s*right/.test(cssSrc45)],
    ['.mon-dkv 补上 .sub 次行（传感器上限）',
      /\.mon-dkv-row \.sub \{/.test(cssSrc45)],
  ];
  rules45.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.45 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.46 —— 温度趋势图主板线消失修复（多系列数据装载）
   ----------------------------------------------------------------------
   用户反馈：「如图温度趋势图上为什么没有主板的温度，请核实」
   核实结论（NAS 实机探针）：数据链路完好 —— mbTemp = 38.0，
   temp_mb 1h 历史 60 个点，CHART_GROUPS.temp.metrics 也含 temp,temp_mb。
   根因在前端：v1.1.44 合并双线图时只改了 series 定义，
   drawCharts / drawPanelCharts 仍按 d.key 只取一条指标 →
   setData([pts]) 只喂一列，LineChart 的 cols[1]（主板）永远空数组。
   本块锁死修复：数据装载必须按 series[].m 逐系列取。
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.46 温度趋势图双线数据装载修复 ---');

  const compSrc46 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8');

  const rules46 = [
    ['drawCharts 按 series[].m 逐系列取数据',
      /var data = \(d\.series \|\| \[\]\)\.map\(function \(s\) \{\s*return \(res\.series && res\.series\[s\.m\]\) \|\| \[\];/.test(compSrc46)],
    ['drawPanelCharts 按 series[].m 逐系列取数据（含 tf 变换）',
      /var data = \(d\.series \|\| \[\]\)\.map\(function \(s\) \{\s*var p = \(res\.series && res\.series\[s\.m\]\) \|\| \[\];/.test(compSrc46)],
    ['不再存在按 d.key 单列装载的旧写法（setData([pts])）',
      /c\.setData\(\[pts\], span\)/.test(compSrc46) === false],
    ['温度图双系列定义（temp/CPU + temp_mb/主板）',
      /m:\s*'temp',\s*label:\s*'CPU'/.test(compSrc46) &&
      /m:\s*'temp_mb',\s*label:\s*'主板'/.test(compSrc46)],
    ['temp 组 metrics 请求含 temp,temp_mb',
      /temp:\s*\{ defs: TEMP_CHARTS, metrics:\s*'temp,temp_mb' \}/.test(compSrc46)],
    ['LineChart.setData 接受多列（cols）数据（chart.js 契约）',
      /LineChart\.prototype\.setData = function \(cols, span\)/.test(
        fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'chart.js'), 'utf8'))],
  ];
  rules46.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.46 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.47 —— 测点计数与列表行数一致（顶部 16 vs 列表 15）
   ----------------------------------------------------------------------
   用户反馈：「顶部统计的是16个温度测点，为什么下方显示的只有15个呢？」
   NAS 实机核实：temps 12 条中有 2 条 type='disk' 的 drivetemp（就是硬盘，
   列表已在 disks 段单独显示），列表渲染时被排除；
   而抽屉副标题公式 temps.length + disks + cpu 把它们重复计入、又漏了主板
   → 12+3+1=16，列表实际 = CPU+主板+10传感器+3硬盘 = 15。
   修复：monitor.js 新增 tempPointCount（全站唯一口径 = 列表行数），
   总览计数与抽屉副标题都调它。
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.47 测点计数口径统一（= 列表行数）---');

  const monSrc47 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const compSrc47 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8');

  const rules47 = [
    ['monitor.js 定义 tempPointCount（全站唯一口径函数）',
      /function tempPointCount\(t\) \{/.test(monSrc47)],
    ['口径排除 temps 中 type=\'disk\' 条目（drivetemp 与硬盘段重复）',
      /function tempPointCount\(t\) \{[\s\S]{0,600}s\.type === 'disk'/.test(monSrc47)],
    ['口径排除无读数传感器（value null/空）',
      /function tempPointCount\(t\) \{[\s\S]{0,800}s\.value === null \|\| s\.value === undefined \|\| s\.value === ''/.test(monSrc47)],
    ['口径含 CPU 与主板（列表里各有一行）',
      /function tempPointCount\(t\) \{[\s\S]{0,400}t\.cpuTemp[\s\S]{0,300}t\.mbTemp/.test(monSrc47)],
    ['tempPointCount 已导出到 FnStyleMonitor',
      /tempPointCount: tempPointCount,/.test(monSrc47)],
    /* v1.1.49：#monTempCount 标题栏统计标签整体移除（HTML + JS 更新逻辑），
       计数逻辑本身（tempPointCount）保留，仍用于抽屉副标题 */
    ['总览计数元素已移除（标题栏右侧统计标签）',
      /cnt\.textContent = tempPointCount\(t\)/.test(monSrc47) === false &&
      /monTempCount/.test(monSrc47) === false],
    ['tempPointCount 口径函数保留（抽屉副标题仍使用）',
      /function tempPointCount\(t\) \{/.test(monSrc47)],
    ['抽屉副标题改调 FnStyleMonitor.tempPointCount',
      /window\.FnStyleMonitor && window\.FnStyleMonitor\.tempPointCount/.test(compSrc47) &&
      /\(temps \+ disks \+ cpu\) \+ ' 个温度测点/.test(compSrc47) === false],
  ];
  rules47.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.47 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.48 —— 抽屉 live 更新 + 传感器名称可读化
   ----------------------------------------------------------------------
   用户反馈：
   ①「温度与散热抽屉界面打开后其中显示的温度都不变化，请核实」
      核实：主面板每 3 秒 refresh() 重绘，但计算抽屉只在 openCompute 时
      渲染一次快照，hero/四格/温度计全部冻结。修复：compute.js 新增
      updateLive（重渲染 body + 旧 [data-charts] 节点搬回 + 恢复滚动 +
      重填点阵 + 回填电费 + 同步副标题），monitor.js refresh() 每轮挂钩。
   ②「acpitz、package id 0 都不知道是什么」
      修复：monitor.js 新增 sensorLabel（内核测点名 → 中文对象名）+
      sensorLabels（同名自动带序号），应用于主页总览 / 抽屉温度计 /
      抽屉传感器 kv 三处。
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.48 抽屉 live 更新 + 传感器名称可读化 ---');

  const monSrc48 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const compSrc48 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8');

  const rules48 = [
    ['compute.js 定义 updateLive（抽屉 live 更新）',
      /function updateLive\(d\) \{/.test(compSrc48)],
    ['updateLive 检查 computeOpen（抽屉关着不做事）',
      /function updateLive\(d\) \{\s*if \(!computeOpen \|\| !d\) return;/.test(compSrc48)],
    ['updateLive 保留旧 [data-charts] 节点（图表实例与 range 选择不丢）',
      /var chartNode = body\.querySelector\('\[data-charts\]'\)/.test(compSrc48) &&
      /replaceChild\(chartNode, slot\)/.test(compSrc48)],
    ['updateLive 恢复滚动位置',
      /var st = body\.scrollTop;/.test(compSrc48) && /body\.scrollTop = st;/.test(compSrc48)],
    ['updateLive 四种抽屉都走同一路径（temp/power/mem/cpu）',
      /kind === 'temp' && window\.FnStyleMonitor && window\.FnStyleMonitor\.tempSheetHtml/.test(compSrc48) &&
      /kind === 'power' && window\.FnStyleMonitor && window\.FnStyleMonitor\.powerSheetHtml/.test(compSrc48)],
    ['updateLive 重填每核点阵 + 回填电费 + 同步副标题',
      /setDetailCores\(host, pc\)/.test(compSrc48) &&
      /loadPowerEnergy\(body\)/.test(compSrc48) &&
      /sub\.textContent = tempSubText\(d\)/.test(compSrc48)],
    ['isOpen / updateLive 已导出到 FnStyleCompute',
      /isOpen: isOpen,/.test(compSrc48) && /updateLive: updateLive,/.test(compSrc48)],
    ['monitor.js refresh() 每轮挂钩 updateLive',
      /window\.FnStyleCompute && window\.FnStyleCompute\.isOpen &&\s*\n\s*window\.FnStyleCompute\.isOpen\(\) && window\.FnStyleCompute\.updateLive/.test(monSrc48)],
    ['loadPowerEnergy 加 60 秒缓存（live 更新不重复打重接口）',
      /PWR_ENERGY_TTL = 60000/.test(monSrc48) &&
      /pwrEnergyCache && Date\.now\(\) - pwrEnergyAt < PWR_ENERGY_TTL/.test(monSrc48)],

    ['monitor.js 定义 sensorLabel（内核测点名 → 中文对象名）',
      /function sensorLabel\(name\) \{/.test(monSrc48)],
    ['sensorLabel 映射 Package id / x86_pkg_temp → CPU 封装',
      /\^package id/.test(monSrc48) && /\^x86_pkg_temp/.test(monSrc48)],
    ['sensorLabel 映射 acpitz → 主板 ACPI 温区',
      /\^acpitz/.test(monSrc48)],
    ['sensorLabel 映射 Core N → CPU 核心 N',
      /\^core\[\\s_-\]\?\(\\d\+\)\$/.test(monSrc48) ||
      /CPU 核心/.test(monSrc48)],
    ['sensorLabels 同名测点自动带序号',
      /function sensorLabels\(list\) \{/.test(monSrc48) &&
      /counts\[l\] <= 1\) return l;/.test(monSrc48)],
    /* v1.1.48 追加需求后主页总览不再显示传感器行（只 CPU/主板/硬盘），
       名称映射转用于抽屉温度计与 kv 表 */
    ['主页总览不再渲染其它传感器（映射仅用于抽屉）',
      /var visLabels = sensorLabels\(visSensors\);/.test(monSrc48) === false &&
      /var drawerLabels = sensorLabels\(drawerSensors\);/.test(monSrc48)],
    ['名称映射应用于抽屉温度计测点',
      /var drawerLabels = sensorLabels\(drawerSensors\);/.test(monSrc48)],
    ['名称映射应用于抽屉「全部传感器」kv 表（排序后的 kvList）',
      /var kvLabels = sensorLabels\(kvList\);/.test(monSrc48)],
    ['渲染处不再直接用内核测点名 s.name 作 label',
      /label: s\.name[,\s]/.test(monSrc48) === false &&
      /esc\(s\.name\)\s*\+\s*'\s*<span class="mon-sensor-tag"/.test(monSrc48) === false],
  ];
  rules48.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  if (bad) {
    console.error('❌ v1.1.48 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.49 —— 温度板块：硬盘名用存储空间名 + 去标题栏统计标签 + 按名排序
   ----------------------------------------------------------------------
   用户反馈：
   ①「硬盘名称使用存储空间的名称显示，才好进行区分」
      → 新增 diskDisplayName / diskDisplayLabels：t.disks 自带 vol 字段
        （fnOS 存储空间 N = /volN），标签改为「存储空间 volN」；
        同一存储空间多块盘（RAID 池）标签撞名时自动追加设备名「· sda」。
        应用于主页硬盘行 / 摘要行「硬盘最高」/ 抽屉温度计 / 抽屉全部传感器。
   ②「去掉温度和散热标题栏右侧的统计标签显示」
      → index.html 删 #monTempCount，monitor.js 删 cnt 更新逻辑。
   ③「抽屉中温度卡片和传感器卡片中各对象按名称排序显示」
      → 温度计 points.sort(localeCompare zh-Hans-CN + numeric)；
        全部传感器先按映射名排序再算同名序号（保证「CPU 封装 1/2」与
        显示顺序一致）。
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.49 硬盘存储空间名 + 去统计标签 + 按名排序 ---');

  const monSrc49 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const htmlSrc49 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const monPy49 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'monitor_collect.py'), 'utf8');

  const rules49 = [
    /* ⓪ 后端：drivetemp hwmon 实例映射回 sd 设备名（实机探针 v1.1.49 核实：
       /sys/block/sda/device/hwmon/hwmon2 → hwmon2 = sda，label 缺失时
       前端只会看到「drivetemp 1」，无法对应到盘） */
    ['采集端定义 _drivetemp_dev_map（反查 /sys/block/*/device/hwmon）',
      /def _drivetemp_dev_map\(\):/.test(monPy49) &&
      /sys\/block\/sd\[a-z\]/.test(monPy49) &&
      /device", "hwmon"/.test(monPy49)],
    ['采集端 drivetemp 测点改名 sd 设备名并强制 type=\'disk\'',
      /if name == "drivetemp":/.test(monPy49) &&
      /drv_map\.get\(os\.path\.basename\(d\)\)/.test(monPy49) &&
      /ttype = "disk"/.test(monPy49)],

    /* ① 硬盘名 → 存储空间名 */
    ['monitor.js 定义 diskDisplayName（vol > 0 → 存储空间 volN）',
      /function diskDisplayName\(x\) \{/.test(monSrc49) &&
      /'存储空间 vol' \+ v/.test(monSrc49)],
    ['diskDisplayLabels 同空间多盘自动追加设备名去歧义',
      /function diskDisplayLabels\(list\) \{/.test(monSrc49) &&
      /seen\[s\] > 1 && d && d\.name/.test(monSrc49)],
    ['主页温度卡片硬盘行改用 diskDisplayLabels（不再用插槽号/设备名）',
      /var diskLbls = diskDisplayLabels\(t\.disks\);/.test(monSrc49) &&
      /'插槽 ' \+ slot/.test(monSrc49) === false],
    ['主页摘要行「硬盘最高」用存储空间名',
      /dekDiskLbls\[hotIdx\] \|\| hot\.name/.test(monSrc49)],
    ['抽屉温度计硬盘行用存储空间名',
      /var sheetDiskLbls = diskDisplayLabels\(t\.disks\);/.test(monSrc49) &&
      /label: sheetDiskLbls\[di\] \|\| String\(x\.name \|\| ''\)/.test(monSrc49)],
    ['抽屉「全部传感器」type=\'disk\' 条目按设备名匹配存储空间名',
      /kvDiskByName\[String\(s\.name \|\| ''\)\.toLowerCase\(\)\]/.test(monSrc49)],

    /* ② 去标题栏统计标签 */
    ['index.html 已移除 #monTempCount 元素',
      /monTempCount/.test(htmlSrc49) === false],
    ['monitor.js 已移除 #monTempCount 更新逻辑',
      /monTempCount/.test(monSrc49) === false],

    /* ③ 按名称排序 */
    ['monitor.js 定义 cmpName（西文在前 + 中文拼音序）',
      /function cmpName\(a, b\) \{/.test(monSrc49) &&
      /localeCompare\(y, 'zh-Hans-CN'/.test(monSrc49)],
    ['cmpName 带 numeric（数字后缀按数值比较）',
      /\{ numeric: true \}/.test(monSrc49)],
    ['抽屉温度计测点按名称排序',
      /points\.sort\(function \(a, b\) \{ return cmpName\(a\.label, b\.label\); \}\)/.test(monSrc49)],
    ['抽屉「全部传感器」先排序再算同名序号',
      /var kvList = temps\.slice\(\)\.sort\(function \(a, b\) \{/.test(monSrc49) &&
      /cmpName\(sensorLabel\(a\.name\), sensorLabel\(b\.name\)\)/.test(monSrc49)],
  ];
  rules49.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---- 渲染级：排序结果 + 存储空间名 + 同空间去歧义 ---- */
  try {
    const data49 = {
      temp: {
        available: true, cpuTemp: 47, mbTemp: 33,
        temps: [
          { name: 'acpitz', chip: 'thermal', value: 39, max: null, crit: null, type: 'acpi' },
          { name: 'coretemp Package id 0', chip: 'coretemp', value: 47, max: 80, crit: 100, type: 'cpu' },
          { name: 'sda', chip: 'drivetemp', value: 36, max: null, crit: null, type: 'disk' },
        ],
        fans: [], volts: [],
        disks: [
          { name: 'sda', temp: 36, vol: 1, mounts: ['/vol1'] },
          { name: 'sdb', temp: 41, vol: 1, mounts: ['/vol1'] },
        ],
      },
    };
    const h49 = global.window.FnStyleMonitor.tempSheetHtml(data49);
    const thermoLabels = (h49.match(/mon-thermo-label" title="([^"]*)"/g) || [])
      .map(s => s.replace(/^mon-thermo-label" title="/, '').replace(/"$/, ''));
    const pos = n => thermoLabels.indexOf(n);
    const renderRules49 = [
      ['硬盘行显示存储空间名（同空间两盘自动带设备名去歧义）',
        pos('存储空间 vol1 · sda') >= 0 && pos('存储空间 vol1 · sdb') >= 0],
      ['温度计测点西文名在前（CPU → CPU 封装 → 存储… → 主板…）',
        pos('CPU') >= 0 && pos('CPU 封装') >= 0 &&
        pos('CPU') < pos('CPU 封装') &&
        pos('CPU 封装') < pos('存储空间 vol1 · sda') &&
        pos('存储空间 vol1 · sdb') < pos('主板') &&
        pos('主板') < pos('主板 ACPI 温区')],
      ['「全部传感器」kv 表：drivetemp(sda) 显示为存储空间名',
        /class="k">存储空间 vol1 · sda<span class="mon-sensor-tag">硬盘</.test(h49)],
      ['「全部传感器」kv 表按名称排序（CPU 封装 → 存储空间 → 主板 ACPI）',
        /CPU 封装[\s\S]*?存储空间 vol1 · sda[\s\S]*?主板 ACPI 温区/.test(h49)],
    ];
    renderRules49.forEach(([label, ok]) => {
      console.log((ok ? '  ✓ ' : '  ✗ ') + label);
      if (!ok) bad++;
    });
    if (!renderRules49.every(r => r[1])) {
      console.log('  ⚠️ 实际温度计顺序: ' + thermoLabels.join(' | '));
    }
  } catch (e) {
    console.error('  ❌ v1.1.49 渲染级断言抛异常: ' + e.message);
    bad++;
  }

  if (bad) {
    console.error('❌ v1.1.49 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ======================================================================
   v1.1.50 —— 能耗卡片精简 + 标题栏本月预估电费 + 每日用电失败退避
   ----------------------------------------------------------------------
   用户反馈五点：
   ① 「在能耗卡片标题栏增加根据当前的实际功耗折算本月的预估电费」
      → header 新增 #monPowerProjCost 徽标：
        预估电费 =（本月已用电 kWh + 当前实时功耗 W/1000 × 本月剩余小时数）× 电价
        ⚠️ 口径与卡片内「本月预计电费」（按历史平均 avg_w）不同 → 徽标注明来源
   ② 「去掉卡片内顶部的此刻功耗显示」
      → 左栏「实时功耗」大数字区（dnum huge）删除，
        总功耗改以部件图例下方「合计 X W」呈现（信息不丢）
   ③ 「同时去掉最右侧的卡片显示」
      → #monPowerStats 去掉 .ov-card 外壳，.stat-cells 直接作网格项
        （原 stat-cells 套在 ov-card 里 = 用户说的「多了一层卡片」）
   ④ 「对应的统计为空，需核实是否已实现；下方的每日用电卡片一直在读取，需核实」
      → 实机浏览器探针核实：数据其实**有值**（今日 0.0060kWh / 本月 0.264kWh /
        本月预计 0.16 / 全年 158.00），daily 也已渲染出 2 根柱子，
        问题在视觉嵌套不在数据；但 ensurePowerDaily/stats 请求失败时
        **不写时间戳** → 每 3 秒轮询重复打接口（用户感知为「一直在读取」）。
        修法：加 pstatsFailAt/pdailyFailAt + 30 秒冷却退避 + 失败时渲染空态
   ⑤ 「去掉下方整机功耗、累计用电卡片的显示（因为已经在抽屉中有显示了）」
      → 板块级趋势图 #monPowerCharts 传 onlyKeys=[] → 不再渲染这两张卡片
        （抽屉里的 POWER_CHARTS 不受影响，仍然保留）
   ====================================================================== */
(function () {
  let bad = 0;
  console.log('\n--- v1.1.50 能耗卡片精简 + 标题栏预估电费 + 失败退避 ---');

  const monSrc50 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'monitor.js'), 'utf8');
  const htmlSrc50 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const cssSrc50 = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  /* ⚠️ 只取 renderPowerOverview 函数体做判断 —— 注释里还留着
     fnmonitor 原始结构说明（写着「实时功耗」大数字），全文匹配会误伤。 */
  const powOverviewBody = (monSrc50.match(/function renderPowerOverview\(d\) \{[\s\S]*?\n  \}/) || [''])[0];

  const rules50 = [
    /* ① 标题栏本月预估电费徽标
       ⚠️ v1.1.56：老板按截图要求「上面两个红框中的内容去掉不显示」，
          标题栏的「本月预估电费」徽标与「硬件 RAPL」来源徽标一并删除，
          摘要行（#monPowerDek）也删除 → 下面几条断言反向校验。 */
    ['index.html 已移除 #monPowerProjCost 徽标（v1.1.56）',
      /<span class="theme-count" id="monPowerProjCost">/.test(htmlSrc50) === false],
    ['index.html 已移除 #monPowerSource 徽标（v1.1.56）',
      /id="monPowerSource"/.test(htmlSrc50) === false],
    ['monitor.js 不再计算 #monPowerProjCost（v1.1.56）',
      /var projCost = \$\('#monPowerProjCost'\)/.test(monSrc50) === false],
    ['monitor.js 不再计算 #monPowerSource（v1.1.56）',
      /var src = \$\('#monPowerSource'\)/.test(monSrc50) === false],
    ['monitor.js 不再渲染能耗摘要行 #monPowerDek（v1.1.56）',
      /var dek = \$\('#monPowerDek'\)/.test(monSrc50) === false],

    /* ② 卡片内顶部此刻功耗
       ⚠️ v1.1.54：用户两次反馈「按图（fnmonitor 截图）调整能耗卡片内的显示内容」，
          已把 v1.1.50 删掉的「实时功耗」大数字区**恢复**回 fnmonitor 的四段结构；
          同时把 v1.1.50 自加的「合计 X W」行去掉（fnmonitor 没有这行）。 */
    ['左栏「实时功耗」标题行 + 来源 pill 已恢复（v1.1.54）',
      /<div class="label">实时功耗<span class="pill"/.test(powOverviewBody)],
    ['左栏大数字区已恢复（普通文本，v1.1.55 去掉 LED 点阵）',
      /dnum huge/.test(powOverviewBody) &&
      /<span class="dm">' \+/.test(powOverviewBody) &&
      /esc\(has \? fnD1\(p\.total\)/.test(powOverviewBody) &&
      /dmDot\(/.test(powOverviewBody) === false],
    ['口径说明行已恢复（RAPL：整机 = CPU 封装 + 内存 + 主板底座 + 活动硬盘）',
      /powerBasisText\(p, has\)/.test(powOverviewBody)],
    ['v1.1.50 自加的「合计 X W」行已删除（fnmonitor 没有这行）',
      /合计 <b>/.test(powOverviewBody) === false],
    ['摘要行已删除（v1.1.56：标题下「此刻 X W，按电价 …」整行去掉）',
      /txt = '此刻 <b>/.test(powOverviewBody) === false &&
      /本月电费预计 <b>/.test(powOverviewBody) === false],

    /* ③ 去掉最右侧卡片外壳（多一层卡片） */
    ['index.html #monPowerStats 不再套 .ov-card',
      /<div class="ov-card" id="monPowerStats">/.test(htmlSrc50) === false &&
      /<div id="monPowerStats">/.test(htmlSrc50)],
    ['CSS 适配 #monPowerStats 作为 .energy 直接网格项',
      /\.energy > #monPowerStats \{ min-width: 0; \}/.test(cssSrc50)],
    ['.energy 网格 align-items:start（左卡内容变少不再强行等高）',
      /\.energy \{[^}]*align-items: start/.test(cssSrc50)],

    /* ④ 每日用电/用电统计失败退避 */
    ['声明失败时间戳 pstatsFailAt / pdailyFailAt',
      /var pstatsFailAt = 0;/.test(monSrc50) && /var pdailyFailAt = 0;/.test(monSrc50)],
    ['定义失败冷却期 PFAIL_BACKOFF = 30000',
      /var PFAIL_BACKOFF = 30000;/.test(monSrc50)],
    ['ensurePowerStats 失败后进入冷却（不每 3 秒重试）',
      /if \(pstatsFailAt && now - pstatsFailAt < PFAIL_BACKOFF\) return pstats;/.test(monSrc50)],
    ['ensurePowerDaily 失败后进入冷却',
      /if \(pdailyFailAt && now - pdailyFailAt < PFAIL_BACKOFF\) return pdaily;/.test(monSrc50)],
    ['失败时渲染空态（不再一直转圈）',
      /function renderPowerDailyEmpty\(\)/.test(monSrc50) &&
      /function renderPowerStatsEmpty\(msg\)/.test(monSrc50) &&
      /renderPowerDailyEmpty\(\);/.test(monSrc50)],
    ['成功时清零失败时间戳',
      /pstatsFailAt = 0;/.test(monSrc50) && /pdailyFailAt = 0;/.test(monSrc50)],

    /* ⑤ 去掉板块级整机功耗/累计用电卡片 */
    ['renderPanelCharts 对 #monPowerCharts 传 only:[]',
      /host: '#monPowerCharts', key: 'power', only: \[\]/.test(monSrc50)],
    ['抽屉内 POWER_CHARTS 仍保留两张图（不误删）',
      /title: '整机功耗'/.test(
        fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8')) &&
      /title: '累计用电（所选范围）'/.test(
        fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'compute.js'), 'utf8'))],

    /* ⑥ 🔴 CSS 注释闭合回归防护
       v1.1.47 遗留：软 RAID 删除说明的注释**漏了结尾的闭合符**，
       导致其后全部 CSS（含 .energy / .stat-cells / .ov-card）被吞进注释里失效 ——
       浏览器里 .stat-cells 的 display:grid 不生效，
       用户看到的「右侧统计多了一层卡片 / 统计为空」就是这个原因。
       断言：CSS 里注释的开闭数量必须相等，且每个开注释段落都有配对的闭合符。
       ⚠️ 写这类注释时切勿在内容里出现闭合符本身，否则会提前闭合注释！ */
    ['app.css 注释开闭数量相等（无未闭合注释）',
      (cssSrc50.match(/\/\*/g) || []).length === (cssSrc50.match(/\*\//g) || []).length],
    ['app.css 无嵌套未闭合注释段（每段开注释都有配对的闭合符）',
      cssSrc50.split('/*').slice(1).every(seg => seg.indexOf('*/') >= 0)],
    ['关键能耗样式规则未被注释吞掉（.energy / .stat-cells / .ov-card 均存在）',
      /\.energy \{[^}]*display: grid/.test(cssSrc50) &&
      /\.stat-cells \{[^}]*display: grid/.test(cssSrc50) &&
      /\.ov-card \{/.test(cssSrc50)],
  ];
  rules50.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---- 渲染级：主页总览 HTML ---- */
  try {
    /* ⚠️ TEMP_POWER_DATA 在另一个 IIFE 里（局部作用域），
       这里自建一份等价数据。 */
    const DATA50 = {
      success: true, available: true,
      power: {
        ok: true, source: 'rapl', total: 68.4,
        package: 42.1, core: 28.6, uncore: 6.2, dram: 7.3,
        base: 8, disksW: 13, disksActive: 3, nics: 1,
        estimation_params: { tdp_w: 65, disk_typical_w: 8, nic_fixed_w: 5, base_w: 8 },
      },
      storage: { disks: [] },
    };
    global.window.FnStyleMonitor.__renderForTest(DATA50);
    const ovHtml50 = els.monPowerOverview ? els.monPowerOverview.innerHTML : '';
    const stHtml50 = els.monPowerStats ? els.monPowerStats.innerHTML : '';
    const renderRules50 = [
      ['左栏「实时功耗」标题行 + 来源 pill（v1.1.54 恢复）',
        /实时功耗/.test(ovHtml50) && /class="pill"/.test(ovHtml50)],
      ['左栏普通文本大数字 + W 单位（v1.1.55 去掉 LED 点阵）',
        /dnum huge/.test(ovHtml50) && /dm-dot/.test(ovHtml50) === false &&
        /<span class="dm">/.test(ovHtml50) &&
        /<span class="unit">W<\/span>/.test(ovHtml50)],
      ['左栏口径说明行（RAPL 口径原文）',
        /整机 = CPU 封装（RAPL 实测）\+ 内存 \+ 主板底座 \+ 活动硬盘/.test(ovHtml50)],
      ['左栏没有 v1.1.50 自加的「合计 X W」行',
        /合计 <b>/.test(ovHtml50) === false],
      ['左栏仍有堆叠条与图例',
        /class="stack"/.test(ovHtml50) && /class="legend"/.test(ovHtml50)],
      ['右栏 .stat-cells 直接渲染（无 ov-card 外壳）',
        /<div class="stat-cells">/.test(stHtml50) &&
        /class="ov-card"/.test(stHtml50) === false],
      ['右栏四格齐全（今日/本月/本月预计/全年）',
        (stHtml50.match(/class="stat-cell"/g) || []).length === 4],
    ];
    renderRules50.forEach(([label, ok]) => {
      console.log((ok ? '  ✓ ' : '  ✗ ') + label);
      if (!ok) bad++;
    });
  } catch (e) {
    console.error('  ❌ v1.1.50 渲染级断言抛异常: ' + e.message);
    bad++;
  }

  if (bad) {
    console.error('❌ v1.1.50 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* ===========================================================================
   v1.1.51：能耗数据取不到（前端 URL 写错）回归防护
   ---------------------------------------------------------------------------
   现象：右栏四格全是「--」、标题栏「本月预估电费」也是「--」、
        每日用电显示「暂不可用」，但直接 curl 9910 端口数据完全正常。
   根因：前端 fetch 用了绝对路径 '/api/power/stats'，在 fnOS 的 iframe
        嵌入场景下会打到 fnOS 主站根路径，而不是本应用；
        且 index.cgi 的路由表里当时也没有 /api/power/* 分支。
   修法：前端一律用相对路径 BASE + 'power/...'（BASE = 'api/'）；
        index.cgi 补上 /api/power/stats 与 /api/power/daily 两条转发路由。
   =========================================================================== */
(function () {
  const src51 = fs.readFileSync(SRC, 'utf8');
  const cgi51 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'index.cgi'), 'utf8');
  let bad51 = 0;

  /* 只看真正的 fetch 调用行，避免误伤注释里的字面量 */
  const fetchLines51 = src51.split('\n').filter(l => /\bfetch\s*\(/.test(l));

  const rules51 = [
    ['monitor.js 里没有任何 fetch 绝对路径 /api/... （iframe 下会打到主站根路径）',
      fetchLines51.every(l => !/fetch\s*\(\s*['"]\/api\//.test(l))],
    ['全部 fetch 调用都基于 BASE 变量（相对路径）',
      fetchLines51.length > 0 && fetchLines51.every(l => /fetch\s*\(\s*BASE\s*\+/.test(l))],
    ['power/stats 用相对路径 BASE + power/stats',
      /fetch\s*\(\s*BASE\s*\+\s*['"]power\/stats['"]/.test(src51)],
    ['power/daily 用相对路径 BASE + power/daily',
      /fetch\s*\(\s*BASE\s*\+\s*['"]power\/daily\?days=['"] \+ PDaily_DAYS/.test(src51) ||
      /fetch\s*\(\s*BASE\s*\+\s*['"]power\/daily\?days=30['"]/.test(src51)],
    ['index.cgi 有 /api/power/stats 路由（转发 9910）',
      /\/api\/power\/stats\)/.test(cgi51) &&
      /127\.0\.0\.1:9910\/api\/power\/stats/.test(cgi51)],
    ['index.cgi 有 /api/power/daily 路由（转发 9910）',
      /\/api\/power\/daily\)/.test(cgi51) &&
      /127\.0\.0\.1:9910\/api\/power\/daily/.test(cgi51)],
    ['index.cgi 的 power 路由放在 case 分派里（不是注释）',
      /case "\$REL" in[\s\S]*?\/api\/power\/stats\)[\s\S]*?\/api\/power\/daily\)[\s\S]*?esac/.test(cgi51)],
    ['power 路由转发失败时返回 503 而不是静默空',
      (cgi51.match(/503 Service Unavailable/g) || []).length >= 2],
    ['monitor.js 仍保留失败退避（不会每 3 秒重复打接口）',
      /PFAIL_BACKOFF/.test(src51) && /pstatsFailAt/.test(src51) && /pdailyFailAt/.test(src51)],
    ['右栏四格在 pstats 为 null 时仍回退到 -- （不会渲染 NaN）',
      /esc\(fnKwh\(pstats && pstats\.today_kwh\)\)/.test(src51)],
  ];

  rules51.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad51++;
  });

  if (bad51) {
    console.error('❌ v1.1.51 断言失败 ' + bad51 + ' 项');
    process.exitCode = 1;
  }
})();

/* ===========================================================================
   v1.1.53：能耗采集逻辑与界面呈现对齐 fnmonitor
   ---------------------------------------------------------------------------
   背景：用户对比 FnStyle 与 fnmonitor 的能耗截图，发现内容不一致 ——
   fnmonitor 能显示最近 6 天能耗，FnStyle 只有 2 天。

   实机核查结论（fnmonitor monitor.db vs FnStyle storage.db）：
     · fnmonitor 采集起点 11-15 13:04，FnStyle 起点 11-19 15:21 → **差 4 天**；
     · 同一天数值换算后一致（11-19: 0.6926/24h vs 0.2575/8.7h ≈ 0.255 ✓）；
     · 采样间隔都是 60s、保留期都是 7 天；
     · **核心缺口**：fnmonitor 有 daily_summary 归档表 + meta.daily_archived_until
       水位线（原始点删了、每日汇总永久保留 400 天），FnStyle 完全没有。

   本次改动：
     ① 采集：新增 daily_summary 归档 + 水位线；cleanup 改为「先归档再删」；
        积分口径由梯形积分改为 fnmonitor 的「瞬时值 × 中位间隔 med，
        断档 > 3×med 按 med 计」。
     ② 接口：无数据天返回 kwh=null + cost=null + power_h=null（不是 0），
        补 avg_w 字段，days 恒为请求天数。
     ③ 界面：回调 v1.1.52 的两处偏差 —— 标题改回动态「近 N 天用电」
        （N = 实际有数据的天数），无数据天不画柱（从第一个有数据天起 slice）。
   =========================================================================== */
(function () {
  const src53 = fs.readFileSync(SRC, 'utf8');
  const hist53 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'history_collect.py'), 'utf8');
  const srv53 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'fnstyle_server.py'), 'utf8');
  const st53 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'storage_collect.py'), 'utf8');
  const css53 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  let bad53 = 0;

  const rules53 = [
    /* ---- 采集逻辑：归档机制 ---- */
    ['storage_collect 建了 daily_summary 表',
      /CREATE TABLE IF NOT EXISTS daily_summary/.test(st53)],
    ['storage_collect 建了 meta 表（水位线存储）',
      /CREATE TABLE IF NOT EXISTS meta/.test(st53)],
    ['有 _daily_watermark（读 meta.daily_archived_until 水位线）',
      /def _daily_watermark/.test(st53) && /daily_archived_until/.test(st53)],
    ['有 _daily_aggregate（按天汇总 metrics）',
      /def _daily_aggregate/.test(st53)],
    ['有 archive_daily（归档并推进水位线）',
      /def archive_daily/.test(st53)],
    ['有 daily_rows（归档部分 + 水位线后的实时尾段）',
      /def daily_rows/.test(st53)],
    ['归档常量 DAILY_KEEP_DAYS=400 / DAILY_CHUNK=7天',
      /DAILY_KEEP_DAYS\s*=\s*400/.test(st53) && /DAILY_CHUNK\s*=\s*7\s*\*\s*86400/.test(st53)],
    ['cleanup 先归档再删（否则原始点删了就永久没了）',
      /def cleanup[\s\S]{0,900}?archive_daily[\s\S]{0,900}?DELETE FROM metrics/.test(st53)],
    ['daily_rows 里用了「已归档 + 实时尾段」两段式',
      /FROM daily_summary WHERE day >= \?/.test(st53) &&
      /_daily_aggregate\(conn, tail_start/.test(st53)],
    /* ---- 采集逻辑：积分口径对齐 fnmonitor ---- */
    ['聚合用「瞬时值 × dt / 3600000」（fnmonitor 口径）',
      /3600000\.0/.test(st53) && /\/ 3600000/.test(st53)],
    ['聚合用中位间隔 med + 断档 3 倍上限',
      /med = dts\[len\(dts\) \/\/ 2\]/.test(st53) && /cap = 3\.0 \* max/.test(st53)],
    ['history_collect 的 energy_stats 也改成中位间隔口径',
      /med = dts\[len\(dts\) \/\/ 2\]/.test(hist53) && /3600000\.0/.test(hist53)],
    ['已废弃梯形积分 ((v0+v1)/2 × dt / 3.6e6)',
      !/\(float\(v0\) \+ float\(v1\)\) \/ 2\.0/.test(hist53)],
    /* ---- 接口返回格式 ---- */
    ['daily_energy 改用 store.daily_rows（不再是直接扫 metrics）',
      /store\.daily_rows/.test(hist53)],
    ['无数据天 kwh=None（不是 0）',
      /"kwh":\s*round\(kwh, 4\) if kwh is not None else None/.test(hist53)],
    ['无数据天 cost=None、power_h=None',
      /"cost":\s*round\(kwh \* rate, 2\) if kwh is not None else None/.test(hist53) &&
      /"power_h":\s*round\(power_s \/ 3600\.0, 2\) if power_s is not None else None/.test(hist53)],
    ['返回 avg_w 字段（当天平均功率）',
      /"avg_w":/.test(hist53)],
    ['days 恒为请求天数（用 range 生成完整序列）',
      /for i in range\(n - 1, -1, -1\)/.test(hist53)],
    ['已删除 _fill_missing_days（补齐逻辑不再需要）',
      !/def _fill_missing_days/.test(hist53)],
    ['服务端不再传 fill_days',
      !/daily_energy\([^)]*fill_days/.test(srv53)],
    ['服务端 days 上限 400（对齐 DAILY_KEEP_DAYS）',
      /min\(400, int\(kv\[5:\]\)\)/.test(srv53)],
    /* ---- 界面呈现对齐 ---- */
    ['PDaily_DAYS = 30（对齐 fnmonitor 的 DAY_BARS）',
      /var\s+PDaily_DAYS\s*=\s*30\b/.test(src53)],
    ['请求用 days=PDaily_DAYS 变量',
      /power\/daily\?days=['"] \+ PDaily_DAYS/.test(src53)],
    ['标题动态化：近 N 天用电（N = 实际有数据的天数）',
      /近 ' \+ (days\.length|n) \+ ' 天用电/.test(src53)],
    ['空态标题是「近 30 天用电」（对齐 fnmonitor）',
      /近 30 天用电/.test(src53)],
    ['无数据天不画柱：从第一个有数据的天起 slice',
      /all\.slice\(first\)/.test(src53) &&
      /all\[fi\]\.has_data && all\[fi\]\.kwh !== null/.test(src53)],
    ['图例写「每日 kWh · 功率历史积分」（有 daily 源时）',
      /每日 kWh · 功率历史积分/.test(src53)],
    ['已删除 nodata 列与 CSS 规则（v1.1.52 的占位样式）',
      !/\bnodata\b/.test(src53) &&
      !/\.ov-bar-col\.nodata\s*\{/.test(css53)],
    ['avg_power 仍用 kwh is not None + power_h>0 过滤空天',
      /d\.get\("kwh"\) is not None and \(d\.get\("power_h"\) or 0\) > 0/.test(hist53)],
  ];

  rules53.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad53++;
  });

  if (bad53) {
    console.error('❌ v1.1.53 断言失败 ' + bad53 + ' 项');
    process.exitCode = 1;
  }
})();

/* ===========================================================================
   v1.1.54：能耗卡片按 fnmonitor 截图 1:1 还原（内容 + 布局）
   ---------------------------------------------------------------------------
   背景：用户两次反馈「请看图，按图上的内容布局调整能耗卡片内的显示内容，
   这个已经给你说了两次了你还是没有按要求完成」——附图是 fnmonitor 能耗板块截图。
   v1.1.53 只对齐了数据口径，界面呈现仍差一档，本次逐项还原。

   还原基准（fnmonitor 上游源码）：
     · ui-src/43-modules.js  energy 模块的 build()/update()  → 左栏四段 +
       右栏四格 + 摘要行 + 柱状图容器
     · ui-src/32-bars.js      BarChart 类                     → SVG 柱状图
     · ui-src/30-core.js      Dot 类 + GLYPH_SRC + F           → 点阵数字 + 数值口径
     · ui-src/10-style.css    .dnum/.stat-cells/.bars/.tip    → 样式

   逐项：
     ① 左栏恢复四段结构（标题行 + 来源 pill / 点阵大数字 + W / 口径说明行 /
       堆叠条 + 图例），并删掉 v1.1.50 自加的「合计 X W」行（fnmonitor 没有）
     ② 大数字改 5×7 LED 点阵（新增 DOT_RAW 字库 + dotSvg + dmDot）
        ⚠️ v1.1.55：用户要求不用点阵 → 整套点阵代码已移除，改回普通文本
     ③ 右栏四格数字改点阵 + fnmonitor 数值口径（金额带 ¥、精度规则）
     ④ 摘要行改 fnmonitor 原文口径「此刻 X W，按电价 …，本月电费预计 ¥…」
     ⑤ 每日用电柱状图由 div 实现换成 SVG（网格线 / 圆角 / dim / live-pt / 浮层）
     ⑥ 主题补 --e-teal（深 #1F9C86 / 浅 #1E9E8A）
   =========================================================================== */
(function () {
  const src54 = fs.readFileSync(SRC, 'utf8');
  const css54 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  const html54 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const cgi54 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'index.cgi'), 'utf8');
  const man54 = fs.readFileSync(path.join(__dirname, '..', 'manifest'), 'utf8');
  const powOverview54 = (src54.match(/function renderPowerOverview\(d\) \{[\s\S]*?\n  \}/) || [''])[0];
  let bad54 = 0;

  console.log('\n--- v1.1.54 能耗卡片按 fnmonitor 截图 1:1 还原 ---');

  const rules54 = [
    /* ---- ① 数字样式 ----
       ⚠️ v1.1.55：用户要求「数字不使用 LED 点阵，调整为与其他的数字一样的样式」
          → v1.1.54 引入的 5×7 LED 点阵整套移除，恢复普通文本。 */
    ['已移除 5×7 LED 字库（DOT_RAW）',
      /var DOT_RAW = \{/.test(src54) === false],
    ['已移除 dotSvg / dmDot / dotGlyph 点阵渲染器',
      /function dotSvg\(/.test(src54) === false &&
      /function dmDot\(/.test(src54) === false &&
      /function dotGlyph\(/.test(src54) === false],
    ['已移除 CSS 的 .dm-dot 规则',
      /\.dm-dot/.test(css54) === false],
    ['已移除主题变量 --dm-off',
      /--dm-off/.test(css54) === false],
    ['数字用普通文本 .dnum > .dm（与其它板块同款）',
      /\.dnum > \.dm \{[^}]*font: 700 calc\(var\(--dm-h\) \* 1\.08\) \/ \.8 var\(--font-display\)/.test(css54)],

    /* ---- ② 左栏四段结构 ---- */
    ['左栏「实时功耗」标题行 + 来源 pill',
      /<div class="label">实时功耗<span class="pill" style="margin-left:auto">/.test(src54)],
    ['左栏点阵大数字 + W 单位（margin-top:18px）',
      /<div class="dnum huge" style="margin-top:18px">/.test(src54) &&
      /<span class="unit">W<\/span>/.test(src54)],
    ['左栏口径说明行（RAPL 原文）',
      /整机 = CPU 封装（RAPL 实测）\+ 内存 \+ 主板底座 \+ 活动硬盘/.test(src54)],
    ['iDRAC 口径带电源容量与近 1 分钟区间（fnmonitor 同款）',
      /iDRAC 实测整机输出/.test(src54) && /电源容量/.test(src54) &&
      /近 1 分钟/.test(src54)],
    ['堆叠条 12px 高（fnmonitor）',
      /class="stack" style="margin-top:22px;height:12px"/.test(src54)],
    ['删掉了 v1.1.50 自加的「合计 X W」行',
      !/合计 <b>/.test(powOverview54)],

    /* ---- ③ 右栏四格：点阵 + fnmonitor 数值口径 ---- */
    ['四格数字全部是普通文本 .dm（v1.1.55 去掉点阵）',
      (src54.match(/esc\(fn/g) || []).length >= 4 &&
      /dmDot\(fn/.test(src54) === false],
    ['金额带 ¥ 前缀（fnmonitor 的 F.yuan）',
      /return '¥' \+ \(n >= 100 \? String\(Math\.round\(n\)\) : n\.toFixed\(2\)\);/.test(src54)],
    ['kWh 精度规则：≥100 取整 / ≥10 一位 / 否则两位',
      /return n >= 100 \? String\(Math\.round\(n\)\) : \(n >= 10 \? n\.toFixed\(1\) : n\.toFixed\(2\)\);/.test(src54)],
    ['d1 精度规则：≥100 取整，否则一位',
      /return Math\.abs\(n\) >= 100 \? String\(Math\.round\(n\)\) : n\.toFixed\(1\);/.test(src54)],
    ['全年估算保留「估算」pill',
      /全年估算<span class="pill est-pill">估算<\/span>/.test(src54)],
    ['副标题口径「约 X kWh · 剩余天数按近 N 天平均 W」',
      /fnKwh\(pstats && pstats\.month_proj_kwh\) \+ ' kWh · 剩余天数按/.test(src54)],

    /* ---- ④ 摘要行（fnmonitor 的 m.r.dek）----
       ⚠️ v1.1.56：摘要行已按用户要求删除 → 反向断言。 */
    ['摘要行已删除（v1.1.56：标题下整行去掉）',
      /txt = '此刻 <b>' \+ fnD1\(p\.total\) \+ ' W<\/b>'/.test(src54) === false &&
      /本月电费预计 <b>' \+ fnYuan\(pstats\.month_proj_yuan\)/.test(src54) === false],

    /* ---- ⑤ 柱状图 SVG（fnmonitor 的 BarChart）---- */
    ['有 drawPowerBars（SVG 绘制）',
      /function drawPowerBars\(\)/.test(src54)],
    ['图表高度 132px（fnmonitor BarChart height）',
      /var h = 132;/.test(src54)],
    ['两条水平虚线网格（0.5 / 1.0）',
      /\[0\.5, 1\]\.forEach\(function \(f\)/.test(src54) &&
      /class="grid-l"/.test(src54)],
    ['柱宽 = clamp(slot-3, 3, 22)（fnmonitor 公式）',
      /var bw = Math\.max\(3, Math\.min\(22, slot - 3\)\);/.test(src54)],
    ['柱高按 max × 1.08（fnmonitor）',
      /max = max \* 1\.08 \|\| 1e-9;/.test(src54)],
    ['柱子是圆角矩形（rx）',
      /class="b"[\s\S]{0,260}?" rx="' \+ Math\.min\(3, bw \/ 2\)/.test(src54)],
    ['历史柱 fill-opacity .42（fnmonitor dim）',
      /r\.dim \? ' style="fill-opacity:\.42"' : ''/.test(src54)],
    ['今天柱顶小圆点 live-pt（r=2.5）',
      /class="live-pt"[\s\S]{0,120}?r="2\.5"/.test(src54)],
    ['X 轴标签用 SVG text（末位固定「今天」）',
      /class="axis-t"[\s\S]{0,160}?text-anchor="middle"/.test(src54) &&
      /\? '今天'/.test(src54)],
    ['逐柱悬浮浮层（.pwr-tip，含 tt / tr）',
      /class="pwr-tip"/.test(src54) && /<div class="tt">/.test(src54) &&
      /<div class="tr">/.test(src54)],
    ['浮层内容「用电 x kWh」',
      /<i class="kb0"><\/i>用电<\/span><b>/.test(src54)],
    ['浮层标题带电费与数据时长',
      /title \+= ' · ' \+ fnYuan\(d\.cost\)/.test(src54) &&
      /title \+= ' · 数据 ' \+ fnD1\(d\.power_h\) \+ ' 小时'/.test(src54)],
    ['悬浮事件用 pointermove + closest(\'[data-i]\') 委托',
      /addEventListener\('pointermove'/.test(src54) &&
      /closest\('\[data-i\]'\)/.test(src54)],
    ['已删除 v1.1.53 的 div 实现',
      !/class="ov-bars"/.test(src54) && !/\.ov-bars \{/.test(css54) &&
      !/class="ov-bar-col"/.test(src54)],

    /* ---- ⑥ 主题色与样式 ---- */
    ['新增 --e-teal（深 #1F9C86 / 浅 #1E9E8A）',
      (css54.match(/--e-teal:/g) || []).length === 2 &&
      /--e-teal:\s*#1F9C86/.test(css54) && /--e-teal:\s*#1E9E8A/.test(css54)],
    ['柱子与图例色块用 teal',
      /\.pwr-bars rect\.b \{[^}]*fill: var\(--e-teal\)/.test(css54) &&
      /\.chart-legend \.lk i \{[^}]*background: var\(--e-teal\)/.test(css54)],
    ['柱状图容器 132px',
      /\.pwr-bars \{[^}]*height: 132px/.test(css54)],
    ['虚线网格样式（1 4 虚线）',
      /\.pwr-bars \.grid-l \{[^}]*stroke-dasharray: 1 4/.test(css54)],
    ['浮层黑底白字（fnmonitor 的 .tip）',
      /\.pwr-tip \{[^}]*background: #0b0e14/.test(css54)],
    ['堆叠条高度 12px（CSS 层对齐 fnmonitor）',
      /\.stack \{[^}]*height: 12px/.test(css54)],

    /* ---- ⑦ 版本号（版本无关：四处格式合法且一致，不写死具体号）---- */
    ['manifest 版本号格式合法', /version\s+= \d+\.\d+\.\d+/.test(man54)],
    ['index.cgi 版本号格式合法', /APP_VERSION="\d+\.\d+\.\d+"/.test(cgi54)],
    ['index.html 版本号格式合法', /id="verPill"[^>]*>v\d+\.\d+\.\d+</.test(html54)],
    ['app.js 版本号格式合法',
      /var APP_VERSION = '\d+\.\d+\.\d+'/.test(
        fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'app.js'), 'utf8'))],
    ['四处版本号彼此一致', (function () {
      const pick = (re, t) => { const m = t.match(re); return m ? m[1] : null; };
      const vMan = pick(/version\s+= (\d+\.\d+\.\d+)/, man54);
      const vCgi = pick(/APP_VERSION="(\d+\.\d+\.\d+)"/, cgi54);
      const vHtml = pick(/id="verPill"[^>]*>v(\d+\.\d+\.\d+)</, html54);
      const vApp = pick(/var APP_VERSION = '(\d+\.\d+\.\d+)'/,
        fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'static', 'js', 'app.js'), 'utf8'));
      return !!(vMan && vCgi && vHtml && vApp
        && vMan === vCgi && vCgi === vHtml && vHtml === vApp);
    })()],
  ];

  rules54.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad54++;
  });

  /* ---- 渲染级：主页总览 HTML ---- */
  try {
    const DATA54 = {
      success: true, available: true,
      power: {
        ok: true, source: 'rapl', total: 29.2,
        package: 2.4, core: 1.8, uncore: 0.6, dram: 0.3,
        base: 8, disksW: 18.5, disksActive: 3, nics: 1,
      },
      storage: { disks: [] },
    };
    global.window.FnStyleMonitor.__renderForTest(DATA54);
    const ov54 = els.monPowerOverview ? els.monPowerOverview.innerHTML : '';
    const st54 = els.monPowerStats ? els.monPowerStats.innerHTML : '';
    const renderRules54 = [
      ['左栏渲染出普通文本大数字（.dm）+ W 单位（v1.1.55 去掉点阵）',
        /dnum huge/.test(ov54) && /dm-dot/.test(ov54) === false &&
        /<span class="dm">/.test(ov54) &&
        /<span class="unit">W<\/span>/.test(ov54)],
      ['左栏渲染出来源 pill（硬件 RAPL）',
        /class="pill"[^>]*>硬件 RAPL</.test(ov54)],
      ['左栏口径行渲染出 RAPL 口径原文',
        /整机 = CPU 封装（RAPL 实测）\+ 内存 \+ 主板底座 \+ 活动硬盘/.test(ov54)],
      ['左栏有堆叠条与图例',
        /class="stack"/.test(ov54) && /class="legend"/.test(ov54)],
      ['左栏没有「合计 X W」行',
        !/合计 <b>/.test(ov54)],
      ['右栏四格全部是普通文本（无 .dm-dot，v1.1.55）',
        (st54.match(/dm-dot/g) || []).length === 0 &&
        (st54.match(/<span class="dm">/g) || []).length === 4],
      ['右栏四格齐全（今日/本月/本月预计/全年）',
        (st54.match(/class="stat-cell"/g) || []).length === 4],
    ];
    renderRules54.forEach(([label, ok]) => {
      console.log((ok ? '  ✓ ' : '  ✗ ') + label);
      if (!ok) bad54++;
    });
  } catch (e) {
    console.error('  ❌ v1.1.54 渲染级断言抛异常: ' + e.message);
    bad54++;
  }

  if (bad54) {
    console.error('❌ v1.1.54 断言失败 ' + bad54 + ' 项');
    process.exitCode = 1;
  }
})();

/* ---------------------------------------------------------------------------
   v1.1.55：能耗卡片数字去 LED 点阵 + 三个「本月预估电费」口径统一
   ---------------------------------------------------------------------------
   老板反馈两条：
     ① 「数字不使用 LED 点阵，调整为与其他的数字一样的样式。」
        → v1.1.54 按 fnmonitor 截图把大数字做成了 5×7 LED 点阵，
          本次整套点阵代码移除（DOT_RAW 字库 / dotSvg / dmDot / .dm-dot CSS /
          --dm-off 变量），改回普通文本 .dnum > .dm，与其它板块完全同款。
     ② 「如图上的 3 个本月预估电费都不一样呢，请核实」
        → 根因是后端 energy_stats 的**单位 bug**：
          fnmonitor 的 monthProj 写 `mk + a.w * (end - d) / 3.6e9`，
          其中 (end - d) 是**毫秒**差；本实现把剩余时长换成了**秒**，
          却沿用 3.6e9 → 结果小了 1000 倍，
          month_proj_kwh ≈ month_kwh（实机 0.5388 vs 0.5317），
          于是「本月预计电费」几乎等于「本月已用电费」。
          另外标题栏徽标还用「此刻实时功耗」单独算了一套，
          与卡内「历史平均功耗」口径不同 → 同屏三个数互相矛盾。
        修复：① 后端秒口径改用 3.6e6，并把剩余时长精确到「下月 1 日 00:00」
              （与 fnmonitor 的 new Date(y, m+1, 1) 一致）；
              ② 前端标题栏徽标改为直接取后端 month_proj_yuan，
                 与卡内「本月预计电费」格、摘要行「本月电费预计」**逐字相同**；
                 当前实时功耗折算值降级为 hover 提示里的参考值。
   =========================================================================== */
(function () {
  const src55 = fs.readFileSync(SRC, 'utf8');
  const css55 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  const hist55 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'history_collect.py'), 'utf8');
  const powOverview55 = (src55.match(/function renderPowerOverview\(d\) \{[\s\S]*?\n  \}/) || [''])[0];
  let bad55 = 0;

  console.log('\n--- v1.1.55 去 LED 点阵 + 三个本月预估电费口径统一 ---');

  const rules55 = [
    /* ---- ① 去 LED 点阵 ---- */
    ['JS 已无 DOT_RAW 字库',
      /DOT_RAW/.test(src55) === false],
    ['JS 已无 dotSvg / dmDot / dotGlyph',
      /dotSvg/.test(src55) === false &&
      /dmDot/.test(src55) === false &&
      /dotGlyph/.test(src55) === false],
    ['CSS 已无 .dm-dot 规则',
      /dm-dot/.test(css55) === false],
    ['CSS 已无 --dm-off 变量',
      /--dm-off/.test(css55) === false],
    ['数字使用普通文本 .dnum > .dm（与其它板块同款）',
      /\.dnum > \.dm \{[^}]*font: 700 calc\(var\(--dm-h\) \* 1\.08\) \/ \.8 var\(--font-display\)/.test(css55)],
    ['左栏大数字是普通文本',
      /<span class="dm">' \+\s*\n?\s*esc\(has \? fnD1\(p\.total\)/.test(powOverview55)],
    ['左栏大数字无点阵',
      /dmDot\(/.test(powOverview55) === false],

    /* ---- ② 后端单位 bug 修复 ---- */
    ['后端 month_proj 用秒口径 3.6e6（不再用 3.6e9）',
      /result\["month_kwh"\] \+ w \* remain_secs \/ 3\.6e6/.test(hist55)],
    ['后端已无 3.6e9 的月预测除数',
      /remain_secs \/ 3\.6e9/.test(hist55) === false],
    ['剩余时长精确到「下月 1 日 00:00」（与 fnmonitor 一致）',
      /end_dt = datetime\.datetime\(now_dt\.year, now_dt\.month \+ 1, 1\)/.test(hist55) &&
      /datetime\.datetime\(now_dt\.year \+ 1, 1, 1\)/.test(hist55)],
    ['剩余时长用 total_seconds() 精确到秒',
      /remain_secs = max\(0\.0, \(end_dt - now_dt\)\.total_seconds\(\)\)/.test(hist55)],
    ['month_proj_remain_days 改为小数天（精确剩余）',
      /month_proj_remain_days.*remain_secs \/ 86400\.0/.test(hist55)],
    /* ⚠️ v1.1.62：month_proj_yuan 不再用 eff（均价）倒推，
       改用 proj_rate（预计电费单价，默认成都最低档 0.5224）。
       ⚠️ 注意：实际代码里这两行是**跨行**的，正则要用 [\s\S] 匹配。 */
    ['month_proj_yuan = month_proj_kwh × proj_rate（v1.1.62 不再用 eff 倒推）',
      /month_proj_yuan[\s\S]{0,80}month_proj_kwh[\s\S]{0,40}proj_rate/.test(hist55)],
    ['month_proj_yuan 不再用 eff 倒推',
      /month_proj_yuan[\s\S]{0,80}month_proj_kwh[\s\S]{0,40}\* eff/.test(hist55) === false],
    ['history_collect 输出 proj_rate_yuan_per_kwh',
      /proj_rate_yuan_per_kwh/.test(hist55)],

    /* ---- ③ 电费口径统一 ----
       ⚠️ v1.1.56：标题栏徽标与摘要行已按用户要求删除，
          「本月预计电费」只剩卡内一格 → 断言改为「卡内取 month_proj_yuan，
          标题栏/摘要行不再渲染」。 */
    ['标题栏徽标不再渲染（v1.1.56 已删）',
      /projCost\.textContent = '本月预估电费 /.test(powOverview55) === false],
    ['标题栏不再单独算 projYuan（消除口径分叉）',
      /var projYuan = projKwh \* rateY;/.test(powOverview55) === false],
    ['当前实时功耗折算值不再单独出现（v1.1.56 整段删除）',
      /按当前实时功耗/.test(powOverview55) === false],
    ['卡内「本月预计电费」格取 month_proj_yuan',
      /fnYuan\(pstats && pstats\.month_proj_yuan\)/.test(powOverview55)],
    ['摘要行「本月电费预计」不再渲染（v1.1.56 已删）',
      /本月电费预计 <b>' \+ fnYuan\(pstats\.month_proj_yuan\)/.test(powOverview55) === false],
    ['「已用电费」与「本月预计电费」标签区分（避免误读为同类）',
      /已计电费 /.test(powOverview55)],
    ['右栏两格加 title 说明口径（已用 vs 预计）',
      /title="本月已用电量 × 电价/.test(powOverview55) &&
      /title="本月已用 \+ 剩余天数按平均功耗推算"/.test(powOverview55)],
  ];
  rules55.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad55++;
  });

  /* ---- 渲染级：电价说明下沉到「本月预计电费」格（v1.1.56） ---- */
  try {
    const DATA55 = {
      power: {
        ok: true, total: 30.2, source: 'rapl',
        cpu_pkg: 2.9, mem: 0.8, disks: 18.5, base: 8.0,
      },
      storage: { disks: [] },
    };
    /* 造一组 stats：三处都用同一个 month_proj_yuan */
    global.window.FnStyleMonitor.__setPowerStatsForTest({
      today_kwh: 0.2801, today_yuan: 0.17,
      month_kwh: 0.5317, month_yuan: 0.32,
      month_proj_kwh: 8.0933, month_proj_yuan: 4.87,
      year_proj_kwh: 260.4, year_proj_yuan: 156.72,
      avg_w: 29.7, avg_basis: '近 2 天平均',
      rate_yuan_per_kwh: 0.6,
      eff_rate_yuan_per_kwh: 0.6,
    });
    global.window.FnStyleMonitor.__renderForTest(DATA55);
    const ov55 = els.monPowerOverview ? els.monPowerOverview.innerHTML : '';
    const st55 = els.monPowerStats ? els.monPowerStats.innerHTML : '';

    const renderRules55 = [
      ['左栏渲染出普通文本数字（无 .dm-dot）',
        /<span class="dm">/.test(ov55) && /dm-dot/.test(ov55) === false],
      ['右栏四格数字全是普通文本（4 个 .dm，无 .dm-dot）',
        (st55.match(/<span class="dm">/g) || []).length === 4 &&
        /dm-dot/.test(st55) === false],
      ['右栏「本月预计电费」渲染为 ¥4.87',
        /本月预计电费<\/div><div class="dnum"><span class="dm">¥4\.87/.test(st55)],
      ['「本月预计电费」格内新增电价说明（sub-rate）',
        /class="sub sub-rate"/.test(st55) &&
        /<span class="rate-lbl">电价<\/span>/.test(st55)],
      ['电价说明显示「0.6 元/kWh」',
        /0\.6 元\/kWh/.test(st55)],
      ['「本月用电」格标为「已计电费」（与预计区分）',
        /已计电费 ¥0\.32/.test(st55)],
      ['全页不再出现「本月预估电费」徽标文案（v1.1.56 已删）',
        /本月预估电费/.test(st55) === false &&
        /本月预估电费/.test(ov55) === false],
      ['不再出现「此刻 X W」摘要行文案（v1.1.56 已删）',
        /此刻 <b>/.test(st55) === false && /此刻 <b>/.test(ov55) === false],
    ];
    renderRules55.forEach(([label, ok]) => {
      console.log((ok ? '  ✓ ' : '  ✗ ') + label);
      if (!ok) bad55++;
    });
  } catch (e) {
    console.error('  ❌ v1.1.55 渲染级断言抛异常: ' + e.message);
    bad55++;
  }

  if (bad55) {
    console.error('❌ v1.1.55 断言失败 ' + bad55 + ' 项');
    process.exitCode = 1;
  }
})();

/* ---------------------------------------------------------------------------
   v1.1.56：能耗标题栏去徽标 + 去摘要行 + 「本月预计电费」格新增电价说明
   ---------------------------------------------------------------------------
   老板截图反馈三处：
     ① 标题栏右上两个徽标（`硬件 RAPL` + `本月预估电费 ¥4.90`）→ 去掉不显示
     ② 标题下的摘要行（`此刻 30.7 W，按电价 0.6 元/kWh，本月电费预计 ¥4.90。`）
        → 去掉不显示
     ③ 右下「本月预计电费」格里 → 新增电价的说明
   实现：
     · index.html 删除 #monPowerSource / #monPowerProjCost / #monPowerDek
     · monitor.js 删除对应的三段渲染代码
     · 「本月预计电费」格内追加 .sub.sub-rate 行：
       「电价 0.6 元/kWh」（口径取后端 eff_rate_yuan_per_kwh = 本月实际均价，
        与 fnmonitor 的 eff = 本月电费 / 本月用电 同源）
   =========================================================================== */
(function () {
  const src56 = fs.readFileSync(SRC, 'utf8');
  const html56 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'index.html'), 'utf8');
  const css56 = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'ui', 'static', 'css', 'app.css'), 'utf8');
  let bad56 = 0;

  console.log('\n--- v1.1.56 能耗标题栏去徽标/摘要行 + 电价说明下沉 ---');

  const rules56 = [
    /* ---- ① 标题栏两个徽标已删 ---- */
    ['index.html 已无 #monPowerSource 徽标',
      /id="monPowerSource"/.test(html56) === false],
    ['index.html 已无 #monPowerProjCost 徽标',
      /id="monPowerProjCost"/.test(html56) === false],
    ['monitor.js 已无 #monPowerSource 赋值',
      /monPowerSource/.test(src56) === false],
    ['monitor.js 已无 #monPowerProjCost 赋值',
      /monPowerProjCost/.test(src56) === false],
    /* ---- ② 摘要行已删 ---- */
    ['index.html 已无 #monPowerDek 摘要行',
      /id="monPowerDek"/.test(html56) === false],
    ['monitor.js 已无 #monPowerDek 渲染代码（注释里的历史说明不算）',
      /var dek = \$\('#monPowerDek'\)/.test(src56) === false &&
      /dek\.innerHTML = txt;/.test(src56) === false],
    ['monitor.js 已无「此刻 <b>… W</b>」摘要文案',
      /此刻 <b>/.test(src56) === false],
    /* ---- ③ 「本月预计电费」格新增电价说明 ---- */
    ['「本月预计电费」格内新增 .sub-rate 电价行',
      /class="sub sub-rate"/.test(src56)],
    ['电价行带「电价」标签',
      /<span class="rate-lbl">电价<\/span>/.test(src56)],
    ['电价行显示「元/kWh」',
      /元\/kWh/.test(src56)],
    ['电价口径优先取后端 eff_rate_yuan_per_kwh（本月实际均价）',
      /eff_rate_yuan_per_kwh/.test(src56)],
    ['CSS 有 .stat-cell .sub-rate 样式',
      /\.stat-cell \.sub-rate \{/.test(css56)],
    ['CSS 有 .sub-rate .rate-lbl 标签样式',
      /\.stat-cell \.sub-rate \.rate-lbl \{/.test(css56)],
    /* ---- ④ 版本号（版本无关：格式合法，不写死具体号）---- */
    ['manifest 版本号格式合法',
      /version\s+= \d+\.\d+\.\d+/.test(
        fs.readFileSync(path.join(__dirname, '..', 'manifest'), 'utf8'))],
  ];

  rules56.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad56++;
  });

  if (bad56) {
    console.error('❌ v1.1.56 断言失败 ' + bad56 + ' 项');
    process.exitCode = 1;
  }
})();

/* ===========================================================================
   v1.1.57：阶梯电价 + 分时电价
   =========================================================================== */
(function () {
  const UI = path.join(__dirname, '..', 'app', 'ui');
  const src = fs.readFileSync(path.join(UI, 'static', 'js', 'monitor.js'), 'utf8');
  const comp = fs.readFileSync(path.join(UI, 'static', 'js', 'compute.js'), 'utf8');
  const css = fs.readFileSync(path.join(UI, 'static', 'css', 'app.css'), 'utf8');
  const srv = fs.readFileSync(path.join(UI, 'fnstyle_server.py'), 'utf8');
  const hist = fs.readFileSync(path.join(UI, 'history_collect.py'), 'utf8');
  const tar = fs.readFileSync(path.join(UI, 'power_tariff.py'), 'utf8');
  const man = fs.readFileSync(path.join(__dirname, '..', 'manifest'), 'utf8');
  let bad = 0;

  console.log('\n--- v1.1.57 阶梯电价 + 分时电价 ---');

  const rules = [
    /* ---- ① 后端模块 ---- */
    ['存在 power_tariff.py 模块', tar.length > 0],
    ['内置四川电价表（0.5224/0.6224/0.8224）',
      /0\.5224/.test(tar) && /0\.6224/.test(tar) && /0\.8224/.test(tar)],
    ['内置四川低谷价（丰水 0.175 / 枯平 0.2535）',
      /0\.1750/.test(tar) && /0\.2535/.test(tar)],
    ['四川夏季阶梯 260/460、非夏季 180/280',
      /tiers_summer"\s*:\s*\[260\.0, 460\.0, None\]/.test(tar) &&
      /tiers_other"\s*:\s*\[180\.0, 280\.0, None\]/.test(tar)],
    ['四川标记 has_time_of_use=True', /"has_time_of_use": True/.test(tar)],
    ['天津标记 has_time_of_use=False（居民不执行分时）',
      /"has_time_of_use": False/.test(tar)],
    ['阶梯按月总电量判定（官方口径，v1.1.59）',
      /档位按\*\*月总电量\*\*判定/.test(tar)],
    ['低谷电量计入阶梯总电量',
      /低谷电量计入阶梯总电量/.test(tar)],
    ['阶梯结转规则起始 2027-01', /"2027-01"/.test(tar)],
    ['丰水期月份 6-10', /"wet_months": \[6, 7, 8, 9, 10\]/.test(tar)],
    /* ---- ② 后端接线 ---- */
    ['history_collect 引入 power_tariff',
      /import power_tariff as pt/.test(hist)],
    ['history_collect 有谷/平拆分函数',
      /def split_energy_by_period/.test(hist)],
    ['history_collect 按小时分桶拆分（kwh@HH）',
      /kwh@%/.test(hist)],
    ['energy_stats 返回 tariff_mode',
      /"tariff_mode"/.test(hist)],
    ['energy_stats 返回谷/平电量',
      /"valley_kwh"/.test(hist) && /"flat_kwh"/.test(hist)],
    ['energy_stats 返回阶梯明细',
      /"tariff_detail"/.test(hist) && /"tier_kwh"/.test(hist)],
    ['后端有 /api/power/tariff 路由',
      /\/api\/power\/tariff/.test(srv)],
    ['后端 tariff GET/POST 处理器',
      /_handle_tariff_get/.test(srv) && /_handle_tariff_save/.test(srv)],
    ['修掉 rate_yuan_per_kwh 死代码（不再从 estimation_params 取）',
      /params\.get\("rate_yuan_per_kwh"\)/.test(srv) === false ||
      /rate = float\(params\.get/.test(srv) === false],
    ['daily_energy 支持 eff_rate 参数',
      /def daily_energy\([^)]*eff_rate/.test(hist)],
    /* ---- ③ 前端 ---- */
    ['前端有电价设置面板',
      /data-tariff-box/.test(src)],
    ['前端有电价配置加载函数 loadTariff',
      /function loadTariff/.test(src)],
    ['loadTariff 已导出',
      /loadTariff: loadTariff/.test(src)],
    ['compute.js 调用 loadTariff',
      /loadTariff/.test(comp)],
    ['前端有启用开关',
      /data-tariff-enabled/.test(src)],
    ['前端有地区选择',
      /data-tariff-region/.test(src)],
    ['前端有谷时段选择',
      /data-tariff-vstart/.test(src) && /data-tariff-vend/.test(src)],
    ['前端有价目表渲染',
      /function tariffTableHtml/.test(src)],
    ['阶梯模式下卡片显示谷/平构成',
      /sub-split/.test(src) && /rate-tag-v/.test(src) && /rate-tag-f/.test(src)],
    ['阶梯模式下抽屉显示谷段/平段明细',
      /谷段/.test(src) && /平段/.test(src)],
    ['抽屉用电区在阶梯模式显示地区名',
      /tariff_region_name/.test(src)],
    /* ---- ③-b ⚠️ 回归防护：电价面板不能覆盖抽屉内容 ----
       v1.1.57 初版 bug：loadTariff(body) 里直接 box.innerHTML = ...，
       而 body 是**整个抽屉**，导致实时功耗 / 堆叠条 / 趋势图 /
       部件明细 / 用电与电费 / 估算参数 全被冲掉（用户反馈「抽屉内容没了」）。 */
    ['loadTariff 先定位 [data-tariff-box] 再更新',
      /querySelector\('\[data-tariff-box\]'\)/.test(src)],
    ['loadTariff 不再直接对 scope 写 innerHTML',
      /function loadTariff\(scope\)[\s\S]{0,400}scope\.innerHTML/.test(src) === false],
    ['applyTariff 只写面板容器',
      /function applyTariff\(tbox, d\) \{[\s\S]{0,900}tbox\.innerHTML = tariffPanelHtml\(d\)/.test(src)],
    ['抽屉原有内容仍在（实时功耗 hero）',
      /mon-hero/.test(src)],
    ['抽屉原有内容仍在（部件明细）',
      /部件明细/.test(src)],
    ['抽屉原有内容仍在（估算参数）',
      /估算参数/.test(src)],
    ['抽屉原有内容仍在（用电与电费区）',
      /data-pwr-energy/.test(src)],
    /* ---- ④ CSS ---- */
    ['CSS 有 .sub-split 样式', /\.stat-cell \.sub-split \{/.test(css)],
    ['CSS 有谷/平标签配色',
      /\.rate-tag-v \{/.test(css) && /\.rate-tag-f \{/.test(css)],
    ['CSS 有电价面板样式',
      /\.tariff-box \{/.test(css) && /\.tariff-table \{/.test(css)],
    ['CSS 有价目表行样式', /\.tariff-tb-row \{/.test(css)],
    ['CSS 有价目表季节说明样式', /\.tariff-tb-note \{/.test(css)],
    /* ---- ⑤ 价目表档位必须用当前生效的那套（不能混用夏季/非夏季）---- */
    ['前端价目表用 active_tiers（当前生效档位）',
      /active\.active_tiers/.test(src)],
    ['前端价目表用 active_valley_rates（当前生效低谷价）',
      /active\.active_valley_rates/.test(src)],
    ['前端不再混用 tiers_summer[0] 与 tiers_other',
      /i === 0 \? '0–' \+ ts\[0\]/.test(src) === false],
    ['后端 describe 返回当前生效档位',
      /"active_tiers"/.test(tar)],
    ['后端 describe 返回季节状态',
      /"is_summer_tier"/.test(tar) && /"is_wet_season"/.test(tar)],
    /* ---- ⑥ v1.1.62：生效档位只反映跨月结转（人口/抄表已移除） ---- */
    ['后端有 effective_tier_limits（生效档位计算）',
      /def effective_tier_limits/.test(tar)],
    ['后端 effective_tier_limits 用了 _effective_caps',
      /_effective_caps\(tiers, carry1, carry2\)/.test(tar)],
    ['后端不再返回 multi_person_extra',
      !/"multi_person_extra"/.test(tar)],
    ['后端不再有 supports_multi_person 开关',
      !/supports_multi_person/.test(tar)],
    ['后端不再有 supports_meter_cycle 开关',
      !/supports_meter_cycle/.test(tar)],
    ['后端 profiles 每项带 effective_tiers',
      /"effective_tiers": eff_limits/.test(tar)],
    ['前端切地区用 effective_tiers（不再用原始档位）',
      /hit\.effective_tiers/.test(src)],
    /* ---- ⑦ v1.1.59：电压等级切换要即时刷新价目表峰价 ---- */
    ['前端有电压下拉的 change 监听',
      /vtSel\.addEventListener\('change'/.test(src)],
    ['前端按电压等级取峰价（voltage_levels → peak_rates）',
      /lvList\[i\]\.peak_rates/.test(src) || /lvList\[li\]\.peak_rates/.test(src)],
    ['前端 tariffTableHtml 按 active.voltage 取峰价',
      /active\.voltage/.test(src)],
    /* ---- ⑤ 版本号（版本无关：格式合法且彼此一致）---- */
    ['manifest 版本号格式合法', /version\s+= \d+\.\d+\.\d+/.test(man)],
    ['index.cgi 版本号格式合法',
      /APP_VERSION="\d+\.\d+\.\d+"/.test(
        fs.readFileSync(path.join(UI, 'index.cgi'), 'utf8'))],
    ['app.js 版本号格式合法',
      /var APP_VERSION = '\d+\.\d+\.\d+'/.test(
        fs.readFileSync(path.join(UI, 'static', 'js', 'app.js'), 'utf8'))],
    ['三处版本号彼此一致', (function () {
      const pick = (re, t) => { const m = t.match(re); return m ? m[1] : null; };
      const vMan = pick(/version\s+= (\d+\.\d+\.\d+)/, man);
      const vCgi = pick(/APP_VERSION="(\d+\.\d+\.\d+)"/,
        fs.readFileSync(path.join(UI, 'index.cgi'), 'utf8'));
      const vApp = pick(/var APP_VERSION = '(\d+\.\d+\.\d+)'/,
        fs.readFileSync(path.join(UI, 'static', 'js', 'app.js'), 'utf8'));
      return !!(vMan && vCgi && vApp && vMan === vCgi && vCgi === vApp);
    })()],
  ];

  rules.forEach(([label, ok]) => {
    console.log((ok ? '  ✓ ' : '  ✗ ') + label);
    if (!ok) bad++;
  });

  /* ---- 渲染级：阶梯模式下卡片显示谷/平构成 ---- */
  try {
    const DATA57 = {
      power: {
        ok: true, total: 30.2, source: 'rapl',
        cpu_pkg: 2.9, mem: 0.8, disks: 18.5, base: 8.0,
      },
      storage: { disks: [] },
    };
    /* 造一组阶梯模式的 stats */
    global.window.FnStyleMonitor.__setPowerStatsForTest({
      today_kwh: 0.6419, today_yuan: 0.39,
      month_kwh: 0.8935, month_yuan: 0.40,
      month_proj_kwh: 8.1005, month_proj_yuan: 3.66,
      year_proj_kwh: 260.5, year_proj_yuan: 116.7,
      avg_w: 29.7, avg_basis: '近 2 天平均',
      rate_yuan_per_kwh: 0.6,
      eff_rate_yuan_per_kwh: 0.4477,
      tariff_mode: 'tiered',
      tariff_region: 'sichuan',
      tariff_region_name: '四川省居民（成都）',
      valley_kwh: 0.2361, flat_kwh: 0.6574,
      valley_yuan: 0.0598, flat_yuan: 0.3434,
      tier_kwh: [0.8935, 0, 0],
      tier_rates: [0.5224, 0.6224, 0.8224],
      valley_label: '23:00–07:00',
      tariff_detail: [
        { tier: 1, kwh: 0.8935, flat_kwh: 0.6574, valley_kwh: 0.2361,
          peak_rate: 0.5224, valley_rate: 0.2535, yuan: 0.4033 },
        { tier: 2, kwh: 0, flat_kwh: 0, valley_kwh: 0,
          peak_rate: 0.6224, valley_rate: 0.3535, yuan: 0 },
        { tier: 3, kwh: 0, flat_kwh: 0, valley_kwh: 0,
          peak_rate: 0.8224, valley_rate: 0.5535, yuan: 0 },
      ],
    });
    global.window.FnStyleMonitor.__renderForTest(DATA57);
    const st57 = els.monPowerStats ? els.monPowerStats.innerHTML : '';

    const renderRules57 = [
      ['阶梯模式渲染出 .sub-split 谷/平行',
        /class="sub sub-split"/.test(st57)],
      ['谷/平行带「谷」「平」标签',
        /rate-tag-v">谷</.test(st57) && /rate-tag-f">平</.test(st57)],
      ['谷/平行显示谷段电量（fnKwh 两位小数）',
        /0\.24 kWh/.test(st57)],
      ['谷/平行显示平段电量（fnKwh 两位小数）',
        /0\.66 kWh/.test(st57)],
      ['阶梯模式均价标签改为「均价」',
        /<span class="rate-lbl">均价<\/span>/.test(st57)],
      ['阶梯模式显示综合单价 0.448 元/kWh',
        /0\.448 元\/kWh/.test(st57)],
      ['阶梯模式不再显示笼统的「电价 0.6」',
        /<span class="rate-lbl">电价<\/span>0\.6/.test(st57) === false],
      ['本月已计电费为阶梯算出的 ¥0.40',
        /已计电费 ¥0\.40/.test(st57)],
    ];
    renderRules57.forEach(([label, ok]) => {
      console.log((ok ? '  ✓ ' : '  ✗ ') + label);
      if (!ok) bad++;
    });
  } catch (e) {
    console.log('  ✗ 渲染级断言异常：' + e.message);
    bad++;
  }

  /* ---- 渲染级：未启用阶梯时保持原样（向后兼容） ---- */
  try {
    global.window.FnStyleMonitor.__setPowerStatsForTest({
      today_kwh: 0.2801, today_yuan: 0.17,
      month_kwh: 0.5317, month_yuan: 0.32,
      month_proj_kwh: 8.0933, month_proj_yuan: 4.87,
      year_proj_kwh: 260.4, year_proj_yuan: 156.72,
      avg_w: 29.7, avg_basis: '近 2 天平均',
      rate_yuan_per_kwh: 0.6,
      eff_rate_yuan_per_kwh: 0.6,
    });
    global.window.FnStyleMonitor.__renderForTest({
      power: { ok: true, total: 30.2, source: 'rapl' },
      storage: { disks: [] },
    });
    const st57b = els.monPowerStats ? els.monPowerStats.innerHTML : '';
    const renderRules57b = [
      ['未启用时仍显示「电价 0.6 元/kWh」',
        /<span class="rate-lbl">电价<\/span>0\.6 元\/kWh/.test(st57b)],
      ['未启用时不渲染 .sub-split',
        /class="sub sub-split"/.test(st57b) === false],
      ['未启用时不渲染谷/平标签',
        /rate-tag-v/.test(st57b) === false],
    ];
    renderRules57b.forEach(([label, ok]) => {
      console.log((ok ? '  ✓ ' : '  ✗ ') + label);
      if (!ok) bad++;
    });
  } catch (e) {
    console.log('  ✗ 向后兼容渲染断言异常：' + e.message);
    bad++;
  }

  if (bad) {
    console.error('❌ v1.1.57 断言失败 ' + bad + ' 项');
    process.exitCode = 1;
  }
})();

/* 抽屉滑入动画断言是异步的（两帧 rAF → setTimeout），必须等它跑完再汇总 */
setTimeout(function () {
  console.log(process.exitCode ? '\n❌ 测试未全部通过' : '\n🎉 全部测试通过');
}, 120);
