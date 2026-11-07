"""Audit of the system entries: disks, boot, updates, memory, CPU."""
import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class FstabTests(EngineCase):
    TIMEOUT = ("systemd[1]: dev-disk-by\\x2duuid-1234.device: Job dev-disk-by\\x2duuid-1234.device/start timed out.\n"
               "systemd[1]: Timed out waiting for device /dev/disk/by-uuid/1234.\n"
               "systemd[1]: Dependency failed for /mnt/data.\n")

    def fstab(self, journal):
        m = self.machine()
        m.write("/etc/fstab", "UUID=1234 /mnt/data ext4 defaults 0 2\n")
        m.journal(journal)
        return engine.evaluate(DB.entries["fstab-missing-device"], m.system())

    def test_a_nofail_drive_that_is_unplugged_is_not_recommended(self):
        # Startup doesn't wait for it, so this is just the advice already followed.
        self.assertFalse(self.fstab(self.TIMEOUT).recommended)

    def test_a_drive_that_held_up_startup_is_recommended(self):
        d = self.fstab(self.TIMEOUT + "systemd[1]: Dependency failed for Local File Systems.\n")
        self.assertTrue(d.recommended)
        self.assertTrue(self.fstab(self.TIMEOUT + "systemd[1]: Dependency failed for Swaps.\n").recommended)


class Ext4Tests(EngineCase):
    def ext4(self, errors="0\n", journal=""):
        m = self.machine()
        m.write("/proc/filesystems", "nodev\tproc\n\text4\n")
        m.write("/sys/fs/ext4/nvme0n1p2/errors_count", errors)
        m.journal(journal)
        return engine.evaluate(DB.entries["fs-ext4-errors"], m.system())

    def test_recorded_errors_are_found_after_the_log_is_gone(self):
        self.assertTrue(self.ext4("3\n").recommended)

    def test_a_clean_file_system_is_not_flagged(self):
        self.assertFalse(self.ext4().found)

    def test_benign_mount_messages_are_not_errors(self):
        d = self.ext4(journal="kernel: EXT4-fs (sda1): orphan cleanup on readonly fs\n"
                              "kernel: EXT4-fs (sda1): recovery complete\n"
                              "kernel: EXT4-fs (sda1): mounted filesystem r/w with ordered data mode.\n")
        self.assertFalse(d.found)

    def test_a_manual_repair_last_boot_alone_is_not_recommended(self):
        # It ended in emergency mode, where the repair was most likely done.
        d = self.ext4(journal="systemd-fsck[312]: /dev/sda2: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.\n")
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)


class BtrfsTests(EngineCase):
    STATS = "write_errs 0\nread_errs 0\nflush_errs 0\ncorruption_errs {}\ngeneration_errs 0\n"

    def btrfs(self, corrupt=0, journal=""):
        m = self.machine()
        m.write("/proc/self/mounts", "/dev/nvme0n1p2 / btrfs rw,noatime,compress=zstd:1 0 0\n")
        m.write("/sys/fs/btrfs/164c189d/devinfo/1/error_stats", self.STATS.format(corrupt))
        m.journal(journal)
        return engine.evaluate(DB.entries["btrfs-checksum-errors"], m.system())

    def test_counts_shown_at_mount_are_not_a_new_failure(self):
        d = self.btrfs(journal="kernel: BTRFS info (device nvme0n1p2): bdev /dev/nvme0n1p2 errs: "
                               "wr 0, rd 0, flush 0, corrupt 4, gen 0\n")
        self.assertFalse(d.found)

    def test_an_old_count_alone_is_only_a_hint(self):
        d = self.btrfs(corrupt=4)
        self.assertEqual(len(d.found), 1)
        self.assertFalse(d.recommended)

    def test_a_failed_checksum_is_recommended(self):
        for line in ("BTRFS warning (device nvme0n1p2): csum failed root 5 ino 257 off 0 csum 0x1 expected csum 0x2 mirror 1",
                     "BTRFS warning (device sda1): scrub: tree block 30408704 mirror 1 has bad csum, has 0x1 want 0x2",
                     "BTRFS error (device sda1 state EA): bdev /dev/sda1 errs: wr 0, rd 0, flush 0, corrupt 1, gen 0"):
            self.assertTrue(self.btrfs(journal=f"kernel: {line}\n").recommended, line)


