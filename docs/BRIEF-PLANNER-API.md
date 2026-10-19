# Brief Planner API · D08 v1

冻结日期：2026-10-20 Asia/Shanghai，LTV-BRIEF-PLANNER-001。D06/D07保持兼容。本轮交付真实私有规划任务及受限工具循环；正式任务未授权不调模型，离线模式只替代模型响应。**不生成媒体、不授权付费、不把结构合格称为内容已验收。** 本地单用户范围，不宣称多用户鉴权。

## 1. 路由与请求

前缀`/api/planner`。ID为32位小写hex；各client key为8–128位`[A-Za-z0-9_-]`。JSON严格、全层级拒绝未知字段。全部响应`Cache-Control:no-store`；`X-LeonTV-Planner-Mode:normal|offline_test`（混合列表为mixed）。

| 方法/路径 | 成功响应 |
| --- | --- |
| POST `/tasks` | 首次201/重放200，`{"task":TaskDetail,"replayed":false,"applied_revision":1}`；只建立正常待授权任务 |
| POST `/offline-tasks` | 同上，显著离线验证任务，初始ready；不会调用真实模型；需显式resume才运行 |
| GET `/tasks?offset=0&limit=50&brief_id=...` | `{"tasks":[TaskSummary],"next_offset":null,"has_more":false}`；limit1–100，brief_id可省略，更新时间较新在前 |
| GET `/tasks/by-key/{client_request_key}` | `{"task":TaskSummary}`，安全查回摘要，先于ID路由注册 |
| GET `/tasks/{task_id}` | `{"task":TaskDetail}`，最新完整计划及任务观察；不含剧本/原始模型消息/工具参数 |
| GET `/tasks/{task_id}/source` | `{"source":FrozenBrief}`，明确按ID读取完整冻结剧本和制作要求 |
| GET `/tasks/{task_id}/events?after_sequence=0&limit=100` | `{"events":[SafeEvent],"next_sequence":N,"has_more":false}`；limit1–200，只安全投影 |
| POST `/tasks/{task_id}/actions` | 200，`{"task":TaskDetail,"replayed":false,"applied_revision":N}` |

正常创建只接受：
```json
{"client_request_key":"persisted-create-key","brief_id":"32lowerhex","brief_revision":1}
```
brief_revision为严格整数>=1，必须是D07已保存的明确版本。离线创建另允许`scenario`，默认`success`，枚举`success|repair|invalid_tool|step_limit|response_unknown`。scenario仅用于离线响应Actor，不能在正常创建提交。正常请求不收mode/授权/模型/费用/正文/hash/路径/URL字段；无live/authorize路由或布尔开关。

动作：
```json
{"client_action_key":"persisted-action-key","expected_revision":1,"action":"resume"}
```
action仅`stop|resume`，expected_revision严格整数>=1。只在allowed_actions中显示的动作可发起，冲突后刷新任务并保留本地选择。创建后前端应保留原键/原请求；响应未知按原键查回/原样重交，不能自动换键。动作响应未知也保留原键/原请求，不能自动换expected_revision重做。

创建键在normal/offline入口间全局唯一，绑定规范化请求（服务端mode/scenario、brief_id、明确revision及原key）；同键变参409。重放返回当前任务及原applied_revision=1，不恢复旧状态。动作键在单任务内唯一，绑定键身份/action/expected_revision；同键同请求返回当前任务及原applied_revision，同键变参409。新键版本过期409且不占键。所有状态写入都递增revision，任务异步推进时revision可变化；旧慢响应不能覆盖当前选中任务。

## 2. 状态和可用动作

| status | blocked_reason | 语义与通常可用动作 |
| --- | --- | --- |
| awaiting_authorization | AUTHORIZATION_REQUIRED | 正式任务持久待授权，模型调用0；stop |
| ready | null | 离线任务已创建，尚未执行；resume/stop |
| queued | null | 离线执行已排队；stop |
| running | null | 同一受限循环正在运行；stop |
| stopped | STOPPED / RECOVERY_REQUIRES_RESUME / MODEL_RESPONSE_UNCONFIRMED | 已停或重启恢复，未决意图保留；仅可安全续行时resume，未决intent不可续行 |
| response_unknown | MODEL_RESPONSE_UNCONFIRMED | 无已确认回执，禁止重调/自动重试；stop |
| plan_ready | null | 结构化计划合格，quality_status仍not_reviewed；无动作 |
| blocked | REPAIR_LIMIT / STEP_LIMIT / CONTEXT_LIMIT | 有限修正/步数/上下文上限；无动作 |

