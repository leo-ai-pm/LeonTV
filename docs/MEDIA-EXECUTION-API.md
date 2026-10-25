# 统一媒体执行账本 · D30

D30把受控图片组件接入原 `ExecutionStore` 的可选 `media` 扩展：报价披露、独立授权、生成意图、原task查询、费用观察与全局认领、文件交付及恢复都使用同一journal、文件锁与事务。旧六个namespace、旧文本账户上限及原quote／grant保持原合同。[D29图片工具](./CONTROLLED-IMAGE-TOOLS-API.md)仍负责确切HTTP与纯回执验证。

默认没有媒体策略、manifest或独立authority guard，不开放真实图片调用；没有新增后台生成调度器、操作员配置UI、素材文本Agent或视觉模型。本轮没有真实模型或付费调用。

## 原生记录与预算

`core/execution/media_ledger.py` 验证可选flat namespace；原schema_version=2帧链继续使用，不给旧文件回填空扩展。扩展包含scope、quote、grant、operation、query、fee、claim、delivery与幂等key记录。每帧及事务入口／出口均重建，已有事实不可被一套重新计算的hash覆盖。

scope绑定确切已接受的原生来源、计划、独立审查、素材输入和服务器冻结manifest，以及独立图片策略、父授权／账本／分配依据与费用来源。创建时用同一事务的原快照核验完整交接；历史重建继续核对接受证据，后来的其他文本调用、费用或文本授权撤销不会反向改写冻结交接。

新媒体账户必须仍是原文本账户。预算从父范围**完整冻结的额度和次数**继承，不借用已结束文本范围剩余的次数。本项目现有父范围为风险¥75.37、累计上限47次；实际原生记录45次不代表可以把差额用于图片。新scope各自累加风险／次数，总风险限额仍受原¥100约束，不重置历史未知。

本版在**创建报价时保守保留整个媒体scope风险范围**；报价和grant均不发HTTP。匹配费用、撤销或到期不释放该保留。平台收费超过单次风险时保留完整金额及额外暴露，并对同账户文本和图片停止新派发。这里的经验风险不是供应商计费硬上界，费用观察也不是最终账单确认。

## 服务端接入

`core/execution/media_runtime.py` 提供 `MediaExecution(runtime, *, policy, budget_anchor, manifest, authority_guard, fee_source, receipt_capture)`，复用传入的原执行runtime／store／execution_lock。参数默认缺省时关闭；没有从旧文本grant隐式推导媒体权限。

| 方法 | 作用 |
| --- | --- |
| `quote(task_id, key=...)` | 冻结完整披露，包括数据类别、媒体策略、父预算、manifest／handoff身份、费用来源与风险性质 |
| `grant(task_id, quote_id, disclosure_hash=..., consent=True, key=...)` | 记录对确切披露的独立同意；不自动开始 |
| `reserve_submit(task_id, grant_id, shot_id, key=...)` | 先持久化单镜version1意图、风险和exact descriptor；不得借新key重置调用 |
| `submit(task_id, operation_id)` | 显式执行原已预留意图；capture开始标记先落盘，随后使用D29 collector |
| `query(task_id, operation_id, key=...)` | 为原POST实际接受的task创建独立GET／序号；不增加付费生成次数 |
| `recover(task_id, operation_or_query_id)` | 只读已有collector回执并采纳同binding事实，不发POST／GET |
| `observe_fee(task_id, operation_id, export_bytes, observed_at=...)` | 原始导出精确核验、费用观察与全局claim在同一原生事务落盘 |
| `register_text_claim(operation_id, export_bytes)` | 从原文本费用事实及原导出证明canonical request key，保留旧别名记录 |
| `record_delivery(task_id, operation_id, query_id, image_bytes)` | 实际解码检查，保存交付字节身份，供后镜使用内部参考图 |
| `revoke(task_id, grant_id)`／`get(task_id)` | 撤销新派发权限／读取安全投影；均不释放风险 |

authority guard必须独立核验真实父授权与分配依据，返回严格True且不得修改输入；自封hash或无条件放行函数不能用于真实启用。媒体策略的合成模式必须与原接受证据模式一致。测试worker与真实通道互斥；此轮没有在生产bootstrap装配新媒体配置。

新quote／grant／意图／查询必须有同帧且唯一绑定的幂等请求记录。grant记录明确绑定严格同意、任务、quote和披露，不能复制已有grant换ID来取得新权限。旧授权、已撤销grant、新调用截止、累计上限、前序未知／待核费及引用字节都在派发前再次核对。

## 防重、核费与交付

提交未知后保留意图／风险，停止同账户新收费请求；更换grant、镜头、scope或重启不能绕过。已经预留但尚未派发的意图也要再次经过全账户门禁。在途HTTP无法被追溯取消，原回执事实仍保留。

相同key的GET重放不新增请求或序号；新GET只查询原accepted task，受独立查询上限约束。撤销后不创建新GET；历史inspect／recover及已捕获重放不需要恢复当前授权。完整HTML／坏JSON／重复JSON键等HTTP回执保留在原collector，原生意图落unknown，不伪造解析成功或重POST。

费用只接受原生成请求ID与原生日志的精确关联。统一claim检查原生文本证据和媒体认领，不能通过换导出、费用来源或旧别名把同一request key再认领。旧文本平台序号必须先由原费用事实、原导出hash及唯一完整匹配行建立映射；无法证明时新媒体授权／派发保持关闭。

D29纯candidate字段保持原样；正式归属由同一journal的claim记录证明。D30记录的是费用观察与归属，不把candidate中的未结算状态改成最终账单。精确重放需要保留第一次 `observed_at`；相同原始导出换新观察时间不能覆盖原事实。退款、自动日志拉取与最终账单结清尚待接入。

交付最多2MiB，实际字节须通过D29技术验证，视觉状态仍为not_reviewed。后镜参考图只能从同scope前序已登记交付重建，PNG／JPEG且仍受D29单参考≤1MiB等限制；不自动转换WEBP、压缩或裁剪。此轮不做图片下载或视觉质量判断。

## 只读产品API与容量

新增 `GET /api/planner/tasks/{task_id}/execution/media`：只返回范围／次数／风险、费用是否已观察和交付状态，不含原文、prompt、raw、费用导出、图片字节、signed URL、密钥或私有路径。使用原只读inspection依赖，禁止请求body和未知query，检查静态目录重叠，不启动费用线程或恢复执行。尚无媒体写入HTTP入口或前端工作台接线。

原journal读取上限仍为64MiB；新写入先检查完整帧链大小，超限时不替换原文件。大图片、完整导出和查询历史会消耗容量；当前不具备无限任务归档或大型素材存储能力，不能把协议最大查询数视为所有尺寸下都保证可用的存储容量。

下一步补服务器媒体配置／授权入口，再接确切接受版本的素材文本角色、逐镜图片及独立视觉审阅、局部返工与人的最终版本验收。原视频通道可用性仍是独立待办，不自动重试旧视频请求。
