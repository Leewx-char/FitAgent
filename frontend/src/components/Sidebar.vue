<template>
  <aside class="sidebar">
    <div class="sidebar-top">
      <div class="sidebar-brand">
        <span class="brand-name">FitAgent</span>
      </div>
      <n-button type="primary" ghost block size="small" @click="newSession">
        + 新建对话
      </n-button>
    </div>

    <nav class="sidebar-nav">
      <router-link to="/" class="nav-item" :class="{ active: $route.name === 'Chat' }">
        对话
      </router-link>
      <router-link to="/dashboard" class="nav-item" :class="{ active: $route.name === 'Dashboard' }">
        数据面板
      </router-link>
      <router-link to="/training-plan" class="nav-item" :class="{ active: $route.name === 'TrainingPlan' }">
        本周计划
      </router-link>
      <router-link to="/memory" class="nav-item" :class="{ active: $route.name === 'Memory' }">
        我的记忆
      </router-link>
      <router-link to="/profile" class="nav-item" :class="{ active: $route.name === 'Profile' }">
        档案
      </router-link>
    </nav>

    <div class="session-list" ref="sessionListRef" @scroll="onSessionScroll" :class="{ 'scrolled': sessionScrollTop }">
      <div
        v-for="session in chatStore.sessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.id === chatStore.currentSessionId }"
        @click="switchSession(session.id)"
      >
        <span class="session-dot">●</span>
        <span class="session-title">{{ session.title || '新对话' }}</span>
        <span class="session-delete" @click.stop="handleDeleteSession(session.id)">×</span>
      </div>
      <div v-if="chatStore.sessions.length === 0" class="session-empty">
        暂无对话
      </div>
    </div>

    <div class="sidebar-bottom">
      <div class="sidebar-profile" v-if="profileStore.profile" @click="showUserMenu = !showUserMenu">
        <div class="profile-avatar">{{ (authStore.user?.username || '用')[0] }}</div>
        <div class="profile-info">
          <div class="profile-name">{{ authStore.user?.username || '用户' }}</div>
          <div class="profile-meta">{{ profileStore.profile.goal }} · {{ profileStore.profile.experience }}</div>
        </div>
        <span class="profile-arrow">›</span>
      </div>
      <div v-else class="sidebar-profile" @click="$router.push('/onboarding')">
        <div class="profile-avatar">?</div>
        <div class="profile-info">
          <div class="profile-name">完善档案</div>
          <div class="profile-meta">点击填写</div>
        </div>
        <span class="profile-arrow">›</span>
      </div>
      <transition name="fade">
        <div v-if="showUserMenu" class="user-menu">
          <div class="menu-item" @click="showUserMenu = false; $router.push('/onboarding')">更新档案</div>
          <div class="menu-item menu-danger" @click="handleLogout">退出登录</div>
        </div>
      </transition>
    </div>
  </aside>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useProfileStore } from '@/stores/profile'
import { useChatStore } from '@/stores/chat'
import { getErrorMessage } from '@/api'
import { getSessions, createSession, deleteSession, getMessages } from '@/api/chat'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const profileStore = useProfileStore()
const chatStore = useChatStore()

const showUserMenu = ref(false)
const sessionListRef = ref(null)
const sessionScrollTop = ref(false)

/** 根据会话列表的滚动距离切换顶部阴影。 */
function onSessionScroll() {
  if (sessionListRef.value) {
    sessionScrollTop.value = sessionListRef.value.scrollTop > 4
  }
}

/** 加载服务端会话列表，供侧边栏展示和切换。 */
async function loadSessions() {
  try {
    const res = await getSessions()
    chatStore.sessions = res.data.data
  } catch (error) {
    message.error(getErrorMessage(error, '加载会话列表失败'))
  }
}

/** 切换到所选会话并加载其历史消息，随后返回聊天页。 */
async function switchSession(sessionId) {
  if (chatStore.currentSessionId === sessionId) return
  chatStore.currentSessionId = sessionId
  try {
    const res = await getMessages(sessionId)
    chatStore.setMessages(res.data.data)
  } catch {
    chatStore.clearMessages()
  }
  router.push('/')
}

/** 创建空会话，将其置为当前会话并清空消息视图。 */
async function newSession() {
  try {
    const res = await createSession()
    chatStore.sessions.unshift(res.data.data)
    chatStore.currentSessionId = res.data.data.id
    chatStore.clearMessages()
    router.push('/')
  } catch (error) {
    message.error(getErrorMessage(error, '创建会话失败'))
  }
}

