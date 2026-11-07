from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class NvidiaModesetOptionTests(EngineCase):
    def evaluate(self, entry, param, value):
        m = self.machine()
        m.modules("nvidia", "nvidia_modeset")
        m.write(f"/sys/module/nvidia_modeset/parameters/{param}", value)
        return engine.evaluate(DB.entries[entry], m.system())

    def test_deep_color_turned_off_any_way_is_fixed(self):
        self.assertEqual(self.evaluate("nvidia-hdmi-deepcolor", "hdmi_deepcolor", "N\n").status, engine.FIXED)
        self.assertEqual(self.evaluate("nvidia-hdmi-deepcolor", "hdmi_deepcolor", "Y\n").status, engine.APPLIES)

    def test_vrr_hidden_through_modprobe_is_fixed(self):
        self.assertEqual(self.evaluate("nvidia-vrr-flip-timeout", "conceal_vrr_caps", "Y\n").status, engine.FIXED)
        self.assertEqual(self.evaluate("nvidia-vrr-flip-timeout", "conceal_vrr_caps", "N\n").status, engine.APPLIES)


class NvidiaHibernateTests(EngineCase):
    SWAPS = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"

    def evaluate(self, swaps, journal=""):
        m = self.machine()
        m.modules("nvidia")
        m.write("/etc/mkinitcpio.conf.d/10-chwd.conf", "MODULES+=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)\n")
        m.write("/proc/swaps", self.SWAPS + swaps)
        m.journal(journal)
        return engine.evaluate(DB.entries["nvidia-hibernate-early-kms"], m.system())

    def test_zram_only_can_not_hibernate(self):
        d = self.evaluate("/dev/zram0                              partition\t32496636\t0\t100\n")
        self.assertEqual(d.status, engine.NOT_APPLICABLE)

    def test_disk_swap_with_early_loading_applies(self):
        d = self.evaluate("/swap/swapfile                          file\t\t16777212\t0\t-2\n")
        self.assertEqual(d.status, engine.APPLIES)
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_a_hibernation_attempt_adds_to_it(self):
        d = self.evaluate("/dev/nvme0n1p3                          partition\t16777212\t0\t-2\n",
                          "kernel: PM: hibernation: hibernation entry\n")
        self.assertEqual(len(d.found), 2)


class NvidiaXidTests(EngineCase):
    def evaluate(self, journal):
        m = self.machine()
        m.modules("nvidia")
        m.journal(journal)
        return engine.evaluate(DB.entries["nvidia-xid"], m.system())

    def test_a_real_xid_is_evidence(self):
        d = self.evaluate("kernel: NVRM: Xid (PCI:0000:01:00): 79, pid='<unknown>', name=<unknown>, "
                          "GPU has fallen off the bus.\n")
        self.assertTrue(d.recommended)
        self.assertTrue(self.evaluate("kernel: NVRM: Xid (PCI:0000:01:00): 13, pid=4242, name=game.exe, "
                                      "Graphics Exception\n").found)

    def test_an_app_being_closed_is_not(self):
        # Xid 45 is the driver cleaning up after a process was killed.
        d = self.evaluate("kernel: NVRM: Xid (PCI:0000:01:00): 45, pid=4242, name=python3, Ch 00000010\n")
        self.assertFalse(d.found)


class NvidiaBootRaceTests(EngineCase):
    def test_waits_for_the_nvidia_card_not_card0(self):
        # simpledrm takes card0 at boot, so the NVIDIA card usually ends up as card1.
        m = self.machine()
        m.modules("nvidia")
        m.journal("Xorg[812]: (EE) NVIDIA(0): Failed to initialize the NVIDIA kernel module. Please see the\n")
        d = engine.evaluate(DB.entries["nvidia-xorg-boot-race"], m.system())
        self.assertTrue(d.recommended)
        script = engine.build_plan(d, m.system()).script
        self.assertNotIn("card0", script)
        self.assertIn('DRIVERS=="nvidia", TAG+="systemd", ENV{SYSTEMD_ALIAS}+="/dev/dri/nvidia"', script)
        self.assertIn("After=dev-dri-nvidia.device", script)


