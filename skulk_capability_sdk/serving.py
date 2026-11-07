"""Serving a capability's own screen or API beyond its host.

A capability that serves a screen usually wants people on other machines to
reach it. The host decides where. At protocol 3 it hands each child
``Startup.serve_host``: the node's Tailscale address, when the node runs
Tailscale. A child then:

- binds loopback and the serve address (``bind_hosts``), never every
  interface, which would also serve the local network the host did not choose;
- publishes its URL on the serve address (``advertised_host``);
- admits a connection only when ``peer_allowed`` accepts the caller's address.

That rule needs no call to the host per connection. A socket bound to the
Tailscale address and loopback only receives connections that Tailscale
delivered or that start on the node itself. A caller also cannot complete a
TCP handshake while claiming a Tailscale address it does not hold, because the
reply goes out through Tailscale to the real address. Tailscale's own access
rules decide which machines reach the address at all.

Without a serve address (no Tailscale on the node, or a host older than
protocol 3) a child serves on loopback, which the host's dashboard opens only
from a browser on the node itself.
"""

from ipaddress import IPv6Address, ip_address, ip_network

from .contracts import Startup

LOOPBACK_HOST = "127.0.0.1"
"""Where a child always serves: the node itself."""

TAILNET_NETWORKS = (ip_network("100.64.0.0/10"), ip_network("fd7a:115c:a1e0::/48"))
"""The ranges Tailscale assigns addresses from: its IPv4 range and IPv6 prefix."""


def bind_hosts(startup: Startup) -> tuple[str, ...]:
    """The addresses a child's server listens on: loopback, then the serve address.

    Bind each one separately. Binding every interface would also accept the
    local network, which the host did not choose to serve.
    """
    if startup.serve_host is None:
        return (LOOPBACK_HOST,)
    return (LOOPBACK_HOST, startup.serve_host)


def advertised_host(startup: Startup) -> str:
    """The host part of the URL a child publishes: the serve address, else loopback.

    An IPv6 serve address comes back in brackets, ready to go into a URL.
    """
    host = startup.serve_host
    if host is None:
        return LOOPBACK_HOST
    return f"[{host}]" if ":" in host else host


def peer_allowed(address: str) -> bool:
    """Whether a connection from ``address`` may be served.

    Loopback callers are on the node. Tailscale callers reached the node
    through its tailnet. Every other caller is refused, including the local
    network, as is anything that is not an IP address. An IPv4 caller seen
    through an IPv6 socket (``::ffff:100.64.0.1``) is judged by its IPv4
    address, and an IPv6 zone suffix (``%en0``) is ignored.
    """
    try:
        parsed = ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    if parsed.is_loopback:
        return True
    return any(parsed in network for network in TAILNET_NETWORKS)
