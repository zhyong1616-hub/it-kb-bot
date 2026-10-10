# -*- coding: utf-8 -*-
"""
指令路由。所有以「/」或特定中文词开头的消息在这里处理。

维护人（钟勇）指令：
  /help              帮助
  /tree [目录]       查看三级目录树 / 某目录下的条目
  /owner [目录 人]   查看或设置目录负责人
  /list [关键词]     列出条目
  /stats             统计
  /add               （多步）新增 —— 直接生效
  /edit <关键词>     （多步）编辑 —— 直接生效
  /del <关键词>      （多步）删除 —— 直接生效
  /pending           查看待审单
  /pending 单号       查看待审单详情
  /ai                （自然语言）用一句话描述修改，AI 生成后确认生效
  改 / 帮我改 / 优化  同上（免打 /ai）

目录负责人指令：
  /tree [目录]       查看目录树 / 自己目录下的条目
  /add /edit /del    仅限自己负责目录，改动立即生效；跨目录会被拒绝

普通员工指令：
  /help              帮助
  /tree [目录]       查看知识库目录结构
  提交 / 我要补充     进入提交流程（需目录负责人审批）
  /status            查看自己提交的审批进度
"""
import re

from .kb_engine import MAINTAINER_ID, MAINTAINER_NAME, SUB_FIELD
from . import ai_assistant as AI
from . import importer as IMPORTER
from . import self_update as UPD

HELP_TEXT = f"""
【员工常用】
- 直接提问：例如「浩辰CAD怎么激活」，命中即自动回答
- 「我是谁」：核查你的企业微信身份
- 「人工」：转人工（{MAINTAINER_NAME}）
- 「提交」：补充/修改知识库（需目录负责人逐条审批）
- 「/status」：查询自己提交的审批进度
- 「/tree」：查看知识库目录结构
- 「/tree <目录>」：查看该目录下的条目，例如 /tree 软件/浩辰CAD许可配置
- 「/version」：查看机器人当前版本
- 「/help」：查看本说明

【维护人 {MAINTAINER_NAME} 专用】（改动立即生效）
- 「/ai <一句话修改要求>」：**自然语言改库**（推荐！例如「把火绒怎么安装的答案改成…」）
- 「/import <目录> <文件链接或路径>」：**批量导入**表格（.xlsx/.csv），确认后生效
- 「/update」：**在线升级程序**（拉取新版本并自动重启；`/update check` 只看不升）
- 「/tree」：查看知识库三级目录树（含条数、负责人）
- 「/tree <目录>」：列出某目录下的条目
- 「/owner」：查看各目录的负责人
- 「/owner <目录> <企微userid>」：指定/更换目录负责人
- 「/learn」：查看【待学习清单】—— 员工问了但答不上来的问题（按热度排序）
- 「/learn <编号>」：给该问题补答案（走审批，通过即入库并自动出列）
- 「/learn skip <编号>」：忽略某条；「/learn clear」清空清单
- 「/list [关键词]」：列出条目（带编号 id）
- 「/add」：新增一条（两步：问题 -> 答案）
- 「/edit <关键词或id>」：引导式编辑，选字段改
- 「/del <关键词或id>」：删除，需确认
- 「/pending」：查看别人提交的待审单
- 「/stats」：统计
- 「/reload」：手动重新加载 kb.csv（改了文件后立即生效；平时会自动检测）
- 或直接点我推送的「审批卡片」按钮逐条审批

【目录负责人】（仅能改动自己负责的目录）
- 「/tree」：查看目录树，「/tree <目录>」看自己目录下的条目
- 「/add」「/edit <关键词>」「/del <关键词>」：改动仅限自己目录，立即生效
- 「/import <自己目录> <文件链接/路径>」：**批量导入**表格到自己目录，确认后生效
- 跨目录的改动会被拒绝，并提示该目录的负责人
"""

# 问候语（纯寒暄，不含具体问题）
_GREETINGS = (
    '你好', '您好', 'hi', 'hello', '在吗', '在么', '在不在',
    '早上好', '中午好', '下午好', '晚上好', '哈喽', '嗨',
)

# "我是钟勇"这类身份声明：允许中间有空格/标点
_SELF_ID = re.compile(r'^(我(是|叫)|我是|我叫|this\s+is|i\s+am|i\'m)\s*[：:]?\s*(.+)$', re.I)

# 身份查询：用户想知道"你是谁/我是谁"
_WHO_QUERY = re.compile(
    r'^(我(是|叫)?谁|我(是|叫)哪[位个]|我(的)?身份|我(的)?账号|我(的)?(企微|企业微信)(号|id)?'
    r'|查(我|一下我)(的)?(身份|账号|信息)'
    r'|who\s*am\s*i|whoami|my\s*(id|name))[\s?？。！!]*$',
    re.I,
)

# 机器人自述：用户想知道"你是谁"
_BOT_QUERY = re.compile(
    r'^(你(是)?谁|你(是)哪[位个]|你(是)什么|你(是)?机器人吗'
    r'|who\s*are\s*you|what\s*are\s*you)[\s?？。！!]*$',
    re.I,
)

# 提问用的人称代词/疑问词，若 "我是X" 里的 X 命中这些，说明不是自报姓名
_NOT_A_NAME = {'谁', '哪位', '哪个', '什么', '啥', '谁啊', '哪位呀',
               '怎么', '如何', '怎么办', '是吗'} 


def _strip_punct(s):
    """去掉中英文标点与空白，便于宽松比对。"""
    return re.sub(r'[\s，。！？、,.!?;；:：\-—_]+', '', s or '')


def _is_greeting(t):
    """判断是否为纯问候（去掉标点后，整句只由问候词构成）。"""
    core = _strip_punct(t).lower()
    if not core:
        return False
    # 整句命中问候词，或"问候词+机器人/助手"之类的简单组合
    for g in _GREETINGS:
        g2 = _strip_punct(g).lower()
        if not g2:
            continue
        rest = core.replace(g2, '', 1)
        if rest in ('', '机器人', '助手', 'bot', '小助手'):
            return True
    return False


def _whoami_text(userid):
    """返回身份查询结果：核查企微身份 + 姓名 + 该身份能做什么。"""
    if _is_maintainer(userid):
        return (f'🔎 已核查你的企业微信身份：\n\n'
                f'- 姓名：**{MAINTAINER_NAME}**\n'
                f'- 账号 ID：`{userid}`\n'
                f'- 角色：**知识库维护人**\n\n'
                f'你可以：\n'
                f'- 直接改库：`/add`、`/edit 关键词`、`/del 关键词`（立即生效）\n'
                f'- 审批别人的提交：`/pending`，或点我推送的审批卡片\n'
                f'- `/stats` 看统计')
    return (f'🔎 已核查你的企业微信身份：\n\n'
            f'- 账号 ID：`{userid}`\n'
            f'- 角色：普通员工\n\n'
            f'你可以：\n'
            f'- 直接提问，命中知识库即自动回答\n'
            f'- 输入「提交」补充知识（需 {MAINTAINER_NAME} 逐条审批）\n'
            f'- 输入「人工」转人工')