class PodmanRegistryTests(EngineCase):
    def evaluate(self, extra=None):
        m = self.machine()
        m.packages = {"podman"}
        # What containers-common ships on Arch: the upstream file with the list commented out.
        m.write("/usr/share/containers/registries.conf", '# unqualified-search-registries = ["example.com"]\n')
        m.write("/usr/share/containers/registries.conf.d/00-shortnames.conf",
                '[aliases]\n  "archlinux" = "docker.io/library/archlinux"\n')
        if extra:
            path, text = extra
            target = m.home / path if not path.startswith("/") else m.root / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return engine.evaluate(DB.entries["podman-no-search-registries"], m.system())

    def test_arch_defaults_have_no_search_registry(self):
        d = self.evaluate()
        self.assertEqual(d.status, engine.APPLIES)
        self.assertEqual(len(d.found), 1)

    def test_an_empty_list_is_still_none(self):
        d = self.evaluate(("/etc/containers/registries.conf", "unqualified-search-registries = []\n"))
        self.assertEqual(d.status, engine.APPLIES)

    def test_a_per_user_config_counts(self):
        d = self.evaluate((".config/containers/registries.conf", "unqualified-search-registries = ['quay.io']\n"))
        self.assertEqual(d.status, engine.FIXED)


class PolkitAgentTests(EngineCase):
    def evaluate(self, desktop, processes):
        m = self.machine()
        for pid, name in enumerate(["systemd", "polkitd", *processes], 1):
            m.write(f"/proc/{pid}/comm", name + "\n")
        system = m.system()
        system.env["XDG_CURRENT_DESKTOP"] = desktop
        return engine.evaluate(DB.entries["polkit-agent-missing"], system)

    def test_window_manager_without_agent(self):
        d = self.evaluate("Hyprland", ["Hyprland", "waybar"])
        self.assertEqual([f.signal.says for f in d.found],
                         ["You're using a window manager that doesn't start a polkit agent by itself"])
        self.assertTrue(self.evaluate("labwc:wlroots", ["labwc"]).found)

    def test_a_running_agent_clears_it(self):
        for agent in ("polkit-gnome-au", "polkit-kde-auth", "hyprpolkitagent", "lxqt-policykit-"):
            with self.subTest(agent=agent):
                d = self.evaluate("sway", ["sway", agent])
                self.assertLess(d.confidence, 10)


class TlpEthernetTests(EngineCase):
    def machine_with_card(self):
        m = self.machine()
        m.packages = {"tlp"}
        m.outputs["services"] = {"tlp.service": "enabled"}
        m.write("/sys/bus/usb/devices/2-1/idVendor", "0bda\n")
        m.write("/sys/bus/usb/devices/2-1/idProduct", "8156\n")
        return m

    def evaluate(self, m):
        return engine.evaluate(DB.entries["power-tlp-framework-ethernet"], m.system())

    def test_fix_appends_to_other_drop_ins(self):
        m = self.machine_with_card()
        m.write("/etc/tlp.d/50-mine.conf", 'USB_DENYLIST="1234:5678"\n')
        d = self.evaluate(m)
        self.assertEqual(d.status, engine.APPLIES)
        self.assertIn('USB_DENYLIST+="0bda:8156"', engine.build_plan(d, m.system()).script)

    def test_settings_tlp_reads_count(self):
        for line in ('USB_DENYLIST+="0bda:8156"\n', 'USB_DENYLIST="1234:5678 0bda:8156"\n', "USB_AUTOSUSPEND=0\n"):
            with self.subTest(line=line):
                m = self.machine_with_card()
                m.write("/etc/tlp.d/01-mine.conf", line)
                self.assertEqual(self.evaluate(m).status, engine.FIXED)

    def test_settings_tlp_ignores_do_not_count(self):
        # tlp-readconfs only accepts NAME=value at the start of a line.
        for line in ('#USB_DENYLIST="0bda:8156"\n', ' USB_DENYLIST = "0bda:8156"\n', "USB_AUTOSUSPEND=1\n"):
            with self.subTest(line=line):
                m = self.machine_with_card()
                m.write("/etc/tlp.d/01-mine.conf", line)
                self.assertEqual(self.evaluate(m).status, engine.APPLIES)


class SambaUsershareTests(EngineCase):
    def evaluate(self, smb_conf):
        m = self.machine()
        m.packages = {"samba", "kdenetwork-filesharing"}
        m.write("/etc/samba/smb.conf", smb_conf)
        return engine.evaluate(DB.entries["samba-usershares-disabled"], m.system())

    def test_a_path_alone_leaves_them_off(self):
        # usershare max shares defaults to 0, which ignores every usershare.
        d = self.evaluate("[global]\n  usershare path = /var/lib/samba/usershares\n")
        self.assertEqual(d.status, engine.APPLIES)

    def test_a_share_limit_turns_them_on(self):
        self.assertEqual(self.evaluate("[global]\n  usershare max shares = 100\n").status, engine.FIXED)
        self.assertEqual(self.evaluate("[global]\n  usershare max shares = 0\n").status, engine.APPLIES)
