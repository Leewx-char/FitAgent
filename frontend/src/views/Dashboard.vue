<template>
  <div class="dashboard-page">
    <div class="dashboard-content">
      <div class="content-header">
        <h1>运动数据面板</h1>
        <n-button size="small" @click="handleSync" :loading="syncing">同步高驰数据</n-button>
      </div>
      <div class="stat-cards">
        <div class="stat-card">
          <span class="stat-label">平均训练负荷</span>
          <span class="stat-value">{{ avgTrainingLoad }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">平均HRV</span>
          <span class="stat-value">{{ avgHrv }}</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">平均静息心率</span>
          <span class="stat-value">{{ avgRhr }}<small> 次/分</small></span>
        </div>
        <div class="stat-card">
          <span class="stat-label">近期运动</span>
          <span class="stat-value">{{ activities.length }}<small> 次</small></span>
        </div>
      </div>

      <div class="chart-row">
        <div class="chart-card">
          <h3>训练负荷 & HRV 趋势</h3>
          <div
            ref="tloadChartRef"
            class="chart"
            role="img"
            aria-label="训练负荷与HRV趋势图，展示过去4周每日数据"
          ></div>
        </div>
        <div class="chart-card">
          <h3>睡眠分析</h3>
          <div
            ref="sleepChartRef"
            class="chart"
            role="img"
            aria-label="睡眠阶段分布图，深睡、浅睡、REM和清醒时长"
          ></div>
        </div>
      </div>

      <div class="activity-section" v-if="activities.length > 0">
        <h3>近期运动记录</h3>
        <ul class="activity-list">
          <li v-for="act in activities" :key="act.id" class="activity-item">
            <span class="act-date">{{ formatDate(act.date) }}</span>
            <span class="act-dot" :style="{ background: sportColor(act.data.sport_name || act.data.name) }"></span>
            <div class="act-info">
              <span class="act-sport">{{ sportName(act.data.sport_name || act.data.name) }}</span>
              <span class="act-meta" v-if="act.data.duration_seconds">
                {{ formatDuration(act.data.duration_seconds) }}
              </span>
              <span class="act-meta" v-if="act.data.distance_meters">
                {{ (act.data.distance_meters / 1000).toFixed(1) }} 公里
              </span>
              <span class="act-meta" v-if="act.data.avg_hr">
                {{ act.data.avg_hr }} 次/分
              </span>
              <span class="act-meta" v-if="act.data.calories">
                {{ (act.data.calories / 1000).toFixed(0) }} 千卡
              </span>
            </div>
          </li>
        </ul>
      </div>

      <div v-if="!dailyMetrics.length && !activities.length && !loading" class="empty-state">
        <p>暂无数据，请先同步高驰数据</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useMessage } from 'naive-ui'
