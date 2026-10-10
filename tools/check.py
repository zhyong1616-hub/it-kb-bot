# -*- coding: utf-8 -*-
"""
一键自检工具（check.bat 调用）
逐项检查环境，并在最后进入"消息监听"模式，验证能否收到企微消息。

用法：python tools/check.py
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)


def line(ch='-', n=60):
    print(ch * n)


def load_env():
    path = os.path.join(BASE, '.env')
    env = {}
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        for l in f:
            l = l.strip()
            if not l or l.startswith('#') or '=' not in l:
                continue
            k, v = l.split('=', 1)
            env[k.strip()] = v.strip()
    return env


def main():
    line('=')
    print('  IT Knowledge Base Bot - SELF CHECK')
    line('=')
    print()

    # --- Python ---
    print('[1] Python:')
    print('    version :', sys.version.split()[0])
    print('    path    :', sys.executable)
    print()

    # --- dependencies ---
    print('[2] Dependencies:')
    try:
        import websockets
        print('    websockets :', websockets.__version__, 'OK')
    except Exception as e:
        print('    websockets : MISSING ->', e)
        print()
        print('    Run install.bat again to fix this.')
        return 1
    print()

    # --- .env ---
    print('[3] Config file (.env):')
    env = load_env()
    if env is None:
        print('    .env NOT FOUND')
        print('    Run install.bat first.')
        return 1
    bot_id = env.get('WECOM_BOT_ID', '')
    secret = env.get('WECOM_BOT_SECRET', '')
    print('    WECOM_BOT_ID       :', (bot_id[:12] + '...') if bot_id else '(EMPTY)')
    print('    WECOM_BOT_SECRET   :', (secret[:12] + '...') if secret else '(EMPTY)')
    print('    KB_MAINTAINER_ID   :', env.get('KB_MAINTAINER_ID', ''))
    print('    KB_MAINTAINER_NAME :', env.get('KB_MAINTAINER_NAME', ''))
    print('    KB_MOCK            :', env.get('KB_MOCK', ''))
    if not bot_id or not secret:
        print()
        print('    ERROR: Bot ID / Secret is empty.')
        print('    Open .env with Notepad and fill them in.')
        return 1
    if env.get('KB_MOCK', '1') == '1':
        print()
        print('    NOTE: KB_MOCK=1, the bot runs in local mock mode.')
        print('          Set KB_MOCK=0 to connect to WeCom.')
    print()

    # --- knowledge base ---
    print('[4] Knowledge base:')
    kb = os.path.join(BASE, 'data', 'kb.csv')
    if os.path.exists(kb):
        import csv
        with open(kb, encoding='utf-8-sig', newline='') as f:
            n = sum(1 for r in csv.reader(f) if r and r[0].strip()) - 1
        print('    data/kb.csv :', n, 'entries OK')
    else:
        print('    data/kb.csv : NOT FOUND (run install.bat)')
    print()

    # --- websocket subscribe ---
    print('[5] WeCom long connection:')
    import asyncio
    rc = asyncio.run(probe(bot_id, secret))
    return rc


async def probe(bot_id, secret):
    import websockets
    import uuid

    def rid(p):
        return f'{p}_{uuid.uuid4().hex[:16]}'

    url = 'wss://openws.work.weixin.qq.com'
    print('    connecting to', url, '...')
    try:
        ws = await websockets.connect(url, ping_interval=None, open_timeout=20)
    except Exception as e:
        print('    CONNECT FAILED:', e)
        print('    Check your network / firewall.')
        return 1
    print('    connected OK')

    await ws.send(json.dumps({
        'cmd': 'aibot_subscribe',
        'headers': {'req_id': rid('sub')},
        'body': {'bot_id': bot_id, 'secret': secret},
    }))
    raw = await asyncio.wait_for(ws.recv(), timeout=15)
    resp = json.loads(raw)
    ec = resp.get('errcode')
    if ec not in (0, None):
        print('    SUBSCRIBE FAILED: errcode =', ec, '|', resp.get('errmsg', ''))
        if ec == 853000:
            print('    -> Bot ID or Secret is wrong, or not saved.')
        await ws.close()
        return 1
    print('    subscribe OK (errcode=0)')
    print()
    line('=')
    print('  ALL CHECKS PASSED.')
    print()
    print('  Now LISTENING for 120 seconds.')
    print('  >>> Please send a message to the bot in WeCom NOW.')
    print('  >>> Example:  你好')
    line('=')
    print()

    try:
        end = time.time() + 120
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, end - time.time()))
            except asyncio.TimeoutError:
                break
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            cmd = frame.get('cmd')
            if cmd == 'aibot_msg_callback':
                body = frame.get('body', {})
                uid = (body.get('from') or {}).get('userid', '')
                content = (body.get('text') or {}).get('content', '')
                print()
                line('*')
                print('  MESSAGE RECEIVED!')
                print('    userid  :', uid)
                print('    content :', content)
                line('*')
                print()
                print('  >>> SUCCESS! Your message channel works.')
                print('  >>> Now run start.bat to use the full bot.')
                await ws.close()
                return 0
            elif cmd == 'aibot_event_callback':
                print('    event:', cmd, json.dumps(frame.get('body', {}), ensure_ascii=False)[:200])
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    print()
    line('=')
    print('  NO MESSAGE RECEIVED in 120 seconds.')
    line('=')
    print()
    print('  Common reasons:')
    print('   1. You did not send a message during the 120s window.')
    print('   2. You sent it to a DIFFERENT bot (check which one).')
    print('   3. The bot is not enabled/published in WeCom admin.')
    print('   4. This bot is not an "API mode + long connection" bot.')
    print()
    return 1


if __name__ == '__main__':
    sys.exit(main())
