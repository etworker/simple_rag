# Demo：文档内容差异对比

用 `version_diff` 引擎对比两份文档（**同一文档不同版本 / 不同级别 / 不同文档**），
找出内容上的**实质差异**。噪声过滤机制已内置为系统能力（`CrossNoiseFilter`），超参数可配置。

## 统一入口：`compare_docs.py`（自动识别类型）

**一个脚本，自动识别两份文档是「同一文档不同版本」还是「不同文档/不同级别」，并自适应选择方法**：

```bash
uv run --project version_diff python demo/compare_docs.py <文档A> <文档B> [--out 报告] [--llm]
```

识别依据：**内容重叠度**（较小文档中能在另一文档找到相似段的比例）。
- 同文档不同版本：重叠度通常 >90%
- 跨文档/跨级别：通常 <10%

| 识别结果 | 使用的方法 | 输出 |
|---|---|---|
| 同一文档不同版本（重叠度≥50%） | `version_compare`（版本配对） | 新增/删除/修改 差异 |
| 不同文档/不同级别（重叠度<50%） | 相似度聚类（Jaccard） | 同一主题但表述不一致 |

**示例**（同一命令，自动选择方法）：
```bash
# 同一文档不同版本 → 自动用版本对比
uv run --project version_diff python demo/compare_docs.py \
  "data/pdf/(二级)(司批)网络与信息安全管理手册/R5-21/(二级)(司批)网络与信息安全管理手册.pdf" \
  "data/pdf/(二级)(司批)网络与信息安全管理手册/R5-22/(二级)(司批)网络与信息安全管理手册.pdf" \
  --out demo/reports/compare_same_doc.md

# 不同级别 → 自动用相似度聚类
uv run --project version_diff python demo/compare_docs.py \
  "data/pdf/(二级)(司批)信息技术管理手册/R3-3/(二级)(司批)信息技术管理手册.pdf" \
  "data/pdf/(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf" \
  --out demo/reports/compare_cross.md
```

## 批量验证：`batch_verify.py`

一键跑多类组合（同文档相邻/跨版、不同级别、不同文档），输出每组合差异统计 + 代表性差异：

```bash
uv run --project version_diff python demo/batch_verify.py --out demo/reports/batch_verify.md
```

内置组合与实测：
| 类型 | 组合 | 实测 |
|---|---|---|
| 同文档相邻版本 | 网络手册 R5-21 → R5-22 | 差异聚焦真实（修订记录/条款增减/重编号） |
| 同文档跨版 | 网络手册 R5-18 → R5-22 | 同上，变化更多 |
| 不同级别 | 二级管理手册 vs 三级工作手册 | 反映层级分工（二级定制度+附录体系） |
| 不同文档 | 网络手册 vs IT运维规范 | 差异主要来自各自独有声明/结构 |

## 说明

- **方法选择已自动**：`compare_docs.py` 自动识别类型，无需手动区分。
- 同文档版本对比用 `version_compare`；跨文件对比用相似度聚类（找同主题不同表述，如生效日期/编号/适用范围/签发主体不一致）。
- 默认纯规则模式（无需 LLM）；加 `--llm` 并对修改类差异生成摘要（需 `.env` 配置有效的 `AWS_BEARER_TOKEN_BEDROCK`）。
- 运行时生成的报告在 `demo/reports/`（已被 `demo/.gitignore` 忽略）。
