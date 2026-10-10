# -*- coding: utf-8 -*-
"""
机器人主服务（长连接模式）。

三条业务线：
  A. 问答：员工提问 -> 命中知识库即答；未命中 -> 引导转人工。
  B. 维护（仅钟勇 userid=ZhongYong）：/add /edit /del 立即生效。
  C. 他人更新：提交 -> 生成待审单 -> 机器人把「审批卡片」主动推给钟勇 ->
     钟勇点【通过】/【驳回】按钮，逐条审批 -> 通过才落库。

运行：
  KB_MOCK=1 python -m app.main   # 沙箱本地模拟
  python -m app.main             # 真实长连接（读 .env）
"""
import asyncio
import os
import re

from .kb_engine import KBEngine, MAINTAINER_ID, MAINTAINER_NAME, LEARN_REMIND_AT
from .wecom_bot import WeComBot
from .commands import dispatch, HELP_TEXT, PENDING_UPDATE
from . import self_update as UPD
from .cards import (build_approval_card, build_result_card, parse_decision_key,
                    build_welcome_card, build_answer_card, parse_question_key)


def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'))

ENGINE = KBEngine()
BOT = None   # 在 main() 里赋值


async def push_to_maintainer(rec):
    """把审批卡片主动推给"该变更所属目录的负责人"（单聊）。

    - 归属目录的负责人是谁，就推给谁（钟勇拥有全部目录，自然是默认人选）
    - 重要：aibot_send_msg 是"主动推送"，企微要求用 **chatid**（会话 id），
      而不是 userid。单聊的 chatid 就是回调里的 body.chatid。
      优先用该负责人最近一次会话里拿到的 chatid；拿不到再退回 userid。
    """
    target_user = _owner_of_record(rec)
    dir_path = ENGINE.dir_path_target(rec)
    card = build_approval_card(rec, dir_path=dir_path, owner_disp=_disp(target_user))
    if os.environ.get('KB_MOCK', '1') == '1':
        print(f'\n>>> [推送审批卡片给 {_disp(target_user)}] PID={rec["pid"]} '
              f'目录={dir_path} '
              f'标题={card["main_title"]["title"]} 按钮={[b["text"] for b in card["button_list"]]}')
        return True

    target = _chatid_of(target_user)
    body = {'chatid': target, 'chat_type': 1,
            'msgtype': 'template_card', 'template_card': card}
    try:
        ok = await BOT._send('aibot_send_msg', f'push_{rec["pid"]}', body)
        if ok:
            print(f'[push] 审批卡片已推送 -> {_disp(target_user)} chatid={target}')
        else:
            # 推送失败不能让审批"看起来成功了"：退回文字通道再试一次
            print(f'[push] 卡片推送失败，改用文字提醒 -> {target}')
            ok = await BOT.send_to_user(
                target,
                f'📋 有一条知识库变更待审批（单号 {rec["pid"]}）。\n'
                f'输入「/pending」查看，回复「同意 {rec["pid"]}」即可通过。')
        return ok
    except Exception as e:
        print(f'[push] 推送异常：{e}')
        return False


def _disp(userid):
    """显示名：钟勇显示中文名，其他人显示 userid。"""
    return MAINTAINER_NAME if userid == MAINTAINER_ID else userid


def _owner_of_record(rec):
    """根据待审单的归属目录，判断该由谁审批。"""
    path = ENGINE.dir_path_target(rec)
    if path and path != '未分类':
        return ENGINE.owner_of(path)
    return MAINTAINER_ID


# 记录各用户会话时拿到的真实 chatid（回调里才有，主动推送必须用它）
_CHATIDS = {}


def remember_chatid(userid, chatid):
    """在收到某人消息时记下他的 chatid —— 主动推送靠它才能送达。"""
    if userid and chatid and _CHATIDS.get(userid) != chatid:
        _CHATIDS[userid] = chatid
        print(f'[push] 记录 {_disp(userid)} 的 chatid = {chatid}')


