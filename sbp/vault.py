# -*- coding: utf-8 -*-
"""
入库与目录页：
- import_inbox：把收件箱里的笔记按规则分类，复制进 Obsidian 库（只新增，不删除、不覆盖别的文件）
- rebuild_index：重建首页 / 板块总览 / 案例首页里「自动生成」的那一段，手写内容原样保留
"""

import datetime
import os
import re
import shutil
from collections import Counter, defaultdict

from .core import (classify, compile_rules, guess_type, load_state, parse_frontmatter,
                   read_inbox, render_frontmatter, sanitize, save_state, unique_path)

AUTO_START = '<!-- sbp:auto:start -->'
AUTO_END = '<!-- sbp:auto:end -->'
HOME_FILE = '🏠 首页.md'
BOARD_FILE = '_板块总览.md'
CASE_FILE = '_案例首页.md'
MAPPING_FILE = '🔒 代号对照表.md'
INBOX_ONLY_KEYS = ('标题', '日期', '来源', 'source', 'source_id')


# ---------------------------------------------------------------- 入库
def import_inbox(cfg, dry_run=False, update=False, log=print):
    """返回 (分类统计 Counter, 新写入的文件列表)。"""
    vault = cfg['vault']
    compiled = compile_rules(cfg)
    imported = load_state(cfg, 'imported')
    stats, written = Counter(), []
    for n in read_inbox(cfg):
        fm = n['fm']
        key = '%s:%s' % (fm.get('source', ''), fm.get('source_id', ''))
        title = fm.get('标题') or os.path.splitext(os.path.basename(n['path']))[0]
        line, case = classify(title, n['body'], cfg, compiled)
        stats[(line, case or '')] += 1
        old_rel = imported.get(key)
        if old_rel and not update:
            continue
        fields = {
            '标题': title, '项目线': line, '案例': case or '', '相关案例': '[]',
            '日期': fm.get('日期', ''), '类型': guess_type(title, cfg),
        }
        for k, v in fm.items():
            if k not in INBOX_ONLY_KEYS:
                fields[k] = v
        fields['来源'] = fm.get('来源', '')
        fields['source_id'] = fm.get('source_id', '')
        text = render_frontmatter(fields) + '\n' + n['body'].lstrip('\n')
        if dry_run:
            log('[预览] %s → %s/%s' % (title, line, case or '（散记）'))
            continue
        dest_dir = os.path.join(vault, line, case or '')
        os.makedirs(dest_dir, exist_ok=True)
        if old_rel and update and os.path.exists(os.path.join(vault, old_rel)):
            path = os.path.join(vault, old_rel)
        else:
            path = unique_path(dest_dir, sanitize(title))
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        imported[key] = os.path.relpath(path, vault)
        written.append(path)
        log('入库：%s/%s ← %s' % (line, case or '（散记）', os.path.basename(path)))
    if not dry_run:
        save_state(cfg, 'imported', imported)
    return stats, written


# ---------------------------------------------------------------- 扫描库
def is_index_file(name):
    return name.startswith(('_', '🏠', '🔒', '📋')) or not name.endswith('.md')


def scan_vault(cfg):
    """返回 {项目线: {子文件夹或 '': [(日期, 相对路径无后缀, 标题)]}}。"""
    vault = cfg['vault']
    out = defaultdict(lambda: defaultdict(list))
    if not os.path.isdir(vault):
        return out
    for line in sorted(os.listdir(vault)):
        ldir = os.path.join(vault, line)
        if line.startswith('.') or not os.path.isdir(ldir):
            continue
        out[line]  # 空项目线也保留
        for dirpath, dirnames, filenames in os.walk(ldir):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
            rel_dir = os.path.relpath(dirpath, ldir)
            sub = '' if rel_dir == '.' else rel_dir.split(os.sep)[0]
            for fn in sorted(filenames):
                if is_index_file(fn):
                    continue
                p = os.path.join(dirpath, fn)
                with open(p, encoding='utf-8', errors='ignore') as f:
                    fm, _ = parse_frontmatter(f.read(2000))
                rel = os.path.relpath(p, vault)[:-3]
                out[line][sub].append((fm.get('日期', ''), rel.replace(os.sep, '/'), fm.get('标题') or fn[:-3]))
    return out


