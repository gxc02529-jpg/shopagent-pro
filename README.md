# ShopAgent Pro

ShopAgent Pro 是一个面向企业落地的全渠道电商智能客服中台参考实现。项目以商品、推荐、订单和售后四个领域 Agent 为核心，打通用户咨询、工具执行、结果返回、用户反馈、人工审核、知识发布和后续复用的完整闭环。

> **数据与隐私声明：本公开仓库已完成脱敏处理。** 仓库不包含真实用户资料、生产凭据或企业业务数据；演示用户、商品、订单、工单、手机号和邮箱均为虚构测试数据，其中手机号、邮箱、地址等样例仅用于验证系统的 PII 脱敏能力。

```text
REST / SSE 网关
      ↓
多 Agent 编排器 ──A2A──> 商品 / 推荐 / 订单 / 售后 Agent
                              ↓
                            MCP
                              ↓
                 商品 / 订单 / 知识 / 工单工具
                              ↓
                  PIM / OMS / MySQL / RAG 适配层
```

开发模式支持单进程和 Mock 数据直接运行；生产配置强制启用 A2A、MCP、Redis、MySQL、强密钥和 HTTPS 公网地址。

## 为什么采用 Multi-Agent、MCP 和 A2A

三者负责不同层次的解耦：

| 机制 | 职责 | 项目价值 |
|---|---|---|
| Multi-Agent | 划分业务决策边界 | 商品、推荐、订单和售后独立演进、授权、扩容与降级，避免巨型 Prompt 和权限扩散 |
| MCP | 规范 Agent 与工具之间的调用 | 统一工具发现、参数校验、权限、超时、熔断、错误语义和指标 |
| A2A | 规范编排器与 Agent 服务之间的协作 | 提供 Agent Card、任务提交、状态查询、结果产物、服务身份和调用方隔离 |

MCP 不承担 Agent 协作，A2A 不替代细粒度工具协议。生产链路会真实跨越两个 HTTP 协议边界，并由自动化测试验证调用次数和协议指标。

## 主要能力

- 商品搜索、详情、库存和个性化推荐
- 用户订单归属校验、物流和退款状态查询
- 售后政策检索、工单幂等创建和状态追踪
- 会话记忆、意图路由、低置信度与异常转人工
- 用户评价绑定原始交互，差评自动生成知识候选
- 知识候选人工审核、并发状态控制、发布失败补偿与重试
- 审核知识进入检索并被后续领域 Agent 复用
- Prometheus 指标、健康检查、就绪检查、审计和全链路 trace

## Agent 协作质量评估

仓库提供 `evaluation/agent_cases.jsonl` 标注集和可执行评测器。当前集合包含商品搜索、商品详情、库存、订单、售后、推荐、问候和越界问题共 56 条，指标定义如下：

| 指标 | 计算口径 |
|---|---|
| 意图识别准确率 | 预测意图与人工标签一致的用例数 / 全部用例数 |
| Agent 路由准确率 | 实际领域 Agent 与标注 Agent 一致的用例数 / 应路由用例数 |
| 自动任务完成率 | 意图、Agent、必需工具、非转人工和无错误同时满足 / 可自动化用例数 |
| 人工转接率 | `need_human=true` 的用例数 / 全部用例数 |
| 转接召回率 | 正确转接的越界或低置信度用例数 / 应转接用例数 |
| 工具成功率 | MCP/本地工具成功调用数 / 工具总调用数 |

2026-09-24 本地确定性回归结果：

| 数据范围 | 意图准确率 | 路由准确率 | 自动任务完成率 | 人工转接率 | 转接召回率 | 工具成功率 |
|---|---:|---:|---:|---:|---:|---:|
| 56 条脱敏 Mock 用例 | 98.21% | 97.62% | 95.92% | 14.29% | 100% | 100% |

执行方式：

```powershell
python -m shopagent.evaluation evaluate --dataset evaluation/agent_cases.jsonl --threshold 0.80
```

这是代码回归基线，不是生产业务效果。真实上线前必须用商家脱敏历史会话构建独立测试集，并按渠道、意图、用户表达方式和大促/平峰分别报告指标；不得用这组 Mock 数字直接宣称达到 85% 生产自动化率。

### 置信度路由与阈值依据

