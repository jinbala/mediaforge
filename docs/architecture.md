# MediaForge 业务流程与排查

启动和服务配置见 [README](../README.md#快速开始)，改动和验收约定见 [开发指南](../CONTRIBUTING.md)。

## 功能索引

| 功能 | 操作入口与行为 | 代码与验收入口 |
| --- | --- | --- |
| 表格导入 | 「爆款商品库 → 导入商品」上传 CSV 或 Excel，匹配商品图片后导入，保存商品信息、图片和检索索引。 | [导入与检索 API](../mediaforge/gateway/routers/rag.py) · [导入测试](../tests/test_product_import.py) |
| 商品链接导入与多图浏览 | 在商品库粘贴拼多多、抖音商城或 17 网分享内容。预览可切换已读取的图片，导入时首图作为封面，其余图片保留在商品相册。读取失败时可重新读取，符合条件时可手动上传。 | [链接导入](../frontend/src/components/ProductLinkImport.tsx) · [商品相册](../frontend/src/components/ProductPhotoGallery.tsx) · [本机浏览器助手](../frontend/product-browser/) · [多图导入测试](../tests/test_product_import.py) |
| 商品可见性管理 | 在商品库隐藏本企业的公共参考条目，可在「已隐藏」恢复。企业私有商品提供删除操作。 | [商品 API](../mediaforge/gateway/routers/products.py) · [隐藏、恢复和删除测试](../tests/test_product_delete.py) |
| 亚马逊调研 | 从工作台或侧栏打开「亚马逊调研」，查询美国站单品、类目榜单和品牌店铺。点「历史记录」可恢复本浏览器近期报告。店铺商品的同配色尺码折叠展示，可以展开查看每个 ASIN。店铺栏目和商品名称自动翻译成简体中文，英文原名保留在页面和导出报告。单品图片需求可带入图片创作。 | [调研页面](<../frontend/src/app/(app)/research/page.tsx>) · [历史记录与报告导出](../frontend/src/lib/amazon-research.ts) · [店铺采集](../mediaforge/rag/amazon_research.py) · [店铺翻译](../mediaforge/rag/amazon_store_translation.py) · [调研 API](../mediaforge/gateway/routers/amazon_research.py) · [接口测试](../tests/test_amazon_research.py)。按[调研流程](#亚马逊调研)核对历史、规格分组、中文、主图、商品跳转和带入操作。 |
| 图片创作与拼多多默认值 | 打开空白图片创作表单，默认选中拼多多，仍可按商品切换目标平台。复用任务带入原有参数。亚马逊调研入口预选 Amazon、主图和详情图，并带入可编辑的风格要求。通过 `POST /api/v1/batch/submit` 提交异步任务。 | [创作表单](<../frontend/src/app/(app)/batch/page.tsx>) · [批量提交 API](../mediaforge/gateway/routers/batch.py)。复核空白表单、复用任务和调研带入路径。 |
| 工作台与任务详情 | 在工作台查看需要处理的任务，可隐藏单条提醒。通过「全部任务」进入详情，核对 SKU、进度、素材及失败原因。 | [工作台](<../frontend/src/app/(app)/dashboard/page.tsx>) · [任务详情](<../frontend/src/app/(app)/tasks/page.tsx>) · [任务 API](../mediaforge/gateway/routers/tasks.py) · [任务测试](../tests/test_tasks_list.py)。隐藏一条提醒并刷新，核对提醒数量及任务列表。 |
| 单图补生成 | 在任务详情的「失败原因」对单张或全部可补生成的失败图操作，可填写修改要求。复核结果回到原任务，已成功的图片仍能打开。 | [失败处理组件](../frontend/src/components/TaskFailureSummary.tsx) · [补生成测试](../tests/test_tasks_list.py) |
| 图片生成服务切换 | 在「设置 → 图片生成服务」为每套配置保存名称、API 地址、API Key 和模型。可尝试读取模型列表、手动填写模型 ID，在多套配置与系统配置间切换。复核刷新后的启用项与正确的模型路由。 | [图片服务设置](../frontend/src/components/ImageProviderSettings.tsx) · [图片服务 API](../mediaforge/gateway/routers/image_provider.py) · [图片服务测试](../tests/test_image_provider.py) |
| 智能助手 | 对话检索参考图，按确认卡片核对生成配置，确认后提交任务并跟踪进度。 | [助手 API](../mediaforge/gateway/routers/agent.py) · [助手工具](../mediaforge/orchestrator/agent_tools.py) · [任务确认测试](../tests/test_agent_production_controls.py) |

## 亚马逊调研

调研表单关闭浏览器自动填充历史。验收时聚焦链接或联想词输入框，检查浏览器历史下拉框不遮挡页面，并粘贴链接按回车提交。

1. **单品调研。** 填写 Amazon 美国站商品链接或 10 位 ASIN。报告展示公开页面可读的主图、商品价、评分、评分数、畅销排名和卖点。可补充每行一条的评论片段。报告分别标注商品公开页和手动录入来源，按评论片段提及次数整理主题，并据此给出产品复核及拍图建议。
2. **类目榜单。** 粘贴美国站 Best Sellers 链接，例如 `https://www.amazon.com/gp/bestsellers/fashion`。单次最多展示 30 件可读取商品的主图以及页面公开的排名、价格和评分。点击商品旁的「调研」打开单品报告。
3. **品牌店铺。** 粘贴带有 `/stores/` 和 `/page/` 的美国站品牌店铺或栏目链接，也可从有店铺链接的单品报告进入。商品样本按 ASIN 匹配店铺提供的主图。有 Amazon 规格数据时，把同款同配色的不同尺码折叠成一行。展开后可查看各尺码的 ASIN，打开对应商品链接或继续调研。缺少规格数据时按 ASIN 逐条显示。栏目和商品名称以简体中文为主，英文原名和每个 ASIN 也保留在 Markdown 中。栏目内容按需查询，每次最多读取 30 个栏目和 30 个商品。
4. 各入口都可以填搜索联想词种子并下载 Markdown 报告。联想词取自 Amazon 搜索建议接口。页面数据和联想词反映查询时的公开样本。公开页缺字段时界面显示空值。若联想词读取失败，已读取的页面报告仍可下载。上游拒绝访问或返回验证页时，接口提示核对链接或稍后重试。
5. 在单品报告编辑「图片制作需求」，再点「带入图片创作」。需求在当前浏览器标签页传入创作表单的风格要求。平台预选 Amazon 美国站，输出预选主图和详情图，使用已有[平台图规则](../mediaforge/workers/platform_rules.py)。填写自有商品原图、SKU、名称和类目后，按图片创作流程提交。
6. 点击三种模式旁的「历史记录」，按中文标题、类型和采集时间查找报告。成功查询后，当前浏览器按企业和账号保存最近 20 份报告。同一类型与规范链接重复查询时，更新该条记录并移到顶部。点击条目可直接恢复当时的报告及表单，点击「开始调研」可获取当前数据。浏览器站点数据清除后，本地历史随之清除。

`POST /api/v1/research/amazon` 使用项目现有会话或 API Key 鉴权与限流。后端从固定 Amazon 美国站来源读取公开 HTML，评论建议使用本地关键词规则。商品的可空 `image_url` 字段从该页面提取，浏览器直接加载 Amazon 图片 CDN。无图或加载失败时显示「无主图」。接口日志记录状态和耗时，省略请求与响应正文。报告在页面内查看或下载，调研过程无需数据库迁移或队列。

店铺翻译复用已配置的文本模型（`OPENROUTER_API_KEY`、`OPENROUTER_BASE_URL`、`AGENT_MODEL`）。常见栏目使用统一译名，其他标题和配色名合并成一次翻译请求，成功译文在进程内缓存。请求使用公开标题和配色文本。返回的 `title_zh` 为可空增量字段，原题名、商品 ASIN、图片和链接沿用 Amazon 数据。模型未配置、超时或只返回部分译文时，仍显示可读取的报告，并提示未翻译的标题保留原文。

店铺分组使用商品卡片中 Amazon 公布的规格表。可空字段 `store_variant` 提供规格组 ASIN、尺码、公共商品名和配色。每组包含规格表中尺码以外的维度均相同的商品，前端只折叠调研结果中已有的 ASIN。Markdown 逐条保留 ASIN 和链接。

单品接口示例传入 `{"mode":"product","source":"B0033SHO4Q","keyword":"toy story"}`。成功响应包含 `source_url`、`collected_at`、`product`、`suggestions` 和 `warnings`。链接类型不匹配时返回 `422`，上游页面无法读取时返回 `502`。

验收时可用 `B0033SHO4Q` 验证单品，使用服装榜单验证排名和单品跳转，使用 `https://www.amazon.com/stores/Hanes/page/F83022EC-A709-4B2F-BCB7-7BBFA1896AB0` 验证栏目继续调研。再补充一条评论片段并查看来源与主题，下载报告，编辑图片需求并检查带入结果。接口回归命令为 `uv run pytest -q tests/test_amazon_research.py`。

历史记录验收可依次完成单品和店铺调研，刷新页面后从「历史记录」打开之前的单品，核对标题、主图、联想词、采集时间和报告下载。用同一商品的 ASIN 和链接重新调研，核对列表只保留最新一条。失败查询后历史条目数量保持原值。窄屏下复核历史入口、弹窗与列表滚动。

店铺中文可用 Oakgarden 的 `https://www.amazon.com/stores/Oakgarden/page/23CB08B9-F986-48AA-B197-242DEB77E0C2` 和 HLTPRO 的 `https://www.amazon.com/stores/HLTPRO/page/08AA412E-82CD-4791-A887-A1C3DFFB990D` 复核。检查两个店铺的栏目译文和英文原名，下载报告核对名称和同名链接，再点击栏目核对报告标题和来源链接。打开 HLTPRO 的「精选优惠」，核对商品中英文标题、主图和 Amazon 链接。

规格分组可查 Oakgarden 的「女士高领长袖上衣」栏目 `https://www.amazon.com/stores/page/6AFD3679-6AE1-44C6-93BD-943F86AE2B80`。核对采集样本是否按配色折叠，展开后检查尺码、ASIN 和调研跳转，下载报告核对全部 ASIN。

## 图片生成与失败处理

```mermaid
flowchart TD
    A["图片创作 · 商品原图与生成配置"] --> B["提交任务 · PostgreSQL 记录任务和 SKU"]
    B --> C["Celery 按 SKU 取任务"]
    C --> D["准备商品信息、参考图和生成提示"]
    D --> E["按输出类型生成并检查图片"]
    E --> F{"是否符合自动重试条件"}
    F -->|符合| G["调整生成策略 · 再试一次"]
    G --> E
    F -->|其他结果| H["汇总任务状态 · 展示素材和失败原因"]
    H -->|人工选择补生成| I["本任务重新排队 · 处理选中的失败图"]
    I --> C
```

1. [任务分发](../mediaforge/orchestrator/dispatcher.py)建立任务和 SKU 记录，将工作交给 [Celery 任务](../mediaforge/orchestrator/tasks.py)。页面通过任务 API 和 SSE 获取状态。
2. [生成编排](../mediaforge/orchestrator/batch_graph.py)和[生成节点](../mediaforge/orchestrator/nodes.py)按 SKU 执行。商品原图提供款式、颜色和结构依据，参考图用于提取视觉风格。按所选输出类型生成主图、详情组图、白底图等素材。
3. [图片 Worker](../mediaforge/workers/image/base.py)执行质检和导出检查，保存实际生成的图片及其结果。生成链路在满足条件的质检失败时自动再试一次。任务可显示已完成、部分失败或失败，有图的失败结果可用于复核。
4. 任务详情的「失败原因」支持补生成单张或全部可补生成的失败图，可填写修改要求。`POST /api/v1/tasks/{job_id}/repair-images` 将指定输出放回原任务，已成功的图保持可用。任务级「重试」调用 `/retry`，按可重试 SKU 创建关联的新任务。 [失败原因组件](../frontend/src/components/TaskFailureSummary.tsx) · [任务接口](../mediaforge/gateway/routers/tasks.py)

工作台的「需要处理」区域中，点击任务旁的「隐藏」会收起这条提醒并更新数量，全部隐藏后会收起整个区域。隐藏按任务 ID 记录在当前浏览器中，刷新后仍有效。任务还可从工作台的「全部任务」查看和处理。

## 模型与规则来源

| 要查什么 | 入口 |
| --- | --- |
| 实际使用的图片服务和模型 | 「设置 → 图片生成服务」查看当前配置。Worker 执行时读取当前启用配置，提交的模型别名匹配时使用其地址、模型 ID 和密钥。系统配置见 [.env.example](../.env.example)，选择逻辑见 [nodes.py](../mediaforge/orchestrator/nodes.py)。 |
| 平台提示、输出比例和各类型验图说明 | [platform_rules.py](../mediaforge/workers/platform_rules.py) |
| 图片组内分工 | [detail_page.py](../mediaforge/workers/image/detail_page.py)定义场景图、卖点图、细节特写的生成指令。 |
| 商品一致性、输出意图、尺寸和边缘纯白等质检 | [图片 Worker 基类](../mediaforge/workers/image/base.py)。审图模型由系统 `QUALITY_MODEL` 配置，任务详情展开 SKU 可查看质检记录。 |
| 拼多多白底图导出规格 | [pinduoduo_white_background.py](../mediaforge/workers/image/pinduoduo_white_background.py)定义图像处理、输出尺寸和文件大小检查。实际商品上架以对应平台商家后台的要求为准。 |

## 失败排查顺序

先记录任务 ID、SKU、具体图片类型和当前结果。任务详情可以展开 SKU 查看本轮提示词、参考信息及质检记录。按出现异常的位置找证据。

| 现象 | 先查哪里 |
| --- | --- |
| 商品链接导入后只看到首图 | 对照导入预览和商品相册，检查本机助手识别出的图片列表、用户选择和导入后展示。 [链接导入组件](../frontend/src/components/ProductLinkImport.tsx) · [商品相册](../frontend/src/components/ProductPhotoGallery.tsx) |
| 提交失败或任务长时间排队 | 检查 API 响应、Celery Worker 日志、对应优先级队列，以及任务和 SKU 状态。 [任务分发](../mediaforge/orchestrator/dispatcher.py) · [状态存储](../mediaforge/db/job_store.py) |
| 模型调用报错或没有图片 | 先核对任务模型别名与启用的服务，结合 Worker 日志检查上游接口响应。 [模型客户端](../mediaforge/workers/openrouter_client.py) |
| 图片生成了，卖点图和细节特写未通过 | 先对照失败图和「输出意图实现度」。卖点图检查商品卖点是否在构图中突出，特写检查画面是否清楚展示局部结构或面料。复核对应输出的提示词和质检原因，再决定是否补生成。 |
| 白底图未通过 | 先看原因属于生成图片边缘检查 `deterministic_checks`，还是最终文件规格 `export_checks`。边缘近白像素不足时，先复核生成图边缘是否有灰底或阴影。 |
| 任务报告成功但图片无法打开 | 核对素材状态和文件路径，检查登录后的文件接口以及 MinIO 对象。 [文件接口](../mediaforge/gateway/main.py) · [对象存储](../mediaforge/storage.py) |

本地启动后可用以下命令检查服务并查看近期日志。API 就绪、队列响应和业务图片验收各有独立证据。

```bash
docker compose ps
docker compose logs --tail 100 api celery-worker frontend
docker compose exec -T celery-worker celery -A mediaforge.celery_app inspect ping --timeout=5
```

如果 Compose 显示 Worker `unhealthy`，先确认容器的健康检查探测对象。后端镜像的默认健康检查访问 HTTP `/health`，Worker 进程响应可用上面的 `inspect ping` 检查。具体任务仍需核对对应队列和日志。 [后端镜像](../Dockerfile) · [Compose 配置](../docker-compose.yml)

| 存储 | 用途 |
| --- | --- |
| PostgreSQL | 租户、商品、任务与素材状态、图片服务配置 |
| MinIO | 商品原图、参考图和生成素材 |
| Redis 与独立的 Redis Broker | 缓存、SSE 事件和 Celery 队列 |
| Milvus / Zilliz Cloud | 参考商品的向量检索索引 |