def case_meta(cfg):
    """配置里登记的案例信息：{案例名: {...}}，以及项目线说明。"""
    cases, lines = {}, {}
    for ln in cfg.get('lines', []):
        lines[ln['name']] = ln
        for c in ln.get('cases', []):
            cases[c['name']] = c
    return cases, lines


def is_case(name, cfg, cases):
    return name in cases or name.startswith(cfg.get('case_prefix', '案例'))


# ---------------------------------------------------------------- 写自动区块
def write_auto(path, auto_text, template, force=False, log=print):
    """
    已有文件且有自动区块标记：只替换标记之间的内容。
    已有文件但没有标记：默认不动（避免覆盖手写内容），加 --force 才按模板重写。
    文件不存在：按模板新建。
    """
    block = '%s\n%s\n%s' % (AUTO_START, auto_text.rstrip('\n'), AUTO_END)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            old = f.read()
        if AUTO_START in old and AUTO_END in old:
            i, j = old.index(AUTO_START), old.index(AUTO_END) + len(AUTO_END)
            new = old[:i] + block + old[j:]
        elif force:
            new = template.replace('{{AUTO}}', block)
        else:
            log('⚠️ 跳过（没有自动区块标记，避免覆盖手写内容；要重建请加 --force）：%s' % path)
            return False
        if new == old:
            return False
    else:
        new = template.replace('{{AUTO}}', block)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new)
    return True


def month_list(notes):
    months = defaultdict(list)
    for date, rel, title in notes:
        months[date[:7] if len(date) >= 7 else '未知日期'].append((date, rel, title))
    lines = []
    for m in sorted(months):
        lines.append('**%s**' % m)
        for date, rel, title in sorted(months[m]):
            label = (date[5:10] + ' ' if len(date) >= 10 else '') + title[:40]
            lines.append('- [[%s|%s]]' % (rel, label))
        lines.append('')
    return '\n'.join(lines).rstrip('\n') or '（暂无笔记）'


