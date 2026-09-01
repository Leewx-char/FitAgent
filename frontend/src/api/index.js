import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    } else if (!error.response) {
      console.error('网络错误：无法连接到服务器')
    }
    return Promise.reject(error)
  },
)

/** 优先拼接服务端返回的错误消息，缺失时使用调用方提供的兜底文案。 */
export function getErrorMessage(error, fallback = '请求失败，请稍后重试') {
  const messages = error.response?.data?.messages
  return Array.isArray(messages) && messages.length > 0 ? messages.join('；') : fallback
}

export default api
