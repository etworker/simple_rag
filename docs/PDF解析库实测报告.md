# PDF 解析库实测报告（T4 GPU / CPU 均已完成）

> 目的：用项目目标文档（中文制度手册/通报）实测各 PDF 解析库，判定"能完整精确解析目标 PDF"的候选后端列表。
> 环境：AWS 宁夏 EC2（g5 系，Tesla T4 15.6GB / CUDA 13.2 驱动 / torch 2.13.0+cu130，8 核 CPU / 30GB 内存）。
> 状态：**GPU 结论与 CPU 结论均已完成**；本地 Windows 隔离环境的最新版样本复测更新至 2026-08-17（CPU 通过 CUDA_VISIBLE_DEVICES="" 强制 CPU 实测）。

---

## 1. 评测对象（两份目标 PDF）

| 键 | 文件 | 类型 | 页数 |
|----|------|------|------|
| manual | sample-A.pdf | 数字 PDF（有文本层） | 116 |
| 样本 B | sample-B.pdf | 数字 PDF（有文本层） | 9 |

## 2. 五类元素判定（ground truth 参考）

| 元素 | 判定标准 |
|------|---------|
| 文本 text | 提取字符数，与 pypdf 基线（manual 75340 / 样本 B 5118）对比覆盖率 |
| 段落 paragraph | 段落切分合理（人工抽检语义连贯） |
| 章节 section | 章节标题层级正确（docling 60 / 规则后端 156，差异因口径） |
| 表格 table | 表格数：docling（深度学习最强）29 表为参考上限，规则后端 17 表（含跨页合并后） |
| 跨页表 cross-page | 跨页长表正确拼接为单表（manual 的 6-9 页 73 行、14-17 页 49 行表） |

## 3. 结果总表（GPU 实测）

| # | 库 | 版本 | 文本(manual) | 段落 | 章节 | 表格 | 跨页表 | 耗时 | 判定 |
|---|----|------|------------|------|------|------|--------|------|------|
| 1 | pypdf | 6.16.1 | 75340(基线) | - | - | - | - | 3.8s | 文本基线 |
| 2 | pdfminer.six | 20260107 | 83817 | - | - | - | - | 7.4s | 文本基线 |
| 3 | pdfplumber | 0.11.10 | 46955 | 693 | 156 | 17 | 6 | 9.7s | ✅ 达标（规则） |
| 4 | **pymupdf（doc_parser 后端）** | 1.28.2 | 47380 | 784 | 165 | 17 | 6 | 5.0s | ✅ 达标（快路径） |
| 5 | pymupdf4llm | 1.28.2 | 71780 | 960 | 237 | 44 | ? | 56.8s | ⚠️ 表格过度拆分 |
| 6 | **docling_cuda** | 2.120.1 | 31684 | 957 | 60 | **29** | 9 | 64.5s | ✅ 达标（准路径） |
| 7 | **docling_cpu** | 2.120.1 | 31684 | 958 | 60 | 29 | 9 | 65.5s | ✅ 达标（CPU 可跑） |
| 8 | mineru(VLM) | 3.4.5 | 3774(notice) | 38 | 4 | 1 | 0 | 310s/9p | ✅ 达标但慢（34s/页） |
| 9 | markitdown | 0.1.7 | 94894 | 407 | 0 | **306** | - | 9.6s | ❌ 表格/章节不可用 |
| 10 | anydoc(firecrawl) | 0.1.9 | 59574 | 72 | 51 | 100 | - | 6.5s | ❌ 中文文本检测失败 |
| 11 | openparse | 0.7.0 | 56041 | 2576 | 0 | 1 | - | 15.8s | ❌ 表格不可用 |
| 12 | camelot | 2.0.0 | - | - | - | FAIL | - | - | ❌ 仅表格且失败 |
| 13 | tabula-py | 2.10.0 | - | - | - | 68 | - | 18.1s | ❌ 过度拆分 |
| 14 | unstructured | 0.25.2 | 0(无poppler) | 0 | 0 | 0 | - | 8.5s | ❌ 依赖残缺+表格弱 |
| 15 | marker(1.10 本地) | 1.10.2 | 80202 | 456 | 198 | 70 | ? | 128.8s | ❌ 表格过度拆分/碎片化 |

