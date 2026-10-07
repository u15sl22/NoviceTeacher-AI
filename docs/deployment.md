# Docker Compose 部署与存储规范

## 交付状态

两个常驻容器：`app`（Vue 构建文件 + FastAPI）和 `db`（PostgreSQL 17）。`migrate` 为一次性数据库结构升级任务；`maintenance` 是按需执行的管理工具容器，不常驻。前端不运行 Vite 开发服务器。

已在 Windows Docker Desktop 上完成真实构建和运行验证：PostgreSQL、Alembic、应用健康检查均通过，容器内 PostgreSQL 测试为 67 passed、1 skipped；现有 SQLite 历史已事务迁移并逐表核验。跨主机恢复脚本已完成自动化测试，正式上服务器时仍应按第 5、6 节完成一次目标主机恢复演练和人工验收。

## 1. 首次启动（全新数据库）

先安装并启动 Docker Desktop，使用 Linux containers。以下命令均在项目根目录运行。确认 `docker version` 和 `docker compose version` 可用。

保留原 `.env` 中的 DeepSeek 配置。另建 `.env.compose`：

```powershell
Copy-Item .env.compose.example .env.compose
```

如果该文件已存在，不要覆盖。将 POSTGRES_PASSWORD 改为自己的密码；建议使用较长的随机字母数字字符串。如果已有数据卷，不要只改配置里的密码，否则数据库原密码不会自动改变。数据库字段会覆盖 `.env` 的 SQLite DATABASE_URL，密码由应用安全组装进连接串。

```powershell
docker compose --env-file .env.compose up -d --build
docker compose --env-file .env.compose ps -a
docker compose --env-file .env.compose logs --tail 100 app migrate
```

打开 http://127.0.0.1:8000 。启动顺序：数据库健康 → Alembic 成功 → 应用启动。迁移失败时应用不会启动。若本地旧服务占用 8000，先停止旧服务，或修改 APP_PORT。

停止：`docker compose --env-file .env.compose stop`。

移除容器但保留数据：`docker compose --env-file .env.compose down`。**不要使用 `down -v`；它会删除卷中的数据。**

## 2. 存储约定

| 数据 | 容器路径 / 卷 | 约定 |
|---|---|---|
| 教案正文、V0/V1、用户、建议、来源、研究日志 | db 的 `/var/lib/postgresql/data` / `pedago_data` | PostgreSQL 是业务数据源 |
| 原始 Word/PDF | app 的 `/data/documents` / `documents` | 独立于镜像；不暴露为静态目录 |
| 本地开发附件 | 项目 `storage/documents` | 不受启动命令工作目录影响，可用 STORAGE_ROOT 改写 |
| 备份 | 人工指定的 `backups/批次目录` | 数据库、附件、校验清单配套保存 |
| SQLite 迁移输入 | `imports/legacy.db` → 工具容器 `/imports/legacy.db` | 只读挂载，不使用 Windows 绝对路径 |

`LocalDocumentStorage` 实现 DocumentStorage 接口：保存返回随机 UUID 相对键，读取使用该键。禁止绝对路径、目录穿越及越界符号链接。以后对象存储可以实现相同接口。`uploaded_documents` 保存文件名、所属用户、SHA-256、解析器、提取正文和 session 对应关系；上传接口只接受 DOCX 与 PDF，默认上限 15 MB。

下载接口先按当前用户查询附件记录，再读取私有 storage key，不公开任意键。原始附件保留，不随某轮正文修改覆盖；提取正文经用户确认后成为 V0，后续正文版本仍保存于数据库。上传教案不会自动进入共享知识库；现有 Dataset 的 source_reference 也不会自动变成可下载附件。

卷名与 COMPOSE_PROJECT_NAME 有关。不要随意改变项目名后误以为数据丢失，也不要尝试将旧的 PostgreSQL 大版本数据目录直接挂到新版本。老 compose 没有固定项目名，其旧卷不会自动被新项目识别；有旧 PostgreSQL 数据时应先从旧实例导出恢复。

## 3. 搬迁现有 SQLite 历史

此路径与“全新数据库直接开始使用”二选一。导入要求目标业务表全部为空；任何已有用户或会话都会阻止导入，不会覆盖。迁移期间停止旧本地应用，防止快照后继续出现新记录。

