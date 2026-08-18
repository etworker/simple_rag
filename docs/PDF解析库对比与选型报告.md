# PDF 解析库对比与选型报告

> 目的：为 doc_parser 多后端插件架构挑选"又准确又快"的 PDF 解析候选。
> 依据：项目需求（docs/需求文档.md FR-1）、doc_parser 设计文档（doc_parser/docs/设计文档.md）、
> 公开基准 opendataloader-bench（200 份真实 PDF）与 pdfmux 200-PDF benchmark。
> 更新日期：2026-08-17

---

## 1. 项目对 PDF 解析的核心要求（摘自文档）

| 需求（docs/需求文档.md FR-1） | 对解析库的要求 |
|------|------|
| FR-1.2/1.3 输出带定位信息的段落+表格 | 结构化输出（非纯文本/markdown），能给出页面与章节定位 |
| FR-1.4 自动识别页眉/页脚 | 解析器需提供带坐标的文本行（坐标过滤） |
| FR-1.5 跨页表格自动合并 | 表格识别需能处理跨页（或解析后经 doc_parser 共享后处理合并） |
| FR-1.6 表格归属最近章节 | 版面结构/阅读顺序正确（表格不被拆散、顺序不乱） |
| 版面识别 | 章节标题层级、阅读顺序（reading order）正确 |
| 表格识别 | 有框线/无框线表格都能正确还原结构与单元格文本 |

doc_parser 现有架构（doc_parser/docs/设计文档.md §2）：后端插件化 ——
每个后端暴露 `extract_pdf_with_<backend>(filepath, config)`，输出统一 `Document(paragraphs, tables)`，
复用共享后处理（跨页表合并 / 章节检测 / 数字断字 / 模板表过滤）。

---

## 2. 评测方法

### 2.1 opendataloader-bench（主要依据）