def _bot_intro_text(userid):
    """机器人自述。"""
    return (f'🤖 我是 **IT 知识库助手**，负责解答公司的软件激活、ERP 系统、'
            f'电脑故障等问题。\n\n'
            f'你的身份是：{("维护人 " + MAINTAINER_NAME) if _is_maintainer(userid) else ("员工（" + userid + "）")}\n\n'
            f'直接描述问题即可，例如「浩辰CAD怎么激活」；'
            f'输入「我是谁」可以查你的身份。')


def _identity_reply(userid, t):
    """处理问候 / "我是谁" / "我是XXX" 身份声明。返回回复文本，未命中返回 None。"""
    if _is_greeting(t):
        if _is_maintainer(userid):
            return (f'👋 {MAINTAINER_NAME} 你好，已识别你的维护人身份，可以直接使用管理指令：\n'
                    '- `/stats` 统计　`/pending` 待审单\n'
                    '- `/add` 新增　`/edit 关键词` 编辑　`/del 关键词` 删除\n'
                    '- 别人提交的变更，我会推「审批卡片」给你，点按钮即可\n\n'
                    '也可以直接输入问题查询知识库。')
        return ('👋 你好！我是 IT 知识库助手，可以帮你解决软件激活、ERP 系统、'
                '电脑故障等问题。\n\n直接描述你遇到的问题即可，'
                '例如「浩辰CAD怎么激活」。')

    # "你是谁" -> 机器人自述
    if _BOT_QUERY.match(t):
        return _bot_intro_text(userid)

    # "我是谁" -> 核查身份（先于自报姓名判断，避免被 _SELF_ID 吃掉）
    if _WHO_QUERY.match(t):
        return _whoami_text(userid)

    m = _SELF_ID.match(t)
    if m:
        who = _strip_punct(m.group(3))
        if not who:
            return None
        # "我是谁/我是哪位/我是哪个" 是身份查询，不是自报姓名
        if who in _NOT_A_NAME:
            return _whoami_text(userid)
        # 报的名字和真实身份对得上 -> 确认；对不上 -> 温和指出
        if who == _strip_punct(MAINTAINER_NAME) or who.lower() == MAINTAINER_ID.lower():
            if _is_maintainer(userid):
                return (f'✅ 已确认身份：你是维护人 {MAINTAINER_NAME}（{MAINTAINER_ID}）。\n\n'
                        '你可以：\n'
                        '- 直接改库：`/add`、`/edit 关键词`、`/del 关键词`（立即生效）\n'
                        '- 审批别人的提交：`/pending` 查看，或点我推送的审批卡片\n'
                        '- `/stats` 看统计')
            # 声称是钟勇，但企微身份不是 —— 明确告知，避免误会
            return (f'⚠️ 你的企业微信身份是员工（{userid}），不是 {MAINTAINER_NAME}。\n\n'
                    f'知识库的修改与审批权限只属于 {MAINTAINER_NAME} 本人（账号 {MAINTAINER_ID}）。\n'
                    f'如果你想补充内容，可以输入「提交」，我会把你的变更转给 {MAINTAINER_NAME} 逐条审批。')
        # 报了别的名字 -> 认出他，按权限给引导
        if _is_maintainer(userid):
            return (f'✅ 已识别你的维护人身份：{MAINTAINER_NAME}（{MAINTAINER_ID}）。'
                    '可以直接使用管理指令，或直接提问查询知识库。')
        return (f'👋 收到，{who}。你的企业微信身份是 员工（{userid}）。\n\n'
                '直接描述你的问题即可，我会尽力解答；'
                f'需要补充知识可输入「提交」（需 {MAINTAINER_NAME} 审批）。')
    return None


# 多步会话状态：{userid: {'step':..., 'action':..., 'data':{}}}
SESSIONS = {}

# 在线升级：dispatch 只返回回执并在这里登记"待执行的升级请求"，
# 由 main.py 在把回执发出去之后再执行（两段式，确保用户先收到回执）。
PENDING_UPDATE = {}   # {'plan':..., 'userid':...}


def _is_maintainer(userid):
    return userid == MAINTAINER_ID


def _can_admin(engine, userid):
    """是否有维护权限：钟勇 或 任意目录的负责人。"""
    return _is_maintainer(userid) or bool(engine.dirs_of_user(userid))


# 维护人专属指令前缀 —— 普通员工敲这些时，要明确说"没权限"，
# 而不是当成普通提问去知识库找答案（否则用户会以为指令生效了）。
_ADMIN_CMDS = ('/add', '/edit', '/del', '/list', '/pending', '/stats', '/done', '/learn',
               '/owner', '/tree', '/reload', '/ai', '/import', '/update',
               '新增', '编辑', '删除', '学习', '负责人', '重载', '帮我改', '优化', '导入', '升级')


def _looks_like_admin_cmd(t):
    for c in _ADMIN_CMDS:
        if t == c or t.startswith(c + ' ') or t.startswith(c + '　'):
            return True
    return False


