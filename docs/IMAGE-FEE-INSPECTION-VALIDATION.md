# 图片费用只读诊断与平台证据 · D33

2026-10-25，代码 `e8783ae`。[接口与命令](./IMAGE-FEE-INSPECTION-API.md)。

已新增独立的只读核费诊断入口，并恢复登录平台的费用/任务观察。真实首镜仍为accepted、已交付、原生费用待认领；后3镜未提交。本轮没有生成、改账或续开权限。

## 冻结实现与检查

公开后端从312文件增加为315文件，仅新增 `image_fee_inspection.py` 和两个测试文件；旧312文件SHA全部不变。新入口复用原control、ExecutionStore只读重建、POST解码和直接身份核费函数，不装配执行runtime、collector、费用观察器或Key。

同一最终源码的定向回归 **237 passed，49.12秒**：

| 范围 | 测试项 |
| --- | ---: |
| 新纯函数与CLI | 57 |
| 新原生集成/实际闭包 | 4 |
| 既有图片费用、回执、策略与包装回归 | 176 |

61项新增检查已包含在237项内；作者、独立及统筹复跑不相加。新增3文件Ruff通过。父进程和detached worker继承socket封禁，外部网络尝试0。315文件清单在隔离验收、复制及提交前后逐文件核对；封禁辅助文件未复制或提交。

D32的2230项/73子测试是上一轮完整回归。本轮只新增独立只读入口、未改旧实现，运行相关定向集，不将历史全量与本轮计数拼成新的完整回归。前端未改，未重复UI/build或重启共享服务。

## 验证行为

- 标准POST头、正文request_id、异步task ID和GET身份分开；缺失、冲突、非法身份各有固定诊断，不能由时间、任务ID、GET ID或唯一消费行替代。
- 坏导出不会掩盖缺失身份；导出是否有效、同账户模型行数及精确候选状态分列。金额未知保留null；只有原candidate_for成功才显示候选金额。
- 候选匹配、费用已观察、交付完成与派发权限分列。诊断不授予导入或执行权限，scope剩余次数不代表可以调用。
- 已有费用与新导出冲突时，保留原生金额，单独显示当前候选和冲突。合成用例的¥2.50候选不能覆盖原¥0.04记录或增加claim。
- 原生集成实际经过合成collector的accepted/missing-ID POST、completed GET和delivery。禁止runtime/Planner/collector/inbox构造、transaction、get/recover、Key loader和socket后，诊断仍成功；原三个store字节及请求计数不变。
- observe_only、到期及代码漂移下仍可诊断合法历史；错误operation、query/text/task ID或另一个有效控制范围被拒绝。
- 纯输入的账户namespace必须匹配descriptor；CLI缺control时仅返回off，带operation/export却不给control会安全拒绝。导出保留原0600、owner、单链接、静态隔离、无symlink及8MiB上限。
- 输出不含原provider task/request ID、剧情、raw、URL、token/group、完整导出或私有路径。标准头之外是否有上游ID继续为unknown，不将未保留的头断言为供应商未返回。

## 真实首镜只读重放

原始封存回执重放稳定复现：accepted task存在，但直接POST请求ID不存在。该历史事实没有被修复或补写；本轮解决的是诊断可见性，尚未实现新的费用认领合同。

使用实际新CLI分别读取“不带导出”及“带原始平台费用导出”的两份报告，结果一致：

| 项目 | 结果 |
| --- | --- |
| 原生operation | accepted |
| POST身份 | missing |
| 诊断状态 | missing_submit_request_id |
| 原任务查询 | 原有2次：pending/completed |
| 交付与质量 | 已交付，not_reviewed |
| 原生费用与当前精确候选 | 均为null，fee_observed=false |
| 加入消费导出后 | 导出有效，同账户/模型消费行1条；仍不认领费用 |
| 剩余计划次数 | 3，仅表示原scope计数余额 |
| 诊断授权/自动后续调用 | import=false、dispatch=false、0次 |

真实重放禁止全部执行/密钥/事务/网络路径，禁止动作触发0次；1980个原受保护文件SHA全部一致，包含原生journal、父账本、旧图片、控制、回执和配置。原POST/GET仍为1/2，没有新增claim、scope、grant或intent。

## 已取得的平台事实

按用户继续指示重建浏览器TaskSpace60，已有登录有效。读取原生网页已请求的接口，未猜新模型/端点或生成任务；浏览器最后正常finish，原始响应保存在本地私有证据中。以下quota金额按当前冻结来源的500000 quota/CNY折算。

1. `/api/log/self` 返回200：同固定token/group/model下有1条消费记录，quota折算 **¥0.040000**，有平台request_id，但不含首镜task ID。
2. `/api/task/self` 返回200：精确匹配首镜原始accepted task ID的记录仅1条，status=SUCCESS、model/group符合，user_id与该消费页一致；该任务记录的quota折算也为 **¥0.040000**。
3. 任务记录的完整原文不含该消费request_id。两边金额、账户和时间相符，仍不能按现有“直接POST请求ID”合同建立canonical费用认领，也不能只凭相符金额关联两行。

任务quota是直接绑定当前task的平台观察；本轮没有证明其等同最终账单，也没有将它伪装成原POST请求ID。不能把两处同额观察相加为¥0.08。任务原文包含制作输入，仅私有保存，未进入公开仓库或安全CLI。

浏览器恢复已完成，不再等待用户重复提供Key或登录。Ego Lite提示有更新，本轮未升级。

## 预算与权限保持

原100元授权、完整风险 **¥81.37/保守53次**、父账本45条记录均未改；风险不是消费。素材既有唯一核费¥0.013906保留，首镜原生费用仍待认领，旧unknown和视频失败记录保持原样。

新增诊断模块位于代码闭包目录，因此旧D32清单不包含它。实际验证：复制前旧控制的代码权限检查通过，复制后新派发检查拒绝，历史只读仍通过。这是显式代码批准边界，不能仅改原control中的manifest、延长原policy有效期或新建grant绕过；本轮没有续权。

## 下一步

下一切片应把已取得的原生任务费用证据纳入独立、可重建的合同：绑定实际POST/task、账户及原始任务导出，明确task.quota的平台观察语义，并处理task身份与直接request身份的全局去重。现有直接ID核费函数保持原合同，sealed raw不改。

随后接同一scope的显式代码/权限续接，保留原allocation、预算、已用次数、旧permit、版本及交付；到期续接需要独立版本合同。当前首镜费用及权限闭合后，继续原计划余下3镜，再接完整图集视觉Reviewer及条件局部返工。

本轮交付只读诊断和真实平台证据，费用写入/续接尚未接通，四镜制作、产品VLM、视频/音频和人工终验仍待完成。代码本地提交，未推送GitHub。

## 后续 D34

原生任务费用认领与原scope代码续接已另行实现。首镜¥0.04观察进入统一账本，第二镜续行停为unknown；原始回执、旧版本、次数和风险继续保留。完整验收与当前性能阻断见[D34记录](./TASK-FEE-CONTINUATION-VALIDATION.md)，本页原验收结果为历史事实。
