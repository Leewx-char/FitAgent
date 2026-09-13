<template>
  <div class="chat-main">
    <div class="conversation-shell">
      <div class="messages-container" ref="messagesRef">
    <div v-if="messages.length === 0 && !uploadResult" class="empty-chat">
      <h2>今天想先解决哪件事？</h2>
      <p class="empty-sub">我可以帮你梳理训练计划、分析数据或讨论动作与恢复。</p>
    </div>

    <div
      v-for="(msg, index) in messages"
      :key="index"
      class="message-row"
      :class="[msg.role, { 'has-tool-chain': msg.role === 'assistant' && msg.toolChain?.length }]"
    >
      <div v-if="msg.role === 'assistant' && msg.toolChain?.length" class="thinking-wrapper">
        <div class="thinking-panel">
          <div
            v-for="(tool, toolIndex) in msg.toolChain"
            :key="toolIndex"
            class="tool-line"
            :class="{ active: tool.status === 'active' }"
          >
            <span class="tool-dot">
              <span v-if="tool.status === 'done'" class="tool-check">✓</span>
              <span v-else class="tool-spinner"></span>
            </span>
            <span class="tool-name">{{ tool.name }}</span>
            <span v-if="tool.count > 1" class="tool-count">×{{ tool.count }}</span>
          </div>
        </div>
      </div>
      <div v-if="msg.role !== 'assistant' || msg.content" class="message-bubble" :class="msg.role">
        <div v-html="renderMarkdown(msg.content)" />
        <section v-if="msg.role === 'assistant' && msg.evidence?.length" class="evidence-panel">
          <button class="evidence-toggle" type="button" @click="toggleEvidence(index)">
            <span>证据来源（{{ msg.evidence.length }}）</span>
            <span>{{ expandedEvidence[index] ? '收起' : '展开' }}</span>
          </button>
          <div v-if="expandedEvidence[index]" class="evidence-list">
            <article v-for="item in msg.evidence" :key="item.evidence_id" class="evidence-card">
              <div class="evidence-card-header">
                <strong>[证据:{{ item.rank }}]</strong>
                <span>{{ item.source_id }}</span>
              </div>
              <p>{{ item.snippet }}</p>
              <div class="evidence-card-meta">
                <span>{{ item.evidence_id }}</span>
              </div>
            </article>
          </div>
        </section>
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
          <span v-if="tool.count > 1" class="tool-count">×{{ tool.count }}</span>
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

    </div>

  <div v-if="uploadResult" class="confirm-overlay">
    <div class="confirm-panel">
      <div class="confirm-header">
        <span class="confirm-title">健康数据提取结果</span>
        <span class="confirm-close" @click="uploadResult = null">✕</span>
      </div>

      <div v-if="uploadResult.data" class="confirm-body">
        <p class="health-disclaimer">识别结果仅供健康信息整理，不构成医疗诊断；请核对后再保存。</p>
        <div class="field-grid">
          <div
            v-for="fieldKey in displayFields"
            :key="fieldKey"
            class="field-item"
            v-if="hasEditableValue(fieldKey)"
          >
            <span class="field-label">{{ fieldLabels[fieldKey] }}</span>
            <n-input v-model:value="editableHealthData[fieldKey].value" size="small" />
            <small v-if="editableHealthData[fieldKey].unit">{{ editableHealthData[fieldKey].unit }}</small>
          </div>
        </div>
        <div v-if="hasUnresolvedConflicts" class="conflict-panel">
          <div class="conflict-title">以下指标在不同页面存在冲突，请选择要保存的值</div>
          <div v-for="(candidates, fieldKey) in uploadResult.data.conflicts" :key="fieldKey" class="conflict-row">
            <span>{{ fieldLabels[fieldKey] || fieldKey }}</span>
            <n-button
              v-for="candidate in candidates"
              :key="`${fieldKey}-${candidate.page}`"
              size="tiny"
              @click="selectConflict(fieldKey, candidate.metric)"
            >
              第{{ candidate.page }}页：{{ candidate.metric.value }} {{ candidate.metric.unit || '' }}
            </n-button>
          </div>
        </div>
        <div v-if="uploadResult.messages?.length" class="health-warnings">
          {{ uploadResult.messages.join('；') }}
        </div>
      </div>

      <div v-else class="confirm-body confirm-error">
        <div class="error-icon">⚠️</div>
        <div class="error-text">{{ uploadResult.messages?.join('；') || '文档解析失败，请重试' }}</div>
      </div>

      <div class="confirm-actions" v-if="uploadResult.data">
        <n-button @click="uploadResult = null">取消</n-button>
        <n-button type="primary" :disabled="hasUnresolvedConflicts" @click="confirmHealthData">确认保存到画像</n-button>
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
import { compactToolChain, useChatStore } from '@/stores/chat'
import { getErrorMessage } from '@/api'
import { updateProfile, uploadHealthDoc } from '@/api/profile'

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
const editableHealthData = ref({})
const thinking = ref(false)
const toolChain = ref([])
const expandedEvidence = ref({})

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
const hasUnresolvedConflicts = computed(() =>
  Object.keys(uploadResult.value?.data?.conflicts || {}).length > 0,
)