def dispatch(engine, userid, text):
    """返回 str 表示已处理；返回 None 表示交给知识库问答"""
    t = text.strip()

    # ---------- 通用：问候 / 身份声明 ----------
    ident = _identity_reply(userid, t)
    if ident is not None:
        return ident

    # ---------- 通用：目录树（所有人可看）----------
    if t in ('/tree', '／tree', '/目录', '目录', '目录树', '/tree树'):
        return _tree_text(engine)
    if t.startswith('/tree ') or t.startswith('目录 '):
        arg = t.replace('/tree', '', 1).replace('目录', '', 1).strip()
        return _tree_dir_text(engine, arg)

    if t in ('/help', '／help', '帮助', '怎么用', '我是谁', '/whoami'):
        me = f'{MAINTAINER_NAME}（{MAINTAINER_ID}）' if _is_maintainer(userid) \
            else (f'目录负责人（{userid}）' if engine.dirs_of_user(userid) else f'员工（{userid}）')
        return f'你的企业微信身份：{me}\n\n{HELP_TEXT.strip()}'
    if t in ('/version', '／version', '版本', '版本号'):
        return f'🤖 IT 知识库机器人\n{version_text(engine)}'
    if t in ('人工', f'找{MAINTAINER_NAME}', '转人工', '找IT', '技术支持'):
        return f'已为您转接 {MAINTAINER_NAME}，请稍候。您也可以先描述具体问题，我会尽力解答。'

    # ---------- 维护人：负责人管理（/owner）----------
    if t in ('/owner', '负责人') and _is_maintainer(userid):
        return _owner_list_text(engine)
    if t.startswith('/owner ') and _is_maintainer(userid):
        arg = t[7:].strip()
        return _owner_set_text(engine, arg)

    # ---------- 目录负责人 / 维护人：目录内维护 ----------
    if _can_admin(engine, userid):
        if t in ('/stats',) and _is_maintainer(userid):
            s = engine.stats()
            lines = [f'知识库共 {s["total"]} 条，待审 {s["pending"]} 条，待学习 {s.get("unmatched", 0)} 条。']
            for k, v in s['by_cat'].items():
                lines.append(f'  {k}：{v} 条')
            return '\n'.join(lines)

        # ---- 自动学习：待学习清单（仅钟勇，全库视角）----
        if (t.startswith('/learn') or t.startswith('学习')) and _is_maintainer(userid):
            arg = t.replace('/learn', '', 1).replace('学习', '', 1).strip()
            # 无参数 -> 列出清单
            if not arg:
                return _learn_list(engine)
            if arg in ('clear', '清空'):
                n = engine.unmatched_count()
                for r in engine.list_unmatched(limit=0):
                    engine.remove_unmatched(r['key'])
                return f'已清空待学习清单（{n} 条）。'
            low = arg.lower()
            if low.startswith('skip') or arg.startswith('忽略'):
                k = arg.replace('skip', '', 1).replace('忽略', '', 1).strip()
                rec = engine.ignore_unmatched(k)
                if not rec:
                    return f'没找到编号/问题「{k}」。'
                return f'已忽略：「{rec["query"]}」（不再出现在清单里）'
            # 数字或文本 -> 进入"给答案"流程
            return _start_learn(engine, userid, arg)

        if t.startswith('/list'):
            kw = t[5:].strip()
            pool = _visible_items(engine, userid)
            hits = [it for it in pool if kw in ''.join(it.values())] if kw else pool
            total = len(hits)
            hits = hits[:20]
            if not hits:
                return '没有匹配条目。'
            scope = '' if _is_maintainer(userid) else f'（范围：{("/".join(engine.dirs_of_user(userid)))}）'
            lines = [f'共 {total} 条，显示前 {len(hits)} 条{scope}：'
                     if total > len(hits) else f'共 {len(hits)} 条{scope}：']
            lines += [f'{i+1}. `{it["id"]}` [{engine.dir_path(it)}] {it["标准问题"]}'
                      for i, it in enumerate(hits)]
            lines.append('\n用 `/edit <id前几位>` 或 `/del <id前几位>` 直接操作该条。')
            return '\n'.join(lines)
        if t.startswith('/pending') and _is_maintainer(userid):
            pid = t[8:].strip()
            if pid:
                rec = next((r for r in engine.list_pending(False) if r['pid'] == pid), None)
                if not rec:
                    return f'未找到单号 {pid}'
                return (f'单号 {rec["pid"]}（{rec["status"]}）\n'
                        f'操作：{rec["action"]}\n提交人：{rec["submitter"]}\n'
                        f'内容：{rec["payload"]}')
            ps = engine.list_pending()
            if not ps:
                return '当前没有待审的变更。'
            return '\n'.join(
                f'{i+1}. {r["pid"]} | {r["action"]} | 提交人 {r["submitter"]} | {_brief(r)}'
                for i, r in enumerate(ps))
        if t in ('/add', '新增'):
            # 目录负责人新增时，先指定归入哪个目录
            if _is_maintainer(userid):
                SESSIONS[userid] = {'step': 'add_q', 'action': 'add', 'data': {}}
                return '【新增】请输入「标准问题」（输入 :cancel 取消）'
            SESSIONS[userid] = {'step': 'add_dir', 'action': 'add', 'data': {}}
            return _add_dir_prompt(engine, userid)

        # ---- AI 自然语言改库 ----
        if t == '/ai' or t in ('ai', 'AI'):
            if not AI.ai_ready():
                return _ai_not_ready_text()
            SESSIONS[userid] = {'step': 'ai_input', 'action': 'ai', 'data': {}}
            return ('🤖 【AI 改库】用一句话告诉我要改什么，例如：\n'
                    '· 把「火绒怎么安装」的答案改成：先访问 sf.cckide.com:6080 下载…\n'
                    '· 新增一条：ERP 打不开怎么办 → 先检查网络，再联系钟勇\n'
                    '· 给「浩辰CAD怎么激活」加一个相似问法：浩辰怎么授权\n\n'
                    '（:cancel 取消）')
        if t.startswith('/ai ') or t.startswith('帮我改 ') or t.startswith('改 ') or t.startswith('优化 '):
            arg = t.split(' ', 1)[1].strip()
            return _ai_handle(engine, userid, arg)

        # ---- 批量导入（表格 -> 条目，确认后生效）----
        if t == '/import' or t == '导入':
            SESSIONS[userid] = {'step': 'import_input', 'action': 'import', 'data': {}}
            scope = '全库任意目录' if _is_maintainer(userid) else \
                f'你负责的目录（{"/".join(engine.dirs_of_user(userid))}）'
            return (f'📥 【批量导入】把表格里的多条知识一次性导入知识库。\n\n'
                    f'格式：CSV 或 Excel（.xlsx），表头需含「标准问题」「标准答案」两列，\n'
                    f'可选：类别 / 分组 / 三级目录 / 相似问法 / 触发关键词 / 升级路径。\n\n'
                    f'你的可导入范围：{scope}\n\n'
                    f'请把「文件链接」或「服务器上的文件路径」发给我，例如：\n'
                    f'· https://你的网盘/浩辰新增.xlsx\n'
                    f'· D:\\\\IT知识库\\\\导入\\\\浩辰新增.xlsx\n\n'
                    f'（我会先解析出清单给你确认，确认后才入库；:cancel 取消）')
        if t.startswith('/import ') or t.startswith('导入 '):
            arg = t.split(' ', 1)[1].strip()
            return _import_handle(engine, userid, arg)

        # ---- 在线升级（仅钟勇）：/update [check] ----
        if t in ('/update', '/升级', '升级') or t.startswith('/update ') or t.startswith('升级 '):
            arg = t.replace('/update', '', 1).replace('/升级', '', 1).replace('升级', '', 1).strip()
            if not _is_maintainer(userid):
                return f'⚠️ 只有 {MAINTAINER_NAME} 可以升级程序。'
            if arg in ('check', '检查', '?', '/check'):
                plan = UPD.check()
                if not plan['ok']:
                    return (f'🔍 升级检查失败：{plan["msg"]}\n'
                            f'当前版本：{plan["local"]}\n来源：{plan["source"]}')
                flag = '有新版本可用 ✅' if plan['newer'] else '已是最新 ✅'
                return (f'🔍 升级检查\n· 当前版本：{plan["local"]}\n'
                        f'· 源上版本：{plan["remote"]}\n· 来源：{plan["source"]}\n'
                        f'· 结论：{flag}\n\n'
                        f'（回复 `/update` 执行升级）')
            # 真正升级：两段式 —— 先回执，再延迟执行，最后退出让守护拉起
            return _update_handle(engine, userid)

        if t.startswith('/edit') or t.startswith('编辑'):
            kw = t.replace('/edit', '', 1).replace('编辑', '', 1).strip()
            if not kw:
                SESSIONS[userid] = {'step': 'edit_find', 'action': 'edit', 'data': {}}
                return '【编辑】请输入要编辑的条目的关键词'
            return _start_edit(engine, userid, kw)
        if t.startswith('/del') or t.startswith('删除'):
            kw = t.replace('/del', '', 1).replace('删除', '', 1).strip()
            if not kw:
                SESSIONS[userid] = {'step': 'del_find', 'action': 'delete', 'data': {}}
                return '【删除】请输入要删除的条目的关键词'
            return _start_delete(engine, userid, kw)

    # ---------- 普通员工误用维护指令：明确拒绝（不要静默变成"查答案"）----------
    if _looks_like_admin_cmd(t):
        return (f'⚠️ 你（{userid}）没有该操作的权限。\n\n'
                f'知识库按目录分配给不同负责人维护：\n'
                f'- 查看目录结构：输入「/tree」\n'
                f'- 补充或修正内容：输入「提交」，我会把你的变更转给对应目录的负责人逐条审批。')

    # ---------- 普通员工的提交流程 ----------
    if t in ('提交', '我要补充', '补充知识', '/submit'):
        SESSIONS[userid] = {'step': 'sub_dir', 'action': 'add', 'data': {}}
        return (f'【提交变更】请依次回答，最后由对应目录的负责人逐条审批。\n'
                f'第 1 步：这条知识属于哪个目录？（可输入 `/tree` 查看）\n'
                f'  例如：软件/浩辰CAD许可配置/激活授权\n'
                f'  不清楚就直接回复「不知道」，我会交给 {MAINTAINER_NAME} 分诊。')

    if t == '/status':
        mine = [r for r in engine.list_pending(False) if r['submitter'] == userid]
        if not mine:
            return '您还没有提交过变更。'
        return '\n'.join(f'{r["pid"]} | {r["action"]} | {r["status"]} | {_brief(r)}' for r in mine)
    if t.startswith('/status'):
        pid = t[7:].strip()
        rec = next((r for r in engine.list_pending(False) if r['pid'] == pid), None)
        if not rec:
            return f'未找到单号 {pid}'
        return f'{rec["pid"]} | {rec["action"]} | {rec["status"]} | {rec.get("reason","")}'

    # ---------- 多步会话继续 ----------
    if userid in SESSIONS:
        return _continue_session(engine, userid, t)

    return None