以每次响应的allowed_actions为准。正常任务stop后可resume回awaiting_authorization，仍不执行模型。离线resume异步执行，轮询GET及events；首次动作可返回queued/running或已推进的状态。

每次模型调用先持久intent（operation_id、上下文和hash），后调用，收到响应先持久receipt再解释/执行工具。已确认receipt恢复时复用，不重复调用；执行锁仍被占用不误判为崩溃。进程退出留下无回执intent时变response_unknown；stop保留未决信息，不提供伪造回执/绕过/强制重试接口。已确认或无未决调用的孤立running/queued恢复为stopped，等待显式resume。本轮不提供生产取消、退款或实时调用对账承诺。

## 3. 精确返回字段

TaskSummary：
`id,revision,mode,scenario,status,blocked_reason,allowed_actions,created_at,updated_at,source,authorization_status,execution_enabled,quality_status,step_count,repair_count,pending_call,plan_version`。

- mode=`normal|offline_test`；正常scenario=null，离线scenario为请求枚举。正常authorization_status=`not_authorized`，离线=`offline_only`；execution_enabled恒false（无生产媒体执行），quality_status恒`not_reviewed`。
- source为安全来源摘要：`brief_id,brief_revision,source_version,source_hash,snapshot_hash,title,production,budget_cny,currency,frozen_at`，无正文。snapshot_hash由服务端绑定全部冻结内容/来源/冻结时间；预算仍只是D07草稿。
- step_count为已落调用意图数；repair_count为协议或计划错误累计数；pending_call为null或`{"operation_id":"32hex","status":"intent|received"}`，不含参数/回复。plan_version为有效计划数，尚无为0。
- TaskDetail增加`client_request_key,limits,plan,plan_versions`。limits固定`{"max_steps":6,"max_repairs":2,"max_output_bytes":65536,"max_context_bytes":1048576}`。plan为null或最新`ShotPlan`；plan_versions为`[{"version":1,"plan_hash":"sha256","created_at":"ISO"}]`，本轮首个结构合格计划结束循环。
- FrozenBrief为source摘要再加`script,requirements`，原样保留D07明确版本；后续Brief编辑不改旧任务。所有hash为64位小写SHA-256，时间为服务端UTC ISO8601。
- SafeEvent：`sequence,type,task_revision,created_at,step,code`；step、code可null，type为固定服务端事件名，不含正文、模型原文、工具参数/私有路径/异常repr。sequence从1连续递增，next_sequence为本页末序号，空页为传入after_sequence。

## 4. Planner协议和计划

一次模型响应只能是严格JSON对象，UTF8<=65536字节；不提取Markdown代码块，不eval。动作二选一：
```json
{"tool":"read_frozen_brief","arguments":{}}
```
```json
{"tool":"submit_shot_plan","arguments":{"plan":{"source_snapshot_hash":"server-snapshot-hash","duration_ms":15000,"ratio":"9:16","shots":[{"id":"S01","duration_ms":7500,"visual":"镜头画面","camera":"镜头运动","audio":"声音说明","depends_on":[]},{"id":"S02","duration_ms":7500,"visual":"第二镜","camera":"推进","audio":"环境声","depends_on":["S01"]}]}}}
```

先读取冻结Brief，工具返回实际冻结数据供后续上下文使用；提交前未读返回SOURCE_NOT_READ。白名单仅这两个本地工具，未知工具/坏协议反馈给下一步并消耗修正次数；不会调用文件/代码/网页/媒体工具。

