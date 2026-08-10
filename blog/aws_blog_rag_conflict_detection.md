# 使用 Amazon Bedrock 为 RAG 知识库构建入库冲突检测

> **导读**：标准 RAG 系统在文档入库时不做一致性校验，当知识库中多份文档对同一事项描述矛盾时，用户在检索阶段才会发现冲突——甚至根本不会被告知。本文介绍一种"入库时主动检测知识冲突"的方案，结合 Amazon Bedrock 的大模型推理能力，在文档进入向量库之前完成矛盾拦截，为企业合规文档 RAG 系统构建质量门禁。

---

## 一、背景与挑战

在航空、金融、医疗等强监管行业，企业通常维护着大量管理手册和标准操作程序（SOP）。以某航空公司为例，仅网络与信息安全领域就有多份手册，每份手册经过数十次修订，形成 R5-18、R5-19、R5-22 等多个版本共存的局面。当这些文档被纳入 RAG 知识库后，一个关键问题浮现：**如果两份手册对同一流程的描述存在矛盾，RAG 系统该怎么办？**

下图是某管理手册的修订记录表，展示了典型的多版本共存局面：

**图0：文档修订记录表（多版本共存）**

![修订记录表](images/pdf_page_1_revision.png)

<!-- 
📌 手动截图说明（修订记录表）：
- 来源：PDF 第1页，修订记录表
- 要点：展示 R4-0 到 R5-22 的多版本修订历史，突出"多版本共存"
- 脱敏：可保留版本号和日期，模糊批准人列
- 尺寸：建议宽度 800px，PNG 格式
-->

标准 RAG 的处理流程是：文档上传 → 解析切块 → Embedding → 写入向量库。整个过程不做任何一致性校验。当用户提问时，检索模块可能同时召回两段互相矛盾的内容。此时大语言模型（LLM）面临两难选择——要么任选其一（可能选错），要么模糊回答（"根据文档A...但文档B指出..."），用户体验极差。

更严重的是，在合规场景下，这种矛盾不仅是体验问题，更是**安全隐患**。例如某航空公司的信息安全管理手册中，如果一份文档规定"数据备份频率为每日一次"，另一份规定"每周一次"，员工按错误版本执行可能导致数据丢失，进而触发监管处罚。

我们需要的是：**在文档入库阶段就主动发现并拦截这类矛盾，而不是等到用户问到时才被动暴露。**

---

## 二、方案概述

本方案的核心理念是：**从被动到主动——在文档进入 RAG 知识库之前，增加一道"质量门禁"（Quality Gate）**，自动检测新文档与已有知识库中的内容是否存在冲突。

整体流程如下：

```
新文档上传 → 文档解析 → 语义配对 → LLM冲突判定 → 人工确认 → 入库/驳回
                ↓                      ↓
         结构化段落/表格         与已有文档中相似段落配对
```

[架构图: 左侧为"文档上传"入口（S3），中间为"解析+冲突检测"处理层（FastAPI服务，包含pdfplumber解析、BGE向量配对、Bedrock LLM判定三个子模块），右侧为"向量知识库"（FAISS索引）。用户通过Web UI上传文档，处理层输出检测报告，用户确认后文档才正式入库。底部标注AWS服务：Amazon S3、Amazon Bedrock、EC2/本地部署]

![系统架构图](images/simple_rag.png)

<!-- 
架构图绘制指南（使用 draw.io + AWS Architecture Icons）：

布局（从左到右）：
1. 左列 — 用户/输入：
   - AWS 图标: Users（用户）+ S3 Bucket（文档上传）
2. 中间 — 处理层（大矩形框，标题"FastAPI on EC2"）：
   - 子模块A: 文档解析（pdfplumber, 图标用 AWS Lambda 或 EC2 Instance）
   - 子模块B: 语义配对（BGE + FAISS, 图标用 Amazon OpenSearch 或自定义 ML）
   - 子模块C: 冲突判定（LLM, 图标用 Amazon Bedrock）
   - 子模块D: 质量门禁（通过/驳回, 图标用 AWS Step Functions 或 Gateway）
