# 已受理诊断任务的观察续行 API · D46

2026-10-31。配套[PRD](./IMAGE-COMPARISON-OBSERVATION-PRD.md)与[验证记录](./IMAGE-COMPARISON-OBSERVATION-VALIDATION.md)。395份源码已由统筹本地提交 `dd6cde1`，manifest SHA256 `e04d9596e35943d744401b104dd95f0cd7af8701df9fc268b3bd2c3f192ed40a`。本版本增加原 D45 A 的显式观察合同；有限离线验证后，真实quote／activation／精确费用导入各一次、同任务GET一次completed。平台费用观察0.040000元；随后单次下载返回HTTP404，未取得图片字节、未登记artifact。404原因未确定。已停止真实执行并完成只读交接，不再重试或更换URL。

## 范围与原生归属

原 A 保留原 POST、wire、raw、receipt、accepted task、旧 quote／activation。旧 lease 到期不会自动更新；旧 `ComparisonExecution` 的新鲜门禁继续拒绝旧批准。新合同只允许精确任务费用、至多一个新的同任务 GET 意图及独立诊断 artifact，POST cap＝0、新风险／生成位＝0、B＝false、自动后续调用＝0。保留风险83.37元／累计包络55、父47条与原100元授权不增加；风险不是消费。

所有记录留在原 `ExecutionStore.media`。新增 `image_comparison_observation_quote` 与 `image_comparison_observation_activation`；query／fee／artifact 沿用 D45 的隔离 kind，只在新 profile 增加精确 `observation_activation_id`。不建立普通 operation，不写正式 delivery，不加入生产参考或正式图集。正式仍2/4，质量未审、人工验收待进行。

观察 disclosure 绑定原 A 的 operation 不可变摘要、descriptor／raw／receipt／capture、accepted task、原 D45 authority／quote／资金／parent、原三条 unknown 及其精确 collector、A 的完整原 collector、control、新代码清单、checkpoint 与显式新 lease。原三 unknown 的记录及证据没有被清除；候选校验仅豁免精确原 `operation:` 命名空间的那三条。当前 GET 的例外也只针对完整绑定同 A／新 activation 的那个 `image_comparison_query:`，不按裸 ID 屏蔽其他种类。

## 内部自动化入口

这是内部 Python API，复用由原 `read_image_control` 得到的严格类型，不新增 HTTP 路由或前端。以下变量均由控制器提供精确原生引用，不从浏览器时间窗猜测。

```python
quote_observation(control, original_A_id, valid_until=explicit_new_lease)
activate_observation(control, quote_id, disclosure_hash=exact_hash, consent=True)
status_summary(control, observation_activation_id)

observer = ComparisonObservationExecution(control, observation_activation_id)
observer.observe_task_fee(original_A_id, task_export, account_export,
                          task_source, observed_at=exact_observation_time)
observer.query(original_A_id, key=stable_query_key)
observer.recover(query_id)  # 本地原 collector；不发 HTTP
observer.record_artifact(original_A_id, query_id, exact_image_bytes)
```

quote 显式给出新截止，出生后最多24小时；同一 A 只允许一个本版观察 quote／activation，不能另建 key 或再次 quote 续期。quote 不执行、不读 Key；activation 必须精确 disclosure hash 与严格 boolean true，同意不执行。出生 checkpoint／原生前缀和 activation 的直接前序 quote 都须匹配；其他插入或改写拒绝。重放同一管理动作不增帧。新授权仍须由统筹审查实际源码及有限计划后明确派发，不由此文档自动批准。

`quote / grant / reserve / reserve_submit / submit / revoke` 及旧私有 dispatch／fee 入口在观察执行器上拒绝。原 A 不因观察进度得到另一次生成位。任何缺失或跨任务、旧证据／父文件／代码漂移、撤权、其他未决操作或超额均停止。

## 费用门槛与重放

本版只接既有 `candidate_for_task`：原完整 task／account exports、固定 task_source、原受理 task、原 account／token／group／model 必须精确匹配。A 的 POST request-id 缺失继续为 missing；不构造 request-id、不使用相近时间或日志行号，不把账户观察金额再加到 task quota。统筹本次纯候选0.040000元已在新观察批准内原生导入，overrun=false；仍是平台观察，不能当成最终账单确认。

quote／activation 可以披露该 A 尚待核费，但 **GET 必须先有精确原生费用记录且无 overrun**。不继承 D40 的 pending-fee GET 例外。全账户原费用归属继续双身份去重，覆盖正式与诊断记录；首份费用与原件不可覆盖。同原件重试复用首份 candidate 的 `observed_at`，忽略重试时新的调用时间，不增帧；冲突原件仍拒绝。新 overrun 如实落账后停止后续动作，不释放风险。

