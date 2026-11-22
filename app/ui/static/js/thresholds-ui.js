/* ==========================================================================
   FnStyle —— 系统监控阈值门限「配置管理」抽屉（v1.1.79）
   --------------------------------------------------------------------------
   老板需求：
     「对软件中系统监控部分相关的阈值门限的配置提取出来进行单独的配置，
       配置页面以抽屉方式呈现，入口为页面顶部栏中增加配置管理按钮点击进入。」

   入口：监控面板状态条右端的「配置管理」齿轮按钮（index.html 里的 #tbOpenThresholds）
         —— v1.1.79 原本在顶栏（与「检查更新」「在新页面打开」同排），
            v1.1.80 按老板反馈移到监控状态条右端（阈值只对系统监控生效）。

   抽屉内容：按后端给的分组（处理器 / 内存 / 存储 / 温度 / 显示着色）
             分段列出每个门限：名称 + 数字输入 + 单位 + 说明。
   底部：恢复默认 + 保存。

   ⚠️ 为什么用**后端返回的清单**渲染，而不是前端写死一份表单：
      后端 monitor_thresholds.SPEC 是唯一权威来源（默认值 / 区间 / 说明都在那）。
      前端只负责「把清单画成表单」——新增门限时后端加一条、前端自动多一行，
      不用两处同步改。

   ⚠️ 保存后必须让各视图重绘：
      阈值影响「等级文案 / 颜色 / 图表阈值虚线 / 告警条」。
      保存成功后除了通知 thresholds.js 的订阅者，
      还要主动让监控模块整页重绘一次（window.FnStyleMonitor.refresh()）。
   ========================================================================== */
