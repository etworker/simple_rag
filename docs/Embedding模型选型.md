# Embedding 模型选型（中文 RAG）

> 面向本项目（simple_rag）中文制度文档 RAG 的文本向量模型选型。
> 更新日期：2026-08-16 ｜ 状态：本机（T4 GPU）实测 bge-small-zh-v1.5 与 jina-v2-base-zh 版本对比结果完全一致，默认定为 **bge-small-zh-v1.5**（GPU 自动加速）；其余候选按推测标注。
>
> **部署边界**：Embedding 在单 CPU 机器上即可运行，T4 只是可选加速资源；A10G 不因 Embedding 而必需，主要服务于 AWS 中国区无法使用 Bedrock 时的自建 `GLM-4.7-Flash` LLM 推理。

---

## 1. 本项目对 embedding 的约束

| 约束 | 说明 |
|------|------|
| 推理后端 | **fastembed（ONNX Runtime）**——零 torch 依赖，`version_diff/device_utils.py::EmbeddingModel` 统一封装 |
| 可选模型 | 仅限 fastembed `TextEmbedding.list_supported_models()` 中的模型 |
| 硬件 | 单 CPU 机器即可运行；T4 GPU 可选，用于加速 Embedding（也可与 Docling/MinerU 共享但需关注显存）；A10G 主要保留给 AWS 中国区自建 `GLM-4.7-Flash` LLM 推理，Embedding 不要求 A10G |
| 语种 | 中文制度文档（手册/规范/通报），段落级检索 |
| 离线 | 模型本地缓存（HF 镜像下载后离线可用） |
| 配置保存 | `POST /api/config` 保存后需要重启（返回 `restart_required=true`）；Web 应用的模型名从配置文件读取，当前 fastembed/ONNX 路径不应用 `dtype` 改变运行精度；`SIMPLE_RAG_EMBEDDING_DEVICE` 可优先覆盖设备 |
| 索引切换 | 更换模型或维度后不能混用旧向量；需清空/重建知识库并重新确认入库，不能只重启服务 |

---

## 2. 主流中文 embedding 模型（业界全景，未实测）

> ⚠️ 本节为业界公开信息（MTEB / 模型卡 / 技术报告），非本机实测。

| 梯队 | 模型 | 维度 | 说明 |
|------|------|------|------|
| 新一代 SOTA | **Qwen3-Embedding-0.6B/4B/8B**（阿里 2025） | 1024/2560/4096 | MTEB 中文第一梯队；指令/无监督双模式；需 transformers/vllm，**fastembed 不支持** |
| 经典中文强 | **BGE-M3**（BAAI） | 1024 | 多语言 + 多粒度 + 三路检索（dense/sparse/multi-vector） |
| | bge-large-zh-v1.5 | 1024 | 中文检索经典强模型 |
| | GTE 系列（gte-large-zh / gte-Qwen2-7B-instruct） | 1024/3584 | 阿里中文专项 |
| 多语言 | multilingual-e5-large | 1024 | MTEB 多语言强 |
| | jina-embeddings-v2/v3 | 768/1024 | 8K 上下文 |
| 轻量 | bge-small-zh-v1.5 | 512 | 快、小 |
| 其他中文专项 | M3E / text2vec / Conan / stella-large-zh / BCE | 各不同 | 生态各异 |

---

## 3. fastembed 支持的中文/多语言模型（本机清单）

> ✅ = 本机已实测；⚠️ = 未实测（推测）

| 模型 | 维度 | 大小 | 实测/推测 | 备注 |
|------|------|------|----------|------|
| **jinaai/jina-embeddings-v2-base-zh** | 768 | 0.64GB | ✅ **实测** | 中文；8K 上下文；GPU 加载+编码正常，同文本相似度 1.0000 |
| **BAAI/bge-small-zh-v1.5** | 512 | 0.09GB | ✅ **实测** | 中文；本项目原默认，GPU 编码正常 |
| intfloat/multilingual-e5-large | 1024 | 2.24GB | ⚠️ 推测 | 多语言最强之一；⚠️ 推测 T4 上与 docling 共存显存偏紧 |
| sentence-transformers/paraphrase-multilingual-mpnet-base-v2 | 768 | 1.00GB | ⚠️ 推测 | 多语言通用；⚠️ 推测效果中等 |
| sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | 384 | 0.22GB | ⚠️ 推测 | 轻量多语言；⚠️ 推测维度低检索精度一般 |
| thenlper/gte-large | 1024 | 1.20GB | ⚠️ 推测 | ⚠️ 偏英文，中文非专项，不推荐 |

> **未实测说明**：multilingual-e5-large / paraphrase-multilingual-mpnet-base-v2 等仅从模型卡/公开评测推测，
> 未在本项目文档上跑过检索对比，标注 ⚠️；如需采用需先实测（切换后重置知识库重测检索命中率）。

