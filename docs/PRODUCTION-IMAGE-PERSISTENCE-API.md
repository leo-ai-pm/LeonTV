# D48 正式图片持久化／只读前端契约 · v1

2026-10-31设计，2026-11-01收尾。后端受限切片已实现并完成首轮隔离原生验证；实际ASGI样本见本轮backend/native-run-05/asgi-fixtures.json，具体验证范围见PRODUCTION-IMAGE-PERSISTENCE-VALIDATION.md。范围是正式MediaExecution及原delivery，D47诊断artifact不计正式镜头。既有A放映室设计不变，本轮不提供浏览器直取CDN、下载或恢复按钮。

## GET响应

`GET /api/planner/tasks/{task_id}/execution/media`，原`private_inspection`只读依赖。原顶层`media`及原字段值保留，**新增**顶层`production_images`。无执行记录继续HTTP404：`{"detail":{"code":"NOT_FOUND","message":"Execution record not found"}}`；前端按“尚无执行记录”空态处理，不能触发创建。存在执行记录但无正式scope则200、not_configured、空shots。未知task/path/body/query仍沿旧422/404/503错误合同；任何GET都不observer/下载/补写/恢复。

```ts
type ProductionImages = {
  schema_version: 1;
  task_id: string;             // 必须等于当前请求32位task id
  source_snapshot_hash: string; // 当前execution task.source.snapshot_hash，64hex
  evidence_kind: null | 'synthetic' | 'empirical_receipts'; // 原scope.handoff.evidence_kind，无scope null
  scope_id: string | null;     // 最新正式scope，无scope为null
  status: 'not_configured' | 'observed' | 'attention';
  read_only: true;
  required_count: number;
  registered_count: number;   // 当前镜头版本的原delivery事实，不等于文件可用
  verified_file_count: number;
  needs_input_count: number;
  shots: ProductionShot[];
};
type ProductionShot = {
  shot_id: string;
  first_frame_version: number;
  operation_id: string | null;
  query_id: string | null;
  opt_in: boolean;
  generation: 'not_started' | 'reserved' | 'submitting' | 'unknown' | 'accepted' | 'rejected';
  completion: 'not_observed' | 'pending' | 'completed' | 'failed' | 'unknown';
  reception: 'not_enabled' | 'not_started' | 'receiving' | 'saved' | 'failed' | 'unknown';
  received: boolean | null;
  persisted: boolean | null;
  delivery_registered: boolean;
  delivery_origin: 'none' | 'legacy' | 'managed';
  historical_saved: boolean;
  file_availability: 'not_recorded' | 'not_verified' | 'not_published' | 'available' | 'missing' | 'invalid';
  authority: 'not_applicable' | 'valid' | 'expired' | 'revoked' | 'unavailable';
  fee_observed: boolean;
  http_status: number | null;
  error_code: string | null;   // 仅后端固定安全码，不能原样显示异常文本
  needs_input: boolean;
  retry_authorized: false;
  quality: 'not_reviewed';
  human_acceptance: 'pending';
};
```

全部字段必有，null表示未建立或未核验，不能转换成成功。只返回以上安全标量，不返回URL、bytes/base64、raw、Key或绝对私有路径。`error_code`允许：HTTP_NOT_200、NETWORK_OR_WORKER、INVALID_BYTES、LOCAL_STORAGE、INTERRUPTED_MISSING_BYTES、RESOURCE_LIMIT、TRUNCATED_RESPONSE、TIMEOUT、CAPACITY_EXCEEDED、AUTHORITY_EXPIRED、AUTHORITY_REVOKED、AUTHORITY_UNAVAILABLE、LEGACY_FILE_NOT_VERIFIED、FILE_MISSING、FILE_INVALID、PERSISTENCE_PENDING、DELIVERY_PENDING，或null。HTTP404只是接收失败事实，根因未知，不显示“链接已过期”。

