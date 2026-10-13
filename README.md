# ai-frontier · AI 前沿每日雷达

每天早上把 **AI / 大模型领域过去 24 小时真正值得知道的新东西**找出来，
写成带判断的报告，输出 Markdown + Word/PDF + Excel 三种格式。

**不需要任何 API Key。** 采集全部走公开接口和 RSS。

---

## 快速开始

**最省事**：双击桌面的 **`AI前沿雷达`**，或项目里的 `启动AI前沿雷达.cmd`。
它打开一个界面（浏览器里，本机服务，没有黑框），里面就是三个按钮：

1. **获取今天信息** —— 约 45 秒，带进度条和实时日志；完成后可直接打开清单
2. **打开 Excel 清单 / 打开可读清单** —— 看采集结果
3. **生成报告** —— 选主题、分数下限、条数上限、分组方式，生成结构化报告（可选同时出 Word/PDF）

底部「过去一周」按日期列出每天的清单和报告。右下角「退出程序」关掉后台服务
（直接关标签页不会退，方便你以后再打开）。

### 第 3 个按钮的边界（重要，别误用）

界面上的**生成报告**做的是**本地筛选与整理**：按你的条件切候选清单，
逐条列出标题、摘要、热度、命中信号和原文链接。

它**不联网核实，也不判断重要性** —— 那需要读懂内容后再去搜索验证，
只有 AI 能做。所以：

| 你要的 | 用哪个 |
|---|---|
| 快速扫一遍今天有什么、自己二次筛选 | 界面上的**生成报告**（本地，立刻出） |
| 带「为什么重要 / 不确定的地方」的分析 | 在 DSH 会话说 **「跑一下今天的 AI 前沿日报」** |

这段话界面上也写了，不会让你误用。

**命令行方式**（等价，适合脚本和排查）：

```cmd
cd ai-frontier
run.cmd collect        :: 采集一轮，约 45 秒
run.cmd status         :: 看今天采到了什么
run.cmd digest         :: 打开候选清单
```

`src/app_server.py` 也可以直接跑：`python src/app_server.py --port 8321`。

---

## 自动化：每天 8:00 采集

```powershell
powershell -ExecutionPolicy Bypass -File install_task.ps1            # 默认 08:00
powershell -ExecutionPolicy Bypass -File install_task.ps1 -Time 07:30
powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove    # 卸载
```

**必须理解这个任务的边界：它只负责采集，不生成报告。**

原因是写报告需要联网核实和领域判断，那一步必须在 DSH 会话里做。
所以 8:00 的任务保证的是"**数据已就绪**"，你还是得说一句才拿得到报告。

任务实际运行的是 `daily_collect.cmd`（不是直接指向 Python），
日志滚动写在 `out\logs\task-last.log`，按天归档的诊断在
`out\logs\collect-<日期>.json`。

### 安装脚本替你改掉了三个 Task Scheduler 默认值

`schtasks` 的默认设置会让任务**永远停在 Queued 而且不报错**：

| 默认值 | 后果 | 已改为 |
|---|---|---|
| `DisallowStartIfOnBatteries = True` | 笔记本用电池时任务不启动，状态一直 Queued | 允许电池供电 |
| `StopIfGoingOnBatteries = True` | 跑到一半拔电源就被杀掉 | 不因断电停止 |
| `StartWhenAvailable = False` | 错过触发时刻（8 点机器没开）就永久跳过 | 错过后补跑 |

这是最典型的**静默失败**：任务看起来配好了，实际一次都没跑。
如果你自己用 `schtasks` 建任务，注意这三个默认值。

---

## 目录结构

```
config/
  sources.json   ① 数据层：20 个信息源 + 抓取参数（全部实测过）
  topic.py       ⓞ 任务定义：追踪什么、什么算重要、评分权重
src/
  net.py         网络访问：重试、并发、TLS、整体时限
  sources.py     解析：原始响应 → 统一 Item
  scoring.py     ⓪ 评估：时间过滤、事件去重、7 维打分
  collect.py     ③ 工作流：编排 + 产出
  report.py      ④ 分发：Markdown / Excel
  make_docx.py   ④ 分发：Markdown → Word / PDF
  status.py      运维：一眼看清今天采到了什么
data/state.json  过去 30 天"已见过"的条目（用于算"首次出现"）
out/
  candidates/    候选 JSON + Excel（每日一份 + latest 副本）
  digests/       人读的候选清单
  reports/       最终报告（由技能生成）
  logs/          每轮运行的诊断日志
```