import * as echarts from 'echarts/core'
import { LineChart, BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { getErrorMessage } from '@/api'
import { syncFitness, getDailyMetrics, getSleepData, getActivities } from '@/api/fitness'

echarts.use([GridComponent, TooltipComponent, LegendComponent, LineChart, BarChart, CanvasRenderer])

const SPORT_MAP = {
  'Run': '跑步',
  'Trail Run': '越野跑',
  'Track Running': '跑道跑步',
  'Road Bike': '公路骑行',
  'Indoor Cycling': '室内骑行',
  'Mountain Bike': '山地骑行',
  'Strength Training': '力量训练',
  'Swim': '游泳',
  'Open Water Swim': '公开水域游泳',
  'Hike': '徒步',
  'Walk': '步行',
  'Yoga': '瑜伽',
  'Treadmill': '跑步机',
  'Sport 1002': '跑步',
}

const SPORT_COLORS = {
  'Run': '#42A5F5',
  'Trail Run': '#66BB6A',
  'Track Running': '#42A5F5',
  'Road Bike': '#FFA726',
  'Indoor Cycling': '#FFA726',
  'Mountain Bike': '#EF5350',
  'Strength Training': '#AB47BC',
  'Swim': '#26C6DA',
  'Open Water Swim': '#26C6DA',
  'Hike': '#8D6E63',
  'Walk': '#90A4AE',
  'Yoga': '#7E57C2',
  'Treadmill': '#42A5F5',
  'Sport 1002': '#42A5F5',
}

function sportName(name) {
  if (!name) return '--'
  return SPORT_MAP[name] || name
}

function sportColor(name) {
  if (!name) return '#ccc'
  return SPORT_COLORS[name] || '#90A4AE'
}

const dailyMetrics = ref([])
const sleepRecords = ref([])
const activities = ref([])
const loading = ref(true)
const syncing = ref(false)
const message = useMessage()

const tloadChartRef = ref(null)
const sleepChartRef = ref(null)
let tloadChart = null
let sleepChart = null

const avgTrainingLoad = computed(() => {
  const vals = dailyMetrics.value.map(d => d.data.training_load).filter(v => v != null)
  if (!vals.length) return '--'
  return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(0)
})

const avgHrv = computed(() => {
  const vals = dailyMetrics.value.map(d => d.data.avg_sleep_hrv).filter(v => v != null)
  if (!vals.length) return '--'
  return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(0) + ' ms'
})

const avgRhr = computed(() => {
  const vals = dailyMetrics.value.map(d => d.data.rhr).filter(v => v != null)
  if (!vals.length) return '--'
  return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(0)
})

function buildTloadHrvChart() {
  if (!tloadChartRef.value) return
  const sorted = [...dailyMetrics.value].sort((a, b) => a.date.localeCompare(b.date))
  const labels = sorted.map(d => d.date.slice(5))
  tloadChart?.dispose()
  tloadChart = echarts.init(tloadChartRef.value)
  tloadChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['训练负荷', 'HRV'], bottom: 0 },
    grid: { left: 80, right: 80, top: 35, bottom: 40 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, margin: 10 }, boundaryGap: ['8%', '8%'] },
    yAxis: [
      { type: 'value', name: '负荷', nameTextStyle: { fontSize: 12 }, nameGap: 20, axisLabel: { fontSize: 11, margin: 10 } },
      { type: 'value', name: 'ms', nameTextStyle: { fontSize: 12 }, nameGap: 20, axisLabel: { fontSize: 11, margin: 10 } },
    ],
    series: [
      {
        name: '训练负荷',
        type: 'line',
        data: sorted.map(d => d.data.training_load ?? null),
        smooth: true,
        lineStyle: { color: '#42A5F5', width: 2 },
        itemStyle: { color: '#42A5F5' },
      },
      {
        name: 'HRV',
        type: 'line',
        yAxisIndex: 1,
        data: sorted.map(d => d.data.avg_sleep_hrv ?? null),
        smooth: true,
        lineStyle: { color: '#66BB6A', width: 2 },
        itemStyle: { color: '#66BB6A' },
      },
    ],
  })
}

function buildSleepChart() {
  if (!sleepChartRef.value) return
  const sorted = [...sleepRecords.value].sort((a, b) => a.date.localeCompare(b.date))
  const labels = sorted.map(d => d.date.slice(5))
  sleepChart?.dispose()
  sleepChart = echarts.init(sleepChartRef.value)
  sleepChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['清醒', 'REM', '浅睡', '深睡'], bottom: 0 },
    grid: { left: 70, right: 35, top: 35, bottom: 40 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, margin: 10 } },
    yAxis: { type: 'value', name: '分钟', nameTextStyle: { fontSize: 12 }, nameGap: 20, axisLabel: { fontSize: 11, margin: 10 } },
    series: [
      { name: '清醒', type: 'bar', stack: 'total', data: sorted.map(d => d.data.phases?.awake_minutes ?? 0), color: '#c6e4fc' },
      { name: 'REM', type: 'bar', stack: 'total', data: sorted.map(d => d.data.phases?.rem_minutes ?? 0), color: '#90CAF9' },
      { name: '浅睡', type: 'bar', stack: 'total', data: sorted.map(d => d.data.phases?.light_minutes ?? 0), color: '#64B5F6' },
      { name: '深睡', type: 'bar', stack: 'total', data: sorted.map(d => d.data.phases?.deep_minutes ?? 0), color: '#42A5F5' },
    ],
  })
}

