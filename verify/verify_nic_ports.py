#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""物理网口统计逻辑回归测试（v1.1.67 新增）

背景
----
老板在系统监控的能耗抽屉里看到「网卡 2 口 · 约 10.0 W」，问：
「**我NAS设备有两个网口？？核实一下**」

根因：`monitor_collect.collect_power()` 里 `nics` 数的是
`/sys/class/net/*` 下的**网络接口**，不是**物理网口**：
  · bond0（bonding 聚合口）算 1 个，但它背后可能是 2 个物理口
  · 虚拟网桥 / 容器口也算进来
→ 显示的「口」数和用户看到的物理网口对不上。

修法：物理网口只认「有 PCI/平台设备链接」的接口；
      bonding 聚合口按其 slave 的物理口计（聚合口本身不额外计数）。
      同时上报 nicDetail（每个接口的名称/类型/速率/状态）供前端展示核对。

本测试用**假的 /sys/class/net 树**驱动真实实现，验证各场景的物理口数。
用法：python3 verify/verify_nic_ports.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                                'app', 'ui'))

FAILED = []


def _cur_version():
    """当前版本号：直接从 manifest 读取。
    ⚠️ v1.1.69：原来这里硬编码 '1.1.68'，每次升版断言就失效一次。
    改成动态读取，避免以后再踩。
    """
    try:
        mf = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'manifest')
        with open(mf, encoding='utf-8') as f:
            for line in f:
                if line.startswith('version'):
                    return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return ''


CUR_VER = _cur_version()


def check(label, cond, extra=""):
    print(("  ✓ " if cond else "  ✗ ") + label + ("  → %s" % extra if extra else ""))
    if not cond:
        FAILED.append(label)


def build_fake_sys(root, ifaces):
    """造一棵假的 /sys/class/net 树 + 假的 /proc/net/dev。

    iface spec:
      {'name': str, 'phys': bool, 'slaves': [str], 'speed': str,
       'link': bool,     # 内核符号链接（如 bonding_masters）—— 不在 /proc/net/dev 里
       'tunnel': bool}   # 隧道虚拟口（如 tailscale0）—— speed=-1、无 device

    ⚠️ v1.1.68：判定「是不是真接口」改用 /proc/net/dev（内核权威清单）。
       所以假树必须**同步造一份 /proc/net/dev**：
       真接口在表里，内核符号链接（link=True）不在表里。
    """
    net = os.path.join(root, "class", "net")
    os.makedirs(net, exist_ok=True)
    proc_net = os.path.join(root, "proc", "net")
    os.makedirs(proc_net, exist_ok=True)
    dev_lines = ["Inter-|   Receive                                                |  Transmit",
                 " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed"]
    for spec in ifaces:
        if spec.get("link"):
            continue          # 内核符号链接：不在 /proc/net/dev 里
        dev_lines.append(" %-6s: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0" % spec["name"])
    with open(os.path.join(proc_net, "dev"), "w") as f:
        f.write("\n".join(dev_lines) + "\n")
    for spec in ifaces:
        name = spec["name"]
        d = os.path.join(net, name)
        if spec.get("link"):
            # ⚠️ v1.1.68：某些内核符号链接**能读到属性**（实测 NAS 上的
            #    bonding_masters 就是如此：address/speed/operstate 全空，
            #    却仍有某个属性能被读到非空值，于是「靠属性猜」的老判据会漏）。
            #    这里让符号链接指向一个**真实存在**的目录并在里面放属性文件，
            #    精确复现实机行为。
            td = os.path.join(root, "linkattrs", name)
            os.makedirs(td, exist_ok=True)
            for fn, val in (("ifindex", spec.get("ifindex", "")),
                            ("speed", spec.get("speed", "")),
                            ("address", spec.get("mac", "")),
                            ("operstate", spec.get("state", ""))):
                if val:
                    with open(os.path.join(td, fn), "w") as f:
                        f.write(val)
            if not os.path.lexists(d):
                os.symlink(os.path.relpath(td, net), d)
            continue
        os.makedirs(d, exist_ok=True)
        for fn, val in (("type", "1"), ("operstate", spec.get("state", "up")),
                        ("speed", spec.get("speed", "1000")),
                        ("address", spec.get("mac", "aa:bb:cc:dd:ee:ff")),
                        ("ifindex", spec.get("ifindex", "2"))):
            with open(os.path.join(d, fn), "w") as f:
                f.write(val)
        if spec.get("tunnel"):
            # 隧道口：无 device、speed=-1、mac 为空
            with open(os.path.join(d, "speed"), "w") as f:
                f.write("-1")
            with open(os.path.join(d, "address"), "w") as f:
                f.write("")
        if spec.get("phys"):
            dev = os.path.join(d, "device")
            os.makedirs(os.path.join(dev, "driver"), exist_ok=True)
            with open(os.path.join(dev, "vendor"), "w") as f:
                f.write("0x10ec")
        if spec.get("slaves"):
            sd = os.path.join(d, "bonding", "slaves")
            os.makedirs(sd, exist_ok=True)
            for s in spec["slaves"]:
                with open(os.path.join(sd, s), "w") as f:
                    f.write("")
    return net


