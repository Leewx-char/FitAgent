这是一个基于RAG的agent运动教练项目。目前项目需要优化性重构，你应该按照这个顺序检查，项目结构，代码规范，遵循原则。

# 项目架构模板
可以把以下结构当作参考结构，做灵活判断：
```
project/
├── README.md
├── AGENTS.md
├── LICENSE
├── .gitignore
├── docker-compose.yml
├── docs/
│   ├── architecture.md
│   └── deployment.md
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── public/
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── store/
│       └── utils/
└── backend/
    ├── Dockerfile
    ├── pyproject.toml
    ├── src/
    │   ├── api/
    │   ├── core/
    │   └── models/
    └── tests/
```
## 关键边界：
- 前端状态管理不要直接绑定后端数据库结构。
- 后端 API contract 应有 schema 或类型定义。
- 前后端共享类型时，优先从 OpenAPI、JSON Schema 或生成工具派生。
# 架构设计原则

## 关注点分离

```
API -> Service -> Repository -> Database / External System
```
上层可以调用下层，下层不能反向依赖上层。
## 可测试性

- 每个模块可独立测试。
- 外部依赖可 mock。
- 核心业务逻辑不依赖 CLI、HTTP、数据库连接对象。
## 可配置性

```
环境变量 > 配置文件 > 默认值
```

配置与代码分离，敏感配置不得提交。
## 可维护性

- 文件名表达职责。
- 目录边界表达模块边界。
- 业务逻辑、平台适配、第三方依赖隔离。

## 版本控制友好

- `data/`、`logs/`、`models/` 默认加入 `.gitignore`。
- 大文件不进 Git，必要时使用对象存储、Release、DVC 或外部数据源。
- 提交源代码、配置示例、文档、测试和小型 fixture。
## 最低门禁

### 代码门禁

- 语法检查通过。
- 单元测试覆盖核心业务。
- lint/format 有明确命令。
- 关键路径纳入版本控制。
### 结构门禁
- 不允许临时脚本成为长期入口。
- 不允许同一职责存在多套实现入口。
- 不允许外部 SDK 类型污染核心业务模型。
- 数据服务不允许新逻辑回流 legacy 壳。
### 运行门禁

- 服务能按 README 启动。
- `stop -> start -> status -> restart -> status` 可验证。
- 日志能证明真实执行源。
- PID、log、run metadata 可追踪。
### 数据门禁

- 每个 active dataset 至少有 contract + writer + collect 或 backfill。
- resource_id 与 registry 一致。
- 质量检查至少覆盖空写、重复写、时间边界、幂等。
### 文档门禁

- README 说明项目定位、安装、启动、测试和目录结构。
- AGENTS 说明 AI Agent 修改边界、验证命令和禁止事项。
- `.env.example` 说明必要配置。
- 架构变化同步更新文档。
# 代码规范
## 模块化编程

- 将代码分割成小的、可重用的模块或函数，每个模块负责只做一件事。
- 使用明确的模块结构和目录结构来组织代码，使代码更易于导航。

## 命名规范

- 使用有意义且一致的命名规范，以便从名称就能理解变量、函数、类的作用。
- 遵循命名约定，如驼峰命名（CamelCase）用于类名，蛇形命名（snake_case）用于函数名和变量名。

## 代码注释

- 为复杂的代码段添加注释，解释代码的功能和逻辑。
- 使用块注释（/_..._/）和行注释（//）来区分不同类型的注释。
## 代码格式化

- 使用一致的代码风格和格式化规则，使用工具如 Prettier 或 Black 自动格式化代码。
- 使用空行、缩进和空格来增加代码的可读性。
## 文档

### 文档字符串

- 在每个模块、类和函数的开头使用文档字符串，解释其用途、参数和返回值。
- 选择一致的文档字符串格式，如 Google Style、NumPy/SciPy Style 或 Sphinx Style。
### 自动化文档生成

- 使用工具如 Sphinx、Doxygen 或 JSDoc 从代码中自动生成文档。
- 保持文档和代码同步，确保文档始终是最新的。
## README 文件

- 在每个项目的根目录中包含一个详细的 README 文件，解释项目目的、安装步骤、用法和示例。
- 使用 Markdown 语法编写 README 文件，使其易于阅读和维护。
# 遵循原则
##  KISS 原则（保持简单）
减少复杂度、魔法代码、晦涩技巧。
## DRY 原则（不要重复）
用函数、类、模块复用逻辑，不要复制粘贴。