def _chatid_of(userid):
    return _CHATIDS.get(userid) or userid


def _maintainer_chatid():
    """兼容旧调用：返回钟勇的推送目标。"""
    return _chatid_of(MAINTAINER_ID)


def to_md(text):
    """把知识库答案转成好看的 markdown（企微只支持 markdown 回复）。

    知识库答案格式约定：
      标题：##步骤1##步骤2##注意：xxx
    - 开头的「XXX：」标题 -> 加粗
    - 其余 ## 分段 -> 换行
    - 数字序号行保持原样（markdown 自动成列表）
    - 转义 markdown 敏感字符，避免格式错乱
    """
    if not text:
        return ''
    parts = [p.strip() for p in str(text).split('##')]
    parts = [p for p in parts if p]
    if not parts:
        return ''
    out = []
    for i, p in enumerate(parts):
        # 首段若是"标题：说明"结构，标题加粗
        m = re.match(r'^([^：:]{2,20})[：:](.*)$', p)
        if i == 0 and m:
            head, tail = m.group(1), m.group(2).strip()
            out.append(f'**{head}**' + (f'：{tail}' if tail else ''))
        elif m and ('注意' in m.group(1) or '提示' in m.group(1) or '说明' in m.group(1)):
            head, tail = m.group(1), m.group(2).strip()
            out.append(f'**{head}**' + (f'：{tail}' if tail else ''))
        else:
            out.append(p)
    body = '\n'.join(out)
    # 转义可能干扰 markdown 的字符（保留 ** 加粗和换行）
    body = body.replace('*', '\\*') if body.count('*') != body.count('**') * 2 else body
    return body


async def on_message(userid, content, ctx):
    """所有消息的总入口"""
    text = (content or '').strip()
    text = re.sub(r'^@\S+\s*', '', text).strip()   # 去掉群聊 @机器人 前缀
    if not text:
        return None

    # 记下钟勇的 chatid —— 后续主动推审批卡片必须用它才能送达
    remember_chatid(userid, ctx.get('chatid', ''))

    # 热更新：若 kb.csv 被外部改过（手动编辑 / 覆盖安装），这里自动重载
    ENGINE.reload_if_changed()

    # 手动刷新（钟勇）：强制重新加载，改完文件不想等下次问答时用
    if text in ('/reload', '／reload', '重载', '刷新知识库') and userid == MAINTAINER_ID:
        ENGINE.load(force=True)
        return f'✅ 已重新加载知识库，当前共 {len(ENGINE.items)} 条。'

    # 备用通道：钟勇用文字回复「同意 <单号>」也能审批（按钮之外）
    m = re.match(r'^(同意|通过|1|驳回|拒绝|0)\s*([0-9a-f]{6,})$', text)
    if m:
        return handle_decision(userid, m.group(1), m.group(2))

    cmd_reply = dispatch(ENGINE, userid, text)
    if cmd_reply is not None:
        # 在线升级：dispatch 已登记待执行升级请求 -> 回执返回后延迟执行（两段式）
        if userid in PENDING_UPDATE:
            req = PENDING_UPDATE.pop(userid)
            asyncio.create_task(_run_self_update(req))
        # 普通员工"提交成功"时，dispatch 返回 (提示文本, 待审单) —— 顺带推审批卡片
        if isinstance(cmd_reply, tuple):
            reply_text, rec = cmd_reply
            approver = _disp(_owner_of_record(rec))
            pushed = await push_to_maintainer(rec)
            if pushed:
                reply_text += (f'\n（已把审批卡片推送给目录负责人 {approver}，'
                               f'他点「通过」即生效）')
            else:
                # 推送没成功就如实说，并给出自助兜底，避免"以为提交了其实没人知道"
                reply_text += (f'\n⚠️ 审批提醒暂时没送达 {approver}。\n'
                               f'建议直接联系他，或让他输入「/pending」查看这条。')
            return to_md(reply_text)
        return to_md(cmd_reply)

    item, score = ENGINE.match(text)
    if item:
        return to_md(item['标准答案'])

    # 未命中 -> 记账到「待学习清单」（自动学习的数据来源）
    rec, is_new = ENGINE.record_unmatched(text, userid)
    if is_new:
        n = ENGINE.unmatched_count()
        print(f'[学习] 新未命中问题已记录：{text!r}（待学习共 {n} 条）')
        # 攒够一定数量，顺手提醒一次维护人（不打扰员工）
        if LEARN_REMIND_AT and n > 0 and n % LEARN_REMIND_AT == 0:
            await notify_maintainer_new_questions(n)

    return ('抱歉，没有找到对应的答案。\n\n'
            '您可以：\n'
            '1. 换个说法再问一次；\n'
            f'2. 输入「人工」或「找{MAINTAINER_NAME}」转人工；\n'
            f'3. 若要补充这条知识，输入「提交」按提示操作（由对应目录的负责人审批）。')


