import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


_PROXY_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")


class ProviderAddressError(ValueError):
    pass


def provider_url(value: str) -> httpx.URL:
    try:
        parts = urlsplit(value)
        url = httpx.URL(value)
        if (
            len(value) <= 1024
            and url.scheme == "https"
            and url.host
            and "%" not in url.host
            and parts.username is None
            and not url.query
            and not url.fragment
        ):
            return url
    except (ValueError, httpx.InvalidURL):
        pass
    raise ValueError("请输入公网 HTTPS 接口地址，地址不能包含账号、密码或参数")


async def _resolve_public_dns(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    # Query a fixed resolver address so the local proxy cannot substitute its DNS answer.
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5.0) as client:
            for record_type in (1, 28):  # A, AAAA
                response = await client.get(
                    "https://1.1.1.1/dns-query",
                    params={"name": host, "type": record_type},
                    headers={"Accept": "application/dns-json"},
                )
                response.raise_for_status()
                answer = response.json()
                if answer["Status"] != 0:
                    break
                addresses = [
                    ipaddress.ip_address(record["data"])
                    for record in answer.get("Answer", [])
                    if record["type"] == record_type
                ]
                if addresses:
                    return addresses
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
        pass
    raise ProviderAddressError("本机代理返回了虚拟地址，无法查询到该域名的公网 IP")


async def resolve_public_address(url: httpx.URL) -> str:
    host = url.raw_host.decode()
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, url.port or 443, type=socket.SOCK_STREAM),
                timeout=5,
            )
        except (OSError, TimeoutError):
            raise ProviderAddressError("无法解析图片生成服务地址") from None
        addresses = [ipaddress.ip_address(record[4][0]) for record in records]
        if addresses and all(ip in _PROXY_FAKE_IP_RANGE for ip in addresses):
            addresses = await _resolve_public_dns(host)
    if not addresses or any(not ip.is_global or ip.is_multicast for ip in addresses):
        raise ProviderAddressError("图片生成服务仅支持公网 HTTPS 地址")
    return str(min(addresses, key=lambda ip: ip.version))


class PublicProviderTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        # TLS connections must not be reused for different hostnames sharing an IP.
        super().__init__(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original = request.url
        # Signed download links may include query parameters.
        url = provider_url(str(original.copy_with(query=None)))
        address = await resolve_public_address(url)
        request.url = original.copy_with(host=address)
        request.extensions["sni_hostname"] = original.raw_host.decode()
        try:
            return await super().handle_async_request(request)
        finally:
            request.url = original
