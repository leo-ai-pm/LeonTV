# 图片续接只读预审 API · D36

本合同定义原图片 scope 的只读续接方案生成、重验和父账本反事实核验。它补充[图片 Operator](./IMAGE-OPERATOR-API.md)、[任务费用与代码续接](./TASK-FEE-CONTINUATION-API.md)及[历史校验与故障阶段](./RUNTIME-VALIDATION-PERF-API.md)。

实现新增三个独立模块与四个测试文件，后端文件布局由原 329 个文件增至 336 个。原 runtime、ledger、control、policy、permit 和 capture 的执行合同继续生效。当前没有有效期续接 activation、预算追加或新派发入口；测试和真实观察结果由独立验收报告记录。

## 输入与权限边界

调用方显式提供原 `LoadedImageControl`、一份私有父账本观察证据和拟定的新有效期。服务端读取原 control、授权文件、冻结父账本、allocation begin、原 `ExecutionStore` 及实际代码，重建方案。

父账本观察文件由调用方通过私有路径显式选择。接口验证该文件与原冻结 allocations 的一致性，并在重验时重新读取同一文件。**这只能证明所选观察文件的内容，不能证明它就是部署配置中的实际 live 财务权威。** 把旧账本复制到另一个私有位置，也不能据此获得续接或消费权限。后续执行合同必须由服务端固定实际 live 路径及身份。

预审保留原 scope、grant、源文件与版本、全部历史帧、封存的 HTTP 证据、原有效期和原 deadline。原 unknown 继续占用调用槽位并保留风险；缺少回执或 collector 目录不证明零 HTTP 或零费用。预审不运行 recover，也不为历史 unknown 补写故障阶段。

## Python 接口

| 模块与接口 | 返回值与作用 |
| --- | --- |
| `image_continuation_parent.load_parent_evidence(control, live_parent_path)` | 读取原授权、冻结父账本及显式观察文件，返回封存的私有证据对象 |
| `image_continuation_parent.verify_parent_evidence(evidence, control)` | 重新读取原文件与所选观察文件，完整重建并比较；成功返回重建的证据对象 |
| `image_continuation_parent.verify_parent_transition(before_evidence, after_raw_bytes, proposed_request)` | 对内存中的原账本加一条拟分配记录做纯核验，返回反事实观察，不读写实际账本 |
| `image_continuation_proposal.build_proposal(control, parent_evidence, *, valid_until)` | 由服务端构造 canonical、带 seal 的只读 proposal |
| `image_continuation_proposal.verify_proposal(value, control, parent_evidence)` | 完整重建当前 proposal 并逐项比较；成功返回严格 `True` |

失败统一抛出 `CreatorError`。父证据接口使用 `IMAGE_CONTINUATION_PARENT_UNAVAILABLE`，方案接口使用 `IMAGE_CONTINUATION_PROPOSAL_UNAVAILABLE`。接口不返回任意异常文本、请求内容或凭证。

### 父账本证据

`schema_version=1`、`kind=image_continuation_parent_evidence_v1` 的私有对象包含：

- 原 control 与授权 hash、provider origin、冻结和观察父账本的原始字节 SHA256。
- 所选私有路径及其 hash、父账本身份、完整原 allocations 与其整体 hash、记录数。
- 从完整 allocations 计算的保留风险、保守调用包络和原 ¥100 预算。
- 原授权、冻结账本和观察账本的完整 base64 证据，以及整个对象的 `evidence_hash`。

加载要求严格的原控制类型和历史权限核验。观察文件必须与 control、授权文件及冻结父账本分离，并位于静态目录之外；它的原 allocations 必须与冻结账本完整相同。接口按原保守 `_call_weight` 计算包络，不按成功请求数或已观察费用回收额度。它拒绝已经追加 allocation 的观察文件。

每份原始授权或父账本读取上限为 8 MiB，整个证据对象上限为 32 MiB。重验先验证严格 JSON、字段、base64 原文和全部 seals，再从实际文件重新构造；输入中自带的 seal 不能代替文件读取。完整父证据属于私有输入，不进入 proposal 或 CLI 标准输出。

### 方案成立条件

服务端要求原 control 为 `enabled`，通过 `for_dispatch=False` 的历史核验，并找到唯一匹配的原 scope 和唯一未撤销 grant。原有效期已经过去时仍可进行此只读预审，原派发门禁继续拒绝过期执行。

同一账户内必须满足：

- 只有一个媒体 operation 为 unknown，且属于所选原 scope/grant；它已经开始 capture，`raw`、`receipt`、`fee_id`、`delivery_id` 均为 `None`。
- 其他媒体 operation 没有 reserved、submitting、unknown 状态，并已有费用观察；所选 scope 内除该 unknown 外的 operation 均已交付。
- 首个 operation 为 accepted，并有经原账本重建验证的费用和 delivery。
- 媒体 query 没有 reserved、submitting、unknown；原生文本 operation 均已 settled；资产 operation 没有 reserved、submitting、unknown，且已有费用观察。
- 账户没有 overrun。

