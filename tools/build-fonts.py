#!/usr/bin/env python3
"""Generate app/fonts.css — the three type families, subset and inlined.

A published MCP App renders inside a sandboxed iframe under a deny-by-default
CSP. Remote fonts would need `_meta.ui.csp.resourceDomains` to name a CDN, hosts
may still refuse it, and every render would phone home. So the app ships its own
type: subset to the characters it actually draws, encoded as base64 woff2, pasted
straight into a `@font-face` block.

This is a **build tool**, not runtime. It is the only thing in the repo that
touches the network or a third-party package (`fonttools`), and its output —
`app/fonts.css` — is committed, so building the app never needs either.

    python3 tools/build-fonts.py

Families are chosen in design/spire-ai/ui/DESIGN_SYSTEM.md: one display, one
body, one mono. All three are SIL Open Font License 1.1, which permits
embedding; the licence notice rides along in the generated file.
"""

from __future__ import annotations

import base64
import io
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "fonts.css"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

QUERY = (
    "https://fonts.googleapis.com/css2"
    "?family=Fraunces:opsz,wght@9..144,600;9..144,700"
    "&family=Newsreader:opsz,wght@6..72,400;6..72,500"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&display=swap"
)

# Everything the interface can draw. Symbols are deliberately absent: the client
# renders entity silhouettes as CSS shapes, not glyphs, because ENTITY_STANDARDS
# wants the shape to carry the meaning even with no colour and no font.
GLYPHS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    "·—–…×→←↑↓“”‘’"
    "ÀÁÂÄÈÉÊËÍÎÏÑÓÔÖÚÜàáâäèéêëíîïñóôöúüç"
)

LICENCE = """/* Fraunces, Newsreader and IBM Plex Mono — SIL Open Font License 1.1.
   Subset to the characters this interface draws and inlined as base64 woff2 so
   the app renders with zero network access under a deny-by-default CSP.
   Regenerate with: python3 tools/build-fonts.py
   Do not hand-edit. */
"""


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read()


def parse_faces(css: str) -> list[dict]:
    """Pull every @font-face block out of the Google Fonts stylesheet."""
    def field(block: str, name: str) -> str:
        found = re.search(rf"{name}:\s*([^;]+);", block)
        return found.group(1).strip() if found else ""

    faces = []
    for block in re.findall(r"@font-face\s*\{(.*?)\}", css, re.S):
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if not url:
            continue
        faces.append({
            "family": field(block, "font-family").strip("'\""),
            "weight": field(block, "font-weight"),
            "style": field(block, "font-style") or "normal",
            "range": field(block, "unicode-range"),
            "url": url.group(1),
        })
    return faces


def covers_basic_latin(unicode_range: str) -> bool:
    """Keep only the subset that carries A-Z, so we download one file per face."""
    for part in unicode_range.split(","):
        part = part.strip().upper().removeprefix("U+")
        if "-" in part:
            low, high = part.split("-", 1)
            try:
                if int(low, 16) <= 0x41 <= int(high, 16):
                    return True
            except ValueError:
                continue
        elif part == "41":
            return True
    return False


def subset(raw: bytes) -> bytes:
    from fontTools import subset as ft_subset  # imported late: build-time only
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(raw))
    options = ft_subset.Options()
    options.flavor = "woff2"
    options.desubroutinize = True
    options.layout_features = ["kern", "liga", "calt"]
    options.drop_tables += ["DSIG"]
    options.notdef_outline = True
    subsetter = ft_subset.Subsetter(options=options)
    subsetter.populate(text=GLYPHS)
    subsetter.subset(font)

    buffer = io.BytesIO()
    font.flavor = "woff2"
    font.save(buffer)
    return buffer.getvalue()


def main() -> int:
    print(f"fetching {QUERY.split('?')[0]} …", file=sys.stderr)
    css = fetch(QUERY).decode("utf-8")

    # Fraunces and Newsreader are variable fonts: Google serves the *same* file
    # for every weight in the range. Embedding it once per weight doubled the
    # bundle for nothing, so faces are grouped by source URL and emitted as a
    # single @font-face with a weight range.
    groups: dict[str, dict] = {}
    for face in parse_faces(css):
        if not covers_basic_latin(face["range"]):
            continue
        group = groups.setdefault(face["url"], {
            "family": face["family"], "style": face["style"], "weights": set(),
        })
        group["weights"].add(int(face["weight"]))

    blocks = [LICENCE]
    total_before = total_after = 0

    for url, group in groups.items():
        raw = fetch(url)
        trimmed = subset(raw)
        total_before += len(raw)
        total_after += len(trimmed)

        weights = sorted(group["weights"])
        weight_rule = (
            str(weights[0]) if len(weights) == 1 else f"{weights[0]} {weights[-1]}"
        )
        print(
            f"  {group['family']} {weight_rule}: "
            f"{len(raw) // 1024}K -> {len(trimmed) // 1024}K",
            file=sys.stderr,
        )

        encoded = base64.b64encode(trimmed).decode("ascii")
        blocks.append(
            "@font-face {\n"
            f"  font-family: '{group['family']}';\n"
            f"  font-style: {group['style']};\n"
            f"  font-weight: {weight_rule};\n"
            "  font-display: block;\n"
            f"  src: url(data:font/woff2;base64,{encoded}) format('woff2');\n"
            "}\n"
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(blocks), encoding="utf-8")
    print(
        f"wrote {OUT.relative_to(ROOT)} — {len(groups)} faces, "
        f"{total_before // 1024}K -> {total_after // 1024}K "
        f"({OUT.stat().st_size // 1024}K base64)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
