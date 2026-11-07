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


if __name__ == "__main__":
    unittest.main()
