<template>
  <section class="plan-page">
    <header class="page-header">
      <div>
        <h1>本周自适应计划</h1>
        <p>基于画像、Coros 近 4 周数据、已确认反馈和 RAG 证据生成；生成前会经过固定安全策略校验。</p>
      </div>
      <n-button type="primary" :loading="generating" @click="generate">生成 / 更新计划</n-button>
    </header>

    <n-alert v-if="plan" type="warning" :show-icon="false" class="safety-note">
      <strong>安全上限：{{ plan.safety.maximum_intensity }}强度。</strong>
      {{ plan.safety.signals?.join('；') }}。{{ plan.safety.disclaimer }}
    </n-alert>

    <n-spin :show="loading">
      <n-empty v-if="!plan && !loading" description="还没有本周计划，先完善档案并同步 Coros 数据后生成。" />
      <template v-else-if="plan">
        <n-card class="overview" size="small">
          <h2>{{ plan.plan.title }}</h2>
          <p>{{ plan.plan.goal }}</p>
          <div class="tags">
            <n-tag v-for="signal in plan.safety.signals" :key="signal" type="warning" size="small">{{ signal }}</n-tag>
          </div>
        </n-card>

        <div class="day-grid">
          <n-card v-for="day in sortedDays" :key="day.day_of_week" size="small" class="day-card">
            <template #header>周{{ weekdays[day.day_of_week - 1] }} · {{ day.title }}</template>
            <template #header-extra><n-tag size="small" :type="tagType(day.kind)">{{ day.kind }}</n-tag></template>
            <p class="focus">{{ day.focus }}</p>
            <ul v-if="day.exercises.length" class="exercise-list">
              <li v-for="exercise in day.exercises" :key="exercise.name">
                <strong>{{ exercise.name }}</strong> · {{ exercise.sets }} 组 × {{ exercise.reps }} · {{ exercise.intensity }}强度
                <span v-if="exercise.notes">{{ exercise.notes }}</span>
              </li>
            </ul>
            <p v-else class="rest-note">{{ day.notes || '按恢复状态灵活安排。' }}</p>
            <div class="feedback">
              <n-button size="tiny" @click="openFeedback(day, true)">完成反馈</n-button>
              <n-button size="tiny" tertiary @click="openFeedback(day, false)">未完成 / 不适</n-button>
            </div>
          </n-card>
        </div>
      </template>
    </n-spin>

    <n-modal v-model:show="feedbackVisible" preset="card" title="记录执行反馈" style="max-width: 440px">
      <n-form label-placement="top">
        <n-form-item label="主观用力程度 RPE（1-10，可不填）"><n-input-number v-model:value="feedback.rpe" :min="1" :max="10" /></n-form-item>
        <n-form-item label="疼痛评分（0-10，可不填）"><n-input-number v-model:value="feedback.pain_score" :min="0" :max="10" /></n-form-item>
        <n-form-item label="备注"><n-input v-model:value="feedback.notes" type="textarea" maxlength="500" /></n-form-item>
      </n-form>
      <template #footer><n-button type="primary" @click="submitFeedback">保存反馈</n-button></template>
    </n-modal>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { NAlert, NButton, NCard, NEmpty, NForm, NFormItem, NInput, NInputNumber, NModal, NSpin, NTag, useMessage } from 'naive-ui'
import { getErrorMessage } from '@/api'
import { generateTrainingPlan, getCurrentPlan, savePlanFeedback } from '@/api/trainingPlans'

const message = useMessage()
const plan = ref(null)
const loading = ref(false)
const generating = ref(false)
const feedbackVisible = ref(false)
const feedbackDay = ref(null)
const feedback = reactive({ completed: true, rpe: null, pain_score: null, notes: '' })
const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const sortedDays = computed(() => [...(plan.value?.plan.days || [])].sort((a, b) => a.day_of_week - b.day_of_week))

function tagType(kind) { return kind === '训练' ? 'success' : kind === '恢复' ? 'warning' : 'default' }
async function load() {
  loading.value = true
  try { plan.value = (await getCurrentPlan()).data.data } catch (error) { message.error(getErrorMessage(error, '读取本周计划失败')) } finally { loading.value = false }
}
async function generate() {
  generating.value = true
  try {
    plan.value = (await generateTrainingPlan()).data.data
    message.success('已生成，并通过安全策略校验')
  } catch (error) {
    const fallback = error.code === 'ECONNABORTED'
      ? '生成计划超时，请稍后重试；首次检索和模型生成可能需要 1—2 分钟'
      : '生成计划失败，请确认档案、运动数据和知识库均可用'
    message.error(getErrorMessage(error, fallback))
  } finally { generating.value = false }
}
function openFeedback(day, completed) {
  feedbackDay.value = day
  feedback.completed = completed
  feedback.rpe = null
  feedback.pain_score = null
  feedback.notes = ''
  feedbackVisible.value = true
}
async function submitFeedback() {
  if (!plan.value || !feedbackDay.value) return
  try {
    await savePlanFeedback(plan.value.id, { day_of_week: feedbackDay.value.day_of_week, ...feedback })
    feedbackVisible.value = false
    message.success('反馈已保存，将在下一版计划中参与恢复判断')
  } catch (error) { message.error(getErrorMessage(error, '保存反馈失败')) }
}
onMounted(load)
</script>

<style scoped>
.plan-page { max-width: 1180px; margin: 0 auto; padding: 28px 24px; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
h1 { margin: 0 0 8px; font-size: 23px; } .page-header p, .overview p { color: var(--text-secondary); margin: 0; font-size: 14px; line-height: 1.6; }
.safety-note { margin-bottom: 16px; } .overview { margin-bottom: 16px; } .overview h2 { margin: 0 0 6px; font-size: 18px; } .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.day-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.focus { margin: 0 0 12px; font-weight: 600; font-size: 14px; }.exercise-list { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; }.exercise-list span { color: var(--text-secondary); margin-left: 5px; }.rest-note { color: var(--text-secondary); font-size: 13px; min-height: 38px; }.feedback { display: flex; gap: 8px; margin-top: 16px; }
@media (max-width: 760px) { .plan-page { padding: 18px 14px; }.page-header { flex-direction: column; }.day-grid { grid-template-columns: 1fr; } }
</style>
