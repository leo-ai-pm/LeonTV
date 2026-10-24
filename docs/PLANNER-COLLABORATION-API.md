# 规划到独立审阅的产品交接 · D23 v1

本迭代把已有独立首审合同接入正式 Planner 的私有持久任务、工作线程和 `/creator`。仅显式离线场景执行合成响应；原 normal 与旧离线任务保持原语义。此合同不授权真实模型、媒体或新费用，不代表真实内容或完整自主协作已经验收。

## 创建与自动交接

沿用 `/api/planner/offline-tasks`、既有创建／动作幂等键与版本规则，不另造任务 API 或运行系统。新增 scenario：`review_accepted`、`review_rejected`、`review_unknown`、`review_response_unknown`、`review_invalid`。旧五种 scenario 保持不变，正常 `/tasks` 不接受 scenario 或执行开关。

创建后仍 ready。一次显式 `resume` 后，现有 worker 自动完成 Planner 的读取与提交工具，再把确切已接收计划交给独立 Reviewer，不需要中途点击“开始审阅”。Reviewer 只使用由 `build_review_input` / `review_messages` 重建的全新 system＋user 上下文；不继承 Planner 的历史、推理或自评。角色调用共用原任务执行锁、intent／receipt、步数上限和事件日志。全部角色合计最多6次调用，Reviewer最多1次，真实付费调用上限为0；暂停、重启或切换角色不能重置。

Reviewer 的九项 binding 由服务端生成并绑定确切来源、当前计划版本／hash和规则。先持久调用意图，后执行响应；先持久回执，再校验报告及生成返工提案。Reviewer 响应不能进入 Planner 的 `execute_tool` 解释路径。

## 任务投影

旧任务的返回字段不变。只有上述新场景追加 `collaboration`，列表与详情中公共字段相同：

```
{
  schema_version: 1,
  evidence_kind: "synthetic",
  roles: ["planner", "reviewer"],
  status: "awaiting_plan" | "review_ready" | "review_running" |
          "accepted" | "rejected" | "unknown" | "response_unknown" | "invalid",
  planner_call_count: number,
  reviewer_call_count: 0 | 1,
  max_total_calls: 6,
  max_reviewer_calls: 1,
  paid_call_limit: 0,
  binding: null | {review_id, run_id, intent_id, task_id, plan_version,
                  plan_hash, source_snapshot_hash, rule_version, rule_hash},
  report_hash: null | sha256
}
```

`step_count` 是包含两个角色的总调用意图数，两个角色计数相加必须等于它。binding 的 run_id、task_id 均为本 Planner 任务ID，intent_id 是唯一 Reviewer operation ID，规则沿用 D18。尚未委派或创建调用意图时 binding 可为null。列表不含报告、原始上下文、模型原文或来源正文。

详情的 `collaboration` 另有 `report`、`repair_task`，初始为null。report是 `validate_review_output` 接收的完整结构化首审报告，report_hash独立匹配它；repair_task为 `build_repair_task` 的结果或null。不得输出原始模型回复、完整审阅上下文或私有路径。详情中的binding必须与当前计划版本、计划hash、来源snapshot以及报告完全一致。

实际 `quality_status` 仍为 `not_reviewed`，execution_enabled仍false。这里 accepted／rejected／unknown是**合成审阅的流程结果**，不能显示成实际作品已验收。

## 分支与恢复

- accepted：任务到plan_ready，保存五项合成审阅结论；repair_task为null。
- rejected：任务blocked，代码`REVIEW_REQUIRES_REPAIR`，保存有限返工提案，不自动改稿或接素材。
- unknown：任务blocked，代码`REVIEW_NEEDS_INPUT`，保存needs_input提案，不当作合格。
- 非法格式／错误绑定：任务blocked，代码`REVIEW_INVALID`，无有效report，不把格式错误伪装为内容拒绝。
- Reviewer调用无确认回执：沿用response_unknown／已暂停及原未决操作，不能自动重调。已有received回执时恢复只处理它，不再调用Reviewer。

stop阻止后续角色委派；在途返回仍保存原回执，保持暂停直到显式resume。恢复沿用同一个任务、计划、意图和累计次数。计划与报告版本不匹配时拒绝，不用旧审阅给新版本放行。拒绝／缺证结果只准备返工范围；真正改稿与复核仍是后续开发，当前首审合同不能伪作复核合同。

## 存储兼容

继续使用原PlannerStore与完整性链。新场景采用精确的嵌套collaboration记录，并为原operations标识角色；旧场景保留原payload与operation字段集。不迁移、补字段或重算历史frame摘要，不读写原付费试验账本。正常任务仍待授权、调用为0。旧实验结果不导入为本轮生产授权。
