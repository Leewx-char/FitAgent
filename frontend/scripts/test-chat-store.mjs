import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '../src/stores/chat.js'

/** 断言聊天流状态符合工具链与最终回答的展示约定。 */
function expect(condition, message) {
  if (!condition) throw new Error(message)
}

setActivePinia(createPinia())
const chat = useChatStore()

chat.addMessage({
  role: 'assistant',
  content: '我先查询天气。',
  evidence: [{ evidence_id: 'weather#1' }],
  toolChain: [{ name: '查询天气', status: 'done' }],
})

expect(chat.resetLastAssistantMessageForToolCall(), '已有工具链的助手消息必须保留')
expect(chat.messages.length === 1, '撤回中间说明时不得删除既有工具链')
expect(chat.messages[0].content === '', '撤回必须清空中间说明文本')
expect(chat.messages[0].evidence.length === 1, '撤回不得丢弃已有检索证据')
expect(chat.messages[0].toolChain[0].name === '查询天气', '工具链必须继续保留')

chat.setLastAssistantToolChain([
  { name: '查询天气', status: 'done' },
  { name: '获取月份', status: 'active' },
])
chat.updateLastAssistantMessage('建议傍晚慢跑 30 分钟。')

expect(chat.messages[0].toolChain.length === 2, '多轮工具调用必须聚合到同一回答')
expect(chat.messages[0].content === '建议傍晚慢跑 30 分钟。', '最终文本必须填入保留的助手消息')

console.log('Chat stream state checks passed')
