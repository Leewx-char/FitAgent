import { createPinia, setActivePinia } from 'pinia'
import { compactToolChain, useChatStore } from '../src/stores/chat.js'

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

const compactedTools = compactToolChain([
  { id: 'rag-1', name: '检索知识库', status: 'done' },
  { id: 'rag-2', name: '检索知识库', status: 'active' },
  { id: 'weather-1', name: '查询天气', status: 'done' },
])

expect(compactedTools.length === 2, '同名工具调用在前端必须合并为一项')
expect(compactedTools[0].count === 2, '合并项必须保留真实调用次数')
expect(compactedTools[0].status === 'active', '任一同名调用进行中时合并项必须显示执行中')
expect(compactedTools[1].name === '查询天气', '不同工具必须保持独立展示')

const completedTools = compactToolChain([
  { id: 'rag-1', name: '检索知识库', status: 'done' },
  { id: 'rag-2', name: '检索知识库', status: 'done' },
])

expect(completedTools[0].status === 'done', '同名调用全部完成后合并项必须显示已完成')

console.log('Chat stream state checks passed')