def _brief(rec):
    p = rec['payload']
    return (p.get('标准问题') or p.get('qid') or '')[:30]


# ================= AI 自然语言改库 =================

def _ai_not_ready_text():
    return ('🤖 AI 改库功能还没启用。\n\n'
            '在服务目录的 `.env` 里填好下面几项，重启一次即可：\n'
            '```\n'
            'AI_ENABLED=1\n'
            'AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1\n'
            'AI_API_KEY=sk-你的key\n'
            'AI_MODEL=qwen-plus\n'
            '```\n'
            '（任何 OpenAI 兼容服务都行；没配 AI 时，仍可用 `/add` `/edit` `/del` 手动改库）')


def _ai_handle(engine, userid, text):
    """处理一句自然语言修改要求：解析 -> 生成预览 -> 等确认。"""
    if not AI.ai_ready():
        return _ai_not_ready_text()
    res = AI.parse_intent(text, engine)
    if not res.get('ok'):
        return (f"🤖 没太理解你的意思：{res.get('error','')}\n\n"
                f"换个说法再试，例如：\n"
                f"· 把「火绒怎么安装」的答案改成：先访问 sf.cckide.com:6080 下载…\n"
                f"· 新增一条：XXX 怎么办 → 步骤…")
    action = res['action']
    payload = res.get('payload') or {}

    # delete：找到条目后确认
    if action == 'delete':
        item = engine._find(res['match']) or engine.match(res['match'] or payload.get('标准问题', ''))[0]
        if not item:
            return f"🤖 没找到要删除的条目（关键词：{res.get('match')}）。"
        if not engine.can_edit(userid, item):
            return (f"⛔ 条目「{item['标准问题']}」属于目录「{engine.dir_path(item)}」，"
                    f"你无权删除。")
        SESSIONS[userid] = {'step': 'ai_confirm_del', 'action': 'ai',
                            'data': {'qid': item['id'], '标准问题': item['标准问题']}}
        return (f"🤖 确认删除这条知识？\n\n"
                f"· 问题：{item['标准问题']}\n"
                f"· 目录：{engine.dir_path(item)}\n\n"
                f"回复「是」删除，「否」取消。")

    # edit：定位条目
    if action == 'edit':
        item = engine._find(res['match']) or engine.match(res['match'] or '')[0]
        if not item:
            return (f"🤖 没定位到要修改的条目（关键词：{res.get('match')}）。\n"
                    f"可以先用 `/list 关键词` 看看，再用 `/ai` 重说一次。")
        if not engine.can_edit(userid, item):
            owner = engine.owner_name_of(engine.dir_path(item))
            return (f"⛔ 条目「{item['标准问题']}」属于目录「{engine.dir_path(item)}」，"
                    f"负责人是 {owner}，你无权修改。")
        changes = {k: v for k, v in payload.items() if v}
        if not changes:
            return '🤖 我没提取到要修改的内容，请说得更具体些。'
        preview = '\n'.join(f'  · {k}：{v}' for k, v in changes.items())
        SESSIONS[userid] = {'step': 'ai_confirm_edit', 'action': 'ai',
                            'data': {'qid': item['id'], 'changes': changes,
                                     '标准问题': item['标准问题']}}
        return (f"🤖 我理解你要【修改】「{item['标准问题']}」\n\n"
                f"将要改成：\n{preview}\n\n"
                f"回复「是/确认」立即生效，「否/取消」放弃。\n"
                f"（如只想追加、不覆盖，回复「追加」）")

    # add：新增
    if action == 'add':
        q = payload.get('标准问题', '').strip()
        a = payload.get('标准答案', '').strip()
        if not q or not a:
            return ('🤖 新增需要「问题」和「答案」两部分。\n'
                    '例如：新增一条：打印机连不上怎么办 → 先检查网线，再联系钟勇')
        # 目录负责人只能加到自己目录
        path = '/'.join([x for x in [payload.get('类别', ''), payload.get('分组', ''),
                                     payload.get('三级目录', '')] if x])
        if not _is_maintainer(userid):
            if not path or not engine.can_edit(userid, path):
                return (f"🤖 这条缺少归属目录（或不在你负责范围内）。\n"
                        f"你可以：用 `/add` 手动指定目录，或在描述里写明目录路径。")
        SESSIONS[userid] = {'step': 'ai_confirm_add', 'action': 'ai',
                            'data': {'payload': payload}}
        loc = f'\n· 目录：{path}' if path else '\n· 目录：（未指定，归入未分类）'
        return (f"🤖 我理解你要【新增】一条知识\n\n"
                f"· 问题：{q}\n· 答案：{a}{loc}\n\n"
                f"回复「是/确认」立即生效，「否/取消」放弃。")

    return '🤖 暂时无法处理这个请求。'


# ================= 批量导入 =================

