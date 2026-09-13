<template>
  <section class="plan-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">训练安排</p>
        <h1>本周计划</h1>
        <p>结合训练档案、最近 7 天数据和已确认反馈生成；开始前始终经过固定安全策略校验。</p>
      </div>
      <n-button type="primary" :loading="generating" @click="generate">生成 / 更新计划</n-button>
    </header>

    <n-spin :show="loading">
      <div v-if="!plan && !loading" class="empty-state">
        <h2>还没有本周计划</h2>
        <p>完善档案并同步 COROS 数据后，即可生成更贴近当前节奏的计划。</p>
      </div>

      <template v-else-if="plan">
        <n-alert type="warning" :show-icon="false" class="safety-note">
          <strong>安全上限：{{ plan.safety.maximum_intensity }}强度。</strong>
          {{ plan.safety.signals?.join('；') }}。{{ plan.safety.disclaimer }}
        </n-alert>

        <section class="plan-layout">
          <div class="plan-content">
            <section class="plan-summary">
              <p class="eyebrow">本周重点</p>
              <h2>{{ plan.plan.title }}</h2>
              <p>{{ plan.plan.goal }}</p>
              <div class="signal-list">
                <n-tag v-for="signal in plan.safety.signals" :key="signal" type="warning" size="small">{{ signal }}</n-tag>
              </div>
            </section>

            <section class="days-section" aria-labelledby="days-title">
              <div class="section-heading">
                <div>
                  <p class="eyebrow">逐日安排</p>
                  <h2 id="days-title">训练节奏</h2>
                </div>
                <span>{{ sortedDays.length }} 天</span>
              </div>
              <article v-for="day in sortedDays" :key="day.day_of_week" class="plan-day">
                <div class="day-marker">周{{ weekdays[day.day_of_week - 1] }}</div>
                <div class="day-detail">
                  <div class="day-title-row">
                    <h3>{{ day.title }}</h3>
                    <n-tag size="small" :type="tagType(day.kind)">{{ day.kind }}</n-tag>
                  </div>
                  <p class="focus">{{ day.focus }}</p>
                  <ul v-if="day.exercises.length" class="exercise-list">
                    <li v-for="exercise in day.exercises" :key="exercise.name">
                      <strong>{{ exercise.name }}</strong>
                      <span>{{ exercise.sets }} 组 × {{ exercise.reps }} · {{ exercise.intensity }}强度</span>
                      <em v-if="exercise.notes">{{ exercise.notes }}</em>
                    </li>
                  </ul>
                  <p v-else class="rest-note">{{ day.notes || '按恢复状态灵活安排。' }}</p>
                </div>
                <div class="feedback-actions">
                  <n-button size="small" @click="openFeedback(day, true)">完成反馈</n-button>
                  <n-button size="small" tertiary @click="openFeedback(day, false)">未完成 / 不适</n-button>
                </div>
              </article>
            </section>
          </div>

          <aside class="plan-aside">
            <p class="eyebrow">执行提示</p>
            <h2>记录你的感受</h2>
            <p>完成后写下 RPE、疼痛和备注。下一次生成时，这些反馈会参与恢复判断。</p>
            <div class="aside-rule"></div>
            <strong>有不适时</strong>
            <p>先暂停高强度动作，并在反馈中如实记录；必要时寻求专业医疗建议。</p>
          </aside>
        </section>
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
import { NAlert, NButton, NEmpty, NForm, NFormItem, NInput, NInputNumber, NModal, NSpin, NTag, useMessage } from 'naive-ui'
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

/** 将计划日类型映射为克制的状态标签样式。 */
function tagType(kind) {
  return kind === '训练' ? 'success' : kind === '恢复' ? 'warning' : 'default'
}

/** 加载当前周计划并维护页面加载状态。 */
async function load() {
  loading.value = true
  try {
    plan.value = (await getCurrentPlan()).data.data
  } catch (error) {
    message.error(getErrorMessage(error, '读取本周计划失败'))
  } finally {
    loading.value = false
  }
}

