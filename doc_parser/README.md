# doc_parser

通用文档解析库 — 把 PDF / Word 文档解析为带定位信息的结构化段落与表格。

---

## 功能概览

- **PDF 解析**（`pdfplumber`）：提取正文段落 + 表格，自动过滤页眉/页脚/水印噪声
- **MinerU 后端**（可选）：使用 VLM/OCR 引擎解析复杂排版（无框线表格、多栏布局、扫描件），准确率 95%+
- **智能后端选择**：`backend: "auto"` 预扫描 PDF 特征，自动在 pdfplumber 和 MinerU 间选择最优后端
- **Word 解析**（`python-docx`）：提取段落 + 表格，检测章节结构
- **跨页段落拼接**：不按页分段，全文拼接后按语义信号（空行/章节标题/句末终止符）重新分段
- **跨页表格合并**：启发式识别被分页截断的同一表格，自动合并并跳过重复表头
- **章节自动识别**：通过正则匹配 `1.2.3`、`第3章` 等模式，为段落和表格标注章节号与标题
- **页眉/页脚过滤**：按坐标位置界定页眉页脚区域 + 统计高频重复行双重过滤
- **空白模板表格过滤**：空单元格率超过阈值的签到表/申请表自动排除
- **版本管理元数据剥离**：修订日期/版次/版本号等独立行从正文流中剥离
- **左侧编号列分离**：部分 PDF 将章节编号放在页面左侧 margin，按 x 坐标分离并拼接到行首
- **序列化支持**：`Document` / `Paragraph` / `Table` 均提供 `to_dict()` / `from_dict()`
- **Markdown 导出**：`Document.to_markdown()` / `parse_to_markdown()` 一键转为 Markdown 格式

---

## 依赖

| 依赖 | 用途 |
|------|------|
| `pdfplumber >= 0.11.4` | PDF 文本与表格提取 |
| `python-docx >= 1.1.2` | Word 文档解析 |
| `loguru >= 0.7.3` | 日志 |
| `mineru[all]` (可选) | VLM/OCR PDF 解析后端，需 GPU |

Python >= 3.10

---

## 安装

```bash
# 在 simple_rag 项目根目录下
uv sync --project doc_parser

# 或单独安装
cd doc_parser
uv sync

# 安装 MinerU 后端（可选，需 GPU）
uv pip install -U "mineru[all]"
```

---

## 快速上手

```python
from doc_parser import parse, Document, Paragraph, Table

# 基本用法
doc = parse("path/to/manual.pdf")
print(f"文件: {doc.filename}")
print(f"段落: {len(doc.paragraphs)} 个, 表格: {len(doc.tables)} 个")

for para in doc.paragraphs:
    print(f"  [{para.location}] {para.text[:80]}")

for table in doc.tables:
    print(f"  表格 #{table.index} ({table.location}), {len(table.rows)} 行")
    for row in table.rows:
        print(f"    {row}")
```

### Word 文档

```python
doc = parse("path/to/spec.docx")
# Word 文档无页码概念，para.page 统一为 0
```

### Markdown 导出

```python
from doc_parser import parse_to_markdown

# 一行搞定
md = parse_to_markdown("manual.pdf")
with open("manual.md", "w", encoding="utf-8") as f:
    f.write(md)

# 或从已解析的 Document 转换
from doc_parser import parse

doc = parse("manual.pdf")
md = doc.to_markdown()
```

Markdown 输出规则：
- 章节标题 → `#` / `##` / `###` 等（层级按编号深度推断：`1` → H1，`1.1` → H2，`1.1.1` → H3）
- 表格 → 标准 Markdown 表格语法（`| col | col |` + `|---|---|`）
- 表格内的 `|` 自动转义为 `\|`，换行替换为空格
- 段落和表格按页码 + 序号交错输出

### 智能后端选择（推荐）

`backend: "auto"` 模式会先对 PDF 做快速预扫描（< 1s），根据文档特征自动选择最合适的后端：

```python
from doc_parser import parse

# ── 智能模式（推荐）── 预扫描后自动决定
doc = parse("any.pdf", config={"extract": {"backend": "auto"}})
```

**决策流程：**

