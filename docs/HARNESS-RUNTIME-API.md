# Harness Runtime API · D06 v1

冻结日期：2026-10-19（Asia/Shanghai）。范围：LTV-HARNESS-PRODUCT-001 零付费产品切片。该合同由本轮实现，不是完整生产Harness已验收的声明。

## 边界

- 正式FastAPI路径 `/api/harness/runs`；保留旧六阶段与Studio接口语义。
- 本版只支持 `mode: "test"`，所有快照、事件、审查、媒体均明确test/fixture。没有模型HTTP或收费供应商；测试决定不是用户真实最终接受。
- Planner/Reviewer/供应商可以是确定性fixture；API、存储、请求查回、项目关联、事件、媒体读取和前端连接使用真实产品实现。
- 私有运行状态、键索引、事件/回执在后端 `harness_data/product_runs/`，位于所有静态挂载之外；接口只返回安全投影。媒体经运行/产物ID接口读取，不返回磁盘路径。

## 1. 创建与查回

`POST /api/harness/runs`，返回201；重复相同请求返回200，`replayed: true`。

```json
{
  "mode": "test",
  "client_request_key": "client-generated-uuid",
  "project_id": "existing-studio-project-id",
  "source": {"hash": "64 lowercase hexadecimal characters", "record_ids": ["stable-record-id"]},
  "production": {"ratio": "9:16", "duration_ms": 15000},
  "authorization_ref": "test-zero-paid-v1",
  "scenario": "success"
}
```

`project_id`可为null以建立未关联的测试运行；非null须为已有未回收Studio项目。关联只保存在Harness私有索引，不改Studio项目或其source_session_id。source只收摘要/稳定record_id，不收小说全文。调用方不能提交语义hash、accepted、费用或任意回执。

`scenario`固定白名单：`success`、`repair`（一次局部返工）、`review_unknown`、`review_rejected`、`unknown_acceptance`、`zero_budget`、`resume_checkpoint`。它们是fixture情境，界面不得标成真实创作成功。

服务端对完整规范化业务输入计算语义hash（不含client_request_key），在同一次锁定原子写中建立key→run和project→run关联。同key同语义返回同run，同key变参409；客户端不能用自报hash欺骗去重。响应丢失后：

`GET /api/harness/runs/by-key/{client_request_key}` → `{"run": RunSummary}`；无记录404。该静态路由先于`/{run_id}`注册。重发创建也不会重复生产。

前端须在POST前持久化client_request_key；查回404/503/超时仍保留原键和已有快照，可以再次查回或同键重交，不能自动换键重建。旧的慢响应不能覆盖当前选中的run。

`GET /api/harness/runs?project_id=...` → `{"runs": [RunSummary]}`，新在前；无关联不借source_session_id找运行。

## 2. 读取与媒体

- `GET /api/harness/runs/{run_id}` → `{"run": RunSummary}`。
- `GET /api/harness/runs/{run_id}/events?after_sequence=0&limit=100` → `{"mode":"test","events":[Event],"next_sequence":N,"has_more":false}`；每次最多200。按持久单调序号读取，重放无工具副作用。
- `GET /api/harness/runs/{run_id}/artifacts/{artifact_id}/media?version=1` → 当前或保留历史fixture MP4。只接受服务端产物ID/版本，校验文件摘要，不接受路径/URL；下载文件名明确test-fixture，响应头`X-LeonTV-Mode: test`。

fixture媒体是带TEST标识的专用合成素材，可播放但没有小说人物、模型画面或生产质量含义。它由正式供应商fixture适配层交给真实存储，不能拿旧付费样片/旧实验结果代替。

## 3. RunSummary

创建/动作响应为`{"run": RunSummary, "replayed": false}`。查询不自动启动或续做；创建成功后由正式后台执行器推进有限fixture步骤。

```json
{
  "schema_version": 1,
  "id": "server-run-uuid",
  "mode": "test",
  "test_mode": true,
  "evidence_type": "fixture",
  "project_id": "project-or-null",
  "client_request_key": "client-generated-uuid",
  "scenario": "success",
  "status": "waiting_human_review",
  "revision": 12,
  "snapshot_sequence": 12,
  "source": {"hash": "...", "record_ids": ["..."]},
  "production": {"ratio": "9:16", "duration_ms": 15000},
  "blocked_reason": null,
  "plan": [{"id":"reference","depends_on":[]},{"id":"shot","depends_on":["reference"]},{"id":"final","depends_on":["shot"]}],
  "artifacts": [],
  "reviews": [],
  "manifest": {"version":1,"final_artifact_id":"final","final_version":1,"final_sha256":"...","review_bundle_hash":"...","valid":true},
  "budget": {"currency":"CNY","paid_limit":"0.00","paid_reserved":"0.00","paid_actual":"0.00","fixture_limit":8,"fixture_used":3,"unresolved_operations":0},
  "metrics": {"model_http_calls":0,"paid_calls":0,"supplier_submissions":3,"review_calls":3,"query_calls":0,"repair_count":0,"intervention_count":0,"final_decision_count":0},
  "final_review": null,
  "allowed_actions": ["stop","revise"],
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC"
}
```

数值为形状示例，实际revision/事件数不固定；前端必须读取当前值。source/production原样安全字段可回显，其余原始请求、正文、私有回执、账本路径和服务器配置不返回。

