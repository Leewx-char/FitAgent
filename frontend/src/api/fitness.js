import api from './index'

/** 读取一次不落库的实时 COROS 数据快照。 */
export function getFitnessSnapshot(weeks = 4) {
  return api.get('/fitness/snapshot', { params: { weeks } })
}

/** 发起当前登录用户的浏览器 OAuth 连接。 */
export function connectCoros() {
  return api.post('/coros/connect')
}

/** 查询当前用户是否存在有效的本地 COROS 连接。 */
export function getCorosConnection() {
  return api.get('/coros/connection')
}

/** 删除当前用户本地的加密 COROS 凭据。 */
export function disconnectCoros() {
  return api.delete('/coros/connection')
}