路由实现位于 `src/shopagent/orchestration/service.py`：先调用可插拔 `LLMProvider.classify()`，调用失败或无结果时回落到规则意图引擎；满足 `intent != UNKNOWN` 且 `confidence >= threshold` 才允许自动路由，否则设置 `need_human=true`。响应会返回 `routing_decision` 和 `routing_threshold`，便于审计本次路由。

当前规则置信度不是模型概率，含义是可审计的证据等级：

| 证据 | 置信度 |
|---|---:|
| 售后工单号精确匹配 | 0.98 |
| 退款进度固定表达 | 0.96 |
| 关键词并提取到 SKU/订单号 | 0.96 |
| 仅提取到 SKU | 0.90 |
| 领域关键词匹配 | 0.86 |
| 无规则命中 | 0.30 |

默认阈值由标注集扫描得出，不是拍脑袋常量。校准器将“错误自动受理”的成本设为 5，将“本可自动处理却转人工”的成本设为 1；在最低加权成本候选中，再与最弱正确自动化样本保留 0.05 安全间隔，因此选择 `0.80`。阈值 `0.50—0.85` 在当前集合上的自动路由判定相同，选择 0.80 是为后续分数漂移预留余量。

```powershell
python -m shopagent.evaluation calibrate --dataset evaluation/agent_cases.jsonl
```

切换大模型、修改分类 Prompt、变更意图标签或渠道流量占比后必须重新校准；生产环境还应按周监测分数分布漂移、错误自动受理率和人工转接率。

## 并发容量基线

仓库提供 `benchmarks/http_benchmark.py`。基线通过真实 HTTP 请求调用 `POST /api/v1/chat`，并启用远程 A2A Agent 与远程 MCP 工具传输；脚本同时校验 HTTP 成功和业务任务是否降级，避免把“HTTP 200 但已转人工”计作成功。

2026-09-24 实测环境：Windows 11、Python 3.12.4、单 Uvicorn 进程、Mock 业务数据、内存存储、A2A+MCP 两层 HTTP 边界；每档 500 请求，混合商品、库存、订单、退款、售后、推荐和问候场景。

| 并发数 | 吞吐量 | P50 | P95 | HTTP 成功率 | 任务成功率 |
|---:|---:|---:|---:|---:|---:|
| 20 | 89.02 RPS | 169.87ms | 311.31ms | 100% | 100% |
| 50 | 105.61 RPS | 450.10ms | 851.86ms | 100% | 100% |
| 100 | 96.27 RPS | 715.46ms | 2,813.31ms | 99.6% | 99.6% |
| 200 | 78.16 RPS | 1,818.67ms | 5,114.48ms | 100% | 98.6% |

结论：当前单进程参考实现的稳定拐点约为 50 并发、105 RPS；100 并发后延迟和错误开始明显上升，**当前数据不支持“单节点 200 QPS”声明**。500 QPS 只能作为集群验收目标，不能由本地 Mock 基线外推为承诺。正式交付必须在生产同构环境使用 Redis、MySQL、真实模型端点和独立部署的 Agent/MCP 服务重新测试。

```powershell
$env:SHOPAGENT_AGENT_TRANSPORT = "a2a"
$env:SHOPAGENT_TOOL_TRANSPORT = "mcp"
$env:SHOPAGENT_A2A_BASE_URL = "http://127.0.0.1:8080/a2a"
$env:SHOPAGENT_MCP_URL = "http://127.0.0.1:8080/mcp"
$env:SHOPAGENT_LOG_LEVEL = "WARNING"
shopagent-api

# 另开终端执行
python benchmarks/http_benchmark.py `
  --base-url http://127.0.0.1:8080 `
  --concurrency 20,50,100,200 `
  --requests 500 `
  --profile full-protocol-a2a-mcp-local-mock
```

企业验收建议至少包含：30 分钟阶梯压测、2 小时稳态压测、Redis/MySQL/模型超时故障注入，以及大促目标流量 1.5 倍的突发压测；门禁建议设置为 HTTP 成功率 ≥99.9%、自动查询任务成功率 ≥99%、目标负载 P95 ≤1s，写操作另设幂等性和数据一致性门禁。

## 安全与工程能力

- 用户、管理员和服务身份采用签名短期令牌
- 用户令牌与 `user_id` 绑定，防止跨用户对象访问
- MCP 工具身份和 A2A 调用方身份由已验证 Token 推导
- Redis 会话隔离、滑动 TTL、无碰撞键和分布式会话锁
- MySQL 持久化交互、反馈、知识、订单、工单和 A2A Task
- PII 脱敏、Prompt Injection 基础护栏和写操作幂等
- 非 root、只读容器与不包含明文 Secret 的 Kubernetes 清单
- Python 3.11/3.12、Redis、MySQL 和 Docker 的 GitHub Actions 验证

## 快速启动

需要 Python 3.11 或 3.12：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,enterprise,quality]"
shopagent-api
```