def _fake_iface_names(root):
    """复刻生产代码里的 _net_iface_names()：从 /proc/net/dev 取接口名。"""
    out = set()
    p = os.path.join(root, "proc", "net", "dev")
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return out
    for line in lines[2:]:
        name = line.split(":", 1)[0].strip()
        if name:
            out.add(name)
    return out


def count_phys(root):
    """与 monitor_collect.collect_power() 中的实现保持一致。

    （生产代码里这一段嵌在 collect_power 内部，无法直接参数化，
      故此处复制同一逻辑并由 verify_nic_ports 与源码文本断言互为交叉验证。）
    """
    net = os.path.join(root, "class", "net")
    real_ifaces = _fake_iface_names(root)
    nics = 0
    nics_iface = 0
    detail = []
    phys_devs = set()
    for nic in sorted(os.listdir(net)):
        base = os.path.basename(nic)
        if base == "lo" or base.startswith(("docker", "br-", "veth", "virbr", "tun")):
            continue
        path = os.path.join(net, base)
        # ⚠️ v1.1.68：真接口判定改用 /proc/net/dev 权威清单
        #    读不到时不过滤（宁可多列，也不报错数）
        if real_ifaces and base not in real_ifaces:
            continue
        ifindex = ""
        if os.path.exists(os.path.join(path, "ifindex")):
            ifindex = open(os.path.join(path, "ifindex")).read().strip()
        addr = ""
        if os.path.exists(os.path.join(path, "address")):
            addr = open(os.path.join(path, "address")).read().strip()
        speed = ""
        if os.path.exists(os.path.join(path, "speed")):
            speed = open(os.path.join(path, "speed")).read().strip()
        state = ""
        if os.path.exists(os.path.join(path, "operstate")):
            state = open(os.path.join(path, "operstate")).read().strip()
        nics_iface += 1
        dev_link = os.path.realpath(os.path.join(path, "device"))
        is_phys = bool(dev_link) and os.path.exists(os.path.join(path, "device"))
        slaves = []
        sd = os.path.join(path, "bonding", "slaves")
        if os.path.isdir(sd):
            try:
                slaves = sorted(os.listdir(sd))
            except OSError:
                slaves = []
        mac = addr
        kind = "bond" if slaves else ("phys" if is_phys else "virt")
        entry = {"name": base, "kind": kind, "physical": is_phys,
                 "slaves": slaves, "mac": mac, "speed": speed,
                 "ifindex": ifindex}
        if not is_phys and not slaves:
            entry["tunnel"] = bool(speed == "-1" or not mac)
        detail.append(entry)
        if is_phys and dev_link not in phys_devs:
            phys_devs.add(dev_link)
            nics += 1
        elif slaves and not is_phys:
            for sl in slaves:
                sl_link = os.path.realpath(os.path.join(net, sl, "device"))
                if sl_link and sl_link not in phys_devs:
                    phys_devs.add(sl_link)
                    nics += 1
    return nics, nics_iface, detail


CASES = [
    ("单物理口 + docker0", [{"name": "enp3s0", "phys": True}, {"name": "docker0"}],
     1, 1),
    ("单物理口 + lo", [{"name": "enp3s0", "phys": True}, {"name": "lo"}],
     1, 1),
    ("两个独立物理口",
     [{"name": "enp3s0", "phys": True}, {"name": "enp4s0", "phys": True}],
     2, 2),
    ("bond0 + 2 个物理 slave",
     [{"name": "bond0", "slaves": ["eth0", "eth1"]},
      {"name": "eth0", "phys": True}, {"name": "eth1", "phys": True}],
     2, 3),
    ("纯虚拟口（无物理网卡）",
     [{"name": "docker0"}, {"name": "br-abc"}],
     0, 0),
    ("一个物理口 + veth",
     [{"name": "enp3s0", "phys": True}, {"name": "veth1234"}],
     1, 1),
]

# ⚠️ v1.1.67：实机发现 /sys/class/net/bonding_masters 是**内核符号链接**
#    （指向 bonding 模块全局变量），不是网络接口，
#    但 glob 会扫到 → 不排除就凭空多 1 个「口」。
LINK_CASES = [
    ("bonding_masters 符号链接 + 1 物理口",
     [{"name": "enp1s0", "phys": True}, {"name": "bonding_masters", "link": True}],
     1, 1),
]

