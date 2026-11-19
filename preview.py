#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FnStyle 本地预览服务（开发/演示用，仅依赖 Python 3 标准库）

用途：不装到飞牛 NAS 也能完整打开并操作 FnStyle 界面 ——
      「Fnstyle个性化设置」「Fnstyle主题库」两个标签页、五个库源、
      31 个主题、库源管理/编辑/添加弹窗、主题预览、配置保存、图片上传、
      「应用到系统」按钮，全部可点可用。

与真机 CGI 的差别只有一处：
      /api/apply 在真机上会改写 /usr/trim/www/ 让样式生效；
      本地预览不触碰任何系统文件，只保存配置并如实提示「预览模式」。

运行：
      python3 preview.py                # 默认 http://127.0.0.1:8765/
      python3 preview.py --port 9000
      python3 preview.py --reset        # 清空本地预览数据，回到出厂默认

数据落点：.preview-data/（不进 fpk 包）
默认图片与壁纸：优先取 pic/ 目录，其次 app/seed/fnstyle/，两者同源。
"""

import argparse
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import sys
import time
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "app" / "ui"
SEED_DIR = ROOT / "app" / "seed"
PIC_DIR = ROOT / "pic"
DATA_DIR = ROOT / ".preview-data"
UPLOAD_DIR = DATA_DIR / "upload"
CACHE_DIR = DATA_DIR / "cache"

CONFIG_FILE = DATA_DIR / "config.json"
LIBS_FILE = DATA_DIR / "theme_libs.json"
THEMES_FILE = DATA_DIR / "themes.json"

SEED_CONFIG = SEED_DIR / "fnstyle.json"
SEED_LIBS = SEED_DIR / "theme_list.json"
SEED_CACHE = SEED_DIR / "theme_cache"   # 出厂内置的主题库快照（离线也能看到 31 个主题）

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


# --------------------------------------------------------------------------
# 本地数据（原子写，避免多线程下写坏 JSON）
# --------------------------------------------------------------------------
def read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(path))


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(str(tmp), str(path))


def ensure_seed_data():
    """首次运行：从 seed 复制出可编辑的预览数据，并预置默认主题库的主题。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        shutil.copyfile(str(SEED_CONFIG), str(CONFIG_FILE))
    if not LIBS_FILE.exists():
        shutil.copyfile(str(SEED_LIBS), str(LIBS_FILE))
    if not THEMES_FILE.exists():
        # 默认勾选「Fndesk 主题库」，首屏直接展示它的 31 个主题
        libs = read_json(LIBS_FILE, [])
        themes = []
        for lib in libs:
            if "fndesk" in str(lib.get("name", "")).lower() and "主题库" in str(lib.get("name", "")):
                themes = fetch_remote_json(lib["url"]) or []
                if isinstance(themes, dict):
                    themes = themes.get("themes", [])
                break
        write_json(THEMES_FILE, themes if isinstance(themes, list) else [])


