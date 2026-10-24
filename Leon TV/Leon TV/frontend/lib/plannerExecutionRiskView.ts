/** Public copy for risk allocation; none of these labels assert a billing bound. */
export const EMPIRICAL_RISK_COPY = {
  title: '经验风险模式',
  disclosure: '风险预留是明确接受的风险分配，不保证供应商用量或实际扣费的硬上限。预算草稿不构成执行授权。',
  retention: '每次提交的风险预留持续保留，不因收到回执、本地估价或平台费用核对而释放。',
  awaitingFee: '平台费用待核对：当前不处理角色输出，不提交下一调用。只有服务端完成规定核费且任务未暂停、授权未撤销、不存在未确认结果时，才继续原工作线程。',
  overrun: '本地估价或平台观察费用超过本次风险预留，已记录超出情况并停止后续执行；估价不代表平台扣费，风险预留不会释放。',
  localEstimate: '本地估价（不是平台扣费）',
  platformObserved: '平台已观察费用（不是供应商账单确认）',
  retainedRisk: '已提交风险预留（持续保留）',
} as const;
