<template>
  <section class="dashboard-page">
    <div class="dashboard-content">
      <header class="page-header">
        <div>
          <p class="eyebrow">训练驾驶舱</p>
          <h1>最近 7 天</h1>
          <p class="page-subtitle">{{ rangeLabel }} · 来自已同步的 COROS 数据</p>
        </div>
        <div class="connection-actions">
          <template v-if="connection.connected">
            <span class="connection-state"><i aria-hidden="true"></i>COROS 已连接</span>
            <n-button size="small" :loading="refreshing" @click="loadSnapshot">刷新数据</n-button>
            <n-button size="small" secondary :loading="disconnecting" @click="handleDisconnect">断开</n-button>
          </template>
          <n-button v-else size="small" type="primary" :loading="connecting" @click="handleConnect">
            连接 COROS
          </n-button>
        </div>
      </header>

      <p v-if="partial" class="data-warning" role="status">
        <strong>部分实时数据暂不可用。</strong>{{ unavailableSourceLabel }}暂未返回，已保留其余可用数据。
      </p>

      <template v-if="connection.connected">
        <section v-if="hasSnapshotData" class="action-layout" aria-label="本周训练概览">
          <article class="training-brief">
            <p class="section-kicker">本周训练状态</p>
            <h2>{{ trainingStatusTitle }}</h2>
            <p class="brief-copy">{{ trainingStatusCopy }}</p>
            <dl class="metric-list">
              <div>
                <dt>步数</dt>
                <dd>{{ weeklySteps }}</dd>
              </div>
              <div>
                <dt>消耗</dt>
                <dd>{{ weeklyCalories }}</dd>
              </div>
              <div>
                <dt>运动时长</dt>
                <dd>{{ weeklyExerciseMinutes }}</dd>
              </div>
              <div>
                <dt>最新负荷</dt>
                <dd>{{ latestTrainingLoad }}</dd>
              </div>
            </dl>
          </article>
          <aside class="next-step">
            <p class="section-kicker">下一步</p>
            <h2>查看本周计划</h2>
            <p>训练计划会结合档案、最近一周运动数据和你的训练反馈生成。</p>
            <router-link to="/training-plan" class="primary-link">进入本周计划</router-link>
          </aside>
        </section>

        <section v-if="hasSnapshotData" class="week-section" aria-labelledby="week-title">
          <div class="section-heading">
            <div>
              <p class="section-kicker">日视图</p>
              <h2 id="week-title">本周节奏</h2>
            </div>
            <span>每天的可用指标</span>
          </div>
          <div class="week-grid">
            <article v-for="day in weekDays" :key="day.date" class="day-cell" :class="{ today: day.isToday }">
              <p class="day-label">{{ day.label }}</p>
              <p class="day-date">{{ day.date.slice(5) }}</p>
              <strong>{{ day.steps }}</strong>
              <span>步数</span>
              <p class="day-meta">{{ day.exerciseMinutes }}</p>
              <p class="day-activity">{{ day.activity }}</p>
            </article>
          </div>
        </section>

        <div v-if="hasSnapshotData" class="insight-grid">
          <section class="data-panel" aria-labelledby="load-title">
            <div class="section-heading compact">
              <div>
                <p class="section-kicker">趋势</p>
                <h2 id="load-title">训练负荷与 HRV</h2>
              </div>
            </div>
            <div
              v-if="hasLoadHrvSeries"
              ref="tloadChartRef"
              class="chart"
              role="img"
              aria-label="最近七天训练负荷与 HRV 趋势图"
            ></div>
            <p v-else class="data-placeholder">COROS 未返回可绘制的训练负荷或 HRV 时序数据。</p>
          </section>

          <section class="data-panel" aria-labelledby="sleep-title">
            <div class="section-heading compact">
              <div>
                <p class="section-kicker">恢复</p>
                <h2 id="sleep-title">睡眠阶段</h2>
              </div>
            </div>
            <div
              v-if="hasSleepStageData"
              ref="sleepChartRef"
              class="chart"
              role="img"
              aria-label="最近七天睡眠阶段分布图"
            ></div>
            <p v-else class="data-placeholder">COROS 未返回睡眠阶段数据</p>
          </section>
        </div>

        <section v-if="activities.length" class="activity-section" aria-labelledby="activity-title">
          <div class="section-heading compact">
            <div>
              <p class="section-kicker">记录</p>
              <h2 id="activity-title">近期运动</h2>
            </div>
            <span>{{ activities.length }} 条</span>
          </div>
          <ul class="activity-list">
            <li v-for="act in activities" :key="act.external_id" class="activity-item">
              <time :datetime="act.date">{{ formatDate(act.date) }}</time>
              <div class="act-main">
                <strong>{{ sportName(act.sport_name || act.name) }}</strong>
                <span>{{ activitySummary(act) }}</span>
              </div>
              <span v-if="act.avg_heart_rate" class="act-heart">均心率 {{ act.avg_heart_rate }} 次/分</span>
            </li>
          </ul>
        </section>

        <div v-if="!hasSnapshotData && !loading" class="empty-state">
          <h2>近期没有可用运动数据</h2>
          <p>完成一次同步后，最近 7 天的训练与恢复记录会显示在这里。</p>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <h2>连接 COROS 后查看训练节奏</h2>
        <p>我们只展示用于训练分析的结构化摘要，不会在面板展示位置或原始文本。</p>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useMessage } from 'naive-ui'