## GET 的时间和持久状态

GET 累计包括所有旧 D45 和观察版本；本版最多一个新意图，并受原 policy 剩余次数约束。独占原 task／store 锁后，先完整验证最新原生状态与实际 control／父／源码／collector，随后签发新 query identity／nonce。新 descriptor 只保留一份完整原 A parent，method 固定 GET、wire 为空、endpoint 只能是规范 accepted task。

本版查询在读 Key 前，以 `submitting` 意图一次持久化。它与旧 profile 的 reserved→submitting 两次写入不同，减少签发后的重复事务；旧 profile 的格式与转换不改。出生事务仍完整验证原状态、费用和累计次数。落账后、claim 前中断也消耗此唯一意图；同 key 重放只返回原记录，不重新发 GET。没有自动轮询或换 key 再查询。

pre-Key、post-Key、final collector 三个边界各自重新读完整原生状态，检查当前原件／code／lease／父资金／未知和费用，不缓存权限。派发仍 ISSUE＋120秒，独立 response window 沿用原合同（本次180秒），cleanup＋5秒，全部不越过新 lease。worker／ReceiptStore／decoder 使用原链条与 claim2。未单独插桩 prepare 方法入口时，claim 是规范边界，ENTRY 与 ISSUE 分开报告。

具体执行器要求查询入场时剩余 lease 大于600秒，核费／artifact 写入入场时大于120秒；计划不能降低这些裕量来让过期批准继续运行。已保存后遇到期限／新鲜检查失败，不重发；原 collector 保留。恢复只读该 query 的原 collector，迟到 ready 不采用为成功；不完整／多候选／不可解析材料保持隔离，保守 unknown。

## 产物、状态与到期

产品runtime没有自动下载器。本次私有受控driver设独立单次下载入口；GET完成后不自动下载。统筹先冻结规范GET receipt及URL hash，再单独派发：最多1次、2MiB、stream检查45秒／进程绝对alarm60秒、网络分项超时10秒、无跳转／重试／API Key，私有落盘不静态发布。下载失败保留accepted／completed事实，不刷新URL或重新GET。实际字节必须经同query的`verify_delivery`完整解码／格式／SHA校验，才能另行登记不可变诊断artifact；同字节重放不增帧。本次真实下载已执行一次，HTTP404后停止，未到字节验证及artifact登记阶段。原始平台导出与规范GET回执的URL哈希完全相同；不能据404推定已过期、已删除或其他原因。

`status_summary` 只返回安全标量，分开 accepted、completed、received／artifact、费用、quality、human。未登记 artifact 时 received 为 null／not_recorded，因为本地已下载但未登记的字节不在原生存储中，须由具体下载证据另列；不将 null 解读为“已下载”或“下载失败”。费用观察不是最终账单；quality 固定未审、人验pending，诊断单例不解释旧403、不构成AB或完整Harness验收。

新 lease 到期后，新原生费用认领、GET、recover采用和artifact写入均拒绝。仍可 `ExecutionStore.read_only`、检查原 collector 或纯费用候选，不获得执行权限；status 显示当前权限无效。原 collector 按冻结 response／cleanup 收尾，不延长旧 permit，不自动建立下一份观察批准。正在运行的收尾仍须通过本版新鲜检查，过期时保留已有证据待另行处置。

## 本次终态与交接

原A继续accepted，唯一新query为completed，精确平台费用0.040000元、overrun=false；原生费用不是最终账单。下载证据明确received=false，artifact=false；原生`status_summary`未登记artifact时的received仍按上述null语义解释。旧三unknown、正式2/4、质量未审及人验pending均不变。

实际ENTRY→claim151.613480秒，未通过120秒入口目标；ISSUE→claim108.830374秒，在原120秒派发期限内。raw及ready也在派发截止前，未验证超过120秒的慢响应尾部。native最终90帧，旧85前缀及旧记录保留，仅A的fee指针更新；parent47及其SHA不变。395源码无漂移，2333原保护项仅预期native变化，新query collector7文件已核对。原83.37元／55风险包络不变，风险不是消费。

具体终态在私有`docs/evidence/LTV-IMAGE-COMPARISON-20261031/backend/LIVE-RESULT.json`与`LIVE-HANDOFF.json`，统筹独立复核在同轮`coordinator-live-closeout.json`。所有已进入的quote／activation／fee／GET／download marker保留，不能重入；15:30UTC观察lease仍有效也不自动授权其他动作。本轮未形成诊断图片交付、AB／完整Harness／视觉或人工验收。
