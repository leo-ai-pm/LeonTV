# 图片有效期续接授权 API · D37

本合同在[只读预审 D36](./IMAGE-CONTINUATION-PREFLIGHT-API.md)之上，定义原图片任务的版本化披露、持久预算意图、真实父账本追加和原生 activation。它复用原 `ExecutionStore`、任务锁、帧链和账户预算。**本阶段只完成授权与本地风险预留，activation 始终为 `dispatch_ready=False`、`runtime_contract_pending=True`。** 原图片 runtime 不把这些记录当成新派发许可。

本文描述接口与验收边界，不把代码完成、合成测试或计划数值表述为真实执行结果。是否实际写入原任务、追加父分配、通过哪些检查，应以本阶段独立验收记录为准。

## 原记录与本阶段范围

原 control、scope、grant、源与内容版本、unknown、原 HTTP 证据、原有效期和旧 permit/deadline 保持原值。原 unknown 继续占用一次图片调用并保留风险；缺少 collector 证据不证明零 HTTP 或零费用。本阶段不重试旧请求、不补写历史故障阶段、不签发新图片 permit、不创建图片 operation，也不自动启动图像、视频、音频或视觉 Reviewer。

针对 D36 已确认的四镜输入，合同关系如下。右列为续接意图成立后的保留值，不等于本阶段已实测完成。

| 项目 | 原输入 | 本阶段合同 |
| --- | --- | --- |
| 原总预算 | ¥100 | ¥100 |
| 父账本完整旧 allocations | 45 条 | 精确保留，最多追加本 proposal 唯一 1 条 |
| 账户保留风险 | ¥81.37 | durable intent 后 ¥82.37 |
| 保守调用包络 | 53 | durable intent 后 54 |
| 图片已占用意图 | 2，含一个 unknown | 仍为 2，不释放旧意图 |
| 图片剩余目标 | 3 个 job | 继承剩余 2 个槽位，加保留 1 个新槽位 |
| 图片累计计划上限 | 4 | 5；尚不能用于派发 |
| 原费用与质量 | 平台费用观察、质量另行验收 | 不改作最终账单或质量通过 |

这是本地保守风险预留，不代表平台资金冻结、实际消费或已经确认的计费硬上界。父分配、授权 activation 与实际模型 POST 分别记账，不能混作已付费调用。

## 持久化顺序

```text
原状态 → quote → durable reservation intent → 原父账本 old + 1 → activation
            无新增风险           立即保留 ¥1 / 1 call              dispatch=False
```

三个原生记录分别使用 `validity_continuation_quote`、`validity_continuation_intent`、`validity_continuation_activation`，保存在原 `media` 命名空间。每条记录沿用 `schema_version`、`kind`、`id`、`scope_id`、`task_id`、`created_at`，并固定携带 `dispatch_ready=False`、`runtime_contract_pending=True`。

- **Quote**：服务端重新构造完整 D36 proposal，并冻结外置父证据；原生记录保存 proposal 与小型 `parent_evidence_ref`。quote 本身不增加账户包络。
- **Prepare**：要求精确的 quote ID、proposal hash 和严格布尔同意，在原任务锁与单次原生事务中追加 intent。该 intent 立即使共享 `scope_envelope` 保守增加 ¥1 / 1 call；这一步尚未写父账本。
- **Activate**：先确认 intent 仍对应当前原生 head，预检后续记录容量；再以服务端固定路径追加或重用唯一父 allocation，验证真实父文件与小型 begin/proof，最后在原任务锁和原生 checkpoint CAS 下追加 activation。

同一 scope 最多存在一个此类 intent 和一个 activation。quote 的 birth 紧接其披露 checkpoint，intent 的 birth 紧接选定 quote，activation 的 birth 紧接对应 intent。每个 birth 帧只追加对应记录，不允许顺带修改原媒体或其他命名空间。重放沿用原 ID、原同意、原时间和原分配，不构造第二个 intent 或第二笔 ¥1。

## Python 接口

### 原生协调器 `image_validity_continuation`

