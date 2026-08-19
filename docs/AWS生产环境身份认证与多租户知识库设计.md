# AWS 生产环境身份认证与多租户知识库设计

> **文档定位**：生产环境的架构思考与候选方案，不是当前代码的实现说明，也不要求本轮完成代码改造。
>
> **核心目标**：在尽量使用 AWS 托管服务的前提下，支持客户已有用户系统、轻量主体标记、多用户分组以及“不同身份用户只能看到授权知识库”的场景。
>
> **当前状态**：当前仓库仍是 FastAPI + 本地文件系统 + 进程内 FAISS 的单机实现，没有正式认证、租户隔离、文档 ACL、OpenSearch、Aurora、DynamoDB 或 S3 适配。本设计与 [架构文档](架构文档.md) 和 [快速原型部署方案](快速原型部署方案.md) 中的目标云方案保持一致，但不应被理解为已经具备生产安全能力。

---

## 1. 先确定三个边界

用户、权限和知识库是三个不同概念：

1. **认证（Authentication）**：当前请求是谁发起的？
2. **授权（Authorization）**：这个主体可以访问哪些租户、分组、知识库和操作？
3. **检索范围（Retrieval scope）**：本次问答实际允许检索哪些文档和向量？

外部用户系统可以负责认证，Simple-RAG 不必保存用户姓名、手机号等个人资料；但是 Simple-RAG 仍然必须获得一个可信、不可由前端随意伪造的主体标识和授权范围。

推荐的最小请求上下文为：

```text
principal_id       主体稳定标识，例如用户、服务账号或 API 客户端
identity_source    身份来源，例如 oidc、gateway、api_key、system
tenant_id          租户/客户标识，单租户时可为 default
groups             主体所属分组
roles              操作角色，例如 reader、editor、admin
allowed_scopes     服务端计算出的知识库范围
```

其中 `allowed_scopes` 不能由浏览器直接提交。客户端可以请求某个知识库，但最终允许范围必须由已验证的 Token、可信网关或服务端权限映射计算得到。

---

## 2. AWS 原生优先的总体原则

### 2.1 优先使用 AWS 托管能力

| 问题 | 优先考虑的 AWS 服务 | 说明 |
| --- | --- | --- |
| 用户登录/用户目录 | Amazon Cognito User Pools（目标区域可用时） | 管理用户、登录和 OIDC/OAuth2 Token；可联邦客户已有 SAML/OIDC 用户系统 |
| 外部用户系统接入 | Application Load Balancer OIDC 或 API Gateway JWT Authorizer | 把认证验证尽量放在入口层，应用只消费已验证的 Claims |
| API 入口 | Amazon API Gateway | JWT 校验、路由、限流、访问日志；适合 API-first 场景 |
| Web 入口 | Application Load Balancer + WAF | 适合 ECS/Fargate 上的 Web 服务和 SSE/长连接，认证可由 ALB OIDC 承担 |
| 权限关系 | Aurora PostgreSQL 或 DynamoDB | 保存租户、分组、知识库和主体的授权关系；生产权限数据不放本地 JSON |
| 原始文档 | Amazon S3 | 文档、页面图片、模型权重；SSE-KMS、版本控制、生命周期和最小 IAM 权限 |
| 向量检索 | Amazon OpenSearch Service / OpenSearch Serverless 向量集合 | 向量与 `tenant_id`、`scope_id`、`allowed_group_ids` 等 metadata 一起存储并过滤 |
| 会话与异步任务 | DynamoDB、SQS、EventBridge、Step Functions | 会话历史、任务状态、解析/审核任务编排和重试 |
| 密钥 | AWS Secrets Manager + AWS KMS | LLM、数据库、OIDC Client Secret 等敏感配置不进入镜像或 Git |
| 监控审计 | CloudWatch、CloudTrail、AWS WAF Logs | 访问、权限决策、检索范围和管理操作可追踪 |

### 2.2 不要为了“使用 AWS”而引入不合适的服务

