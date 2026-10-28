# 图片有限对照 API · D45

2026-10-29。[PRD](./IMAGE-BOUNDED-COMPARISON-PRD.md)（SHA256 `c08c03a27f16b308fffd5380f439be4b951d96a29febaf3146a290d9e2c0c58f`）· [验证](./IMAGE-BOUNDED-COMPARISON-VALIDATION.md)。源码已由统筹实施验收，冻结于 `c91198558860c1faf085c2b0d0f82fa2d4b1acee`；之后一次真实A已返回HTTP200／accepted。费用、GET、图片仍待验证，入口性能目标未通过；最新真实记录与到期边界见文末。

## 本版本范围

只安排 A：对历史已成功第二镜的**原始 wire 与参考版本**做一次独立诊断。新用途为 `bounded_http403_diagnostic_only`，原账户、任务、模型、端点、图像参数及原内容目标不变。旧请求／permit／nonce／claim不重用；原五次提交与三个unknown不修改、不释放风险。

当前 A 最多 **1 POST**；总体至多2是统筹上限，不是本合同的第二位。B保持deferred，没有资金和准入。不能用新key、新quote或新批次重复A。同scope只允许一个诊断intent／activation／operation；原正式同镜两次上限不放宽。

A受理只证明这次输入被受理。历史第二镜与失败第三镜同时有prompt和参考图差异，不是字段单变量对照；不确定原因仍为`undetermined`。A没有规范受理立即停，不运行B。诊断成功不表示第三镜交付、质量通过、Key故障已排除或通道持续健康。

## 原生记录与资金

所有新增记录仍在原 `ExecutionStore.media`，通过原账本重建和事务验证；不新开账户／账本。新前缀：`image_comparison_quote / intent / activation / operation / query / fee / artifact`。

- quote：冻结原生出生checkpoint、原账户／scope／grant、旧runtime／D37资金事实、当前源码、原三unknown与完整collector文件清单、精确原请求引用及风险增量。
- intent：引用确切proposal hash及严格boolean consent，先在原账户完整保留 **1元／1次**；尚未写父分配也不释放此风险。
- activation：在原父账本共享锁中，比较精确before字节，只添加唯一purpose-bound分配；记录exact after SHA。中断后只能观察／完成同一分配，不另扣一位。

开发时基线为100元授权、82.37元／54次保留包络、原父46条。2026-10-29真实A已在原账户新增1元／1次，合计83.37元／55次、原父47条；这是保留风险及累计次数上限，实际费用仍待核实。既有两笔4+1、原scope／grant／报价及hash历史不改。合成fixture使用它自身opening+1验证，不强塞真实金额。

`scope_budget`把新intent计入共享保守包络；全账户费用归属同时检查新旧图像、文本与素材费用。新费用超风险可落账，然后停止后续调用；不为避免停机改写历史收费。

## 读写接口

以下为内部Python API；都接收由原`read_image_control`加载的真实类型。示例是占位符，不能直接复跑旧D43脚本。

```python
quote_comparison(control, max_posts=1, valid_until=None, response_window_seconds=180)
prepare_comparison(control, quote_id, proposal_hash=exact_hash, consent=True)
activate_comparison(control, intent_id, proposal_hash=exact_hash, consent=True)
status_summary(control, quote_id)
```

已有管理CLI增加`comparison-quote / comparison-prepare / comparison-activate / comparison-status`。quote显式支持`--max-comparison-posts 1 --response-window-seconds 180 --valid-until <fresh-lease>`；prepare用`--quote / --proposal-hash / --consent`；activate用`--intent / --proposal-hash / --consent`；status用`--quote`。输出quote、intent、activation内部身份和hash，不输出原文、raw、Key、平台task ID或媒体URL。管理命令不派发。

经适用新批准后，`ComparisonExecution(control, activation_id, ...)`复用原`ControlledImageCapture`、detached worker、ReceiptStore及原图像decoder／费用候选／完整图像校验：

```python
reserve(key=stable_key)              # 新累计提交序号6，保留提交意图
submit(operation_id)                 # 一次POST；重启/重复调用不重发
recover(identifier, query=False)     # 本地原collector恢复，可能原生记账；有效lease内，不发HTTP
observe_fee(operation_id, export_bytes, observed_at=exact_time)
query(operation_id, key=stable_key)   # 仅已受理且费用精确匹配的原任务
record_artifact(operation_id, query_id, image_bytes)
```

