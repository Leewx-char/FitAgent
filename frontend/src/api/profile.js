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

export function uploadHealthDoc(file) {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/upload/health-doc', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}