| 接口 | 作用 |
| --- | --- |
| `quote_continuation(base, *, valid_until)` | 从原 `LoadedImageControl` 与真实当前输入构造并持久化 quote；返回 `quote`、`replayed`、`automatic_followup_calls=0` |
| `prepare_continuation(base, quote_id, *, proposal_hash, consent)` | 精确同意后持久化 intent，立即保留新增风险和调用槽位；返回 `intent` |
| `activate_continuation(base, intent_id, *, proposal_hash, consent)` | 完成或重验唯一父分配与原生 activation；返回 `activation`，派发标志仍为 False |
| `require_reservation_intent(payload, proposal)` | 纯核验唯一且精确匹配的既有 intent，activation 后仍能识别同一意图 |
| `require_current_continuation(control, payload, proposal)` | 实时检查实际原生 payload、原控制与版本、grant 未撤销、新有效期、当前代码及 unknown/collector/阶段证据 |
| `validate_record`、`validate_transition`、`validate_history` | 在原 journal 重建中核验严格字段、记录绑定、状态转换、出生帧和相邻关系 |

`base` 必须为原 `LoadedImageControl`。`consent` 仅接受严格 `True`，整数 `1`、字符串或缺省值均不能替代同意。协调器使用原任务执行锁；父 I/O 不重复获取该任务锁。

原控制的历史核验使用 `for_dispatch=False`，因此已过期的原 policy 仍可用于证明出处；它不恢复原派发权限。拟新有效期沿用 D36 的时区、晚于当前时间和原有效期、24 小时窗口等限制。

### 父证据与写入器 `image_continuation_parent_io`

| 接口 | 作用 |
| --- | --- |
| `server_parent_path(control)` | 从实际 backend 与原授权 task 派生唯一父账本位置；只供受控服务端代码使用 |
| `parent_authority_hash(control)` | 对固定派生路径身份、原授权与原 control 计算稳定绑定 |
| `load_current_parent_evidence(control)` | 从固定位置读取 D36 before-only 证据，要求完整旧 allocations 尚未追加 |
| `freeze_parent_evidence(control, proposal, evidence)` | 冻结完整私有 before 证据，返回小型 reference |
| `read_frozen_parent_evidence(control, proposal)` | 重建既有 before artifact；父账本已追加后仍可用于核验原出处 |
| `verify_frozen_parent_reference(control, proposal, reference)` | 重新构造 reference，精确核对 artifact 原文 SHA、路径 hash 与所有绑定 |
| `reserve_extension(control, proposal, *, before_evidence)` | 在已有原生 intent 之后，最多追加一个精确父分配，或重用已完成的同一分配 |
| `verify_extension(control, proposal, *, before_evidence, receipt)` | 重新读取实际父文件与持久 proof，核验真实 old + 1；返回严格 True |
| `validate_extension_receipt(receipt, proposal, *, intent)` | 纯核验 receipt、proposal 与 intent 的字段闭合；不能代替实际父文件核验 |

接口失败使用固定 `CreatorError`。协调器为 `IMAGE_VALIDITY_CONTINUATION_UNAVAILABLE`，父 I/O 为 `IMAGE_CONTINUATION_PARENT_IO_UNAVAILABLE`。不向调用者返回任意异常文本、凭证、私有脚本或请求正文。

## 服务端父路径与分配合同

父写地址由本机实际 backend、原授权中已固定的 task 单路径组件及既有预算文件位置派生。该 task 拒绝路径穿越、分隔符和非法组件。控制文件、授权、冻结父快照、实际代码与状态根继续按原控制核验。新的写入口不接受调用方提供父账本路径，不使用 `evidence.live_parent_path` 来选择写地址。

D36 的 `--parent-ledger` 仍只选择观察文件。把旧账本复制为另一个私有文件，即使内容完全相同，也不能使它成为本阶段的写权威；D37 的 before evidence 必须精确绑定服务端派生位置。该信任边界依赖受控服务端/本地管理入口加载原 control，不是任意自封 JSON 或公开 hash 的授权能力。

父写入使用原有 `.budget.lock`。旧目录可保持当前用户所有、不可由组或其他用户写入的 0755，既有锁可保持同等受保护的 0644；不得为适配新合同擅自 chmod、替换原锁或重建第二份 live 预算账本。实际父文件要求当前用户所有、0600、regular、single-link、无 symlink、严格有界 JSON；锁也要求 owner、regular、single-link、无 symlink 和不可被其他用户写入。

在同一父锁内只接受两种情况：

