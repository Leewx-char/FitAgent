# 01 - 基础设施收尾与健康文档识别加固

> **状态**：已完成（2026-07-23）
>
> **范围**：工程依赖基线、数据库迁移、健康文档识别、上传提醒与前端确认流程。
> **迁移前提**：初始迁移仅适用于可重建的空开发 MySQL 数据库，不承担生产存量数据回填。

---

## 一、工程基线

`pyproject.toml` 是唯一的 Python 依赖入口：它定义 setuptools 构建后端、`app` 包发现、
运行依赖以及 `dev` extra（pytest、Ruff）。不再保留
`requirements.txt` 兼容副本，以避免两份依赖清单漂移。

Windows 与 macOS/Linux 的 libmagic 实现不同，依赖按平台安装：

- 非 Windows：`python-magic`；
- Windows：`python-magic-bin`，不与前者同时安装，避免同名 `magic` 包互相覆盖。

开发环境和运行环境命令分别为：

```bash
python -m pip install -e ".[dev]"
python -m pip install .
```

后端质量门禁：

```bash
ruff format --check app
ruff check app
pytest app/tests
```

`app/__init__.py` 已加入，使应用可以被安装、导入与迁移脚本稳定发现。

## 二、数据库初始化与迁移

Alembic 是唯一表结构创建入口。`alembic/versions/20260723_01_initial_schema.py` 为当前
ORM 模型生成了空开发数据库的初始 schema。

应用启动不再运行 `Base.metadata.create_all()`。`AUTO_CREATE_DATABASE` 仅允许本地开发时
创建**空数据库**，不创建任何表；生产环境必须设为 `false` 并由部署流程预先创建数据库。

```bash
# .env 配置好 MySQL 后；只在本地开发且 AUTO_CREATE_DATABASE=true 时创建数据库
python -c "from app.core.database import ensure_database_exists; ensure_database_exists()"

# 所有环境均使用迁移创建/更新表结构
alembic upgrade head
```

后续模型变更必须新增一个独立迁移，并先在可还原的开发数据库验证升级与回滚：

```bash
alembic revision --autogenerate -m "describe_schema_change"
alembic upgrade head
```

## 三、模型配置与健康文档处理

`config/models.yml` 的启动必填配置如下：

```yaml
chat_model_name: deepseek-v4-pro
embedding_model_name: text-embedding-v1
vl_primary_model_name: qwen-vl-plus
vl_fallback_model_name: qwen-vl-max
```

工厂层按 `primary` / `fallback` 显式选择视觉模型，不再读取不存在的 `vl_model_name`。
模型能力边界依据 [DashScope Qwen API 文档](https://help.aliyun.com/en/model-studio/qwen-api-via-dashscope)：

- 可选文字 PDF：把文本交给聊天模型提取；
- 扫描 PDF 和图片：每页使用 `qwen-vl-plus`；
- 单页模型调用异常、JSON 无法解析或 Pydantic 契约校验失败时：以更高 DPI 仅重试该页，
  使用 `qwen-vl-max`；
- 扫描 PDF 默认处理全部页面，`HEALTH_DOCUMENT_MAX_PAGES` 默认上限为 20，超限在调用
  外部模型前拒绝。

模型与上传接口统一使用 `{code, messages, data}` 信封：`code=0` 表示成功，`messages`
承载提示和警告，成功时 `data` 包含 `metrics` 与 `conflicts`；失败时 `data` 为 `null`。
多页中同一指标的不同值不会自动覆盖：后端在 `conflicts` 中返回页码和候选指标，前端要求
用户选择或编辑后才能保存。提取结果只保留十项健身相关指标及其单位；提示词和确认页均
禁止诊断、治疗建议或医疗结论。

临时原文件与 PDF 渲染图片会在处理结束时清理。测试使用仓库内的
`app/tests/fixtures/text_health_report.pdf`，不依赖运行时生成 PDF 或 `fpdf`。

## 四、上传提醒与确认流程

本项目不面向大规模生产部署，因此不维护服务端同意审计表或独立隐私接口。用户点击上传时，
前端以简短确认框说明：文件会发送至 DashScope 提取指标、临时文件会在处理后清理。
用户可以取消本次选择；后端不会持久化授权状态。

`messages` 会标注需要复核的页码和原因，上传响应仅返回指标与冲突候选；用户仅在确认页
编辑/选择后，才调用既有画像接口写入 `health_data`。上传操作日志只记录操作状态，不记录
文件名或体检内容。

## 五、环境变量

`.env.example` 记录了本阶段新增配置：

```dotenv
AUTO_CREATE_DATABASE=true
HEALTH_DOCUMENT_MAX_PAGES=20
HEALTH_DOCUMENT_RENDER_DPI=200
HEALTH_DOCUMENT_FALLBACK_RENDER_DPI=300
```

环境变量优先于默认值；敏感的 MySQL 密码、DashScope Key 与 JWT 密钥只放在本地 `.env` 或
部署平台的密钥管理中。

## 六、验收记录

- [x] `python -m pip install -e ".[dev]"` 可完成 editable 安装，且不需要
  `requirements.txt`。
- [x] `ruff format --check app` 与 `ruff check app` 通过。
- [x] `pytest app/tests -q --disable-warnings` 通过（38 项）。
- [x] `alembic upgrade head --sql` 成功生成 MySQL 初始 schema SQL。
- [x] `npm run build` 成功完成前端生产构建。
- [x] 覆盖统一响应契约、主模型成功、单页 Max 兜底、多页聚合、指标冲突、超过 20 页拒绝、
  临时文件清理与静态 PDF fixture。

## 七、后续边界

05 数据治理继续负责 JSON 类型迁移、数据契约、回填、清理、备份和迁移演练；不要重复建立
Alembic 基础设施。
