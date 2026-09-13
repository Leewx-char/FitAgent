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

expect(app.includes('--primary: #3B6E8F'), '缺少低饱和蓝主色令牌')
expect(app.includes('prefers-reduced-motion'), '应用必须支持减少动态效果')
expect(!sidebar.includes('class="brand-icon"'), '侧栏不应再渲染 F 图标')
expect(dashboard.includes('getFitnessSnapshot(1)'), 'Dashboard 必须请求最近一周数据')

console.log('UI contract checks passed')