- **IAM** 主要用于 AWS 资源和服务间访问，不应直接当作普通业务用户登录系统。服务访问 S3、OpenSearch、DynamoDB 时使用 IAM Role；员工/客户登录应用则使用 Cognito 或客户 OIDC。
- **API Gateway API Key** 可以识别调用方和限制用量，但不等于人类用户认证。若需要用户级权限，仍应使用 JWT、OIDC 或 Lambda Authorizer。
- **S3 Bucket Policy** 可以保护原始文件，但不能替代应用层的知识库授权。用户不能通过 S3 路径自行决定可见范围。
- **OpenSearch 的网络访问策略** 只能保护集群入口，不能自动理解业务上的“用户属于哪个组”。业务权限仍必须进入查询过滤条件。
- **Amazon Verified Permissions** 可以作为集中式 Cedar 策略引擎的候选，但必须先确认目标区域可用性和中国区支持情况；中国区方案不应把它作为唯一前置依赖。

---

## 3. 客户已有用户系统的接入方式

### 3.1 客户已有 OIDC/SAML 系统

优先采用“客户 IdP + AWS 入口服务 + Simple-RAG”的模式：

```text
浏览器
  │
  ▼
ALB OIDC 或 API Gateway JWT Authorizer
  │  验证 issuer、签名、audience、过期时间、scope
  ▼
ECS/Fargate 上的 rag_server
  │  只消费可信 Claims，不直接处理密码
  ├─ Aurora/DynamoDB：读取主体到知识库的授权关系
  ├─ OpenSearch：带权限 metadata 过滤检索
  └─ S3：通过服务端 IAM Role 访问原始文档
```

客户 Token 中可以包含：

```json
{
  "sub": "customer-user-123",
  "iss": "https://idp.example.com",
  "aud": "simple-rag",
  "tenant_id": "customer-a",
  "groups": ["it", "security"],
  "scope": "rag:query rag:document:read"
}
```

不建议直接把客户 Token 中的 `groups` 当成最终授权结果。更稳妥的方式是：

1. 验证 Token 的签名和标准 Claims。
2. 读取 `sub`、`tenant_id`、`groups` 等身份输入。
3. 通过权限映射得到 `allowed_scopes`。
4. 对每个请求和每次检索执行服务端授权。

这样可以允许客户在外部系统管理用户组，同时由 Simple-RAG 管理“哪些组对应哪些知识库”。

### 3.2 使用 ALB OIDC

对于部署在 ECS/Fargate 或 EC2 后面的 Web 应用，可以考虑由 ALB 完成 OIDC 登录跳转和会话处理，后端只接收来自 ALB 的身份信息。

适用场景：

- 主要是浏览器 Web UI。
- 应用已有 HTTP/SSE 接口，不希望在应用内部实现登录流程。
- 客户已有兼容 OIDC 的身份提供商。

边界：

- 必须禁止用户绕过 ALB 直接访问服务任务。
- 后端安全组只允许来自 ALB 的流量。
- 应用不能盲目信任用户自行提交的身份 Header。
- 需要明确 ALB 注入的身份信息如何传递到后端，以及如何审计。

### 3.3 使用 API Gateway JWT Authorizer

对于 API-first 或多个客户端调用的场景，可以将 API 暴露在 API Gateway HTTP API 后面，由 JWT Authorizer 校验外部 OIDC/OAuth2 Token，再将 Claims 传给后端。

适用场景：

- 浏览器、移动端、第三方系统和服务间调用共用 API。
- 需要按路由配置 scope。
- 需要 API Gateway 的限流、访问日志和入口治理。

API Gateway JWT Authorizer 的校验逻辑包括 issuer、签名密钥、audience/client_id、过期时间以及路由 scope。实际使用前仍需在目标 AWS 区域确认 HTTP API、JWT Authorizer 和相关集成能力。

### 3.4 客户只有旧式 SSO 或自定义登录系统

如果客户没有标准 OIDC/SAML，可以按优先级选择：

