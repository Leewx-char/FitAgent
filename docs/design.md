---
name: FitAgent
description: 多智能体 AI 运动教练 — 简洁、高效、专业
colors:
  clear-pool-blue: "#42A5F5"
  pool-blue-hover: "#1E88E5"
  pool-blue-pressed: "#1565C0"
  pool-blue-suppl: "#64B5F6"
  pool-blue-light: "#c6e4fc"
  glacier-mist: "#F8FBFF"
  bright-white: "#FFFFFF"
  steel-ink: "#2C3E50"
  soft-slate: "#8E99A4"
  turf-green: "#66BB6A"
  caution-amber: "#FFA726"
  alert-red: "#EF5350"
typography:
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.6
  heading:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.3
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
rounded:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "12px"
  bubble: "16px"
  full: "50%"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  2xl: "24px"
  3xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.clear-pool-blue}"
    textColor: "{colors.bright-white}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.pool-blue-hover}"
  sidebar-nav:
    backgroundColor: "{colors.bright-white}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
  card:
    backgroundColor: "{colors.bright-white}"
    rounded: "{rounded.xl}"
    padding: "20px"
  input:
    backgroundColor: "{colors.bright-white}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.md}"
  chat-bubble-user:
    backgroundColor: "{colors.pool-blue-light}"
    textColor: "{colors.steel-ink}"
    rounded: "16px 16px 4px 16px"
    padding: "10px 16px"
  chat-bubble-assistant:
    backgroundColor: "{colors.bright-white}"
    textColor: "{colors.steel-ink}"
    rounded: "4px 16px 16px 16px"
    padding: "10px 16px"
---

# Design System: FitAgent

## 1. Overview

**Creative North Star: "The Training Floor"**

一间洁净、实用、目的明确的训练空间。白墙映着天光，哑光橡胶地面不反射多余光线。没有装饰性的镜面，没有多余的器材散落地面。每一件设备都有明确的位置，每一个动作都有清晰的轨迹。这就是 FitAgent 的视觉哲学：**空间由功能定义，美感来自克制**。

密度偏低，留白充沛。信息层级通过字号和字重区分，不用线条和边框来划分区域。色彩的角色是信号灯，不是装饰画——Clear Pool Blue 的出现意味着"这里有可操作的内容"，它的稀缺性本身就是信息。

**Key Characteristics:**
- 高留白、低密度：每屏只承载一个清晰的信息任务
- 色彩信号化：蓝色 = 可交互；绿色/琥珀/红 = 状态信号；中性色 = 承载层
- 无阴影静止、浅阴影激活：Flat by default 的高程模型
- 系统字体栈：跨平台原生渲染，零字体加载延迟

## 2. Colors

调色板以单一主色驱动，辅以严格的中性色阶梯和三个状态信号色。这是 product.md 中"简洁"原则的直接体现：不超过10个语义色。

### Primary

