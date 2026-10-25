# 显式图片 Operator API（D32）

本切片为已接受的原生计划和已核费的原生素材设计结果提供独立图片控制文件、Python 装配入口和 CLI。它复用原 `ExecutionRuntime`、`ExecutionStore`、任务锁、媒体 namespace、detached receipt collector 与费用 claim，不建立第二个执行引擎或账本。

普通应用启动、旧默认工厂和文本执行仍保持原配置。本切片不接入普通 bootstrap，不新增图片写 HTTP API、UI、定时查询或费用观察器。读取文件、设置 `mode=enabled`、创建 quote/grant 均不会自动开始生成。

本文描述公共接口；冻结源码、完整检查及真实执行记录见[验收报告](./IMAGE-OPERATOR-VALIDATION.md)。下列配置限制不是实际消费或最终账单。

## 入口与默认行为

Python 入口：

```python
read_image_control(path, backend_root, *, static_roots=())
build_image_session(control, *, receipt_capture=None, credential_loader=None)
```

`read_image_control` 返回 `LoadedImageControl`，提供：

| 接口 | 含义 |
| --- | --- |
| `document`、`control_hash`、`path` | 严格控制文档、控制文件原字节 SHA-256、原路径 |
| `roots` | `creator`、`planner`、`execution` 三个原私有 store 根目录 |
| `verify_authority(backend_root=None, *, for_dispatch=False)` | 重新验证控制与证明；成功返回严格 `True`，失败抛 `IMAGE_CONTROL_UNAVAILABLE` |
| `guard(scope)` | 对实际原生图片 scope 做当前派发校验；仅成功时返回严格 `True` |
| `verified_asset()` | 从原生素材 operation 的实际 receipt、费用和 claim 重建 `{manifest, origin}`；不授予派发权限 |
| `check_private(static_roots=())` | 检查控制、证明、store 路径与实际静态目录的隔离 |

CLI 入口为 `python -m core.execution.image_operator`。必须显式给出 `--control`；没有该参数时，仅 `status` 返回 `mode=off`、`dispatch_available=false`、`automatic_followup_calls=0`。旧 `LEONTV_EXECUTION_CONTROL_FILE` 不会隐式启用图片控制。

| 控制状态 | 可执行行为 |
| --- | --- |
| 无控制文件参数 | 只读 off 状态 |
| `observe_only` | 只读 `status`；拒绝所有写命令，包括 recover、导入与 revoke |
| `enabled` 且当前权限完整有效 | 可显式 quote、grant、reserve、start、query；每个阶段独立验证 |
| `enabled`，但代码漂移、策略过期或父分配不再可派发 | 在历史证明仍成立时，可读状态并执行本地 recover、费用导入、delivery 导入、revoke；不得新 quote/grant/reserve/start/query |

`build_image_session` 构造的原 `ExecutionRuntime` 仍为文本 disabled、无 runtime API key。用户请求只能提交操作 ID、shot ID、幂等 key 和同意的 disclosure hash 等 metadata，不能选择任意 provider、model、prompt、wire、manifest、参考图或调用额度。

## 控制文件与父证明

控制文件的 `schema_version=1`、`kind=image_execution_control_v1`，拒绝未知字段、重复 JSON key、类型归一化和不完整冻结对象。

| 字段 | 冻结内容 |
| --- | --- |
| `mode` | `observe_only` 或 `enabled` |
| `roots` | 三个绝对、规范、互不包含的原 store 路径 |
| `admission` | task/source/brief 的精确版本；接受计划、接受报告、handoff、provenance；以及 `asset_operation_id`、`asset_manifest_hash`、`asset_proof_hash` |
| `policy` | 完整 `ImageToolPolicy.frozen()`；模型固定 `gpt-image-2-all`，9:16、1k、medium；生成次数、查询次数、请求/响应大小、deadline、每次经验风险、有效期均由服务器冻结 |
| `budget_anchor` | 完整 `MediaBudgetAnchor.frozen()`，继承原 100 CNY、历史完整预留与保守调用数，不用实际调用数或费用重置 opening |
| `scope_envelope` | `image_execution_scope_envelope_v1`，含原预算、opening、scope 与累计风险/次数及 `envelope_hash` |
| `fee_source` | 完整 `FrozenFeeSource`；固定账户 namespace、token、group、每 CNY 500000 quota |
| `authority` | 原授权路径/raw SHA、父 ledger 路径、selected allocation key、独立 begin 路径/raw SHA |
| `code_manifest` | 后端相对路径到实际文件 SHA-256 的完整运行闭包映射 |
| `reference_profile` | 可省略或为 `null`；显式启用时必须是完整冻结 profile |

