import api from '@/api'

/** 获取当前用户的全部对话会话，供侧边栏初始化列表。 */
export function getSessions() {
  return api.get('/sessions')
}

/** 以指定标题创建会话，默认使用“新对话”。 */
export function createSession(title = '新对话') {
  return api.post('/sessions', { title })
}

/** 删除指定会话及其服务端保存的内容。 */
export function deleteSession(sessionId) {
  return api.delete(`/sessions/${sessionId}`)
}

/** 读取指定会话的历史消息，用于切换会话后恢复聊天记录。 */
export function getMessages(sessionId) {
  return api.get(`/sessions/${sessionId}/messages`)
}