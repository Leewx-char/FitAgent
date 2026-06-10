<template>
  <div class="chat-layout">
    <aside class="sidebar">
      <div class="sidebar-top">
        <div class="sidebar-brand">
          <div class="brand-icon">F</div>
          <span class="brand-name">FitAgent</span>
        </div>
        <n-button type="primary" ghost block size="small" @click="newSession">
          + 新建对话
        </n-button>
      </div>

      <div class="session-list">
        <div
          v-for="session in chatStore.sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: session.id === chatStore.currentSessionId }"
          @click="switchSession(session.id)"
        >
          <span class="session-dot">●</span>
          <span class="session-title">{{ session.title || '新对话' }}</span>
          <span class="session-delete" @click.stop="handleDeleteSession(session.id)">×</span>
        </div>
        <div v-if="chatStore.sessions.length === 0" class="session-empty">
          暂无对话
        </div>
      </div>

      <div class="sidebar-bottom">
        <div class="sidebar-profile" v-if="profileStore.profile" @click="showUserMenu = !showUserMenu">
          <div class="profile-avatar">{{ (authStore.user?.username || '用')[0] }}</div>
          <div class="profile-info">
            <div class="profile-name">{{ authStore.user?.username || '用户' }}</div>
            <div class="profile-meta">{{ profileStore.profile.goal }} · {{ profileStore.profile.experience }}</div>
          </div>
          <span class="profile-arrow">›</span>
        </div>
        <div v-else class="sidebar-profile" @click="$router.push('/onboarding')">
          <div class="profile-avatar">?</div>
          <div class="profile-info">
            <div class="profile-name">完善档案</div>
            <div class="profile-meta">点击填写</div>
          </div>
          <span class="profile-arrow">›</span>
        </div>
        <transition name="fade">
          <div v-if="showUserMenu" class="user-menu">
            <div class="menu-item" @click="showUserMenu = false; $router.push('/profile')">查看画像</div>
            <div class="menu-item" @click="showUserMenu = false; $router.push('/onboarding')">更新档案</div>
            <div class="menu-item menu-danger" @click="handleLogout">退出登录</div>
          </div>
        </transition>
      </div>
    </aside>

    <main class="chat-main">
      <div class="messages-container" ref="messagesRef">
        <div v-if="messages.length === 0 && !uploadResult" class="empty-chat">
          <div class="empty-icon">🏋️</div>
          <h3>你好，{{ authStore.user?.username || '运动达人' }}！</h3>
          <p>告诉我你的训练疑问，我来帮你解答</p>
          <div class="quick-actions">
            <n-tag
              v-for="action in quickActions"
              :key="action"
              class="quick-tag"
              @click="sendQuick(action)"
            >
              {{ action }}
            </n-tag>
          </div>
        </div>

        <div
          v-for="(msg, index) in messages"
          :key="index"
          class="message-row"
          :class="msg.role"
        >
          <div class="message-bubble" :class="msg.role">
            <div v-html="renderMarkdown(msg.content)" />
          </div>
        </div>

        <div v-if="thinking" class="message-row assistant">
          <div class="thinking-panel">
            <div
              v-for="(tool, idx) in toolChain"
              :key="idx"
              class="tool-line"
              :class="{ active: tool.status === 'active' }"
            >
              <span class="tool-icon">{{ tool.status === 'done' ? '✓' : '' }}</span>
              <span class="tool-name">{{ tool.name }}</span>
              <span v-if="tool.status === 'active'" class="dots">
                <span class="dot" />
                <span class="dot" />
                <span class="dot" />
              </span>
            </div>
            <div v-if="toolChain.length === 0" class="tool-line active">
              <span class="tool-name">思考中</span>
              <span class="dots">
                <span class="dot" />
                <span class="dot" />
                <span class="dot" />
              </span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="uploadResult" class="confirm-overlay">
        <div class="confirm-panel">
          <div class="confirm-header">
            <span class="confirm-title">健康数据提取结果</span>
            <span class="confirm-close" @click="uploadResult = null">✕</span>
          </div>

          <div v-if="uploadResult.status === 'ok'" class="confirm-body">
            <div class="confirm-meta">
              <n-tag size="small" type="info">{{ uploadResult.doc_type || '文档' }}</n-tag>
            </div>
            <div class="confirm-data-grid">
              <div
                v-for="key in displayFields"
                :key="key"
                class="confirm-data-item"
              >
                <span class="data-label">{{ fieldLabels[key] }}</span>
                <span class="data-value" v-if="getFieldValue(key)">
                  {{ getFieldValue(key) }}<span class="data-unit" v-if="getFieldUnit(key)">{{ getFieldUnit(key) }}</span>
                </span>
                <span class="data-value data-none" v-else>未识别</span>
            </div>
            </div>
            <template v-if="uploadResult.data.other_findings && uploadResult.data.other_findings.length">
              <div class="confirm-section-title">其他发现</div>
              <div class="confirm-data-grid">
                <div
                  v-for="(item, idx) in uploadResult.data.other_findings"
                  :key="'other-' + idx"
                  class="confirm-data-item"
                >
                  <span class="data-label">{{ item.field }}</span>
                  <span class="data-value">{{ item.value }}</span>
              </div>
            </div>
            </template>
            <div v-if="uploadResult.data.raw_summary" class="confirm-summary">
              {{ uploadResult.data.raw_summary }}
            </div>
          </div>

          <div v-else class="confirm-body confirm-error">
            <div class="error-icon">⚠️</div>
            <div class="error-text">{{ uploadResult.message || '文档解析失败，请重试' }}</div>
            <div v-if="uploadResult.status === 'encrypted'" class="error-hint">请截图后以图片形式重新上传</div>
          </div>

          <div class="confirm-actions" v-if="uploadResult.status === 'ok'">
            <n-button @click="uploadResult = null">取消</n-button>
            <n-button type="primary" @click="confirmHealthData">确认保存到画像</n-button>
          </div>
          <div class="confirm-actions" v-else>
            <n-button @click="uploadResult = null">关闭</n-button>
          </div>
        </div>
      </div>

      <div class="input-area">
        <div class="input-top-row">
          <span class="upload-hint" @click="triggerUpload">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            体检报告
          </span>
          <span class="upload-formats">支持 JPG/PNG/WebP/PDF，最大10MB</span>
          <span class="upload-status" v-if="uploading">识别中...</span>
        </div>
        <div class="input-bottom-row">
          <n-input
            v-model:value="inputText"
            type="textarea"
            :rows="2"
            :autosize="{ minRows: 1, maxRows: 4 }"
            placeholder="输入你的问题..."
            @keydown.enter.exact="handleSend"
          />
          <n-button
            type="primary"
            :disabled="!inputText.trim() || streaming || uploading"
            @click="handleSend"
            style="margin-left: 12px; align-self: flex-end"
          >
            发送
          </n-button>
        </div>
        <input
          ref="fileInputRef"
          type="file"
          accept=".jpg,.jpeg,.png,.webp,.pdf"
          style="display: none"
          @change="handleFileSelect"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useProfileStore } from '@/stores/profile'
