<template>
  <div class="chat-main">
    <div class="messages-container" ref="messagesRef">
    <div v-if="messages.length === 0 && !uploadResult" class="empty-chat">
      <div class="empty-brand">
        <div class="empty-logo">F</div>
        <div class="empty-ring"></div>
      </div>
      <h2>你好，{{ authStore.user?.username || '运动达人' }}</h2>
      <p class="empty-sub">我可以帮你制定训练计划、分析数据、解答健身疑问</p>
      <div class="quick-actions">
        <button
          v-for="action in quickActions"
          :key="action"
          class="quick-btn"
          @click="sendQuick(action)"
        >
          {{ action }}
        </button>
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

    <div v-if="thinking" class="thinking-wrapper">
      <div class="thinking-panel">
        <div class="thinking-pulse"></div>
        <div
          v-for="(tool, idx) in toolChain"
          :key="idx"
          class="tool-line"
          :class="{ active: tool.status === 'active' }"
        >
          <span class="tool-dot">
            <span v-if="tool.status === 'done'" class="tool-check">✓</span>
            <span v-else class="tool-spinner"></span>
          </span>
          <span class="tool-name">{{ tool.name }}</span>
        </div>
        <div v-if="toolChain.length === 0" class="tool-line active">
          <span class="tool-dot">
            <span class="tool-spinner"></span>
          </span>
          <span class="tool-name">思考中</span>
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
        <div class="doc-type-badge">{{ uploadResult.doc_type === 'pdf' ? 'PDF' : '图片' }}</div>
        <div class="field-grid">
          <div
            v-for="fieldKey in displayFields"
            :key="fieldKey"
            class="field-item"
            v-show="getFieldValue(fieldKey)"
          >
            <span class="field-label">{{ fieldLabels[fieldKey] }}</span>
            <span class="field-value">
              {{ getFieldValue(fieldKey) }}
              <small v-if="getFieldUnit(fieldKey)">{{ getFieldUnit(fieldKey) }}</small>
            </span>
          </div>
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
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { uploadHealthDoc, updateProfile } from '@/api/profile'

marked.setOptions({ breaks: true, gfm: true })

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const chatStore = useChatStore()

const messagesRef = ref(null)
const inputText = ref('')
const streaming = ref(false)
const uploading = ref(false)
const uploadResult = ref(null)
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
  return DOMPurify.sanitize(marked.parse(text))
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
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

  const token = authStore.token
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
        authStore.logout()
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
                assistantMsg += event.content || ''
                if (!msgAdded) {
                  chatStore.addMessage({ role: 'assistant', content: assistantMsg })
                  msgAdded = true
                } else {
                  chatStore.updateLastAssistantMessage(event.content || '')
                }
                scrollToBottom()
              }
            }
          }
          return read()
        })
      }

      return read()
    })
    .catch((err) => {
      streaming.value = false
      thinking.value = false
      chatStore.addMessage({ role: 'assistant', content: '网络连接失败，请检查网络后重试' })
    })
}

function triggerUpload() {
  fileInputRef.value?.click()
}

async function handleFileSelect(e) {
  const file = e.target.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    const res = await uploadHealthDoc(formData)
    uploadResult.value = res.data
  } catch (err) {
    uploadResult.value = { status: 'error', message: '上传失败，请检查网络连接' }
  } finally {
    uploading.value = false
    e.target.value = ''
  }
}

async function confirmHealthData() {
  try {
    await updateProfile({ health_data: uploadResult.value.data })
    uploadResult.value = null
    message.success('健康数据已保存到档案')
  } catch (err) {
    message.error('保存失败，请重试')
  }
}

onMounted(async () => {
  if (chatStore.messages.length > 0) {
    await nextTick()
    scrollToBottom()
  }
})

watch(toolChain, () => {
  if (thinking.value) scrollToBottom()
}, { deep: true })
</script>

<style scoped>
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
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
  padding: 60px 24px;
  text-align: center;
}

.empty-brand {
  position: relative;
  width: 80px;
  height: 80px;
  margin-bottom: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-logo {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: linear-gradient(135deg, var(--primary), #1E88E5);
  color: #fff;
  font-size: 28px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2;
}

.empty-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 2px solid var(--primary-light);
  animation: empty-pulse 3s ease-out infinite;
  transform-origin: center;
}

@keyframes empty-pulse {
  0%, 100% { transform: scale(1); opacity: 0.3; }
  50% { transform: scale(1.15); opacity: 0.8; }
}

.empty-chat h2 {
  color: var(--text-primary);
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 8px;
}

.empty-sub {
  color: var(--text-secondary);
  font-size: 15px;
  margin: 0 0 28px;
  max-width: 360px;
}

.quick-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: center;
}