import * as echarts from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getErrorMessage } from '@/api'
import {
  connectCoros,
  disconnectCoros,
  getCorosConnection,
  getFitnessSnapshot,
} from '@/api/fitness'

echarts.use([GridComponent, TooltipComponent, LegendComponent, LineChart, BarChart, CanvasRenderer])

const SPORT_MAP = {
  Run: '跑步',
  'Trail Run': '越野跑',
  'Track Running': '跑道跑步',
  'Road Bike': '公路骑行',
  'Indoor Cycling': '室内骑行',
  'Mountain Bike': '山地骑行',
  'Strength Training': '力量训练',
  Swim: '游泳',
  'Open Water Swim': '公开水域游泳',
  Hike: '徒步',
  Walk: '步行',
  Yoga: '瑜伽',
  Treadmill: '跑步机',
  'Sport 1002': '跑步',
}

const SOURCE_LABELS = {
  activities: '活动记录',
  daily: '每日健康',
  sleep: '睡眠',
  training_load: '训练负荷',
  resting_heart_rate: '静息心率',
  sleep_hrv: '睡眠 HRV',
}

const dailyMetrics = ref([])
const sleepRecords = ref([])
const activities = ref([])
const startDate = ref('')
const endDate = ref('')
const partial = ref(false)
const unavailableSources = ref([])
const loading = ref(true)
const refreshing = ref(false)
const connecting = ref(false)
const disconnecting = ref(false)
const connection = ref({ connected: false, status: 'not_connected' })
const message = useMessage()

const tloadChartRef = ref(null)
const sleepChartRef = ref(null)
let tloadChart = null
let sleepChart = null

/** 将设备返回的运动类型转为中文名称，未知类型保留原值。 */
function sportName(name) {
  if (!name) return '--'
  return SPORT_MAP[name] || name
}

/** 仅累计存在的数值，避免把未返回的数据错误当作零。 */
function sumMetric(key) {
  const values = dailyMetrics.value.map(item => item[key]).filter(Number.isFinite)
  return values.length ? values.reduce((total, value) => total + value, 0) : null
}

/** 返回最近一个有效指标，保证面板不凭空推断身体状态。 */
function latestMetric(key) {
  const sorted = [...dailyMetrics.value].sort((a, b) => b.date.localeCompare(a.date))
  return sorted.find(item => Number.isFinite(item[key]))?.[key] ?? null
}

