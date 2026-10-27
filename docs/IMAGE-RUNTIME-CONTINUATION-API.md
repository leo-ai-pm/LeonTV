# 图片运行时续接 API · D38

本合同在[D37续接授权](./IMAGE-VALIDITY-CONTINUATION-API.md)上，为**同一原任务、同一已保留intent、同一父分配**增加精确代码与运行时版本批准，并将其交给v2图片描述符和原媒体执行器。它不重新申请预算、不重建scope或grant，也不释放原unknown。

本文描述代码合同，不代表已经在原任务中激活、通过全量测试、完成真实派发或交付媒体。实际操作和验收结果应另行记录。管理授权、实际HTTP、费用观察、视觉质量及人工终验分别判断。

## 保留的边界

- D37的quote、intent、activation及`dispatch_ready=false`／`runtime_contract_pending=true`保持原值。D38追加自己的版本记录，不覆写旧同意或日期。
- 原scope、grant、policy、parent anchor、旧operation及nonce、HTTP证据、费用、交付和已消耗次数保持。原unknown继续占用调用与风险，缺少回执不证明零HTTP或零费用。
- D38管理动作固定`additional_risk_cny="0.000000"`、`additional_paid_calls=0`。既有父预算与私有预留证明只读，不再添加一笔1元／1次。
- 对D37四镜方案，v2有效策略继承原四次，加上已保留的一次，累计上限为五次。后续目标仍是原方案中的第二镜替代attempt及第三、第四镜，序号3、4、5；原unknown不被重发。
- 没有自动重试、模型回退、后台调度或默认生产启用。新unknown、待核费或超额继续阻止新的付费POST；撤销或代码／来源／有效期／原collector漂移则使新请求的当前授权失效。

本地保留风险不是实际消费、平台资金冻结或已确认的计费硬上界。

## 管理记录与版本链

原`ExecutionStore`的`media`命名空间新增两个种类：

| 记录 | 字段与含义 |
| --- | --- |
| `runtime_continuation_quote` | 通用记录字段加`disclosure`及`disclosure_hash`；冻结原范围、原grant、D37 intent／activation／父证明、精确代码、新有效策略、原unknown与剩余目标、当前checkpoint及前一批准版本 |
| `runtime_continuation_activation` | 通用字段加`quote_id`、`disclosure_hash`、`previous_activation_id`、`activation_sequence`和严格布尔`consent=True` |

报价只追加一个元数据帧。首次激活必须紧接该报价，在同一原任务锁、精确披露hash和原生checkpoint CAS下追加一个激活帧；不能在同一事务夹带修改其他记录。批准序号从1递增，前一激活ID必须对应当时最新的已批准版本。当前版本和检查点仍满足重放条件时，相同输入的管理重放复用原记录，不增加预算或刷新其时间。

新有效期须带时区、晚于当前时间和原policy有效期，报价创建到新有效期最多24小时。旧D37有效期已经过去时，也只能在既有已保留事实之上明确批准新lease；不能将旧记录的日期改成未来。原单次请求期限仍不超过120秒。

后续代码变化或lease更新需要新的精确报价与批准，沿用原intent及已留额度。旧版本可以用于历史重建；新派发必须选择当前最新批准。

## Python接口

### 版本授权：`image_runtime_continuation`

