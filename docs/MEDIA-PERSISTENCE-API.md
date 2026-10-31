# 完成图片的受限本地持久化 API · D47

2026-10-31。首版接入现有 `ComparisonExecution` 的隔离图片链，复用同一 `ExecutionStore.media`、规范回执及 `image_comparison_artifact`。普通制作delivery、视频、UI暂不接。404份public source／348份runtime closure已冻结，相关回归、同源码授权的原生链及关键恢复补验已通过本轮有限验证；未来真实任务仍需其适用显式授权。验收证据见[验证记录](./MEDIA-PERSISTENCE-VALIDATION.md)及[PRD](./MEDIA-PERSISTENCE-PRD.md)。

## 适用授权与调用

媒体接收默认关闭。必须在未来comparison operation出生前，对准确comparison activation完成新quote／严格consent activation，再在reserve出生记录绑定。新增 `media_persistence_quote`、`media_persistence_activation`、`media_persistence_receive`；不建独立业务／预算账本，不新增生成POST／任务查询GET／风险或正式交付。

```python
from core.execution.media_persistence_contract import (
    quote_media_persistence, activate_media_persistence,
)
from core.execution.image_comparison_runtime import ComparisonExecution

quote = quote_media_persistence(control, comparison_activation_id,
                                valid_until=explicit_until)["quote"]
activation = activate_media_persistence(
    control, quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
)["activation"]
execution = ComparisonExecution(control, comparison_activation_id)
operation = execution.reserve(
    key=stable_submit_key, persistence_activation_id=activation["id"],
)["operation"]
# 原submit及精确费用流程保持；本授权不自动执行它们。
result = execution.query(operation["id"], key=stable_query_key)
# 规范completed后同一产品入口连续接收／校验／保存／登记。
result = execution.recover(query_id, query=True)
# 仅采用同query原collector及本地字节，不重发GET或资源请求。
```

也可调用内部 `MediaPersistenceExecution(control, activation_id).receive_completed(operation_id, query_id)` 做同一幂等恢复决策。它只接原生ID，不接调用方URL、文件路径或权限回调。首次query completed、已有completed query重放及recover采用completed均接入该决策；pending／unknown／平台SUCCESS／裸URL不触发。

quote冻结原scope/task/comparison authority、控制文件、当前代码及有效期限，保留原资金／费用／未知门禁。新lease不超过comparison原有效期限，出生至多24小时。activation必须严格boolean True与准确disclosure hash，并满足原生前序checkpoint。一个comparison activation只能有一个新媒体授权，且quote／activation时不能已有operation。reserve字段在出生后不可补挂或改写；已有D45／D46任务、已消耗marker与automatic_followup_calls=0不会追授。此接口不批准B、新A、模型切换、额度追加或自动重试。

## 一次性原生意图与恢复

唯一receive意图先于任何可能发网动作持久化，绑定原operation/query/descriptor/raw/receipt、URL SHA、新媒体activation及期限。意图出生即消费机会；即使随后未证明发网，也不会补发。锁覆盖同任务并发，原生转换保证每operation最多一个receive，换key／重启／删临时文件不重置。

| 状态／证据 | 行为 |
| --- | --- |
| receiving，尚无完整字节＋proof＋耐久确认 | 只检查本地，结果不明时在有效当前权限内记unknown，不联网 |
| HTTP404／其他非200 | 原生failed，保留HTTP状态，404根因unknown；不跳转、刷新URL或重试 |
| 超时／网络或worker结果不明 | unknown，保留已消费意图；无完整及时确认不提升成功 |
| 完整字节＋proof＋及时耐久确认 | 当前权限核验后本地发布／native saved／原artifact登记；网络0 |
| saved但artifact未登记 | 明确persisted与登记未完成，恢复只用同字节 |
| 原artifact已登记、receive指针未写 | 精确对账原operation/query的artifact事实，单列pointer_pending；本地同步，不重下载 |
| 当前代码／控制／grant／父／lease无效 | 保留原文件与原状态，只读返回needs_input；不新写unknown／saved或发布文件 |

`_proof`为纯读验，发布及原生写各自重新核对当前绑定／代码／权限／lease。过期时，已有完整part+proof或blob也不能借重放继续写入。所有原Key前／后／collector独立新鲜边界及120／180／5任务响应合同保持。

## 有界资源与私有证据

- 资源请求最多1次；只取规范completed回执的唯一精确HTTPS URL，标准443、无userinfo／fragment，拒绝私网／保留／混合DNS地址。连接固定一个已校验地址，TLS保留hostname与证书验证；无DNS二次解析、redirect／HEAD／Range／fallback、API Key／Cookie或代理环境。
- 原始字节最多2MiB，头部与实际流双重限额；拒绝编码体、非图片MIME及格式不匹配。完整复用`verify_delivery`：PNG／JPEG／WEBP，原32Mi像素、8192单边限制，保留准确原字节SHA／size／尺寸；不提升质量结论。
- worker启动至网络／完整解码／字节、proof及确认写入受45秒绝对截止；父进程timeout与worker独立SIGALRM双重夹紧，按started_at扣启动延迟，并不晚于lease减5秒；清理至多5秒，网络分项至多10秒。DNS阻塞也在独立截止内。
- 意图事务与当前新鲜检查时间另计。意图出生前要求剩余lease大于120秒；worker启动前大于50秒。网络后本地发布、native saved及artifact另受当前lease门禁，不延长原期限；分段实际耗时单列，不用45秒宣称整条链SLA。
- 固定私有execution子目录 `media-persistence/`，目录0700／文件0600。每层O_NOFOLLOW，拒绝symlink、硬链接替身、非普通文件、覆盖或任意路径。part用O_EXCL；原子不覆盖link发布，再fsync目录，随后删除同名part。精确link/unlink间隙只在证明两个固定名字为同inode时本地收尾。
- 每execution媒体证据最多32MiB／64个文件或预留位置（另有一个空容量锁文件）；未完成part保留2MiB＋18KiB及4个临时条目额度，目录独占容量锁防并发超配。失败文件不自动清理，也不因清理重置原生机会。

每个意图使用固定 `<id>.part`、`<id>.blob`、`<id>.ready.json`、`<id>.commit.json`。worker只持有预先打开的字节／proof／确认FD，无路径能力；完整解码后fsync字节与proof，**之后**取confirmed_at并写与proof SHA和意图hash绑定的确认。JSON字段时间早于截止不能单独证明耐久。恢复要求完整确认且durable_at在worker45秒／lease边界内；缺失、部分、错hash或明确迟到拒绝。

## 分层状态与安全摘要

返回 `accepted/completed/received/persisted/artifact/needs_input`，以及quality=`not_reviewed`、human_acceptance=`pending`、retry_authorized=False。不返回签名URL、Key或raw。received／persisted只在本次完整本地复验后为True；当前文件丢失／损坏为False。权限／存储异常而尚未复验为None／not_verified，needs_input=True；`historical_saved`和原artifact事实仍单列，不把历史元数据当当前可用。

`artifact_id`以准确原native artifact为准，`reception_pointer_pending`说明两次原生提交间未同步。原artifact存在不保证当前文件仍可用。已存但登记失败保持partial；部分transport错误归`NETWORK_OR_WORKER`，不推断服务端执行、费用或404根因。

D46仍是accepted／completed、平台费用0.04元、一次404且无字节／artifact的历史结果。本轮不取回旧图、不清unknown、不加入正式图集或生产参考；正式2/4、质量未审、人验待进行。该内部切片不等于全产品／视频／AB／完整Harness验收。