def cache_key(url):
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def fetch_remote_json(url):
    """运行缓存 → 出厂内置快照 → 联网拉取 → 落缓存；失败返回 None。"""
    clean = re.sub(r"[?&]v=\d+$", "", url)
    cached = CACHE_DIR / (cache_key(clean) + ".json")
    if cached.exists():
        return read_json(cached, None)
    seeded = SEED_CACHE / (cache_key(clean) + ".json")
    if seeded.exists():
        return read_json(seeded, None)
    try:
        req = urllib.request.Request(clean, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        data = json.loads(raw.decode("utf-8"))
        write_bytes(cached, raw)
        return data
    except Exception as exc:  # 离线时保持界面可用
        print("[proxy] %s -> %s" % (clean, exc), file=sys.stderr)
        return None


def ok(msg, **extra):
    out = {"success": True, "message": msg}
    out.update(extra)
    return out


def fail(msg):
    return {"success": False, "message": msg}


def safe_name(name):
    """与真机 CGI 一致：只保留安全字符，防目录穿越。"""
    name = os.path.basename(name or "")
    name = re.sub(r"[^A-Za-z0-9._-]", "", name)
    return name if name and name not in (".", "..") else ""


def find_resource(name):
    """上传文件 → pic/ 默认素材 → seed/fnstyle/ 兜底。"""
    if not name or ".." in name or name.startswith("/"):
        return None
    for cand in (UPLOAD_DIR / name, PIC_DIR / name, SEED_DIR / "fnstyle" / name):
        if cand.is_file():
            return cand
    return None


def lib_meta_for(url):
    """proxy-json：按 URL 去官方清单里匹配 {name,desc,from}。"""
    clean = re.sub(r"[?&]v=\d+$", "", url)
    libs = read_json(LIBS_FILE, [])
    for lib in libs:
        if str(lib.get("url", "")).strip() == clean.strip():
            return {
                "name": lib.get("name", ""),
                "desc": lib.get("desc", ""),
                "from": lib.get("from", ""),
            }
    return None


# --------------------------------------------------------------------------
# 系统监控 mock（仅本地预览用）
# --------------------------------------------------------------------------
def mock_monitor():
    """构造一份结构与真机 monitor_collect.py 完全一致的监控数据。

    字段名必须与后端一致，否则前端渲染会走「不可用」分支，
    本地预览就失去了验证价值。
    """
    import random
    now = int(time.time())
    rnd = random.Random(now // 3)

    cpu_pct = rnd.randint(6, 58)
    mem_pct = rnd.randint(38, 79)
    temp_pct = rnd.randint(35, 62)

    return {
        "success": True,
        "available": True,
        "ts": now,
        "interval": 3,
        "uptime": "12 天 7 小时",
        "cpu": {
            "available": True,
            "percent": cpu_pct,
            "model": "Intel(R) Core(TM) i5-12400F 6-Core 12-Thread Processor",
            "cores": 12,
            "freqText": "4.40 GHz",
            "loadavg": ["1.24", "0.98", "0.87"],
            "coresPercent": [rnd.randint(0, 95) for _ in range(12)],
        },
        "mem": {
            "available": True,
            "total": 34359738368,
            "used": int(34359738368 * mem_pct / 100),
            "availableBytes": int(34359738368 * (100 - mem_pct) / 100),
            "percent": mem_pct,
            "totalText": "32.0 GB",
            "usedText": "%.1f GB" % (32.0 * mem_pct / 100),
            "availText": "%.1f GB" % (32.0 * (100 - mem_pct) / 100),
            "swapTotal": 2147483648,
            "swapUsed": 268435456,
        },
        "gpu": {
            "available": True,
            "gpus": [{
                "vendor": "NVIDIA",
                "name": "NVIDIA GeForce RTX 3060",
                "temp": 48,
                "util": rnd.randint(0, 35),
                "memUsed": 1073741824,
                "memTotal": 12884901888,
                "power": 42.5,
                "utilAvailable": True,
            }],
        },
        "temp": {
            "available": True,
            "source": "thermal+hwmon",
            "cpuTemp": 47.0,
            "mbTemp": 33.0,
            "temps": [
                {"name": "x86_pkg_temp", "chip": "thermal", "value": 47.0, "max": None, "crit": None, "type": "cpu"},
                {"name": "acpitz", "chip": "thermal", "value": 39.0, "max": None, "crit": None, "type": "acpi"},
                {"name": "coretemp Package id 0", "chip": "coretemp", "value": 47.0, "max": 80.0, "crit": 100.0, "type": "cpu"},
                {"name": "coretemp Core 0", "chip": "coretemp", "value": 45.0, "max": 80.0, "crit": 100.0, "type": "cpu"},
                {"name": "coretemp Core 3", "chip": "coretemp", "value": 43.0, "max": 80.0, "crit": 100.0, "type": "cpu"},
                {"name": "nvme composite", "chip": "nvme", "value": 38.0, "max": 70.0, "crit": 80.0, "type": "disk"},
                {"name": "motherboard", "chip": "nct6798", "value": 33.0, "max": None, "crit": None, "type": "board"},
                {"name": "pch", "chip": "nct6798", "value": 41.0, "max": None, "crit": None, "type": "pch"},
                {"name": "gpu", "chip": "amdgpu", "value": 52.0, "max": None, "crit": None, "type": "gpu"},
            ],
            "fans": [
                {"name": "nct6798 fan1", "rpm": 1180},
                {"name": "nct6798 fan2", "rpm": 962},
                {"name": "cpu fan", "rpm": 1420},
            ],
            "volts": [
                {"name": "Vcore", "value": 1.28},
                {"name": "+3.3V", "value": 3.31},
                {"name": "+5V", "value": 5.02},
                {"name": "+12V", "value": 12.06},
            ],
            "disks": [
                # slot = 机箱物理插槽（fnmonitor 的 diskSlot 口径，来自 iDRAC）
                {"name": "sda", "slot": 1, "model": "WDC WD40EFZX-68AWUN0", "temp": 36.0,
                 "size": 4000787030016, "sizeText": "3.6 TB", "type": "HDD"},
                {"name": "sdb", "slot": 2, "model": "Samsung SSD 980 PRO 1TB", "temp": 41.0,
                 "size": 1000204886016, "sizeText": "931.5 GB", "type": "SSD"},
                {"name": "nvme0n1", "model": "Samsung SSD 980 PRO 1TB", "temp": 40.0,
                 "size": 1000204886016, "sizeText": "931.5 GB", "type": "SSD",
                 "state": "standby", "standby": True},
            ],
        },
        "storage": {
            "available": True,
            "volumes": [{
                "name": "vol1", "path": "/vol1",
                "total": 5121101905920, "used": 3651694682112, "free": 1316031559680,
                "percent": temp_pct,
                "totalText": "4.7 TB", "usedText": "3.3 TB", "freeText": "1.2 TB",
                "children": [
                    {"name": "@appshare", "total": 214748364800, "used": 161061273600,
                     "free": 53687091200, "percent": 75},
                    {"name": "@docker", "total": 107374182400, "used": 53687091200,
                     "free": 53687091200, "percent": 50},
                    {"name": "homes", "total": 858993459200, "used": 300647710720,
                     "free": 558345748480, "percent": 35},
                ],
            }],
            "raid": [],
            "disks": [
                {"name": "sda", "loc": 1, "model": "WDC WD40EFZX-68AWUN0", "temp": 36.0,
                 "size": 4000787030016, "sizeText": "3.6 TB", "type": "HDD"},
                {"name": "sdb", "loc": 2, "model": "Samsung SSD 980 PRO 1TB", "temp": 41.0,
                 "size": 1000204886016, "sizeText": "931.5 GB", "type": "SSD"},
            ],
        },
        "power": {
            "ok": True,
            "source": "rapl",
            "total": 68.4,
            "package": 42.1,
            "core": 28.6,
            "uncore": 6.2,
            "dram": 7.3,
            "base": 8.0,
            "disksW": 13.0,
            "disksActive": 3,
            "nics": 1,
            "estimation_params": {
                "tdp_w": 65.0,
                "disk_typical_w": 8.0,
                "nic_fixed_w": 5.0,
                "base_w": 8.0,
                "rate_yuan_per_kwh": 0.6,
            },
        },
        "uptimeSeconds": 1036440,
    }

def mock_monitor_empty():
    """全部不可用的降级场景（验证前端空状态显示）。"""
    return {
        "success": True, "available": True, "ts": int(time.time()),
        "interval": 3, "uptime": "—",
        "cpu": {"available": False},
        "mem": {"available": False},
        "gpu": {"gpus": [], "available": False},
        "temp": {"available": False, "source": "none", "cpuTemp": None,
                 "mbTemp": None, "temps": [], "fans": [], "volts": [], "disks": []},
        "storage": {"available": False, "volumes": [], "raid": [], "disks": []},
        "power": {"ok": False, "source": "unknown"},
        "uptimeSeconds": 0,
    }


# 历史序列的可用范围（秒），与 fnmonitor 的 _RANGE_SECONDS 一致
_HISTORY_RANGE_SEC = {
    "10m": 600, "1h": 3600, "6h": 21600,
    "24h": 86400, "7d": 604800, "all": 0,
}


def mock_monitor_history(range_="1h", metrics="cpu,load1"):
    """构造处理器历史序列（趋势图用）。

    真机上后端每 60 秒写一条 SQLite 记录；本机预览按同样的形态生成
    一条假曲线：CPU 在 30~85% 之间波动（有峰值、有低谷，能看出阈值线
    与峰值标注的效果），负载在 0.2~2.5 之间波动。
    """
    seconds = _HISTORY_RANGE_SEC.get(range_, 3600)
    if seconds <= 0:
        seconds = 7 * 86400
    # 采样间隔 60 秒；点太多前端会糊，所以长范围按 5~10 分钟抽稀
    step = 60
    if seconds > 6 * 3600:
        step = 300
    if seconds > 24 * 3600:
        step = 1800
    count = max(2, min(720, seconds // step))

    now = time.time()
    start = now - (count - 1) * step
    names = [m.strip() for m in (metrics or "cpu,load1").split(",") if m.strip()]

    series = {}
    for i, name in enumerate(names):
        pts = []
        for k in range(count):
            ts = start + k * step
            if name == "cpu":
                # 两段正弦叠加 + 趋势，制造出有峰有谷的形态
                phase = (k / max(1, count - 1)) * 6.0
                v = 52 + 26 * math.sin(phase + i) + 12 * math.sin(phase * 2.3)
                if k == int(count * 0.62):
                    v = 93.5          # 制造一个高峰，验证峰值标注
                v = max(1.0, min(99.0, v))
            elif name == "load1":
                v = 1.1 + 0.9 * math.sin((k / max(1, count - 1)) * 4.0 + i)
                v = max(0.0, v)
            elif name == "temp":
                v = 46 + 6 * math.sin((k / max(1, count - 1)) * 5.0 + i)
                v = max(28.0, min(82.0, v))
            elif name == "temp_mb":
                v = 33 + 4 * math.sin((k / max(1, count - 1)) * 3.2 + i)
                v = max(26.0, min(58.0, v))
            elif name == "power":
                v = 62 + 18 * math.sin((k / max(1, count - 1)) * 4.4 + i)
                v = max(20.0, min(140.0, v))
            else:
                v = 50.0
            pts.append([round(ts, 2), round(v, 2)])
        series[name] = pts

    return {"success": True, "range": range_, "series": series}


def mock_power_stats():
    """用电量与电费统计（对齐 fnmonitor 的能耗统计口径）。

    真机上由 history_collect.energy_stats() 从 SQLite 的 power 指标梯形积分；
    本机预览按同样的口径造一份假数据，用于验证能耗板块的用电与电费展示。

    v1.1.43：补齐月 / 年推算字段（month_proj_* / year_proj_* / avg_w），
    对齐 fnmonitor 总览四格「本月预计电费」「全年估算」所需的数据。
    """
    rate = 0.6
    now = time.time()
    boot_kwh = 68.4 * 1036440 / 3.6e6
    today_kwh = 1.2847
    month_kwh = 21.6390
    avg_w = 31.5
    # 本月剩余天数：按当月最后一天算
    import calendar
    import datetime
    now_dt = datetime.datetime.fromtimestamp(now)
    remain_days = (calendar.monthrange(now_dt.year, now_dt.month)[1] - now_dt.day)
    month_proj_kwh = round(month_kwh + avg_w * remain_days * 86400 / 3.6e9, 4)
    year_proj_kwh = round(avg_w * 8.76, 1)
    eff = rate
    return {
        "success": True,
        "today_kwh": today_kwh,
        "month_kwh": month_kwh,
        "boot_kwh": round(boot_kwh, 4),
        "today_yuan": round(today_kwh * rate, 2),
        "month_yuan": round(month_kwh * rate, 2),
        "rate_yuan_per_kwh": rate,
        "basis": "history",
        # v1.1.43：月 / 年推算
        "month_proj_kwh": month_proj_kwh,
        "month_proj_yuan": round(month_proj_kwh * eff, 2),
        "month_proj_remain_days": remain_days,
        "year_proj_kwh": year_proj_kwh,
        "year_proj_yuan": round(year_proj_kwh * eff, 2),
        "avg_w": avg_w,
        "avg_basis": "近 3 天平均",
        "eff_rate_yuan_per_kwh": eff,
        "ts": int(now),
    }


def mock_power_daily(days=30):
    """每日用电量聚合（对齐 fnmonitor 的 /api/daily 口径）。

    真机上由 history_collect.daily_energy() 从 power 历史梯形积分按自然日分桶；
    本机预览造一份形态一致的假数据（近 N 天，每天略有起伏）。
    """
    import datetime
    n = max(1, min(30, int(days or 30)))
    now = datetime.datetime.now()
    out = []
    base = 1.05
    for i in range(n - 1, -1, -1):
        d = now - datetime.timedelta(days=i)
        # 用确定性伪随机（同一天永远同一个值），避免每次刷新图都在跳
        seed = (d.year * 10000 + d.month * 100 + d.day) % 97
        kwh = round(base + (seed % 40) / 100.0, 4)
        power_h = 24.0 if i else 20.5
        out.append({
            "day": "%04d-%02d-%02d" % (d.year, d.month, d.day),
            "kwh": kwh,
            "cost": round(kwh * 0.6, 2),
            "power_h": power_h,
            "has_data": True,
        })
    return {"days": out, "rate_yuan_per_kwh": 0.6, "success": True}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "FnStylePreview/1.0"

    def log_message(self, fmt, *args):
        # 只打印 API 与异常，静态资源不刷屏
        if self.path.startswith("/api/") or " 4" in (fmt % args) or " 5" in (fmt % args):
            sys.stderr.write("[preview] %s %s\n" % (self.address_string(), fmt % args))

    # ---------- 响应工具 ----------
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, cache=False):
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "Not Found")
            return
        ctype = MIME.get(path.suffix.lower()) or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=600" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def body_bytes(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            return self.handle_api(path[5:], query, "GET")

        rel = path.lstrip("/") or "index.html"
        if rel == "index.html":
            return self.send_file(UI_DIR / "index.html")
        # 静态资源只允许落在 app/ui/ 内
        target = (UI_DIR / rel).resolve()
        if UI_DIR.resolve() not in target.parents or not target.is_file():
            return self.send_error(404, "Not Found")
        return self.send_file(target, cache=True)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if not path.startswith("/api/"):
            return self.send_error(404, "Not Found")
        return self.handle_api(path[5:], query, "POST")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        if not path.startswith("/api/"):
            return self.send_error(404, "Not Found")
        return self.handle_api(path[5:], query, "DELETE")

    # ---------- API ----------
    def handle_api(self, name, query, method):
        # ---- 个性化配置 ----
        if name == "config":
            if method == "GET":
                return self.send_json(read_json(CONFIG_FILE, {}))
            try:
                patch = json.loads(self.body_bytes().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(fail("请求体不是合法 JSON"))
            cfg = read_json(CONFIG_FILE, {})
            cfg.update(patch)
            write_json(CONFIG_FILE, cfg)
            return self.send_json(ok("配置已保存"))

        # ---- 版本信息（本地预览：无远程可比，latest 与 current 一致）----
        if name == "version":
            cur = "0.0.0"
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "manifest"), "r", encoding="utf-8") as mf:
                    for line in mf:
                        if line.strip().startswith("version"):
                            cur = line.split("=", 1)[1].strip()
                            break
            except OSError:
                pass
            return self.send_json({
                "current": cur,
                "latest": cur,
                "updateAvailable": False,
                "downloadUrl": "",
                "releaseDate": "",
                "changelog": "",
            })

        if name == "fndesk-config":
            return self.send_json({})

        # ---- 系统监控（本地预览用假数据）----
        # 真机上由 monitor_collect.py 采集；本机（Mac）没有 /proc、/vol*、
        # RAPL 等，采不到真数据，所以给一份结构完全一致的假数据，
        # 用于本地验证前端渲染与排版。
        if name == "monitor":
            # MON_JSON=<路径>：直接回放实机采集到的真实数据（用于实机数据 + 本地渲染的组合验证）
            _real = os.environ.get("MON_JSON")
            if _real and os.path.isfile(_real):
                try:
                    with open(_real, "r", encoding="utf-8") as _f:
                        return self.send_json(json.load(_f))
                except Exception:
                    pass
            return self.send_json(mock_monitor()) if os.environ.get("MON_FULL") else self.send_json(mock_monitor_empty())

        if name == "monitor/history":
            # 处理器历史序列（趋势图数据源）
            # 真机上由 monitor_collect/history_collect 写 SQLite 后查询；
            # 本机预览生成一条形态一致的假曲线，用于验证图表渲染。
            return self.send_json(mock_monitor_history(
                (query.get("range", ["1h"])[0] or "1h"),
                (query.get("metrics", ["cpu,load1"])[0] or "cpu,load1")))

        if name == "power/stats":
            # 用电量与电费统计（能耗板块用）
            return self.send_json(mock_power_stats())

        if name == "power/daily":
            # 每日用电量聚合（能耗板块的柱状图用）
            try:
                _days = int(query.get("days", ["30"])[0] or 30)
            except Exception:
                _days = 30
            return self.send_json(mock_power_daily(_days))


        if name == "apply":
            # 真机上这一步会改写 /usr/trim/www/；预览模式只如实说明，
            # 不假装已经写入 NAS，避免误导。
            return self.send_json(ok(
                "已应用到系统（本地预览模式：配置已保存，未改写 fnOS 系统文件）"
            ))

        # ---- 主题库源 ----
        if name == "theme-libs":
            if method == "GET":
                return self.send_json(read_json(LIBS_FILE, []))
            try:
                libs = json.loads(self.body_bytes().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(fail("请求体不是合法 JSON"))
            write_json(LIBS_FILE, libs)
            return self.send_json(ok("主题库源列表已保存"))

        # ---- 主题数据缓存 ----
        if name == "themes":
            if method == "GET":
                return self.send_json(read_json(THEMES_FILE, []))
            try:
                themes = json.loads(self.body_bytes().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self.send_json(fail("请求体不是合法 JSON"))
            write_json(THEMES_FILE, themes)
            return self.send_json(ok("主题数据已保存"))

        # ---- 远程 JSON 代理 ----
        if name == "proxy":
            url = (query.get("url") or [""])[0]
            if not url.startswith(("http://", "https://")):
                return self.send_json(fail("缺少或非法的 url 参数"))
            data = fetch_remote_json(url)
            return self.send_json(data if data is not None else [])

        if name == "proxy-json":
            url = (query.get("url") or [""])[0]
            if not url.startswith(("http://", "https://")):
                return self.send_json(fail("缺少或非法的 url 参数"))
            data = fetch_remote_json(url)
            meta = lib_meta_for(url)
            if isinstance(data, list):
                return self.send_json({"_meta": meta, "themes": data} if meta else data)
            if isinstance(data, dict):
                data = dict(data)
                if meta:
                    data["_meta"] = meta
                return self.send_json(data)
            return self.send_json([])

        # ---- 图片/视频读取与删除 ----
        if name == "file":
            fname = (query.get("name") or [""])[0]
            target = find_resource(fname)
            if method == "DELETE":
                if target is None:
                    return self.send_json(fail("文件不存在：%s" % fname))
                try:
                    (UPLOAD_DIR / fname).unlink(missing_ok=True)
                    return self.send_json(ok("已删除"))
                except OSError as exc:
                    return self.send_json(fail("删除失败：%s" % exc))
            if target is None:
                return self.send_json(fail("文件不存在：%s" % fname))
            return self.send_file(target)

        # ---- 上传（前端用原始二进制 body，不是 multipart）----
        if name == "upload":
            field = (query.get("field") or ["loginBackground"])[0]
            original = (query.get("name") or [""])[0]
            raw = self.body_bytes()
            if not raw:
                return self.send_json(fail("上传内容为空"))
            safe = safe_name(original) or ("upload" + (Path(original).suffix or ".bin"))
            stored = "fnstyle_" + safe
            write_bytes(UPLOAD_DIR / stored, raw)
            url = "/deskdata/fnstyle/" + stored
            cfg = read_json(CONFIG_FILE, {})
            cfg[field] = url
            write_json(CONFIG_FILE, cfg)
            return self.send_json(ok("上传成功：" + stored, data={
                "field": field,
                "name": stored,
                "url": url,
                "preview": "api/file?name=" + urllib.parse.quote(stored),
            }))

        return self.send_json(fail("Not Found：" + name))


def bind_server(host, port):
    """从指定端口起向后找空闲端口，避免和别的本地服务撞车。"""
    last = OSError()
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate), Handler), candidate
        except OSError as exc:
            last = exc
    raise last


def main():
    ap = argparse.ArgumentParser(description="FnStyle 本地预览服务")
    ap.add_argument("--port", type=int, default=8765, help="起始端口（默认 8765，被占用则自动顺延）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--reset", action="store_true", help="清空预览数据，恢复出厂默认")
    args = ap.parse_args()

    if args.reset and DATA_DIR.exists():
        shutil.rmtree(str(DATA_DIR))
        print("[reset] 已清空 .preview-data/")

    ensure_seed_data()
    themes = read_json(THEMES_FILE, [])
    libs = read_json(LIBS_FILE, [])
    server, port = bind_server(args.host, args.port)
    print("FnStyle 本地预览已启动")
    print("  地址      : http://%s:%d/" % (args.host, port))
    print("  库源      : %d 个   预置主题: %d 个" % (len(libs), len(themes)))
    print("  默认素材  : pic/（loginBackground / loginLogo / deviceLogo / webFavicon / desktopWallpaper）")
    print("  预览数据  : .preview-data/   （--reset 可恢复默认）")
    print("  停止      : Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