def _import_handle(engine, userid, src):
    """解析文件 -> 与现有库比对 -> 生成确认清单（不落库）。

    src 允许两种形式：
      「<目录> <链接/路径>」  —— 显式指定这批知识归入哪个目录
      「<链接/路径>」         —— 目录取自表格内的「类别/分组/三级目录」列
    """
    if not _can_admin(engine, userid):
        return (f'⚠️ 你（{userid}）没有批量导入的权限。\n'
                f'如需补充内容，输入「提交」走审批，或联系 {MAINTAINER_NAME}。')

    src = (src or '').strip()
    if not src:
        return '📥 请把文件链接或路径发给我（或输入 /import 查看格式说明）。'

    # 是否显式指定了目录：以"以 /  分隔且看起来像目录"的第一段判断
    forced_dir, file_src = _split_dir_and_src(src)

    # 目录权限校验（指定了目录时）
    if forced_dir and not engine.can_edit(userid, forced_dir):
        owner = engine.owner_name_of(forced_dir)
        return (f'⛔ 目录「{forced_dir}」的负责人是 {owner}，你无权向该目录导入。\n'
                f'你可以导入到：{"/".join(engine.dirs_of_user(userid)) or "（无）"}')

    records, errors, tmp = IMPORTER.load_source(file_src)
    try:
        if not records and errors:
            return '📥 导入失败：\n- ' + '\n- '.join(errors[:8])
        if not records:
            return '📥 文件里没有解析到有效条目（需含「标准问题」「标准答案」两列，且都有内容）。'

        # 应用指定目录（表格里没写目录时用它的）
        if forced_dir:
            parts = forced_dir.split('/')
            for r in records:
                if not r.get('类别'):
                    r['类别'] = parts[0] if len(parts) > 0 else ''
                if not r.get('分组'):
                    r['分组'] = parts[1] if len(parts) > 1 else ''
                if not r.get('三级目录'):
                    r['三级目录'] = parts[2] if len(parts) > 2 else ''

        # 权限：逐条校验归属目录
        denied = []
        for r in records:
            path = '/'.join([x for x in [r.get('类别', ''), r.get('分组', ''),
                                         r.get('三级目录', '')] if x])
            if not _is_maintainer(userid) and (not path or not engine.can_edit(userid, path)):
                denied.append(r.get('标准问题', ''))
        if denied:
            head = '\n'.join(f'  · {x}' for x in denied[:5])
            more = f'\n  …还有 {len(denied) - 5} 条' if len(denied) > 5 else ''
            return (f'⛔ 有 {len(denied)} 条不在你负责的目录内，已中止导入：\n{head}{more}\n\n'
                    f'请修正表格里的「类别/分组/三级目录」列，或用 '
                    f'`/import <你的目录> <链接>` 指定归入目录。')

        plan = engine.bulk_plan(records)
        total = len(plan['add']) + len(plan['edit']) + len(plan['same']) + len(plan['conflict'])

        # 存进会话，等用户确认
        SESSIONS[userid] = {'step': 'import_confirm', 'action': 'import',
                            'data': {'plan': plan, 'forced_dir': forced_dir}}

        lines = [f'📥 解析完成，共 {total} 条：',
                 f'  · 新增 {len(plan["add"])} 条',
                 f'  · 修改 {len(plan["edit"])} 条',
                 f'  · 无变化 {len(plan["same"])} 条',
                 f'  · 文件内重复 {len(plan["conflict"])} 条（已忽略）', '']
        if plan['add']:
            lines.append('【新增】')
            for r in plan['add'][:8]:
                loc = '/'.join([x for x in [r.get('类别', ''), r.get('分组', ''),
                                            r.get('三级目录', '')] if x]) or '未分类'
                lines.append(f'  + [{loc}] {r["标准问题"]}')
            if len(plan['add']) > 8:
                lines.append(f'  …还有 {len(plan["add"]) - 8} 条')
        if plan['edit']:
            lines.append('【修改】')
            for e in plan['edit'][:8]:
                fs = '、'.join(e['changes'].keys())
                lines.append(f'  ~ {e["问题"]}（改动：{fs}）')
            if len(plan['edit']) > 8:
                lines.append(f'  …还有 {len(plan["edit"]) - 8} 条')
        if errors:
            lines.append('')
            lines.append(f'（另有 {len(errors)} 行被跳过：{errors[0]}）')
        lines += ['', '确认导入？回复「是」执行，「否」取消。']
        return '\n'.join(lines)
    finally:
        IMPORTER.cleanup(tmp)


def _split_dir_and_src(src):
    """把 '/import 软件/浩辰CAD https://...xlsx' 拆成 (目录, 来源)。

    规则：如果第一个空格前的片段不含 '.'（不像文件名/域名），且后段存在，
    则把首段当目录；否则整串都是来源。
    """
    src = src.strip()
    # 链接/路径整体优先（无空格则无歧义）
    if ' ' not in src:
        return '', src
    first, rest = src.split(' ', 1)
    rest = rest.strip()
    looks_like_dir = ('/' in first or '\\' in first) and '.' not in first
    if looks_like_dir and rest:
        return first.replace('\\', '/').strip('/'), rest
    return '', src


# ================= 在线升级 =================

def _update_handle(engine, userid):
    """两段式升级：这里只"检查 + 登记 + 返回回执"，
    真正的执行与退出由 main.py 在回执发出后完成。"""
    plan = UPD.check()
    if not plan['ok']:
        return (f'❌ 升级失败：{plan["msg"]}\n\n'
                f'当前版本：{plan["local"]}\n'
                f'来源：{plan["source"]}\n\n'
                f'提示：\n'
                f'· 确认 `.env` 里配了 KB_UPDATE_URL / KB_UPDATE_GIT / KB_UPDATE_DIR\n'
                f'· 或直接把新包放进 F:\\KIDE_BOT_update，再发一次 `/update`\n'
                f'· 也可用 `/update check` 只看不改')
    if not plan['newer']:
        return (f'✅ 当前已是最新版本（{plan["local"]}），无需升级。\n'
                f'来源：{plan["source"]}')
    PENDING_UPDATE[userid] = {'plan': plan, 'userid': userid}
    return (f'🚀 发现新版本 {plan["remote"]}（当前 {plan["local"]}），开始升级…\n\n'
            f'来源：{plan["source"]}\n'
            f'约 5 秒后服务会重启，**重启期间机器人短暂离线**，属正常现象。\n'
            f'重启后请回一句「版本」或直接提问，确认服务已恢复。')


def version_text(engine):
    """机器人在线状态回复：当前版本 + 上次升级结果。"""
    v = UPD.local_version()
    lines = [f'当前版本：{v}',
             f'升级来源：{UPD.describe_source()}']
    res = UPD.pop_result()
    if res:
        lines.append(f'（上次升级：{res.get("from","?")} → {res.get("to","?")}，'
                     f'{res.get("ts","")}，覆盖 {res.get("files","?")} 项）')
    return '\n'.join(lines)



# ================= 目录视图 =================

def _visible_items(engine, userid):
    """某人可见/可维护的条目范围：钟勇=全部；目录负责人=自己负责目录并集。"""
    if _is_maintainer(userid):
        return list(engine.items)
    dirs = engine.dirs_of_user(userid)
    if not dirs:
        return []
    out, seen = [], set()
    for d in dirs:
        for it in engine.items_in_dir(d):
            if id(it) not in seen:
                seen.add(id(it))
                out.append(it)
    return out