1. 由客户身份平台增加 OIDC/OAuth2 适配层。
2. 在 AWS 入口使用 Lambda Authorizer，对客户 Token 或内部签名票据进行验证。
3. 将客户已有登录系统放在前置网关，向后端传递受保护的身份上下文。
4. 最后才考虑在 ECS/Fargate 上部署 Keycloak 等自建身份服务。

不建议 Simple-RAG 自己实现密码登录、密码找回、MFA、账号生命周期和复杂 SSO 协议。身份系统越复杂，越应该由客户现有 IdP 或 AWS 托管服务承担。

---

## 4. 轻量级身份模式

“只看一个标记”是可以支持的，但这个标记必须具备可信来源和防伪造机制。

### 4.1 单租户公开/内部模式

如果客户不关心用户信息，也不需要分组权限，可以使用：

```text
tenant_id = default
scope_id  = default
principal_id = anonymous 或 system
```

这种模式适合：

- 内部演示。
- 单团队工具。
- 不包含敏感资料的知识库。
- 只有一个统一访问边界的系统。

它不能提供用户级聊天历史、审计和权限隔离。

### 4.2 可信网关传入一个主体标记

网关完成认证后，只传入最小字段：

```text
principal_id = customer-user-123
```

Simple-RAG 不保存姓名、邮箱和手机号，只使用 `principal_id` 查找授权范围。

这个模式的关键不是字段数量，而是链路可信：

```text
用户 → 认证网关 → 私有 ALB/API Gateway → rag_server
```

必须阻止用户直接访问 `rag_server`，否则用户可以伪造 `X-User-Id`、`X-Group-Id` 等 Header。

### 4.3 API Key / Client Token 模式

对于机器调用，可以将一个 API Key 映射为：

```text
client_id
tenant_id
allowed_scopes
```

API Key 应由 Secrets Manager、客户密钥系统或 API Gateway 管理和轮换。API Gateway 的 API Key 更适合客户端识别、配额和限流；涉及人类用户权限时，应升级为 JWT/OIDC。

### 4.4 轻量模式的最低安全要求

即使只保存一个标记，也建议具备：

- 稳定的 `principal_id`，不能每次随机生成。
- Token 或 Header 的可信来源。
- 过期、吊销或轮换机制。
- 服务端计算 `allowed_scopes`。
- 访问日志中记录主体和知识库范围。
- 禁止使用客户端提交的 `group_id` 直接决定权限。

---

## 5. 分组用户与知识库授权模型

### 5.1 推荐的资源层级

```text
Tenant
  └─ KnowledgeScope（知识库/命名空间）
       ├─ Document
       │    └─ Chunk / Vector
       └─ AccessPolicy
            ├─ Group
            ├─ Principal
            └─ Role
```

建议把 `KnowledgeScope` 作为主要授权边界，而不是直接给每个段落单独做复杂 ACL。

示例：

```text
company-a:it
company-a:security
company-a:shared
company-a:finance
```

用户可以被授权到多个 Scope：

```text
user-123 → company-a:it, company-a:shared
user-456 → company-a:finance
```

### 5.2 授权关系建议

| 主体 | 资源 | 权限 | 示例 |
| --- | --- | --- | --- |
| 用户/服务账号 | KnowledgeScope | `read` | 可以问答和查看来源 |
| 用户/服务账号 | KnowledgeScope | `write` | 可以上传和发起审核 |
| 用户/服务账号 | KnowledgeScope | `manage` | 可以删除、确认版本和管理成员 |
| 用户组 | KnowledgeScope | `read` | 组内所有用户可检索 |
| 租户管理员 | Tenant | `admin` | 管理本租户的组和知识库 |
| 平台管理员 | 全局 | `platform_admin` | 仅限运营人员，需单独审计 |

默认策略应为 **deny by default**：没有明确授权就不能列出、检索、预览、下载或修改文档。

### 5.3 组权限与文档 ACL 的取舍

#### 方案 A：每个组/知识库独立索引

