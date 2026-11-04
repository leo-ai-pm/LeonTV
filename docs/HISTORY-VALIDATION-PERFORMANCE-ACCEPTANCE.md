# D52 历史校验性能独立验收

2026-11-04（Asia/Shanghai）。入口 `b380d953251be00da76d0fea6642bc7a4dd490aa`。

**结论：本轮局部历史校验优化及只读集成接缝有限接受；原90帧实际GET的同机≤5秒目标未达，继续开放。** 在本次有限源码和证据范围内未发现阻碍本地集成的实现问题。11月4日原90帧真实本地HTTP为11.846／12.215／11.989秒，15秒timer未改，最小实测余量2.785247秒；这不证明生产负载下稳定无超时，也不是整项性能目标完成。

本次按已交付的HV01–HV04作一次最终独立审查。依据统筹冻结报告（SHA `e71ba951ed9f2f73a2ee35e0ca708cc48b11cd7fe04d5f23693ec1e3f87d5a18`）、后端RESULT／SOURCE-MAP／公开validation，结合新`history_validation`、两旧模块有限diff及新增测试文本。运行、测量、实际HTTP和完整保护审计分别由后端、统筹执行；PRD作静态源码／AST／哈希核对，没有运行应用、Runtime、测试、网络或读取原账本、真实剧本、Key、配置、执行session。后端报告保留交接前的HTTP待核状态；最终HTTP结论采用随后完成的统筹报告。

| 条件 | 独立判断 | 依据与边界 |
| --- | --- | --- |
| HV01 同源真实时延 | 来源与口径证据接受；真实5秒目标不通过 | 原90帧输入SHA、入口／最终源码和实际ASGI结果可追溯，另有实际HTTP。入口ASGI只有1个基线样本，最终3个；profile分开。11月2日ASGI与11月4日HTTP不是配对试验。轻合成69帧和旧到期路径不替代真实90帧 |
| HV02 完整历史证明 | 当前局部实现有限接受 | 复用绑定完整类型化实际值、依赖集合、累计费用占用输入及语义环境；ID只定位槽位，指纹碰撞仍须比较完整marker。帧哈希链、每帧角色／媒体与全局／状态变更约束、最终payload及特殊历史校验仍运行。有限重算hash非法历史和依赖变化拒绝证据见下文；这不证明未来新增规则的依赖自动完整 |
| HV03 新鲜性与只读接缝 | 有限接受 | 历史纯值证明不缓存当前权限、时间、源码或外部文件。native-02有16个实际ASGI正常响应经未改前端decoder及1个损坏native的503拒绝；16含重复和profile样本，不是16种独立业务状态。独立task／source／brief／native绑定与另行6次实际HTTP成立，各次读取零写、外部尝试0；无浏览器UI／共享部署结论 |
| HV04 最终版本与保护 | 有限接受 | 最终428源、stage392、authority106由统筹完整映射；PRD核对两改动生产文件、一新生产文件、一新测试的SHA均匹配最终与native-02 stage，三生产文件亦匹配权限闭包。75相邻回归及两次同case构建按下述版本计数，旧保护结果采用统筹审计，不冒称PRD重新扫描实物 |

**性能口径。** 同机隔离副本的非profile正常请求如下；每组三个最终数分别为首次读取与两次重复请求，不代表全机器冷启动或生产负载分布。

| 日期、输入与路径 | 入口／对照（秒） | 最终（秒） | 判断 |
| --- | --- | --- | --- |
| 11月2日，原90帧实际ASGI | 13.485834，单样本 | 11.812218／11.487838／11.524807 | 每次完整90帧重建1次；完整payload、DTO和输入字节保持；三次5秒断言均失败 |
| 11月4日，同原90帧实际localhost HTTP→现有API／controller | 无同日同口径入口HTTP前测 | 11.845841／12.214753／11.989045 | 三次ready；最小15秒余量2.785247秒，真实目标未达，不计算跨日期配对提速 |
| 11月4日，新native-02有效managed合成69帧实际ASGI | 同最终validator的旧GET编排：4.415653／4.409946／4.583189 | 1.612904／1.514036／1.586434 | 完整重建3→1是既有D51编排对照，不能计作D52新增结构优化的独立收益 |
| 11月4日，同有效合成69帧实际HTTP→现有API／controller | 无同口径入口HTTP前测 | 1.561597／1.578191／1.550643 | 三次ready，测量时managed权限valid、文件available；quote-heavy69不等同原90或D50 runtime-heavy69 |

原90帧SHA为 `829fbcaf5f6ac3a90dcc625aa063977c1e5cbf46a694f9a988e5360d3b8748e8`；字节一致、完整payload与独立逐帧折叠相等及原3个unknown保持，依据后端结果和统筹报告，PRD未读取该原账本。旧D50到期69帧返回、available／registered和两条合成unknown保持，只作兼容证据。D51旧测试和历史读数仅作入口，不替代D52改动验证。最终独立profile为15.598335秒，含探针开销，累计耗时互有包含，不作为正常时延或与子项相加。

