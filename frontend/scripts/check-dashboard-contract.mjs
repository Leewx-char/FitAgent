import { readFileSync } from 'node:fs'
import { activityNameInChinese, latestLoadRecord } from '../src/utils/dashboardPresentation.js'

function source(path) {
  return readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')
}

function expect(condition, message) {
  if (!condition) throw new Error(message)
}

const dashboard = source('src/views/Dashboard.vue')
const layout = source('src/views/AppLayout.vue')

expect(!dashboard.includes(' · 比'), '最新负荷不应把负荷比拼接为不完整文案')
expect(activityNameInChinese('Track Run') === '跑道跑步', '跑道跑必须转换为中文')
expect(activityNameInChinese('Running') === '跑步', '跑步必须转换为中文')
expect(activityNameInChinese('Unlisted Sport') === '运动训练', '未知英文运动必须使用中文兜底')
expect(
  JSON.stringify(latestLoadRecord([
    { date: '2026-09-11', training_load: 11, training_load_ratio: 0.25 },
    { date: '2026-09-10', training_load: 35, training_load_ratio: 0.8 },
  ])) === JSON.stringify({ load: 11, ratio: 0.25 }),
  '最新负荷与负荷比必须来自同一日期',
)
expect(layout.includes('height: 100vh;'), '桌面应用壳必须固定在视口高度')
expect(layout.includes('overflow: hidden;'), '页面滚动必须限制在主内容区')

console.log('Dashboard contract checks passed')