> 样本 B 结果见 §6 逐库说明；docling_cpu 的 样本 B 表格跨页标记需复核。

## 4. 判定说明（为什么排除/保留）

| 库 | 结论 | 原因 |
|----|------|------|
| pdfplumber | ✅ 保留 | 规则文本+表格 17 表/6 跨页表完整；margin 编号列定制成熟 |
| pymupdf | ✅ 保留（快路径） | 与 pdfplumber 表格/跨页一致，快约 2 倍；章节识别 165 略多（margin 聚合更全） |
| docling | ✅ 保留（准路径） | 表格 29（含规则后端漏掉的无框线表）、跨页 9；GPU 0.56s/页，CPU 可跑 |
| mineru | ✅ 达标但慢 | 样本 B 1 表（与 docling 一致）；T4 上 VLM 34s/页（310s/9页）、batch=4 会 OOM；适合扫描件专用，数字 PDF 不推荐默认 |
| marker | ❌ 排除 | 1.10 本地模式跑通（2.x 需 docker 拉 vllm 镜像，国内不可达）；manual 70 表严重过度拆分/碎片化（3-5 行碎片块、跨页表被拆散）；GPL-3.0 许可 |
| markitdown | ❌ 排除 | 表格 306 个（把目录/公文头当表格）、章节 0——无结构还原能力 |
| anydoc | ❌ 排除 | 中文 PDF 文本检测失败（"no extractable text, OCR required"），本地版无 OCR |
| openparse | ❌ 排除 | 表格提取默认关闭，启用 pymupdf 策略后仍仅 1 表；段落 2576 过度切分 |
| camelot | ❌ 排除 | 仅表格专项且 manual 解析抛 StopIteration；无版面/段落/章节 |
| tabula-py | ❌ 排除 | 仅表格专项；68 表过度拆分（跨页表被切成多个）；依赖 Java |
| unstructured | ❌ 排除 | 依赖链过长（poppler/pdf2image/inference 全要）；无 poppler 输出为空；表格 TEDS 0.588 弱 |
| pymupdf4llm | ⚠️ 参考 | 文本 71780/表格 44 偏多（过度拆分），无结构化章节；可作 PyMuPDF 快速 markdown 参考 |

### 补充实测（2026-08-16）：制度类样本 样本 A 旧版→样本 A 当前版 的 LLM 研判

对 **样本 A 版本对比** 用 docling / pdfplumber 两个后端跑版本对比，并经 LLM（bedrock GLM）研判：

| 后端 | 结果 | LLM 研判 |
|------|------|---------|
| **pdfplumber** | 3 实质性（含真实修改"§6.1.4 职责：科室→处室"）+ 10 细微；段落完整 | ✅ **更可靠**：找到真实修改、段落完整 |
| **docling** | 6 实质性（§3.2.6 段落被拆成 3 个碎句 removed）| ❌ **碎句假象**："4.2.5.2 使用规定"标题行被布局模型插入句子中间，拆散完整段落 |

**结论**：对**文本型制度文档**（制度类样本等），**pdfplumber 解析/段落切分更可靠**（docling 的布局模型偶把标题行插入段落造成碎句）；docling 的优势在**无框线表格识别**（网络安全类样本场景）。当前默认入口为 **auto** 路由；需要稳定文本段落时可显式选择 pdfplumber。

## 5. GPU 结论（达标后端列表 = doc_parser 后端候选）

**两份目标 PDF 均能"完整精确"解析（五类元素齐全）的后端：**

| 后端 | 定位 | GPU 实测 |
|------|------|---------|
| **pymupdf**（新增快路径） | 数字文本 PDF 秒级解析 | 116 页 4.99s；17 表/6 跨页表/165 章节 |
| **pdfplumber**（兜底） | 轻量规则解析 | 116 页 9.75s；17 表/6 跨页表/156 章节 |
| **docling**（准路径） | 深度学习版面+表格（无框线表最强） | 116 页 64.5s（GPU 0.56s/页）；29 表/9 跨页表 |
| **mineru**（扫描件路径） | 中文 OCR/VLM | 待补（GPU 推理中，预期慢但准） |