/** 将紧凑日期转为面向用户的月日格式。 */
function formatDate(dateStr) {
  return dateStr?.slice(5) || '--'
}

/** 将分钟数转为紧凑时长，缺失值保留为不可用。 */
function formatMinutes(minutes) {
  if (!Number.isFinite(minutes)) return '--'
  const hours = Math.floor(minutes / 60)
  const remainder = Math.round(minutes % 60)
  return hours ? `${hours}小时${remainder}分` : `${remainder}分`
}

/** 将秒数转为中文时长，供活动摘要复用。 */
function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return ''
  return formatMinutes(seconds / 60)
}

/** 合并活动的可用摘要字段，不泄露位置等敏感信息。 */
function activitySummary(activity) {
  const parts = []
  if (activity.duration_seconds) parts.push(formatDuration(activity.duration_seconds))
  if (activity.distance_meters) parts.push(`${(activity.distance_meters / 1000).toFixed(1)} 公里`)
  if (activity.calories) parts.push(`${Math.round(activity.calories)} 千卡`)
  return parts.length ? parts.join(' · ') : '未返回更多指标'
}

/** 根据服务端范围逐日构建七天视图，缺失记录照常保留日期。 */
function buildWeekDays() {
  if (!startDate.value || !endDate.value) return []
  const records = new Map(dailyMetrics.value.map(item => [item.date, item]))
  const activityByDate = new Map()
  activities.value.forEach((activity) => {
    if (!activityByDate.has(activity.date)) activityByDate.set(activity.date, activity)
  })
  const result = []
  const cursor = new Date(`${startDate.value}T00:00:00`)
  const last = new Date(`${endDate.value}T00:00:00`)
  const today = new Date().toISOString().slice(0, 10)
  while (cursor <= last) {
    const date = cursor.toISOString().slice(0, 10)
    const record = records.get(date) || {}
    const activity = activityByDate.get(date)
    result.push({
      date,
      label: ['日', '一', '二', '三', '四', '五', '六'][cursor.getDay()],
      isToday: date === today,
      steps: Number.isFinite(record.steps) ? record.steps.toLocaleString() : '--',
      exerciseMinutes: Number.isFinite(record.exercise_minutes) ? formatMinutes(record.exercise_minutes) : '运动时长未返回',
      activity: activity ? sportName(activity.sport_name || activity.name) : '暂无活动',
    })
    cursor.setDate(cursor.getDate() + 1)
  }
  return result
}

const weekDays = computed(buildWeekDays)
const hasSnapshotData = computed(() => dailyMetrics.value.length > 0 || sleepRecords.value.length > 0 || activities.value.length > 0)
const weeklySteps = computed(() => {
  const value = sumMetric('steps')
  return value === null ? '--' : value.toLocaleString()
})
const weeklyCalories = computed(() => {
  const value = sumMetric('calories')
  return value === null ? '--' : `${Math.round(value).toLocaleString()} 千卡`
})
const weeklyExerciseMinutes = computed(() => formatMinutes(sumMetric('exercise_minutes')))
const latestTrainingLoad = computed(() => {
  const value = latestMetric('training_load')
  const ratio = latestMetric('training_load_ratio')
  if (value === null) return '--'
  return ratio === null ? `${Math.round(value)}` : `${Math.round(value)} · 比 ${ratio.toFixed(2)}`
})
const hasLoadHrvSeries = computed(() => dailyMetrics.value.some(item => Number.isFinite(item.training_load) || Number.isFinite(item.avg_sleep_hrv)))
const hasSleepStageData = computed(() => sleepRecords.value.some((record) => {
  const phases = record.phases || {}
  return ['awake_minutes', 'rem_minutes', 'light_minutes', 'deep_minutes'].some(key => Number.isFinite(phases[key]))
}))
const rangeLabel = computed(() => startDate.value && endDate.value ? `${formatDate(startDate.value)} 至 ${formatDate(endDate.value)}` : '等待同步')
const unavailableSourceLabel = computed(() => unavailableSources.value.map(source => SOURCE_LABELS[source] || source).join('、'))
const trainingStatusTitle = computed(() => activities.value.length ? '近期训练记录已同步' : '等待本周训练记录')
const trainingStatusCopy = computed(() => activities.value.length
  ? `已记录 ${activities.value.length} 次活动。训练建议会继续结合恢复与反馈。`
  : '基于最近 7 天的可用数据整理；新增活动后可再次刷新。')