私有文件必须由当前用户拥有、权限为 `0600`、单硬链接，直接父目录由当前用户拥有且不可被 group/other 写入。路径的任意层级不得经过 symlink；控制与证明不得位于 store 内或静态发布目录。控制文件上限 2 MiB，证明和导入文件各有独立上限；失败输出不包含路径、原始内容或密钥。

父证明同时验证原授权的 raw SHA 与 JSON fingerprint，原授权状态、策略 origin（live 固定 Tudou）、CNY/100 总额，父 ledger raw SHA、授权 fingerprint、原 limit，以及 selected allocation 的对象 digest、request digest 和风险金额。父 ledger 的其他完整预留之和必须等于 opening；历史普通记录按至少一次、新 scope 按其 `max_new_calls` 保守计数。未使用的既有分配同样保留，不因未发生 HTTP、撤销或费用较低而消失。

新父 ledger 的其他分配中，必须存在与实际 D31 素材 scope `parent_allocation_hash` 完全一致的原素材 allocation，且为 `asset_design_scope_v1`、1 CNY/1 call。新 opening 至少继承该素材 scope 的完整累计上限；`76.37/48` 是代码的最小下界，不是固定当前余额。首次图片 scope 出生还必须覆盖原共享 `scope_envelope`；已有合法 scope 继续使用其出生帧证明，后续其他 scope 不倒算或释放其风险。

新 selected request 使用 `kind=image_execution_scope_v1`，绑定以下字段：

```text
model, endpoint, query_endpoint_prefix, source_hash, admission,
policy_hash, max_new_calls, max_queries, per_submission_risk_cny,
scope_risk_cny, risk_basis, scope_envelope_hash, backend_manifest_hash,
fee_source_hash, account_scope_id, asset_operation_id,
asset_manifest_hash, asset_proof_hash, manifest_origin_hash,
reference_profile_hash
```

`endpoint` 固定为策略 origin 下的 `/v1/images/generations/async`，`query_endpoint_prefix` 为 `/v1/tasks/`。`manifest_origin_hash` 是重建的 `origin.origin_hash`；无 profile 时 `reference_profile_hash` 仍需显式为 `null`。派发另要求 `scope_status=open`，selected allocation 为 `reserved`，且没有 provider task 或 actual charge。

独立 begin 文档严格为：

```text
schema_version=1
kind=image_execution_allocation_begin_v1
operation_key=selected operation_key
allocation_hash=digest(selected allocation)
request_sha256=selected.request_sha256
status=begun
begun_at=带时区、非未来的 ISO 时间
```

begin 的 raw SHA 冻结在 authority，对象 digest 冻结在 anchor；begin 不写回 selected，避免循环 hash。控制/CLI 不修改旧授权、父 ledger 或 begin 文件。

代码闭包覆盖原 execution/planner/planner_live 目录和固定关键文件，并加入素材来源桥、图片控制/CLI、媒体、图片收据/费用、引用转换等依赖。声明一个无关文件或只哈希存在的文件不够；固定要求的 package initializer 也必须实际分发。派发时映射 digest 必须与父 request 一致，每个实际文件 SHA 逐项匹配。代码漂移只能保留可验证的历史读取，不能获得新派发权。

## 原生素材来源与引用

图片控制不接受手填 manifest。它从 admission 指定的原生素材 operation 调用原 `verified_manifest`/`manifest_origin`，重建实际 HTTP 输出、素材结构、费用 export/candidate、全局 claim 与 manifest proof，要求已经 `manifest_ready`。task、source、接受计划/报告、账户、原授权及 fee source 均须闭合。

`asset_manifest_hash` 取实际 manifest 的 `manifest_hash`，`asset_proof_hash` 取重建 proof 的 `proof_hash`。图片 scope 同时冻结完整 `manifest_origin`；scope identity 包含该 origin 的完整指纹，不能用同内容的手填 JSON 或其他素材 operation 替换。

首次图片 scope 使用当前完整原生 handoff。已有 scope 按 policy、anchor、manifest、origin、profile 精确匹配，复用出生时冻结的 handoff，并独立核对当前历史接受证据的稳定部分。因此原文本 grant 后续撤销、其他任务计数变化或后续 scope 不会改写已有图片身份。尚未出生图片 scope 的控制文件若原 handoff 已变化，仍拒绝加载/派发，应重新按当前合法证明冻结。

