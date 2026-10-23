# 首帧视觉审阅与局部图片修订

`core/planner/asset_review.py` 提供纯元数据合同。它复验 D19 来源、分镜、文字审阅及素材清单，绑定完整图片集，使审阅结论只对确切的图片版本有效。它不打开图片、不调用模型、不创建付费许可，也没有新增产品 HTTP 入口。

```python
payload = build_visual_input(asset_payload, manifest, images, {
    "review_id": "example/visual-01",
    "manifest_hash": manifest_hash_frozen_by_controller,
})
report = validate_visual_review(review_json, payload)
scope = build_image_repair(payload, report)
revision = validate_image_revision(scope, revised_images)
```

`images` 是原镜头完整列表，每项为 `shot_id/version/sha256/width/height`。合同拒绝缺图、重复镜头、非正整数及非 9:16 尺寸；调用者仍须实际解码文件、计算摘要和核对尺寸，不能只相信输入声明。输入派生 `source_snapshot_hash/plan_version/plan_hash/manifest_hash/image_set_hash/rule_version/rule_hash`，连同独立 `review_id` 绑定报告。

报告精确包含 `binding/shot_checks/cross_checks/findings/overall_verdict`。逐镜检查恰好覆盖原镜头；多图必须有至少关联两镜的跨镜检查，单镜字段不能冒充跨镜字段。三态为 `accepted/rejected/needs_input`，总判定按明确拒绝、必要缺证、全部通过的顺序计算。重复 JSON 键、额外字段、非有限数及旧图片集绑定均拒绝，不抽取 Markdown 或修复非法 JSON。

沿用 AS01 来源忠实、AS02 共享角色与场景连续、AS03 首帧构图和动作起点、AS04 技术文件交付、AS05 独立实际视觉核验。技术检查不代替视觉判断，文本自评不代替查看图片，首帧通过不代表视频或人工终验通过。

每条 finding 记录 `finding_id/criterion/shot_ids/observation/repair_instruction/executable`。`shot_ids` 是确切修复目标；比较用的合格参考镜头写在观察中，不能因此被纳入返工。只有明确 rejected 的目标且有非空指令才可执行；缺证不能转换成一次补图调用。

全部 accepted 时不创建返工任务。其余情况生成 `image_repair_ready` 或等待补证的 scope：绑定原报告摘要，保留完整原图列表，列明目标的原版本／摘要、下一版本、finding 和指令；无关图片进入 preserve。实际执行者须独立冻结 scope 摘要和权限预算，scope 自身不是授权，不能靠重算摘要扩大范围。

`validate_image_revision` 要求提交完整新图片集：仅目标版本加一且图像摘要确实变化；其他图片全部元数据原样保留，不增删镜头。来源、分镜及共享清单保持原绑定，返回 `recheck_required=True`。新图须重新查看，旧报告不能自动继承。

本轮真实执行和限制见[验证记录](./ASSET-VISUAL-REVIEW-VALIDATION.md)。正式运行接入还需要视觉供应商适配、运行事件及产品内任务调度；独立 Codex 视觉审阅不等于已集成产品 VLM。