3. 右列 — 输出/存储：
   - FAISS 向量知识库（图标用 Amazon OpenSearch Serverless）
   - Amazon S3（检测报告归档）
4. 底部虚线框 — 中国区替代方案：
   - EC2 G5 + llama.cpp + GLM-4.7-flash Q4-K-M（2并发×80K上下文）

箭头流向：S3上传 → 解析 → 配对 → 判定 → 门禁 → (通过) → FAISS入库
                                          ↓ (Bedrock API调用)
                                     Amazon Bedrock (东京/美国)
-->

与标准 RAG 系统相比，我们在"解析"和"入库"之间插入了冲突检测环节。只有通过检测（或人工确认）的文档才能进入知识库，从源头保证知识库的一致性。

---

## 三、文档解析层

### 挑战

企业合规文档（尤其是 PDF 格式的管理手册）排版复杂，给自动化解析带来诸多挑战：

| 排版特征 | 具体表现 | 对解析的影响 |
|----------|---------|-------------|
| 左侧编号列 | 章节编号（1.1、1.1.2）在页面左 margin 区域 | pdfplumber 按 y 坐标聚行时编号和正文混在一起，章节识别不稳定 |
| 跨页表格 | 大表格跨 2-3 页，续页可能重复表头 | 被拆成多个独立表格，丢失整体性 |
| 页眉页脚元数据 | "修订日期：2024-01-02 BK-J-62 页码：1.1-1" | 混入正文段落，干扰语义匹配 |

下图展示了实际 PDF 文档的排版特征：

**图1：目录页 — 左侧 margin 编号列（4/4.1/5/5.1/6/6.1...）与右侧标题分离排版**

![目录页示例](images/pdf_page_15_toc.png)

**图2：有效页清单 — 密集的结构化表格（页号×修订次数×状态×日期）**

![表格页示例](images/pdf_page_9_table.png)

<!-- 来源：第15页目录 + 第9页有效页清单，无需脱敏，仅遮盖 logo -->

### 方案：配置化解析策略

我们基于 pdfplumber 构建了一套配置化的文档解析器，通过 YAML 配置适配不同文档的排版特征，无需为每种文档硬编码处理逻辑。

**核心技术亮点：左 margin 编号列自动分离**

```python
def _assemble_line(line_words, margin_number_x, margin_number_re):
    """
    将同一行的 word 列表拼接为文本。
    当启用 margin 编号列分离时，自动将左侧编号固定到行首，
    消除 PDF 文字流顺序的不确定性。
    """
    sorted_by_x = sorted(line_words, key=lambda w: w['x0'])

    if margin_number_x <= 0 or margin_number_re is None:
        return ' '.join(w['text'] for w in sorted_by_x)

    # 分离编号列和正文列
    number_parts = []
    body_parts = []
    for w in sorted_by_x:
        if w['x0'] < margin_number_x and margin_number_re.match(w['text'].strip()):
            number_parts.append(w['text'].strip())
        else:
            body_parts.append(w['text'])

    body_text = ' '.join(body_parts)
    if number_parts:
        number_text = number_parts[0]
        return f"{number_text} {body_text}" if body_text else number_text
    return body_text
```

对应的配置：

```yaml
extract:
  margin_number_x: 130          # 编号列 x 坐标阈值（pt）
  margin_number_pattern: '^(?:\d+(?:\.\d+)*|[A-Z])$'
  table_empty_cell_threshold: 0.6  # 空白模板表格过滤
```

通过这套方案，我们对某航空公司的 179 页管理手册实现了：
- 章节识别准确率从 ~50% 提升到 ~95%
- 14 处跨页表格自动合并
- 17 个空白模板表格（签到表/申请表）自动过滤

---

## 四、语义配对与冲突检测

