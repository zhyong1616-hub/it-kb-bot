# -*- coding: utf-8 -*-
"""
知识库引擎：负责
1. 加载知识库 CSV（问题/相似问法/答案/关键词/处理人）
2. 问题匹配（命中即返回答案）
3. "他人更新需钟勇逐条审批"的工作流（提交 -> 待审 -> 通过/驳回 -> 落库）
数据全部落地为本地 JSON + CSV，便于查看与备份。
"""
import csv
import json
import os
import re
import time
import uuid
from difflib import SequenceMatcher

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('KB_DATA_DIR') or os.path.join(BASE, 'data')
KB_CSV = os.path.join(DATA_DIR, 'kb.csv')
PENDING_JSON = os.path.join(DATA_DIR, 'pending.json')
AUDIT_JSON = os.path.join(DATA_DIR, 'audit.json')
# 未命中问题（"待学习清单"）：员工问了但知识库答不上来的问题，去重累计频次
UNMATCHED_JSON = os.path.join(DATA_DIR, 'unmatched.json')
# 目录负责人配置：{目录路径: 负责人userid}
OWNERS_JSON = os.path.join(DATA_DIR, 'owners.json')

# 三级目录字段名（一级=类别，二级=分组，三级=三级目录）
CATEGORY_FIELD = '类别'
GROUP_FIELD = '分组'
SUB_FIELD = '三级目录'

# 未命中问题累计到多少条时，主动提醒维护人（0 表示不提醒）
LEARN_REMIND_AT = int(os.environ.get('KB_LEARN_REMIND_AT', '5'))

# 维护人：
#   MAINTAINER_ID   —— 用于精确匹配"谁在操作/审批"（企业微信 userid）
#   MAINTAINER_NAME —— 用于所有对外话术里显示的称呼
MAINTAINER_ID = os.environ.get('KB_MAINTAINER_ID', 'ZhongYong')
MAINTAINER_NAME = os.environ.get('KB_MAINTAINER_NAME', '钟勇')
# 兼容旧变量
MAINTAINER = MAINTAINER_NAME

CSV_HEADER = ['id', '类别', '分组', '三级目录', '标准问题', '相似问法', '标准答案',
              '触发关键词', '处理人', '升级路径', '更新时间']


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        try:
            return json.load(f)
        except Exception:
            return default


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


