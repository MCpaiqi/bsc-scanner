# 可复现安装、检查与交付

Python 的兼容范围仍写在 `pyproject.toml`；可重复安装使用
`requirements/dev.lock` 或 `requirements/runtime.lock` 中的精确版本、平台条件和 SHA256。
两个锁都包含构建工具。开发依赖、运行依赖和构建声明由 manifest 指纹核对；
Git 的 CRLF/LF 转换不会造成误报。检查器只用 Python 标准库，不安装依赖。

## 干净环境

使用新的虚拟环境（Python 3.12 或 3.13），在项目根目录运行：

```bash
python scripts/lock_dependencies.py check
python -m pip install --require-hashes --only-binary=:all: -r requirements/dev.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
python scripts/test.py -q --tb=short
```

可用 `python -m venv .venv` 新建环境，然后在 Windows 激活 `.venv\Scripts\Activate.ps1`，
在 POSIX 激活 `source .venv/bin/activate`。保留已有环境，不用清理其他人的环境来排查问题。
CI 不带本地 `.env`，不提供链上 provider 凭证，显式关闭实盘。
本地如存在 `.env`，配置仍按正常设置加载；离线复现应使用干净导出目录和独立测试服务。
`test.ps1`、`test.sh` 是同一检查入口的包装器。入口不调用 pip install 或 uv sync。

前端使用 `.node-version` 指定的 Node 24.15.0（随附 npm 11.12.1）。
这是当前 jsdom 30.0.1 支持的 Node24 最低补丁版本；`web/.npmrc` 会拒绝不兼容的引擎。
直接依赖固定在既有锁图的版本：

```bash
npm --prefix web ci --no-audit --no-fund
npm --prefix web test -- --reporter=dot
npm --prefix web run build
```

## 显式服务测试

SQLite 检查会跳过需要 PostgreSQL、Redis 或真实链上网络的测试。
在**专用、可丢弃**数据库和 Redis 上设置以下变量后再次运行同一入口：

- `SMMS_RESEARCH_TEST_DATABASE_URL`：PostgreSQL 的 SQLAlchemy psycopg2 URL。
- `SMMS_MIGRATION_TEST_DATABASE_URL`：隔离迁移测试数据库的同类 URL。
- `SMMS_REDIS_TEST_URL` 和 `REDIS_URL`：隔离 Redis URL。

测试按唯一 schema 创建和删除自己的数据库对象；Redis 测试会主动断开测试连接。
这些地址不能指向生产服务。GitHub CI 使用临时 PostgreSQL16 / Redis7 service containers，
先升级全新数据库到迁移 head，再以 Asia/Hong_Kong 数据库时区运行完整测试。
链上网络测试保持显式 opt-in，不以它们的跳过来证明 provider 可用。

## 依赖更新

普通检查和安装不需要 uv。只有修改依赖时，在独立工具环境安装 `uv==0.12.5` 后执行：

```bash
python scripts/lock_dependencies.py update
# 明确要升级锁图时：
python scripts/lock_dependencies.py update --upgrade
python scripts/lock_dependencies.py check
```

解析器固定公共 PyPI、禁止自动下载 Python，在临时目录从 build-system.requires 生成构建输入。
它不创建或修改项目 `uv.lock` / `.venv`。默认保留已有锁的偏好；`--constraints PATH`
只用于显式约束迁移。更新产生两个锁和 manifest，需一起审阅并重新跑干净安装、服务测试及 CI。
前端变更需同时审阅 package.json 和 package-lock.json，保持直接版本精确并运行 npm ci。
仅更改格式或工具设置不需要重解依赖。

## 可检查的产物

```bash
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
python scripts/build_release.py --output ../artifacts/smms-source-v1
python scripts/release_audit.py ../artifacts/smms-source-v1
```

wheel 保留 Python 模块、迁移和 legacy static。React dist 与 configs 是应用部署输入，
由 Dockerfile 复制到 `/app/web/dist` 和 `/app/configs`，不是 wheel 自带资源。
source ZIP 包含配置、Python/npm 锁和源代码，排除 tests、node_modules、生成目录和私有运行数据。
在 Git checkout 中只读取已跟踪文件；已导出的 source 目录可在没有 Git 时构建。
checkout 缺少 Git、Git 发现失败或 source root 位于另一 checkout 内时，拒绝退化成全目录打包。
导出目录需放在 checkout 之外。发布前先提交或明确检查暂存区中的输入。

source manifest 列出实际文件 SHA256；干净 checkout 可记录 source_commit，导出目录或修改过的
checkout 为 null。它是可核对的输入清单，不是签名或生产部署证明；构建期间不要并发修改源文件。
ZIP 固定顺序、时间戳和权限；相同字节的输入在同一压缩实现下可重复输出。
现有输出不可覆盖，核验完全相同的重复构建才复用旧产物，否则选择新的 `--output`。
release_pipeline 依次检查锁、后端、Ruff、前端测试/构建、source build/audit；
默认生成唯一产物目录，`--skip-frontend` 的结果只代表后端门禁。

## GitHub CI 范围

`.github/workflows/research-verification.yml` 对 main/master/codex/** 的 push 和 PR 执行：
Ubuntu Python3.12/3.13、Windows Python3.13、真实 PostgreSQL16/Redis7、Node 前端，
以及 source/wheel/runtime/web 镜像构建和 HTTP smoke。Actions 与基础镜像按不可变引用固定。
checkout 不保留凭证；workflow 只有 contents:read 权限，不推镜像、不部署。
source/wheel 产物关联该次提交 SHA。

只有对应 SHA 的实际运行成功，才能称 CI 已验证。容器 smoke 不是完整 Compose 拓扑、
持久卷重启或生产链上验证；这些继续由 RESEARCH_RELIABILITY.md 的 R13/R14 跟踪。
固定依赖与镜像摘要也不等同跨 OS/CPU 的逐字节相同镜像，需定期显式更新和重验。