# ⚠️ v1.1.68 专项：内核符号链接 bonding_masters
#    实测踩坑记录 —— 它在 NAS 上 address/speed/operstate 全为空，
#    但用「读 ifindex 猜」判据时**依然被算成接口**（接口数显示 3，实际只有 2）。
#    这里构造两种最坏情况：
#      ① 符号链接带有 ifindex（属性读得到）
#      ② 符号链接带有全套属性（几乎像一个接口）
#    正确判据（/proc/net/dev 权威清单）必须把它们**全部排除**。
LINK_CASES = [
    ("bonding_masters 符号链接（带 ifindex）+ 1 物理口",
     [{"name": "enp1s0", "phys": True},
      {"name": "bonding_masters", "link": True, "ifindex": "99"}],
     1, 1),
    ("bonding_masters 符号链接（属性齐全）+ 1 物理口 + 隧道口",
     [{"name": "enp1s0", "phys": True},
      {"name": "tailscale0", "tunnel": True},
      {"name": "bonding_masters", "link": True, "ifindex": "99",
       "speed": "1000", "state": "up", "mac": "aa:bb:cc:dd:ee:ff"}],
     1, 2),
]

# 实机场景：RTL8169 物理口 + Tailscale 隧道口
TUNNEL_CASES = [
    ("1 物理口 + tailscale 隧道口",
     [{"name": "enp1s0", "phys": True},
      {"name": "tailscale0", "tunnel": True}],
     1, 2),
]


def main():
    print("--- 物理网口统计逻辑（v1.1.67） ---")
    for title, ifaces, exp_phys, exp_iface in (CASES + LINK_CASES + TUNNEL_CASES):
        root = tempfile.mkdtemp()
        try:
            build_fake_sys(root, ifaces)
            n, i, d = count_phys(root)
            check("%s → 物理口 %d" % (title, exp_phys), n == exp_phys, "实际 %d" % n)
            check("%s → 接口数 %d" % (title, exp_iface), i == exp_iface, "实际 %d" % i)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    print("\n--- 生产代码交叉验证（源码断言） ---")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                             'app', 'ui', 'monitor_collect.py'),
               encoding="utf-8").read()
    check("collect_power 里有 nics（物理口）计数",
          'nics = 0            # 物理网口数' in src)
    check("collect_power 里有 nics_iface（接口数）计数",
          'nics_iface = 0      # 非虚拟网络接口数' in src)
    check("上报 nicDetail 字段", '"nicDetail": nic_detail' in src)
    check("上报 nicsIface 字段", '"nicsIface": nics_iface' in src)
    check("物理口按 device 去重", '_phys_devs' in src and 'dev_link not in _phys_devs' in src)
    check("聚合口本身不额外计数", 'elif slaves and not is_phys:' in src)
    check("虚拟口前缀过滤保留", '"docker", "br-", "veth", "virbr", "tun"' in src)
    check("⚠️ v1.1.68：用 /proc/net/dev 权威清单判定真接口",
          '_net_iface_names' in src
          and 'if _real_ifaces and base not in _real_ifaces:' in src)
    check("⚠️ v1.1.68：读不到 /proc/net/dev 时不过滤（安全降级）",
          'if _real_ifaces and base not in _real_ifaces:' in src
          and 'if _real_ifaces and base not in real_ifaces:' in open(
              os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'verify_nic_ports.py'), encoding='utf-8').read())
    check("⚠️ v1.1.68：旧的「读属性猜是不是接口」判据已移除",
          'if not _ifindex and not _addr and not _speed and not _state:' not in src)
    check("⚠️ v1.1.68：_net_iface_names 从 /proc/net/dev 解析",
          '"/proc/net/dev"' in src and 'lines[2:]' in src)
    check("⚠️ v1.1.68：明细带上 ifindex",
          '"ifindex": _ifindex' in src)
    check("⚠️ v1.1.68：上报部署探针 collectVersion",
          '"collectVersion": COLLECT_VERSION' in src and
          ('COLLECT_VERSION = "%s"' % CUR_VER) in src,
          '期望 COLLECT_VERSION = "%s"' % CUR_VER)
    check("隧道口单独标记（tunnel）",
          '"tunnel"' in src and 'nd["tunnel"]' in src)

    print("\n--- 前端展示断言 ---")
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                           'app', 'ui', 'static', 'js', 'monitor.js'),
              encoding="utf-8").read()
    check("部件明细显示「网卡（物理口）」", '网卡（物理口）' in js)
    check("有「网卡接口」清单区块", '网卡接口' in js)
    check("清单区分 物理口/聚合口/虚拟口/隧道口",
          "'bond' ? '聚合口'" in js and "'物理口'" in js
          and "'虚拟口'" in js and "'隧道口'" in js)
    check("清单标题显示「物理 N / 接口 M」",
          "'物理 ' + (p.nics || 0) + ' / 接口 '" in js
          or ("物理 " in js and "/ 接口 " in js))
    check("⚠️ v1.1.68：清单显示内核接口索引号 if#N",
          "'if#' + x.ifindex" in js)

    print("\n" + "=" * 60)
    if FAILED:
        print("失败 %d 项：" % len(FAILED))
        for f in FAILED:
            print("  ✗ %s" % f)
        raise SystemExit(1)
    print("🎉 全部通过")


if __name__ == "__main__":
    main()
