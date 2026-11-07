import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class NetworkCase(EngineCase):
    def evaluate(self, entry_id, machine):
        return engine.evaluate(DB.entries[entry_id], machine.system())


class EthernetDownshiftTests(NetworkCase):
    def nic(self, journal):
        m = self.machine()
        m.pci("0000:03:00.0", "8086", "1533", "020000", "igb")
        m.journal(journal)
        return self.evaluate("network-ethernet-downshift", m)

    def test_phylib_downshift_is_found(self):
        d = self.nic("kernel: r8169 0000:03:00.0 enp3s0: Downshift occurred from negotiated speed 1Gbps "
                     "to actual speed 100Mbps, check cabling!\n")
        self.assertTrue(d.recommended)

    def test_intel_smartspeed_downgrade_is_found(self):
        d = self.nic("kernel: igb 0000:03:00.0 eno1: Link Speed was downgraded by SmartSpeed\n")
        self.assertTrue(d.recommended)

    def test_overheating_is_not_blamed_on_the_cable(self):
        d = self.nic("kernel: igb 0000:03:00.0 eno1: The network adapter link speed was downshifted "
                     "because it overheated\n")
        self.assertFalse(d.found)


class WaitOnlineTests(NetworkCase):
    def test_current_unit_description_is_matched(self):
        m = self.machine()
        m.outputs["services"] = {"systemd-networkd.service": "enabled"}
        m.journal("systemd[1]: Failed to start systemd-networkd-wait-online.service - "
                  "Wait for Network to be Online.\n")
        self.assertTrue(self.evaluate("network-networkd-wait-online-timeout", m).recommended)


class DhcpcdTests(NetworkCase):
    def nm(self, **services):
        m = self.machine()
        m.packages = {"networkmanager", "dhcpcd"}
        m.outputs["services"] = {"NetworkManager.service": "enabled", **services}
        return m

    def test_installed_but_unused_dhcpcd_is_fine(self):
        d = self.evaluate("network-nm-dhcpcd-conflict", self.nm())
        self.assertEqual(d.status, engine.FIXED)

    def test_dhcpcd_service_is_found(self):
        d = self.evaluate("network-nm-dhcpcd-conflict", self.nm(**{"dhcpcd.service": "enabled"}))
        self.assertTrue(d.recommended)

    def test_a_per_interface_instance_is_found(self):
        m = self.nm()
        m.write("/etc/systemd/system/multi-user.target.wants/dhcpcd@enp3s0.service", "")
        d = self.evaluate("network-nm-dhcpcd-conflict", m)
        self.assertTrue(d.recommended)
        plan = engine.build_plan(d, m.system())
        self.assertIn('xargs -r systemctl disable < "$TS_BACKUP/dhcpcd-units"', plan.script)
        self.assertIn('xargs -r systemctl enable < "$TS_BACKUP/dhcpcd-units"', plan.undo_script)


class IwdTests(NetworkCase):
    def nm(self):
        m = self.machine()
        m.packages = {"networkmanager", "iwd", "wpa_supplicant"}
        m.outputs["services"] = {"NetworkManager.service": "enabled", "iwd.service": "enabled"}
        return m

    def test_iwd_next_to_wpa_supplicant_backend_is_found(self):
        self.assertTrue(self.evaluate("network-nm-iwd-conflict", self.nm()).recommended)

    def test_iwd_as_networkmanagers_backend_is_not_a_conflict(self):
        m = self.nm()
        m.write("/etc/NetworkManager/conf.d/wifi_backend.conf", "[device]\nwifi.backend=iwd\n")
        self.assertEqual(self.evaluate("network-nm-iwd-conflict", m).status, engine.NOT_APPLICABLE)


class UsbEthernetTests(NetworkCase):
    PREFIX = "kernel: r8152 2-1:1.0 enx00e04c680001: "

    def adapter(self, journal, quirks="\n"):
        m = self.machine()
        m.modules("r8152")
        m.write("/sys/module/usbcore/parameters/quirks", quirks)
        m.journal(journal)
        return self.evaluate("network-usb-ethernet-tx-timeout", m)

    def test_transmit_timeout_is_recommended(self):
        self.assertTrue(self.adapter(self.PREFIX + "Tx timeout\n").recommended)

    def test_unplugging_alone_is_not_recommended(self):
        # Pending transmits fail with -71 or are cancelled with -2 when the adapter goes away.
        d = self.adapter(self.PREFIX + "Tx status -71\n" + self.PREFIX + "Tx status -2\n")
        self.assertEqual(len(d.found), 2)
        self.assertFalse(d.recommended)

    def test_no_lpm_quirk_among_others_is_fixed(self):
        d = self.adapter(self.PREFIX + "Tx timeout\n", "0bda:8153:gk,2357:0601:b\n")
        self.assertEqual(d.status, engine.FIXED)


class OpenVpnNetworkdTests(NetworkCase):
    FAILED = "openvpn[812]: ERROR: Linux route add command failed\n"
    UNREACHABLE = "openvpn[812]: sitnl_send: rtnl: generic error (-101): Network is unreachable\n"

    def vpn(self, journal):
        m = self.machine()
        m.packages = {"openvpn"}
        m.outputs["services"] = {"systemd-networkd.service": "enabled"}
        m.journal(journal)
        return m

    def test_unreachable_network_from_netlink_is_recommended(self):
        d = self.evaluate("vpn-openvpn-networkd-tun-routes", self.vpn(self.UNREACHABLE + self.FAILED))
        self.assertTrue(d.recommended)

    def test_any_failed_route_alone_is_not_recommended(self):
        d = self.evaluate("vpn-openvpn-networkd-tun-routes", self.vpn(self.FAILED))
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_tun_left_alone_under_another_name_is_fixed(self):
        m = self.vpn(self.UNREACHABLE + self.FAILED)
        m.write("/etc/systemd/network/10-vpn.network", "[Match]\nName=tun0\n\n[Link]\nUnmanaged=yes\n")
        self.assertEqual(self.evaluate("vpn-openvpn-networkd-tun-routes", m).status, engine.FIXED)


if __name__ == "__main__":
    unittest.main()