/** 请求生成计划；超时时保留检索与模型生成的专用提示。 */
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
  } finally {
    generating.value = false
  }
}

/** 为指定计划日重置反馈表单并打开反馈弹窗。 */
function openFeedback(day, completed) {
  feedbackDay.value = day
  feedback.completed = completed
  feedback.rpe = null
  feedback.pain_score = null
  feedback.notes = ''
  feedbackVisible.value = true
}

/** 将当前计划日的执行反馈提交到服务端。 */
async function submitFeedback() {
  if (!plan.value || !feedbackDay.value) return
  try {
    await savePlanFeedback(plan.value.id, { day_of_week: feedbackDay.value.day_of_week, ...feedback })
    feedbackVisible.value = false
    message.success('反馈已保存，将在下一版计划中参与恢复判断')
  } catch (error) {
    message.error(getErrorMessage(error, '保存反馈失败'))
  }
}

onMounted(load)
</script>

<style scoped>
.plan-page { max-width: 1180px; margin: 0 auto; padding: 40px 44px 56px; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow { color: var(--text-quiet); font-size: 12px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; }
h1 { margin: 4px 0 8px; color: var(--text-primary); font-size: 28px; letter-spacing: -.035em; }
.page-header p, .plan-summary > p, .plan-aside p { margin: 0; color: var(--text-secondary); font-size: 14px; line-height: 1.7; }
.safety-note { margin-bottom: 16px; }
.plan-layout { display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 16px; }
.plan-content, .plan-aside { border: 1px solid var(--border); border-radius: 10px; background: var(--bg-card); }
.plan-summary { padding: 28px; border-bottom: 1px solid var(--border); }
.plan-summary h2, .section-heading h2, .plan-aside h2, .empty-state h2 { margin: 6px 0 8px; color: var(--text-primary); font-size: 20px; letter-spacing: -.02em; }
.signal-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 18px; }
.days-section { padding: 24px 28px 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.section-heading h2 { font-size: 17px; }
.section-heading span { color: var(--text-quiet); font-size: 13px; }
.plan-day { display: grid; grid-template-columns: 70px minmax(0, 1fr) auto; gap: 16px; padding: 20px 0; border-top: 1px solid var(--border); }
.day-marker { padding-top: 3px; color: var(--primary-dark); font-size: 13px; font-weight: 700; }
.day-title-row { display: flex; align-items: center; gap: 8px; }
.day-title-row h3 { margin: 0; color: var(--text-primary); font-size: 16px; }
.focus { margin: 7px 0 12px; color: var(--text-secondary); font-size: 14px; line-height: 1.6; }
.exercise-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
.exercise-list li { display: flex; flex-wrap: wrap; gap: 5px; color: var(--text-secondary); font-size: 13px; line-height: 1.55; }
.exercise-list strong { color: var(--text-primary); }
.exercise-list em { width: 100%; color: var(--text-quiet); font-style: normal; }
.rest-note { margin: 0; color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
.feedback-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.plan-aside { align-self: start; padding: 28px; background: #F1F5F7; }
.plan-aside strong { display: block; color: var(--text-primary); font-size: 14px; margin-bottom: 7px; }
.aside-rule { height: 1px; margin: 22px 0; background: var(--border); }
.empty-state { padding: 84px 24px; border: 1px dashed var(--border); border-radius: 10px; color: var(--text-secondary); text-align: center; }
.empty-state p { font-size: 14px; line-height: 1.7; }
@media (max-width: 960px) { .plan-page { padding: 32px 28px 48px; } .plan-layout { grid-template-columns: 1fr; } .plan-aside { display: none; } }
@media (max-width: 700px) { .plan-page { padding: 24px 16px 40px; } .page-header { flex-direction: column; } .plan-summary, .days-section { padding-left: 18px; padding-right: 18px; } .plan-day { grid-template-columns: 1fr; gap: 10px; } .feedback-actions { align-items: flex-start; flex-direction: row; flex-wrap: wrap; } }
</style>
