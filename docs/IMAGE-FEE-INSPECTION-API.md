# 图片费用只读诊断 API（D33）

公开实现：`backend/core/execution/image_fee_inspection.py`。本模块读取已有 POST 回执、原生记录及可选平台费用导出，返回安全诊断元数据。它不导入费用、不认领费用、不结算或释放风险，不生成图片、不查询上游、不加载密钥或启动 observer。

```python
inspect_receipt(request, raw, source, *, export_bytes=None, observed_at) -> dict
inspect_native(control, operation_id, *, export_bytes=None, observed_at=None) -> dict
parser() -> argparse.ArgumentParser
main(argv=None) -> int
```

`inspect_receipt` 是纯函数：`request` 必须是可完整重建的 `FrozenImageRequest` POST，不能传 GET、字典或仅重算哈希的伪 descriptor；`raw` 为原始捕获 envelope 或 `None`。`source` 按 `FrozenFeeSource` 严格验证，其 `account_namespace` 必须等于 descriptor 的 `budget_anchor.account_scope_id`。函数不修改输入，不缓存结果；每次按当前 raw、source 和 export 重新诊断。

`export_bytes` 是完整原始平台 JSON 日志页的 bytes；`observed_at` 是本次核验时间。直接匹配复用原 `candidate_for`，同时核对真实 POST request ID、消费类型、token/group、model、时间与 quota，不能用 task ID、GET request ID、相近时间或唯一候选行推定匹配。

`inspect_native` 先执行 `control.verify_authority(for_dispatch=False)`，再通过原 `ExecutionStore.read_only()` 重建 journal，限定原 task 和匹配 scope 的 POST operation。它不构造执行 runtime。省略 `observed_at` 时，有已观察费用则复用原候选的 `observed_at`，否则使用当前 UTC 时间。

所有正常结果固定包含以下字段：

| 字段 | 固定值与含义 |
| --- | --- |
| `read_only` | `True`：本次操作仅诊断。 |
| `import_authorized` | `False`：诊断不给费用导入或 claim 权限。 |
| `new_dispatch_authorized` | `False`：诊断不给新 POST、GET 或续接权限。 |
| `automatic_followup_calls` | 整数 `0`：诊断不会追加网络动作。 |

这三个 read-only/授权字段描述本模块的能力边界，不是在判断实际当前 grant 是否有效或是否具备派发资格。错误 CLI JSON 也保留这四个固定字段。

`fee_match_status` 仅有以下 10 种值：

| 状态 | 含义 |
| --- | --- |
| `receipt_unavailable` | 原 POST raw 不存在。 |
| `receipt_invalid` | raw envelope、编码、JSON 或时间等无法通过原回执校验。 |
| `missing_submit_request_id` | 没有可用的直接 POST request ID。 |
| `conflicting_submit_request_id` | 捕获标准头与 body 的 request ID 相互冲突。 |
| `invalid_submit_request_id` | 捕获的直接 request ID 格式无效。 |
| `export_required` | 已有直接身份，尚未提供费用导出。 |
| `export_invalid` | 导出格式或行数据无效。 |
| `direct_match_missing` | 合法导出中没有对应的直接消费记录。 |
| `evidence_conflict` | 同 ID 的费用行冲突，或来源/model/时间等证据不一致；native 比对冲突也用此值。 |
| `direct_match` | 原核费函数完成一次精确候选重建。 |

missing/conflicting/invalid POST 身份优先，不被坏导出覆盖。导出仍可独立解析，结果通过 `export_status=not_provided|valid|invalid`、`export_error_code`、`export_sha256` 表示；错误码仅为 `FEE_EXPORT_INVALID` 或 `FEE_EVIDENCE_CONFLICT`，否则为 `None`。

`candidate_matches=True` 仅表示本次 `candidate_for` 成功，不等于导入、全局 claim、任务完成或派发权限。候选金额 `candidate_platform_fee_cny` 使用六位小数字符串；`candidate_overrun` 表示候选超过该次风险额。未成功时两者及 `candidate_hash` 均为 `None`。零费用不证明完成或释放风险；晚到 POST 可保留直接费用事实，但其任务仍可为 `unconfirmed`。

