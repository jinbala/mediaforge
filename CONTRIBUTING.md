# MediaForge 开发指南

按 [README](README.md#快速开始) 配置开发环境，按 [业务流程与排查](docs/architecture.md) 找到改动入口。公开贡献可先通过 Issue 讨论业务场景，再从 `main` 创建独立分支提交 Pull Request。

## 开发流程

1. **明确需求与验收。** 记录用户从哪个页面或接口操作、当前结果、期望结果和可复现条件。缺陷有任务记录时，保留脱敏后的任务 ID、SKU 和失败类型。
2. **定位与设计。** 先找到负责该行为的模块和已有测试，核对受影响的调用方、历史数据及配置。涉及 API、数据库、异步任务状态或质量判定的变更，说明行为变化和兼容方式。
3. **实现与收尾。** 围绕一个业务目标改动。数据结构变更补 Alembic 迁移，接口变更同步请求响应模型和前端类型，业务流程变化更新流程文档。涉及异步执行时，明确部分失败、再次操作和成功结果保留的行为。
4. **验证与评审。** 按下面的验证表选择检查项，记录命令、环境、实际结果和未覆盖场景。页面改动附脱敏截图或复核路径，接口行为变化附请求与响应示例。
5. **交付与发布。** 按 [PR 模板](.github/pull_request_template.md) 说明变更和验收证据。涉及迁移、外部服务或队列的发布，记录执行顺序、服务检查和回退方式。把本地验证、CI 和上线后的业务验收分别记录。

例如修复部分失败的图片，验收写成「同一任务选择指定失败图补生成，已成功图仍可打开和下载。补生成结束后，本任务显示新结果和剩余失败原因」。这条验收可对应任务接口、素材状态和页面三个检查点。

## 文件归属

| 要改什么 | 放在哪里 |
| --- | --- |
| 页面、共用组件、前端 API 调用与类型 | `frontend/src/app/`、`frontend/src/components/`、`frontend/src/lib/`、`frontend/src/types/` |
| 商品链接解析和本机登录浏览器助手 | `frontend/product-browser/`，网页对接在 `frontend/src/lib/` |
| 后端 HTTP、业务数据模型与数据访问 | `mediaforge/gateway/routers/`、`mediaforge/models/`、`mediaforge/db/` |
| 编排、模型调用、图片规则与检索 | `mediaforge/orchestrator/`、`mediaforge/workers/`、`mediaforge/rag/` |
| 数据库迁移和自动化测试 | `alembic/versions/`、`tests/` |
| 可复用的初始化、迁移辅助或运维命令 | `scripts/` |
| 文档和演示素材 | 根目录 README 维护启动入口，`docs/` 维护流程说明，`docs/screenshots/` 放文档截图，`data/` 和 `images/` 放获准公开的示例 |
| 临时排查、配置和业务数据 | 临时文件放 `tmp/`，本地配置用 `.env`，公开配置样例用 `.env.example`，业务记录和图片走应用的数据存储 |

## 新功能文档

新增功能时，在同一次改动里更新[功能索引](docs/architecture.md#功能索引)。从用户能找到的页面或接口写起，链接主要实现，说明关键状态、配置或限制，给出可复核的操作步骤或测试入口。已有功能发生变化时更新原条目。涉及跨模块流程、启动方式或环境变量的变化，同时更新对应章节，PR 中附文档链接。

## 验证方式

从仓库根目录运行后端和 Compose 命令。Python 测试依赖通过 `uv sync --frozen --extra dev` 安装。前端在 `frontend/` 执行 `npm ci`，Windows PowerShell 可使用 `npm.cmd`。

| 改动范围 | 验证入口 |
| --- | --- |
| 图片生成、质检、任务状态与补生成 | `uv run pytest -q tests/test_image_workers.py tests/test_batch_nodes.py tests/test_batch_graph.py tests/test_batch_persistence.py tests/test_tasks_router.py tests/test_tasks_list.py` |
| 图片服务配置、切换和路由 | `uv run pytest -q tests/test_image_provider.py tests/test_batch_nodes.py tests/test_image_workers.py` |
| 商品库导入与删除 | `uv run pytest -q tests/test_product_import.py tests/test_product_delete.py`，涉及本机浏览器助手时增加 `npm run build:product-browser` |
| 前端页面、接口接入或构建 | 在 `frontend/` 执行 `npm run lint` 和 `npm run build`，按用户操作路径复核界面 |
| 数据库迁移 | `uv run alembic heads`，在验证库执行 `uv run alembic upgrade head` 并检查受影响的历史数据路径 |
| Compose 或环境配置 | `docker compose config --quiet` 和 `docker compose --profile local-milvus config --quiet` |
| 文档 | 检查相对链接、命令、路径和描述对应的实现，运行 `git diff --check` |

跨模块改动扩大测试范围。后端全量用 `uv run pytest -q`，CI 命令见 [ci.yml](.github/workflows/ci.yml)。涉及真实出图质量时，单独使用有代表性的 SKU 验收，记录结果数量、任务 ID、失败原因和图片复核路径。第三方付费调用和真实业务数据按测试环境权限执行。

## 提交要求

- API Key、访问令牌、`.env`、生产数据和用户隐私数据应留在受控环境。
- 公开的商品图片、品牌素材和训练数据需具备相关授权。
- 本地日志、生成结果、临时脚本和调试文件留在本地。提交前检查 `git status --short` 和 `git diff --check`，暂存后检查 `git diff --cached`。

## 提交信息

建议使用简短、可读的提交信息，例如：

```text
feat: add authenticated MinIO file route
fix: preserve selected image model in batch jobs
docs: clarify Docker deployment variables
```

提交代码即表示你有权按本项目的 MIT License 提供相关内容。
