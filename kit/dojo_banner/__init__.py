"""README banners in the dojocoding.io visual system.

One ink (navy #201E3D), one accent (peach #FF7151, fills only), paper #F1F1F9,
one family (Archivo), 2px rules and hard offset shadows. Text is shaped with
HarfBuzz and drawn as outlines, so the SVGs render the same everywhere,
including GitHub, whose image sandbox loads no web fonts.

    dojo-banner docs/assets/banner.toml            # writes banner-light.svg, banner-dark.svg
    dojo-banner docs/assets/banner.toml --preview  # and PNG previews, if Chrome is installed
"""
from __future__ import annotations

import argparse
import base64
import math
import os
import shutil
import subprocess
import re
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ASSETS = Path(__file__).parent / "assets"
CACHE = Path(os.environ.get("DOJO_KIT_CACHE", Path.home() / ".cache" / "dojo-readme-kit"))
FONT_URLS = {  # both OFL, fetched once into CACHE
    "archivo": "https://github.com/google/fonts/raw/main/ofl/archivo/Archivo%5Bwdth%2Cwght%5D.ttf",
    "mono": "https://github.com/google/fonts/raw/main/ofl/ibmplexmono/IBMPlexMono-Medium.ttf",
}

# -- tokens (dojocoding.io DESIGN.md) -------------------------------------------

INK, INK_2, PAPER = "#201E3D", "#4A4866", "#F1F1F9"
PANEL, PANEL_2 = "#FFFFFF", "#E7E6F1"
ACCENT, ACCENT_SOFT = "#FF7151", "#FFD9D0"
OK, OK_BG, BAD, BAD_BG = "#1F7A3F", "#E4F3E8", "#B42318", "#FBEAE7"

# Light is the paper chapter; dark is the navy chapter, where ink, rules and
# shadows invert. Cards and the plate are light surfaces in both, so anything
# drawn on them keeps navy ink.
THEMES = {
    "light": dict(page=PAPER, ink=INK, ink_2=INK_2, rule=INK, meta_fill=INK, meta_text=PAPER),
    "dark": dict(page=INK, ink=PAPER, ink_2="#C9C8DD", rule=PAPER, meta_fill=PAPER, meta_text=INK),
}

W, H = 1280, 428
LEFT_X, LEFT_W = 64, 700


# -- type -----------------------------------------------------------------------

def _fetch(key: str) -> Path:
    path = CACHE / Path(urllib.request.url2pathname(FONT_URLS[key].rsplit("/", 1)[1]))
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(FONT_URLS[key], path)
    return path


