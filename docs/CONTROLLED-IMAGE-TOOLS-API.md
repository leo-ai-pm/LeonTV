# 受控图片工具组件 · D29

为后续素材执行新增独立媒体策略、图片许可、原始提交／查询回执、文件交付检查与费用候选适配，复用既有 `ReceiptStore`、`capture_once` 和 detached worker。没有新增调度器、金融账本或生成 HTTP 入口；原文本任务、四角色、账户、grant 与六个 journal namespace 保持原合同。[验收](./CONTROLLED-IMAGE-TOOLS-VALIDATION.md)。

## 权限与请求

`core/execution/image_policy.py` 提供 `MediaBudgetAnchor`、`ImageToolPolicy`、`ImageToolPermit`、`FrozenImageRequest` 及 `build_image_submit`／`build_image_query`。

新 scope 固定为 `accepted_plan_image_tools_v1`，不能使用旧文本 grant。它冻结原100元、历史风险和次数、独立媒体范围与 begin 引用，以及媒体 grant／披露、handoff／provenance、素材输入、manifest／job、来源、计划、接受报告及单镜版本。新范围必须满足期初风险＋范围风险＝累计限额≤100元，历史次数＋范围次数＝累计次数上限；这是内部一致性检查，不证明实际已经预留；本组件不维护累计金融账本，实际已用次数、额度和唯一派发仍须由持久控制器核验。

首次图片仅允许原清单镜头的 version1。POST body 从已验证 `product-assets-v1` 的确切 job 编译，不能由 Agent 添加 provider 参数、路径或任意外链。参考图使用前序内部镜头的原始字节、版本和 SHA；仅接受内存实际验证的 PNG／JPEG，以 MIME data URL 构造，禁止路径、raw base64 或虚构 MIME。单参考图≤1MiB、每边≤4096像素、拒绝动画；整个请求仍受策略字节上限约束，不自动裁剪、压缩或更换素材。

本版固定土豆 `gpt-image-2-all`、9:16、1k、medium，POST `/v1/images/generations/async`，GET `/v1/tasks/{task_id}`。这些端点、参数及 data URL 形状已只读核对[官方图片文档](https://docs.ai-tudou.net/gpt-image-2(new)-docs.html)。生产 origin 固定 `https://api.ai-tudou.net`；合成模式可以使用明确的 synthetic origin。原文档未建立提交 task ID 与收费日志 request ID 的等价关系，组件不作此推断。

POST 许可声明1次生成意图及对应单次风险，策略范围最多12次；真正扣减次数／预留风险仍须持久控制器在派发前完成。GET有独立 capture ID、nonce、read permit和查询序号，查询序号最多120，付费生成次数／风险为0。GET引用原始 POST descriptor 和完整 raw 回执，由其实际 accepted task ID 推导固定路径；不能递归引用另一次GET、重写原POST、改变预算或将查询许可改为生成。

## 调用与持久回执

`core/execution/image_capture.py` 的 `ControlledImageCapture` 默认没有 `authority_verifier`，不能调用。真正接入时，独立 verifier 必须只读核对**实际已持久的**媒体报价、grant、父账本分配／begin、累计次数、接受来源、manifest／reference 字节身份及原提交回执，并返回严格 `True`。只检查调用者自己重算的 permit/hash，或使用无条件放行函数，不构成真实媒体授权。

本轮尚未在原 journal 接入这些媒体授权／预留记录，也未在默认 bootstrap 注册 live 图片工具。因此没有真实媒体范围、报价、grant 或付费调用；不能把可构造的 descriptor 当作生产启用。

`capture(request, api_key_in_memory)` 通过 verifier 后才使用原 `capture_once`。严格 JSON 树及摘要检查阻止 verifier 修改候选输入，包括 JSON 序列化后摘要可能相同的 tuple/list 替换。密钥仅经原 stdin IPC 传递，不写 descriptor、argv、环境或回执。

原 collector 增加明确的图片 kind 分支。POST／GET方法、origin、路径和 exact wire 均由冻结 descriptor 决定；禁止重定向，使用绝对截止与响应大小上限，保留完整 HTTP body、时间与请求标识。原 claim 在派发前持久化；已存在的 capture 不再发HTTP，只可检查原证据。提交未知、观察进程退出、通知前崩溃或持久化失败均不能触发第二次POST。

每次查询独立存证，保留原提交引用。`inspect(request)` 与 `ready_raw(request)` 只读；迟到、歧义、缺确认或不完整记录不自动采纳。descriptor历史重建不依赖当前密钥、策略启用或当前时钟；新调用仍须未过截止并通过真实 verifier。

## 提交、查询与文件

`core/execution/image_receipts.py` 分别提供：

| 函数 | 结论范围 |
| --- | --- |
| `decode_submit(request, raw)` | accepted／rejected／unconfirmed；原始摘要、实际 task ID、独立生成请求 ID |
| `decode_query(request, raw)` | pending／completed／failed／unconfirmed；严格核对所有出现的 task ID，查询请求 ID 单独保留 |
| `verify_delivery(query_request, query_raw, image_bytes)` | 对确切完成查询验证原文件 SHA、字节数、实际像素和格式；视觉状态始终 not_reviewed |

header `x-request-id` 与显式 body `request_id` 冲突、任务 ID 不一致、错误业务 envelope或非法输入不择一修复。只有 task ID 无生成 request ID时，可以确认任务被接受，但费用关联仍待证。GET请求 ID不能代替生成请求 ID。

截止后到达的完整 HTTP 可保留原始事实及直接请求 ID，但提交为 unconfirmed，不能由它生成新查询；迟到查询不提供可交付URL。费用事实与任务恢复分别处理。

文件交付函数不下载或修改图片。它要求原查询完成，对调用者取得的确切 bytes 用 Pillow 实际验证单帧 PNG／JPEG／WEBP、9:16、≤32MiB、每边≤8192且总像素≤32M。交付 hash绑定原查询及文件；尺寸完整不代表内容通过，没有伪造视觉Reviewer。

## 图片费用候选

`core/execution/image_fees.py` 提供 `candidate_for`／`verify_candidate`，复用原生 `/api/log/self` JSON解析、稳定 request ID摘要与固定quota换算。

只有**原POST捕获的明确生成请求 ID**与原生日志 `request_id`完全相等，且账户 token、group、模型、消费类型与时间窗一致，才能得到精确候选。仅有 task ID、唯一新增记录、同一时间／规格、余额差或查询ID均不足以匹配。缺少直接身份保持 `FEE_EXPORT_PENDING`；冲突或歧义停止，不猜测。

候选保留原始导出／POST／descriptor 摘要、完整金额与观察时间。即使收费超过风险也返回完整事实及 overrun／停止建议；零费用不代表图片完成。迟到或不确认提交的直接收费事实可记录为候选，但不能因此恢复任务。

本模块没有创建金融 claim、结算或释放风险；`global_claim_status=not_claimed`、`settlement_performed=false`、`risk_released=false`。重复验证是纯重放，不表示已经跨文本／图片全局去重。后续必须复用统一 claim registry，并处理媒体收费／退款和整个账户的超风险门禁，才能将候选交给正式账本。

## 下一段接线

在原 `ExecutionStore` 的同一帧／锁／事务中增加显式媒体扩展，冻结独立媒体报价与授权；保留旧六namespace及已结束文本任务原样，不修改旧账户限额或借剩余次数扩权。将统一风险／调用数、全局费用claim、停止／恢复与上述工具组件接起来，再接素材文本角色、逐镜图像和独立视觉审阅。当前仍无操作员UI、自动素材制作或新媒体交付。
