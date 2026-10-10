# -*- coding: utf-8 -*-
"""
初始化机器人知识库数据：把上层目录的「03_知识库内容清单_定制版.csv」
转换成服务用的 data/kb.csv（若已存在则跳过，避免覆盖线上改动）。

用法：python tools/init_data.py
"""
import csv
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
KB_CSV = os.path.join(BASE, 'data', 'kb.csv')

# 候选源文件（按优先级）
CANDIDATES = [
    os.path.join(BASE, '..', '03_知识库内容清单_定制版.csv'),
    os.path.join(BASE, 'tools', 'seed_kb.csv'),
]

HEADER = ['id', '类别', '分组', '三级目录', '标准问题', '相似问法', '标准答案',
          '触发关键词', '处理人', '升级路径', '更新时间']

# 源文件里除 id/更新时间 外的列（决定顺序）。新源含「三级目录」，旧源不含。
SRC_COLS_NEW = ['类别', '分组', '三级目录', '标准问题', '相似问法', '标准答案',
                '触发关键词', '处理人', '升级路径']
SRC_COLS_OLD = ['类别', '分组', '标准问题', '相似问法', '标准答案',
                '触发关键词', '处理人', '升级路径']

# 目录负责人默认配置文件（全部先归钟勇，之后用 /owner 改）
OWNERS_JSON = os.path.join(BASE, 'data', 'owners.json')
DEFAULT_OWNERS = {
    '_说明': '目录负责人配置。key=目录路径（用 / 分隔），value=负责人的企业微信 userid。',
    '_优先级': '越具体的路径优先级越高；三级 > 二级 > 一级。钟勇(ZhongYong) 天然拥有全部目录的最高权限。',
    'owners': {
        '软件/浩辰CAD许可配置': 'ZhongYong',
        '软件/AutoCAD与Revit': 'ZhongYong',
        '软件/SketchUp': 'ZhongYong',
        '软件/火绒安全软件': 'ZhongYong',
        '软件/软件通用问题': 'ZhongYong',
        'ERP/ERP登录权限': 'ZhongYong',
        'ERP/ERP项目管理': 'ZhongYong',
        'ERP/ERP查询导出': 'ZhongYong',
        '电脑故障/系统故障': 'ZhongYong',
        '电脑故障/外设网络': 'ZhongYong',
        '电脑故障/常用软件报障': 'ZhongYong',
    },
}


def _ensure_owners():
    """若 owners.json 不存在则写入默认（不覆盖已有配置）。"""
    if os.path.exists(OWNERS_JSON):
        return
    import json
    os.makedirs(os.path.dirname(OWNERS_JSON), exist_ok=True)
    with open(OWNERS_JSON, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_OWNERS, f, ensure_ascii=False, indent=2)


def main():
    _ensure_owners()
    if os.path.exists(KB_CSV):
        with open(KB_CSV, encoding='utf-8-sig', newline='') as f:
            n = sum(1 for r in csv.reader(f) if r and r[0].strip()) - 1
        print(f'    data/kb.csv 已存在（{n} 条），跳过初始化以保护现有改动。')
        return 0

    src = next((p for p in CANDIDATES if p and os.path.exists(p)), None)
    if not src:
        print('    [错误] 找不到源知识库 CSV，请把 03_知识库内容清单_定制版.csv 放到上级目录，')
        print('          或把种子数据放到 tools/seed_kb.csv。')
        return 1

    os.makedirs(os.path.dirname(KB_CSV), exist_ok=True)
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(src, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.reader(f))

    # 判断源文件列数（是否含「三级目录」列）
    src_header = [c.strip() for c in rows[0]] if rows else []
    has_sub = '三级目录' in src_header
    cols = SRC_COLS_NEW if has_sub else SRC_COLS_OLD

    out_rows = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        body = r[:len(cols)]
        if not has_sub:
            # 旧源没有三级目录：在「分组」后插一个空位，保证列对齐
            body = body[:2] + [''] + body[2:]
        body = (body + [''] * len(cols))[:len(cols)]
        out_rows.append([uuid.uuid4().hex[:8]] + body + [now])

    with open(KB_CSV, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(out_rows)

    print(f'    已从 {os.path.relpath(src, BASE)} 导入 {len(out_rows)} 条到 data/kb.csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