class Font:
    """One font at one variable-axis location (e.g. Archivo wght 800, wdth 112)."""

    _tt: dict[str, TTFont] = {}

    def __init__(self, key: str, **axes: float) -> None:
        path = _fetch(key)
        if key not in Font._tt:
            Font._tt[key] = TTFont(path)
        self.tt = Font._tt[key]
        self.upem = self.tt["head"].unitsPerEm
        self.axes = axes
        self.glyphs = self.tt.getGlyphSet(location=axes) if axes else self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()
        self.hb = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))
        if axes:
            self.hb.set_variations(axes)
        self.id = key + "".join(f"-{k}{v:g}" for k, v in sorted(axes.items()))
        os2 = self.tt["OS/2"]
        self.cap = os2.sCapHeight / self.upem
        self.asc = self.tt["hhea"].ascent / self.upem
        self.desc = -self.tt["hhea"].descent / self.upem

    def shape(self, text: str) -> list[tuple[int, int, int, int]]:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": True})
        return [(i.codepoint, p.x_advance, p.x_offset, p.y_offset)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        """Advance width in px; tracking is in em, added after every glyph like CSS letter-spacing."""
        glyphs = self.shape(text)
        return sum(g[1] for g in glyphs) * size / self.upem + tracking * size * len(glyphs)


class Defs:
    """Glyph outlines used by one document, each emitted once."""

    def __init__(self) -> None:
        self.paths: dict[str, str] = {}

    def glyph(self, font: Font, gid: int) -> str | None:
        key = f"{font.id}-{gid}"
        if key not in self.paths:
            pen = SVGPathPen(font.glyphs, ntos=lambda v: f"{v:.0f}")
            font.glyphs[font.order[gid]].draw(TransformPen(pen, (1, 0, 0, -1, 0, 0)))
            self.paths[key] = pen.getCommands()
        return key if self.paths[key] else None

    def render(self) -> str:
        return "<defs>" + "".join(f'<path id="{k}" d="{d}"/>' for k, d in self.paths.items()) + "</defs>"


class Doc:
    """An SVG under construction: shapes in order, glyph outlines in <defs>."""

    def __init__(self, w: float, h: float, title: str) -> None:
        self.w, self.h, self.title = w, h, title
        self.defs = Defs()
        self.parts: list[str] = []

    def add(self, *svg: str) -> None:
        self.parts.extend(svg)

    def text(self, font: Font, s: str, x: float, y: float, size: float, fill: str, *,
             tracking: float = 0.0, anchor: str = "start") -> float:
        """Draw text with its baseline at y. Returns its advance width."""
        width = font.width(s, size, tracking)
        x -= {"start": 0.0, "middle": width / 2, "end": width}[anchor]
        scale = size / font.upem
        uses = []
        for gid, adv, dx, dy in font.shape(s):
            ref = self.defs.glyph(font, gid)
            if ref:
                uses.append(f'<use href="#{ref}" transform="translate({x + dx * scale:.2f} {y - dy * scale:.2f}) '
                            f'scale({scale:.5f})"/>')
            x += adv * scale + tracking * size
        self.parts.append(f'<g fill="{fill}">{"".join(uses)}</g>')
        return width

    def svg(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.title, {chr(34): "&quot;"})}">'
                f"<title>{escape(self.title)}</title>{self.defs.render()}{''.join(self.parts)}</svg>\n")


# The faces the system uses, by role (DESIGN.md "Type").
def _faces() -> dict[str, Font]:
    return {
        "display": Font("archivo", wght=800, wdth=112),   # names, headlines
        "strong": Font("archivo", wght=700, wdth=100),    # chips, labels
        "label": Font("archivo", wght=700, wdth=118),     # the nameplate tab
        "body": Font("archivo", wght=600, wdth=100),      # taglines, rows
        "quiet": Font("archivo", wght=500, wdth=100),     # secondary copy
        "tall": Font("archivo", wght=800, wdth=62),       # stat numerals
        "mono": Font("mono"),                             # ledger metadata, code
    }


F: dict[str, Font] = {}


def faces() -> dict[str, Font]:
    if not F:
        F.update(_faces())
    return F


# -- primitives -----------------------------------------------------------------

def rrect(x: float, y: float, w: float, h: float, r: float, fill: str, stroke: str | None = None,
          sw: float = 2.0, extra: str = "") -> str:
    s = f' stroke="{stroke}" stroke-width="{sw:g}"' if stroke else ""
    # Strokes are centred on the path; inset by half so the box keeps its outer size.
    i = sw / 2 if stroke else 0
    return (f'<rect x="{x + i:.2f}" y="{y + i:.2f}" width="{w - 2 * i:.2f}" height="{h - 2 * i:.2f}" '
            f'rx="{max(r - i, 0):.2f}" fill="{fill}"{s}{extra}/>')


def hard_shadow(x, y, w, h, r, color, offset) -> str:
    return rrect(x + offset, y + offset, w, h, r, color)


def sparkle(cx: float, cy: float, r: float, fill: str = ACCENT, stroke: str = INK, rotate: float = 8) -> str:
    """The ✦ stamp: a four-point star with concave sides."""
    k = 0.18 * r
    d = (f"M0 {-r}Q{k} {-k} {r} 0Q{k} {k} 0 {r}Q{-k} {k} {-r} 0Q{-k} {-k} 0 {-r}Z")
    return (f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="2" stroke-linejoin="round" '
            f'transform="translate({cx:.1f} {cy:.1f}) rotate({rotate})"/>')


