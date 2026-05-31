import api from '@/api'

export function getProfile() {
  return api.get('/profile')
}

export function createProfile(data) {
  return api.post('/profile', data)
}

export function updateProfile(data) {
  return api.put('/profile', data)
}