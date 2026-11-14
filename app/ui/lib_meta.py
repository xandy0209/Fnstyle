#!/usr/bin/env python3
"""FnStyle —— 主题库源元信息解析器

用途：给 index.cgi 的 /api/proxy-json 路由提供 JSON 处理能力。

背景（2026-11-12 实测）：
    FnDesk 前端 saveThemeForm() 保存新增主题库时，会去拉
    `api/proxy-json?url=...` 并期待返回体里带 `_meta`（含 name/desc/from）。
    但 FnDesk 的 proxy-json 只是**原样透传**远程 JSON，
    而所有远程主题库文件（theme_bbs.json / theme_mrfz_V3.json ...）
    都是**纯数组**、根本不含 _meta —— 所以 FnDesk 那条 _meta 分支
    是死代码，真实元信息实际来自官方清单 `theme_list.json`。

本脚本把这件事做对：
    输入：一个远程 URL（可带 ?v=时间戳）
    输出：JSON
        - 远程体本身是对象且含 _meta  → 原样透传（兼容未来带 _meta 的库）
        - 远程体是数组                 → 尝试从官方清单 theme_list.json
                                        里按 URL 匹配出 {name,desc,from}，
                                        命中则输出 {"_meta": {...}, "data": [...]}
                                        未命中则输出 {"_meta": null, "data": [...]}
    拉取失败 → 输出 {"success":false,"message":"..."}

用法：
    lib_meta.py <url>
    退出码恒为 0，stdout 恒为一个 JSON 文档。
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# FnDesk 官方主题库清单（含 name/desc/from 的权威来源）
DEFAULT_LIST_URL = "https://fndesk.imcq.top/theme/theme_list.json"

# 允许用环境变量覆盖，便于离线环境自建清单
LIST_URL = os.environ.get("FNSTYLE_THEME_LIST_URL", DEFAULT_LIST_URL)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FnStyle/1.0"

_TIMEOUT = 30


def _ctx():
    """fnOS 宿主可能没有系统 CA 证书，退化为不校验（仅用于拉取公开 JSON）。"""
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def http_get(url):
    """返回 bytes；失败抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ctx()) as resp:
        return resp.read()


def strip_cache_bust(url):
    """去掉 ?v=时间戳 之类的缓存击穿参数，只留 ?v= 之前的主路径。"""
    if not url:
        return url
    base = url.split("?", 1)[0]
    return base


def norm(u):
    """URL 归一化：去首尾空白 + 去 ?v= 时间戳 + 小写，用于匹配。"""
    if not u:
        return ""
    u = str(u).strip()
    if "?v=" in u:
        u = u.split("?v=", 1)[0]
    if u.endswith("?"):
        u = u[:-1]
    return u.lower()


def load_default_meta(target_url):
    """从官方清单里按 URL 找 {name,desc,from}；找不到返回 None。"""
    try:
        raw = http_get(LIST_URL)
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    key = norm(target_url)
    for item in data:
        if not isinstance(item, dict):
            continue
        if norm(item.get("url")) == key:
            return {
                "name": str(item.get("name") or "").strip(),
                "desc": str(item.get("desc") or "").strip(),
                "from": str(item.get("from") or "").strip(),
            }
    return None


def main():
    if len(sys.argv) < 2:
        json.dump({"success": False, "message": "缺少 url 参数"}, sys.stdout, ensure_ascii=False)
        return
    url = sys.argv[1].strip()
    if not url.lower().startswith(("http://", "https://")):
        json.dump({"success": False, "message": "仅支持 http/https"}, sys.stdout, ensure_ascii=False)
        return

    try:
        raw = http_get(url)
    except urllib.error.HTTPError as e:
        json.dump({"success": False, "message": "远程拉取失败：HTTP %s" % e.code},
                  sys.stdout, ensure_ascii=False)
        return
    except Exception as e:
        json.dump({"success": False, "message": "远程拉取失败：%s" % (e.message if hasattr(e, "message") else e)},
                  sys.stdout, ensure_ascii=False)
        return

    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except Exception:
        # 不是合法 JSON —— 原样回传，让前端自己处理
        sys.stdout.write(text)
        return

    # 情况 1：远程体是对象且自带 _meta —— 原样透传
    if isinstance(data, dict) and isinstance(data.get("_meta"), dict):
        sys.stdout.write(json.dumps(data, ensure_ascii=False))
        return

    # 情况 2：远程体是纯数组 —— 去官方清单里匹配元信息
    if isinstance(data, list):
        meta = load_default_meta(url)
        out = {"_meta": meta, "data": data, "url": strip_cache_bust(url)}
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        return

    # 情况 3：其它对象 —— 原样透传
    sys.stdout.write(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
