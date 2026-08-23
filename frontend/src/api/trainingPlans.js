import api from './index'

// 训练计划需要依次完成混合检索与结构化模型生成，不能沿用普通 CRUD 的 15 秒超时。
const PLAN_GENERATION_TIMEOUT_MS = 120_000

export function getCurrentPlan(weekStart = '') {
  return api.get('/training-plans/current', { params: weekStart ? { week_start: weekStart } : {} })
}

export function generateTrainingPlan(weekStart = '') {
  return api.post(
    '/training-plans/generate',
    weekStart ? { week_start: weekStart } : {},
    { timeout: PLAN_GENERATION_TIMEOUT_MS },
  )
}

export function savePlanFeedback(planId, feedback) {
  return api.post(`/training-plans/${planId}/feedback`, feedback)
}