文档解析完成后，需要将新文档中的每个段落与知识库中已有的段落进行**语义配对**，找出描述相似主题的段落对，然后由 LLM 判断它们之间是否存在矛盾。

### 4.1 向量相似度配对

我们使用 BGE（BAAI/bge-base-zh-v1.5）模型对段落进行向量化，通过 FAISS 索引实现高效的相似度检索：

```python
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

class VectorStore:
    def __init__(self, model_name="BAAI/bge-base-zh-v1.5"):
        self.model = SentenceTransformer(model_name)
        self.index = None

    def build_index(self, paragraphs):
        embeddings = self.model.encode([p.text for p in paragraphs])
        self.index = faiss.IndexFlatIP(embeddings.shape[1])
        faiss.normalize_L2(embeddings)
        self.index.add(embeddings)

    def find_similar(self, query_text, top_k=3, threshold=0.80):
        query_vec = self.model.encode([query_text])
        faiss.normalize_L2(query_vec)
        scores, indices = self.index.search(query_vec, top_k)
        return [(idx, score) for idx, score in zip(indices[0], scores[0])
                if score >= threshold]
```

当新文档的某个段落与已有知识库中的段落相似度超过阈值（默认 0.80）时，这对段落被标记为"候选冲突对"，进入下一步 LLM 判定。

### 4.2 LLM 差异判定

候选冲突对被批量发送给 Amazon Bedrock 上的大语言模型进行深度分析。我们设计了一套**差异分类体系**：

| 类别 | 含义 | 处理方式 |
|------|------|---------|
| `inconsistency` | 两份文档对同一事项描述矛盾 | **阻断入库**，需人工确认 |
| `substantive` | 流程步骤、技术参数有实质变更 | 提醒用户，建议确认 |
| `scope` | 适用范围不同 | 提示，不阻断 |
| `numbering` | 仅编号/格式调整 | 自动放行 |
| `metadata` | 修订日期等元数据差异 | 自动忽略 |

LLM 判定的核心调用（通过 Amazon Bedrock Converse API）：

```python
import boto3

def judge_differences(diff_pairs, model_id="us.amazon.glm-4.7-flash"):
    """批量调用 Bedrock 判定差异类别"""
    client = boto3.client("bedrock-runtime", region_name="us-east-1")

    prompt = f"""你是文档一致性审查专家。以下是 {len(diff_pairs)} 对相似段落，
请判断每对之间是否存在实质性矛盾。
对于每一对，输出分类：inconsistency/substantive/scope/numbering/metadata
以及简要理由。

{format_pairs(diff_pairs)}"""

    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 2048}
    )
    return parse_judgments(response)
```

为控制成本，我们采用**批量调用策略**：每 5 对差异合并为一次 LLM 请求，而非逐对调用。对于 179 页文档的完整检测流程，通常只需 10-15 次 LLM 调用。

---

## 五、版本对比 vs 一致性检查：双模式设计

实际业务中，"文档变更"有两种不同的场景需求：

### version 模式：同一文档新旧版本对比

当用户上传某手册的新版本（如 R5-22 替代 R5-21）时，系统自动对比两个版本的差异，帮助审核人员快速定位变更点。关注的是"**改了什么**"。

### consistency 模式：跨文档一致性核查

当知识库中同时存在多份文档（如《信息安全管理手册》和《IT运维管理规范》），系统检查它们对同一事项的描述是否一致。关注的是"**是否矛盾**"——即 RAG 冲突风险。

```yaml
# config.yaml
diff:
  mode: "consistency"   # "version" | "consistency"
  similarity_threshold: 0.80
  enable_llm_filter: true
  categories:
    inconsistency:
      show: true
      label: "文档间不一致"
      description: "两篇文档对同一事项的描述存在矛盾（RAG冲突风险）"
    substantive:
      show: false
      label: "实质内容变更"
      description: "流程步骤、技术参数有实际不同"
```

