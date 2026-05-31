<template>
  <div class="chat-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2 class="logo">FitAgent</h2>
      </div>

      <div class="sidebar-profile" v-if="profileStore.profile">
        <div class="profile-brief">
          <div class="profile-name">{{ authStore.user?.username || '用户' }}</div>
          <div class="profile-tags">
            <n-tag size="small" type="primary">{{ profileStore.profile.goal }}</n-tag>
            <n-tag size="small" type="info">{{ profileStore.profile.experience }}</n-tag>
          </div>
        </div>
        <n-button text size="tiny" @click="$router.push('/profile')">查看画像</n-button>
      </div>

      <n-divider style="margin: 12px 0" />

      <div class="session-list">
        <div
          v-for="session in chatStore.sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: session.id === chatStore.currentSessionId }"
          @click="switchSession(session.id)"
        >
          <span class="session-title">{{ session.title || '新对话' }}</span>
          <n-button text size="tiny" @click.stop="handleDeleteSession(session.id)">
            ✕
          </n-button>
        </div>
      </div>

      <div class="sidebar-bottom">
        <n-button block @click="newSession">新建对话</n-button>
        <n-button block type="error" ghost style="margin-top: 8px" @click="handleLogout">退出登录</n-button>
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
          <div class="message-avatar">
            {{ msg.role === 'user' ? '👤' : '🤖' }}
          </div>
          <div class="message-bubble" :class="msg.role">
            <div v-html="renderMarkdown(msg.content)" />
          </div>
        </div>

        <div v-if="streaming" class="message-row assistant">
          <div class="message-avatar">🤖</div>
          <div class="message-bubble assistant typing">
            <span class="typing-dot" /><span class="typing-dot" /><span class="typing-dot" />
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
import { ref, computed, onMounted, nextTick, watch } from 'vue'
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

const messages = computed(() => chatStore.messages)

const quickActions = [
  '帮我制定减脂训练计划',
  '膝盖不好怎么练腿？',
  '蛋白质每天吃多少？',
  '新手健身注意事项',
]

function renderMarkdown(text) {
  let html = text || ''
  html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>')
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  html = html.replace(/^\- (.+)$/gm, '<li>$1</li>')
  html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
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
      const sid = response.headers.get('X-Session-Id')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let assistantMsg = ''
      let msgAdded = false

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
          const lines = chunk.split('\n')
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6)
              if (data === '[DONE]') {
                streaming.value = false
                loadSessions()
                return
              }
              if (!msgAdded) {
                chatStore.addMessage({ role: 'assistant', content: '' })
                msgAdded = true
              }
              assistantMsg += data
              chatStore.messages[chatStore.messages.length - 1].content = assistantMsg
              scrollToBottom()
            }
          }
          read()
        })
      }
      read()
    })
    .catch(() => {
      streaming.value = false
      message.error('发送失败')
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

.sidebar {
  width: 260px;
  background: white;
  border-right: 1px solid #eee;
  display: flex;
  flex-direction: column;
  padding: 16px;
}

.sidebar-header {
  margin-bottom: 16px;
}

.logo {
  font-size: 20px;
  font-weight: 700;
  color: var(--primary);
}

.sidebar-profile {
  background: var(--primary-light);
  border-radius: 10px;
  padding: 12px;
}

.profile-brief {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.profile-name {
  font-weight: 600;
  font-size: 14px;
}

.profile-tags {
  display: flex;
  gap: 4px;
}

.session-list {
  flex: 1;
  overflow-y: auto;
}

.session-item {
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
  transition: background 0.2s;
  font-size: 13px;
}

.session-item:hover {
  background: #f0f5ff;
}

.session-item.active {
  background: var(--primary-light);
}

.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.sidebar-bottom {
  margin-top: auto;
  padding-top: 12px;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-page);
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
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
  display: flex;
  margin-bottom: 16px;
  align-items: flex-start;
}

.message-row.user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}

.message-row.user .message-avatar {
  margin-left: 8px;
}

.message-row.assistant .message-avatar {
  margin-right: 8px;
}

.message-bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
}

.message-bubble.user {
  background: var(--primary);
  color: white;
  border-bottom-right-radius: 4px;
}

.message-bubble.assistant {
  background: white;
  color: var(--text-primary);
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.message-bubble.assistant :deep(h3) {
  font-size: 15px;
  margin: 8px 0 4px;
}

.message-bubble.assistant :deep(h4) {
  font-size: 14px;
  margin: 6px 0 4px;
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
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 16px;
}

.typing-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-secondary);
  animation: bounce 1.4s ease-in-out infinite;
}

.typing-dot:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.input-area {
  display: flex;
  padding: 16px 24px;
  background: white;
  border-top: 1px solid #eee;
  align-items: flex-end;
}
</style>