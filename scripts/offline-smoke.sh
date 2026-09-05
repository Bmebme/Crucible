#!/bin/bash
# 离线冒烟测试: 断网容器里跑完整摄入链路, 抓"偷偷联网"的库
# (hf-mirror / openaipublic / files.pythonhosted 等内网杀手)
#
# 用法 (基础镜像重打后、摆渡前必跑):
#   bash scripts/offline-smoke.sh [镜像=deploy-crucible:amd64-cpu]
#
# 通过标准: EXIT=0 —— bge/tiktoken/LightRAG-init 全部离线可用;
#   ainsert 的失败必须且只能是"连接 LLM 失败" (预期), 不是隐性依赖错误
set -e
IMG="${1:-deploy-crucible:amd64-cpu}"

echo "== 离线冒烟: $IMG (--network none, 部署等价挂载) =="
# 部署等价: 内网部署脚本挂 tiktoken 缓存 + TIKTOKEN_CACHE_DIR env。
# 实调发现: LightRAG 构造器本身 (lightrag.py:1344 __post_init__ →
# utils.py TokenCounter) 就会调用 tiktoken —— 挂载/缓存不对, init 即
# 联网炸, 不是只有 ainsert 阶段才用。
docker run --rm --network none -i \
  -v "$HOME/.cache/tiktoken:/root/.cache/tiktoken" \
  "$IMG" python - <<'PYEOF'
import asyncio
import os
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TIKTOKEN_CACHE_DIR"] = "/root/.cache/tiktoken"

FAILURES: list[tuple[str, Exception]] = []


def check(name: str, fn) -> None:
    t0 = time.time()
    try:
        fn()
        print(f"  ✓ {name} ({time.time() - t0:.1f}s)")
    except Exception as e:
        FAILURES.append((name, e))
        print(f"  ✗ {name}: {type(e).__name__}: {str(e)[:200]}")


def embed_load() -> None:
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    v = m.encode(["离线冒烟测试"])
    assert v.shape == (1, 512), f"维度异常: {v.shape}"


def tiktoken_cached() -> None:
    import tiktoken
    for name in ("cl100k_base", "o200k_base"):
        enc = tiktoken.get_encoding(name)
        assert len(enc.encode("离线冒烟测试")) > 0


def rag_init() -> None:
    async def _go() -> None:
        from crucible.config import Config
        from crucible.engines.rag_engine import RagEngine
        cfg = Config()
        cfg.rag_workdir = "/tmp/ragtest-smoke"
        eng = RagEngine(cfg)
        assert await eng.ensure_ready("/tmp/ragtest-smoke"), "ensure_ready False"
    asyncio.run(_go())


check("bge 离线加载+编码 (烘焙缓存)", embed_load)
check("tiktoken 双编码离线命中 (缓存+env)", tiktoken_cached)
check("LightRAG 全新 workdir init", rag_init)

# 4. ainsert 断网探针: LLM 指向不可达地址 —— 失败必须是"连 LLM 失败"
#    (预期), 若失败原因含 tiktoken/HF/网络下载则视为隐性依赖 bug。
#    注意: LightRAG 的 ainsert 会内部吞掉 LLM 失败 (重试后
#    "Failed to extract document"), ingest 仍返回 True —— 所以本步只
#    看 traceback 里的异常来源, 不断言返回值。
print("  … ainsert 断网探针 (预期失败于连接 LLM, 看日志 traceback):")
from crucible.config import Config
from crucible.engines.rag_engine import RagEngine

async def _probe() -> None:
    cfg = Config(llm_base="http://127.0.0.1:9/v1", llm_api_key="x", llm_model="smoke")
    cfg.rag_workdir = "/tmp/ragtest-smoke"
    eng = RagEngine(cfg)
    assert await eng.ensure_ready("/tmp/ragtest-smoke")
    ok = await eng.ingest("/tmp/ragtest-smoke", "离线冒烟: 一台设备产生一条告警。", "smoke.md")
    print(f"  ingest 返回 {ok} (预期 False); 上方 traceback 应为 ConnectError/127.0.0.1:9")

asyncio.run(_probe())

print()
if FAILURES:
    print("SMOKE FAILED:")
    for name, e in FAILURES:
        print(f"  - {name}: {e}")
    sys.exit(1)
print("SMOKE PASSED: 离线路径全通, 无隐性网络依赖")
PYEOF