双模式共享同一套解析和配对基础设施，仅在 LLM 判定的 prompt 和分类展示策略上有差异，实现了代码复用的最大化。

---

## 六、在 AWS 上的部署架构

本方案在 AWS 上的典型部署方式如下：

| 组件 | AWS 服务 | 说明 |
|------|---------|------|
| 文档存储 | Amazon S3 | 原始 PDF/DOCX 存储，支持版本控制 |
| LLM 推理 | Amazon Bedrock | 调用 GLM-4.7-flash（东京/美国区域）进行差异判定 |
| 应用服务 | EC2 / ECS | 运行 FastAPI + 文档解析 + FAISS |
| 向量模型 | 本地 GPU 实例 | BGE 模型推理（也可用 Bedrock Embedding） |
| 前端 | S3 + CloudFront | 静态 SPA 页面 |

**中国区域部署要点**（无 Amazon Bedrock）：

在 AWS 中国区域（北京/宁夏），Amazon Bedrock 服务不可用。我们采用 **EC2 G5 实例自建 LLM** 的替代方案：

- **模型**：GLM-4.7-flash 的 Q4-K-M 量化版本（约 5GB 显存）
- **推理引擎**：llama.cpp，提供 OpenAI-compatible API 接口
- **性能**：2 并发 × 80K 上下文窗口，满足批量冲突判定需求
- **代码切换**：仅需修改 config 中的 `base_url` 和 `model` 参数，业务代码零改动

向量模型（BGE）在本地 GPU 实例上运行，无需出境流量，满足数据合规要求。

---

## 七、效果与案例

### 实际解析效果

以某航空公司《网络与信息安全管理手册》R5-22 版本为例：

| 指标 | 数值 |
|------|------|
| 文档页数 | 179 页 |
| 提取段落数 | ~320 段 |
| 检测到的表格 | 42 个 |
| 跨页表格自动合并 | 14 处 |
| 空白模板表格过滤 | 17 个 |
| 端到端解析耗时 | < 5 秒 |

### 冲突检测案例（已匿名化）

在对两份手册（信息安全管理手册 vs IT运维管理规范）进行一致性检查时，系统检测到以下矛盾：

> **文档A** §5.1："网络巡检频率为**每日一次**，巡检内容应包括核心交换机、防火墙..."
>
> **文档B** §3.2："日常网络巡检**每周不少于两次**，重点关注网络设备运行状态..."

系统将此判定为 `inconsistency`（文档间不一致），自动阻断入库并提醒管理员确认。

### 对比：有无冲突检测的差异

| 维度 | 无冲突检测 | 有冲突检测 |
|------|-----------|-----------|
| 矛盾发现时机 | 用户提问时（被动） | 入库时（主动） |
| 用户感知 | 收到模糊/矛盾的回答 | 知识库始终一致 |
| 修复成本 | 高（需追溯+重新索引） | 低（入库前拦截） |
| 合规审计 | 有风险 | 有完整变更记录 |

---

## 八、总结与展望

本文介绍了一种在 RAG 系统入库阶段实现知识冲突主动检测的方案。通过配置化的文档解析、向量语义配对、LLM 智能判定的三层架构，我们在文档进入知识库之前就拦截了潜在的矛盾内容，从源头保证了知识库的一致性。

**适用场景**：
- 航空公司：运行手册、安全管理手册多版本管理
- 金融机构：合规政策文档跨部门一致性
- 医疗行业：诊疗指南、操作规程版本控制
- 制造业：质量体系文件、工艺标准管理

**下一步计划**：
- 构建多文档关联知识图谱，可视化文档间的引用和覆盖关系
- 基于冲突检测结果，自动生成修改建议
- 支持增量更新检测，仅对变更段落重新配对

---

## 关于作者

[作者姓名] 是 [所属团队] 的 [职位]，专注于 [领域方向]。

---

*本文中的代码示例可在 [GitHub 仓库链接] 获取。如有问题，欢迎通过 [联系方式] 与我们交流。*