**排除清单**：markitdown / anydoc / openparse / camelot / tabula / unstructured / marker（原因见 §4，均"无简单方法挽回"）。

**待评估**：olmOCR（7B VLM，T4 显存紧张）、opendataloader（GitHub 源码安装）、PaddleOCR-VL / pix2text（中文公式专项）。

## 6. 逐库详情（样本 B + 关键发现）

- **样本 B 跨页表（修正验证计划假设）**：docling 正确识别第 8-9 页的跨页表（"序号/风险措述/涉及企事业单位/局方督导单位部门"，3 行 4 列，page=8 page_end=9）——样本 B **也有跨页表**；pdfplumber/pymupdf 0 表（公文头部表格为无框线/红头样式，规则后端漏）；mineru/marker 各识别 1 表——**无框线表格必须走深度学习后端**。
- **manual 跨页表**：docling 正确合并 6-9 页（73 行）、14-17 页（49 行）、82-85 页（47 行）等 9 处跨页表；pdfplumber/pymupdf 合并 6 处。
- **markitdown 306 表**：把"0.1 声明 0.1-1"目录行、"发电单位…签发盖章"公文头当表格——markdown 输出的表格判定无结构约束。
- **marker 70 表碎片化**：surya 表格检测把跨页表拆成多个块（如有效页清单 25 行块 + 3-5 行碎片），70 块 vs ground truth 29——过度拆分不可用。
- **mineru T4 特性与加速**：VLM 模式默认 batch=4 会 OOM；**最优配置 batch=2（mineru_batch_size=2）约 13-16s/页（9.2GB 显存），比 batch=1 的 34s/页快约 2.5 倍**；样本 B 1 表识别正确。
  - **并行结论**：T4 单卡 16GB 无法双 VLM 实例并行（每实例 >7.5GB，2 实例超显存 OOM）；页级切分多进程脚本见 temp/mineru_parallel.py，适用于多卡/更大显存（g5.12xlarge 4 卡可 4 进程并行）。
  - **更优加速**：mineru 支持 vllm 推理引擎（PagedAttention + continuous batching，预计 3-5 倍），需另装 vllm（匹配 torch/CUDA 版本）。
  - 注意：mineru do_parse 内部 spawn 子进程，脚本调用需 `if __name__ == "__main__":` main guard（否则末尾报 bootstrapping 错误）。
- **transformers 版本坑**：marker 2.x 安装把 transformers 升到 5.15 会破坏 mineru（Qwen2VLConfig 缺 max_position_embeddings），需降回 4.57.6；marker 2.x 还需 docker 拉 vllm 镜像（国内网络不可达）——**多库共存环境要锁 transformers<5，marker 用 1.x 本地模式**。

---

## 7. CPU 结论（CUDA_VISIBLE_DEVICES="" 强制 CPU 实测）

> 说明：本机为 GPU 机，CPU 测试通过 `CUDA_VISIBLE_DEVICES=""` 禁用 GPU（torch.cuda.is_available()=False）模拟无 GPU 环境；8 核 CPU / 30GB 内存。

| 库 | CPU 结论 | 实测（样本 B=9 页 / manual=116 页） |
|----|---------|------|
| pdfplumber | ✅ 可用 | 纯 CPU 库：notice 1.09s / manual 9.75s；17 表/6 跨页表 |
| pymupdf | ✅ 可用 | 纯 CPU 库：notice 0.16s / manual 4.99s；17 表/6 跨页表 |
| docling_cpu | ✅ 可用 | 强制 cpu：manual 65.5s，29 表/9 跨页表——**与 CUDA 结果一致，CPU 可跑** |
| mineru(pipeline) | ✅ 可用（慢） | pipeline 模式 notice 111.9s（12.4s/页），33 段/1 表/4 章节——**CPU 也能识别无框线表格**；VLM 引擎仅 GPU |
| marker(1.x) | ✅ 可用（慢） | notice 326.1s（36s/页，比 GPU 90s 慢 3.6 倍），1 表/14 章节识别正确 |
| 其余排除库 | 无需补 | 表格/章节/中文能力缺失，与 CPU/GPU 无关（GPU 实测已排除） |

