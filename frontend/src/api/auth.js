import api from '@/api'

/** 提交用户名和密码以注册账号。 */
export function register(username, password) {
  return api.post('/auth/register', { username, password })
}

/** 以表单编码提交凭据，换取登录访问令牌。 */
export function login(username, password) {
  const params = new URLSearchParams()
  params.append('username', username)
  params.append('password', password)
  return api.post('/auth/login', params, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
}

/** 获取当前访问令牌对应的用户资料。 */
export function getCurrentUser() {
  return api.get('/auth/me')
}