function handleResize() {
  tloadChart?.resize()
  sleepChart?.resize()
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  return dateStr.slice(5)
}

function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h}小时${m}分` : `${m}分`
}

async function loadData() {
  loading.value = true
  try {
    const [dailyRes, sleepRes, actRes] = await Promise.all([
      getDailyMetrics(4),
      getSleepData(4),
      getActivities(),
    ])
    dailyMetrics.value = dailyRes.data.data
    sleepRecords.value = sleepRes.data.data
    activities.value = actRes.data.data
    await nextTick()
    buildTloadHrvChart()
    buildSleepChart()
  } catch (error) {
    console.error('加载运动数据失败:', error)
    message.error(getErrorMessage(error, '加载运动数据失败，请稍后重试'))
  } finally {
    loading.value = false
  }
}

async function handleSync() {
  syncing.value = true
  try {
    const response = await syncFitness()
    await loadData()
    if (response.data.data.partial) {
      message.warning(`部分同步完成：${response.data.data.unavailable_sources.join('、')} 暂不可用`)
    } else {
      message.success(`同步完成，写入 ${response.data.data.upserted} 条记录`)
    }
  } catch (error) {
    console.error('同步失败:', error)
    message.error(getErrorMessage(error, '同步失败，请稍后重试'))
  } finally {
    syncing.value = false
  }
}

onMounted(() => {
  loadData()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  tloadChart?.dispose()
  sleepChart?.dispose()
})
</script>

<style scoped>
.dashboard-page {
  min-height: 100vh;
  background: var(--bg-page);
}

.content-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.content-header h1 {
  flex: 1;
  margin: 0;
  font-size: 20px;
  color: var(--text-primary);
}

.dashboard-content {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}

.stat-card {
  background: var(--bg-card);
  border-radius: 12px;
  padding: 20px;
  transition: box-shadow 0.15s var(--ease-out-expressive, cubic-bezier(0.16, 1, 0.3, 1));
}

.stat-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.stat-label {
  display: block;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}

.stat-value small {
  font-size: 14px;
  font-weight: 400;
  color: var(--text-secondary);
}

.chart-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 24px;
}

.chart-card {
  background: var(--bg-card);
  border-radius: 12px;
  padding: 20px;
  transition: box-shadow 0.15s var(--ease-out-expressive, cubic-bezier(0.16, 1, 0.3, 1));
}

.chart-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.chart-card h3 {
  margin: 0 0 12px;
  font-size: 15px;
  color: var(--text-primary);
}

.chart {
  width: 100%;
  height: 360px;
}

.activity-section {
  background: var(--bg-card);
  border-radius: 12px;
  padding: 20px;
  transition: box-shadow 0.15s var(--ease-out-expressive, cubic-bezier(0.16, 1, 0.3, 1));
}

.activity-section:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.activity-section h3 {
  margin: 0 0 16px;
  font-size: 15px;
  color: var(--text-primary);
}

.activity-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.activity-item {
  display: flex;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #e2e8f0;
}

.activity-item:last-child {
  border-bottom: none;
}

.act-date {
  width: 55px;
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.act-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-right: 4px;
}

.act-info {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.act-sport {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
  min-width: 80px;
}

.act-meta {
  font-size: 13px;
  color: var(--text-secondary);
}

.empty-state {
  text-align: center;
  padding: 60px 0;
  color: var(--text-secondary);
  font-size: 15px;
}

@media (max-width: 768px) {
  .dashboard-content {
    padding: 16px;
  }

  .chart-row {
    grid-template-columns: 1fr;
  }

  .chart {
    height: 280px;
  }

  .stat-value {
    font-size: 22px;
  }

  .activity-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .act-date {
    width: auto;
  }
}

@media (max-width: 480px) {
  .content-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .content-header h1 {
    font-size: 18px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stat-card,
  .chart-card,
  .activity-section {
    transition: none;
  }
}
</style>
