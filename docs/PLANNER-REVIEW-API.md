# 独立分镜审阅与返工范围 · D18

这是可复用的 Python 合同接口，供受控编排器装配审阅上下文、校验模型判断并准备返工任务。它不新增 HTTP 路由，不签发权限或额度，不启用正式生产。实际调用与核费由外层控制器负责。[验证与实际结果](./PLANNER-REVIEW-VALIDATION.md)。

## 独立审阅

```python
from core.planner.review import (
    RULE_HASH, RULE_VERSION,
    build_review_input, review_messages, validate_review_output,
)

payload = build_review_input(source, accepted_plan, binding)
messages = review_messages(payload)
report = validate_review_output(original_assistant_content, payload)
```

调用者必须先证明该确切计划已经过实际提交工具接收并核费。纯接口不能用候选计划或模型自报来证明这个前提。输入沿用原 `check_source`／`checked_plan`，保持 source snapshot、严格 ShotPlan 与版本绑定。Python 输入也只接纳原生 JSON 树，复制过程不能把错误容器转换成合法结构。

`binding` 精确包含 `review_id/run_id/intent_id/task_id/plan_version/plan_hash/source_snapshot_hash/rule_version/rule_hash` 九项。`plan_hash` 使用现有 `creator.digest`；规则为 `D15-RV-v0.2`，提供五条完整标准及原验收文档的版本摘要。旧 Planner 的执行规则引用与 Reviewer 的 RV 规则包分别核对，不混用摘要。

载荷精确包含 `contract_version/binding/source/plan/rules/prior_findings`。首次审阅的 prior_findings 为空；本接口暂只支持首审。source 保留完整冻结对象，并由服务端按原 Unicode 字符下标切成最多 800 字符的段，提供 `SRC0001` 等固定 ID、start/end 和原文；不摘要、不修剪、不让模型猜字符偏移。plan 保留实际原对象及逻辑引用。实际文件路径、账本、配置、Key及原模型 HTTP 回执不进入审阅载荷。

`review_messages` 重新核对载荷，只返回新的 system＋user 两条消息。生成者的历史、推理、自评和预设通过结论不进入审阅上下文；来源或计划中的命令按待审数据处理。角色分离的同模型调用不等于 Judge 可靠性已经校准。

## 五条判断与结果完整性

| 规则 | 审查范围 |
| --- | --- |
| RV-F | 关键事件、因果、对白与人物关系是否忠实于冻结来源及选段 |
| RV-T | 每镜对白、动作、反应与转场能否在文字层面的时间安排中容纳 |
| RV-C | 人物、物件与空间状态是否连续，跨镜变化是否有交代 |
| RV-A | 可见动作与运镜是否明确、相容，并能承接叙事 |
| RV-G | 现有字段能否转成明确且不矛盾的后续生成输入 |

每项判断为 accepted/rejected/unknown。输出须原样回显 binding、恰好列齐五条规则，并提供 checked_scope、有效来源段与镜头字段定位、判断依据及 findings。非 accepted 项必须有可定位的问题或缺证、影响、最小修改／保留范围及复核条件。没有“每项机械枚举全部镜头”的额外格式门槛，必要覆盖仍按原标准审查。

镜头定位为现有 shot_id＋`id/duration_ms/visual/camera/audio/depends_on` 之一，或 `{"shot_id":"*","field":"$"}` 表示整计划。未知镜头、来源段、字段或错误组合拒绝。finding_id 在本次审阅内唯一，首审 prior_finding_id 为 null。

原正文必须是单个严格 JSON，最多 65,536 UTF-8 字节；拒绝重复键、非有限值、额外字段、Markdown 提取、补写或人工修复。聚合由控制器复算：任一 rejected 则 rejected；否则任一 unknown 则 unknown；五项 accepted 才 accepted。失败仅抛固定 `ReviewValidationError`／`REVIEW_INVALID`，不带私有内容或原异常。

输出通过校验仅证明绑定、结构与定位有效，不能机械证明判断正确。格式／绑定无效时内容记 unknown，不能当作计划内容 rejected。完整 HTTP、输出完整性、审阅结果、费用与人工接受分别记录；未核费不跨下一执行门。推理文本只留在私有 raw／parsed 归档，不拼入工具或后续上下文。

## 版本化返工任务

```python
from core.planner.repair import build_repair_task, validate_repair_candidate

task = build_repair_task(payload, report)
candidate = validate_repair_candidate(new_plan, payload, task, report)
```

accepted 返回 None，不为演示制造返工。存在 unknown 或缺少有效可编辑字段时返回 needs_input；否则为 content_repair_ready。任务绑定原来源、父计划版本／hash、review_id、report_hash、规则和下一版本号，列出问题引用、显式可编辑字段、保留字段及依赖后继复核范围。依赖后继不会自动获得编辑权，保留要求优先。

候选必须与原 report 重新生成的任务一致，继续通过原严格 ShotPlan 检查；镜头成员、顺序、ID、来源、规则、总时长和画幅不变，范围外字段不能改，也不能用未变更的计划冒充返工。错误仅为固定 `RepairValidationError`／`REPAIR_INVALID`。增加／删除镜头及重排不属于当前范围。

这些函数不调用模型、不改旧计划、不提交新版本、不自动获得返工／复核额度。结构有效的候选仍须实际提交、另存新版本、核费并独立复核；旧版总体审阅不能自动继承给新版。D18 的真实范围只允许一次 Reviewer，实际改稿、复核、媒体和人工终验另记状态。
