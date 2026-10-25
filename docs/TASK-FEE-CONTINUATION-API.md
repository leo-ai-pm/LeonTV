# 原生任务费用与代码续接 API

本合同在[原图片 Operator](./IMAGE-OPERATOR-API.md)上增加两条独立路径：按已接受的原生任务认领平台 quota 观察，以及在原 scope 内明确批准新的代码闭包。费用认领不授予执行权限；代码批准不启动生成。两条路径均使用原 `ExecutionStore`、原任务锁和原媒体 journal，没有第二套账本或执行器。

## 原生任务 quota 证据

`core/execution/task_image_fees.py` 提供纯函数：

```python
FrozenTaskFeeSourceV1(...).frozen() -> dict
checked_task_source(value) -> dict
parse_task_export(raw_bytes) -> dict
candidate_for_task(
    request, submit_raw, task_export_bytes, account_export_bytes,
    fee_source, task_source, *, observed_at,
) -> dict
verify_task_candidate(
    candidate, request, submit_raw, task_export_bytes, account_export_bytes,
    fee_source, task_source,
) -> bool
claim_keys(candidate) -> tuple[str, ...]
```

`request` 必须是可独立、完整重建的 `FrozenImageRequest` POST。原 `submit_raw` 必须按原 deadline 得到 `accepted`，且包含有效的原生 `accepted_task_id`。缺失的直接 POST request ID 可以继续保持缺失；冲突、无效、晚到或未确认的提交不能通过本路径升级为 accepted。

两个 export 均为完整原始 JSON 页的 bytes，分别来自 `/api/task/self` 和 `/api/log/self`。每页上限 8 MiB，严格校验 envelope、分页、完整行数、字段和整数类型；不能拿部分列表或手填费用表替代。任务页按外层 `task_id` 精确匹配原回执，要求 `SUCCESS`、`generate`、`gpt-image-2`、原 model/group 和固定用户身份一致，并检查任务时间与原 permit、回执及观察时间一致。嵌套上游结果中的 ID 不作原生身份或费用请求身份。

`FrozenTaskFeeSourceV1` 是服务器预先固定的账户来源，字段为：

| 字段 | 绑定 |
| --- | --- |
| `schema_version`、`kind` | 整数 `1`、`native_image_task_fee_source_v1` |
| `account_namespace` | 原预算账户 namespace |
| `fee_source_hash` | 原 scope 的完整 `FrozenFeeSource` 指纹 |
| `platform_user_id` | 固定的平台账户正整数 ID |
| `account_export_hash` | 完整原始账户日志页的 SHA-256 |
| `origin` | 与原 descriptor 相同的策略 origin |

`.frozen()` 另返回 `task_fee_source_hash`。读取带 seal 的对象时会独立重建并核对 seal，拒绝未知字段和类型归一化。

账户日志页只证明原固定 token/group 对应的所有行均属于已固定的用户；其完整 schema、重复记录和原始 SHA 仍须有效。**不使用该页的 request ID、时间、金额、模型相似性或唯一行数量建立 task 与消费行的映射。** 因此任务 quota 与消费页金额不能相加，也不能把账户页 request ID 补入原 POST。

候选固定为 `schema_version=2`、`kind=image_task_fee_candidate_v1`、`proof_basis=exact_native_task_quota`。它绑定原 operation/task、descriptor、原 POST raw/receipt、完整任务行、两份 export、两种来源指纹和观察时间。金额为 `quota / 500000`，以六位小数字符串保存；`observation_kind=platform_task_quota_observation`。

候选保持 `global_claim_status=not_claimed`，以及 `completion_verified`、`provider_bill_confirmed`、`settlement_performed`、`risk_released` 均为 `False`。`SUCCESS` 是平台任务行的必要条件，不改写原生 operation 的状态，也不替代交付、视觉或人工验收。

## 认领、去重与重放

本地导入入口为：

```python
import_task_fee(
    store, task_id, operation_id, task_export_bytes, account_export_bytes,
    task_source, *, observed_at,
)
MediaExecution.observe_task_fee(
    task_id, operation_id, task_export_bytes, account_export_bytes,
    task_source, *, observed_at,
)
```

入口取得原任务锁，在同一个原 store transaction 内完整重建证据，原子追加一条媒体 `fee` 和一条对应 `claim`，并把原 operation 的 `fee_id` 从 `None` 绑定该记录。原 POST raw、receipt、descriptor、状态、查询、交付和 scope 均保持原值。

任务主身份为 `tudou-task-sha256:` 加原生 task ID 的 SHA-256，不按 scope 分区。只有原 POST 实际提供了直接 request ID，才另加入原 `tudou-request-sha256:` 别名。旧 `image_fee_candidate_v1` 也会提供其已知原生 task 次身份。所有可用身份在原 journal 的文本、素材和媒体费用 owner 之间统一去重；owner 包含所属类型，不能利用相同局部 operation ID 跨 namespace 借用费用。

同一原 operation 的相同双 export/source 重复导入，会在锁内复用首次保存的 `observed_at`，精确验证后返回 `replayed=True`，不增加 frame。金额、原字节、source 或任一绑定变化均停止并保留原观察。同一 operation 已采用旧直接请求证据时，也不能用任务证据改换或覆盖它；两个视图不能形成两笔费用。

零 quota、正常费用、撤销、到期和完成都不释放原完整风险预留。超额事实仍可保存，随后阻断账户新的收费派发；旧 unknown、其他待核费和未完成的旧费用身份桥接仍独立阻断。认领不会恢复 unknown、重提请求或解除所有执行门槛。

