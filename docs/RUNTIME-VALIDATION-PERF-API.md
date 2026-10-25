# 图片历史校验与故障阶段 API · D35

本合同补充[图片 Operator](./IMAGE-OPERATOR-API.md)、[原生媒体执行](./MEDIA-EXECUTION-API.md)和[任务费用与代码续接](./TASK-FEE-CONTINUATION-API.md)。变更限于同次历史重建的纯 descriptor 证明复用、续接代码清单采集的调用边界，以及私有故障阶段证据。预算、次数、原授权、有效期、回执和 unknown 的原合同继续生效。

合同不代表运行时全部性能问题已解决，也不证明仍有效授权下的真实派发、图集质量或产品终验已经通过。测量和验收结论由独立验证报告记录。

## 同次历史重建的纯证明复用

`core.execution.store.validate_state(state)` 在一次同步调用中建立独立的 `_descriptor_snapshot()` 作用域，再完整执行原有 frame 链、namespace、版本和媒体约束校验。

`FrozenImageRequest` 的 `_checked(value)` 每次首先执行有大小上限的严格 `json_copy`，拒绝非 JSON 容器与值。作用域内的证明键为整个输入的稳定 JSON 序列化内容，不使用对象地址、调用方给出的 seal 或局部字段作为成功证明。布尔、整数、浮点数等不同 JSON 表示不能通过 Python 的宽松相等关系复用证明。

首次遇到输入时仍独立重建完整 POST 或 GET descriptor，包括原策略、permit、素材、manifest、引用和 GET 的原提交证据，再核对完整 seal。**只有成功重建的完整输入**才可在同次作用域内跳过重复纯重建；每次返回的值仍来自本次严格 JSON 拷贝。失败输入不进入证明集，修改任何字段后的输入必须重新校验。

证明集最多保留 **32 个不同成功输入、合计 8 MiB 序列化内容**；达到任一上限后，未保留的输入继续走完整校验。每次新的 `validate_state` 调用建立新集合，嵌套调用也建立自己的集合，结束或抛错时均通过 `finally` 恢复先前 ContextVar。

集合不保存 journal 的有效结论、授权、当前时间、撤销状态、文件系统或凭证结果，不跨请求、重启或独立历史快照缓存权限。完整历史 frame 校验和业务门禁没有被省略。

## 代码清单采集与授权校验

`current_code_manifest(control)` 仍是独立入口：要求原 `LoadedImageControl`，先调用其完整历史权限校验，再从实际服务器文件收集代码闭包；不接受调用方提供的文件 hash。

内部 `_collect_code_manifest(control)` 只执行实际文件读取、大小与闭包检查，不授予权限，也不保留已核 snapshot。代码续接的 quote、activate 和派发校验仅在**同次决策已完成 `_eligible(control)` 的当前权限重建后**使用该采集函数，避免为纯清单读取重复调用同一完整权限入口。

每次决策仍重新读取原证明、journal、grant、最新 activation、原有效期、额度和实际代码。原 Operator 在加载 Key **之前和之后**仍分别执行 control 与原生 reservation 的独立当前校验，并检查原 deadline；后面的受控采集也保留原 authority、环境和时限检查。一次决策的成功不能被后续请求借用，读取代码清单不能解除过期、撤销、unknown、待核费或超额阻断。

## 固定故障阶段证据

`core/execution/media_failure_phase.py` 提供内部接口：

```python
CaptureTrace().active()
capture_stage(stage)
record_failure_phase(execution_root, native_record, stage)
checked_failure_phase(value, native_record)
read_failure_phase(execution_root, native_record)
failure_projection(execution_root, native_record)
```

`MediaExecution._run` 只在原生 submitting 帧提交后，为本次同步图片 capture 建立 `CaptureTrace`。固定 hook 记录最近进入的观察阶段；退出时通过 `finally` 恢复先前上下文。没有此同步图片作用域时，hook 不创建状态或文件，包括原有文本 capture 路径。

固定阶段如下。它表示 observer 到达的代码边界，不能单凭阶段推断 HTTP 是否发送。