import { useChatStore } from '@/stores/chat'
import { getSessions, createSession, deleteSession, getMessages } from '@/api/chat'
import { uploadHealthDoc, updateProfile } from '@/api/profile'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const profileStore = useProfileStore()
const chatStore = useChatStore()

const messagesRef = ref(null)
const inputText = ref('')
const streaming = ref(false)
const uploading = ref(false)
const uploadResult = ref(null)
const showUserMenu = ref(false)
const fileInputRef = ref(null)
const thinking = ref(false)
const toolChain = ref([])

const messages = computed(() => chatStore.messages)

const fieldLabels = {
  height_cm: '身高',
  weight_kg: '体重',
  bmi: 'BMI',
  body_fat: '体脂率',
  heart_rate: '心率',
  blood_pressure: '血压',
  blood_sugar: '血糖',
  cholesterol: '胆固醇',
  alt: '谷丙转氨酶',
  uric_acid: '尿酸',
}

const displayFields = Object.keys(fieldLabels)

function getFieldValue(key) {
  const field = uploadResult.value?.data?.[key]
  if (!field || typeof field !== 'object') return null
  return field.value != null ? field.value : null
}

function getFieldUnit(key) {
  const field = uploadResult.value?.data?.[key]
  if (!field || typeof field !== 'object') return null
  return field.unit || null
}

const quickActions = [
  '帮我制定减脂训练计划',
  '膝盖不好怎么练腿？',
  '蛋白质每天吃多少？',
  '新手健身注意事项',
]

