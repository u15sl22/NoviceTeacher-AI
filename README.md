# NoviceTeacher-AI


**A Knowledge-Augmented Human-AI Framework for Scaffolding Novice Teacher Lesson-Plan Revision and Professional Growth**

研究型教案修订 MVP。支持粘贴文本或上传 DOCX/文本型 PDF → 提取预览 → 动态分段 → 0–2 条建议 → Yes/No → 单元修订 → 多轮 → 最终教案。原文件、V0 和完整修订过程持久保存，刷新后从数据库恢复。

## 快速运行（Windows）

前置：Python 3.12、Node.js 22。所有命令从仓库根目录执行。

```powershell
./scripts/setup.ps1
./scripts/start.ps1 -Mock
```

打开 **http://127.0.0.1:8000**。输入页提供三年级数学《分数的初步认识》示例。Mock 页面明确标注“模拟演示 · 非真实 AI”，不需要网络或密钥。SQLite 数据保存在根目录 `pedago_loop.db`，重启仍然保留。

### 教授 Demo：DeepSeek

`setup.ps1` 仅在 `.env` 不存在时复制 `.env.example`。在根目录 `.env` 配置：

```dotenv
SUGGESTION_PROVIDER=generic_llm
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=填写你自己的密钥
LLM_TIMEOUT_SECONDS=45
LLM_EXTRA_BODY={"thinking":{"type":"disabled"},"max_tokens":2048}
```

```powershell
./.venv/Scripts/python.exe scripts/smoke_llm.py
./scripts/start.ps1
```

`smoke_llm.py` 会实际发送一个数学教案片段，验证真实服务的 JSON 和建议数量。密钥仅留在服务器环境变量，不进入浏览器、配置快照、Git 或日志。缺少密钥、网络错误和超时会明确报错，不会自动回退 Mock。

**切换服务需要重启后端并新建会话**。既有会话继续使用创建时保存的模型、URL 和策略快照，避免实验条件漂移；服务密钥从当前服务器配置读取。未来同时运行多个供应商时，应注入按供应商解析密钥的实现。

### 智谱：只改配置

参考 `.env.zhipu.example`：

```dotenv
SUGGESTION_PROVIDER=generic_llm
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4.7-flash
LLM_API_KEY=填写智谱密钥
```

两者共用 `GenericLLMSuggestionProvider` → `CompatibleLLMClient`。模型名、URL 均在配置层，核心流程不出现 DeepSeek / 智谱分支。可填写账号实际可用的其他兼容模型。

接口依据：[DeepSeek 首次调用](https://api-docs.deepseek.com/)、[DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode)、[智谱对话补全](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8)。配置示例核对日期：2026-09-16。

## PostgreSQL

现在提供完整 Docker Compose 编排：**应用（前端静态页面＋后端）与 PostgreSQL 两个常驻容器**，另有一次性迁移任务、独立附件卷和备份恢复工具。操作细节见 **[部署与存储规范](docs/deployment.md)**。

```powershell
Copy-Item .env.compose.example .env.compose
# 先修改 .env.compose 中的数据库密码；DeepSeek 密钥仍在 .env
docker compose --env-file .env.compose up -d --build
```

容器自动连接内部 PostgreSQL，无需改写原 `.env` 的 SQLite 配置。默认访问 http://127.0.0.1:8000 。已有 SQLite 历史不会自动搬迁，应先按照部署文档迁移到空目标，再启动应用。

不使用 Docker、而连接已有 PostgreSQL 时，可设置：

```dotenv
DATABASE_URL=postgresql+psycopg://pedago:pedago@localhost:5432/pedago_loop
```

随后执行 `scripts/start.ps1` 自动升级表结构。SQLite 与 PostgreSQL 数据不自动同步；迁移后应选定一个作为业务数据源。当前 Docker/PostgreSQL 已完成在线构建、历史迁移和容器内回归测试。

## 开发与验证

```powershell
# 迁移（不用 ORM create_all 替代正式迁移）
./.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
# 后端测试
./.venv/Scripts/python.exe -m pytest backend/tests -q
# 后端热重载
./.venv/Scripts/python.exe -m uvicorn app.api:app --app-dir backend --reload
# 另一个终端，前端开发服务器代理 /api 到 8000
cd frontend
npm ci
npm run dev
# 构建
npm run build
```

浏览器测试：先在一个独立终端运行 `scripts/start.ps1 -Mock`，再在 `frontend` 执行 `npm run test:e2e`。默认使用本机无头 Microsoft Edge；也可修改 Playwright 配置使用已安装的 Chromium。测试会创建并保留演示会话，建议使用独立测试数据库。

后端依赖锁定在 `backend/requirements.lock.txt`；前端锁定在 `frontend/package-lock.json`。

对真实 PostgreSQL 运行同一套状态机测试：设置 `TEST_DATABASE_URL` 为专用测试实例连接串，再运行 pytest。每个用例创建独立 `pedago_test_<uuid>` schema，结束后只清理该测试 schema，不操作业务 schema。

## 数据与使用规则

- `V0` 保留输入原文；每轮完成立即保存 `V1…V5`，即使随后选择结束也有完整快照。
- 单元正文每次实际变化，新增 `SectionVersion` 并链接上一版本。拒绝不改变正文。
- 首版修改策略为**追加具体修订内容**，两条采纳依次累加，不互相覆盖。界面会说明此行为。
- 建议的教学依据标为“暂定，未经知识库验证”。当前没有正式 RAG 或来源背书。
- 每轮每单元最多生成一组成功建议；0 条建议同样持久标记已生成。失败可重试。
- 必须处理所有建议后进入下一单元；完成一轮才可继续或结束。后端拒绝第六轮及结束后的编辑。
- 复制恢复链接可在另一页面恢复。浏览器仅保存会话定位信息；待确认的首次提交临时保存请求编号和原始载荷，成功后清除。
- 最终页面可查看任意轮次教案、下载文本、导出完整 JSON 研究记录。导出包含所有轮次的建议、决策、单元版本、生成上下文、原始模型返回、检索记录、事件及时间戳。
- 无登录，默认仅监听 `127.0.0.1`。恢复链接包含会话标识，适用于当前本机教授演示范围。

## 代码导航

```text
backend/app/
  api.py                  薄路由、请求事务、静态页面
  models.py               持久化领域模型和数据库约束
  schemas.py              输入验证
  workflow.py             会话、版本、轮次状态机和事件
  history.py              只读历史查询
  orchestrator.py          协调研究模块
  providers/
    interfaces.py         可替换模块契约和 DTO
    defaults.py           首版默认研究策略
    llm.py                真实模型传输与建议校验
    config.py             会话配置快照与模块工厂
backend/migrations/       冻结的迁移脚本
backend/tests/            契约、状态机、恢复、并发、迁移测试
frontend/src/             Vue 四阶段工作台
frontend/tests/           真实浏览器流程测试
scripts/                  安装、启动、真实服务探测
docs/architecture.md      扩展边界、API、研究取舍
```

## 当前范围

已实现通用反馈和可替换接口；未实现正式 RAG、Active Inquiry、三条件实验、成长评分、统计分析、登录或管理后台。不能据此宣称教学效果或教师成长已得到验证。

Git 已初始化为 `main`。待真实模型和目标 PostgreSQL 环境验收后，可提交稳定版本、创建 `dev`，再打 `v0.1-mvp`；教授 Demo 验收后再标记 `v0.2-demo`。

当前本机验收结果与待验证项见 [docs/validation.md](docs/validation.md)。如果 8000 端口正在运行 Mock，可使用 `scripts/start.ps1 -Port 8001` 另启真实模型演示。
