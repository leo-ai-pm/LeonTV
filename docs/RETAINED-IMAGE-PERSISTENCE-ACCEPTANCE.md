# D50 retained-unknown正式图片持久化有限验收

2026-11-01（Asia/Shanghai）。状态：短清单已交付，等待统筹结果后一次性最终审查，尚未验收通过。仅补 `RuntimeContinuedImageControl + retained_unknown` 的合成原生接缝，不重做D48普通链／UI，不新增窗口或子代理。

| 条件 | 有限验收依据 |
| --- | --- |
| RP01 保留与准入 | 合成场景明确原unknown数量并保留原记录、账户、预算／累计次数、已有delivery及旧权限；未来operation出生前准确绑定新opt-in，不清unknown、不换账户重置或追授旧操作。合成unknown不得称原真实账户回放 |
| RP02 原生自动链 | 由原session取得规范completed，自动进入一次共享worker接收、完整验证／耐久保存及原delivery；提交／查询mock次数、本地接收次数和外部尝试明确。重复query／recover不二次下载；不以诊断artifact代正式delivery。若使用前镜来源，准确关联原delivery |
| RP03 只读投影 | 实际ASGI及独立绑定task／source样本同源，当前文件可用与历史保存／登记分别准确；GET文件指纹不变、不触发下载／恢复。沿用既有严格decoder合同；质量未审、人验pending，不把合成结果称真实媒体或完整Harness |
| RP04 版本与准备边界 | 实测源码、变更／相关回归、零外网和保护报告可对照；原状态／旧tests／前端／依赖不受改动。未来一镜包只列原生门禁、准确待绑定输入／有效性、一次动作与停止条件／缺项，不编造ID，不执行、续权、付费或旧任务重试 |

入口7213480；统筹清单报告backend417／frontend116、保护2532、原失败现场30一致。原native90／parent47、正式2/4／unknown3、原100元／保留风险83.37元／累计55保持，风险不是消费。COMP01仍开放，旧50通过／1失败不因本轮绿灯关闭。

本轮PRD仅读文本和结果摘要，不执行runtime／tests／network或Git，不读真实秘密、不改代码或历史记录。四项是条件数，不是测试数，不扩矩阵、不安排长测；结果到位后仅按实际范围验收。真实一镜执行包是准备，尚未派发；不要求用户重复Key、登录或预算。
