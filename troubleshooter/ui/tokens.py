"""oneDesign tokens (assets/one.json) resolved to colors. Same math as oneDesign's engine.js."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

TOKENS = json.loads((Path(__file__).parent / "assets" / "one.json").read_text())
ACCENT = "blue"


def _gamma(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


@dataclass(frozen=True)
class Color:
    r: float
    g: float
    b: float
    a: float = 1.0

    @classmethod
    def oklch(cls, L, C, H, a=1.0):
        L = min(max(L, 0.0), 1.0)
        h = math.radians(H)
        x, y = C * math.cos(h), C * math.sin(h)
        l = (L + 0.3963377774 * x + 0.2158037573 * y) ** 3
        m = (L - 0.1055613458 * x - 0.0638541728 * y) ** 3
        s = (L - 0.0894841775 * x - 1.2914855480 * y) ** 3
        return cls(_gamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
                   _gamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
                   _gamma(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s), a)

    def alpha(self, a):
        return Color(self.r, self.g, self.b, a)

    @property
    def css(self) -> str:
        r, g, b = (round(v * 255) for v in (self.r, self.g, self.b))
        return f"rgba({r},{g},{b},{round(self.a * 255)})"

    @property
    def hex(self) -> str:
        return "#%02x%02x%02x" % tuple(round(v * 255) for v in (self.r, self.g, self.b))


WHITE = Color(1, 1, 1)
BLACK = Color(0, 0, 0)


def resolve(dark: bool) -> dict[str, Color]:
    mode = "dark" if dark else "light"
    hue, chroma = TOKENS["accents"][ACCENT]["hue"], TOKENS["accents"][ACCENT]["chroma"]
    n = TOKENS["color"]["neutral"][mode]
    nc = TOKENS["color"]["neutralChroma"]
    ink = WHITE if dark else BLACK
    c: dict[str, Color] = {}
    for role in ("canvas", "sidebar", "surface", "surfaceAlt", "float", "headerbar"):
        c[role] = Color.oklch(n[role], nc, hue)
    for role in ("text", "text2", "text3"):
        c[role] = Color.oklch(n[role], nc * 2, hue)
    c["stroke"] = ink.alpha(n["stroke"])
    c["strokeStrong"] = ink.alpha(n["strokeStrong"])
    c["hover"] = ink.alpha(0.07 if dark else 0.05)
    c["active"] = ink.alpha(0.12 if dark else 0.09)
    c["fill"] = ink.alpha(0.09 if dark else 0.06)
    c["fillStrong"] = ink.alpha(0.16 if dark else 0.12)
    aL = TOKENS["color"]["accent"][mode]
    c["accent"] = Color.oklch(aL, chroma, hue)
    c["accentHover"] = Color.oklch(aL + (0.04 if dark else -0.04), chroma, hue)
    c["accentPressed"] = Color.oklch(aL + (0.08 if dark else -0.08), chroma, hue)
    c["onAccent"] = Color.oklch(0.2, min(chroma, 0.06), hue) if aL > 0.68 else WHITE
    c["accentText"] = Color.oklch(max(aL, 0.76) if dark else min(aL, 0.52), chroma, hue)
    cL, ocL, cf = TOKENS["color"]["container"][mode]
    c["container"] = Color.oklch(cL, chroma * cf, hue)
    c["onContainer"] = Color.oklch(ocL, chroma * 0.8, hue)
    for name, status in TOKENS["status"].items():
        L = 0.74 if dark else 0.56
        c[name] = Color.oklch(L, status["chroma"], status["hue"])
        c[name + "Soft"] = Color.oklch(L, status["chroma"], status["hue"], 0.2 if dark else 0.13)
    c["onDanger"] = Color.oklch(0.2, 0.06, 25) if dark else WHITE
    return c
