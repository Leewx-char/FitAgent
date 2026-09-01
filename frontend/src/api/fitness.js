import api from './index'

/** 请求同步可选日期范围内的运动设备数据。 */
export function syncFitness(startDay = '', endDay = '') {
  return api.post('/fitness/sync', { start_day: startDay, end_day: endDay })
}

/** 获取指定周数的每日训练和恢复指标。 */
export function getDailyMetrics(weeks = 4) {
  return api.get('/fitness/daily', { params: { weeks } })
}

/** 获取指定周数的睡眠阶段记录。 */
export function getSleepData(weeks = 4) {
  return api.get('/fitness/sleep', { params: { weeks } })
}

/** 按可选起止日期获取运动活动明细。 */
export function getActivities(startDay = '', endDay = '') {
  return api.get('/fitness/activities', { params: { start_day: startDay, end_day: endDay } })
}
