# NoviceTeacher-AI / PedagoLoop

**A Knowledge-Augmented Human-AI Framework for Scaffolding Novice Teacher Lesson-Plan Revision and Professional Growth**<br>
**一个以知识增强和人机协作为基础、支持新手教师教案修订与专业成长的框架**

研究型教案修订 MVP。支持粘贴文本或上传 DOCX、文本型/扫描型 PDF，经过文字提取或后台 OCR、预览校对、动态分段、0–2 条建议、采纳/拒绝/自定义再生成、单元修订和多轮迭代后生成最终教案。自定义候选可反复生成，直到教师最终采纳或拒绝。原文件、V0 和完整修订过程会持久保存，刷新页面后可从数据库恢复。

This research-oriented lesson-plan revision MVP accepts pasted text, DOCX files, and text-based or scanned PDFs. Its workflow covers text extraction or background OCR, proofreading, dynamic sectioning, 0–2 suggestions, accept/reject/custom regeneration, section revision, multiple rounds, and a final lesson plan. Teachers can refine a candidate repeatedly until they finally accept or reject it. The source file, V0, and the complete revision history are persisted and can be restored after a page refresh.

## 快速运行 / Quick Start (Windows)

前置环境：Python 3.12、Node.js 22。所有命令均从仓库根目录执行。

Prerequisites: Python 3.12 and Node.js 22. Run all commands from the repository root.

```powershell
./scripts/setup.ps1
./scripts/start.ps1 -Mock
```

打开 **http://127.0.0.1:8000**。输入页提供三年级数学《分数的初步认识》示例。Mock 模式明确标注“模拟演示 · 非真实 AI”，无需网络或密钥。SQLite 数据默认保存在根目录 `pedago_loop.db`，重启后仍然保留。

Open **http://127.0.0.1:8000**. The input page includes a Grade 3 mathematics sample lesson. Mock mode is clearly marked as a simulated demonstration, requires no network or API key, and stores persistent SQLite data in `pedago_loop.db` at the repository root.

### 教授演示：DeepSeek / Professor Demo: DeepSeek

`setup.ps1` 只会在 `.env` 不存在时复制 `.env.example`。在根目录 `.env` 中配置以下内容；不要把真实密钥提交到 Git。

`setup.ps1` copies `.env.example` only when `.env` does not exist. Configure the root `.env` as follows, and never commit a real API key to Git.

```dotenv
SUGGESTION_PROVIDER=generic_llm
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=your_api_key
LLM_TIMEOUT_SECONDS=45
LLM_EXTRA_BODY={"thinking":{"type":"disabled"},"max_tokens":2048}
LLM_PROFILES=[{"id":"deepseek","label":"DeepSeek","provider":"deepseek","base_url":"https://api.deepseek.com","model":"deepseek-flash","extra_body":{"thinking":{"type":"disabled"},"max_tokens":2048}},{"id":"zhipu","label":"智谱 GLM","provider":"zhipu","base_url":"https://open.bigmodel.cn/api/paas/v4","model":"glm-4.7-flash","extra_body":{"max_tokens":2048}}]
DEFAULT_LLM_PROFILE=deepseek
ZHIPU_API_KEY=
LLM_API_KEYS={}
```

```powershell
./.venv/Scripts/python.exe scripts/smoke_llm.py
./scripts/start.ps1
```

`smoke_llm.py` 会向真实服务发送一个数学教案片段，并至少生成和处理一条建议，以验证模型服务、决定、版本和导出链路。Docker/PostgreSQL 验收命令如下；临时 schema 会自动删除。

`smoke_llm.py` sends a short mathematics lesson-plan excerpt to the live service and generates and processes at least one suggestion. This verifies the model integration, decisions, versioning, and export flow. Use the following command for Docker/PostgreSQL acceptance; its temporary schema is removed automatically.

```powershell
docker compose --env-file .env.compose run --rm maintenance python scripts/smoke_llm.py --postgres --profile deepseek
```

密钥只存在于服务器环境变量中，不进入浏览器、配置快照、Git 或日志。缺少密钥、网络错误和超时都会明确报错，不会静默回退到 Mock。输入页可以为新教案选择已配置的模型；已有会话继续使用创建时冻结的 profile、模型、URL 和策略。修改服务器 profile 或密钥后需要重启应用。

Secrets remain in server-side environment variables and are never included in the browser, configuration snapshots, Git, or logs. Missing keys, network failures, and timeouts return explicit errors instead of silently falling back to Mock. A configured model can be selected for each new lesson plan. Existing sessions continue using the profile, model, URL, and strategy frozen at creation time. Restart the application after changing server profiles or keys.