**完整性与复用边界。** `store.validate_state`先持有防御复制的原生输入，每个已复制delta捕获严格类型的完整值；全部帧哈希／previous hash／revision检查仍在。关系校验成功后形成局部证明，完整帧校验返回后提交，正常或异常离开重建上下文即清除。私有payload与捕获值有守卫，原生副本退出时再次核对；嵌套、线程及复制context不能延长证明寿命。容量／非严格JSON不支持时回退原校验，64MiB是当前表值保守计费上限，不是进程RSS上限。

Scope证明覆盖全部CORE和asset_design，有manifest_origin时额外覆盖全局media scope／operation／query／fee／claim／image_comparison_fee成员。普通记录依赖各自完整scope、目标记录和累计used_requests；operation／query还覆盖同scope引用、grant、延续与自身费用等依赖。只有已审计的普通v1／v2／v3分支参与，retained schema4及observation／comparison／persistence等复杂合约保留完整输入路径。解码限制、Pillow、描述符／引用参数、编码器、费用解析、manifest必需集合及Decimal上下文参与语义键，变化会重算。后续新增依赖必须扩展闭包或保留完整输入回退，本轮接受不能自动覆盖未来扩展。

PRD独立比较发现`media_ledger`前后135处`require`调用的AST多重集相同，全局／状态变更约束及后续模块尾部字节相同；这项机械比较连同依赖审查、有限对抗回归共同支持结论，单凭调用数相同不足以证明完整性。18项新回归覆盖实际重复delivery校验红绿（后续前缀5次→每次完整重建1次）、query／scope／account／delivery／解码参数变化、重算hash后非法不可变字段／终态／撤权及帧哈希破坏、完整类型／指纹碰撞、防御输出、失败／容量回退、同读私有输入与caller变更、上下文寿命和manifest_origin全局成员变化。PRD只审测试文本及后端结果，未执行这些测试。

**版本、集成与保护。** 最终仅改`store.py`、`media_ledger.py`，新增`history_validation.py`及`test_history_record_validation.py`。最终清单文件SHA为 `4bf904111dff90f1a4b15745fddde1d7cf08e524d0f38cbce9ca90933c4a0e29`，规范映射树SHA为 `f067166984a1e81fcc6f7d1432890f5f545b50ef95ba305cf1633292b33a486f`，二者编码口径不同。

后端75通过／126.65秒＝18新＋57旧相邻回归，外部尝试0；禁用插件自动加载产生既有`asyncio_mode`配置警告。11月2日native-01复合检查1通过／112.38秒（scene111.493秒），11月4日同最终源码新建native-02同case再通过／121.59秒（scene120.364秒）。这是同一个case的两次合成构建，不算两个不同测试；scene已包含在pytest时间内，不相加。11月4日没有再跑75项回归，没有改源码／测试，也未重跑COMP01或全历史suite。

native-01到期证据原样保留。native-02在新scope／quote出生前设置初始有界合成权限，managed至北京时间2026-11-04 15:52:28、policy至16:02:26；没有续旧权限或假时钟，后续到期快路径不能代表有效授权计时。组装仍为文本／资产MockTransport、四条同步图片raw及离线PNG proof，不验证真实capture、下载器或模型质量。读取分支之间的自有fixture构造不计作“读取零写”；零写指各次读取本身。

合成task／source／brief实际API绑定与native来源相符，但Planner DTO仍为`awaiting_authorization`／plan_version0，不能表述为其已进入native的`plan_ready`。原90帧HTTP绑定来自隔离原生task，不冒称原task API。原90前两图仍为legacy未核验、第三条unknown、第四条未开始；文件可用、历史登记、当前权限、质量未审与人验pending分别表达。

统筹报告两自有HTTP服务器已停、端口已关、所有fixture文件不变、外部尝试0。前端116、旧tests／依赖、保护6032与原失败现场30全部SHA匹配；旧D52/backend3290文件／27链接及native-01的675文件／1链接亦保持。上述完整实物审计属于统筹／后端，PRD未重新扫描；D51全部证据继续受保护。

**保留事项与后续范围。** 本组局部优化已封版。剩余主要成本是复杂runtime／validity／observation／comparison历史及依赖改变后的operation／query重算；真实5秒目标保留，后续只能另行有界诊断这些成本，本次不扩热点或安排新测试矩阵。真实3条unknown处置与未来生成准入仍为单独待办，不触发付费执行。

原真实native90／parent47、正式图2/4／unknown3、原100元／保留风险83.37元／cap55保持，风险不是消费。COMP01仍开放，原失败未被后续绿色检查覆盖；无实际Key／provider／付费生成／下载／费用观察、增资、续原权限或旧任务重试。本轮不接受真实媒体质量、人验、有界自主协作整体、完整Agent Harness或Personal Agent。

PRD只更新本文件及本轮`prd/RESULT.json`，未改后端、旧证据、AGENTS、统筹文件或Git，未新增窗口／子代理／工作树。主要依据为本轮`coordinator-integration-review.json`、后端`RESULT.json`／`SOURCE-MAP.json`、公开`HISTORY-VALIDATION-PERFORMANCE-VALIDATION.md`，以及上文列明的有限源码／测试文本。SHA、执行归属及机器结论见本轮`prd/RESULT.json`。最终审查至此结束。
