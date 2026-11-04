# D52 历史校验结构性提速：后端验证

2026-11-02。入口 `b380d953251be00da76d0fea6642bc7a4dd490aa`。本文件由既有后端窗口维护，独立验收与整合由 PRD／统筹窗口负责。

本轮完成一组局部历史关系校验复用，**原90帧实际 GET 仍未达到5秒目标**。D52 入口同输入 ASGI 为13.485834秒；最终三次为 **11.812218／11.487838／11.524807秒**。均为正常非 profile 的实际路由请求，每次仍完成一次90帧重建，完整 payload 与独立逐帧折叠相等，返回 DTO 与 D51 相等，所有输入文件字节不变。没有提高前端15秒超时，也不构成生产 SLA。

## 冻结源码与改动边界

- 入口426文件，最终428文件；两处旧生产模块修改：`core/execution/store.py`、`core/execution/media_ledger.py`。
- 新生产模块 `core/execution/history_validation.py`，新测试 `tests/test_history_record_validation.py`。旧 tests 和依赖文件逐项 SHA 不变。
- 最终清单 `evidence/LTV-HISTORY-VALIDATION-PERFORMANCE-20261102/backend/final-source-manifest.json`；清单文件 SHA256 为 `4bf904111dff90f1a4b15745fddde1d7cf08e524d0f38cbce9ca90933c4a0e29`，规范 JSON 映射树 SHA256 为 `f067166984a1e81fcc6f7d1432890f5f545b50ef95ba305cf1633292b33a486f`。两种 SHA 的编码口径不同。
- `media_ledger` 原135处 `require` 调用的完整 AST 多重集相同；全局约束、历史变更约束及其后模块尾部字节相同。每帧角色校验、所有帧哈希链、最终状态校验和特殊历史校验仍运行。
- 没有接口、前端、共享服务、依赖、Git、真实配置／Key／执行 session 或真实付费动作改动。D51旧证据未修改。

## 诊断与实际红绿回归

先冻结入口源码，在原90帧只读副本上运行 `backend/read_baseline.py`。实际 GET 200／no-store，13.485834秒；5秒断言退出1，完整数据与字节检查通过，网络尝试0。该失败留存在 `baseline-01/result.json`。

随后按三个可证伪假设检查：未变记录及其依赖反复关系校验、完整 scope 上下文重复计算、特殊历史检查重复重建前缀。实际调用点红回归显示：同一合法 delivery 在三个后续无关 quote 和最终检查中共被校验5次，期望1次，测试失败。机械拆分后加入本次重建内证明，原回归变绿；最终该检查连续执行两次完整重建，每次均为1次，证明未跨读取复用。红样本留在 `red-01-fixtures`，原执行及耗时见本窗口记录和 `backend/PROGRESS.md`。

没有证据支持取消特殊历史检查，因此第三个优化方向未实施。本轮不再扩展新热点。

## 减少了哪些重复工作

`validate_state` 建立一次私有、防御复制的同步重建上下文。每个已复制 delta 捕获严格类型的完整实际值；帧的全部原校验返回成功后，才提交该帧产生的成功证明。ID 仅定位证明槽位，命中条件是完整 typed marker 相等；实际值指纹发生碰撞也不能替代完整比较。失败不缓存，输出独立复制。

| 关系组 | 证明绑定的输入 |
| --- | --- |
| 整组 scope | 全部六个 CORE 命名空间、全部 asset_design、scope 有序集合；存在 manifest_origin 时额外绑定全局 media scope／operation／query／fee／claim／image_comparison_fee 的完整成员和记录，覆盖资产验证间接使用的 ID、分配与费用冲突 |
| 普通 quote／grant／key | 自身完整记录、scope、对应 quote 或目标记录 |
| delivery／reference_derivation | 自身完整记录、scope、完整 query 或目标 operation |
| text claim／media claim／fee | 自身、完整累计 used_requests、CORE 或所需 fee；fee 额外包含全部 asset_design 和全局费用归属成员 |
| 普通 operation／query | 自身及同 scope 的完整 quote／grant／operation／delivery／reference_derivation 集合，全部 code／validity／runtime 延续记录及自身 fee；仅明确审计过的 v1／v2／v3 分支适用 |
| 其余复杂合约 | 保持原完整 payload 校验；保留未知风险的 schema4、observation、comparison、persistence 等不猜测依赖闭包 |

每次证明还捕获实际的图片解码限制和 Pillow 配置、描述符与引用大小限制、JPEG 参数和编码器身份、费用解析字段／限制、manifest 必需文件集合和 Decimal 计算上下文。参数变化会重新计算；计算期间参数变化不能种入成功证明。当前权限、时间、源码和外部文件读取不进入此表。

完整私有 payload 在 scope 组入口及每帧提交时与捕获值核对；本次 native 防御副本在退出时再次核对。异常、嵌套退出及复制出去的 context 均不能延长证明寿命，跨线程不可使用。单表实际值保守计费上限64 MiB、证明槽位最多512；严格 JSON／深度／容量不支持时退回原校验，没有新的宽松 schema。证明内引用受槽位数约束，64 MiB 指当前表值计费，不是进程 RSS 上限。

