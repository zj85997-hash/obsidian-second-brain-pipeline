# -*- coding: utf-8 -*-
"""
飞书云文档 / 知识库：docx 正文 → 收件箱。

用法（只读权限）：
1. 在飞书开放平台建一个「企业自建应用」，开通用户身份权限：
   drive:drive:readonly、wiki:wiki:readonly、docx:document:readonly，
   重定向 URL 填 http://127.0.0.1:8899/callback
2. 设置环境变量 FEISHU_APP_ID、FEISHU_APP_SECRET（不要写进配置文件或代码）
3. python3 -m sbp feishu-auth      # 浏览器授权，令牌存到 state 目录（权限 600，已被 .gitignore 忽略）
4. python3 -m sbp collect feishu   # 遍历「我的空间」和知识库，拉取 docx 正文

全程开启 HTTPS 证书校验。公司网络有自签证书时，用配置 sources.feishu.ca_bundle
或环境变量 SSL_CERT_FILE 指定证书文件，不要关掉校验。
"""

import datetime
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..core import load_state, save_state, state_path, tz, write_inbox_note

API = 'https://open.feishu.cn/open-apis'
AUTHORIZE = 'https://accounts.feishu.cn/open-apis/authen/v1/authorize'
DEFAULTS = {
    'app_id_env': 'FEISHU_APP_ID',
    'app_secret_env': 'FEISHU_APP_SECRET',
    'port': 8899,
    'scopes': 'offline_access drive:drive:readonly wiki:wiki:readonly docx:document:readonly',
    'extra_wiki_spaces': [],
    'ca_bundle': '',
    'label': '飞书文档',
}
RATE_LIMIT_CODES = (99991400, 99991401)


def settings(cfg):
    s = dict(DEFAULTS)
    s.update(cfg.get('sources', {}).get('feishu', {}))
    return s


def ssl_context(s):
    cafile = s.get('ca_bundle') or os.environ.get('SSL_CERT_FILE') or None
    return ssl.create_default_context(cafile=os.path.expanduser(cafile) if cafile else None)


def http_json(url, ctx, method='GET', headers=None, params=None, body=None, timeout=30):
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header('Content-Type', 'application/json; charset=utf-8')
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except ValueError:
            return {'code': e.code, 'msg': str(e)}


# ---------------------------------------------------------------- 授权
def credentials(s):
    app_id, secret = os.environ.get(s['app_id_env']), os.environ.get(s['app_secret_env'])
    if not app_id or not secret:
        raise RuntimeError('请先设置环境变量 %s 和 %s' % (s['app_id_env'], s['app_secret_env']))
    return app_id, secret


def auth(cfg, log=print):
    """本地起一个回调服务，浏览器授权后用 code 换 user_access_token，存到 state/feishu-token.json。"""
    s = settings(cfg)
    app_id, secret = credentials(s)
    redirect = 'http://127.0.0.1:%d/callback' % s['port']
    holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == '/callback':
                holder['code'] = urllib.parse.parse_qs(parsed.query).get('code', [None])[0]
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write('✅ 授权成功，可以关闭此页面。'.encode('utf-8'))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', s['port']), Handler)
    url = '%s?%s' % (AUTHORIZE, urllib.parse.urlencode(
        {'app_id': app_id, 'redirect_uri': redirect, 'state': 'sbp', 'scope': s['scopes']}))
    log('请在浏览器里完成授权：\n%s' % url)
    webbrowser.open(url)
    threading.Thread(target=server.handle_request, daemon=True).start()
    for _ in range(600):
        if holder.get('code'):
            break
        time.sleep(1)
    server.server_close()
    if not holder.get('code'):
        raise RuntimeError('10 分钟内没有收到授权回调')
    d = http_json(API + '/authen/v2/oauth/token', ssl_context(s), method='POST', body={
        'grant_type': 'authorization_code', 'client_id': app_id, 'client_secret': secret,
        'code': holder['code'], 'redirect_uri': redirect})
    if 'access_token' not in d:
        raise RuntimeError('换取令牌失败：%s' % json.dumps(d, ensure_ascii=False)[:300])
    d['obtained_at'] = int(time.time())
    path = state_path(cfg, 'feishu-token')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    os.chmod(path, 0o600)
    log('✅ 令牌已保存到 %s（有效期 %s 秒，过期后重新运行 feishu-auth）' % (path, d.get('expires_in')))


def access_token(cfg):
    tok = load_state(cfg, 'feishu-token')
    if not tok.get('access_token'):
        raise RuntimeError('还没有授权，请先运行：python3 -m sbp feishu-auth')
    if tok.get('obtained_at') and tok.get('expires_in') and time.time() > tok['obtained_at'] + tok['expires_in'] - 60:
        raise RuntimeError('飞书令牌已过期，请重新运行：python3 -m sbp feishu-auth')
    return tok['access_token']


# ---------------------------------------------------------------- 遍历与拉取
def api(path, token, ctx, params=None, retry=3, log=print):
    for i in range(retry):
        d = http_json(API + path, ctx, headers={'Authorization': 'Bearer ' + token}, params=params)
        if d.get('code') == 0:
            return d.get('data', {})
        if d.get('code') in RATE_LIMIT_CODES:
            time.sleep(1.5 * (i + 1))
            continue
        log('  接口 %s 返回 code=%s msg=%s' % (path, d.get('code'), d.get('msg')))
        return {}
    return {}


