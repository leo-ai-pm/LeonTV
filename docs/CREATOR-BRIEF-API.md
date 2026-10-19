# Creator Brief API · D07 v1

冻结日期：2026-10-20（Asia/Shanghai）；LTV-CREATOR-BRIEF-001。正式本地FastAPI草稿功能，D06不变。保存是真实私有持久化，**不授权、不创建run、不调模型、不生成媒体、不付费**。本版不引入账户或多用户鉴权。

## 1. 路由

前缀`/api/creator/briefs`，by-key静态路由先于`/{brief_id}`注册。

| 方法/路径 | 成功响应 |
| --- | --- |
| POST `/api/creator/briefs` | 首次201、重放200；`{"brief":BriefDetail,"replayed":false,"applied_revision":1}` |
| GET `/api/creator/briefs?offset=0&limit=50` | `{"briefs":[BriefSummary],"next_offset":null,"has_more":false}`；limit 1–100，更新较新的在前 |
| GET `/api/creator/briefs/by-key/{client_request_key}` | `{"brief":BriefSummary}`，只返回当前安全摘要；找到ID后显式GET全文；不存在404 |
| GET `/api/creator/briefs/{brief_id}` | `{"brief":BriefDetail}`，当前完整版本 |
| GET `/api/creator/briefs/{brief_id}?revision=1` | 同上，显式读取不可变历史；未知版本404 |
| PUT `/api/creator/briefs/{brief_id}` | 200 `{"brief":BriefDetail,"replayed":false,"applied_revision":2}`；完整替换内容，不做隐式字段合并 |

brief_id为服务端32位小写十六进制；创建/更新key为8–128位`[A-Za-z0-9_-]`。全部响应`Cache-Control: no-store`。无删除、授权、执行或发布接口。

## 2. 创建字段及默认值

```json
{
  "client_request_key":"persisted-client-uuid",
  "title":"我的开场短片",
  "script":"用户填写的剧本正文",
  "requirements":"人物、镜头、声音等制作要求",
  "production":{"duration_seconds":15,"ratio":"9:16","style":"realistic_cinematic"},
  "budget_cny":null
}
```

- 创建必填client_request_key、script；其余默认title=`未命名创作简报`、requirements=`""`、production如上、budget_cny=null。
- title去两端空白后1–120字符；script必须非空白，最大200000字符，保留原文包括换行/首尾空白；requirements最大20000字符，可空，不做模型改写。
- 本版production只接受15秒、9:16、realistic_cinematic；对象可省略或使用这些默认值，其他规格422。
- budget_cny为null或**十进制字符串**，不接受JSON数字、指数、负数、超过2位小数；范围0–1000000.00元，金额规范化为两位小数，例如`"100"→"100.00"`。null是预算待定，`"0.00"`是明确零预算，均不授权。
- 全层级禁止未知字段；不收客户端source_hash/source_version/revision、授权/执行状态、路径/URL引用、project_id/run_id/source_session_id。script里的文字为惰性内容，不会当指令执行。

## 3. 显式更新、幂等与冲突

```json
{
  "client_update_key":"persisted-update-uuid",
  "expected_revision":1,
  "title":"修改后的短片",
  "script":"修改后的剧本",
  "requirements":"修改后的要求",
  "production":{"duration_seconds":15,"ratio":"9:16","style":"realistic_cinematic"},
  "budget_cny":"100.00"
}
```

PUT必须提供上面全部字段。每个成功的新update key创建一个新revision，即使内容相同。仅script与当前文本不同时递增source_version；改标题/要求/预算不变更source_version。恢复旧文本也形成新来源版本，不覆盖历史。

- 创建键全局唯一。服务端对规范化内容（不含key）算语义hash；同键同内容返回原brief，同键变参409 KEY_CONFLICT，不信任自报hash。创建键永久绑定首次内容，更新不重绑。
- POST前持久原键/原请求；响应丢失按by-key查回或原样重交。404/503/超时不自动换键重建。创建重放返回**当前**完整brief、applied_revision=1，不回滚后续修改。
- 更新键在单brief内唯一；新键expected_revision必须等于当前revision，否则409 REVISION_CONFLICT，不写快照或占用键。
- 同更新键同规范化请求（含expected_revision）重放200，不再创建版本；即使已有更新版本，也返回当前brief及原applied_revision。同键换内容或expected_revision则409 KEY_CONFLICT。
- 更新结果未知保留原键/原请求重交，不自动改expected_revision；明确冲突后保留未保存输入，重新读取/合并再显式保存新键。旧慢响应不能覆盖当前选中Brief或本地未保存输入。

