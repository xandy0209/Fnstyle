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
/* 再注入 SMART 状态判定的测试钩子（bayLedState / smartAlertsText） */
const patched2 = patched.replace(
  'window.FnStyleMonitor = {',
  'window.__bayLedState = bayLedState;\n  window.__smartAlertsText = smartAlertsText;\n  window.FnStyleMonitor = {'
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
if (typeof bayLedState !== 'function' || typeof smartAlertsText !== 'function') {
  console.error('❌ SMART 测试钩子未就绪');
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
  ];
  let bad = 0;
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

console.log(process.exitCode ? '\n❌ 测试未全部通过' : '\n🎉 全部测试通过');
