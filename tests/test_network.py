"""Phase 2: the .network files, ordinary and diode."""

import re

from builder import config, network
from builder.errors import ConfigError

from .helpers import Scratch, node


def iface(name, mode, addresses=(), gateways=(), dns=(), options=(), **kw):
    d = {"name": name, "mode": mode, "addresses": list(addresses), "gateways": list(gateways),
         "dns": list(dns), "options": list(options)}
    d.update(kw)
    return d


class Net(Scratch):
    def files(self, interfaces, deployment=None, nodes=None, name="test-node"):
        nodes = nodes or {name: node(network={"interfaces": interfaces})}
        self.make(nodes=nodes, deployment=deployment)
        dep = config.load_deployment(self.dep)
        network.validate_deployment(dep)
        return network.network_files(dep.node(name), dep)

    def body(self, interfaces, name):
        files = self.files(interfaces)
        return next(v for k, v in files.items() if k.endswith(f"-{name}.network"))


class Modes(Net):
    SHAPES = [
        iface("eth-dhcp", "dhcp"),
        iface("eth-dhcp4", "dhcp4", ["2001:db8::2/64"], ["fe80::1"], ["2606:4700:4700::1111"]),
        iface("eth-static", "static", ["203.0.113.10/24", "2001:db8:1::2/64"], ["203.0.113.1", "fe80::1"],
              ["1.1.1.1", "2606:4700:4700::1111"]),
        iface("eth-onlink", "static", ["203.0.113.10/32"], ["203.0.113.1"], ["1.1.1.1"], ["onlink"]),
        iface("eth-ra", "static", ["2001:db8:2::2/64"], ["fe80::1"], options=["ra"]),
        iface("eth-nocarrier", "static", ["10.0.0.2/24"], options=["nocarrier"]),
    ]

    def setUp(self):
        super().setUp()
        self.f = self.files(self.SHAPES)

    def s(self, name):
        return next(v for k, v in self.f.items() if k.endswith(f"-{name}.network"))

    def test_file_names_follow_the_order(self):
        self.assertEqual(sorted(self.f)[0], "/etc/systemd/network/10-eth-dhcp.network")
        self.assertIn("/etc/systemd/network/15-eth-nocarrier.network", self.f)

    def test_dhcp(self):
        self.assertIn("DHCP=yes", self.s("eth-dhcp"))
        self.assertNotIn("Address=", self.s("eth-dhcp"))
        self.assertIn("Name=eth-dhcp", self.s("eth-dhcp"))

    def test_dhcp4_with_static_v6(self):
        s = self.s("eth-dhcp4")
        self.assertIn("DHCP=ipv4", s)
        self.assertIn("Address=2001:db8::2/64", s)
        self.assertIn("Gateway=fe80::1", s)
        self.assertIn("IPv6AcceptRA=no", s)

    def test_static_both_families(self):
        s = self.s("eth-static")
        for want in ("DHCP=no", "Address=203.0.113.10/24", "Address=2001:db8:1::2/64", "Gateway=203.0.113.1",
                     "Gateway=fe80::1", "DNS=1.1.1.1", "DNS=2606:4700:4700::1111"):
            self.assertIn(want, s)

    def test_onlink_becomes_a_route(self):
        s = self.s("eth-onlink")
        self.assertIn("[Route]\nGateway=203.0.113.1\nGatewayOnLink=yes", s)
        network_section = s.split("[Route]")[0]
        self.assertNotIn("Gateway=", network_section)

    def test_ra_keeps_router_advertisements(self):
        self.assertNotIn("IPv6AcceptRA=no", self.s("eth-ra"))

    def test_nocarrier(self):
        self.assertIn("ConfigureWithoutCarrier=yes", self.s("eth-nocarrier"))
        self.assertIn("IgnoreCarrierLoss=yes", self.s("eth-nocarrier"))
        self.assertNotIn("ConfigureWithoutCarrier", self.s("eth-dhcp"))

    def test_network_keys_stay_in_their_section(self):
        # networkd silently ignores a [Network] key anywhere else.
        network_keys = {"DHCP", "Address", "DNS", "IPv6AcceptRA", "ConfigureWithoutCarrier", "IgnoreCarrierLoss",
                        "LinkLocalAddressing", "LLDP", "EmitLLDP"}
        for path, body in self.f.items():
            section = ""
            for line in body.splitlines():
                if line.startswith("["):
                    section = line
                elif "=" in line:
                    key = line.split("=")[0]
                    if key in network_keys:
                        self.assertEqual(section, "[Network]", f"{path}: {line}")
                    if key == "GatewayOnLink":
                        self.assertEqual(section, "[Route]", f"{path}: {line}")

    def test_link_local_gateway_with_onlink_stays_a_plain_gateway(self):
        s = self.body([iface("e", "static", ["2001:db8::2/64"], ["fe80::1", "203.0.113.1"], options=["onlink"])], "e")
        self.assertIn("Gateway=fe80::1", s.split("[Route]")[0])
        self.assertIn("[Route]\nGateway=203.0.113.1", s)


