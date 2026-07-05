import api from './index'

export function syncFitness(startDay = '', endDay = '') {
  return api.post('/fitness/sync', { start_day: startDay, end_day: endDay })
}

export function getDailyMetrics(weeks = 4) {
  return api.get('/fitness/daily', { params: { weeks } })
}

export function getSleepData(weeks = 4) {
  return api.get('/fitness/sleep', { params: { weeks } })
}

export function getActivities(startDay = '', endDay = '') {
  return api.get('/fitness/activities', { params: { start_day: startDay, end_day: endDay } })
}