def _tree_text(engine):
    """渲染三级目录树（含条目数、负责人）。"""
    tree = engine.build_tree()
    if not tree:
        return '知识库还是空的。'

    def owner_tag(path):
        o = engine.owner_of(path)
        if o == MAINTAINER_ID:
            return f'👑{MAINTAINER_NAME}'
        return f'👤{o}'

    lines = [f'📚 知识库目录结构（共 {sum(r["total"] for r in tree)} 条）', '']
    for cat in tree:
        lines.append(f'■ {cat["name"]}（{cat["total"]} 条）　负责人 {owner_tag(cat["path"])}')
        for grp in cat['children']:
            lines.append(f'  ├ {grp["name"]}（{grp["total"]} 条）　'
                         f'负责人 {owner_tag(grp["path"])}')
            for sub in grp['children']:
                lines.append(f'  │   · {sub["name"]}（{sub["count"]} 条）　'
                             f'负责人 {owner_tag(sub["path"])}')
    lines += ['',
              '查看某目录下的条目：输入 `/tree <目录路径>`',
              '例如：`/tree 软件/浩辰CAD许可配置`']
    return '\n'.join(lines)


def _tree_dir_text(engine, arg):
    """列出某目录下的条目。"""
    path = engine.norm_path(arg)
    if not path:
        return f'没看懂目录「{arg}」。输入 `/tree` 查看完整目录结构。'
    items = engine.items_in_dir(path)
    if not items:
        return (f'目录「{path}」下暂无条目。\n'
                f'（或用 `/tree` 查看正确的目录全路径）')
    owner = engine.owner_of(path)
    owner_disp = MAINTAINER_NAME if owner == MAINTAINER_ID else owner
    lines = [f'📂 {path}　共 {len(items)} 条　负责人：{owner_disp}', '']
    for i, it in enumerate(items, 1):
        sub = it.get(SUB_FIELD) or ''
        tag = f'[{sub}] ' if sub else ''
        lines.append(f'{i}. `{it["id"]}` {tag}{it["标准问题"]}')
    lines.append('\n用 `/edit <id前几位>` 或 `/del <id前几位>` 操作条目（仅限你有权限的目录）。')
    return '\n'.join(lines)


def _owner_list_text(engine):
    """列出各目录的负责人。"""
    owners = engine.load_owners()
    cats = engine.build_tree()
    lines = [f'👥 目录负责人（钟勇 {MAINTAINER_ID} 拥有全部权限）', '']
    for cat in cats:
        lines.append(f'■ {cat["name"]}')
        for grp in cat['children']:
            if grp['path'] in owners:
                o = owners[grp['path']]
                lines.append(f'  ├ {grp["name"]} -> {MAINTAINER_NAME if o == MAINTAINER_ID else o}')
            else:
                lines.append(f'  ├ {grp["name"]} -> （继承上级）')
            for sub in grp['children']:
                if sub['path'] in owners:
                    o = owners[sub['path']]
                    lines.append(f'  │   · {sub["name"]} -> '
                                 f'{MAINTAINER_NAME if o == MAINTAINER_ID else o}')
    lines += ['',
              '设置/更换负责人：`/owner <目录全路径> <企微userid>`',
              '例如：`/owner 软件/浩辰CAD许可配置/激活授权 ZhZhangSan`']
    return '\n'.join(lines)


def _owner_set_text(engine, arg):
    """解析 `/owner <目录> <userid>` 并设置。"""
    parts = arg.rsplit(None, 1)
    if len(parts) != 2:
        return ('格式：`/owner <目录全路径> <企微userid>`\n'
                '例如：`/owner 软件/浩辰CAD许可配置/激活授权 ZhZhangSan`\n'
                '（目录路径可用 `/tree` 查看）')
    path, uid = parts[0].strip(), parts[1].strip()
    ok, msg = engine.set_owner(path, uid)
    if not ok:
        return f'❌ {msg}'
    return f'✅ {msg}'


def _add_dir_prompt(engine, userid):
    """目录负责人新增条目时，先让其指明归入哪个目录。"""
    dirs = engine.dirs_of_user(userid)
    lines = ['【新增】请先选择这条知识归入哪个目录，直接回复目录路径：', '']
    for d in dirs:
        n = len(engine.items_in_dir(d))
        lines.append(f'- {d}（现有 {n} 条）')
    lines += ['', '例如回复：软件/浩辰CAD许可配置/激活授权',
              '（输入 :cancel 取消）']
    return '\n'.join(lines)


def _learn_list(engine):
    """展示待学习清单（按提问次数降序，越多人问越靠前）。"""
    items = engine.list_unmatched(limit=0)
    if not items:
        return ('📚 待学习清单是空的。\n\n'
                '当员工问了知识库答不上来的问题，我会**自动记下来**（同一问题重复问会累计次数）。\n'
                '你随时输入 `/learn` 就能看到这份清单。')
    lines = [f'📚 待学习清单（共 {len(items)} 条，按提问次数排序）：', '']
    for i, r in enumerate(items[:15], 1):
        n = r.get('count', 1)
        who = len(r.get('askers') or [])
        lines.append(f'{i}. {r["query"]}　（问过 {n} 次 / {who} 人）')
    if len(items) > 15:
        lines.append(f'…还有 {len(items) - 15} 条')
    lines += ['',
              '回复 `/learn <编号>` 给这条补答案（走审批，通过即入库）',
              '回复 `/learn skip <编号>` 忽略这条',
              '回复 `/learn clear` 清空清单']
    return '\n'.join(lines)


def _start_learn(engine, userid, arg):
    """进入"给未命中问题补答案"流程。"""
    rec = engine.get_unmatched(arg)
    if not rec:
        return (f'没找到编号/问题「{arg}」。输入 `/learn` 查看清单。\n'
                '若是要新增知识库里完全没有的问题，直接用 `/add`。')
    SESSIONS[userid] = {'step': 'learn_a', 'action': 'learn',
                        'data': {'标准问题': rec['query'], 'learn_key': rec['key']}}
    return (f'📝 为这个问题补充答案：\n\n「{rec["query"]}」（被问过 {rec.get("count", 1)} 次）\n\n'
            f'请输入【标准答案】（多条之间用 ## 分行；:cancel 取消）\n'
            f'提示：答案里尽量写清步骤，必要时注明「联系{MAINTAINER_NAME}」。')


def _qid_match(engine, kw):
    """按 id 前缀精确找条目（用于 /edit 2 这种"按编号"定位）。"""
    kw = (kw or '').strip().lstrip('#')
    if not kw:
        return None
    for it in engine.items:
        if it['id'].startswith(kw):
            return it
    return None


def _start_edit(engine, userid, kw):
    """开始编辑：支持 关键词 / 编号(id前缀)。展示当前内容 + 字段菜单。"""
    item = _qid_match(engine, kw) or engine.match(kw)[0]
    if not item:
        return f'没找到与「{kw}」匹配的条目，请换个关键词，或用 /list 查看编号。'
    if not engine.can_edit(userid, item):
        owner = engine.owner_name_of(engine.dir_path(item))
        return (f'⛔ 这条属于目录「{engine.dir_path(item)}」，负责人是 {owner}。\n'
                f'你不在该目录的负责人之列，无法直接修改。\n\n'
                f'如需修改，可输入「提交」走审批，或联系 {owner}。')
    SESSIONS[userid] = {'step': 'edit_menu', 'action': 'edit', 'data': {'qid': item['id']}}
    return _edit_menu(item)


