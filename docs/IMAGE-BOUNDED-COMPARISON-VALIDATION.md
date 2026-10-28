# 图片有限对照验证 · D45

2026-10-29。接口见[API](./IMAGE-BOUNDED-COMPARISON-API.md)，验收输入为[PRD](./IMAGE-BOUNDED-COMPARISON-PRD.md) SHA256 `c08c03a27f16b308fffd5380f439be4b951d96a29febaf3146a290d9e2c0c58f`。源码由统筹冻结于 `c91198558860c1faf085c2b0d0f82fa2d4b1acee`（388份公开文件）。实施及独立审查通过；之后真实A一次返回HTTP200／accepted，费用、GET及图片未完成。入口性能目标未通过，正式图集仍2/4；实际记录见文末。

| PRD | 实现位置 | 合成证据／限制 |
| --- | --- | --- |
| BC01 | `image_comparison._proposal`／status_summary | 精确source descriptor、wire与reference hash；A单样本，因果undetermined；B无执行 |
| BC02 | collector／_unknown／verify_current | 原两条无raw＋一条完整403-unconfirmed；原record与任意collector增改失效；原保护审计另列 |
| BC03 | prepare／activate、scope_budget | 同账户完整intent保留1元/1次；原父唯一增量、中断与重放；不能只凭quote提交 |
| BC04 | candidate_gate／ComparisonExecution | A至多1 POST；不同key不增位，新unknown停止；B无资助／准入 |
| BC05 | policy／_fresh／shared collector | 原120派发＋独立响应合同；Key边界各自新鲜；真实120秒目标未验收 |
| BC06 | 原candidate_for／共享fee owner／status_summary | 错身份拒绝、费用原件与首次事实不可改写；生成与账单分层 |
| BC07 | submit／recover／query | 已发不重POST；非法完整响应、claim前中断保守unknown；未发GET同key继续原期限 |
| BC08 | 独立artifact kind／原reference_sources | 正式operation/delivery/图集不新增，诊断不作后继参考；质量未验收 |
| BC09 | 原正式门禁保留／结果标量 | 不能以A成功解锁第三／第四镜或B；正式仍2/4、人验pending |

## 已完成的观察

- Task0：379份源码匹配交接，2220保护文件一致；原79帧／父46条／POST5／GET3／交付2／费用2／unknown3保持。
- 入口原生红测：合成原账本真实链到五意图／三unknown，旧入口拒绝；279.91秒，外部socket0。
- 候选初轮原生入口失败后，使用已保留的合成账本直接API定位父解析tuple适配错误；修复后报价通过。测试断言改为fixture自身opening+1，未改产品判据。
- 原生独立执行探针已完成资金→POST→精确费用→同任务GET→独立artifact，136.81秒，socket0；不是最终冻结验收，也不是性能SLA。
- 第一批相关纯合同／兼容271通过，14.23秒，socket0；响应合同扩展与恢复修复后须对受影响组复验，不能将各次重叠计数相加。
- 真实原79帧一次只读重建14.700秒，2220保护文件及原计数完全不变，执行／凭据函数／网络尝试0。首次审计器因清单36项相对路径以错误cwd解析而提前停止；更正审计路径后通过，产品与保护基线未改变。

## 最终相关验证

| 组 | 结果 | 命令耗时／socket | 源码边界 |
| --- | --- | --- | --- |
| 合同／兼容 | 426项通过 | 27.366秒／0 | 当前源码的前一个窄修复快照 |
| 原生端到端 | 1项通过，内含5个独立合成分支及原生事务攻击检查 | 1701.247秒／0 | 与上组相同生产源码 |
| 最终迟到ready边界 | 1项通过 | 28.197秒／0 | 最终388文件精确快照 |

五个分支为：规范受理→精确task费用→同任务GET→独立artifact；403-unconfirmed；完整HTML502；非法JSON200；submitting后、claim前中断。各分支使用纯合成五历史快照的独立副本，分别核对旧记录、parent、风险与次数；模拟调用不能跨分支相加当作一段真实生产历史。

原生事务另验证：同ID费用及export／artifact字节与证明不能改写，intent／activation历史不可变，intent出生早于quote拒绝；撤销可以保存并阻断后续派发；新overrun费用可以保存并全账户停止；诊断不能伪装正式delivery；代码或原collector变化使准入失效。测试使用实际原生store／权限／预算／receipt，替代供应商，不mock门禁或账本。

最后生产差异只有`image_comparison_runtime.py`的一个晚证据分支：从“返回旧submitting”改为`_finish(..., None)`，保守unknown，不采用迟到raw。此前runtime SHA `94d652afbbfb275386728673637373550f4a799eb976340906136acb5ae6ebbb`，最终为 `76615d6f79f3c2ea896d428ae63487bf2df425f2f676fcf730800396ede9785e`。最终专项已覆盖该变化；没有再跑无关的28分钟原生流程。对应test文件补永久自包含边界及可选的已建合成历史复跑入口，完整指纹在私有RESULT。

迟到边界先有一次fixture目录创建遗漏（8.561秒），这不是语义红测。修正fixture后真实原生红测25.068秒复现永久submitting；修复后26.806秒通过，随后最终lint版本专项28.197秒通过。原件hash不变、重复恢复不写、不重POST。此前189.85秒的GET／claim前红测、224.186秒的两项绿测与这里的流程重叠，不累加为新用例。

