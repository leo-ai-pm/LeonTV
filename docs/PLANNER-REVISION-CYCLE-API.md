# 有限改稿与独立复核 · D24 v1

本轮复用原Planner API、任务、worker、执行锁、动作幂等键及pending intent／receipt；只有显式新离线场景使用合成响应。normal、原五场景及D23首审场景的字段、累计上限和历史摘要均不改变。真实付费调用上限0，生产关闭，真实质量恒为not_reviewed。

## 创建与边界

新增scenario：`revision_accepted`、`revision_rejected`、`revision_needs_input`、`revision_invalid`、`revision_response_unknown`、`recheck_unknown`、`recheck_response_unknown`、`recheck_invalid`。沿用offline-tasks创建，ready后一次resume自动执行，角色之间没有新增启动按钮。

默认合成路径为Planner读取／提交v1→独立Reviewer首拒→Repairer一次有限改稿→Rechecker独立复核v2。总意图上限仍6；Reviewer、Repairer、Rechecker各最多1次，Planner及三角色之和必须等于原step_count。常规完整路径5次。repair_count仍是原Planner协议错误次数，不借它重置或混入内容返工计数。

首审实际accepted时直接接受v1，不制造缺陷或v2；unknown或无法获得可改范围时blocked／needs_input，不能补造源事实。只有D18 build_repair_task结果为content_repair_ready时才委派Repairer。它只返回严格JSON `{binding,plan}`，binding原样回显本次repair_binding，plan为裸ShotPlan；不执行工具。校验binding后，validate_repair_candidate验证确切来源、原报告、提案、计划成员／顺序和允许字段的实际变更后，原plans追加v2。无变更、越权或结构无效都blocked／REPAIR_INVALID，不保存v2或继续复核。

Rechecker使用全新system＋user，完整来源、v2、原规则、v1报告／提案和旧问题，不继承Planner或Repairer聊天、推理、自评。当前复核必须绑定v2，不用v1首审结论给v2放行。复核再拒绝、缺证或非法均停止，不进行第二次改稿。

## 公共投影

仅新增场景追加`revision_cycle`，不含D23 `collaboration`。公共summary字段：

```
{
 schema_version: 1, evidence_kind: "synthetic",
 roles: ["planner", "reviewer", "repairer", "rechecker"],
 status: "awaiting_plan" | "review_ready" | "review_running" |
         "review_response_unknown" | "review_invalid" | "needs_input" |
         "repair_ready" | "repair_running" | "repair_invalid" |
         "repair_response_unknown" | "recheck_ready" | "recheck_running" |
         "recheck_response_unknown" | "recheck_invalid" |
         "accepted" | "rejected" | "unknown",
 planner_call_count, reviewer_call_count, repairer_call_count, rechecker_call_count,
 max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1,
 max_rechecker_calls: 1, paid_call_limit: 0,
 first_review_binding: null | <D18九字段binding，v1>,
 first_report_hash: null | sha256,
 repair_binding: null | <下述十字段>,
 recheck_binding: null | <D18九字段binding，v2>,
 recheck_report_hash: null | sha256,
 accepted_plan_version: null | 1 | 2
}
```

repair_binding的精确字段为`task_id,run_id,intent_id,source_snapshot_hash,parent_plan_version,parent_plan_hash,review_id,report_hash,repair_task_hash,next_plan_version`。服务端在Repairer intent生成；task_id/run_id为本任务，intent_id为该operation，parent为v1，review/report为首审，repair_task_hash为完整提案digest，next_plan_version为2。回执必须原样回显，不接受其他意图／报告／范围的候选。

detail追加四字段：`parent_plan`、`first_report`、`repair_task`、`recheck_report`（初始null）。parent_plan是已接收v1，用于显示与核验有限范围，不含来源正文；首审已有v1时可提供。原task.plan永远是当前最新已接收版本；plan_versions保留v1/v2元数据。列表不含这四项、上下文或原始响应。

