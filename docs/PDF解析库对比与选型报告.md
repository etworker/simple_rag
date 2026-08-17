# PDF 解析库对比与选型报告

> 目的：为 doc_parser 多后端插件架构挑选"又准确又快"的 PDF 解析候选。
> 依据：项目需求（docs/需求文档.md FR-1）、doc_parser 设计文档（doc_parser/docs/设计文档.md）、
> 公开基准 opendataloader-bench（200 份真实 PDF）与 pdfmux 200-PDF benchmark。
> 更新日期：2026-08-16

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
| **默认路径** | **pdfplumber（默认）** | 文本型制度文档段落切分最稳（R3-2→R3-3 LLM 研判：找到真实修改、无碎句） | MIT、轻量、margin 编号列定制 |
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

| 后端 | notice548 (9页) | manual (116页) | 段落 | 表格 | 跨页表 | 章节 |
|------|----------------|---------------|------|------|--------|------|
| pdfplumber | 1.08s | 9.75s | 693 | 17 | 6 | 156 |
| **pymupdf（快路径）** | **0.66s** | **4.99s** | 784 | 17 | 6 | 165 |
| docling(cuda) | 57.9s(首启) | 64.5s | 957 | **29** | **9** | 60 |

   > 结论：pymupdf 比 pdfplumber 快约 2 倍且表格/跨页表一致；docling GPU 0.56s/页，表格识别 29 vs 17
   > （多识别 12 张无框线/复杂表格）——验证深度学习后端对无框线表格的价值。
5. **测试与文档**：doc_parser 77 passed（含新增 pymupdf/路由测试）、rag_server 58+5skipped、llm_chat 39 passed；
   已同步 README / doc_parser 设计文档 / API 参考 / 使用手册；T4 多包验证计划见 temp/pdf_parsers_verification.md。