参考图只来自同图片 scope 内 manifest 声明的前序 shot，必须已经有可验证的 completed query 和导入 delivery。`reserve` 没有上传任意参考图的参数。

可选 `reference_profile` 将转换授权同时冻结在控制、父 request 和 scope identity 中。未启用时保留原 PNG/JPEG 直接引用路径；启用时记录原字节/像素、目标 job、encoder、转换和输出 hash。先保留合格 PNG/JPEG，或做无损 PNG 表示；仅在 `allow_lossy_jpeg=true` 时允许固定质量 85/75/65 的有界本地尝试。不会裁剪、缩放、旋转或拍平透明度；引用 wire、嵌套 GET descriptor 和 collector IPC 的容量在 reserve 写 intent 前检查；不满足即停止，不增加远程生成或隐式降级。转换证明不等于视觉质量验收。

## CLI 参数与效果

`--control /绝对私有路径/image-control.json` 可放在子命令前或后。各命令都可给出 `--task TASK`，但它必须等于 admission 的生成 task。CLI 不提供创建父 allocation、自动同意或动态改策略的命令。

| 命令及参数 | 效果 |
| --- | --- |
| `status [--operation OP]` | 安全只读 metadata；不给完整 manifest、故事、raw、URL、wire、私有路径或 key |
| `quote --key KEY` | 冻结/复用图片 scope，记录 disclosure；完整 scope 风险从此保留，不发 HTTP |
| `grant --quote Q --disclosure-hash HASH --consent --key KEY` | 仅对精确 quote/hash 记同意，不开始生成 |
| `reserve --grant G --shot SHOT --key KEY` | 在原 journal 中冻结一次 POST intent、引用和 permit，消耗本地生成次数预留，不发 HTTP |
| `start --operation OP` | 显式开始该原始 POST；已记 start 的 operation 重放，不再次 POST |
| `query --operation OP --key KEY` | 为已 accepted 的原 operation 发一次绑定原 task 的 GET；同 key 重放同 query，新 key 才创建下一条有界 query |
| `recover --operation OP_OR_QUERY` | 只接纳原 descriptor 对应的已持久 raw；不重新 POST/GET |
| `fees-import --operation OP EXPORT_JSON` | 导入实际 POST 的原平台 export，记录费用观察和唯一 claim |
| `legacy-fees-import --operation OLD_TEXT_OP EXPORT_JSON` | 原 native text ordinal alias 的费用桥接，不改旧费用 evidence |
| `delivery-import --operation OP --query QUERY IMAGE_FILE` | 将本地图片字节绑定原 completed query，记录 delivery hash，不自动下载 |
| `revoke --grant G` | 撤销图片 grant，不释放风险、不清空历史次数 |

`recover` 的 `--operation` 可填 query ID；`status --operation` 则要求图片 operation ID。`legacy-fees-import` 可指向同账户、同原授权的另一个原 native task；生成 admission 不因此放宽。其原 export、固定 fee source 和原费用证据必须精确对应。

示例仅展示调用顺序，所有路径/ID/key 均应换为操作员已有的私有值：

```sh
python -m core.execution.image_operator status
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json quote --key quote-image-001
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json grant --quote QUOTE_ID --disclosure-hash DISCLOSURE_HASH --consent --key grant-image-001
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json reserve --grant GRANT_ID --shot S01 --key reserve-s01-001
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json start --operation OPERATION_ID
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json fees-import --operation OPERATION_ID /ABS_PRIVATE/post-export.json
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json query --operation OPERATION_ID --key query-s01-001
python -m core.execution.image_operator --control /ABS_PRIVATE/image-control.json delivery-import --operation OPERATION_ID --query QUERY_ID /ABS_PRIVATE/frame-s01.png
```

CLI 成功退出码为 0，错误为 1；错误仅输出安全 code 与 `automatic_followup_calls=0`。首次 `start` 与每次新 `query` 都可能发一个真实网络请求。CLI 没有自动下一镜、自动轮询、自动费用导入或自动 repair；所有结果带 `automatic_followup_calls=0`。状态中的 `control_authority_valid`、新生成 `dispatch_available` 和指定 operation 的 `query_available` 是分别计算的 metadata，不能替代后续动作的重新校验。