def list_documents(cfg, token, ctx, log=print):
    """返回 [{name, token, url, path, modified}]：我的空间递归 + 知识库空间里的 docx。"""
    s = settings(cfg)
    docs = []

    def walk(folder_token, path, depth=0):
        if depth > 8:
            return
        page = None
        while True:
            params = {'folder_token': folder_token, 'page_size': 200}
            if page:
                params['page_token'] = page
            data = api('/drive/v1/files', token, ctx, params, log=log)
            files = data.get('files', [])
            for f in files:
                if f.get('type') == 'docx':
                    docs.append({'name': f.get('name', ''), 'token': f.get('token'), 'url': f.get('url', ''),
                                 'path': path, 'modified': f.get('modified_time', '')})
                elif f.get('type') == 'folder':
                    walk(f['token'], path + '/' + f.get('name', ''), depth + 1)
            page = data.get('page_token')
            if not page or not files:
                break
            time.sleep(0.15)

    walk('', '我的空间')
    spaces = [sp.get('space_id') for sp in api('/wiki/v2/spaces', token, ctx, {'page_size': 50}, log=log).get('items', [])]
    spaces += [sid for sid in s.get('extra_wiki_spaces', []) if sid not in spaces]

    def walk_wiki(space_id, parent=None, path='知识库', depth=0):
        if depth > 8:
            return
        page = None
        while True:
            params = {'page_size': 50}
            if parent:
                params['parent_node_token'] = parent
            if page:
                params['page_token'] = page
            data = api('/wiki/v2/spaces/%s/nodes' % space_id, token, ctx, params, log=log)
            for n in data.get('items', []):
                title = n.get('title', '')
                if n.get('obj_type') == 'docx':
                    docs.append({'name': title, 'token': n.get('obj_token'), 'url': '',
                                 'path': path, 'modified': n.get('obj_edit_time', '')})
                if n.get('has_child'):
                    walk_wiki(space_id, n.get('node_token'), path + '/' + title, depth + 1)
            page = data.get('page_token')
            if not page:
                break
            time.sleep(0.15)

    for sid in spaces:
        walk_wiki(sid)
    log('飞书：找到 docx %d 篇' % len(docs))
    return docs


def fetch_raw(doc, token, ctx):
    d = http_json('%s/docx/v1/documents/%s/raw_content' % (API, doc['token']), ctx,
                  headers={'Authorization': 'Bearer ' + token})
    return d


# ---------------------------------------------------------------- 转换
DATE_PATTERNS = [
    (re.compile(r'^(\d{2})\.(\d{1,2})\.(\d{1,2})$'), lambda m: '20%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))),
    (re.compile(r'^(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})$'), lambda m: '%s-%02d-%02d' % (m.group(1), int(m.group(2)), int(m.group(3)))),
]


def guess_date(doc, cfg):
    """标题是日期（26.03.02 / 2026.3.2 / 2026-03-02）就用标题，否则用修改时间。"""
    name = (doc.get('name') or '').strip()
    for pat, fn in DATE_PATTERNS:
        m = pat.match(name)
        if m:
            return fn(m)
    mod = str(doc.get('modified') or '')
    if mod.isdigit():
        return datetime.datetime.fromtimestamp(int(mod), tz(cfg)).strftime('%Y-%m-%d')
    return ''


def doc_to_inbox(cfg, doc, content, label):
    title = (doc.get('name') or '').strip() or '无标题-%s' % doc['token'][:8]
    extra = {'飞书目录': doc.get('path', '')}
    if doc.get('url'):
        extra['飞书链接'] = doc['url']
    return write_inbox_note(cfg, 'feishu', label, doc['token'], title, guess_date(doc, cfg),
                            (content or '').strip() or '（空文档）', extra=extra)


def collect(cfg, docs_dir=None, log=print):
    """
    docs_dir：离线模式，读取已拉取的 JSON（每篇一个文件：name / token / path / url / modified / content）。
    否则在线遍历并拉取。返回 (新写入数, 失败列表)。
    """
    s = settings(cfg)
    added, failed = 0, []
    if docs_dir:
        for fn in sorted(os.listdir(docs_dir)):
            if fn.endswith('.json'):
                with open(os.path.join(docs_dir, fn), encoding='utf-8') as f:
                    d = json.load(f)
                if doc_to_inbox(cfg, d, d.get('content', ''), s['label']):
                    added += 1
        log('飞书（离线）：新写入 %d 篇' % added)
        return added, failed
    ctx = ssl_context(s)
    token = access_token(cfg)
    progress = load_state(cfg, 'feishu-progress')
    for doc in list_documents(cfg, token, ctx, log=log):
        if progress.get(doc['token']) == 'done':
            continue
        d = {}
        for attempt in range(3):
            d = fetch_raw(doc, token, ctx)
            if d.get('code') in RATE_LIMIT_CODES:
                time.sleep(1.2 * (attempt + 1))
                continue
            break
        if d.get('code') != 0:
            failed.append((doc['name'], d.get('code'), (d.get('msg') or '')[:60]))
            continue
        if doc_to_inbox(cfg, doc, d.get('data', {}).get('content', ''), s['label']):
            added += 1
            log('  + %s' % doc['name'])
        progress[doc['token']] = 'done'
        save_state(cfg, 'feishu-progress', progress)
        time.sleep(0.12)
    log('飞书：新写入 %d 篇，失败 %d 篇' % (added, len(failed)))
    return added, failed