| 边界 | `stage` |
| --- | --- |
| 进入采集 | `capture_entry` |
| Key 前的 control、原生 reservation、时限检查 | `control_before_key`、`native_before_key`、`deadline_before_key` |
| 选择/加载凭证及检查 | `credential_load`、`credential_check` |
| Key 后的独立检查 | `control_after_key`、`native_after_key`、`deadline_after_key` |
| 受控图片采集的 descriptor、权限、凭证、时限、环境 | `image_descriptor`、`image_authority`、`image_credential`、`image_deadline`、`image_environment` |
| collector 输入、持久 claim 与既有 claim | `collector_ipc`、`collector_prepare`、`collector_existing_claim` |
| worker 启动、输入投递、回执观察与观察结束 | `worker_launch`、`worker_feed`、`collector_observe`、`collector_close` |

capture 抛出异常时，原 catch 仍把 operation/query 保存为 unknown；新增步骤只尝试封存独立私有 sidecar。未捕获失败或成功采集不创建 sidecar，读取缺失证据不创建目录。原生 journal、descriptor、permit、原始 HTTP envelope 和 normalized receipt 的 schema 均不增加诊断字段。

sidecar 每个 operation/query 只有一个固定文件名，位于原执行根目录的独立 `media-failures` 子目录。其 schema 为：

| 字段 | 约束 |
| --- | --- |
| `schema_version`、`kind` | 严格整数 `1`、`native_image_failure_phase_v1` |
| `binding`、`binding_hash` | 由完整原 descriptor 独立重建的完整图片 binding 及其 hash |
| `native_record_kind`、`scope_id` | 精确绑定原 operation/query 类型和 scope |
| `capture_started_at` | 与原生记录完全相同，处于原 permit 签发之后及当前时间之前 |
| `stage` | 上表固定枚举之一 |
| `recorded_at` | 不早于原 capture start、不晚于校验当前时间的带时区时间 |
| `observer_stage_only` | 严格 `True` |
| `http_outcome`、`fee_outcome` | 均固定为 `unknown` |
| `retry_authorized` | 严格 `False` |
| `evidence_hash` | 完整诊断对象的 seal |

写入只接受当前 `status=submitting` 的原生记录；不能为已经 unknown、accepted 或仍 reserved 的历史记录补造阶段。首次写入使用原私有 ReceiptStore 的原子首写方式；重复调用保留并验证首份文件，不覆盖阶段或时间。目录要求当前用户、0700；文件要求当前用户、0600、单链接、无 symlink，并与静态目录隔离，读取上限 16 KiB。

`checked_failure_phase` 是纯校验：严格 JSON、精确字段集、seal、完整 descriptor 重建、完整 binding/hash、原生关联和固定结果字段均须成立。重封别的 binding、仅改 hash 或利用 `0.0`/`False` 与整数相等不能通过。诊断不保存任意异常类型或文本、Key、请求体、脚本、URL、响应内容或实际私有路径。

诊断写入失败会被隔离，随后仍执行原 unknown 持久化。它不释放风险、恢复次数、生成费用观察、把原操作归类为零费用，或授予重发权限；缺少 collector 文件也不能补充这些结论。历史证据缺失继续保持缺失，不按新代码推断当时的阶段。

## 只读 status 投影

沿用原 Operator 的显式命令，以下名称均为占位符：

```sh
python -m core.execution.image_operator --control CONTROL status --operation OPERATION
```

该 status 路径读取原 `ExecutionStore`，独立验证 sidecar 与所选原生记录的绑定，不构造执行 session、不加载 Key、不启动 collector，也不运行 recover 或 HTTP。原 control 的有效性和原业务门禁仍单独投影。

`operation.failure_diagnostic` 及相关 `queries[*].failure_diagnostic` 在无记录时为 `{"status": "missing"}`；通过验证时为 `status=observed`，仅包含 `stage`、`recorded_at`、`observer_stage_only`、`http_outcome`、`fee_outcome`、`retry_authorized` 和 `evidence_hash`。不输出完整 binding 或 sidecar 路径。

坏证据按固定 `MEDIA_FAILURE_EVIDENCE_UNAVAILABLE` 安全错误处理，不返回任意异常细节。`observed` 只表示有可验证的阶段观察，原 operation/query 状态保持独立；`automatic_followup_calls` 仍为 `0`。本合同没有自动重试、续期、新授权、新 scope 或自动下一镜。
