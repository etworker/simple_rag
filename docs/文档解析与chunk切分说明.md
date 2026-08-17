# 文档解析与 chunk 切分说明

> simple_rag 的文档解析、chunk 切分、向量检索、版本对比细节说明。
> 更新日期：2026-08-16（表格入检索、上下文扩展、跨页合并、LLM 页眉清洗）

---

## 1. 核心结论（一句话）

- **chunk = 段落（Paragraph）**：一个段落文本就是一个检索单元（一个向量）。
- **段落按语义切分**（不是固定长度、不是按行）：章节标题/空行/句末标点/长度上限作为切分信号。
- **表格也参与向量检索**：每个表格转成一段结构化文本（markdown 行拼接）加入索引（08-16 新增）。
- 每段/每表带**定位信息**：页码、章节号、章节标题、来源文件。

---

## 2. 段落切分（chunk 生成）

### 2.1 规则后端（pdfplumber / pymupdf）—— 流式分段

流程（bk_pdfplumber / bk_pymupdf 共用 doc_parser._text）：

```
1. 每页提取文字行（带坐标），排除页眉/页脚区域、表格区域、高频重复行
2. 全部行拼接为"正文流"（记录每页的字符偏移边界 page_boundaries）
3. split_stream 在流上按语义信号分段：
   ① 章节标题行前断开（标题独占一段）
   ② 行内标题粘连拆分（正文句号后紧跟章节标题）
   ③ 连续空行断开
   ④ 句末终止符（。！？）+ 段落达到一定长度 → 断开
   ⑤ 长度上限（max_paragraph_length 默认 600）强制断开
4. segment_and_locate：每段反查页码、追踪章节号/章节标题
```

**切分信号优先级**：章节标题 > 行内标题 > 空行 > 句末标点+长度 > 强制长度。

> 08-16 修复：**"；"（分号）不再作为段落断点**——它是列表项分隔符，
> 以"；"结尾的列表项（如巡检项 a/b/c）跨页时不应被拆碎。行内标题拆分
> 改用独立字符集 `inline_title_end_chars`（含"；"，保留"正文…；第二章 标题"同行拆分）。

### 2.2 深度学习后端（docling）—— 布局模型段落

- docling 布局模型输出 TextItem（语义段落），直接作为 Paragraph；SectionHeaderItem 作为标题段落。
- 后处理：
  - **碎尾合并**：布局模型把同一视觉段落拆成多个 TextItem 时，同页相邻、前段不以句末标点结尾、后段 <60 字 → 合并（防"整句拆散"）。
  - **页眉前缀剥离**：每页顶部手册名被拼到段首时，按段首高频前缀剥离（防"手册名前缀"假差异）。

### 2.3 段落 = chunk 的边界约定

| 项 | 说明 |
|----|------|
| chunk 内容 | 整段文本（通常 10~600 字，多为 40-200 字） |
| 不做 | 固定字数切块、句子级切块、滑窗重叠 |
| 为什么 | 企业制度文档段落天然语义完整；句子级切块会拆散"条款+说明"的语义 |
| 已知风险 | PDF 跨页断句/排版导致段落边界不完美（有碎尾合并/页眉剥离/跨页列表合并兜底） |

### 2.4 跨页问题与处理（08-16 修复）

PDF 文本层按视觉行/词拆分，跨页时：
- **中文断字**（"安全防护策" + "略，阻止…"）→ 行拼接时中文间不加空格（`join_cjk_lines`），还原"策略"
- **跨页列表**（旧版 §5.1.7 巡检项 a/b/c 在第 87 页、d/e/f 在第 88 页）→
  - "；"不打断段落（分号是列表项分隔符）
  - 页眉/页脚判断加 y_tolerance 容差（覆盖恰好落在 margin 边界的页眉）
  - noise 行（修订日期等）在段落累积中跳过而不打断
  → 跨页列表合并为完整段落，避免"两版拆分点不同 → 假差异"

---

## 3. 表格处理与检索

| 项 | 说明 |
|----|------|
| 对象 | 每个表格一个 Table 对象 |
| 内容 | rows 二维数组（list[list[str]]）+ headers（表头行，可选）+ context_before（表格前上下文） |
| 跨页 | 跨页表格自动合并为单个 Table（同文件+页码连续+列数接近），跳过重复表头 |
| 存储 | 解析结果保留 Table，供 Web 预览与版本对比 |
| **向量检索** | **08-16 新增：表格转成 markdown 结构化文本（`_tables_to_paragraphs`）加入向量索引**，location 标"表格: 章节"。问"修订记录表里 5.1-1~5 是什么"也能命中 |
| 版本对比 | 表格按表头相似度配对，逐行 diff（行增删/单元格修改/列增删） |

**表格转文本格式**（加入索引的 chunk 内容）：

```markdown
> 表格前上下文（如有）

| 列1 | 列2 | 列3 |
|-----|-----|-----|
| a   | b   | c   |
```

---

## 4. 定位信息

| 字段 | 示例 | 说明 |
|------|------|------|
| page | 3 | 起始页码（Word 为 0） |
| page_end | 5 | 跨页段落结束页码 |
| chapter | 2.3 | 章节号 |
| chapter_title | 备份策略 | 章节标题 |
| location | 第3-4页 / §2.3 / 备份策略 | 只读属性（前端显示用）；表格为"表格: 章节" |
| source_file | 手册.pdf | 来源文件 |