function renderMarkdown(text) {
  if (!text) return ''
  let html = text
  html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>')
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  html = html.replace(/^(\d+)\. (.+)$/gm, '<li class="ol-item">$2</li>')
  html = html.replace(/^\- (.+)$/gm, '<li>$1</li>')
  html = html.replace(/((?:<li[^>]*>.*<\/li>\s*)+)/g, '<ul>$1</ul>')
  html = html.replace(/\n/g, '<br>')
  return html
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

async function loadSessions() {
  try {
    const res = await getSessions()
    chatStore.sessions = res.data
  } catch (err) {
    message.error('加载会话列表失败')
  }
}

async function switchSession(sessionId) {
  if (chatStore.currentSessionId === sessionId) return
  chatStore.currentSessionId = sessionId
  try {
    const res = await getMessages(sessionId)
    chatStore.setMessages(res.data)
  } catch {
    chatStore.clearMessages()
  }
  scrollToBottom()
}

async function newSession() {
  try {
    const res = await createSession()
    chatStore.sessions.unshift(res.data)
    chatStore.currentSessionId = res.data.id
    chatStore.clearMessages()
  } catch {
    message.error('创建会话失败')
  }
}

async function handleDeleteSession(sessionId) {
  try {
    await deleteSession(sessionId)
    chatStore.sessions = chatStore.sessions.filter((s) => s.id !== sessionId)
    if (chatStore.currentSessionId === sessionId) {
      chatStore.currentSessionId = null
      chatStore.clearMessages()
    }
  } catch {
    message.error('删除失败')
  }
}

function handleSend(e) {
  if (e?.shiftKey) return
  if (!inputText.value.trim() || streaming.value || uploading.value) return
  e?.preventDefault()
  sendMessage(inputText.value.trim())
  inputText.value = ''
}

function sendQuick(text) {
  if (streaming.value || uploading.value) return
  sendMessage(text)
}

function sendMessage(text) {
  chatStore.addMessage({ role: 'user', content: text })
  chatStore.currentSessionId = chatStore.currentSessionId || null
  streaming.value = true
  thinking.value = true
  toolChain.value = []
  scrollToBottom()

  const token = localStorage.getItem('token')
  const sessionId = chatStore.currentSessionId || ''

  fetch('/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      message: text,
      session_id: sessionId || undefined,
    }),
  })
    .then((response) => {
      if (response.status === 401) {
        streaming.value = false
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        router.push('/login')
        message.error('登录已过期，请重新登录')
        return null
      }
      if (!response.ok) {
        streaming.value = false
        message.error(`请求失败 (${response.status})`)
        return null
      }
      const sid = response.headers.get('X-Session-Id')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let assistantMsg = ''
      let msgAdded = false
      let sseBuffer = ''

      function read() {
        return reader.read().then(({ done, value }) => {
          if (done) {
            streaming.value = false
            if (sid && !chatStore.currentSessionId) {
              chatStore.currentSessionId = sid
            }
            if (!msgAdded && assistantMsg) {
              chatStore.addMessage({ role: 'assistant', content: assistantMsg })
            }
            loadSessions()
            return
          }

          const chunk = decoder.decode(value, { stream: true })
          sseBuffer += chunk
          const parts = sseBuffer.split('\n\n')
          sseBuffer = parts.pop()
          for (const part of parts) {
            const lines = part.split('\n')
            for (const line of lines) {
              if (!line.startsWith('data: ')) continue
              const raw = line.slice(6)
              if (raw === '[DONE]') {
                thinking.value = false
                toolChain.value = []
                streaming.value = false
                loadSessions()
                return
              }
              let event
              try {
                event = JSON.parse(raw)
              } catch {
                event = { type: 'text', content: raw }
              }

              if (event.type === 'tool') {
                if (toolChain.value.length > 0) {
                  toolChain.value[toolChain.value.length - 1].status = 'done'
                }
                toolChain.value.push({ name: event.name || '', status: 'active' })
                continue
              }

              if (event.type === 'error') {
                thinking.value = false
                toolChain.value = []
                streaming.value = false
                const errMsg = event.content || '服务异常，请稍后重试'
                chatStore.addMessage({ role: 'assistant', content: errMsg })
                message.error(errMsg)
                continue
              }

              if (event.type === 'text') {
                thinking.value = false
                toolChain.value = []
                if (!msgAdded) {
                  chatStore.addMessage({ role: 'assistant', content: '' })
                  msgAdded = true
                }
                assistantMsg += event.content
                chatStore.messages[chatStore.messages.length - 1].content = assistantMsg
                scrollToBottom()
              }
            }
          }
          read()
        }).catch(() => {
          thinking.value = false
          toolChain.value = []
          streaming.value = false
          if (!msgAdded && assistantMsg) {
            chatStore.addMessage({ role: 'assistant', content: assistantMsg })
          } else if (!assistantMsg) {
            chatStore.addMessage({ role: 'assistant', content: '连接中断，请重试' })
          }
          message.error('连接中断，请重试')
        })
      }
      read()
    })
    .catch(() => {
      streaming.value = false
      message.error('网络连接失败，请检查网络后重试')
    })
}

