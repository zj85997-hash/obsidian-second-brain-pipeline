# -*- coding: utf-8 -*-
"""
得到大脑（Get笔记）：录音转写 → 收件箱。

依赖官方命令行工具 getnote（npm 包 @getnote/cli），登录和 API Key 都由它管理，本脚本不碰凭据：
    npm i -g @getnote/cli && getnote auth     # 或设置环境变量 GETNOTE_API_KEY
"""

import json
import time

from ..core import load_state, save_state, write_inbox_note
from .runner import fill, run_json

DEFAULTS = {
    'list_cmd': ['getnote', 'notes', '--all', '-o', 'json'],
    'transcript_cmd': ['getnote', 'note', 'transcript', '{id}', '-o', 'json'],
    'audio_types': ['recorder_audio', 'audio', 'class_audio', 'local_audio', 'recorder_flash_audio'],
    'label': '得到大脑',
}


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get('sources', {}).get('getnote', {}))
    return s


def parse_note_list(stdout_or_obj):
    """兼容整段 JSON（data.notes）和逐行 JSON 两种输出。"""
    if isinstance(stdout_or_obj, dict):
        return stdout_or_obj.get('data', {}).get('notes', [])
    text = stdout_or_obj.strip()
    try:
        return parse_note_list(json.loads(text))
    except ValueError:
        notes = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and 'note_id' in obj:
                notes.append(obj)
            elif isinstance(obj, dict):
                notes.extend(parse_note_list(obj))
        return notes


def collect(cfg, notes_json=None, transcripts_dir=None, log=print):
    """
    notes_json / transcripts_dir：离线模式，读已导出的清单和转写文件（transcripts_dir/<note_id>.json），
    不调用命令行。否则调用 getnote 在线拉取。返回 (新写入数, 失败列表)。
    """
    s = settings(cfg)
    if notes_json:
        with open(notes_json, encoding='utf-8') as f:
            notes = parse_note_list(json.load(f))
    else:
        notes = parse_note_list(run_json(s['list_cmd'], raw=True))
    audios = [n for n in notes if n.get('note_type') in set(s['audio_types'])]
    audios.sort(key=lambda n: n.get('created_at') or '')
    log('得到大脑：共 %d 条笔记，其中录音 %d 条' % (len(notes), len(audios)))

    progress = load_state(cfg, 'getnote-progress')
    added, failed = 0, []
    for n in audios:
        nid = str(n.get('note_id') or n.get('id'))
        if progress.get(nid) == 'done':
            continue
        transcript, err = None, ''
        for attempt in range(3):
            try:
                if transcripts_dir:
                    with open('%s/%s.json' % (transcripts_dir, nid), encoding='utf-8') as f:
                        d = json.load(f)
                else:
                    d = run_json(fill(s['transcript_cmd'], id=nid))
                if d.get('success'):
                    transcript = (d.get('data', {}).get('transcript') or '').strip()
                    break
                err = json.dumps(d.get('error', {}), ensure_ascii=False)[:200]
            except Exception as e:  # noqa: BLE001  命令行失败、文件缺失都重试
                err = str(e)[:200]
            if transcripts_dir:
                break
            time.sleep(2 * (attempt + 1))
        title = (n.get('title') or '无标题').strip()
        if transcript is None:
            failed.append((nid, title, err))
            log('  ✗ %s：%s' % (title, err))
            continue
        created = n.get('created_at') or ''
        path = write_inbox_note(
            cfg, 'getnote', s['label'], nid, title, created[:10], transcript,
            header_lines=['**录音时间**: %s' % created, '**Note ID**: `%s`' % nid])
        progress[nid] = 'done'
        save_state(cfg, 'getnote-progress', progress)
        if path:
            added += 1
            log('  + %s' % title)
    log('得到大脑：新写入 %d 条，失败 %d 条' % (added, len(failed)))
    return added, failed