/** 用当前每日指标重建训练负荷与 HRV 的双轴趋势图。 */
function buildTloadHrvChart() {
  if (!tloadChartRef.value || !hasLoadHrvSeries.value) return
  const sorted = [...dailyMetrics.value].sort((a, b) => a.date.localeCompare(b.date))
  tloadChart?.dispose()
  tloadChart = echarts.init(tloadChartRef.value)
  tloadChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['训练负荷', 'HRV'], bottom: 0, textStyle: { color: '#5C6874' } },
    grid: { left: 52, right: 52, top: 24, bottom: 42 },
    xAxis: { type: 'category', data: sorted.map(item => item.date.slice(5)), boundaryGap: false, axisLine: { lineStyle: { color: '#DCE2E7' } }, axisLabel: { color: '#5C6874' } },
    yAxis: [
      { type: 'value', name: '负荷', nameTextStyle: { color: '#5C6874' }, axisLabel: { color: '#5C6874' }, splitLine: { lineStyle: { color: '#EEF1F3' } } },
      { type: 'value', name: 'ms', nameTextStyle: { color: '#5C6874' }, axisLabel: { color: '#5C6874' }, splitLine: { show: false } },
    ],
    series: [
      { name: '训练负荷', type: 'line', data: sorted.map(item => item.training_load ?? null), smooth: false, connectNulls: false, lineStyle: { color: '#3B6E8F', width: 2 }, itemStyle: { color: '#3B6E8F' } },
      { name: 'HRV', type: 'line', yAxisIndex: 1, data: sorted.map(item => item.avg_sleep_hrv ?? null), smooth: false, connectNulls: false, lineStyle: { color: '#7F9AA8', width: 2 }, itemStyle: { color: '#7F9AA8' } },
    ],
  })
}

/** 用可用的睡眠阶段数据重建堆叠柱状图。 */
function buildSleepChart() {
  if (!sleepChartRef.value || !hasSleepStageData.value) return
  const sorted = [...sleepRecords.value].sort((a, b) => a.date.localeCompare(b.date))
  sleepChart?.dispose()
  sleepChart = echarts.init(sleepChartRef.value)
  sleepChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['清醒', 'REM', '浅睡', '深睡'], bottom: 0, textStyle: { color: '#5C6874' } },
    grid: { left: 48, right: 18, top: 24, bottom: 42 },
    xAxis: { type: 'category', data: sorted.map(item => item.date.slice(5)), axisLine: { lineStyle: { color: '#DCE2E7' } }, axisLabel: { color: '#5C6874' } },
    yAxis: { type: 'value', name: '分钟', nameTextStyle: { color: '#5C6874' }, axisLabel: { color: '#5C6874' }, splitLine: { lineStyle: { color: '#EEF1F3' } } },
    series: [
      { name: '清醒', type: 'bar', stack: 'total', data: sorted.map(item => item.phases?.awake_minutes ?? 0), color: '#D7E0E5' },
      { name: 'REM', type: 'bar', stack: 'total', data: sorted.map(item => item.phases?.rem_minutes ?? 0), color: '#B8CDD9' },
      { name: '浅睡', type: 'bar', stack: 'total', data: sorted.map(item => item.phases?.light_minutes ?? 0), color: '#7F9AA8' },
      { name: '深睡', type: 'bar', stack: 'total', data: sorted.map(item => item.phases?.deep_minutes ?? 0), color: '#3B6E8F' },
    ],
  })
}

