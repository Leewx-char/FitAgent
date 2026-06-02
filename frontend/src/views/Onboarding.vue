<template>
  <div class="onboarding-container">
    <div class="onboarding-card">
      <div class="onboarding-header">
        <h1>完善你的健身档案</h1>
        <p>让我们更好地了解你，为你提供个性化建议</p>
      </div>

      <div class="steps-wrapper" ref="stepsRef">
        <n-steps :current="currentStep" :status="stepStatus" size="small">
          <n-step title="基本信息" />
          <n-step title="身体数据" />
          <n-step title="健身目标" />
          <n-step title="训练经验" />
          <n-step title="特殊情况" />
        </n-steps>
      </div>

      <!-- Step 1: 基本信息 -->
      <div v-show="currentStep === 1" class="step-content">
        <h3>你的性别是？</h3>
        <n-radio-group v-model:value="form.gender" size="large">
          <n-space>
            <n-radio-button value="男">男</n-radio-button>
            <n-radio-button value="女">女</n-radio-button>
          </n-space>
        </n-radio-group>

        <h3 style="margin-top: 24px">你的年龄是？</h3>
        <n-input-number
          v-model:value="form.age"
          :min="10"
          :max="100"
          placeholder="请输入年龄"
          style="width: 200px"
        />
      </div>

      <!-- Step 2: 身体数据 -->
      <div v-show="currentStep === 2" class="step-content">
        <h3>你的身高和体重是？</h3>
        <div class="body-inputs">
          <div class="body-input-item">
            <n-input-number
              v-model:value="form.height"
              :min="100"
              :max="250"
              placeholder="身高"
            >
              <template #suffix>cm</template>
            </n-input-number>
            <span class="input-label">身高</span>
          </div>
          <div class="body-input-item">
            <n-input-number
              v-model:value="form.weight"
              :min="30"
              :max="300"
              :step="0.1"
              placeholder="体重"
            >
              <template #suffix>kg</template>
            </n-input-number>
            <span class="input-label">体重</span>
          </div>
        </div>
      </div>

      <!-- Step 3: 健身目标 -->
      <div v-show="currentStep === 3" class="step-content">
        <h3>你的健身目标是什么？</h3>
        <n-radio-group v-model:value="form.goal">
          <n-space vertical>
            <n-radio value="减脂">减脂 — 减少体脂，让身材更紧致</n-radio>
            <n-radio value="增肌">增肌 — 增加肌肉量，让身体更强壮</n-radio>
            <n-radio value="塑形">塑形 — 塑造身体线条，追求匀称美</n-radio>
            <n-radio value="耐力">耐力 — 提升心肺功能，增强体能</n-radio>
            <n-radio value="健康">健康 — 保持健康，养成运动习惯</n-radio>
          </n-space>
        </n-radio-group>
      </div>

      <!-- Step 4: 训练经验 -->
      <div v-show="currentStep === 4" class="step-content">
        <h3>你的运动经验如何？</h3>
        <n-radio-group v-model:value="form.experience">
          <n-space vertical>
            <n-radio value="新手">新手 — 从未系统训练过</n-radio>
            <n-radio value="中级">中级 — 有一定训练经验，能做基本动作</n-radio>
            <n-radio value="高级">高级 — 长期训练，对动作和计划有较深理解</n-radio>
          </n-space>
        </n-radio-group>

        <h3 style="margin-top: 24px">每周计划训练几天？</h3>
        <n-slider v-model:value="form.weekly_days" :min="1" :max="7" :step="1" :marks="{ 1: '1天', 3: '3天', 5: '5天', 7: '7天' }" />
        <p style="color: var(--text-secondary); margin-top: 8px">每周 {{ form.weekly_days }} 天训练</p>
      </div>

      <!-- Step 5: 特殊情况 -->
      <div v-show="currentStep === 5" class="step-content">
        <h3>你有伤病史吗？</h3>
        <n-checkbox-group v-model:value="form.injuries">
          <n-space vertical>
            <n-checkbox value="膝盖">膝盖问题</n-checkbox>
            <n-checkbox value="腰椎">腰椎问题</n-checkbox>
            <n-checkbox value="肩部">肩部问题</n-checkbox>
            <n-checkbox value="踝关节">踝关节问题</n-checkbox>
            <n-checkbox value="手腕">手腕问题</n-checkbox>
          </n-space>
        </n-checkbox-group>
        <p v-if="form.injuries.length === 0" style="color: var(--success); margin-top: 8px">
          没有伤病史，保持健康运动！
        </p>

        <h3 style="margin-top: 28px">有饮食限制吗？</h3>
        <n-checkbox-group v-model:value="form.diet_restrict">
          <n-space vertical>
            <n-checkbox value="素食">素食</n-checkbox>
            <n-checkbox value="低碳">低碳水</n-checkbox>
            <n-checkbox value="乳糖不耐受">乳糖不耐受</n-checkbox>
            <n-checkbox value="无麸质">无麸质</n-checkbox>
          </n-space>
        </n-checkbox-group>

        <h3 style="margin-top: 28px">训练偏好（可选）</h3>
        <div class="pref-tags">
          <n-tag
            v-for="pref in preferenceOptions"
            :key="pref"
            :type="form.selectedPrefs.includes(pref) ? 'primary' : 'default'"
            :bordered="form.selectedPrefs.includes(pref)"
            style="cursor: pointer"
            @click="togglePref(pref)"
          >
            {{ pref }}
          </n-tag>
        </div>
      </div>

      <div class="step-actions">
        <n-button v-if="currentStep > 1" @click="prevStep">上一步</n-button>
        <n-button
          v-if="currentStep < 5"
          type="primary"
          :disabled="!canNext"
          @click="nextStep"
        >
          下一步
        </n-button>
        <n-button
          v-if="currentStep === 5"
          type="primary"
          :loading="submitting"
          @click="handleSubmit"
        >
          完成，开始训练！
        </n-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useProfileStore } from '@/stores/profile'

