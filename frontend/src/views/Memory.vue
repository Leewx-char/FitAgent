<template>
  <section class="memory-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">个性化设置</p>
        <h1>我的记忆</h1>
        <p>只有你确认的信息才会进入后续训练建议；你始终可以查看和撤销。</p>
      </div>
      <n-button secondary :loading="loading" @click="load">刷新</n-button>
    </header>

    <div class="memory-layout">
      <main class="memory-content">
        <section v-if="proposed.length" class="proposed-section" aria-labelledby="proposed-title">
          <div class="section-heading">
            <div>
              <p class="eyebrow">需要你的决定</p>
              <h2 id="proposed-title">待确认</h2>
            </div>
            <span>{{ proposed.length }} 条</span>
          </div>
          <article v-for="item in proposed" :key="item.id" class="memory-row proposed-row">
            <div>
              <strong>{{ item.display_text }}</strong>
              <small>{{ expiresText(item) }}</small>
            </div>
            <div class="actions">
              <n-button size="small" type="primary" @click="confirm(item)">确认</n-button>
              <n-button size="small" tertiary @click="revoke(item)">忽略</n-button>
            </div>
          </article>
        </section>

        <section class="confirmed-section" aria-labelledby="confirmed-title">
          <div class="section-heading">
            <div>
              <p class="eyebrow">当前生效</p>
              <h2 id="confirmed-title">已确认记忆</h2>
            </div>
            <span>{{ confirmed.length }} 条</span>
          </div>
          <div v-if="confirmed.length" class="memory-table" role="table" aria-label="已确认的跨会话记忆">
            <div v-for="item in confirmed" :key="item.id" class="memory-row confirmed-row" role="row">
              <div role="cell">
                <strong>{{ item.display_text }}</strong>
                <small>{{ expiresText(item) }}</small>
              </div>
              <span class="active-tag" role="cell">已生效</span>
              <n-button size="small" tertiary type="error" role="cell" @click="revoke(item)">撤销</n-button>
            </div>
          </div>
          <n-empty v-else description="尚未确认任何跨会话记忆" class="memory-empty" />
        </section>
      </main>

      <aside class="memory-aside">
        <p class="eyebrow">你掌握控制权</p>
        <h2>记忆如何帮助训练</h2>
        <p>例如目标、可用器械和持续反馈，可以让建议更贴合你的训练场景。</p>
        <div class="aside-rule"></div>
        <strong>不会自动保存</strong>
        <p>聊天中识别到的信息先列为待确认；未确认内容不会作为长期记忆使用。</p>
        <strong>随时撤销</strong>
        <p>撤销后，信息不再参与后续的个性化建议。</p>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { NButton, NEmpty, useMessage } from 'naive-ui'
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
.memory-page { max-width: 1120px; margin: 0 auto; padding: 40px 44px 56px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 24px; }
.eyebrow { color: var(--text-quiet); font-size: 12px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; }
h1 { margin: 4px 0 8px; color: var(--text-primary); font-size: 28px; letter-spacing: -.035em; }
.page-header p, .memory-aside p { margin: 0; color: var(--text-secondary); font-size: 14px; line-height: 1.7; }
.memory-layout { display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 16px; }
.memory-content, .memory-aside { border: 1px solid var(--border); border-radius: 10px; background: var(--bg-card); }
.proposed-section, .confirmed-section { padding: 24px 28px; }
.proposed-section { border-bottom: 1px solid var(--border); background: #FCF9F0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 8px; }
.section-heading h2, .memory-aside h2 { margin: 6px 0 8px; color: var(--text-primary); font-size: 18px; letter-spacing: -.02em; }
.section-heading > span { color: var(--text-quiet); font-size: 13px; }
.memory-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 0; border-top: 1px solid var(--border); }
.memory-row strong, .memory-row small { display: block; }
.memory-row strong { color: var(--text-primary); font-size: 14px; line-height: 1.5; }
.memory-row small { margin-top: 5px; color: var(--text-secondary); font-size: 12px; }
.actions { display: flex; gap: 8px; flex: 0 0 auto; }
.confirmed-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; }
.active-tag { padding: 3px 8px; border-radius: 999px; background: var(--success-light); color: var(--success); font-size: 12px; font-weight: 650; }
.memory-empty { padding: 40px 0 20px; }
.memory-aside { align-self: start; padding: 28px; background: #F1F5F7; }
.memory-aside strong { display: block; margin: 18px 0 6px; color: var(--text-primary); font-size: 14px; }
.aside-rule { height: 1px; margin: 22px 0; background: var(--border); }
@media (max-width: 900px) { .memory-page { padding: 32px 28px 48px; } .memory-layout { grid-template-columns: 1fr; } .memory-aside { display: none; } }
@media (max-width: 700px) { .memory-page { padding: 24px 16px 40px; } .page-header { flex-direction: column; } .proposed-section, .confirmed-section { padding-left: 18px; padding-right: 18px; } .memory-row { align-items: flex-start; flex-direction: column; } .confirmed-row { display: flex; } .actions { flex-wrap: wrap; } }
</style>
