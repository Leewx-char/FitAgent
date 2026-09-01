<template>
  <div class="profile-page">
    <div class="profile-card">
      <div class="profile-header">
        <h2>健身画像</h2>
        <n-button v-if="!editing" type="primary" size="small" @click="startEdit">编辑</n-button>
        <n-space v-else>
          <n-button size="small" @click="cancelEdit">取消</n-button>
          <n-button type="primary" size="small" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </div>

      <n-spin :show="loading">
        <div v-if="profile" class="profile-grid">
          <div class="profile-section">
            <h4>基本信息</h4>
            <div class="profile-items">
              <div class="profile-item">
                <span class="label">性别</span>
                <span v-if="!editing" class="value">{{ profile.gender }}</span>
                <n-radio-group v-else v-model:value="editForm.gender" size="small">
                  <n-radio-button value="男">男</n-radio-button>
                  <n-radio-button value="女">女</n-radio-button>
                </n-radio-group>
              </div>
              <div class="profile-item">
                <span class="label">年龄</span>
                <span v-if="!editing" class="value">{{ profile.age }} 岁</span>
                <n-input-number v-else v-model:value="editForm.age" :min="10" :max="100" size="small" style="width: 120px" />
              </div>
              <div class="profile-item">
                <span class="label">身高</span>
                <span v-if="!editing" class="value">{{ profile.height }} cm</span>
                <n-input-number v-else v-model:value="editForm.height" :min="100" :max="250" size="small" style="width: 120px" />
              </div>
              <div class="profile-item">
                <span class="label">体重</span>
                <span v-if="!editing" class="value">{{ profile.weight }} kg</span>
                <n-input-number v-else v-model:value="editForm.weight" :min="30" :max="300" :step="0.1" size="small" style="width: 120px" />
              </div>
            </div>
          </div>

          <div class="profile-section">
            <h4>训练信息</h4>
            <div class="profile-items">
              <div class="profile-item">
                <span class="label">健身目标</span>
                <span v-if="!editing" class="value">
                  <n-tag type="primary" size="small">{{ profile.goal }}</n-tag>
                </span>
                <n-select
                  v-else
                  v-model:value="editForm.goal"
                  :options="goalOptions"
                  size="small"
                  style="width: 160px"
                />
              </div>
              <div class="profile-item">
                <span class="label">运动经验</span>
                <span v-if="!editing" class="value">
                  <n-tag type="info" size="small">{{ profile.experience }}</n-tag>
                </span>
                <n-select
                  v-else
                  v-model:value="editForm.experience"
                  :options="expOptions"
                  size="small"
                  style="width: 160px"
                />
              </div>
              <div class="profile-item">
                <span class="label">每周训练</span>
                <span v-if="!editing" class="value">{{ profile.weekly_days }} 天/周</span>
                <n-slider v-else v-model:value="editForm.weekly_days" :min="1" :max="7" :step="1" style="width: 160px" />
              </div>
            </div>
          </div>

          <div class="profile-section">
            <h4>特殊情况</h4>
            <div class="profile-items">
              <div class="profile-item">
                <span class="label">伤病史</span>
                <span v-if="!editing" class="value">
                  <n-tag v-if="profile.injuries?.length === 0" size="small">无</n-tag>
                  <n-tag v-for="injury in profile.injuries" :key="injury" type="warning" size="small" style="margin-right: 4px">
                    {{ injury }}
                  </n-tag>
                </span>
                <n-checkbox-group v-else v-model:value="editForm.injuries">
                  <n-space>
                    <n-checkbox value="膝盖">膝盖</n-checkbox>
                    <n-checkbox value="腰椎">腰椎</n-checkbox>
                    <n-checkbox value="肩部">肩部</n-checkbox>
                    <n-checkbox value="踝关节">踝关节</n-checkbox>
                    <n-checkbox value="手腕">手腕</n-checkbox>
                  </n-space>
                </n-checkbox-group>
              </div>
              <div class="profile-item">
                <span class="label">饮食限制</span>
                <span v-if="!editing" class="value">
                  <n-tag v-if="profile.diet_restrict?.length === 0" size="small">无</n-tag>
                  <n-tag v-for="d in profile.diet_restrict" :key="d" size="small" style="margin-right: 4px">
                    {{ d }}
                  </n-tag>
                </span>
                <n-checkbox-group v-else v-model:value="editForm.diet_restrict">
                  <n-space>
                    <n-checkbox value="素食">素食</n-checkbox>
                    <n-checkbox value="低碳">低碳</n-checkbox>
                    <n-checkbox value="乳糖不耐受">乳糖不耐受</n-checkbox>
                    <n-checkbox value="无麸质">无麸质</n-checkbox>
                  </n-space>
                </n-checkbox-group>
              </div>
            </div>
          </div>

          <div class="profile-section" v-if="profile.preferences">
            <h4>训练偏好</h4>
            <div class="profile-items">
              <div class="profile-item" v-if="profile.preferences.preferred_time">
                <span class="label">喜欢时间</span>
                <span class="value">{{ profile.preferences.preferred_time }}</span>
              </div>
              <div class="profile-item" v-if="profile.preferences.equipment">
                <span class="label">偏好器械</span>
                <span class="value">{{ profile.preferences.equipment }}</span>
              </div>
              <div class="profile-item" v-if="profile.preferences.gym !== undefined">
                <span class="label">训练场所</span>
                <span class="value">{{ profile.preferences.gym ? '健身房' : '居家' }}</span>
              </div>
            </div>
          </div>

          <div class="profile-section" v-if="hasHealthData">
            <h4>健康数据</h4>
            <div class="health-data-grid">
              <div v-for="key in healthFields" :key="key" class="health-data-item">
                <span class="data-label">{{ healthLabels[key] }}</span>
                <span class="data-value" v-if="getHealthValue(key)">
                  {{ getHealthValue(key) }}<span class="data-unit" v-if="getHealthUnit(key)">{{ getHealthUnit(key) }}</span>
                </span>
                <span class="data-value data-none" v-else>未识别</span>
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="!loading" class="empty-profile">
          <p>还没有健身档案</p>
          <n-button type="primary" @click="$router.push('/onboarding')">去创建</n-button>
        </div>
      </n-spin>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useMessage } from 'naive-ui'
