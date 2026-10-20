# Planner Execution API · D09 v1

冻结日期2026-10-20 Asia/Shanghai，LTV-PLANNER-EXECUTION-001，基线b4a2bcf。默认live关闭；本轮仅合成policy/HTTP transport验证，0真实模型/0付费。D06/D07/D08合同及私有格式保持兼容，offline任务不得升级。

## 1. 执行域和边界

D09给D08的**normal任务**增加独立执行域。D08来源只读，完整冻结source及其hash复制后永久绑定；D09自己的消息、计划、授权、操作/费用记录在**单个私有journal**中，是执行唯一事实源。不会跨两个os.replace声称原子，也不回写/迁移D08任务；D08原normal状态仍未授权，其旧resume仍不调用模型。前端的执行状态/计划必须观察D09，不能把D08旧status当D09执行结果。D09 plan_ready也只表示结构合格，quality_status始终not_reviewed。

本轮journal仅允许首次固定的唯一account身份，聚合所有任务/策略版本；服务端换account_scope_id也返回POLICY_UNAVAILABLE，不能换bucket清旧消耗。不同真实账户另行评审。私有格式为schema_version=2的变更记录journal，每次只存变化namespace/record，保留独立head/摘要链并重放恢复；未发布schema1合成树不自动迁移，D08原格式不受影响。

私有根`harness_data/planner_execution/`，原子提交整个执行域，跨进程全局锁及逐任务执行锁。所有journal版本有链及独立head；损坏、单尾删、symlink、递归静态树父/同/子相交均拒绝读写且保留文件。无旧格式回退或自动迁移。上下文、wire、完整回执/内容/usage、许可和报价策略只在私有树；公共API只安全投影。无密钥浏览器存储/日志/响应，无图片/视频/音频/搜索/任意文件工具。

## 2. 路由

前缀`/api/planner`。ID为32位小写hex，client key为8–128位`[A-Za-z0-9_-]`；全部JSON strict/extra forbid，全部响应no-store。下面`E=/api/planner/tasks/{task_id}/execution`。

| 方法/路径 | 返回 |
| --- | --- |
| GET `/execution/policy` | `{"available":false,"blocked_reason":"LIVE_DISABLED","environment":"disabled","policy":null}`；可用时policy为SafePolicy |
| GET `E` | `{"execution":ExecutionDetail}`，没有域记录时revision=0，安全只读观察；不提交模型 |
| POST `E/quotes` | 首次201/重放200，`{"quote":Quote,"execution":ExecutionDetail,"replayed":false,"applied_revision":N}`；报价不授权不执行 |
| GET `E/quotes/by-key/{client_request_key}` | `{"quote":Quote}`，不存在404 |
| POST `E/grants` | 首次201/重放200，`{"grant":Grant,"execution":ExecutionDetail,"replayed":false,"applied_revision":N}`；授权不自动执行 |
| GET `E/grants/by-key/{client_request_key}` | `{"grant":Grant}` |
| POST `E/grants/{grant_id}/revoke` | 200 `{"grant":Grant,"execution":ExecutionDetail,"replayed":false,"applied_revision":N}` |
| POST `E/actions` | 200 `{"execution":ExecutionDetail,"replayed":false,"applied_revision":N}` |
| GET `E/events?after_sequence=0&limit=100` | `{"events":[SafeEvent],"next_sequence":N,"has_more":false}`；limit1–200 |

GET不调用模型/创建报价/授权。没有服务端已核实策略/开关/密钥时available=false，报价/授权/执行返回明确409原因，0client/0request。默认环境disabled；测试注入时environment=synthetic_test，不表示真实模型已执行。offline task所有D09路由409 TASK_NOT_ELIGIBLE。

### 请求

报价：
```json
{"client_request_key":"quote-key-persisted","expected_execution_revision":0,"expected_task_revision":1}
```
首次冻结明确D08 task revision的全部source；之后source不可改，expected_task_revision仍需与当前D08 normal任务一致，避免静默引用旧观察。expected_execution_revision针对D09域版本，不是D08 revision。新报价使用当前服务端策略，不收客户端金额/model/endpoint/rates/token/hash/source。

授权：
```json
{"client_request_key":"grant-key-persisted","expected_execution_revision":1,"quote_id":"32hex","accepted_disclosure_hash":"64hex","accepted_scope":"frozen_brief_and_tool_history_to_named_provider","consent":true}
```
仅引用已显示报价/披露摘要并明确同意。服务端验证绑定及有效期，budget草稿/旧100元不能授予范围。授权金额/调用数/token/有效期来自quote，不允许自报。新grant不能重置已消费调用、历史金额或未知hold。

