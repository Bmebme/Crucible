# Crucible 部署指南

> 2026-09-04 更新。本文覆盖两种形态: 本机快速启动 (当前可用) 与服务器部署
> (含已知缺口)。架构与决策见 [engineering-plan.md](engineering-plan.md)。

## 1. 架构总览

```
浏览器 ──→ crucible:8080 (融合服务+UI) ─┬─→ llm-wiki daemon:19828 (页面引擎, 当前为宿主机进程)
                                        ├─→ postgres:5432 (元数据层)
                                        ├─→ docreader:8081 (MinerU 文档解析)
                                        └─→ LightRAG (进程内, 数据在 <project>/.lightrag-*)
```

依赖关系: crucible 是唯一入口; llm-wiki 与 docreader 是它的两个后端服务;
PG 存元数据; 项目数据目录 (wiki 文件 + LightRAG 工作目录) 被挂载进 crucible。

## 2. GitHub 一键启动 (推荐路径)

```bash
git clone <repo> && cd crucible/deploy
./bootstrap.sh
# 首次: 生成 .env → 填入 LLM_API_KEY → 再跑一次 ./bootstrap.sh
```

bootstrap.sh 自动完成: .env 检查 → 基础镜像经 daocloud 绕 Docker Hub →
构建 (crucible + docreader) → 启动 → 健康检查 → 打印注册项目提示。

**一键的边界** (仍需手工):
- `.env` 里的 LLM_API_KEY (secrets 不入库)
- llm-wiki daemon (py-llm-wiki 仓库, 未容器化)
- 项目数据 (wiki 文件 + `.lightrag-*`)

**docreader 两档**:
- 默认: markitdown 轻量版 (构建 ~5 分钟, 解析质量一般)
- 完整 MinerU: 按 §3.3 生成 `deploy-docreader:with-models` 镜像后,
  在 `.env` 设 `DOCREADER_FULL_IMAGE=deploy-docreader:with-models`,
  `docker compose up -d docreader` 即切换 (crucible 无需改动)

## 2.1 本机快速启动 (当前机器, 已验证)

```bash
# 0. 前置: Docker Desktop 运行中; llm-wiki daemon 跑在本机 19828 (py-llm-wiki)

cd deploy
cp .env.example .env        # 填 LLM_API_KEY; 检查 HF_CACHE_HOST/DATA_ROOT 路径
docker compose up -d        # 起 postgres + crucible + docreader

# 1. 注册项目 (注意: 容器内路径 = /data/<项目名>, 即 DATA_ROOT/<项目名>)
curl -X POST http://127.0.0.1:8080/projects -H 'Content-Type: application/json' \
  -d '{"id":"mae","path":"/data/mae","wiki_project_id":"current",
       "rag_workdir":"/data/mae/.lightrag-zh2"}'

# 2. 打开 UI
open http://127.0.0.1:8080
```

镜像说明: 本机 docreader 用的是 `deploy-docreader:with-models`
(docker commit 自实测容器, 模型已烧入); crucible 由 Dockerfile 构建。

## 3. 服务器部署 (新机器)

### 3.1 已知缺口 (按顺序解决)

1. **镜像可移植性** — `deploy-docreader:with-models` 是本地 commit 产物,
   不在仓库里。服务器上需要: `docker compose build docreader` (Dockerfile
   配方已实测) + 模型下载 (见 3.3)。或将镜像推送到私有 registry。
2. **llm-wiki daemon 未容器化** — 当前是宿主机进程 (py-llm-wiki 仓库
   clone + conda/venv 安装 + 19828 启动)。容器化列为后续任务; 服务器上
   先按 py-llm-wiki 的 README 部署 daemon, crucible 通过
   `CRUCIBLE_WIKI_BASE=http://<llm-wiki-host>:19828` 访问。
3. **项目数据迁移** — wiki 文件 + `.lightrag-*` 工作目录需拷贝到服务器
   DATA_ROOT 下; PG 元数据 (projects 表) 可重新注册, 台账/审核队列如需
   保留则 pg_dump 迁移。

