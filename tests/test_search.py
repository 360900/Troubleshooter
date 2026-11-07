import unittest

from troubleshooter.database import load_database
from troubleshooter.search import STRONG_MATCH, SearchIndex

# Plain-language descriptions, none of them copied from an entry, and what must turn up.
QUERIES = {
    "my game keeps flickering": {"display", "amd-psr-flicker", "intel-psr-flicker", "nvidia-nouveau-to-open"},
    "the picture flashes on and off while playing": {"display", "amd-psr-flicker", "intel-psr-flicker"},
    "screen stays black when I open my laptop lid": {"nvidia-sleep"},
    "computer won't wake up from standby": {"nvidia-sleep", "sleep"},
    "everything looks garbled after resuming": {"nvidia-sleep"},
    "my wireless connection dies every ten minutes": {"wifi-realtek-rtw88", "wifi-realtek-rtw89", "wifi-mediatek-aspm", "wifi-intel-power", "network"},
    "there is no wi-fi on my macbook": {"wifi-broadcom-wl"},
    "bcm4360": {"wifi-broadcom-wl"},
    "steam refuses to launch": {"nvidia-lib32", "multilib-disabled", "games"},
    "pacman can't find steam": {"multilib-disabled"},
    "windows program doesn't open in wine": {"wine-mono-missing", "wine"},
    "exe won't run": {"wine-mono-missing", "wine"},
    "my gtx 1060 stopped working after the upgrade": {"nvidia-open-on-legacy"},
    "old nvidia card is not supported anymore": {"nvidia-open-on-legacy"},
    "nouveau is slow": {"nvidia-nouveau-to-open", "nvidia-nouveau-to-580xx"},
    "fps is terrible on my rtx card": {"nvidia-nouveau-to-open"},
    "dxvk says no vulkan on my radeon": {"amd-vulkan-missing", "amd-radeon-to-amdgpu"},
    "speakers are silent, it only shows dummy output": {"sof-firmware-missing"},
    "no audio": {"sof-firmware-missing", "sound"},
    "cannot pair my earbuds": {"bluetooth-service-off", "bluetooth"},
    "firefox vanishes when lots of tabs are open": {"oom-no-swap", "memory-freeze-earlyoom", "crashes"},
    "apps just close by themselves": {"oom-no-swap", "crashes"},
    "my pc locks up when gaming": {"nvidia-xid", "amd-ring-timeout", "crashes"},
    "game crashes to desktop": {"games-max-map-count", "game-crash-coredump", "crashes"},
    "wifi gone after installing a new kernel": {"dkms-headers-missing"},
    "flikering scren": {"display", "amd-psr-flicker", "intel-psr-flicker"},
    "blutooth": {"bluetooth", "bluetooth-service-off"},
    "printer not found": {"cups-network-discovery", "cups-service-off", "devices"},
    "little squares where the smileys should be": {"fonts-emoji-missing"},
    "screen sharing doesn't work": {"portal-kde-missing", "portal-gnome-missing", "portal-wlr-missing", "portal-hyprland-missing"},
    "pacman says invalid signature": {"pacman-signature-invalid"},
    "windows drive won't mount": {"ntfs-windows-fast-startup"},
    "clock is wrong after using windows": {"time-dual-boot-offset"},
    "xbox controller won't connect": {"xbox-wireless-adapter-driver", "xbox-bluetooth-ertm"},
    "my laptop drains battery while sleeping": {"sleep-s2idle-battery-drain"},
    "my virtual machine says kvm is not available": {"virt-hardware-virtualization-off"},
    "docker says permission denied on the socket": {"docker-permission-denied"},
    "fingerprint login stopped working": {"fprint-firmware-unsupported", "fprint-after-sleep"},
    "can't type chinese characters": {"fcitx5-gnome-wayland-popup", "fcitx5-plasma-wayland-kwin", "fcitx5-gtk-missing"},
    "brightness keys do nothing on my laptop": {"backlight-no-control"},
    "thunderbolt dock is not recognized": {"thunderbolt-bolt-missing", "thunderbolt-device-pending"},
    "wireguard tunnel has no dns": {"wireguard-resolvconf-missing"},
    "network share isn't mounted after boot": {"nfs-remote-fs-disabled", "nfs-mount-before-network"},
    "secure boot blocks my wifi driver": {"secureboot-unsigned-module-rejected"},
    "yay fails to build anything": {"aur-base-devel-missing"},
    "scanner is not found": {"sane-airscan-missing", "sane-ipp-usb-missing"},
    "the login screen theme is broken": {"sddm-theme-qt-version"},
    "my macbook pro speakers make no sound": {"mbp-audio-driver"},
    "touch bar stays black": {"mbp-touchbar-driver", "mbp-t1-recovery"},
    "macbook doesn't wake up after closing the lid": {"mbp-sleep-fixes"},
    "macbook wifi can't see 5ghz": {"mbp-wifi-bcm43602"},
    "boot into macos from grub": {"mbp-grub-macos-entry"},
}


def shown(index, query):
    """What the home page shows: up to 4 categories and 6 entries from the top 12 hits."""
    hits = index.search(query, 12)
    hits = [h for h in hits if h.score >= hits[0].score * STRONG_MATCH]
    categories = [h.target.id for h in hits if h.is_category][:4]
    entries = [h.target.id for h in hits if not h.is_category][:6]
    return set(categories + entries)


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = SearchIndex(load_database())

    def test_descriptions_find_the_right_entries(self):
        for query, wanted in QUERIES.items():
            found = shown(self.index, query)
            with self.subTest(query=query, found=found):
                self.assertTrue(found & wanted)

    def test_nonsense_finds_nothing(self):
        for query in ("asdfgh", "qwerty zxcv", "the a of", ""):
            self.assertEqual(self.index.search(query), [], query)

    def test_scores_stay_between_0_and_1(self):
        for query in QUERIES:
            for hit in self.index.search(query):
                self.assertTrue(0 < hit.score <= 1)