**CPU 环境结论**：doc_parser 四条路径在 CPU 下全部可用且结果与 GPU 一致——pymupdf/pdfplumber（秒级）、docling（0.56s/页级，29 表/9 跨页表，与 CUDA 相同）、mineru pipeline（12.4s/页，能识别无框线表）、marker 1.x（36s/页，1 表正确）。CPU 无 GPU 时 select_backend 自动路由：扫描件→mineru(pipeline)、无框线表格→docling(cpu)、数字文本→pymupdf。**结论：GPU 不是 doc_parser 解析链路的硬依赖，CPU 可完整运行（仅速度差异）**。

**待办（下一步）**：
1. 补充 opendataloader（GitHub 源码安装）评测
2. 复核 marker 1.x 的 surya 表格拆分参数（若后续需要可调 surya 表格检测阈值）


## 8. 补充实测（2026-08-17）：pdf-oxide、pymupdf-layout、Marker 2.0 与 PyMuPDF QA

> 本节是当前工作区代码和隔离环境的最新补充结果。前文 Marker 1.10/1.10.2 的历史结果保留不变；Marker 2.0 不应与旧版本数据混用。

### 8.1 三个新增库的实际运行结果

测试样本仍为：样本 A 当前版《制度类样本》（116 页）和 样本 B 通报（9 页）。

| 库/版本 | 样本 A 当前版 | 样本 B | 结构与质量判断 |
|---|---:|---:|---|
| pdf-oxide 0.3.77 | 约 0.998s；66077 文本字符；73735 Markdown 字符；49 个页级 table 结果 | 约 0.048s；5152 文本字符；5378 Markdown 字符；0 个 table 结果 | 极快；页级 API 适合文本层预扫描，但 样本 B 第 8–9 页无框线表漏检，未提供项目 Document/跨页表结构 |
| pymupdf-layout 1.28.2 + pymupdf4llm 1.28.2 | 约 82.902s；77486 Markdown 字符；44 个 Markdown 表块；231 个标题 | 约 6.218s；5516 Markdown 字符；1 个 Markdown 表块；8 个标题 | 布局/Markdown 较强，但 CPU 明显慢于 PyMuPDF；输出不是项目 Document，样本 B 未形成项目级显式跨页表 |
| marker-pdf 2.0.0（CPU fast、disable_ocr、disable_multiprocessing） | 约 39.139s；日志 tables_pdftext=40、tables_total=67 | 约 24.711s；日志 tables_pdftext=1、tables_total=2 | 比历史 1.10 快，但有数字/字符异常；样本 A 当前版 的 table processor block 明显多于项目 21 表和 Docling 参考 29 表 |

说明：pdf-oxide 的 page.tables、pymupdf-layout 的 Markdown 表块、Marker 的 tables_total 都是页级或内部 block 统计，不能直接当作最终唯一表格数。Marker 本次关闭 OCR/VLM，因此不代表其 OCR/VLM 模式结果。三个新增库均未适配项目 Document，也未接入 inspect_document() 进行完全同口径 QA。

### 8.2 当前 PyMuPDF 后端真实样本的确定性 QA

使用当前工作区的 PyMuPDF 后端重新解析后执行 `doc_parser.qa.inspect_document()`，没有调用 LLM：

| 样本 | 当前解析指标 | 规则 QA 结果 | 主要发现 |
|---|---|---|---|
| 样本 A 当前版 | 695 段、21 表、40 个跨页段落、3 个跨页表 | 43 个候选问题；4 个 high、39 个 medium；状态 review | 跨页段落需要抽查；3 个跨页表需检查表头/行顺序；目录/表单页的点线内容产生了部分疑似误报；有 1 个跨页段落以冒号结束，需重点核对 |
| 样本 B | 30 段、0 表、6 个跨页段落 | 6 个 medium；状态 review | 无框线表仍未进入结构化表格；正文中出现“共9页”等页码污染迹象，第 7–8 页表格内容被压入段落文本，需走 Docling/MinerU 或人工复核 |

