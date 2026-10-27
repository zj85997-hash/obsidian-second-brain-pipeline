# -*- coding: utf-8 -*-
"""调用外部命令行工具（getnote / dws / osascript）的小工具。"""

import json
import shutil
import subprocess


def fill(cmd, **values):
    """把命令模板里的 {id} 等占位符换成实际值。"""
    return [part.format(**values) for part in cmd]


def run_json(cmd, raw=False, timeout=300):
    """运行命令并解析 stdout 的 JSON；raw=True 时返回原始文本。命令不存在或失败时抛出 RuntimeError。"""
    if shutil.which(cmd[0]) is None:
        raise RuntimeError('找不到命令 %s，请先安装并登录（见 README）' % cmd[0])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or '命令失败').strip()[:300])
    return r.stdout if raw else json.loads(r.stdout)