async def _run_self_update(req):
    """两段式升级第二步：等回执发出去 -> 应用更新 -> 退出（由守护/计划任务拉起）。

    req 可含：
      plan   —— self_update.check() 的结果
      userid —— 手动升级时通知谁；自动升级可为 None（则通知维护人）
      quiet  —— True 表示这是自动升级，尽量少打扰

    只在 https/文件 IO 上耗时，所以放到线程里跑，别阻塞事件循环。
    """
    target = req.get('userid') or MAINTAINER_ID
    quiet = req.get('quiet', False)
    if not quiet:
        await asyncio.sleep(3)      # 手动升级：给回执留出发送时间
    else:
        await asyncio.sleep(1)
    plan = req['plan']
    print(f'[升级] 开始应用更新：{plan.get("local")} -> {plan.get("remote")}')
    ok, msg = await asyncio.to_thread(UPD.do_update, plan)
    print(f'[升级] {"成功" if ok else "失败"}：{msg}')
    if not ok:
        # 升级失败：主动告知，并继续正常服务（不退出）
        try:
            if BOT is not None:
                await BOT.send_to_user(_chatid_of(target),
                                       f'❌ {"自动" if quiet else ""}升级失败：{msg}\n'
                                       f'服务继续以旧版本运行。')
        except Exception:
            pass
        return

    # 成功：告知维护人，然后退出（退出码 42 表示"因升级而退出"）
    try:
        if BOT is not None:
            await BOT.send_to_user(
                _chatid_of(target),
                f'✅ {"自动" if quiet else ""}升级完成：'
                f'{plan.get("local")} → {plan.get("remote")}。'
                f'服务正在重启，约 10 秒后恢复。\n（{msg}）')
    except Exception:
        pass
    await asyncio.sleep(1)
    print('[升级] 退出主进程，等待守护/计划任务拉起新版本…')
    os._exit(42)


async def notify_maintainer_new_questions(n):
    """待学习清单攒够一批时，主动提醒维护人（失败不影响主流程）。"""
    try:
        if BOT is None:
            return
        text = (f'📚 待学习清单已有 **{n}** 条员工没问到答案的问题。\n\n'
                f'输入 `/learn` 查看并批量补充知识。')
        await BOT.send_to_user(_chatid_of(MAINTAINER_ID), text)
        print(f'[学习] 已提醒 {MAINTAINER_NAME}：待学习 {n} 条')
    except Exception as e:
        print(f'[学习] 提醒维护人失败（忽略）：{e}')


def _can_decide(userid, rec):
    """谁可以审批这条变更：钟勇 或 该变更归属目录的负责人。"""
    if userid == MAINTAINER_ID:
        return True
    return userid == _owner_of_record(rec)


def handle_decision(userid, verdict, pid):
    approve = verdict in ('同意', '通过', '1')
    rec = next((r for r in ENGINE.list_pending(False) if r['pid'] == pid), None)
    if not rec:
        return f'未找到单号 {pid}。'
    if not _can_decide(userid, rec):
        owner = _disp(_owner_of_record(rec))
        return (f'抱歉，这条变更属于目录「{ENGINE.dir_path_target(rec)}」，'
                f'审批权在 {owner}。')
    done, msg = ENGINE.decide(pid, approve, userid)
    if not done:
        return f'审批失败：{msg}'
    return _decision_text(done, approved=approve)