这些规则 QA 结果**不是 LLM 审查结果**。`parse_qa` 是应用层可选旁路，默认 `enabled=false`；现有测试使用 mock LLM 和合成 Document，没有对上述两份真实 PyMuPDF 输出执行真实 LLM 请求，也没有生成逐项 LLM 审查报告。因此目前不能声称 PyMuPDF 输出“已经通过 LLM 审查”或“没有断裂”。

当前可以确认的是：PyMuPDF 已有页眉/页脚过滤、重复行过滤、段落分段和跨页表合并代码；确定性 QA 能发现跨页、短碎片、相邻重复、表格列数异常等候选。但真实样本的逐段语义完整性、页眉页脚残留率和每张表的错列/漏行情况仍未完成 LLM 或人工逐项验收。

生产建议不变：数字文本 PDF 默认 PyMuPDF；发现无框线表格、表格列异常或 QA 高风险页时，局部调用 Docling；扫描件走 MinerU。pdf-oxide 可作为高速文本层预扫描，pymupdf-layout 可作 Markdown 实验，Marker 2.0 暂不作为默认制度文档解析器。


## 9. 最新样本复测记录（2026-08-17，独立批次）

本节是针对当前工作区最新版样本的独立复测，结果不覆盖第 8 节，便于追溯运行批次。

### 9.1 输入文件与运行方式

| 样本 | 实际文件 | 页数 | 文件大小 |
|---|---|---:|---:|
| 样本 A 当前版 手册 | `sample-A.pdf` | 116 | 7,115,776 bytes |
| 样本 B 通报 | `sample-B.pdf` | 9 | 299,150 bytes |

样本确认：样本 A 当前版 是手册目录中当前最高版本，手册 PDF 修改时间为 2026-05-09；样本 B PDF 修改时间为 2026-08-05。

所有包均使用 `uv run --isolated --with ...`，未复用项目运行环境。原始输出保存在 `temp/pdf_eval_latest_2026-08-17/`。

### 9.2 最新复测结果

| 库及版本 | 样本 A 当前版 手册 | 样本 B 通报 | 输出位置/说明 |
|---|---:|---:|---|
| pdf-oxide 0.3.77 | 1.344s；66077 文本字符；74425 Markdown 字符；49 个页级 table 结果 | 0.062s；5152 文本字符；5426 Markdown 字符；0 个 table 结果 | `pdf_oxide/manual.md`、`notice.md` 及对应 JSON |
| pymupdf-layout 1.28.2 + pymupdf4llm 1.28.2 + PyMuPDF 1.28.2（`use_ocr=False`） | 92.306s；77486 Markdown 字符；44 个表分隔块；231 个标题 | 5.577s；5516 Markdown 字符；1 个表分隔块；8 个标题 | `pymupdf_layout/manual.md`、`notice.md` 及对应 JSON |
| marker-pdf 2.0.0（CPU fast、`--disable_ocr`、`--disable_multiprocessing`） | Marker 内部处理 52.705s；外部 uv/PowerShell 墙钟 72.549s；日志 `tables_pdftext=40`、`tables_total=67`；meta 有 44 个 Table block；Markdown 88275 字符、40 个表分隔块、261 个标题 | Marker 内部处理 9.407s；外部 uv/PowerShell 墙钟 30.630s；日志 `tables_pdftext=1`、`tables_total=2`；meta 第 8、9 页各有 1 个 Table block；Markdown 4877 字符、1 个表分隔块、12 个标题 | `marker_manual/`、`marker_notice/`，包含 Markdown、`_meta.json`；手册另有页面 JPEG |

Marker 的“内部处理时间”来自其日志 `Total time`；“外部墙钟”包含 uv 隔离环境解析/启动开销。三种库的表格指标仍不是完全同口径：pdf-oxide 是页级结果，pymupdf-layout 是 Markdown 表分隔块，Marker 同时报告内部 processor blocks 和 meta page blocks，均不能直接视为最终唯一表格数。

### 9.3 本批次输出质量观察

