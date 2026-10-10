# -*- coding: utf-8 -*-
"""
企业微信「智能机器人」长连接客户端。

- 真实模式：连 wss://openws.work.weixin.qq.com，发 aibot_subscribe 订阅，
  收 aibot_msg_callback / aibot_event_callback，
  用 aibot_respond_msg 回复，aibot_respond_update_msg 更新卡片。
- Mock 模式：不连企微，本地模拟消息回调，用于沙箱跑通与自测。

协议要点（依据企业微信开发者中心文档）：
  订阅:   {"cmd":"aibot_subscribe","headers":{"req_id":..},"body":{"bot_id":..,"secret":..}}
  收消息: aibot_msg_callback  body:{msgid,aibotid,chatid,chattype,from.userid,msgtype,text.content}
  收事件: aibot_event_callback(eventtype=template_card_event/enter_chat/...)
  回复:   aibot_respond_msg       body:{msgtype:"text"/"template_card", ...}
  更新卡片: aibot_respond_update_msg（透传事件 req_id + task_id）
  心跳:   每 30 秒 ping 一次
  频率:   每会话 30 条/分钟、1000 条/小时；单条 ≤2048 字节
"""
import asyncio
import json
import os
import uuid

try:
    import websockets
except Exception:
    websockets = None

WS_URL = 'wss://openws.work.weixin.qq.com'
HEARTBEAT_SEC = 30


def gen_req_id(prefix='req'):
    return f'{prefix}_{uuid.uuid4().hex[:16]}'


