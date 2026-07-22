# FitAgent 前端

基于 Vue 3 + Vite + Naive UI 构建的运动教练应用前端。

## 启动

```bash
npm install
npm run dev
```

浏览器打开 http://localhost:5173

## 技术栈

- Vue 3 (Composition API + `<script setup>`)
- Vite
- Pinia (状态管理)
- Naive UI (组件库)
- vue-router (路由)
- ECharts (图表)
- marked + DOMPurify (Markdown 渲染)
- Axios (HTTP 客户端)

## 项目结构

```
src/
├── main.js          # 入口
├── App.vue          # 根组件
├── router/          # 路由配置
├── stores/          # Pinia 状态管理
├── api/             # Axios 封装
├── views/           # 页面组件
├── components/      # 通用组件
└── assets/          # 静态资源
```
