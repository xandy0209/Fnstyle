# FnStyle

面向飞牛 fnOS 的独立应用，提供 **「Fnstyle 个性化设置」** 与 **「Fnstyle 主题库」** 两大模块：把网站标题、登录页、LOGO、桌面壁纸、任务栏等样式一键应用到 fnOS 系统，并在卸载时自动还原系统原生样式。

以独立 fpk 应用分发，不依赖 FnDesk。

---

## 功能

### Fnstyle 个性化设置

| 分组 | 可配置项 |
|---|---|
| 网站信息 | 网站标题、描述 |
| 登录框 | 透明度、显示名字、水平/垂直位置 |
| 桌面与窗口 | 任务栏沉底横置、窗口毛玻璃、图标排序、点击音效、名称显示 |
| 图片与壁纸 | 登录背景、登录 LOGO、设备 LOGO、网页 favicon、桌面壁纸（支持 MP4 视频壁纸） |
| 卸载与还原 | 卸载后自动还原系统默认样式（**默认开启**，可关闭） |

5 个媒体字段均支持上传、预览与「默认」按钮恢复出厂图片，出厂素材取自 `pic/` 与 `app/seed/fnstyle/`。

### Fnstyle 主题库

- **打开即默认加载**：安装时播种默认库 31 个主题，首屏即有内容，无需手动点「重载选库」；后台静默增量更新，失败自动重试、不清空已有主题。
- 预置 5 个库源，支持勾选、重载、添加 / 编辑 / 删除；外链超时或返回错误页时自动回退出厂快照。
- 主题预览窗口提供「资源大图 / 登录 LOGO / 设备 LOGO」三个标签，可直接在预览里应用到系统。

---

## 安装

### 下载

| 安装包 | 直链 |
|---|---|
| `fnstyle_1.1.0_all.fpk` | `https://github.com/xandy0209/Fnstyle/releases/download/v1.1.0/fnstyle_1.1.0_all.fpk` |

