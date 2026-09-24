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

没有 Redis/MySQL 时，本地结果为 `90 passed, 2 skipped`，覆盖率为 83.21%，高于 80% 门禁。两项跳过测试是 live Redis/MySQL 集成验证。

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
