/* ==========================================================================
   FnStyle —— 系统监控阈值门限统一读取层（v1.1.79）
   --------------------------------------------------------------------------
   老板需求：「对软件中系统监控部分相关的阈值门限的配置提取出来进行单独的配置，
              配置页面以抽屉方式呈现，入口为页面顶部栏中增加配置管理按钮点击进入。」

   本文件是**前端**侧的唯一阈值入口：
     · 启动时从 GET api/monitor/thresholds 拉一份配置（异步）
     · 拉回来之前，各调用点用**默认值**兜底（保证首屏立刻能渲染）
     · 拉回来之后，通过 th('cpuWarn') 之类的 getter 立即取到新值

   ⚠️ 关键设计：**不能把阈值做成常量快照**
      如果各文件写成 `var CPU_WARN = 85;` 然后在加载时替换一次，
      异步配置回来后就永远不生效了 —— 因为常量在脚本加载时就已固化。
      所以所有取值必须走**函数调用**（TH.get(key)），
      每次用的时候现取，配置一变立刻生效。

   用法：
     · 全局：window.FnStyleThresholds.get('cpuWarn')
     · 各模块内部：var TH = window.FnStyleThresholds; if (TH) TH.get('cpuWarn')

   ⚠️ 加载顺序：thresholds.js 必须**先于** monitor.js / compute.js /
      overview.js 加载，否则它们在顶层取 TH 时会拿到 undefined。
      —— 不过本文件在加载时会立刻塞一个「带默认值的桩」进 window，
         即使顺序反了也不会崩，只是会先用默认值。
   ========================================================================== */
(function () {
  'use strict';

  var BASE = 'api/';

  /* 前端兜底默认值 —— 必须与后端 monitor_thresholds.SPEC 的 def 一致。
     后端是权威，这里只是「后端还没回来之前」的临时值。 */
  var DEFAULTS = {
    cpuWarn: 85, cpuCrit: 95,
    memWarn: 85, memCrit: 95,
    capWarn: 85, capCrit: 95,
    tempWarn: 60, tempCrit: 75,
    diskTempWarn: 50, diskTempCrit: 58,
    barWarn: 70, barCrit: 90,
    scanGrowthCrit: 10
  };

  var current = {};
  var loaded = false;
  var loading = false;
  var listeners = [];
  var lastError = '';

  /* 复制默认值到 current —— 保证 get() 任何时候都有值 */
  function resetToDefaults() {
    current = {};
    for (var k in DEFAULTS) {
      if (Object.prototype.hasOwnProperty.call(DEFAULTS, k)) current[k] = DEFAULTS[k];
    }
  }
  resetToDefaults();

  /* 取阈值。key 未知时返回 fallback（默认 undefined）。 */
  function get(key, fallback) {
    var v = current[key];
    if (v === undefined || v === null || isNaN(v)) {
      if (fallback !== undefined) return fallback;
      return DEFAULTS[key];
    }
    return v;
  }

  /* 便捷取值：warn / crit 成对取 */
  function pair(wkey, ckey) {
    return { warn: get(wkey), crit: get(ckey) };
  }

  /* 是否已成功加载过后端配置 */
  function isLoaded() { return loaded; }

  function getLastError() { return lastError; }

  /* 订阅配置变化（保存后回调，用于让各视图重绘） */
  function onChange(fn) {
    if (typeof fn === 'function') listeners.push(fn);
  }

  function notify() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](current); } catch (e) { /* 单个订阅者出错不影响其它 */ }
    }
  }

  /* 把后端返回的 items 合并进 current */
  function applyConfig(cfg) {
    if (!cfg || typeof cfg !== 'object') return;
    var changed = false;
    for (var k in cfg) {
      if (!Object.prototype.hasOwnProperty.call(cfg, k)) continue;
      if (k === 'updated') continue;
      var v = cfg[k];
      if (v === undefined || v === null || v === '' || isNaN(v)) continue;
      if (current[k] !== v) changed = true;
      current[k] = Number(v);
    }
    if (changed) notify();
  }

  /* 拉取后端配置。返回 Promise（无 fetch 时 resolve(null)）。 */
  function load(force) {
    if (loading && !force) {
      return Promise.resolve(current);
    }
    if (loaded && !force) {
      return Promise.resolve(current);
    }
    if (!window.fetch) {
      lastError = '当前环境不支持 fetch';
      return Promise.resolve(null);
    }
    loading = true;
    return fetch(BASE + 'monitor/thresholds', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        loading = false;
        if (!j || !j.success) {
          lastError = (j && j.error) || '阈值配置读取失败';
          return null;
        }
        lastError = '';
        applyConfig(j.config || {});
        loaded = true;
        notify();
        return current;
      })
      .catch(function (e) {
        loading = false;
        lastError = (e && e.message) || '阈值配置读取失败';
        return null;
      });
  }

  /* 保存配置。body 形如 {cpuWarn: 80, ...}；{__reset:true} 表示恢复默认。
     返回 Promise，resolve {ok, message, config}。 */
  function save(body) {
    if (!window.fetch) {
      return Promise.resolve({ ok: false, message: '当前环境不支持 fetch' });
    }
    return fetch(BASE + 'monitor/thresholds', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) {
        return { ok: r.ok && !!(j && j.success), message: (j && (j.message || j.error)) || '', data: j };
      }).catch(function () {
        return { ok: false, message: '响应解析失败' };
      });
    }).catch(function (e) {
      return { ok: false, message: (e && e.message) || '保存失败（网络错误）' };
    });
  }

  /* 保存成功后把新值合并进 current */
  function applySaved(data) {
    if (data && data.config) {
      applyConfig(data.config);
      loaded = true;
      notify();
    }
  }

  window.FnStyleThresholds = {
    get: get,
    pair: pair,
    load: load,
    save: save,
    applySaved: applySaved,
    onChange: onChange,
    isLoaded: isLoaded,
    getLastError: getLastError,
    DEFAULTS: DEFAULTS,
    /* 供测试 / 调试：直接注入一份配置 */
    __setForTest: function (cfg) { applyConfig(cfg || {}); loaded = true; }
  };

  /* 页面加载后自动拉一次（失败也不阻塞页面） */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { load(); });
  } else {
    load();
  }
})();
