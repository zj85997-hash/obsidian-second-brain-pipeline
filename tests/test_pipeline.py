# -*- coding: utf-8 -*-
"""运行：python3 -m unittest discover -s tests -v（全部离线，不访问网络、不调用外部命令）"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sbp import __main__ as cli                                   # noqa: E402
from sbp.core import (apply_replacements, classify, compile_replacements, load_config,  # noqa: E402
                      sanitize, unique_path)
from sbp.sources import apple_notes, dingtalk, feishu, getnote, runner  # noqa: E402
from sbp.vault import AUTO_END, AUTO_START, import_inbox, rebuild_index, replace_in_tree  # noqa: E402


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def run_demo(out):
    quiet(cli.main, ['demo', '--out', out])
    return load_config(os.path.join(out, 'config.json'))


def files_under(root):
    out = set()
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            out.add(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, '/'))
    return out


class DemoPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, 'demo')
        # 演示必须完全离线：一旦调用外部命令就让测试失败
        self._orig = runner.run_json
        runner.run_json = lambda *a, **k: self.fail('demo 不应调用外部命令')
        self.cfg = run_demo(self.out)
        self.vault = self.cfg['vault']

    def tearDown(self):
        runner.run_json = self._orig
        self.tmp.cleanup()

    def test_notes_land_in_expected_folders(self):
        files = files_under(self.vault)
        p01 = '01-客户项目/案例P01-某家居品牌·AI客服落地/'
        expected = {
            p01 + 'P01 家居品牌 AI 客服需求沟通.md',     # 标题规则
            p01 + 'P01 客服知识库搭建方案.md',           # 飞书
            p01 + '钉钉文件转写-2026-03-12 19-30.md',    # 钉钉「文件」改名 + 内容规则
            p01 + '周五下午和 A003 的电话.md',           # 人工覆盖
            '01-客户项目/案例P02-某职业院校·AI课程共建/P02 校企课程共建研讨.md',
            '02-课程与培训/AI 训练营第一天课堂记录.md',
            '02-课程与培训/公开课讲稿：两小时搭建第二大脑.md',
            '03-学习与思考/读书会分享：《原则》读后感.md',
            '03-学习与思考/会议纪要自动化的想法.md',    # 苹果备忘录
            '15-日记与复盘/26.03.02.md',                # 飞书日记（标题是日期）
            '15-日记与复盘/2026.3.9.md',                # 备忘录日记
            '99-存档/测试录音.md',
        }
        self.assertTrue(expected <= files, expected - files)
        notes = [f for f in files if not os.path.basename(f).startswith(('_', '🏠', '🔒'))]
        self.assertEqual(len(notes), 12)
        self.assertFalse(any('删掉的草稿' in f for f in files))   # 「最近删除」被跳过
        self.assertIn('🔒 代号对照表.md', files)

    def test_frontmatter_and_body(self):
        p = os.path.join(self.vault, '01-客户项目/案例P02-某职业院校·AI课程共建/P02 校企课程共建研讨.md')
        text = read(p)
        self.assertIn('项目线: 01-客户项目', text)
        self.assertIn('案例: 案例P02-某职业院校·AI课程共建', text)
        self.assertIn('日期: 2026-03-05', text)
        self.assertIn('来源: 钉钉听记', text)
        # 同一发言人的连续段落被合并
        self.assertIn('🔴 A004 [00:00:00]\n学校这学期想开一门 AI 电商实战课，大概 60 课时，学生以大二为主。', text)
        self.assertIn('🟢 A002 [00:00:15]', text)

    def test_import_is_idempotent(self):
        stats, written = quiet(import_inbox, self.cfg)
        self.assertEqual(written, [])
        self.assertEqual(sum(stats.values()), 12)

    def test_index_keeps_handwritten_content(self):
        page = os.path.join(self.vault, '01-客户项目/案例P01-某家居品牌·AI客服落地/_案例首页.md')
        text = read(page)
        self.assertIn(AUTO_START, text)
        self.assertIn('共 4 篇笔记', text)
        text = text.replace('## 🎯 背景与需求\n\n（待补全）', '## 🎯 背景与需求\n\n手写的背景，不能被覆盖。')
        with open(page, 'w', encoding='utf-8') as f:
            f.write(text)
        quiet(rebuild_index, self.cfg)
        self.assertIn('手写的背景，不能被覆盖。', read(page))

    def test_index_never_overwrites_page_without_markers(self):
        home = os.path.join(self.vault, '🏠 首页.md')
        with open(home, 'w', encoding='utf-8') as f:
            f.write('# 我自己写的首页\n')
        quiet(rebuild_index, self.cfg)
        self.assertEqual(read(home), '# 我自己写的首页\n')
        quiet(rebuild_index, self.cfg, force=True)
        self.assertIn(AUTO_END, read(home))

    def test_replace_preview_copy_and_rename(self):
        target = os.path.join(self.vault, '02-课程与培训/AI 训练营第一天课堂记录.md')
        before = read(target)
        self.assertIn('Cloud Code', before)
        compiled = compile_replacements(self.cfg['replacements'])
        n_files, n_total = quiet(replace_in_tree, self.vault, compiled, apply_replacements)
        self.assertGreaterEqual(n_files, 2)
        self.assertEqual(read(target), before)       # 预览不改文件
        # 导出一份「P01 → 客户甲」的副本，文件名和链接一起替换，原库不动
        out = os.path.join(self.tmp.name, 'share')
        quiet(replace_in_tree, self.vault, compile_replacements([{'from': 'P01', 'to': '客户甲'}]),
              apply_replacements, out=out, rename=True)
        share = files_under(out)
        self.assertIn('01-客户项目/案例客户甲-某家居品牌·AI客服落地/客户甲 客服知识库搭建方案.md', share)
        self.assertFalse(any('P01' in f for f in share))
        self.assertNotIn('🔒 代号对照表.md', share)                           # 对照表不进副本
        home = read(os.path.join(out, '🏠 首页.md'))
        self.assertIn('[[01-客户项目/案例客户甲-某家居品牌·AI客服落地/_案例首页|', home)
        self.assertTrue(os.path.exists(os.path.join(self.vault, '01-客户项目/案例P01-某家居品牌·AI客服落地')))

    def test_replace_in_place_with_rename(self):
        compiled = compile_replacements([{'from': 'P02', 'to': 'P99'}])
        quiet(replace_in_tree, self.vault, compiled, apply_replacements, apply=True, rename=True)
        files = files_under(self.vault)
        self.assertIn('01-客户项目/案例P99-某职业院校·AI课程共建/P99 校企课程共建研讨.md', files)
        self.assertFalse(os.path.exists(os.path.join(self.vault, '01-客户项目/案例P02-某职业院校·AI课程共建')))


class UnitTest(unittest.TestCase):
    CFG = {'default_line': '90-待归类',
           'overrides': {'特殊标题': ['A线', None]},
           'rules': [{'line': '99-存档', 'title': '测试录音'},
                     {'line': '01-客户', 'case': '案例P01', 'title': 'P01'},
                     {'line': '01-客户', 'case': '案例P01', 'content': '家居'}]}

    def test_classify_order(self):
        self.assertEqual(classify('特殊标题', '', self.CFG), ('A线', None))
        self.assertEqual(classify('P01 周会', '', self.CFG), ('01-客户', '案例P01'))
        self.assertEqual(classify('无题', '聊了家居门店', self.CFG), ('01-客户', '案例P01'))
        self.assertEqual(classify('无题', 'x' * 700 + '家居', self.CFG), ('90-待归类', None))  # 只看前 600 字
        self.assertEqual(classify('测试录音', '家居', self.CFG), ('99-存档', None))          # 标题规则优先

    def test_replacement_guard_and_order(self):
        compiled = compile_replacements([
            {'from': '小明', 'to': 'A001', 'guard': '小明(?!星)'},
            {'from': '小明同学', 'to': 'A002'}])
        text, detail = apply_replacements('小明同学和小明，还有小明星', compiled)
        self.assertEqual(text, 'A002和A001，还有小明星')
        self.assertEqual(sum(detail.values()), 2)

    def test_sanitize_and_unique_path(self):
        self.assertEqual(sanitize(' a/b:c*?\n '), 'a b c')
        self.assertEqual(sanitize(''), '无标题')
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'x.md'), 'w').close()
            self.assertEqual(os.path.basename(unique_path(d, 'x')), 'x-2.md')

    def test_dingtalk_markdown(self):
        md = dingtalk.paragraphs_to_markdown([
            {'nickName': 'A001', 'startTime': 0, 'paragraph': '你好，'},
            {'nickName': 'A001', 'startTime': 1000, 'paragraph': '开始吧。'},
            {'nickName': 'A002', 'startTime': 3723000, 'paragraph': '好。'}])
        self.assertEqual(md, '🔴 A001 [00:00:00]\n你好，开始吧。\n\n🟢 A002 [01:02:03]\n好。')

    def test_getnote_list_formats(self):
        obj = {'data': {'notes': [{'note_id': '1'}]}}
        self.assertEqual(len(getnote.parse_note_list(obj)), 1)
        self.assertEqual(len(getnote.parse_note_list(json.dumps(obj))), 1)
        ndjson = '{"note_id": "1"}\n{"note_id": "2"}\n'
        self.assertEqual([n['note_id'] for n in getnote.parse_note_list(ndjson)], ['1', '2'])

    def test_feishu_guess_date(self):
        cfg = {'timezone_offset_hours': 8}
        self.assertEqual(feishu.guess_date({'name': '26.03.02'}, cfg), '2026-03-02')
        self.assertEqual(feishu.guess_date({'name': '2026-3-9'}, cfg), '2026-03-09')
        self.assertEqual(feishu.guess_date({'name': '方案', 'modified': '1772456400'}, cfg), '2026-03-02')

    def test_apple_notes_stream(self):
        text = ('@@@FOLDER@@@03__灵感\n[{"name": "甲 乙", "id": "1", "body": "<div>正文</div>"}]\n'
                '@@@FOLDER@@@日记\n@@@CHUNK@@@\n[{"folder": "日记", "name": "丙", "id": "2"}]\n@@@DONE@@@TOTAL:2\n')
        items = apple_notes.parse_stream(text)
        self.assertEqual([(f, n['name']) for f, n in items], [('灵感', '甲 乙'), ('日记', '丙')])

    def test_feishu_requires_env_credentials(self):
        saved = {k: os.environ.pop(k, None) for k in ('FEISHU_APP_ID', 'FEISHU_APP_SECRET')}
        try:
            with self.assertRaises(RuntimeError):
                feishu.credentials(feishu.settings({}))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == '__main__':
    unittest.main()
