# -*- coding: utf-8 -*-
"""
企业微信「模板卡片」构造与解析。

审批卡片用 button_interaction 类型，钟勇直接点【通过】/【驳回】即可。
依据企微开发者中心文档（智能机器人 · 模板卡片类型）。

关键约束：
- 回复/更新卡片必须在收到回调后 5 秒内完成
- 点击按钮 -> aibot_event_callback，eventtype=template_card_event
  事件体含 EventKey、TaskID、CardType
- 更新卡片用 aibot_respond_update_msg，透传事件帧的 req_id + 原 card 的 task_id
"""


def build_approval_card(rec, dir_path='', owner_disp=''):
    """根据待审单生成审批卡片。

    dir_path/owner_disp 由调用方根据"归属目录"传入，便于审批人一眼看出
    这条变更落在哪个目录、归谁负责。
    """
    action_cn = {'add': '新增', 'edit': '编辑', 'delete': '删除'}.get(rec['action'], rec['action'])
    p = rec['payload']
    q = p.get('标准问题') or p.get('qid') or ''
    a = p.get('标准答案', '') or ''
    # 卡片正文里 ## 显示为换行
    a_show = a.replace('##', '\n')[:300]

    sub = f"提交人：{rec['submitter']}　操作：{action_cn}"
    if dir_path:
        sub += f"　目录：{dir_path}"
    desc = f"问题：{q}\n答案：{a_show}" if a_show else f"问题：{q}"
    if owner_disp:
        desc = f"审批人：{owner_disp}\n" + desc

    return {
        'card_type': 'button_interaction',
        'source': {'icon_url': '', 'desc': 'IT 知识库审批'},
        'main_title': {'title': '📋 知识库变更待审批', 'desc': sub},
        'quote_area': {'type': 1, 'url': '', 'title': '', 'quote_text': desc},
        'task_id': rec['pid'],
        'button_list': [
            {'text': '✅ 通过', 'style': 1, 'key': f'approve:{rec["pid"]}'},
            {'text': '❌ 驳回', 'style': 2, 'key': f'reject:{rec["pid"]}'},
        ],
        'card_action': {'type': 1, 'url': ''},
    }


def build_result_card(rec, approved):
    """审批后原地更新卡片（去掉按钮，显示结果）"""
    action_cn = {'add': '新增', 'edit': '编辑', 'delete': '删除'}.get(rec['action'], rec['action'])
    q = rec['payload'].get('标准问题') or rec['payload'].get('qid') or ''
    if approved:
        title = '✅ 已通过并生效'
        desc = f"问题：{q}\n操作：{action_cn}\n提交人：{rec['submitter']}"
    else:
        title = '❌ 已驳回'
        desc = f"问题：{q}\n操作：{action_cn}\n提交人：{rec['submitter']}\n原因：{rec.get('reason','')}"
    return {
        'card_type': 'button_interaction',
        'source': {'icon_url': '', 'desc': 'IT 知识库审批'},
        'main_title': {'title': title, 'desc': desc},
        'task_id': rec['pid'],
        'button_list': [],   # 空按钮列表 = 不可再点
        'card_action': {'type': 1, 'url': ''},
    }


def parse_card_event(event_body):
    """
    从 aibot_event_callback 的 body 里解析卡片点击。
    返回 (event_key, task_id) 或 (None, None)
    """
    ev = event_body.get('event') or event_body
    # 官方把详情放在 template_card_event 子对象里
    tc = ev.get('template_card_event') or ev
    key = tc.get('event_key') or tc.get('EventKey') or ''
    task_id = tc.get('task_id') or tc.get('TaskID') or ''
    return key, task_id


def parse_decision_key(key):
    """把按钮 key 解析成 (action, pid)。key 形如 approve:abcd1234"""
    if not key or ':' not in key:
        return None, None
    a, pid = key.split(':', 1)
    return a, pid


# ============================================================
#  欢迎卡片 / 高频问题菜单
#  （企微 API 模式不支持"输入框按钮"，用进入会话时推卡片来等效实现）
# ============================================================

# 高频问题（可按需修改；key 用 q:<问题> 形式，点击后直接返回该问题答案）
WELCOME_QUESTIONS = [
    '浩辰CAD怎么激活',
    'AUTODESK CAD怎么激活',
    'ERP系统怎么登录',
    '打印机连不上怎么办',
    '火绒客户端怎么安装',
    '电脑开不了机怎么办',
]


def build_welcome_card(questions=None):
    """员工进入会话时推送的"高频问题"菜单卡片。

    企微 button_list 最多支持 6 个，故这里最多列 6 条。
    """
    qs = (questions or WELCOME_QUESTIONS)[:6]
    return {
        'card_type': 'button_interaction',
        'source': {'icon_url': '', 'desc': 'IT 知识库助手'},
        'main_title': {
            'title': '👋 我是 IT 知识库助手',
            'desc': '点下面按钮直接提问，或在输入框输入你的问题',
        },
        'quote_area': {
            'type': 1, 'url': '', 'title': '',
            'quote_text': '常见问题快速入口（也可直接打字提问）',
        },
        'task_id': 'welcome',
        'button_list': [
            {'text': q[:20], 'style': 1, 'key': f'q:{q}'} for q in qs
        ],
        'card_action': {'type': 1, 'url': ''},
    }


def build_answer_card(question, answer):
    """点击快捷问题后，用卡片展示答案（比 markdown 更醒目）。"""
    return {
        'card_type': 'button_interaction',
        'source': {'icon_url': '', 'desc': 'IT 知识库助手'},
        'main_title': {'title': f'💡 {question}', 'desc': ''},
        'quote_area': {'type': 1, 'url': '', 'title': '', 'quote_text': answer[:500]},
        'task_id': 'answer',
        'button_list': [],
        'card_action': {'type': 1, 'url': ''},
    }


def parse_question_key(key):
    """解析快捷问题 key —— "q:浩辰CAD怎么激活" -> "浩辰CAD怎么激活" """
    if not key or not key.startswith('q:'):
        return None
    return key[2:] or None
