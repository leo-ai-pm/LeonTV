# 经验风险文本执行 · D26

在原 `ExecutionRuntime` 中显式选择 `empirical_risk_v1`，复用原任务、报价、授权、账户、worker、角色与回执链路。它用于供应商用量硬上界尚未证实时的小额真实文字试跑；默认应用仍不启用真实执行。四角色规则沿用[统一角色执行](./PLANNER-ROLE-EXECUTION-API.md)，最终结果见[本轮验收](./PLANNER-EMPIRICAL-EXECUTION-VALIDATION.md)。

## 服务端范围

`EmpiricalExecutionPolicy` 独立于原 `ExecutionPolicy`，scope 为 `frozen_brief_and_bounded_roles_empirical_risk_to_named_provider`。首次报价冻结 accounting kind、来源、环境、工作流与父预算锚点。旧任务、旧授权和浏览器待确认请求不能自动升级；旧正式策略仍要求原可信输入上界与回执校验。

本模式不声明 `billing_bound_verified`，不提供虚构的 `TrustedInputBounder`。`max_tokens`／`max_completion_tokens` 是请求配置；收到的实际用量另行观察。真实环境为 `empirical_live`，合成验收为 `synthetic_empirical_test`，历史环境从记录本身读取。

`BudgetAnchor` 由服务端冻结原授权摘要、父账本与范围分配摘要、历史风险和次数、新范围额度、累计账户上限。历史风险只加入一次；新授权、角色切换和进程重启不清零。每次新调用前再次检查父锚点和累计额度。父账本整体预留范围、原生执行账本记录范围内各次意图，不声称两个文件之间存在逐次事务原子性。

## 一次调用如何前进

1. 原 `/api/planner/tasks/{id}/execution/quotes` 完整披露四角色、指定供应商、来源外发范围与经验风险。授权仍通过原 `grants`；授权本身不开始执行，原 `actions` 的 `resume` 单独启动。
2. 原生 journal 原子保存意图、调用数与单次 1 元风险分配，再走既有 detached collector。总数最多 6，首审、改稿和复核各最多 1 次；首审接受时跳过改稿，不制造问题凑齐角色。
3. 完整回执保存后先进入 `PLATFORM_FEE_REQUIRED`。此时不处理工具或角色正文，不提交下一调用。客户端只能观察或暂停，不能提交费用金额或跳过核费。
4. 服务端 `observe_fee(task_id, operation_id, evidence)` 核对原始回执、wire、模型、响应 ID、基础用量、平台行和导出摘要，并要求独立的 `fee_verifier` 返回严格 true。没有对外 HTTP 写账路由。
5. 核费后，在原授权仍有效且未暂停的情况下，原 worker 自动处理这份已收到的正文并决定交接。暂停或撤销后不因迟到的费证自动恢复；更换授权本身也不启动。明确续行复用已收到的回执，不重新请求模型。

当前费用证据支持基础用量完整的 `complete`／`truncated` 回执。没有原始回执、模型不匹配或基础用量不完整时，不能凭平台行补齐正文或解除未知；这类外部费用旁证与原生已匹配费用分开保存。

## 三种金额必须分开

| 字段 | 含义 |
| --- | --- |
| `risk_cny`／`risk_allocated_cny` | 分配的风险持续保留；核费不释放、不转换为实际支出 |
| `local_estimate_cny` | 按回执 token 和冻结费率计算的估价，不是平台扣费 |
| `platform_observed_cny` | 已匹配平台用量日志中的费用观察，不是最终供应商账单 |

未知金额为 null；任一操作未知时，其相关合计也为 null。没有操作时合计为明确的 0。旧兼容金额字段不能作为经验风险页面的实际费用。`provider_bill_confirmed` 始终为 false。

同一平台行不能分配给两个操作。同一证据完全相同的重放幂等，变更后的重放冲突。费用事实即使超过预留也必须先保存，随后阻止该账户所有后继调用；不能截断费用、写成零或借新任务绕过。估价超额与平台观察超额均会停止，但页面区分其依据。

平台观察适配器须独立确认标识的稳定性：本次土豆日志的 `id` 是筛选序号，不能作为跨导出唯一键；实际采用平台 `request_id` 的摘要，并检查其跨操作唯一性。平台请求标识、HTTP响应ID和模型body响应ID分别保留，不能互相冒充。

## 停止与边界

未知／截断不执行正文，不自动重试；原风险和调用数保留。截断回执可记录精确匹配的费用，但不能因此成为完整输出。账务证据有效、语义内容非法时仍记录费用并停止。当前 scope 不包含图像、视频、音频、模型替换或视频通道重试。

独立 Reviewer 是独立上下文和审阅职责；使用同一基础模型不等于已校准的独立质量评估。模型的文字接受、媒体质量和人的最终验收仍是不同结论。默认生产 bootstrap 与默认回执采集继续关闭；一次私有显式启用的真实试跑不等于全面上线。
