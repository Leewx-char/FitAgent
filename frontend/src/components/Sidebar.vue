<template>
  <aside class="sidebar">
    <div class="sidebar-top">
      <div class="sidebar-brand">
        <div class="brand-icon">F</div>
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
import { getSessions, createSession, deleteSession, getMessages } from '@/api/chat'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const profileStore = useProfileStore()
const chatStore = useChatStore()

const showUserMenu = ref(false)
const sessionListRef = ref(null)
const sessionScrollTop = ref(false)

function onSessionScroll() {
  if (sessionListRef.value) {
    sessionScrollTop.value = sessionListRef.value.scrollTop > 4
  }
}

async function loadSessions() {
  try {
    const res = await getSessions()
    chatStore.sessions = res.data
  } catch {
    message.error('加载会话列表失败')
  }
}

async function switchSession(sessionId) {
  if (chatStore.currentSessionId === sessionId) return
  chatStore.currentSessionId = sessionId
  try {
    const res = await getMessages(sessionId)
    chatStore.setMessages(res.data)
  } catch {
    chatStore.clearMessages()
  }
  router.push('/')
}

async function newSession() {
  try {
    const res = await createSession()
    chatStore.sessions.unshift(res.data)
    chatStore.currentSessionId = res.data.id
    chatStore.clearMessages()
    router.push('/')
  } catch {
    message.error('创建会话失败')
  }
}

async function handleDeleteSession(sessionId) {
  try {
    await deleteSession(sessionId)
    chatStore.sessions = chatStore.sessions.filter((s) => s.id !== sessionId)
    if (chatStore.currentSessionId === sessionId) {
      chatStore.currentSessionId = null
      chatStore.clearMessages()
    }
  } catch {
    message.error('删除失败')
  }
}

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
  width: 260px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e2e8f0;
  height: 100vh;
}

.sidebar-top {
  padding: 16px 16px 12px;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.brand-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--primary), #42A5F5);
  color: white;
  font-weight: 700;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 8px;
  margin-bottom: 8px;
}

.nav-item {
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--text-primary);
  text-decoration: none;
  transition: all 0.2s ease;
  position: relative;
}

.nav-item:hover {
  background: var(--primary-light);
  transform: translateX(2px);
}

.nav-item.active {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
  transform: translateX(0);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
  border-top: 1px solid #e2e8f0;
  padding-top: 8px;
  position: relative;
  transition: box-shadow 0.3s ease;
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
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
  transition: background 0.15s;
  font-size: 13px;
  color: var(--text-primary);
  position: relative;
}

.session-item:hover {
  background: #eef2f7;
}

.session-item.active {
  background: #e3edf7;
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
  border-top: 1px solid #e2e8f0;
  padding: 10px 12px;
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
  background: #eef2f7;
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
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
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
  background: #f0f5ff;
}

.menu-danger {
  color: var(--danger);
}

.menu-danger:hover {
  background: #fff0f0;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