const router = useRouter()
const message = useMessage()
const profileStore = useProfileStore()

const currentStep = ref(1)
const stepStatus = ref('process')
const submitting = ref(false)
const stepsRef = ref(null)

watch(currentStep, () => {
  nextTick(() => {
    if (stepsRef.value) {
      stepsRef.value.scrollTo({
        left: stepsRef.value.scrollWidth,
        behavior: 'smooth',
      })
    }
  })
})

const preferenceOptions = ['健身房', '家里', '户外', '哑铃', '杠铃', '自重训练', '早上', '下午', '晚上']

const form = reactive({
  gender: '',
  age: null,
  height: null,
  weight: null,
  goal: '',
  experience: '',
  weekly_days: 3,
  injuries: [],
  diet_restrict: [],
  selectedPrefs: [],
})

const canNext = computed(() => {
  switch (currentStep.value) {
    case 1: return form.gender && form.age
    case 2: return form.height && form.weight
    case 3: return form.goal !== ''
    case 4: return form.experience !== ''
    case 5: return true
    default: return false
  }
})

function prevStep() {
  if (currentStep.value > 1) currentStep.value--
}

function nextStep() {
  if (canNext.value && currentStep.value < 5) currentStep.value++
}

function togglePref(pref) {
  const idx = form.selectedPrefs.indexOf(pref)
  if (idx >= 0) {
    form.selectedPrefs.splice(idx, 1)
  } else {
    form.selectedPrefs.push(pref)
  }
}

async function handleSubmit() {
  submitting.value = true
  try {
    const preferences = {}

    const prefMap = {
      '健身房': { gym: true },
      '家里': { gym: false },
      '户外': { gym: false },
    }
    for (const p of form.selectedPrefs) {
      Object.assign(preferences, prefMap[p] || {})
    }

    const hasGym = form.selectedPrefs.some((p) => ['健身房', '哑铃', '杠铃'].includes(p))
    const hasHome = form.selectedPrefs.some((p) => ['家里', '自重训练'].includes(p))
    if (hasGym) preferences.gym = true
    if (hasHome) preferences.gym = false

    const timePrefs = form.selectedPrefs.filter((p) => ['早上', '下午', '晚上'].includes(p))
    if (timePrefs.length > 0) preferences.preferred_time = timePrefs[0]

    const equipPrefs = form.selectedPrefs.filter((p) => ['哑铃', '杠铃', '自重训练'].includes(p))
    if (equipPrefs.length > 0) preferences.equipment = equipPrefs.join('/')

    await profileStore.saveProfile({
      gender: form.gender,
      age: form.age,
      height: form.height,
      weight: form.weight,
      goal: form.goal,
      experience: form.experience,
      weekly_days: form.weekly_days,
      injuries: form.injuries.length > 0 ? form.injuries : [],
      diet_restrict: form.diet_restrict.length > 0 ? form.diet_restrict : [],
      preferences: Object.keys(preferences).length > 0 ? preferences : {},
    })

    message.success('档案创建成功！')
    router.push('/')
  } catch (err) {
    message.error(err.response?.data?.detail || '提交失败，请重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.onboarding-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #F8FBFF 0%, var(--primary-light) 100%);
  padding: 32px 20px;
}

.onboarding-card {
  width: 520px;
  padding: 36px 32px;
  background: white;
  border-radius: 16px;
  box-shadow: 0 4px 24px rgba(66, 165, 245, 0.12);
}

.steps-wrapper {
  overflow-x: auto;
  margin-bottom: 24px;
  padding: 8px 16px 12px;
}

.steps-wrapper :deep(.n-steps) {
  min-width: 400px;
}

.steps-wrapper :deep(.n-step) {
  min-height: 48px;
}

.onboarding-header {
  text-align: center;
  margin-bottom: 24px;
}

.onboarding-header h1 {
  font-size: 24px;
  color: var(--text-primary);
}

.onboarding-header p {
  color: var(--text-secondary);
  font-size: 14px;
  margin-top: 6px;
}

.step-content h3 {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text-primary);
}

.step-content h3:not(:first-child) {
  margin-top: 20px;
}

.body-inputs {
  display: flex;
  gap: 24px;
}

.body-input-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.input-label {
  color: var(--text-secondary);
  font-size: 13px;
}

.step-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
}

.pref-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>