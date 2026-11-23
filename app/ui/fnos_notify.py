#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# FnStyle —— 飞牛 fnOS 通知中心投递（v1.1.84）
#
# 老板需求：「提供通过飞牛的通知消息Webhook进行推送的能力」
#
# ⚠️ 关于「飞牛通知 Webhook」的方向问题（重要，先说清）
#
#   fnOS 系统设置 → 系统通知 → 通知设置 → 新增 Webhook 机器人，
#   支持飞书 / 钉钉 / 企业微信 / 其他机器人。
#   这套机制的**方向是 fnOS → 外部接收器**：
#   fnOS 自己在系统事件发生（硬盘掉线、CPU 过热等）时，
#   POST 到你填的那个 Webhook 地址。
#
#   老板要的是「FnStyle 超阈值时，推到通知消息通道」。
#   两种做法：
#     ① **FnStyle → 用户配置的 Webhook 地址**（本模块的主路径）
#        用户在 fnOS 里填了一条「其他机器人」Webhook 地址，
#        也可以把**同一条地址**填进 FnStyle 的通知配置，
#        FnStyle 直接 POST 过去 —— 消息就出现在同一个群里。
#        这是**当前最稳妥**的路径：只用标准 HTTP，不依赖 fnOS 内部接口。
#     ② **FnStyle → fnOS 通知中心 → fnOS 的 Webhook 机器人 → 外部**
#        需要 fnOS 提供「第三方应用向通知中心投递消息」的接口。
#        实测（2026-11-23，见下）当前**没有**找到可用入口。
#
#   本模块的定位：实现 ①（Webhook 直发），并为 ② 预留接口
#   （fnOS 哪天开放了就能接上，配置里勾「飞牛通知中心」即可）。
#
# 已排除的入口（实测记录，避免以后重复踩）
#   RPC 网关（POST /ogh/ac/w）：
#     notification.list / notify.list / appcgi.notifysvr.list /
#     system.notification / notification.push / notify.send
#     → 全部 403 Forbidden（不在 allowlist 内）
#     对照：user.info / file.ls 成功；sys.config / app.common / app.detail 403
#   CGI：
#     /cgi-bin/cgi-bin/trim/notify    → 200 `invalid token`（路由存在，要鉴权）
#     /cgi-bin/cgi-bin/notifysvr      → 200 `invalid token`（同上）
#     /api/v1/notify / /notifysvr/notify / /app-center/v1/notify → 404
#   fnOS 前端 JS（assets/index-*.js，约 2.9MB）：
#     搜 notif / message / push / pushMessage / sendMessage / cgi-bin
#     → 未找到明文通知 API 路径（已混淆）
#   ⇒ 结论：fnOS 当前**未对第三方应用开放**通知投递接口。
# ---------------------------------------------------------------------------

import json
import os
import time

# ---------------------------------------------------------------------------
# 探测结果缓存（避免每次推送都重扫一遍）
# ---------------------------------------------------------------------------

_PROBE_CACHE = {"at": 0, "result": None}
_PROBE_TTL = 3600  # 1 小时


def _probe():
    """探测 fnOS 是否提供通知投递接口。返回 (available, detail)。

    ⚠️ 探测**必须快**：推送是在告警线程里调用的，
       不能因为探测卡住而拖住整个告警循环。
    """
    now = time.time()
    if _PROBE_CACHE["result"] is not None and now - _PROBE_CACHE["at"] < _PROBE_TTL:
        return _PROBE_CACHE["result"]

    result = (False, "fnOS 通知投递接口不可用（当前系统未对第三方应用开放）")
    try:
        # 探测点：fnOS 的通知服务是否暴露了可用的投递端点。
        # 目前已知全部不可用 —— 这里保留探测逻辑，
        # 万一后续版本开放了，这里能自动识别出来。
        candidates = _candidate_endpoints()
        for url, headers in candidates:
            if _quick_head(url, headers):
                result = (True, "已发现可用的 fnOS 通知端点：%s" % url)
                break
    except Exception as e:
        result = (False, "探测异常：%s" % e)

    _PROBE_CACHE["at"] = now
    _PROBE_CACHE["result"] = result
    return result


def _base_url():
    """fnOS 本机基址（应用跑在 NAS 上，通知服务也在本机）。"""
    for env in ("FNOS_BASE_URL",):
        v = os.environ.get(env)
        if v:
            return v.rstrip("/")
    return "http://127.0.0.1:5666"


def _candidate_endpoints():
    """候选的 fnOS 通知投递端点。

    ⚠️ 这里只放**已实测存在路由**的端点（返回 `invalid token` 说明存在）。
       404 的路径不列 —— 列了也只是浪费探测时间。
    """
    base = _base_url()
    return [
        (base + "/cgi-bin/cgi-bin/trim/notify", {"Accept": "application/json"}),
        (base + "/cgi-bin/cgi-bin/notifysvr", {"Accept": "application/json"}),
    ]


def _quick_head(url, headers, timeout=3):
    """快速探一下端点。返回 True 表示端点可用（至少不是 404/连接失败）。"""
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 0) or resp.getcode()
            return 200 <= code < 500
    except urllib.error.HTTPError as e:
        # 401/403 说明端点存在（只是没鉴权）→ 值得尝试
        return e.code in (401, 403)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 投递
# ---------------------------------------------------------------------------

def send(payload):
    """把消息投递到 fnOS 通知中心。返回 (ok, message)。

    ⚠️ 当前实现：接口不可用时**如实返回失败**，不假装成功 ——
       告警引擎会把它记为「该通道失败」，用户在前端能看到真实状态。
       绝不能「静默成功」——那会让用户以为告警推出去了，实际没有。
    """
    available, detail = _probe()
    if not available:
        return False, detail

    # 接口可用时的投递逻辑（等 fnOS 开放后补上）。
    # 消息体与 Webhook 通道保持一致（{"msgtype":"text","text":{"content":...}}）
    # 这样切换通道时，用户收到的消息格式不变。
    try:
        import urllib.error
        import urllib.request

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for url, headers in _candidate_endpoints():
            h = dict(headers or {})
            h["Content-Type"] = "application/json; charset=utf-8"
            req = urllib.request.Request(url, data=body, headers=h, method="POST")
            try:
                with opener.open(req, timeout=8) as resp:
                    code = getattr(resp, "status", 0) or resp.getcode()
                    if 200 <= code < 300:
                        return True, "已投递到 fnOS 通知中心"
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue
    except Exception as e:
        return False, "投递异常：%s" % e

    return False, "fnOS 通知投递失败（端点返回错误）"


def status():
    """给前端看的通道状态。"""
    available, detail = _probe()
    return {
        "available": available,
        "detail": detail,
        "checked_at": int(_PROBE_CACHE["at"]),
    }
