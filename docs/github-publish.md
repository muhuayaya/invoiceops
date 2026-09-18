# GitHub 发布清单

本文用于把当前工作区整理为一个可公开审阅的 GitHub 仓库。当前项目没有配置远程仓库，因此不会在本地自动推送到未知地址。

## 1. 发布前检查

在项目根目录执行：

```powershell
git status --short
git status --ignored --short
git check-ignore -v .env ml/artifacts/xlmr-v3/model.safetensors node_modules
```

确认以下内容没有被提交：

- `.env`、JWT 密钥、数据库密码和 SSH 私钥；
- `node_modules/`、`.venv/`、`dist/` 和缓存；
- `ml/artifacts/` 下的模型权重；XLM-R 权重体积大且当前不随 API 镜像部署；
- `ml/data/processed/`、`ml/data/generated/` 下的本地生成数据；
- IDE 配置和本地 Codex 辅助文件。

仓库提交的是可复现的代码、配置模板、脱敏示例、评估记录和部署文档，不是本机运行产生的秘密或大体积制品。

## 2. 本地检查门禁

```powershell
python -m pytest -q
ruff check apps src ml tests scripts
npm ci --ignore-scripts
npm run check
npm run build
openspec validate --all --strict --json
```

有 Docker 的机器再执行：

```powershell
docker compose config
docker compose up --build -d
curl.exe --fail http://localhost:8000/healthz
curl.exe --fail http://localhost:8000/readyz
curl.exe --fail http://localhost:3000/
docker compose down
```

## 3. 创建首个提交

确认 `git status` 中没有不应发布的文件后：

```powershell
git add -A
git diff --cached --check
git diff --cached --stat
git commit -m "chore: prepare InvoiceOps PoC for GitHub"
git branch -M main
```

## 4. 关联并推送 GitHub 仓库

先在 GitHub 创建一个空仓库，不要自动生成 README、License 或 `.gitignore`，然后把地址替换为自己的仓库地址：

```powershell
git remote add origin https://github.com/<账号>/<仓库名>.git
git push -u origin main
```

如果已经存在 `origin`：

```powershell
git remote set-url origin https://github.com/<账号>/<仓库名>.git
git push -u origin main
```

推送前要确认 GitHub 仓库使用 HTTPS token 或 SSH key 认证。不要把 token 写入脚本、`.env` 或文档。

## 5. GitHub 仓库设置建议

- 将默认分支设为 `main`；
- 开启 Actions；
- 保护 `main`，至少要求 CI 通过后再合并；
- 开启 Dependabot 或定期依赖升级；
- 如果需要部署，把生产环境变量配置在 GitHub Environments/Secrets，而不是仓库文件；
- 在仓库首页明确标注：这是 PoC，线上默认使用规则 fallback，XLM-R 目前是离线候选模型。

项目功能、Docker 启动和演进方向分别见 [本机 Docker 部署指南](deployment-local-docker.md)、[项目使用手册](使用手册-v1.md) 和 [后续演进路线](roadmap-v1.md)。
