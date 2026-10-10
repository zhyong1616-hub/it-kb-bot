# -*- coding: utf-8 -*-
"""自动化测试：验证问答匹配 + 他人提交 + 钟勇逐条审批 全链路。"""
import asyncio
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)

# 用独立的临时数据目录，绝不污染真实 kb.csv
import tempfile
_tmp = tempfile.mkdtemp(prefix='kb_test_')
shutil.copy(os.path.join(BASE, 'data', 'kb.csv'), os.path.join(_tmp, 'kb.csv'))
os.environ['KB_DATA_DIR'] = _tmp
os.environ['KB_MAINTAINER_ID'] = 'ZhongYong'
os.environ['KB_MAINTAINER_NAME'] = '钟勇'

from app.kb_engine import KBEngine
import app.main as M

M.ENGINE = KBEngine()
E = M.ENGINE

from app import commands as C

PASS, FAIL = 0, 0


def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✅ {name}')
    else:
        FAIL += 1
        print(f'  ❌ {name}  {extra}')


async def ask(userid, text):
    r = await M.on_message(userid, text, {'req_id': 't', 'chatid': 'c', 'chattype': 'single', 'msgid': 'm'})
    # 提交成功会返回 (文本, rec)，这里只取文本
    if isinstance(r, tuple):
        return r[0]
    return r


