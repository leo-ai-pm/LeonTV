# 从已验收首帧交接视频任务

`core/planner/video_brief.py` 是不执行网络、读图、授权或计费的纯交接层。

```python
brief = build_video_brief(visual_payload, visual_report)
# expected_brief_hash 由执行者独立冻结，不能直接信任待核 brief 自报的摘要。
checked = checked_video_brief(
    brief, visual_payload, visual_report, expected_brief_hash,
)
```

输入重建并核对完整来源、15 秒 9:16 分镜、素材清单、当前图片版本／摘要及独立视觉报告。只有当前图片集的 `overall_verdict=accepted` 才能交接；旧图通过结论不能给新图片使用，拒绝或缺证不产生视频简报。

输出包含 `binding/duration_ms/ratio/style/timeline/characters/scenes/prompt/brief_hash`。binding 继承视觉审阅的来源、计划、清单、图片集和规则引用，增加实际视觉报告摘要，并区分上游文字审阅及其规则。timeline 逐镜保留原 ID、顺序、时长、画面、机位、音频，派生连续起止时码，并引用对应图片顺序、版本和摘要；总时长必须恰为 15000ms。

两种视频模型可以复用同一份编译 prompt：按原时码、原镜头内容和参考图对应关系生成，角色／场景共享定型来自原清单，不另造动作、对白、人物或字幕要求。它只是模型输入；模型是否实际按时码切镜、保留角色、演绎动作及生成正确对白，必须检查真实视频。

`checked_video_brief` 从原父输入重建全部字段并要求全等，同时核对执行者独立冻结的摘要。手改 prompt、时码或图片引用后再重算自报摘要仍不能通过。严格输入及固定中性错误沿用现有合同风格；不在异常里输出来源内容。

这一层不选择供应商、参数或价格，不创建调用许可、运行事件或生产 HTTP 路由。实际执行者还须核对文件与公网参考图有效性、原授权与历史模型调用次数、预算、稳定请求意图、回执恢复及实际收费。当前验证状态见[双模型视频实测记录](./VIDEO-GENERATION-VALIDATION.md)。