CLI 示例中的路径、ID 和 hash 均为已有私有值的占位符，应在 backend 目录运行：

```sh
python -m core.execution.image_operator --control CONTROL task-fees-import TASK_EXPORT \
  --operation OPERATION --account-export ACCOUNT_EXPORT --source TASK_FEE_SOURCE
```

该命令要求显式 `enabled` control，使用原历史 admission/scope 校验；代码漂移或 policy 到期不妨碍仍可验证的费用事实追加。命令在构建 session 前直接使用原 store，不构造执行 runtime、不加载凭证、不发 HTTP、不运行 collector 或 recover。它不授予新的调用权限。

三个输入文件沿用私有路径 helper：当前用户、0600、单链接、无 symlink、父目录不可被他人写入，且与静态目录隔离；来源文件上限 16 KiB。成功 JSON 仅含原生 operation 安全 metadata、重放状态、观察类型/金额和固定边界字段，不输出原始 export、平台 request/task ID、token/group、URL 或私有路径。坏参数或不可用证据返回安全错误 code，退出码为 1；成功为 0。

[D33 只读诊断](./IMAGE-FEE-INSPECTION-API.md)仍按原直接请求合同报告缺失的 POST ID。新增任务观察不会伪造该 ID，也不会改变旧 `image_fees.candidate_for` 的匹配规则。

## 原 scope 的代码续接

`core/execution/image_code_continuation.py` 提供：

```python
quote_code_continuation(control, grant_id)
activate_code_continuation(control, quote_id, *, disclosure_hash, consent)
ContinuedImageControl(base_control, activation_id)
```

仅原 `enabled` control、仍在原 policy 有效期内、原父 allocation 保持 open/reserved 且原 grant 未撤销时，才可提出或激活续接。不能用续接刷新过期策略、补造父授权或更换 allocation。

quote 从服务器实际文件收集新代码闭包，保留原完整清单，记录代码差异；原清单中的文件缺失会拒绝。它同时固定原 control、scope、grant、预算 anchor、父 allocation、source/plan/manifest/origin、引用 profile、fee source、原 `valid_until` 和 journal checkpoint。剩余次数来自原已持久 operation/query，包含已 reserve 的调用；这些值是 quote 时的快照。quote 的新增分配恒为 `new_paid_calls=0`、`new_risk_cny=0.000000`，不消费或重置原额度。

激活要求精确 `quote_id`、`disclosure_hash` 和严格布尔 `consent=True`。在原任务锁和原 transaction 内，当前 journal 必须仍紧接该 quote 的出生 frame，payload、剩余计数、grant、实际代码及前一 activation 均须一致；期间其他写入会使该 quote 失效，需重新取得当前 disclosure。不能只提交一个自行声明的批准标志或当前文件 hash。

quote/activation 作为不可变记录保存在原媒体 namespace。完整 journal 重建同时验证其出生 frame、checkpoint head 坐标、前后 frame hash、完整 payload 和连续 activation 链。成功重放原 activation 不新增 frame；每个 scope 仅最新 activation 可授权当前代码，旧 approval 不能通过切回旧代码恢复权限。

`ContinuedImageControl` 每次派发仍重新检查原父证明、精确 scope/原 grant、当前代码闭包、最新 activation、原有效期和原已用次数。scope 内其他预存 grant 不能借用该续接；已有其他 grant 的 reserved/submitting intent 也不能混入。撤销原 grant 立即阻断后续派发。激活后不允许创建新的原图片 quote/grant，新 operation/query 必须继续使用披露的原 grant。

续接保留原 source/plan/image 版本、引用来源和转换授权、原 scope cap、查询 cap 及全部历史记录；不修改原控制文件、父 request 中的旧 manifest、原 allocation、scope policy、grant 或已有 permit。已有 intent 的 `deadline_at` 不刷新；新 intent 仍按原 policy 的请求时限及 `valid_until` 取截止时间。未知、待核费、超额、前序交付和引用等原门槛继续独立生效。

```sh
python -m core.execution.image_code_continuation status
python -m core.execution.image_code_continuation --control CONTROL quote --grant ORIGINAL_GRANT
python -m core.execution.image_code_continuation --control CONTROL activate \
  --quote CODE_QUOTE --disclosure-hash DISCLOSURE_HASH --consent
python -m core.execution.image_code_continuation --control CONTROL status --activation ACTIVATION
python -m core.execution.image_operator --control CONTROL --continuation ACTIVATION status
```

后续 `reserve`、`start`、`query` 等仍须按[原 Operator 命令](./IMAGE-OPERATOR-API.md)分别调用，并显式携带 `--continuation ACTIVATION`；省略后继续使用原 control 的原代码权限。续接状态中的 `control_authority_valid=True` 不代表费用、unknown 或引用等业务门槛均已通过，续接 CLI 固定 `dispatch_available=False`，不会代发请求。

两个 CLI 均默认 off，不从旧环境指针隐式启用。续接 CLI 的成功/错误退出码为 0/1，错误为 `IMAGE_CODE_CONTINUATION_UNAVAILABLE`；输出仅含安全批准 metadata。导入、quote、activate、status 和原 Operator 结果均保持 `automatic_followup_calls=0`，没有自动下一镜、轮询、重试、替换模型或返工。

共享预留、原生媒体记录与其余边界见[媒体执行 API](./MEDIA-EXECUTION-API.md)；实际运行与验收结果由单独验证报告记录，不能从本合同推断真实调用、视觉质量或最终产品验收。
