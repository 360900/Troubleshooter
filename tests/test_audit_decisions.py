"""Checks for the decisions taken after the database audit."""
import os
import time

from tests.test_audit_rest1 import plan_for, run_script
from tests.test_engine import DB, EngineCase
from troubleshooter import engine
from troubleshooter.system import Result


class DecisionCase(EngineCase):
    def evaluate(self, m, entry_id):
        return engine.evaluate(DB.entries[entry_id], m.system())


class NftablesNextToUfwTests(DecisionCase):
    OLD_CONF = "#!/usr/bin/nft -f\nflush ruleset\n\ntable inet filter {\n  chain input {\n    policy drop\n  }\n}\n"
    NEW_CONF = "#!/usr/bin/nft -f\ndestroy table inet filter\ntable inet filter {\n  chain input {\n    policy drop\n  }\n}\n"

    def machine_with(self, conf, ufw="yes", nftables="enabled"):
        m = self.machine()
        m.packages = {"ufw", "nftables"}
        m.outputs["services"] = {"ufw.service": "enabled", "nftables.service": nftables}
        m.write("/etc/ufw/ufw.conf", f"ENABLED={ufw}\n")
        m.write("/etc/nftables.conf", conf)
        return m

    def test_flushing_config_is_recommended(self):
        d = self.evaluate(self.machine_with(self.OLD_CONF), "firewall-ufw-nftables-conflict")
        self.assertTrue(d.recommended)
        self.assertGreaterEqual(d.confidence, 80)

    def test_current_default_config_still_blocks_what_ufw_allows(self):
        self.assertTrue(self.evaluate(self.machine_with(self.NEW_CONF), "firewall-ufw-nftables-conflict").recommended)

    def test_ufw_turned_off_or_nftables_disabled_does_not_apply(self):
        for m in (self.machine_with(self.OLD_CONF, ufw="no"), self.machine_with(self.OLD_CONF, nftables="disabled")):
            self.assertEqual(self.evaluate(m, "firewall-ufw-nftables-conflict").status, engine.NOT_APPLICABLE)

    def test_fix_disables_nftables(self):
        script = plan_for(self.machine_with(self.OLD_CONF), "firewall-ufw-nftables-conflict").script
        self.assertIn("systemctl disable nftables.service", script)