legacy已登记保留delivery_registered=true、delivery_origin=legacy；其本地文件file_availability=not_verified、received/persisted=null，不凭旧delivered布尔值升级。managed保存未登记可received/persisted=true但delivery_registered=false。part+完整及时proof尚未发布可received=true、persisted=false、file_availability=not_published；GET不替它完成发布。文件丢失/损坏后历史登记保留，当前received/persisted=false；权限无法核验且未检查文件时为null/not_verified。

文件发布与原生saved提交非原子：准确blob+完整及时proof已经存在时，即使原生接收记录仍为receiving／unknown、saved=null，本次只读核验也可received/persisted=true、file_availability=available；historical_saved=false、delivery_registered=false、error_code=DELIVERY_PENDING、needs_input=true。available不要求reception=saved或historical_saved=true，也不单独构成“正式交付已保存”。若delivery已提交而接收指针尚未同步，保留delivery_registered=true、historical_saved=true，但needs_input=true；GET不补写指针。

权限是“此接收profile当前是否有效”，不是GET授权执行。过期但文件与delivery完整时可以保留可用事实；只有尚未完成或文件不可用时needs_input=true。quality/human永不因下载或delivery登记提升。

## 最小实例

机器可读的完整8例在本轮`backend/API-CONTRACT-EXAMPLES.json`（契约草稿，非测试结果）。以下只展示状态差异，省略的字段仍按上面必有。

| 例 | HTTP／状态 | 关键值 |
| --- | --- | --- |
| 1 无执行记录 | 404 NOT_FOUND | 前端空态，不通过GET创建 |
| 2 有task、无scope | 200 not_configured | scope_id=null、required_count=0、shots=[] |
| 3 legacy交付 | attention | opt_in=false、delivery_registered=true、legacy、file=not_verified、received/persisted=null |
| 4 managed成功 | observed | completed、saved、received/persisted=true、registered=true、file=available、fee_observed=true |
| 5 completed无图／未接收 | attention | completed、receiving、received/persisted=false、registered=false、PERSISTENCE_PENDING |
| 6 404／unknown接收 | attention | completed保留，reception=failed/http404或unknown/TIMEOUT；received/persisted=false、needs_input=true、retry=false |
| 7 已保存未登记 | attention | saved、file=available、received/persisted=true、registered=false、DELIVERY_PENDING |
| 8 丢失且接收权限到期 | attention | historical_saved=true、registered=true、file=missing、received/persisted=false、authority=expired、FILE_MISSING |

前端只展示状态与原因；不把needs_input变成可重试权限。诊断行不混入shots/registered_count。task切换后必须核对production_images.task_id及source_snapshot_hash，后者精确匹配当前PlannerDetail.source.snapshot_hash；陈旧响应不得覆盖当前task。evidence_kind直接取原scope.handoff，managed不意味着真实provider。

## 内部执行接缝

将新增出生前的`quote_production_image_persistence(control, grant_id, shot_id, valid_until=...)`／严格consent activation。`MediaExecution.reserve_submit(..., persistence_activation_id=...)`显式绑定新operation；默认None保持关闭，旧grant/descriptor/operation不补挂。query首次completed、已有query重放、recover采用completed统一接收，单次耐久intent先于网络。

以上入口现已实现，供后端内部显式配置，未新增公网授权／执行API。现有正式图片协议的permit只支持first_frame_version=1；DTO与新权限按该准确版本绑定，本轮没有扩展版本协议。执行／恢复须使用build_image_session创建的准确typed control会话；只读GET独立于该执行会话。

复用D47固定HTTPS、公网单地址、无Key/代理/redirect/重试、2MiB、完整decode、45秒独立worker＋5秒清理、私有证据32MiB/64位置、post-fsync确认及本地恢复；原budget/费用/unknown/正式quality gate不变。正式登记使用原`delivery`而非diagnostic artifact。详细当前权限、持久字段及实际验证在DESIGN/后续VALIDATION补齐。本轮不执行真实provider，不追授D46下载404或旧automatic0。