最终独立 profile 的实际调用计数：scope 67次调用／37次计算，delivery 69／2，quote 57／1，grant 56／1，普通 operation 149／80，query 101／64。其他复杂合约仍走完整校验。profile 的15.598335秒包含探针和采样开销，不能当作正常 GET；父子累计时间不能相加。

## 最终验证

- 最终源码的18项新回归与57项相邻旧回归共 **75 passed／126.65秒**，网络 guard 开启，未改旧 tests。包含依赖 query／scope／account／delivery／解码参数变化、重算哈希后的非法不可变字段与终态／撤权变化、帧哈希破坏、完整类型及指纹碰撞、防御输出、失败／容量回退、同读私有数据与 native 副本变更、外部调用者变更、嵌套／异常／复制 context／线程寿命，以及 manifest_origin 的全局成员变化。pytest 在禁用插件自动加载时报告既有 `asyncio_mode` 配置警告。
- 原90帧三次实际 ASGI 200／no-store、一次完整90帧重建／请求，输入 SHA256 `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`。三次5秒断言均失败；数据检查全部通过。见 `final-original90/result.json`。
- 原90帧完整 payload 与逐帧折叠相等，3个 unknown 不变。D50旧69帧到期样本的实际 ASGI 返回与 D51 完全相等，已保存文件仍 available／registered，2个合成 unknown 保留，全部输入字节不变，网络尝试0。见 `final-compatibility.json`。到期路径不用于替代正常90帧性能结论。
- 最终源码新建的 managed 合成 compound **1 passed／112.38秒**，内部场景111.493秒，不相加；公开 stage392／权限闭包106逐项匹配最终源码，闭包含新模块。网络尝试0、实际下载器0。合成当前 GET1.479688／1.467776／1.474126秒，文件／proof／源码／native／撤权／unknown变化及恢复均通过，独立实际 task／source／brief→native plan源绑定通过。样本为 quote-heavy69，不能替代原90帧或 D50 runtime-heavy69帧。详情及统筹 HTTP 启动交接见本轮 `backend/RESULT.json`、`SOURCE-MAP.json`、`HANDOFF.md`。

## 仍未完成的目标

5秒目标保持开放。完整历史校验仍占主要耗时；复杂 runtime／validity／observation／comparison 的校验，以及依赖发生变化后必须重算的 operation／query，仍产生大量序列化、摘要和引用关系处理。本轮局部证明没有将这些完整规则消除，也没有建立跨请求缓存。本次已验证组在此收口，后续优化需另立有限诊断与验收。

最终实际 localhost HTTP、未改前端解码和独立 task／source／brief 绑定由统筹交接后单独验证，本文件不提前宣称通过。COMP01未关闭；原 native90／parent47、正式图2/4／unknown3、原100元／保留风险83.37元／cap55保留，风险不是消费。当前不构成真实生视频、媒体质量、人验或完整 Harness 验收；无需用户再次提供 Key、登录或原预算。


## 2026-11-04：当前权限交接样本 native-02（同一最终源码）

11月2日native-01已到期。本次仅以原公开runner一次性新建native-02，最终428源码／18项新tests逐项SHA不变，没有继续优化或重跑75项回归。原native-01、原90帧计时和旧证据均保留。新compound **1 passed／121.59秒**，内部场景120.363827秒，不相加；stage392／authority106及实际权限源码与最终源码相等。

真实UTC参考区间为2026-11-04T04:01:08Z至04:04:22Z。首帧出生2026-11-04T04:02:21.600389+00:00，新media scope出生2026-11-04T04:02:27.172340+00:00。新scope前配置初始4小时policy，新persistence quote／image operation前配置3小时50分managed期限；不修改时钟、不续旧fixture。当前managed权限到期北京时间 **2026-11-04 15:52:28**，policy到期 **16:02:26**。

当前有效权限下正常实际ASGI GET为 **1.612904／1.514036／1.586434秒**，69帧完整重建一次／请求；旧GET编排＋同最终校验器为4.415653／4.409946／4.583189秒，重建三次。有限文件／proof／源码／native／撤权／unknown分支和恢复均通过，独立task/source/brief的实际响应与native plan源绑定相等。profile独列，quote-heavy69不能替代原90帧、D50 runtime-heavy69或生产SLA；真实5秒目标和COMP01仍开放。

新task `6df2d4c484fc4988995223e8dabc3283`，brief `7aa418bf48e64c72b2eaa3169b0085a2`，snapshot `b9ce684485623551ecee463fb5f1200cfe99d4cdabdb53ba38459e95217b13c1`。完整路径、出生／到期口径、原始ASGI响应、源闭包、保护哈希与启动命令见本轮 `backend/native-02/handoff-current.json` 和 `backend/HANDOFF.md` 末节。旧D52/backend3290文件／27链接（不含授权追加文档）及旧native-01的675文件／1链接前后全匹配；更广保护复核留给统筹。网络尝试、真实provider和实际下载器均0。

本次性能采样已停止，尚未启动HTTP服务；统筹将串行复核原90与新合成的实际HTTP及未改前端契约。没有新增真实生成、质量、人验或完整Harness接受结论。
