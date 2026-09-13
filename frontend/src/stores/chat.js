import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 将同名工具的展示状态合并，并保留真实执行次数。 */
export function compactToolChain(tools) {
  const groupedTools = new Map()
  for (const tool of tools) {
    const key = tool.name || tool.id
    const current = groupedTools.get(key)
    if (current) {
      current.count += 1
      if (tool.status === 'active') current.status = 'active'
      continue
    }
    groupedTools.set(key, { ...tool, count: 1 })
  }
  return [...groupedTools.values()]
}

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

  /** 用最新工具调用快照更新末条助手消息，供回答上方的思维链展示。 */
  function setLastAssistantToolChain(tools) {
    const lastMessage = messages.value.at(-1)
    if (lastMessage?.role === 'assistant') {
      lastMessage.toolChain = tools.map((tool) => ({ ...tool }))
    }
  }

  /** 撤回工具调用前的临时文本；已有工具链时保留该助手消息和调用记录。 */
  function resetLastAssistantMessageForToolCall() {
    const lastMessage = messages.value.at(-1)
    if (lastMessage?.role !== 'assistant') return false
    if (lastMessage.toolChain?.length) {
      lastMessage.content = ''
      return true
    }
    messages.value.pop()
    return false
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
    setLastAssistantToolChain,
    resetLastAssistantMessageForToolCall,
    clearMessages,
  }
})
