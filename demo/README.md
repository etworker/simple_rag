# Demo：不同级别文档内容差异对比

用 `version_diff` 引擎对比**两份不同级别**的文档（例如《二级…信息技术管理手册》与
《三级…信息技术部工作手册》），找出内容上的**实质差异**（非版本差异）。

## 用法

```bash
uv run --project version_diff python demo/cross_level_diff.py <文档A路径> <文档B路径> [--out 报告输出] [--llm]
```

- `<文档A路径>`：视为「旧」
- `<文档B路径>`：视为「新」
- `--out`：报告输出到 `.md` 文件（不指定则打印到控制台）
- `--llm`：启用 LLM（对修改类差异生成摘要，需在 `.env` 配置有效的 `AWS_BEARER_TOKEN_BEDROCK`）；默认纯规则模式，无需 LLM
- `--embedding`：embedding 模型名（默认 `BAAI/bge-small-zh-v1.5`）

## 示例

对比《二级信息技术管理手册 R3-3》与《三级信息技术部工作手册 R6-7》：

```bash
uv run --project version_diff python demo/cross_level_diff.py \
  "data/pdf/(二级)(司批)信息技术管理手册/R3-3/(二级)(司批)信息技术管理手册.pdf" \
  "data/pdf/(三级)(司批)信息技术部工作手册/R6-7/(三级)(司批)信息技术部工作手册.pdf" \
  --out demo/reports/cross_level_diff.md
```

## 说明

- 输出按「新增 / 删除 / 修改」分组；「新增」= 文档B 有而 文档A 无，「删除」= 文档A 有而 文档B 无。
- 自动过滤「目录 / 记录清单 / 页码占位 / 短编号」等**版式噪声**（跨级文档常因体例不同而整体不同），
  聚焦实质内容差异。
- 需先在项目根 `.env` 配置 LLM 鉴权（`AWS_BEARER_TOKEN_BEDROCK` 或 `OPENAI_API_KEY`）。
