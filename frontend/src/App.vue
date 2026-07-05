<template>
  <n-config-provider :theme-overrides="themeOverrides">
    <n-message-provider>
      <router-view />
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { NConfigProvider, NMessageProvider } from 'naive-ui'

const themeOverrides = {
  common: {
    primaryColor: '#42A5F5',
    primaryColorHover: '#1E88E5',
    primaryColorPressed: '#1565C0',
    primaryColorSuppl: '#64B5F6',
    borderRadius: '8px',
  },
  Button: {
    borderRadiusMedium: '8px',
    borderRadiusLarge: '10px',
  },
  Input: {
    borderRadius: '8px',
  },
  Card: {
    borderRadius: '12px',
  },
}
</script>

<style>
:root {
  --primary: #42A5F5;
  --primary-light: #c6e4fc;
  --primary-dark: #1E88E5;
  --bg-page: #F8FBFF;
  --bg-card: #FFFFFF;
  --text-primary: #2C3E50;
  --text-secondary: #8E99A4;
  --success: #66BB6A;
  --warning: #FFA726;
  --danger: #EF5350;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background-color: var(--bg-page);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image: radial-gradient(circle at 20% 80%, rgba(66, 165, 245, 0.03) 0%, transparent 50%),
                    radial-gradient(circle at 80% 20%, rgba(30, 136, 229, 0.03) 0%, transparent 50%);
  pointer-events: none;
  z-index: 0;
}

a {
  text-decoration: none;
  color: var(--primary);
}
</style>