class TlpPdTests(DecisionCase):
    def machine_with(self, *extra):
        m = self.machine()
        m.packages = {"tlp", "power-profiles-daemon", *extra}
        m.outputs["services"] = {"tlp.service": "enabled", "power-profiles-daemon.service": "enabled"}
        return m

    def test_fix_swaps_power_profiles_daemon_for_tlp_pd(self):
        m = self.machine_with()
        plan = plan_for(m, "power-tlp-ppd-conflict")
        order = [plan.script.index(line) for line in (
            "systemctl stop power-profiles-daemon.service", "pacman -Rdd --noconfirm power-profiles-daemon",
            "pacman -S --needed --noconfirm tlp-pd", "systemctl enable --now tlp-pd.service")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("mask", plan.script)
        result = run_script(m, plan.script)
        self.assertEqual(result.returncode, 0, result.stderr)
        undo = plan.undo_script
        self.assertLess(undo.index("pacman -Rns --noconfirm tlp-pd"),
                        undo.index("pacman -S --needed --noconfirm power-profiles-daemon"))

    def test_tlp_pd_or_an_old_mask_counts_as_fixed(self):
        m = self.machine_with("tlp-pd")
        self.assertEqual(self.evaluate(m, "power-tlp-ppd-conflict").status, engine.FIXED)
        m = self.machine_with()
        m.outputs["services"]["power-profiles-daemon.service"] = "masked"
        self.assertEqual(self.evaluate(m, "power-tlp-ppd-conflict").status, engine.FIXED)


class MultilibTests(DecisionCase):
    def test_refresh_is_a_full_upgrade(self):
        m = self.machine()
        m.write("/etc/pacman.conf", "[options]\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n")
        script = plan_for(m, "multilib-disabled").script
        self.assertIn("pacman -Syu --noconfirm", script)
        self.assertNotIn("pacman -Sy --noconfirm", script)


class FirmwareMissingTests(DecisionCase):
    def test_gives_advice_instead_of_reinstalling_linux_firmware(self):
        entry = DB.entries["firmware-missing"]
        self.assertEqual(entry.fixes, [])
        self.assertIn("pacman -F", entry.advice)


class OldGfxoffMaskTests(DecisionCase):
    def machine_with(self, mask, ran_old_fix=True):
        m = self.machine()
        m.packages = {"grub"}
        m.write("/etc/default/grub", f'GRUB_CMDLINE_LINUX_DEFAULT="quiet amdgpu.ppfeaturemask={mask}"\n')
        if ran_old_fix:
            (m.root / "var/lib/troubleshooter/backups/20260101-120000-amd-gfxoff-freeze").mkdir(parents=True)
        return m

    def test_old_mask_from_our_fix_is_recommended(self):
        m = self.machine_with("0xffff7fff")
        self.assertIn("amd-gfxoff-overdrive-mask", self.scan_ids(m))
        self.assertIn("amdgpu.ppfeaturemask=0xfff73fff", plan_for(m, "amd-gfxoff-overdrive-mask").script)

    def test_a_mask_set_by_hand_is_left_alone(self):
        self.assertNotIn("amd-gfxoff-overdrive-mask", self.scan_ids(self.machine_with("0xffff7fff", ran_old_fix=False)))

    def test_current_mask_counts_as_fixed(self):
        m = self.machine_with("0xffff7fff")
        m.write("/etc/default/grub",
                'GRUB_CMDLINE_LINUX_DEFAULT="quiet amdgpu.ppfeaturemask=0xffff7fff amdgpu.ppfeaturemask=0xfff73fff"\n')
        self.assertEqual(self.evaluate(m, "amd-gfxoff-overdrive-mask").status, engine.FIXED)


class EspOutOfSpaceTests(DecisionCase):
    def test_only_recent_failures_count(self):
        m = self.machine()
        old = time.strftime("[%Y-%m-%dT%H:%M:%S%z]", time.localtime(time.time() - 365 * 86400))
        new = time.strftime("[%Y-%m-%dT%H:%M:%S%z]", time.localtime(time.time() - 86400))
        line = " [ALPM-SCRIPTLET] cp: error writing '/boot/vmlinuz-linux': No space left on device\n"
        m.write("/var/log/pacman.log", old + line)
        self.assertEqual(self.evaluate(m, "esp-out-of-space").found, [])
        m.write("/var/log/pacman.log", old + line + new + line)
        self.assertEqual(len(self.evaluate(m, "esp-out-of-space").found), 1)


class BluetoothHardBlockTests(DecisionCase):
    def machine_with(self, rfkill):
        m = self.machine()
        m.outputs["commands"] = {("rfkill", "-n", "-o", "TYPE,HARD"): Result(0, rfkill)}
        return m

    def test_platform_switch_is_found_without_an_adapter(self):
        # A blocked dell-bluetooth switch can hide the adapter, so nothing is in /sys/class/bluetooth.
        d = self.evaluate(self.machine_with("wlan      unblocked\nbluetooth blocked\n"), "bluetooth-hard-blocked")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertTrue(d.found)

    def test_unblocked_or_absent_bluetooth(self):
        unblocked = self.machine_with("bluetooth unblocked\nwlan      unblocked\n")
        self.assertEqual(self.evaluate(unblocked, "bluetooth-hard-blocked").status, engine.FIXED)
        none = self.machine_with("wlan      unblocked\n")
        self.assertEqual(self.evaluate(none, "bluetooth-hard-blocked").status, engine.NOT_APPLICABLE)


class NetworkdResolvConfTests(DecisionCase):
    def test_dangling_stub_link_counts_as_no_dns(self):
        m = self.machine()
        m.outputs["services"] = {"systemd-networkd.service": "enabled", "systemd-resolved.service": "disabled"}
        m.write("/etc/nsswitch.conf", "hosts: mymachines resolve [!UNAVAIL=return] files myhostname dns\n")
        os.symlink("../run/systemd/resolve/stub-resolv.conf", m.root / "etc/resolv.conf")
        d = self.evaluate(m, "network-networkd-no-resolved")
        self.assertTrue(d.found)
        self.assertIn("stub-resolv.conf", d.found[0].evidence.lines[0])
