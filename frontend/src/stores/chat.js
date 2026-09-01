import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 提供会话选择和当前消息列表的 Pinia store。 */
export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)
  const messages = ref([])

  /** 用指定会话的历史消息替换当前展示列表。 */
  function setMessages(msgs) {
    messages.value = msgs
  }

  /** 在当前会话消息列表末尾追加一条消息。 */
  function addMessage(msg) {
    messages.value.push(msg)
  }

  /** 将流式文本追加到末条助手消息；不存在时新建该消息。 */
  function updateLastAssistantMessage(content) {
    const lastIdx = messages.value.length - 1
    if (lastIdx >= 0 && messages.value[lastIdx].role === 'assistant') {
      messages.value[lastIdx].content += content
    } else {
      messages.value.push({ role: 'assistant', content })
    }
  }

  /** 清空当前会话在界面中展示的消息。 */
  function clearMessages() {
    messages.value = []
  }

  return {
    sessions,
    currentSessionId,
    messages,
    setMessages,
    addMessage,
    updateLastAssistantMessage,
    clearMessages,
  }
})