| 检测项 | 条件 | 选择 | 理由 |
|--------|------|------|------|
| 扫描件 | 平均文字 < 50 字/页 | MinerU | 需要 OCR |
| 扫描件 | 大图片覆盖 > 50% 页面 | MinerU | 图片型 PDF |
| 无框线表格 | 有绘图对象但 pdfplumber 提取不到表格 | MinerU | 表格被漏掉 |
| 无框线表格 | 文本含表头关键词 + 列对齐特征 | MinerU | 疑似表格 |
| 正常文档 | pdfplumber 表格提取正常 | pdfplumber | 速度快 |

预扫描只采样前 5 页，耗时通常 0.5-2s。若 MinerU 未安装，自动降级到 pdfplumber。

### 手动指定后端

```python
from doc_parser import parse

# ── 强制 MinerU ── 处理复杂排版（无框线表格、多栏布局、扫描件）
doc = parse(
    "complex_layout.pdf",
    config={
        "extract": {
            "backend": "mineru",  # 切换到 MinerU 后端
            "mineru_backend": "auto",  # 默认值：自动检测 GPU
        }
    },
)

# ── GPU 机器：强制 VLM（准确率 95%+）
doc = parse(
    "complex_layout.pdf",
    config={
        "extract": {
            "backend": "mineru",
            "mineru_backend": "vlm",
        }
    },
)

# ── CPU 机器：强制 pipeline（准确率 ~86%，无需 GPU）
doc = parse(
    "complex_layout.pdf",
    config={
        "extract": {
            "backend": "mineru",
            "mineru_backend": "pipeline",
        }
    },
)
```

MinerU 后端特点：
- **`auto`（默认）**：自动检测 GPU → 有则 VLM，无则 pipeline，**CPU 机器也能跑**
- **`vlm` 后端**（需 GPU ≥8GB VRAM）：准确率 95%+，擅长无框线表格和多栏布局
- **`pipeline` 后端**（CPU）：准确率 ~86%，无需 GPU，速度较慢
- 复用 `doc_parser` 的后处理逻辑（章节检测、跨页表格合并等）

| 机器 | `mineru_backend` | 实际后端 | 准确率 |
|------|------------------|----------|--------|
| 有 GPU | `auto`（默认） | vlm | 95%+ |
| 无 GPU | `auto`（默认） | pipeline | ~86% |
| 有 GPU | `vlm` | vlm | 95%+ |
| 任意 | `pipeline` | pipeline | ~86% |

GPU 推荐：T4 16GB / V100 / A10 / A100 均可，T4 性价比最高

---

### 自定义配置

```python
doc = parse(
    "manual.pdf",
    config={
        "extract": {
            "min_paragraph_length": 20,  # 过滤更短的段落
            "max_paragraph_length": 1000,  # 允许更长的段落
            "header_margin_pct": 5,  # 缩小页眉过滤区域
            "table_empty_cell_threshold": 0.8,  # 更宽松的模板表格过滤
        }
    },
)
```

---

## API 参考

### `parse(filepath, config=None) -> Document`

解析文档为结构化段落 + 表格。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `filepath` | `str` | 文件路径，支持 `.pdf` / `.docx` |
| `config` | `dict \| None` | 解析配置，可选。在 `config["extract"]` 下传入配置项 |

**返回：** `Document` 对象

**异常：** 不支持的格式（如 `.doc`）抛出 `ValueError`

### `parse_to_markdown(filepath, config=None) -> str`

解析文档并直接转为 Markdown（等价于 `parse(filepath, config).to_markdown()`）。

### `Document`

```python
@dataclass
class Document:
    filename: str
    paragraphs: List[Paragraph]
    tables: List[Table]

    def to_dict(self) -> dict        # 序列化
    def to_markdown(self) -> str     # 转 Markdown
    @classmethod
    def from_dict(cls, d: dict) -> Document  # 反序列化
```

### `Paragraph`

```python
@dataclass
class Paragraph:
    text: str               # 段落正文
    page: int               # 起始页码
    page_end: int           # 结束页码（跨页段落）
    chapter: str            # 章节号（如 "2.3"）
    chapter_title: str      # 章节标题
    source_file: str        # 来源文件名
    index: int              # 段落序号

    @property
    def location(self) -> str  # "第3-4页 / §2.3 / 备份策略"
    def to_dict(self) -> dict
    def to_markdown(self) -> str     # 转 Markdown 片段
    @classmethod
    def from_dict(cls, d: dict) -> "Paragraph"
```

