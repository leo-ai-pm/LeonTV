# D50 retained-unknown正式图片持久化独立验收

2026-11-01（Asia/Shanghai），审查提交dd5c718。**RP01–RP04在限定合成接缝范围内通过；真实一镜仍未就绪，不等于真实账户、性能或完整Harness通过。** 本次独立读了场景／runner及既有two_unknown_case实现、原生结果、实际ASGI、源码映射和准备包；未仅按统筹passed字段判定，未执行runtime／tests／network／Git或读取真实秘密，仅更新本报告及本轮prd/RESULT.json。

| 条件 | 独立判断与证据 |
| --- | --- |
| RP01 保留与准入 | **合成范围通过。** 真实typed RuntimeContinued session沿原scope／grant／账户保留2条合成unknown、原4个operation及2个delivery；新操作出生前绑定opt-in，使用已有第五位置。源码断言旧记录／frame前缀／父账本和包络不变，旧Loaded控制及未资助S04被拒；合成风险81.37元／cap53不变，不冒充真实83.37／55。旧permit实际等待过期，未改时钟／validator |
| RP02 原生自动链 | **已测路径通过。** 1个复合pytest通过541.05秒；目标S03的mock POST1＋任务GET1，规范completed后实际worker接收1次、解码／SHA／耐久proof后登记原delivery，前镜精确取原S02 delivery。query／recover／submit重放不增加请求或执行目录写入；费用未观察与第六reserve均拒绝，外部0 |
| RP03 只读投影 | **数据合同通过，性能不作通过声明。** 独立核对2份实际媒体响应与另附task／source／brief，task和来源hash一致、synthetic明确。S03当前available并正式登记；S01／S02只保留legacy登记且未核验；S04未生成。GET／重放执行目录指纹不变，质量not_reviewed、人验pending；统筹将原响应交未改decoder／view，标签与字段一致，非D50浏览器端到端验收 |
| RP04 版本与准备边界 | **报告及边界通过。** 最终后端419＝原417＋2新测试工具；原生产／旧tests／前端116、保护2532及旧失败现场30不变。104项authority与实际stage／最终源码同hash。真实准备包明确3条unknown处置、可用正式位置和当前源码／lease缺项，不编造ID、不执行／续权／增资／付费／旧任务重试 |

计数按来源分开：合成setup加目标的supplier mock总数10＝text3＋asset1＋image6；native-result的all_model_http=6只指image计数，不是全部模型请求。目标增量仅上述POST1／任务GET1，资源worker GET另计1；历史missing-supplier fixture不证明真实unknown未执行或零费用。1个复合节点不是多项独立pytest，541秒包含合成历史、旧permit等待和重放，不是生成或下载SLA。真实3条unknown没有被重放，真实交付仍2/4。

## 实测源码与最终工具差异

最终419项清单SHA为`dbfa887e16f9700f5c461bc1099aac5360df55f9c2a2d3a2db3b150419053aea`。本次独立比较清单：原417项同hash；实际stage383与最终仅差两个新测试文件；authority104项两边完全一致。两新文件当前读SHA与最终清单相符。

精确diff确认：场景仅把禁止读真实Key且在成功长测中未调用的无参pytest.fail回调lambda改成同体def；runner仅为connect_ex增加与connect相同的合成server端口限制。后端最终guard短探针，经统筹核对脚本及结果，匹配runner e39dac…，允许2次指定本地连接、阻断2次人工错误端口且未外发；这2次不是native额外资源／模型请求。**没有声称完整最终测试工具源码重跑过541秒长测**，也不把Ruff或guard负例算产品测试通过。

## 未通过或范围外

- **真实执行未就绪。** 合成只覆盖特定2条unknown；真实第三条raw-present unknown尚无适用处置版本，原正式4＋1第五位已消耗，没有准确未来可消费位置及当前源码／lease绑定。D46 GET-only观察lease已过期，且本就不授予正式POST。准备包不等于授权，不据此发新调用。
- **读取时延未验收。** 两次合成媒体GET为14.553／14.204秒，接近现有前端15秒超时；只证明该次ASGI响应及离线解码正确，未证明经HTTP前端可稳定显示、真实90帧读取余量或生产SLA。
- **COMP01仍开放。** 原50通过／1失败及历史缺失候选不变，本轮绿灯不证明其原因或修复。真实provider／TLS、图片内容质量、最终人验、视频及完整Harness均未验收；既有D48普通链／UI未重做。

真实基线保持native90／parent47、2/4／unknown3、原100元／保留风险83.37元／累计55，风险不是消费；此处引用保护报告，PRD不读取真实Runtime或重复保护审计。

下一步可完善支持真实3条unknown的处置、未来正式位置及当前源码／lease的准确执行包，并评估只读耗时；由统筹决定后续工作。本轮不执行、续权、增资、付费或重试，不要求用户重复Key、登录或原100元确认。详细结果与证据hash见本轮prd/RESULT.json。