[opendataloader-bench](https://github.com/opendataloader-project/opendataloader-bench) 是 2026 年维护的
文档结构/版面评测基准，**200 份真实 PDF**（学术论文、财报、合同、扫描件、政府文件），三个指标：

- **NID（阅读顺序）**：抽取文本与真值的归一化编辑距离
- **TEDS（表格结构）**：表格 DOM 树编辑距离（结构与单元格文本）
- **MHS（标题层级）**：标题识别与嵌套层级

### 2.2 pdfmux 200-PDF benchmark（交叉印证）

[pdfmux 评测](https://pdfmux.com/blog/pdfmux-vs-pymupdf-vs-marker-vs-docling/) 用同一套 opendataloader-bench
数据测了 6 个引擎，结论与 opendataloader 官方一致（docling 表格 0.887、mineru 0.873、marker 0.808）。

---

## 3. 横向对比总表

数据来源：opendataloader-bench（速度 s/page 为官方测值；marker/mineru 为官方默认配置，GPU 下可显著提速）。

| 引擎 | Overall | 阅读顺序 | 表格 TEDS | 标题 MHS | 速度(s/页) | 许可 | GPU 加速 | 中文/OCR |
|------|---------|---------|-----------|----------|-----------|------|----------|----------|
| opendataloader hybrid | **0.907** | **0.934** | **0.928** | 0.821 | 0.463 | Apache-2.0 | 否 | 一般 |
| docling | **0.882** | 0.898 | **0.887** | **0.824** | 0.762 | **MIT** | ✅ | ✅ |
| marker | 0.861 | 0.890 | 0.808 | 0.796 | 53.9 | GPL-3.0 | 必须 | ✅ |
| unstructured hi_res | 0.841 | 0.904 | 0.588 | 0.749 | 3.008 | Apache-2.0 | ✅ | ✅ |
| opendataloader local | 0.831 | 0.902 | 0.489 | 0.739 | **0.015** | Apache-2.0 | 否 | 一般 |
| mineru | 0.831 | 0.857 | **0.873** | 0.743 | 5.96 | AGPL-3.0 | ✅ | **中文最强** |
| pymupdf4llm | 0.732 | 0.885 | 0.401 | 0.412 | **0.091** | AGPL-3.0 | 否 | ✅ |
| pdfplumber | 未入榜（官方称复杂文档得分低于 PyMuPDF） | | | | ~0.1-1 | MIT | 否 | ✅ |
| camelot | 仅表格（lattice/stream 规则法） | | | | 慢 | MIT | 否 | ✅ |
| surya/marker-2 | marker 同源，OCR 83.3% olmOCR-bench，5 pages/s(RTX5090) | | | | GPU 快 | Apache-2.0(代码) | 必须 | 90+ 语言 |

> 注：速度数值受机器与配置影响，参考意义在于**量级**：pymupdf/pdfplumber ~0.01-0.1s/页（规则法），
> docling ~0.8s/页（深度学习，GPU 可提速），mineru/marker 数秒~数十秒/页（VLM/多模型）。

---

## 4. 各候选库详细分析

### 4.1 规则/轻量级（快，无深度学习）

| 库 | 优点 | 缺点 | 结论 |
|----|------|------|------|
| **PyMuPDF (fitz / pymupdf4llm)** | 极快（0.09s/页，C 实现）；文本层提取质量高；阅读顺序 0.885；自带 `find_tables()` 框线表格检测与 `get_text("dict")` 带坐标行提取 | 无版面深度学习模型：无框线表格弱（TEDS 0.401）、标题层级弱（MHS 0.412）；AGPL-3.0 许可 | ✅ **快路径候选**（数字文本 PDF 首选） |
| **pdfplumber（已内置）** | 轻量、MIT；规则表格（有框线）可用；doc_parser 已深度定制（margin 编号列/页眉页脚/跨页合并） | 无版面模型；复杂文档准确度低于 PyMuPDF；纯 Python 较慢 | ✅ 保留（默认/兜底） |
| camelot | 表格规则法（lattice 有框线 / stream 无框线） | 仅表格、无版面；慢；依赖重 | ❌ 单点能力不足 |
| tabula-py | 表格提取 | 依赖 Java；许可 AGPL；维护停滞 | ❌ 不选 |
| pdfminer.six / pypdf | 底层文本 | 无表格/版面 | ❌ 不选 |

### 4.2 深度学习级（准，GPU 可加速）

| 库 | 优点 | 缺点 | 结论 |
|----|------|------|------|
| **Docling（已内置）** | 开源免费方案中 **表格最强（TEDS 0.887）**、**标题层级最强（MHS 0.824）**；阅读顺序 0.898；**MIT 许可**；跨页表格不重复表头；输出结构化 DoclingDocument（可转 Paragraph/Table）；GPU 加速（AcceleratorOptions） | 纯 CPU ~0.76s/页 | ✅ **准路径首选**（复杂版面/表格/章节） |
| **MinerU（已内置）** | **中文 OCR/VLM 场景最强**；表格 TEDS 0.873；GPU 加速 | 默认 5.96s/页 慢；AGPL-3.0；依赖重（lmdeploy/vllm） | ✅ 保留（中文扫描件专用） |
| marker / surya | 深度学习布局+表格+OCR；GPU 下快（surya 5 pages/s）；90+ 语言 | marker 默认 53.9s/页（CPU 极慢）；**GPL-3.0 许可**（传染性强）；输出 markdown 需再结构化 | ⚠️ 候选（评估后暂缓，许可与适配成本高） |
| unstructured hi_res | 多策略；hi_res 用 YOLOX+TableTransformer | 表格 TEDS 仅 0.588；依赖多而杂；速度 3s/页 | ❌ 不选 |
| opendataloader | **总分第一（0.907）**；输出带 bounding box；Apache-2.0 | 输出为 markdown/JSON，需适配结构化对象；hybrid 依赖 docling 做表格 | ⚠️ 候选（后续评估适配成本） |
| PaddleOCR/PP-StructureV3 | 中文版面分析+表格识别 | 集成成本高；表格 TEDS 未入 opendataloader 榜 | ⚠️ 候选（中文专项可选） |

---

## 5. 候选列表（结论）

结合"又准确又快"、项目现状（GPU T4 可用、中文制度文档为主、结构化输出要求、已有 pdfplumber/mineru/docling 三后端），
推荐 **4 条解析路径**：

| 路径 | 后端 | 定位 | 依据 |
|------|------|------|------|
| **默认路径** | **auto 路由** | 扫描件→MinerU、无框线表格→Docling、数字文本→PyMuPDF；文本型制度文档可显式固定 pdfplumber | 按文档特征选择；固定后端便于复现实验 |
| **快路径** | **PyMuPDF（新增 bk_pymupdf）** | 数字文本 PDF：秒级解析，阅读顺序好，表格用 `find_tables()` 规则提取 | 0.091s/页、NID 0.885；比 pdfplumber 快 10-50 倍 |
| **准路径** | **Docling（无框线表格场景）** | 复杂版面/无框线表格：TableFormer 表格识别，跨页表不重复表头，GPU 加速（注意：布局模型偶把标题行插入段落造成碎句） | TEDS 0.887、MHS 0.824、MIT |
| **中文扫描件路径** | **MinerU** | 扫描件/图片 PDF/VLM：中文 OCR 最强，GPU 加速 | 表格 TEDS 0.873、中文场景实测最优 |

**待评估候选（不立即接入）**：opendataloader（总分第一但需结构化适配）、marker/surya（GPL 许可+markdown 输出）、
PaddleOCR（中文专项）。

---

## 6. 落地结果（2026-08-16 已完成）

1. **新增 bk_pymupdf 后端**（doc_parser/src/doc_parser/backend/bk_pymupdf.py）：复用共享后处理（跨页表合并/章节检测/数字断字/模板表过滤），
   与 bk_pdfplumber 结构对齐；`_extract_lines` 用 words 按 y 坐标聚合（修复 PyMuPDF 不合并 margin 编号列导致的章节丢失）。
2. **注册表与分发**：`BACKENDS` 增加 `"pymupdf"`；`_pdf.py` 分发支持；
   `select_backend` auto 模式：扫描件 → mineru，无框线表格 → docling（准），数字文本 → pymupdf（快），不可用时降级 pdfplumber。
3. **GPU 校验通过**：torch 2.13.0+cu130（cuda=True）/ faiss-gpu（num_gpus=1）/ onnxruntime-gpu（CUDAExecutionProvider）/ fastembed GPU 编码实测 OK / docling cuda。
4. **真实 PDF 实测**（data/pdf 下中文文档）：

| 后端 | 样本 B (9页) | manual (116页) | 段落 | 表格 | 跨页表 | 章节 |
|------|----------------|---------------|------|------|--------|------|
| pdfplumber | 1.08s | 9.75s | 693 | 17 | 6 | 156 |
| **pymupdf（快路径）** | **0.66s** | **4.99s** | 784 | 17 | 6 | 165 |
| docling(cuda) | 57.9s(首启) | 64.5s | 957 | **29** | **9** | 60 |

   > 结论：pymupdf 比 pdfplumber 快约 2 倍且表格/跨页表一致；docling GPU 0.56s/页，表格识别 29 vs 17
   > （多识别 12 张无框线/复杂表格）——验证深度学习后端对无框线表格的价值。
5. **测试与文档**：doc_parser 77 passed（含新增 pymupdf/路由测试）、rag_server 58+5skipped、llm_chat 39 passed；
   已同步 README / doc_parser 设计文档 / API 参考 / 使用手册；T4 多包验证计划见 temp/pdf_parsers_verification.md。


---

## 7. 补充评估（2026-08-17）

### 7.1 新增库结果

在 样本 A 当前版（116 页）和 样本 B（9 页）上使用隔离环境实际运行：

| 库 | 样本 A 当前版 | 样本 B | 结论 |
|---|---:|---:|---|
| pdf-oxide 0.3.77 | 0.998s；66077 文本字符；49 个页级 table 结果 | 0.048s；5152 文本字符；0 个 table 结果 | 极快，适合文本层预扫描；无框线表漏检，缺少项目级结构化和跨页合并 |
| pymupdf-layout 1.28.2 | 82.902s；77486 Markdown 字符；44 个 Markdown 表块 | 6.218s；5516 Markdown 字符；1 个 Markdown 表块 | 布局 Markdown 较强，但 CPU 慢，不能直接替代项目 Document 后端 |
| marker-pdf 2.0.0 | 39.139s；tables_total=67 个内部 table blocks | 24.711s；tables_total=2 个内部 table blocks | 比 1.10 快，但有字符异常和表格过度拆分风险；未作为默认路径 |

表块数量均不是最终唯一表格数量。Marker 本次使用 CPU fast + `--disable_ocr`，不代表 OCR/VLM 模式。

### 7.2 PyMuPDF LLM 审查状态

截至本补充报告，PyMuPDF 的真实解析指标已经有记录，但上述 样本 A 当前版 和 样本 B 的 PyMuPDF 输出**尚未经过真实 LLM 逐项审查**。应用层 `parse_qa` 默认关闭，已有测试只使用 mock LLM 和合成 Document。

当前对真实样本执行的是确定性 QA：

- 样本 A 当前版：695 段、21 表、40 个跨页段落、3 个跨页表；43 个规则候选问题，其中 4 个 high、39 个 medium，状态为 `review`。
- 样本 B：30 段、0 表、6 个跨页段落；状态为 `review`。发现“共9页”等页码污染以及第 7–8 页表格内容混入正文的迹象。

因此不能宣称 PyMuPDF 输出没有断裂。跨页段落中有一部分是正常分页，另有目录/表单点线内容和无框线表格漏检等需要人工或其他后端复核的情况。当前推荐继续采用 PyMuPDF 快路径，并将 QA 高风险页局部送 Docling/MinerU。


---

## 8. 最新样本独立复测（2026-08-17）

为避免沿用旧输出，本轮使用当前工作区样本重新执行：

- 样本 A 当前版 手册：116 页，`sample-A.pdf`
- 样本 B 通报：9 页，`sample-B.pdf`
- 所有依赖通过 `uv run --isolated --with ...` 运行
- 原始输出：`temp/pdf_eval_latest_2026-08-17/`

| 后端 | 样本 A 当前版 手册 | 样本 B 通报 | 关键观察 |
|---|---:|---:|---|
| pdf-oxide 0.3.77 | 1.344s；66077 文本字符；74425 Markdown 字符；49 个页级 table 结果 | 0.062s；5152 文本字符；5426 Markdown 字符；0 个 table 结果 | 极快；样本 B 无框线表仍漏检 |
| pymupdf-layout 1.28.2 + pymupdf4llm 1.28.2 + PyMuPDF 1.28.2 | 92.306s；77486 Markdown 字符；44 个表分隔块；231 个标题 | 5.577s；5516 Markdown 字符；1 个表分隔块；8 个标题 | CPU 较慢；只输出 Markdown，未生成项目结构化对象 |
| marker-pdf 2.0.0 | 内部 52.705s；外部 72.549s；日志 tables_total=67；meta Table blocks=44；88275 Markdown 字符 | 内部 9.407s；外部 30.630s；日志 tables_total=2；meta 第8/9页各1个 Table block；4877 Markdown 字符 | 表格统计口径不同且存在拆分；notice 数字/日期明显损坏 |

Marker 参数为 `--mode fast --disable_ocr --disable_multiprocessing --output_format markdown --disable_tqdm`，手册额外使用 `--page_range=0-115`。Marker 关闭 OCR，所以本轮不能代表 OCR/VLM 模式。

### 最新批次结论

1. pdf-oxide 的速度优势在本轮仍然成立，但不识别 样本 B 第 8–9 页无框线表，因此定位为文本层预扫描工具。
2. pymupdf-layout 的 Markdown 版面能力可以保留作实验用途，但 样本 A 当前版 本轮耗时约 92 秒，不能替代项目 PyMuPDF 快路径。
3. Marker 2.0 能保留手册的 样本 A 当前版 版本信息，也能在 样本 B 第 8、9 页生成表格 block；但 样本 B 出现 `(2026 年第 号)`、`2026 24 日`、`28. 55`、`10. 57` 等明显缺失/断裂，不能直接作为制度文档默认路径。
4. Marker 的 `tables_total=67` 是内部处理统计，本轮最终 meta 为 44 个 Table block；两者都不能直接当最终唯一表格数。
5. 本轮没有将三个新库接入项目 `Document`、`inspect_document()` 或真实 LLM 审查流程；结果是原生输出复测，不是统一结构化 QA 分数。

当前生产推荐不变：普通数字文本 PDF 走 PyMuPDF；无框线/复杂表格高风险页局部走 Docling；扫描件走 MinerU；新三个库作为预扫描、Markdown 实验或独立对照工具保留。


## 9. 最新统一复测补充：表格专项库（2026-08-17）

本节与第 8 节一样，使用当前工作区最新版 样本 A 当前版 手册（116 页）和 样本 B 通报（9 页）。本轮所有专项库通过 `uv run --isolated --no-project` 单独解析依赖；本节结果优先于同名库的历史数据。完整汇总见 `temp/pdf_eval_latest_2026-08-17/all_backends_summary.json`。

### 9.1 专项库实际结果

| 库/版本 | 样本 A 当前版 手册 | 样本 B | 选型判断 |
|---|---:|---:|---|
| openparse 0.7.0（PyMuPDF table strategy） | 8.171s；116 nodes；115 paragraphs；1 table；0 headings | 1.788s；9 nodes；9 paragraphs；0 table；0 headings | 节点抽取可用，但无章节层级且无框线表漏检；不接入主路径 |
| camelot 2.0.0 lattice | 0.593s，`StopIteration` | 5.819s；2 tables（页 8/9） | 手册失败；只保留为可选专项工具 |
| camelot 2.0.0 stream | 0.672s，`StopIteration` | 0.334s；10 tables/9 pages/138 rows | 通报正文被大量误识别为表格，不能默认启用 |
| tabula-py 2.10.0 | 16.377s；68 DataFrames；642 rows；非空 cells 1354 | 1.860s；0 DataFrames | 能运行但表格过度拆分、空表较多；Java/subprocess 依赖成本高 |

Camelot 的 `tables` 与 tabula-py 的 DataFrame 数量均是专项返回数量，不等同于最终语义表格。tabula-py 本次缺 `jpype` 后回退到 tabula-java subprocess，PDFBox 同时报告 XRef 修正、JPEG2000 JAI ImageIO 缺失和字体 glyph 警告。原始专项记录和两种 Camelot flavor 的逐表 JSON 保存在 `temp/pdf_eval_latest_2026-08-17/table_specialists/`。

### 9.2 与当前项目后端的关系

| 后端类型 | 样本 A 当前版 手册 | 样本 B | 能否直接满足项目 `Document` 要求 |
|---|---:|---:|---|
| 项目 PyMuPDF 1.28.2 | 3.063s；695 段；165 章；21 表；3 跨页表 | 0.108s；30 段；6 章；0 表 | 可以，作为数字文本快路径；无框线表需 QA 触发升级 |
| 项目 pdfplumber 0.11.10 | 6.221s；651 段；156 章；21 表；4 跨页表 | 0.541s；28 段；5 章；0 表 | 可以，作为规则兜底 |
| Docling CPU 2.120.1 | 214.746s；569 段；52 章；39 表；4 跨页表 | 45.618s；27 段；4 章；1 跨页表 | 可以，适合复杂/无框线表格；本批次不应与历史 29/9 混用 |
| MinerU pipeline 3.4.5 | 1074.590s；868 段；141 章；29 表；4 跨页表 | 51.629s；33 段；4 章；1 表 | 可以，但 CPU 极慢，主要用于扫描件 |

### 9.3 选型结论更新

- **主路径不变**：数字文本 PDF 走项目 PyMuPDF；规则兜底走项目 pdfplumber；无框线/复杂表格高风险页局部调用 Docling；扫描件调用 MinerU。
- **专项库不纳入主路径**：openparse 没有项目章节/跨页结构，Camelot 在手册上直接失败，tabula-py 虽能返回 68 个 DataFrame 但出现大量空/稀疏/拆分结果。
- **指标必须分层**：原生页级表、Markdown 分隔块、内部 processor block、专项 DataFrame 数量，都不能直接与项目 `Document.tables` 横向排名。
- **质量审查边界**：本轮仍未对 PyMuPDF 或新增库输出执行真实 LLM 逐项审查；PyMuPDF 的确定性 QA 仍有 review/high 候选。因此不能宣称没有断裂，推荐保留 QA 高风险页的 Docling/MinerU 复核链路。
- **未执行项**：anydoc/firecrawl 需要云端/API，未纳入本地离线同环境复测。


## 10. Win11 CPU 覆盖与优先级复核（2026-08-17）

### 10.1 CPU 测试是否充分

针对当前项目目标——数字型中文制度/通报 PDF 的本地解析和结构化——本轮 Windows PowerShell 隔离环境已经覆盖了主要可选路径：

- **底层文本**：pypdf、pdfminer.six、pypdfium2、pdfplumber、PyMuPDF；
- **项目结构化**：PyMuPDF、pdfplumber、Docling CPU、MinerU pipeline CPU；
- **独立版面/Markdown**：pdf-oxide、pymupdf-layout/pymupdf4llm、Marker 2.0 fast；
- **表格专项**：openparse、Camelot lattice/stream、tabula-py；
- **通用解析**：unstructured[pdf] fast。

两个匿名样本均已实际运行或留下明确错误记录，并记录了版本、耗时、字符/节点、章节、表格、跨页表、DataFrame 或 Markdown block 等指标。因此，**对于数字文本 PDF 在 Win11 CPU 上的可运行性和当前项目选型，测试是充分的**。

但它不是所有 PDF 能力的完整认证。两个样本均有文本层；Docling、Marker 和 pymupdf-layout 本批次关闭 OCR，MinerU 运行的是 CPU pipeline 而非 VLM；尚未覆盖扫描件、低清图片、旋转页、公式密集页、多语言页，也没有穷举 openparse/Camelot/tabula-py 的所有调参组合。真实 LLM 逐项审查和完整人工真值标注也尚未完成。

### 10.2 默认配置优先级

Win11 CPU 结果**没有改变默认选择顺序**，反而验证了现有路由：

| 优先级 | 路径 | CPU 角色 | 说明 |
|---:|---|---|---|
| 1 | `auto` → 项目 PyMuPDF | 数字文本默认路径 | 项目后端样本 A/样本 B 为 3.063s/0.108s，快于 pdfplumber 的 6.221s/0.541s |
| 2 | 项目 pdfplumber | 规则兜底/稳定文本 | 适合需要定制文本行、页眉页脚和段落规则的场景 |
| 3 | Docling CPU | 无框线/复杂表格风险页 | 本批次 214.746s/45.618s，慢但表格能力更强，应局部升级而非全量默认 |
| 4 | MinerU pipeline CPU | 扫描件/OCR 专用 | 本批次 1074.590s/51.629s，CPU 可跑但非常慢，不适合作为普通数字 PDF 默认路径 |
| 5 | pdf-oxide | 高速文本层预扫描 | 速度优势明显，但不提供项目 `Document` 和可靠无框线表结构 |
| 6 | openparse/Camelot/tabula-py | 表格专项实验 | Camelot 在样本 A 上失败，tabula-py 过度拆分，均不进入主路由 |

Marker、unstructured 和 pymupdf-layout 继续作为对照或实验路径，不替代项目结构化后端。CPU-only 部署的实际变化是性能策略而不是功能路由：优先 PyMuPDF，Docling/MinerU 按风险或文档类型局部调用，并设置任务队列、超时和资源上限。

### 10.3 最终边界

当前结论可以表述为：**Win11 CPU 已充分覆盖本项目当前两类数字型 PDF 的候选库比较，但尚未充分覆盖扫描件/OCR/VLM 的泛化能力。** 因此生产默认仍是 `auto → PyMuPDF → pdfplumber/Docling/MinerU` 的按特征升级链路，而不是把某个专项库或 CPU 深度学习库提升为全量默认后端。
