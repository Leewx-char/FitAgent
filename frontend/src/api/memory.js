import api from './index'

export function getMemories(includeRevoked = false) {
  return api.get('/memory', { params: { include_revoked: includeRevoked } })
}

export function updateMemory(memoryId, payload) {
  return api.patch(`/memory/${memoryId}`, payload)
}

export function revokeMemory(memoryId) {
  return api.delete(`/memory/${memoryId}`)
}