### 3.2 步骤

```bash
# 0. 前置: Docker + docker compose; 网络需要能访问
#    pypi.tuna / mirrors.aliyun / hf-mirror / modelscope.cn (见下)

git clone <repo> && cd crucible/deploy
cp .env.example .env && vim .env   # LLM_API_KEY, HF_CACHE_HOST, DATA_ROOT

docker pull docker.m.daocloud.io/library/postgres:16-alpine   # Docker Hub 被墙时
docker tag docker.m.daocloud.io/library/postgres:16-alpine postgres:16-alpine
docker pull docker.m.daocloud.io/library/python:3.12-slim
docker tag docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim

docker compose build   # crucible + docreader (首次 ~30-60 分钟, 含 torch 下载)
docker compose up -d postgres
```

### 3.3 MinerU 模型 (一次性, 有坑已踩平)

HF 镜像缺 5 个模型目录的 LFS 文件 (TabRec/TabCls/MFD/OriCls/ReadingOrder),
ModelScope 有全量但目录级下载 API 有缺陷。补救脚本已入库:

```bash
# 1. 先跑一次解析触发基础模型下载 (Layout/MFR/OCR)
docker exec -i deploy-docreader-1 curl -s -F "file=@<任意.pdf>" http://localhost:8081/parse > /dev/null

# 2. 补全缺失目录 (~3GB, 含 1.87GB StructEqTable safetensors)
docker cp scripts/fetch_mineru_models.py deploy-docreader-1:/tmp/
docker exec -i deploy-docreader-1 python3 /tmp/fetch_mineru_models.py

# 3. 把容器连同模型 commit 成本地镜像 (省去下次下载)
docker commit deploy-docreader-1 deploy-docreader:with-models
```

### 3.4 注册项目 + 验证

```bash
curl -X POST http://localhost:8080/projects -H 'Content-Type: application/json' \
  -d '{"id":"<name>","path":"/data/<name>","wiki_project_id":"<llm-wiki 侧 id>"}'
curl -s http://localhost:8080/health      # wiki ok + rag ready
curl -s -X POST http://localhost:8080/fusion/enum -d '{"hint":"组件","project_id":"<name>"}' -H 'Content-Type: application/json'
```

## 4. 网络与镜像源约定 (国内网络环境实测)

| 资源 | 约定 |
|---|---|
| pip | crucible/Dockerfile 用 tuna; docreader 大包 (torch) 走 tuna, 其余 aliyun (aliyun 的 torch 校验和坏包, 已避开) |
| HF 模型 | HF_ENDPOINT=https://hf-mirror.com + 缓存目录绑定; 缺失 LFS 走 ModelScope 补全脚本 |
| Docker Hub | 被墙时走 docker.m.daocloud.io 拉取后本地 tag |
| GitHub | 间歇性阻断, 推送失败重试即可 |
| LLM | DeepSeek, deploy/.env 统一配置 (唯一事实源) |

## 5. 常见问题

1. **查询返回空/rag=0** — 项目 path 是容器内路径 (/data/...) 但容器里不存在
   → 检查 DATA_ROOT 绑定与注册路径 (本地/容器命名空间不同, 见
   engineering-plan 进度表备注)。
2. **docreader 解析失败** — 看 `docker logs deploy-docreader-1`: 模型缺失
   (NoSuchFile) → 跑 3.3 补全; OOM → mem_limit 已是 6g, 换更小的 PDF 或
   增大 Docker Desktop 内存。
3. **首次 enum 慢 (~10s)** — 正常的 LightRAG 模型加载, 之后热缓存 ~5s。
4. **M2 无结论** — LLM 不可用或两引擎无召回, 降级并列输出是设计行为
   (响应 notes 里有说明)。

## 6. 待办 (部署侧)

- [ ] llm-wiki daemon 容器化 (Dockerfile + env 覆盖 llmConfig)
- [ ] 镜像推送私有 registry (免服务器重复构建)
- [ ] torch CPU-only 构建 (镜像 9.88GB → ~6GB)
- [ ] PG 备份脚本 (pg_dump + 项目目录打包)
- [ ] SSO 接入 (待组织方案)

