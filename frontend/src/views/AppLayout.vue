<template>
  <div class="app-layout">
    <Sidebar />
    <main class="app-main">
      <router-view v-slot="{ Component }">
        <transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import Sidebar from '@/components/Sidebar.vue'
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.app-main {
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow-y: auto;
  background: var(--bg-page);
}

.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity 0.18s ease-out;
}

.page-fade-enter-from,
.page-fade-leave-to {
  opacity: 0;
}

@media (max-width: 760px) {
  .app-layout {
    height: auto;
    min-height: 100vh;
    flex-direction: column;
    overflow: visible;
  }
}
</style>