(function () {
  'use strict';

  var TH = window.FnStyleThresholds;
  var BASE = 'api/';

  var overlay = null;
  var bodyEl = null;
  var open = false;
  var items = [];        // 后端返回的门限清单
  var groups = [];       // 分组顺序
  var bound = false;

  function $(sel) { return document.querySelector(sel); }

  function esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function getOverlay() {
    if (!overlay) overlay = $('#monThresholdOverlay');
    return overlay;
  }

  /* ---------------- 表单渲染 ---------------- */

  /* 一个门限行的 HTML */
  function itemRow(it) {
    var step = it.unit === '℃' ? 1 : 1;
    var html = '<div class="thr-row" data-thr-key="' + esc(it.key) + '">';
    html += '<div class="thr-row-main">';
    html += '<label class="thr-lbl" for="thrIn_' + esc(it.key) + '">' +
      esc(it.label) + '</label>';
    html += '<div class="thr-ctl">';
    html += '<input class="thr-input" type="number" id="thrIn_' + esc(it.key) + '"' +
      ' data-thr-input="' + esc(it.key) + '"' +
      ' value="' + esc(String(it.value)) + '"' +
      ' min="' + esc(String(it.min)) + '"' +
      ' max="' + esc(String(it.max)) + '"' +
      ' step="' + step + '" inputmode="numeric">';
    if (it.unit) {
      html += '<span class="thr-unit">' + esc(it.unit) + '</span>';
    }
    html += '<span class="thr-def" title="默认值 ' + esc(String(it.default)) + '">默认 ' +
      esc(String(it.default)) + '</span>';
    html += '</div>';
    html += '</div>';
    if (it.desc) {
      html += '<p class="thr-desc">' + esc(it.desc) + '</p>';
    }
    html += '</div>';
    return html;
  }

  /* 整个抽屉 body 的 HTML */
  function bodyHtml(data) {
    var html = '';

    html += '<div class="thr-intro">以下门限决定系统监控各板块的告警分级与配色。' +
      '修改后立即生效并保存在本机。</div>';

    (groups.length ? groups : []).forEach(function (g) {
      var list = items.filter(function (it) { return it.group === g.key; });
      if (!list.length) return;
      html += '<section class="thr-sec">';
      html += '<h4 class="thr-sec-t">' + esc(g.label) + '</h4>';
      html += '<div class="thr-rows">';
      list.forEach(function (it) { html += itemRow(it); });
      html += '</div></section>';
    });

    html += '<div class="thr-actions">';
    html += '<button type="button" class="btn sm" data-thr-reset>恢复默认</button>';
    html += '<button type="button" class="btn primary sm" data-thr-save>保存阈值</button>';
    html += '<span class="thr-msg" data-thr-msg></span>';
    html += '</div>';

    return html;
  }

  /* 渲染抽屉内容 */
  function render(data) {
    if (!bodyEl) return;
    if (data && data.items) {
      items = data.items;
      groups = data.groups || [];
    }
    bodyEl.innerHTML = bodyHtml();
    bindBody();
  }

  /* 打开抽屉 */
  function openDrawer() {
    var ov = getOverlay();
    if (!ov) return;
    ov.hidden = false;
    open = true;
    /* 下一帧再加 .on，保证过渡动画能触发（与 monitor.js 的 showDrawer 同构） */
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { ov.classList.add('on'); });
    });

    bodyEl = $('#monThresholdBody');
    if (!bodyEl) return;
    bodyEl.innerHTML = '<div class="mon-info">阈值配置加载中…</div>';

    /* 优先用后端返回的完整清单（含默认值/区间/说明） */
    if (!window.fetch) {
      bodyEl.innerHTML = '<div class="mon-info">当前环境不支持读取配置</div>';
      return;
    }
    fetch(BASE + 'monitor/thresholds', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.success) {
          bodyEl.innerHTML = '<div class="mon-info">阈值配置读取失败</div>';
          return;
        }
        render(j);
        /* 顺便把配置灌进统一读取层（首屏可能还没拉到） */
        if (TH && TH.applySaved) TH.applySaved(j);
      })
      .catch(function () {
        bodyEl.innerHTML = '<div class="mon-info">阈值配置读取失败</div>';
      });
  }

  /* 关闭抽屉 */
  function closeDrawer() {
    var ov = getOverlay();
    if (!ov) return;
    ov.classList.remove('on');
    open = false;
    setTimeout(function () {
      if (!open) ov.hidden = true;
    }, 320);
  }

  /* 读取当前表单值（只提交与初始值不同的键 —— 减少提交量，也避免误覆盖） */
  function collect() {
    var payload = {};
    items.forEach(function (it) {
      var inp = bodyEl.querySelector('[data-thr-input="' + it.key + '"]');
      if (!inp) return;
      var raw = inp.value;
      if (raw === '' || raw === null) return;
      var v = Number(raw);
      if (!isFinite(v)) return;
      payload[it.key] = v;
    });
    return payload;
  }

  /* 表单内的事件绑定（每次重绘后重新绑） */
  function bindBody() {
    if (!bodyEl) return;

    var saveBtn = bodyEl.querySelector('[data-thr-save]');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var msg = bodyEl.querySelector('[data-thr-msg]');
        var payload = collect();
        saveBtn.disabled = true;
        if (msg) msg.textContent = '保存中…';
        if (!TH || !TH.save) {
          saveBtn.disabled = false;
          if (msg) msg.textContent = '保存接口不可用';
          return;
        }
        TH.save(payload).then(function (res) {
          saveBtn.disabled = false;
          if (!res.ok) {
            if (msg) msg.textContent = res.message || '保存失败';
            return;
          }
          /* 灌入新值 + 通知订阅者重绘 */
          if (TH.applySaved) TH.applySaved(res.data);
          if (msg) msg.textContent = '已保存';
          /* 让监控视图立刻按新门限重绘（等级文案 / 颜色 / 阈值虚线） */
          if (window.FnStyleMonitor && window.FnStyleMonitor.refresh) {
            window.FnStyleMonitor.refresh();
          }
          setTimeout(function () {
            var m2 = bodyEl.querySelector('[data-thr-msg]');
            if (m2) m2.textContent = '';
          }, 2500);
        });
      });
    }

    var resetBtn = bodyEl.querySelector('[data-thr-reset]');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        var msg = bodyEl.querySelector('[data-thr-msg]');
        resetBtn.disabled = true;
        if (msg) msg.textContent = '恢复中…';
        if (!TH || !TH.save) {
          resetBtn.disabled = false;
          if (msg) msg.textContent = '保存接口不可用';
          return;
        }
        TH.save({ __reset: true }).then(function (res) {
          resetBtn.disabled = false;
          if (!res.ok) {
            if (msg) msg.textContent = res.message || '恢复默认失败';
            return;
          }
          if (TH.applySaved) TH.applySaved(res.data);
          /* 重新渲染表单（恢复默认后输入框要回到默认值） */
          render(res.data);
          var m2 = bodyEl.querySelector('[data-thr-msg]');
          if (m2) m2.textContent = '已恢复默认';
          if (window.FnStyleMonitor && window.FnStyleMonitor.refresh) {
            window.FnStyleMonitor.refresh();
          }
        });
      });
    }
  }

  /* 抽屉自身的绑定（遮罩点击 / 关闭按钮 / ESC） */
  function bindDrawer() {
    if (bound) return;
    var ov = getOverlay();
    if (!ov) return;
    bound = true;

    ov.addEventListener('click', function (e) {
      if (e.target === ov) closeDrawer();
    });
    var btn = $('#btnCloseMonThreshold');
    if (btn) btn.addEventListener('click', closeDrawer);

    document.addEventListener('keydown', function (e) {
      if ((e.key === 'Escape' || e.key === 'Esc') && open) {
        e.preventDefault();
        closeDrawer();
      }
    });
  }

  /* 顶栏入口按钮 */
  function bindTopbar() {
    var btn = $('#tbOpenThresholds');
    if (!btn || btn.__bound) return;
    btn.__bound = true;
    btn.addEventListener('click', function () {
      bindDrawer();
      openDrawer();
    });
  }

  /* 初始化 */
  function init() {
    bindTopbar();
    /* 抽屉 DOM 可能还没就绪 —— DOMContentLoaded 时再试一次 */
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        bindTopbar();
        bindDrawer();
      });
    } else {
      bindDrawer();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.FnStyleThresholdsUI = {
    open: openDrawer,
    close: closeDrawer
  };
})();