### 智谱及其他兼容模型 / Zhipu and Other Compatible Models

智谱配置可参考 `.env.zhipu.example`：

For Zhipu, use `.env.zhipu.example` as a reference:

```dotenv
ZHIPU_API_KEY=your_zhipu_api_key
```

重启后可在输入页选择 DeepSeek 或智谱。要增加其他兼容 Chat Completions 的服务，请在 `LLM_PROFILES` 中添加 profile，并在 `LLM_API_KEYS` 中以 profile ID 配置密钥，例如 `{"my_model":"your_api_key"}`。所有模型共用 `GenericLLMSuggestionProvider` → `CompatibleLLMClient`，核心流程不绑定具体供应商。

After restarting, users can select DeepSeek or Zhipu on the input page. To add another Chat Completions-compatible service, add a profile to `LLM_PROFILES` and map its profile ID to a key in `LLM_API_KEYS`, for example `{"my_model":"your_api_key"}`. All providers share `GenericLLMSuggestionProvider` → `CompatibleLLMClient`; the core workflow is provider-independent.

接口参考 / API references: [DeepSeek First API Call](https://api-docs.deepseek.com/), [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode), [Zhipu Chat Completions](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8). 配置示例核对日期 / Configuration last checked: 2026-09-16.

## Docker Compose 与 PostgreSQL / Docker Compose and PostgreSQL

项目提供完整 Docker Compose 编排，包括四个常驻容器：应用、模型 Worker、OCR Worker 和 PostgreSQL。另有一次性数据库迁移任务、独立附件卷和备份恢复工具。OCR 使用镜像内的 Tesseract 简体中文/英文模型，PDF 不会发送给第三方 OCR 服务。详细操作见 [部署与存储规范](docs/deployment.md)。

The project includes a complete Docker Compose stack with four long-running containers: the application, model Worker, OCR Worker, and PostgreSQL. It also provides a one-shot migration task, a dedicated attachment volume, and backup/restore tools. OCR uses the Simplified Chinese and English Tesseract models installed in the image; PDFs are not sent to a third-party OCR service. See [Deployment and Storage](docs/deployment.md) for operational details.

```powershell
Copy-Item .env.compose.example .env.compose
# 修改数据库密码；DeepSeek 密钥继续保存在 .env
# Change the database password; keep the DeepSeek key in .env
docker compose --env-file .env.compose up -d --build
```

默认访问地址为 **http://127.0.0.1:8000**。容器自动连接内部 PostgreSQL，无需修改 `.env` 中的本地 SQLite 配置。已有 SQLite 历史不会自动迁移；请按照部署文档先迁移到空目标数据库，再启动应用。

The default address is **http://127.0.0.1:8000**. Containers connect to the internal PostgreSQL service automatically, without changing the local SQLite setting in `.env`. Existing SQLite history is not migrated automatically; follow the deployment guide to migrate it into an empty target database before starting the application.

不使用 Docker、但需要连接已有 PostgreSQL 时，可设置：

To connect to an existing PostgreSQL instance without Docker, set:

```dotenv
DATABASE_URL=postgresql+psycopg://pedago:pedago@localhost:5432/pedago_loop
```

随后运行 `scripts/start.ps1` 自动升级表结构。SQLite 与 PostgreSQL 不会自动同步，迁移后必须选定一个业务数据源。当前 Docker/PostgreSQL 方案已经通过在线构建、历史迁移、OCR 和容器内回归测试。

Then run `scripts/start.ps1` to upgrade the schema automatically. SQLite and PostgreSQL are not synchronized, so choose one authoritative data source after migration. The current Docker/PostgreSQL setup has passed live image builds, history migration, OCR, and in-container regression tests.

## 开发与验证 / Development and Validation

```powershell
# 数据库迁移；正式迁移不要用 ORM create_all 代替
# Database migration; do not replace formal migrations with ORM create_all
./.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head

# 后端测试 / Backend tests
./.venv/Scripts/python.exe -m pytest backend/tests -q

# 后端热重载 / Backend hot reload
./.venv/Scripts/python.exe -m uvicorn app.api:app --app-dir backend --reload

# 在另一个终端启动前端开发服务器；/api 代理到 8000
# In another terminal, start the frontend dev server; /api is proxied to 8000
cd frontend
npm ci
npm run dev

# 生产构建 / Production build
npm run build
```

浏览器测试：先在独立终端运行 `scripts/start.ps1 -Mock`，再到 `frontend` 目录执行 `npm run test:e2e`。默认使用本机无头 Microsoft Edge，也可调整 Playwright 配置使用 Chromium。测试会创建并保留演示会话，建议使用独立测试数据库。

Browser tests: run `scripts/start.ps1 -Mock` in a separate terminal, then execute `npm run test:e2e` from `frontend`. Tests use local headless Microsoft Edge by default; the Playwright configuration can be changed to use Chromium. Because browser tests create and retain demo sessions, use a dedicated test database.

后端依赖锁定在 `backend/requirements.lock.txt`，前端依赖锁定在 `frontend/package-lock.json`。对真实 PostgreSQL 运行状态机测试时，将 `TEST_DATABASE_URL` 设置为专用测试实例。每个用例会创建独立的 `pedago_test_<uuid>` schema，结束时只清理自己的测试 schema，不会操作业务 schema。

Backend dependencies are locked in `backend/requirements.lock.txt`; frontend dependencies are locked in `frontend/package-lock.json`. To run the state-machine suite against PostgreSQL, set `TEST_DATABASE_URL` to a dedicated test instance. Each test creates an isolated `pedago_test_<uuid>` schema and removes only that schema afterward, leaving the business schema untouched.

### 数据集只读清洗 / Read-Only Dataset Cleaning

```powershell
./.venv/Scripts/python.exe scripts/clean_dataset.py
```

该命令只读取 41 份原教案和 1118 条原始批注，不连接业务数据库，也不调用外部模型。运行前后会核对两个源 JSON 和 41 份教案的 SHA-256。详细派生记录写入被 Git 忽略的 `.runtime/dataset-cleaning/`，摘要见 [数据集只读清洗报告](docs/dataset-cleaning-report.md)。所有记录保持 `unreviewed / not_verified`；重复、模板、泄漏和原文定位结果只是待人工核验的机械标记。`scripts/convert_legacy_docs.ps1` 可从旧 DOC 生成派生 DOCX，`scripts/ocr_dataset_pdfs.ps1` 可从扫描 PDF 生成本地 OCR 文本，二者都不会覆盖源文件。

This command only reads 41 source lesson plans and 1,118 raw annotations. It neither connects to the business database nor calls an external model. It compares the SHA-256 hashes of the two source JSON files and all 41 lesson plans before and after processing. Detailed derived artifacts are written to the Git-ignored `.runtime/dataset-cleaning/`; see the [Read-Only Dataset Cleaning Report](docs/dataset-cleaning-report.md) for a summary. Every record remains `unreviewed / not_verified`; duplicate, template, leakage, and quote-location results are mechanical flags awaiting human review. `scripts/convert_legacy_docs.ps1` creates derived DOCX files from legacy DOC files, while `scripts/ocr_dataset_pdfs.ps1` creates local OCR text from scanned PDFs. Neither script overwrites source files.

## 数据与使用规则 / Data and Usage Rules

- `V0` 保留输入原文；每轮完成后立即保存 `V1…V5`。<br>
  `V0` preserves the original input; `V1…V5` are saved immediately after their respective rounds.
- 单元正文每次实际变化都会创建新的 `SectionVersion` 并链接上一版本；拒绝建议不会修改正文。<br>
  Every actual section change creates a new `SectionVersion` linked to its predecessor; rejecting a suggestion does not change the text.
- 首版修改策略会追加具体修订内容；同一单元中采纳的两条建议依次累加。<br>
  The initial revision strategy appends concrete revisions; two accepted suggestions in one section accumulate in order.
- 建议中的教学依据会标记验证状态。检索框架已经实现，但只有人工核验为 verified 的知识和案例可以进入上下文；当前基线没有已核验内容。<br>
  Pedagogical evidence carries a verification state. Retrieval is implemented, but only human-verified knowledge and cases can enter model context; the current baseline contains no verified items.
- 每轮每单元最多生成一组成功建议；0 条建议同样会被持久记录，失败任务可以重试。<br>
  Each section can have at most one successful suggestion set per round; zero suggestions are also persisted, and failed jobs can be retried.
- 每条当前建议可以采纳、拒绝，或填写最多 2,000 字的自定义要求让模型异步生成下一版；完整候选链保留在历史中，只有最终采纳才修改正文。<br>
  Each current suggestion can be accepted, rejected, or regenerated asynchronously from a custom instruction of up to 2,000 characters. The full candidate chain is retained, and only final acceptance changes the lesson text.
- 真实模型任务先保存到数据库，再由 Worker 在请求事务之外调用模型；页面显示排队和运行状态并自动获取结果。<br>
  Live-model jobs are saved before a Worker calls the model outside the request transaction; the page displays queued/running states and retrieves results automatically.
- 长单元按 UTF-8 字节预算在模型内部切块，教师仍按原教学单元审阅；开始前会估算调用次数并检查安全上限。<br>
  Long sections are split internally according to a UTF-8 byte budget while teachers continue reviewing the original teaching unit; estimated calls and safety limits are checked before revision begins.
- 扫描或部分扫描 PDF 先保存原文件，再由 OCR Worker 逐页识别。页面显示进度，失败可重试，成功后必须由用户校对正文。<br>
  Scanned or partially scanned PDFs are stored first and then processed page by page by the OCR Worker. The page displays progress, supports retries, and requires users to proofread recognized text.
- 所有建议处理完后才能进入下一单元；完成一轮后才能继续或结束。后端拒绝第六轮和结束后的编辑。<br>
  Users must process every suggestion before moving to the next section and must complete a round before continuing or finishing. The backend rejects a sixth round and edits after termination.
- 恢复链接可在另一页面打开。浏览器只保存会话定位信息；首次提交的请求编号和原始载荷只会临时保存，并在成功后清除。<br>
  A recovery link can reopen the session in another page. The browser stores only session-location data; the first submission's request ID and payload are temporary and cleared after success.
- 最终页面支持查看任意轮次、下载文本并导出完整 JSON 研究记录，包括建议、决定、版本、上下文、模型原始返回、检索记录、事件和时间戳。<br>
  The final page can display any round, download text, and export a complete JSON research record containing suggestions, decisions, versions, context, raw model responses, retrieval records, events, and timestamps.
- 当前没有正式登录，服务默认只监听 `127.0.0.1`。恢复链接包含会话标识，仅适用于当前本机教授演示范围。<br>
  Formal authentication is not implemented, and the service listens on `127.0.0.1` by default. Recovery links contain session identifiers and are intended only for the current local professor-demo scope.

## 代码导航 / Code Map

```text
backend/app/
  api.py                  路由和请求事务 / Routes and request transactions
  models.py               领域模型和数据库约束 / Domain models and database constraints
  schemas.py              输入验证 / Input validation
  workflow.py             会话、版本和轮次状态机 / Sessions, versions, and round state machine
  analysis.py             长教案预检与内部切块 / Long-plan preflight and internal chunking
  worker.py               模型任务、租约和版本核对 / Model jobs, leases, and version checks
  ocr.py                  PDF 页面识别适配器 / PDF page OCR adapter
  ocr_worker.py           OCR 任务、进度和重试 / OCR jobs, progress, and retries
  history.py              只读历史查询 / Read-only history queries
  providers/              可替换模型和研究模块 / Replaceable model and research modules
backend/migrations/       Alembic 迁移 / Alembic migrations
backend/tests/            后端测试 / Backend tests
frontend/src/             Vue 四阶段工作台 / Vue four-stage workspace
frontend/tests/           浏览器流程测试 / Browser workflow tests
scripts/                  安装、启动、迁移和验收工具 / Setup, start, migration, acceptance tools
docs/system-requirements-and-architecture.md  完整需求与架构 / Full requirements and architecture
docs/deployment.md        部署、存储和恢复 / Deployment, storage, and recovery
docs/validation.md        当前验收记录 / Current validation record
```

## 当前范围 / Current Scope

系统已经实现可替换模型、持久异步任务、在线 OCR、完整版本历史和仅限 verified 内容的知识/案例检索框架。正式 RAG 内容、Active Inquiry、三条件实验、成长评分、统计分析、正式登录和管理后台仍未完成。因此，当前版本不能用于宣称教学效果或教师专业成长已经得到验证。

The system currently provides replaceable model providers, durable asynchronous jobs, online OCR, complete version history, and a retrieval framework limited to verified knowledge and cases. Curated RAG content, Active Inquiry, the three-condition experiment, growth scoring, statistical analysis, formal authentication, and an administration UI are still pending. This version therefore cannot support claims that teaching effectiveness or teacher professional growth has been validated.

当前本机验收结果和待验证事项见 [验收记录 / Validation Record](docs/validation.md)。如果端口 8000 正在运行 Mock，可使用 `scripts/start.ps1 -Port 8001` 启动另一个真实模型演示实例。

See the [Validation Record](docs/validation.md) for current local results and remaining checks. If Mock is already running on port 8000, start a separate live-model demo with `scripts/start.ps1 -Port 8001`.