import { getErrorMessage } from '@/api'
import { useProfileStore } from '@/stores/profile'

const message = useMessage()
const profileStore = useProfileStore()

const loading = ref(false)
const editing = ref(false)
const saving = ref(false)
const profile = ref(null)

const goalOptions = [
  { label: '减脂', value: '减脂' },
  { label: '增肌', value: '增肌' },
  { label: '塑形', value: '塑形' },
  { label: '耐力', value: '耐力' },
  { label: '健康', value: '健康' },
]

const expOptions = [
  { label: '新手', value: '新手' },
  { label: '中级', value: '中级' },
  { label: '高级', value: '高级' },
]

const editForm = reactive({
  gender: '',
  age: null,
  height: null,
  weight: null,
  goal: '',
  experience: '',
  weekly_days: 3,
  injuries: [],
  diet_restrict: [],
})

const healthLabels = {
  bmi: 'BMI',
  body_fat: '体脂率',
  heart_rate: '心率',
  blood_pressure: '血压',
  blood_sugar: '血糖',
  cholesterol: '胆固醇',
  alt: '谷丙转氨酶',
  uric_acid: '尿酸',
}

const healthFields = Object.keys(healthLabels)

const hasHealthData = computed(() => {
  const hd = profile.value?.health_data
  if (!hd || typeof hd !== 'object') return false
  return Object.values(hd).some(field => field?.value != null)
})

/** 从档案健康数据中读取指定指标的数值，缺失时返回空值。 */
function getHealthValue(key) {
  const field = profile.value?.health_data?.[key]
  if (!field || typeof field !== 'object') return null
  return field.value != null ? field.value : null
}

