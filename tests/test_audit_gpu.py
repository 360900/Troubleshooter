"""Graphics entries: what they catch, and what a healthy machine must not trip."""
from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class RadeonToAmdgpuTests(EngineCase):
    def hawaii(self, radeon_si="1", amdgpu_si="0"):
        m = self.machine()
        m.pci("0000:01:00.0", "1002", "67b1", "030000", "radeon")  # R9 290
        m.write("/sys/module/radeon/parameters/si_support", radeon_si + "\n")
        m.write("/sys/module/radeon/parameters/cik_support", radeon_si + "\n")
        m.write("/sys/module/amdgpu/parameters/si_support", amdgpu_si + "\n")
        m.write("/sys/module/amdgpu/parameters/cik_support", amdgpu_si + "\n")
        return m

    def evaluate(self, m):
        return engine.evaluate(DB.entries["amd-radeon-to-amdgpu"], m.system())

    def test_older_kernel_defaults_are_not_mistaken_for_a_choice(self):
        # Before 6.19 radeon claims these cards by default and sysfs shows radeon 1, amdgpu 0.
        self.assertTrue(self.evaluate(self.hawaii()).recommended)

    def test_newer_kernel_with_radeon_still_bound_is_caught(self):
        self.assertTrue(self.evaluate(self.hawaii("-1", "-1")).recommended)

    def test_radeon_chosen_with_a_parameter_is_left_alone(self):
        m = self.hawaii()
        m.write("/proc/cmdline", "root=x radeon.cik_support=1\n")
        self.assertEqual(self.evaluate(m).status, engine.FIXED)
        m = self.hawaii()
        m.write("/etc/modprobe.d/amdgpu.conf", "options amdgpu cik_support=0\n")
        self.assertEqual(self.evaluate(m).status, engine.FIXED)

    def test_card_on_amdgpu_does_not_apply(self):
        m = self.machine()
        m.pci("0000:01:00.0", "1002", "67b1", "030000", "amdgpu")
        self.assertEqual(self.evaluate(m).status, engine.NOT_APPLICABLE)


class VulkanMissingTests(EngineCase):
    ENTRIES = ("amd-vulkan-missing", "intel-vulkan-missing")

    def gpu(self, entry, packages=(), nvidia=False):
        m = self.machine()
        if entry == "amd-vulkan-missing":
            m.pci("0000:05:00.0", "1002", "73bf", "030000", "amdgpu")  # RX 6800
        else:
            m.pci("0000:00:02.0", "8086", "a7a0", "030000", "i915")  # Raptor Lake
        if nvidia:
            m.pci("0000:01:00.0", "10de", "2860", "030200", "nvidia")
        m.packages = {"mesa", "vulkan-icd-loader", *packages}
        return engine.evaluate(DB.entries[entry], m.system())

    def test_no_vulkan_driver_and_nothing_using_vulkan_is_not_recommended(self):
        for entry in self.ENTRIES:
            with self.subTest(entry=entry):
                d = self.gpu(entry)
                self.assertEqual(d.status, engine.APPLIES)
                self.assertFalse(d.recommended)

    def test_games_without_the_driver_are_recommended(self):
        for entry in self.ENTRIES:
            for packages in ({"steam"}, {"wine-staging"}, {"vulkan-swrast"}):
                with self.subTest(entry=entry, packages=packages):
                    self.assertTrue(self.gpu(entry, packages).recommended)

    def test_games_on_the_nvidia_card_of_a_hybrid_laptop_are_left_alone(self):
        for entry in self.ENTRIES:
            with self.subTest(entry=entry):
                self.assertFalse(self.gpu(entry, {"steam", "nvidia-utils"}, nvidia=True).recommended)

    def test_installed_driver_is_fixed(self):
        self.assertEqual(self.gpu("amd-vulkan-missing", {"steam", "vulkan-radeon"}).status, engine.FIXED)
        self.assertEqual(self.gpu("intel-vulkan-missing", {"steam", "vulkan-intel"}).status, engine.FIXED)


class NvidiaPackageNameTests(EngineCase):
    def test_open_driver_for_the_lts_kernel_on_pascal_is_caught(self):
        # Arch names it nvidia-open-lts, and CachyOS also has a 580xx open build.
        for driver in ("nvidia-open-lts", "nvidia-580xx-open-dkms"):
            with self.subTest(driver=driver):
                m = self.machine()
                m.pci("0000:01:00.0", "10de", "1c03", "030000")  # GTX 1060
                m.packages = {driver, "nvidia-utils"}
                self.assertIn("nvidia-open-on-legacy", self.scan_ids(m))

    def test_kepler_next_to_a_turing_card_keeps_nouveau(self):
        m = self.machine()
        m.pci("0000:01:00.0", "10de", "2484", "030000", "nouveau")  # RTX 3070
        m.pci("0000:02:00.0", "10de", "1180", "030000", "nouveau")  # GTX 680
        m.modules("nouveau")
        self.assertNotIn("nvidia-nouveau-to-open", self.scan_ids(m))

    def test_steam_driver_swap_skips_machines_with_an_nvidia_kernel_driver(self):
        for driver in ("nvidia-open-lts", "nvidia-580xx-open-dkms"):
            with self.subTest(driver=driver):
                m = self.machine()
                m.pci("0000:05:00.0", "1002", "73bf", "030000", "amdgpu")
                m.packages = {"steam", "lib32-nvidia-utils", "nvidia-utils", driver}
                self.assertNotIn("steam-nvidia-vulkan-on-amd", self.scan_ids(m))


class ModesetOffTests(EngineCase):
    def test_parameter_that_left_the_card_without_a_driver_is_recommended(self):
        m = self.machine()
        m.pci("0000:00:02.0", "8086", "a7a0", "030000")
        m.write("/proc/cmdline", "root=x i915.modeset=0\n")
        self.assertIn("gpu-driver-modeset-off", self.scan_ids(m))

    def test_radeon_off_while_amdgpu_drives_the_card_is_harmless(self):
        # An old way to move SI and CIK cards to amdgpu.
        m = self.machine()
        m.pci("0000:01:00.0", "1002", "6798", "030000", "amdgpu")  # HD 7970
        m.write("/proc/cmdline", "root=x radeon.modeset=0\n")
        self.assertNotIn("gpu-driver-modeset-off", self.scan_ids(m))


class GdmNoGpuTests(EngineCase):
    def test_current_gnome_shell_wording_is_caught(self):
        for line in ("gnome-shell[1203]: Failed to setup: No GPUs found",
                     "org.gnome.Shell.desktop[1927]: Failed to setup: No GPUs found",
                     "gnome-shell[569]: Failed to create backend: No GPUs found"):
            with self.subTest(line=line):
                m = self.machine()
                m.packages = {"gdm"}
                m.journal(f"Nov 07 10:00:00 host {line}\n")
                self.assertIn("gdm-wayland-no-gpu", self.scan_ids(m))