function triggerUpload() {
  fileInputRef.value?.click()
}

async function handleFileSelect(e) {
  const file = e.target.files?.[0]
  if (!file) return
  e.target.value = ''

  const maxSize = 10 * 1024 * 1024
  if (file.size > maxSize) {
    message.error('文件大小超过10MB限制')
    return
  }

  const allowedExts = ['.jpg', '.jpeg', '.png', '.webp', '.pdf']
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
  if (!allowedExts.includes(ext)) {
    message.error('仅支持 JPG/PNG/WebP 图片和 PDF 文件')
    return
  }

  uploading.value = true
  try {
    const res = await uploadHealthDoc(file)
    uploadResult.value = res.data
    if (res.data.status === 'ok') {
      message.success('文档解析完成，请确认提取的健康数据')
    } else if (res.data.status === 'encrypted') {
      message.warning('该PDF已加密，请截图后以图片形式上传')
    } else if (res.data.status === 'unrelated') {
      message.warning('该文档与健康数据无关')
    }
  } catch (err) {
    message.error(err?.response?.data?.detail || '上传失败，请重试')
  } finally {
    uploading.value = false
  }
}

async function confirmHealthData() {
  if (!uploadResult.value?.data) return
  try {
    const data = uploadResult.value.data
    const payload = { health_data: data }
    if (data.height_cm?.value != null) payload.height = data.height_cm.value
    if (data.weight_kg?.value != null) payload.weight = data.weight_kg.value
    await updateProfile(payload)
    await profileStore.fetchProfile()
    message.success('健康数据已保存到画像')
    uploadResult.value = null
  } catch (err) {
    message.error('保存失败，请重试')
  }
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

onMounted(async () => {
  await profileStore.fetchProfile()
  await loadSessions()
})

watch(toolChain, () => {
  if (thinking.value) scrollToBottom()
}, { deep: true })
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* ===== 侧边栏 ===== */
.sidebar {
  width: 260px;
  background: #f7f8fa;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e8e8e8;
}

.sidebar-top {
  padding: 16px 16px 12px;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.brand-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--primary), var(--primary-dark));
  color: white;
  font-weight: 700;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.session-empty {
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  padding: 24px 0;
}

.session-item {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
  transition: background 0.15s;
  font-size: 13px;
  color: var(--text-primary);
  position: relative;
}

.session-item:hover {
  background: #eef2f7;
}

.session-item.active {
  background: #e3edf7;
}