class MicrocodeTests(EngineCase):
    def cpu(self, vendor, extra=""):
        m = self.machine()
        m.write("/proc/cpuinfo", f"vendor_id\t: {vendor}\nflags\t\t: fpu sse2 avx2{extra}\n")
        return m

    def test_missing_microcode_is_recommended(self):
        self.assertIn("microcode-amd-missing", self.scan_ids(self.cpu("AuthenticAMD")))
        self.assertIn("microcode-intel-missing", self.scan_ids(self.cpu("GenuineIntel")))

    def test_virtual_machines_are_left_alone(self):
        self.assertNotIn("microcode-amd-missing", self.scan_ids(self.cpu("AuthenticAMD", " hypervisor")))

    def test_git_packages_and_other_providers_count(self):
        m = self.cpu("AuthenticAMD")
        m.packages = {"amd-ucode-git"}
        self.assertNotIn("microcode-amd-missing", self.scan_ids(m))
        m = self.cpu("AuthenticAMD")
        m.write("/usr/lib/firmware/amd-ucode/microcode_amd_fam19h.bin", "x")
        self.assertNotIn("microcode-amd-missing", self.scan_ids(m))
        m = self.cpu("GenuineIntel")
        m.write("/usr/lib/firmware/intel-ucode/06-b7-01", "x")
        self.assertNotIn("microcode-intel-missing", self.scan_ids(m))


class NtfsTests(EngineCase):
    def plugin(self, tag):
        m = self.machine()
        m.packages = {"ntfs-3g"}
        m.journal(f"ntfs-3g[2211]: Could not load plugin /usr/lib/ntfs-3g/ntfs-plugin-{tag}.so: Success\n")
        return "ntfs3g-unsupported-reparse-point" in self.scan_ids(m)

    def test_compressed_and_onedrive_files_are_recommended(self):
        self.assertTrue(self.plugin("80000017"))
        self.assertTrue(self.plugin("9000001a"))

    def test_store_app_aliases_are_ignored(self):
        self.assertFalse(self.plugin("8000001b"))

    def test_each_driver_refusing_a_windows_drive_is_caught(self):
        for line in ('kernel: ntfs3(nvme0n1p3): volume is dirty and "force" flag is not set!',
                     "kernel: ntfs3(nvme0n1p3): failed to replay log file. Can't mount rw!",
                     "kernel: ntfs: (device sda3): ntfs_fill_super(): Windows is hibernated.  Mounting read-only.",
                     "mount[812]: Windows is hibernated, refused to mount."):
            m = self.machine()
            m.modules("ntfs3")
            m.journal(line + "\n")
            self.assertIn("ntfs-windows-fast-startup", self.scan_ids(m), line)


class OomTests(EngineCase):
    KILL = ("kernel: Out of memory: Killed process 4242 (firefox) total-vm:9000000kB, anon-rss:7000000kB, "
            "file-rss:0kB, shmem-rss:0kB, UID:1000 pgtables:20000kB oom_score_adj:100\n")

    def oom(self, journal, swaps=""):
        m = self.machine()
        m.write("/proc/swaps", "Filename\tType\tSize\tUsed\tPriority\n" + swaps)
        m.journal(journal)
        return m

    def test_no_swap_alone_is_not_a_problem(self):
        self.assertNotIn("oom-no-swap", self.scan_ids(self.oom("")))

    def test_killed_apps_without_swap_are_recommended(self):
        self.assertIn("oom-no-swap", self.scan_ids(self.oom(self.KILL)))

    def test_a_service_hitting_its_own_limit_is_not_blamed_on_swap(self):
        self.assertNotIn("oom-no-swap", self.scan_ids(self.oom(self.KILL.replace("Out of", "Memory cgroup out of"))))

    def test_zram_counts_as_swap(self):
        m = self.oom(self.KILL, "/dev/zram0\tpartition\t32496636\t0\t100\n")
        self.assertNotIn("oom-no-swap", self.scan_ids(m))

    def test_undo_turns_compressed_swap_off_again(self):
        m = self.oom(self.KILL)
        system = m.system()
        plan = engine.build_plan(engine.evaluate(DB.entries["oom-no-swap"], system), system)
        self.assertIn("systemctl stop systemd-zram-setup@zram0.service", plan.undo_script)


if __name__ == "__main__":
    unittest.main()
