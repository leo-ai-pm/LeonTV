# 服务端执行控制与平台费用收件箱 · D27

在同一个 `ExecutionRuntime`、原 Planner API、报价／授权／意图／回执 journal 中增加正式服务端装配层。D26 的专用启动和费用桥不再是新范围接入的必需实现。生产默认关闭；本轮没有新建付费范围或发出模型请求。[验收记录](./EXECUTION-CONTROL-VALIDATION.md)。

## 配置与启动

唯一入口为服务端环境变量 `LEONTV_EXECUTION_CONTROL_FILE`，值为独立私有 JSON 文件的规范绝对路径。未设置或为空时，仍使用原默认存储与关闭的执行器。此文件不属于浏览器 `/api/config`，不含 API key。密钥只在明确启用且完整验证通过后，从既有服务端供应商配置读取。

| 模式 | 行为 |
| --- | --- |
| 未配置 | 原默认执行器，真实执行关闭 |
| `observe_only` | 只读既有 Brief／Planner／Execution；不恢复 worker、不读密钥、不写原 journal，变更请求返回 `CONTROL_READ_ONLY` |
| `enabled` | 配置验证通过后可以使用原报价与授权接口；仍需单独 `resume`，加载配置本身不生成 |

配置每进程加载一次。修改文件不会热启用；执行前的原始字节、代码与来源检查会阻止漂移后的新请求。服务重启后，旧任务的迟到费用可以入账，但不会自动续行；需要本进程的新显式 `resume`。重放旧的幂等操作键不算重新启动。新任务显式启动后，核费与角色交接沿原 worker 自动前进。

`observer_enabled` 只控制本地收件箱投递线程。线程在正式执行路由完成实际静态挂载隔离检查后启动；查看回执诊断不会启动它。观察模式永不启动线程。没有平台导出时不创建空费用收件箱。

## 控制文件合同

| 字段 | 冻结内容 |
| --- | --- |
| `schema_version`, `mode` | 版本 1；观察或显式启用 |
| `roots` | `creator`、`planner`、`execution`、`fees` 四个互不重叠的私有目录 |
| `policy` | 完整 `EmpiricalExecutionPolicy.frozen()`，包含 `policy_hash` |
| `budget_anchor` | 完整 `BudgetAnchor.frozen()`，包含 `parent_anchor_hash` |
| `authority` | 原授权路径及原始 SHA256、父账本路径、原范围分配键 |
| `code_manifest` | 后端相对路径到 SHA256，必须覆盖执行、规划及装配的生产代码闭包 |
| `admission` | 单个既有 normal task 的 ID、Planner revision、snapshot、Brief ID／revision、source hash |
| `fee_source` | 账户命名空间、平台 token 名、平台 group、固定 `quota_per_cny=500000` |
| `observer_enabled` | 明确布尔值，是否投递已导入的费用记录 |

启用时同时验证原 100 元授权、父账本原始摘要、分配记录摘要、预留状态、累计历史风险与次数、指定供应商／模型／工作流／输出参数、来源及完整代码。历史范围按其 `max_new_calls` 保守累计，不能把一个多次范围当作一次，或通过换授权归零。

新启用分配的 request 必须明确包含 `scope_status="open"`、完整 `admission`、`chat_output_parameter`、`scope_risk_cny` 及原预算和期初风险。D26 的旧分配没有这些新准入域，只能用于观察，不能因剩余两次额度而重新启用。观察模式允许旧代码和过期策略用于历史读取，不放宽账本与原授权绑定。

文件须为当前用户拥有、权限 0600、有大小上限的普通单链接文件；路径不经过符号链接，控制文件／授权／父账本不得放入数据根或公开静态目录。实际 FastAPI mount 在 Brief、Planner、Execution 依赖处重复检查，不能借额外挂载公开费用文件或授权证明。错误仅返回固定代码与消息，不回显路径、正文或密钥。

## 本地操作入口

在后端目录运行；这些命令不会构造 live runtime、创建报价／授权或调用模型。

```sh
python -m core.execution.operator status
python -m core.execution.operator status --control /absolute/private/control.json
python -m core.execution.operator install /absolute/private/bundle.json --to /absolute/private/new-control.json
python -m core.execution.operator fees-import /absolute/private/native-export.json --control /absolute/private/control.json
```

`install` 校验并发布到新的私有位置，不覆盖已有控制文件。启用还需要由部署进程设置上述环境变量并重启；CLI 不修改共享服务环境。`status` 只显示模式、模型、导入／待确认／已应用计数及 `generation_posts=0`。

本版本接收土豆 `/api/log/self` 的原始 JSON 导出，不提供浏览器填写费用金额的接口，也不自动登录平台或下载账单。导出须为完整、至多 100 行的指定分页形状；`total` 必须等于行数。只匹配消费记录 `type=2`，原始导出按 SHA256 原字节保存。账户日志的取得仍是操作端工作。

## 匹配、持久化与恢复

费用适配器独立重建原 operation／policy／context／wire／permit／raw receipt 绑定，再以冻结 token、group、模型、基础输入输出用量和意图至回执时间窗唯一匹配。没有候选则等待；多候选则保留歧义，不按顺序猜测。平台 quota 按固定换算得到最多六位小数金额；HTTP 请求 ID、模型响应 ID 和平台 request ID 分别保留。

平台日志行 `id` 是筛选序号，不作跨导出的唯一键。使用 `tudou-request-sha256:` 加平台 `request_id` 的 UTF-8 SHA256，并在冻结账户来源下保证跨操作唯一。重复导出幂等；同一 request 的财务字段冲突拒绝。所有候选参与唯一性判断，不剔除已占用行来制造唯一匹配。

收件箱先保存原始导出及不可变 prepared 费证，再交给原 `observe_fee` 验证并保存，最后 ACK。若在原 journal 提交后、ACK 前崩溃，重启仅补 ACK，不重调模型或重复处理正文。已冻结的导出摘要与观察时间不因后续重叠导出改变。

历史序号别名仅可通过已经原生接受的完整费证及其原始导出注册。该兼容入口不能为新操作创建序号身份，也不重写旧 native journal。本轮用 D26 全部四条费用验证了此路径。

有效超额费用仍须先记事实，再由原账务门禁停止后继请求。风险预占、估价、平台费用观察与最终账单保持不同语义；本适配器不把平台日志称作最终供应商账单，也不解除缺失回执的 unknown。

## 当前产品边界

已完成服务端配置、准入、同一引擎装配、费用导入与恢复；尚无操作员配置 UI、自动平台日志拉取或跨供应商费用插件。没有开启通用真实生产。素材、视觉审核、视频、音频工具及最终人的版本验收仍待接入；真实内容改稿／复核等待真实不合格任务验证。