1. 原始 before 字节完全一致：先验证当前 grant、拟新有效期、实际原生 intent/head、代码、源与 unknown 证据；重建 candidate after，再一次性追加精确 allocation。
2. 已存在完全相同的 old + 1：完整重验并重用，不能重新分配、改变 quote reference、风险、request、status 或 provider/settlement 字段。

allocation 必须为 `reserved`，金额 100 fen、保守调用 1 次，provider task 与实际结算金额均为空。稳定 key 和 quote reference 由完整 proposal basis 派生；request 固定绑定原 control hash、旧 allocations hash、before 原文字节 SHA 与 continuation basis hash。全部旧记录逐项保持，保留总额摘要按完整记录计算。任何其他新增项、旧记录变化、同 key 不同 request、已开始的 allocation、假零风险或重复追加均拒绝。

写入器不构造 `BudgetLedger`，也不调用其 `snapshot()` 或 `begin()`。这些旧 API 有写入副作用；尤其 `begin()` 会把父 allocation 改为 submitting，与本阶段的 scope allocation 证明含义不同。

## 私有 before artifact 与 begin/proof

完整父证据不进入原生 journal；它由专用写器保存在原 execution root 下的独立 `continuation-parent` 目录，以 proposal hash 确定文件名。目录权限为 **0700**，文件为 **0600**；读取拒绝 symlink、硬链接和不安全元数据。每个 before artifact 上限 **32 MiB**，不借用 `ReceiptStore` 的 2 MiB 单文件限额。内容经严格 JSON、完整原文、父身份、旧 allocations 和全部 hashes 重建；首次原子写入后只允许精确重用。

quote 仅保存 `image_continuation_parent_evidence_ref_v1` 小型引用：proposal/control/authority/evidence hashes、artifact hash、原文字节 SHA、路径 hash 与 `ref_hash`。完整原路径、旧记录、原文 base64 不进入公开 CLI。后续 prepare、parent writer 和 activation 重验该引用；即使 JSON 语义相同，原文空白变化也不能替代已固定的 artifact SHA。

父分配证明采用 `image_continuation_parent_reservation_receipt_v1`，小型文件上限 **1 MiB**。它绑定 intent ID/hash、proposal/basis、服务端 authority hash、before evidence 与父 before/after SHA、旧 allocations hash/count、唯一新 allocation/request/key hash，以及保留风险和调用包络。

内嵌 `image_continuation_parent_allocation_begin_v1` 使用 `status=allocation_reserved_observed`，记录实际观察时刻并固定 `http_dispatch_performed=False`。它证明已观察到该 scope allocation，而不是 HTTP 已开始、已返回、零费用或生成成功。receipt 固定 `allocation_reserved=True`、`dispatch_authorized=False`。纯 receipt 核验还要与 quote 保存的 authority reference 绑定；实际父来源必须另做 fresh `verify_extension`。

## 中断、幂等与容量

父文件和原生 journal **不是两文件原子事务**。风险保留故意先于父写入，失败不回收原生 intent。

| 中断位置 | 保留与恢复规则 |
| --- | --- |
| quote 后、prepare 前 | 没有新增账户风险；冻结文件不授予消费能力 |
| intent 后、父写前 | 新增 ¥1 / 1 call 已保守保留；可在同 intent、同代码和仍有效授权下继续，不能再创建一份分配 |
| 父已追加、proof 尚未完成 | 实际父分配继续保留；仅能重验 exact old + 同一 new1，再补事实观察证明 |
| proof 完成、原生 activation 未写入 | 保留原 intent、父分配和原 proof；用相同分配重验后再执行原生 CAS |
| activation 已存在 | 重验同一记录和实际父证明；不重发图片、不新增预算、不刷新原 begin |
| 过期、撤销、代码/版本/collector 漂移 | 禁止新父分配或新 activation；已保留风险不释放，已发生的财务事实仍可独立核验 |

写入使用受保护临时文件、fsync、原子替换或首次创建及目录同步。权限错误、损坏或无法确认的落盘结果保持失败关闭，不能据此释放风险或重发请求。异常注入下的恢复不能表述为任意磁盘损坏或任意 SIGKILL 都能自动修复。

