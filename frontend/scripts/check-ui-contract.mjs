import { readFileSync } from 'node:fs'

function source(path) {
  return readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')
}

function expect(condition, message) {
  if (!condition) throw new Error(message)
}

const app = source('src/App.vue')
const sidebar = source('src/components/Sidebar.vue')
const dashboard = source('src/views/Dashboard.vue')
const chat = source('src/views/Chat.vue')
const trainingPlan = source('src/views/TrainingPlan.vue')
const memory = source('src/views/Memory.vue')
const profile = source('src/views/Profile.vue')

expect(app.includes('--primary: #3B6E8F'), '缺少低饱和蓝主色令牌')
expect(app.includes('prefers-reduced-motion'), '应用必须支持减少动态效果')
expect(!sidebar.includes('class="brand-icon"'), '侧栏不应再渲染 F 图标')
expect(sidebar.includes('sidebar-collapse'), '侧栏必须提供收起与展开控制')
expect(sidebar.includes('toggleCollapse'), '侧栏收起控制必须可操作')
expect(sidebar.includes(":aria-label=\"isCollapsed ? '展开侧边栏' : '收起侧边栏'\""), '侧栏收起控制必须具备可访问名称')
expect(dashboard.includes('getFitnessSnapshot(1)'), 'Dashboard 必须请求最近一周数据')
expect(dashboard.includes('COROS 未返回睡眠阶段数据'), 'Dashboard 必须展示睡眠阶段空态')
expect(!chat.includes('class="empty-logo"'), '对话空态不应再渲染 F 图标')
expect(!chat.includes('quickActions'), '对话空态不应保留快捷提示')
expect(chat.includes('今天想先解决哪件事？'), '对话空态必须保留核心提问')
expect(chat.includes("event.type === 'text_reset'"), '对话必须能撤回工具调用前的文本说明')
expect(chat.includes('msg.toolChain?.length'), '工具调用记录必须绑定到对应助手消息')
expect(chat.includes("'has-tool-chain'"), '工具调用记录必须纵向显示在对应答案上方')
expect(chat.includes("event.type === 'tool_completed'"), '并行工具完成时必须更新对应思维链状态')
expect(chat.includes('resetLastAssistantMessageForToolCall'), '多轮工具调用时必须保留既有调用链')
expect(chat.includes('mergeEvidenceCards'), '多轮工具调用时必须保留先前检索证据')
expect(trainingPlan.includes('最近 7 天数据'), '计划页必须说明一周数据范围')
expect(memory.includes('待确认'), '记忆页必须保留确认流程')
expect(profile.includes('训练档案'), '档案页必须使用新的页面标题')

console.log('UI contract checks passed')