| 接口 | 作用 |
| --- | --- |
| `quote_runtime_continuation(control, *, valid_until)` | 从原`LoadedImageControl`、真实当前journal与已留父证明生成并保存新披露，返回`quote`、`replayed`及`automatic_followup_calls=0` |
| `activate_runtime_continuation(control, quote_id, *, disclosure_hash, consent)` | 精确同意后保存或重验对应激活；返回`activation`及重放标志，不创建媒体operation |
| `latest_activation_id(payload, scope_id)` | 纯读取该scope最新批准版本；没有时返回`None` |
| `authority_for(payload, activation_id)` | 从已核验journal重建指定历史版本的完整authority，不声明当前仍有效 |
| `validate_authority(value)` | 纯核验authority的严格字段、封印、有效策略、两笔真实分配及目标算术；自封JSON不能单独授予权限 |
| `verified_current(control, activation_id)`（D41） | 独立重新核验实际原控制与journal，再核验最新版本、来源／grant、原unknown collector、既留资金、当前代码和lease；返回本次决定的`(payload, authority)`，不缓存 |
| `verify_current(control, payload, activation_id)` | 独立新鲜核验实际head、最新版本、当前代码、来源／grant、原unknown collector、既有父证明及新lease；成功返回严格`True` |
| `validate_record`／`validate_transition`／`validate_history` | 原journal的字段、出生前缀、相邻提交、版本链和checkpoint完整性检查 |

`consent`只接受严格`True`，整数`1`或字符串不能替代。协调器错误为固定`IMAGE_RUNTIME_CONTINUATION_UNAVAILABLE`，不传播任意异常正文或私有内容。

`verify_current`核验该版本的实时授权绑定，不能替代具体候选的预算／unknown／费用／次数／nonce门禁。各次权限判断独立读取，不缓存跨请求许可。

### 已留资金事实：`image_continuation_funding`

- `frozen_funding(payload, activation_id)`从已核验D37记录纯重建资金事实引用，不证明live出处。
- `read_funding(control, payload, activation_id)`重新核验实际原控制、固定父路径、不可变before artifact与原文SHA、实际旧记录加唯一预留、持久proof和原分配。给定payload必须匹配真实head。
- 内部`_collect_funding`只供同一次已经完成实际控制／head核验的判断使用，避免在该判断里重复重建同一历史；它不保留缓存，也不作为独立授权入口。

事实读取不要求当前代码等于D37报价代码，也不以旧lease是否仍有效决定“预留曾经发生”。新请求的精确代码和新有效期由D38独立判断。D37原写入器和旧读取合同保持严格，不被放宽为新的派发许可。

资金事实错误为`IMAGE_CONTINUATION_FUNDING_UNAVAILABLE`。读取器不构造`BudgetLedger`，不调用其`reserve`、`begin`或有写副作用的`snapshot`，也不调用D37的`reserve_extension`。

## v2 authority与描述符

v2 authority是经严格核验的规范dict，以`schema_version=2`、`kind=image_continuation_authority_v2`封存运行时批准ID／版本、原scope与grant、同一D37 intent／activation／receipt、原policy、原budget anchor、有效policy、代码hash、原unknown、目标和两笔分配。

有效policy只由原policy复制后改变累计`max_submissions`和新`valid_until`；其余字段仍须相同。原budget anchor仍指原真实分配，不能把两笔allocation的集合hash塞进v1的单个`parent_allocation_hash`。

`allocations`恰为两项，分别为`role=original`与`role=continuation`。每项独立携带真实`allocation_hash`、`begin_hash`、`risk_cny`和`max_calls`，合计必须与有效策略一致。authority本身的`authority_hash`是完整结构封印，不被冒充为某一笔父记录。

`image_continuation_policy`提供：

| 接口／模型 | 合同 |
| --- | --- |
| `ImageContinuationPermit` | schema2，`scope=accepted_plan_image_tools_v2`、`job_kind=controlled_image_http_v2`，额外绑定authority hash、激活ID／版本、attempt／replacement及引用绑定hash |
| `make_continuation_permit(...)` | 为明确目标生成独立operation／capture／nonce，严格匹配预定累计序号；query单独取得有界GET许可 |
| `build_image_submit_v2(...)` | 用有效policy、原anchor、完整authority、原已接受素材及精确引用生成POST描述符 |
| `build_image_query_v2(...)` | 仅查询该POST真实受理的任务，继承原完整描述符和回执绑定，不能推广上游ID或重新提交 |
| `bind_reference_sources(sources, references)` | 绑定原生source operation、query、delivery ID、delivery hash、原文件SHA和镜头版本 |