def image(path: Path, x: float, y: float, w: float, h: float) -> str:
    data = base64.b64encode(path.read_bytes()).decode()
    return f'<image href="data:image/png;base64,{data}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"/>'


def chip(doc: Doc, text: str, x: float, y: float, *, kind: str = "status", theme: dict | None = None,
         fill: str = PANEL_2, h: float | None = None) -> float:
    """Pills: 'peach' (what a repo ships), 'meta' (mono, navy) and 'status' (ledger chip on light cards).
    Returns the chip width."""
    f = faces()
    t = theme or THEMES["light"]
    if kind == "peach":
        h = h or 46
        w = f["strong"].width(text, 21) + 40
        doc.add(rrect(x, y, w, h, h / 2, ACCENT, t["rule"]))
        doc.text(f["strong"], text, x + 20, y + h / 2 + f["strong"].cap * 21 / 2, 21, INK)
    elif kind == "meta":
        h = h or 46
        label = text.upper()
        w = f["mono"].width(label, 16, 0.06) + 40 - 0.06 * 16
        doc.add(rrect(x, y, w, h, h / 2, t["meta_fill"], t["rule"]))
        doc.text(f["mono"], label, x + 20, y + h / 2 + f["mono"].cap * 16 / 2, 16, t["meta_text"], tracking=0.06)
    else:
        h = h or 28
        label = text.upper()
        w = f["mono"].width(label, 13, 0.06) + 24 - 0.06 * 13
        doc.add(rrect(x, y, w, h, h / 2, fill, INK, 1.5))
        doc.text(f["mono"], label, x + 12, y + h / 2 + f["mono"].cap * 13 / 2, 13, INK, tracking=0.06)
    return w


def status_width(text: str) -> float:
    f = faces()
    return f["mono"].width(text.upper(), 13, 0.06) + 24 - 0.06 * 13


# -- the card ---------------------------------------------------------------------

CARD_BOXES = {  # x, y, w, h: the artifact on the plate, per motif
    "terminal": (812, 84, 430, 262),
    "tools": (812, 84, 430, 262),
    "ledger": (812, 60, 430, 308),
    "custom": (838, 46, 404, 318),
}


def card(doc: Doc, cfg: dict, theme: dict, body: Callable[[Doc, float, float, float, float], None] | None) -> tuple:
    kind = cfg.get("kind", "terminal")
    x, y, w, h = cfg.get("box", CARD_BOXES["custom" if body else kind])
    doc.add(hard_shadow(x, y, w, h, 24, INK, 8), rrect(x, y, w, h, 24, PANEL, INK))
    f = faces()
    doc.text(f["mono"], cfg.get("label", "").upper(), x + 22, y + 37, 14, INK, tracking=0.06)
    if cfg.get("status"):
        sw = status_width(cfg["status"])
        chip(doc, cfg["status"], x + w - 20 - sw, y + 15, fill=OK_BG if cfg.get("status_ok") else PANEL_2)
    doc.add(f'<path d="M{x + 1} {y + 58}H{x + w - 1}" stroke="{INK}" stroke-width="2"/>')
    by, bh = y + 60, h - 60
    if body:
        body(doc, x, by, w, bh)
    elif kind == "terminal":
        _terminal(doc, cfg, x, by, w, bh)
    elif kind == "tools":
        _tools(doc, cfg, x, by, w, bh)
    elif kind == "ledger":
        _ledger(doc, cfg, x, by, w, bh)
    return x, y, w, h


def _parts(line) -> list[tuple[str, bool]]:
    """A terminal line is "text" or ["text", "dim text"]."""
    if isinstance(line, str):
        return [(line, False)]
    return [(line[0], False)] + ([(" " + line[1], True)] if len(line) > 1 else [])