## 7. 内网部署 (离线摆渡)

内网通常无外网, 用 pack-offline.sh 打成单目录拷入:

```bash
# 外网机器 (本机):
scripts/pack-offline.sh                  # 产出 offline-bundle/
# 拷入内网 (scp/U盘/内网中转机):
# 内网机器:
./restore-intranet.sh                    # 恢复镜像+缓存+代码
cd crucible/deploy && cp .env.example .env
# 编辑 .env: LLM_BASE 指向内网 LLM 端点 (OpenAI 兼容), 见下
docker compose up -d
```

**内网前提 (部署前必须确认)**:
1. **镜像架构一致**: 本机打包是 arm64 (Apple Silicon); 内网若是 x86_64 服务器,
   打包前重建: `docker compose build --platform linux/amd64` (首次约 40 分钟,
   torch 重新拉 amd64 轮子)
2. **内网 LLM 端点**: 判别兜底/M2 比对/L3 消解/导读/摄入抽取都需要
   OpenAI 兼容的 LLM 接口; 无则这些能力静默降级 (规则判别/M1 仍可用)
3. **内网 LightRAG demo**: 若启用, 把 rag_engine 换 HTTP 客户端指向它
   (待办: 同契约替换)

## 8. crucible 镜像的内源清单 (内网构建用)

crucible 镜像 (deploy/Dockerfile) 构建下载三类东西:

### 8.1 基础镜像 + apt

```
python:3.12-slim (Docker Hub → 内网 registry 代理)
apt: libgomp1 curl (debian trixie, 无 build-essential —— wheel 安装足够)
```

### 8.2 pip 包 (115 个, 大头清单)

| 大头 | 大小 | 说明 |
|---|---|---|
| torch 2.14.0 | ~850MB | linux/amd64 默认捆绑 CUDA |
| nvidia-cudnn-cu13 9.24.0.43 | 651MB | CUDA 链 (后续 CPU-only 瘦身可省) |
| nvidia-cublas-cu13 13.1.1.3 | 543MB | 同上 |
| nvidia-cusparselt-cu13 0.8.1 | 221MB | 同上 |
| nvidia-nccl-cu13 2.30.7 | 216MB | 同上 |
| nvidia-curand/nvshmem/cufft/cusolver/cusparse/nvjitlink/nvtx/cuda-runtime/cuda-nvrtc/cuda-cupti/cufile + cuda-toolkit + cuda-bindings | ~300MB | CUDA 依赖链完整集合 |

功能依赖 (版本取自 linux 解析): lightrag-hku 1.5.7, sentence-transformers,
transformers 5.x, tokenizers, safetensors, huggingface-hub, scikit-learn, scipy,
numpy, pandas, networkx, tiktoken, aiohttp, nano-vectordb, pipmaster, google-genai,
google-api-core/google-auth/protobuf/grpc 链, fastapi/starlette/uvicorn, sqlalchemy/
asyncpg/alembic/greenlet, pydantic/pydantic-core, httpx, python-multipart, pyyaml,
typer/rich, triton, sympy, regex, xlsxwriter, pypinyin, json_repair, tenacity,
python-dotenv, requests/urllib3, yarl/multidict/frozenlist/propcache (aiohttp 链)

**内源配置**: 若内源是全量 PyPI 代理 (Nexus/Artifactory/devpi), 无需手工清单,
`ENV PIP_INDEX_URL=http://<内源>/simple` 即自动; 若手工同步, 注意 CUDA 链
(nvidia-*) 与 torch 轮子必须取 linux/amd64 的 manylinux 版本, 且**大包要在
内源预热缓存** (首次构建 4GB 下载量, 内源缓存后分钟级)。

### 8.3 运行时模型 (非构建期, 摆渡包已含)

bge-small-zh-v1.5 嵌入 (HF 缓存) + tiktoken cl100k_base + MinerU 模型
(docreader 镜像已烧入) —— 见 §7 离线摆渡。