```text
OpenSearch index: tenant_a_it
OpenSearch index: tenant_a_security
```

优点是边界直观、隔离强、误检风险低；缺点是索引数量和运维复杂度随知识库增长。

适合：

- 客户数量少但数据敏感。
- 组边界清晰。
- 不同知识库生命周期和数据保留策略不同。

#### 方案 B：共享索引 + metadata filter

每个向量和文档 metadata 至少包含：

```json
{
  "tenant_id": "company-a",
  "scope_id": "company-a:it",
  "document_id": "doc-001",
  "allowed_group_ids": ["it", "security"],
  "visibility": "group"
}
```

检索条件必须包含授权过滤：

```text
tenant_id = current_tenant
AND scope_id IN allowed_scopes
```

需要更细粒度时，再增加 `allowed_group_ids` 或文档 ACL。共享索引适合知识库数量多、共享文档多的场景，但必须验证 OpenSearch 过滤条件、索引设计和召回效果。

#### 方案 C：命名空间 + 共享文档

这是推荐的折中方案：普通文档进入组级 Scope，共享文档进入 `shared` Scope；用户授权多个 Scope，检索时按 Scope 过滤。

相对于任意文档任意用户 ACL，这种模式更易审计、迁移和排障。

---

## 6. 必须覆盖的权限检查点

权限不能只加在问答接口。以下所有操作都必须带上服务端计算的授权范围：

- 知识库列表和文档列表。
- 文档详情、PDF 预览、页面图片和原始文件下载。
- 文档上传、删除、版本确认和拒绝。
- 预审核、重跑、审核报告和任务进度。
- RAG 检索、冲突检测和来源引用。
- 对话历史、会话列表和会话删除。
- 解析缓存、向量缓存、页面缓存和任务状态。
- 管理员查看成员、组和授权关系。

尤其要避免“先全库召回，再删除无权限结果”的实现。正确方向是把权限范围作为检索条件的一部分：

```text
用户 Token
  → principal_id / tenant_id / groups
  → 授权服务得到 allowed_scopes
  → OpenSearch metadata filter
  → 只对授权文档进行召回
  → LLM 只接收授权上下文
```

否则可能发生：

- 有权限文档没有进入 top_k，导致答案质量下降。
- 通过返回数量、相似度或错误信息推断其他组存在的文档。
- 预审核、缓存或日志把别组内容带入当前会话。

### 6.1 缓存必须包含权限范围

缓存 Key 不能只使用问题和模型，例如：

```text
question + embedding_model + llm_model
```

至少需要考虑：

```text
tenant_id + allowed_scope_hash + question + embedding_config + llm_config + document_version
```

对话历史也不能仅按 `session_id` 存取而不校验 `principal_id` 和 `tenant_id`。

---

## 7. AWS 服务化生产拓扑

```text
[ 用户浏览器 / 客户系统 / API 客户端 ]
                    │
          CloudFront（如目标区域可用）
                    │
       ┌────────────┴────────────┐
       │                         │
[ WAF + ALB OIDC ]       [ API Gateway JWT ]
       │                         │
       └────────────┬────────────┘
                    ▼
          [ ECS/Fargate rag_server ]
             │       │        │
             │       │        ├─► Secrets Manager + KMS
             │       │        ├─► DynamoDB（会话/任务）
             │       │        └─► S3（文档/页面/报告）
             │       │
             │       └──────► Aurora PostgreSQL（租户/组/ACL/业务元数据）
             │
             └──────────────► OpenSearch（向量 + metadata filter）

[ 异步解析/审核 ]：SQS → ECS Worker / Step Functions → S3 + OpenSearch
[ 中国区自建 LLM ]：ECS/Fargate rag_server → 内网 EC2 A10G vLLM
[ 运维审计 ]：CloudWatch + CloudTrail + WAF Logs + 告警
```

### 7.1 入口层

