# 素材角色与分镜首帧合同

后续：[D28产品素材交接](./PRODUCT-ASSET-HANDOFF-API.md)新增显式多镜profile与正式只读入口；本页D19／D20历史合同语义保持。
这一层将已通过独立文字审阅的分镜交给素材角色，产出角色／场景定型和逐镜首帧输入。它是纯 Python 合同，不新增 HTTP 接口、供应商授权或后台自动生成入口。真实执行结果另见 [验收记录](./ASSET-HANDOFF-VALIDATION.md)。

`core/planner/assets.py` 提供：

```python
payload = build_asset_input(review_payload, review_report,
                            {"asset_task_id": "example-assets", "manifest_version": 1})
messages = asset_messages(payload)
manifest = validate_asset_output(model_json, payload)
jobs = image_jobs(payload, manifest, expected_manifest_hash=manifest["manifest_hash"])
```

输入复验原 source、ShotPlan、Reviewer 规则及 accepted 报告的确切引用；合同通过不证明这些输入来自一次真实、已核费的调用，执行者仍须核对持久回执和授权。上下文为新 system＋user，携带完整冻结来源及计划，不携带 Planner 历史、隐藏推理或自评。

模型返回角色／场景共享定义和每镜构图、动作起点及引用。输出只能覆盖既有镜头，不编辑剧情、镜头 ID、时长或原依赖。程序将共享外貌、服饰、环境、光线及单镜描述拼入完整首帧 prompt，计算清单摘要。后续任务引用已冻结的摘要；图像引用只允许先前生成的镜头，不能把任意外链写入清单。

实际调用器负责供应商、规格、价格、总预算继承、一次提交意图、回执及逐步核费；清单本身不构成付费许可。生成图片仍需验证实际尺寸、文件及内容，不能将文本 accepted 继承为图片视觉合格。

土豆图片客户端保留原四位置参数调用，并支持显式稳定输出路径：

```python
client.generate_image(prompt, references, "9:16", save_dir,
                      save_path="/private/task/shot_01/frame.png")
client.resume_image("/private/task/shot_01/frame.png.tudou-task.json")
```

固定路径提交前保存 `submitting`，同一路径加排他锁。已有 task_id 只继续原任务；无 ID 的未知结果和失败终态阻止新提交。已完成结果保留于回执，下载或来源 sidecar 保存失败可补交付。旧随机目录调用仍会为每次新调用选择新路径；需要防重的任务必须复用显式 `save_path`。图片来源链接保存在私有 sidecar，可能过期。

本轮接线采用土豆专用 [异步图片文档](https://docs.ai-tudou.net/gpt-image-2(new)-docs.html)，与模型广场的通用示例分开核对：`gpt-image-2-all`，POST `/v1/images/generations/async`，GET `/v1/tasks/{task_id}`。观察价目不能代替实际账单，也不构成硬计费上界。