---

## 5. RAG 检索链路（问答）

### 5.1 检索流程（DocStore.search）

```
1. 用户问题 → embedding（同一模型 bge-small-zh-v1.5，GPU auto）
2. FAISS 内积检索（向量已归一化，等价余弦相似度）
3. 取 top_k（默认 5），过滤 similarity_threshold（默认 0.5）以下
4. 返回 RetrievedChunk（text/source_file/location/score/paragraph_index）
```

### 5.2 上下文扩展（08-16 新增）

- 默认 `retrieval.context_radius = 2`：每个命中 chunk 扩展**同文档前后 2 个相邻段落**。
- 目的：段落切分可能把完整语义拆成相邻几段（如"巡检内容如下："和列表项分属两段），
  单段命中时 LLM 只见局部。扩展后 LLM 看到完整上下文。
- 实现：`doc_store.get_neighbor_texts(global_index, radius)` 按全局索引定位同文档相邻段落。
- 关闭：`retrieval.context_radius = 0`。

### 5.3 冲突检测

- 检索结果两两配对（Jaccard 2-gram 门控 + LLM 确认），发现矛盾时在答案中醒目提示。

### 5.4 上下文拼给 LLM

```
[1] 文件名 | 位置
段落文本（已含上下文扩展）
[2] ...
```

- 编号 [1][2] 供 LLM 引用，答案中的 [1] 对应底部来源 B1。
- 来源溯源：source_list 带 text 前 200 字、文件、位置、score。

---

## 6. 与版本对比的关系

- 版本对比（version_compare）在**段落级**配对（语义向量）+ 字级 diff。
- chunk 切分质量直接影响差异质量：
  - 段落被拆散（整句拆断）→ 同一内容出现"删除 X + 新增 Y"（已用碎尾合并/二次配对缓解）
  - 页眉/标题拼入段落 → "前缀增删"假差异（已用页眉剥离缓解）
  - **跨页列表拆分点不同** → "新增/删除列表项"假差异（08-16 已修复：分号不断段 + 页眉容差 + noise 不打断）
- **命中是否扩展上下文**：版本对比的段落配对是**逐段独立**的，不扩展相邻段落——
  差异报告按段落粒度展示。但 LLM 判定差异时会把旧/新全文给 LLM（本身含完整段落）。
- 表格对比独立于段落（表头配对 + 行级 diff）。

---

## 7. 页眉/页脚识别（LLM 辅助，08-16）

### 7.1 规则层（第一道防线，默认）

- **坐标边界**：header_margin_pct / footer_margin_pct（默认 8%），加 y_tolerance 容差
- **高频重复行**：出现 ≥ repeat_line_threshold_pct（30%）页数的行判为页眉/页脚残留
- **noise 行**：修订日期/版本号/页码等模式剥离（不打断段落）

### 7.2 LLM 研判层（第二道兜底，可选）

> 规则对"表格区域内绕过坐标剔除的页眉"、不同文档版式变化可能失效。
> 08-16 新增 `rag_server/app/services/llm_cleanup.py`：

- 规则预筛：段首匹配"手册名/公司名"等疑似页眉前缀 → 候选
- LLM 确认：把候选段首打包给 LLM（一次调用），逐条判断"页眉残留应剥离 / 正文应保留"
- 确认后剥离段首页眉前缀
- 启用：`parse_cleanup.enabled = true`（默认 false；开启后解析结果变化，需重置知识库）

---

## 8. 配置项（chunk / 检索相关）

| 配置键 | 默认 | 说明 |
|--------|------|------|
| extract.min_paragraph_length | 10 | 短于此的段落丢弃 |
| extract.max_paragraph_length | 600 | 段落超过此长度强制断开 |
| extract.sentence_break_min_length | 40 | 句末标点断段的段落最小长度 |
| extract.sentence_end_chars | 。！？.!？ | 句末终止符集合（不含"；"） |
| extract.inline_title_end_chars | 。；！？.!？ | 行内标题拆分终止符（含"；"） |
| extract.header_margin_pct | 8 | 页眉区域百分比（y_tolerance 容差内） |
| extract.footer_margin_pct | 8 | 页脚区域百分比 |
| extract.repeat_line_threshold_pct | 30 | 重复行判页眉页脚阈值 |
| extract.docling_merge_split_paras | True | docling 碎尾合并开关 |
| extract.docling_strip_header_prefix | True | docling 页眉前缀剥离开关 |
| pre_review.parse_backend | pdfplumber | PDF 解析后端 |
| retrieval.top_k | 5 | 检索返回段落数 |
| retrieval.similarity_threshold | 0.5 | 检索相似度下限 |
| **retrieval.context_radius** | **2** | **命中段落前后扩展段数（0=关闭）** |
| **parse_cleanup.enabled** | **false** | **LLM 页眉清洗开关** |
| parse_cleanup.llm_profile | pre_review | LLM 页眉研判用的 profile |

---

## 9. 与 Embedding 的关系

- embedding 模型：bge-small-zh-v1.5（512 维，默认）/ jina-v2-base-zh（768 维，备选），GPU auto。
- 段落（含表格转文本）逐条 embedding → 归一化 → FAISS。
- 向量缓存（VectorStore）：按"内容哈希 + 段落数 + 配置哈希"缓存，配置/模型变化自动失效。
- 详见 docs/Embedding模型选型.md。