---

## 信息源（全部实测，不是"听说能用"）

| 类别 | 源 |
|---|---|
| 论文 | arXiv cs.CL / cs.LG / cs.AI（RSS）、HuggingFace Daily Papers |
| 开源 | HuggingFace 模型趋势榜、GitHub 高星新仓库（两个查询） |
| 社区 | Hacker News（4 个关键词，带 points/评论数） |
| 官方 | OpenAI、Anthropic、Google DeepMind |
| 实验室 | Microsoft Research、BAIR、MIT CSAIL |
| 分析 | Interconnects、Sebastian Raschka、Simon Willison、Lilian Weng、The Gradient、Import AI |
| 媒体 | The Decoder |

### 被剔除的源（以及为什么）

这些源看起来都很合理，但实测拿不到正文。**"条数多"和"能用"是两回事。**

| 源 | 原因 |
|---|---|
| HuggingFace Blog | 876 条里 0 条含正文，纯摘要（摘要就是抄标题） |
| 量子位 | 10 条 0 条可用 |
| 机器之心 | RSS 返回 HTML 首页 |
| 新智元 | HTTP 404 |
| Anthropic RSS | HTTP 404（改抓新闻页） |
| **arXiv 官方 API** | **HTTP 429 限速**，改用 rss.arxiv.org |
| arXiv RSS cs.CV | 实测返回 0 条 item |
| Reddit r/LocalLLaMA | 本机 403，无法稳定访问 |
| Import AI | Substack 拒绝本机访问（保留在配置里，失败不影响整体） |

---

## 这套东西的关键设计决定

### 1. 分数是本轮相对分位，不是绝对质量分

各分项都有地板值（源权重、新鲜度、首次出现几乎条条满分），
直接加权求和会让绝大多数条目挤在 60-70 区间，排序看不出差别。
所以按本轮最高分归一化到 100。

**代价：跨天比较分数没有意义。** 同一天内排序才有意义。

### 2. "独立印证" 和 "渠道收录" 是两件事

- **独立印证**：有几个【互相独立】的来源团体报道了同一件事。
  一篇 arXiv 论文同时挂 cs.CL/cs.LG/cs.AI，三个源抓到它 —— **只算 1 个**，
  因为那是同一份预印本的交叉列表，不是三个来源的印证。
- **渠道收录**：同一生态内被几个渠道收到（arXiv 收录 + HuggingFace Daily
  人工选中 = 2）。它不等于独立印证，但被人工挑选本身是质量信号。

实测结果：**独立印证常常是 0**。这是诚实的结果而不是故障 ——
现阶段绝大多数条目只出现在 arXiv 一条线上。这个信号要等来源变多才有用。

### 3. 时间窗口有下限（72 小时），且这是故意的

用户要的是"过去 24 小时"。但 arXiv 和实验室博客是**按天公告**的，
纯粹按 24 小时切，很容易一条都不剩（取决于几点跑）。

所以窗口 = `max(24h, 72h)`，但对 24 小时之外的条目**扣新鲜度分**，
让它们排在最后而不是直接丢弃。

**真正做到"只看过去 24 小时"的办法是每天定时跑**（见下面的定时任务），
不是把窗口设死。

### 4. 抓取有整体时限（75 秒）

这台机器的 VPN 会做 TLS 中间人（所以 `insecure_tls: true`），
并且路径抖动频繁：同一网址这次超时、下次就好。

没有整体时限时，个别站点一路卡到超时会把一轮采集拖到 **150 秒以上**。
有闸门之后是 **45 秒左右**。宁可丢掉一两个慢源 —— 它们第二天自然会回来，
但当天日报必须能跑出来。

### 5. 已知会抖动的源排在最前面派发

线程池按提交顺序派发，把 flaky 源放前面才能拿到完整的重试窗口，
而不是死在整体时限上。

### 6. 失败要大声

- 每个源单独记录成功/失败，一个源坏掉不影响整轮
- 候选清单里有一节 **「来源体检」**，❌ 就是今天没产出的源
- 报告里必须写清今天哪些源没跑出来

**源会悄悄死掉而不报错。** 上一版项目最有价值的发现就是这个：
Wired 返回 50 条但 0 条可用 —— 它不报错，只是安静地给你没用的东西。

---

## 怎么换成别的领域

不必改代码，改两个配置就够：

