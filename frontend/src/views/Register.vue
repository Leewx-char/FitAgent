<template>
  <div class="auth-container">
    <div class="auth-card">
      <div class="auth-header">
        <div class="auth-logo">F</div>
        <h1 class="auth-title">FitAgent</h1>
        <p class="auth-subtitle">注册账号，开启你的健身之旅</p>
      </div>

      <n-form ref="formRef" :model="form" :rules="rules" label-placement="top">
        <n-form-item path="username" label="用户名">
          <n-input
            v-model:value="form.username"
            placeholder="请输入用户名"
            size="large"
          />
        </n-form-item>

        <n-form-item path="password" label="密码">
          <n-input
            v-model:value="form.password"
            type="password"
            show-on-toggle
            placeholder="请输入密码（至少6位）"
            size="large"
          />
        </n-form-item>

        <n-form-item path="confirmPassword" label="确认密码">
          <n-input
            v-model:value="form.confirmPassword"
            type="password"
            show-on-toggle
            placeholder="请再次输入密码"
            size="large"
            @keyup.enter="handleRegister"
          />
        </n-form-item>

        <n-button
          type="primary"
          block
          size="large"
          :loading="loading"
          @click="handleRegister"
          style="margin-top: 8px"
        >
          注册
        </n-button>
      </n-form>

      <div class="auth-footer">
        已有账号？
        <router-link to="/login">立即登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { getErrorMessage } from '@/api'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
  confirmPassword: '',
})

const rules = {
  username: { required: true, message: '请输入用户名', trigger: 'blur' },
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码至少6位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    {
      validator: (rule, value) => value === form.password,
      message: '两次密码不一致',
      trigger: 'blur',
    },
  ],
}

async function handleRegister() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    await authStore.register(form.username, form.password)
    message.success('注册成功，请登录')
    router.push('/login')
  } catch (err) {
    message.error(getErrorMessage(err, '注册失败'))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #e8f4fd 0%, #F8FBFF 40%, var(--primary-light) 100%);
}

.auth-card {
  width: 400px;
  padding: 44px 40px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(66, 165, 245, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04);
}

.auth-header {
  text-align: center;
  margin-bottom: 36px;
}

.auth-logo {
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--primary), #1E88E5);
  color: #fff;
  font-size: 26px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
}

.auth-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.auth-subtitle {
  color: var(--text-secondary);
  font-size: 14px;
}

.auth-footer {
  text-align: center;
  margin-top: 20px;
  color: var(--text-secondary);
  font-size: 14px;
}
</style>