---

> 说明：本节及 §4.1 是 **2026-08-16 的历史实验快照**，样本、硬件、缓存状态和后端配置有限，不是 SLA，也不代表每次部署耗时或结果固定。当前 Web 默认仍以 `rag_server/config.json`/示例配置为准：`BAAI/bge-small-zh-v1.5`、fastembed/ONNX、`device=auto`。

| 模型 | 维度 | 加载+编码（GPU） | 同文本相似度 | 结论 |
|------|------|----------------|-------------|------|
| BAAI/bge-small-zh-v1.5 | 512 | 秒级（轻量） | 1.0000 | **当前默认**；轻量、快、版本对比与 jina 结果一致 |
| jinaai/jina-embeddings-v2-base-zh | 768 | 45s（含首次模型下载 0.64GB） | 1.0000 | 备选；base 级更准、8K 上下文 |

### 4.1 版本对比实测：jina vs bge-small（2026-08-16 网络手册 R5-21→R5-22）

**结论：解析后端 + 吸收逻辑是版本对比差异的主要来源，embedding 模型影响很小。**

相同 docling 解析（页眉剥离+碎尾合并）+ 相同纯规则 LLM 下：

| 模型 | 设备 | 耗时（含向量计算冷启动） | 结果 |
|------|------|----------------------|------|
| jina-v2-base-zh | GPU | 6.3s | 6 条（removed=0/added=1/modified=5） |
| bge-small-zh-v1.5 | CPU | 44.3s | 6 条（removed=0/added=1/modified=5） **完全一致** |
| bge-small-zh-v1.5 | GPU | 8.9s | 6 条（removed=0/added=1/modified=5） **完全一致** |

- 三组结果逐条一致：removed=0（半句"用户应自行承担…"被吸收进 modified）、added=1、modified=5、minor=13。
- **版本对比对 embedding 不敏感**：配对由精确文本哈希桶 + 语义检索兜底，阈值 0.80 下两类模型都能正确配对主要段落；
  差异（如 removed 半句）由解析碎片 + 吸收逻辑决定，与 embedding 无关。
- 耗时差异：GPU 8-9s vs CPU 44s（约 5 倍）；纯 CPU 用 bge-small 完全可接受（单次对比 <1 分钟）。

**切换决策（08-16 更新：默认切 bge-small）**：
- 版本对比实测（网络手册 R5-21→R5-22、信息技术手册 R3-2→R3-3）：**bge-small 与 jina 结果完全一致**
  （removed 吸收、modified 配对逐条相同），差异主要来自解析后端与吸收逻辑而非 embedding 模型。
- bge-small 优势：模型 0.09GB（vs 0.64GB）、GPU 8.9s / 纯 CPU 44.3s（单次对比 <1 分钟）均可跑；
  GPU 环境 auto 自动加速。RAG 检索如遇长尾语义区分度不足，可再切换 jina（768 维 + 8K 上下文）。

---

## 5. 推荐结论

| 场景 | 推荐 | 依据 |
|------|------|------|
| **当前（默认）** | **BAAI/bge-small-zh-v1.5** | 轻量（0.09GB）、GPU/CPU 均可、版本对比与 jina 完全一致，实测可用 |
| RAG 检索要求更高语义区分度 | jinaai/jina-embeddings-v2-base-zh | 768 维 + 8K 上下文，base 级评测更优（版本对比无差异，RAG 长尾可选） |
| 追求多语言最强 | multilingual-e5-large（⚠️ 需实测显存） | 1024 维，T4 与 docling 共存偏紧 |
| 极致中文（未来） | Qwen3-Embedding / BGE-M3 | 需扩展 embedding 后端（fastembed → transformers/ONNX 直载），⚠️ 未实测 |

**升级路径（换后端以支持 SOTA 模型）**：
1. 在 `device_utils.EmbeddingModel` 增加 transformers/ONNX 直载分支（保留 `.encode()` 接口，下游零改动）
2. 加载 Qwen3-Embedding-0.6B（GPU 约 2-4GB）或 BGE-M3
3. 切换后重置知识库重测

---

## 6. 配置方法

```jsonc
// rag_server/config.json
{
  "embedding": {
    "model": "BAAI/bge-small-zh-v1.5",             // 默认；GPU 环境 auto 自动加速
    "device": "auto",                                  // auto/cuda/cpu
    "dtype": "auto"
  }
}
```

> 保存配置后先重启服务再验证；如果模型、维度或相关 embedding 配置变化，按 API 返回的提示执行知识库 reset/rebuild。模型缓存目录只缓存模型权重；向量缓存目录按文档内容和有效配置保存向量/FAISS 索引，不会把不同模型的向量自动转换或合并。