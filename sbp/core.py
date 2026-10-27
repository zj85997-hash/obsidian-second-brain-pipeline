# -*- coding: utf-8 -*-
"""
公共部分：配置、收件箱笔记读写、分类规则、词条替换。只用 Python 标准库。
"""

import datetime
import json
import os
import re

INBOX_KEYS = ('标题', '日期', '来源', 'source', 'source_id')


# ---------------------------------------------------------------- 配置
def expand(path):
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path))) if path else path


def load_config(path):
    """读取 JSON 配置。相对路径以配置文件所在目录为基准。"""
    path = expand(path)
    with open(path, encoding='utf-8') as f:
        cfg = json.load(f)
    base = os.path.dirname(path)
    for key in ('vault', 'inbox', 'state'):
        val = cfg.get(key)
        if val:
            val = os.path.expanduser(os.path.expandvars(val))
            cfg[key] = val if os.path.isabs(val) else os.path.abspath(os.path.join(base, val))
    cfg.setdefault('state', os.path.join(cfg.get('inbox') or base, '.sbp-state'))
    cfg.setdefault('default_line', '90-待归类')
    cfg.setdefault('timezone_offset_hours', 8)
    cfg.setdefault('lines', [])
    cfg.setdefault('rules', [])
    cfg.setdefault('overrides', {})
    cfg.setdefault('replacements', [])
    cfg.setdefault('sources', {})
    cfg['_path'] = path
    return cfg


def tz(cfg):
    return datetime.timezone(datetime.timedelta(hours=cfg.get('timezone_offset_hours', 8)))


# ---------------------------------------------------------------- 文本工具
def sanitize(title, limit=80):
    """把标题变成合法文件名：去掉 \\/:*?"<>| 和换行，压缩空白。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', ' ', title or '')
    name = re.sub(r'\s+', ' ', name).strip().strip('.')
    return name[:limit] or '无标题'


def parse_frontmatter(text):
    """解析文件开头的 --- 块（简单的「键: 值」格式）。返回 (dict, 正文)。"""
    m = re.match(r'^---\n(.*?)\n---\n?', text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, text[m.end():]


def render_frontmatter(fields):
    lines = ['---']
    for k, v in fields.items():
        v = '' if v is None else str(v)
        if k.endswith('_id') or k in ('note_id', 'taskUuid', 'doc_token'):
            v = '"%s"' % v
        lines.append('%s: %s' % (k, v))
    lines.append('---')
    return '\n'.join(lines) + '\n'


def unique_path(folder, stem, suffix='.md'):
    """folder/stem.md 已存在时依次尝试 stem-2、stem-3 …，返回不冲突的路径。"""
    path = os.path.join(folder, stem + suffix)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, '%s-%d%s' % (stem, n, suffix))
        n += 1
    return path


# ---------------------------------------------------------------- 收件箱
def write_inbox_note(cfg, source, label, source_id, title, date, body, extra=None, header_lines=None):
    """
    把一条来源笔记写进收件箱 inbox/<label>/标题.md。
    同一来源的同一 ID 已经写过就跳过（返回 None），否则返回新文件路径。
    """
    folder = os.path.join(cfg['inbox'], label)
    os.makedirs(folder, exist_ok=True)
    index = load_state(cfg, 'inbox-index')
    key = '%s:%s' % (source, source_id)
    if key in index and os.path.exists(index[key]):
        return None
    fields = {'标题': title, '日期': date or '', '来源': label, 'source': source, 'source_id': source_id}
    fields.update(extra or {})
    head = '# %s\n\n' % title
    if header_lines:
        head += '\n'.join('> %s' % h for h in header_lines) + '\n\n---\n\n'
    path = unique_path(folder, sanitize(title))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(render_frontmatter(fields) + '\n' + head + (body or '').strip() + '\n')
    index[key] = path
    save_state(cfg, 'inbox-index', index)
    return path


def read_inbox(cfg):
    """遍历收件箱，返回 [{path, fm, body}]，按日期排序。"""
    notes = []
    root = cfg['inbox']
    if not os.path.isdir(root):
        return notes
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in filenames:
            if not fn.endswith('.md'):
                continue
            p = os.path.join(dirpath, fn)
            with open(p, encoding='utf-8', errors='ignore') as f:
                fm, body = parse_frontmatter(f.read())
            if fm.get('source_id') is None:
                continue
            notes.append({'path': p, 'fm': fm, 'body': body})
    notes.sort(key=lambda n: (n['fm'].get('日期', ''), n['fm'].get('标题', '')))
    return notes


# ---------------------------------------------------------------- 状态文件
def state_path(cfg, name):
    os.makedirs(cfg['state'], exist_ok=True)
    return os.path.join(cfg['state'], name + '.json')


def load_state(cfg, name):
    p = state_path(cfg, name)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(cfg, name, data):
    p = state_path(cfg, name)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


# ---------------------------------------------------------------- 分类
def compile_rules(cfg):
    out = []
    for r in cfg.get('rules', []):
        out.append({
            'line': r['line'], 'case': r.get('case'),
            'title': re.compile(r['title']) if r.get('title') else None,
            'content': re.compile(r['content']) if r.get('content') else None,
        })
    return out


def classify(title, content, cfg, compiled=None):
    """
    返回 (项目线, 案例或 None)。
    顺序：① overrides 精确匹配标题 → ② 按 rules 顺序，标题正则命中即返回
          → ③ 再按 rules 顺序用内容正则（只看正文前 600 字）→ ④ default_line。
    """
    if title in cfg.get('overrides', {}):
        line, case = cfg['overrides'][title]
        return line, case or None
    compiled = compiled or compile_rules(cfg)
    for r in compiled:
        if r['title'] is not None and r['title'].search(title or ''):
            return r['line'], r['case']
    peek = (content or '')[:600]
    for r in compiled:
        if r['content'] is not None and r['content'].search(peek):
            return r['line'], r['case']
    return cfg['default_line'], None


NOTE_TYPES = [
    (r'课程|培训|课堂|教学|实操|通识', '课程实录'),
    (r'分享|演讲|路演|讲座|讲解', '分享实录'),
    (r'洽谈|合作|对接|签约|需求沟通|客户会议', '商务洽谈'),
    (r'会议|讨论|复盘|早会|晨会|例会|研讨|评审|分工|同步', '会议记录'),
    (r'日记|随笔|灵感', '日记'),
]


def guess_type(title, cfg=None):
    rules = (cfg or {}).get('note_types') or NOTE_TYPES
    for pat, name in rules:
        if re.search(pat, title or ''):
            return name
    return '其他'


# ---------------------------------------------------------------- 词条替换（纠错 / 代号化）
def compile_replacements(entries):
    """entries: [{"from": "错词或真名", "to": "正确写法或代号", "guard": "可选正则"}]，长词优先。"""
    out = []
    for e in sorted(entries, key=lambda x: -len(x['from'])):
        pat = re.compile(e['guard']) if e.get('guard') else re.compile(re.escape(e['from']))
        out.append((e['from'], e['to'], pat))
    return out


def apply_replacements(text, compiled):
    """返回 (新文本, {"from → to": 次数})。"""
    detail = {}
    for src, dst, pat in compiled:
        text, n = pat.subn(lambda m, d=dst: d, text)   # 用函数替换，避免 \1 之类被当成反向引用
        if n:
            detail['%s → %s' % (src, dst)] = n
    return text, detail
