# -*- coding: utf-8 -*-
"""
命令行入口：python3 -m sbp <命令> [参数]

  collect getnote|dingtalk|feishu|apple-notes   采集到收件箱（支持离线导入已导出的数据）
  feishu-auth                                    飞书浏览器授权
  classify                                       预览收件箱的分类结果，不写文件
  import                                         分类并复制进 Obsidian 库，然后重建目录页
  index                                          只重建目录页（首页 / 板块总览 / 案例首页）
  replace                                        词条替换：纠正转写错字，或把真名换成代号
  sync                                           一键增量：在线采集已配置的来源 → 入库 → 重建目录
  demo                                           用自带的虚构数据把整条管线跑一遍
"""

import argparse
import json
import os
import sys
from collections import Counter

from . import __version__
from .core import apply_replacements, classify, compile_replacements, compile_rules, load_config, read_inbox
from .sources import apple_notes, dingtalk, feishu, getnote
from .vault import import_inbox, rebuild_index, replace_in_tree

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, '..', 'examples')


def cmd_collect(cfg, args):
    if args.source == 'getnote':
        return getnote.collect(cfg, args.notes_json, args.transcripts_dir)
    if args.source == 'dingtalk':
        return dingtalk.collect(cfg, args.minutes_json, args.transcripts_dir)
    if args.source == 'feishu':
        return feishu.collect(cfg, args.docs_dir)
    if args.source == 'apple-notes':
        return apple_notes.collect(cfg, args.stream)


def cmd_classify(cfg, args):
    compiled = compile_rules(cfg)
    counts, pending = Counter(), []
    for n in read_inbox(cfg):
        title = n['fm'].get('标题', '')
        line, case = classify(title, n['body'], cfg, compiled)
        counts[(line, case or '')] += 1
        if line == cfg['default_line']:
            pending.append((n['fm'].get('日期', ''), title))
    for (line, case), c in sorted(counts.items()):
        print('%-40s %4d' % (line + ('/' + case if case else ''), c))
    print('\n待归类 %d 条：' % len(pending))
    for d, t in pending:
        print('  %s  %s' % (d, t[:50]))


def cmd_import(cfg, args):
    stats, written = import_inbox(cfg, dry_run=args.dry_run, update=args.update)
    if not args.dry_run:
        rebuild_index(cfg, force=args.force)
    print('完成：新写入 %d 篇%s' % (len(written), '（预览模式，未写文件）' if args.dry_run else ''))


def cmd_replace(cfg, args):
    entries = cfg.get('replacements', [])
    if args.mapping:
        with open(args.mapping, encoding='utf-8') as f:
            entries = json.load(f)
    if not entries:
        sys.exit('没有可用的替换词条：在配置 replacements 里写，或用 --mapping 指定 JSON 文件')
    root = os.path.abspath(os.path.expanduser(args.path or cfg['vault']))
    compiled = compile_replacements(entries)
    n_files, n_total = replace_in_tree(root, compiled, apply_replacements, apply=args.apply,
                                       out=os.path.abspath(os.path.expanduser(args.out)) if args.out else None,
                                       rename=args.rename)
    mode = '已写入副本 %s' % args.out if args.out else ('已原地修改' if args.apply else '预览（加 --apply 原地修改，或 --out 导出副本）')
    print('%s：%d 个文件，共 %d 处' % (mode, n_files, n_total))


def cmd_sync(cfg, args):
    for name in cfg.get('sync_sources', []):
        print('== 采集 %s' % name)
        try:
            {'getnote': getnote.collect, 'dingtalk': dingtalk.collect,
             'feishu': feishu.collect, 'apple-notes': apple_notes.collect}[name](cfg)
        except Exception as e:  # noqa: BLE001  某个来源失败不影响其它来源
            print('  ✗ %s：%s' % (name, e))
    stats, written = import_inbox(cfg)
    rebuild_index(cfg)
    print('同步完成：新入库 %d 篇' % len(written))


