<template>
  <section class="memory-page">
    <header class="page-header">
      <div>
        <h1>我的记忆</h1>
        <p>聊天中识别的个人信息需要你确认后，才会用于后续个性化建议。</p>
      </div>
      <n-button :loading="loading" secondary @click="load">刷新</n-button>
    </header>

    <n-alert type="info" :show-icon="false" class="notice">
      记忆只保存你明确说过或主动添加的信息；你可以随时撤销，过期后也不会再提供给 Agent。
    </n-alert>

    <div class="memory-grid">
      <n-card title="待确认" size="small" class="memory-card">
        <template v-if="proposed.length">
          <div v-for="item in proposed" :key="item.id" class="memory-row">
            <div>
              <strong>{{ item.display_text }}</strong>
              <small>{{ expiresText(item) }}</small>
            </div>
            <div class="actions">
              <n-button size="small" type="primary" @click="confirm(item)">确认</n-button>
              <n-button size="small" tertiary @click="revoke(item)">忽略</n-button>
            </div>
          </div>
        </template>
        <n-empty v-else size="small" description="暂无待确认记忆" />
      </n-card>

      <n-card title="已确认" size="small" class="memory-card">
        <template v-if="confirmed.length">
          <div v-for="item in confirmed" :key="item.id" class="memory-row">
            <div>
              <strong>{{ item.display_text }}</strong>
              <small>{{ expiresText(item) }}</small>
            </div>
            <n-button size="small" tertiary type="error" @click="revoke(item)">撤销</n-button>
          </div>
        </template>
        <n-empty v-else size="small" description="尚未确认任何跨会话记忆" />
      </n-card>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NEmpty, useMessage } from 'naive-ui'
import { getErrorMessage } from '@/api'
import { getMemories, revokeMemory, updateMemory } from '@/api/memory'

const message = useMessage()
const memories = ref([])
const loading = ref(false)
const proposed = computed(() => memories.value.filter((item) => item.status === 'proposed'))
const confirmed = computed(() => memories.value.filter((item) => item.status === 'confirmed'))

/** 将记忆到期时间格式化为页面显示文案。 */
function expiresText(item) {
  return item.expires_at
    ? `到期：${new Date(item.expires_at).toLocaleDateString('zh-CN')}`
    : '不会自动到期'
}

/** 读取记忆列表，供待确认与已确认分组展示。 */
async function load() {
  loading.value = true
  try {
    const response = await getMemories()
    memories.value = response.data.data
  } catch (error) {
    message.error(getErrorMessage(error, '读取记忆失败'))
  } finally {
    loading.value = false
  }
}

/** 将待确认记忆设为已确认，并刷新列表。 */
async function confirm(item) {
  try {
    await updateMemory(item.id, { status: 'confirmed' })
    message.success('已确认，将用于后续个性化建议')
    await load()
  } catch (error) {
    message.error(getErrorMessage(error, '确认失败'))
  }
}

/** 撤销指定记忆，并刷新列表反映最新状态。 */
async function revoke(item) {
  try {
    await revokeMemory(item.id)
    message.success('已撤销')
    await load()
  } catch (error) {
    message.error(getErrorMessage(error, '撤销失败'))
  }
}

onMounted(load)
</script>

<style scoped>
.memory-page { max-width: 1040px; margin: 0 auto; padding: 28px 24px; }
.page-header { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }
h1 { margin: 0 0 8px; font-size: 23px; }
p { margin: 0; color: var(--text-secondary); font-size: 14px; }
.notice { margin-bottom: 18px; }
.memory-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.memory-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 0; border-bottom: 1px solid #edf1f5; }
.memory-row:last-child { border-bottom: 0; }
.memory-row strong, .memory-row small { display: block; }
.memory-row small { color: var(--text-secondary); font-size: 12px; margin-top: 5px; }
.actions { display: flex; gap: 8px; flex-shrink: 0; }
@media (max-width: 720px) { .memory-page { padding: 18px 14px; } .memory-grid { grid-template-columns: 1fr; } }
</style>
