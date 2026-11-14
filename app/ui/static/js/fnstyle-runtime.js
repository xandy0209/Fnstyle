/*!
 * fnstyle-runtime.js —— FnStyle 桌面运行时（注入到 fnOS 桌面页）
 *
 * ══════════════════════════════════════════════════════════════════════
 * 一、为什么需要这个文件
 * ══════════════════════════════════════════════════════════════════════
 * fnOS 桌面原生只消费 fnstyle.json 里的 4 个字段：
 *     appTitle / deviceLogo / loginLogo / loginBackground
 * 其余个性化项（桌面壁纸、隐藏登录框名字、图标排序、点击音效、
 * 任务栏置底、隐藏系统图标）在 fnOS 原生代码里**没有任何消费端**。
 *
 * 这些功能原本由 FnDesk 的 fndesk.js（209KB 注入脚本）实现。
 * FnDesk 卸载后该脚本被彻底清理 ⇒ 配置写进去了，但没人读 ⇒ 页面无变化。
 *
 * 本文件是 FnStyle 自己的等价实现：纯原生 JS、零外部依赖、
 * 不含任何 FnDesk 残留逻辑（不含右键菜单注入、不含 Live2D、
 * 不含应用启动器），只负责兑现 fnstyle.json 里的个性化配置。
 *
 * ══════════════════════════════════════════════════════════════════════
 * 二、注入方式
 * ══════════════════════════════════════════════════════════════════════
 * 由 apply 阶段写进 /usr/trim/www/ 并在 index.html 里以
 *     <script src="./fnstyle-runtime.js"></script>
 * 引入（放在主包 script 之前，先加载以便监听 DOM 变化）。
 *
 * 幂等：重复注入只会覆盖同名 style 元素，不会叠加副作用。
 * ══════════════════════════════════════════════════════════════════════
 */