协调器在 quote、intent 及父写前预检原生 journal 容量，为后续 bounded receipt/activation 和帧开销留出空间；最终原生事务仍执行完整容量检查。容量预检不能替代 checkpoint CAS，也不能把多文件写入变成原子操作。

## 历史重建与实时权限分离

原 `media_ledger`、`scope_budget`、`ExecutionStore` 的重建路径识别三个新记录种类。完整历史核验按原始帧重建严格字段、内容版本、原 grant、quote checkpoint、unknown、已交付与费用依据、预算增量、原文引用以及 quote→intent→activation 的相邻 birth；不通过实时文件读取、当前时间或权限缓存决定历史记录曾经是否成立。

新父分配前则必须重新读取真实当前状态与代码：原控制历史核验通过，grant 未撤销，当前时间仍在拟定有效期内，pending intent 是对应当前 head，原 source/admission、scope、manifest 与披露一致，原 unknown 的完整 collector 和故障阶段投影仍相同。已有 intent 不等于当前授权仍有效，已存在的 receipt 也不能绕过新 activation 的实时检查。

D36 的纯 `verify_parent_transition` 继续返回反事实 `proposed_only=True`、`authority_granted=False`，其 old-before-only evidence loader 不被放宽为接受已追加父账本。D37 依靠固定来源的 immutable before artifact，再读取真实 old + 1 父文件核验，避免把 after copy 冒充 before 或 live 权威。

## 投影与 CLI

仅在存在 durable intent 时，媒体投影才增加 `continuation` 字段、把额外 ¥1 加入媒体保留风险，并将 `reservation_stage` 标为 `scope_and_continuation_intent`。该分组分别给出 intent/activation 数量、`additional_reserved_risk_cny`、`additional_reserved_call_slots` 与按 `scope_envelope` 重算的当前账户风险/保守调用包络。

原 `paid_call_count` 仍计真实媒体 operation；新增预算意图不是一次 POST。对原两次图片意图的输入，它仍为 2，不能因保留一个槽位显示为 3。无此 intent 的旧投影保持原 schema。activation 后也不增加 actual paid count、费用观察或 delivery。

在后端目录调用，下列参数均为占位符：

```sh
python -m core.execution.image_validity_operator status
python -m core.execution.image_validity_operator status --control CONTROL
python -m core.execution.image_validity_operator quote --control CONTROL --valid-until NEW_EXPIRY
python -m core.execution.image_validity_operator prepare --control CONTROL --quote QUOTE_ID --proposal-hash PROPOSAL_HASH --consent
python -m core.execution.image_validity_operator activate --control CONTROL --intent INTENT_ID --proposal-hash PROPOSAL_HASH --consent
```

默认无 control 的 status 为 `mode=off`。显式 control 下为 `mode=authorization_only`；阶段包括未开始、quote ready、reservation pending 和 activation recorded/runtime pending。CLI 没有父路径覆盖参数，没有生成、查询、下载或 Key 配置入口。

标准输出只含阶段/记录数量、当前风险与调用包络、proposal hash、拟有效期、重放标志和固定运行边界。返回的 quote、intent、activation opaque resource ID 仅用于下一步管理操作；不输出原媒体 operation/task ID、账户凭证、父原文、私有路径、脚本或提示词。

所有结果固定 `dispatch_ready=False`、`runtime_contract_pending=True`、`paid_calls=0`、`automatic_followup_calls=0`；这里的 0 描述本次管理动作，不改写历史调用。解析或执行失败统一返回固定 `IMAGE_VALIDITY_REVIEW_UNAVAILABLE`，退出码 1；成功退出码为 0。

## 后续 runtime 合同

D37 activation 不修改旧 policy，不给原未知请求补发许可，也不让原图片 Operator 绕过过期、unknown、代码或费用门禁。完成真实图集仍需后续 runtime/ledger/descriptor 版本合同、实际有效窗口内的执行验收、视觉审查与最终人工验收。

后续代码升级必须沿用**同一个 intent 与已保留的额度**，单独批准新的代码闭包和 runtime 版本，不能再 reserve 一笔 ¥1、重新建 scope 重置消耗、修改旧 quote 或覆写 D37 activation。历史核验需要按 quote 出生前缀重建原披露，再独立验证后续授权与新尝试，不能让新增媒体 operation 或后来的代码状态反过来改变原 quote 的含义。
