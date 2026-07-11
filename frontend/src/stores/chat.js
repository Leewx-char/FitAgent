import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)
  const messages = ref([])

  function setMessages(msgs) {
    messages.value = msgs
  }

  function addMessage(msg) {
    messages.value.push(msg)
  }

  function updateLastAssistantMessage(content) {
    const lastIdx = messages.value.length - 1
    if (lastIdx >= 0 && messages.value[lastIdx].role === 'assistant') {
      messages.value[lastIdx].content += content
    } else {
      messages.value.push({ role: 'assistant', content })
    }
  }

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
