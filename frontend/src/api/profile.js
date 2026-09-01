import api from '@/api'

/** 读取当前用户的健身档案。 */
export function getProfile() {
  return api.get('/profile')
}

/** 创建首次填写的健身档案。 */
export function createProfile(data) {
  return api.post('/profile', data)
}

/** 更新档案中调用方提供的字段。 */
export function updateProfile(data) {
  return api.put('/profile', data)
}

/** 上传健康文档并等待服务端提取健康指标。 */
export function uploadHealthDoc(file) {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/upload/health-doc', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}