request-id费用路径只采用原POST request identity精确匹配；另有文末所述显式task费用路径。两者均拒绝仅时间相近、错账户／group／model及已占用身份；无精确证据则保持待核费。当前不猜平台task ID，不使用历史同窗quota0认领零费。GET逐个显式执行、同任务、总数沿用原max_queries；本版本没有下载器或自动调度。

## 新鲜门禁与期限

每个Key前、Key后及最终collector边界独立验证当前原生head、原source／plan／manifest／grant、源码、父资金、用途、旧三unknown与精确collector文件。只在同一次决定中复用已验证snapshot，不缓存跨边界权限。新增／改变record、commit、ready或原证据使计划失效。

诊断permit独立绑定D42响应合同：**dispatch为签发后120秒，response为dispatch截止再加1..180秒，cleanup固定5秒，且response不得越过新lease**。claim使用version2，worker watchdog上限沿用305；旧profile字段／hash／截止不升级。不能延长旧permit，也不能因为迟到重新发送。真实ENTRY→prepare、ISSUE→claim及慢响应尾部仍须另验。

## 结果与恢复

五项`response_diagnostic`只展示已验证标量，`retry_authorized=false`。生成、费用、诊断结论与正式交付分开：`stage`区分未运行／reserved／submitting／unknown／accepted_fee_pending／accepted_fee_observed／diagnostic_artifact_delivered；观察费用不是最终账单。

完整但不可解析的响应原件保存在collector；native稳定unknown，不伪造规范receipt。submitting后、claim前中断也保留意图并恢复为unknown，不重POST。已reserved且未发送的GET仅可由原key在原期限内继续一次；`recover`本身不执行GET。迟到／不完整／多候选材料保持隔离，不自动采用或结算。

`artifact`只作诊断证据，quality仍`not_reviewed`；它不属于正式`delivery`，不进入原图集、不作生产参考。正式图集继续2/4、人工验收pending。旧费用／intent／activation／artifact不可改写；撤销可保存，后续新鲜门禁拒绝派发。

缺少POST request-id但已有规范受理任务时，显式`observe_task_fee(operation_id, task_export, account_export, task_source, observed_at=...)`复用既有`candidate_for_task`合同。task_source为服务端固定账户归属与export hash，候选须精确匹配原accepted task、model／group／user；两类费用共用全账户双身份去重。403未受理不能使用该路径。两种证据都只是平台费用观察，不能更换已落账的首份费用或宣称最终账单确认。

## 最新真实状态与续行边界（2026-10-29）

冻结源码`c91198558860c1faf085c2b0d0f82fa2d4b1acee`后，统筹显式派发一次A：原第二镜相同wire、原模型／端点／参数，累计提交序号6。规范原件返回HTTP200、`accepted`且有确切任务身份；阶段为`accepted_fee_pending`。这证明该输入本次被受理，尚未证明任务完成或可用图片、整条生成通道健康、第三镜403原因或产品闭环。B与重试均未执行／授权。

原生历史79→85帧、父账本46→47条；旧79帧前缀、全部旧记录和三条unknown不变。新诊断POST1、GET0、费用0、artifact0；这里的费用0指尚无认领记录，不能推断零扣费。正式图集仍2/4，质量`not_reviewed`、人验pending。A原collector七份文件已完整核对与冻结，不能改写为正式delivery或后继参考。

实际reserve ENTRY→claim为150.336358秒，未过120秒入口目标；permit ISSUE→claim为115.444601秒，在原120秒派发期限内，仅余4.555399秒。claim→raw为3.263654秒，未覆盖超过dispatch截止的慢响应尾部。两种计时不得互相替代，不能宣布性能验收。

本次activation租约固定于 **2026-10-28 21:30 UTC（北京时间10月29日05:30）** 到期。等待费用后台登录不会延长期限。到期前也必须取得精确费用原件及单独派发，才能核费导入、一次同任务GET或记录独立artifact；私有续行脚本仅准备，不自动执行、轮询或下载。当前POST缺少request-id，不能凭相近时间认领日志；采用task路径时必须精确匹配原受理task及账户、model／group／user。

到期后仍可只读验证原生历史／collector／源文件，以及在本地对新导出的材料运行纯`candidate_for_task`或`candidate_for`核对；这不导入费用、不获得执行权限。`observe_task_fee`／`observe_fee`、新的GET、`recover`原生采用和`record_artifact`均经`_fresh`要求当前lease有效，不能在旧activation上续做。若到期前已合法派发，原collector只能按原冻结的response／cleanup期限收尾；不能延长、再发或将迟到原件当作成功。不得自动新建quote／intent／activation、增加资金、更换账户或绕过旧三unknown。本轮不新增产品续期功能。