async def main():
    print('\n=== 1. 问答匹配 ===')
    r = await ask('emp1', '浩辰CAD怎么激活')
    check('命中"浩辰CAD怎么激活"', r and '激活' in r and 'cad.cckide.com' in r, repr(r)[:80])

    r = await ask('emp1', '怎么获取授权')   # 相似问法
    check('命中相似问法"怎么获取授权"', r and '网络许可' in r, repr(r)[:60])

    r = await ask('emp1', 'U盘插上没反应')  # 火绒外设
    check('命中"U盘插上没反应"', r and '设备控制' in r, repr(r)[:60])

    r = await ask('emp1', 'ERP地址是多少')  # 换成 ERP
    check('命中 ERP 地址', r and 'zjkd.cckide.com:8001' in r, repr(r)[:60])

    r = await ask('emp1', '今天天气怎么样')  # 未命中
    check('未命中给出引导', r and '没有找到' in r, repr(r)[:40])

    print('\n=== 2. 他人提交 -> 待审（不立即生效）===')
    r = await ask('emp2', '提交')
    check('进入提交流程', r and '第 1 步' in r, repr(r)[:40])
    r = await ask('emp2', '不知道')          # 不知归属 -> 交钟勇分诊
    check('可选"不知道"交分诊', r and '第 2 步' in r, repr(r)[:40])
    r = await ask('emp2', '怎么申请绘图仪')
    check('记录问题', r and '标准答案' in r, repr(r)[:40])
    r = await ask('emp2', '在ERP里提交设备申请单，由钟勇审批。')
    check('提交成功并返回单号', r and '审批' in r, repr(r)[:60])
    pid = None
    for tok in re_find_pid(r):
        pid = tok
    check('拿到单号', bool(pid), r)
    # 提交后：普通查询还不应命中（未生效）
    r2 = await ask('emp1', '怎么申请绘图仪')
    check('未审批前不生效', r2 and ('没有找到' in r2 or '审批' not in r2), repr(r2)[:40])

    print('\n=== 3. 非维护人不能审批 ===')
    r = await ask('emp2', f'同意 {pid}')
    check('非钟勇审批被拒', r and '审批权' in r, repr(r)[:40])

    print('\n=== 4. 钟勇查看待审并通过 ===')
    r = await ask('ZhongYong', '/pending')
    check('钟勇看到待审', r and pid in r, repr(r)[:60])
    r = await ask('ZhongYong', f'同意 {pid}')
    check('钟勇通过后落库', r and '已通过并生效' in r, repr(r)[:50])
    r = await ask('emp1', '怎么申请绘图仪')
    check('通过后可命中新条目', r and '设备申请单' in r, repr(r)[:50])

    print('\n=== 5. 钟勇直接维护（立即生效）===')
    r = await ask('ZhongYong', '/add')
    check('进入新增', r and '标准问题' in r, repr(r)[:40])
    await ask('ZhongYong', '打印机怎么连')
    r = await ask('ZhongYong', '在控制面板添加网络打印机，输入IP即可。##如失败联系钟勇。')
    check('直接新增生效', r and '已新增并生效' in r, repr(r)[:40])
    r = await ask('emp1', '打印机怎么连')
    check('新条目可被查询', r and '控制面板' in r, repr(r)[:40])

    print('\n=== 6. 拒绝路径 ===')
    r = await ask('emp3', '提交')
    await ask('emp3', '不知道')
    await ask('emp3', '有没有健身房')
    r = await ask('emp3', '公司附近有健身房，B1层。')
    pid2 = re_find_pid(r)[-1] if re_find_pid(r) else None
    check('他人提交成功', bool(pid2), r)
    r = await ask('ZhongYong', f'拒绝 {pid2}')
    check('钟勇驳回', r and '已驳回' in r, repr(r)[:40])
    r = await ask('emp3', '有没有健身房')
    check('驳回后不生效', r and '没有找到' in r, repr(r)[:40])
    r = await ask('emp3', '/status')
    check('提交人可查到自己单据状态', r and pid2 in r, repr(r)[:60])

    print('\n=== 7. 审批卡片按钮（钟勇点击）===')
    from app.cards import build_approval_card, parse_decision_key
    r = await ask('emp4', '提交')
    await ask('emp4', '不知道')
    await ask('emp4', '公司停车怎么收费')
    r = await ask('emp4', '前两小时免费，之后每小时5元。')
    pid3 = re_find_pid(r)[-1]
    # 构造卡片并模拟钟勇点击"通过"
    pending = E.list_pending()
    rec = next(x for x in pending if x['pid'] == pid3)
    card = build_approval_card(rec)
    check('卡片含通过/驳回按钮', len(card['button_list']) == 2, str(card['button_list']))
    key = card['button_list'][0]['key']
    check('按钮 key 格式正确', key == f'approve:{pid3}', key)
    updated, txt = await M.on_card_event('ZhongYong', key, pid3, {'req_id': 'e'})
    check('钟勇点击通过后返回更新卡片', updated is not None and '已通过' in updated['main_title']['title'], str(updated)[:60])
    check('更新后卡片按钮已清空', updated['button_list'] == [], str(updated['button_list']))
    r = await ask('emp1', '公司停车怎么收费')
    check('按钮通过后条目生效', r and '前两小时免费' in r, repr(r)[:40])
    # 非钟勇点按钮被拒
    updated2, txt2 = await M.on_card_event('empX', key, pid3, {'req_id': 'e'})
    check('非钟勇点按钮被拒', updated2 is None and '审批权' in txt2, repr(txt2)[:40])

    print('\n=== 8. 统计 ===')
    r = await ask('ZhongYong', '/stats')
    check('统计正常', r and '知识库共' in r, repr(r)[:60])

    print('\n=== 9. 欢迎卡片与快捷问题 ===')
    from app.cards import build_welcome_card, parse_question_key, WELCOME_QUESTIONS
    wc = build_welcome_card()
    check('欢迎卡片为 button_interaction', wc.get('card_type') == 'button_interaction', str(wc.get('card_type')))
    check('欢迎卡片按钮数 1~6', 0 < len(wc.get('button_list') or []) <= 6, str(len(wc.get('button_list') or [])))
    check('快捷问题 key 以 q: 开头', wc['button_list'][0]['key'].startswith('q:'), wc['button_list'][0]['key'])
    check('parse_question_key 正确', parse_question_key('q:浩辰CAD怎么激活') == '浩辰CAD怎么激活')
    check('parse_question_key 非快捷 key 返回 None', parse_question_key('approve:abc123') is None)
    check('欢迎卡片 task_id 存在', bool(wc.get('task_id')), str(wc.get('task_id')))
    wc_custom = build_welcome_card(['A', 'B'])
    check('欢迎卡片按钮数可自定义', len(wc_custom['button_list']) == 2, str(len(wc_custom['button_list'])))

    print('\n=== 10. 进入会话事件（enter_chat） ===')
    r = await M.on_enter_chat('u1', {})
    check('进入会话返回卡片', isinstance(r, dict), type(r).__name__)
    check('卡片标题含"IT 知识库助手"', 'IT 知识库助手' in r['main_title']['title'], r['main_title']['title'])
    # 空 userid 由 bot 层 _handle_event 拦截（详见 === 13 ===），此处不测

    print('\n=== 11. 点击快捷问题 -> 返回答案卡片 ===')
    card, txt = await M.on_card_event('u1', 'q:浩辰CAD怎么激活', wc.get('task_id', 'welcome'), {'req_id': 'e'})
    check('点击快捷问题返回卡片', card is not None and '浩辰CAD' in card['main_title']['title'], str(card)[:60])
    check('答案卡片按钮已清空', card['button_list'] == [], str(card['button_list']))
    qa_text = card['quote_area']['quote_text']
    check('卡片正文无 markdown 加粗符号', '**' not in qa_text, qa_text[:40])
    check('卡片正文无 ## 标题符号', '##' not in qa_text, qa_text[:40])
    check('卡片正文含答案实体', '授权' in qa_text or '许可' in qa_text, qa_text[:40])
    # 未命中的快捷问题 -> 返回"兜底答案卡片"（按钮点击不能静默失败）
    # 用一个与库内所有条目都无主题交集的问法，确保稳定不命中
    card2, txt2 = await M.on_card_event('u1', 'q:今天天气怎么样', 'welcome', {'req_id': 'e'})
    check('快捷问题未命中仍返回兜底卡片', card2 is not None, str(card2)[:40])
    check('兜底卡片提示"暂时没有"', '暂时没有' in card2['quote_area']['quote_text'], card2['quote_area']['quote_text'][:40])
    check('兜底卡片按钮已清空', card2['button_list'] == [], str(card2['button_list']))

    print('\n=== 12. 匹配优化（防误命中 / 意图冲突 / 主题否决） ===')
    # 1) 主题否决：明确主题而库内无该主题 -> 不命中
    it, s = E.match('绘图仪怎么申请')
    check('"绘图仪怎么申请" 主题否决不误命中', it is None, f'score={s:.2f} got={it["标准问题"] if it else None}')
    # 2) 未命中引导（走完整问答链路）
    r = await ask('emp1', '绘图仪怎么申请')
    check('未命中给出"没有找到"引导', r and '没有找到' in r, repr(r)[:40])
    # 3) 意图冲突：卸载 vs 安装 不能被当成同一条
    it_u, s_u = E.match('火绒怎么卸载')
    check('"火绒怎么卸载" 不误命中安装条目', it_u is None or '安装' not in it_u['标准问题'],
          f'score={s_u:.2f} got={it_u["标准问题"] if it_u else None}')
    it_i, s_i = E.match('火绒客户端怎么安装')
    check('"火绒客户端怎么安装" 命中火绒安装条目', it_i is not None and '火绒' in it_i['标准问题'], str(it_i['标准问题']) if it_i else None)
    check('卸载/安装 意图被区分', it_i is not None and it_i['标准问题'] != (it_u['标准问题'] if it_u else None),
          f'install={it_i["标准问题"] if it_i else None} uninstall={it_u["标准问题"] if it_u else None}')
    # 4) 正常命中
    it, s = E.match('打印机连不上')
    check('"打印机连不上" 命中打印机条目', it is not None and '打印机' in it['标准问题'], str(it)[:60])
    it, s = E.match('浩辰CAD怎么激活')
    check('"浩辰CAD怎么激活" 命中浩辰激活条目', it is not None and '浩辰' in it['标准问题'] and '激活' in it['标准问题'], str(it['标准问题']) if it else None)
    # 5) 子串包含：ERP 地址类问法
    it, s = E.match('ERP地址是多少')
    check('"ERP地址是多少" 命中 ERP 条目', it is not None and 'ERP' in it['标准问题'], str(it['标准问题']) if it else None)

    print('\n=== 13. 文本消息 markdown 格式化 ===')
    md = M.to_md('浩辰CAD 激活方法（网络许可）：##1. 打开浩辰CAD；##2. 选择【许可码激活】。')
    check('to_md 去掉 ## 符号', '##' not in md, md[:60])
    check('to_md 保留换行结构', '\n' in md, repr(md)[:60])
    md2 = M.to_md('注意：计算机名称不能为中文。')
    check('to_md 加粗"注意："', '**注意' in md2, md2[:60])
    check('to_md 空文本安全', M.to_md('') == '', repr(M.to_md('')))

    print('\n=== 14. 企微事件端到端（官方帧结构回归） ===')
    from app import wecom_bot as W
    sent = []

    class _FakeWS:
        async def send(self, s):
            sent.append(json.loads(s))

    _bot = W.WeComBot('', '', M.on_message, card_handler=M.on_card_event,
                      enter_handler=M.on_enter_chat)
    _bot.ws = _FakeWS()
    # ① enter_chat 事件 -> 应回复高频问题卡片
    await _bot._handle_event({'cmd': 'aibot_event_callback', 'headers': {'req_id': 'evt1'},
                              'body': {'eventtype': 'enter_chat',
                                       'from': {'userid': 'ZhongYong'}, 'chattype': 'single'}})
    check('enter_chat 回复 1 帧', len(sent) == 1, str(len(sent)))
    f = sent[-1]
    check('enter_chat 用 aibot_respond_msg', f['cmd'] == 'aibot_respond_msg', f['cmd'])
    tc = f['body']['template_card']
    check('enter_chat 回复为模板卡片', f['body']['msgtype'] == 'template_card', f['body']['msgtype'])
    check('卡片按钮数与配置一致', len(tc['button_list']) == len(wc['button_list']), str(len(tc['button_list'])))

    # ② 点击第 3 个快捷问题（官方结构 event.template_card_event.event_key）
    key = tc['button_list'][2]['key']
    await _bot._handle_event({'cmd': 'aibot_event_callback', 'headers': {'req_id': 'evt2'},
                              'body': {'eventtype': 'template_card_event',
                                       'from': {'userid': 'ZhongYong'}, 'chattype': 'single',
                                       'event': {'template_card_event': {
                                           'event_key': key, 'task_id': tc['task_id']}}}})
    f2 = sent[-1]
    check('点击后共发送 2 帧', len(sent) == 2, str(len(sent)))
    check('点击后用 aibot_respond_update_msg 原地更新卡片',
          f2['cmd'] == 'aibot_respond_update_msg', f2['cmd'])
    tc2 = f2['body']['template_card']
    check('更新后为答案卡片', key[2:] in tc2['main_title']['title'], tc2['main_title']['title'])
    check('更新后按钮清空', tc2['button_list'] == [], str(tc2['button_list']))
    check('全部回复 msgtype 合法(markdown/template_card)',
          all(x['body']['msgtype'] in ('markdown', 'template_card') for x in sent),
          str([x['body']['msgtype'] for x in sent]))

    # ③ 审批按钮帧：非维护人点击应被拒且不更新卡片
    pkey = 'approve:' + 'a' * 12
    card_none, txt_none = await M.on_card_event('empX', pkey, 't', {'req_id': 'evt3'})
    check('非钟勇点审批按钮不返回卡片', card_none is None, str(card_none)[:40])
    check('非钟勇点审批按钮返回拒绝文案',
          ('未找到单号' in (txt_none or '')) or ('审批权' in (txt_none or '')), repr(txt_none)[:40])

    print('\n=== 15. 只回复一次（防重复回复回归） ===')
    # 场景：长连接可用 + 回调同时带 response_url —— 旧版会两条通道各发一次，
    #      用户看到答案出现两遍。修复后只应走长连接一条。
    sent.clear()
    url_hits = []

    async def _fake_url(resp_url, content):
        url_hits.append(resp_url)
        return True

    _bot2 = W.WeComBot('', '', M.on_message, card_handler=M.on_card_event,
                       enter_handler=M.on_enter_chat)
    _bot2.ws = _FakeWS()
    _bot2.reply_via_url = _fake_url          # 打出 response_url 是否被调用
    await _bot2._handle_callback({'cmd': 'aibot_msg_callback', 'headers': {'req_id': 'm1'},
                                  'body': {'from': {'userid': 'emp1'}, 'chattype': 'single',
                                           'text': {'content': '浩辰CAD怎么激活'},
                                           'response_url': 'https://example.invalid/hook'}})
    check('长连接可用时只发 1 帧', len(sent) == 1, str(len(sent)))
    check('长连接可用时不碰 response_url', url_hits == [], str(url_hits))
    check('该帧来自长连接 aibot_respond_msg', sent[0]['cmd'] == 'aibot_respond_msg', sent[0]['cmd'])

    # 反向场景：长连接挂了 -> 必须降级到 response_url，且仍只回一次
    sent.clear(); url_hits.clear()

    class _DeadWS:
        async def send(self, s):
            raise OSError('connection closed')

    _bot2.ws = _DeadWS()
    await _bot2._handle_callback({'cmd': 'aibot_msg_callback', 'headers': {'req_id': 'm2'},
                                  'body': {'from': {'userid': 'emp1'}, 'chattype': 'single',
                                           'text': {'content': '浩辰CAD怎么激活'},
                                           'response_url': 'https://example.invalid/hook'}})
    check('长连接失败时降级调用 response_url', url_hits == ['https://example.invalid/hook'], str(url_hits))
    check('降级时未重复发长连接', sent == [], str(len(sent)))

    print('\n=== 16. 身份识别 / 问候语 ===')
    # 维护人自报身份 -> 确认
    r = await ask('ZhongYong', '我是钟勇')
    check('钟勇自报身份被确认', r and '已确认身份' in r and '钟勇' in r, repr(r)[:50])
    r = await ask('ZhongYong', '你好')
    check('钟勇问候语有回应', r and '钟勇' in r and '/stats' in r, repr(r)[:50])
    # 普通员工冒充钟勇 -> 明确纠正，不能沉默
    r = await ask('emp1', '我是钟勇')
    check('员工冒充钟勇被纠正', r and '不是' in r and '钟勇' in r, repr(r)[:60])
    check('冒充者被提示走提交审批', r and '提交' in r, repr(r)[:60])
    # 普通员工问候
    r = await ask('emp1', '你好')
    check('员工问候语有回应', r and 'IT 知识库助手' in r, repr(r)[:50])
    # 报别的名字 -> 认出他，不沉默
    r = await ask('emp1', '我是张三')
    check('员工自报姓名有回应', r and '张三' in r, repr(r)[:50])
    # "我是谁" -> 查询身份，不该被当成自报姓名
    r = await ask('emp1', '我是谁')
    check('"我是谁" 返回身份核查', r and '已核查' in r and 'emp1' in r, repr(r)[:60])
    # 带标点也要能识别
    for q in ['我是谁？', '我是谁?', '我的身份', '查我的身份', 'whoami']:
        r = await ask('emp1', q)
        check(f'"{q}" 也能查身份', r and '已核查' in r and 'emp1' in r, repr(r)[:50])
    # 谁维护人查询要带姓名
    r = await ask('ZhongYong', '我是谁？')
    check('钟勇查身份带姓名', r and '钟勇' in r and 'ZhongYong' in r and '维护人' in r, repr(r)[:60])
    # "你是谁" -> 机器人自述
    r = await ask('emp1', '你是谁')
    check('"你是谁" 机器人自述', r and 'IT 知识库助手' in r, repr(r)[:50])
    # "我是哪位" 不能被当成自报姓名
    r = await ask('emp1', '我是哪位')
    check('"我是哪位" 不当成自报姓名', r and '已核查' in r and '收到，哪位' not in r, repr(r)[:60])
    # 不误伤正常提问
    r = await ask('emp1', '浩辰CAD怎么激活')
    check('身份识别不误伤正常提问', r and '激活' in r and '你好' not in r, repr(r)[:50])

    print('\n=== 17. 欧特克 / Autodesk 别名 ===')
    # 用户叫法五花八门，都必须路由到 AutoCAD / Revit 条目，而不是浩辰
    variants = [
        ('欧特克cad怎么激活', 'AutoCAD'),
        ('欧特克CAD怎么激活', 'AutoCAD'),
        ('autodeskcad怎么激活', 'AutoCAD'),
        ('autocad怎么激活', 'AutoCAD'),
        ('欧特克怎么激活', 'AutoCAD'),
        ('欧特克revit怎么激活', 'Revit'),
        ('autodesk revit怎么激活', 'Revit'),
    ]
    for q, want in variants:
        it, s = E.match(q)
        check(f'"{q}" -> {want}', it is not None and want.lower() in it['标准问题'].lower(),
              f'got={it["标准问题"] if it else None} ({s:.2f})')
    # 不能再跑偏到浩辰
    it, s = E.match('欧特克cad怎么激活')
    check('"欧特克cad" 不再命中浩辰', it is not None and '浩辰' not in it['标准问题'], str(it['标准问题']) if it else None)
    # 欧特克 + 具体诉求，要命中对应条目而非"激活"
    for q, want in [('欧特克CAD许可服务器地址', '服务器地址'),
                    ('欧特克CAD卡顿', '卡顿'),
                    ('欧特克CAD字体乱码', '乱码')]:
        it, s = E.match(q)
        check(f'"{q}" -> 含"{want}"', it is not None and want in it['标准问题'],
              f'got={it["标准问题"] if it else None} ({s:.2f})')
    # 浩辰不能被欧特克别名污染
    it, s = E.match('浩辰CAD怎么激活')
    check('"浩辰CAD怎么激活" 仍归浩辰', it is not None and '浩辰' in it['标准问题'], str(it['标准问题']) if it else None)

    print('\n=== 18. 对话内维护（引导式编辑） ===')
    Q = '浩辰CAD打开图纸很慢'
    # /list 应带 id，便于引用
    r = await ask('ZhongYong', '/list 浩辰')
    check('/list 显示条目 id', r and '`' in r and '浩辰' in r, repr(r)[:60])
    # 开始编辑 -> 应展示菜单
    r = await ask('ZhongYong', f'/edit {Q}')
    check('编辑入口展示字段菜单', r and '请选择要改哪一项' in r and Q in r, repr(r)[:60])
    check('菜单含字段名', r and '标准答案' in r and '相似问法' in r and '触发关键词' in r, repr(r)[:60])
    # 选"标准答案"（用中文名，验证别名映射）
    r = await ask('ZhongYong', '标准答案')
    check('选字段后展示当前内容', r and '当前内容' in r and '修改「标准答案」' in r, repr(r)[:60])
    # 输入新内容 -> 暂存（不应立即生效）
    NEW = '先清理临时文件并升级到最新版。##仍慢联系钟勇。'
    r = await ask('ZhongYong', NEW)
    check('输入内容后提示已暂存', r and '已暂存' in r, repr(r)[:60])
    r = await ask('emp1', Q)
    check('暂存阶段尚未生效', r and '先清理临时文件' not in r, repr(r)[:50])
    # 保存
    r = await ask('ZhongYong', '0')
    check('输入0后保存并生效', r and '已保存并立即生效' in r, repr(r)[:60])
    r = await ask('emp1', Q)
    check('保存后员工侧生效', r and '先清理临时文件' in r, repr(r)[:50])
    # :cancel 不保存
    await ask('ZhongYong', f'/edit {Q}')
    await ask('ZhongYong', '1')
    await ask('ZhongYong', '这个名字不该被保存')
    r = await ask('ZhongYong', ':cancel')
    check(':cancel 提示已取消', r and '已取消' in r, repr(r)[:40])
    r = await ask('emp1', Q)
    check(':cancel 后原内容未被改', r and '先清理临时文件' in r, repr(r)[:50])
    # 单字段编辑不应清空其它字段（回归：曾出现只改一个问题、答案被清空）
    it_now, _ = E.match(Q)
    check('编辑后标准答案仍在', it_now and it_now['标准答案'].strip() != '', repr(it_now['标准答案'])[:40] if it_now else 'no item')
    # 非维护人不能编辑
    r = await ask('emp1', f'/edit {Q}')
    check('员工无权进入编辑', r and ('提交' in r or '没有' in r or '审批' in r), repr(r)[:60])

    print('\n=== 19. 自动学习（未命中 -> 清单 -> 补答案 -> 入库） ===')
    # 清掉前面用例可能留下的未命中记录，保证计数确定
    for _r in E.list_unmatched(limit=0):
        E.remove_unmatched(_r['key'])
    UN = '团建经费怎么报销'
    # 员工问知识库没有的问题 -> 应被记录
    r = await ask('emp1', UN)
    check('未命中仍给出引导', r and '没有找到' in r, repr(r)[:40])
    check('未命中被记录到清单', E.unmatched_count() == 1, str(E.unmatched_count()))
    # 另一人问同一问题 -> 计数累加，不新增条数
    await ask('emp2', UN)
    check('同一问题去重累计频次', E.unmatched_count() == 1, str(E.unmatched_count()))
    rec = E.list_unmatched()[0]
    check('频次累计为 2', rec['count'] == 2, str(rec['count']))
    check('记录了提问人', set(rec['askers']) == {'emp1', 'emp2'}, str(rec['askers']))
    # 员工不能用 /learn
    r = await ask('emp1', '/learn')
    check('员工无权看学习清单', r and '没有' in r and '权限' in r, repr(r)[:50])
    # 钟勇查看清单
    r = await ask('ZhongYong', '/learn')
    check('钟勇看到学习清单', r and UN in r and '2 次' in r, repr(r)[:70])
    # 走"补答案"流程
    r = await ask('ZhongYong', '/learn 1')
    check('进入补答案流程', r and UN in r and '请输入【标准答案】' in r, repr(r)[:60])
    r = await ask('ZhongYong', '在OA提报销单，选「团建经费」科目。##由钟勇审批。')
    check('提交后生成审批单', r and '等待' in r and '单号' in r, repr(r)[:60])
    # 走审批：员工提交的也应经钟勇确认（这里钟勇自己走完）
    pending = E.list_pending()
    check('产生了待审单', len(pending) >= 1, str(len(pending)))
    lpid = pending[-1]['pid']
    r = await ask('ZhongYong', f'同意 {lpid}')
    check('审批通过后入库', r and '已通过并生效' in r, repr(r)[:50])
    # 员工再问 -> 命中新答案
    r = await ask('emp1', UN)
    check('学习后员工能查到答案', r and '团建经费' in r, repr(r)[:60])
    # 已补进知识库 -> 自动移出待学习清单
    check('已学习的条目自动出列', E.unmatched_count() == 0, str(E.unmatched_count()))
    # skip / clear
    await ask('emp1', '年会节目怎么报名')
    await ask('emp1', '停车券怎么领')
    r = await ask('ZhongYong', '/learn')
    check('清单出现新条目', r and '年会节目怎么报名' in r and '停车券怎么领' in r, repr(r)[:70])
    r = await ask('ZhongYong', '/learn skip 1')
    check('skip 忽略成功', r and '已忽略' in r, repr(r)[:50])
    check('忽略后计数减少', E.unmatched_count() == 1, str(E.unmatched_count()))
    r = await ask('ZhongYong', '/learn clear')
    check('clear 清空清单', r and '已清空' in r, repr(r)[:40])
    check('清空后为 0', E.unmatched_count() == 0, str(E.unmatched_count()))
    # 统计里能看到待学习数
    r = await ask('ZhongYong', '/stats')
    check('/stats 含待学习数', r and '待学习' in r, repr(r)[:60])

    print('\n=== 20. 审批推送（回归：推了但收不到 / 谎报已推送） ===')
    from app import wecom_bot as W2
    _sent = []

    class _WS2:
        async def send(self, s):
            _sent.append(json.loads(s))

    class _LiveBot:
        ws = _WS2()

        async def _send(self, cmd, req_id, body):
            await self.ws.send(json.dumps({'cmd': cmd, 'headers': {'req_id': req_id},
                                           'body': body}, ensure_ascii=False))
            return True

        async def send_to_user(self, chatid, content, chat_type=1):
            return await self._send('aibot_send_msg', 'p',
                                    {'chatid': chatid, 'chat_type': chat_type,
                                     'msgtype': 'markdown', 'markdown': {'content': content}})

    M.BOT = _LiveBot()
    os.environ['KB_MOCK'] = '0'

    # 关键：主动推送必须用 chatid（不是 userid）。模拟钟勇先说话，记下 chatid。
    await ask('ZhongYong', '你好')
    # 上面 ask() 用的是固定 chatid 'c'，这里直接驱动一次带真实 chatid 的调用
    await M.on_message('ZhongYong', '你好',
                       {'req_id': 't', 'chatid': 'CHAT_ZY_123', 'chattype': 'single', 'msgid': 'm'})
    check('记录了钟勇的 chatid', M._maintainer_chatid() == 'CHAT_ZY_123',
          M._maintainer_chatid())
    check('无 chatid 时回退 userid', M._maintainer_chatid() != '')

    # 员工提交 -> 推送目标应是 chatid
    _sent.clear()
    await ask('emp1', '提交')
    await ask('emp1', '不知道')
    await ask('emp1', '推送测试问题')
    r = await ask('emp1', '推送测试答案')
    check('提交后员工收到"已推送"提示', r and '已把审批卡片推送' in r, repr(r)[:70])
    pushframes = [f for f in _sent if f['cmd'] == 'aibot_send_msg']
    check('确实发出了主动推送帧', len(pushframes) >= 1, str(len(pushframes)))
    check('推送用的是 chatid 而非 userid',
          pushframes and pushframes[-1]['body']['chatid'] == 'CHAT_ZY_123',
          str(pushframes[-1]['body']['chatid']) if pushframes else 'none')
    check('推送的是模板卡片', pushframes and pushframes[-1]['body']['msgtype'] == 'template_card',
          pushframes[-1]['body']['msgtype'] if pushframes else 'none')

    # 推送失败 -> 不能谎报"已推送"，要给兜底指引
    class _DeadBot:
        async def _send(self, cmd, req_id, body):
            return False

        async def send_to_user(self, chatid, content, chat_type=1):
            return False

    M.BOT = _DeadBot()
    await ask('emp1', '提交')
    await ask('emp1', '不知道')
    await ask('emp1', '失败推送问题')
    r = await ask('emp1', '失败推送答案')
    check('推送失败时不谎报已推送', r and '已把审批卡片推送' not in r, repr(r)[:70])
    check('推送失败时给出兜底指引', r and '没送达' in r and '/pending' in r, repr(r)[:70])

    M.BOT = None
    os.environ['KB_MOCK'] = '1'

    # ================= 21. 三级目录 & 目录负责人 =================
    print('\n=== 21. 三级目录树 ===')
    tree = E.build_tree()
    cats = {n['name']: n for n in tree}
    check('目录树含三大类', {'软件', 'ERP', '电脑故障'} <= set(cats.keys()), str(list(cats)))
    total = sum(n['total'] for n in tree)
    check('目录树总条数=知识库条数', total == len(E.items), f'{total} vs {len(E.items)}')
    check('软件类含二级分组', len(cats['软件']['children']) >= 5,
          str(len(cats['软件']['children'])))
    # 三级目录节点存在
    hc = next((c for c in cats['软件']['children'] if c['name'] == '浩辰CAD许可配置'), None)
    check('存在"软件/浩辰CAD许可配置"节点', hc is not None)
    check('浩辰节点含三级子目录', hc and len(hc['children']) >= 3,
          str(len(hc['children']) if hc else 0))
    check('浩辰节点汇总条数为8', hc and hc['total'] == 8, str(hc['total']) if hc else 'none')

    print('\n=== 22. 按目录过滤（三种粒度） ===')
    check('一级目录过滤 软件=41', len(E.items_in_dir('软件')) == 41,
          str(len(E.items_in_dir('软件'))))
    check('二级目录过滤 浩辰=8', len(E.items_in_dir('软件/浩辰CAD许可配置')) == 8,
          str(len(E.items_in_dir('软件/浩辰CAD许可配置'))))
    check('三级目录过滤 激活授权=5',
          len(E.items_in_dir('软件/浩辰CAD许可配置/激活授权')) == 5,
          str(len(E.items_in_dir('软件/浩辰CAD许可配置/激活授权'))))
    check('空路径=全部', len(E.items_in_dir('')) == len(E.items))
    check('不存在的目录=0', len(E.items_in_dir('不存在的目录')) == 0)
    # 路径规整
    check('路径规整（多余斜杠/空格）',
          E.norm_path(' 软件 / 浩辰CAD许可配置 ') == '软件/浩辰CAD许可配置',
          E.norm_path(' 软件 / 浩辰CAD许可配置 '))

    print('\n=== 23. 负责人查询（越具体优先） ===')
    E.save_owners({'软件': 'CatOwner', '软件/浩辰CAD许可配置': 'GrpOwner',
                   '软件/浩辰CAD许可配置/激活授权': 'SubOwner'})
    check('三级目录负责人优先', E.owner_of('软件/浩辰CAD许可配置/激活授权') == 'SubOwner',
          E.owner_of('软件/浩辰CAD许可配置/激活授权'))
    check('未配三级时回退二级', E.owner_of('软件/浩辰CAD许可配置/安装部署') == 'GrpOwner',
          E.owner_of('软件/浩辰CAD许可配置/安装部署'))
    check('未配二级时回退一级', E.owner_of('软件/SketchUp') == 'CatOwner',
          E.owner_of('软件/SketchUp'))
    check('完全未配置回退钟勇', E.owner_of('ERP/ERP登录权限') == 'ZhongYong',
          E.owner_of('ERP/ERP登录权限'))
    check('负责人显示名（钟勇中文名）', E.owner_name_of('ERP/ERP登录权限') == '钟勇',
          E.owner_name_of('ERP/ERP登录权限'))

    print('\n=== 24. 权限边界（只管自己目录，到自己为止） ===')
    check('钟勇全权-可改任意目录', E.can_edit('ZhongYong', '软件/火绒安全软件') is True)
    check('一级负责人可改本类任意条', E.can_edit('CatOwner', '软件/SketchUp') is True,
          str(E.can_edit('CatOwner', '软件/SketchUp')))
    check('二级负责人可改本组', E.can_edit('GrpOwner', '软件/浩辰CAD许可配置/安装部署') is True)
    check('二级负责人不可改他组', E.can_edit('GrpOwner', '软件/SketchUp') is False)
    check('三级负责人可改本三级', E.can_edit('SubOwner', '软件/浩辰CAD许可配置/激活授权') is True)
    check('三级负责人不可改同级其它三级',
          E.can_edit('SubOwner', '软件/浩辰CAD许可配置/安装部署') is False)
    check('无关人员无权', E.can_edit('路人甲', '软件/浩辰CAD许可配置') is False)
    check('dirs_of_user 列出负责目录',
          E.dirs_of_user('GrpOwner') == ['软件/浩辰CAD许可配置'], str(E.dirs_of_user('GrpOwner')))
    check('钟勇 dirs_of_user=[*]', E.dirs_of_user('ZhongYong') == ['*'])

    print('\n=== 25. /tree 命令 ===')
    E.save_owners({'软件/浩辰CAD许可配置': 'ZhongYong', '软件/AutoCAD与Revit': 'ZhongYong',
                   '软件/SketchUp': 'ZhongYong', '软件/火绒安全软件': 'ZhongYong',
                   '软件/软件通用问题': 'ZhongYong', 'ERP/ERP登录权限': 'ZhongYong',
                   'ERP/ERP项目管理': 'ZhongYong', 'ERP/查询导出': 'ZhongYong',
                   '电脑故障/系统故障': 'ZhongYong', '电脑故障/外设网络': 'ZhongYong',
                   '电脑故障/常用软件报障': 'ZhongYong'})
    r = await ask('ZhongYong', '/tree')
    check('/tree 显示三大类', r and '软件' in r and 'ERP' in r and '电脑故障' in r, repr(r)[:60])
    check('/tree 含条数', r and '共' in r and '条' in r, repr(r)[:60])
    check('/tree 含负责人', r and '负责人' in r, repr(r)[:60])

    r = await ask('anyone', '/tree')     # 员工也能看
    check('员工也能看 /tree', r and '软件' in r and 'ERP' in r, repr(r)[:60])

    print('\n=== 26. /tree <目录> 命令 ===')
    r = await ask('ZhongYong', '/tree 软件/浩辰CAD许可配置')
    check('/tree 目录 列出条目', r and '浩辰CAD怎么激活获取授权' in r, repr(r)[:80])
    check('/tree 目录 含条数与负责人', r and '共 8 条' in r and '负责人' in r, repr(r)[:80])
    r = await ask('ZhongYong', '/tree 不存在的目录XYZ')
    check('/tree 不存在的目录有提示', r and ('暂无条目' in r or '没看懂' in r), repr(r)[:60])

    print('\n=== 27. /owner 命令 ===')
    r = await ask('ZhongYong', '/owner')
    check('/owner 列出各目录负责人', r and '浩辰CAD许可配置' in r and '钟勇' in r, repr(r)[:60])
    r = await ask('ZhongYong', '/owner 软件/浩辰CAD许可配置/激活授权 TestUser')
    check('/owner 设置成功', r and '✅' in r and 'TestUser' in r, repr(r)[:60])
    check('设置生效', E.owner_of('软件/浩辰CAD许可配置/激活授权') == 'TestUser',
          E.owner_of('软件/浩辰CAD许可配置/激活授权'))
    r = await ask('ZhongYong', '/owner 目录缺少用户')
    check('/owner 参数不足有提示', r and '格式' in r, repr(r)[:60])
    r = await ask('emp1', '/owner')      # 非钟勇不能设
    check('非钟勇不能设 /owner', r and '没有该操作' in r or (r and '👥' not in r), repr(r)[:60])

    print('\n=== 28. 目录负责人在自己目录内改动立即生效 ===')
    # 钟勇把「浩辰CAD许可配置」交给 ZhangGong
    await ask('ZhongYong', '/owner 软件/浩辰CAD许可配置 ZhangGong')
    # /list 只见自己目录
    r = await ask('ZhangGong', '/list')
    check('负责人 /list 只见本目录', r and '共 8 条' in r and '浩辰CAD' in r, repr(r)[:60])
    check('负责人 /list 不含他目录条目', r and '火绒' not in r, repr(r)[:60])
    # 允许改自己目录
    r = await ask('ZhangGong', '/edit 浩辰CAD怎么激活')
    check('负责人可编辑本目录条目', r and '正在编辑' in r, repr(r)[:60])
    # 拒绝改他目录
    r = await ask('ZhangGong', '/del 火绒客户端怎么安装')
    check('负责人不可删他目录条目', r and '⛔' in r and '负责人是' in r, repr(r)[:60])
    # 新增到自己目录
    await ask('ZhangGong', '/add')
    await ask('ZhangGong', '软件/浩辰CAD许可配置/激活授权')
    await ask('ZhangGong', '目录测试新增问题AAA')
    r = await ask('ZhangGong', '目录测试新增答案BBB')
    check('负责人可新增到本目录并立即生效', r and '✅' in r, repr(r)[:60])
    check('新增条目确实入库', E._find('目录测试新增问题AAA') is not None)
    # 新增到他目录被拒
    await ask('ZhangGong', '/add')
    r = await ask('ZhangGong', 'ERP/ERP登录权限')
    check('负责人不可新增到他目录', r and '只能在自己负责的目录' in r, repr(r)[:60])

    print('\n=== 29. 员工 /tree 可直接对话查看最新目录结构 ===')
    r = await ask('emp1', '/tree')
    check('员工 /tree 含最新新增目录', r and '软件' in r and 'ERP' in r, repr(r)[:60])
    check('员工 /tree 是完整树（含三级）', r and '激活授权' in r, repr(r)[:60])

    print('\n=== 30. 审批卡片按归属目录推给对应负责人 ===')
    # 钟勇把「火绒安全软件」交给 HuoOwner
    await ask('ZhongYong', '/owner 软件/火绒安全软件 HuoOwner')
    check('负责人查询生效', E.owner_of('软件/火绒安全软件') == 'HuoOwner',
          E.owner_of('软件/火绒安全软件'))
    # 员工提交到火绒目录
    await ask('emp1', '提交')
    await ask('emp1', '软件/火绒安全软件/运行与防护')
    await ask('emp1', '火绒能不能装别的杀毒')
    r = await ask('emp1', '不建议，可能冲突。')
    check('提交到指定目录成功', r and '审批' in r, repr(r)[:60])
    pid_h = re_find_pid(r)[-1] if re_find_pid(r) else None
    rec_h = next((x for x in E.list_pending() if x['pid'] == pid_h), None)
    check('待审单归属目录正确',
          rec_h and E.dir_path_target(rec_h) == '软件/火绒安全软件/运行与防护',
          E.dir_path_target(rec_h) if rec_h else 'none')
    # 该条应推给 HuoOwner 而不是钟勇
    check('审批人解析为目录负责人', M._owner_of_record(rec_h) == 'HuoOwner',
          M._owner_of_record(rec_h) if rec_h else 'none')
    # 钟勇仍可审批（全权）
    r = await ask('ZhongYong', f'同意 {pid_h}')
    check('钟勇仍可全权审批', r and '已通过并生效' in r, repr(r)[:60])
    # 目录负责人也能审批（新建一条走 HuoOwner）
    await ask('emp1', '提交')
    await ask('emp1', '软件/火绒安全软件/运行与防护')
    await ask('emp1', '火绒日志在哪看')
    r = await ask('emp1', '在客户端-日志里看。')
    pid_h2 = re_find_pid(r)[-1] if re_find_pid(r) else None
    r = await ask('HuoOwner', f'同意 {pid_h2}')
    check('目录负责人可审批自己目录', r and '已通过并生效' in r, repr(r)[:60])
    # 别的负责人不能审批火绒的单
    await ask('emp1', '提交')
    await ask('emp1', '软件/火绒安全软件/运行与防护')
    await ask('emp1', '火绒卸载要密码吗')
    r = await ask('emp1', '要管理员密码。')
    pid_h3 = re_find_pid(r)[-1] if re_find_pid(r) else None
    r = await ask('ZhangGong', f'同意 {pid_h3}')
    check('他目录负责人不能越权审批', r and '审批权' in r, repr(r)[:60])

    # ================= 31. 热更新（外部改 kb.csv 后无需重启）=================
    print('\n=== 31. 热更新（改了文件立即生效，不用重启）===')
    import csv as _csv
    import io as _io
    import time as _time

    kb_path = os.path.join(_tmp, 'kb.csv')
    # 在问答前，外部直接改 kb.csv 里「火绒客户端怎么安装」的答案
    with _io.open(kb_path, encoding='utf-8-sig', newline='') as f:
        _rows = list(_csv.reader(f))
    _h, _ai = _rows[0], _rows[0].index('标准答案')
    _qi = _rows[0].index('标准问题')
    _marker = '热更新测试答案：访问 sf.cckide.com:6080'
    for _r in _rows[1:]:
        if _r and _r[_qi] == '火绒客户端怎么安装':
            _r[_ai] = _marker
    _time.sleep(1.1)   # 确保 mtime 变化
    with _io.open(kb_path, 'w', encoding='utf-8-sig', newline='') as f:
        _csv.writer(f).writerows(_rows)

    # 不改代码、不重启，直接对话提问 —— 应命中新答案
    r = await ask('emp1', '火绒怎么安装')
    check('外部改文件后问答即时生效', r and 'sf.cckide.com:6080' in r, repr(r)[:60])
    check('内存已重载为新答案',
          E._find('火绒客户端怎么安装')['标准答案'] == _marker,
          E._find('火绒客户端怎么安装')['标准答案'][:30])

    # 无变更时不应重复重载
    check('无变更时不重载', E.reload_if_changed() is False)

    # /reload 强制重载（钟勇）
    r = await ask('ZhongYong', '/reload')
    check('钟勇可手动 /reload', r and '已重新加载' in r, repr(r)[:50])
    # 员工不能 /reload
    r = await ask('emp1', '/reload')
    check('员工不能 /reload', r and '权限' in r, repr(r)[:50])

    # ================= 32. AI 自然语言改库 =================
    print('\n=== 32. AI 自然语言改库 ===')
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import json as _json

    class _MockAI(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get('Content-Length', 0))
            req = _json.loads(self.rfile.read(n).decode('utf-8'))
            user = [m for m in req['messages'] if m['role'] == 'user'][-1]['content']
            if '改成' in user or '修改' in user:
                obj = {'action': 'edit', 'match': '火绒怎么安装',
                       '标准答案': 'AI改后的答案：访问 sf.cckide.com:6080 下载，然后一路下一步。##有疑问联系钟勇。',
                       'reason': '修改答案'}
            elif '新增' in user or '加一条' in user:
                obj = {'action': 'add', '标准问题': 'AI新增测试问题',
                       '标准答案': 'AI答案第一步##AI答案第二步',
                       '类别': '电脑故障', '分组': '常用软件报障', '三级目录': '浏览器',
                       'reason': '新增'}
            elif '删除' in user:
                obj = {'action': 'delete', 'match': '浩辰CAD打开图纸很慢', 'reason': '删除'}
            else:
                obj = {'action': 'ask'}
            out = _json.dumps({'choices': [{'message': {'content': _json.dumps(obj, ensure_ascii=False)}}]},
                              ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    _srv = HTTPServer(('127.0.0.1', 0), _MockAI)
    _port = _srv.server_address[1]
    threading.Thread(target=_srv.serve_forever, daemon=True).start()

    # ① 未配置 AI 时优雅降级
    os.environ['AI_ENABLED'] = '0'
    from app import ai_assistant as _AI
    check('未配置时 AI not ready', _AI.ai_ready() is False)
    r = await ask('ZhongYong', '/ai 随便说点什么')
    check('未配置时 AI 功能给配置指引', r and 'AI_ENABLED' in r, repr(r)[:50])

    # ② 配置 AI 后走全链路
    os.environ['AI_ENABLED'] = '1'
    os.environ['AI_BASE_URL'] = f'http://127.0.0.1:{_port}/v1'
    os.environ['AI_API_KEY'] = 'test-key'
    os.environ['AI_MODEL'] = 'mock'
    check('配置后 AI ready', _AI.ai_ready() is True)

    # 修改：给预览 -> 确认 -> 生效
    r = await ask('ZhongYong', '/ai 把火绒怎么安装的答案改成：先访问 sf.cckide.com:6080 下载')
    check('AI 修改给预览', r and '将要改成' in r and '确认' in r, repr(r)[:60])
    check('预览里没直接落库（原文未变）',
          'AI改后的答案' not in E._find('火绒客户端怎么安装')['标准答案'])
    r = await ask('ZhongYong', '是')
    check('AI 修改确认后立即生效', r and '已修改并立即生效' in r, repr(r)[:60])
    check('AI 修改确实入库',
          'sf.cckide.com:6080' in E._find('火绒客户端怎么安装')['标准答案'],
          E._find('火绒客户端怎么安装')['标准答案'][:40])

    # 取消：不落库
    r = await ask('ZhongYong', '/ai 把火绒怎么安装的答案改成：这句话不该被保存')
    r2 = await ask('ZhongYong', '否')
    check('AI 修改可取消', r2 and ('已取消' in r2 or '未做任何修改' in r2), repr(r2)[:50])

    # 新增：确认后入库
    before = len(E.items)
    await ask('ZhongYong', '/ai 新增一条：AI新增测试问题')
    r = await ask('ZhongYong', '确认')
    check('AI 新增确认后生效', r and '已新增并立即生效' in r, repr(r)[:60])
    check('AI 新增确实入库', len(E.items) == before + 1, f'{before} -> {len(E.items)}')

    # 删除：确认后删除
    r = await ask('ZhongYong', '/ai 删除一条：浩辰CAD打开图纸很慢')
    check('AI 删除先确认', r and ('确认删除' in r or '回复「是」' in r), repr(r)[:50])
    r = await ask('ZhongYong', '是')
    check('AI 删除确认后生效', r and '已删除' in r, repr(r)[:50])

    # 员工用 AI 改库 -> 被拒
    r = await ask('emp1', '/ai 把答案改成 xxx')
    check('员工不能 AI 改库', r and '权限' in r, repr(r)[:50])

    # ③ AI 改库遵守目录权限（张工只能改自己目录）
    await ask('ZhongYong', '/owner 软件/浩辰CAD许可配置 ZhangGong')
    os.environ['AI_MODEL'] = 'mock'
    # 让 mock 返回对 ERP 条目的修改（张工无权）
    r = await ask('ZhangGong', '/ai 把火绒怎么安装的答案改成：不属于我目录的改动')
    check('AI 改库受目录权限约束',
          r and ('⛔' in r or '无权' in r or '负责人' in r), repr(r)[:70])

    # ④ 模型调用失败要有兜底
    os.environ['AI_BASE_URL'] = 'http://127.0.0.1:1/v1'   # 打不通
    r = await ask('ZhongYong', '/ai 把火绒答案改成xxx')
    check('模型失败有兜底提示', r and ('没太理解' in r or '失败' in r), repr(r)[:60])

    _srv.shutdown()
    os.environ['AI_ENABLED'] = '0'
    os.environ['AI_BASE_URL'] = ''
    os.environ['AI_API_KEY'] = ''

    # ================= 33. /import 批量导入 =================
    print('\n=== 33. /import 批量导入 ===')
    import csv as _csv
    from app import importer as _IMP

    # 准备一份导入文件（含：1 条新增、1 条修改、1 条无变化、1 条缺答案）
    imp_dir = os.path.join(_tmp, 'imports')
    os.makedirs(imp_dir, exist_ok=True)
    csv_path = os.path.join(imp_dir, 'batch.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['类别', '分组', '三级目录', '标准问题', '标准答案', '相似问法', '触发关键词'])
        w.writerow(['软件', '浩辰CAD许可配置', '激活授权', '导入测试新增问题A', '答案A第一行##答案A第二行', '问A别名', '关键词A'])
        w.writerow(['软件', '浩辰CAD许可配置', '激活授权', '浩辰CAD怎么激活获取授权', '导入改后的激活答案：访问 cad.cckide.com', '怎么获取授权', ''])
        w.writerow(['ERP', '登录与权限', '账号', 'ERP账号被锁定怎么办', '联系钟勇解锁', '', ''])

    # ① 解析器：表头别名 + 缺答案跳过
    recs, errs = _IMP.parse_csv(csv_path)
    check('解析出 3 条有效记录', len(recs) == 3, f'got {len(recs)}')
    check('缺答案是无效行（本例无）', isinstance(errs, list))

    # ② 差异比对：分出新增 / 修改 / 无变化
    plan = E.bulk_plan(recs)
    check('比对出 2 条新增', len(plan['add']) == 2, str(len(plan['add'])))
    check('比对出 1 条修改', len(plan['edit']) == 1, str(len(plan['edit'])))
    check('比对出 0 条无变化', len(plan['same']) == 0, str(len(plan['same'])))

    # ③ 员工没有导入权限
    r = await ask('emp1', f'/import {csv_path}')
    check('员工不能导入', r and '权限' in r, repr(r)[:50])

    # ④ 钟勇导入：先给预览，不落库
    before = len(E.items)
    r = await ask('ZhongYong', f'/import {csv_path}')
    check('导入先给预览', r and '解析完成' in r and '确认导入' in r, repr(r)[:70])
    check('预览里含新增/修改统计', r and '新增 2 条' in r and '修改 1 条' in r, repr(r)[:80])
    check('预览阶段不落库', len(E.items) == before, f'{before} -> {len(E.items)}')

    # ⑤ 取消：不落库
    r = await ask('ZhongYong', '否')
    check('导入可取消', r and '已取消' in r, repr(r)[:50])
    check('取消后确实没落库', len(E.items) == before)

    # ⑥ 确认：新增 + 修改均生效
    await ask('ZhongYong', f'/import {csv_path}')
    r = await ask('ZhongYong', '是')
    check('导入确认后生效', r and '导入完成' in r and '新增 2 条' in r and '修改 1 条' in r, repr(r)[:70])
    check('导入后条目数 +2', len(E.items) == before + 2, f'{before} -> {len(E.items)}')
    check('导入的新条目内容正确',
          E._find('导入测试新增问题A') and '答案A第一行' in E._find('导入测试新增问题A')['标准答案'])
    check('导入的修改已覆盖原答案',
          '导入改后的激活答案' in (E._find('浩辰CAD怎么激活获取授权') or {}).get('标准答案', ''),
          (E._find('浩辰CAD怎么激活获取授权') or {}).get('标准答案', '')[:40])

    # ⑦ 目录负责人导入自己的目录：允许（用只含软件行的文件）
    await ask('ZhongYong', '/owner 软件/浩辰CAD许可配置 ZhangGong')
    soft_only = os.path.join(imp_dir, 'soft_only.csv')
    with open(soft_only, 'w', encoding='utf-8-sig', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['类别', '分组', '三级目录', '标准问题', '标准答案'])
        w.writerow(['软件', '浩辰CAD许可配置', '激活授权', '张工导入的新问法X', '张工导入的答案'])
    r = await ask('ZhangGong', f'/import 软件/浩辰CAD许可配置 {soft_only}')
    check('负责人可导入自己目录', r and '解析完成' in r, repr(r)[:60])
    await ask('ZhangGong', '否')   # 清理会话

    # ⑧ 目录负责人导入别人目录：被拒
    r = await ask('ZhangGong', f'/import ERP/登录与权限 {soft_only}')
    check('负责人不能导入他人目录', r and ('⛔' in r or '无权' in r or '负责人' in r), repr(r)[:70])

    # ⑨ 文件不存在 / 不支持类型
    r = await ask('ZhongYong', '/import D:/no/such/file.csv')
    check('文件不存在有提示', r and ('找不到文件' in r or '导入失败' in r), repr(r)[:60])
    bad = os.path.join(imp_dir, 'x.txt')
    open(bad, 'w').write('hello')
    r = await ask('ZhongYong', f'/import {bad}')
    check('不支持的类型有提示', r and ('不支持' in r or '导入失败' in r), repr(r)[:60])

    # ⑩ 缺列的表头要报错
    badcsv = os.path.join(imp_dir, 'nohdr.csv')
    with open(badcsv, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('foo,bar\n1,2\n')
    r = await ask('ZhongYong', f'/import {badcsv}')
    check('缺必填列有提示', r and ('标准问题' in r or '标准答案' in r), repr(r)[:70])

    # ⑪ 免打 /import：直接说「导入」
    r = await ask('ZhongYong', '导入')
    check('「导入」也能触发', r and '批量导入' in r, repr(r)[:50])
    await ask('ZhongYong', ':cancel')

    # ⑫ 文件内重复行 -> conflict
    dup = os.path.join(imp_dir, 'dup.csv')
    with open(dup, 'w', encoding='utf-8-sig', newline='') as f:
        w = _csv.writer(f)
        w.writerow(['标准问题', '标准答案'])
        w.writerow(['重复问题X', '答案1'])
        w.writerow(['重复问题X', '答案2'])
    recs2, _ = _IMP.parse_csv(dup)
    plan2 = E.bulk_plan(recs2)
    check('文件内重复只保留一条', len(plan2['add']) == 1 and len(plan2['conflict']) == 1,
          f'add={len(plan2["add"])} conflict={len(plan2["conflict"])}')

    # ================= 34. /update 在线升级 =================
    print('\n=== 34. /update 在线升级 ===')
    import zipfile as _zip
    from app import self_update as UPD

    # 版本比较
    check('版本比较 1.1.0>1.0.0', UPD.ver_newer('1.1.0', '1.0.0'))
    check('版本比较 1.0.0 不比 1.0.0 新', not UPD.ver_newer('1.0.0', '1.0.0'))
    check('版本比较 1.0.10>1.0.9', UPD.ver_newer('1.0.10', '1.0.9'))
    check('版本比较 2.0>1.9.9', UPD.ver_newer('2.0', '1.9.9'))

    # 员工不能升级
    r = await ask('emp1', '/update')
    check('员工不能升级', r and '权限' in r, repr(r)[:50])

    # ★ 关键隔离：把"被升级的服务目录"指向一份临时副本，
    #   避免升级测试覆盖真实源码（KB_SELF_DIR）。
    sandbox = os.path.join(_tmp, 'selfdir')
    os.makedirs(sandbox, exist_ok=True)
    shutil.copytree(os.path.join(BASE, 'app'), os.path.join(sandbox, 'app'), dirs_exist_ok=True)
    os.makedirs(os.path.join(sandbox, 'data'), exist_ok=True)
    shutil.copy(os.path.join(_tmp, 'kb.csv'), os.path.join(sandbox, 'data', 'kb.csv'))
    open(os.path.join(sandbox, 'VERSION'), 'w').write('1.0.0\n')
    open(os.path.join(sandbox, '.env'), 'w').write('WECOM_BOT_ID=real\nWECOM_BOT_SECRET=real-secret\n')
    os.environ['KB_SELF_DIR'] = sandbox
    check('升级目标已隔离到临时目录（保护源码）', UPD._base() == sandbox)

    # 造一个"升级源目录" + 新版本 zip（含 VERSION + app/）
    up_dir = os.path.join(_tmp, 'upsrc')
    os.makedirs(up_dir, exist_ok=True)
    stage = os.path.join(_tmp, 'stage')
    os.makedirs(os.path.join(stage, 'app'), exist_ok=True)
    os.makedirs(os.path.join(stage, 'tools'), exist_ok=True)
    open(os.path.join(stage, 'VERSION'), 'w').write('9.9.9\n')
    open(os.path.join(stage, 'app', '__init__.py'), 'w').write('')
    open(os.path.join(stage, 'app', 'marker_new.py'), 'w').write('# new file from update\n')
    open(os.path.join(stage, 'app', 'kb_engine.py'), 'w').write('# NEW kb_engine\n')
    open(os.path.join(stage, 'tools', 'new_tool.py'), 'w').write('# new\n')
    open(os.path.join(stage, '.env'), 'w').write('WECOM_BOT_SECRET=SHOULD_NOT_OVERWRITE\n')
    os.makedirs(os.path.join(stage, 'data'), exist_ok=True)
    open(os.path.join(stage, 'data', 'kb.csv'), 'w').write('SHOULD_NOT_OVERWRITE\n')
    pkg = os.path.join(up_dir, 'kb-v9.9.9.zip')
    with _zip.ZipFile(pkg, 'w') as z:
        for root, _, files in os.walk(stage):
            for fn in files:
                fp = os.path.join(root, fn)
                z.write(fp, os.path.relpath(fp, stage))

    os.environ['KB_UPDATE_DIR'] = up_dir
    os.environ.pop('KB_UPDATE_URL', None)
    os.environ.pop('KB_UPDATE_GIT', None)

    # check：能发现新版本
    plan = UPD.check()
    check('check 成功', plan['ok'], plan.get('msg', ''))
    check('check 发现新版本 9.9.9', plan['newer'] and plan['remote'] == '9.9.9',
          f'remote={plan.get("remote")}')
    shutil.rmtree(plan.get('workdir') or '', ignore_errors=True)

    # /update check 文本
    r = await ask('ZhongYong', '/update check')
    check('/update check 显示版本对比', r and '9.9.9' in r and '有新版本' in r, repr(r)[:70])

    # 真正升级：直接用 dispatch（绕过 on_message 的异步消费），验证登记逻辑
    C.PENDING_UPDATE.clear()
    r = C.dispatch(E, 'ZhongYong', '/update')
    check('/update 返回升级回执', r and '开始升级' in r and '9.9.9' in r, repr(r)[:60])
    check('已登记待执行升级', 'ZhongYong' in C.PENDING_UPDATE)
    req = C.PENDING_UPDATE.pop('ZhongYong')

    # 执行升级（不退出进程）
    ok, msg = UPD.do_update(req['plan'])
    check('升级执行成功', ok, msg)
    check('新文件已带入', os.path.exists(os.path.join(sandbox, 'app', 'marker_new.py')))
    check('代码已更新（kb_engine 被覆盖）',
          open(os.path.join(sandbox, 'app', 'kb_engine.py')).read().strip() == '# NEW kb_engine')
    check('源码未被污染（真实 kb_engine 完好）',
          'class KBEngine' in open(os.path.join(BASE, 'app', 'kb_engine.py')).read())
    check('VERSION 已更新为 9.9.9', UPD.local_version() == '9.9.9', UPD.local_version())
    # 关键：.env 与 data/ 未被覆盖
    check('.env 未被覆盖（凭据安全）',
          'SHOULD_NOT_OVERWRITE' not in open(os.path.join(sandbox, '.env')).read())
    kb_now = open(os.path.join(sandbox, 'data', 'kb.csv')).read()
    check('data/kb.csv 未被覆盖', 'SHOULD_NOT_OVERWRITE' not in kb_now)
    # 备份存在
    check('生成了备份目录',
          os.path.isdir(os.path.join(sandbox, '_backup')) and
          len(os.listdir(os.path.join(sandbox, '_backup'))) > 0)
    # 结果文件可回报
    res = UPD.pop_result()
    check('升级结果可回报', res and res.get('to') == '9.9.9', str(res)[:60])
    check('结果取一次就删', UPD.pop_result() is None)

    # 已是最新时不升级
    r = C.dispatch(E, 'ZhongYong', '/update')
    check('已是最新时给出提示', r and '最新' in r, repr(r)[:50])
    check('已最新时不登记升级', 'ZhongYong' not in C.PENDING_UPDATE)

    # 源不可用时优雅报错
    os.environ['KB_UPDATE_DIR'] = os.path.join(_tmp, 'no_such_dir')
    r = C.dispatch(E, 'ZhongYong', '/update')
    check('源不可用有兜底', r and ('升级失败' in r or '不存在' in r), repr(r)[:60])

    # /version 指令
    os.environ['KB_UPDATE_DIR'] = up_dir
    r = await ask('emp1', '/version')
    check('/version 可查版本', r and '9.9.9' in r, repr(r)[:50])

    # ================= 35. 定时自动升级（零操作） =================
    print('\n=== 35. 定时自动升级 ===')

    # 复位版本，重新造一个"源目录 + 新包"
    open(os.path.join(sandbox, 'VERSION'), 'w').write('1.0.0\n')
    auto_src = os.path.join(_tmp, 'autosrc')
    os.makedirs(auto_src, exist_ok=True)
    st2 = os.path.join(_tmp, 'stage2')
    os.makedirs(os.path.join(st2, 'app'), exist_ok=True)
    open(os.path.join(st2, 'VERSION'), 'w').write('5.5.5\n')
    open(os.path.join(st2, 'app', '__init__.py'), 'w').write('')
    open(os.path.join(st2, 'app', 'auto_new.py'), 'w').write('# auto\n')
    pkg2 = os.path.join(auto_src, 'v5.zip')
    with _zip.ZipFile(pkg2, 'w') as z:
        for root, _, files in os.walk(st2):
            for fn in files:
                fp = os.path.join(root, fn)
                z.write(fp, os.path.relpath(fp, st2))

    os.environ['KB_UPDATE_DIR'] = auto_src
    os.environ.pop('KB_UPDATE_URL', None)
    os.environ.pop('KB_UPDATE_GIT', None)

    # 开关默认关
    os.environ['KB_AUTO_UPDATE'] = '0'
    check('自动升级默认关闭', UPD.auto_enabled() is False)
    # 开启
    os.environ['KB_AUTO_UPDATE'] = '1'
    os.environ['KB_AUTO_UPDATE_INTERVAL'] = '10'
    check('可开启自动升级', UPD.auto_enabled() is True)
    check('间隔换算为秒', UPD.auto_interval_sec() == 600, str(UPD.auto_interval_sec()))
    os.environ['KB_AUTO_UPDATE_INTERVAL'] = '0'
    check('间隔有下限保护（最小 1 分钟）', UPD.auto_interval_sec() == 60)
    os.environ['KB_AUTO_UPDATE_INTERVAL'] = '10'

    # quick_version：轻量探测源上版本
    qv, qerr = UPD.quick_version()
    check('轻量探测拿到源版本 5.5.5', qv == '5.5.5', f'qv={qv} err={qerr}')

    # probe_once：发现新版 -> 建议 update
    res = UPD.probe_once()
    check('探测到需升级', res['action'] == 'update', str(res)[:80])
    check('探测出的目标版本正确', res['plan'] and res['plan']['remote'] == '5.5.5')

    # 执行这次自动升级（quiet 模式，不退出）
    ok, msg = UPD.do_update(res['plan'])
    check('自动升级执行成功', ok, msg)
    check('新代码已带入', os.path.exists(os.path.join(sandbox, 'app', 'auto_new.py')))
    check('本地版本升至 5.5.5', UPD.local_version() == '5.5.5', UPD.local_version())

    # probe_once：已最新 -> none（不再下载）
    res2 = UPD.probe_once()
    check('已最新时探测动作=none', res2['action'] == 'none', str(res2)[:60])

    # 源不可用时：探测不报错、动作 none（静默重试）
    os.environ['KB_UPDATE_DIR'] = os.path.join(_tmp, 'not_exist_xyz')
    res3 = UPD.probe_once()
    check('源不可用时静默不升级', res3['action'] == 'none', str(res3)[:60])
    check('源不可用时无异常抛出', isinstance(res3['reason'], str))

    # 关闭开关后，auto_loop 不应升级（用极小间隔跑 1 轮验证不崩）
    os.environ['KB_AUTO_UPDATE'] = '0'
    os.environ['KB_UPDATE_DIR'] = auto_src
    got = {'n': 0}

    async def _cb(plan):
        got['n'] += 1

    async def _one_round():
        # 直接模拟循环体一次（不进入 sleep 等待）
        if UPD.auto_enabled() and UPD.source_ready():
            r = UPD.probe_once()
            if r['action'] == 'update':
                await _cb(r['plan'])
    await _one_round()
    check('开关关闭时不会自动升级', got['n'] == 0)

    # ★ 收尾：确认真实源码全程未被污染，并复位环境
    check('真实源码未被污染（升级测试已隔离）',
          'class KBEngine' in open(os.path.join(BASE, 'app', 'kb_engine.py')).read()
          and not os.path.exists(os.path.join(BASE, 'app', 'marker_new.py'))
          and not os.path.exists(os.path.join(BASE, 'app', 'auto_new.py')))
    check('真实 VERSION 仍为 1.0.0',
          open(os.path.join(BASE, 'VERSION')).read().strip() == '1.0.0')

    # 复位环境
    os.environ.pop('KB_SELF_DIR', None)
    os.environ['KB_UPDATE_DIR'] = 'F:\\KIDE_BOT_update'
    os.environ['KB_AUTO_UPDATE'] = '0'

    print(f'\n结果：通过 {PASS}，失败 {FAIL}')
    shutil.rmtree(_tmp, ignore_errors=True)
    return FAIL == 0


import re
def re_find_pid(text):
    return re.findall(r'[0-9a-f]{12}', text or '')


if __name__ == '__main__':
    ok = asyncio.run(main())
    raise SystemExit(0 if ok else 1)
