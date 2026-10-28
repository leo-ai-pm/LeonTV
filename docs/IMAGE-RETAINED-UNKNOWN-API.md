# 图片未知结果的保留风险计划 · D43

2026-10-28。[响应生命周期](./IMAGE-RESPONSE-LIFECYCLE-API.md) · [本轮验收](./IMAGE-RETAINED-UNKNOWN-VALIDATION.md)。

一次请求没有精确回执，不能据此判断它没有执行或没有扣费。D43让原运行报价明确列出必须保留的未知请求、可使用既有额度的新候选，以及额度不足的待安排镜头。新尝试使用新operation、nonce和累计序号，原未知请求不重发、不删除，也不改成成功或零费。

## 原账本中的新披露

`quote_runtime_continuation`增加显式参数`retain_unknown_risk=True`，必须同时指定D42响应窗口。新报价披露schema3，批准重建为authority schema4；媒体描述符仍沿用D42的`controlled_image_http_v3`／permit schema4。原scope、grant、D37意图、两笔资金分配、预算和执行器都不替换。

新`retained_unknown_plan`冻结以下内容：

- 原scope、grant、base runtime批准及资金事实的hash；base必须来自原生历史，版本为2或3。在原冻结操作集合未变化时，后续代码／租期续接可沿用同一计划，不能偷偷换计划；新候选加入后的再次续期不由本合同自动授权。
- 当前scope全部操作的ID、hash和累计序号，账户中的其他操作、费用和查询状态也须满足原门禁。
- 所有保留unknown的原record／descriptor／permit、保留风险及精确collector证据。原已被替代镜头的unknown仍计入风险，不重复安排已交付镜头。
- `funded_targets`只包含现有剩余额度能覆盖的候选，明确镜头、attempt、replacement及累计序号。
- `deferred_targets`说明尚缺的次数和风险预留，**没有提交序号，不得签发permit**。

报价本身不执行生成。原报价hash、明确consent、真实出生帧、当前来源、grant、代码、租期及资金的检查继续生效。旧版本报价与authority维持原结构和原限制；单独构造一个hash自洽的字典不能获得权限。

## 证据与停止条件

仅支持完整核验后的两类本地状态：原collector缺失，或原绑定有效、已结束cleanup、没有响应候选的incomplete。incomplete须有原claim和observer-closed，并只允许claim／worker／started／observer-closed四种记录。计划冻结文件名、大小、SHA以及原检查结果。

`incomplete`和候选数0单独不足以证明没有迟到响应：缺commit的record也可能得到相同摘要。因此任意新增record、commit、ready、error或文件变化，均使计划失效。每次Key前、Key后及最终collector权限判断都会重新核对原文件；报价历史时间也必须晚于原响应／cleanup结束时间。

新候选只可精确替代计划中的未交付unknown，并受同镜最多两次尝试、全部历史nonce去重、原累计次数、精确参考版本和费用门禁限制。候选检查中的临时事实视图仅排除已明确保留的unknown，以核对新候选是否允许执行；原存储和全局unknown状态不改变。任何再出现的新unknown仍停止后续付费动作。

## 当前任务的范围

本轮报价时，原scope累计上限5次，已消耗4个提交意图，剩余1次。批准的计划只使用这最后一次既有预留：

| 镜头 | 计划 | 权限边界 |
| --- | --- | --- |
| 第三镜 | 新attempt2，累计序号5，精确引用已交付第二镜 | 仅新批准的这一候选；旧第三镜unknown继续保留 |
| 第四镜 | deferred，后续需1次／1元风险预留 | 本计划不能提交，也没有增加父分配 |

本轮该名额现已执行：新POST返回HTTP403且没有可采用的任务ID，状态为unknown，当前已用5／5，不能重跑第三镜或提交第四镜。完整事实见验收记录。

原100元总授权、82.37元保留风险、54次保守账户包络和父46条记录不变。上述1元是将来所需的风险预留，不是已产生费用或本轮追加预算；后续还须在原账户内形成明确的新资金与运行计划。

状态投影区分`funded_remaining_jobs`、`deferred_jobs`和总`remaining_jobs`。已批准候选完成但还有deferred时返回`NEEDS_FUNDING_OR_NEW_PLAN`，不误报整组完成。技术交付仍不等于视觉质量通过或人工接受。

## 显式操作参数

```text
python -m core.execution.image_runtime_operator quote \
  --control /absolute/private/image-control.json \
  --valid-until <ISO-8601-time> \
  --response-window-seconds 180 \
  --retain-unknown-risk
```

示例不是可重跑的真实任务命令。该开关仅可用于quote；activate仍引用原报价和disclosure hash。输出展示保留unknown数量、可执行候选及待安排镜头，不输出剧本、图片wire或凭据。真实运行结果以[验收记录](./IMAGE-RETAINED-UNKNOWN-VALIDATION.md)为准。
