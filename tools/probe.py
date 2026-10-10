# -*- coding: utf-8 -*-
"""
连通性探针：用真实 Bot ID / Secret 连接企业微信长连接，
验证「订阅是否成功 + 能否收到消息回调」。

用法（沙箱里跑，凭据从环境变量读）：
    WECOM_BOT_ID=xxx WECOM_BOT_SECRET=yyy python3 tools/probe.py 45

最后参数 = 监听秒数（默认 40 秒）。监听期间请在企微里给机器人发消息。
"""
import asyncio
import json
import os
import sys
import uuid

try:
    import websockets
except Exception:
    print('缺少 websockets 库：pip install websockets')
    sys.exit(1)

WS_URL = 'wss://openws.work.weixin.qq.com'


def rid(p='probe'):
    return f'{p}_{uuid.uuid4().hex[:16]}'


async def main():
    bot_id = os.environ.get('WECOM_BOT_ID', '')
    secret = os.environ.get('WECOM_BOT_SECRET', '')
    listen = int(sys.argv[1]) if len(sys.argv) > 1 else 40

    if not bot_id or not secret:
        print('缺少 WECOM_BOT_ID / WECOM_BOT_SECRET')
        sys.exit(2)

    print(f'机器人 ID：{bot_id}')
    print(f'目标：{WS_URL}')

    async with websockets.connect(WS_URL, ping_interval=None, open_timeout=20) as ws:
        print('[1/3] WebSocket 已连接 ✅')

        # --- 订阅 ---
        await ws.send(json.dumps({
            'cmd': 'aibot_subscribe',
            'headers': {'req_id': rid('sub')},
            'body': {'bot_id': bot_id, 'secret': secret},
        }))
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        print(f'[2/3] 订阅响应：{raw}')

        resp = json.loads(raw)
        errcode = resp.get('errcode', resp.get('body', {}).get('errcode'))
        errmsg = resp.get('errmsg', resp.get('body', {}).get('errmsg', ''))
        print(f'      errcode={errcode} errmsg={errmsg}')
        if errcode not in (0, None):
            print('❌ 订阅失败，请检查 Bot ID / Secret 是否正确，或机器人是否已发布。')
            return
        print('[3/3] 订阅成功 ✅  现在开始监听，请在企微里给机器人发一条消息…')

        # --- 监听 + 心跳 ---
        hb_task = None

        async def hb():
            while True:
                await asyncio.sleep(30)
                try:
                    await ws.send(json.dumps({'cmd': 'ping', 'headers': {'req_id': rid('ping')}}))
                    print('   [心跳] ping 已发送')
                except Exception as e:
                    print(f'   [心跳] 失败 {e}')
                    return

        hb_task = asyncio.create_task(hb())
        deadline = asyncio.get_event_loop().time() + listen
        try:
            while True:
                left = deadline - asyncio.get_event_loop().time()
                if left <= 0:
                    print(f'\n监听 {listen} 秒结束，未再收到新消息。')
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    print(f'\n监听 {listen} 秒结束，未收到消息。')
                    break
                print(f'\n📨 收到帧：{raw}')
                # 尝试回一条文本，验证发送通道
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                cmd = frame.get('cmd')
                rq = frame.get('headers', {}).get('req_id', '')
                body = frame.get('body', {})
                if cmd == 'aibot_msg_callback':
                    userid = (body.get('from') or {}).get('userid', '')
                    content = (body.get('text') or {}).get('content', '')
                    print(f'   -> 消息：userid={userid} content={content}')
                    reply = json.dumps({
                        'cmd': 'aibot_respond_msg',
                        'headers': {'req_id': rq},
                        'body': {'msgtype': 'text',
                                 'text': {'content': '✅ 连接测试成功！我是 IT 知识库机器人，收到你的消息了。\n直接问我问题试试，比如「浩辰CAD怎么激活」。'}},
                    }, ensure_ascii=False)
                    await ws.send(reply)
                    print('   -> 已回复测试消息 ✅')
                elif cmd == 'aibot_event_callback':
                    print(f'   -> 事件：{json.dumps(body, ensure_ascii=False)[:400]}')
        finally:
            if hb_task:
                hb_task.cancel()


if __name__ == '__main__':
    asyncio.run(main())
