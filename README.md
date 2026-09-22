# 智能旅行规划助手

基于 FastAPI、LangGraph 和 Vue 3 的旅行计划生成应用。系统通过状态图编排偏好解析、外部数据获取、行程生成、结果校验、重规划与降级兜底，并提供 SSE 实时进度、SQLite 持久化、地图可视化和在线编辑能力。

## 在线体验

- 前端：<https://trip-planner-production-a0be.up.railway.app>
- 后端 API 文档：<https://trip-planner-production-7000.up.railway.app/docs>
- 后端健康检查：<https://trip-planner-production-7000.up.railway.app/health>

## 功能特性

- **LangGraph 工作流**：使用 `StateGraph` 编排 6 个核心节点，覆盖偏好提取、并行数据获取、行程规划、结果校验、重规划和降级计划。
- **SSE 实时反馈**：前端实时显示当前执行节点和进度百分比，用户可以中途取消生成。
- **外部数据接入**：优先通过 MCP 调用高德地图工具，MCP 不可用时自动降级到高德 REST API，保证主流程可用。
- **行程持久化**：生成的计划保存到 SQLite，可通过 `plan_id` 直接访问、刷新恢复和分享。
- **结果可视化与编辑**：支持每日行程、天气预报、预算汇总、景点地图标记和行程编辑保存。
- **图片渐进加载**：景点图片按批次异步加载，失败时使用前端占位样式，避免阻塞结果页渲染。
- **生产部署**：提供 Dockerfile 与 Docker Compose 配置，当前演示环境部署在 Railway。

## 技术栈

### 后端

- Python 3.11
- FastAPI + Uvicorn
- LangGraph + LangChain
- Pydantic v2 + Pydantic Settings
- SQLite 持久化
- MCP / 高德地图 REST API
- LangSmith 可观测性（可选）

### 前端

- Vue 3 + TypeScript
- Vite
- Ant Design Vue
- Vue Router
- Axios + Fetch SSE
- 高德地图 JavaScript API

## 架构流程

```text
Vue 3 表单
    │
    ▼
FastAPI /api/trip/plan/stream
    │
    ▼
LangGraph StateGraph
    ├─ extract_prefs     解析用户偏好
    ├─ parallel_fetch    并行获取景点、天气、酒店数据
    ├─ planner           生成结构化行程
    ├─ validator         校验日期、地点、行程完整性
    ├─ replanner         校验失败时按错误反馈重试
    └─ fallback          多次失败后生成可用的降级计划
    │
    ▼
SSE 推送进度 → SQLite 保存 → Vue 结果页 / 地图展示
```

校验结果决定后续路径：

```text
validator ── 通过 ──▶ END
validator ── 未通过且可重试 ──▶ replanner ──▶ planner
validator ── 多次失败 ──▶ fallback
```

## 项目结构

```text
trip-planner/
├── backend/
│   └── app/
│       ├── api/
│       │   ├── main.py
│       │   └── routes/
│       ├── graph/
│       │   ├── builder.py
│       │   ├── nodes.py
│       │   ├── state.py
│       │   └── tools.py
│       ├── models/
│       ├── services/
│       └── config.py
├── frontend/
│   ├── src/
│   │   ├── services/
│   │   ├── types/
│   │   └── views/
│   ├── Dockerfile
│   └── nginx.conf
├── docker-compose.yml
└── README.md
```

## 本地开发

### 1. 准备环境

- Python 3.11+
- Node.js 20+
- 高德开放平台 Key
- 支持 OpenAI 协议的 LLM API Key
- Unsplash Access Key（可选，用于景点图片）

### 2. 启动后端

```bash
cd backend
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

后端环境变量参考：

```env
LLM_MODEL_ID=your-model-name
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
AMAP_API_KEY=your-amap-web-service-key
UNSPLASH_ACCESS_KEY=your-unsplash-access-key
CORS_ORIGINS=http://localhost:5173
```

如需启用 LangSmith，可继续配置：

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-langsmith-api-key
LANGCHAIN_PROJECT=trip-planner
```

### 3. 启动前端

```bash
cd frontend
npm install
```

创建 `frontend/.env`：

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_AMAP_WEB_JS_KEY=your-amap-js-api-key
VITE_AMAP_SECURITY_CODE=your-amap-security-code
```

启动开发服务器：

```bash
npm run dev
```

访问：<http://localhost:5173>

> 注意：`VITE_AMAP_WEB_JS_KEY` 必须是高德开放平台中的 **Web端(JS API)** Key；后端使用的 `AMAP_API_KEY` 通常是 **Web服务** Key。两类 Key 的用途不同，不能混用。

## API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/trip/plan` | 同步生成旅行计划 |
| `POST` | `/api/trip/plan/stream` | SSE 流式生成并推送节点进度 |
| `GET` | `/api/trip/plan/{plan_id}` | 根据 ID 获取已保存计划 |
| `PUT` | `/api/trip/plan/{plan_id}` | 更新已保存计划 |
| `GET` | `/api/trip/plans` | 获取最近计划列表 |
| `GET` | `/api/trip/health` | 检查 LangGraph、MCP 和 LLM 状态 |
| `GET` | `/api/map/poi` | 搜索兴趣点 |
| `GET` | `/api/map/weather` | 查询城市天气 |
| `POST` | `/api/map/route` | 规划路线 |
| `GET` | `/api/poi/photo` | 获取景点图片 |

启动后端后，完整接口文档位于：

```text
http://localhost:8000/docs
```

## Docker 部署

在项目根目录执行：

```bash
docker compose up --build
```

默认访问地址：

- 前端：<http://localhost>
- 后端：<http://localhost:8000>

部署前需要在 `backend/.env` 中配置后端密钥，不要把真实 `.env` 提交到 Git。

## Railway 部署要点

### 后端服务

- Root Directory：`backend`
- 端口：`8000`
- 关键变量：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`、`AMAP_API_KEY`、`UNSPLASH_ACCESS_KEY`、`CORS_ORIGINS`

### 前端服务

- Root Directory：`frontend`
- 端口：`80`
- 关键变量：

```env
VITE_API_BASE_URL=https://your-backend-domain
VITE_AMAP_WEB_JS_KEY=your-amap-js-api-key
VITE_AMAP_SECURITY_CODE=your-amap-security-code
```

这些 `VITE_` 变量在构建阶段注入，修改后必须重新构建前端才会生效。

## 使用流程

1. 在首页填写目的地、出行日期、交通方式、住宿偏好和旅行标签。
2. 点击生成计划，前端通过 SSE 显示当前执行进度。
3. 生成完成后进入结果页，查看每日路线、酒店、餐饮、天气、预算和地图。
4. 需要调整时进入编辑模式，保存后计划会更新到 SQLite。
5. 复制结果页 URL，可在新标签页中通过 `plan_id` 恢复行程。

## 贡献

欢迎提交 Issue 或 Pull Request。提交前请运行：

```bash
cd frontend
npm run build
```

## 开源协议

CC BY-NC-SA 4.0

## 致谢

- [HelloAgents](https://github.com/datawhalechina/Hello-Agents) - 智能体教程
- [HelloAgents框架](https://github.com/jjyaoao/HelloAgents) - 智能体框架
- [高德开放平台](https://lbs.amap.com/) - 地图与位置服务
- [amap-mcp-server](https://github.com/sugarforever/amap-mcp-server) - 高德地图 MCP 服务器
