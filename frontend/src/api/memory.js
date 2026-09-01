import api from './index'

/** 获取当前用户记忆，可选择包含已撤销项。 */
export function getMemories(includeRevoked = false) {
  return api.get('/memory', { params: { include_revoked: includeRevoked } })
}

/** 更新指定记忆的状态或其他允许编辑的内容。 */
export function updateMemory(memoryId, payload) {
  return api.patch(`/memory/${memoryId}`, payload)
}

/** 撤销指定记忆，使其不再参与后续个性化建议。 */
export function revokeMemory(memoryId) {
  return api.delete(`/memory/${memoryId}`)
}