20个改动Python文件Ruff通过；统筹另通过`git diff --check`及三路独立复核。未删除、改写或放宽既有测试；未重跑全套后端／前端。

## 开发冻结时的原任务保护与性能

本窗口最终只读重建原79帧14.680秒，handoff的2220保护文件全不变。统筹独立15.22785秒重建、2221保护文件全不变。原parent46、POST5／GET3／delivery2／fee2／unknown3、82.37元／54次保留包络与100元原授权不变。原图集仍2/4，质量not_reviewed、人验pending。

这些是单次历史读取，**不是ENTRY<120、ISSUE→claim或慢响应真实验收**。原D43 ENTRY124.648秒未过目标；ISSUE→claim94.930秒是另一项边界。真实前须按已准备计划记录ENTRY／ISSUE／claim／raw／ready，保持120派发、180响应与cleanup5，不延长旧期限。

## 真实A准备交付（执行前历史）

私有`EXECUTION-PLAN.json`与`execute_a_once.py`已准备、仅做语法编译检查，未运行。它们固定原第二镜379748字节wire的hash、原账户／模型／端点／参数、源码freeze、+1元／1次、唯一parent47、83.37／55及index6提案。完整preflight后连续quote→intent→父分配→activation→稳定key reserve/submit；不先真实quote再等改源或长测。独占入口marker防止工具中断后重入；之后只能核原native／collector，不重发，不自动GET／核费／下载或执行B。

上述开发阶段真实资金、批准、POST、GET、费用、下载、交付、真实Key loader均0；无前端／共享服务命令／本窗口Git提交或push。当时实际A能否受理／完成、实际费用、慢响应与性能、视觉及用户接受均not_run。之后的独立真实派发如下，不将开发证据改写成真实模型验证。

## 真实A单次执行与审计

统筹在冻结源码及具体脚本审查后显式派发一次A；本窗口仅调用原脚本一次并等待同一进程退出0。真实链为quote→intent→原父唯一分配→activation→index6 reserve／submit，使用历史第二镜完全相同wire（379748字节）、原账户／模型／端点／参数。原始collector与规范decoder确认HTTP200／accepted、有受理任务身份；POST request-id缺失，不能编造日志身份。阶段`accepted_fee_pending`，尚无GET、费用认领、下载或artifact，未完成图片验证。

| 实际检查 | 结果及边界 |
| --- | --- |
| 源文件 | 388份冻结源码逐份SHA一致，未为真实执行改源 |
| 原生历史 | 79→85帧；旧79前缀及全部旧记录不变 |
| 原父资金 | 46→47条，原46记录不变；新增1元／1次，保留风险83.37元、累计上限55次，原100元授权不变；不是消费金额 |
| 原任务 | scope1／grant1／POST5／GET3／delivery2／fee2／unknown3不变 |
| 新诊断 | POST1／accepted1／unknown0／GET0／费用记录0／artifact0；费用未知，不能视作零扣费 |
| 文件保护 | 2221份基线仅预期native＋parent两份变化，其他0；A新collector七份文件逐份size／SHA核对 |
| 读检 | 后端85帧只读22.552557秒，统筹独立20.724秒；执行／Key／网络尝试0，不代表派发性能 |
| 后续保护 | 2221份现状＋A collector七份，共2228份；另冻结85帧前缀供续行审计 |
| 正式产物 | 仍2/4首帧，质量not_reviewed、人验pending；A诊断不加入正式图集，不解锁B或第三／第四镜 |

计时分别保留：reserve ENTRY→ISSUE34.891757秒、ENTRY→claim150.336358秒（120秒目标未过）；ISSUE→claim115.444601秒（原120秒内，仅余4.555399秒）；claim→raw3.263654秒、ISSUE→ready118.719766秒。claim是规范prepare边界，未单独插桩prepare方法入口。本次raw／ready均在dispatch截止前，**没有验证超过120秒的实际慢响应尾部**，也未通过整体入口性能或内容验收。较长只读样本及狭小派发余量是后续GET的实际风险，不能放宽deadline来宣称通过。

私有结果为`backend/A-LIVE-RESULT.json`，统筹独立审计为`coordinator-live-acceptance.json`（均在本阶段私有evidence目录）。费用后台当前跳转登录，统筹等待用户登录；没有读取／改动Key、模型或分组。仅准备同activation的精确task费用核对／导入、单次同任务GET及独立artifact入口；每步分别派发，不自动衔接，不新增下载器、付费POST、B、重试或产品续期功能。新脚本仅做AST／Ruff及本地证据准备检查，不代表这些真实步骤已运行。

租约固定2026-10-28 21:30UTC（北京时间10月29日05:30）。到期后只读原件与纯候选核对仍可进行；旧activation上的费用导入、GET、原生recover采用和artifact记录必须停止。已合法派发的collector只按原截止收尾；不因等待登录自动续建quote／intent／activation、增加资金、清unknown或另发请求。实际A仅支持“这个历史输入本次受理”，第三镜403原因仍undetermined，通道健康与产品自主闭环尚未验收。