def _terminal(doc: Doc, cfg: dict, x, y, w, h) -> None:
    f = faces()["mono"]
    lines = [_parts(line) for line in cfg.get("lines", [])]
    if cfg.get("dim"):
        lines.append([(cfg["dim"], True)])
    widest = max((sum(f.width(s, 17) for s, _ in parts) for parts in lines), default=1)
    size = min(17.0, (w - 48) / widest * 17)
    step = min(size * 1.9, (h - 30) / max(len(lines), 1))
    base = y + 18 + step / 2 + f.cap * size / 2
    for i, parts in enumerate(lines):
        cx = x + 22
        for s, dim in parts:
            cx += doc.text(f, s, cx, base + i * step, size, INK_2 if dim else INK)
    if cfg.get("dim"):  # the peach caret closes the last line
        doc.add(rrect(cx + 3, base + (len(lines) - 1) * step - size * 0.95, size * 0.62, size * 1.22, 0, ACCENT))


def _tools(doc: Doc, cfg: dict, x, y, w, h) -> None:
    f = faces()["mono"]
    cx, cy = x + 20, y + 22
    for name in cfg.get("tools", []):
        tw = f.width(name, 15) + 26
        if cx + tw > x + w - 20:
            cx, cy = x + 20, cy + 44
        doc.add(rrect(cx, cy, tw, 32, 16, PANEL_2, INK, 1.5))
        doc.text(f, name, cx + 13, cy + 16 + f.cap * 15 / 2, 15, INK)
        cx += tw + 10


def _ledger(doc: Doc, cfg: dict, x, y, w, h) -> None:
    fs = faces()
    rows = cfg.get("rows", [])
    rh = h / max(len(rows), 1)
    for i, row in enumerate(rows):
        title, sub, *tag = row
        ry = y + i * rh
        tag_w = status_width(tag[0]) if tag else 0
        room = w - 44 - (tag_w + 12 if tag else 0)
        size = min(19.0, room / max(fs["display"].width(title, 19, -0.01), 1) * 19)
        doc.text(fs["display"], title, x + 20, ry + rh / 2 - 2, size, INK, tracking=-0.01)
        doc.text(fs["quiet"], sub, x + 20, ry + rh / 2 + 18, 15, INK_2)
        if tag:
            chip(doc, tag[0], x + w - 20 - tag_w, ry + rh / 2 - 14)
        if i < len(rows) - 1:
            doc.add(f'<path d="M{x + 1} {ry + rh:.1f}H{x + w - 1}" stroke="{INK}" stroke-width="1"/>')


# -- the banner -------------------------------------------------------------------

def _split_marker(s: str) -> list[tuple[str, bool]]:
    """'as [open data]' -> [('as ', False), ('open data', True)]: brackets mark the one marker phrase."""
    out, rest = [], s
    while "[" in rest and "]" in rest:
        a, b = rest.index("["), rest.index("]")
        if a:
            out.append((rest[:a], False))
        out.append((rest[a + 1:b], True))
        rest = rest[b + 1:]
    if rest:
        out.append((rest, False))
    return out


def _fit_name(name: str, font: Font) -> tuple[float, list[str]]:
    """One line from 104 px down to 66 px; otherwise two balanced lines (never split at a hyphen)."""
    tr = -0.03
    for size in range(104, 64, -2):
        if font.width(name, size, tr) <= LEFT_W:
            return size, [name]
    words = name.split(" ")
    if len(words) > 1:
        best = min((max(font.width(" ".join(words[:i]), 100, tr), font.width(" ".join(words[i:]), 100, tr)), i)
                   for i in range(1, len(words)))
        lines = [" ".join(words[:best[1]]), " ".join(words[best[1]:])]
        return min(80.0, math.floor(LEFT_W / best[0] * 100)), lines
    return math.floor(LEFT_W / font.width(name, 100, tr) * 100), [name]