.quick-btn {
  padding: 8px 18px;
  border-radius: 20px;
  border: 1px solid var(--primary-light);
  background: var(--bg-card);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
  font-family: inherit;
}

.quick-btn:hover {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(66, 165, 245, 0.25);
}

.quick-btn:active {
  transform: translateY(0);
}

.message-row {
  display: flex;
  margin-bottom: 16px;
}

.message-row.user {
  justify-content: flex-end;
}

.message-bubble {
  max-width: 70%;
  padding: 10px 16px;
  line-height: 1.7;
  word-break: break-word;
}

.message-bubble.user {
  border-radius: 16px;
  background: var(--primary-light);
  color: var(--text-primary);
}

.message-bubble.assistant {
  border-radius: 16px;
  background: var(--bg-card);
  color: var(--text-primary);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.message-bubble :deep(h2) { font-size: 16px; font-weight: 600; margin: 8px 0; }
.message-bubble :deep(h3) { font-size: 15px; font-weight: 600; margin: 6px 0; }
.message-bubble :deep(h4) { font-size: 14px; font-weight: 600; margin: 6px 0; }
.message-bubble :deep(strong) { font-weight: 600; color: var(--text-primary); }
.message-bubble :deep(em) { font-weight: 400; color: var(--text-secondary); }
.message-bubble :deep(code) { padding: 2px 6px; border-radius: 4px; background: rgba(0,0,0,0.06); font-size: 13px; }
.message-bubble :deep(pre) { padding: 12px; border-radius: 8px; background: #1a1a2e; color: #c6e4fc; overflow-x: auto; }
.message-bubble :deep(pre code) { background: none; padding: 0; }
.message-bubble :deep(ul) { padding-left: 20px; }
.message-bubble :deep(li) { margin: 4px 0; }

.thinking-wrapper {
  display: flex;
  margin-bottom: 16px;
}

.thinking-panel {
  position: relative;
  border-radius: 12px;
  background: var(--bg-card);
  padding: 14px 18px;
  font-size: 13px;
  overflow: hidden;
}

.thinking-pulse {
  position: absolute;
  inset: 0;
  border-radius: 12px;
  background: transparent;
  box-shadow: inset 0 0 0 1px rgba(66, 165, 245, 0.12);
  animation: think-glow 2s ease-in-out infinite;
  pointer-events: none;
}

@keyframes think-glow {
  0%, 100% { box-shadow: inset 0 0 0 1px rgba(66, 165, 245, 0.08); }
  50% { box-shadow: inset 0 0 0 1px rgba(66, 165, 245, 0.22); }
}

.tool-line {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 0;
  position: relative;
  z-index: 1;
}

.tool-line:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 9px;
  bottom: -5px;
  top: 22px;
  width: 1px;
  background: rgba(66, 165, 245, 0.12);
}

.tool-dot {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.tool-check {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(102, 187, 106, 0.15);
  color: var(--success);
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.tool-spinner {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--primary-light);
  border-top-color: var(--primary);
  animation: tool-spin 0.8s linear infinite;
}

@keyframes tool-spin {
  to { transform: rotate(360deg); }
}

.tool-name {
  font-size: 13px;
  color: var(--text-secondary);
}

.tool-line.active .tool-name {
  color: var(--primary);
  font-weight: 600;
}

.confirm-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0,0,0,0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: 32px 20px;
}

.confirm-panel {
  background: var(--bg-card);
  border-radius: 12px;
  width: 480px;
  max-height: 90vh;
  overflow-y: auto;
}

.confirm-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e2e8f0;
}

.confirm-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.confirm-close {
  font-size: 18px;
  cursor: pointer;
  color: var(--text-secondary);
  padding: 4px;
}

.confirm-body {
  padding: 16px 20px;
}

.doc-type-badge {
  display: inline-block;
  border-radius: 8px;
  padding: 4px 12px;
  background: var(--primary-light);
  color: var(--primary);
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 16px;
}

.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.field-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.field-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.field-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.field-value small {
  font-weight: 400;
  font-size: 12px;
  color: var(--text-secondary);
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
  margin-bottom: 8px;
}

.error-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 20px;
  border-top: 1px solid #e2e8f0;
}

.input-area {
  border-top: 1px solid #e2e8f0;
  padding: 12px 20px 16px;
  background: var(--bg-card);
}

.input-top-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.upload-hint {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--primary);
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 4px;
  transition: background 0.15s;
}

.upload-hint:hover {
  background: var(--primary-light);
}

.upload-formats {
  font-size: 12px;
  color: var(--text-secondary);
}

.upload-status {
  font-size: 12px;
  color: var(--primary);
  font-weight: 500;
}

.input-bottom-row {
  display: flex;
  gap: 12px;
}
</style>