first_review_binding及recheck_binding的run_id/task_id为本任务ID，intent_id为各自operation ID；首次v1和复核v2来源／规则相同，review_id／intent_id各不相同。accepted_plan_version只在当前有效合成结论accepted时为该版本，否则null；不映射为真实quality accepted或执行许可。

## 复核纯合同

新增`core/planner/recheck.py`；D18 review.py／repair.py保持原合同与字节。

`build_recheck_input(source, parent_plan, first_binding, first_report, repair_task, candidate, binding)`严格重建D18首审及repair校验后，构造：

```
{contract_version:"D24-recheck-contract-v1", binding,
 source:<D18完整来源对象>, plan:<D18当前candidate对象>, rules:<原D18规则>,
 parent:{plan:<v1 ShotPlan>, report:<确切D18首审报告>,
         report_hash:<digest>, repair_task:<确切D18提案>},
 prior_findings:<首审非accepted规则的完整findings>}
```

binding必须继承task/run/source/rules、version=parent+1、hash=candidate，review_id/intent_id必须新建。`checked_recheck_payload`重建并核对整个输入；`recheck_messages(payload)`输出独立上下文；`validate_recheck_output(raw,payload)`只校验结构／引用，不证明判断真伪。输入8MiB上限，实际worker仍使用原1MiB上下文限制，响应64KiB。

输出：

```
{binding:<本次v2九字段>,
 parent_review:{review_id,plan_version,plan_hash,report_hash},
 resolutions:[{prior_finding_id,rule_id,status:"resolved"|"unresolved"|"unknown",
               source_segment_ids,targets,note}],
 rules:<五项D18规则结构；finding.prior_finding_id允许null或原问题ID>,
 overall_verdict:"accepted"|"rejected"|"unknown"}
```

每个prior finding必须恰有一项resolution，规则、真实来源段和镜头字段匹配，resolution必须包含该旧finding全部来源段与原targets，可追加有效证据；不允许丢弃／编造旧问题或移到无关镜头。unresolved／unknown分别需要同规则rejected／unknown的新finding引用该prior ID；resolved不能仍有引用该ID的新finding。新发现问题可prior_finding_id=null。所有五项规则仍完整审阅，聚合沿用rejected优先，再unknown；accepted必须所有旧问题resolved。非法归RECHECK_INVALID，不能假作内容接受／拒绝。

## 终态与恢复

首审缺证或提案needs_input：blocked／REVIEW_NEEDS_INPUT。首审无效：blocked／REVIEW_INVALID。改稿无效：blocked／REPAIR_INVALID。复核拒绝：blocked／RECHECK_REQUIRES_REPAIR（上限已用，不再自动改）；缺证：blocked／RECHECK_NEEDS_INPUT；无效：blocked／RECHECK_INVALID。当前版本合成accepted：plan_ready。

任何角色无确认回执：原response_unknown／已暂停，固定MODEL_RESPONSE_UNCONFIRMED，保留原intent与计数，不自动重调。已received回执恢复只处理一次，不重发；stop阻止后续角色委派并保留在途receipt，显式resume再推进。角色未知投影为各自*_response_unknown，Planner未知时保留awaiting_plan与原task未知状态。累计上限／字节上限触发沿用STEP_LIMIT／CONTEXT_LIMIT，不能借换角色或版本绕过。

每次存储读写重建实际Planner工具接收、首审context／报告／提案、Repairer context及回执候选、v2和Rechecker context／报告。只允许intent→received→processed或intent→unknown；不可更改已收到响应、旧操作／计划／binding，不允许跳过回执写v2、伪造解决状态或扩大编辑权。旧任务不迁移或重算摘要，旧真实实验和付费账本不读写。

公共system／schema模板可以按值复用，但不缓存来源、binding、报告或已验证的私有任务。每次事务入口仍检查全部历史frame；出口以严格JSON类型树和稳定指纹识别是否改变，未变的读取省去第二次重复检查，任何写入仍须出口全量校验。指纹不能用Python容器相等替代，须区分bool／int／float、list／tuple、字符串键／整数键并拒绝非有限数及非法UTF8；不得改变原frame／source摘要算法。
