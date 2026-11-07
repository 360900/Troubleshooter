import os
import unittest

from tests.test_engine import DB, EngineCase
from troubleshooter import engine


class DesktopCase(EngineCase):
    def evaluate(self, entry_id, m, env=None):
        system = m.system()
        system.env = env or {}
        return engine.evaluate(DB.entries[entry_id], system)


class JavaTests(DesktopCase):
    def java(self, default_target):
        m = self.machine()
        m.packages = {"java-runtime-common", "jre-openjdk"}
        m.write("/usr/lib/jvm/java-25-openjdk/bin/java", "")
        for link in ("default", "default-runtime"):
            os.symlink(default_target, m.root / "usr/lib/jvm" / link)
        return self.evaluate("java-default-broken", m)

    def test_links_left_on_dev_null_are_found(self):
        # Removing the default Java leaves both links pointing at /dev/null, as packaged.
        self.assertTrue(self.java("/dev/null").recommended)

    def test_working_links_are_fixed(self):
        self.assertEqual(self.java("java-25-openjdk").status, engine.FIXED)


class MissingPackageTests(DesktopCase):
    def test_git_builds_count_as_installed(self):
        cases = [
            ("kde-powerdevil-missing", {"plasma-workspace", "powerdevil-git"}, "/usr/lib/org_kde_powerdevil", "KDE"),
            ("portal-kde-missing", {"plasma-workspace", "xdg-desktop-portal-kde-git"},
             "/usr/share/xdg-desktop-portal/portals/kde.portal", "KDE"),
            ("portal-gnome-missing", {"gnome-shell", "xdg-desktop-portal-gnome-git"},
             "/usr/share/xdg-desktop-portal/portals/gnome.portal", "GNOME"),
            ("xfce-gvfs-missing", {"thunar", "gvfs-git"}, "/usr/lib/gvfsd", "XFCE"),
        ]
        for entry_id, packages, path, desktop in cases:
            with self.subTest(entry=entry_id):
                m = self.machine()
                m.packages = packages
                env = {"XDG_CURRENT_DESKTOP": desktop}
                self.assertTrue(self.evaluate(entry_id, m, env).recommended)
                m.write(path, "")
                self.assertEqual(self.evaluate(entry_id, m, env).status, engine.FIXED)

    def test_thunar_without_gvfs_is_only_recommended_in_xfce(self):
        m = self.machine()
        m.packages = {"thunar"}
        self.assertFalse(self.evaluate("xfce-gvfs-missing", m, {"XDG_CURRENT_DESKTOP": "Hyprland"}).recommended)
        self.assertTrue(self.evaluate("xfce-gvfs-missing", m, {"XDG_CURRENT_DESKTOP": "XFCE"}).recommended)


if __name__ == "__main__":
    unittest.main()