/** 判断提取出的指定健康指标是否有可保存的数值。 */
function hasEditableValue(key) {
  const value = editableHealthData.value?.[key]?.value
  return value !== null && value !== undefined && value !== ''
}

/** 深拷贝文档提取结果，避免在冲突选择中直接改动原始响应。 */
function prepareEditableHealthData(data) {
  editableHealthData.value = JSON.parse(JSON.stringify(data || {}))
}

/** 采用用户选择的冲突指标，并将该指标从待解决冲突中移除。 */
function selectConflict(key, metric) {
  editableHealthData.value[key] = JSON.parse(JSON.stringify(metric))
  delete uploadResult.value.data.conflicts[key]
}

/** 将助手 Markdown 渲染为已净化的 HTML，供消息内容安全展示。 */
function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}

/** 切换指定消息的证据卡片展开状态。 */
function toggleEvidence(index) {
  expandedEvidence.value[index] = !expandedEvidence.value[index]
}

/** 等待消息 DOM 更新后，将消息容器滚动到末尾。 */
async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

/** 复制当前工具状态，避免后续流式更新改写已绑定消息的调用记录。 */
function snapshotToolChain() {
  return compactToolChain(toolChain.value)
}

/** 将所有仍在执行的工具标为完成，并同步到已绑定的助手消息。 */
function completePendingTools() {
  const hasActiveTool = toolChain.value.some((tool) => tool.status === 'active')
  if (!hasActiveTool) return
  toolChain.value.forEach((tool) => {
    if (tool.status === 'active') tool.status = 'done'
  })
  chatStore.setLastAssistantToolChain(snapshotToolChain())
}

/** 根据工具调用标识更新完成状态，支持并行工具分别结束。 */
function completeTool(toolId) {
  const tool = toolChain.value.find((item) => item.id === toolId)
  if (!tool || tool.status === 'done') return
  tool.status = 'done'
  chatStore.setLastAssistantToolChain(snapshotToolChain())
}

/** 合并不同工具返回的证据，避免多轮工具调用覆盖已展示的来源。 */
function mergeEvidenceCards(currentCards, incomingCards) {
  const knownIds = new Set(currentCards.map((item) => item?.evidence_id).filter(Boolean))
  return [
    ...currentCards,
    ...incomingCards.filter((item) => !item?.evidence_id || !knownIds.has(item.evidence_id)),
  ]
}

/** 处理发送按钮和回车事件，忽略换行、空内容及忙碌状态。 */
function handleSend(e) {
  if (e?.shiftKey) return
  if (!inputText.value.trim() || streaming.value || uploading.value) return
  e?.preventDefault()
  sendMessage(inputText.value.trim())
  inputText.value = ''
}