### `Table`

```python
@dataclass
class Table:
    rows: list              # 二维数组 list[list[str]]
    headers: list           # 表头（可选）
    page: int               # 起始页码
    page_end: int           # 结束页码（跨页表格）
    chapter: str            # 所属章节号
    chapter_title: str      # 所属章节标题
    context_before: str     # 表格前一段正文
    source_file: str        # 来源文件名
    index: int              # 表格序号

    @property
    def location(self) -> str
    def to_dict(self) -> dict
    def to_markdown(self) -> str     # 转 Markdown 表格
    @classmethod
    def from_dict(cls, d: dict) -> "Table"
```

---

## 配置项

所有配置项通过 `config["extract"]` 传入，与默认配置深拷贝后合并。

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| `header_margin_pct` | `8` | 页眉区域占页面高度的百分比 |
| `footer_margin_pct` | `8` | 页脚区域占页面高度的百分比 |
| `repeat_line_threshold_pct` | `30` | 行出现次数超过此百分比 → 判为页眉/页脚 |
| `min_paragraph_length` | `10` | 段落最小字符数 |
| `max_paragraph_length` | `600` | 段落最大字符数，超长时强制断开 |
| `max_chapter_title_length` | `80` | 章节标题最大长度 |
| `sentence_break_min_length` | `40` | 句末断句的最小段落长度 |
| `chapter_patterns` | (4 条正则) | 章节标题匹配模式 |
| `noise_line_patterns` | (9 条正则) | 元数据行剥离模式 |
| `margin_number_x` | `130` | 左侧编号列 x 坐标阈值（0 = 禁用） |
| `margin_number_pattern` | `^(?:\d+(?:\.\d+)*\|[A-Z])$` | 编号列匹配正则 |
| `table_empty_cell_threshold` | `0.6` | 空单元格率阈值（1.0 = 禁用过滤） |
| `table_empty_placeholders` | `["□", "☐", "○", "——"]` | 视为空单元格的占位符 |
| `backend` | `"pdfplumber"` | PDF 解析后端：`"pdfplumber"` / `"mineru"` / `"auto"` |
| `mineru_backend` | `"auto"` | MinerU 后端模式：`"vlm"` / `"pipeline"` / `"auto"` |
| `mineru_output_dir` | `""` | MinerU 输出目录（空=临时目录） |
| `mineru_timeout` | `600` | MinerU 解析超时秒数 |

### 章节匹配模式（默认）

| 模式 | 匹配示例 |
|------|----------|
| `^(\d+\.\d+\.\d+)\s+(.+)` | `1.2.3 安全管理要求` |
| `^(\d+\.\d+)\s+(.+)` | `2.3 备份策略` |
| `^(\d+)\s+(.+)` | `3 总则` |
| `^第\s*(\d+)\s*[章节]\s*(.+)` | `第3章 运维管理` |

### 元数据行剥离模式（默认）

匹配以下独立行并从段落流中剥离：

- `修订日期：2026-05-08`
- `发布日期：2026-05-08`
- `修订时间：2026-05-08 14:30`
- `2026-05-08`（纯日期行）
- `版次：A`
- `版本号：v2.0`
- `R5-22` / `版本 v2.0`
- `修订次数：15 ... 页码`

---

## 不支持的格式

- `.doc`（旧版 Word 二进制格式）：需先用 Word/LibreOffice 转换为 `.docx`
- ~~图片型 PDF（扫描件）：本库不做 OCR~~ → **使用 MinerU 后端可解析扫描件**

---

## 示例脚本

`examples/convert_to_md.py` — PDF/Word → Markdown 转换示例：

```bash
uv run python examples/convert_to_md.py "your_file.pdf" --out output.md
```

---

## 设计文档

详细设计请见 [docs/设计文档.md](docs/设计文档.md)。

---

## 测试

```bash
cd doc_parser
uv run python -m pytest tests/ -q
```