- **ALB**：适合 Web UI、SSE 和 ECS/Fargate；可以承接 OIDC 登录，但需要保护后端任务网络。
- **API Gateway**：适合 API 客户端、JWT 验证、路由 scope、限流和使用量治理。
- **AWS WAF**：保护公开入口，配置基础 Web 攻击规则、请求大小、速率和恶意 IP 控制。
- **CloudFront**：仅在目标分区、合规要求和实际区域支持时使用；中国区对外访问还需结合 ICP 备案、域名和证书策略确认。

不必同时强制使用 ALB 和 API Gateway。第一阶段可以选择：

- 只有 Web：ALB + WAF + ECS。
- 只有 API：API Gateway + WAF + ECS/Lambda 集成。
- Web 和开放 API 并存：ALB 负责 Web，API Gateway 负责 API。

### 7.2 计算与异步任务

- **ECS/Fargate**：运行无 GPU 的 rag_server、API、管理服务和普通解析 Worker。
- **EC2 GPU**：中国区自建 `GLM-4.7-Flash` 时运行 vLLM；也可以承载需要 GPU 的 Docling/MinerU，但应先评估是否与 LLM 共享显存。
- **ECR**：保存 rag_server、Worker 和 vLLM 镜像。
- **SQS**：把文档解析、Embedding、版本对比和审核拆成可重试任务，避免 HTTP 请求长期占用。
- **Step Functions**：当流程包含解析、Embedding、审核、人工确认和失败补偿时，用状态机表达阶段和重试策略。
- **EventBridge**：传递文档入库、审核完成、权限变更和运维事件。

### 7.3 数据层

| 数据 | 推荐服务 | 设计重点 |
| --- | --- | --- |
| 原始 PDF/DOCX | S3 | 以 `tenant_id/scope_id/document_id/version/` 分层；SSE-KMS、版本控制、生命周期和访问日志 |
| 文档元数据与 ACL | Aurora PostgreSQL | 租户、组、Scope、Document、授权关系和版本事务；数据量小也可先使用 DynamoDB |
| 向量与检索 metadata | OpenSearch Service | 向量与 Scope/tenant metadata 同存；检索必须带 metadata filter |
| 会话历史 | DynamoDB | `tenant_id + principal_id + session_id` 作为访问边界；TTL 清理过期会话 |
| 解析/审核任务 | DynamoDB + SQS | 任务状态和幂等键分离；结果较大时存 S3，仅在 DynamoDB 保存索引 |
| 临时缓存 | ElastiCache Redis（可选） | 缓存 Key 包含租户和 Scope；不把 Redis 当作唯一数据源 |

### 7.4 服务身份与密钥

- ECS Task Role、Worker Role 和 GPU 实例 Role 分离，按服务授予最小 S3/OpenSearch/DynamoDB 权限。
- LLM API Key、OIDC Client Secret、数据库凭据放入 Secrets Manager。
- KMS CMK 按数据域或租户隔离时，需要同时设计密钥管理员、使用者和审计者角色。
- 不把 AWS 长期 Access Key、客户 Token 或数据库密码写入镜像、`.env`、Git 或任务日志。

---

## 8. AWS 中国区与全球区的身份方案

### 8.1 全球 AWS 区域

如果客户部署在 Cognito User Pools 可用的区域，优先考虑：

```text
客户外部 IdP（可选）
        ↓ 联邦
Amazon Cognito User Pools
        ↓ JWT
API Gateway JWT Authorizer / ALB OIDC
        ↓
ECS/Fargate rag_server
```

Cognito 负责用户目录和登录时，应用仍应把 `sub`、租户和组映射到自己的 `KnowledgeScope` 授权模型，不应只依赖 Cognito Group 名称完成全部业务权限。

### 8.2 AWS 中国区

AWS 中国区服务和全球分区并不完全相同。AWS 中国官方文档说明，Amazon Cognito 在北京区域可用，但 Cognito User Pools 当前不可用。因此中国区不能把 Cognito User Pools 作为默认前置依赖，必须在实际目标区域和账号中逐项确认服务能力。

推荐优先级：