```powershell
# 创建一致快照，不直接复制可能带 WAL 的活跃 db 文件
./.venv/Scripts/python.exe scripts/prepare_sqlite.py
docker compose --env-file .env.compose build app
docker compose --env-file .env.compose up -d --wait db
docker compose --env-file .env.compose run --rm migrate
docker compose --env-file .env.compose run --rm maintenance python scripts/migrate_sqlite.py /imports/legacy.db
docker compose --env-file .env.compose up -d app
```

不要在导入前打开新应用，否则 `/api/me` 会创建开发用户，目标便不再为空。如果目标之前已启动 app，先停止；对已有数据的目标使用另一个全新的 Compose 项目，而不是删除数据。

迁移要求源和目标 Alembic revision 一致；如不一致，在本机升级 **imports/legacy.db 副本** 后再导入。保留原库不动。导入在一个 PostgreSQL 事务中完成：按依赖顺序导入，保留全部 ID、时间、JSON、关系与旧快照，逐表逐行比对，任何失败回滚。它不自动变更 legacy-import 的 owner，也不会把 raw 批注提升为 verified。

验收后再决定是否停止日常使用 SQLite。原库仍可用于回退，但切换到 PostgreSQL 后的新记录不会自动同步回旧库。

## 4. 配套备份

要求 Docker 正在运行、db 可用；期间不要运行其他写入工具或开放第二个应用实例。脚本会暂停 app，生成 PostgreSQL 逻辑备份、附件归档、SHA-256 清单，然后恢复原来正在运行的 app。在 Windows/Linux 均通过 Python 二进制流传输，不使用 PowerShell 文本重定向数据库备份。

```powershell
./.venv/Scripts/python.exe scripts/compose_data.py backup backups/2026-09-30-first
```

每次使用新目录。只有包含 `manifest.json` 且校验通过的备份才算完整。失败的目录保留供检查，不作为恢复来源。备份不包含 `.env`、密钥、代码或镜像；这些另行管理。定期将备份复制到另一台设备。

## 5. 在新服务器恢复

复制项目或使用相同版本镜像、备份目录，并在新环境配置 `.env` / `.env.compose`。恢复目标必须是**空数据库（尚无表）＋空附件卷**，脚本拒绝覆盖现有环境。选择新的 COMPOSE_PROJECT_NAME 可建立独立目标卷。

```powershell
docker compose --env-file .env.compose build app
docker compose --env-file .env.compose up -d --wait db
# 此时不要运行 migrate 或启动 app
python scripts/compose_data.py restore backups/2026-09-30-first
docker compose --env-file .env.compose up -d
```

脚本先核验清单和空目标，恢复附件，再用单事务恢复数据库。失败时应用保持停止；数据库与附件不是跨系统原子事务，故应检查失败目标并使用新的空目标重试，不要直接让用户进入部分恢复的环境。成功后启动步骤再运行 Alembic，将备份升级到代码版本。

需携带**完整配套目录**，不能只搬数据库或只搬附件。恢复后核对用户历史、V0/V1、源附件下载、决策及导出。

## 6. 验收命令

```powershell
docker compose --env-file .env.compose config --quiet
docker compose --env-file .env.compose build
docker compose --env-file .env.compose up -d --wait
docker compose --env-file .env.compose run --rm maintenance python scripts/test_postgres.py
docker compose --env-file .env.compose run --rm maintenance python scripts/smoke_llm.py --postgres --profile deepseek
```

PostgreSQL 测试和真实模型 smoke 均在独立随机 schema 中运行并清理，不删除业务 schema。真实模型命令会产生供应商 API 用量，并要求至少生成一条建议。还须人工验证：新建并修订 → 重建 app 后恢复 → 备份 → 新项目恢复 → 对比历史和附件。首次真实跨库迁移需核对脚本的逐表输出以及应用页面。

部署默认仅绑定 `127.0.0.1`，数据库不发布宿主端口。当前仍使用 DevelopmentAuthProvider；容器化没有增加登录。未来公网部署还需 HTTPS、真实认证、反向代理与备份计划。本次不安装 Docker、不开放公网、不启动云资源。
