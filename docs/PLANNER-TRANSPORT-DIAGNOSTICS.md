# Planner 私有传输诊断 · D12

状态：实现及后端隔离验收完成，源码已冻结，待主窗口独立验收。

## 范围与非结论

只补正式 `ControlledTransport → ExecutionRuntime → ExecutionStore` 的错误分类信息。MockTransport 已复现 ConnectTimeout 和 ReadTimeout 在实际执行链中均变成无分类的 `RESPONSE_UNCONFIRMED`。这不是对真实 M01-02 网络根因、供应商是否受理或计费的判断。

不改 strict normalizer、策略或 quote/permit hash、请求 wire、账务/hold/调用计数、生产禁用状态、D09 对外响应及前端。

## 最小合同

`ResponseUnconfirmed` 只携带固定白名单 `code` / `phase`，不得携带原始异常文本、异常类名、traceback、URL、请求/响应内容、凭证或来源剧本。未知异常收敛为固定通用分类；异常链不对外传播。

新增可选私有字段 `operation.transport_diagnostic`，对象严格只有 `code` 和 `phase`。只在已持久化 intent 后且传输未确认时保存；正常操作不添加空字段。有效配对：

| code | phase | 观测范围 |
| --- | --- | --- |
| `PERMIT_REJECTED` | `preflight` | sink 输入/许可绑定校验未通过或校验无法完成，未构造网络客户端 |
| `CONNECT_TIMEOUT` | `connect` | HTTPX ConnectTimeout 类型，仅记录客户端分类 |
| `READ_TIMEOUT` | `read` | HTTPX ReadTimeout 类型 |
| `WRITE_TIMEOUT` | `write` | HTTPX WriteTimeout 类型 |
| `POOL_TIMEOUT` | `pool` | HTTPX PoolTimeout 类型 |
| `TRANSPORT_ERROR` | `unknown` | 其他异常或无法可靠分类的传输失败 |
| `OVERALL_DEADLINE` | `deadline` | 提交前许可已过期、线程/观察者达到整体绝对 deadline；不宣称取消成功 |
| `RECEIPT_TOO_LARGE` | `receipt` | 原有 1 MiB 原始回执 envelope 上限触发 |

HTTPX 分类不证明网络真实阶段，不证明供应商未受理或免费。若绝对 deadline 已到，优先 `OVERALL_DEADLINE`，不使用过期后的错误/回执重写诊断。失败清理异常不得抹掉较早的固定分类。

Runtime 仍使用 `needs_reconciliation`（已停止则保持 stopped）与公开 `RESPONSE_UNCONFIRMED`；原 hold、调用次数及操作键保留，不自动重发或释放预留。诊断不进入公共 operation/事件/策略投影，也不成为重试或计费依据。

Store 对存在的字段检查固定配对、无额外键以及未确认操作状态。旧 journal 无字段继续原样读取，不补写、不迁移、不猜测历史分类。该字段不参加原 policy/wire/permit 摘要。

## 验收结果

- 红测试：实际 FastAPI quote/grant/resume、实际持久化/锁/worker、仅 HTTP 为合成 MockTransport。ConnectTimeout / ReadTimeout 均稳定失败于缺少私有字段；原账务断言通过。
- 绿测试覆盖四种阶段超时、未知异常、sink 拒绝且 0 HTTP、绝对 deadline 与迟到返回、超大 envelope、异常文本/URL/凭证不入新增诊断、公共响应不新增字段。
- 重开 store/runtime 后诊断与未知 hold 保留；reconcile/resume/新 grant 不自动重发；旧 fixture 字节不变；无效私有诊断拒绝写入。
- 原 normalizer、策略与 wire 源码机械比对；隔离 targeted/full tests、Ruff、diff check、同一 manifest；全程真实 HTTP/付费/出站为 0。

- D12 新增回归 **53 项**；最初两项红测试 2 failed / 0.87s，修复后原测试转绿。
- 最终执行链定向 **222 passed / 26.85s**；隔离全量 **577 passed / 49.75s**；无 failure/error/skip。该全量包含主窗口同轮新增的 3 项 Planner 工具提示合同回归。
- 定向、全量及当前源码 **204 文件**清单完全一致。39 项机械保护检查通过：Chat wire 构造、strict normalizer、Responses/policy/protocol 全文件、公开安全投影/原账务方法和 sink 许可校验逻辑保持原样；只改变传输错误分类及其私有持久化。
- Ruff（本窗口源码与测试，包含同轮主窗口 Planner 改动）、限定路径 `git diff --check` 均通过。
- 两轮验证均由隔离源码副本执行，排除配置、凭证和私有运行状态；Python 父/子进程 socket 出站被禁止。**网络出站 0、真实模型 HTTP 0、付费 0**。没有启停共享服务、安装、stage、commit 或 push。
- 证据位于 `docs/evidence/LTV-PLANNER-RECOVERY-20261022/`：`backend-result.json`、`backend-*-isolation.json`、JUnit/日志、`backend-unchanged-guards.json`、Ruff 与 diff 记录。

同轮主窗口另行补全了新任务 SYSTEM 的两个工具输入 schema；这属于主窗口的提示合同修改，已包含在最终验证清单。旧任务/既存执行上下文不回填；本诊断补丁自身未改变 wire 构造或协议校验。

真实 M01-02 的网络根因仍未知。此改动不会补写它的历史诊断、释放原预留、确认免费、自动恢复或宣告计划/内容质量通过。HTTPX 分类只提供后续已授权请求的有限客户端观测。