ShotPlan严格拒绝未知字段：source_snapshot_hash必须匹配服务端冻结摘要，duration_ms严格整数15000，ratio固定9:16；shots1–12个，id为`[A-Za-z][A-Za-z0-9_-]{0,31}`且唯一，duration_ms整数1–15000、合计15000；visual非空1–2000字符，camera/audio可空0–500字符，UTF8有效。depends_on为0–11个唯一已存在镜头ID，禁止自身、悬空和环（可引用其他顺序的镜头）。结构错误代码进入工具反馈，反馈影响下一次Actor上下文；累计错误达到2次则REPAIR_LIMIT，最多6次模型调用，耗尽STEP_LIMIT。整个上下文超过1MiB则CONTEXT_LIMIT，不再外发。

离线Actor根据实际工具历史响应：success读取后给合格计划；repair先提交总时长错误计划，读取实际校验反馈后修正；invalid_tool重复请求非法工具直至修正上限；step_limit重复合法读取直至步数上限；response_unknown模拟收到响应前的不确定错误。只有模型响应是合成的，持久化/循环/工具/校验/反馈/恢复均为同一实际代码。合格后plan_ready仍不代表语义、画面或人工审核通过。

真实文本adapter提供单次受控调用实现，复用项目已有HTTP/文本消息模式，绕开旧客户端的日志/隐式重试；需要服务端签发的调用许可，绑定任务、operation/context/source和具体模型/端点/隐私范围/有效期/调用与token/费用上界。公开API不签发许可，也不从预算或旧授权推导许可；本轮不开放正常执行，测试只注入模拟transport，无真实请求。

适配器输入字节上限按**实际序列化发送的完整JSON请求体**计算（包括消息转换/二次JSON编码）；循环max_context_bytes按内部上下文UTF8 JSON计算，两者分别检查。适配器目前只校验许可中的符号费用上界，未实现真实价格计算、累计费用预占/结算或usage/request-id/finish-reason供应商回执归一；适配器自身operation去重为进程内，跨进程未决保护来自任务持久intent/receipt。本轮没有许可签发服务，不能把此适配代码称为完整收费控制或已验证供应商能力。

## 5. 错误与私有持久化

错误格式`{"detail":{"code":"REVISION_CONFLICT","message":"固定安全提示","current_revision":2}}`，current_revision仅版本冲突返回。

| HTTP | code |
| --- | --- |
| 422 | INVALID_INPUT |
| 404 | NOT_FOUND（包括Brief/版本/任务/创建key） |
| 409 | KEY_CONFLICT / REVISION_CONFLICT / ACTION_NOT_ALLOWED |
| 503 | PRIVATE_STORAGE_UNAVAILABLE / CORRUPT_STORAGE |

所有错误不回显正文或Pydantic input。私有根为`harness_data/brief_planner/`，与D06/D07隔离，递归静态树双向不相交；拒绝根/祖先/状态/锁symlink和路径逃逸。原子替换和跨进程锁同次提交任务/键/事件/回执/计划，完整性摘要链检测意外改写（不宣称防恶意重算签名）。损坏停止读写、不清空、不自动迁移、不覆盖原文件。原始模型消息、上下文、工具参数/结果只在私有树；公共列表/事件不泄漏这些内容。真实模型/付费/媒体生成及生产质量验收另立迭代。

## 6. 本轮实现与验证

2026-10-20：正式API注册、不可变冻结源、跨进程私有任务日志、白名单工具及反馈循环已实现；独立head_revision/head_hash与末尾frame核对，单处尾删也拒绝读写，不会丢掉成功动作的幂等回执。不宣称检测整份合法旧文件回滚或恶意重算所有摘要。

- 最终源隔离定向191项通过（Planner81 + D07 64 + D06 46），全量355项通过，Ruff通过。定向/全量/交付源代码SHA-256一致，真实模型HTTP/付费/出站均0。
- 验证真实API/文件/OS锁/跨进程、全部版本来源冻结、反馈修正/上限、未知意图不重调、确认回执恢复、停止与迟到回执、wire字节限额、深层坏JSON、私有静态/symlink/损坏/原子写/单尾删保护。仅模型响应或底层HTTP transport由合成Actor替代，未验证真实服务商能力或生产内容质量。
- 正式前端、独立HTTP、实际进程中断与重启的最终范围见[规划任务验证](./BRIEF-PLANNER-VALIDATION.md)。原始日志、JUnit、排除生产资料的拷贝清单及源码hash保留在本地忽略的证据目录。