- **pdf-oxide**：样本 A 当前版 速度约 1.3 秒，样本 B 约 0.06 秒；样本 B 仍为 0 个表，未识别第 8–9 页无框线表。适合文本层和页级预扫描，不直接提供项目 `Document` 或跨页合并结果。
- **pymupdf-layout/pymupdf4llm**：样本 A 当前版 生成 44 个 Markdown 表分隔块，样本 B 生成 1 个；样本 B 没有形成项目级显式跨页合并表。输出是 Markdown，不是项目 `Document/Paragraph/Table`。
- **Marker 2.0**：样本 A 当前版 的日志 `tables_total=67`，但最终 meta 的 Table block 为 44，说明内部检测统计与最终输出 block 不同，不能把 67 当唯一表格数。样本 B 的 meta 在第 8、9 页各保存一个 Table block，但没有项目级跨页表对象。
- **Marker 样本 B 文本质量存在明显问题**：输出出现 `(2026 年第 号)`、`2026 24 日`、`28. 55`、`10. 57` 等日期、编号和数字断裂/缺失。该批次使用 `--disable_ocr`，不能代表 OCR/VLM 模式。
- **Marker 手册开头可识别 样本 A 当前版 版本记录**：Markdown 中保留了 `样本 A 当前版 / 2026.05.10 / 2026.05.09 / 有效` 和版次信息，但全量结果仍有表格 block 拆分问题。

本批次仍未将三个新库适配到项目 `Document` 并运行 `inspect_document()`，也未使用真实 LLM 对这些输出做逐项语义审查；本节结论仅代表本次原生输出复测。


## 10. 最新统一复测补充：表格专项库与全量汇总（2026-08-17）

本节补充本轮在同一批最新版样本、Windows PowerShell 和 `uv run --isolated --no-project` 环境下完成的结果。**本节数值优先于第 3/4/7 节中同名库的历史或 GPU 批次数据**；历史数据保留用于追溯。完整机器可读汇总为 `temp/pdf_eval_latest_2026-08-17/all_backends_summary.json`。

### 10.1 表格专项库实测

这些库只承担表格检测/抽取，不输出项目 `Document` 的段落、章节和跨页对象；`tables` 是库返回的表或 DataFrame 数量，不能直接当作最终语义表格数。

| 库/版本 | 调用方式 | 样本 A 当前版 手册（116页） | 样本 B（9页） | 结论 |
|---|---|---:|---:|---|
| openparse 0.7.0 | `DocumentParser(table_args={parsing_algorithm: pymupdf, table_output_format: markdown})` | 8.171s；116 nodes；115 paragraphs；1 table；71181 字符 | 1.788s；9 nodes；9 paragraphs；0 table；6688 字符 | 能输出页面级节点，但章节识别为 0；无框线表仍漏检；不适合作为项目结构化主后端 |
| camelot 2.0.0（lattice） | `read_pdf(pages="all", flavor="lattice")` | **FAIL**：0.593s，`StopIteration` | 5.819s；2 tables，页 8/9，5 rows | 手册直接失败；通报能找到第 8/9 页表，但仅专项能力 |
| camelot 2.0.0（stream） | `read_pdf(pages="all", flavor="stream")` | **FAIL**：0.672s，`StopIteration` | 0.334s；10 tables，覆盖 9 页，138 rows | 通报产生大量正文误检/碎表，不能作为默认策略 |
| tabula-py 2.10.0 | `read_pdf(pages="all", multiple_tables=True)` | 16.377s；68 DataFrames；642 rows；2350 cells；其中非空 1354 | 1.860s；0 DataFrames | Java 可用（缺 `jpype`，自动回退 subprocess）；手册过度拆分且有空/稀疏表，通报无框线表漏检 |

运行日志中的环境事实也保留在专项 JSON：tabula-py 的 PDFBox 报告了手册 XRef stream 偏移修正、JPEG2000 JAI ImageIO 不可用和字体 glyph 警告；这些不是静默忽略的质量结论，而是本次真实运行日志的一部分。原始结果位于 `temp/pdf_eval_latest_2026-08-17/table_specialists/`。

### 10.2 本批次全量结果摘要

