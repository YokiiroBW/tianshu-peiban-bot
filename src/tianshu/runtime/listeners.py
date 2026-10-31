"""Explicit public HTTP/TLS and loopback model/control listeners."""

import ipaddress
import ssl

from aiohttp import web


def loopback_host(value):
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


async def start_sites(runner, settings, role):
    config = settings.get("broker" if role == "model-broker" else "http", {})
    host = config.get("host", "127.0.0.1")
    if role == "model-broker" and not loopback_host(host):
        raise ValueError("broker_listener_must_be_loopback")
    sites = []
    port = config.get("port", 8071 if role == "model-broker" else 8070)
    if port is not None:
        sites.append(web.TCPSite(runner, host, port))
    if role == "interactive":
        tls = config.get("tls", {})
        if tls.get("enabled"):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(tls["certificate_file"], tls["key_file"])
            sites.append(
                web.TCPSite(runner, tls.get("host", host), tls["port"], ssl_context=context)
            )
        onebot = settings.get("channels", {}).get("listen", {})
        if onebot.get("port") and onebot["port"] != port:
            sites.append(web.TCPSite(runner, onebot.get("host", host), onebot["port"]))
    for site in sites:
        await site.start()
    return tuple(sites)
