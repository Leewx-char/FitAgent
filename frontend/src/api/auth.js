import api from '@/api'

export function register(username, password) {
  return api.post('/auth/register', { username, password })
}

export function login(username, password) {
  const params = new URLSearchParams()
  params.append('username', username)
  params.append('password', password)
  return api.post('/auth/login', params, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
}

export function getCurrentUser() {
  return api.get('/auth/me')
}