def _rich_line(doc: Doc, font: Font, segs: list[tuple[str, bool]], x: float, base: float, size: float,
               color: str, shadow: str) -> None:
    """Text with one peach marker phrase: fill, 0.12 em pad and radius, 4 px hard shadow (DESIGN.md .mark)."""
    cx = x
    for s, marked in segs:
        w = font.width(s, size)
        if marked:  # like CSS inline padding, the pad takes up room on both sides
            pad = 0.12 * size
            top, bot = base - font.asc * size * 0.93, base + font.desc * size * 0.95
            doc.add(rrect(cx + 4, top + 4, w + 2 * pad, bot - top, pad, shadow),
                    rrect(cx, top, w + 2 * pad, bot - top, pad, ACCENT))
            cx += pad
        doc.text(font, s, cx, base, size, INK if marked else color)
        cx += w + (0.12 * size if marked else 0)


def plate(doc: Doc, t: dict) -> None:
    """T4: the peach-soft slab at -3 degrees, bleeding off the right edge."""
    doc.add(f'<g transform="rotate(-3 818 265)">{hard_shadow(818, 30, 640, 470, 40, t["rule"], 14)}'
            f'{rrect(818, 30, 640, 470, 40, ACCENT_SOFT, t["rule"])}</g>')


def tile(doc: Doc, glyph: str, cx: float, cy: float, t: dict) -> None:
    """The small code tile (hacienda's </>): navy, peach hard shadow, tilted."""
    f = faces()["mono"]
    size = 44 if len(glyph.strip()) == 1 else 32
    g = Doc(0, 0, "")
    g.defs = doc.defs
    g.text(f, glyph, 0, f.cap * size / 2, size, PAPER, anchor="middle")
    doc.add(f'<g transform="translate({cx:.1f} {cy:.1f}) rotate(-6)">'
            f'{hard_shadow(-46, -46, 92, 92, 16, ACCENT, 6)}{rrect(-46, -46, 92, 92, 16, INK, t["rule"])}'
            f'{"".join(g.parts)}</g>')


def banner(cfg: dict, theme: str, body: Callable | None = None) -> str:
    t = THEMES[theme]
    f = faces()
    alt = cfg.get("alt") or f'{cfg["name"]} by Dojo Coding: {cfg["tagline"].replace("[", "").replace("]", "")}'
    doc = Doc(W, H, alt)
    doc.add(f'<rect width="{W}" height="{H}" fill="{t["page"]}"/>')
    plate(doc, t)

    if cfg.get("layout") == "org":
        _org_left(doc, cfg, t, theme)
    else:
        _repo_left(doc, cfg, t)

    c = cfg.get("card", {})
    x, y, w, h = card(doc, c, t, body)
    doc.add(sparkle(x + w - 12, y - 6, 26))
    if cfg.get("tile"):
        tile(doc, cfg["tile"], x - 32, y + h - 4, t)
    elif cfg.get("layout") == "org":
        doc.add(f'<g transform="translate({x - 26} {y + h + 4}) rotate(-8)">'
                f'{hard_shadow(-35, -35, 70, 70, 16, t["rule"], 6)}{rrect(-35, -35, 70, 70, 16, ACCENT, INK)}</g>')
    return doc.svg()


def _repo_left(doc: Doc, cfg: dict, t: dict) -> None:
    f = faces()
    size, lines = _fit_name(cfg["name"], f["display"])
    segs = _split_marker(cfg["tagline"])
    plain = "".join(s for s, _ in segs)
    tag_size = 30.0
    while f["body"].width(plain, tag_size) > LEFT_W and tag_size > 22:
        tag_size -= 1
    lh = size * 0.98
    # stack: brand 42 | 26 | name caps | lines | tagline | 28 | chips 46
    name_first = 42 + 26 + f["display"].cap * size
    name_last = name_first + lh * (len(lines) - 1)
    tag_base = name_last + size * 0.22 + 18 + f["body"].cap * tag_size
    chips_top = tag_base + tag_size * 0.3 + 26
    total = chips_top + 46
    top = max(30.0, (H - total) / 2)

    doc.add(image(ASSETS / "dojocoding-mark.png", LEFT_X, top, 42, 42))
    doc.text(f["display"], "Dojo Coding", LEFT_X + 54, top + 21 + f["display"].cap * 27 / 2, 27, t["ink"],
             tracking=-0.01)
    for i, line in enumerate(lines):
        doc.text(f["display"], line, LEFT_X - size * 0.04, top + name_first + i * lh, size, t["ink"], tracking=-0.03)
    _rich_line(doc, f["body"], segs, LEFT_X, top + tag_base, tag_size, t["ink"], t["rule"])
    cx = LEFT_X
    for text in cfg.get("chips", []):
        cx += chip(doc, text, cx, top + chips_top, kind="peach", theme=t) + 14
    if cfg.get("meta"):
        chip(doc, cfg["meta"], cx, top + chips_top, kind="meta", theme=t)