撤销：`{"client_action_key":"revoke-key-persisted","expected_execution_revision":2}`。动作：`{"client_action_key":"execute-key-persisted","expected_execution_revision":2,"action":"resume"}`，action仅`resume|stop|reconcile`。resume显式启动/续行；stop阻止后续新提交但不声称取消在途；reconcile只处理**已持久化回执**，不查询不存在的账单API、不新发模型、不接收客户端金额/usage/receipt。无回执的未知意图不能被reconcile释放或重发。

各资源键在执行域对应namespace全局唯一，绑定task/key/全部规范化请求；同键变参409 KEY_CONFLICT。重放返回当前execution和原resource/applied_revision，不重新签发/提交。先查重放再核对新动作revision；新键版本过期409且不占键。未知响应保留原键/原请求查回/原样重交；禁止自动换键、改revision或以新grant清额度。旧慢响应不得覆盖新任务观察。

## 3. 返回字段

SafePolicy：`id,version,policy_hash,provider,model,endpoint,currency,input_cny_per_million,output_cny_per_million,max_input_tokens,max_output_tokens,max_input_bytes,max_output_bytes,request_deadline_seconds,max_calls,task_limit_cny,account_limit_cny,account_max_calls,valid_until,privacy_scope,pricing_basis,input_bound_basis`。provider仅openai_compatible；endpoint为明确无secret HTTPS路径，无fallback/provider自动切换。所有金额/费率为十进制字符串（金额6位小数），币种CNY。

Quote：`id,task_id,planner_task_revision,snapshot_hash,policy,per_call_hold_cny,total_limit_cny,max_calls,expires_at,disclosure_hash,created_at,status`；status=`valid|expired|policy_stale`。完整披露绑定task/source、exact model/endpoint、策略费率版本、范围、上限和有效期。per_call_hold是服务端保守本地预占上界，不是报价账单/扣款；total_limit是该任务的**累计**限额，不是每次grant重置额度。

Grant：`id,task_id,quote_id,snapshot_hash,policy_hash,disclosure_hash,scope,status,total_limit_cny,max_calls,expires_at,created_at,revoked_at`；status=`active|expired|revoked|policy_stale`。报价过期/策略变化、撤销、到期均阻止新提交；不释放已在途/未决hold，不承诺退款。

ExecutionDetail：`task_id,revision,environment,status,blocked_reason,allowed_actions,source,policy_available,active_grant_id,quotes,grants,ledger,operations,plan,quality_status,created_at,updated_at`。

- revision为此任务D09域版本，初始0；source为D08安全来源摘要（含snapshot_hash、无正文）；plan为null或D08 ShotPlan；quality_status固定not_reviewed。
- quotes/grants为上述安全对象数组；active_grant_id为当前选择grant或null；创建新grant仅选择新授权，历史不删。
- ledger：`currency,locally_accounted_cny,held_cny,task_limit_cny,call_count,max_calls,account_locally_accounted_cny,account_held_cny,account_limit_cny,account_call_count,account_max_calls,provider_bill_confirmed`。provider_bill_confirmed恒false。本地已计价+未决hold不得超过任务/当前grant及account累计上限；call_count含已提交意图，stop/resume/换grant不减。
- OperationSummary：`id,step,status,grant_id,quote_id,policy_hash,context_hash,hold_cny,locally_accounted_cny,provider_request_id,provider_response_id,input_tokens,output_tokens,finish_reason,receipt_status,created_at,received_at`。status=`submitting|received|settled|needs_reconciliation`，receipt_status=`none|received|confirmed_local|unconfirmed`；未知token/ID/金额为null，不显示为0元。无content/raw/请求体/私有路径/secret。
- SafeEvent：`sequence,type,execution_revision,created_at,operation_id,code`；最后两项可null，固定安全事件/原因，无原文。sequence按task从1连续；分页同D08。

## 4. 状态、原子提交点和回执

| status | 典型blocked_reason | allowed_actions通常值 |
| --- | --- | --- |
| not_authorized | AUTHORIZATION_REQUIRED / LIVE_DISABLED / POLICY_UNAVAILABLE | [] |
| ready | null | resume |
| queued / running | null | stop |
| stopped | STOPPED / RECOVERY_REQUIRES_RESUME | resume（无未知时） |
| blocked | GRANT_REVOKED / GRANT_EXPIRED / POLICY_STALE / BUDGET_EXHAUSTED / CALL_LIMIT / STEP_LIMIT / REPAIR_LIMIT / CONTEXT_LIMIT / LIVE_DISABLED | resume（仅门禁恢复且无未决） |
| needs_reconciliation | RESPONSE_UNCONFIRMED / RECEIPT_INCOMPLETE / RECEIPT_OUT_OF_BOUNDS / RECEIPT_TRUNCATED / MODEL_MISMATCH | stop或reconcile（仅有可处理持久回执） |
| plan_ready | null | [] |

