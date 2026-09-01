import api from './index'

// 训练计划需要依次完成混合检索与结构化模型生成，不能沿用普通 CRUD 的 15 秒超时。
const PLAN_GENERATION_TIMEOUT_MS = 120_000

/** 按可选周起始日读取当前训练计划。 */
export function getCurrentPlan(weekStart = '') {
  return api.get('/training-plans/current', { params: weekStart ? { week_start: weekStart } : {} })
}

/** 生成指定周的计划，并为检索与模型生成使用较长超时。 */
export function generateTrainingPlan(weekStart = '') {
  return api.post(
    '/training-plans/generate',
    weekStart ? { week_start: weekStart } : {},
    { timeout: PLAN_GENERATION_TIMEOUT_MS },
  )
}

/** 保存某个计划日的完成状态、主观用力程度和备注。 */
export function savePlanFeedback(planId, feedback) {
  return api.post(`/training-plans/${planId}/feedback`, feedback)
}
