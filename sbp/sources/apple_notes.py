# -*- coding: utf-8 -*-
"""
苹果「备忘录」(Apple Notes) → 收件箱。只能在 macOS 上导出。

    python3 -m sbp collect apple-notes                    # 自动运行导出脚本（需授权「控制备忘录」）
    python3 -m sbp collect apple-notes --stream 导出.txt   # 离线：解析已导出的文本流
"""

import datetime
import html
import json
import os
import re
import subprocess
import tempfile

from ..core import tz, write_inbox_note

DEFAULTS = {'label': '苹果备忘录', 'skip_folders': ['Recently Deleted', '最近删除']}
HERE = os.path.dirname(os.path.abspath(__file__))
JXA = os.path.join(HERE, '..', '..', 'scripts', 'export_apple_notes.jxa')


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get('sources', {}).get('apple_notes', {}))
    return s


def html_to_md(h):
    """备忘录正文是 HTML，转成够用的 Markdown：标题、粗体、斜体、列表、链接、换行。"""
    if not h:
        return '（空笔记）'
    h = re.sub(r'src="data:[^"]*"', 'src=""', h)
    h = re.sub(r'<br\s*/?>', '\n', h, flags=re.I)
    h = re.sub(r'</(?:div|p)>', '\n', h, flags=re.I)
    h = re.sub(r'</(?:ul|ol)>', '\n\n', h, flags=re.I)   # 列表结束空一行，后文不会被并进最后一项
    h = re.sub(r'</li>', '', h, flags=re.I)   # <li> 已经换行，这里不再加空行
    for level in (1, 2, 3):
        h = re.sub(r'<h%d[^>]*>' % level, '\n' + '#' * level + ' ', h, flags=re.I)
        h = re.sub(r'</h%d>' % level, '\n', h, flags=re.I)
    h = re.sub(r'<(?:b|strong)(?:\s[^>]*)?>', '**', h, flags=re.I)
    h = re.sub(r'</(?:b|strong)>', '**', h, flags=re.I)
    h = re.sub(r'<i(?:\s[^>]*)?>', '*', h, flags=re.I)
    h = re.sub(r'</i>', '*', h, flags=re.I)
    h = re.sub(r'<li[^>]*>', '\n- ', h, flags=re.I)
    h = re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', h, flags=re.S | re.I)
    t = html.unescape(re.sub(r'<[^>]+>', '', h))
    t = re.sub(r'[ \t]+\n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip() or '（空笔记）'


def parse_stream(text):
    """解析导出流，返回 [(文件夹, 笔记 dict)]。兼容「每文件夹一个 JSON」和「分块 JSON」两种写法。"""
    notes, folder, expect_json = [], None, False
    # 只按 \n 切分：正文里可能有 U+2028 等字符，splitlines() 会把一行 JSON 切断
    for line in text.split('\n'):
        if line.startswith('@@@FOLDER@@@'):
            folder = line[len('@@@FOLDER@@@'):].strip()
            folder = folder.split('__', 1)[1] if re.match(r'^\d+__', folder) else folder
            expect_json = True
            continue
        if line.startswith('@@@CHUNK@@@'):
            expect_json = True
            continue
        if line.startswith('@@@DONE@@@'):
            break
        if expect_json and line.strip().startswith('['):
            try:
                for n in json.loads(line):
                    notes.append((n.get('folder') or folder or '未分组', n))
            except ValueError:
                pass
    return notes


def export_stream(log=print):
    if not os.path.exists(JXA):
        raise RuntimeError('找不到导出脚本：%s' % JXA)
    fd, path = tempfile.mkstemp(prefix='apple-notes-', suffix='.txt')
    log('正在导出备忘录（笔记多时需要几分钟）…')
    with os.fdopen(fd, 'w') as f:
        r = subprocess.run(['osascript', '-l', 'JavaScript', JXA], stdout=f, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise RuntimeError('导出失败（是否已允许控制「备忘录」？）：%s' % r.stderr.strip()[:300])
    return path


def collect(cfg, stream=None, log=print):
    s = settings(cfg)
    path = stream or export_stream(log)
    with open(path, encoding='utf-8', errors='ignore') as f:
        items = parse_stream(f.read())
    added = 0
    for folder, n in items:
        if folder in s['skip_folders']:
            continue
        created = n.get('created') or ''
        date = ''
        if created:
            try:
                date = datetime.datetime.fromisoformat(created.replace('Z', '+00:00')).astimezone(tz(cfg)).strftime('%Y-%m-%d')
            except ValueError:
                date = created[:10]
        title = (n.get('name') or '').strip() or '无标题-%s' % (date or '未知日期')
        nid = n.get('id') or '%s:%s:%s' % (folder, title, created)
        if write_inbox_note(cfg, 'apple_notes', s['label'], nid, title, date, html_to_md(n.get('body', '')),
                            extra={'文件夹': folder, '附件数': n.get('attachments', 0)}):
            added += 1
    log('苹果备忘录：解析 %d 条，新写入 %d 条' % (len(items), added))
    return added, []
