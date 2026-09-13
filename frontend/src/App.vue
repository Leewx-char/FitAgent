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
    primaryColor: '#3B6E8F',
    primaryColorHover: '#28536F',
    primaryColorPressed: '#1F455D',
    primaryColorSuppl: '#6B94AC',
    successColor: '#276749',
    warningColor: '#8A5D12',
    errorColor: '#A33B32',
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
  --primary: #3B6E8F;
  --primary-light: #EAF1F5;
  --primary-dark: #28536F;
  --bg-page: #F7F8FA;
  --bg-card: #FFFFFF;
  --text-primary: #1F2933;
  --text-secondary: #5C6874;
  --text-quiet: #7B8794;
  --border: #DCE2E7;
  --success: #276749;
  --success-light: #E8F3ED;
  --warning: #8A5D12;
  --warning-light: #FFF8E7;
  --danger: #A33B32;
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

a {
  text-decoration: none;
  color: var(--primary);
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
</style>