/** 追加用户消息并消费聊天接口的 SSE 流，持续更新助手回复和工具状态。 */
function sendMessage(text) {
  chatStore.addMessage({ role: 'user', content: text })
  chatStore.currentSessionId = chatStore.currentSessionId || null
  streaming.value = true
  thinking.value = true
  toolChain.value = []
  scrollToBottom()

  const token = authStore.token
  const sessionId = chatStore.currentSessionId || ''
  let assistantMsg = ''
  let evidenceCards = []
  let msgAdded = false

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
    .then(async (response) => {
      if (response.status === 401) {
        streaming.value = false
        authStore.logout()
        router.push('/login')
        message.error('登录已过期，请重新登录')
        return null
      }
      if (!response.ok) {
        streaming.value = false
        let errorMessage = `请求失败 (${response.status})`
        try {
          const payload = await response.json()
          if (payload.messages?.length) errorMessage = payload.messages.join('；')
        } catch {
          // 非 JSON 响应保留 HTTP 状态提示。
        }
        message.error(errorMessage)
        return null
      }
      const sid = response.headers.get('X-Session-Id')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let sseBuffer = ''

      /** 递归读取 SSE 数据块，按事件类型同步聊天、证据和工具链状态。 */
      function read() {
        return reader.read().then(({ done, value }) => {
          if (done) {
            completePendingTools()
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
                completePendingTools()
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
                thinking.value = false
                toolChain.value.push({ id: event.id || '', name: event.name || '', status: 'active' })
                if (!msgAdded) {
                  chatStore.addMessage({
                    role: 'assistant',
                    content: '',
                    evidence: evidenceCards,
                    toolChain: snapshotToolChain(),
                  })
                  msgAdded = true
                } else {
                  chatStore.setLastAssistantToolChain(snapshotToolChain())
                }
                scrollToBottom()
                continue
              }

              if (event.type === 'tool_completed') {
                completeTool(event.id || '')
                continue
              }

              if (event.type === 'text_reset') {
                const preservedToolChain = msgAdded && chatStore.resetLastAssistantMessageForToolCall()
                assistantMsg = ''
                if (!preservedToolChain) evidenceCards = []
                msgAdded = Boolean(preservedToolChain)
                thinking.value = !preservedToolChain
                if (!preservedToolChain) toolChain.value = []
                scrollToBottom()
                continue
              }

              if (event.type === 'evidence') {
                const incomingCards = Array.isArray(event.items) ? event.items : []
                evidenceCards = mergeEvidenceCards(evidenceCards, incomingCards)
                if (msgAdded) {
                  const lastMessage = chatStore.messages.at(-1)
                  if (lastMessage?.role === 'assistant') lastMessage.evidence = evidenceCards
                }
                continue
              }

              if (event.type === 'error') {
                completePendingTools()
                thinking.value = false
                toolChain.value = []
                streaming.value = false
                const errMsg = event.content || '服务异常，请稍后重试'
                if (msgAdded) {
                  chatStore.updateLastAssistantMessage(errMsg)
                } else {
                  chatStore.addMessage({ role: 'assistant', content: errMsg })
                }
                message.error(errMsg)
                continue
              }

              if (event.type === 'text') {
                completePendingTools()
                thinking.value = false
                assistantMsg += event.content || ''
                if (!msgAdded) {
                  chatStore.addMessage({
                    role: 'assistant',
                    content: assistantMsg,
                    evidence: evidenceCards,
                    toolChain: snapshotToolChain(),
                  })
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
      completePendingTools()
      streaming.value = false
      thinking.value = false
      toolChain.value = []
      const errMsg = '网络连接失败，请检查网络后重试'
      if (msgAdded) {
        chatStore.updateLastAssistantMessage(errMsg)
      } else {
        chatStore.addMessage({ role: 'assistant', content: errMsg })
      }
    })
}

/** 经用户确认后打开健康文档文件选择框。 */
function triggerUpload() {
  const acknowledged = window.confirm(
    '提醒：文件会发送至 DashScope 模型提取指标，处理结束后会清理临时文件。是否继续选择文件？',
  )
  if (acknowledged) {
    fileInputRef.value?.click()
  }
}

/** 上传选中文档，保存提取结果并初始化可编辑健康指标。 */
async function handleFileSelect(e) {
  const file = e.target.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const res = await uploadHealthDoc(file)
    uploadResult.value = res.data
    if (res.data.data) {
      prepareEditableHealthData(res.data.data.metrics)
    }
  } catch (err) {
    uploadResult.value = {
      messages: [getErrorMessage(err, '上传失败，请检查网络连接')],
      data: null,
    }
  } finally {
    uploading.value = false
    e.target.value = ''
  }
}

/** 在所有冲突已选择后，将可编辑健康指标保存到用户档案。 */
async function confirmHealthData() {
  if (hasUnresolvedConflicts.value) {
    message.warning('请先选择冲突指标的保存值')
    return
  }
  try {
    await updateProfile({ health_data: editableHealthData.value })
    uploadResult.value = null
    editableHealthData.value = {}
    message.success('健康数据已保存到档案')
  } catch (error) {
    message.error(getErrorMessage(error, '保存失败，请重试'))
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

.message-row {
  display: flex;
  margin-bottom: 16px;
}

.message-row.user {
  justify-content: flex-end;
}

.message-row.has-tool-chain {
  flex-direction: column;
  align-items: flex-start;
}

.message-row.has-tool-chain .thinking-wrapper {
  margin-bottom: 8px;
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

.evidence-panel {
  margin-top: 12px;
  border-top: 1px solid #e2e8f0;
  padding-top: 10px;
}

.evidence-toggle {
  width: 100%;
  display: flex;
  justify-content: space-between;
  border: 0;
  background: transparent;
  color: var(--primary);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 0;
}

.evidence-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.evidence-card {
  border: 1px solid #dbeafe;
  border-radius: 8px;
  background: #f8fbff;
  padding: 10px;
}

.evidence-card-header,
.evidence-card-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.evidence-card-header span,
.evidence-card-meta {
  color: var(--text-secondary);
}

.evidence-card p {
  margin: 6px 0;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.6;
}

.evidence-card-meta {
  flex-wrap: wrap;
  word-break: break-all;
}

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

.tool-count {
  color: var(--text-quiet);
  font-variant-numeric: tabular-nums;
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

.health-disclaimer {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
  margin: 0 0 12px;
}

.health-warnings {
  color: #b26a00;
  font-size: 13px;
  line-height: 1.6;
  margin-top: 12px;
}

.conflict-panel {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid #ffdca8;
  border-radius: 8px;
  background: #fffaf0;
}

.conflict-title {
  margin-bottom: 8px;
  color: #7a4a00;
  font-size: 13px;
  font-weight: 600;
}

.conflict-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
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

/* 将对话、上下文和输入区收束为安静的三段式工作台。 */
.chat-main {
  min-height: 100vh;
  height: auto;
  background: var(--bg-page);
}

.conversation-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  flex: 1;
  min-height: 0;
}

.messages-container {
  padding: 32px max(28px, 6vw);
  background: var(--bg-page);
}

.empty-chat {
  align-items: flex-start;
  justify-content: center;
  max-width: 620px;
  margin: 0 auto;
  padding: 70px 0;
  text-align: left;
}

.empty-chat h2 {
  margin: 8px 0 10px;
  font-size: 24px;
  letter-spacing: -.03em;
}

.empty-sub { margin: 0 0 24px; max-width: 460px; line-height: 1.7; }
.message-row { margin-bottom: 18px; }
.message-bubble { max-width: min(760px, 78%); border: 1px solid transparent; }
.message-bubble.user { border-radius: 10px; background: var(--primary-light); }
.message-bubble.assistant { border-color: var(--border); border-radius: 10px; box-shadow: none; }
.evidence-panel { border-color: var(--border); }
.evidence-card { border-color: var(--border); background: #F7F9FA; }

.thinking-panel { border: 1px solid var(--border); border-radius: 10px; box-shadow: none; }
.thinking-pulse { display: none; }
.tool-line:not(:last-child)::after { background: var(--border); }

.input-area { padding: 14px max(28px, 6vw) 18px; border-color: var(--border); }
.input-bottom-row { max-width: 900px; }
.input-area :deep(.n-input) { background: #FAFBFC; }
.confirm-panel { border: 1px solid var(--border); border-radius: 10px; }
.confirm-header, .confirm-actions { border-color: var(--border); }

@media (max-width: 960px) {
  .messages-container { padding-left: 28px; padding-right: 28px; }
}

@media (max-width: 700px) {
  .chat-main { min-height: calc(100vh - 54px); }
  .messages-container { padding: 22px 16px; }
  .empty-chat { padding: 48px 0; }
  .message-bubble { max-width: 90%; }
  .input-area { padding-left: 16px; padding-right: 16px; }
}
</style>
