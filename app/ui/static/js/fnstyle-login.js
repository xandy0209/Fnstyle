/*!
 * fnstyle-login.js —— FN定制化配置：登录页运行时注入（v2 架构）
 *
 * ══════════════════════════════════════════════════════════════════════
 * 一、为什么是这个文件（v1 → v2 的架构级修正）
 * ══════════════════════════════════════════════════════════════════════
 *
 * 【v1 的做法（有问题）】
 *   直接改写 /usr/trim/www/assets/ 下的原生 CSS/JS：
 *     · apply_style_overrides.py  → 追加 .gradient-border 覆盖规则到
 *                                    assets/login-form-*.css
 *     · patch_cache_bust.py      → 改写 assets/*.js 里的
 *                                    /deskdata/fnstyle/xxx.png?v=<时间戳>
 *   问题：**文件名不变，内容变了**。
 *
 * 【fnOS 的缓存契约】
 *   /usr/trim/nginx/conf/conf.d/html_nocache.conf：
 *       location ~ ^/assets/* {
 *           add_header cache-control "max-age=1209600, immutable";
 *       }
 *   ⇒ 14 天 + immutable，浏览器**连条件请求都不发**。
 *
 * 【v1 踩的坑（2026-11-13 实测）】
 *   违反 immutable 契约 ⇒ 浏览器 14 天内一直用旧版：
 *     ① 用户在 FnStyle 里换图/调位置 → 点「应用到系统」→ 页面毫无变化
 *     ② 用户卸载 FnStyle → 服务端已恢复纯净版 → 浏览器仍是旧版
 *        ⇒ 表现为「登录页 LOGO 破图 + 卡片跑到 60vw 位置 + 背景变深蓝灰」
 *
 * 【为什么不能改 nginx 来解决】
 *   nginx -t 会从**加密的** /usr/trim/share/.restore/ng.conf.zip
 *   重建整个 /usr/trim/nginx/conf/ 目录
 *   ⇒ 实测：写进去的 conf.d/*.conf 和 nginx.conf 的改动
 *     在下一次 nginx -t 时全部消失 ⇒ 这条路完全堵死。
 *
 * 【v2 的做法（本文件）】
 *   ① **完全不碰 /assets/ 下的任何文件**
 *   ② 把本脚本放到 /assets/ 之外（/fnstyle-login.js）
 *      —— 该路径**不带 immutable 头**（实测 404 时由 fallback 处理，
 *         正常部署时走 nginx 默认，无 max-age=1209600）
 *   ③ 由 index.html 以 <script src="./fnstyle-login.js"> 引入
 *   ④ 用 DOM API 在运行时设置：
 *        · 登录页 LOGO    → 替换 <img> 的 src
 *        · 登录页背景    → 注入 <style> 覆盖 body 背景
 *        · 登录框毛玻璃/透明度/位置 → 注入 <style> 覆盖 .gradient-border
 *      ⇒ 全部走「独立 URL + 运行时 DOM」，**不违反任何缓存契约**
 *
 * 【为什么这样能生效且不会反复】
 *   · 本脚本自身 URL 固定（/fnstyle-login.js），但它**不带 immutable**，
 *     浏览器每次都会重新验证 ⇒ 改配置后刷新即生效
 *   · 它读的配置是 deskdata/fnstyle.json?v=<时间戳>（动态时间戳）
 *   · 它设置的图片 URL 也带 ?v=<时间戳> ⇒ 换图必生效
 *
 * ══════════════════════════════════════════════════════════════════════
 * 二、注入的 DOM 挂载点
 * ══════════════════════════════════════════════════════════════════════
 *   登录卡片 : .gradient-border（原生 class，来自 login-C-JnA4tt.js）
 *   LOGO     : .gradient-border img[alt="logo"]（原生 alt 固定为 "logo"）
 *   背景     : body / html（原生用 /static/bg/wallpaper-1.webp 或内联渐变）
 *
 *   ⚠️ 这些 class 名来自 fnOS 原生构建，可能随版本变化。
 *      ⇒ 每个挂载点都做了多候选匹配 + 失败降级（不影响页面可用）。
 *
 * ══════════════════════════════════════════════════════════════════════
 * 三、字段语义（与 fnstyle.json 一致）
 * ══════════════════════════════════════════════════════════════════════
 *   loginLogo        登录页 LOGO 图片路径
 *   loginBackground  登录页背景图/视频路径
 *   loginOpacity     0..30   → 卡片背景 alpha = opacity/100
 *   FndeskGlass      -1..50  → -1=飞牛原设(blur 8px)，0=关闭，N=blur(Npx)
 *   fndeskLoginVW    -80..80 → 卡片水平偏移（vw）
 *   fndeskLoginVH    -80..80 → 卡片垂直偏移（vh）
 *   appTitle         浏览器标签页标题（登录页 + 桌面）
 *   deviceLogo       桌面「我的设备」区域的设备 LOGO
 */
