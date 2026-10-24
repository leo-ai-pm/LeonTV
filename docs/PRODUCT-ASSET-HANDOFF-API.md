# 产品素材交接 · D28

将原受控文本执行中确切接受的分镜交给素材准备层，新增正式只读接口及 `product-assets-v1`。它支持 1–12 镜，解决旧 D19 三镜合同无法接收 D26 四镜计划的问题。旧 D19 schema、输出及已有媒体保持原样。[验收记录](./PRODUCT-ASSET-HANDOFF-VALIDATION.md)。

## 正式交接入口

`GET /api/planner/tasks/{task_id}/execution/asset-handoff`

复用原执行路由和 `private_inspection`，只读原 journal，不走 `get/recover/transaction`，不创建任务、启动 worker 或观察线程。参数只接受任务 ID，不接受正文、查询参数、外部图片 URL 或本地路径。实际静态挂载隔离检查与原回执检查一致。

服务端从同一已验证快照要求：

- 任务确为 `bounded_revision_v1`、`plan_ready`，角色状态为 accepted，最新计划与 `accepted_plan_version` 精确一致。
- v1 使用实际首审；v2 使用完整首审拒绝→受限改稿→复核接受链。不能把复核报告裁剪成一次新首审。
- 当前任务的原调用全部已结算，存在原始回执及对应规范化结果；经验风险模式须已匹配平台费用。同账户超风险记录、未知、未核费或未接受状态不可交接。
- 对应 Reviewer／Rechecker 的实际原回执重新解析后，与保存的接受报告、绑定、计划和来源完全相同。客户端不能提交一份自称 accepted 的报告替代它。

不存在返回 `NOT_FOUND`／404；不满足交接条件返回固定 `ASSET_HANDOFF_UNAVAILABLE`／409；原 journal 损坏沿用 `CORRUPT_STORAGE`／503。错误不回显原上下文、剧本或存储路径。

返回 `handoff` 包含安全来源摘要、确切计划版本及原计划、接受报告／输入摘要、实际接受操作 ID、素材输入摘要、逐镜准备记录、预算历史投影及 `handoff_hash`。逐镜记录状态为 `awaiting_manifest`，整体为 `awaiting_asset_authorization`；没有凭空补角色造型或声称图片已生成。

`permissions` 中素材文本、图片提交、视觉审阅、返工及付费调用全部为 0。原文本 grant 没有获得媒体权限。`inherited_budget` 来源是冻结执行 journal，明确 `is_current_balance=false`；保留原预算、期初风险／次数、范围和账户计数，不表示实时余额或新的可支配额度。风险不是消费。

完整素材 Agent 上下文由服务端内部 `build_asset_context(runtime, task_id)` 构造；公开投影不包含完整 source、接受输入／报告、原始回执、隐藏推理或 provider 请求参数。查看此接口不要求当前策略未过期或密钥仍配置，不会恢复历史范围。

## 新版素材清单

`core/planner/product_assets.py` 复用旧素材的共享角色／场景模型、安全文本限制及服务端提示词编译；单独定义明确的新合同及多镜 schema。

```python
payload = build_asset_input(acceptance_kind, acceptance_payload,
                            acceptance_report,
                            {"asset_task_id": "example-assets", "manifest_version": 1})
messages = asset_messages(payload)
manifest = validate_asset_output(model_json, payload)
jobs = image_jobs(payload, manifest,
                  expected_manifest_hash=manifest["manifest_hash"])
```

`acceptance_kind` 为 `first_review` 或 `recheck`，分别严格重建原 accepted v1／v2。binding 冻结完整接受输入与报告摘要、来源快照、计划版本／摘要、review ID、规则及素材任务 ID。新清单不能改写来源或接受类型。

每个原镜头恰好一张 9:16 写实影视首帧，保持原镜序、时长与依赖；同一角色、服饰、环境及光线全局定型一次。只允许内部前序镜引用；本 profile 要求依赖已按原镜序排列，无法满足时停止，不重排或删镜来凑素材。全局定义须实际使用，后镜不能引用未生成的未来镜头或任意 URL。

输出不含 provider、路径、Key、工具权限或伪造的媒体链接。严格 JSON、完整镜头覆盖及独立冻结的 manifest hash 校验通过后，得到的仍是文本图像任务描述；真实文件、尺寸、内容和费用须由后续媒体执行器验证。

## 视觉审阅与局部修订

原 `asset_review.build_visual_input` 根据明确的 `product-assets-v1` 选择新版素材校验器；默认 D19 路径不变。原 AS01–AS05 规则、完整图片集绑定、拒绝／缺证语义与定点版本修订继续复用。

四镜完整图片集可以仅修订一个被拒镜头，另外三镜的版本、摘要和尺寸保持原样。修订后的完整集合有新 `image_set_hash`，旧报告不能用于新集合；缺图或不可判断不会转换成一次补图授权。纯元数据合同不证明实际查看过图片，也不提供全生命周期返工次数或费用许可。

## 图片交付身份

通用土豆图片客户端继续使用服务器指定的稳定 `save_path`：已有平台 task 只恢复原 task，unknown 不重提。缓存交付须有原字节 SHA256／大小绑定且与本地文件一致，不能仅凭文件非空复用。旧无绑定记录通过原 task 重新交付，不把现存文件直接认作原结果。

已完成任务的图片链接失效时，仅允许通过原 task 查询刷新并有界重试下载；不创建新的付费提交。文件身份完整不等于图像内容或视觉质量通过。素材调度仍须实际解码图片、记录尺寸并将精确字节交给独立视觉上下文。

## 尚未接通

本轮没有素材文本／图片／VLM 的新付费请求，没有操作员 UI 或自动媒体制作。本接口是正式产品里的素材交接准备层。后续在同一执行主链增加媒体工具权限、稳定逐镜输出路径、多模态回执与独立媒体费用适配，再接实际素材 Agent、视觉审阅和局部返工。现有 Chat token 费用收件箱不能直接充当异步图片账单适配器，旧 VLM 包装器的隐藏重试也不能进入受控调用链。