预审还要求全局原文本费用桥接不存在未完成项；其他账户不能借此预审取得执行权限。

原 unknown 的 collector 检查必须为 `missing` 且候选数为 0。若已有 collector 证据，调用方应先按原合同检查该证据，不能继续生成“无回执风险保留”方案。故障阶段投影沿用 D35，只记录实际存在的观察；历史缺失继续缺失。

服务端从原 manifest 构造 jobs，要求 job 数与原图片上限一致。它据已交付镜头推导待交付目标，精确标记一个 `replacement_of`；替代项的 `attempt_version=2`，产品首帧的 `first_frame_version` 仍为 1。此处只描述一次新尝试，`new_operation_id` 和 `new_nonce` 均为 `None`。

新有效期必须带时区，晚于当前时间和原有效期，且处于本次当前时间起的 24 小时窗口内；输出统一为 UTC ISO 时间。原单请求 deadline 仍要求 120 秒。预审不重新签发 permit，不移动旧 deadline。

### 继承预算与次数

接口从原 native `scope_envelope` 和完整父证据分别计算风险与调用包络，并要求二者一致。新增调用数按以下关系推导：

```text
继承剩余 = 原上限 - 已占用意图数
拟新增调用 = 待交付 job 数 - 继承剩余
拟新增风险 = 拟新增调用 × 原每次风险
```

当前 profile 只接受推导结果为 **1 次、¥1**，并要求拟追加后保留风险不超过原 ¥100。针对原四镜场景，合同中的计划值如下；右列均未实际分配或激活。

| 项目 | 原记录中的继承值 | 提议续接后的值 |
| --- | --- | --- |
| 图片累计上限 | 4 | 5 |
| 已占用图片意图 | 2，含原 unknown | 保留这 2 次 |
| 可继承剩余图片槽位 | 2 | 加拟新增 1 次后计划完成 3 个待交付 job |
| 账户保留风险 | ¥81.37 | ¥82.37 |
| 保守调用包络上限 | 53 | 54 |
| 原总预算 | ¥100 | ¥100 |
| 风险释放、旧意图释放 | 0 | 0 |

保留风险不等于实际消费，平台费用观察不等于最终账单。proposal 的 `paid_calls=0` 只描述本次只读操作，不把历史消费或 unknown 归零。

### Proposal 字段与无循环 seal

顶层 schema 为严格整数 `1`，`kind=image_continuation_proposal_v1`。固定结果字段为：

```json
{
  "read_only": true,
  "dispatch_available": false,
  "approval_granted": false,
  "additional_allocation_status": "proposed_only",
  "paid_calls": 0,
  "automatic_followup_calls": 0
}
```

顶层其余字段为 `basis`、`basis_hash`、`parent_allocation` 和 `proposal_hash`。服务端从实际文件构造完整 `basis`，包括：

| 内容 | 绑定 |
| --- | --- |
| 原任务与许可 | control、scope、grant 的 hashes、account 和完整 admission |
| 原内容与资产 | source、plan、asset input、manifest、manifest origin、reference profile、fee source |
| 原历史与代码 | 原 policy/anchor hash、原有效期、真实 journal checkpoint、原代码清单 hash、实际当前代码清单、前一代码 activation |
| 父账本 | 私有父证据的安全摘要和 fingerprint；不携带路径、原记录正文或 base64 |
| 原 unknown | 完整原 operation/descriptor/permit hash、原时限、保留风险与已耗用槽位、collector 和阶段投影 |
| 已交付依据 | 实际 operation、费用 candidate、delivery 的 hash 及费用观察；质量仍为 `not_reviewed` |
| 拟继续内容 | 待交付 job hash、精确替代关联、尝试序号、原引用关系、按真实交付 operation 选择引用的要求 |
| 拟调整范围 | 新有效期、累计上限、继承与新增风险/次数、原 120 秒请求时限及计划状态 |

服务端先对完整 basis 计算 `basis_hash`，再构造拟分配 request。request 的 `continuation_basis_hash` 引用该 hash，因此不把分配 request 的自身 hash 再放回 basis。

`parent_allocation` 使用 `image_continuation_parent_allocation_proposal_v1`，包含服务端派生的稳定 `operation_key`、`request`、`upper_cny=1.000000` 与 `quote_ref`。request 为 `image_validity_continuation_parent_scope_v1`，固定绑定原 control hash、原 allocations 整体 hash、原观察账本 SHA256、basis hash，以及拟新增 1 次/¥1。其存在不等于调用了父账本 reserve。

