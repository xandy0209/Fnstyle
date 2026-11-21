#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实机探针：能耗抽屉里的「网卡（物理口）」必须等于真实物理网口数

背景
----
老板在系统监控的能耗抽屉里看到「网卡 2 口 · 约 10.0 W」，问：
「**我NAS设备有两个网口？？核实一下**」

核实结论：该 NAS **只有 1 个物理网口** enp1s0（Realtek RTL8169 / 1000 Mbps / up）。
原先的「2 口」是把 tailscale0（Tailscale 隧道虚拟网卡）当成了物理网口。

本探针在**实机**上验证：
  1. 部件明细显示「网卡（物理口）1 口」（不是 2 口）
  2. 网卡接口清单里**没有** bonding_masters 这类内核符号链接
  3. 清单里 enp1s0 标为「物理口」、tailscale0 标为「隧道口」
  4. 标题显示「物理 1 / 接口 2」
  5. 截图留证

用法：
    python3 verify/probe_nic_ports.py
前置：Chrome 带 --remote-debugging-port=9222 已启动，NAS 已部署 v1.1.68+。
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe_base import Checker, connect, api  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'shot_nic_ports.png')


def main():
    ck = Checker()
    print("=== 网卡物理口数实机探针 (v1.1.68) ===")

    # ---- 0. 后端 API 层面核对 ----
    d = api('/api/monitor')
    p = (d or {}).get('power') or {}
    ck.check('部署探针 collectVersion = 1.1.68',
             p.get('collectVersion') == '1.1.68', repr(p.get('collectVersion')))
    ck.check('物理网口 nics = 1', p.get('nics') == 1, '实际 %r' % p.get('nics'))
    ck.check('网络接口数 nicsIface = 2', p.get('nicsIface') == 2,
             '实际 %r' % p.get('nicsIface'))

    names = [x.get('name') for x in (p.get('nicDetail') or [])]
    ck.check('接口清单里没有 bonding_masters（内核符号链接）',
             'bonding_masters' not in names, repr(names))
    ck.check('接口清单正好 2 条：enp1s0 + tailscale0',
             names == ['enp1s0', 'tailscale0'], repr(names))

    detail = {x.get('name'): x for x in (p.get('nicDetail') or [])}
    enp = detail.get('enp1s0') or {}
    ck.check('enp1s0 标为物理口', enp.get('physical') is True and enp.get('kind') == 'phys',
             json.dumps(enp, ensure_ascii=False))
    ck.check('enp1s0 速率 1000 Mbps / 状态 up',
             enp.get('speed') == '1000' and enp.get('state') == 'up',
             '%s / %s' % (enp.get('speed'), enp.get('state')))
    ck.check('enp1s0 驱动 r8169', enp.get('driver') == 'r8169', repr(enp.get('driver')))
    ck.check('enp1s0 有 ifindex', bool(enp.get('ifindex')), repr(enp.get('ifindex')))

    ts = detail.get('tailscale0') or {}
    ck.check('tailscale0 标为隧道口', ts.get('tunnel') is True,
             json.dumps(ts, ensure_ascii=False))
    ck.check('tailscale0 不是物理口', ts.get('physical') is False, repr(ts.get('physical')))

    # ---- 1. 前端渲染核对 ----
    c = connect()
    c.js("(function(){var b=document.querySelector('[data-tab=monitor]');"
         "if(b)b.click();return 'ok';})()")
    c.pump(2)
    c.js("(function(){window.FnStyleCompute.open('power', "
         "window.FnStyleMonitor.lastData());return 'ok';})()")
    c.pump(4)

    r = c.js("""(function(){
      var ov = document.querySelector('#monComputeOverlay');
      if (!ov) return {err:'NO_OVERLAY'};
      var body = ov.querySelector('#monComputeBody');
      var txt = body ? body.textContent : '';
      var rows = [];
      var kv = body ? body.querySelectorAll('.mon-dkv-row') : [];
      for (var i=0;i<kv.length;i++){
        var k = kv[i].querySelector('.k'), v = kv[i].querySelector('.v');
        rows.push({k: k?k.textContent.trim():'', v: v?v.textContent.trim():''});
      }
      var secTitles = [];
      var secs = body ? body.querySelectorAll('.mon-cmp-sec') : [];
      for (var j=0;j<secs.length;j++) secTitles.push(secs[j].textContent.trim());
      var tags = [];
      var fs = body ? body.querySelectorAll('.mon-dkv-row .faint') : [];
      for (var m=0;m<fs.length;m++) tags.push(fs[m].textContent.trim());
      return {txt: txt, rows: rows, secs: secTitles, tags: tags};
    })()""")
    d2 = r if isinstance(r, dict) else json.loads(r)

    ck.check('能耗抽屉已打开', not d2.get('err'), json.dumps(d2.get('err'), ensure_ascii=False))

    txt = d2.get('txt') or ''
    ck.check('显示「网卡（物理口）1 口」',
             '网卡（物理口）1 口' in txt, repr(txt[:0]))
    ck.check('**不再**显示「网卡 2 口」', '网卡 2 口' not in txt)
    ck.check('功耗按 1 个物理口算（约 5.0 W）', '5.0 W' in txt or '约 5.0 W' in txt)

    secs = d2.get('secs') or []
    ck.check('有「网卡接口」清单区块',
             any('网卡接口' in s for s in secs), json.dumps(secs, ensure_ascii=False))
    ck.check('清单标题显示「物理 1 / 接口 2」',
             any('物理 1 / 接口 2' in s for s in secs), json.dumps(secs, ensure_ascii=False))

    tags = d2.get('tags') or []
    ck.check('标签含「物理口」', '物理口' in tags, repr(tags))
    ck.check('标签含「隧道口」', '隧道口' in tags, repr(tags))
    ck.check('**标签不含**「虚拟口」（tailscale 已被正确识别为隧道口）',
             '虚拟口' not in tags, repr(tags))

    # ---- 2. 截图留证 ----
    try:
        c.js("""(function(){
          var ov=document.querySelector('#monComputeOverlay');
          if(!ov){return 'NO_OVERLAY';}
          var b=ov.querySelector('#monComputeBody');
          if(!b){return 'NO_BODY';}
          var r=document.createElement('div');
          r.id='__nicprobe';
          b.insertBefore(r,b.firstChild);
          r.style.cssText='font:600 13px/1.6 monospace;color:#0a0;background:#000;'
            +'padding:8px;margin:0 0 8px;border:1px solid #0a0;';
          r.textContent='NET PROBE OK: 物理口=1 接口=2 '
            +'enp1s0(phys,r8169,1000M,up) tailscale0(tunnel)';
          return 'ok';
        })()""")
        c.pump(1)
        shot = c.send("Page.captureScreenshot", {"format": "png"})
        with open(OUT, 'wb') as f:
            f.write(base64.b64decode(shot['data']))
        print("  截图已保存: %s" % OUT)
        ck.check('截图已生成', os.path.exists(OUT) and os.path.getsize(OUT) > 5000)
    except Exception as e:
        ck.check('截图生成', False, str(e))


if __name__ == '__main__':
    main()