| 分组/后端 | 版本 | 样本 A 当前版 手册 | 样本 B |
|---|---|---:|---:|
| pypdf | 6.16.1 | 2.457s；75455 字符 | 0.854s；5126 字符 |
| pdfminer.six | 20260107 | 4.215s；83817 字符 | 0.517s；5344 字符 |
| pypdfium2 | 5.13.0 | 0.368s；65019 字符 | 0.163s；5132 字符 |
| 原生 pdfplumber | 0.11.10 | 5.694s；61707 字符；53 页级表 | 0.530s；5078 字符；0 页级表 |
| 原生 PyMuPDF | 1.28.2 | 2.573s；76908 字符；50 页级表 | 0.095s；5043 字符；0 页级表 |
| MarkItDown | 0.1.7 | 5.683s；114771 字符；306 Markdown 表块 | 0.609s；8823 字符；19 Markdown 表块 |
| unstructured fast | 0.25.2 + `unstructured[pdf]` | 183.684s；3553 elements；0 Table element | 1.335s；216 elements；0 Table element |
| 项目 PyMuPDF | 1.28.2 | 3.063s；695 段；165 章；21 表；40 跨页段；3 跨页表 | 0.108s；30 段；6 章；0 表；6 跨页段 |
| 项目 pdfplumber | 0.11.10 | 6.221s；651 段；156 章；21 表；47 跨页段；4 跨页表 | 0.541s；28 段；5 章；0 表；6 跨页段 |
| Docling CPU | 2.120.1 | 214.746s；569 段；52 章；39 表；4 跨页表 | 45.618s；27 段；4 章；1 表；1 跨页表 |
| MinerU pipeline CPU | 3.4.5 | 1074.590s；868 段；141 章；29 表；4 跨页表 | 51.629s；33 段；4 章；1 表；0 跨页表 |
| pdf-oxide | 0.3.77 | 1.344s；66077 文本字符；49 页级表 | 0.062s；5152 文本字符；0 页级表 |
| pymupdf-layout + pymupdf4llm | 1.28.2 | 92.306s；77486 Markdown 字符；44 表分隔块；231 标题 | 5.577s；5516 Markdown 字符；1 表分隔块；8 标题 |
| marker-pdf | 2.0.0，CPU fast，disable OCR | 内部 52.705s/外部 72.549s；88275 Markdown 字符；meta 44 Table blocks | 内部 9.407s/外部 30.630s；4877 Markdown 字符；meta 2 blocks（页 8/9） |

### 10.3 指标口径与最终判定

1. 项目结构化后端的 `tables` 是共享后处理后的 `Document` 表格；原生 pdfplumber/PyMuPDF 的 53/50 是页级检测；Docling/MinerU 的字符数包含段落和表格单元格，三者不可直接用单一数字排名。
2. 本批次 Docling CPU 为 **39 表/4 跨页表**，MinerU pipeline 为 **29 表/4 跨页表**，与历史记录中的 Docling 29/9 不属于同一结果，不能混用。
3. 表格专项库的结论是：openparse 适合实验性节点/表格抽取，Camelot 在 样本 A 当前版 失败，tabula-py 能运行但过度拆分；三者都不替代项目 PyMuPDF/pdfplumber/Docling/MinerU 后端。
4. anydoc/firecrawl 未执行，因为依赖云端/API，不符合本轮离线同环境、避免项目数据外传的约束。
5. 本批次没有对这些原生专项输出接入 `Document`、`inspect_document()` 或真实 LLM 逐项审查；PyMuPDF 真实样本仍只能报告确定性 QA 的候选风险，不能声称“已通过 LLM 审查”或“没有断裂”。

生产建议仍为：数字文本 PDF 默认项目 PyMuPDF；QA 高风险页或无框线/复杂表格局部走 Docling；扫描件走 MinerU；pdf-oxide 作为高速预扫描；openparse/Camelot/tabula-py 仅作为专项实验工具。


## 11. Win11 CPU 覆盖范围与默认优先级复核（2026-08-17）

### 11.1 已覆盖的 CPU 能力

本轮在 Windows PowerShell 的隔离环境中，对样本 A（116 页、制度类数字 PDF）和样本 B（9 页、通报类数字 PDF）完成了以下 CPU 覆盖：