**`config/sources.json`** —— 把 `sources` 换成新领域的源。
加源之前**一定先验证它给不给你正文**（正文长度中位数 < 200 字的源不能用）。

**`config/topic.py`** —— 改 `TOPICS`（主题标签）、`SALIENCE`（重要信号词典）、
`SCORING`（评分权重）。词典里越具体、越难伪造的信号权重越高；
"ai" 这类词几乎不该给分。

`src/` 下面六层里，**网络层、解析框架、去重、打分机制、报告渲染全都不用动** ——
它们跟领域无关。这就是把结构分层的意义：换任务时你知道哪几层要重写。

---

## 换一台机器 / 给别人用

代码里为**本机**写死了几处默认路径（都有环境变量可以覆盖，不用改代码）：

| 位置 | 写死了什么 | 怎么覆盖 |
|---|---|---|
| `src/make_docx.py` | DSH 自带 LibreOffice Kit 的 node 和 cli 路径 | `AF_LO_NODE` / `AF_LO_CLI` |
| `run.cmd` `daily_collect.cmd` `启动AI前沿雷达.cmd` | Python 解释器候选路径（先找 DSH 运行时，再退回 PATH） | `AF_PYTHON` |
| `install_task.ps1` | 同上 | `AF_PYTHON` |

**为什么要优先 DSH 自带的 Python**：它才装了 `openpyxl` 和 `python-docx`。
系统 PATH 里的 Python 通常没有这两个库，选了它 Excel 和 Word 会失败
（或报 `ModuleNotFoundError`）。这个坑实测踩过，脚本里已经按"DSH 优先"排。

**LibreOffice 不要用系统装的**：`make_docx.py` 只调 DSH 随应用分发的
LibreOffice Kit。如果找不到会明确报错并让你只交 Markdown，
而不是偷偷换一个渲染器 —— 换渲染器会让排版结果不可预期。

### 换领域不用改代码

`config/sources.json`（换信息源）+ `config/topic.py`（换主题标签和信号词典）。
`src/` 下的网络层、解析框架、去重、打分、报告渲染都与领域无关。
加源之前**一定先用 `run.cmd collect --only <源id>` 验证它给不给你正文**。

---

## 常见问题

**Q: 为什么有些源总是失败？**
这台机器的网络环境对某些站点（Substack、部分 Google 域）不稳定，
或对方做了反爬。这些源标了 `flaky: true`，失败被接受。
想排查某个源，用 `run.cmd collect --only <源id>`。

**Q: 候选有 120 条，太多读不完。**
候选清单是给机器和检索用的**素材池**，不是给人读的最终产物。
人读的是技能生成的 `out/reports/<日期>.md`，里面只留 8-15 条。

**Q: 为什么 `data/state.json` 要留着？**
它记录过去 30 天出现过的条目，用来算"首次出现"这个分数。
删掉不会出错，只是所有条目都会被当成新的。

**Q: 能不能不要 Excel？**
`run.cmd collect --json-only`。

**Q: 报错 `ModuleNotFoundError: No module named 'docx'` 或 `openpyxl`？**
选错 Python 了。必须用 DSH 自带的运行时（它带这两个库），
系统 PATH 里的 Python 通常没有。`run.cmd` 已经会自动优先选对；
如果你在别处手动调用，用 `set AF_PYTHON=<DSH python 路径>`
或直接看 `run.cmd help` 打印出来的 `Interpreter:`。

**Q: 报错 `EEXIST: file already exists` ？（已修复）**
LibreOffice 转换不会覆盖同名 PDF。`make_docx.py` 现在会先删旧产物。
如果你在别的脚本里自己调 LibreOffice，注意这个坑。

---

## 报告输出

`out/reports/` 下每个日期一份，同时留一份 `latest.md` 快照（方便引用和对比）：

| 文件 | 用途 |
|---|---|
| `out/reports/<日期>.md` | 日报正文（Markdown，按日期归档） |
| `out/reports/<日期>.docx` | Word 版，可转发/批注 |
| `out/reports/<日期>.pdf` | PDF 版，可打印/分发 |
| `out/reports/latest.md` | 最近一次的正文快照 |
| `out/candidates/<日期>.xlsx` | 候选清单表格，可排序筛选 |

三种格式的分工：
**Markdown** 用于检索和版本管理，**Word/PDF** 用于给别人看，
**Excel** 用于你自己二次筛选（含分数明细、来源体检、主题汇总四个工作表）。
