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
             v1.1.84 起新增两段：
               · 电费设置（电价面板从能耗计算抽屉搬来）
               · 告警通知（超阈值告警的推送配置与选择项）
   底部：恢复默认门限 + 保存阈值。

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
  var alertsData = null; // 后端返回的告警通知配置与可选项清单（v1.1.84）

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

  /* 电价设置分段的 HTML（v1.1.84）
     ------------------------------------------------------------
     老板需求：「电费的设置调整到设置抽屉中」。
     ⇒ 电价面板从「能耗计算抽屉」搬到这里。

     ⚠️ 只放**占位容器**，实际内容由 FnStyleTariff.mount() 异步填充
        （电价在后端 /api/power/tariff，前端不能自己算单价）。
     ⚠️ 单独一个保存按钮（不跟阈值共用「保存阈值」）：
        两个是不同后端、不同生命周期 —— 阈值是「立刻影响全局判定」，
        电价是「影响电费数字」。合成一个按钮会让用户以为改一个就都存了。 */
  function tariffSectionHtml() {
    return '<section class="thr-sec thr-sec-tariff">' +
      '<h4 class="thr-sec-t">电费设置</h4>' +
      '<div class="tariff-box" data-tariff-box>' +
      '<div class="mon-info">电价配置加载中…</div></div>' +
      '</section>';
  }

  /* --------------------------------------------------------------------------
     告警通知分段的 HTML（v1.1.84）
     --------------------------------------------------------------------------
     老板需求：「对于超阈值的生成告警消息，提供通过飞牛的通知消息Webhook
                进行推送的能力，同时提供通知消息的选择配置。」

     内容：
       · 总开关（启用 / 停用）
       · 投递通道（Webhook / 飞牛通知中心）—— 勾选哪个就用哪个
       · Webhook 地址 + 密钥
       · 告警级别（留意 / 严重）
       · 告警指标（逐项勾选）
       · 静默期
       · 测试推送按钮

     ⚠️ 清单（指标 / 级别 / 通道）由**后端返回**（GET api/monitor/alerts），
        前端不写死 —— 与阈值的做法一致，后端加一项前端自动多一行。
     ⚠️ Webhook 地址是**敏感信息**（通常带密钥），
        输入框用 type="password" 遮挡，避免被旁人看到 / 被浏览器记住。
     -------------------------------------------------------------------------- */
  function alertsSectionHtml() {
    var a = alertsData || {};
    var cfg = a.config || {};
    var metrics = a.metrics || [];
    var levels = a.levels || [];
    var channels = a.channels || [];
    var html = '';

    html += '<section class="thr-sec thr-sec-alerts">';
    html += '<h4 class="thr-sec-t">告警通知</h4>';

    /* --- 总开关 --- */
    html += '<div class="thr-row thr-row-switch">';
    html += '<div class="thr-row-main">';
    html += '<label class="thr-lbl">启用超阈值告警推送</label>';
    html += '<div class="thr-ctl">';
    html += '<label class="thr-switch"><input type="checkbox" data-al-enabled' +
      (cfg.enabled ? ' checked' : '') + '>' +
      '<span class="thr-switch-track"></span></label>';
    html += '</div></div>';
    html += '<p class="thr-desc">开启后，处理器 / 内存 / 容量 / 温度 / 硬盘健康' +
      '任一项越过上面配置的门限时，会按下面的选择推送告警消息。' +
      '告警在服务端判定，浏览器关掉也会继续推送。</p>';
    html += '</div>';

    /* --- 投递通道 --- */
    html += '<div class="thr-sub">投递通道</div>';
    html += '<div class="thr-rows">';
    (channels || []).forEach(function (c) {
      var on = (cfg.channels || []).indexOf(c.key) >= 0;
      html += '<div class="thr-row thr-row-check">';
      html += '<label class="thr-check"><input type="checkbox" data-al-channel="' +
        esc(c.key) + '"' + (on ? ' checked' : '') + '>';
      html += '<span>' + esc(c.label) + '</span></label>';
      if (c.key === 'fnos') {
        html += '<p class="thr-desc">投递到飞牛 fnOS 自身的通知中心' +
          '（需系统开放第三方应用投递接口；不可用时会在状态里注明）。</p>';
      }
      html += '</div>';
    });
    html += '</div>';

    /* --- Webhook 地址 --- */
    html += '<div class="thr-sub">Webhook 地址</div>';
    html += '<div class="thr-rows">';
    html += '<div class="thr-row">';
    html += '<div class="thr-row-main">';
    html += '<label class="thr-lbl" for="alWebhookUrl">接收地址</label>';
    html += '<div class="thr-ctl">';
    html += '<input class="thr-input thr-input-wide" type="url" id="alWebhookUrl" ' +
      'placeholder="https://…" data-al-url value="' +
      esc(cfg.webhook_url || '') + '">';
    html += '</div></div>';
    html += '<p class="thr-desc">填飞书 / 钉钉 / 企业微信机器人的 Webhook 地址即可；' +
      '与 fnOS「系统通知 → 通知设置」里配的「其他机器人」地址填同一个，' +
      '消息就会进同一个群。</p>';
    html += '</div>';
    html += '<div class="thr-row">';
    html += '<div class="thr-row-main">';
    html += '<label class="thr-lbl" for="alWebhookSecret">加签密钥</label>';
    html += '<div class="thr-ctl">';
    html += '<input class="thr-input thr-input-wide" type="password" ' +
      'id="alWebhookSecret" placeholder="选填" data-al-secret value="">';
    html += '</div></div>';
    html += '<p class="thr-desc">企业微信 / 钉钉的加签密钥（选填）。' +
      '出于安全考虑不显示已保存的值，留空表示不修改。</p>';
    html += '</div>';
    html += '</div>';

    /* --- 告警级别 --- */
    html += '<div class="thr-sub">告警级别</div>';
    html += '<div class="thr-rows">';
    (levels || []).forEach(function (l) {
      var on = (cfg.levels || []).indexOf(l.key) >= 0;
      html += '<div class="thr-row thr-row-check">';
      html += '<label class="thr-check"><input type="checkbox" data-al-level="' +
        esc(l.key) + '"' + (on ? ' checked' : '') + '>';
      html += '<span>' + esc(l.label) + '</span></label>';
      html += '</div>';
    });
    html += '</div>';

    /* --- 告警指标（按后端分组分段） --- */
    html += '<div class="thr-sub">告警指标</div>';
    html += '<div class="thr-rows">';
    (metrics || []).forEach(function (m) {
      html += '<div class="thr-row thr-row-check">';
      html += '<label class="thr-check"><input type="checkbox" data-al-metric="' +
        esc(m.key) + '"' + (m.enabled ? ' checked' : '') + '>';
      html += '<span>' + esc(m.label) + '</span></label>';
      if (m.desc) {
        html += '<p class="thr-desc">' + esc(m.desc) + '</p>';
      }
      html += '</div>';
    });
    html += '</div>';

    /* --- 静默期 --- */
    html += '<div class="thr-row">';
    html += '<div class="thr-row-main">';
    html += '<label class="thr-lbl" for="alCooldown">重复提醒静默期</label>';
    html += '<div class="thr-ctl">';
    html += '<input class="thr-input" type="number" id="alCooldown" ' +
      'data-al-cooldown min="0" max="86400" step="60" inputmode="numeric" value="' +
      esc(String(cfg.cooldown === undefined ? 300 : cfg.cooldown)) + '">';
    html += '<span class="thr-unit">秒</span>';
    html += '</div></div>';
    html += '<p class="thr-desc">同一指标在静默期内不重复推送；' +
      '但等级从「留意」升到「严重」时会立即推送。</p>';
    html += '</div>';

    /* --- 操作按钮 + 状态 --- */
    html += '<div class="thr-actions thr-actions-alerts">';
    html += '<button type="button" class="btn sm" data-al-test>测试推送</button>';
    html += '<button type="button" class="btn primary sm" data-al-save>保存通知配置</button>';
    html += '<span class="thr-msg" data-al-msg></span>';
    html += '</div>';
    html += '<div class="thr-alert-status" data-al-status></div>';

    html += '</section>';
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

    /* ⚠️ v1.1.85：按钮区从「抽屉最底部」移到「阈值配置下方」。
       老板反馈：按钮位置不对，应该在阈值配置下面显示。

       为什么原来不对：v1.1.84 把电价设置、告警通知两段接在阈值分组之后，
       按钮区又留在最底部 ⇒ 「保存阈值」离它操作的表单隔了两大段，
       用户改完门限要往下翻过电价和告警配置才找得到按钮，
       而且按钮紧挨着「保存通知配置」，两个「保存」很容易点错。

       ⇒ 按钮紧跟阈值分组，既贴着它操作的表单，
          又和下面「保存通知配置」在视觉上分开（各自成段）。 */
    html += '<div class="thr-actions thr-actions-thresholds">';
    html += '<button type="button" class="btn sm" data-thr-reset>恢复默认门限</button>';
    html += '<button type="button" class="btn primary sm" data-thr-save>保存阈值</button>';
    html += '<span class="thr-msg" data-thr-msg></span>';
    html += '</div>';

    /* ⚠️ v1.1.84：电价设置放在**阈值分组之后**——
       阈值是「监控告警的门限」，电价是「算账的参数」，
       两者不是一类东西，视觉上分开成两段更清楚。 */
    html += tariffSectionHtml();

    /* ⚠️ v1.1.84：告警通知放在最后——
       它是「这些门限超了之后要怎么办」，逻辑上依赖前面的门限配置，
       放最后读起来是自然的因果顺序。 */
    html += alertsSectionHtml();

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
    /* ⚠️ v1.1.84：电价面板异步挂载（内容来自 /api/power/tariff）。
       必须**在 render 之后**调用 —— 容器是 render 生成的。
       为什么放最后：阈值表单要**先**渲染出来（同步），
       用户点开抽屉立刻就能看到门限；电价是慢接口，异步补上即可。 */
    mountTariff();
    /* ⚠️ v1.1.84：告警通知配置（/api/monitor/alerts）。
       同样异步：拉回来后要**重画**告警段（清单来自后端，前端不写死）。 */
    loadAlerts();
  }

  /* 只重绘**阈值表单**部分（不动电价面板）
     ------------------------------------------------------------
     ⚠️ v1.1.84 新增。用途：「恢复默认门限」之后要把输入框刷回默认值，
        但如果整个 body 重绘，电价面板也会被一起冲掉 ——
        用户可能正在改单价却被「恢复默认门限」顺带清空。
        ⇒ 拆成两块渲染：阈值表单 + 电价面板，各管各的。 */
  function renderThresholdsOnly(data) {
    if (!bodyEl) return;
    if (data && data.items) {
      items = data.items;
      groups = data.groups || [];
    }
    /* 找到阈值区的容器（电价区在它后面，不动） */
    var secs = bodyEl.querySelectorAll('.thr-sec');
    var first = secs.length ? secs[0] : null;
    if (!first) {
      /* 找不到阈值区 → 退化成整体重绘（不该发生） */
      render(data);
      return;
    }
    /* 重建阈值区：把所有 .thr-sec（阈值分组）替换成新内容，
       保留电价区（.thr-sec-tariff）与告警段（.thr-sec-alerts）不动。

       ⚠️ v1.1.85：按钮区（.thr-actions-thresholds）现在夹在阈值分组与电价区之间，
          它**不是** .thr-sec ⇒ 上面的「删除所有 .thr-sec」不会碰它，
          但「插入到电价区之前」也**不会**把它带上
          ⇒ 必须**单独**替换按钮区，否则会出现「阈值区重绘后按钮消失」。
          做法：先删旧按钮区，再把新按钮区插到电价区之前。 */
    var tmp = document.createElement('div');
    tmp.innerHTML = bodyHtml();
    var newSecs = tmp.querySelectorAll('.thr-sec');
    var newTariff = tmp.querySelector('.thr-sec-tariff');
    var newAlerts = tmp.querySelector('.thr-sec-alerts');
    var newActions = tmp.querySelector('.thr-actions-thresholds');
    /* 先删掉旧的阈值区（保留电价区与告警段） */
    var tariffSec = bodyEl.querySelector('.thr-sec-tariff');
    var alertsSec = bodyEl.querySelector('.thr-sec-alerts');
    Array.prototype.forEach.call(secs, function (s) {
      if (s === tariffSec || s === alertsSec) return;   /* 电价区 / 告警段不动 */
      if (s.parentNode) s.parentNode.removeChild(s);
    });
    /* 删掉旧按钮区（它要被新的替换） */
    var oldActions = bodyEl.querySelector('.thr-actions-thresholds');
    if (oldActions && oldActions.parentNode) {
      oldActions.parentNode.removeChild(oldActions);
    }
    /* 把新的阈值区 + 按钮区插到电价区之前 */
    var anchor = bodyEl.querySelector('.thr-sec-tariff');
    Array.prototype.forEach.call(newSecs, function (s) {
      /* ⚠️ 电价区与告警段都用**旧的**（上面没删它们）⇒ 这里必须一并跳过。
         只跳过电价区会插进来一个**新的告警段** ⇒ 抽屉里出现两个告警段
         （实测分段数 7 → 8）。 */
      if (s === newTariff || s === newAlerts) return;
      if (anchor && anchor.parentNode) {
        anchor.parentNode.insertBefore(s, anchor);
      }
    });
    if (newActions && anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(newActions, anchor);
    }
    bindBody();
  }

  /* 把电价面板挂到抽屉里 */
  function mountTariff() {
    if (!bodyEl) return;
    var host = bodyEl.querySelector('[data-tariff-box]');
    if (!host) return;
    var T = window.FnStyleTariff;
    if (!T || !T.mount) {
      host.innerHTML = '<div class="mon-info">电价配置模块不可用</div>';
      return;
    }
    T.mount(host, {
      onSaved: function () {
        /* 电价变了 → 让所有依赖电费数字的地方立刻重算 */
        if (T.invalidateStats) T.invalidateStats();
        if (window.FnStyleMonitor && window.FnStyleMonitor.refresh) {
          window.FnStyleMonitor.refresh();
        }
      }
    });
  }

  /* --------------------------------------------------------------------------
     告警通知配置（v1.1.84）
     -------------------------------------------------------------------------- */

  /* 拉取告警通知配置，并把告警段重画出来 */
  function loadAlerts() {
    if (!bodyEl || !window.fetch) return;
    return fetch(BASE + 'monitor/alerts', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.success) return null;
        alertsData = j;
        renderAlertsSection();
        refreshAlertStatus();
        return j;
      })
      .catch(function () { /* 静默失败：保持占位 */ });
  }

  /* 只重画告警段（不动阈值表单与电价面板） */
  function renderAlertsSection() {
    if (!bodyEl) return;
    var old = bodyEl.querySelector('.thr-sec-alerts');
    if (!old) return;
    var tmp = document.createElement('div');
    tmp.innerHTML = alertsSectionHtml();
    var fresh = tmp.querySelector('.thr-sec-alerts');
    if (!fresh) return;
    old.parentNode.replaceChild(fresh, old);
    bindAlerts();
  }

  /* 收集告警段的表单值 */
  function collectAlerts() {
    var out = {};
    var en = bodyEl.querySelector('[data-al-enabled]');
    out.enabled = !!(en && en.checked);

    var chs = bodyEl.querySelectorAll('[data-al-channel]');
    out.channels = Array.prototype.map.call(chs, function (e) {
      return e.checked ? e.getAttribute('data-al-channel') : null;
    }).filter(Boolean);

    var lvs = bodyEl.querySelectorAll('[data-al-level]');
    out.levels = Array.prototype.map.call(lvs, function (e) {
      return e.checked ? e.getAttribute('data-al-level') : null;
    }).filter(Boolean);

    var ms = {};
    Array.prototype.forEach.call(
      bodyEl.querySelectorAll('[data-al-metric]'), function (e) {
        ms[e.getAttribute('data-al-metric')] = !!e.checked;
      });
    out.metrics = ms;

    var url = bodyEl.querySelector('[data-al-url]');
    if (url) out.webhook_url = url.value;

    var sec = bodyEl.querySelector('[data-al-secret]');
    /* ⚠️ 密钥留空 = 不修改（后端 save_config 对空串是「继承旧值」语义）*/
    if (sec && sec.value) out.webhook_secret = sec.value;

    var cd = bodyEl.querySelector('[data-al-cooldown]');
    if (cd && cd.value !== '') {
      var n = Number(cd.value);
      if (isFinite(n)) out.cooldown = Math.round(n);
    }
    return out;
  }

  /* 告警段的事件绑定 */
  function bindAlerts() {
    if (!bodyEl) return;

    var saveBtn = bodyEl.querySelector('[data-al-save]');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var msg = bodyEl.querySelector('[data-al-msg]');
        var payload = collectAlerts();
        saveBtn.disabled = true;
        if (msg) msg.textContent = '保存中…';
        fetch(BASE + 'monitor/alerts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (r) {
          return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        }).then(function (res) {
          saveBtn.disabled = false;
          if (!res.ok || !res.j || !res.j.success) {
            if (msg) msg.textContent = (res.j && res.j.error) || '保存失败';
            if (msg) msg.className = 'thr-msg err';
            return;
          }
          alertsData = res.j;
          if (msg) {
            msg.textContent = '已保存';
            msg.className = 'thr-msg ok';
          }
          refreshAlertStatus();
          setTimeout(function () {
            var m2 = bodyEl.querySelector('[data-al-msg]');
            if (m2) { m2.textContent = ''; m2.className = 'thr-msg'; }
          }, 2500);
        }).catch(function () {
          if (msg) { msg.textContent = '保存失败（网络错误）'; msg.className = 'thr-msg err'; }
          saveBtn.disabled = false;
        });
      });
    }

    var testBtn = bodyEl.querySelector('[data-al-test]');
    if (testBtn) {
      testBtn.addEventListener('click', function () {
        var msg = bodyEl.querySelector('[data-al-msg]');
        testBtn.disabled = true;
        if (msg) { msg.textContent = '发送中…'; msg.className = 'thr-msg'; }

        /* ⚠️ v1.1.84 改进：后端的「测试推送」读的是**已保存**的配置
           （不接受请求体里的地址），所以用户「填了地址还没保存就点测试」
           会得到一句莫名其妙的「请先配置 Webhook 地址」。
           ⇒ 测试前先把当前表单存一次，用真实的用户意图去测。
           ⚠️ 存的是「用户当前填的内容」，不是「旧配置」⇒ 语义一致。 */
        var payload = collectAlerts();
        fetch(BASE + 'monitor/alerts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        }).then(function (r) {
          return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        }).then(function (sres) {
          if (!sres.ok || !sres.j || !sres.j.success) {
            throw new Error((sres.j && sres.j.error) || '保存失败');
          }
          alertsData = sres.j;
          return fetch(BASE + 'monitor/alerts/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
          });
        }).then(function (r) {
          return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        }).then(function (res) {
          testBtn.disabled = false;
          if (!res.ok || !res.j || !res.j.success) {
            if (msg) {
              msg.textContent = (res.j && res.j.error) || '测试推送失败';
              msg.className = 'thr-msg err';
            }
            return;
          }
          if (msg) { msg.textContent = '测试消息已发送'; msg.className = 'thr-msg ok'; }
          refreshAlertStatus();
        }).catch(function (e) {
          if (msg) {
            msg.textContent = (e && e.message) ? e.message : '测试推送失败（网络错误）';
            msg.className = 'thr-msg err';
          }
          testBtn.disabled = false;
        });
      });
    }
  }

  /* 刷新「最近推送状态」提示
     ⚠️ 推送是**看不见**的操作 —— 没有状态回显，
        用户无法确认告警到底推没推出去。这是必要的可观测性。 */
  function refreshAlertStatus() {
    if (!bodyEl || !window.fetch) return;
    fetch(BASE + 'monitor/alerts/status', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !j.success) return;
        var host = bodyEl.querySelector('[data-al-status]');
        if (!host) return;
        var last = j.last || {};
        var parts = [];
        if (!j.enabled) {
          parts.push('告警推送当前未启用。');
        } else if (last && last.at) {
          var t = new Date((last.at || 0) * 1000);
          var ts = t.getFullYear() + '-' +
            String(t.getMonth() + 1).padStart(2, '0') + '-' +
            String(t.getDate()).padStart(2, '0') + ' ' +
            String(t.getHours()).padStart(2, '0') + ':' +
            String(t.getMinutes()).padStart(2, '0');
          parts.push('最近检查：' + ts);
          if (last.alerts) {
            parts.push('当前超限 ' + last.alerts + ' 项');
          } else {
            parts.push('当前无超限项');
          }
          if (last.message) parts.push(last.message);
        } else {
          parts.push('尚未产生判定记录（服务刚启动时需要等待一个判定周期）。');
        }
        if (j.fnos && j.fnos.available === false && j.fnos.detail) {
          parts.push('飞牛通知中心：' + j.fnos.detail);
        }
        host.textContent = parts.join('　·　');
      })
      .catch(function () { /* 静默失败 */ });
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
        /* ⚠️ v1.1.84：这里的「恢复默认」**只针对阈值门限**，
           不碰电价。抽屉里现在有两类配置（阈值 + 电价），
           但两类配置的后端接口、生命周期都不同 ——
           阈值是 POST /api/monitor/thresholds + {__reset:true}，
           电价是 POST /api/power/tariff。
           如果一个按钮同时清两个，用户按下「恢复默认」时
           自己的电价设置也被悄悄抹掉 —— 这是不可逆的数据损失。
           ⇒ 保持单一职责：只重置阈值。
           ⇒ 抽屉底部按钮文案也改成了「恢复默认门限」以消除歧义。 */
        TH.save({ __reset: true }).then(function (res) {
          resetBtn.disabled = false;
          if (!res.ok) {
            if (msg) msg.textContent = res.message || '恢复默认失败';
            return;
          }
          if (TH.applySaved) TH.applySaved(res.data);
          /* 重新渲染阈值表单（恢复默认后输入框要回到默认值）。
             ⚠️ v1.1.84：只重绘阈值区，**不动电价面板** ——
                用户可能正在改单价，不该被「恢复默认门限」顺带清空。 */
          renderThresholdsOnly(res.data);
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