def cmd_demo(args):
    out = os.path.abspath(os.path.expanduser(args.out))
    if os.path.exists(out) and os.listdir(out):
        sys.exit('输出目录不是空的：%s（换一个目录，避免覆盖）' % out)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(EXAMPLES, 'config.demo.json'), encoding='utf-8') as f:
        demo = json.load(f)
    demo.update({'vault': os.path.join(out, 'vault'), 'inbox': os.path.join(out, 'inbox'),
                 'state': os.path.join(out, 'inbox', '.sbp-state')})
    cfg_path = os.path.join(out, 'config.json')
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(demo, f, ensure_ascii=False, indent=2)
    cfg = load_config(cfg_path)
    raw = os.path.join(EXAMPLES, 'raw')
    getnote.collect(cfg, os.path.join(raw, 'getnote', 'notes.json'), os.path.join(raw, 'getnote', 'transcripts'))
    dingtalk.collect(cfg, os.path.join(raw, 'dingtalk', 'minutes.json'), os.path.join(raw, 'dingtalk', 'transcripts'))
    feishu.collect(cfg, os.path.join(raw, 'feishu', 'docs'))
    apple_notes.collect(cfg, os.path.join(raw, 'apple_notes', 'export-stream.txt'))
    import_inbox(cfg)
    rebuild_index(cfg)
    print('\n✅ 演示完成。用 Obsidian 打开这个文件夹看看：%s' % cfg['vault'])


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m sbp', description='第二大脑汇聚管线 v%s' % __version__)
    ap.add_argument('--config', default='config.json', help='配置文件路径（默认 ./config.json）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('collect', help='采集到收件箱')
    c.add_argument('source', choices=['getnote', 'dingtalk', 'feishu', 'apple-notes'])
    c.add_argument('--notes-json', help='得到大脑：已导出的笔记清单 JSON（离线）')
    c.add_argument('--minutes-json', help='钉钉：已导出的听记清单 JSON（离线）')
    c.add_argument('--transcripts-dir', help='得到大脑 / 钉钉：已导出的转写目录（离线）')
    c.add_argument('--docs-dir', help='飞书：已拉取的文档 JSON 目录（离线）')
    c.add_argument('--stream', help='苹果备忘录：已导出的文本流（离线）')

    sub.add_parser('feishu-auth', help='飞书浏览器授权')
    sub.add_parser('classify', help='预览分类结果')
    i = sub.add_parser('import', help='分类入库并重建目录页')
    i.add_argument('--dry-run', action='store_true', help='只预览，不写文件')
    i.add_argument('--update', action='store_true', help='已入库的笔记也用收件箱里的新版本覆盖')
    i.add_argument('--force', action='store_true', help='目录页没有自动区块标记时也按模板重写')
    x = sub.add_parser('index', help='只重建目录页')
    x.add_argument('--force', action='store_true')
    r = sub.add_parser('replace', help='词条替换（纠错 / 代号化）')
    r.add_argument('--path', help='要处理的目录（默认整个库）')
    r.add_argument('--mapping', help='替换词条 JSON：[{"from": "...", "to": "...", "guard": "可选正则"}]')
    r.add_argument('--apply', action='store_true', help='原地修改（默认只预览）')
    r.add_argument('--out', help='写到一份副本目录，原库不动（适合导出可分享版本）')
    r.add_argument('--rename', action='store_true', help='文件名和文件夹名也一起替换')
    sub.add_parser('sync', help='一键增量同步')
    d = sub.add_parser('demo', help='用虚构数据演示整条管线')
    d.add_argument('--out', default='./sbp-demo', help='演示输出目录（必须为空或不存在）')
    args = ap.parse_args(argv)

    if args.cmd == 'demo':
        return cmd_demo(args)
    if not os.path.exists(args.config):
        sys.exit('找不到配置文件 %s：先把 config.example.json 复制成 config.json 并修改' % args.config)
    cfg = load_config(args.config)
    try:
        if args.cmd == 'collect':
            cmd_collect(cfg, args)
        elif args.cmd == 'feishu-auth':
            feishu.auth(cfg)
        elif args.cmd == 'classify':
            cmd_classify(cfg, args)
        elif args.cmd == 'import':
            cmd_import(cfg, args)
        elif args.cmd == 'index':
            rebuild_index(cfg, force=args.force)
        elif args.cmd == 'replace':
            cmd_replace(cfg, args)
        elif args.cmd == 'sync':
            cmd_sync(cfg, args)
    except RuntimeError as e:
        sys.exit('❌ %s' % e)


if __name__ == '__main__':
    main()