1. 客户已有 OIDC/SAML 系统 + ALB OIDC 或 API Gateway JWT。
2. 客户身份系统提供一个可信的签名 Token，入口层或 Lambda Authorizer 验证。
3. 如果客户没有可用 IdP，在 ECS/Fargate 部署 Keycloak，并使用 ALB/API Gateway 保护其管理和认证流量。
4. 仅在合规、网络和区域条件允许时，考虑跨区域身份服务；跨境身份和数据传输必须单独评估，不能默认启用。

中国区自建 LLM 路径仍然是：

```text
中国区入口/身份系统
  → ECS/Fargate rag_server
  → OpenSearch/Aurora/DynamoDB/S3
  → 私网 EC2 A10G + vLLM + GLM-4.7-Flash
```

Bedrock 不可用时，不应为了统一架构而把中国区请求强制跨区域调用 LLM；跨区域访问需要结合数据合规、网络、延迟、账号分区和客户合同要求判断。

### 8.3 客户已有用户系统的 AWS 化价值

即使用户系统由客户提供，仍可以销售和交付 AWS 服务：

- API Gateway/ALB/WAF：入口和安全治理。
- ECS/Fargate：运行 RAG 应用和 Worker。
- S3：数据存储和版本管理。
- OpenSearch：向量检索和 metadata 过滤。
- Aurora/DynamoDB：授权关系、业务元数据和会话。
- SQS/Step Functions/EventBridge：异步解析与审核流程。
- KMS/Secrets Manager：密钥和加密控制。
- CloudWatch/CloudTrail：监控、审计和运营。

“客户负责身份认证”不意味着 AWS 价值减少，而是把边界从“管理用户账号”转为“承载安全的知识库应用平台”。

---

## 9. 推荐的产品化分层

### Level 0：单租户快速原型

- 一个 `tenant_id=default`。
- CPU 或 T4 运行解析和 Embedding。
- 外部 LLM 或其他区域 Bedrock。
- 本地文件系统/FAISS。
- 不承诺生产权限隔离。

### Level 1：AWS 单租户生产

- ECS/Fargate + ALB/API Gateway + WAF。
- S3、OpenSearch、Aurora/DynamoDB、Secrets Manager、KMS。
- 外部 OIDC 或 Cognito（目标区域可用时）。
- 单一知识库或少量 Scope。

### Level 2：多租户、多分组

- `tenant_id` 和 `scope_id` 成为所有资源的必填字段。
- OpenSearch metadata filter。
- Aurora/DynamoDB 保存授权关系。
- 会话、缓存、任务、日志全部带租户边界。
- 组级 Scope + shared Scope 作为默认权限模型。

### Level 3：高可用和可运营平台

- SQS/Step Functions 异步化解析与审核。
- ECS 多任务、多 AZ，OpenSearch/Aurora/DynamoDB 按规模扩展。
- GPU 推理节点池化，或中国区 EC2 A10G 与应用层分离。
- 完整 CloudTrail、CloudWatch、WAF、告警、备份和灾备演练。
- 可选 Verified Permissions 或独立策略服务集中管理复杂授权。

---

## 10. 安全与运营要求

### 10.1 默认拒绝与越权防护

- 没有有效身份或授权范围时，不返回文档列表、来源和检索结果。
- 任何 `tenant_id`、`group_id`、`scope_id` 都不能只取自客户端请求参数。
- 所有文档读写、预览、问答、审核和管理接口都执行授权。
- LLM Prompt 只能包含已授权文档片段。
- 管理员权限与普通读写权限分离，并记录操作审计。

### 10.2 数据隔离

最低隔离键建议为：

```text
tenant_id + scope_id + document_id + version
```

以下数据都不能跨租户复用：

- 文档和页面文件。
- 向量和解析缓存。
- 版本审核报告。
- 对话历史。
- 问答结果缓存。
- 任务状态和错误详情。

### 10.3 账号和权限变更

组成员变化后，需要考虑：