/** 在窗口尺寸变化时同步调整已渲染的图表。 */
function handleResize() {
  tloadChart?.resize()
  sleepChart?.resize()
}

/** 释放图表实例，防止条件渲染后保留失效画布。 */
function disposeCharts() {
  tloadChart?.dispose()
  sleepChart?.dispose()
  tloadChart = null
  sleepChart = null
}

/** 读取一次最近七天的实时快照，并在 DOM 更新后绘制图表。 */
async function loadSnapshot() {
  loading.value = true
  refreshing.value = true
  try {
    const response = await getFitnessSnapshot(1)
    const snapshot = response.data.data
    dailyMetrics.value = snapshot.daily_metrics || []
    sleepRecords.value = snapshot.sleep_records || []
    activities.value = snapshot.activities || []
    startDate.value = snapshot.start_date || ''
    endDate.value = snapshot.end_date || ''
    partial.value = Boolean(snapshot.partial)
    unavailableSources.value = snapshot.unavailable_sources || []
    disposeCharts()
    await nextTick()
    buildTloadHrvChart()
    buildSleepChart()
  } catch (error) {
    if (error.response?.status !== 409) {
      console.error('加载实时运动数据失败:', error)
      message.error(getErrorMessage(error, '加载实时运动数据失败，请稍后重试'))
    }
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

/** 创建服务端授权请求，再由浏览器前往 COROS 官方授权页。 */
async function handleConnect() {
  connecting.value = true
  try {
    const response = await connectCoros()
    window.location.assign(response.data.data.authorization_url)
  } catch (error) {
    console.error('发起 COROS 授权失败:', error)
    message.error(getErrorMessage(error, '暂时无法发起 COROS 授权'))
  } finally {
    connecting.value = false
  }
}

/** 清除本地令牌和界面快照；不会假定存在远端撤销接口。 */
async function handleDisconnect() {
  disconnecting.value = true
  try {
    await disconnectCoros()
    connection.value = { connected: false, status: 'not_connected' }
    dailyMetrics.value = []
    sleepRecords.value = []
    activities.value = []
    startDate.value = ''
    endDate.value = ''
    partial.value = false
    unavailableSources.value = []
    disposeCharts()
    message.success('已断开 COROS')
  } catch (error) {
    console.error('断开 COROS 失败:', error)
    message.error(getErrorMessage(error, '断开 COROS 失败，请稍后重试'))
  } finally {
    disconnecting.value = false
  }
}

onMounted(async () => {
  try {
    const response = await getCorosConnection()
    connection.value = response.data.data
    if (connection.value.connected) await loadSnapshot()
    else loading.value = false
  } catch (error) {
    loading.value = false
    console.error('读取 COROS 连接状态失败:', error)
  }
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  disposeCharts()
})
</script>

<style scoped>
.dashboard-page { min-height: 100%; }
.dashboard-content { max-width: 1180px; margin: 0 auto; padding: 40px 44px 56px; }
.page-header, .connection-actions, .section-heading, .activity-item { display: flex; align-items: center; }
.page-header { justify-content: space-between; gap: 24px; margin-bottom: 28px; }
.eyebrow, .section-kicker { color: var(--text-quiet); font-size: 12px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; }
.page-header h1 { margin: 4px 0 6px; color: var(--text-primary); font-size: 28px; letter-spacing: -.035em; }
.page-subtitle { color: var(--text-secondary); font-size: 14px; }
.connection-actions { gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
.connection-state { display: inline-flex; align-items: center; gap: 7px; color: var(--success); font-size: 13px; font-weight: 650; }
.connection-state i { width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.data-warning { margin: 0 0 20px; padding: 12px 14px; border: 1px solid #E9D6A5; border-radius: 8px; background: var(--warning-light); color: #6C4B12; font-size: 14px; line-height: 1.6; }
.data-warning strong { margin-right: 4px; }
.action-layout { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(272px, .8fr); gap: 16px; margin-bottom: 16px; }
.training-brief, .next-step, .week-section, .data-panel, .activity-section { border: 1px solid var(--border); border-radius: 10px; background: var(--bg-card); }
.training-brief { padding: 28px; }
.training-brief h2, .next-step h2, .section-heading h2, .empty-state h2 { margin: 6px 0 8px; color: var(--text-primary); font-size: 20px; letter-spacing: -.02em; }
.brief-copy, .next-step p { max-width: 620px; color: var(--text-secondary); font-size: 14px; line-height: 1.7; }
.metric-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 28px; }
.metric-list div { min-width: 0; padding-left: 12px; border-left: 2px solid var(--primary-light); }
.metric-list dt { color: var(--text-secondary); font-size: 12px; }
.metric-list dd { margin: 5px 0 0; color: var(--text-primary); font-size: 16px; font-weight: 700; overflow-wrap: anywhere; }
.next-step { padding: 28px; background: #F1F5F7; }
.next-step .primary-link { display: inline-flex; margin-top: 24px; color: var(--primary-dark); font-size: 14px; font-weight: 700; }
.next-step .primary-link:hover { text-decoration: underline; }
.week-section, .data-panel, .activity-section { padding: 24px; }
.week-section { margin-bottom: 16px; }
.section-heading { justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading.compact { margin-bottom: 14px; }
.section-heading h2 { font-size: 17px; }
.section-heading > span { color: var(--text-quiet); font-size: 13px; }
.week-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); border-top: 1px solid var(--border); border-left: 1px solid var(--border); }
.day-cell { min-height: 156px; padding: 14px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.day-cell.today { background: #F4F8FA; }
.day-label { color: var(--text-secondary); font-size: 12px; }
.day-date { margin: 3px 0 16px; color: var(--text-quiet); font-size: 12px; }
.day-cell strong { display: block; color: var(--text-primary); font-size: 17px; }
.day-cell > span { color: var(--text-secondary); font-size: 12px; }
.day-meta, .day-activity { margin-top: 10px; color: var(--text-secondary); font-size: 12px; line-height: 1.45; }
.day-activity { color: var(--primary-dark); }
.insight-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.chart { width: 100%; height: 300px; }
.data-placeholder { display: grid; place-items: center; min-height: 300px; padding: 24px; color: var(--text-secondary); font-size: 14px; line-height: 1.6; text-align: center; background: #FAFBFC; border: 1px dashed var(--border); }
.activity-list { list-style: none; margin: 0; padding: 0; }
.activity-item { gap: 16px; padding: 15px 0; border-top: 1px solid var(--border); }
.activity-item time { flex: 0 0 48px; color: var(--text-quiet); font-size: 13px; }
.act-main { display: flex; flex: 1; align-items: baseline; gap: 12px; min-width: 0; }
.act-main strong { color: var(--text-primary); font-size: 14px; }
.act-main span, .act-heart { color: var(--text-secondary); font-size: 13px; }
.act-heart { flex: 0 0 auto; }
.empty-state { padding: 84px 24px; border: 1px dashed var(--border); border-radius: 10px; color: var(--text-secondary); text-align: center; }
.empty-state p { font-size: 14px; line-height: 1.7; }
@media (max-width: 980px) { .dashboard-content { padding: 32px 28px 48px; } .action-layout, .insight-grid { grid-template-columns: 1fr; } .metric-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .dashboard-content { padding: 24px 16px 40px; } .page-header { align-items: flex-start; flex-direction: column; } .connection-actions { justify-content: flex-start; } .week-section, .data-panel, .activity-section, .training-brief, .next-step { padding: 18px; } .week-grid { overflow-x: auto; grid-template-columns: repeat(7, minmax(112px, 1fr)); } .day-cell { min-height: 148px; } .activity-item { align-items: flex-start; } .act-main { flex-direction: column; gap: 4px; } .act-heart { display: none; } }
</style>