/** 从档案健康数据中读取指定指标的单位，缺失时返回空值。 */
function getHealthUnit(key) {
  const field = profile.value?.health_data?.[key]
  if (!field || typeof field !== 'object') return null
  return field.unit || null
}

/** 加载档案到页面状态；失败时清空展示并提示用户。 */
async function loadProfile() {
  loading.value = true
  try {
    await profileStore.fetchProfile()
    profile.value = profileStore.profile
  } catch (error) {
    console.error('加载 profile 失败:', error)
    message.error(getErrorMessage(error, '加载档案失败'))
    profile.value = null
  } finally {
    loading.value = false
  }
}

/** 将当前档案复制到编辑表单并进入编辑模式。 */
function startEdit() {
  if (!profile.value) return
  editing.value = true
  Object.assign(editForm, {
    gender: profile.value.gender,
    age: profile.value.age,
    height: profile.value.height,
    weight: profile.value.weight,
    goal: profile.value.goal,
    experience: profile.value.experience,
    weekly_days: profile.value.weekly_days,
    injuries: [...(profile.value.injuries || [])],
    diet_restrict: [...(profile.value.diet_restrict || [])],
  })
}

/** 退出编辑模式，不提交编辑表单内容。 */
function cancelEdit() {
  editing.value = false
}

/** 仅提交编辑表单中与当前档案不同的字段。 */
async function saveEdit() {
  saving.value = true
  try {
    const data = {}
    if (editForm.gender !== profile.value.gender) data.gender = editForm.gender
    if (editForm.age !== profile.value.age) data.age = editForm.age
    if (editForm.height !== profile.value.height) data.height = editForm.height
    if (editForm.weight !== profile.value.weight) data.weight = editForm.weight
    if (editForm.goal !== profile.value.goal) data.goal = editForm.goal
    if (editForm.experience !== profile.value.experience) data.experience = editForm.experience
    if (editForm.weekly_days !== profile.value.weekly_days) data.weekly_days = editForm.weekly_days

    const injuriesChanged = JSON.stringify(editForm.injuries) !== JSON.stringify(profile.value.injuries || [])
    const dietChanged = JSON.stringify(editForm.diet_restrict) !== JSON.stringify(profile.value.diet_restrict || [])
    if (injuriesChanged) data.injuries = editForm.injuries
    if (dietChanged) data.diet_restrict = editForm.diet_restrict

    await profileStore.saveProfile(data)
    profile.value = profileStore.profile
    editing.value = false
    message.success('画像更新成功')
  } catch (error) {
    message.error(getErrorMessage(error, '更新失败'))
  } finally {
    saving.value = false
  }
}

onMounted(loadProfile)
</script>

<style scoped>
.profile-page {
  min-height: 100vh;
  background: var(--bg-page);
  display: flex;
  justify-content: center;
  padding: 40px 20px;
}

.profile-card {
  width: 640px;
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: 0 2px 12px rgba(66, 165, 245, 0.08);
}

.profile-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.profile-header h2 {
  font-size: 20px;
  color: var(--text-primary);
}

.profile-grid {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.profile-section h4 {
  font-size: 15px;
  color: var(--text-primary);
  font-weight: 600;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.profile-section h4::before {
  content: '';
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: var(--primary);
}

.profile-items {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.profile-item {
  display: flex;
  align-items: center;
  gap: 16px;
}

.profile-item .label {
  width: 80px;
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 14px;
}

.profile-item .value {
  font-size: 14px;
}

.empty-profile {
  text-align: center;
  padding: 40px 0;
  color: var(--text-secondary);
}

.health-data-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.health-data-item {
  background: #f8fafd;
  border: 1px solid #e8f0f8;
  border-radius: 10px;
  padding: 12px 14px;
}

.health-data-item .data-label {
  display: block;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.health-data-item .data-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.health-data-item .data-unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 2px;
}

.health-data-item .data-none {
  color: var(--text-secondary);
  font-weight: 400;
}

</style>