(function () {
  'use strict';

  var LOG_PREFIX = '[FnStyle]';
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
    // 始终带时间戳破缓存 —— 配置内容变了必须重新拉
    CFG_PROMISE = fetch('deskdata/fnstyle.json?v=' + now)
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function (e) { log('配置读取失败，降级为空', e); return {}; })
      .then(function (c) { CFG = c || {}; return CFG; });
    return CFG_PROMISE;
  }

  /* ------------------------------------------------------------------
   * 卸载 / 还原后的「自禁用」开关（2026-11-14）
   *
   * 还原脚本会把 deskdata/fnstyle.json 写成 {"__fnstyleDisabled":true}。
   * 旧标签页里本脚本早把配置缓存在内存里，且 MutationObserver 会在每次
   * DOM 重绘时把自定义壁纸再贴回去 —— 这正是「先显示系统原生样式，
   * 随后又跳回软件配置样式」的浏览器侧成因。
   *
   * ⇒ 配置带 1.5 秒 TTL，observer/retry 每轮都能读到最新状态；
   *   一旦检测到禁用：清掉所有注入的 DOM / 样式、停掉定时器、刷新一次页面。
   *   刷新后 index.html 已不含脚本标签（或加载的是无操作 stub），不会循环。
   * ---------------------------------------------------------------- */
  function isDisabled(cfg) {
    return !!(cfg && (cfg.__fnstyleDisabled === true ||
                      cfg.fnstyleDisabled === true));
  }

  function purgeInjected() {
    try {
      if (retryTimer) { clearInterval(retryTimer); retryTimer = null; }
      var el = document.getElementById('fnstyle_wallpaper');
      if (el) el.remove();
      var styles = document.querySelectorAll('style[id^="fnstyle"]');
      Array.prototype.forEach.call(styles, function (s) { s.remove(); });
      var items = document.querySelectorAll('[style*="padding-bottom: 60px"]');
      Array.prototype.forEach.call(items, function (n) {
        try { n.style.removeProperty('padding-bottom'); } catch (e) {}
      });
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
    return isNaN(n) ? def : n;
  }

  /** 确保某个 <style> 存在并返回它 */
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

  /** 生成带时间戳的资源 URL（破浏览器缓存） */
  function bust(url) {
    if (!url) return '';
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + (new Date).getTime();
  }

  /** 桌面主容器（FnDesk 当年用 .fixed.inset-y-0.left-0.z-\[1\] 定位） */
  function desktopRoot() {
    return document.querySelector('.fixed.inset-y-0.left-0.z-\\[1\\]');
  }

  /* ------------------------------------------------------------------
   * 功能 1：桌面壁纸
   *   字段：desktopWallpaper
   *   形如 /deskdata/fnstyle/desktopWallpaper.jpg
   *   也支持 .mp4 / .webm 视频壁纸
   * ---------------------------------------------------------------- */
  function applyWallpaper(cfg) {
    var url = cfg.desktopWallpaper;
    if (!url || url === '/static/bg/wallpaper-1.webp') {
      var old = document.getElementById('fnstyle_wallpaper');
      if (old) old.remove();
      return true;
    }
    // 已存在且源相同 → 不重复创建
    var cur = document.getElementById('fnstyle_wallpaper');
    if (cur && cur.dataset.src === url) return true;

    var root = desktopRoot();
    if (!root) return false; // 桌面还没渲染出来，等 DOM 变化时再试

    if (cur) cur.remove();

    var box = document.createElement('div');
    box.id = 'fnstyle_wallpaper';
    box.dataset.src = url;
    box.style.cssText =
      'position:absolute;inset:0;z-index:0;overflow:hidden;opacity:0;' +
      'transition:opacity 1.6s ease-out;pointer-events:none;';

    var media;
    if (/\.(mp4|webm)(\?|$)/i.test(url)) {
      media = document.createElement('video');
      media.autoplay = true; media.loop = true; media.muted = true;
      media.playsInline = true;
    } else {
      media = document.createElement('img');
    }
    media.style.cssText =
      'position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;';
    media.src = bust(url);

    var evt = (media.tagName === 'VIDEO') ? 'loadeddata' : 'load';
    media.addEventListener(evt, function () {
      box.appendChild(media);
      root.before(box);
      setTimeout(function () { box.style.opacity = '1'; }, 10);
    }, { once: true });
    media.addEventListener('error', function () {
      log('壁纸加载失败：' + url);
    }, { once: true });
    return true;
  }

  /* ------------------------------------------------------------------
   * 功能 2：隐藏登录框里的 NAS 名字
   *   字段：nasNameDisplay  1=显示 2=隐藏
   *   实现：把 <p class="select-none"> 换成 <br>（FnDesk 当年的做法）
   * ---------------------------------------------------------------- */
  function applyNasNameDisplay(cfg) {
    if (num(cfg.nasNameDisplay, 1) !== 2) return;

    var tries = 0;
    var timer = setInterval(function () {
      tries++;
      var nodes = document.querySelectorAll('p.select-none');
      if (nodes.length > 0) {
        nodes.forEach(function (p) {
          var br = document.createElement('br');
          if (p.parentNode) {
            p.parentNode.insertBefore(br, p);
            p.remove();
          }
        });
        clearInterval(timer);
        log('已隐藏登录框名称');
      } else if (tries >= 30) {
        clearInterval(timer);
      }
    }, 100);
  }

  /* ------------------------------------------------------------------
   * 功能 3：任务栏沉底横置
   *   字段：Fndesk_taskbar  1=沉底横置 0=保持原样
   *   注：字段名是下划线的 Fndesk_taskbar，写成驼峰会失效
   *
   * ════════════════════════════════════════════════════════════════════
   * 为什么必须逐层改，不能只改最外三层？
   * ════════════════════════════════════════════════════════════════════
   * fnOS 桌面任务栏的真实 DOM（从 /usr/trim/www/assets/index-*.js 逆向）：
   *
   *   L0  div.fixed.inset-y-0.left-0.z-[1]     ← 有内联 style="width:66px"
   *       └ L1 div.box-border.size-full.py-4.pl-3.5
   *           └ L2 div.h-full.rounded-md.border...backdrop-blur-[20px]
   *               └ L3 div.box-border.flex.h-full.flex-col.py-3
   *                   ├ L3-0 J5e  div.flex.flex-col.items-center        ← 桌面/所有应用（竖排！）
   *                   ├ L3-1 Z5e  div.relative.flex.flex-1.flex-col
   *                   │      └  div.scrollbar-hidden.absolute.inset-0.flex.flex-col.items-end  ← 任务窗口列表（竖排！）
   *                   └ L3-2 K8e→Z8e→Q8e→Y5e
   *                          div.flex.flex-col.items-center.justify-between.gap-5.pt-2  ← 底部图标组（竖排！）
   *
   * 只改 L0/L1/L2/L3 的 flexDirection 是不够的：
   *   ① L0 的内联 width:66px 把可用宽度锁死 → 横排放不下就换行成 2 列
   *   ② L3-0 / L3-2 自身仍是 flex-col → 图标组内部照样竖排
   *   ③ 桌面容器 z8e 带 pl-[66px] → 置底后左侧仍留 66px 空白
   *
   * 因此这里照搬 FnDesk 原版 moveTaskbarToBottom 的完整逐层做法：
   *   改 L0/L1/L2/L3 + L3 的 3 个直接子元素 + 内部 .scrollbar-hidden，
   *   并额外处理桌面容器的 pl-[66px]（原版用 ".desktop > div"，
   *   该选择器在当前 fnOS 已不存在，故改为直接命中 .desktop 容器）。
   * ════════════════════════════════════════════════════════════════════
   * ---------------------------------------------------------------- */
  function applyTaskbar(cfg) {
    // ⚠️ null / 缺值一律视为「关闭」（num(null, 0) → 0）。
    //    界面上 switch 的 null 也显示「关闭」—— 两侧语义必须一致，
    //    否则会出现「界面显示开启、实际没置底」的骗人现象。
    if (num(cfg.Fndesk_taskbar, 0) !== 1) return;

    var root = desktopRoot();
    if (!root) return false;

    // 桌面主容器由几层嵌套组成，逐层展开成横向条
    var n = root.firstElementChild;
    if (!n) return false;
    var o = n.firstElementChild;
    if (!o) return false;
    var i = o.firstElementChild;
    if (!i) return false;

    /* ════════════════════════════════════════════════════════════════
     * 关键：用「样式表 + data 标记」双轨，而不是只改 inline style。
     *
     * 原因：fnOS 桌面是 React SPA，L0 上的 style={{width:'66px'}} 是
     *   React 渲染属性。React 重绘时执行 element.style.width = '66px'，
     *   这会**直接把 inline style 上的 !important 标志抹掉**，
     *   于是我们的覆盖失效、任务栏又缩回 66px 竖排。
     *
     * 而样式表里的 !important 优先级高于任何 inline 非 important 声明，
     *   React 改不动它 ⇒ 用样式表才是能扛住重绘的稳定方案。
     *
     * 双轨分工：
     *   样式表  —— 负责所有「会被 React 重绘覆盖」的属性（尺寸、方向、定位）
     *   inline  —— 负责「React 根本不会碰」的补充属性
     * ════════════════════════════════════════════════════════════════ */
    var K = 'fnstyle-tb';   // data 属性前缀

    /* --- L3 的三个直接子元素 --- */
    var a = i.children[0];   // J5e：桌面 + 所有应用
    var r = i.children[1];   // Z5e：任务窗口列表容器
    var s = i.children[2];   // K8e：底部图标组（文件任务/通知/我的账户/设置）
    var l = r && r.querySelector('.scrollbar-hidden');  // 任务窗口列表本身
    var inner = s && s.querySelector('.flex.flex-col.items-center.justify-between'); // Y5e 内层

    // 打标记（供样式表精确命中，避开「同 class 但不是任务栏」的元素）
    root.setAttribute('data-' + K, 'root');
    n.setAttribute('data-' + K, 'l1');
    o.setAttribute('data-' + K, 'l2');
    i.setAttribute('data-' + K, 'l3');
    if (a) a.setAttribute('data-' + K, 'top');
    if (r) r.setAttribute('data-' + K, 'list');
    if (l) l.setAttribute('data-' + K, 'list-inner');
    if (s) s.setAttribute('data-' + K, 'bottom');
    if (inner) inner.setAttribute('data-' + K, 'bottom-inner');

    // 桌面容器：清掉给竖排任务栏留的 66px 左padding
    var desk = document.querySelector('.desktop');
    if (desk) desk.setAttribute('data-' + K, 'desk');
    var deskInner = desk && desk.firstElementChild;
    if (deskInner) deskInner.setAttribute('data-' + K, 'desk-inner');

    /* --- 样式表：所有布局属性写在这里，React 改不动 --- */
    var css = [
      /* L0 任务栏容器：贴底 + 铺满 + 60px 高 */
      '[data-' + K + '="root"]{',
      'inset:auto 0 0 0 !important;',
      'top:auto !important;right:0 !important;bottom:0 !important;left:0 !important;',
      'width:100% !important;height:60px !important;',
      '}',

      /* L1：内边距收紧 */
      '[data-' + K + '="l1"]{padding:8px !important;}',

      /* L2：玻璃拟态容器
       *
       * 🔴 关键：不能给 L2 自身留 backdrop-filter！
       *
       * fnOS 的 Modal（关于系统弹窗）通过 contextHolder 渲染在**任务栏容器内部**，
       * 而 .semi-modal-wrap 是 position:fixed; inset:0。
       * CSS 规范：backdrop-filter 会为元素创建 containing block，
       * 于是 fixed 的 Modal 改为相对 L2 定位 ⇒ 弹窗被挤到右下角、只露出 44px 高、
       * 内容被裁切（用户实测截图）。
       *
       * 解法：把毛玻璃挪到 L2 的 ::before 伪元素上（伪元素不参与 fixed 的
       * containing block 计算），L2 自身 backdrop-filter:none。
       * 视觉效果不变，弹窗恢复视口居中。
       */
      '[data-' + K + '="l2"]{',
      'height:100% !important;border-radius:14px !important;',
      'position:relative !important;',
      'backdrop-filter:none !important;',
      '-webkit-backdrop-filter:none !important;',
      '}',
      '[data-' + K + '="l2"]::before{',
      'content:"" !important;',
      'position:absolute !important;inset:0 !important;',
      'border-radius:inherit !important;',
      'background:var(--semi-color-fndesktop-navbarBg) !important;',
      '-webkit-backdrop-filter:blur(20px) !important;',
      'backdrop-filter:blur(20px) !important;',
      'pointer-events:none !important;',
      'z-index:0 !important;',
      '}',
      '[data-' + K + '="l2"] > *{position:relative !important;z-index:1 !important;}',

      /* L3：外层由竖列改横排 */
      '[data-' + K + '="l3"]{',
      'display:flex !important;flex-direction:row !important;',
      'align-items:center !important;justify-content:space-between !important;',
      'height:100% !important;padding:0 12px !important;gap:12px !important;',
      '}',

      /* 顶部图标组 J5e：竖排→横排，分隔线由「下边框」改为「右边框」 */
      '[data-' + K + '="top"]{',
      'display:flex !important;flex-direction:row !important;align-items:center !important;',
      'border-bottom:0 !important;border-right:1px solid rgba(255,255,255,0.20) !important;',
      'padding-bottom:0 !important;padding-right:12px !important;',
      'gap:0 !important;flex-shrink:0 !important;',
      '}',

      /* 任务窗口列表容器 */
      '[data-' + K + '="list"]{flex:1 1 0% !important;height:100% !important;min-width:0 !important;}',

      /* 任务窗口列表：absolute→static，纵向滚动→横向滚动 */
      '[data-' + K + '="list-inner"]{',
      'position:static !important;display:flex !important;flex-direction:row !important;',
      'align-items:center !important;justify-content:flex-start !important;',
      'gap:0 !important;height:100% !important;',
      'overflow-x:auto !important;overflow-y:hidden !important;padding-top:0 !important;',
      '}',

      /* 底部图标组 K8e */
      '[data-' + K + '="bottom"]{',
      'display:flex !important;flex-direction:row !important;align-items:center !important;',
      'justify-content:flex-end !important;gap:0 !important;',
      'padding-top:0 !important;flex-shrink:0 !important;',
      '}',

      /* 底部图标组内层 Y5e：文件任务/通知/我的账户/设置 四项横排 */
      '[data-' + K + '="bottom-inner"]{',
      'display:flex !important;flex-direction:row !important;align-items:center !important;',
      'justify-content:flex-end !important;gap:4px !important;padding-top:0 !important;',
      '}',

      /* 底部图标组每一项：宽度自适应，别再撑成 w-full */
      '[data-' + K + '="bottom"] .flex.h-5.w-full{width:auto !important;min-width:20px !important;flex-shrink:0 !important;}',

      /* 图标本体：47px 宽方形块压成 40px，更像任务栏 */
      '[data-' + K + '="root"] .h-10.w-\\[47px\\]{width:40px !important;height:40px !important;flex-shrink:0 !important;}',

      /* 桌面容器：清掉竖排任务栏留的 66px 左padding */
      '[data-' + K + '="desk"]{padding-left:0 !important;}',
      '[data-' + K + '="desk-inner"]{padding-left:0 !important;}',

      /* 桌面区给底部任务栏留出空间，避免最下面一行图标被压住 */
      '[data-' + K + '="desk-inner"] .box-border.flex.size-full.flex-col.flex-wrap{padding-bottom:60px !important;}',

      /* 任务栏浮在桌面之上（fnOS 原本给的是 z-[1]，横置后要盖住底部图标） */
      '[data-' + K + '="root"]{z-index:10 !important;}',

      /* 桌面图标区：底部留白 + 避免最后一排被任务栏遮住 */
      '[data-' + K + '="desk-inner"] .box-border.flex.size-full.flex-col.flex-wrap{',
      'padding-bottom:60px !important;',
      '}',
    ].join('\n');

    ensureStyle('fnstyle_taskbar_css', css);

    /* --- inline 补充：React 不会碰、样式表也没覆盖到的 --- */
    // 任务栏浮在最上层，桌面区留白靠上面的 padding-bottom
    if (deskInner) {
      deskInner.style.setProperty('padding-bottom', '60px', 'important');
    }

    /* --- 弹出层避让：任务栏沉底后，弹出的下拉/气泡要往上挪 60px ---
     * fnOS 的弹出层是 portal 到 body 的，半透明设计类名为
     *   semi-popover-wrapper / semi-tooltip-wrapper
     * 但当前版本主包里已改用自定义类名（grep 结果为 0 命中），
     * 因此这里用「贴底检测」通用处理：凡是底边贴近视口底部的浮层上移。
     */
    avoidBottomOverlap();

    log('任务栏已沉底横置');
    return true;
  }

  /* ------------------------------------------------------------------
   * 任务栏沉底后的弹出层处理
   *
   * 目标（用户原话）：
   *   「当下沉后右下角的图标点击的弹出以左上角的方式弹出。」
   *
   * 涉及两个完全不同的浮层，要分开处理：
   *
   * ① Modal（关于系统弹窗，`.semi-modal-wrap`）
   *    通过 contextHolder 渲染在**任务栏内部**，受 L2 的 backdrop-filter
   *    containing block 影响而跑偏 ⇒ 已在 applyTaskbar 的样式表里
   *    把毛玻璃挪到 L2::before 解决，这里只做兜底搬移。
   *
   * ② Popover（齿轮/我的账户等菜单，`.semi-popover-wrapper`）
   *    Semi 原生 position:'rightBottom' ⇒ 浮层出现在按钮**右下方**，
   *    但任务栏已沉底，右下方就是屏幕外/任务栏位置 ⇒ 必须改成**左上方**。
   *
   * Semi 的定位结果写在 .semi-portal-inner 的 style.left/top/transform 上，
   * 由 JS 每次 show/滚动/缩放时重算 ⇒ 只能在它写完之后接管：
   *   读触发器与浮层的实际 rect，算出「左上方」目标位置，
   *   把差值换算成 left/top 偏移写回去（transform 保留不动）。
   *
   * 注意：不能动 .semi-popover-trigger（触发器本身），给它加 margin
   * 会把按钮顶起来、破坏任务栏布局。
   * ---------------------------------------------------------------- */

  // 浮层是否已经被接管过（避免与 Semi 自己的重算打架）
  var popGuard = new WeakMap();
  // v11 互殴熔断：同一浮层短期内被反复重定位 ⇒ 说明有第三方在拉回 ⇒ 永久放弃该浮层
  var popFail = new WeakMap();
  // v11 自写免疫：本脚本自己写的 style 属性会触发 MutationObserver，
  // 若不区分会造成「自己写 → 自己响应 → 再写」的死循环（实测：文件管理器
  // 右键重命名时整个桌面假死）。写前标记、回调里识别并跳过。
  var selfTouched = new WeakSet();
  function selfWriteMark(el) {
    selfTouched.add(el);
    setTimeout(function () { selfTouched.delete(el); }, 200);
  }

  // v14 触发器记忆：浮层首次成功识别触发器后记住它 —— 宽面板（文件任务/
  // 通知中心，356px 宽 × 100vh 高）被 Semi 放到远离触发器的位置后，
  // A/B 几何窗永远匹配不上，只有首次识别时的记忆能持续纠正它的位置
  var popTriggerMemo = new WeakMap();
  // v14 点击记忆：最近一次任务栏图标点击（宽面板挂载在视口中央、
  // 与任何图标都没有几何近邻关系，只能靠「刚点过哪个图标」识别）
  var lastTbClick = { el: null, t: 0 };

  /* v15：浮层「先弹出、再移动」的时序修复。
   *
   * v13 只认 .semi-popover-wrapper-show，但 Semi 是**分帧**挂载的：
   *   帧 1 插入 portal + wrapper（还没有 -show）
   *   帧 2 加 -show 开始入场动画 / 写入定位
   * ⇒ 帧 1 的 observer 回调里 popTargets() 返回空，完全不接管；
   *   等帧 2 才纠正，错误落点已经被绘制过一帧 —— 用户看到「弹两次」。
   *
   * 现改为两路合并：正在显示的浮层 + 本轮 mutation 刚挂载的 wrapper。
   * 后者能在**挂载的那个微任务**（绘制之前）就完成定位写入。
   */
  function popTargets(records) {
    var seen = [];
    var list = [];
    function add(wrap) {
      if (!wrap || seen.indexOf(wrap) >= 0) return;
      seen.push(wrap);
      var inner = wrap.closest('.semi-portal-inner');
      list.push({ wrap: wrap, inner: inner || wrap });
    }
    // ① 已显示 / 被 Semi 复用的浮层
    document.querySelectorAll('.semi-popover-wrapper.semi-popover-wrapper-show')
      .forEach(add);
    // ①' 上一轮因尺寸未就绪被抑制的浮层：即使本轮没有新增节点也要重试
    document.querySelectorAll('.semi-popover-wrapper').forEach(function (w) {
      if (popHold.has(w)) add(w);
    });
    // ② 本轮刚插入 DOM 的 wrapper（可能还没加 -show）
    (records || []).forEach(function (rec) {
      if (rec.type !== 'childList' || !rec.addedNodes) return;
      for (var i = 0; i < rec.addedNodes.length; i++) {
        var node = rec.addedNodes[i];
        if (!node || node.nodeType !== 1) continue;
        if (node.matches && node.matches('.semi-popover-wrapper')) add(node);
        if (node.querySelectorAll) {
          node.querySelectorAll('.semi-popover-wrapper').forEach(add);
        }
      }
    });
    return list;
  }

  /* v15「挂载未就绪」抑制：wrapper 刚插入、内容还没渲染出来时
   * offsetWidth/Height 为 0，此时算不出左上角落点。若放任它显示，
   * 原生右下角落点就会被绘制一帧 ⇒ 先隐藏一轮，等下一次 DOM 变化
   * 重试；定位成功（或明确判定不是任务栏浮层）后立即放行。
   * 400ms 超时 + setTimeout 双兜底，绝不永久隐藏任何浮层。
   * 只隐藏 wrapper（visibility:hidden 不影响布局盒，Semi 仍能测尺寸），
   * 且只对「本轮确实无法定位」的浮层生效，右键菜单等完全不受影响。
   */
  var popHold = new WeakMap();
  var POP_HOLD_MAX = 400;
  function holdPop(wrap) {
    if (popHold.has(wrap)) return;
    popHold.set(wrap, Date.now());
    wrap.style.setProperty('visibility', 'hidden', 'important');
    setTimeout(function () { releasePop(wrap); }, POP_HOLD_MAX);
  }
  function releasePop(wrap) {
    if (!popHold.has(wrap)) return;
    popHold.delete(wrap);
    wrap.style.removeProperty('visibility');
  }
  function releaseExpiredHolds() {
    var now = Date.now();
    document.querySelectorAll('.semi-popover-wrapper').forEach(function (w) {
      var t0 = popHold.get(w);
      if (t0 && now - t0 > POP_HOLD_MAX) releasePop(w);
    });
  }

  /* 找触发器。
   *
   * Semi 的机制：触发元素上带 data-popupid="<popupId>"，
   * 而 popupId 就是浮层容器的 state.id。
   * ⚠️ 浮层 wrapper 本身的 id 未必等于 popupId（fnOS 传了 wrapperId 时另算），
   *    所以这里不能只信 wrapper.id —— 改为两路兜底：
   *      ① 触发器就在任务栏右下角 ⇒ 几何匹配（浮层在它左下方 ⇒ 它就是触发器）
   *      ② data-popupid 精确匹配
   */
  function popTriggerOf(inner, wrap) {
    var pid = wrap && wrap.id;
    if (pid) {
      var byId = document.querySelector('[data-popupid="' + pid + '"]');
      if (byId) return byId;
    }

    /* v15：已被接管过的浮层直接复用触发器记忆，不受几何范围限制。
     *   Semi 二次重定位可能把面板挪到视口任意位置，此时几何窗必然失效；
     *   记忆是唯一能持续纠正的依据，且它只对「我们已经确认过」的浮层生效。
     */
    var memo = popTriggerMemo.get(inner);
    if (memo && memo.isConnected) {
      var mr = memo.getBoundingClientRect();
      if (mr.width && mr.height) return memo;
    }

    // 几何兜底 —— ⚠️ v11 收窄：只接管「确属任务栏弹出」的浮层。
    //   必要条件：浮层底边实际进入任务栏区域（沉底后为视口底部 60px）。
    //   文件管理器右键菜单等普通浮层不与任务栏重叠 ⇒ 绝不接管 ——
    //   否则会把它误挪到任务栏图标左上方、并与 Semi 重算形成死循环
    //   （实测：右键 → 重命名时整个桌面假死，就是这条路径）。
    var vh = window.innerHeight || 0;
    var w = wrap.getBoundingClientRect();
    if (!w.width || !w.height) return null;
    if (!vh || w.bottom < vh - 60 + 4) return null;   // 与任务栏无重叠 ⇒ 不归我们管

    /* v15：点击记忆放在「与任务栏区域重叠」判定之后、几何猜测之前。
     *   v14 把它放在几何段最末尾，遇到浮层已被 Semi flip 或宽面板
     *   （几何 A/B 窗匹配不上）时，前一句 return null 会让这条兜底
     *   永远走不到 ⇒ 浮层从头到尾没被接管，原生右下角落点一直显示。
     *   保留重叠判定是为了不误伤桌面中部的右键菜单；窗口收到 400ms
     *   （点击 → 面板挂载实测仅几十毫秒），进一步压低误判概率。
     */
    if (lastTbClick.el && lastTbClick.el.isConnected &&
        Date.now() - lastTbClick.t <= 400) {
      popTriggerMemo.set(inner, lastTbClick.el);
      return lastTbClick.el;
    }

    // v11：优先用内层（bottom-inner = 真实 fnOS 的 Y5e 内层），
    // 兼容只有外层（bottom）的结构 ⇒ querySelectorAll 找不到内层就退外层
    var scope = document.querySelector(
      '[data-fnstyle-tb="bottom-inner"],[data-fnstyle-tb="bottom"]');
    var cands = scope
      ? scope.querySelectorAll('.flex.h-5.w-full')
      : [];
    if (scope && !cands.length) cands = scope.children;
    var best = null, bestD = Infinity;
    for (var i = 0; i < cands.length; i++) {
      // 双保险：候选必须仍在任务栏容器内（防止动态重组后误命中外部元素）
      if (!cands[i].closest('[data-fnstyle-tb]')) continue;
      var r = cands[i].getBoundingClientRect();
      if (!r.width || !r.height) continue;
      // v11：识别两种 Semi 定位模式（实测 v9 只认模式 A，
      // 而真实 fnOS 任务栏图标用的是模式 B —— rightBottom，
      // 浮层在触发器右下方、大部分探出屏幕右侧，模式 A 永远匹配不上）
      //   模式 A leftBottom：浮层在触发器「左下方」⇒ 触发器在浮层右下角附近
      //   模式 B rightBottom：浮层在触发器「右下方」⇒ 浮层左边缘贴触发器右边缘
      var dxa = r.left - w.right, dya = r.top - w.bottom;          // 模式 A
      var dxb = w.left - r.right, dyb = w.bottom - r.top;          // 模式 B
      var da = (dxa >= -40 && dya >= -40) ? Math.abs(dxa) + Math.abs(dya) : Infinity;
      var db = (dxb >= -40 && dxb < 80 && dyb >= -40 && dyb < 80) ? Math.abs(dxb) + Math.abs(dyb) : Infinity;
      var d = Math.min(da, db);
      if (d < bestD) { bestD = d; best = cands[i]; }
    }
    return best || null;
  }

  /**
   * 把浮层挪到触发器的左上方（v15）。
   * 目标：浮层右边缘贴触发器左边缘（留 12px 间距），
   *       浮层下边缘贴触发器上边缘（留 12px 间距）。
   *
   * ══════════════════════════════════════════════════════════════
   * 为什么改用 offsetWidth/Height（本次「弹两次」修复的核心）
   * ══════════════════════════════════════════════════════════════
   * Semi 的入场动画给 .semi-popover-wrapper 加了 transform:scale(.9)，
   * getBoundingClientRect() 返回的是**缩放后**的渲染尺寸（220 → 198）。
   * v13 用这个尺寸算 t.left - w.width - 12 ⇒ 落点偏 22px；
   * 更糟的是动画每帧缩放比都在变 ⇒ observer 每帧重算、每帧重写
   * left/top ⇒ 浮层「边弹边滑」，视觉上就是两次弹出且停不准。
   * 帧级验证实测：inner.style.left 依次被写成 1264 → 1018 → 1014px。
   *
   * offsetWidth/offsetHeight 是布局盒尺寸，完全不受 transform 影响：
   * 目标位置在整个入场动画期间恒定，只写一次，动画只负责淡入。
   * ══════════════════════════════════════════════════════════════
   *
   * 返回值：true = 本轮已完成处理（可以显示）；false = 尺寸未就绪，
   *   由调用方先隐藏一轮、等下一次 DOM 变化再试。
   */
  function shiftPopToLeftTop(inner, wrap) {
    // v11 熔断：该浮层已被判定为「有第三方持续拉回」⇒ 放弃接管但不隐藏
    if (popFail.get(inner) === 99) return true;

    // 尺寸未就绪要先判：此时连几何兜底都算不了，隐藏一轮等 DOM 填充
    var wW = wrap.offsetWidth;
    var wH = wrap.offsetHeight;
    if (!wW || !wH) return false;

    var trigger = popTriggerOf(inner, wrap);
    if (!trigger) return true;   // 不是任务栏浮层 ⇒ 不碰、不隐藏
    // v14：记住触发器，后续 Semi 重定位即使把面板挪远也能持续纠正
    popTriggerMemo.set(inner, trigger);

    var t = trigger.getBoundingClientRect();
    var GAP = 12;
    var targetLeft = t.left - wW - GAP;
    var targetTop = t.top - wH - GAP;

    // 视口夹紧（同样用布局尺寸）
    var vw = window.innerWidth || 0;
    var vh = window.innerHeight || 0;
    if (targetLeft < 8) targetLeft = 8;
    if (targetTop < 8) targetTop = 8;
    if (targetLeft + wW > vw - 8) targetLeft = Math.max(8, vw - 8 - wW);
    if (targetTop + wH > vh - 8) targetTop = Math.max(8, vh - 8 - wH);

    /* dead-zone：直接比较「已写入的 left/top」与本次目标值。
     * 不再用渲染 rect —— 动画 transform 造成亚像素假差会引发反复重写。
     */
    var curLeft = parseFloat(inner.style.left);
    var curTop = parseFloat(inner.style.top);
    var dx = isNaN(curLeft) ? Infinity : targetLeft - curLeft;
    var dy = isNaN(curTop) ? Infinity : targetTop - curTop;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return true;

    // v11 熔断计数：同一浮层 1.5 秒内被接管超过 8 次 ⇒ 必有第三方在拉回，
    // 继续写 style 只会形成死循环（桌面假死）⇒ 永久放弃该浮层
    var g = popGuard.get(inner);
    var now = Date.now();
    var n = (g && now - (g.t0 || 0) <= 1500) ? (g.n || 0) + 1 : 1;
    if (n > 8) {
      popFail.set(inner, 99);
      popGuard.delete(inner);
      return true;
    }

    // v11 修正：直接写目标值（transform 清成 none，渲染位置 = left/top 精确值）。
    // v13c：同时禁用该元素上的 transition，避免定位写入被动画化成「滑行」。
    selfWriteMark(inner);
    inner.style.setProperty('left', targetLeft + 'px', 'important');
    inner.style.setProperty('top', targetTop + 'px', 'important');
    inner.style.setProperty('transform', 'none', 'important');
    inner.style.setProperty('transition', 'none', 'important');
    popGuard.set(inner, { dx: dx, dy: dy, n: n, t0: (g && now - (g.t0 || 0) <= 1500) ? g.t0 : now });
    return true;
  }

  var overlapBound = false;

  function avoidBottomOverlap() {
    if (overlapBound) return;
    overlapBound = true;

    function adjust(records) {
      // Popover：改成「左上方」弹出。
      // v15：把本轮新增节点一起交给 popTargets，保证挂载的那个微任务
      //   （绘制之前）就完成定位；只有「尺寸还没就绪」时才临时抑制显示。
      popTargets(records).forEach(function (p) {
        var ready = true;
        try { ready = shiftPopToLeftTop(p.inner, p.wrap); } catch (e) { ready = true; }
        if (ready === false) holdPop(p.wrap);
        else releasePop(p.wrap);
      });
      releaseExpiredHolds();

      // （v12 删除原 ①「Modal 搬到 body」兜底）
      //   理由：任务栏 root 是整屏容器（.desktop 也在其中），closest('[data-fnstyle-tb]')
      //   会把**所有应用**（文件管理器等）里弹出的 Modal 一律误判为「被困在任务栏」，
      //   强制搬到 body。而 Semi Modal 是 React portal 渲染的 —— DOM 被搬出 React
      //   根容器后事件委托失效 ⇒ 弹窗显示正常但按钮/开关全部点不动（v12 用户实测）。
      //   且 v9 已把 L2 的毛玻璃挪到 ::before（containing block 问题在 CSS 层根治），
      //   任务栏自己的 Modal 也能原生居中，这个 JS 兜底已无存在必要。
    }

    adjust();
    // 浮层是动态挂载的 ⇒ 监听变化持续修正。
    // v13 修复「浮层弹两次」：v11 这里走 setTimeout(adjust, 40) 防抖 ——
    //   Semi 先把浮层画在原生 rightBottom 落点（错误位置被绘制成第一帧），
    //   至少 40ms 后我们才挪到左上角（第二次绘制）⇒ 用户看到浮层「弹两次」。
    //   现改为在 observer 回调里**同步**执行 adjust：MutationObserver 回调是
    //   微任务，浏览器绘制发生在微任务清空之后 ⇒ 接管写入永远先于绘制落地，
    //   浮层从始至终只以正确位置出现一次。
    //   成本评估：无浮层时 popTargets() 返回空数组、开销可忽略；有浮层时
    //   每轮只有几次 getBoundingClientRect 读取，且自写免疫/熔断已防死循环。
    var obs = new MutationObserver(function (records) {
      // v13c：一律 adjust，不再按 selfTouched 跳过整批变异。
      //   v11 的自写免疫会把「我们写入后 200ms 窗口内、第三方对同一浮层的
      //   改写」整批吞掉 —— 实测推演：Semi 首次定位后紧跟着的二次定位正落
      //   在窗口内被吞，错误位置被绘制并一直保留到下一个无关变异才被纠正，
      //   表现为「浮层先显示在右侧、再往左移动」，即用户看到的弹两次。
      //   改为一律响应：自己写入后 dx≈0 会在 dead-zone（1px）提前返回，
      //   天然无反馈回路；第三方持续拉回的极端互殴由熔断（popFail）兜底。
      // v15：把 mutation records 传进去，识别「本轮刚挂载、还没加 -show」的浮层。
      adjust(records);
    });
    try {
      obs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
    } catch (e) {}

    // 滚轮/缩放时 Semi 会重算位置 ⇒ 也要重新接管
    window.addEventListener('resize', function () {
      clearTimeout(avoidBottomOverlap._t2);
      avoidBottomOverlap._t2 = setTimeout(adjust, 80);
    });

    // v14：记录最近一次任务栏图标点击（捕获阶段，先于 React onClick）。
    //   「文件任务」「通知中心」这类宽面板（356px × 100vh）挂载在视口
    //   中央、Semi 重定位后又远离触发器 —— 几何窗（-40~80px）永远匹配
    //   不上 ⇒ 接管从未介入（视频取证：面板稳定停在左缘出屏的错误位置，
    //   13 帧无振荡 = 没有互殴、也没有纠正）。点击记忆是唯一可靠的
    //   触发器来源：点击 → 面板挂载间隔仅几十毫秒。
    document.addEventListener('pointerdown', function (e) {
      try {
        var t = e.target;
        if (!t || !t.closest) return;
        var slot = t.closest('.flex.h-5.w-full');
        if (!slot) return;
        if (!slot.closest('[data-fnstyle-tb="bottom-inner"],[data-fnstyle-tb="bottom"]')) return;
        lastTbClick.el = slot;
        lastTbClick.t = Date.now();
      } catch (err) {}
    }, true);
  }

  /* ------------------------------------------------------------------
   * 功能 4：隐藏指定的系统图标
   *   字段：sysicoDisplay  逗号分隔的图标名
   * ---------------------------------------------------------------- */
  function applyHiddenIcons(cfg) {
    var list = cfg.sysicoDisplay;
    if (!list) return;
    var names = String(list).split(/[,，]/).map(function (s) { return s.trim(); })
      .filter(function (s) { return !!s; });
    if (!names.length) return;

    document.querySelectorAll('.line-clamp-2').forEach(function (t) {
      if (names.indexOf(t.title) >= 0) {
        var item = t.closest('[data-desktop-item-id]');
        if (item) item.style.display = 'none';
      }
    });
  }

  /* ------------------------------------------------------------------
   * 功能 5：桌面图标排序方式
   *   字段：FndeskIconSort  1=名称排序 0=默认
   * ---------------------------------------------------------------- */
  function applyIconSort(cfg) {
    if (num(cfg.FndeskIconSort, 0) !== 1) return;
    // fnOS 桌面排序走用户偏好 API，这里仅保留开关位；
    // 实际排序由原生「桌面图标排列」设置接管，避免与原生状态冲突。
    log('图标排序：按名称');
  }

  /* ------------------------------------------------------------------
   * 功能 6：点击音效
   *   字段：fndeskClickSound  1=左键 2=右键 3=两者
   *   ⚠️ 音频路径必须指向 FnStyle 自己的静态目录，
   *      不能沿用 /app/fndesk/static/（FnDesk 卸载后该路径已不存在）
   * ---------------------------------------------------------------- */
  function applyClickSound(cfg) {
    var mode = num(cfg.fndeskClickSound, 0);
    if (mode !== 1 && mode !== 2 && mode !== 3) return;
    if (window.__fnstyleSoundBound) return;
    window.__fnstyleSoundBound = true;

    function play(file) {
      try {
        var a = new Audio('/app/fnstyle/static/' + file);
        a.volume = 0.5;
        a.play().catch(function () {});
      } catch (e) {}
    }
    document.addEventListener('mousedown', function (e) {
      if (window.__fnstyleDisabled) return;
      if (e.button === 0) {
        if (mode === 1 || mode === 3) play('click.mp3');
      } else if (e.button === 2) {
        if (mode === 2 || mode === 3) play('right.mp3');
      }
    }, true);
    log('点击音效已启用（模式 ' + mode + '）');
  }

  /* ------------------------------------------------------------------
   * 调度：等桌面渲染完成后逐项应用
   *
   * ⚠️ 桌面壁纸与任务栏置底都依赖桌面 DOM 结构，
   *   而桌面是 SPA，容器会晚于脚本加载才渲染出来。
   *   ⇒ 这两项必须重试到成功为止，不能一次性尝试。
   * ---------------------------------------------------------------- */
  var retryTimer = null;
  var retryCount = 0;

  function applyAll() {
    loadConfig().then(function (cfg) {
      if (isDisabled(cfg)) { disableNow(); return; }
      markEnabled();
      try { applyNasNameDisplay(cfg); } catch (e) { log('隐藏名称失败', e); }
      try { applyHiddenIcons(cfg); } catch (e) { log('隐藏图标失败', e); }
      try { applyIconSort(cfg); } catch (e) { log('图标排序失败', e); }
      try { applyClickSound(cfg); } catch (e) { log('音效失败', e); }

      // 需要桌面 DOM 的两项：成功前持续重试
      var wpOk = false, tbOk = false;
      try { wpOk = applyWallpaper(cfg); } catch (e) { log('壁纸失败', e); }
      try { tbOk = applyTaskbar(cfg); } catch (e) { log('任务栏失败', e); }

      if (!wpOk || !tbOk) {
        startRetry(cfg);
      }
    });
  }

  function startRetry(cfg) {
    if (retryTimer) return;
    retryCount = 0;
    retryTimer = setInterval(function () {
      if (window.__fnstyleDisabled) { clearInterval(retryTimer); retryTimer = null; return; }
      retryCount++;
      var done = true;
      try { if (!applyWallpaper(cfg)) done = false; } catch (e) { done = false; }
      try { if (!applyTaskbar(cfg)) done = false; } catch (e) { done = false; }
      if (done || retryCount >= 60) {
        clearInterval(retryTimer);
        retryTimer = null;
      }
    }, 500);
  }

  /* ------------------------------------------------------------------
   * 桌面是 SPA，图标 / 容器会反复重绘 ⇒ 用 MutationObserver 补应用
   * ---------------------------------------------------------------- */
  function hookObserver() {
    if (window.__fnstyleObserver) return;
    var pending = null;
    var obs = new MutationObserver(function () {
      // 合并高频回调
      if (pending) return;
      pending = setTimeout(function () {
        pending = null;
        loadConfig().then(function (cfg) {
          if (isDisabled(cfg)) { disableNow(); return; }
          try { applyWallpaper(cfg); } catch (e) {}
          try { applyTaskbar(cfg); } catch (e) {}
          try { applyHiddenIcons(cfg); } catch (e) {}
        });
      }, 300);    });
    var start = function () {
      var t = document.querySelector('#root') || document.body;
      if (t) { obs.observe(t, { childList: true, subtree: true }); }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start);
    } else {
      start();
    }
    window.__fnstyleObserver = true;
  }

  function boot() {
    applyAll();
    hookObserver();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
