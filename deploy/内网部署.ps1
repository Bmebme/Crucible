# Crucible 内网一键部署 (拖包版)
# 用法: 把本脚本与三个 tar 放在同一目录, 填好下方【部署参数】, PowerShell 执行:
#   powershell -ExecutionPolicy Bypass -File .\内网部署.ps1
# 前置: Docker Desktop 运行中; 内网 LLM 端点可达

# ============ 部署参数 (按你的环境修改) ============
$LLM_BASE   = "http://<内网LLM地址>/v1"
$LLM_API_KEY = "<内网LLM key>"
$LLM_MODEL  = "deepseek-chat"
$DATA_ROOT  = "D:\kb-data"          # 项目数据目录 (llm-wiki 与 crucible 共享挂载)
$LLM_WIKI_STATE = "D:\kb-state"     # llm-wiki 状态目录 (app-state 等)
$PG_HOST    = "host.docker.internal" # postgres 地址 (容器内视角)
$PG_URL     = "postgresql+asyncpg://crucible:crucible@${PG_HOST}:5432/crucible"
$PIP_SOURCE = ""   # 内网 pip 源, 空=不覆盖
$PIP_TRUSTED_HOST = ""  # 内源是 http 时必填主机名
# ==================================================

$ErrorActionPreference = "Stop"
Write-Host "[1/6] 加载镜像" -ForegroundColor Cyan
foreach ($t in @("py-llm-wiki-amd64.tar", "crucible-cpu.tar", "docreader-final.tar")) {
    if (Test-Path $t) { docker load -i $t } else { Write-Host "  ⚠ 缺少 $t (跳过)" -ForegroundColor Yellow }
}
# postgres: 有 tar 用 tar, 没有则从内网 registry 拉
if (Test-Path "postgres-amd64.tar") { docker load -i postgres-amd64.tar }
else { Write-Host "  ⚠ 未找到 postgres-amd64.tar, 假设内网 registry 已有 postgres:16-alpine-amd64 或手动 load" -ForegroundColor Yellow }

Write-Host "[2/6] 启动 postgres" -ForegroundColor Cyan
docker rm -f crucible-pg 2>$null
docker run -d --name crucible-pg --restart unless-stopped `
  -e POSTGRES_USER=crucible -e POSTGRES_PASSWORD=crucible -e POSTGRES_DB=crucible `
  -p 5432:5432 -v crucible-pg-data:/var/lib/postgresql/data `
  postgres:16-alpine-amd64 | Out-Null

Write-Host "[3/6] 启动 llm-wiki (含 UI, 同端口 19828)" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $LLM_WIKI_STATE | Out-Null
$appState = Join-Path $LLM_WIKI_STATE "app-state.json"
if (-not (Test-Path $appState)) {
    @'
{
  "projectRegistry": {},
  "apiConfig": {"allowUnauthenticated": true, "allowLanAccess": true}
}
'@ | Out-File -Encoding utf8 $appState
    Write-Host "  → 已生成 $appState (项目注册见【注册项目】)" -ForegroundColor Yellow
}
# llmConfig 直接写进 app-state: deploy 参数是唯一事实源, UI 设置页读 state 而非 env
$state = @{}
if (Test-Path $appState) { $state = Get-Content $appState -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable }
$state["llmConfig"] = @{
    provider = "custom"; apiMode = "chat_completions"
    customEndpoint = $LLM_BASE; apiKey = $LLM_API_KEY; model = $LLM_MODEL
}
$state | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 $appState
Write-Host "  → llmConfig 已写入 $appState" -ForegroundColor Yellow
docker rm -f crucible-llmwiki 2>$null
docker run -d --name crucible-llmwiki --restart unless-stopped `
  -p 19828:19828 `
  -v "${LLM_WIKI_STATE}:/data" -v "${DATA_ROOT}:/projects" `
  -e "LLM_WIKI_LLM_BASE=$LLM_BASE" -e "LLM_WIKI_LLM_API_KEY=$LLM_API_KEY" -e "LLM_WIKI_LLM_MODEL=$LLM_MODEL" `
  py-llm-wiki:amd64 | Out-Null

Write-Host "[4/6] 启动 docreader (MinerU)" -ForegroundColor Cyan
docker rm -f crucible-docreader 2>$null
docker run -d --name crucible-docreader --restart unless-stopped -p 8081:8081 `
  deploy-docreader:amd64-squashed | Out-Null

Write-Host "[5/6] 启动 crucible 融合服务" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "$DATA_ROOT" | Out-Null
docker rm -f crucible-app 2>$null
$pipArg = @()
if ($PIP_SOURCE) { $pipArg += @("-e", "PIP_INDEX_URL=$PIP_SOURCE") }
if ($PIP_TRUSTED_HOST) { $pipArg += @("-e", "PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST") }
docker run -d --name crucible-app --restart unless-stopped `
  -p 8080:8080 `
  @pipArg `
  -e "CRUCIBLE_DATABASE_URL=$PG_URL" `
  -e "CRUCIBLE_WIKI_BASE=http://host.docker.internal:19828" `
  -e "CRUCIBLE_DOCREADER_BASE=http://host.docker.internal:8081" `
  -e "CRUCIBLE_LLM_BASE=$LLM_BASE" -e "CRUCIBLE_LLM_API_KEY=$LLM_API_KEY" -e "CRUCIBLE_LLM_MODEL=$LLM_MODEL" `
  -e "HF_HUB_OFFLINE=1" `
  -v "${DATA_ROOT}:/data" `
  deploy-crucible:amd64-cpu | Out-Null

Write-Host "[6/6] 健康检查" -ForegroundColor Cyan
Start-Sleep -Seconds 12
Write-Host ("  crucible:  " + (Invoke-RestMethod http://localhost:8080/health).status)
Write-Host ("  llm-wiki:  " + (Invoke-RestMethod http://localhost:19828/health).status)
Write-Host ("  docreader: " + (Invoke-RestMethod http://localhost:8081/health).status)

Write-Host ""
Write-Host "部署完成。接下来:" -ForegroundColor Green
Write-Host "  1. llm-wiki 建项目: 开 http://localhost:19828 → New Project → 路径 /projects/<产品名>"
Write-Host "     (项目目录必须含 schema.md, 从现有项目复制)"
Write-Host "  2. 拿到项目稳定 id: GET http://localhost:19828/api/v1/projects (uuid, 勿用 current)"
Write-Host "  3. crucible 注册项目:"
Write-Host "     curl -X POST http://localhost:8080/projects -H 'Content-Type: application/json' -d '{\"id\":\"<产品名>\",\"path\":\"/data/<产品名>\",\"wiki_project_id\":\"<uuid>\",\"rag_workdir\":\"/data/<产品名>/.lightrag\"}'"
Write-Host "  4. 打开融合台: http://localhost:8080 (上传文档走 MinerU 双通道)"
