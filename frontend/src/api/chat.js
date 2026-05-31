import api from '@/api'

export function getSessions() {
  return api.get('/sessions')
}

export function createSession(title = '新对话') {
  return api.post('/sessions', { title })
}

export function deleteSession(sessionId) {
  return api.delete(`/sessions/${sessionId}`)
}

export function getMessages(sessionId) {
  return api.get(`/sessions/${sessionId}/messages`)
}

export function chatMessage(message, sessionId = null) {
  return api.post('/chat', { message, session_id: sessionId })
}