def _edit_menu(item, hint=''):
    kw = f'（当前：{(item.get("触发关键词") or "无")}）'
    body = (f'📝 正在编辑：**{item["标准问题"]}**\n'
            f'编号：`{item["id"]}`　目录：{engine_dir_of(item)}\n\n'
            '请选择要改哪一项，直接回复数字：\n'
            '1. 标准问题\n'
            '2. 标准答案\n'
            '3. 相似问法（多条之间用 ## 分隔）\n'
            f'4. 触发关键词{kw}\n'
            '5. 分组\n'
            '6. 三级目录\n'
            '0. 完成并保存（也可直接用 /done 结束）\n\n'
            '回复 :cancel 取消全部修改（已改的先不保存）')
    return (hint + '\n\n' + body) if hint else body


def engine_dir_of(item):
    """条目的可读目录路径（类别/分组/三级目录）。"""
    parts = [(item.get('类别') or '').strip(),
             (item.get('分组') or '').strip(),
             (item.get(SUB_FIELD) or '').strip()]
    return '/'.join([p for p in parts if p]) or '未分类'


def _show_field(item, field):
    """展示某字段当前内容，便于"先看再改"。"""
    cur = (item.get(field) or '').strip() or '（空）'
    if len(cur) > 400:
        cur = cur[:400] + ' …（已截断）'
    return (f'【修改「{field}」】\n'
            f'当前内容：\n{cur}\n\n'
            f'👉 请直接输入新的「{field}」内容（:cancel 取消本次修改）\n'
            '提示：这一步输入什么就存什么，不会当成菜单选项。')


def _finish_edit(engine, userid, st):
    """把暂存的字段改动一次性写库（立即生效）。"""
    changes = st['data'].get('changes') or {}
    SESSIONS.pop(userid, None)
    if not changes:
        return '没有改动，编辑已结束。'
    payload = {'qid': st['data']['qid']}
    payload.update(changes)
    rec = engine.submit_change('edit', payload, userid)
    done, msg = engine.decide(rec['pid'], True, userid)
    if not done:
        return f'保存失败：{msg}'
    lines = '\n'.join(f'  · {k}：{v[:60]}' for k, v in changes.items())
    return f'✅ 已保存并立即生效，共修改 {len(changes)} 项：\n{lines}'


def _start_delete(engine, userid, kw):
    item = _qid_match(engine, kw) or engine.match(kw)[0]
    if not item:
        return f'没找到与「{kw}」匹配的条目，请换个关键词，或用 /list 查看编号。'
    if not engine.can_edit(userid, item):
        owner = engine.owner_name_of(engine.dir_path(item))
        return (f'⛔ 这条属于目录「{engine.dir_path(item)}」，负责人是 {owner}。\n'
                f'你不在该目录的负责人之列，无法删除。\n\n'
                f'如需删除，请联系 {owner}。')
    SESSIONS[userid] = {'step': 'del_confirm', 'action': 'delete',
                        'data': {'qid': item['id'], '标准问题': item['标准问题']}}
    return f'确认删除「{item["标准问题"]}」？回复「是」删除，「否」取消。'


