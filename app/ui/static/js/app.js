/* ==========================================================================
   FnStyle —— 前端逻辑
   后端 API（相对 index.cgi）：
     GET/POST  api/config        读写本应用配置
     GET       api/fndesk-config 读 FnDesk 当前配置
     POST      api/apply         把本应用配置应用到 fnOS 系统
     GET/POST  api/theme-libs    主题库源列表
     GET/POST  api/themes        主题数据
     GET       api/proxy?url=    跨域拉远程 JSON
     POST      api/upload        上传文件
   ========================================================================== */
(function () {
  'use strict';

  var BASE = 'api/';
  var PAGE_SIZE = 10;

  /* 配置项定义：键 → 显示名、类型、范围、提示
     group : 所属分组标题
     wide  : 独占整行（默认 false，两列并排）
     numeric: 值为「数字语义」——必须以 JSON 数字写入，不能是字符串。
              FnDesk 侧用严格比较判断，类型不符会导致永远不生效：
                Fndesk_taskbar → 0 !== Fndesk_taskbar
                nasNameDisplay → 2 === nasNameDisplay
              对应的 FnDesk 元素 id 与 json key 见每项注释。 */
  var FIELDS = [
    {
      key: 'appTitle', label: '网站标题', type: 'text', group: '网站信息',
      hint: '浏览器标签页标题，登录页与桌面一致',
      placeholder: '例如：我的飞牛,My NAS'
    },
    {
      key: 'web_desc', label: '网站描述', type: 'text', group: '网站信息',
      hint: '分享到社交平台时显示的描述',
      placeholder: '飞牛 fnOS 私有云存储系统'
    },
    {
      key: 'loginOpacity', label: '登录框透明度', type: 'range', group: '登录框',
      min: 0, max: 30, step: 1,
      hint: '0 = 全透明（最清晰），30 = 最不透明（最模糊）'
    },
    {
      // 元素 id: nasNameDisplay   json key: nasNameDisplay   数字 1=显示 2=隐藏
      // ⚠️ nullValue 必须等于 runtime 里的缺省行为（num(x,1) → 1 = 显示），
      //    不是 offValue(2 = 隐藏)。写错会导致界面显示「隐藏」但实际显示着名字，
      //    或者反过来 —— 界面与实际必须一致。
      key: 'nasNameDisplay', label: '登录框名字', type: 'switch', group: '登录框',
      onValue: '1', offValue: '2', numeric: true, nullValue: '1',
      onLabel: '显示', offLabel: '隐藏',
      hint: '是否在登录框上显示设备名字'
    },
    {
      key: 'fndeskLoginVW', label: '登录框水平位置', type: 'range', group: '登录框',
      min: -80, max: 80, step: 5, unit: '%',
      hint: '相对屏幕宽度的左右偏移，0 = 居中'
    },
    {
      key: 'fndeskLoginVH', label: '登录框垂直位置', type: 'range', group: '登录框',
      min: -80, max: 80, step: 5, unit: '%',
      hint: '相对屏幕高度的上下偏移，0 = 居中'
    },
    {
      // 元素 id: FndeskTaskbar   json key: Fndesk_taskbar   数字 1=开启 0=关闭
      // ⚠️ json key 是下划线的 Fndesk_taskbar，写成驼峰 FndeskTaskbar 一律无效
      // ⚠️ nullValue = 0，与 runtime 的 num(x,0) 缺省行为一致
      key: 'Fndesk_taskbar', label: '任务栏沉底横置', type: 'switch', group: '桌面与窗口',
      onValue: '1', offValue: '0', numeric: true, nullValue: '0',
      onLabel: '开启', offLabel: '关闭',
      hint: '任务栏贴底并横向排列'
    },
    {
      // 元素 id: FndeskGlass   json key: FndeskGlass   数字 -1=飞牛原设 0=关闭 N=模糊半径(px)
      // ⚠️ 实际生效的是**登录框**的毛玻璃（改写登录页 CSS 实现），不是桌面窗口
      key: 'FndeskGlass', label: '窗口毛玻璃效果', type: 'range', group: '桌面与窗口',
      min: -1, max: 50, step: 1, numeric: true,
      hint: '登录框毛玻璃强度：-1 = 飞牛默认，0 = 关闭，越大越模糊'
    },
    /* ---- 图片类：位置与顺序保持原样，不参与两列并排 ---- */
    {
      key: 'loginBackground', label: '飞牛登录背景', type: 'media', group: '图片与壁纸', wide: true,
      accept: 'image/jpeg,image/png,image/gif,image/webp,image/bmp,image/x-icon,video/mp4',
      hint: '支持 MP4 视频壁纸'
    },
    {
      key: 'loginLogo', label: '飞牛登录 LOGO', type: 'media', group: '图片与壁纸', wide: true,
      accept: 'image/jpeg,image/png,image/gif,image/webp,image/bmp,image/x-icon',
      hint: '建议 300×150 左右'
    },
    {
      key: 'deviceLogo', label: '飞牛设备 LOGO', type: 'media', group: '图片与壁纸', wide: true,
      accept: 'image/jpeg,image/png,image/gif,image/webp,image/bmp,image/x-icon'
    },
    {
      key: 'webFavicon', label: '飞牛网页 favicon', type: 'media', group: '图片与壁纸', wide: true,
      accept: 'image/jpeg,image/png,image/gif,image/webp,image/bmp,image/x-icon',
      hint: '建议正方形，不超过 256×256'
    },
    {
      key: 'desktopWallpaper', label: '桌面默认壁纸', type: 'media', group: '图片与壁纸', wide: true,
      accept: 'image/jpeg,image/png,image/gif,image/webp,image/bmp,image/x-icon,video/mp4',
      hint: '支持 MP4 视频壁纸'
    },
    /* ---- 卸载行为：不是样式字段，不参与「应用到系统」的渲染 ---- */
    {
      // 值同时落一份到 ${DATA_DIR}/auto_restore（见 index.cgi），
      // 卸载回调以 root 身份读该文件决定是否还原系统样式。
      key: 'autoRestoreOnUninstall', label: '卸载后自动还原系统默认样式',
      type: 'switch', group: '卸载与还原', wide: true,
      onValue: '1', offValue: '0', numeric: true, nullValue: '1',
      onLabel: '开启', offLabel: '关闭',
      hint: '卸载本软件后约 15 秒，自动把登录页背景 / LOGO / 桌面壁纸 / 网站标题等恢复为飞牛原生样式（后台延迟执行，避免影响卸载）；关闭则保留最后一次应用的样式'
    }
  ];

  /* 分组顺序 */
  var GROUPS = ['网站信息', '登录框', '桌面与窗口', '图片与壁纸', '卸载与还原'];

  /* FnDesk 侧的默认值（点「默认」时回填） */
  var DEFAULTS = {
    appTitle: '飞牛 fnOS',
    web_desc: '飞牛 fnOS 私有云存储系统',
    loginOpacity: '30',
    nasNameDisplay: '1',
    fndeskLoginVW: '0',
    fndeskLoginVH: '0',
    Fndesk_taskbar: '1',
    FndeskGlass: '-1',
    autoRestoreOnUninstall: '1',
    /* 「图片与壁纸」的默认值 = 软件出厂自带的 5 张图（pic/ 目录素材，
       安装时播种到 /deskdata/fnstyle/）。点「默认」就是回到这 5 张，
       不能用「页面加载时读到的当前配置」当基线 —— 用户应用过主题后，
       当前配置里是外链 URL，那样点「默认」等于没变。 */
    loginBackground: '/deskdata/fnstyle/loginBackground.png',
    loginLogo: '/deskdata/fnstyle/loginLogo.png',
    deviceLogo: '/deskdata/fnstyle/deviceLogo.png',
    webFavicon: '/deskdata/fnstyle/webFavicon.png',
    desktopWallpaper: '/deskdata/fnstyle/desktopWallpaper.jpg'
  };

  var state = {
    config: {},
    preview: {},
    libs: [],
    themes: [],
    shownThemes: 0,
    selectedLibs: {},
    pending: {}
  };

  /* ----------------------------- 工具 ----------------------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(msg, kind) {
    var box = $('#notice');
    if (!box) return;
    var el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    box.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('show'); });
    setTimeout(function () {
      el.classList.remove('show');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
    }, 2600);
  }

  /* 顶栏的「已连接」状态胶囊已移除（它只判定一次，后端挂了也不变红，
     还容易被误读成网络连接状态）。失败反馈统一走 toast。 */

  function api(path, opts) {
    opts = opts || {};
    var url = BASE + path;
    var init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    }
    return fetch(url, init).then(function (r) {
      return r.text().then(function (txt) {
        try { return JSON.parse(txt); }
        catch (e) { return { success: false, message: '响应解析失败: ' + txt.slice(0, 120) }; }
      });
    }).catch(function (e) {
      /* ⚠️ 这里把网络异常折叠成响应对象返回（而不是 reject），
         调用方拿到的是 { success:false, message:... } —— 会被 isConfig() 判为「非配置」。
         文案要让用户一眼看懂该做什么：顶栏状态灯移除后，这里是唯一的失败提示。
         ⚠️ 只能保留 2 个键：isConfig() 用 Object.keys(r).length <= 2 来识别包装对象。 */
      return { success: false, message: '后端服务不可用，请确认应用已启动' };
    });
  }

  /* 主题对象里的 URL 字段有时带反引号 */
  function cleanUrl(u) {
    if (!u) return '';
    return String(u).replace(/[`'"]/g, '').trim();
  }

  /* 判断后端返回的是「配置对象」还是「响应包装」 */
  function isConfig(r) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) return false;
    if ('success' in r && Object.keys(r).length <= 2) return false;
    if ('message' in r && Object.keys(r).length <= 2) return false;
    return true;
  }

  /* ------------------------- 个性化设置渲染 ------------------------- */
  function renderPersonalize(cfg) {
    var body = $('#personalizeBody');
    if (!body) return;
    state.config = cfg || {};

    var html = '';

    GROUPS.forEach(function (gname) {
      var items = FIELDS.filter(function (f) { return f.group === gname; });
      if (!items.length) return;

      html += '<section class="fgroup">';
      html += '<h3 class="fgroup-title"><span class="fgroup-bar"></span>' + esc(gname) + '</h3>';
      html += '<div class="fgrid">';

      items.forEach(function (f) {
        // ⚠️ 保留原始值：null（脏数据）/ undefined（未配置）/ ''（空串）三者语义不同，
        //    后面 switch 分支要分别处理。归一成 '' 会把 null 的信息丢掉。
        var raw = state.config[f.key];
        var val = raw;
        if (val === undefined || val === null) val = '';
        var id = 'f_' + f.key;

        html += '<div class="fitem' + (f.wide ? ' fitem-wide' : '') + '">';
        html += '<div class="fitem-head">';
        html += '<label for="' + id + '">' + esc(f.label) + '</label>';
        if (f.hint) html += '<span class="hint">' + esc(f.hint) + '</span>';
        html += '</div>';
        html += '<div class="fitem-body">';

        if (f.type === 'text') {
          html += '<input type="text" class="flexible" id="' + id + '" data-key="' + f.key +
                  '" value="' + esc(val) + '" placeholder="' + esc(f.placeholder || '') + '">';
          html += '<button class="btn sm" data-default="' + f.key + '" title="恢复默认值">默认</button>';

        } else if (f.type === 'switch') {
          // ⚠️ 界面必须与 runtime 语义完全一致，否则就是「界面骗人」。
          //
          // runtime 的判定：num(cfg.X, DEF) === 目标值
          //   Fndesk_taskbar : num(x, 0) !== 1 → 0 = 关闭
          //   nasNameDisplay : num(x, 1) !== 2 → 1 = 显示
          //
          // 三种「拿不到值」的情况要分别处理：
          //   · raw === null      → 脏数据（曾被写成 null）
          //                        ⇒ 用 nullValue（= runtime 缺省行为）显示
          //   · raw === undefined → 该 key 压根没配置
          //                        ⇒ 同样用 nullValue（= runtime 缺省行为）
          //   · raw === ''        → 空串，等同未配置
          //
          // ⚠️ nullValue 是每个字段显式声明的（见字段定义），
          //    **不能统一用 offValue** ——
          //    nasNameDisplay 的 offValue 是 2（隐藏），
          //    但 runtime 缺省是 1（显示），用 offValue 会把行为反转。
          var cur;
          if (raw === null || raw === undefined || raw === '') {
            cur = (f.nullValue !== undefined)
                    ? f.nullValue
                    : (DEFAULTS[f.key] === undefined ? '' : DEFAULTS[f.key]);
          } else {
            cur = raw;
          }
          var on = String(cur) === f.onValue;
          html += '<label class="switch" for="' + id + '">';
          html += '<input type="checkbox" id="' + id + '" data-key="' + f.key + '" data-on="' +
                  esc(f.onValue) + '" data-off="' + esc(f.offValue) + '"' + (on ? ' checked' : '') + '>';
          html += '<span class="slider"></span>';
          html += '</label>';
          html += '<span class="switch-text" id="t_' + f.key + '">' +
                  esc(on ? f.onLabel : f.offLabel) + '</span>';
          html += '<div class="spacer"></div>';
          html += '<button class="btn sm" data-default="' + f.key + '" title="恢复默认值">默认</button>';

        } else if (f.type === 'range') {
          html += '<div class="range-wrap">';
          html += '<input type="range" id="' + id + '" data-key="' + f.key +
                  '" min="' + f.min + '" max="' + f.max + '" step="' + f.step +
                  '" value="' + esc(val === '' ? DEFAULTS[f.key] : val) + '">';
          html += '<span class="range-val" id="v_' + f.key + '">' +
                  esc(val === '' ? DEFAULTS[f.key] : val) + (f.unit || '') + '</span>';
          html += '</div>';
          html += '<button class="btn sm" data-default="' + f.key + '" title="恢复默认值">默认</button>';

        } else if (f.type === 'media') {
          html += '<div class="preview" id="p_' + f.key + '"></div>';
          html += '<input type="text" class="flexible" id="' + id + '" data-key="' + f.key +
                  '" value="' + esc(val) + '" placeholder="图片 / 视频 URL，或直接上传">';
          html += '<button class="btn sm" data-upload="' + f.key + '" data-accept="' +
                  esc(f.accept || 'image/*') + '">上传</button>';
          /* 媒体字段的「默认」= 恢复为软件出厂自带的 5 张图片（pic/ 素材） */
          html += '<button class="btn sm" data-default="' + f.key +
                  '" title="恢复为软件默认图片">默认</button>';
        }

        html += '<button class="btn primary sm" data-save="' + f.key + '">保存</button>';
        html += '</div></div>';
      });

      html += '</div></section>';
    });

    body.innerHTML = html;
    bindPersonalize();
    renderPreviews();
  }

  function bindPersonalize() {
    /* 滑块实时数值 */
    $$('#personalizeBody input[type="range"]').forEach(function (el) {
      el.addEventListener('input', function () {
        var key = el.getAttribute('data-key');
        var f = FIELDS.filter(function (x) { return x.key === key; })[0];
        var v = $('#v_' + key);
        if (v) v.textContent = el.value + (f && f.unit ? f.unit : '');
      });
    });

    /* 开关：切换后实时更新文字并立即保存 */
    $$('#personalizeBody input[type="checkbox"][data-on]').forEach(function (el) {
      el.addEventListener('change', function () {
        var key = el.getAttribute('data-key');
        var f = FIELDS.filter(function (x) { return x.key === key; })[0];
        if (!f) return;
        var t = $('#t_' + key);
        if (t) t.textContent = el.checked ? f.onLabel : f.offLabel;
        saveField(key, el.checked ? f.onValue : f.offValue);
      });
    });

    /* 保存 */
    $$('#personalizeBody [data-save]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-save');
        saveField(key);
      });
    });

    /* 默认值 */
    $$('#personalizeBody [data-default]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-default');
        var input = $('#f_' + key);
        if (!input) return;
        var f = FIELDS.filter(function (x) { return x.key === key; })[0];

        if (input.type === 'checkbox') {
          input.checked = String(DEFAULTS[key]) === input.getAttribute('data-on');
          var t = $('#t_' + key);
          if (t && f) t.textContent = input.checked ? f.onLabel : f.offLabel;
          saveField(key, input.checked ? f.onValue : f.offValue);
          return;
        }

        /* 所有字段都回填静态出厂默认值；媒体字段的 DEFAULTS 就是
           /deskdata/fnstyle/ 下的 5 张 pic 出厂素材路径。 */
        var def = DEFAULTS[key] === undefined ? '' : DEFAULTS[key];
        input.value = def;
        if (input.type === 'range') {
          var v = $('#v_' + key);
          if (v) v.textContent = input.value;
        }
        /* 上传过图后 state.preview 缓存的 CGI 预览地址优先级高于配置路径，
           不清掉的话配置已恢复出厂、缩略图却还停在上一次上传的图上。 */
        if (f && f.type === 'media') delete state.preview[key];
        saveField(key);
      });
    });

    /* 上传 */
    $$('#personalizeBody [data-upload]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-upload');
        var accept = btn.getAttribute('data-accept') || 'image/*';
        var inp = document.createElement('input');
        inp.type = 'file';
        inp.accept = accept;
        inp.addEventListener('change', function () {
          if (inp.files && inp.files[0]) uploadFile(key, inp.files[0]);
        });
        inp.click();
      });
    });

    /* 回车即保存 */
    $$('#personalizeBody input[type="text"]').forEach(function (el) {
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') saveField(el.getAttribute('data-key'));
      });
    });
  }

  function saveField(key, explicitVal) {
    var f = FIELDS.filter(function (x) { return x.key === key; })[0];
    var val = explicitVal;
    if (val === undefined || val === null) {
      var el = $('#f_' + key);
      if (!el) return;
      val = el.value;
    }
    // 数字语义的字段必须写成 JSON 数字（FnDesk 侧用 === / !== 严格比较）
    if (f && f.numeric) {
      var n = parseInt(val, 10);
      // ⚠️ parseInt(null) / parseInt(undefined) → NaN，
      //    而 JSON.stringify({k: NaN}) 会变成 {"k":null} ⇒ 配置被写成 null，
      //    runtime 侧 num(null,0) !== 1 ⇒ 开关「界面显示开启但实际不生效」。
      //
      //    兜底必须用 **nullValue**（= 该字段在 runtime 下的缺省行为），
      //    不能用 offValue：
      //      Fndesk_taskbar : nullValue=0（关闭）= offValue ✓
      //      nasNameDisplay : nullValue=1（显示）≠ offValue(2=隐藏) ✗
      //    用错会把用户行为反转。
      if (isNaN(n)) {
        var fallback = (f.nullValue !== undefined) ? f.nullValue
                                                   : (DEFAULTS[key] === undefined ? '' : DEFAULTS[key]);
        n = parseInt(fallback, 10);
        if (isNaN(n)) n = 0;
      }
      val = n;
    }
    var payload = {};
    payload[key] = val;
    api('config', { method: 'POST', body: payload }).then(function (r) {
      if (r && r.success) {
        state.config[key] = val;
        toast('「' + labelOf(key) + '」已保存', 'ok');
        renderPreviews();
      } else {
        toast((r && r.message) || '保存失败', 'err');
      }
    });
  }

  function labelOf(key) {
    var f = FIELDS.filter(function (x) { return x.key === key; })[0];
    return f ? f.label : key;
  }

  function uploadFile(key, file) {
    // 用原始二进制上传（multipart 在 CGI 环境下容易损坏二进制内容）
    var url = BASE + 'upload?field=' + encodeURIComponent(key) +
              '&name=' + encodeURIComponent(file.name);
    toast('正在上传 ' + file.name + ' …');
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
      body: file
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (r && r.success) {
          var d = r.data || {};
          var el = $('#f_' + key);
          // 配置里存 deskdata 路径（与 FnDesk 兼容，同步时零拷贝）
          if (el) el.value = d.url || '';
          state.config[key] = d.url || '';
          // 预览用 CGI 路径（保证本应用独立可用）
          state.preview[key] = d.preview || d.url || '';
          toast('上传成功，已写入「' + labelOf(key) + '」', 'ok');
          renderPreviews();
        } else {
          toast((r && r.message) || '上传失败', 'err');
        }
      })
      .catch(function (e) { toast('上传失败: ' + e.message, 'err'); });
  }

  // 把配置里的路径转成可预览的 URL
  function previewUrl(key, url) {
    if (!url) return '';
    /* 本应用上传后拿到的预览地址最优先 */
    if (state.preview[key]) return state.preview[key];
    /* 形如 /deskdata/fnstyle/loginBackground.png —— 这是**fnOS 站点相对路径**，
       由 fnOS 网关(:8443)提供。而本页面在独立端口(9910)/反代下打开时，
       这个路径请求不到 → 缩略图 404 后被 onerror 隐藏成空白框。
       这些资源在应用 DATA_DIR/fnstyle/ 下有同名副本，
       改走 /api/file?name= 通道即可正常显示，且两条访问链路都通用。 */
    var m = /^\/?deskdata\/fnstyle\/([^/?#]+)$/.exec(url);
    if (m) return 'api/file?name=' + encodeURIComponent(m[1]);
    return url;
  }

  function renderPreviews() {
    FIELDS.filter(function (f) { return f.type === 'media'; }).forEach(function (f) {
      var box = $('#p_' + f.key);
      if (!box) return;
      var url = state.config[f.key] || '';
      if (!url) {
        box.innerHTML = '<span class="none">无</span>';
        return;
      }
      var src = previewUrl(f.key, url);
      if (/\.mp4($|\?)/i.test(url) || /video\.mp4/i.test(url)) {
        box.innerHTML = '<video src="' + esc(src) + '" muted loop autoplay playsinline></video>';
      } else {
        box.innerHTML = '<img src="' + esc(src) + '" alt="预览" onerror="this.style.display=\'none\'">';
      }
    });
  }

  /* ---------------------------- 主题库 ---------------------------- */

  /* 默认库源：优先勾选「Fndesk 主题库」（名称匹配，兼容改名后的显示名）
     ★ EXPLICIT_KEY 区分「用户手动勾选过」与「系统默认初始化」：
       用户显式点成「全不选」要被尊重，不能被下次打开的默认加载重新勾上；
       而全新安装 / 清过浏览器数据的状态，必须默认有库源可加载。 */
  var DEFAULT_LIB_KEY = 'fnstyle_theme_selection_v1';
  var DEFAULT_LIB_EXPLICIT_KEY = 'fnstyle_theme_selection_explicit_v1';

  function isDefaultLib(lib) {
    if (!lib) return false;
    var n = String(lib.name || '').trim();
    /* 「Fndesk 主题库」/「FnStyle 主题库」/「Fndesk - 主题库」都算 */
    if (/^fn\s?desk\s*主题库$/i.test(n)) return true;
    if (/^fn\s?style\s*主题库$/i.test(n)) return true;
    return false;
  }

  /* 读取本地保存的勾选记录 → { set: {url:true}, explicit: bool } */
  function loadLibSelection() {
    try {
      var raw = localStorage.getItem(DEFAULT_LIB_KEY);
      if (!raw) return null;
      var arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return null;
      var set = {};
      arr.forEach(function (u) { set[String(u || '').trim()] = true; });
      return {
        set: set,
        explicit: localStorage.getItem(DEFAULT_LIB_EXPLICIT_KEY) === '1'
      };
    } catch (e) {
      return null;
    }
  }

  /* 保存勾选记录；explicit=true 表示这是用户手动操作的结果 */
  function saveLibSelection(explicit) {
    try {
      var urls = Object.keys(state.selectedLibs).filter(function (u) { return state.selectedLibs[u]; });
      localStorage.setItem(DEFAULT_LIB_KEY, JSON.stringify(urls));
      if (explicit) localStorage.setItem(DEFAULT_LIB_EXPLICIT_KEY, '1');
    } catch (e) { /* 隐私模式等场景忽略 */ }
  }

  function loadLibs() {
    return api('theme-libs').then(function (r) {
      var list = Array.isArray(r) ? r : (r && r.data) || [];
      state.libs = list;

      /* 恢复勾选：用户手动选过 → 原样尊重（含「全不选」）；
         否则（全新安装 / 无记录 / 记录为空）→ 默认勾选 Fndesk 主题库，
         找不到默认名时退回第一个库源，保证「主题库默认进行加载」有源可拉。 */
      var saved = loadLibSelection();
      var restored = saved && saved.explicit;
      if (saved && restored) {
        state.selectedLibs = saved.set;
        /* 记录里可能有已被删除的库源 —— 清理掉 */
        Object.keys(state.selectedLibs).forEach(function (u) {
          var still = list.some(function (l) { return l.url === u; });
          if (!still) delete state.selectedLibs[u];
        });
      } else {
        list.forEach(function (l) {
          if (isDefaultLib(l)) state.selectedLibs[l.url] = true;
        });
        if (!Object.keys(state.selectedLibs).length && list.length) {
          state.selectedLibs[list[0].url] = true;
        }
        saveLibSelection(false);
      }

      renderLibs();
      return list;
    });
  }

  /* 库源下拉多选 */
  function renderLibs() {
    var optBox = $('#libSelectOptions');
    var label = $('#libSelectLabel');
    if (!optBox) return;

    if (!state.libs.length) {
      optBox.innerHTML = '<div class="empty" style="padding:1.2rem .6rem">' +
        '<span class="ic">📭</span>暂无主题库来源</div>';
      if (label) label.textContent = '暂无库源';
      return;
    }

    var html = '';
    state.libs.forEach(function (lib, i) {
      var checked = state.selectedLibs[lib.url] ? ' checked' : '';
      html += '<label class="ms-option">';
      html += '<input type="checkbox" class="lib-cb" data-i="' + i + '"' + checked + '>';
      html += '<span class="mo-text">';
      html += '<span class="nm">' + esc(lib.name || '未命名') + '</span>';
      if (lib.from) html += '<span class="fr">' + esc(lib.from) + '</span>';
      html += '</span>';
      html += '<button type="button" class="btn sm" data-edit-lib2="' + i + '" title="编辑">✎</button>';
      html += '</label>';
    });
    optBox.innerHTML = html;

    /* 同步按钮上的摘要文字 */
    updateLibSummary();

    $$('.lib-cb', optBox).forEach(function (cb) {
      cb.addEventListener('change', function () {
        var lib = state.libs[parseInt(cb.getAttribute('data-i'), 10)];
        if (!lib) return;
        if (cb.checked) state.selectedLibs[lib.url] = true;
        else delete state.selectedLibs[lib.url];
        updateLibSummary();
        saveLibSelection(true);
      });
    });
    $$('[data-edit-lib2]', optBox).forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        /* 从下拉进入 → 保存/取消后直接关闭弹窗（「从哪来回哪去」） */
        openLibEditor('inline');
        editLibItem(parseInt(btn.getAttribute('data-edit-lib2'), 10));
      });
    });
  }

  /* 更新下拉按钮上的选中摘要 */
  function updateLibSummary() {
    var label = $('#libSelectLabel');
    if (!label) return;
    var names = state.libs.filter(function (l) { return state.selectedLibs[l.url]; })
                        .map(function (l) { return l.name || '未命名'; });
    if (!state.libs.length) { label.textContent = '暂无库源'; return; }
    if (!names.length) { label.textContent = '请选择库源'; return; }
    if (names.length === 1) { label.textContent = names[0]; return; }
    if (names.length === state.libs.length) { label.textContent = '全部 ' + names.length + ' 个库源'; return; }
    label.textContent = '已选 ' + names.length + ' / ' + state.libs.length;
  }

  function saveLibs() {
    api('theme-libs', { method: 'POST', body: state.libs }).then(function (r) {
      if (r && r.success) {
        toast('库源列表已保存', 'ok');
        renderLibs();
        renderLibEditor();
      }
      else toast((r && r.message) || '保存失败', 'err');
    });
  }

  /* ------------------------------------------------------------------
   * 主题库来源编辑器（弹窗）
   * 能力对齐 FnDesk 的主题库管理器：
   *   - 列表视图：名称 / 提供者 / 描述 / URL + 编辑、删除
   *   - 添加模式：只填 URL，保存时自动拉取元信息（名称/描述/提供者）
   *   - 编辑模式：4 个字段全填，保存时直接采用表单内容（不重新拉取）
   *   - URL 查重
   * ------------------------------------------------------------------ */
  var libEditIndex = -1;   /* >=0 表示编辑模式；-1 表示新增模式 */
  /* 弹窗入口来源：'manage' = 从「⚙ 管理库源」进入（保存/取消后回到列表视图）
                       'inline' = 从下拉框里的 ✎ 编辑进入（保存/取消后直接关闭弹窗）
     —— 秉承「从哪来回哪去」 */
  var libEditorFrom = 'manage';

  function openLibEditor(from) {
    var ov = $('#libEditorOverlay');
    if (!ov) return;
    libEditorFrom = (from === 'inline') ? 'inline' : 'manage';
    ov.hidden = false;
    if (libEditorFrom === 'manage') {
      showLibListView();
    } else {
      /* 从下拉进入：直接显示表单（调用方随后会填入被编辑项） */
      showLibFormView();
    }
    renderLibEditor();
  }

  function closeLibEditor() {
    var ov = $('#libEditorOverlay');
    if (!ov) return;
    ov.hidden = true;
    libEditIndex = -1;
  }

  function showLibListView() {
    var form = $('#libEditorForm'), list = $('#libEditorListContainer');
    if (form) form.hidden = true;
    if (list) list.hidden = false;
  }

  function showLibFormView() {
    var form = $('#libEditorForm'), list = $('#libEditorListContainer');
    if (form) form.hidden = false;
    if (list) list.hidden = true;
  }

  /* 状态提示：info / loading / success / warn / error */
  function setLibMetaStatus(kind, text) {
    var el = $('#teFetchMetaStatus');
    if (!el) return;
    el.className = 'lib-editor-hint lib-editor-hint--' + kind;
    el.textContent = text || '';
  }

  /* 渲染编辑器里的库源列表 */
  function renderLibEditor() {
    var box = $('#libEditorList');
    var cnt = $('#libEditorCount');
    if (cnt) cnt.textContent = state.libs.length;
    if (!box) return;

    if (!state.libs.length) {
      box.innerHTML = '<div class="lib-editor-empty">暂无主题库来源，点击「+ 添加库源」创建</div>';
      return;
    }

    var html = '';
    state.libs.forEach(function (lib, i) {
      var name = lib.name || '未命名';
      var desc = lib.desc ? esc(lib.desc) : '';
      var from = lib.from ? esc(lib.from) : '';
      var url = lib.url ? esc(lib.url) : '';
      html += '<div class="lib-editor-item">';
      html += '<div class="lib-editor-item-info">';
      html += '<div class="lib-editor-item-name">';
      html += '<span class="lib-editor-item-name-text">' + esc(name) + '</span>';
      if (from) html += '<span class="lib-editor-item-from">· ' + from + '</span>';
      html += '</div>';
      if (desc) html += '<div class="lib-editor-item-desc">' + desc + '</div>';
      html += '<div class="lib-editor-item-url" title="' + url + '">' + url + '</div>';
      html += '</div>';
      html += '<div class="lib-editor-item-actions">';
      html += '<button type="button" class="btn sm" data-edit-lib="' + i + '" title="编辑">✎ 编辑</button>';
      html += '<button type="button" class="btn sm danger" data-del-lib2="' + i + '" title="删除">🗑 删除</button>';
      html += '</div>';
      html += '</div>';
    });
    box.innerHTML = html;

    $$('[data-edit-lib]', box).forEach(function (btn) {
      btn.addEventListener('click', function () {
        editLibItem(parseInt(btn.getAttribute('data-edit-lib'), 10));
      });
    });
    $$('[data-del-lib2]', box).forEach(function (btn) {
      btn.addEventListener('click', function () {
        deleteLibItem(parseInt(btn.getAttribute('data-del-lib2'), 10));
      });
    });
  }

  /* 新增模式：只显示 URL 输入框 */
  function showAddLibForm() {
    libEditIndex = -1;
    var form = $('#libEditorForm');
    if (form && form.reset) form.reset();
    var title = $('#libEditorFormTitle');
    if (title) title.textContent = '添加主题库来源';
    $('#teGroupUrl').hidden = false;
    $('#teGroupName').hidden = true;
    $('#teGroupDesc').hidden = true;
    $('#teGroupFrom').hidden = true;
    showLibFormView();
    setLibMetaStatus('info', '保存时会自动读取该 URL 的元信息（名称 / 描述 / 提供者）');
    var u = $('#teUrl');
    if (u) u.focus();
  }

  /* 编辑模式：4 个字段全填 */
  function editLibItem(i) {
    var lib = state.libs[i];
    if (!lib) return;
    libEditIndex = i;
    var form = $('#libEditorForm');
    if (form && form.reset) form.reset();
    $('#teUrl').value = lib.url || '';
    $('#teName').value = lib.name || '';
    $('#teDesc').value = lib.desc || '';
    $('#teFrom').value = lib.from || '';
    var title = $('#libEditorFormTitle');
    if (title) title.textContent = '编辑主题库来源';
    $('#teGroupUrl').hidden = false;
    $('#teGroupName').hidden = false;
    $('#teGroupDesc').hidden = false;
    $('#teGroupFrom').hidden = false;
    showLibFormView();
    setLibMetaStatus('info', '编辑模式下：保存时直接使用表单内容，不重新读取元信息');
  }

  /* 表单收尾：按入口来源决定「回到列表」还是「直接关闭弹窗」
     —— 从下拉框 ✎ 编辑进入 → 直接关闭；从「⚙ 管理库源」进入 → 回到列表视图 */
  function finishLibForm() {
    libEditIndex = -1;
    if (libEditorFrom === 'inline') {
      closeLibEditor();
    } else {
      showLibListView();
      renderLibEditor();
    }
  }

  function cancelLibForm() {
    finishLibForm();
  }

  function deleteLibItem(i) {
    var lib = state.libs[i];
    if (!lib) return;
    var name = lib.name || '未命名';
    if (!confirm('确定要删除主题库「' + name + '」吗？此操作不可撤销。')) return;
    state.libs.splice(i, 1);
    delete state.selectedLibs[lib.url];
    saveLibs();
  }

  /* 保存表单 */
  function saveLibForm() {
    var urlEl = $('#teUrl');
    var url = urlEl ? urlEl.value.trim() : '';
    if (!url) { toast('请填写主题库的源地址 URL', 'warn'); if (urlEl) urlEl.focus(); return; }
    try { new URL(url); }
    catch (e) { toast('URL 格式不正确，请输入完整 URL（以 http:// 或 https:// 开头）', 'warn'); return; }

    /* 编辑模式：直接采用表单内容 */
    if (libEditIndex >= 0) {
      state.libs[libEditIndex] = {
        name: ($('#teName').value || '').trim(),
        desc: ($('#teDesc').value || '').trim(),
        from: ($('#teFrom').value || '').trim(),
        url: url
      };
      finishLibForm();
      saveLibs();
      toast('主题库已更新', 'ok');
      return;
    }

    /* 新增模式：查重 */
    var key = url.toLowerCase();
    var dup = -1;
    state.libs.forEach(function (l, i) {
      if (dup < 0 && (l.url || '').trim().toLowerCase() === key) dup = i;
    });
    if (dup >= 0) {
      setLibMetaStatus('warn', '该 URL 已存在于主题库列表中（第 ' + (dup + 1) + ' 项），未重复添加');
      toast('该主题库 URL 已存在，无需重复添加', 'warn');
      return;
    }

    /* 新增模式：拉取元信息 */
    setLibMetaStatus('loading', '正在拉取 ' + url + ' ...');
    var bust = url + (url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + Date.now();
    api('proxy-json?url=' + encodeURIComponent(bust)).then(function (r) {
      var meta = (r && typeof r === 'object' && !Array.isArray(r)) ? r._meta : null;
      if (!meta || typeof meta !== 'object') {
        setLibMetaStatus('warn', '未能读取到元信息，已按「未命名库源」添加，可在列表中点「编辑」补充名称与描述');
        state.libs.push({ name: '未命名库源', desc: '', from: '', url: url });
        finishLibForm();
        saveLibs();
        toast('已添加主题库（未读取到元信息）', 'warn');
        return;
      }
      var mname = String(meta.name || '').trim() || '未命名库源';
      state.libs.push({
        name: mname,
        desc: String(meta.desc || '').trim(),
        from: String(meta.from || '').trim(),
        url: url
      });
      finishLibForm();
      saveLibs();
      toast('已添加主题库：' + mname, 'ok');
    }).catch(function () {
      setLibMetaStatus('error', '拉取元信息失败，请检查 URL 是否可访问');
    });
  }

  /* ---------------------------- 加载反馈 ---------------------------- */
  /* 「重载选库」的加载中状态：
     - 按钮变成「⟳ 加载中…」并禁用
     - 主题网格上覆盖半透明遮罩，显示正在从几个库源拉取
     - 结束后按钮与遮罩恢复
     全程用 try/finally 保证异常时也能恢复，不会卡在 loading。 */
  function setLoadBusy(busy, count) {
    var btn = $('#btnLoadSelected');
    var mask = $('#themeLoadMask');
    var title = $('#themeLoadTitle');
    var sub = $('#themeLoadSub');

    if (btn) {
      if (busy) {
        btn.classList.add('loading');
        btn.disabled = true;
        btn.innerHTML = '<span class="btn-spin"></span>加载中…';
      } else {
        btn.classList.remove('loading');
        btn.disabled = false;
        btn.textContent = '重载选库';
      }
    }

    if (mask) {
      if (busy) {
        mask.hidden = false;
        if (title) title.textContent = '正在加载主题库…';
        if (sub) sub.textContent = count ? ('正在从 ' + count + ' 个主题库拉取主题…') : '';
        /* 清掉网格里的初始占位符，避免与遮罩内容重复 */
        var grid = $('#themeGrid');
        if (grid && grid.querySelector('.loading')) grid.innerHTML = '';
      } else {
        mask.hidden = true;
        if (sub) sub.textContent = '';
      }
    }
  }

  /* 主题的稳定标识（与后端 / FnDesk 主题库一致：优先 ID，退回标题） */
  function themeId(t) {
    if (!t || typeof t !== 'object') return null;
    var id = t.ID;
    if (id === undefined || id === null) id = t.标题;
    if (id === undefined || id === null) return null;
    return String(id);
  }

  /* 启动时默认加载的状态提示：只更新主题数标签，不遮挡网格、不弹 toast。
     首页已经有「加载主题中…」占位，这里再给一个可见的进度说明。 */
  function setAutoLoadHint(busy) {
    var el = $('#themeCount');
    if (!el) return;
    el.textContent = busy
      ? (state.themes.length
          ? (state.themes.length + ' 个主题 · 正在更新…')
          : '正在加载主题库…')
      : (state.themes.length + ' 个主题');
  }

  /* 拉取选中库源的主题，合并写入
     silent=true  → 启动时默认加载：静默、失败不清空已有主题
     opts.merge   → 与本地已有主题合并去重（仅默认加载使用），
                     避免个别库源失败时把出厂快照覆盖成残缺结果
     返回 Promise，便于调用方等待 */
  function loadSelectedLibs(silent, opts) {
    opts = opts || {};
    var picked = state.libs.filter(function (l) { return state.selectedLibs[l.url]; });
    if (!picked.length) {
      if (!silent) toast('请至少勾选一个主题库', 'warn');
      return Promise.resolve(false);
    }

    if (!silent) setLoadBusy(true, picked.length);
    else setAutoLoadHint(true);
    if (!silent) toast('正在加载 ' + picked.length + ' 个主题库…');

    var jobs = picked.map(function (lib) {
      return api('proxy?url=' + encodeURIComponent(lib.url))
        .then(function (data) {
          if (!data) return [];
          if (Array.isArray(data)) return data;
          if (Array.isArray(data.themes)) return data.themes;
          return [];
        })
        .catch(function () { return []; });
    });

    return Promise.all(jobs).then(function (results) {
      var incoming = [];
      var seen = {};
      results.forEach(function (arr) {
        arr.forEach(function (t) {
          var id = themeId(t);
          if (id === null || seen[id]) return;
          seen[id] = true;
          incoming.push(t);
        });
      });
      var merged = incoming;
      if (silent && opts.merge) {
        /* 默认加载走「增量合并」：所有库源都失败时保留本地主题，
           部分库源失败时也不会把已有的出厂快照覆盖成残缺结果。 */
        if (!incoming.length) return false;
        merged = state.themes.slice();
        /* ⚠️ 必须用「本地集合」重新去重：上面 seen 已经装了全部 incoming，
           直接复用会把刚拉到的主题全判成重复，合并结果反而变空。 */
        var localSeen = {};
        merged.forEach(function (t) {
          var id = themeId(t);
          if (id !== null) localSeen[id] = true;
        });
        incoming.forEach(function (t) {
          var id = themeId(t);
          if (id === null || localSeen[id]) return;
          localSeen[id] = true;
          merged.push(t);
        });
      }
      if (!merged.length) {
        if (!silent) toast('所选主题库中没有有效主题', 'err');
        /* 网格可能已被清空（首次加载失败）→ 恢复空状态提示 */
        if (!silent) renderThemes(true);
        return false;
      }

      state.themes = merged;
      return api('themes', { method: 'POST', body: merged }).then(function (r) {
        if (r && r.success) {
          if (!silent) toast('成功合并 ' + merged.length + ' 个主题', 'ok');
          renderThemes(true);
          return true;
        }
        if (!silent) {
          toast((r && r.message) || '保存主题失败', 'err');
          renderThemes(true);
        }
        return false;
      });
    }).catch(function (e) {
      if (!silent) {
        toast('加载失败：' + ((e && e.message) || e), 'err');
        renderThemes(true);
      }
      return false;
    }).then(function (ok) {
      if (!silent) setLoadBusy(false);
      else setAutoLoadHint(false);
      return ok;
    });
  }

  function loadThemes() {
    return api('themes').then(function (r) {
      var list = Array.isArray(r) ? r : (r && r.data) || [];
      state.themes = list;
      renderThemes(true);
      return list;
    });
  }

  /* 每次打开软件都「默认进行加载」主题库，不依赖手动点「重载选库」。
   *
   * ⚠️ 旧实现用 localStorage 打「只做一次」的标记，而且是在请求发出之前
   *    就写入：首次遇到外链超时 / 断网 → 标记已落 → 之后再也不会加载，
   *    主题库永远停在「暂无任何主题数据」。现在改成：
   *      · 每次页面启动执行一次（仅内存标志，刷新页面会重新执行）；
   *      · 失败不写任何标记 ⇒ 下次打开自动重试，不需要清浏览器缓存；
   *      · 本地已播种 / 缓存的主题先渲染，远程结果回来再增量合并。
   */
  var autoLoadStarted = false;

  function autoLoadDefaultLibs() {
    if (autoLoadStarted) return Promise.resolve(false);
    /* 必须等库源加载完，否则不知道该拉哪个；用户显式全不选则不接管 */
    var picked = state.libs.filter(function (l) { return state.selectedLibs[l.url]; });
    if (!picked.length) return Promise.resolve(false);
    autoLoadStarted = true;
    return loadSelectedLibs(true, { merge: true });
  }

  function renderThemes(reset) {
    var grid = $('#themeGrid');
    if (!grid) return;
    // reset 时回到首批展示数量（分页加载更多）
    if (reset) state.shownThemes = PAGE_SIZE;

    if (!state.themes.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1">' +
        '<span class="ic">🎨</span>暂无任何主题数据<br>请在上方选择主题库来源后点击「重载选库」</div>';
      $('#themeCount').textContent = '0 个主题';
      return;
    }

    var slice = state.themes.slice(0, state.shownThemes);
    grid.innerHTML = slice.map(function (t) {
      var cover = cleanUrl(t.t1 || t.t0 || t.t4);
      var id = esc(t.ID);
      return '<div class="theme-card" data-theme-id="' + id + '">' +
        '<div class="theme-thumb">' +
          (cover ? '<img src="' + esc(cover) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' : '') +
          '<div class="ov"></div>' +
        '</div>' +
        '<div class="theme-meta">' +
          '<div class="tt">' + esc(t['标题'] || ('主题 ' + t.ID)) + '</div>' +
          '<div class="id">ID ' + id + '</div>' +
        '</div>' +
        '<div class="theme-actions">' +
          /* 只保留「预览」：应用统一在预览弹窗底部做（先看清效果再应用） */
          '<button class="btn sm" data-preview-theme="' + id + '">预览</button>' +
        '</div>' +
      '</div>';
    }).join('');

    $('#themeCount').textContent = state.themes.length + ' 个主题';

    $$('[data-preview-theme]', grid).forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        previewTheme(btn.getAttribute('data-preview-theme'));
      });
    });
    $$('.theme-card', grid).forEach(function (card) {
      card.addEventListener('click', function () {
        previewTheme(card.getAttribute('data-theme-id'));
      });
    });

    /* 「加载更多」按钮：放在主题网格下方，全部加载完则隐藏 */
    var lb = $('#btnLoadMore');
    if (lb) {
      var done = state.shownThemes >= state.themes.length;
      lb.disabled = done;
      lb.hidden = done;
      var wrap = lb.parentNode;
      if (wrap) wrap.hidden = done;
    }
  }

  /* ------------------------------------------------------------------
     主题预览
     ------------------------------------------------------------------
     主题对象里的 t0~t5 含义（与 FnDesk applyTheme.js 一致）：
       t0 = 封面图（卡片缩略图用）
       t1 = 登录页 LOGO      → loginLogo
       t2 = 登录页背景      → loginBackground（可能是 mp4 视频壁纸）
       t3 = 设备 LOGO       → deviceLogo
       t4 = 桌面壁纸        → desktopWallpaper（可能是 mp4 视频壁纸）
       t5 = 站点 favicon    → webFavicon（多数主题没有）
     预览分四个舞台：
       登录页效果 = t2 背景 + t1 LOGO + t3 设备 LOGO 的组合模拟
       桌面壁纸   = t4
       登录LOGO   = t1 的大图
       设备LOGO   = t3 的大图
     ------------------------------------------------------------------ */
  /* tab  = 点右侧清单该项时应该切到哪个舞台标签
     key  = 该项对应的大图字段（仅 asset 类标签需要）
     「登录LOGO」「设备LOGO」两个标签共用 asset 视图，靠 key 区分。 */
  var TP_FIELDS = [
    { key: 't0', label: '封面图',     use: '仅用于主题库卡片展示', tab: 'login',  tabKey: '' },
    { key: 't1', label: '登录页 LOGO', use: '飞牛登录页左上角标识', tab: 'asset', tabKey: 't1' },
    { key: 't2', label: '登录页背景', use: '飞牛登录页背景',       tab: 'login',  tabKey: '' },
    { key: 't3', label: '设备 LOGO',  use: '登录框内的设备标识',   tab: 'asset', tabKey: 't3' },
    { key: 't4', label: '桌面壁纸',   use: '飞牛桌面背景',         tab: 'desktop', tabKey: '' },
    { key: 't5', label: '站点图标',   use: '浏览器标签页 favicon', tab: 'asset', tabKey: 't1' }
  ];

  var tpTheme = null;      /* 当前预览的主题对象 */
  var tpStage = 'login';   /* 当前舞台：login / desktop / asset */
  var tpAssetKey = 't1';   /* 大图舞台当前查看的字段（t1=登录LOGO / t3=设备LOGO） */

  /* 判断 URL 是否为视频（mp4/webm/ogg） */
  function isVideoUrl(u) {
    return /\.(mp4|webm|ogg|mov|m4v)(\?|#|$)/i.test(String(u || ''));
  }

  /* 往容器里塞一张图或一段视频；加载失败时给出占位提示 */
  function fillTpMedia(box, url, altText) {
    if (!box) return;
    box.innerHTML = '';
    if (!url) {
      box.classList.add('tp-empty');
      box.innerHTML = '<span class="tp-empty-txt">该主题未提供此项资源</span>';
      return;
    }
    box.classList.remove('tp-empty');
    if (isVideoUrl(url)) {
      var v = document.createElement('video');
      v.src = url;
      v.muted = true;
      v.loop = true;
      v.autoplay = true;
      v.playsInline = true;
      v.setAttribute('playsinline', '');
      v.className = 'tp-media';
      v.addEventListener('error', function () { tpMediaFail(box, url, altText); });
      box.appendChild(v);
      /* 部分浏览器 autoplay 被策略拦截时，手动再触发一次 */
      var p = v.play();
      if (p && typeof p.catch === 'function') p.catch(function () { /* 静默 */ });
    } else {
      var im = document.createElement('img');
      im.src = url;
      im.alt = altText || '';
      im.className = 'tp-media';
      im.addEventListener('error', function () { tpMediaFail(box, url, altText); });
      box.appendChild(im);
    }
  }

  function tpMediaFail(box, url, altText) {
    if (!box) return;
    box.classList.add('tp-empty');
    box.innerHTML = '<span class="tp-empty-txt">资源加载失败<br><code>' + esc(url) + '</code></span>';
  }

  function openThemePreview(id) {
    var t = state.themes.filter(function (x) { return String(x.ID) === String(id); })[0];
    if (!t) { toast('未找到该主题', 'err'); return; }
    tpTheme = t;
    tpStage = 'login';
    tpAssetKey = 't1';

    var ov = $('#themePreviewOverlay');
    if (!ov) return;
    ov.hidden = false;

    /* 标题 / 副标题 */
    var title = $('#tpTitle');
    if (title) title.textContent = t['标题'] || ('主题 ' + t.ID);
    var sub = $('#tpSub');
    if (sub) sub.textContent = 'ID ' + t.ID + ' · 预览效果仅供参考，实际以应用后飞牛桌面呈现为准';

    renderTpStages();
    renderTpAssetList();

    /* 高亮当前卡片 */
    $$('.theme-card').forEach(function (c) {
      c.classList.toggle('selected', c.getAttribute('data-theme-id') === String(id));
    });
  }

  function closeThemePreview() {
    var ov = $('#themePreviewOverlay');
    if (!ov) return;
    ov.hidden = true;
    /* 停掉可能正在播放的视频 */
    $$('#themePreviewOverlay video').forEach(function (v) {
      try { v.pause(); } catch (e) { /* ignore */ }
    });
    tpTheme = null;
  }

  /* 渲染三个舞台的内容 */
  function renderTpStages() {
    if (!tpTheme) return;
    var t = tpTheme;

    /* --- 登录页舞台 --- */
    var bgBox = $('#tpLoginBg');
    if (bgBox) {
      bgBox.innerHTML = '';
      var bgUrl = cleanUrl(t.t2);
      if (bgUrl) {
        if (isVideoUrl(bgUrl)) {
          var v = document.createElement('video');
          v.src = bgUrl; v.muted = true; v.loop = true; v.autoplay = true; v.playsInline = true;
          v.setAttribute('playsinline', '');
          v.className = 'tp-media';
          v.addEventListener('error', function () { tpMediaFail(bgBox, bgUrl, '登录页背景'); });
          bgBox.appendChild(v);
          var p = v.play();
          if (p && typeof p.catch === 'function') p.catch(function () {});
        } else {
          var bi = document.createElement('img');
          bi.src = bgUrl; bi.alt = '登录页背景'; bi.className = 'tp-media';
          bi.addEventListener('error', function () { tpMediaFail(bgBox, bgUrl, '登录页背景'); });
          bgBox.appendChild(bi);
        }
      } else {
        bgBox.classList.add('tp-empty');
        bgBox.innerHTML = '<span class="tp-empty-txt">该主题未提供登录页背景</span>';
      }
    }

    var logo = $('#tpLoginLogo');
    if (logo) {
      var lu = cleanUrl(t.t1);
      if (lu) { logo.src = lu; logo.hidden = false; logo.onerror = function () { logo.hidden = true; }; }
      else { logo.hidden = true; logo.removeAttribute('src'); }
    }

    var dev = $('#tpDeviceLogo');
    if (dev) {
      var du = cleanUrl(t.t3);
      if (du) { dev.src = du; dev.hidden = false; dev.onerror = function () { dev.hidden = true; }; }
      else { dev.hidden = true; dev.removeAttribute('src'); }
    }

    /* --- 桌面壁纸舞台 --- */
    var wp = $('#tpWallpaper');
    if (wp) fillTpMedia(wp, cleanUrl(t.t4), '桌面壁纸');

    /* --- 大图舞台（登录LOGO / 设备LOGO） --- */
    var stage = $('#tpAssetStage');
    if (stage) {
      var def = TP_FIELDS.filter(function (f) { return f.key === tpAssetKey; })[0] || TP_FIELDS[1];
      fillTpMedia(stage, cleanUrl(t[tpAssetKey]), def.label);
      var cap = $('#tpAssetCap');
      if (cap) {
        var cu = cleanUrl(t[tpAssetKey]);
        cap.innerHTML = '<b>' + esc(def.label) + '</b><span class="tp-cap-use">' + esc(def.use) + '</span>' +
          (cu ? '<code class="tp-cap-url">' + esc(cu) + '</code>' : '');
      }
    }

    /* --- 舞台切换 ---
       「登录LOGO」与「设备LOGO」两个标签共用 asset 视图，
       靠各自的 data-key 决定看哪个字段。 */
    $$('#tpStageTabs .tp-stage-tab').forEach(function (btn) {
      var s = btn.getAttribute('data-stage');
      var k = btn.getAttribute('data-key');
      var on = (s === tpStage) && (!k || k === tpAssetKey);
      btn.classList.toggle('active', on);
    });
    var vLogin = $('#tpViewLogin'), vDesk = $('#tpViewDesktop'), vAsset = $('#tpViewAsset');
    if (vLogin) vLogin.hidden = tpStage !== 'login';
    if (vDesk) vDesk.hidden = tpStage !== 'desktop';
    if (vAsset) vAsset.hidden = tpStage !== 'asset';
  }

  /* 渲染右侧资源清单 */
  function renderTpAssetList() {
    var box = $('#tpAssetList');
    if (!box || !tpTheme) return;
    var t = tpTheme;
    var html = '';
    TP_FIELDS.forEach(function (f) {
      var u = cleanUrl(t[f.key]);
      var thumb = u
        ? '<span class="tp-al-thumb">' +
            (isVideoUrl(u)
              ? '<video src="' + esc(u) + '" muted loop autoplay playsinline></video>'
              : '<img src="' + esc(u) + '" alt="" loading="lazy" onerror="this.parentNode.classList.add(\'tp-al-broken\')">') +
          '</span>'
        : '<span class="tp-al-thumb tp-al-none">—</span>';
      html += '<div class="tp-asset-item' + (u ? '' : ' tp-al-empty') + '" data-asset-key="' + f.key + '">' +
        thumb +
        '<span class="tp-al-main">' +
          '<span class="tp-al-label">' + esc(f.label) + '</span>' +
          '<span class="tp-al-use">' + esc(f.use) + '</span>' +
        '</span>' +
        '<span class="tp-al-tag">' + (u ? (isVideoUrl(u) ? '视频' : '图片') : '无') + '</span>' +
      '</div>';
    });
    box.innerHTML = html;

    $$('.tp-asset-item', box).forEach(function (item) {
      item.addEventListener('click', function () {
        var key = item.getAttribute('data-asset-key');
        var f = TP_FIELDS.filter(function (x) { return x.key === key; })[0];
        if (!f) return;
        if (!cleanUrl(tpTheme[key])) {
          toast('该主题没有「' + f.label + '」资源', 'err');
          return;
        }
        /* 按该项归属的标签切换：
           t1 → 「登录LOGO」标签、t3 → 「设备LOGO」标签、
           t2 → 「登录页效果」、t4 → 「桌面壁纸」 */
        tpStage = f.tab;
        if (f.tabKey) tpAssetKey = f.tabKey;
        renderTpStages();
      });
    });

    var note = $('#tpFootNote');
    if (note) {
      var n = TP_FIELDS.filter(function (f) { return cleanUrl(t[f.key]); }).length;
      note.textContent = '共 ' + n + ' 项可用资源';
    }
  }

  function previewTheme(id) {
    openThemePreview(id);
  }

  /* 只刷新「图片与壁纸」分组的输入框与缩略图。
     ⚠️ 不用 renderPersonalize() 全量重建 —— 那会把「网站标题」等
        其它分组里用户正在输入、尚未保存的内容一并冲掉。 */
  function syncMediaFields() {
    FIELDS.filter(function (f) { return f.type === 'media'; }).forEach(function (f) {
      var input = $('#f_' + f.key);
      if (input) input.value = state.config[f.key] || '';
    });
    renderPreviews();
  }

  /* 主题卡片「应用」按钮的 loading 态
     （点一下要走 POST config → POST apply 两步异步，必须给反馈 + 防重复点击） */
  function setApplyBusy(btn, busy, text) {
    if (!btn) return;
    if (busy) {
      /* ⚠️ 只在**第一次**进入 busy 时记录原文。
            applyTheme() 会分两步（写配置 → 应用系统）各调一次 setApplyBusy(true)，
            第二次进来时 btn.textContent 已经是「应用中…」了，
            若无条件覆盖 origText，恢复时按钮文案就永远停在「应用中…」。 */
      if (!btn.dataset.origText) btn.dataset.origText = btn.textContent;
      btn.classList.add('loading');
      btn.disabled = true;
      btn.innerHTML = '<span class="btn-spin"></span>' + (text || '应用中…');
    } else {
      btn.classList.remove('loading');
      btn.disabled = false;
      var orig = btn.dataset.origText || '应用';
      btn.textContent = orig;
      delete btn.dataset.origText;
    }
  }

  /* 把主题的资源 URL 写入个性化字段，并直接应用到 fnOS 系统
     ---------------------------------------------------------------
     调用入口：**只有预览弹窗底部的「应用到系统」按钮**
     （主题库卡片上不再有「应用」按钮 —— 流程是「先预览，看清效果，再应用」）
     执行两步：
       1. POST api/config  写入主题的 5 个资源字段
       2. POST api/apply   触发后台 worker 写 /usr/trim/www/（真正生效）
       3. 同步刷新个性化设置面板 → 「图片与壁纸」分组里的缩略图与
          输入框同步刷新成新主题的资源
     ⚠️ api/apply 是异步的（先回响应、后台再写 www/），
        收到 200 只代表「已受理」，实际生效要等 2~5 秒（nginx 重建）。 */
  function applyTheme(id, opts) {
    opts = opts || {};
    var t = state.themes.filter(function (x) { return String(x.ID) === String(id); })[0];
    if (!t) { toast('未找到该主题', 'err'); return; }
    var map = {
      loginLogo: cleanUrl(t.t1),
      loginBackground: cleanUrl(t.t2),
      deviceLogo: cleanUrl(t.t3),
      desktopWallpaper: cleanUrl(t.t4),
      webFavicon: cleanUrl(t.t5)
    };
    var payload = {};
    var any = false;
    Object.keys(map).forEach(function (k) {
      if (map[k]) { payload[k] = map[k]; any = true; }
    });
    if (!any) { toast('该主题没有可用的图片字段', 'err'); return; }

    var title = t['标题'] || ('主题 ' + id);
    var btn = opts.btn || null;

    /* ---- 第 1 步：写入本应用配置 ---- */
    toast('正在应用主题「' + title + '」…');
    setApplyBusy(btn, true, '应用中…');
    api('config', { method: 'POST', body: payload }).then(function (r) {
      if (!r || !r.success) {
        setApplyBusy(btn, false);
        toast((r && r.message) || '主题写入失败', 'err');
        return;
      }
      Object.keys(payload).forEach(function (k) {
        state.config[k] = payload[k];
        /* 主题资源是外链，不走本应用的 CGI 预览通道 */
        state.preview[k] = '';
      });
      /* 主题没提供的资源字段，也要清掉旧的 CGI 预览通道 */
      Object.keys(state.preview).forEach(function (k) { state.preview[k] = ''; });

      /* 同步刷新「图片与壁纸」分组的缩略图 + 输入框 */
      syncMediaFields();

      if (opts.skipSystem) {
        setApplyBusy(btn, false);
        toast('主题「' + title + '」已写入个性化设置', 'ok');
        return;
      }

      /* ---- 第 2 步：应用到 fnOS 系统 ---- */
      setApplyBusy(btn, true, '应用中…');
      api('apply', { method: 'POST', body: {} }).then(function (ar) {
        setApplyBusy(btn, false);
        if (ar && ar.success) {
          toast('主题「' + title + '」已应用到系统，正在生效…', 'ok');
          if (opts.onDone) opts.onDone();
        } else {
          toast('已写入设置，但应用到系统失败：' + ((ar && ar.message) || '未知错误'), 'err');
        }
      }).catch(function (e) {
        setApplyBusy(btn, false);
        toast('已写入设置，但应用到系统失败：' + e.message, 'err');
      });
    }).catch(function (e) {
      setApplyBusy(btn, false);
      toast('主题写入失败：' + e.message, 'err');
    });
  }

  // 只把当前配置应用到 fnOS 系统（不重写 FnDesk 侧配置）
  //
  // ⚠️ 后端是异步的：
  //   写 /usr/trim/www/ 会触发 fnOS 的 nginx 停启重建（ngx_http_trim_recover 模块），
  //   整个过程约 2 秒，期间在途连接会被切断 → 浏览器 fetch() 抛 "Failed to fetch"。
  //   所以后端的做法是：**先把 HTTP 响应发回来，再由独立后台进程去写 www/**。
  //   这里收到 200 就说明「已受理」，实际生效需要等 2~5 秒（nginx 重建 + 校验）。
  function applyToSystem() {
    toast('正在应用到 fnOS 系统…');
    api('apply', { method: 'POST', body: {} }).then(function (r) {
      if (r && r.success) {
        toast(r.message || '已应用到系统，正在生效…', 'ok');
      } else {
        toast((r && r.message) || '应用失败', 'err');
      }
    });
  }

  /* ---------------------------- 更新检查 ----------------------------
   * 打开软件时自动检查 GitHub 上的最新版本（后端 /api/version 服务端拉取，
   * 避开浏览器 CORS），发现新版本后在顶部弹出更新横幅并附下载链接。
   * 「自动检查更新」开关与「忽略的版本」只存浏览器 localStorage，
   * 不写 fnstyle.json（属于客户端偏好，不是系统个性化配置）。 */
  var UPDATE_IGNORE_KEY = 'fnstyle_ignored_version_v1';
  var AUTOCHECK_KEY = 'fnstyle_autocheck_update_v1';

  function getAutoCheck() {
    try { return localStorage.getItem(AUTOCHECK_KEY) !== '0'; } catch (e) { return true; }
  }
  function setAutoCheck(v) {
    try { localStorage.setItem(AUTOCHECK_KEY, v ? '1' : '0'); } catch (e) {}
  }

  function showUpdateBanner(r) {
    var b = $('#updateBanner');
    if (!b) return;
    $('#ubLatest').textContent = 'v' + (r.latest || '');
    $('#ubCurrent').textContent = 'v' + (r.current || '');
    var parts = [];
    if (r.releaseDate) parts.push(r.releaseDate);
    if (r.changelog) parts.push(r.changelog);
    $('#ubChangelog').textContent = parts.join('  ·  ');
    var dl = $('#ubDownload');
    if (dl) dl.href = r.downloadUrl || '';
    b.hidden = false;
  }
  function hideUpdateBanner() {
    var b = $('#updateBanner');
    if (b) b.hidden = true;
  }

  /* opts.silent=true 时不改写「立即检查更新」按钮的状态文字（自动检查用） */
  function checkForUpdate(opts) {
    opts = opts || {};
    var statusEl = $('#updateCheckStatus');
    if (statusEl && !opts.silent) statusEl.textContent = '检查中…';
    return api('version').then(function (r) {
      if (!r || r.success === false || !r.latest) {
        if (statusEl && !opts.silent) statusEl.textContent = '检查更新失败';
        return null;
      }
      if (!r.updateAvailable) {
        if (statusEl && !opts.silent) statusEl.textContent = '已是最新版本（v' + r.latest + '）';
        hideUpdateBanner();
        return r;
      }
      /* 用户曾点过「忽略此版本」→ 不再弹横幅 */
      var ig = null;
      try { ig = localStorage.getItem(UPDATE_IGNORE_KEY); } catch (e) {}
      if (ig && ig === String(r.latest)) {
        if (statusEl && !opts.silent) statusEl.textContent = '已是最新（已忽略 v' + r.latest + '）';
        return r;
      }
      showUpdateBanner(r);
      if (statusEl && !opts.silent) statusEl.textContent = '';
      return r;
    }).catch(function () {
      if (statusEl && !opts.silent) statusEl.textContent = '检查更新失败';
      return null;
    });
  }

  function setupUpdateUI() {
    var cb = $('#autoCheckUpdate');
    if (cb) {
      cb.checked = getAutoCheck();
      var txt = $('#autoCheckText');
      if (txt) txt.textContent = cb.checked ? '开启' : '关闭';
      cb.addEventListener('change', function () {
        setAutoCheck(cb.checked);
        if (txt) txt.textContent = cb.checked ? '开启' : '关闭';
        if (cb.checked) checkForUpdate({ silent: true });
      });
    }
    var ignore = $('#ubIgnore');
    if (ignore) ignore.addEventListener('click', function () {
      var latest = ($('#ubLatest').textContent || '').replace(/^v/, '');
      try { if (latest) localStorage.setItem(UPDATE_IGNORE_KEY, latest); } catch (e) {}
      hideUpdateBanner();
    });
    var later = $('#ubLater');
    if (later) later.addEventListener('click', hideUpdateBanner);
    var check = $('#btnCheckUpdate');
    if (check) check.addEventListener('click', function () { checkForUpdate({ silent: false }); });
  }

  /* ---------------------------- 初始化 ---------------------------- */
  function init() {
    /* 标签页 */
    function switchTab(name) {
      var btn = null;
      $$('.tab').forEach(function (t) {
        if (t.getAttribute('data-tab') === name) btn = t;
      });
      if (!btn) return;
      $$('.tab').forEach(function (x) { x.classList.remove('active'); });
      $$('.panel').forEach(function (x) { x.classList.remove('active'); });
      btn.classList.add('active');
      var p = $('#panel-' + name);
      if (p) p.classList.add('active');
      syncTabAction(name);
    }

    /* 「应用到系统」按钮只在「飞牛个性化设置」标签下显示，
       固定挂在标签栏最右侧 */
    function syncTabAction(name) {
      var slot = $('#tabAction');
      if (!slot) return;
      slot.innerHTML = '';
      if (name !== 'personalize') return;
      var b = document.createElement('button');
      b.className = 'btn primary';
      b.id = 'btnApplyNow';
      b.textContent = '应用到系统';
      b.addEventListener('click', applyToSystem);
      slot.appendChild(b);
    }
    $$('.tab').forEach(function (t) {
      t.addEventListener('click', function () {
        switchTab(t.getAttribute('data-tab'));
      });
    });
    // 支持 #tab 直达（如 #themes）
    if (location.hash && location.hash.length > 1) {
      switchTab(location.hash.replace('#', ''));
    } else {
      syncTabAction('personalize');
    }

    /* 加载配置 */
    api('config').then(function (r) {
      if (!isConfig(r)) {
        // 后端返回的是「响应包装」而不是配置对象：
        //   - 网络不可达 → api() 折叠成 {success:false, message:'后端服务不可用…'}
        //   - 响应被截断导致解析失败 → {success:false, message:'响应解析失败: …'}
        // message 本身已经写清楚了，不要再加「读取配置失败：」这类前缀重复一遍。
        toast((r && r.message) || '读取配置失败', 'err');
        renderPersonalize({});
        return;
      }
      renderPersonalize(r);
    }).catch(function () {
      /* 兜底：api() 正常情况下不会 reject（异常已折叠），这里只在它被改动时生效。 */
      toast('后端服务不可用，请确认应用已启动', 'err');
    });

    /* 主题库：打开软件即默认加载。
       ⚠️ 顺序不能乱：先渲染本地已播种 / 缓存的主题（秒开，离线也有内容），
          再读库源，最后静默拉远程增量更新。旧版 loadLibs() 与 loadThemes()
          并行，慢返回的本地结果会把刚拉到的远程主题覆盖回旧数据。 */
    loadThemes()
      .then(loadLibs)
      .then(autoLoadDefaultLibs)
      .catch(function () { /* api() 已把网络异常折叠成响应对象，这里只防意外 */ });

    /* 更新检查：绑定「更新」设置卡片，并按开关自动检查一次 */
    setupUpdateUI();
    if (getAutoCheck()) {
      checkForUpdate({ silent: true });
    }

    /* 库源下拉菜单开合 */
    var msToggle = $('#libSelectToggle');
    var msMenu = $('#libSelectMenu');
    if (msToggle && msMenu) {
      msToggle.addEventListener('click', function (e) {
        e.stopPropagation();
        msMenu.hidden = !msMenu.hidden;
        msToggle.classList.toggle('open', !msMenu.hidden);
      });
      document.addEventListener('click', function (e) {
        if (msMenu.hidden) return;
        if (msMenu.contains(e.target) || msToggle.contains(e.target)) return;
        msMenu.hidden = true;
        msToggle.classList.remove('open');
      });
    }

    /* 按钮 */
    var ids = {
      btnAllLibs: function () {
        state.libs.forEach(function (l) { state.selectedLibs[l.url] = true; });
        saveLibSelection(true);
        renderLibs();
      },
      btnNoLibs: function () {
        state.libs.forEach(function (l) { delete state.selectedLibs[l.url]; });
        saveLibSelection(true);
        renderLibs();
      },
      btnManageLibs: function () { openLibEditor('manage'); },
      btnLoadSelected: function () { loadSelectedLibs(false); },
      btnLoadMore: function () {
        state.shownThemes += PAGE_SIZE;
        renderThemes(false);
      }
    };
    Object.keys(ids).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('click', ids[id]);
    });

    /* 库源编辑器弹窗 */
    var libForm = $('#libEditorForm');
    if (libForm) libForm.addEventListener('submit', function (e) { e.preventDefault(); saveLibForm(); });
    var btnClose = $('#btnCloseLibEditor');
    if (btnClose) btnClose.addEventListener('click', closeLibEditor);
    var btnShowAdd = $('#btnShowAddLibForm');
    if (btnShowAdd) btnShowAdd.addEventListener('click', showAddLibForm);
    var btnCancelForm = $('#btnCancelLibForm');
    if (btnCancelForm) btnCancelForm.addEventListener('click', cancelLibForm);
    var btnSaveForm = $('#btnSaveLibForm');
    if (btnSaveForm) btnSaveForm.addEventListener('click', function (e) { e.preventDefault(); saveLibForm(); });
    var libOverlay = $('#libEditorOverlay');
    if (libOverlay) {
      /* 点击遮罩空白处关闭 */
      libOverlay.addEventListener('click', function (e) {
        if (e.target === libOverlay) closeLibEditor();
      });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && libOverlay && !libOverlay.hidden) closeLibEditor();
      if (e.key === 'Escape') {
        var tpOv = $('#themePreviewOverlay');
        if (tpOv && !tpOv.hidden) closeThemePreview();
      }
    });

    /* ---------------- 主题预览弹窗 ---------------- */
    var tpOverlay = $('#themePreviewOverlay');
    if (tpOverlay) {
      /* 点击遮罩空白处关闭 */
      tpOverlay.addEventListener('click', function (e) {
        if (e.target === tpOverlay) closeThemePreview();
      });
    }
    var btnTpClose = $('#btnCloseThemePreview');
    if (btnTpClose) btnTpClose.addEventListener('click', closeThemePreview);
    var btnTpClose2 = $('#btnCloseThemePreview2');
    if (btnTpClose2) btnTpClose2.addEventListener('click', closeThemePreview);

    /* 舞台切换
       「登录LOGO」/「设备LOGO」两个标签共用 asset 视图，
       用 data-key 指定要看哪个字段。 */
    $$('#tpStageTabs .tp-stage-tab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        tpStage = btn.getAttribute('data-stage');
        var k = btn.getAttribute('data-key');
        if (k) tpAssetKey = k;
        renderTpStages();
      });
    });

    /* 预览弹窗里的「应用到系统」
       —— 主题库卡片上不再有「应用」按钮，应用统一走这里。
       应用期间弹窗**保持打开**（让用户看得到过程与结果），
       按钮进入 loading 态防重复点击；成功后自动关闭弹窗。 */
    var btnTpApply = $('#btnApplyPreviewedTheme');
    if (btnTpApply) btnTpApply.addEventListener('click', function () {
      if (!tpTheme) return;
      var id = tpTheme.ID;
      applyTheme(id, {
        btn: btnTpApply,
        onDone: function () {
          /* 应用成功 → 关掉预览弹窗，回到主题库 */
          closeThemePreview();
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