/** 删除会话；若删除的是当前会话，同时清除当前消息。 */
async function handleDeleteSession(sessionId) {
  try {
    await deleteSession(sessionId)
    chatStore.sessions = chatStore.sessions.filter((s) => s.id !== sessionId)
    if (chatStore.currentSessionId === sessionId) {
      chatStore.currentSessionId = null
      chatStore.clearMessages()
    }
  } catch (error) {
    message.error(getErrorMessage(error, '删除失败'))
  }
}

/** 退出登录并导航到登录页。 */
function handleLogout() {
  authStore.logout()
  router.push('/login')
}

onMounted(async () => {
  await profileStore.fetchProfile()
  await loadSessions()
})
</script>

<style scoped>
.sidebar {
  width: 232px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--border);
  height: 100vh;
}

.sidebar-top {
  padding: 24px 16px 14px;
}

.sidebar-brand {
  margin: 0 8px 20px;
}

.brand-name {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-primary);
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 8px;
  margin-bottom: 14px;
}

.nav-item {
  padding: 9px 12px;
  border-radius: 8px;
  font-size: 14px;
  color: var(--text-secondary);
  text-decoration: none;
  transition: background-color 0.16s ease, color 0.16s ease;
}

.nav-item:hover {
  background: #F1F4F6;
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--primary-light);
  color: var(--primary-dark);
  font-weight: 600;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
  position: relative;
  transition: box-shadow 0.16s ease;
}

.session-list.scrolled {
  box-shadow: inset 0 6px 6px -6px rgba(0, 0, 0, 0.06);
}

.session-empty {
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  padding: 24px 0;
}

.session-item {
  padding: 9px 12px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
  transition: background-color 0.16s ease;
  font-size: 13px;
  color: var(--text-primary);
  position: relative;
}

.session-item:hover {
  background: #F1F4F6;
}

.session-item.active {
  background: var(--primary-light);
}

.session-dot {
  font-size: 8px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.session-item.active .session-dot {
  color: var(--primary);
}

.session-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.session-delete {
  opacity: 0;
  font-size: 16px;
  color: var(--text-secondary);
  flex-shrink: 0;
  width: 20px;
  text-align: center;
  line-height: 1;
  transition: opacity 0.15s;
}

.session-item:hover .session-delete {
  opacity: 1;
}

.session-delete:hover {
  color: var(--danger);
}

.sidebar-bottom {
  border-top: 1px solid var(--border);
  padding: 12px;
  position: relative;
}

.sidebar-profile {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.sidebar-profile:hover {
  background: #F1F4F6;
}

.profile-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--primary-light);
  color: var(--primary-dark);
  font-weight: 600;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.profile-info {
  flex: 1;
  min-width: 0;
}

.profile-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile-meta {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 1px;
}

.profile-arrow {
  font-size: 16px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.user-menu {
  position: absolute;
  bottom: 100%;
  left: 8px;
  right: 8px;
  background: white;
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(31, 41, 51, 0.1);
  margin-bottom: 6px;
  overflow: hidden;
}

.menu-item {
  padding: 10px 16px;
  font-size: 13px;
  color: var(--text-primary);
  cursor: pointer;
  transition: background 0.15s;
}

.menu-item:hover {
  background: #F1F4F6;
}

.menu-danger {
  color: var(--danger);
}

.menu-danger:hover {
  background: #FFF3F2;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 760px) {
  .sidebar {
    width: 100%;
    height: auto;
    flex-direction: row;
    align-items: center;
    border-right: 0;
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    gap: 14px;
  }

  .sidebar-top {
    padding: 0;
  }

  .sidebar-brand {
    margin: 0;
  }

  .brand-name {
    font-size: 16px;
  }

  .sidebar-top :deep(.n-button),
  .session-list,
  .sidebar-bottom {
    display: none;
  }

  .sidebar-nav {
    flex: 1;
    flex-direction: row;
    overflow-x: auto;
    margin: 0;
    padding: 0;
    gap: 2px;
  }

  .nav-item {
    flex: 0 0 auto;
    padding: 7px 8px;
    font-size: 13px;
    white-space: nowrap;
  }
}
</style>