原`FrozenImageRequest`统一重建v1与v2；v1格式、旧封印及旧permit不自动升级。原collector仍负责单次HTTP、私有回执与有界查询。描述符一致性不代替原生预留和当前权限。

## 精确候选与交付引用

`media_continuation_gate.candidate_gate(payload, candidate, *, exclude_candidate=False)`只对完整绑定的同scope v2候选，临时排除**精确披露的那个原unknown**来检查其余事实；原payload及全局事实函数不被修改。

门禁核验最新批准、目标hash、attempt／replacement、实际累计序号、未重复的目标、全账户费用／未知／超额、原grant、已结清文本操作和未完成查询，并禁止复用任何旧nonce。`exclude_candidate=True`只用于已经原生保存、仍为reserved或submitting的同一候选；不能借此忽略别的操作。

全局`has_pending_unknown`和`has_pending_fees`仍可为真。文字角色、素材角色、其他scope及未绑定的候选不能借用该例外。新增unknown或待核费仍停止后续付费动作。

上述候选门禁针对新的付费POST。既有`MediaExecution.query`保留对明确accepted task的显式、有界GET观察，不套用POST候选门禁；GET仍须核验当前revision、lease、grant、代码、父证明及原POST绑定，没有自动query或自动retry。`runtime_status.query_available`采用同样边界。只读recover及既有精确费用／交付导入继续按原事实通道处理。

query必须完整继承其父POST的authority。切换到新runtime revision后，不能把旧POST移植到新批准来获得GET许可；当前合同保守拒绝这种跨版本新查询，需要后续独立合同，不能承诺跨lease无缝续查。

引用选择绑定确切已交付操作、查询和文件，不再按镜头名选第一个历史操作。第二镜新attempt交付后，第三镜需要引用该确定版本；原unknown即使镜头名相同，也不能充当参考图。转换后的PNG／JPEG仍沿用原转换披露和证明，原始交付字节保留。

原`MediaExecution`增加显式`continuation_activation_id`与`continuation_guard`。未提供时仍走旧合同；提供后，reserve／start／query使用匹配的v2 authority和候选门禁，原scope、grant与幂等键体系继续复用。Key前后仍独立核验实际授权和原生请求绑定，POST另核验具体候选门禁；采集器还执行自己的最终检查。加载凭证不是权限。

## D41：同一次权限决定共用新鲜snapshot

`RuntimeContinuedImageControl.verified_current()`调用上述新接口，每次重新读取原控制、实际状态和外部资金／代码证明。`verify_current(control, payload, activation_id)`仍会独立获取实际snapshot，并要求调用者给出的payload与之完全匹配；调用者传入的旧snapshot或修改后的dict不能代替当前权限。

原`build_image_session`为v2执行器显式传入`continuation_control`。`MediaExecution`严格核验其类型为`RuntimeContinuedImageControl`，原执行目录与激活ID匹配，且`continuation_guard`是该同一control的原`guard_runtime`绑定方法；不能同时提供一个不同或更严格的guard后被静默忽略。构造、权限决定和collector最终回调均重新检查绑定，后续替换control／guard也不能借用已有权限。

reserve和start分别取得新的实际snapshot，要求其与当前transaction的payload相同。Key前、Key后和collector最终检查又分别取得独立的新snapshot；同一次决定中的配置、原生记录、descriptor、grant和POST候选门禁使用该次刚核验的状态。原policy／anchor／manifest／费用来源／参考转换profile／素材来源绑定继续核验，跨类型unknown、待核费、超额、累计次数、nonce和精确交付引用规则不变。

snapshot不保存在control或runtime中，不跨越Key边界或请求复用。返回的dict只是该次核验结果，修改它不能改变原状态或下一次的权限判断。旧v1及只提供原callback的路径保留，D40仅GET观察合同也不变。