或从 [Releases 页面](https://github.com/xandy0209/Fnstyle/releases/tag/v1.1.0) 下载。

校验值：

| 文件 | 大小（字节） | MD5 | SHA256 |
|---|---:|---|---|
| `fnstyle_1.1.0_all.fpk` | 1,269,646 | `63a6d61d067fe7e80f64b73050024739` | `cd3ff59ac89f77b341bb0ed476a72903ad12498a48cbdb4b4c5170b824ad37c3` |

### 安装步骤

**方式 A · 应用中心**：fnOS「应用中心」→「手动安装」→ 选择 fpk 文件。

**方式 B · 命令行**（宿主上以 root 执行）：

```bash
trim-cli app install-fpk fnstyle_1.1.0_all.fpk --dry-run --yes
trim-cli app install-fpk fnstyle_1.1.0_all.fpk --yes
```

> ⚠️ **不支持覆盖安装**，升级需先卸载再装。卸载不会删除数据目录里的配置、主题与上传图片。
> 装完请打开应用**再点一次「应用到系统」**，并强制刷新浏览器（Cmd/Ctrl + Shift + R）——运行时脚本由这一步部署到系统 `www/`。

环境要求：飞牛 fnOS 0.9.0 及以上，x86 / ARM 均可，系统自带 `python3`。

---

## 卸载与还原

软件内置「卸载后自动还原系统默认样式」开关（默认开启），无需另装任何还原工具。卸载采用**两段式**，避免触发 fnOS nginx 停启重建、切断应用中心的卸载请求：

1. **同步阶段**：只中和还原源 `www.zip / tow / raw`，完全不写 `/usr/trim/www/`，卸载请求正常返回。
2. **后台阶段**：独立进程延迟 12 秒后再处理 `www/`、两轮复查并逐项自检，结果写入 `restore_status.json`。

三次写入源（`www.zip`、`tow/`、`www/`）里的注入脚本会被替换为无操作占位、配置写成空值，因此即使系统重建 `www/` 也不会把旧样式带回来；运行时脚本读到空配置会自行停止套用并自动强刷一次。卸载日志位于 `/usr/trim/share/.restore/fnstyle-guard/`。

---

## 自动更新提醒

FnStyle 内置**「有更新自动提醒」**能力，无需接入任何第三方应用商店，纯靠公开 GitHub 仓库即可工作。

**工作原理**（避开 fnOS 浏览器跨域限制）：

1. 打开软件、且「自动检查更新」开关开启时，前端请求本地后端 `GET /api/version`。
2. 后端（NAS 侧）拉取远程版本清单做语义化版本比较：
   - 首选仓库根目录的 `version.json`（`raw.githubusercontent.com`）；
   - 兜底 GitHub Releases API（`api.github.com/.../releases/latest`）。
   - 两者均可由环境变量 `FNSTYLE_VERSION_URL` / `FNSTYLE_VERSION_API` 覆盖，便于自托管或内网测试。
3. 发现更高版本时，前端在页面顶部弹出更新横幅，展示**版本号、发布日期、变更说明**，并提供：
   - **前往下载** —— 跳转到 GitHub Release 附件（fpk 直链）；
   - **忽略此版本** —— 该版本号写入 `localStorage`，不再打扰；
   - **稍后** —— 本次会话不再提示。

网络失败、清单不可达或已是最新版时静默降级（`updateAvailable:false`），不报错、不阻塞界面。

**设置入口**：`Fnstyle个性化设置 → 更新` 分组，提供「自动检查更新」开关（默认开启）与「立即检查更新」按钮。开关状态与忽略版本仅存于浏览器 `localStorage`，不写入 `fnstyle.json`，卸载重装后恢复默认。

要发布一个新版本，只需：在仓库根目录 `version.json` 更新 `version` / `downloadUrl` / `changelog`，并将新 fpk 上传到对应 GitHub Release 即可，客户端会自动感知。

---

## 从源码构建

```bash
git clone https://github.com/xandy0209/Fnstyle.git
cd Fnstyle
bash build.sh              # 产出 ./fnstyle_1.1.0_all.fpk（仓库根目录）
```

`build.sh` 输出到仓库根目录，发布前需自行 `cp` 到 `dist/`。脚本会重算 `manifest` 的 `checksum`（即 `app.tgz` 的 MD5）。

本地预览：`python3 preview.py`（默认端口 8765，被占用时自动顺延）。

---

## 目录结构

```
manifest / config/ / wizard/     fpk 元信息、权限与安装向导
cmd/                            生命周期回调、系统还原核心 restore_system.py、延迟执行器
app/ui/
  index.html / static/          前端页面与样式
  index.cgi                    后端 CGI（纯 bash，不依赖 python3）
  fnstyle_server.py            独立 HTTP 服务（9910 + 网关 unix socket）
  apply_style_overrides.py     应用到系统：部署注入脚本 + 改 index.html
  fnstyle-runtime.js           桌面运行时（壁纸 / 任务栏 / 浮层 / 图标）
  fnstyle-login.js              登录页运行时注入
  apply_worker.sh / recheck.sh 后台写入与复查
app/seed/                       出厂数据：配置、5 张图片、5 个库源、31 个主题快照
pic/                            界面规格里的 5 张默认图片（原始素材）
verify/                         11 组自动化验证脚本
dist/                           已构建的 fpk 安装包
version.json                    远程版本清单（GitHub 自动更新读取此文件）

---

## 验证

`verify/` 下 11 组脚本覆盖写入链路、首次应用、浏览器渲染、默认按钮、空配置自禁用、浮层帧级时序、卸载还原、自动还原开关、异步卸载、主题库默认加载、自动更新检查；`verify/result_*.json` 是对应结果。当前版本全部通过。

---

## 说明

本项目通过改写 fnOS 系统 Web 资源实现个性化，属于系统级定制。首次使用建议先备份 `/usr/trim/share/.restore/` 下的原生副本；本软件安装时也会在系统级 guard 目录留一份基线。