.session-dot {
  font-size: 8px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.session-item.active .session-dot {
  color: var(--primary);
}

.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.session-delete {
  opacity: 0;
  font-size: 16px;
  color: var(--text-secondary);
  flex-shrink: 0;
  width: 20px;
  text-align: center;
  line-height: 1;
  transition: opacity 0.15s;
}

.session-item:hover .session-delete {
  opacity: 1;
}

.session-delete:hover {
  color: var(--danger);
}

.sidebar-bottom {
  border-top: 1px solid #e8e8e8;
  padding: 10px 12px;
  position: relative;
}

.sidebar-profile {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.sidebar-profile:hover {
  background: #eef2f7;
}

.profile-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--primary-light);
  color: var(--primary-dark);
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.profile-info {
  flex: 1;
  min-width: 0;
}

.profile-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile-meta {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 1px;
}

.profile-arrow {
  font-size: 16px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.user-menu {
  background: white;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  margin-top: 6px;
  overflow: hidden;
}

.menu-item {
  padding: 10px 16px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
  transition: background 0.15s;
}

.menu-item:hover {
  background: #f0f5ff;
}

.menu-danger {
  color: var(--danger);
}

.menu-danger:hover {
  background: #fff0f0;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* ===== 聊天区 ===== */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-page);
  position: relative;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px 48px;
}

.empty-chat {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-secondary);
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.empty-chat h3 {
  color: var(--text-primary);
  margin-bottom: 8px;
}

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
  justify-content: center;
}

.quick-tag {
  cursor: pointer;
}

.message-row {
  margin-bottom: 20px;
  display: flex;
}

.message-row.user {
  justify-content: flex-end;
}

.message-row.assistant {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 75%;
  font-size: 14px;
  line-height: 1.7;
}

.message-bubble.user {
  background: var(--primary);
  color: white;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
}

.message-bubble.assistant {
  background: #f0f1f3;
  color: var(--text-primary);
  padding: 10px 16px;
  border-radius: 4px 16px 16px 16px;
}

.message-bubble.assistant :deep(h2) {
  font-size: 16px;
  font-weight: 600;
  margin: 12px 0 6px;
}

.message-bubble.assistant :deep(h3) {
  font-size: 15px;
  font-weight: 600;
  margin: 10px 0 4px;
}

.message-bubble.assistant :deep(h4) {
  font-size: 14px;
  font-weight: 600;
  margin: 8px 0 4px;
}

.message-bubble.assistant :deep(ul) {
  padding-left: 20px;
  margin: 6px 0;
}

.message-bubble.assistant :deep(li) {
  margin: 2px 0;
}

.message-bubble.assistant :deep(code) {
  background: #f0f5ff;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}

.message-bubble.assistant :deep(pre) {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
}

.message-bubble.assistant :deep(strong) {
  color: var(--primary-dark);
}

/* ===== 确认面板 ===== */
.confirm-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.confirm-panel {
  background: white;
  border-radius: 12px;
  width: 480px;
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
}

.confirm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
}

.confirm-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.confirm-close {
  font-size: 18px;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
}

.confirm-close:hover {
  color: var(--text-primary);
}

.confirm-body {
  padding: 16px 20px;
}

.confirm-meta {
  margin-bottom: 12px;
}

.confirm-data-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.confirm-data-item {
  background: #f7f8fa;
  border-radius: 8px;
  padding: 10px 12px;
}

.data-label {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.data-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.data-unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 2px;
}

.data-none {
  color: var(--text-secondary);
  font-weight: 400;
}

.confirm-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-top: 16px;
  margin-bottom: 8px;
}

.confirm-summary {
  margin-top: 12px;
  font-size: 13px;
  color: var(--text-secondary);
  padding: 8px 12px;
  background: #f7f8fa;
  border-radius: 6px;
}

.confirm-error {
  text-align: center;
  padding: 32px 20px;
}

.error-icon {
  font-size: 36px;
  margin-bottom: 12px;
}

.error-text {
  font-size: 14px;
  color: var(--text-primary);
}

.error-hint {
  font-size: 13px;
  color: var(--primary);
  margin-top: 8px;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px 16px;
  border-top: 1px solid #f0f0f0;
}

.input-area {
  display: flex;
  flex-direction: column;
  padding: 12px 48px 20px;
  gap: 8px;
}

.input-top-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.upload-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--primary);
  cursor: pointer;
  user-select: none;
  transition: opacity 0.15s;
}

.upload-hint:hover {
  opacity: 0.8;
}

.upload-formats {
  font-size: 12px;
  color: var(--text-secondary);
}

.upload-status {
  font-size: 12px;
  color: var(--primary);
}

.input-bottom-row {
  display: flex;
  align-items: flex-end;
}

.thinking-panel {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--text-primary);
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  min-width: 180px;
}

.tool-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.tool-line.active {
  color: var(--text-primary);
}

.tool-icon {
  width: 16px;
  font-size: 12px;
  color: #52c41a;
  flex-shrink: 0;
  text-align: center;
}

.tool-name {
  white-space: nowrap;
}

.dots {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-left: 2px;
}

.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--primary);
  animation: dot-seq 1.2s ease-in-out infinite;
}

.dot:nth-child(1) { animation-delay: 0s; }
.dot:nth-child(2) { animation-delay: 0.4s; }
.dot:nth-child(3) { animation-delay: 0.8s; }

@keyframes dot-seq {
  0%, 30%, 100% { transform: scale(1); opacity: 0.4; }
  15% { transform: scale(1.8); opacity: 1; }
}
</style>