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
expect(dashboard.includes('getFitnessSnapshot(1)'), 'Dashboard 必须请求最近一周数据')
expect(dashboard.includes('COROS 未返回睡眠阶段数据'), 'Dashboard 必须展示睡眠阶段空态')
expect(!chat.includes('class="empty-logo"'), '对话空态不应再渲染 F 图标')
expect(!chat.includes('quickActions'), '对话空态不应保留快捷提示')
expect(chat.includes('今天想先解决哪件事？'), '对话空态必须保留核心提问')
expect(trainingPlan.includes('最近 7 天数据'), '计划页必须说明一周数据范围')
expect(memory.includes('待确认'), '记忆页必须保留确认流程')
expect(profile.includes('训练档案'), '档案页必须使用新的页面标题')

console.log('UI contract checks passed')
