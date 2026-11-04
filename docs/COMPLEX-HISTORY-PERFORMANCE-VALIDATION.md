# D53 复杂历史读取：引用绑定计算提速验证

2026-11-04。入口 `9f285ea7108acbc32ea1c91c54e4a037f4520ca0`。既有后端窗口交付这一组实现，统筹负责集成／Git，PRD负责独立验收。

本轮完成一组引用绑定的局部计算改进，**真实90帧5秒目标仍未达到**。当前入口同输入实际 ASGI GET 为11.386197秒；最终三次正常请求为 **10.475942／10.641566／10.647682秒**。每次完整验证90帧一次，完整 payload 与独立折叠相等，DTO与冻结基准相等，所有输入字节不变，网络尝试0。前端15秒超时保持，未建立生产 SLA，也不将旧日计时当同期对照。

## 最小改动与冻结来源

入口428份后端文件，最终429份。仅一个旧生产模块 `core/execution/image_continuation_policy.py` 改动，新增 `tests/test_reference_binding_snapshot.py`。其余427份旧文件、旧 tests 和依赖SHA相同。没有前端、AGENTS、PRD、统筹文件或Git改动；D52证据及两份公开报告不可变。

最终后端清单 `evidence/LTV-COMPLEX-HISTORY-PERFORMANCE-20261104/backend/final-source-manifest.json`，文件SHA256 `8ec7933b4b1fb2f6277b493418b1c69a81a0ddb86f560c2412484218bace2b9d`；规范映射树SHA256 `2936db7ed8c0a9b0b9a00f7a765dbc02d3fa3016e687670ee5ffde9734c711fa`。两种SHA对应不同编码。

原 `bind_reference_sources` 的参数及整个函数体，以 `_bind_reference_sources` 名称原样保留；除函数名外AST完全相同。原模块全部59处 `require` AST相同。所有帧哈希、逐帧变更、记录／跨记录、最终状态和特殊历史约束均沿原路径；没有改动store、media_ledger或D52历史证明图。

## 红信号、假设与单变量结果

先冻结428份入口，在本轮隔离原90副本上运行真实路由反馈命令：`backend/read_baseline.py --fixture backend/original90-copy --output backend/baseline-01 --reference backend/baseline-response-reference.json`，Python路径显式使用 `entry-source/tests` 与 `entry-source`。实际200／no-store、11.386197秒，5秒断言退出1；完整数据、SHA和网络检查通过。精确命令及证据位于 `backend/PROGRESS.md`、`baseline-01.log`。

随后列出三个可证伪假设：既有纯值边界的重复序列化／引用处理、复杂披露内容的重复重建、D52自身的捕获／guard开销。D52独立profile中 JSON编码器自耗时5.102秒，但 `_checked` 全部20次累计仅0.196秒；纯authority校验230次累计仅0.093秒。不能由父级累计时间误判缓存收益。引用绑定38次累计1.136秒，且每次完整解码源图；因此只选择这条输入完整、结果为小型绑定列表的路径。复杂披露关系、通用digest／JSON以及D52 guard不扩展修改。

在实际 `bind_reference_sources` 调用点先建立红回归：真实合成WEBP源证据及其转换PNG引用，同次读取4个调用重复完整解码4次，期望1次，测试0.15秒失败；修复后0.14秒通过。测试还确认下一次读取和读取边界外会重新构建。红／绿输出分别为 `red-01.log`、`green-01.log`。

单变量候选只启用这条绑定复用，原90帧实际 GET 为10.439057秒，完整数据及字节相同，仍未满足5秒。该候选源码单独保存在 `candidate-01/`；之后仅补强成功证明的严格设置比较及可选记录失败回退，再通过全部有限回归并冻结最终版本。

## 输入证明和寿命边界

包装函数复用既有 `_reference_snapshot` 的32项／16MiB池，不增加一层通用缓存或复杂依赖图。完整token包含：

- 原始source列表全文：operation／query／delivery ID、完整delivery元数据与声明哈希、实际原图base64字节、shot和严格版本；
- references列表全文：转换后的实际base64字节、MIME、SHA、shot及严格版本；
- 当前源字段集合、source字节／像素限制、reference字节与descriptor限制、Pillow像素限制及截断配置；
- 既有helper捕获的实际编码器身份。命名字段使绑定输入与原表示转换证明的输入不同，共享同一容量上限。

