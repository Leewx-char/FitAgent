import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as loginApi, register as registerApi, getCurrentUser } from '@/api/auth'

/** 提供持久化登录状态及认证相关操作的 Pinia store。 */
export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))

  const isLoggedIn = computed(() => !!token.value)

  /** 同步更新内存与本地存储中的令牌和用户信息。 */
  function setAuth(newToken, newUser) {
    token.value = newToken
    user.value = newUser
    localStorage.setItem('token', newToken)
    localStorage.setItem('user', JSON.stringify(newUser))
  }

  /** 登录后保存访问令牌，并继续请求当前用户资料。 */
  async function login(username, password) {
    const res = await loginApi(username, password)
    const { access_token } = res.data.data
    localStorage.setItem('token', access_token)
    token.value = access_token

    const userRes = await getCurrentUser()
    user.value = userRes.data.data
    localStorage.setItem('user', JSON.stringify(userRes.data.data))
  }

  /** 注册账号；成功后的页面跳转由调用页面处理。 */
  async function register(username, password) {
    await registerApi(username, password)
  }

  /** 清除本地登录状态，使后续路由守卫要求重新登录。 */
  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  /** 在已有令牌时刷新用户资料；请求失败则退出登录。 */
  async function fetchUser() {
    if (!token.value) return
    try {
      const res = await getCurrentUser()
      user.value = res.data.data
      localStorage.setItem('user', JSON.stringify(res.data.data))
    } catch {
      logout()
    }
  }

  return { token, user, isLoggedIn, setAuth, login, register, logout, fetchUser }
})
