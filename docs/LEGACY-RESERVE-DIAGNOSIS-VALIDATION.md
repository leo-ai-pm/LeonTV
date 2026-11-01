# D49 COMP01 有界诊断验证

2026-11-01，Asia/Shanghai。入口c688966cf8f5dc72e4addce864bdfa32603b2012，后端412／前端116／保护2532／原失败合成现场30项。范围是D48旧legacy首次reserve退出校验的CORRUPT_STORAGE；原50pass／1fail继续保留。本阶段没有因证据不足而修改生产validator或放宽旧约束。

## 三类结论

历史原失败：D48未提交候选和原进程状态不可恢复，原实际断言及原因仍未确定。D48保序重建在entry／D48都过，以及人工时间反例，均不当作历史定因；本轮不重复这些旧比较。

D49真实未干预故障：常规分支、调度窗口分支和一次同进程原测试顺序前缀均未捕获新的COMP01。前缀实际41pass／10deselected，524.50秒，目标节点首次reserve也未失败。没有真实自然红信号，不交付“生产缺陷修复”结论；交付可重复诊断能力，历史问题继续开放。

人工反例和工具修正：新的真实reserve调用路径中，明确将operation创建时间置于permit之前1微秒，仅用于证明捕获接缝。原时间validator仍拒绝；人工反例不证明历史原因。工具初版校准2fail／1pass暴露合成text endpoint漏项；修正后真实路径校准3pass，再最终工具4pass（含runner超时处理），具体命令和SHA见本轮backend证据。这些是工具验证，不是COMP01产品修复红绿。

## 可重复公共入口

在backend目录：

```sh
.venv/bin/python tests/run_legacy_reserve_diagnosis.py --iterations 8 --output /absolute/new-pilot-output
.venv/bin/python tests/run_legacy_reserve_diagnosis.py --iterations 128 --build-delay-ms 30 --output /absolute/new-output
.venv/bin/python tests/run_legacy_reserve_diagnosis.py --mode prefix --output /absolute/new-prefix-output
```

`--output`为必填的新持久目录，不能复用已有public-backend；默认入口不会在失败返回后删除唯一现场。不传目录时CLI直接以2退出，不启动fixture。工具不引用私有evidence路径。batch最多256次、额外调度0–40ms；prefix只取原image_operator→media_ledger→media_runtime到目标unknown节点（41节点），不跑后半组。batch子进程300秒、prefix660秒上限。超时保留partial stdout/stderr、返回124并标timed_out，已有捕获文件保留，不能当通过。

隔离副本只含公开源码与固定测试数据，不复制真实config.yaml／.env／harness_data，子进程环境剔除密钥。sitecustomize硬阻断DNS、connect、connect_ex及sendto；receipt supplier是既有合成transport，运行不连接provider或原账户。合成fixture的scope.validation_only和固定synthetic域名在捕获前检查。

批量通过重置新合成fixture的准确已验证journal前缀加速。每批只建1个新合成账户；每次是独立前缀分支，不是128个fresh账户，也不是生产恢复、额度重置方案或真实执行授权。

## 捕获与语义保护

临时sys.settrace只在reserve周围启用。原transaction、role／media／payload validator照常运行，未mock其判定；首次实际异常时在store通用包装之前保序写candidate＋previous、原始栈与源码行、墙钟／单调采样及实际now／utc_now返回，文件0600、目录0700。不用sort_keys重排上下文或重算／修复候选。

最终helper同时记录PYTEST_CURRENT_TEST。prefix中的预期拒绝不等于非预期测试失败；以pytest实际结果及节点映射判断。已启动前缀使用启动时副本，不为后来元数据改动重跑。其32次reserve已另存ordered-prefix-reserve-node-map.json：28次以准确已存operation时间三元组及fixture目录交叉对应，余下1次重放和3次预期拒绝按pytest临时目录创建区间对应。后者是明确标注的事后关联，不伪称启动时直接捕获的nodeid。

采集callback、源码读取或result.json落盘失败仅记capture_errors，原异常对象／成功主体语义保持，不用采集错误替换应用错误。真实reserve人工反例验证数据日志未提交、旧时间断言仍成立。统筹另有3项独立helper probe，以及真实短子进程触发TimeoutExpired的runner probe；后者只证明runner超时处理，不计native产品pytest通过。

