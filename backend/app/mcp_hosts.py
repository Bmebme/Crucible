"""MCP Host 白名单 (配置文件驱动, 支持热改)。

背景: mcp SDK 的 FastMCP 在 host=127.0.0.1 时会自动开启 DNS rebinding
保护, 且白名单**只含本机** -> 内网其他机器用 http://<内网IP>:8080/mcp
访问会被判 421 "Invalid Host header" (页面不受影响, 所以现象是"页面能开、
MCP 连不上")。

这里接管该校验 (SDK 侧关掉), 换成配置文件驱动、支持前缀匹配的实现:

配置文件: $CRUCIBLE_MCP_HOSTS_FILE
        -> /data/mcp_allowed_hosts.txt (容器, /data 是宿主挂载目录)
        -> <仓库根>/mcp_allowed_hosts.txt (本地开发)
不存在时自动生成一份带注释的默认配置, 改完**重启即生效**(无需重建镜像)。

规则 (每行一条, # 开头为注释, 空行忽略):
    *                 放行全部主机 (等价于关掉校验)
    10.0.0.0/8        网段 (CIDR, 推荐): 该段内所有地址
    10.1.             前缀匹配: 主机名/IP 以该串开头 (端口忽略)
    192.168.1.23      精确匹配 (端口忽略)
    192.168.1.23:8080 精确匹配且端口一致
"""
from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_HOSTS_FILE = "/data/mcp_allowed_hosts.txt"

# 本机永远放行 (无论配置怎么写, 避免把自己锁在外面)
ALWAYS_ALLOW = ("127.0.0.1", "localhost", "::1", "[::1]")

DEFAULT_CONTENT = """\
# Crucible MCP Host 白名单
# ------------------------------------------------------------------
# 作用: 决定哪些地址可以访问 /mcp 端点 (客户端连 http://<地址>:8080/mcp)。
# 页面 (前端) 不受此限制, 但 MCP 端点受——这是 mcp SDK 的 DNS rebinding
# 防护默认行为 (只认本机), 所以内网其他机器访问会被判 421。
#
# 规则: 每行一条, '#' 开头是注释, 空行忽略
#   *                  放行全部主机 (最省事, 内网可信时可用)
#   10.1.2.0/24        网段 (CIDR, 推荐): 该段内所有地址都放行
#   192.168.1.23       精确匹配主机名/IP (端口忽略)
#   10.0.0.5:8080      精确匹配且端口也要一致
#
# 改完重启后端容器即生效, 不需要重新构建镜像。
# 收紧办法: 把下面不需要的网段删掉, 只留实际要用的地址。
# ------------------------------------------------------------------
127.0.0.1
localhost
::1
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
100.64.0.0/10
"""


def hosts_file_path() -> Path:
    """配置文件路径: 环境变量 > /data(容器) > 仓库根(开发)。"""
    env = os.environ.get("CRUCIBLE_MCP_HOSTS_FILE", "").strip()
    if env:
        return Path(env)
    data = Path(DEFAULT_HOSTS_FILE)
    if Path("/data").is_dir() and os.access("/data", os.W_OK):
        return data
    return Path(__file__).resolve().parent.parent.parent / "mcp_allowed_hosts.txt"


def ensure_hosts_file(path: Path | None = None) -> Path:
    """配置文件不存在时生成默认版 (失败只告警, 不影响启动)。"""
    p = path or hosts_file_path()
    try:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(DEFAULT_CONTENT, encoding="utf-8")
            logger.info("mcp hosts 配置已生成: %s", p)
    except OSError as e:  # pragma: no cover - 只读挂载等
        logger.warning("mcp hosts 配置生成失败 (%s), 使用内置默认规则", e)
    return p


def load_rules(path: Path | None = None) -> list[str]:
    """读取规则; 文件缺失/为空时回落到内置默认规则。"""
    p = path or hosts_file_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        raw = DEFAULT_CONTENT
    rules = [
        ln.strip() for ln in raw.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return rules or ["127.0.0.1", "localhost", "::1"]


def _bare_host(host: str) -> str:
    """从 Host 头取主机部分 (去端口): '1.2.3.4:8080' / '[::1]:8080' / '::1'。"""
    if host.startswith("["):                       # 方括号 IPv6
        return host[1:host.index("]")] if "]" in host else host
    if host.count(":") == 1:                       # 只有一段冒号 = host:port
        return host.split(":", 1)[0]
    return host                                    # 裸 IPv6 / 裸主机名


def _is_ipv6_literal(s: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(s), ipaddress.IPv6Address)
    except ValueError:
        return False


def _match_rule(host: str, bare: str, rule: str) -> bool:
    r = rule.lower().strip()
    if r == "*":
        return True
    if "/" in r:                                   # CIDR 网段
        try:
            net = ipaddress.ip_network(r, strict=False)
            return ipaddress.ip_address(bare) in net
        except ValueError:
            return False
    if ":" in r and not _is_ipv6_literal(r):       # host:port 精确匹配
        return host == r
    if r.endswith("."):                            # 前缀匹配 (含末尾点, 避免 100.1000 命中 100.100.)
        return bare.startswith(r)
    return bare == r or host == r                  # 精确匹配 (忽略端口)


def host_allowed(host_header: str | None, rules: list[str] | None = None) -> bool:
    """Host 头是否放行。host_header 形如 '100.100.3.7:8080'。"""
    if not host_header:
        return False
    host = host_header.strip().lower()
    bare = _bare_host(host)
    if bare in ALWAYS_ALLOW or host in ALWAYS_ALLOW:
        return True     # 本机永远放行
    return any(_match_rule(host, bare, rule)
               for rule in (rules if rules is not None else load_rules()))


class HostAllowlistMiddleware:
    """只对 /mcp 生效的 Host 校验 (纯 ASGI, 不依赖路由注册方式)。

    在路由匹配之前执行, 所以无论 /mcp 是 mount 还是"路由提升"注册都能拦到。
    """

    def __init__(self, app, path_prefix: str = "/mcp", hosts_file: Path | None = None):
        self.app = app
        self.prefix = path_prefix
        self.hosts_file = ensure_hosts_file(hosts_file)
        self.rules = load_rules(self.hosts_file)
        logger.info("MCP Host 白名单 (%s): %s", self.hosts_file, self.rules)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith(self.prefix):
            headers = dict(scope.get("headers") or [])
            host = headers.get(b"host", b"").decode("latin-1") or None
            if not host_allowed(host, self.rules):
                logger.warning("MCP 拒绝 Host=%s (白名单: %s)", host, self.hosts_file)
                await self._reject(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send):
        body = (
            "421 Invalid Host header — 该地址不在 MCP 白名单内。"
            "请在配置文件中加入对应地址后重启后端。"
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 421,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