## 密钥、截止时间与恢复

只有实际网络 capture 才延迟加载 Tudou key。live 模式固定校验 Tudou origin，禁止测试 worker factory/options；不把 key 注入原文本 runtime。loader 调用前后都分别重验控制、原生持久 operation/query、descriptor、scope、grant 和 deadline，随后仍使用原受控 capture。loader 获得字符串不等于获得派发权限。

`reserve` 生成 permit 的 `issued_at` 时开始计时：`deadline_at=min(issued_at + policy.request_deadline_seconds, policy.valid_until)`，配置最多 120 秒。应在 reserve 后立即显式 start；重启、重放或再次读取不会刷新该时间。过期的未开始 intent 不发 HTTP；已开始但 raw 不可确认时保留 unknown 与风险，不能通过新 grant、另一 shot 或新 key 绕过账户阻断。晚到 envelope 继续受原截止与 binding 检验，不会自动变成可查询的 accepted task。

每次 `verify_authority` 或 `guard` 只独立读取一次原生 journal snapshot，并在该次校验内复用接受证据、素材证明和 opening 计算。不存在跨命令、跨请求或全局私有缓存。优化没有合并 loader 前后的两次验证，source、control、parent proof、code 文件仍在每次校验中重新读取。

恢复复用原 `ReceiptStore` 中原 capture ID/binding 的 durable raw。缺少 raw 时不猜测提交失败，也不生成新提交；已有 raw 时按原 descriptor 分类。scope 内每个 shot 只能有一个 submission，grant 替换或撤销不会重置 scope 次数。任何后续网络动作仍需当前有效的显式权限，已有历史证据本身不授予调用权限。

## 费用、交付与未完成的验收

三种身份必须分开：POST 的 `submit_request_id`、异步生成的 `accepted_task_id`、GET 的 `query_request_id`。GET 只查询原 POST 接受的 task，查询成功或拿到图片 URL 不等于观察到该 POST 的费用。

图片费用仅用实际 POST receipt 的 `submit_request_id` 精确匹配平台 export；缺少/冲突的 request ID 不以时间、task ID 或列表序号替代。还核对固定 token/group/model、时间窗口、raw/descriptor 和 export hash。平台 request ID 转成稳定 claim key，与原 text、素材和媒体 claim 一起去重；另一 operation 不能复用同一费用记录。

费用 export 上限 8 MiB。同 export 重放复用第一次 `observed_at`，不新造当前时间导致证据冲突。并发同 export 以原已接受的时间重放；不同 export/候选不能覆盖已有观察。legacy 导入只新增可验证的原 text claim，不原地改写旧 ordinal 证据、不续跑旧文本。

平台观察金额按 `quota / 500000` 计算。观察不是最终账单、结算或风险释放；超额事实仍持久保留并阻断账户新收费派发。未知、待核费及旧 alias 未桥接同样阻断新收费 intent。正常费用低于预留、grant 撤销、策略过期与操作完成都不归还已保留风险。

delivery 导入文件上限 2 MiB，沿用私有路径、权限、单链接与静态隔离检查；校验原 completed query、PNG/JPEG/WEBP、单帧、9:16 和实现限定的尺寸/像素数，再记录导入字节 hash。该动作不发下载请求，也不生成新图片。`visual_quality=not_reviewed`；结构、收据、费用或 delivery 完整均不能替代视觉、剧情一致性或人工验收。

合成验收、真实调用及费用分别记录在[验收报告](./IMAGE-OPERATOR-VALIDATION.md)，不能从合成用例推断真实生成或视觉质量。

## 对应公共源码

- `core/execution/image_control.py`：控制、父证明、admission、单次 snapshot 与原生素材来源校验。
- `core/execution/image_operator.py`：显式 factory、CLI、安全投影、loader 前后验证与本地导入。
- `core/execution/media_runtime.py`、`media_ledger.py`：原生媒体 scope/intent/query/recovery/fee/delivery 与历史身份。
- `core/execution/asset_media_handoff.py`、`asset_design_ledger.py`：实际素材 receipt/fee/claim/manifest proof 重建。
- `core/execution/image_fees.py`、`image_receipts.py`：POST 费用身份、GET 状态与交付解码。
- `core/execution/reference_derivation.py`、`scope_budget.py`：有界本地引用表示、共享完整预留和唯一费用 owner。
- `tests/test_control_code_package.py`：实际分发包必须包含固定代码权限闭包文件。
