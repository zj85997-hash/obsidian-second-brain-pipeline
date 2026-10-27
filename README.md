# 第二大脑汇聚管线

> 把**得到大脑录音转写、钉钉听记、飞书文档、苹果备忘录**汇进一个 Obsidian 库，按「项目线 / 案例」自动分类，自动生成首页和案例目录。还能把人名、公司名一键换成代号，导出一份可以分享的版本。
> 零依赖 Python（只用标准库），所有数据都留在你自己的电脑上。

**Second Brain Pipeline for Obsidian** — collects voice-note transcripts (Get笔记 / DingTalk Minutes), Feishu docs and Apple Notes into one Obsidian vault, classifies them into project folders with regex rules, builds index pages, and can pseudonymize names before you share. Pure Python standard library. Docs are in Chinese.

---

## 为什么做这个

我的笔记散在四个地方：开会、上课用得到大脑和钉钉录音转写，写方案用飞书，随手记用苹果备忘录。时间一长，谁也找不到。

我用这套脚本把它们汇成一个 Obsidian 库，现在库里有 2400 多篇笔记：
- 得到大脑转写 800 多篇
- 苹果备忘录近 800 篇
- 飞书文档 400 多篇
- 钉钉听记几十篇

库按「项目线 → 案例」组织，每个案例有一张按月排列的时间线。这个仓库是它的通用版：我自己的分类规则和人名都拿掉了，换成可配置的模板和全部虚构的演示数据。

## 流程

```
得到大脑 ─┐
钉钉听记 ─┤  collect   ┌─────────┐  import（分类 + 入库）  ┌──────────────┐
飞书文档 ─┼──────────▶ │ 收件箱   │ ──────────────────────▶ │ Obsidian 库   │
苹果备忘录┘            │ inbox/  │                         │ 项目线/案例/  │
                       └─────────┘                         └──────┬───────┘
                                                                  │ index
                                              首页 · 板块总览 · 案例首页（按月时间线）
                                                                  │ replace --out
                                              可分享副本：真名 → A001 / P01 代号
```

- **收件箱**：每个来源先转成统一格式的 Markdown，带标题、日期、来源和 ID。同一条不会重复采集。
- **分类**：按配置里的正则规则把笔记放进「项目线 / 案例」文件夹。规则认不出来的进「待归类」，个别笔记可以按标题人工指定。
- **入库**：只新增文件，**默认不删除、不覆盖你库里已有的文件**。`import --update` 是例外：它会用收件箱里的新版本重写它自己导入过的笔记，你在这些笔记里的手改会丢，需要时再加。
- **目录页**：只更新页面里自动生成的那一段，**你手写的背景、总结原样保留**。`--force` 是例外：没有自动区块标记的目录页会按模板整页重写。

## 5 分钟试跑（不需要任何账号）

需要 Python 3.8 及以上：

```bash
git clone https://github.com/zj85997-hash/obsidian-second-brain-pipeline.git
cd obsidian-second-brain-pipeline
python3 -m sbp demo --out ./sbp-demo
```

用 Obsidian 打开 `sbp-demo/vault`。四个来源的 12 篇虚构笔记已经分好类：

```
🏠 首页.md
01-客户项目/
  案例P01-某家居品牌·AI客服落地/   ← 4 篇（标题规则、正文规则、人工指定都有）
  案例P02-某职业院校·AI课程共建/   ← 钉钉听记，按发言人合并段落
02-课程与培训/  03-学习与思考/  15-日记与复盘/  99-存档/
```

演示数据里的人名一律写成 A001、A002…，公司写成 P01、P02…。

## 用在自己的笔记

### 1. 写配置

```bash
cp config.example.json config.json
```

至少改三处：
- **路径**：`vault`（Obsidian 库）和 `inbox`（收件箱）。
- **lines**：你的项目线和案例。
- **rules**：分类规则。

配置文件里每一项都有说明。

### 2. 采集