(function () {
  'use strict';

  var LOG_PREFIX = '[FnStyle/login]';
  function log() {
    if (window.__FNSTYLE_DEBUG__) {
      try { console.log.apply(console, [LOG_PREFIX].concat([].slice.call(arguments))); } catch (e) {}
    }
  }

  /* ------------------------------------------------------------------
   * 配置读取
   * ---------------------------------------------------------------- */
  var CFG = null;
  var CFG_PROMISE = null;
  var CFG_AT = 0;
  var CFG_TTL = 1500;   // 1.5 秒后重新拉，保证「卸载/还原」能在一个已打开的标签页里生效

  function loadConfig() {
    var now = (new Date).getTime();
    if (CFG_PROMISE && (now - CFG_AT) < CFG_TTL) return CFG_PROMISE;
    CFG_AT = now;
    CFG_PROMISE = fetch('deskdata/fnstyle.json?v=' + now)
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function (e) { log('配置读取失败，降级为空', e); return {}; })
      .then(function (c) { CFG = c || {}; return CFG; });
    return CFG_PROMISE;
  }

  /* ------------------------------------------------------------------
   * 卸载 / 还原后的「自禁用」开关（2026-11-14）
   * 还原脚本把配置写成 {"__fnstyleDisabled":true}；已打开的旧标签页
   * 会在下一轮重试里读到它，清掉注入样式、停掉标题覆盖并刷新一次页面。
   * ---------------------------------------------------------------- */
  function isDisabled(cfg) {
    return !!(cfg && (cfg.__fnstyleDisabled === true ||
                      cfg.fnstyleDisabled === true));
  }

  function purgeInjected() {
    try {
      titleValue = '';
      if (retryTimer) { clearInterval(retryTimer); retryTimer = null; }
      var bg = document.getElementById('fnstyle-login-bg');
      if (bg) bg.remove();
      var styles = document.querySelectorAll('style[id^="fnstyle"]');
      Array.prototype.forEach.call(styles, function (s) { s.remove(); });
    } catch (e) {}
  }

  function disableNow() {
    var first = !window.__fnstyleDisabled;
    window.__fnstyleDisabled = true;
    purgeInjected();
    if (!first) return;
    try {
      if (sessionStorage.getItem('fnstyle_restored') === '1') return;
      sessionStorage.setItem('fnstyle_restored', '1');
      setTimeout(function () { location.reload(); }, 60);
    } catch (e) {}
  }

  function markEnabled() {
    window.__fnstyleDisabled = false;
    try { sessionStorage.removeItem('fnstyle_restored'); } catch (e) {}
  }

  /* ------------------------------------------------------------------
   * 工具
   * ---------------------------------------------------------------- */
  function num(v, def) {
    var n = parseInt(v, 10);
    if (isNaN(n)) return def;
    return n;
  }

  /** 带时间戳的资源 URL（破浏览器缓存） */
  function bust(url) {
    if (!url) return '';
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + (new Date).getTime();
  }

  /** 确保 <style id> 存在并写入内容 */
  function ensureStyle(id, css) {
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement('style');
      el.id = id;
      (document.head || document.documentElement).appendChild(el);
    }
    if (el.textContent !== css) el.textContent = css;
    return el;
  }

  /* ------------------------------------------------------------------
   * 挂载点查找（多候选 + 降级）
   * ---------------------------------------------------------------- */

  /** 登录卡片 */
  function findCard() {
    return document.querySelector('.gradient-border') ||
           document.querySelector('[class*="gradient-border"]');
  }

  /** 登录页 LOGO（原生 alt 固定 "logo"） */
  function findLogo() {
    var card = findCard();
    if (card) {
      var imgs = card.querySelectorAll('img');
      for (var i = 0; i < imgs.length; i++) {
        if ((imgs[i].alt || '') === 'logo') return imgs[i];
      }
      if (imgs.length) return imgs[0];
    }
    var all = document.querySelectorAll('img[alt="logo"]');
    return all.length ? all[0] : null;
  }

  /* ------------------------------------------------------------------
   * 功能 1：登录页 LOGO
   * ---------------------------------------------------------------- */
  function applyLogo(cfg) {
    var url = (cfg.loginLogo || '').trim();
    if (!url) return false;   // 空 = 用飞牛原生图标，不干预

    var img = findLogo();
    if (!img) return false;

    var want = bust(url);
    if (img.getAttribute('src') === want) return true;

    // 保留原生尺寸/类名，只换 src
    img.setAttribute('src', want);
    // 破图时隐藏 alt 文本（原生 data:svg 转外链失败会显示 "logo" 字样）
    img.setAttribute('onerror', 'this.style.visibility="hidden"');
    log('LOGO → ' + url);
    return true;
  }

  /* ------------------------------------------------------------------
   * 功能 2：登录页背景
   * ---------------------------------------------------------------- */
  function applyBackground(cfg) {
    var url = (cfg.loginBackground || '').trim();
    if (!url) return false;

    var isVideo = /\.(mp4|webm)(\?|$)/i.test(url);
    var css;
    if (isVideo) {
      // 视频背景：用 <video> 铺满，CSS 只负责兜底色
      css = '#fnstyle-login-bg{display:none}';
      ensureStyle('fnstyle_login_bg_css', css);
      mountVideo(url);
    } else {
      css = '#fnstyle-login-bg{display:block!important;position:fixed;inset:0;' +
            'width:100vw;height:100vh;z-index:0;pointer-events:none;' +
            'background-image:url("' + bust(url) + '");' +
            'background-size:cover;background-position:center;background-repeat:no-repeat;}';
      ensureStyle('fnstyle_login_bg_css', css);
      ensureBgDiv();
    }
    log('背景 → ' + url);
    return true;
  }

  function ensureBgDiv() {
    var el = document.getElementById('fnstyle-login-bg');
    if (!el) {
      el = document.createElement('div');
      el.id = 'fnstyle-login-bg';
      (document.body || document.documentElement).insertBefore(el, document.body.firstChild);
    }
    return el;
  }

  var videoEl = null;
  function mountVideo(url) {
    if (!videoEl) {
      videoEl = document.createElement('video');
      videoEl.id = 'fnstyle-login-bg';
      videoEl.autoplay = true;
      videoEl.loop = true;
      videoEl.muted = true;
      videoEl.playsInline = true;
      videoEl.style.cssText =
        'display:block;position:fixed;inset:0;width:100vw;height:100vh;' +
        'z-index:0;pointer-events:none;object-fit:cover;';
      (document.body || document.documentElement).insertBefore(videoEl, document.body.firstChild);
    }
    var want = bust(url);
    if (videoEl.getAttribute('src') !== want) {
      videoEl.setAttribute('src', want);
      videoEl.play().catch(function () {});
    }
  }

  /* ------------------------------------------------------------------
   * 功能 3：登录框样式（毛玻璃 / 透明度 / 位置）
   * ---------------------------------------------------------------- */
  function applyCardStyle(cfg) {
    var glass = num(cfg.FndeskGlass, -1);
    var opacity = num(cfg.loginOpacity, 10);
    var vw = num(cfg.fndeskLoginVW, 0);
    var vh = num(cfg.fndeskLoginVH, 0);

    // 毛玻璃：-1 = 飞牛原设（原生实测为 blur(8px)）
    var blur;
    if (glass === -1) blur = 8;
    else if (glass === 0) blur = 0;
    else blur = Math.max(0, Math.min(100, glass));

    // 透明度：0..30 → alpha 0..0.30（10 = 原生 rgba(255,255,255,.1)）
    opacity = Math.max(0, Math.min(30, opacity));
    var alpha = Math.round(opacity / 100.0 * 10000) / 10000;

    vw = Math.max(-80, Math.min(80, vw));
    vh = Math.max(-80, Math.min(80, vh));

    // ⚠️ 三个值全都是「默认值」时 ⇒ 不注入任何覆盖 ⇒ 保持飞牛原生外观。
    //    这是关键：避免「什么都没配」却被强行改样式。
    if (glass === -1 && opacity === 10 && vw === 0 && vh === 0) {
      var old = document.getElementById('fnstyle_login_card_css');
      if (old) old.remove();
      return false;
    }

    var css =
      '.gradient-border{' +
      'position:relative;' +
      'margin-left:' + vw + 'vw;' +
      'margin-top:' + vh + 'vh;' +
      'background-color:rgba(255,255,255,' + alpha.toFixed(4) + ')!important;' +
      'backdrop-filter:blur(' + blur + 'px)!important;' +
      '-webkit-backdrop-filter:blur(' + blur + 'px)!important;' +
      '}';
    ensureStyle('fnstyle_login_card_css', css);
    log('卡片样式 → blur=%spx alpha=%s offset=%svw/%svh', blur, alpha, vw, vh);
    return true;
  }

  /* ------------------------------------------------------------------
   * 功能 4：浏览器标签页标题
   * ----------------------------------------------------------------
   * 替代 v3 的 patch_title_overrides.py（那版改写 assets/index-*.js 的 R3()）。
   *
   * ⚠️ 为什么用运行时覆盖而不是改 bundle：
   *   · assets/* 是 immutable 的，改 bundle 内容 = 浏览器 14 天不重拉
   *   · bundle 文件名**绝不能改**（193 个 chunk 依赖 from"./index-XXX.js"）
   *   · 原生 R3() 会在 SPA 加载时覆盖 document.title
   *     ⇒ 需要监听并持续维持
   *
   * 做法：MutationObserver 监听 <title> 变化 + visibilitychange，
   *       标题被改回原生时立刻改回配置值。
   */
  var titleValue = '';
  var titleBound = false;

  function currentBaseTitle() {
    // 原生格式：「主机名 - 飞牛 fnOS」，取横杠后半段作为品牌部分
    var t = document.title || '';
    var i = t.indexOf(' - ');
    return i >= 0 ? t.slice(i + 3) : '飞牛 fnOS';
  }

  function applyTitle(cfg) {
    var want = (cfg.appTitle || '').trim();
    if (!want || want === '飞牛 fnOS') {
      titleValue = '';
      return false;
    }
    titleValue = want;

    // 立刻设置
    if (document.title !== want) document.title = want;

    if (titleBound) return true;
    titleBound = true;

    // 监听 title 变化（原生 R3() 会改）
    var head = document.querySelector('head');
    if (head && window.MutationObserver) {
      var obs = new MutationObserver(function () {
        if (titleValue && document.title !== titleValue) {
          document.title = titleValue;
        }
      });
      obs.observe(head, { childList: true, subtree: true, characterData: true });
    }

    // 切标签页时原生也会改
    document.addEventListener('visibilitychange', function () {
      if (titleValue) document.title = titleValue;
    });

    log('标题 → ' + want);
    return true;
  }

  /* ------------------------------------------------------------------
   * 功能 5：桌面设备 LOGO
   * ----------------------------------------------------------------
   * 原生「我的设备」区域的 LOGO 是内联 SVG 或静态资源，
   * 用 DOM 匹配替换 src。若找不到匹配元素则静默跳过（不影响其他功能）。
   */
  function applyDeviceLogo(cfg) {
    var url = (cfg.deviceLogo || '').trim();
    if (!url) return false;

    // 设备区域通常在「我的设备」卡片里，LOGO 是个 img
    var cands = document.querySelectorAll(
      'img[src*="deviceLogo"], img[src*="device-logo"], img[data-device-logo], ' +
      '[class*="device"] img[alt="logo"], [class*="device"] img');
    if (!cands.length) return false;

    var want = bust(url);
    var hit = false;
    for (var i = 0; i < cands.length; i++) {
      var el = cands[i];
      // 跳过小图标（宽度很小的那种多半是菜单图标）
      if (el.naturalWidth && el.naturalWidth < 24) continue;
      if (el.getAttribute('src') === want) { hit = true; continue; }
      el.setAttribute('src', want);
      el.setAttribute('onerror', 'this.style.visibility="hidden"');
      hit = true;
    }
    if (hit) log('设备 LOGO → ' + url);
    return hit;
  }

  /* ------------------------------------------------------------------
   * 调度
   * ---------------------------------------------------------------- */
  function applyAll() {
    loadConfig().then(function (cfg) {
      if (isDisabled(cfg)) { disableNow(); return; }
      markEnabled();
      try { applyLogo(cfg); } catch (e) { log('LOGO 失败', e); }
      try { applyBackground(cfg); } catch (e) { log('背景失败', e); }
      try { applyCardStyle(cfg); } catch (e) { log('卡片样式失败', e); }
      try { applyTitle(cfg); } catch (e) { log('标题失败', e); }
      try { applyDeviceLogo(cfg); } catch (e) { log('设备LOGO失败', e); }
    });
  }

  /**
   * 登录页是 SPA，卡片是异步渲染出来的 ⇒ 必须重试到成功。
   * 桌面页也会加载本脚本，但桌面页没有 .gradient-border ⇒ 60 次后放弃，
   * 不会影响桌面。
   */
  var retryTimer = null;
  var retryCount = 0;

  function startRetry(cfg) {
    if (retryTimer) return;
    retryTimer = setInterval(function () {
      if (window.__fnstyleDisabled) { clearInterval(retryTimer); retryTimer = null; return; }
      // 重试窗口最长 30 秒，期间每 1.5 秒重读一次配置：
      // 卸载/还原一落地，旧标签页就会立刻停止套用旧样式
      loadConfig().then(function (fresh) {
        if (isDisabled(fresh)) { disableNow(); return; }
      });
      retryCount++;
      var card = findCard();
      var logo = findLogo();
      var done = true;
      try { if (!applyLogo(cfg) && !logo) done = false; } catch (e) { done = false; }
      try { if (!card) done = false; } catch (e) { done = false; }
      // 设备 LOGO 所在区域在桌面 SPA 里可能晚于本脚本才渲染出来。
      // applyAll() 只在 boot 时调用过一次 applyDeviceLogo，晚渲染的元素会漏掉，
      // 表现为「设备 LOGO 配了但没生效」。这里每次重试顺带补一次；
      // 前 20 次（10 秒）内没等到就放弃，避免登录页（本就没有设备 LOGO）
      // 把这个定时器一直拖到 60 次上限。
      try {
        if (!applyDeviceLogo(cfg) && retryCount < 20) done = false;
      } catch (e) {
        if (retryCount < 20) done = false;
      }
      if (done || retryCount >= 60) {
        clearInterval(retryTimer);
        retryTimer = null;
      }
    }, 500);
  }

  function boot() {
    loadConfig().then(function (cfg) {
      if (isDisabled(cfg)) { disableNow(); return; }
      applyAll();
      startRetry(cfg);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
