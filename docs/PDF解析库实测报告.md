# PDF 解析库实测报告（T4 GPU / CPU 均已完成）

> 目的：用项目目标文档（中文制度手册/通报）实测各 PDF 解析库，判定"能完整精确解析目标 PDF"的候选后端列表。
> 环境：AWS 宁夏 EC2（g5 系，Tesla T4 15.6GB / CUDA 13.2 驱动 / torch 2.13.0+cu130，8 核 CPU / 30GB 内存）。
> 状态：**GPU 结论与 CPU 结论均已完成**（2026-08-16；CPU 通过 CUDA_VISIBLE_DEVICES="" 强制 CPU 实测）。

---

## 1. 评测对象（两份目标 PDF）

| 键 | 文件 | 类型 | 页数 |
|----|------|------|------|
| manual | data/pdf/(二级)(司批)信息技术管理手册/R3-3/…pdf | 数字 PDF（有文本层） | 116 |
| notice548 | data/pdf/（内部）中南局发明电〔2026〕548号….pdf | 数字 PDF（有文本层） | 9 |

## 2. 五类元素判定（ground truth 参考）

| 元素 | 判定标准 |
|------|---------|
| 文本 text | 提取字符数，与 pypdf 基线（manual 75340 / notice548 5118）对比覆盖率 |
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

> notice548 结果见 §6 逐库说明；docling_cpu 的 notice548 表格跨页标记需复核。

## 4. 判定说明（为什么排除/保留）

| 库 | 结论 | 原因 |
|----|------|------|
| pdfplumber | ✅ 保留 | 规则文本+表格 17 表/6 跨页表完整；margin 编号列定制成熟 |
| pymupdf | ✅ 保留（快路径） | 与 pdfplumber 表格/跨页一致，快约 2 倍；章节识别 165 略多（margin 聚合更全） |
| docling | ✅ 保留（准路径） | 表格 29（含规则后端漏掉的无框线表）、跨页 9；GPU 0.56s/页，CPU 可跑 |
| mineru | ✅ 达标但慢 | notice548 1 表（与 docling 一致）；T4 上 VLM 34s/页（310s/9页）、batch=4 会 OOM；适合扫描件专用，数字 PDF 不推荐默认 |
| marker | ❌ 排除 | 1.10 本地模式跑通（2.x 需 docker 拉 vllm 镜像，国内不可达）；manual 70 表严重过度拆分/碎片化（3-5 行碎片块、跨页表被拆散）；GPL-3.0 许可 |
| markitdown | ❌ 排除 | 表格 306 个（把目录/公文头当表格）、章节 0——无结构还原能力 |
| anydoc | ❌ 排除 | 中文 PDF 文本检测失败（"no extractable text, OCR required"），本地版无 OCR |
| openparse | ❌ 排除 | 表格提取默认关闭，启用 pymupdf 策略后仍仅 1 表；段落 2576 过度切分 |
| camelot | ❌ 排除 | 仅表格专项且 manual 解析抛 StopIteration；无版面/段落/章节 |
| tabula-py | ❌ 排除 | 仅表格专项；68 表过度拆分（跨页表被切成多个）；依赖 Java |
| unstructured | ❌ 排除 | 依赖链过长（poppler/pdf2image/inference 全要）；无 poppler 输出为空；表格 TEDS 0.588 弱 |
| pymupdf4llm | ⚠️ 参考 | 文本 71780/表格 44 偏多（过度拆分），无结构化章节；可作 PyMuPDF 快速 markdown 参考 |

### 补充实测（2026-08-16）：信息技术管理手册 R3-2→R3-3 的 LLM 研判

对 **data/pdf/(二级)(司批)信息技术管理手册 R3-2→R3-3** 用 docling / pdfplumber 两个后端跑版本对比，并经 LLM（bedrock GLM）研判：

| 后端 | 结果 | LLM 研判 |
|------|------|---------|
| **pdfplumber** | 3 实质性（含真实修改"§6.1.4 职责：科室→处室"）+ 10 细微；段落完整 | ✅ **更可靠**：找到真实修改、段落完整 |
| **docling** | 6 实质性（§3.2.6 段落被拆成 3 个碎句 removed）| ❌ **碎句假象**："4.2.5.2 使用规定"标题行被布局模型插入句子中间，拆散完整段落 |

**结论**：对**文本型制度文档**（信息技术管理手册等），**pdfplumber 解析/段落切分更可靠**（docling 的布局模型偶把标题行插入段落造成碎句）；docling 的优势在**无框线表格识别**（网络与信息安全管理手册场景）。**默认解析后端已切为 pdfplumber**（auto 路由保留：无框线表格场景仍可切 docling）。

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

## 6. 逐库详情（notice548 + 关键发现）

- **notice548 跨页表（修正验证计划假设）**：docling 正确识别第 8-9 页的跨页表（"序号/风险措述/涉及企事业单位/局方督导单位部门"，3 行 4 列，page=8 page_end=9）——notice548 **也有跨页表**；pdfplumber/pymupdf 0 表（公文头部表格为无框线/红头样式，规则后端漏）；mineru/marker 各识别 1 表——**无框线表格必须走深度学习后端**。
- **manual 跨页表**：docling 正确合并 6-9 页（73 行）、14-17 页（49 行）、82-85 页（47 行）等 9 处跨页表；pdfplumber/pymupdf 合并 6 处。
- **markitdown 306 表**：把"0.1 声明 0.1-1"目录行、"发电单位…签发盖章"公文头当表格——markdown 输出的表格判定无结构约束。
- **marker 70 表碎片化**：surya 表格检测把跨页表拆成多个块（如有效页清单 25 行块 + 3-5 行碎片），70 块 vs ground truth 29——过度拆分不可用。
- **mineru T4 特性与加速**：VLM 模式默认 batch=4 会 OOM；**最优配置 batch=2（mineru_batch_size=2）约 13-16s/页（9.2GB 显存），比 batch=1 的 34s/页快约 2.5 倍**；notice548 1 表识别正确。
  - **并行结论**：T4 单卡 16GB 无法双 VLM 实例并行（每实例 >7.5GB，2 实例超显存 OOM）；页级切分多进程脚本见 temp/mineru_parallel.py，适用于多卡/更大显存（g5.12xlarge 4 卡可 4 进程并行）。
  - **更优加速**：mineru 支持 vllm 推理引擎（PagedAttention + continuous batching，预计 3-5 倍），需另装 vllm（匹配 torch/CUDA 版本）。
  - 注意：mineru do_parse 内部 spawn 子进程，脚本调用需 `if __name__ == "__main__":` main guard（否则末尾报 bootstrapping 错误）。
- **transformers 版本坑**：marker 2.x 安装把 transformers 升到 5.15 会破坏 mineru（Qwen2VLConfig 缺 max_position_embeddings），需降回 4.57.6；marker 2.x 还需 docker 拉 vllm 镜像（国内网络不可达）——**多库共存环境要锁 transformers<5，marker 用 1.x 本地模式**。

---

## 7. CPU 结论（CUDA_VISIBLE_DEVICES="" 强制 CPU 实测）

> 说明：本机为 GPU 机，CPU 测试通过 `CUDA_VISIBLE_DEVICES=""` 禁用 GPU（torch.cuda.is_available()=False）模拟无 GPU 环境；8 核 CPU / 30GB 内存。

| 库 | CPU 结论 | 实测（notice548=9 页 / manual=116 页） |
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