## 实际分组与版本

证据根：`docs/evidence/LTV-LEGACY-RESERVE-DIAGNOSIS-20261101/backend/`。各run保留public-source-manifest、命令结果、网络计数、保序样本和条件；工具版本变动单列，生产源码始终保持入口字节。

| 分组 | 结果 | 实际条件 |
| --- | --- | --- |
| pilot-08 | 1复合节点pass，8分支，8.39s | 初版trace；单次reserve中位0.396s；网络0 |
| batch-128-clocks | 1复合节点pass，128分支，68.21s | 默认调度，完整时钟采样，网络0 |
| batch-128-delay30 | 1复合节点pass，128分支，60.50s | build后额外30ms，真实时钟不替换，网络0 |
| capture-calibration | 2fail／1pass，11.62s | 工具合成endpoint保护漏项，保留原失败，不算产品故障 |
| capture-calibration-final | 3pass，8.70s | 校准捕获／result sink／源码读取失败语义，人工反例 |
| final-helper-checks | 4pass，9.29s | 最终helper元数据及runner超时输出；与前组3节点重叠 |
| ordered-prefix-once | 41pass，10deselected，524.50s，exit0 | 原旧测试顺序，网络0，32次reserve／捕获0 |

分支数不等于pytest节点数，重叠组不相加为独立测试总数。旧测试字节不改，无新增skip，不将D48原50／1覆盖成全绿，不声称全历史suite通过。

前缀32次reserve中29次成功／重放，另2次MEDIA_RESPONSE_UNCONFIRMED和1次MEDIA_PLATFORM_FEE_REQUIRED均由原测试预期并通过；没有非预期测试失败，也没有post-validation candidate捕获。最终源码相比该次前缀的3个工具文件差异仅为：trace加入nodeid元数据；runner完善timeout输出并要求持久output；新测试增加默认网络fixture和1项超时检查。生产文件未变。4项工具检查覆盖元数据／超时修订；之后CLI持久目录收尾另做缺output／help边界和同一runner超时短检查。plugin／scenario与前缀副本一致，不因工具完善重跑长前缀。

最终后端源码417项＝入口412＋5个新tests/helper，未修改任何入口生产模块或旧测试。各阶段源码清单、最终5文件SHA、前缀差异及保护检查见backend/FINAL-SOURCE-CHECK.json和RESULT.json；Ruff对5个新文件通过。新增工具都在tests下，未接入默认产品运行时。

最终清单SHA256：d16cc33a67f6ab5f2f7504cfe62cc8260cff692bd214c7c42c74f08ab6086250。最后的CLI检查为缺output退出2、help退出0、重叠runner超时节点1pass，网络0；只完善证据保留，不重复长前缀或改变原校验。

## 条件比较与剩余范围

条件排序及可证伪预测见backend/HYPOTHESES.md。时钟审计中相邻绝对wall_ns倒退均0；128默认分支permit→record为0.011664–0.091175s，额外30ms分支为0.043381–0.055471s。相对时钟采样偏差不当作故障，更不归因NTP或时间同步。

没有原未提交候选，不能恢复准确UUID／nonce／时间及原前序进程／负载／OS条件；trace本身也可能改变调度。已执行一次前缀仍未复现，因此停止扩大同一循环，交付经验证的诊断能力和这些边界，不假造定因或无证据改生产代码。原权限、预算／unknown、时间顺序和完整性检查继续有效。没有自然红信号时，本文条件排序和负例不构成因果证明。

入口ENTRY-CHECK与最终核对均保留保护2532及原失败现场30不变，前端116与入口一致；旧412项源码无变化。原native90／parent47／正式2/4／unknown3、原100元／保留风险83.37／累计55保持，风险不是消费；仅作文件SHA核对，不加载真实Runtime。无真实Key／provider／付费／原任务重试、前端／依赖／AGENTS修改、Git／push或共享服务重启。

本轮有限交付是诊断能力，不是COMP01已修复。后续由统筹决定带着该开放问题准备正式出图链路的合成验收及保留原unknown／预算的真实验证计划；当前不追加实现，不自动执行原任务重试、续权或实付，不要求用户补交已经丢失的历史候选。
