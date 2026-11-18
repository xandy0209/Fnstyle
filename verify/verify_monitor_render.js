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
  'btnCloseMonDrawer', 'monDwSpark', 'monDwTimeFrom', 'monDwTimeTo']
  .forEach(id => { els[id] = mkEl(id); });

/* 抽屉的 overlay 需要 classList / hidden / 事件钩子 */
els.monDrawerOverlay.classList = {
  _s: {},
  add: function (c) { this._s[c] = true; },
  remove: function (c) { delete this._s[c]; },
  contains: function (c) { return !!this._s[c]; },
};
els.monDrawerOverlay.hidden = true;
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
  '  window.FnStyleMonitor = {'
);
if (patched2 === patched) {
  console.error('❌ 未能注入 SMART 测试钩子（源码结构变化）');
  process.exit(1);
}
global.document = {
  readyState: 'complete', hidden: false,
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
if (typeof bayLedState !== 'function' || typeof smartAlertsText !== 'function') {
  console.error('❌ SMART 测试钩子未就绪');
  process.exit(1);
}
if (typeof openDiskDrawer !== 'function' || typeof dwSmartTable !== 'function'
    || typeof dwInfoKv !== 'function' || typeof setLastData !== 'function') {
  console.error('❌ 抽屉测试钩子未就绪');
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
    // 色阶图例：点阵与容量行的颜色含义
    ['色阶图例条', 'ramp-bar'],
    ['色阶图例说明', '点阵与容量行颜色 = 空间占用'],
    ['LED 语义说明', '盘位 LED = 硬盘健康与 SMART 告警状态'],
    ['LED 图例-休眠未巡检', '休眠 / 未巡检'],
    ['巡检时间', '巡检于'],
    ['盘位排序提示', '盘位按存储空间排列'],
    // 原表格信息已整合进盘位卡片
    ['盘位显示设备名', 'sda'],
    ['盘位显示型号', 'WDC WD40EFZX'],
    ['盘位显示存储空间标签', '存储空间 vol1'],
    // 其余板块
    ['SMART 计数表', 'SMART 告警计数'],
    ['软 RAID', '软 RAID'],
    ['阵列卡', '阵列卡'],
    ['硬盘巡检', '硬盘巡检'],
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
    ['标题为「硬盘 存储空间 vol1」', els.monDrawerTitle.textContent === '硬盘 存储空间 vol1'],
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
  const missingOk = els.monDrawerTitle.textContent === '未找到硬盘'
    && /可能已被移除/.test(els.monDrawerBody.innerHTML);
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

/* ---------- 源码级断言：容量单位进制不能被改回去 ---------- */
(function checkDecimalUnits() {
  console.log('\n--- 容量单位进制（源码级）---');
  const src = fs.readFileSync(SRC, 'utf8');
  const rules = [
    ['fmtDisk 函数存在', /function fmtDisk\s*\(/],
    ['fmtDisk 用十进制常量 1e12', /var T = 1e12, G = 1e9, M = 1e6/],
    ['存储池汇总用 fmtDisk', /totText = fmtDisk\(tSum\)/],
    ['盘位容量行用 fmtDisk', /d\.usedText \|\| fmtDisk\(d\.used \|\| 0\)/],
    // 内存/Swap/显存仍走 1024 进制（fnmonitor 的 F.bytes），不能被一起改成十进制
    ['Swap 仍用 fmtBytes', /fmtBytes\(mem\.swapTotal\)/],
    ['显存仍用 fmtBytes', /fmtBytes\(g\.memTotal\)/],
    // fmtBytes 不能被改成十进制
    ['fmtBytes 仍按 1024 递减', /while \(v >= 1024 && i < units\.length - 1\)/],
  ];  let bad = 0;
  rules.forEach(([label, re]) => {
    const ok = re.test(src);
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

console.log(process.exitCode ? '\n❌ 测试未全部通过' : '\n🎉 全部测试通过');