| 来源 | 准备 | 命令 |
|---|---|---|
| 得到大脑 | 安装官方命令行 `npm i -g @getnote/cli`，再运行 `getnote auth` 登录 | `python3 -m sbp collect getnote` |
| 钉钉听记 | 安装并登录钉钉官方命令行 `dws` | `python3 -m sbp collect dingtalk` |
| 飞书文档 | 建一个只读的企业自建应用，设置环境变量 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`，运行 `python3 -m sbp feishu-auth` | `python3 -m sbp collect feishu` |
| 苹果备忘录 | 只能在 macOS 上用；第一次会弹窗请求「控制备忘录」权限 | `python3 -m sbp collect apple-notes` |

几点说明：
- 每个来源也支持离线导入已经导出的文件，比如 `--notes-json`、`--transcripts-dir`、`--docs-dir`、`--stream`，详见 `python3 -m sbp collect -h`。
- 采集是增量的，中断了重跑会从断点继续。

### 3. 分类入库

```bash
python3 -m sbp classify          # 先预览：每个文件夹会分到几篇，哪些规则没认出来
python3 -m sbp import --dry-run  # 再预览：每篇会放到哪里
python3 -m sbp import            # 正式入库，并重建目录页
```

日常只要一条命令：`python3 -m sbp sync`。它会在线采集 `sync_sources` 里配置的来源，再入库、重建目录。

### 4. 分类规则怎么写

```json
"rules": [
  {"line": "99-存档", "title": "空白录音|测试录音"},
  {"line": "01-客户项目", "case": "案例P01-某制造企业·AI培训", "title": "P01|培训考核"},
  {"line": "01-客户项目", "case": "案例P01-某制造企业·AI培训", "content": "P01"}
],
"overrides": {"周五下午的电话": ["01-客户项目", "案例P01-某制造企业·AI培训"]}
```

匹配顺序：
1. `overrides` 按完整标题人工指定。
2. 所有 `title` 规则从上往下，第一条命中就生效。
3. 所有 `content` 规则，只看正文前 600 字。
4. 都没命中，进 `default_line`。

## 人名、公司名代号化

给学员看库、发给别人之前，可以把真名换成代号，统一用**人名 A001 起、公司 P01 起**。对照关系写在配置的 `replacements` 里：

```json
"replacements": [
  {"from": "某真名", "to": "A001"},
  {"from": "某公司全称", "to": "P01"},
  {"from": "小明", "to": "A002", "guard": "小明(?!星)"}
]
```

```bash
python3 -m sbp replace                                # 预览：哪些文件、替换几处
python3 -m sbp replace --out ~/Desktop/可分享版 --rename # 导出一份代号化副本，原库不动
python3 -m sbp replace --apply                        # 原地替换（同一个功能也能用来批量纠正转写错字）
```

几点说明：
- `--rename` 会连文件名、文件夹名一起替换，正文里的 `[[链接]]` 同步改，链接不会断。
- `guard` 是可选的正则，用来避免误伤别的词。
- 代号和真名的对照写在 `private_mapping` 里，只会生成到你本机库里的 `🔒 代号对照表.md`，导出副本时自动排除。

## 安全与隐私

- **代码里没有任何凭据**：
  - 得到大脑、钉钉的登录由官方命令行工具管理。
  - 飞书应用凭据只从环境变量读取。
  - 飞书令牌存在 state 目录，文件权限 600，已被 `.gitignore` 忽略。
- **证书校验始终开启**：飞书接口全程校验 HTTPS 证书。公司网络有自签证书时，用 `ca_bundle` 配置或 `SSL_CERT_FILE` 环境变量指定证书，不要关掉校验。
- **只读访问**：飞书只申请只读权限；脚本不会修改、删除任何云端数据。
- **不提交个人数据**：`config.json`、收件箱、库、进度文件都在 `.gitignore` 里。

## 目录结构

```
├── sbp/                      # python3 -m sbp
│   ├── __main__.py           # 命令行
│   ├── core.py               # 配置、收件箱、分类规则、词条替换
│   ├── vault.py              # 入库、目录页、代号化导出
│   └── sources/              # getnote.py · dingtalk.py · feishu.py · apple_notes.py
├── scripts/export_apple_notes.jxa   # 备忘录导出脚本（macOS）
├── config.example.json       # 配置模板
├── examples/                 # 虚构演示数据（四个来源的原始格式）+ 演示配置
├── tests/                    # 单元测试（全部离线）
└── docs/index.html           # 项目介绍页（GitHub Pages）
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试全部离线运行，不访问网络，也不调用外部命令。覆盖以下内容：
- 四个来源的格式转换
- 分类顺序
- 重复入库
- 手写内容保护
- 代号化导出与改名

## 关于作者

付钟浩（Alex），AI 培训讲师、FDE（前沿部署工程师）。这套管线和我的《两小时炼出第二大脑》课是同一套思路：先把散落的笔记汇到一处，再按项目线分类，最后让目录自己长出来。

## 许可

[MIT](LICENSE)
