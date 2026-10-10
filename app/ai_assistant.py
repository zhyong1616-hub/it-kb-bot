# -*- coding: utf-8 -*-
"""
AI 维护助手：把"自然语言"变成"知识库操作"。

设计原则（重要）：
1. **只理解 + 生成，不直接落库**：AI 输出的结果会先给用户看预览，
   用户点【确认】才写入知识库。防止模型幻觉污染知识库。
2. **可降级**：未配置 AI（AI_ENABLED=0 或无 Key）时，自动回退到
   固定指令格式（/改 关键词 | 新内容），功能不失效。
3. **只依赖标准库**：用 urllib 直接调 OpenAI 兼容的 /chat/completions，
   不引入额外依赖（服务器上零安装成本）。

支持的接口（任何 OpenAI 兼容服务都行）：
  - 阿里云百炼： https://dashscope.aliyuncs.com/compatible-mode/v1
  - DeepSeek：   https://api.deepseek.com/v1
  - 公司内部网关： https://your-gateway/v1
"""
import json
import os
import re
import urllib.error
import urllib.request

# ---------- 配置（全部来自环境变量 / .env） ----------
# 注意：这些值在「每次调用」时实时读取（见 _conf()），而不是在 import 时定死，
# 因为 .env 是在 main.py 里后加载的、且运行时可能被改。避免 import 顺序陷阱。

# 知识库允许的操作
ACTIONS = ('add', 'edit', 'delete', 'ask')


def _conf():
    """实时读取配置（每次调用都读，避免 .env 加载顺序问题）。"""
    return {
        'enabled': os.environ.get('AI_ENABLED', '0') == '1',
        'base_url': os.environ.get('AI_BASE_URL', '').rstrip('/'),
        'api_key': os.environ.get('AI_API_KEY', ''),
        'model': os.environ.get('AI_MODEL', 'qwen-plus'),
        'timeout': int(os.environ.get('AI_TIMEOUT', '20') or 20),
    }


def ai_ready():
    """AI 是否已配置就绪。"""
    c = _conf()
    return bool(c['enabled'] and c['base_url'] and c['api_key'])


def _chat(messages, temperature=0.2):
    """调用 OpenAI 兼容的 /chat/completions。返回模型回复文本。

    失败抛异常，由调用方兜底。不依赖 requests，用标准库 urllib。
    """
    c = _conf()
    if not (c['enabled'] and c['base_url'] and c['api_key']):
        raise RuntimeError('AI 未配置（AI_ENABLED=0 或缺 AI_BASE_URL/AI_API_KEY）')
    url = f"{c['base_url']}/chat/completions"
    payload = {
        'model': c['model'],
        'messages': messages,
        'temperature': temperature,
        'stream': False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {c['api_key']}",
        },
    )
    with urllib.request.urlopen(req, timeout=c['timeout']) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    return body['choices'][0]['message']['content']


def _extract_json(text):
    """从模型回复里稳健地抠出第一个 JSON 对象。"""
    if not text:
        return None
    text = text.strip()
    # 去掉 ```json ... ``` 代码围栏
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    # 直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 抠第一个 { ... } 块
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


SYSTEM_PROMPT = """你是公司 IT 知识库的维护助手。用户会用自然语言提出对知识库的修改要求。
你要把用户的话，翻译成一条结构化的知识库操作。

知识库条目字段：
- 标准问题（必填，一句话描述问题）
- 标准答案（必填，解决步骤；多条之间用 ## 分隔）
- 相似问法（可选，多种问法；之间用 ## 分隔）
- 触发关键词（可选，用、分隔）
- 类别 / 分组 / 三级目录（可选，用于归档）

请只输出一个 JSON 对象，不要任何多余文字，格式：
{
  "action": "add" | "edit" | "delete",
  "match": "用于在知识库里定位已有条目的关键词（edit/delete 必填，add 留空）",
  "标准问题": "...",
  "标准答案": "...",
  "相似问法": "...",
  "触发关键词": "...",
  "类别": "...",
  "分组": "...",
  "三级目录": "...",
  "reason": "一句话说明你的理解"
}

规则：
- 用户要"改成/修改/更新/修正/补充"某条 → action=edit，match 填能定位到那条的关键词
- 用户要"新增/加一条/添加" → action=add
- 用户要"删除/去掉" → action=delete
- 答案要写成清晰的分步骤，步骤之间用 ## 分隔；末尾如需转人工统一写"联系钟勇"
- 不确定的字段留空字符串，不要编造
"""


def parse_intent(user_text, engine):
    """把用户自然语言要求解析成结构化操作。

    返回 dict：{'ok':bool, 'action':..., 'payload':{...}, 'match':..., 'reason':...}
    或 {'ok':False, 'error': '...'}
    """
    if not ai_ready():
        return {'ok': False, 'error': 'AI 未配置'}
    # 给模型一点上下文：现有条目的问题列表（便于 edit/delete 精确定位）
    listing = '\n'.join(f'- {it.get("标准问题","")}' for it in engine.items[:200])
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'system', 'content': f'知识库现有条目（供定位用）：\n{listing}'},
        {'role': 'user', 'content': user_text},
    ]
    try:
        reply = _chat(messages)
    except Exception as e:
        return {'ok': False, 'error': f'调用模型失败：{e}'}
    obj = _extract_json(reply)
    if not obj or 'action' not in obj:
        return {'ok': False, 'error': '模型未能理解，请换个说法', 'raw': reply}
    action = str(obj.get('action', '')).strip().lower()
    if action not in ('add', 'edit', 'delete'):
        return {'ok': False, 'error': f'模型返回了不支持的操作：{action}', 'raw': reply}

    payload = {}
    for k in ('标准问题', '标准答案', '相似问法', '触发关键词', '类别', '分组', '三级目录'):
        v = obj.get(k)
        if v:
            payload[k] = str(v).strip()
    return {
        'ok': True,
        'action': action,
        'match': str(obj.get('match') or '').strip(),
        'payload': payload,
        'reason': str(obj.get('reason') or '').strip(),
    }


def polish_answer(user_text, context=''):
    """让模型把一句粗糙的话，润色成规范的标准答案（可选能力）。

    返回润色后的答案文本；失败时原样返回 user_text。
    """
    if not ai_ready():
        return user_text
    sys = ('你是 IT 知识库编辑。把用户给的答案草稿整理成规范、清晰的分步骤说明。'
           '步骤之间用 ## 分隔。只输出整理后的正文，不要任何解释或前后缀。'
           '如需转人工，统一写"联系钟勇"。')
    if context:
        sys += f'\n相关条目：{context}'
    try:
        return _chat([
            {'role': 'system', 'content': sys},
            {'role': 'user', 'content': user_text},
        ], temperature=0.1).strip()
    except Exception:
        return user_text