- artifact摘要：`id,version,kind,mode,generation_status,quality_status,required_scopes,dependencies[{artifact_id,version,sha256}],sha256,media_url,review_ids,stale`。媒体若不适用为null。
- review摘要：`id,artifact_id,artifact_version,artifact_sha256,mode,evidence_type,status,scopes,rule_version,valid,reason,receipt_id`；receipt_id是不可解引用磁盘路径的逻辑ID，不是原始judge输出。
- generation状态：pending/submitting/succeeded/unknown/failed；quality状态：unknown/accepted/rejected。accepted只表示fixture合同判断，绝不表示小说/人物/对白通过。
- run状态：queued/running/blocked/needs_reconciliation/stopped/waiting_human_review/test_accepted/test_returned/failed。

## 4. 停止、恢复、对账与版本失效

`POST /api/harness/runs/{run_id}/actions`

```json
{"mode":"test","client_action_key":"action-uuid","expected_revision":12,"action":"stop"}
```

action为stop/resume/reconcile/revise；revise另带`artifact_id`（reference或shot）。同action key同请求幂等，不重复调度/记事件；变参409。新动作expected_revision不符409，前端先刷新再重试。

- stop：持久停止并阻止新步骤；已发生的unknown不清掉。没有远端取消/退款宣称。
- 门禁以所有operations未决状态为准，不只看run.status；仍有submitting/unknown时禁止revise或resume后新生，stop也不能解禁。需先reconcile原操作，allowed_actions同步禁止绕过路径。
- resume：只推进可恢复的剩余步骤；已有成功工具回执/版本复用。正在运行不重复启动；质量unknown/rejected或预算阻断不盲目重生。恢复响应不得重置预算/次数。
- reconcile：只对unknown_acceptance fixture查原操作回执，不再次supplier提交；没有客户端task_id注入字段。然后可恢复剩余依赖。
- revise：显式测试上游改版。新增版本，相关审查和所有后代失效，保留旧文件/判断；标blocked/STALE_DEPENDENCY。之后resume仅修复受影响集合，未改变的上游不重生。

执行采用跨进程运行锁。重建应用/重启后，锁已释放但运行仍为queued/running时标stopped/RECOVERABLE_INTERRUPTION；无回执的提交意图标needs_reconciliation，不自动重发。读取可完成此恢复标记但绝不派发工具。

## 5. 测试终审

`POST /api/harness/runs/{run_id}/reviews`

```json
{
  "mode": "test",
  "client_review_key": "review-uuid",
  "expected_revision": 12,
  "manifest_version": 1,
  "final_sha256": "current-final-sha256",
  "decision": "accept",
  "feedback": "optional test note"
}
```

decision为accept/return，记录明确test_operator、test_mode、时间与版本；反馈私有存储，摘要只回是否已记录。正常提交须处于waiting_human_review，当前revision/manifest/final哈希、实际文件及完整依赖、rule版本、有效accepted证据在同一原子事务内匹配。任一unknown/rejected/stale/证据不足均409。hash相同但规则或依赖失效也拒绝。

final_review摘要字段为`id,mode:"test",test_mode:true,actor:"test_operator",decision:"test_accept"|"test_return",recorded_at,manifest_version,final_sha256,feedback_recorded`；返回值不含反馈正文。

首次写决定后revision增加；未发生后续变更时重复相同review key/请求返回原决定，不再写事件。同key改决定409；后续版本/证据改变后重复旧请求也409，不能借幂等键绕过过期检查。最终状态是test_accepted/test_returned，不是用户真实作品已接受。

## 6. Event与错误

Event：`sequence,timestamp,run_id,mode,type,operation_id?,tool_call_id?,artifact_id?,version?,dependencies?,generation_status?,quality_status?,reason?,receipt_id?,judge_id?,amount_cny?,usage?`；只投影白名单安全字段。关键变更与事件在同次原子写中落盘，快照可按事件序列追溯。原文、提示词、路径、headers和原始响应不进入事件。

错误统一为`{"detail":{"code":"VERSION_CONFLICT","message":"..."}}`。代码包括KEY_CONFLICT、VERSION_CONFLICT、RUN_ACTIVE、QUALITY_UNKNOWN、QUALITY_REJECTED、STALE_DEPENDENCY、BUDGET_EXHAUSTED、REMOTE_OUTCOME_UNKNOWN、NOT_FOUND、PRIVATE_STORAGE_UNAVAILABLE。Pydantic输入不合法返回422且不回显原始输入内容；未知字段禁止。

本版不提供付费授权、真实模型调用、用户身份认证、发布或替代旧工作流的入口。前端必须始终显示测试模式；后续E2/E3另立能力与费用合同。

## 7. 本轮实现与验证（2026-10-19）

实现位于实际后端`api/routers/harness.py`、`api/schemas/harness.py`、`core/harness/`，已注册到`api.app:app`；没有新增独立实验CLI。内置带TEST标识的专用MP4只作为供应商fixture，正式API、后台执行、原子存储、锁、事件、媒体校验和终审均为实际代码路径。

定向API验证46项通过；完整后端在不含config.yaml、用户code、harness_data、temp的干净副本中210项通过，socket出站保护记录0次外网尝试、0真实模型HTTP、0付费。原始日志/JUnit与隔离范围记录在`docs/evidence/LTV-HARNESS-PRODUCT-001/backend-*`。历史164项只是对照基线，本轮实际总数为210。

本轮验证覆盖同键并发/变参/响应丢失查回、queued启动竞态、两个Runtime竞争锁、应用重建恢复、unknown/rejected/预算门禁、stop或revise不绕过未决操作、原操作对账、局部返工、失效传播、同哈希旧终审与并发终审、真实媒体正向、私有字段/静态树/路径与symlink负向。测试终审明确test_operator，不是用户真实接受。

源码已冻结待统筹联调；后端窗口没有重启共享服务。本轮不宣称真实Planner/Reviewer或供应商已接入、创作质量通过、完整Agent Harness或Personal Agent成立。