以allowed_actions为准；blocked状态可以观察/撤销/重新报价授权，但不能绕过原有hold或累计调用。已停止但仍有未知hold时reason保持RESPONSE_UNCONFIRMED，禁止resume。

提交点在同一个journal锁内：重验task/source/当前policy版本/endpoint/model/范围/grant及quote有效期/撤销/全部余额与调用上限；原子提交hold+operation intent+permit绑定+callcount，再释放全局锁进行一次HTTP。每任务执行锁跨进程只允许一个派发者。revoke在此提交点之后不能伪称取消已提交op，只阻止下一提交点。HTTP不持全局锁。整体request deadline与输入wire/token/输出token/bytes上界由policy限定，无隐式重试或改键。未知接受/进程在意图后退出时永不自动重发，hold保留。

input_bound_basis文字或billing_bound_verified标记本身不构成token证明。服务端还必须注入绑定精确policy_hash的可信input bounder（含核实依据），缺失则POLICY_UNAVAILABLE；每个实际wire在预占/意图前计算上界，超policy.max_input_tokens或非法结果时零新hold/调用/HTTP并阻断CONTEXT_LIMIT。sink重算实际wire/context摘要和token上界，再核对许可的任务/source/grant/quote/account/策略/model/端点/费用/字节/token/到期绑定。不存在bytes天然等于真实token的默认算法；本轮测试bounder仅对SyntheticProvider的合成约定成立，生产仍无已核实bounder。

原始响应先私有持久，再归一/本地计价/工具处理。标准支持内容+provider_request_id（header x-request-id或明确request_id）+response id+model+finish_reason+usage.prompt_tokens/completion_tokens/total_tokens。严格整数非bool/非负，input/output不超许可，总数一致；未知计费字段、缺usage/ID/model、超界、非stop finish、截断、HTTP/timeout异常都不视为0元成功，不把可能收费的hold释放。已确认完整receipt可按冻结费率Decimal计算并向上舍入到0.000001元，替换对应hold为locally_accounted金额；这只是本地计价，**不是供应商账单已确认**。累计计价/hold与调用数跨进程、跨grant保留；account域合计所有任务和策略版本的消耗，不能因换grant/停启重置。

恢复只复用已确认回执和实际本地工具结果；未确认保留hold、needs_reconciliation，不允许客户端写账或新授权抹除。正常源仍用D08两工具、6步/2错误、15秒/来源/ID/DAG/字节限制；结构合格不等于内容验收。不声明本地gate能强制第三方实际账单遵守报价；无供应商核实边界/完整策略时根本不签发许可。

## 5. 错误

格式`{"detail":{"code":"...","message":"固定安全说明","current_revision":N}}`；current_revision仅版本冲突。

- 422 INVALID_INPUT；404 NOT_FOUND。
- 409 KEY_CONFLICT / REVISION_CONFLICT / TASK_NOT_ELIGIBLE / ACTION_NOT_ALLOWED / LIVE_DISABLED / POLICY_UNAVAILABLE / QUOTE_EXPIRED / POLICY_STALE / CONSENT_MISMATCH。
- 503 PRIVATE_STORAGE_UNAVAILABLE / CORRUPT_STORAGE。

不回显Pydantic input、原文、价格输入或异常repr。本轮无客户端自报billing结算、无退款/取消供应商任务API，真实试运行需后续具体模型、核实费率/接受范围和小额明确授权。

## 6. 本轮实现与验证

2026-10-20：单delta journal的报价/授权/撤销/预占/意图/许可/单次请求/私有回执/本地计价与保守对账/受限规划循环已接入正式API。默认live仍关闭；normal和offline D08文档不升级或回写。

- 最终源隔离定向267项通过，全量431项通过（新执行域76项），Ruff通过；定向/全量/当前198源文件SHA一致。真实模型HTTP、付费、出站均0。
- 覆盖原子费用和跨进程门禁、换grant/策略scope不清旧额度、实际token上界/sink绑定、撤销/到期/未知/usage与截断边界、确认回执复用和实际进程硬中断、严格规划工具循环、微元计价/金额投影、私有树/静态/symlink/损坏/单尾删及旧格式拒绝迁移。
- 同cohort6任务journal698,487B，12任务/24op/121frames为1,396,586B；定向/全量GET约0.13/0.17秒，重建无新操作。机器日志和私有交接在本地保留，公开结果见[独立验收范围](./PLANNER-EXECUTION-VALIDATION.md)。
- 主窗口最终源独立验收：默认关闭16项、合成传输75项HTTP检查通过；实际服务重启、两处进程硬中断和浏览器原键恢复已通过。前端107项测试通过。
- 合成policy/transport/bounder只用于验证实际控制路径，未证明真实provider价格/账单/tokenization或生产内容质量。真实文本试运行须先核实协议与计费边界，再绑定具体输入和小额上限。
