# -*- coding: utf-8 -*-
"""
钉钉 AI 听记：逐字稿 → 收件箱（按发言人合并段落，带时间戳和颜色标记）。

依赖钉钉官方命令行工具 dws，登录由它管理，本脚本不碰凭据。
清单和逐字稿命令可以在配置 sources.dingtalk 里改。
"""

import datetime
import json
import re
import time

from ..core import load_state, save_state, tz, write_inbox_note
from .runner import fill, run_json

DEFAULTS = {
    'list_cmd': ['dws', 'minutes', '+list-all', '--format', 'json'],
    'transcript_cmd': ['dws', 'minutes', '+transcript', '--id', '{id}', '--format', 'json'],
    'label': '钉钉听记',
}
COLORS = ['🔴', '🟢', '🔵', '🟡', '🟠', '🟣', '🟤', '⚫']


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get('sources', {}).get('dingtalk', {}))
    return s


def fmt_ts(ms):
    sec = int(ms // 1000)
    return '%02d:%02d:%02d' % (sec // 3600, sec % 3600 // 60, sec % 60)


def paragraphs_to_markdown(paragraphs):
    """连续同一发言人的段落合并成一块：「🔴 发言人 [00:01:23]」+ 正文。"""
    blocks, colors = [], {}
    for p in paragraphs:
        spk = p.get('nickName') or (p.get('speakerDisplay') or {}).get('nickName') or '发言人'
        if spk not in colors:
            colors[spk] = COLORS[len(colors) % len(COLORS)]
        text = p.get('paragraph', '')
        if blocks and blocks[-1][0] == spk:
            blocks[-1][2] += text
        else:
            blocks.append([spk, p.get('startTime', 0), text])
    return '\n\n'.join('%s %s [%s]\n%s' % (colors[b[0]], b[0], fmt_ts(b[1]), b[2])
                       for b in blocks if b[2].strip())


def collect(cfg, minutes_json=None, transcripts_dir=None, log=print):
    """离线模式：minutes_json + transcripts_dir/<taskUuid>.json。返回 (新写入数, 失败列表)。"""
    s = settings(cfg)
    if minutes_json:
        with open(minutes_json, encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = run_json(s['list_cmd'])
    minutes = data.get('data', {}).get('minutes', [])
    log('钉钉听记：共 %d 条' % len(minutes))
    progress = load_state(cfg, 'dingtalk-progress')
    added, failed = 0, []
    for m in sorted(minutes, key=lambda x: x.get('startTime', 0)):
        tid = m['taskUuid']
        if progress.get(tid) == 'done':
            continue
        raw, err = None, ''
        for attempt in range(3):
            try:
                if transcripts_dir:
                    with open('%s/%s.json' % (transcripts_dir, tid), encoding='utf-8') as f:
                        raw = json.load(f)
                else:
                    raw = run_json(fill(s['transcript_cmd'], id=tid))
                if raw.get('ok'):
                    break
                err = json.dumps(raw.get('error', {}), ensure_ascii=False)[:200]
                raw = None
            except Exception as e:  # noqa: BLE001
                err = str(e)[:200]
            if transcripts_dir:
                break
            time.sleep(2 * (attempt + 1))
        title = (m.get('title') or '无标题').strip()
        if raw is None:
            failed.append((tid, title, err))
            log('  ✗ %s：%s' % (title, err))
            continue
        dt = datetime.datetime.fromtimestamp(m.get('startTime', 0) / 1000, tz(cfg))
        if re.match(r'^\d{4}-\d{2}-\d{2} [\d:]+ 文件$', title):
            title = '钉钉文件转写-%s' % dt.strftime('%Y-%m-%d %H-%M')
        body = paragraphs_to_markdown(raw.get('data', {}).get('paragraphList', [])) or '（无转写内容）'
        path = write_inbox_note(
            cfg, 'dingtalk', s['label'], tid, title, dt.strftime('%Y-%m-%d'), body,
            header_lines=['**录音时间**: %s' % dt.strftime('%Y-%m-%d %H:%M:%S'), '**taskUuid**: `%s`' % tid])
        progress[tid] = 'done'
        save_state(cfg, 'dingtalk-progress', progress)
        if path:
            added += 1
            log('  + %s' % title)
    log('钉钉听记：新写入 %d 条，失败 %d 条' % (added, len(failed)))
    return added, failed
