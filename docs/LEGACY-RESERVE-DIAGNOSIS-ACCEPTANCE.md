# D49 旧流程reserve诊断能力最终验收 · v1.0

2026-11-01（Asia/Shanghai）。**有限诊断工具交付通过；没有证明历史COMP01根因、没有自然复现产品缺陷，也没有生产bug修复。** 原D48正式兼容组50通过／1失败继续开放，原记录不改写。本轮新增5个公开诊断／测试文件，生产代码、旧tests、前端和依赖均未改；统筹负责本地集成与Git，PRD未运行产品／测试／网络或读取真实秘密，未新增窗口或子代理。

## 接受的工具范围

公开入口须显式指定新的持久输出目录：

```sh
.venv/bin/python tests/run_legacy_reserve_diagnosis.py --output /absolute/new-persistent-dir
```

工具只使用新合成fixture／隔离公开源码。原reserve与validator照常执行；失败时保序捕获candidate＋previous、包装前具体断言／源码栈、墙钟／单调时钟及函数返回。当前helper另记录pytest节点；采集故障不替换应用原异常或正常结果。runner保留超时输出，退出124；不传--output在执行前退出2，不再自动清理唯一失败现场。没有生成图片、外部授权或原任务重试入口。

最终源码清单417项，SHA `d16cc33a67f6ab5f2f7504cfe62cc8260cff692bd214c7c42c74f08ab6086250`；原412项在清单中同hash，新增文件仅为trace／scenario／prefix plugin／runner／回归测试。五个新增文件实读hash与最终清单相符；原412实际文件、前端116、保护2532、原失败现场30不变引用后端／统筹报告，PRD不重复全量实际审计。

## 证据与版本

| 组 | 实测 | 接受范围与限制 |
| --- | --- | --- |
| pilot-08 | 1个复合节点通过8.39秒，8次同baseline分支，外网0；reserve中位0.396251秒 | 正常反馈入口，不是8账户／8pytest，未捕获自然失败 |
| batch-128-clocks | 同一scenario节点通过68.21秒，128次合成baseline分支，外网0 | 未自然失败，不推缺陷不存在或原失败概率 |
| batch-128-delay30 | 同一scenario节点通过60.50秒，128分支，build后额外30ms，外网0 | 原时钟／validator保持，UUID与开始时刻不同，不当作同输入因果实验 |
| capture-calibration-final | 3参数通过8.70秒，外网0 | 真实reserve中人工_record把created_at改为issued_at前一微秒，原时间约束拒绝；正常采集／result写失败／source读取失败保留原CORRUPT_STORAGE与state字节。是反例校准，不是产品bug红绿或历史原因 |
| final-helper-checks | 4通过9.29秒，外网0 | 包含前述3参数及runner超时回归；3项与前组重叠，不相加为7个独立测试 |
| 原顺序prefix | 41通过／10剔除／524.50秒，外网0；32条reserve诊断记录 | 到原unknown目标节点为止，保持原test bodies与顺序；无非预期失败、自然失败候选0，不是原51节点整组通过 |
| 独立helper probe | 当前helper4ab984b6…，3项检查通过 | 原异常对象身份、成功body不变、callback错误包含；未导入应用，不算产品／reserve pytest |
| C03／超时短验 | 当前runner113a47b7…，缺参退出2／help退出0；真实短子进程触发124且保留partial stdout，network0 | CLI与runner错误处理；替换子命令，不算产品native结果。单节点timeout回归与4项检查重叠，不另加 |
| 保护／同源与Ruff | 原412、前端116、保护2532、现场30均不变；5文件Ruff通过 | 各旧source在所有隔离run无漂移；Ruff不是pytest，保护报告不算测试通过数 |

最初capture校准为1通过／2失败，原因是新helper遗漏现有synthetic.example白名单，已修正；该工具准备错误及初版退出写入覆盖原异常均保留历史，不是历史产品根因。C03默认临时目录清理问题已以必填持久--output关闭。

各run的诊断源码并非全部等于最终版。prefix使用e3b8 helper／195f5 runner／旧3参数测试；当前新增节点元数据、异常输出处理及timeout测试分别由最终4项检查和独立probe支持。4项检查时runner为6e745…；最终只改CLI为必填输出并删除TemporaryDirectory分支，根静态核对`run()`完整函数源码不变，短CLI及timeout补验明确匹配最终113a47…，不重跑长prefix、不称所有旧run在最终417字节上重验。

32次prefix节点映射是事后取证，28次有已持久operation时间元组佐证，原trace直接捕获pytest节点为0；当前helper的新字段不追授给旧记录。32条中29次成功／重放、2次预期MEDIA_RESPONSE_UNCONFIRMED、1次预期MEDIA_PLATFORM_FEE_REQUIRED；预期拒绝不是新COMP01。

## LR01–LR07有限判定

| 条件 | 最终结论 |
| --- | --- |
| LR01 三类结论 | 通过口径核对：历史原因未知、自然产品复现未获、人工反例／工具缺陷单列 |
| LR02 捕获可追溯 | 已证受控校准中保序及包装前旧断言，原异常／正常结果保持；当前元数据与源版本明确，不声称自然失败已捕获 |
| LR03 因果有界 | 假设与比较条件明确；绝对wall倒退观察0，相对wall−mono约±0.448ms不是倒退。permit→record观察均正11.664–91.175ms；不定时钟／NTP原因 |
| LR04 产品修复红绿 | 无自然产品缺陷或生产修改，未交付也未声称产品修复；该项产品红绿不适用本次工具交付 |
| LR05 旧语义保持 | 原validator／旧tests未改，人工非法时间仍拒绝；采集容错与C03关闭，公共工具无私有证据路径依赖 |
| LR06 版本与计数 | 旧50／1保留；迭代／重复节点／剔除与多版本分别记录，不称全历史suite通过 |
| LR07 隔离保护 | 后端／统筹零外网与保护报告接受；无真实账户／Key／原状态或费用变更，PRD不重复运行 |

## 未关闭及后续边界

**COMP01仍开放。** 原未提交candidate的准确UUID／nonce／时间与原实际断言不可恢复；原进程和OS负载、罕见调度／时钟变化、trace观察效应、既有或D48性能敏感条件仍未排除。未复现不是已修复，不把人工时间反例写成原失败原因，也不改D48原50／1记录。

原因未知不泛化为禁止后续开发。可以准备保留旧unknown、预算／风险／累计上限的正式出图合成链与有界真实验证方案；next-validation-brief仅为准备，尚未派发执行。本轮不付费、不调用provider、不续权、不重试旧任务，不要求用户Key／登录／原预算确认。

原native90／parent47、正式2/4／unknown3、原100元／风险83.37元／累计55保持，风险不是消费；D46唯一404继续停止。真实媒体、内容质量、最终人验及完整Harness均未验收。最终来源与结构化结论见D49/prd/ACCEPTANCE.json和RESULT.json。