class Refused(Net):
    def assertRefusedIface(self, entry, fragment):
        with self.assertRaises(ConfigError) as cm:
            self.files([entry])
        msg = str(cm.exception)
        self.assertIn(fragment, msg)
        self.assertIn("node test-node", msg)

    def test_unknown_mode(self):
        self.assertRefusedIface(iface("eth0", "magic"), "Use dhcp, dhcp4, dhcp6 or static")

    def test_address_without_prefix(self):
        self.assertRefusedIface(iface("eth0", "static", ["10.0.0.2"]), "has no prefix length")

    def test_static_without_address(self):
        self.assertRefusedIface(iface("eth0", "static"), "missing an address")

    def test_unknown_option(self):
        self.assertRefusedIface(iface("eth0", "dhcp", options=["wireless"]), "unknown interface option")

    def test_no_name(self):
        self.assertRefusedIface({"mode": "dhcp"}, "has no name")

    def test_interface_named_in_the_message(self):
        self.assertRefusedIface(iface("eth7", "magic"), "interface eth7")

    def test_no_interfaces(self):
        with self.assertRaises(ConfigError) as cm:
            self.files([])
        self.assertIn("comes up unreachable", str(cm.exception))

    def test_bad_mac(self):
        self.assertRefusedIface(iface("eth0", "dhcp", mac="02:00:00:00:00"), "is not a MAC address")


class MacAndArp(Net):
    def test_permanent_mac_match(self):
        s = self.body([iface("lan", "dhcp", match_permanent_mac="00:1B:21:AA:BB:01")], "lan")
        self.assertIn("[Match]\nPermanentMACAddress=00:1b:21:aa:bb:01", s)
        self.assertNotIn("Name=", s)

    def test_fixed_mac_and_no_arp(self):
        s = self.body([iface("lan", "static", ["10.0.0.1/24"], mac="02:00:00:00:00:01", arp=False)], "lan")
        self.assertIn("[Link]\nMACAddress=02:00:00:00:00:01\nARP=no", s)

    def test_name_match_without_mac(self):
        s = self.body([iface("lan", "dhcp")], "lan")
        self.assertIn("Name=lan", s)
        self.assertNotIn("[Link]", s)


RING = [
    {"name": "ab", "from": {"node": "node-a", "interface": "diode-ab", "address": "10.10.1.1/30"},
     "to": {"node": "node-b", "interface": "diode-ab", "address": "10.10.1.2/30", "mac": "02:00:0a:0a:01:02"}},
    {"name": "bc", "from": {"node": "node-b", "interface": "diode-bc", "address": "10.10.2.1/30"},
     "to": {"node": "node-c", "interface": "diode-bc", "address": "10.10.2.2/30", "mac": "02:00:0a:0a:02:02"}},
    {"name": "ca", "from": {"node": "node-c", "interface": "diode-ca", "address": "10.10.3.1/30"},
     "to": {"node": "node-a", "interface": "diode-ca", "address": "10.10.3.2/30", "mac": "02:00:0a:0a:03:02"}},
]


def ring_nodes(mutate=None):
    ifaces = {
        "node-a": ["diode-ab", "diode-ca"],
        "node-b": ["diode-ab", "diode-bc"],
        "node-c": ["diode-bc", "diode-ca"],
    }
    out = {}
    for i, (n, names) in enumerate(ifaces.items()):
        lst = [{"name": "mgmt", "mode": "dhcp"}]
        lst += [{"name": x, "match_permanent_mac": f"00:1b:21:aa:0{i}:0{j}"} for j, x in enumerate(names)]
        out[n] = node(network={"interfaces": lst})
    if mutate:
        mutate(out)
    return out


