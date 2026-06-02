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
        <div v-if="messages.length === 0" class="empty-chat">
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

        <div v-if="streaming" class="message-row assistant">
          <div class="message-bubble assistant">
            <div v-if="toolSteps.length" class="tool-steps">
              <div v-for="(step, idx) in toolSteps" :key="idx" class="tool-step">
                <span class="tool-icon">🔍</span> {{ step }}...
              </div>
            </div>
            <div v-else class="typing">
              <span class="typing-dot" /><span class="typing-dot" /><span class="typing-dot" />
            </div>
          </div>
        </div>
      </div>

      <div class="input-area">
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
          :disabled="!inputText.trim() || streaming"
          @click="handleSend"
          style="margin-left: 12px; align-self: flex-end"
        >
          发送
        </n-button>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useProfileStore } from '@/stores/profile'
import { useChatStore } from '@/stores/chat'
import { getSessions, createSession, deleteSession, getMessages } from '@/api/chat'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const profileStore = useProfileStore()
const chatStore = useChatStore()

const messagesRef = ref(null)
const inputText = ref('')
const streaming = ref(false)
const toolSteps = ref([])
const showUserMenu = ref(false)

const messages = computed(() => chatStore.messages)

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
  if (!inputText.value.trim() || streaming.value) return
  e?.preventDefault()
  sendMessage(inputText.value.trim())
  inputText.value = ''
}

function sendQuick(text) {
  if (streaming.value) return
  sendMessage(text)
}

function sendMessage(text) {
  chatStore.addMessage({ role: 'user', content: text })
  chatStore.currentSessionId = chatStore.currentSessionId || null
  streaming.value = true
  toolSteps.value = []
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
        toolSteps.value = []
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        router.push('/login')
        message.error('登录已过期，请重新登录')
        return null
      }
      if (!response.ok) {
        streaming.value = false
        toolSteps.value = []
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
            toolSteps.value = []
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
                streaming.value = false
                toolSteps.value = []
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
                toolSteps.value.push(event.name)
                scrollToBottom()
                continue
              }

              if (event.type === 'error') {
                streaming.value = false
                toolSteps.value = []
                const errMsg = event.content || '服务异常，请稍后重试'
                chatStore.addMessage({ role: 'assistant', content: errMsg })
                message.error(errMsg)
                continue
              }

              if (event.type === 'text') {
                if (!msgAdded) {
                  chatStore.addMessage({ role: 'assistant', content: '' })
                  toolSteps.value = []
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
          streaming.value = false
          toolSteps.value = []
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
      toolSteps.value = []
      message.error('网络连接失败，请检查网络后重试')
    })
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

onMounted(async () => {
  await profileStore.fetchProfile()
  await loadSessions()
})
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* ===== 侧边栏（豆包风格） ===== */
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

/* 底部用户区 */
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

/* 消息样式（无头像） */
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

.typing {
  padding: 12px 0;
}

.typing-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-secondary);
  display: inline-block;
  animation: bounce 1.4s ease-in-out infinite;
}

.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}

.tool-steps {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.8;
}

.tool-step {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tool-icon {
  font-size: 12px;
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.input-area {
  display: flex;
  padding: 16px 48px 20px;
  background: var(--bg-page);
  align-items: flex-end;
}
</style>