| 能力类别 | 已测试库/后端 | 覆盖情况 |
|---|---|---|
| 底层文本提取 | pypdf、pdfminer.six、pypdfium2、原生 pdfplumber、原生 PyMuPDF | 两个样本均完成实际运行，记录版本、耗时、字符数和页数 |
| 项目结构化 | 项目 PyMuPDF、项目 pdfplumber、Docling CPU、MinerU pipeline CPU | 均输出项目结构化指标；包含段落、章节、表格和跨页对象 |
| Markdown/版面输出 | pdf-oxide、pymupdf-layout/pymupdf4llm、Marker 2.0 fast | 均完成 CPU 运行；记录 Markdown、标题、表块或内部 block 指标 |
| 表格专项 | openparse、Camelot lattice/stream、tabula-py | 均完成实际调用；成功结果、空结果和 `StopIteration` 均已记录 |
| 通用文档解析 | unstructured[pdf] fast | 两个样本均完成运行，记录 elements 和 Table element 数量 |

因此，针对**数字型文本 PDF 在 Win11 CPU 上的本地可运行性、速度量级和主要结构化缺陷**，当前测试已经足以支持后端选型。

### 11.2 尚未覆盖、不能过度外推的能力

“已充分测试”仅针对本报告的两个数字型代表样本和当前配置，不等于所有 PDF 类型的完整能力认证：

1. 两个样本都有文本层，没有单独的扫描件、低质量图片、旋转页面、复杂公式或多语言样本；OCR 泛化能力尚未充分验证。
2. 本批次 Docling 使用 CPU 且关闭 OCR，Marker 使用 `fast + disable_ocr`，pymupdf-layout 使用 `use_ocr=False`；MinerU 测试的是 CPU pipeline，不是 VLM 模式。因此不能把本轮结果当作 OCR/VLM 质量结论。
3. openparse 只测试 PyMuPDF 表格策略，未测试 table-transformers/Unitable；Camelot 未对区域、线条和 `table_areas` 做针对性调参；tabula-py 只测试默认 `read_pdf(pages="all", multiple_tables=True)`，没有穷举 lattice/stream/area 组合。
4. 没有建立两份样本的完整人工真值标注，也没有对所有库执行真实 LLM 逐段审查；当前质量判断仍是结构化指标、原始输出抽检和确定性 QA 的组合。
5. anydoc/firecrawl 依赖云端或外部 API，未放入本地离线同环境比较。

### 11.3 默认配置和选择优先级是否变化

**没有改变默认优先级，但 CPU 下需要明确性能边界。** 当前建议仍为：

1. **默认 `auto` 路由**：数字文本 PDF → 项目 PyMuPDF；检测到无框线/复杂表格风险 → 局部升级 Docling；扫描件/图片 PDF → MinerU pipeline；不可用时回退 pdfplumber。
2. **数字文本 CPU 快路径：PyMuPDF 优先**。本批次项目后端样本 A/样本 B 为 3.063s/0.108s，快于项目 pdfplumber 的 6.221s/0.541s，且段落、章节和有框线表格输出满足当前主流程。
3. **规则兜底：pdfplumber**。其文本段落稳定性和可定制性仍有价值，但不应单纯因为 CPU 环境而替换 PyMuPDF 默认位置。
4. **复杂/无框线表格：Docling CPU**。本批次为 214.746s/45.618s，明显慢，但识别到 39/1 张表，仍适合按 QA 风险局部调用，不适合作为所有数字 PDF 的 CPU 默认后端。
5. **扫描件/OCR：MinerU pipeline CPU**。本批次为 1074.590s/51.629s，CPU 可运行但速度很慢；在 CPU-only 部署中应作为必要时的专用路径，而不是默认全量路径。
6. **不提升专项库优先级**：pdf-oxide 只作高速预扫描；openparse、Camelot、tabula-py 只作表格实验工具；Marker、unstructured 和 pymupdf-layout 不替代项目结构化主路径。

结论：Win11 CPU 实测没有理由改变 `auto → PyMuPDF/pdfplumber/Docling/MinerU` 的优先级；变化仅是部署提示——CPU-only 环境应尽量走 PyMuPDF，Docling/MinerU 必须按风险或文档类型局部调用，并为长耗时设置任务队列和超时。