- **Clear Pool Blue** (#42A5F5)：主强调色。用于按钮背景、链接、选中态、聚焦环。≤10% 屏幕覆盖率。
- **Pool Blue Hover** (#1E88E5)：悬停态。加深 15%。
- **Pool Blue Pressed** (#1565C0)：按下态。加深 25%。
- **Pool Blue Suppl** (#64B5F6)：辅助蓝。用于大面积的浅色提示区背景。
- **Pool Blue Light** (#c6e4fc)：聊天用户气泡背景、tag 底色。最浅的蓝色层。

### Neutral

- **Steel Ink** (#2C3E50)：正文颜色。偏冷的深蓝灰，对比度 10.3:1 对白色。不用纯黑，避免刺眼。
- **Soft Slate** (#8E99A4)：辅助文字、占位符、未激活图标。对比度 3.8:1 对白色——注意这只是辅助信息，主文字必须用 Steel Ink。
- **Glacier Mist** (#F8FBFF)：页面背景。极浅的冷调白，带 0.005 OKLCH chroma 向蓝色偏。不是暖调奶油白（anti-cream）。
- **Bright White** (#FFFFFF)：卡片和容器背景。与 Glacier Mist 形成 2-3% 的亮度差，足够区分层级。

### Status Signals

- **Turf Green** (#66BB6A)：成功 / 正常。
- **Caution Amber** (#FFA726)：警告 / 注意。
- **Alert Red** (#EF5350)：错误 / 危险。

### Named Rules

**The One Voice Rule.** 任何屏幕上，Clear Pool Blue 的覆盖率不超过 10%。蓝色是信号，不是氛围。如果整个页面看起来是蓝色的，它已经失效了。

**The Signal Inversion Rule.** 当 Clear Pool Blue 作为背景时（如主按钮），文字必须变为 Bright White。当它作为文字时，背景必须是无色中性。

**The Anti-Cream Rule.** 页面背景必须是冷调近白（Glacier Mist），而不是暖调奶油色、沙色、米色。这是 product.md 中"拒绝平庸 SaaS"的直接体现——暖调 body bg 是 2025-2026 AI 产出的最大信号。

## 3. Typography

**Font Stack:** 系统原生字体栈。`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`。

**Character:** 无个性即个性。系统字体在每个平台都是最锐利、最可靠的渲染选择。零额外加载，零 FOUT，零字体版权问题。这是"高效"原则的极致——字体不应该是前端需要关心的东西。

### Hierarchy

- **Heading** (600 weight, 16-18px, line-height 1.3)：页面标题、区块标题、卡片标题。只有两个字重差异（400→600），不引入多余的视觉噪音。
- **Body** (400 weight, 14px, line-height 1.6)：正文、对话内容、运动记录列表。最大行宽 75ch。
- **Label** (500 weight, 13px, line-height 1.4)：标签、统计指标名称、次要元数据。
- **Caption** (400 weight, 11-12px)：辅助注释、时间戳、版本号。

### Named Rules

**The Two-Weight Rule.** 只使用两个字重：400（正文）和 600（标题）。不用 500/700/800/900 来制造伪层级。信息的层级通过空间和位置来表达，不通过字重调色板。

**The No-Font Rule.** 永不引入 Web Font。加载第三方字体所增加的延迟，对于一款承诺"高效"的工具是不可接受的。系统字体已经足够好了。

## 4. Elevation

这个系统是 **Flat by default**。

静止状态下，没有任何阴影。所有表面在 z=0 平面共存。层级通过背景色差区分：Bright White 卡片浮在 Glacier Mist 页面上方，仅凭 2-3% 的亮度差就足够建立深度感知。

阴影仅作为**状态响应**出现：悬停或聚焦时，元素获得极浅的 lift shadow（0 2px 8px rgba(0,0,0,0.06)）。这是功能性的，不是装饰性的——它告诉用户"这个东西现在可以交互了"，然后在交互结束或失焦后消失。

### Shadow Vocabulary

- **Lift** (`0 2px 8px rgba(0,0,0,0.06)`)：悬停浮起。仅限可点击的卡片和按钮。
- **Focus Ring** (`0 0 0 2px {primary}`)：键盘聚焦环。2px Clear Pool Blue 环形。

### Named Rules

**The Flat-By-Default Rule.** 表面静态时无阴影。阴影只作为对状态的响应出现（悬停、聚焦）。如果它看起来像个 2014 年 App 的卡片，阴影太深且模糊太大。

**The 8px Limit Rule.** 任何阴影的 blur 不超过 8px，spread 不超过 0。超过这个值的阴影看起来不专业。

## 5. Components

### Buttons

- **Shape:** 圆角 8px（默认），10px（大按钮）。方中带圆，不追求 pill。
- **Primary:** Clear Pool Blue 背景 (#42A5F5)，Bright White 文字，padding 10px 20px。字重 600。
- **Hover:** 背景加深至 Pool Blue Hover (#1E88E5)。出现 Lift 阴影。
- **Focus:** 2px 蓝色聚焦环，无偏移。
- **Ghost:** 透明背景，Clear Pool Blue 文字，悬停时透明蓝底色。

### Sidebar Navigation

- 260px 宽，Bright White 背景，无阴影。
- 导航项 padding 10px 12px，8px 圆角。
- 默认：Steel Ink 文字。悬停：Pool Blue Light 底色。
- 选中态：Clear Pool Blue 底色，Bright White 文字。左侧 3px 标识线（不违规 side-stripe 禁止——这不是装饰性条纹，而是选中态的完整标识）。

### Chat Bubbles

- **用户气泡:** Pool Blue Light 底色 (#c6e4fc)，16px 圆角，右下角收为 4px。对齐右侧。
- **AI 气泡:** Bright White 底色，16px 圆角，左下角收为 4px。左侧对齐。
- 内边距 10px 16px，body 字号 14px，最大行宽 65ch。

### Data Cards (Dashboard)

- 12px 圆角，Bright White 背景，padding 20px。
- 静态无阴影。悬停时 Lift 阴影。
- 统计值: 28px / 700 weight / Steel Ink。
- 标签: 13px / 400 weight / Soft Slate。

### Chips / Tags

- 4px 圆角，padding 2px 6px。
- 蓝色系：Pool Blue Light 底色，Clear Pool Blue 文字。
- 状态色：Turf Green / Caution Amber / Alert Red 浅底色。

### Inputs / Fields

- 8px 圆角，Bright White 背景，1px Soft Slate 边框。
- 聚焦：边框变为 Clear Pool Blue，出现聚焦环。
- 占位符必须用 Soft Slate（不要更浅的灰色——对比度不足）。

## 6. Do's and Don'ts

### Do:

- **Do** 用留白组织信息，不用分割线。当两个区域需要区分时，增加间距，不画线。
- **Do** 让蓝色 ≤10% 单屏覆盖率。如果一屏里超过 3 个蓝色元素，重新审视。
- **Do** 正文永远用 Steel Ink (#2C3E50)，不用柔和灰假装优雅。
- **Do** 图表数据点从 0 基线开始。不要截断 Y 轴来夸大变化。
- **Do** 用 `text-wrap: balance` 在标题上，`text-wrap: pretty` 在长文本上。
- **Do** 每个动画都要有 `@media (prefers-reduced-motion: reduce)` 的降级方案。

### Don't:

- **Don't** 使用 `border-left` 或 `border-right` >1px 做彩色条纹强调。
- **Don't** 用 `background-clip: text` 做渐变文字。
- **Don't** 嵌套卡片。一个卡片就够了；两层卡片永远不对。
- **Don't** 使用 >16px 的圆角在卡片上。12px 是上限。
- **Don't** 堆砌 hero metric 模板（大数字 + 小标签 + 蓝色渐变点缀）。这是 SaaS 陈词滥调。
- **Don't** 在 body 背景使用暖调奶油色、沙色、米色。反例参照 product.md 的"拒绝平庸 SaaS"。
- **Don't** 用玻璃拟态（backdrop-filter blur）做默认卡片风格。极罕见且有意义时才用，否则不用。
- **Don't** 在页面顶部加小而全大写加宽字距的 "ABOUT" "FEATURES" "WORKOUTS" eyebrow。每屏一个 eyebrow 是 AI 语法。
- **Don't** 让 AI 思考面板之外的任何区域出现 `backdrop-filter: blur()`。透明模糊是 AI 廉价感的第一信号。

### The Quick SMELL Test

如果一屏里有以下任一项，退回重做：
- 背景是暖调米白（OKLCH L 0.84-0.97, C < 0.06, hue 40-100）
- 三个以上阴影不同的卡片并排
- 蓝色元素超过屏幕面积的 20%
- 正文是灰色（对比度 <4.5:1）而非 Steel Ink
- 出现了 gradient text 或 glassmorphism
