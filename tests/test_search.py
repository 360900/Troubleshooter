import unittest

from troubleshooter.database import load_database
from troubleshooter.search import SearchIndex

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
    "firefox vanishes when lots of tabs are open": {"oom-no-swap", "crashes"},
    "apps just close by themselves": {"oom-no-swap", "crashes"},
    "my pc locks up when gaming": {"nvidia-xid", "amd-ring-timeout", "crashes"},
    "game crashes to desktop": {"games-max-map-count", "game-crash-coredump", "crashes"},
    "wifi gone after installing a new kernel": {"dkms-headers-missing"},
    "flikering scren": {"display", "amd-psr-flicker", "intel-psr-flicker"},
    "blutooth": {"bluetooth", "bluetooth-service-off"},
}
TOP = 5


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = SearchIndex(load_database())

    def test_descriptions_find_the_right_entries(self):
        for query, wanted in QUERIES.items():
            found = {hit.target.id for hit in self.index.search(query, TOP)}
            with self.subTest(query=query, found=found):
                self.assertTrue(found & wanted)

    def test_nonsense_finds_nothing(self):
        for query in ("asdfgh", "qwerty zxcv", "the a of", ""):
            self.assertEqual(self.index.search(query), [], query)

    def test_scores_stay_between_0_and_1(self):
        for query in QUERIES:
            for hit in self.index.search(query):
                self.assertTrue(0 < hit.score <= 1)