helper先捕获完整防御副本，原函数也从这份副本计算，防止key与计算读取不同的可变调用者输入。证明仅在原函数成功、设置及编码器未变化、当前scope仍活动且属于当前线程时进入原池。比较设置使用严格JSON表示区分bool／int／float。返回值及存储值均独立复制；不缓存可变Pillow图像或像素对象。输入／设置变化重新计算；失败不进入成功表；非严格JSON、超限或可选环境读取失败退回原函数。嵌套、异常、复制context和线程不能延长证明寿命，读取退出清空既有池。

这段纯函数仅核对给定证据的字节／格式／元数据并形成绑定列表。实际delivery／operation归属、scope／任务／引用关系仍在外层原生校验中；当前权限、时钟、source源码及文件存在性／内容仍由原边界重新读取。本证明不授权HTTP、生成或下载，也不跨请求保存账本／权限结论。

## 最终验证与实际成本

有限相邻回归 **93 passed／5.39秒**，其中22项新检查、71项旧检查。覆盖完整输入与重算哈希变更、严格bool／float版本、source身份变化、当前解码与容量限制、失败不入表、调用者捕获后变更、独立输出、嵌套／线程／复制context、共享池隔离，以及原有重算哈希的非法原生历史。旧tests未修改，未重跑COMP01原失败batch／prefix或全历史suite。Ruff通过。禁用pytest插件自动加载时保留既有 `asyncio_mode` 配置警告。

最终原90帧输入SHA为 `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`；三次真实ASGI数据／字节检查均通过，三次5秒断言均失败。所有请求由独立进程执行，Python路径使用最终冻结源码。

另行profile为14.501838秒，含探针开销，不能替代正常请求。实际绑定仍被调用38次，原完整绑定函数只计算2次；`reference_derivation._source` 从D52 profile的50次降为14次。新绑定路径累计0.270秒，其中完整计算约0.061秒；旧绑定路径累计1.136秒。父子耗时不相加。profile源码和计数分别记录于 `final-profile/profile.pstats`、`profile-core.json`。

剩余成本仍主要是完整历史处理中的JSON序列化、复制、摘要及复杂关系重建；最终profile JSON编码器自耗时5.066秒、deepcopy自耗时1.062秒，均为profile数据。未为追求5秒扩展新的热点、推测复杂依赖或跳过规则。本轮这组改进在此封版。

最终源码新建的有界合成样本及task／source／brief实际绑定、文件／源码／撤权／unknown分支、stage／authority映射、UTC出生／到期时间和只读启动方法见本轮 `backend/RESULT.json`、`SOURCE-MAP.json`、`HANDOFF.md`。新quote-heavy69帧仅用于当前权限读取接缝交接；热点收益以原90帧和实际绑定调用点为依据，不能以轻样本替代真实90或D50 runtime-heavy69。

本轮仅一次新合成compound **1 passed／122.78秒**，内部场景时长不相加；stage393／authority106及实际权限源码均匹配最终429文件。当前有效权限下正常ASGI GET为 **1.764411／1.564329／1.564412秒**。task／source／brief均200且native源绑定通过，有限文件／源码／撤权／unknown及恢复检查通过，最终69帧字节保持。新scope出生UTC2026-11-04T04:35:01.022836，managed期限在quote／operation出生前设置，至北京时间 **2026-11-04 16:25:01.919135**；policy至16:35:00.449346。未修改时钟、未续旧fixture。

D52受保护4002文件／28链接（含两份公开报告）逐项匹配入口，规范树前后均 `ae3f796d75a10c86346653594742d9d426bd3c0d5101952bf0fac38798e2a556`；更广保护10034／前端116留给统筹独立复核。后端全部性能采样已经结束，没有启动HTTP服务，源码和新样本冻结，后续不再增加回归或测量。

统筹将在后端采样结束后串行做实际HTTP与未改前端解码，PRD独立验收；本报告不提前宣称其通过。原native90／parent47、formal2/4／unknown3、原100元／保留风险83.37元／cap55保持，风险不是消费。COMP01与真实5秒目标开放。无真实Key／config／执行session／provider／生成／下载／费用观察／增资／续旧权限／重试／共享重启／push；本轮不等于媒体质量、人验、完整Harness或Personal Agent验收。