class WeComBot:
    def __init__(self, bot_id, secret, handler, card_handler=None,
                 enter_handler=None, mock=False):
        self.bot_id = bot_id
        self.secret = secret
        self.handler = handler            # async def handler(userid, content, ctx) -> str|dict|None
        self.card_handler = card_handler  # async card_handler(userid, key, task_id, ctx) -> (card|None, text|None)
        self.enter_handler = enter_handler  # async enter_handler(userid, ctx) -> dict|str|None（进入会话）
        self.mock = mock
        self.ws = None
        self._stop = False

    async def run(self):
        if self.mock:
            await self._run_mock()
        else:
            await self._run_real()

    # ---------- 真实长连接 ----------
    async def _run_real(self):
        if websockets is None:
            raise RuntimeError('缺少 websockets 库，请 pip install websockets')
        while not self._stop:
            try:
                async with websockets.connect(WS_URL, ping_interval=None) as ws:
                    self.ws = ws
                    await self._subscribe()
                    print('[bot] 已连接并订阅成功')
                    hb = asyncio.create_task(self._heartbeat())
                    try:
                        await self._recv_loop(ws)
                    finally:
                        hb.cancel()
            except Exception as e:
                print(f'[bot] 连接异常：{e}，5 秒后重连')
                await asyncio.sleep(5)

    async def _subscribe(self):
        await self.ws.send(json.dumps({
            'cmd': 'aibot_subscribe',
            'headers': {'req_id': gen_req_id('sub')},
            'body': {'bot_id': self.bot_id, 'secret': self.secret},
        }))
        resp = json.loads(await self.ws.recv())
        if resp.get('errcode') not in (0, None):
            raise RuntimeError(f'订阅失败：{resp}')

    async def _heartbeat(self):
        """每 30 秒 ping 一次"""
        while True:
            await asyncio.sleep(HEARTBEAT_SEC)
            try:
                await self.ws.send(json.dumps({'cmd': 'ping', 'headers': {'req_id': gen_req_id('ping')}}))
            except Exception:
                return

    async def _recv_loop(self, ws):
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            cmd = frame.get('cmd')
            # --- 诊断日志：任何回调都原样打印，便于排查"收不到消息" ---
            if cmd in ('aibot_msg_callback', 'aibot_event_callback'):
                print('\n' + '*' * 60)
                print(f'  [收到消息回调] cmd={cmd}')
                print(f'  {json.dumps(frame, ensure_ascii=False)[:900]}')
                print('*' * 60)
            if cmd == 'aibot_msg_callback':
                asyncio.create_task(self._handle_callback(frame))
            elif cmd == 'aibot_event_callback':
                asyncio.create_task(self._handle_event(frame))

    async def _handle_callback(self, frame):
        body = frame.get('body', {})
        userid = (body.get('from') or {}).get('userid', '')
        content = (body.get('text') or {}).get('content', '')
        print(f'[消息] userid={userid!r} content={content!r}')
        ctx = {
            'req_id': frame.get('headers', {}).get('req_id', ''),
            'chatid': body.get('chatid', ''),
            'chattype': body.get('chattype', 'single'),
            'msgid': body.get('msgid', ''),
            'response_url': body.get('response_url', ''),
        }
        try:
            reply = await self.handler(userid, content, ctx)
        except Exception as e:
            import traceback
            print(f'[错误] 处理消息异常：{e}')
            traceback.print_exc()
            reply = None
        if not reply:
            print('[回复] 无内容（handler 返回空）')
            return

        # 只用一条通道回复，避免"回复两次"：
        #   优先长连接 aibot_respond_msg（企微官方推荐、最可靠）；
        #   只有当长连接不可用（未连接 / 发送异常）时，才降级走 response_url。
        sent_ok = await self.reply(ctx['req_id'], reply)
        if sent_ok:
            print('[回复] 长连接 aibot_respond_msg 已发送')
            return

        print('[回复] 长连接不可用，降级走 response_url')
        if ctx.get('response_url'):
            ok = await self.reply_via_url(ctx['response_url'], reply)
            print('[回复] response_url HTTP 回复', '成功' if ok else '失败')
        else:
            print('[回复] 无 response_url 可降级，本次回复失败')

    async def _handle_event(self, frame):
        body = frame.get('body', {})
        req_id = frame.get('headers', {}).get('req_id', '')
        eventtype = body.get('eventtype') or body.get('event', {}).get('eventtype', '')
        userid = (body.get('from') or {}).get('userid', '')
        print(f'[事件] eventtype={eventtype!r} userid={userid!r}')

        # --- 进入会话：推送「高频问题」菜单卡片 ---
        if eventtype == 'enter_chat' and self.enter_handler:
            ctx = {'req_id': req_id, 'chatid': body.get('chatid', ''),
                   'chattype': body.get('chattype', 'single'), 'event': 'enter_chat'}
            try:
                content = await self.enter_handler(userid, ctx)
            except Exception as e:
                print(f'[错误] enter_handler 异常：{e}')
                content = None
            if content:
                # enter_chat 通常无 response_url，优先用长连接回复
                await self.reply(req_id, content)
                ok = bool(req_id)
                print('[欢迎] 已推送高频问题卡片' if ok else '[欢迎] 缺少 req_id')
            return

        # --- 模板卡片按钮点击 ---
        if eventtype == 'template_card_event' and self.card_handler:
            from .cards import parse_card_event
            key, task_id = parse_card_event(body)
            updated_card, reply_text = await self.card_handler(userid, key, task_id, {'req_id': req_id})
            if updated_card:
                await self.update_card(req_id, updated_card)
                print('[卡片] 已更新')
            elif reply_text:
                await self.reply(req_id, reply_text)
                print('[回复] 已发送')

    # ---------- 发送 ----------
    # 注意：企微智能机器人的回复**只支持 markdown 和 模板卡片**，
    #      用 msgtype=text 会报 errcode 40008 invalid message type。
    async def reply(self, req_id, content):
        """content: str -> markdown 文本；dict -> 模板卡片

        返回 True 表示已成功发出；False 表示长连接不可用（调用方可降级）。
        """
        if isinstance(content, dict):
            body = {'msgtype': 'template_card', 'template_card': content}
        else:
            body = {'msgtype': 'markdown', 'markdown': {'content': content}}
        return await self._send('aibot_respond_msg', req_id, body)

    async def update_card(self, req_id, template_card):
        return await self._send('aibot_respond_update_msg', req_id,
                                {'msgtype': 'template_card', 'template_card': template_card})

    async def send_to_user(self, chatid, content, chat_type=1):
        """主动给某人/某个会话推送消息（需对方先与机器人有过会话）。

        参数是 **chatid**（会话 id），不是 userid ——
        企微 aibot_send_msg 要求用回调里拿到的 body.chatid。
        content: str -> markdown；dict -> 模板卡片
        chat_type: 1 单聊，2 群聊
        """
        if isinstance(content, dict):
            body = {'chatid': chatid, 'chat_type': chat_type,
                    'msgtype': 'template_card', 'template_card': content}
        else:
            body = {'chatid': chatid, 'chat_type': chat_type,
                    'msgtype': 'markdown', 'markdown': {'content': content}}
        ok = await self._send('aibot_send_msg', gen_req_id('push'), body)
        if not ok:
            print(f'[推送] aibot_send_msg 发送失败 -> chatid={chatid}')
        return ok

    async def reply_via_url(self, response_url, content):
        """通过回调里的 response_url 用 HTTP 回复（长连接回复兜底）"""
        try:
            import urllib.request
            if isinstance(content, dict):
                payload = {'msgtype': 'template_card', 'template_card': content}
            else:
                payload = {'msgtype': 'markdown', 'markdown': {'content': content}}
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            req = urllib.request.Request(
                response_url, data=data,
                headers={'Content-Type': 'application/json'}, method='POST')
            loop = asyncio.get_event_loop()

            def _do():
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.read().decode('utf-8', 'ignore')
            resp = await loop.run_in_executor(None, _do)
            print(f'       response_url 返回：{resp[:200]}')
            # 判断是否真的成功
            try:
                j = json.loads(resp)
                return j.get('errcode', -1) == 0
            except Exception:
                return True
        except Exception as e:
            print(f'       response_url 异常：{e}')
            return False

    async def _send(self, cmd, req_id, body):
        """发送一帧。返回 True/False，不抛异常（便于上层降级）。"""
        if self.ws is None:
            print(f'[发送] 失败：长连接未建立（cmd={cmd}）')
            return False
        try:
            await self.ws.send(json.dumps({
                'cmd': cmd, 'headers': {'req_id': req_id}, 'body': body,
            }, ensure_ascii=False))
            return True
        except Exception as e:
            print(f'[发送] 失败：{e}（cmd={cmd}）')
            return False

    # ---------- Mock 模式 ----------
    async def _run_mock(self):
        print('[bot] Mock 模式启动。输入消息模拟用户提问；输入 :quit 退出。')
        print('     （模拟"钟勇"本人操作请输入：ZhongYong> 你的消息）')
        loop = asyncio.get_event_loop()
        while not self._stop:
            content = await loop.run_in_executor(None, input, '你> ')
            if content.strip() in (':quit', ':q'):
                self._stop = True
                break
            userid = 'mock_user'
            # 允许模拟指定用户： "ZhongYong> 同意 xxx"
            if '>' in content:
                maybe, rest = content.split('>', 1)
                if maybe.strip() and ' ' not in maybe.strip():
                    userid = maybe.strip()
                    content = rest.strip()
            ctx = {'req_id': gen_req_id('mock'), 'chatid': 'mock', 'chattype': 'single', 'msgid': 'm'}
            reply = await self.handler(userid, content, ctx)
            if isinstance(reply, dict):
                print(f'机器人> [卡片] {reply.get("main_title",{}).get("title","")} | '
                      f'按钮：{[b["text"] for b in reply.get("button_list",[])]}\n')
            elif reply:
                print(f'机器人> {reply}\n')