# ---------------------------------------------------------------- 重建目录页
def rebuild_index(cfg, force=False, log=print):
    vault = cfg['vault']
    data = scan_vault(cfg)
    cases, lines_cfg = case_meta(cfg)
    changed = 0

    # 1) 案例首页
    for line, subs in data.items():
        for sub, notes in subs.items():
            if not sub or not is_case(sub, cfg, cases):
                continue
            meta = cases.get(sub, {})
            dates = sorted(d for d, _, _ in notes if d)
            span = meta.get('span') or ('%s ~ %s' % (dates[0], dates[-1]) if dates else '')
            template = '\n'.join([
                '---', '案例: %s' % sub, '行业: %s' % meta.get('industry', ''),
                '服务类型: %s' % meta.get('service', ''), '时间跨度: %s' % span, '状态: 整理中', '---', '',
                '# %s' % sub, '',
                '> %s' % (meta.get('note') or '本页的「关键节点」由脚本自动生成，其余部分可以手写，重建时不会被覆盖。'), '',
                '## 🎯 背景与需求', '', '（待补全）', '',
                '## 🧩 我的服务内容', '', '（待补全）', '',
                '## 📌 关键节点', '', '{{AUTO}}', '',
                '## 📦 产出与成果', '', '（待补全）', ''])
            auto = '共 %d 篇笔记\n\n%s' % (len(notes), month_list(notes))
            changed += write_auto(os.path.join(vault, line, sub, CASE_FILE), auto, template, force, log)

    # 2) 板块总览（有案例子文件夹的项目线才生成）
    for line, subs in data.items():
        case_subs = [s for s in subs if s and is_case(s, cfg, cases)]
        if not case_subs:
            continue
        rows = []
        for sub in sorted(subs):
            notes = subs[sub]
            if not sub:
                rows.append('- （散记）（%d 篇）' % len(notes))
            elif is_case(sub, cfg, cases):
                rows.append('- [[%s/%s/%s|%s]]（%d 篇）' % (line, sub, CASE_FILE[:-3], sub, len(notes)))
            else:
                rows.append('- %s（%d 篇）' % (sub, len(notes)))
        desc = lines_cfg.get(line, {}).get('desc', '')
        template = '\n'.join(['---', '项目线: %s' % line, '---', '', '# %s' % line, '', desc, '',
                              '## 子板块', '', '{{AUTO}}', ''])
        changed += write_auto(os.path.join(vault, line, BOARD_FILE), '\n'.join(rows), template, force, log)

    # 3) 首页
    order = [ln['name'] for ln in cfg.get('lines', [])]
    order += sorted(l for l in data if l not in order)
    total = 0
    rows = []
    for line in order:
        if line not in data:
            continue
        subs = data[line]
        n_line = sum(len(v) for v in subs.values())
        total += n_line
        rows.append('### %s（%d 篇）' % (line, n_line))
        desc = lines_cfg.get(line, {}).get('desc', '')
        if desc:
            rows.append(desc)
        for sub in sorted(subs):
            if not sub:
                if any(s for s in subs if s):
                    rows.append('- （散记）（%d 篇）' % len(subs[sub]))
            elif is_case(sub, cfg, cases):
                rows.append('- [[%s/%s/%s|%s]]（%d 篇）' % (line, sub, CASE_FILE[:-3], sub, len(subs[sub])))
            else:
                rows.append('- %s（%d 篇）' % (sub, len(subs[sub])))
        rows.append('')
    rows.append('**共 %d 篇笔记**（目录更新于 %s）' % (total, datetime.date.today().isoformat()))
    home = cfg.get('home', {})
    template = '\n'.join(['---', '类型: 首页', '---', '', '# 🏠 %s' % home.get('title', '我的第二大脑'), '',
                          '> %s' % home.get('intro', '按项目线组织的个人知识库。下面的目录由脚本自动生成。'), '',
                          '## 📇 项目线总览', '', '{{AUTO}}', ''])
    changed += write_auto(os.path.join(vault, HOME_FILE), '\n'.join(rows), template, force, log)

    # 4) 本地代号对照表（只在配置了 private_mapping 时生成；不要分享或提交）
    mapping = cfg.get('private_mapping') or {}
    if mapping:
        lines = ['---', '类型: 代号对照表', '保密: 仅本机自用', '---', '', '# 🔒 代号对照表', '',
                 '> 对外展示、给学员看库时只用代号；本表不要随库分享，也不要提交到任何仓库。', '',
                 '| 代号 | 真实名称 |', '|---|---|']
        lines += ['| %s | %s |' % (k, v) for k, v in mapping.items()]
        p = os.path.join(vault, MAPPING_FILE)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    return changed


# ---------------------------------------------------------------- 词条替换（作用于目录）
def replace_in_tree(root, compiled, apply_fn, apply=False, out=None, rename=False, log=print):
    """
    对 root 下所有 .md 做词条替换。
    - 默认只预览；apply=True 原地修改；out=目录 时写到一份副本（适合导出可分享版本）。
    - rename=True 时文件名、文件夹名里的词条也一起替换（正文里的 [[链接]] 同步被替换，链接保持一致）。
    返回 (改动文件数, 总替换次数)。跳过 🔒 开头的文件。
    """
    n_files, n_total = 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        rel_dir = os.path.relpath(dirpath, root)
        for fn in filenames:
            src = os.path.join(dirpath, fn)
            if fn.startswith('🔒'):
                continue
            target_rel = os.path.join(rel_dir, fn) if rel_dir != '.' else fn
            if rename:
                target_rel, _ = apply_fn(target_rel, compiled)
            if not fn.endswith('.md'):
                if out:
                    dst = os.path.join(out, target_rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                continue
            with open(src, encoding='utf-8', errors='ignore') as f:
                text = f.read()
            new, detail = apply_fn(text, compiled)
            cnt = sum(detail.values())
            if cnt:
                n_files += 1
                n_total += cnt
                log('%s：%s' % (target_rel, '；'.join('%s ×%d' % kv for kv in detail.items())))
            if out:
                dst = os.path.join(out, target_rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(new)
            elif apply and (cnt or target_rel != os.path.join(rel_dir, fn)):
                dst = os.path.join(root, target_rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(new)
                if os.path.abspath(dst) != os.path.abspath(src):
                    os.remove(src)
    if apply and rename and not out:
        # 原地改名后，删掉被搬空的旧文件夹
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            if dirpath != root and not os.listdir(dirpath):
                os.rmdir(dirpath)
    return n_files, n_total