这项改动减少同一次决定中的重复历史重建，不改变120秒permit期限、不刷新旧permit、不追加父预算，也不自动派发。代码／lease变化仍需同一D37 intent及既有4+1分配上的精确新批准。代码与合成检查、真实体量测量及后续派发分别见[D41提交校验验收](./IMAGE-SUBMIT-SNAPSHOT-VALIDATION.md)；本接口说明不单独证明真实付费POST已通过。

## 管理CLI与实际操作CLI

在后端目录调用。以下全部为占位符，不是私有路径、任务ID或真实命令参数。

```sh
python -m core.execution.image_runtime_operator status
python -m core.execution.image_runtime_operator status --control CONTROL
python -m core.execution.image_runtime_operator quote --control CONTROL --valid-until NEW_EXPIRY
python -m core.execution.image_runtime_operator activate --control CONTROL --quote QUOTE_ID --disclosure-hash DISCLOSURE_HASH --consent
```

缺省status为`mode=off`。显式管理模式为`runtime_revision_review`，成功结果仍固定`paid_calls=0`、`automatic_followup_calls=0`、新增风险／调用为0、`dispatch_available=false`。`current_authority_valid`只表示当前批准绑定仍可核验；没有创建可派发候选。管理命令没有图片生成、下载或Key参数。

管理stdout只返回阶段、记录计数、opaque管理资源ID、精确披露／代码hash、新有效期与重放标志，不输出原脚本、父原文、凭证或私有路径。管理错误统一为`IMAGE_RUNTIME_REVIEW_UNAVAILABLE`并退出1；成功退出0。

实际操作仍通过原图片CLI显式选择新批准：

```sh
python -m core.execution.image_operator --control CONTROL --runtime-continuation ACTIVATION_ID status
python -m core.execution.image_operator --control CONTROL --runtime-continuation ACTIVATION_ID reserve --grant ORIGINAL_GRANT_ID --shot TARGET_SHOT --key NEW_REQUEST_KEY
python -m core.execution.image_operator --control CONTROL --runtime-continuation ACTIVATION_ID start --operation NEW_OPERATION_ID
```

`--runtime-continuation`与旧`--continuation`互斥；新模式禁止再通过旧quote／grant入口重建授权，也不开放`legacy-fees-import`。原有query、recover、精确核费和delivery-import仍分别执行其合同，没有自动后续调用。

`RuntimeContinuedImageControl`将原控制与明确激活ID绑定。运行时status分列`runtime_authority_valid`、`dispatch_available`、`global_unknown_retained`、`global_pending_fees`、剩余目标与累计上限。只有一个尚未开始、仍在独立permit期限内、通过具体候选门禁的v2 operation，才可显示可派发。所有目标已交付时显示`SCOPE_FINISHED`；这仍不等于视觉质量或人工验收通过。

## 验收边界

先完成代码与合成同journal闭环、错误输入和重放检查，再在原任务中单独核验管理元数据及既有预算不变。管理记录落盘不证明实际派发成功；需要在真实历史体量和有效窗口中测量reserve→collector.prepare是否满足原单次120秒限制，且保留Key前后独立检查。

只有实际HTTP回执、精确费用证明、交付技术核验、整组视觉Reviewer／必要返工以及人工接受分别成立，才能推进对应生产阶段。本合同不宣称完整自主制作、最终成片质量或Personal Agent已完成。

后续[D39真实派发与费用验收](./IMAGE-DISPATCH-ACCEPTANCE-VALIDATION.md)记录单次真实受理、入口与签发两种时间口径及尚未查询交付的边界；本D38合同和历史测试结果不被改写为完整生产验收。


## D42显式响应合同

`quote_runtime_continuation(..., response_window_seconds=180)` 可报价新v3合同，省略参数保持原v2形状与语义。完整响应策略必须在原quote中披露并批准；不是改变旧policy的默认值。新permit绑定120秒提交期限与独立响应截止，现有POST／GET／预算／unknown门禁仍有效。[详细合同](./IMAGE-RESPONSE-LIFECYCLE-API.md) · [验证与未完成事项](./IMAGE-RESPONSE-LIFECYCLE-VALIDATION.md)。