def _decision_text(rec, approved):
    act = {'add': '新增', 'edit': '编辑', 'delete': '删除'}.get(rec['action'], rec['action'])
    if approved:
        return f'已通过并生效。\n单号：{rec["pid"]}\n操作：{act}\n提交人：{rec["submitter"]}'
    return (f'已驳回。\n单号：{rec["pid"]}\n操作：{act}\n'
            f'提交人：{rec["submitter"]}\n原因：{rec.get("reason","")}')


async def on_enter_chat(userid, ctx):
    """员工当天首次进入会话 → 推「高频问题」菜单卡片（等效"输入框按钮"）"""
    print(f'[欢迎] {userid} 进入会话，推送高频问题卡片')
    return build_welcome_card()


async def on_card_event(userid, key, task_id, ctx):
    """处理卡片按钮点击：审批按钮 / 快捷问题按钮"""
    ENGINE.reload_if_changed()   # 热更新：卡片提问也走最新数据
    # --- 快捷问题：点击后直接回答案 ---
    q = parse_question_key(key)
    if q:
        item, score = ENGINE.match(q)
        if item:
            # 卡片正文是纯文本，去掉 markdown 加粗符号
            ans = item['标准答案'].replace('##', '\n').replace('**', '')
        else:
            ans = f'抱歉，暂时没有「{q}」的答案，请直接描述你的问题。'
        return build_answer_card(q, ans), None

    # --- 审批按钮 ---
    action, pid = parse_decision_key(key)
    if not pid:
        return None, '无效的卡片操作。'
    rec = next((r for r in ENGINE.list_pending(False) if r['pid'] == pid), None)
    if not rec:
        return None, f'未找到单号 {pid}。'
    if not _can_decide(userid, rec):
        owner = _disp(_owner_of_record(rec))
        return None, (f'抱歉，这条变更属于目录「{ENGINE.dir_path_target(rec)}」，'
                      f'审批权在 {owner}。')
    approve = (action == 'approve')
    done, msg = ENGINE.decide(pid, approve, userid)
    if not done:
        return None, f'审批失败：{msg}'
    return build_result_card(done, approve), None


async def main():
    global BOT
    mock = os.environ.get('KB_MOCK', '1') == '1'
    bot_id = os.environ.get('WECOM_BOT_ID', '')
    secret = os.environ.get('WECOM_BOT_SECRET', '')
    print(f'=== IT 知识库机器人 ===  维护人：{MAINTAINER_NAME}（{MAINTAINER_ID}）')
    print(f'模式：{"Mock(本地模拟)" if mock else "真实长连接"}  知识库条数：{len(ENGINE.items)}')
    print(f'版本：{UPD.local_version()}  升级源：{UPD.describe_source()}')
    if UPD.auto_enabled():
        print(f'自动升级：已开启（每 {UPD.auto_interval_sec() // 60} 分钟检查一次）')
    else:
        print('自动升级：未开启（可用 /update 手动升级）')
    print(HELP_TEXT)
    BOT = WeComBot(bot_id, secret, on_message, card_handler=on_card_event,
                   enter_handler=on_enter_chat, mock=mock)

    # 定时自动升级（零操作）：仅当开启才起后台任务
    if UPD.auto_enabled():
        asyncio.create_task(_auto_update_watch())

    await BOT.run()


async def _auto_update_watch():
    """自动升级后台任务：发现新版 -> 走 _run_self_update（quiet 模式）。"""
    async def _on_ready(plan):
        await _run_self_update({'plan': plan, 'userid': MAINTAINER_ID, 'quiet': True})
    await UPD.auto_loop(_on_ready)


if __name__ == '__main__':
    asyncio.run(main())
