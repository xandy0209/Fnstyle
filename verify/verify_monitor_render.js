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
 'btnMonScan']
  .forEach(id => { els[id] = mkEl(id); });

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
      totalText: '500.0 GB', usedText: '312.5 GB', freeText: '187.5 GB',
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
        name: 'sda', model: 'WDC WD40EFZX-68AWUN0', type: 'disk',
        size: 4000653453312, sizeText: '3.64 TB',
        serial: 'WD-WX32D0051234', temp: 34, rpm: 5400,
        health: 'PASSED', grade: 'ok', gradeLabel: '正常', detail: '未发现异常计数',
        mounted: '/vol1', standby: false, proto: 'SATA',
        reallocated_sectors: 0, reallocated_events: 0,
        pending_sectors: 0, offline_uncorrectable: 0,
        uncorrectable: 0, udma_crc_errors: 0,
      },
      {
        name: 'sdb', model: 'WDC WD40EFZX-68AWUN0', type: 'disk',
        size: 4000653453312, sizeText: '3.64 TB',
        serial: 'WD-WX32D0055678', temp: 41, rpm: 5400,
        health: 'PASSED', grade: 'warn', gradeLabel: '需留意',
        detail: '重映射扇区计数 3',
        mounted: '/vol1', standby: false, proto: 'SATA',
        reallocated_sectors: 3, reallocated_events: 3,
        pending_sectors: 0, offline_uncorrectable: 0,
        uncorrectable: 0, udma_crc_errors: 12,
      },
      {
        name: 'sdc', model: 'Samsung SSD 870 EVO 1TB', type: 'disk',
        size: 1000204886016, sizeText: '931.5 GB',
        serial: 'S6P2NJ0T123456', temp: null, rpm: null,
        health: 'PASSED', grade: 'crit', gradeLabel: '故障',
        detail: '不可纠正扇区计数 2；待映射扇区 5',
        mounted: '/vol1', standby: false, proto: 'SATA',
        reallocated_sectors: 10, reallocated_events: 10,
        pending_sectors: 5, offline_uncorrectable: 2,
        uncorrectable: 2, udma_crc_errors: 0,
      },
      {
        name: 'sdd', model: 'ST4000VN008-2DR166', type: 'disk',
        size: 4000787030016, sizeText: '3.64 TB',
        serial: 'ZDH1AAAA', temp: 36, rpm: 5900,
        health: 'PASSED', grade: 'unknown', gradeLabel: '未知',
        detail: '休眠中，未唤醒检测',
        mounted: '/vol1', standby: true, proto: 'SAS',
        reallocated_sectors: 0, reallocated_events: 0,
        pending_sectors: 0, offline_uncorrectable: 0,
        uncorrectable: 0, udma_crc_errors: 0,
      },
    ],
    diskio: [
      { name: 'sda', read: 1048576, write: 524288, readBps: 1048576, writeBps: 524288,
        readIOps: 32, writeIOps: 16, readText: '1.0 MB/s', writeText: '512.0 KB/s' },
      { name: 'sdc', read: 0, write: 0, readBps: 0, writeBps: 0,
        readIOps: 0, writeIOps: 0, readText: '0 B/s', writeText: '0 B/s' },
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
      totalText: '500.0 GB', usedText: '312.5 GB', freeText: '187.5 GB',
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
global.document = {
  readyState: 'complete', hidden: false,
  querySelector: function (sel) { return sel.charAt(0) === '#' ? (els[sel.slice(1)] || null) : null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
};
global.window = global;
(0, eval)(patched);
console.log('✅ 测试钩子注入成功');

/* ---------- 各用例 ---------- */
let html = renderCase('完整数据', { storage: FULL.storage, ts: FULL.ts });
if (html) {
  const checks = [
    ['存储总览', '存储总览'],
    ['存储池', '存储池 vol1'],
    ['物理磁盘表', '物理磁盘'],
    ['SMART 计数表', 'SMART 告警计数'],
    ['软 RAID', '软 RAID'],
    ['阵列卡', '阵列卡'],
    ['硬盘巡检', '硬盘巡检'],
    ['休眠标记', '休眠'],
    ['故障盘分级', '故障'],
    ['成员盘故障 (F)', '(F)'],
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

console.log(process.exitCode ? '\n❌ 测试未全部通过' : '\n🎉 全部测试通过');