- 新权限何时生效。
- 被移除用户的现有会话是否立即失效。
- 已缓存的问答结果是否需要失效。
- 正在运行的审核任务是否继续。
- S3 预签名 URL 是否提前撤销或缩短有效期。
- OpenSearch 查询是否使用最新授权快照。

建议权限变更事件通过 EventBridge 通知缓存和会话服务；在没有完整事件化之前，采用短 Token TTL、短缓存 TTL 和每次请求读取关键授权关系来降低风险。

### 10.4 生产验收

- 未登录用户不能访问受保护 API。
- 用户不能通过修改 `tenant_id`、`group_id`、`scope_id` 访问其他知识库。
- 组 A 的向量检索不会返回组 B 的来源。
- 文档预览、原始下载、审核报告和聊天历史均执行相同授权。
- 权限撤销后，新请求不再返回原知识库内容。
- 任务、缓存、日志和错误响应不泄露其他租户数据。
- AWS 资源访问均使用 IAM Role，长期密钥不落盘。
- 关键访问和管理操作可在 CloudTrail/CloudWatch 中追溯。

---

## 11. 当前不做的实现决定

本文只记录生产设计，不在当前任务中实现以下内容：

- 不修改现有 FastAPI 路由和中间件。
- 不新增用户数据库、OIDC 回调或 JWT 验证代码。
- 不把本地 FAISS 直接改造成 OpenSearch 客户端。
- 不把本地文件缓存直接迁移到 S3/DynamoDB。
- 不部署 Cognito、Keycloak、API Gateway、ALB 或 Verified Permissions。
- 不改变当前 CPU/T4/A10G 的部署分工。

代码改造前需要先确定客户身份模式、AWS 目标分区、租户边界、权限模型、数据分类、SLA 和成本预算。

---

## 12. 建议的决策顺序

1. **先确定 AWS 分区和数据合规边界**：全球区域、中国区，是否允许跨区访问。
2. **再确定身份来源**：客户 OIDC/SAML、Cognito、API Gateway/Lambda Authorizer，还是 Keycloak。
3. **确定授权粒度**：单租户、组级 Scope、文档 ACL，避免一开始直接设计任意 ACL。
4. **确定向量隔离策略**：独立索引、共享索引 metadata filter，还是 Scope 命名空间。
5. **确定数据服务**：S3 + OpenSearch + Aurora/DynamoDB 的职责边界。
6. **最后设计异步化和高可用**：SQS、Step Functions、ECS 多 AZ、GPU 推理池。

身份系统不是所有客户都要由我们托管；但入口、数据、检索、审计和运维平台尽量使用 AWS，可以同时满足客户现有用户体系和 AWS 云服务交付目标。

---

## 13. 参考资料

- [Amazon Cognito in Amazon Web Services China](https://docs.amazonaws.cn/en_us/aws/latest/userguide/cognito.html)：中国区 Cognito 的区域差异，包括北京区域 User Pools 当前不可用的说明。
- [Control access to HTTP APIs with JWT authorizers in API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html)：API Gateway HTTP API 的 JWT 校验、Claims 和路由 scope。
- [Authenticate users using an Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/listener-authenticate-users.html)：ALB 与 OIDC 用户认证的官方说明。
- [Working with vector search collections](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vector-search.html)：OpenSearch 向量集合、metadata 和过滤能力说明。
- [AWS 中国区 API Gateway](https://docs.amazonaws.cn/en_us/aws/latest/userguide/api-gateway.html)：中国分区 API Gateway 服务入口，具体功能需按目标区域确认。
- [AWS 中国区 OpenSearch Service](https://docs.amazonaws.cn/en_us/opensearch-service/latest/developerguide/setting-up.html)：中国区 OpenSearch Service 文档入口。

> AWS 服务能力、配额、价格和区域支持会变化，本文的中国区判断应在实际项目验收日期依据目标账号和官方区域文档重新确认。本文对 AWS 官方资料的服务能力说明均为概括性转述，未复制原文。