class Diodes(Net):
    def ring(self, links=None, nodes=None):
        import copy
        self.make(nodes=nodes or ring_nodes(), deployment={"diode_links": links or copy.deepcopy(RING)})
        dep = config.load_deployment(self.dep)
        network.validate_deployment(dep)
        return dep

    def refused(self, fragment, links=None, nodes=None):
        with self.assertRaises(ConfigError) as cm:
            self.ring(links, nodes)
        self.assertIn(fragment, str(cm.exception))

    def link(self, **change):
        import copy
        links = copy.deepcopy(RING)
        for path, value in change.items():
            obj = links[0]
            keys = path.split("__")
            for k in keys[:-1]:
                obj = obj[k]
            obj[keys[-1]] = value
        return links

    def test_ring_gives_each_node_one_sender_and_one_receiver(self):
        dep = self.ring()
        for name in ("node-a", "node-b", "node-c"):
            files = network.network_files(dep.node(name), dep)
            diode = [b for p, b in files.items() if "diode" in p]
            self.assertEqual(len(diode), 2, name)
            self.assertEqual(sum("[Neighbor]" in b for b in diode), 1, name)
            self.assertEqual(sum("\nMACAddress=" in b for b in diode), 1, name)

    def test_sender_points_its_neighbour_at_the_receiver_mac(self):
        dep = self.ring()
        a = network.network_files(dep.node("node-a"), dep)
        b = network.network_files(dep.node("node-b"), dep)
        sender = next(v for k, v in a.items() if k.endswith("-diode-ab.network"))
        receiver = next(v for k, v in b.items() if k.endswith("-diode-ab.network"))
        self.assertIn("[Neighbor]\nAddress=10.10.1.2\nLinkLayerAddress=02:00:0a:0a:01:02", sender)
        self.assertIn("ConfigureWithoutCarrier=yes", sender)
        self.assertIn("Address=10.10.1.1/30", sender)
        self.assertIn("MACAddress=02:00:0a:0a:01:02", receiver)
        self.assertIn("Address=10.10.1.2/30", receiver)
        self.assertNotIn("[Neighbor]", receiver)
        for body in (sender, receiver):
            for want in ("ARP=no", "RequiredForOnline=no", "LinkLocalAddressing=no", "LLDP=no", "PermanentMACAddress="):
                self.assertIn(want, body)

    def test_unknown_node(self):
        self.refused("unknown node 'node-x'", self.link(to__node="node-x"))

    def test_unknown_interface(self):
        self.refused("has no interface 'diode-zz'", self.link(to__interface="diode-zz"))

    def test_not_the_same_subnet(self):
        self.refused("not in the same subnet", self.link(to__address="10.10.9.2/30"))

    def test_duplicate_mac(self):
        self.refused("also used by diode link", self.link(to__mac="02:00:0a:0a:02:02"))

    def test_mac_not_locally_administered(self):
        self.refused("not locally administered", self.link(to__mac="00:1b:21:0a:01:02"))

    def test_multicast_mac(self):
        self.refused("multicast", self.link(to__mac="03:00:0a:0a:01:02"))

    def test_interface_in_two_links(self):
        self.refused("already used by diode link", self.link(from__interface="diode-ca"))

    def test_gateway_on_a_diode_interface(self):
        def m(nodes):
            nodes["node-a"]["network"]["interfaces"][1]["gateways"] = ["10.10.1.2"]
        self.refused("cannot have a gateway or DNS", nodes=ring_nodes(m))

    def test_dns_on_a_diode_interface(self):
        def m(nodes):
            nodes["node-a"]["network"]["interfaces"][1]["dns"] = ["1.1.1.1"]
        self.refused("cannot have a gateway or DNS", nodes=ring_nodes(m))

    def test_mode_on_a_diode_interface(self):
        def m(nodes):
            nodes["node-a"]["network"]["interfaces"][1]["mode"] = "static"
        self.refused("'mode' comes from its link", nodes=ring_nodes(m))

    def test_ordinary_interface_needs_a_mode(self):
        def m(nodes):
            nodes["node-a"]["network"]["interfaces"][0].pop("mode")
        self.refused("has no mode", nodes=ring_nodes(m))