键索引、快照与更新回执同次跨进程锁定原子提交。

## 4. 返回字段

BriefSummary字段：`id,revision,title,production,budget_cny,currency,budget_status,authorization_status,execution_enabled,source_hash,source_version,script_characters,created_at,updated_at`。

- currency固定CNY；budget_status为pending（null）或draft（有金额，包括0），不是余额/报价/预占。
- authorization_status固定`not_authorized`，execution_enabled固定false，由服务端投影，历史版本也一样，客户端不能覆盖。
- source_hash为服务端对原样script的UTF-8字节算SHA-256，不规范化换行；source_version初始1；script_characters为Unicode码点数。时间是服务端ISO-8601 UTC。
- 摘要不返回script、requirements、正文节选、私有路径、授权记录或密钥；title为用户明确填写的名字或固定默认，不用正文自动生成。

BriefDetail为对应版本的Summary，加`script,requirements,client_request_key,current_revision,is_current,versions`。versions仅列`revision,source_version,source_hash,saved_at`；历史正文通过带revision的显式GET读取。历史GET的revision/source等属于被选快照，current_revision始终为最新版本。

每次保存保留完整不可变逻辑快照，并用摘要链检测意外改写/损坏；不宣称防恶意篡改签名。只有已知ID的显式完整读取返回正文，不宣称多用户访问控制。

## 5. 错误、隐私和执行边界

格式：`{"detail":{"code":"REVISION_CONFLICT","message":"...","current_revision":2}}`；current_revision仅版本冲突返回，不含当前正文。

| HTTP | code | 语义 |
| --- | --- | --- |
| 422 | INVALID_INPUT | 字段/金额/规格/未知字段/路径参数非法；不回显Pydantic input或剧本 |
| 404 | NOT_FOUND | Brief、创建键或历史版本不存在 |
| 409 | KEY_CONFLICT | 已成功占用的创建/更新键变参 |
| 409 | REVISION_CONFLICT | 新更新基于过期版本，用户草稿仍未保存 |
| 503 | PRIVATE_STORAGE_UNAVAILABLE | 私有树与静态挂载冲突、symlink、I/O不可用 |
| 503 | CORRUPT_STORAGE | JSON/schema/来源摘要/快照链/键索引损坏；停止读写，不清空或覆盖损坏文件 |

私有存储在后端`harness_data/creator_briefs/`，与D06/旧媒体/旧会话隔离且在全部静态挂载之外；原子替换/跨进程锁，原快照值不可改。正文不进入列表/查回摘要、验证错误或本功能日志。

私有根与递归StaticFiles挂载必须双向不相交：静态树包含私有根、等于私有根、或位于私有根内部均拒绝；不能只检查其中一个方向。

不读取/套用已结束的100元试制授权；有金额仍未授权且不可执行。未来Brief→run和真实模型另立迭代合同，不能仅通过改按钮或客户端字段开启。

## 6. 本轮实现与验证

2026-10-20：D07 v1已接入正式FastAPI注册，使用私有JSON状态、OS跨进程锁、原子替换和不可变逻辑快照；本节仅记录实现，不改变前述合同。共享HarnessStore仅补齐静态树双向隔离检查。

- 独立副本最终源定向110项通过（Brief 64项 + D06回归46项），全量后端274项通过；均为合成材料，无真实配置/剧本/回执读取，出站请求/真实模型HTTP/付费均0。
- 覆盖创建/原键查回/语义重放、并发与跨进程保存、完整更新/过期revision/晚到重放、来源变版/历史/应用重建、预算草稿/伪造授权拒绝、摘要和422隐私、损坏文件保留、原子替换失败、静态父同子与symlink拒绝。
- 日志、JUnit、拷贝排除项和源代码SHA-256清单见[本轮后端进度](evidence/LTV-CREATOR-BRIEF-001/backend-progress.md)及[机器结果](evidence/LTV-CREATOR-BRIEF-001/backend-result.json)。定向、全量、交付代码hash一致。
- 独立审查发现的更新键索引完整性P2已修复：私有更新回执摘要绑定实际client_update_key、expected_revision和内容，合法键名单字符改动也会503 CORRUPT_STORAGE并保留损坏字节。公开字段不变；无旧摘要回退或自动迁移，未读写真实私有资料。修复前报告已另存，最终测试使用新源与新合成私有树。
- 验证范围为后端ASGI、实际私有文件及新应用/新进程恢复；正式前端/浏览器联调由主窗口继续。Brief保存不会进入生产执行。
