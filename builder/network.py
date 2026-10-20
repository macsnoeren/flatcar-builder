"""systemd-networkd configuration: one .network file per interface.

Everything here is checked on the build machine. A wrong network file is a node
that boots perfectly and cannot be reached, and on a VPS the way back is the
provider's console — so a typo has to cost a second here instead.

Two kinds of interface:

  ordinary  everything is in node.yml: mode, addresses, gateways, dns, options.
  diode     one end of a one-way link from deployment.yml (diode_links). node.yml
            only says which card it is; addresses, the fixed MAC and the static
            neighbour come from the link, so both ends are written from ONE
            description and cannot drift apart.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Optional

from .config import NET_MODES, NET_OPTIONS, Deployment, DiodeLink, Interface, Node
from .errors import ConfigError

MAC_RE = re.compile(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}")


@dataclass
class DiodeRole:
    link: DiodeLink
    sending: bool


def _fail(node: Node, iface: Interface, msg: str) -> ConfigError:
    label = iface.name or "(unnamed)"
    return ConfigError(iface.where, f"node {node.name}, interface {label}: {msg}")


def is_ipv6(addr: str) -> bool:
    return ":" in addr


def is_linklocal_v6(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_link_local and is_ipv6(addr)
    except ValueError:
        return False


def _check_mac(value: str, node: Node, iface: Interface, what: str) -> str:
    if not MAC_RE.fullmatch(value):
        raise _fail(node, iface, f"{what} {value!r} is not a MAC address (write 02:00:00:00:00:01)")
    return value.lower()


def diode_roles(dep: Deployment) -> dict:
    """{(node, interface): DiodeRole} for every end of every diode link."""
    roles = {}
    for link in dep.diode_links:
        for end, sending in ((link.sender, True), (link.receiver, False)):
            roles[(end.node, end.interface)] = DiodeRole(link, sending)
    return roles


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_deployment(dep: Deployment) -> None:
    validate_diode_links(dep)
    roles = diode_roles(dep)
    for node in dep.nodes:
        validate_node(node, roles)


def validate_node(node: Node, roles: dict) -> None:
    if not node.interfaces:
        raise ConfigError(f"nodes/{node.name}/node.yml: network.interfaces",
                          f"is empty for node {node.name}. A node that configures no interface comes up "
                          "without a network, and on a VPS that means it comes up unreachable")
    names = set()
    for iface in node.interfaces:
        if not iface.name:
            raise _fail(node, iface, "has no name")
        if iface.name in names:
            raise _fail(node, iface, "is configured twice")
        names.add(iface.name)
        if iface.match_permanent_mac:
            _check_mac(iface.match_permanent_mac, node, iface, "match_permanent_mac")
        if iface.mac:
            _check_mac(iface.mac, node, iface, "mac")
        if (node.name, iface.name) in roles:
            _validate_diode_iface(node, iface)
        else:
            _validate_ordinary(node, iface)


def _validate_ordinary(node: Node, iface: Interface) -> None:
    if iface.mode is None:
        raise _fail(node, iface, "has no mode. Use dhcp, dhcp4, dhcp6 or static "
                                 "(or make it one end of a diode link in deployment.yml)")
    if iface.mode not in NET_MODES:
        raise _fail(node, iface, f"unknown mode {iface.mode!r}. Use dhcp, dhcp4, dhcp6 or static")
    for a in iface.addresses:
        if "/" not in a:
            raise _fail(node, iface, f"address {a!r} has no prefix length "
                                     "(write 192.0.2.10/24 or 2001:db8::10/64)")
        try:
            ipaddress.ip_interface(a)
        except ValueError:
            raise _fail(node, iface, f"address {a!r} is not an IP address with a prefix length") from None
    for g in iface.gateways:
        try:
            ipaddress.ip_address(g)
        except ValueError:
            raise _fail(node, iface, f"gateway {g!r} is not an IP address") from None
    for s in iface.dns:
        try:
            ipaddress.ip_address(s)
        except ValueError:
            raise _fail(node, iface, f"dns server {s!r} is not an IP address") from None
    if iface.mode == "static" and not iface.addresses:
        raise _fail(node, iface, "is static but is missing an address")
    for o in iface.options:
        if o not in NET_OPTIONS:
            raise _fail(node, iface, f"unknown interface option {o!r} (onlink, ra, nocarrier)")


def _validate_diode_iface(node: Node, iface: Interface) -> None:
    for key in ("mode", "mac"):
        if getattr(iface, key) is not None:
            raise _fail(node, iface, f"is a diode interface; '{key}' comes from its link in deployment.yml")
    for key in ("addresses", "options"):
        if getattr(iface, key):
            raise _fail(node, iface, f"is a diode interface; '{key}' comes from its link in deployment.yml")
    if iface.gateways or iface.dns:
        raise _fail(node, iface, "is a diode interface and cannot have a gateway or DNS: "
                                 "nothing ever comes back over it")


def validate_diode_links(dep: Deployment) -> None:
    used = {}
    macs = {}
    link_names = set()
    for link in dep.diode_links:
        if link.name in link_names:
            raise ConfigError(link.where, f"diode link {link.name!r} is defined twice")
        link_names.add(link.name)
        if link.sender.node == link.receiver.node:
            raise ConfigError(link.where, f"diode link {link.name}: both ends are on node {link.sender.node}")
        nets = []
        for end in (link.sender, link.receiver):
            node = dep.node(end.node)
            if node is None:
                raise ConfigError(end.where, f"diode link {link.name}: unknown node {end.node!r}")
            if node.interface(end.interface) is None:
                raise ConfigError(end.where, f"diode link {link.name}: node {end.node} has no interface "
                                             f"{end.interface!r} in its node.yml")
            key = (end.node, end.interface)
            if key in used:
                raise ConfigError(end.where, f"diode link {link.name}: interface {end.interface} on {end.node} "
                                             f"is already used by diode link {used[key]}")
            used[key] = link.name
            if "/" not in end.address:
                raise ConfigError(end.where, f"diode link {link.name}: address {end.address!r} has no prefix length")
            try:
                nets.append(ipaddress.ip_interface(end.address))
            except ValueError:
                raise ConfigError(end.where, f"diode link {link.name}: {end.address!r} is not an IP address "
                                             "with a prefix length") from None
        if nets[0].network != nets[1].network:
            raise ConfigError(link.where, f"diode link {link.name}: {link.sender.address} and "
                                          f"{link.receiver.address} are not in the same subnet")
        if nets[0].ip == nets[1].ip:
            raise ConfigError(link.where, f"diode link {link.name}: both ends have address {nets[0].ip}")

        mac = link.receiver.mac
        if not MAC_RE.fullmatch(mac):
            raise ConfigError(link.receiver.where, f"diode link {link.name}: {mac!r} is not a MAC address")
        first = int(mac[:2], 16)
        if first & 0x01:
            raise ConfigError(link.receiver.where, f"diode link {link.name}: {mac} is a multicast address")
        if not first & 0x02:
            raise ConfigError(link.receiver.where,
                              f"diode link {link.name}: {mac} is not locally administered; a fixed MAC has to "
                              "be (second bit of the first octet set, e.g. 02:…) so it cannot collide with a "
                              "card's own address")
        if mac.lower() in macs:
            raise ConfigError(link.receiver.where, f"diode link {link.name}: MAC {mac} is also used by "
                                                   f"diode link {macs[mac.lower()]}")
        macs[mac.lower()] = link.name

    for node in dep.nodes:
        for iface in node.interfaces:
            if iface.mac and iface.mac.lower() in macs:
                raise ConfigError(iface.where, f"node {node.name}, interface {iface.name}: MAC {iface.mac} is "
                                               f"also the receiver MAC of diode link {macs[iface.mac.lower()]}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def network_files(node: Node, dep: Deployment) -> dict:
    """{"/etc/systemd/network/10-<name>.network": body} for every interface, in order."""
    roles = diode_roles(dep)
    out = {}
    for idx, iface in enumerate(node.interfaces, start=10):
        role = roles.get((node.name, iface.name))
        body = render_diode(iface, role) if role else render_ordinary(iface)
        out[f"/etc/systemd/network/{idx}-{iface.name}.network"] = body
    return out


def _match(iface: Interface) -> list:
    # The PERMANENT address, so a MAC set with MACAddress= on the same card does
    # not stop the file from matching it.
    if iface.match_permanent_mac:
        return ["[Match]", f"PermanentMACAddress={iface.match_permanent_mac.lower()}"]
    return ["[Match]", f"Name={iface.name}"]


def render_ordinary(iface: Interface) -> str:
    lines = _match(iface)

    link = []
    if iface.mac:
        link.append(f"MACAddress={iface.mac.lower()}")
    if iface.arp is False:
        link.append("ARP=no")
    if link:
        lines += ["", "[Link]"] + link

    lines += ["", "[Network]"]
    lines.append({"dhcp": "DHCP=yes", "dhcp4": "DHCP=ipv4", "dhcp6": "DHCP=ipv6", "static": "DHCP=no"}[iface.mode])

    have_static_v6 = False
    for a in iface.addresses:
        have_static_v6 |= is_ipv6(a)
        lines.append(f"Address={a}")

    # A static IPv6 address and router advertisements together leave the node
    # with two opinions about its default route. The written-down one wins: RA
    # is off unless the interface asks for it with 'ra'.
    if have_static_v6 and "ra" not in iface.options:
        lines.append("IPv6AcceptRA=no")

    # Off by default: on an ordinary link it would only hide a cable problem.
    if "nocarrier" in iface.options:
        lines += ["ConfigureWithoutCarrier=yes", "IgnoreCarrierLoss=yes"]

    lines += [f"DNS={d}" for d in iface.dns]

    # A plain gateway is a [Network] key; one that needs GatewayOnLink has to be
    # a [Route], the only section where networkd accepts that setting. Plain ones
    # first, so every [Network] key stays above the first [Route]. A link-local
    # IPv6 gateway is on the link by definition and never needs a route.
    onlink = "onlink" in iface.options
    for g in iface.gateways:
        if not onlink or is_linklocal_v6(g):
            lines.append(f"Gateway={g}")
    if onlink:
        for g in iface.gateways:
            if not is_linklocal_v6(g):
                lines += ["", "[Route]", f"Gateway={g}", "GatewayOnLink=yes"]

    return "\n".join(lines) + "\n"


def render_diode(iface: Interface, role: DiodeRole) -> str:
    link = role.link
    me = link.sender if role.sending else link.receiver
    lines = _match(iface)

    lines += ["", "[Link]"]
    if not role.sending:
        # The address the sender's neighbour entry points at.
        lines.append(f"MACAddress={link.receiver.mac.lower()}")
    # Nothing answers over a diode: no ARP, and the link does not count towards
    # "online", or boot would wait for a link that is never reported up.
    lines += ["ARP=no", "RequiredForOnline=no"]

    lines += ["", "[Network]", "DHCP=no", f"Address={me.address}"]
    if role.sending:
        # The sending card usually sees no light coming back, i.e. no carrier.
        lines += ["ConfigureWithoutCarrier=yes", "IgnoreCarrierLoss=yes"]
    lines += ["LinkLocalAddressing=no", "IPv6AcceptRA=no", "LLDP=no", "EmitLLDP=no"]

    if role.sending:
        peer = str(ipaddress.ip_interface(link.receiver.address).ip)
        lines += ["", "[Neighbor]", f"Address={peer}", f"LinkLayerAddress={link.receiver.mac.lower()}"]

    return "\n".join(lines) + "\n"


def describe_role(role: Optional[DiodeRole]) -> str:
    if role is None:
        return ""
    return f"diode {'sender' if role.sending else 'receiver'} of link {role.link.name}"