def _org_left(doc: Doc, cfg: dict, t: dict, theme: str) -> None:
    f = faces()
    lockup = ASSETS / ("dojocoding-lockup-white.png" if theme == "dark" else "dojocoding-lockup.png")
    lines = cfg["headline"]
    size, lh = 66.0, 66 * 1.02
    top = 52.0
    doc.add(image(lockup, LEFT_X, top, 52 * 2027 / 739, 52))
    base = top + 52 + 30 + f["display"].cap * size
    for i, line in enumerate(lines):
        _rich_line(doc, f["display"], _split_marker(line), LEFT_X - 3, base + i * lh, size, t["ink"], t["rule"])
    lede_base = base + lh * (len(lines) - 1) + 30 + f["quiet"].cap * 26 + 6
    doc.text(f["quiet"], cfg["lede"], LEFT_X, lede_base, 26, t["ink_2"])
    # nameplate: navy index tab with a peach sparkle, then the offices in mono
    tab_y = lede_base + 30
    label = cfg["nameplate"]
    tw = f["label"].width(label, 19) + 54
    doc.add(rrect(LEFT_X + 3, tab_y + 3, tw, 40, 6, ACCENT), rrect(LEFT_X, tab_y, tw, 40, 6, t["ink"]))
    doc.add(sparkle(LEFT_X + 21, tab_y + 20, 8, ACCENT, ACCENT, 0))
    doc.text(f["label"], label, LEFT_X + 38, tab_y + 20 + f["label"].cap * 19 / 2, 19, t["page"])
    doc.text(f["mono"], cfg.get("offices", "").upper(), LEFT_X + tw + 18, tab_y + 20 + f["mono"].cap * 14 / 2, 14,
             t["ink_2"], tracking=0.06)


# -- CLI ------------------------------------------------------------------------------

def chrome() -> str | None:
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium",
              "chromium-browser"):
        if Path(c).exists() or shutil.which(c):
            return c
    return None


def preview(svg: Path, slug: str) -> Path | None:
    """Screenshot an SVG to a PNG in the temp dir (never next to the SVG, so it can't be committed)."""
    exe = chrome()
    if not exe:
        return None
    png = Path(tempfile.gettempdir()) / "dojo-banner" / f"{slug}-{svg.stem}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--screenshot={png}",
                    f"--window-size={W},{H}", "--force-device-scale-factor=1", svg.resolve().as_uri()],
                   check=True, capture_output=True)
    return png


def build(config: Path, previews: bool = False) -> list[Path]:
    cfg = tomllib.loads(config.read_text())
    out = (config.parent / cfg.get("out", ".")).resolve()
    written = []
    for theme in ("light", "dark"):
        path = out / f"banner-{theme}.svg"
        path.write_text(banner(cfg, theme))
        written.append(path)
        if previews:
            p = preview(path, re.sub(r"[^a-z0-9]+", "-", cfg["name"].lower()).strip("-"))
            if p:
                written.append(p)
    return written


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="dojo-banner", description=__doc__.split("\n\n")[0])
    ap.add_argument("config", nargs="+", type=Path, help="banner.toml file(s)")
    ap.add_argument("--preview", action="store_true", help="also write PNG previews (needs Chrome)")
    args = ap.parse_args(argv)
    for config in args.config:
        for path in build(config, args.preview):
            print(path)


if __name__ == "__main__":
    main(sys.argv[1:])