`same_account_model_row_count` 是合法完整导出中 type=2 且 token/group/model 一致的 canonical 行数量；相同财务内容的重复行只计一次。未提供或无效导出时为 `None`。计数不按时间猜匹配，也不是映射证明。

回执元数据另含 `receipt_classification`、`request_id_status`、`captured_standard_header_present`、`body_request_id_present`、`accepted_task_id_present` 以及原生 operation ID、model 和证据哈希。presence 字段仅表示捕获字段/规范化身份是否存在，不代替其有效性校验。

`upstream_other_request_id_presence` 固定为 `unknown`：原 collector 未保留其他响应头，不能从缺少捕获的 `x-request-id` 推断上游没有任何其他请求标识。本模块不补造 header、映射表或费用身份。

native 结果另含 `native_status`、`fee_observed`、`native_platform_observed_cny`、`native_fee_candidate_conflict`、`query_count`、`query_status_counts`、`delivery_present`、`quality_status="not_reviewed"`、`scope_risk_cny` 和 `remaining_call_count`。

已有原生费用与当前有效候选按完整 `candidate_hash` 比对。冲突时返回 `fee_match_status=evidence_conflict`，同时保留 `candidate_matches=True`、当前候选金额，并用 `native_platform_observed_cny` 独立显示已观察金额；不覆盖旧费用。不具备双方可比条件时，冲突标志为 `None`。

`remaining_call_count` 仅为该 scope 的 `max_submissions - 已持久 operation 数`（下限 0），包含已预留调用的消耗；它不代表当前可派发，也不重置预算或图片版本。所有 `next_action` 都是只读建议，固定枚举为 `inspect_original_receipt`、`provide_original_export`、`review_direct_fee_candidate`、`await_exact_fee_evidence`、`inspect_existing_task`、`await_separate_dispatch_authority`，不会执行建议动作。

CLI 仅接受 `status`（可省略）及三个参数：

```sh
python -m core.execution.image_fee_inspection
python -m core.execution.image_fee_inspection status
python -m core.execution.image_fee_inspection status --control CONTROL --operation OPERATION
python -m core.execution.image_fee_inspection status --control CONTROL --operation OPERATION --export EXPORT
```

`CONTROL`、`OPERATION`、`EXPORT` 是操作者提供的本地参数占位符。无 `--control` 且无其他参数时返回 `mode="off"`；无 control 却传 `--operation` 或 `--export` 会拒绝。有 control 时必须提供原 POST operation ID，不能传 query ID。参数不包含 provider、model、任意 manifest、映射字段或启用开关。

CLI 的可选导出文件通过原私有路径读取 helper：拒绝与 backend/code、frontend/public 或控制声明的静态根重叠；校验 owner、0600、父目录、单链接、无 symlink，并限定 8 MiB。导出内容仍须通过原严格日志解析。

`observe_only`、policy 到期或代码清单漂移不妨碍合法历史只读诊断；原 control、父 proof、原生 source/scope 和 journal 完整性仍须成立。诊断不会续权，也不会因读取证据恢复 unknown 或重提请求。

正常 CLI 输出 JSON、退出码 0；坏参数或不可用输入返回固定 JSON 错误、退出码 1。纯 API 的坏 descriptor/source 抛 `IMAGE_FEE_INSPECTION_UNAVAILABLE`（503，固定 message）；native 可保留 `IMAGE_CONTROL_UNAVAILABLE`。不输出原 provider request/task ID、URL、token/group、原始 source/raw/export 或私有路径；输出哈希不提供导入权限。

新增模块进入 `core/execution` 的代码闭包。旧 control 的旧清单不能授权新增代码下的新派发；历史诊断仍可读。后续续接必须走另行明确批准的升级合同，本轮不实现升级，不改原 code manifest、parent allocation/request 或原 scope 预算与次数。

冻结源码、相关回归和真实只读验证见[验收记录](./IMAGE-FEE-INSPECTION-VALIDATION.md)。
