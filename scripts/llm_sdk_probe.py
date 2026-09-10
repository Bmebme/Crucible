"""内网 LLM 服务 openai SDK 行为探针 (不经过 crucible 代码)。

背景: 内网 RAG (LightRAG) 用 openai SDK 调同一 LLM 服务没问题,
crucible 自建 httpx 客户端观察到的行为不同 (思考过程进正文)。
本脚本直接对比 SDK 的四种请求形态, 定位差异来源。

用法 (内网, crucible 仓库目录):
  python scripts/llm_sdk_probe.py <base> <key> <model> [问题]

  base 填根地址 (SDK 自动拼 /chat/completions), 与 RAG 侧一致。
"""
import asyncio
import sys
import time

from openai import AsyncOpenAI


async def probe(client, label, question, stream, extra_body=None):
    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": question}],
        stream=stream,
    )
    if extra_body:
        kwargs["extra_body"] = extra_body
    t0 = time.monotonic()
    try:
        if stream:
            parts, rc_parts = [], []
            s = await client.chat.completions.create(**kwargs)
            async for ch in s:
                if not ch.choices:
                    continue
                d = ch.choices[0].delta
                if d is None:
                    continue
                rc = getattr(d, "reasoning_content", None)
                if not rc:
                    rc = (getattr(d, "model_extra", None) or {}).get("reasoning_content")
                if rc:
                    rc_parts.append(str(rc))
                if d.content:
                    parts.append(d.content)
            content = "".join(parts)
            rc = "".join(rc_parts)
        else:
            r = await client.chat.completions.create(**kwargs)
            m = r.choices[0].message
            content = m.content or ""
            rc = getattr(m, "reasoning_content", None) or ""
        dt = time.monotonic() - t0
        print(f"\n[{label}] {dt:.1f}s")
        print(f"  reasoning_content: {len(rc)} 字符 {rc[:80]!r}")
        print(f"  content:          {len(content)} 字符 {content[:200]!r}")
    except Exception as e:
        print(f"\n[{label}] 异常 ({time.monotonic() - t0:.1f}s): {type(e).__name__}: {e}")


async def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    base, key = sys.argv[1], sys.argv[2]
    global model
    model = sys.argv[3]
    question = sys.argv[4] if len(sys.argv) > 4 else "1+1等于几？只回答数字"
    client = AsyncOpenAI(base_url=base, api_key=key or "EMPTY", timeout=120.0)
    print(f"base={base}  model={model}  question={question!r}")
    await probe(client, "① 非流式 无参数", question, stream=False)
    await probe(client, "② 流式   无参数", question, stream=True)
    await probe(
        client, "③ 非流式 thinking=disabled", question,
        stream=False, extra_body={"thinking": {"type": "disabled"}},
    )
    await probe(
        client, "④ 流式   thinking=disabled", question,
        stream=True, extra_body={"thinking": {"type": "disabled"}},
    )


if __name__ == "__main__":
    asyncio.run(main())