`proposal_hash` 覆盖除它自身以外的完整顶层对象。strict JSON 和精确字段集区分布尔、整数、浮点数及额外字段。修改字段后重新计算所有公开 seals，也不能代替服务端重建。

### 重验与采集末尾核对

`verify_proposal` 先检查格式、固定只读字段与 seals，再用 proposal 中的拟定有效期调用完整 `build_proposal`，与重建结果做完整 JSON 比较。它重新读取原 control、父证据、原生账本和当前代码，保留原历史校验。

构造末尾还要求：原生 state 原始字节不变；所选父账本观察文件、control、原授权、冻结父账本及 begin 的原始 SHA 一致；实际代码清单再次相同；unknown 的 collector/阶段观察再次相同。接口遇到这些漂移即拒绝输出当前方案，不缓存跨请求权限。

有效期是调用方申请的计划参数。另一份处于允许窗口内、按全部现有证据重新构造的未来时间方案会产生不同 hash；接口不证明任何人已经批准该时间。它也不验证未来时刻仍能派发。

### 父账本反事实核验

`verify_parent_transition` 只处理调用方给出的内存证据和拟定 after 原始字节。它重建 before 的内部原文与 seals，要求父账本身份和全部旧 allocations 相同，只新增拟 request 对应的一个完整 reserved allocation：¥1、1 次、真实 request hash、固定 quote reference、无 provider task、无实际结算金额。若 after 含保留总额摘要，摘要也必须与完整记录计算相符。

返回对象为 `image_continuation_parent_transition_observation_v1`，固定 `proposed_only=True`、`authority_granted=False`。调用方若需确认观察文件仍新鲜，必须另行调用 `verify_parent_evidence`。反事实核验本身不读取部署中的 live 账本，也不 reserve、begin 或激活任何记录。

## 显式 CLI 与私有导出

以下参数名均为占位符，在后端目录调用：

```sh
python -m core.execution.image_continuation_operator status
python -m core.execution.image_continuation_operator status --control CONTROL
python -m core.execution.image_continuation_operator propose --control CONTROL --parent-ledger PARENT_OBSERVATION --valid-until NEW_EXPIRY
python -m core.execution.image_continuation_operator propose --control CONTROL --parent-ledger PARENT_OBSERVATION --valid-until NEW_EXPIRY --output PRIVATE_PROPOSAL
python -m core.execution.image_continuation_operator verify --control CONTROL --parent-ledger PARENT_OBSERVATION --proposal PRIVATE_PROPOSAL
```

无 control 的默认 `status` 为 `mode=off`；带 enabled control 的 status 只报告 `review_only` 模式，不报告派发权限已经通过。CLI 不从环境变量选择 control、父账本、凭证或输出位置。

`propose` 和 `verify` 的标准输出只含模式、proposal/basis hashes、原/拟有效期、limits、budget、待交付 job 数、替代项数量及固定只读结果。它不输出完整 basis、原生 operation 标识、私有路径、脚本、提示词、图片数据或父账本原文。失败返回固定 `IMAGE_CONTINUATION_REVIEW_UNAVAILABLE`，退出码 1；成功退出码为 0。

`--output` 只允许在已存在、当前用户所有、权限为 **0700** 的私有目录中首次写入独立 proposal 文件，文件权限为 **0600**。输出必须位于静态目录之外，与原 control、所选父观察文件、各原状态根、授权、冻结账本及 begin 路径互不重叠。接口拒绝 symlink 和不安全的已有文件；已有输出必须与本次完整 proposal 相同，不能覆盖为另一方案。

proposal 读取及结构检查上限为 16 MiB；首次导出还须满足原 `ReceiptStore` 的 2 MiB 单文件上限和容量约束。完整 proposal 含私有运行关联，导出文件不属于可发布的公共文档。

## 后续执行合同的前置条件

实际续接需要单独实现原 scope 内的版本化授权。实现必须由服务端固定实际 live 父账本路径，验证本场景原 45 条 allocations 完整不变并只追加精确 1 条，将原/新父账本原始 hash、真实 allocation/begin、原 source/plan/manifest、unknown、完整当前代码和 journal checkpoint 绑定在同一披露中。

后续 quote/activation 需要精确披露同意、同任务锁和 checkpoint CAS，并让 runtime、ledger、scope budget 与历史重建支持新的有效期、累计次数和一次替代尝试。原 scope/grant 和旧 operation/permit 保留原字节；新增未知、待核费、超额、撤销、过期、代码或版本漂移继续停止执行。新增预算应先保守留存，再进入 activation；中断不能释放已经预留的风险。

当前 preflight 不提供上述 activation，现有图片 Operator 也不消费 proposal 作为执行许可。图集生成、产品内视觉 Reviewer、按需修复、视频和最终人工验收仍由后续实际执行与验收合同处理。