def _continue_session(engine, userid, t):
    st = SESSIONS[userid]
    step = st['step']
    if t == ':cancel':
        SESSIONS.pop(userid, None)
        return '已取消。'

    # ---- AI 改库：输入自然语言 ----
    if step == 'ai_input':
        SESSIONS.pop(userid, None)
        return _ai_handle(engine, userid, t)

    # ---- 批量导入：输入来源 ----
    if step == 'import_input':
        SESSIONS.pop(userid, None)
        return _import_handle(engine, userid, t)

    # ---- 批量导入确认 ----
    if step == 'import_confirm':
        SESSIONS.pop(userid, None)
        if t in ('否', '取消', 'no', 'n', '放弃'):
            return '📥 已取消，未导入任何内容。'
        if t not in ('是', '确认', '确定', 'yes', 'y', '保存', 'ok', '导入'):
            return '📥 请回复「是/确认」执行导入，「否」取消。'
        plan = st['data']['plan']
        ok_add = ok_edit = 0
        fails = []
        for r in plan['add']:
            rec = engine.submit_change('add', r, userid)
            done, msg = engine.decide(rec['pid'], True, userid)
            if done:
                ok_add += 1
            else:
                fails.append(f'{r.get("标准问题","")}：{msg}')
        for e in plan['edit']:
            rec = engine.submit_change('edit', {'qid': e['qid'], **e['changes']}, userid)
            done, msg = engine.decide(rec['pid'], True, userid)
            if done:
                ok_edit += 1
            else:
                fails.append(f'{e["问题"]}：{msg}')
        out = [f'✅ 导入完成并立即生效：新增 {ok_add} 条，修改 {ok_edit} 条。']
        if fails:
            out.append(f'⚠️ 有 {len(fails)} 条失败：')
            out += [f'  · {x}' for x in fails[:5]]
        return '\n'.join(out)

    # ---- AI 改库确认：修改 ----
    if step == 'ai_confirm_edit':
        SESSIONS.pop(userid, None)
        item = engine._find(st['data']['qid'])
        if not item:
            return '🤖 条目已不存在，操作取消。'
        if t in ('否', '取消', 'no', 'n', '放弃'):
            return '🤖 已取消，未做任何修改。'
        changes = st['data']['changes']
        if t in ('追加', '附加', 'append'):
            # 只把「标准答案」追加到原文后面
            new_a = changes.get('标准答案')
            if new_a:
                old = item.get('标准答案', '')
                changes = {'标准答案': old + '##' + new_a}
            else:
                return '🤖 这条没有可追加的答案内容。'
        if t not in ('是', '确认', '确定', 'yes', 'y', '保存', 'ok'):
            return ('🤖 请回复「是/确认」立即生效，「否」取消，'
                    '或「追加」把答案附加到原文后。')
        rec = engine.submit_change('edit', {'qid': item['id'], **changes}, userid)
        done, msg = engine.decide(rec['pid'], True, userid)
        if not done:
            return f'🤖 保存失败：{msg}'
        lines = '\n'.join(f'  · {k}：{v}' for k, v in changes.items())
        return f'✅ 已修改并立即生效：\n{lines}'

    # ---- AI 改库确认：新增 ----
    if step == 'ai_confirm_add':
        SESSIONS.pop(userid, None)
        if t in ('否', '取消', 'no', 'n', '放弃'):
            return '🤖 已取消，未新增。'
        if t not in ('是', '确认', '确定', 'yes', 'y', '保存', 'ok'):
            return '🤖 请回复「是/确认」新增，「否」取消。'
        rec = engine.submit_change('add', st['data']['payload'], userid)
        done, msg = engine.decide(rec['pid'], True, userid)
        if not done:
            return f'🤖 新增失败：{msg}'
        return f'✅ 已新增并立即生效：{st["data"]["payload"].get("标准问题","")}'

    # ---- AI 改库确认：删除 ----
    if step == 'ai_confirm_del':
        SESSIONS.pop(userid, None)
        if t in ('是', '确认', '确定', 'yes', 'y', '删除'):
            rec = engine.submit_change('delete', {'qid': st['data']['qid']}, userid)
            done, msg = engine.decide(rec['pid'], True, userid)
            if not done:
                return f'🤖 删除失败：{msg}'
            return f'✅ 已删除：{st["data"]["标准问题"]}'
        return '🤖 已取消删除。'

    # ---- 自动学习：为未命中问题补答案 ----
    if step == 'learn_a':
        SESSIONS.pop(userid, None)
        st['data']['标准答案'] = t                      # ← 关键：把用户输入存成答案
        st['data'].pop('learn_key', None)              # learn_key 不入库
        rec = engine.submit_change('add', st['data'], userid)
        return (f'已提交，等待 {MAINTAINER_NAME} 审批。\n'
                f'单号：{rec["pid"]}\n'
                f'问题：{st["data"]["标准问题"]}', rec)

    # ---- 目录负责人新增：先选目录 -> 问题 -> 答案 ----
    if step == 'add_dir':
        path = engine.norm_path(t)
        if not path or not engine.can_edit(userid, path):
            return ('⚠️ 你只能在自己负责的目录下新增。\n\n' + _add_dir_prompt(engine, userid))
        parts = path.split('/')
        st['data']['类别'] = parts[0] if len(parts) > 0 else ''
        st['data']['分组'] = parts[1] if len(parts) > 1 else ''
        st['data']['三级目录'] = parts[2] if len(parts) > 2 else ''
        st['data']['_dir'] = path
        st['step'] = 'add_q'
        return f'目录：{path}\n\n请输入「标准问题」（:cancel 取消）'

    # ---- 维护人直接新增 ----
    if step == 'add_q':
        st['data']['标准问题'] = t
        st['step'] = 'add_a'
        return '请输入「标准答案」（用 ## 表示换行）'
    if step == 'add_a':
        st['data']['标准答案'] = t
        SESSIONS.pop(userid, None)
        rec = engine.submit_change('add', st['data'], userid)
        _, msg = engine.decide(rec['pid'], True, userid)  # 有权限者直接生效
        # 目录负责人新增后，补一条归属说明
        if st['data'].get('_dir'):
            return (f'✅ 已新增并立即生效。\n'
                    f'目录：{st["data"]["_dir"]}\n'
                    f'标准问题：{st["data"]["标准问题"]}')
        return f'已新增并生效。\n标准问题：{st["data"]["标准问题"]}'

    # ---- 维护人编辑（引导式：选字段 -> 看当前值 -> 改）----
    if step == 'edit_find':
        return _start_edit(engine, userid, t)
    if step == 'edit_menu':
        item = engine._find(st['data']['qid'])
        if not item:
            SESSIONS.pop(userid, None)
            return '条目已不存在，编辑已取消。'
        if t in ('0', '/done', '完成', '保存', 'done'):
            return _finish_edit(engine, userid, st)
        # 字段选择：数字 或 中文名 都行
        fmap = {'1': '标准问题', '2': '标准答案', '3': '相似问法',
                '4': '触发关键词', '5': '分组', '6': SUB_FIELD,
                '标准问题': '标准问题', '问题': '标准问题',
                '标准答案': '标准答案', '答案': '标准答案',
                '相似问法': '相似问法', '相似问题': '相似问法',
                '触发关键词': '触发关键词', '关键词': '触发关键词',
                '分组': '分组', '类别': '分组',
                '三级目录': SUB_FIELD, '目录': SUB_FIELD}
        if t not in fmap:
            return _edit_menu(item, hint='⚠️ 没看懂，请输入 1~6 或字段名，输入 0 完成并保存。')
        field = fmap[t]
        st['step'] = 'edit_value'
        st['data']['field'] = field
        return _show_field(item, field)
    if step == 'edit_value':
        item = engine._find(st['data']['qid'])
        if not item:
            SESSIONS.pop(userid, None)
            return '条目已不存在，编辑已取消。'
        field = st['data']['field']
        if t == ':cancel':
            st['step'] = 'edit_menu'
            return '已取消本次修改。\n\n' + _edit_menu(item)
        st['data'].setdefault('changes', {})[field] = t
        st['step'] = 'edit_menu'
        return _edit_menu(item, hint=f'✅ 已暂存「{field}」的修改（尚未保存，输入 0 生效）')
    if step == 'edit_field':          # 兼容旧流程
        item = engine._find(st['data']['qid'])
        if item and t != ':skip':
            st['data']['标准答案'] = t
        rec = engine.submit_change('edit', st['data'], userid)
        engine.decide(rec['pid'], True, userid)
        SESSIONS.pop(userid, None)
        return f'已编辑并生效：{st["data"].get("标准问题","")}'

    # ---- 维护人删除 ----
    if step == 'del_find':
        return _start_delete(engine, userid, t)
    if step == 'del_confirm':
        if t in ('是', 'yes', 'y'):
            rec = engine.submit_change('delete', st['data'], userid)
            engine.decide(rec['pid'], True, userid)
            SESSIONS.pop(userid, None)
            return f'已删除：{st["data"].get("标准问题","")}'
        SESSIONS.pop(userid, None)
        return '已取消删除。'

    # ---- 普通员工提交（走审批，含选目录）----
    if step == 'sub_dir':
        if t in ('不知道', '不清楚', '不确定', '跳过', ':skip'):
            st['data'].pop('_dir', None)
        else:
            path = engine.norm_path(t)
            if path and engine.items_in_dir(path):
                parts = path.split('/')
                st['data']['类别'] = parts[0] if len(parts) > 0 else ''
                st['data']['分组'] = parts[1] if len(parts) > 1 else ''
                st['data']['三级目录'] = parts[2] if len(parts) > 2 else ''
                st['data']['_dir'] = path
            else:
                return (f'没认出目录「{t}」。\n'
                        f'请回复一个有效目录（可输入 `/tree` 查看），'
                        f'或回复「不知道」交给 {MAINTAINER_NAME} 分诊。')
        st['step'] = 'sub_q'
        return '第 2 步：请输入「问题」'
    if step == 'sub_q':
        st['data']['标准问题'] = t
        st['step'] = 'sub_a'
        return '第 3 步：请输入「标准答案」'
    if step == 'sub_a':
        st['data']['标准答案'] = t
        SESSIONS.pop(userid, None)
        rec = engine.submit_change('add', st['data'], userid)
        # 返回 (提示文本, 待审单)，由 main 负责把审批卡片推给对应目录负责人
        # 注意：这里不承诺"已推送"，推送结果由 main 拼到回复里
        approver = engine.owner_name_of(st['data'].get('_dir') or '')
        return (f'已提交，等待目录负责人（{approver}）审批。\n'
                f'单号：{rec["pid"]}', rec)

    SESSIONS.pop(userid, None)
    return None