class KBEngine:
    def __init__(self):
        self.items = []          # list[dict]
        self._kb_mtime = 0.0     # kb.csv 最近一次加载时的修改时间
        self.load()

    # ---------- 读取 ----------
    def _csv_mtime(self):
        try:
            return os.path.getmtime(KB_CSV)
        except OSError:
            return 0.0

    def load(self, force=False):
        """读取 kb.csv 到内存。

        热更新：若文件修改时间比上次加载新（或 force=True），才真正重读；
        否则直接复用内存，避免每次问答都读盘。
        返回 True 表示本次确实重新加载了。
        """
        mt = self._csv_mtime()
        if not force and mt and mt == self._kb_mtime and self.items:
            return False
        self.items = []
        if not os.path.exists(KB_CSV):
            self._kb_mtime = 0.0
            return True
        with open(KB_CSV, encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row.get('标准问题', '').strip():
                    self.items.append(row)
        self._kb_mtime = mt
        return True

    def reload_if_changed(self):
        """按需热更新：文件被外部改过（如用户直接编辑 / 覆盖安装）时自动重载。

        在每次问答/列表前调用，代价极低（一次 stat），保证"改了文件就生效"。
        返回 True 表示检测到变更并重新加载。
        """
        changed = self.load()
        if changed:
            print(f'[热更新] 检测到 kb.csv 变更，已重新加载（{len(self.items)} 条）')
        return changed

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(KB_CSV, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADER)
            w.writeheader()
            for it in self.items:
                w.writerow({k: it.get(k, '') for k in CSV_HEADER})
        # 自己写盘后同步 mtime，避免下一次误判为"外部变更"
        self._kb_mtime = self._csv_mtime()

    # ---------- 匹配 ----------
    @staticmethod
    def _norm(s):
        return re.sub(r'[\s,，。！!？?、；;：:·\-_/\\（）()\[\]【】"]+', '', str(s)).lower()

    # 常见"动作/意图"词：用于判断意图是否一致，避免"安装"命中"卸载"
    _INTENT_PAIRS = [
        ('卸载', '删除', '清除', '移除'),
        ('安装', '部署', 'setup', '重装'),
        ('激活', '授权', '获取授权'),
        ('申请', '开通', '新增'),
        ('登录', '登陆', '进不去'),
        ('导出', '下载', '另存'),
    ]

    @classmethod
    def _intent(cls, s):
        """返回文本包含的意图集合（如 {'卸载'}）"""
        s = cls._norm(s)
        found = set()
        for group in cls._INTENT_PAIRS:
            for w in group:
                if w in s:
                    found.add(group[0])   # 用组首词作代表
        return found

    # 高频"问题/动作"泛词，不参与主题判断（否则"连不上/打不出来"会互相误命中）
    _STOP = set('怎么 如何 什么 为什么 哪里 在哪 可以 能否 是不是 请问 一下 我的 我 了 的 是 有 无 '
                '连不上 打不出来 用不了 打不开 没反应 失灵 不了 无法 不能 不行 报错 故障 问题 '
                '求解 求助 帮忙 谢谢 呢 吗 啊 呀 安装 卸载 删除 激活 申请 登录 导出 设置 配置 '
                '解决方法 办法 方法 步骤 教程'.split())

    @classmethod
    def _keywords(cls, s):
        """提取"主题词"：去掉泛词后的有意义片段（中文按2字滑窗 + 英文/数字串）"""
        s = cls._norm(s)
        toks = set()
        # 英文/数字串
        for m in re.findall(r'[a-z0-9]{2,}', s):
            toks.add(m)
        # 中文按 2 字滑窗
        zh = re.sub(r'[a-z0-9]+', '', s)
        for i in range(len(zh) - 1):
            toks.add(zh[i:i + 2])
        # 去掉泛词构成的片段
        for w in cls._STOP:
            toks.discard(w)
        return toks

    @classmethod
    def _topic_overlap(cls, a, b):
        """主题词重合度：0~1。用于判断两条是否在说"同一件事" """
        ka, kb = cls._keywords(a), cls._keywords(b)
        if not ka or not kb:
            return 0.0
        inter = ka & kb
        return len(inter) / min(len(ka), len(kb))

    # 常见"主题名"：出现在 query 里就必须优先匹配同主题，防止跨主题误命中
    # 主题词表（用于主题否决 / 主题重合度）。
    # 注意：'欧特克'/'acad' 必须与 'autocad' 一同列出，否则会被当成两个不同主题，
    #       导致「欧特克CAD」查不到 AutoCAD 条目。
    _TOPICS = ['打印机', '绘图仪', '扫描仪', '投影仪', '复印机',
               '浩辰', 'gstarcad', 'cad', 'autocad', 'acad', '欧特克', 'autodesk', 'revit',
               'sketchup', 'su',
               '火绒', 'erp', '邮箱', 'outlook', 'office', 'wps',
               '键盘', '鼠标', '显示器', '电脑', '主机', '网络', 'wifi', 'vpn']

    # 同义词归一：把不同叫法折叠成同一个主题标签，避免"同物不同名"被判为不同主题。
    _TOPIC_SYNONYM = {
        'acad': 'autocad', '欧特克': 'autodesk', 'autodeskcad': 'autodesk',
        'gstarcad': '浩辰', 'su': 'sketchup',
    }

    @classmethod
    def _topics_of(cls, s):
        s = cls._norm(s)
        raw = {t for t in cls._TOPICS if t in s}
        # 归一化同义词（acad -> autocad，欧特克 -> autodesk …）
        return {cls._TOPIC_SYNONYM.get(t, t) for t in raw}

    def match(self, query, threshold=0.62):
        """返回 (item, score) 或 (None, best_score)

        评分策略（按可信度）：
          1) 标准问题/相似问法 完全相等           -> 1.0
          2) 互为子串，且**意图一致**              -> 0.80~0.95（按长度比）
          3) 序列相似度（difflib）
          4) 触发关键词：仅作加分，要求长关键词
        额外约束：
          - 意图冲突（卸载 vs 安装）→ 重罚
          - query 含明确主题词（打印机/浩辰…）而条目主题完全不同 → 否决
        """
        q = self._norm(query)
        if not q:
            return None, 0.0
        q_intent = self._intent(q)
        q_topics = self._topics_of(q)

        best, best_score = None, 0.0
        for it in self.items:
            it_text = ' '.join(str(v) for v in it.values())
            it_topics = self._topics_of(it_text)

            # 主题否决：query 有明确主题，但这条完全不属于该主题
            if q_topics and not (q_topics & it_topics):
                continue

            cands = [it.get('标准问题', '')]
            cands += [p for p in re.split(r'##|\n', it.get('相似问法', '')) if p.strip()]

            item_best = 0.0
            for c in cands:
                cn = self._norm(c)
                if not cn:
                    continue
                c_intent = self._intent(c)

                if cn == q:
                    score = 1.0
                elif cn in q or q in cn:
                    ratio = min(len(cn), len(q)) / max(len(cn), len(q))
                    score = 0.80 + 0.15 * ratio
                    if q_intent and c_intent and not (q_intent & c_intent):
                        score = 0.30
                    else:
                        ov = self._topic_overlap(q, c)
                        if ov < 0.34:
                            score *= 0.70        # 主题重合太少 → 打折
                else:
                    score = SequenceMatcher(None, q, cn).ratio()
                    if q_intent and c_intent and not (q_intent & c_intent):
                        score = min(score, 0.35)
                item_best = max(item_best, score)

            kws = [self._norm(p) for p in re.split(r'[、,，]', it.get('触发关键词', '')) if p.strip()]
            for kn in kws:
                if len(kn) >= 3 and kn in q:
                    item_best = min(0.99, max(item_best, 0.70 + 0.02 * len(kn)))

            if item_best > best_score:
                best, best_score = it, item_best

        if best_score >= threshold:
            return best, best_score
        return None, best_score

    # ---------- 审批工作流 ----------
    def submit_change(self, action, payload, submitter):
        """
        action: 'add' | 'edit' | 'delete'
        payload: 对于 edit/delete 需含 qid；对于 add 需含问题/答案等
        返回 pending 记录
        """
        pending = _load_json(PENDING_JSON, [])
        rec = {
            'pid': uuid.uuid4().hex[:12],
            'action': action,
            'payload': payload,
            'submitter': submitter,
            'status': 'pending',          # pending / approved / rejected
            'created_at': _now(),
            'decided_at': '',
            'decided_by': '',
            'reason': '',
        }
        pending.append(rec)
        _save_json(PENDING_JSON, pending)
        self._audit('submit', rec)
        return rec

    def list_pending(self, only_pending=True):
        pending = _load_json(PENDING_JSON, [])
        if only_pending:
            return [r for r in pending if r['status'] == 'pending']
        return pending

    def decide(self, pid, approve, decider, reason=''):
        """钟勇审批。approve=True 时真正落库。"""
        pending = _load_json(PENDING_JSON, [])
        rec = next((r for r in pending if r['pid'] == pid), None)
        if not rec:
            return None, '未找到该审批单'
        if rec['status'] != 'pending':
            return None, f"该单已处理（{rec['status']}），不可重复操作"

        rec['decided_at'] = _now()
        rec['decided_by'] = decider
        rec['reason'] = reason

        if approve:
            ok, msg = self._apply(rec)
            if not ok:
                rec['status'] = 'pending'
                _save_json(PENDING_JSON, pending)
                return None, msg
            rec['status'] = 'approved'
            # 自动学习闭环：这条问题已补进知识库 -> 从未命中清单移除
            try:
                self.remove_unmatched(self._norm(rec.get('payload', {}).get('标准问题', '')))
            except Exception:
                pass
        else:
            rec['status'] = 'rejected'

        _save_json(PENDING_JSON, pending)
        self._audit('approve' if approve else 'reject', rec)
        return rec, 'ok'

    def _apply(self, rec):
        """把审批通过的变更真正写入知识库"""
        act = rec['action']
        p = rec['payload']
        if act == 'add':
            q = (p.get('标准问题') or '').strip()
            a = (p.get('标准答案') or '').strip()
            if not q or not a:
                return False, '新增需同时填写「标准问题」和「标准答案」'
            if any(self._norm(it['标准问题']) == self._norm(q) for it in self.items):
                return False, '已存在相同标准问题，请改用"编辑"'
            self.items.append({
                'id': uuid.uuid4().hex[:8],
                '类别': p.get('类别', ''), '分组': p.get('分组', ''),
                '三级目录': p.get('三级目录', ''),
                '标准问题': q, '相似问法': p.get('相似问法', ''),
                '标准答案': a, '触发关键词': p.get('触发关键词', ''),
                '处理人': MAINTAINER_NAME, '升级路径': p.get('升级路径', ''),
                '更新时间': _now(),
            })
        elif act == 'edit':
            it = self._find(p.get('qid') or p.get('标准问题'))
            if not it:
                return False, '待编辑条目不存在'
            for k in ['类别', '分组', '三级目录', '标准问题', '相似问法', '标准答案',
                      '触发关键词', '升级路径']:
                if k in p and p[k] != '':
                    it[k] = p[k]
            it['更新时间'] = _now()
        elif act == 'delete':
            it = self._find(p.get('qid') or p.get('标准问题'))
            if not it:
                return False, '待删除条目不存在'
            self.items.remove(it)
        else:
            return False, f'未知操作 {act}'
        self.save()
        return True, 'ok'

    def _find(self, key):
        if not key:
            return None
        for it in self.items:
            if it.get('id') == key or self._norm(it.get('标准问题', '')) == self._norm(key):
                return it
        return None

    # ---------- 批量导入：差异比对 ----------
    def bulk_plan(self, records):
        """把导入记录与现有知识库比对，分出「新增」「修改」「无变化」。

        records: [{'标准问题','标准答案','类别','分组','三级目录',...}, ...]
        返回 {'add': [...], 'edit': [{'qid','问题','changes':{...},'原始':rec}, ...],
              'same': [...], 'conflict': [...]}
          - conflict：同一份文件里出现重复的标准问题
        """
        add, edit, same, conflict = [], [], [], []
        seen = {}
        upd_fields = ['类别', '分组', '三级目录', '标准问题', '相似问法',
                      '标准答案', '触发关键词', '升级路径']
        for rec in records:
            q = (rec.get('标准问题') or '').strip()
            k = self._norm(q)
            if k in seen:
                conflict.append(rec)
                continue
            seen[k] = True
            it = self._find(q)
            if not it:
                # 精确没找到时，用模糊匹配兜底：
                # 导入文件里的问题名常与库里略有差异（多字/少字/同义），
                # 若模型判定是同一问法（高分），按「修改」处理，避免重复新增。
                cand, score = self.match(q, threshold=0.80)
                if cand:
                    it = cand
            if not it:
                add.append(rec)
                continue
            changes = {}
            for f in upd_fields:
                new_v = (rec.get(f) or '').strip()
                if new_v and new_v != (it.get(f) or '').strip():
                    changes[f] = new_v
            if changes:
                edit.append({'qid': it['id'], '问题': it['标准问题'],
                             'changes': changes, '原始': rec})
            else:
                same.append(rec)
        return {'add': add, 'edit': edit, 'same': same, 'conflict': conflict}

    def _audit(self, event, rec):
        logs = _load_json(AUDIT_JSON, [])
        logs.append({'ts': _now(), 'event': event,
                     'pid': rec.get('pid'), 'submitter': rec.get('submitter'),
                     'decider': rec.get('decided_by'), 'action': rec.get('action'),
                     'status': rec.get('status')})
        _save_json(AUDIT_JSON, logs)

    # ---------- 未命中问题（自动学习：待沉淀的知识） ----------
    def record_unmatched(self, query, userid=''):
        """记录一条"答不上来"的提问。

        - 按归一化文本去重，累计 count，更新 last_ts / askers
        - 返回 (item, is_new)：is_new=True 表示这是首次出现的新问题
        - 任何异常都不能影响正常问答
        """
        q = (query or '').strip()
        if not q:
            return None, False
        try:
            key = self._norm(q)
            if not key:
                return None, False
            logs = _load_json(UNMATCHED_JSON, [])
            rec = next((r for r in logs if r.get('key') == key), None)
            if rec:
                rec['count'] = int(rec.get('count', 1)) + 1
                rec['last_ts'] = _now()
                if userid and userid not in rec.get('askers', []):
                    rec.setdefault('askers', []).append(userid)
                is_new = False
            else:
                rec = {
                    'key': key, 'query': q, 'count': 1,
                    'first_ts': _now(), 'last_ts': _now(),
                    'askers': [userid] if userid else [],
                    'ignored': False,
                }
                logs.append(rec)
                is_new = True
            _save_json(UNMATCHED_JSON, logs)
            return rec, is_new
        except Exception as e:
            print(f'[学习] 记录未命中失败（忽略）：{e}')
            return None, False

    def list_unmatched(self, include_ignored=False, limit=20):
        """列出待学习问题，按"提问次数"降序（越多人问越该优先补）。"""
        logs = _load_json(UNMATCHED_JSON, [])
        if not include_ignored:
            logs = [r for r in logs if not r.get('ignored')]
        logs.sort(key=lambda r: (-int(r.get('count', 1)), r.get('last_ts', '')))
        return logs[:limit] if limit else logs

    def unmatched_count(self):
        """待学习问题条数（不含已忽略）。"""
        return len([r for r in _load_json(UNMATCHED_JSON, []) if not r.get('ignored')])

    def get_unmatched(self, idx_or_key):
        """按序号（1-based，对应 list_unmatched 的顺序）或归一化 key 取一条。"""
        items = self.list_unmatched(include_ignored=True, limit=0)
        s = str(idx_or_key).strip().lstrip('#')
        if s.isdigit():
            i = int(s) - 1
            # 序号基于"未忽略"列表，避免忽略的条目占号
            visible = [r for r in items if not r.get('ignored')]
            if 0 <= i < len(visible):
                return visible[i]
            return None
        return next((r for r in items if r.get('key') == self._norm(s)), None)

    def ignore_unmatched(self, idx_or_key):
        """忽略某条（不再提醒，但记录仍在，便于回溯）。"""
        rec = self.get_unmatched(idx_or_key)
        if not rec:
            return None
        logs = _load_json(UNMATCHED_JSON, [])
        for r in logs:
            if r.get('key') == rec.get('key'):
                r['ignored'] = True
        _save_json(UNMATCHED_JSON, logs)
        return rec

    def remove_unmatched(self, key):
        """把某条从未命中清单里彻底移除（一般是"已补进知识库"后调用）。"""
        logs = _load_json(UNMATCHED_JSON, [])
        before = len(logs)
        logs = [r for r in logs if r.get('key') != key]
        if len(logs) != before:
            _save_json(UNMATCHED_JSON, logs)
            return True
        return False

    # ---------- 统计 ----------
    def stats(self):
        from collections import Counter
        return {
            'total': len(self.items),
            'by_group': dict(Counter(it.get('分组', '') for it in self.items)),
            'by_cat': dict(Counter(it.get('类别', '') for it in self.items)),
            'by_sub': dict(Counter(it.get(SUB_FIELD, '') for it in self.items)),
            'pending': len(self.list_pending()),
            'unmatched': self.unmatched_count(),
        }

    # ================= 三级目录 & 目录负责人 =================

    # ---- 负责人配置读写 ----
    def load_owners(self):
        """读取目录负责人配置，返回 {目录路径: userid}"""
        raw = _load_json(OWNERS_JSON, {})
        owners = raw.get('owners') if isinstance(raw, dict) else None
        if not isinstance(owners, dict):
            owners = raw if isinstance(raw, dict) else {}
        return {str(k): str(v) for k, v in owners.items() if v}

    def save_owners(self, owners):
        """写回目录负责人配置（保留 _说明 等元信息）。"""
        raw = _load_json(OWNERS_JSON, {})
        if not isinstance(raw, dict) or 'owners' not in raw:
            raw = {
                '_说明': '目录负责人配置。key=目录路径（用 / 分隔，支持 一级 / 一级/二级 / 一级/二级/三级），'
                       'value=负责人的企业微信 userid。',
                '_优先级': '越具体的路径优先级越高；三级 > 二级 > 一级。'
                         f'{MAINTAINER_NAME}({MAINTAINER_ID}) 天然拥有全部目录的最高权限。',
                'owners': {},
            }
        raw['owners'] = owners
        _save_json(OWNERS_JSON, raw)

    # ---- 路径工具 ----
    @staticmethod
    def norm_path(path):
        """把用户输入的目录路径规整为 '一级/二级/三级' 形式。"""
        if not path:
            return ''
        s = str(path).strip().strip('/').strip('／')
        s = re.sub(r'[/／>》\-—>]+', '/', s)
        s = re.sub(r'/+', '/', s)
        parts = [p.strip() for p in s.split('/') if p.strip()]
        return '/'.join(parts)

    @classmethod
    def dir_path(cls, item, depth=None):
        """取某条目的目录路径：类别/分组/三级目录（跳过空层级）。

        depth 可指定只取前 N 级（1=类别，2=类别/分组，3=完整）。
        """
        parts = [(item.get(CATEGORY_FIELD) or '').strip(),
                 (item.get(GROUP_FIELD) or '').strip(),
                 (item.get(SUB_FIELD) or '').strip()]
        parts = [p for p in parts if p]
        if depth:
            parts = parts[:depth]
        return '/'.join(parts)

    # ---- 目录树 ----
    def build_tree(self, max_depth=3):
        """构建三级目录树。

        返回嵌套结构（list[dict]），每个节点：
          {name, path, count, owner, children[], is_owner_me?}
        子节点按名称排序；owner 为该节点的"有效负责人"（继承自上级）。
        """
        owners = self.load_owners()

        def make(name, path, depth):
            children = []
            if depth < max_depth:
                # 收集下一级候选名
                names = []
                for it in self.items:
                    p = self.dir_path(it).split('/') if self.dir_path(it) else []
                    if len(p) > depth and '/'.join(p[:depth]) == path:
                        if p[depth] not in names:
                            names.append(p[depth])
                for nm in sorted(names):
                    children.append(make(nm, f'{path}/{nm}' if path else nm, depth + 1))
            # 节点自身条目数 = 恰好停在当前深度的条目（不含子目录）
            self_count = len(self.items_in_dir(path, exact_depth=depth)) if path else 0
            # 汇总条目数 = 本节点 + 所有子节点
            total = self_count + sum(c['total'] for c in children)
            node = {
                'name': name,
                'path': path,
                'depth': depth,
                'count': self_count,     # 直接挂在当前目录的条目
                'total': total,          # 含子目录的全部条目
                'owner': self.owner_of(path, owners=owners),
                'children': children,
            }
            return node

        roots = []
        cats = []
        for it in self.items:
            c = (it.get(CATEGORY_FIELD) or '').strip()
            if c and c not in cats:
                cats.append(c)
        for c in sorted(cats):
            roots.append(make(c, c, 1))
        # 兜底：没有归属类别（或类别为空）的条目，单独挂到「未分类」下，避免"看得见却不在树里"
        uncat = [it for it in self.items if not (it.get(CATEGORY_FIELD) or '').strip()]
        if uncat:
            roots.append({
                'name': '未分类',
                'path': '未分类',
                'depth': 1,
                'count': len(uncat),
                'total': len(uncat),
                'owner': MAINTAINER_ID,
                'children': [],
            })
        return roots

    def items_in_dir(self, path, exact_depth=None):
        """按目录路径过滤条目。

        支持三种粒度（前缀匹配）：
          '软件'                         -> 该一级目录下所有条目
          '软件/浩辰CAD许可配置'          -> 该二级目录下所有条目
          '软件/浩辰CAD许可配置/激活授权'  -> 该三级目录下所有条目
        exact_depth 指定时改为"恰好这一层"的匹配（用于统计节点自身条目数）。
        """
        p = self.norm_path(path)
        if not p:
            return list(self.items)
        want = p.split('/')
        out = []
        for it in self.items:
            parts = self.dir_path(it).split('/') if self.dir_path(it) else []
            if exact_depth is not None:
                if len(parts) == exact_depth and '/'.join(parts[:exact_depth]) == p:
                    out.append(it)
            else:
                if parts[:len(want)] == want:
                    out.append(it)
        return out

    # ---- 负责人 ----
    def owner_of(self, path, owners=None):
        """查某目录的负责人：越具体的路径优先（三级 > 二级 > 一级）。

        若该目录本身没有配置负责人，则向上级回溯；都没有则返回默认维护人。
        """
        if owners is None:
            owners = self.load_owners()
        p = self.norm_path(path)
        if not p:
            return MAINTAINER_ID
        parts = p.split('/')
        for i in range(len(parts), 0, -1):
            key = '/'.join(parts[:i])
            o = owners.get(key)
            if o:
                return o
        return MAINTAINER_ID

    def owner_name_of(self, path):
        """负责人显示名（钟勇显示为中文名，其他人显示 userid）。"""
        o = self.owner_of(path)
        return MAINTAINER_NAME if o == MAINTAINER_ID else o

    def set_owner(self, path, userid):
        """设置某目录的负责人。返回 (ok, msg)。"""
        p = self.norm_path(path)
        if not p:
            return False, '目录路径不能为空'
        uid = (userid or '').strip()
        if not uid:
            return False, '负责人不能为空'
        owners = self.load_owners()
        old = owners.get(p)
        owners[p] = uid
        self.save_owners(owners)
        return True, (f'已把目录「{p}」的负责人从 {old or "（无）"} 改为 {uid}'
                      if old else f'已设置目录「{p}」的负责人为 {uid}')

    def can_edit(self, userid, item_or_path):
        """判断某用户是否有权修改某条目/某目录下的知识。

        规则（"只管自己目录，到自己为止"）：
          - 钟勇(MAINTAINER_ID)：全权 -> True
          - 其它人：仅当他是该条目所属目录（或其上级目录）的负责人 -> True
        """
        if userid == MAINTAINER_ID:
            return True
        if isinstance(item_or_path, (dict,)):
            path = self.dir_path(item_or_path)
        else:
            path = self.norm_path(item_or_path)
        if not path:
            return False
        owners = self.load_owners()
        parts = path.split('/')
        # 该路径自身或其任意上级目录的负责人是谁
        for i in range(1, len(parts) + 1):
            key = '/'.join(parts[:i])
            if owners.get(key) == userid:
                return True
        return False

    def dirs_of_user(self, userid):
        """列出某人负责的所有目录路径（用于提示/校验）。"""
        if userid == MAINTAINER_ID:
            return ['*']
        return [k for k, v in self.load_owners().items() if v == userid]

    def dir_path_target(self, rec):
        """取一条待审单（pending 记录）的归属目录路径。

        - add：payload 里直接带 类别/分组/三级目录
        - edit/delete：按 qid/标准问题 反查原条目
        """
        p = rec.get('payload') or {}
        path = '/'.join([x for x in [(p.get(CATEGORY_FIELD) or '').strip(),
                                     (p.get(GROUP_FIELD) or '').strip(),
                                     (p.get(SUB_FIELD) or '').strip()] if x])
        if not path:
            item = self._find(p.get('qid') or p.get('标准问题'))
            if item:
                path = self.dir_path(item)
        return path or '未分类'
