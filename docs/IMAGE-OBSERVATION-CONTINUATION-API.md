# 已受理图片的仅GET续查 · D40

本合同让原生任务在代码或运行lease变化后，明确批准**查询同一个已受理图片任务**。它沿用原scope、grant、D37 intent及真实4+1资金分配；原POST、受理回执、旧unknown、已用次数和费用认领保持。管理批准、新GET、文件交付和视觉质量分别验收。

## 原任务与新观察授权

`image_observation_continuation`在原`ExecutionStore.media`中追加`image_observation_quote`和`image_observation_activation`，不创建另一套执行账本。报价绑定原v2 POST的operation、descriptor、完整raw、规范receipt、capture时间和accepted task ID，同时绑定原控制、scope／grant、素材来源、既留资金证明、当前精确代码闭包、新观察lease与出生checkpoint。

批准仅允许GET，新增预算／付费POST额度为0。原运行时授权可以作为历史事实读取；观察授权不修改其代码、日期、nonce或策略，也不能用于新的图片提交。旧v2跨runtime revision直接查询仍然拒绝。

报价和批准各自只能单独追加一帧，使用原任务锁、精确披露hash、严格`consent=True`和相邻checkpoint CAS。批准按原POST分别形成版本链；新派发必须使用该POST最新观察批准。新lease须在报价时有效且不超过24小时；每次新GET的独立permit仍最多120秒。相同有效批准的元数据重放不会因查询次数已经耗尽而创建新范围或重置次数。

## 接口

| 接口 | 合同 |
| --- | --- |
| `quote_observation(control, operation_id, *, valid_until)` | 从原控制、真实当前历史和固定父证明生成仅GET披露；不发请求或预留新资金 |
| `activate_observation(control, quote_id, *, disclosure_hash, consent)` | 记录或重放精确同意；不自动查询 |
| `authority_for(payload, activation_id)` | 纯重建指定历史观察批准，不声明当前有效 |
| `validate_authority(value)` | 严格检查完整规范结构；调用者自封JSON不能授予权限 |
| `verified_current(control, activation_id)` | 每次独立重读原控制及实际原生状态，核对来源／grant／代码／lease／原collector／固定父资金，返回该次的`(payload, authority)` |
| `verify_current(control, payload, activation_id)` | 独立执行上述新鲜检查，再要求输入与实际状态一致 |
| `candidate_gate(payload, candidate, *, exclude_candidate=False)` | 纯检查完整GET候选、精确父请求、最新观察批准、累计查询、nonce及全局停机事实；不能替代新鲜控制核验 |

`verified_current`的snapshot只在该次同步权限决定中用于原生候选检查，不保存到control／runtime，也不跨越Key边界复用。原受理POST必须仍有唯一、绑定正确、已通知的collector原始回执，与原生raw完全一致；缺失时拒绝，不补造历史证据。

## GET描述符与原执行器

新`controlled_image_observation_http_v1`仅支持GET和固定`/v1/tasks/<原accepted task>`端点，wire为空。schema3的`ImageObservationPermit`绑定观察批准、原operation／descriptor／raw／receipt／task身份和独立capture／nonce；`paid_calls=0`、`risk_cny="0.000000"`属于本地权限合同，不是最终账单声明。

描述符保留一次完整`parent_submit`及原raw，供规范查询回执与交付重建；不再顶层重复参考图或父POST的wire。实际大小包含worker参数、binding和最坏长度凭证JSON，在保存新GET意图前核验原IPC上限。

`ImageObservationExecution`继承原媒体执行器，使用原锁、transaction、`_run`、`ControlledImageCapture`、detached worker和ReceiptStore。quote／grant／reserve_submit／submit／revoke入口明确拒绝；只读恢复及交付导入限制为所选原POST和对应观察查询。

Key前、Key后、collector最终检查分别取得新的实际snapshot；每一次同时核验控制和该snapshot中的完整原生候选。原120秒期限不变，没有跨请求许可缓存。普通v1／v2执行路径继续使用原合同。

## 查询次数和全局门禁

原POST的所有历史查询共同计数，包括v2查询、其他观察revision、reserved／submitting／unknown；新批准不能重新发一份查询额度。新GET序号必须是历史总数加一，不超过原policy上限。nonce与全部历史媒体POST／GET比较，不只比较同一revision。

只有精确披露的那个旧媒体unknown，以及披露允许观察其待核费状态的所选accepted POST，能从临时媒体事实视图中排除。文字／素材中相同ID记录、其他unknown、待核费、超额、未结文字操作及未决查询仍阻止新GET。全局事实及原记录不修改。`exclude_candidate=True`只允许精确匹配已保存、reserved或submitting的同一查询，不能忽略任意ID。

未知／过期停止，不自动轮询、重试或重新提交。只读collector恢复不产生新的HTTP请求；已开始操作的重放沿用原始证据。

## 显式CLI

以下参数均为占位符，不是私有任务、凭证或路径。

```sh
python -m core.execution.image_observation_operator status
python -m core.execution.image_observation_operator quote --control CONTROL --operation ORIGINAL_POST --valid-until NEW_EXPIRY
python -m core.execution.image_observation_operator activate --control CONTROL --quote QUOTE --disclosure-hash HASH --consent
python -m core.execution.image_operator --control CONTROL --observation ACTIVATION query --operation ORIGINAL_POST --key NEW_QUERY_KEY
```

缺省管理模式为off，无环境变量隐式启用。管理CLI只能status／quote／activate，stdout返回固定元数据与错误码，不加载Key或执行模型。原图片CLI的`--observation`与两种旧continuation选择互斥，只允许status、query、recover、task-fees-import、delivery-import；选中观察批准不启动查询。

`dispatch_available=false`及`query_available=false`的status表示没有自动派发；`current_authority_valid`只报告精确批准当前是否仍可核验。显式查询仍需完成候选、次数及新鲜门禁，不能把状态页当作通用凭证。

## 单次校验中的引用证明复用

`reference_derivation.references_from_proofs`只在一次`validate_state`作用域内保留成功的纯校验。键包含严格完整JSON输入和实际encoder；最多32项／16MiB，返回防御副本，异常不缓存，嵌套作用域恢复，线程隔离，退出显式清空。容量不足时执行原完整校验，不跳过检查。

实际来源解析、逐帧历史、类型、引用身份、费用及候选门禁仍逐次执行。该纯计算优化不授予权限；各次独立控制判断和Key边界仍重新读取真实状态。