启动后可以访问：

- 用户演示：<http://localhost:8080/>
- 运营审核：<http://localhost:8080/admin>
- OpenAPI：<http://localhost:8080/docs>
- Prometheus 指标：<http://localhost:8080/metrics>
- 健康检查：<http://localhost:8080/health>
- 就绪检查：<http://localhost:8080/ready>

## Docker 企业模式

```powershell
Copy-Item .env.enterprise.example .env.enterprise
# 替换所有 CHANGE_ME 占位值
docker compose --env-file .env.enterprise config --quiet
docker compose --env-file .env.enterprise up -d --build --wait
```

企业模式配置：

| 配置 | 开发默认 | 企业生产 |
|---|---|---|
| `SHOPAGENT_AGENT_TRANSPORT` | `local` | `a2a` |
| `SHOPAGENT_TOOL_TRANSPORT` | `local` | `mcp` |
| `SHOPAGENT_MEMORY_BACKEND` | `memory` | `redis` |
| `SHOPAGENT_OPERATIONS_BACKEND` | `memory` | `mysql` |

生产启动会拒绝本地传输、内存持久化、通配 CORS、非 HTTPS 公网地址和弱密钥。内置 HMAC Token 适合单一企业信任域；跨组织部署应替换为 OIDC/JWKS，并为服务间通信启用 mTLS。

## 测试说明

安装测试依赖并执行完整质量门禁：

```powershell
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest --cov=shopagent --cov-report=term-missing tests
python -m build
```

没有 Redis/MySQL 时，本地结果为 `92 passed, 2 skipped`。两项跳过测试是 live Redis/MySQL 集成验证。

执行真实中间件测试：

```powershell
$env:SHOPAGENT_TEST_REDIS_URL = "redis://localhost:6379/15"
$env:SHOPAGENT_TEST_DATABASE_URL = "mysql+pymysql://shopagent:password@127.0.0.1:3306/shopagent_test"
python -m pytest tests/test_integration_backends.py -m integration -v
```

GitHub Actions 会自动启动 Redis 和 MySQL，分别在 Python 3.11、3.12 下执行全部测试，同时构建 Docker 镜像并验证包版本。

关键测试：

- `tests/test_transport_chain.py`：验证网关 → A2A → Agent → MCP → Tool 的真实 HTTP 主链路
- `tests/test_closed_loop.py`：验证差评知识审核发布后能影响后续 Agent 回答
- `tests/test_idempotency_and_security.py`：验证幂等、并发和用户越权隔离
- `tests/test_api_e2e_enterprise.py`：验证反馈、审核、审计和运营看板闭环
- `tests/test_integration_backends.py`：验证真实 Redis/MySQL 读写生命周期
- `tests/test_evaluation.py`：锁定 Agent 质量门禁和 0.80 阈值校准结果

## 代码结构

```text
src/shopagent/
  agents/          # 商品、推荐、订单和售后领域 Agent
  orchestration/   # 意图识别、路由、聚合、降级和记忆更新
  protocols/       # MCP 与 A2A 协议适配
  tools/           # 业务工具、授权、超时、熔断和指标
  ports/           # 存储、检索、LLM 与业务系统抽象
  adapters/        # Mock、Redis、MySQL 和远程协议实现
  operations/      # 反馈、知识审核、审计和运营数据
  rag/             # 可替换知识检索服务
  security/        # 身份令牌、护栏和脱敏
tests/             # 单元、端到端、协议与真实中间件测试
```

## 项目边界

仓库提供可运行、可测试、可拆分部署的参考实现，不包含商家的真实 PIM/OMS/WMS、人工坐席系统、企业 SSO、云密钥管理和生产监控平台。正式上线需要实现对应 Port 的企业适配器，并完成容量压测、数据合规、备份恢复、灾备演练和安全测试。

本项目采用专有许可证，未经授权不得复制或分发。
