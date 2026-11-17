"""Build self-hosted UI fonts from licensed Inter/Noto Sans SC/JetBrains Mono.

Install .[font-build] and supply a directory with the three woff2 files and OFLs.
Includes GB2312 Chinese plus every current project UI character. No network calls.
"""

import argparse
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
import hashlib
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / "src/nwpu_forwarder/static/fonts"
    destination.mkdir(parents=True, exist_ok=True)
    chars = set(chr(i) for i in range(32, 127))
    for lead in range(0xA1, 0xF8):
        for trail in range(0xA1, 0xFF):
            try:
                chars.update(bytes((lead, trail)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    for path in (root / "src/nwpu_forwarder").rglob("*"):
        if path.suffix in {".html", ".js", ".py"}:
            chars.update(path.read_text(encoding="utf-8"))
    for family in ("Inter", "NotoSansSC", "JetBrainsMono"):
        font = TTFont(args.source_dir / (family + ".woff2"))
        if family == "NotoSansSC":
            available = font.getBestCmap()
            options = subset.Options()
            options.flavor = "woff2"
            options.recalc_timestamp = False
            options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
            sub = subset.Subsetter(options=options)
            sub.populate(
                unicodes={ord(char) for char in chars if ord(char) in available}
            )
            sub.subset(font)
        font.flavor = "woff2"
        output = destination / (family + ".woff2")
        font.save(output)
        license_text = (args.source_dir / (family + "-OFL.txt")).read_text(
            encoding="utf-8"
        )
        (destination / (family + "-OFL.txt")).write_text(
            "\n".join(line.rstrip() for line in license_text.splitlines()) + "\n",
            encoding="utf-8",
        )
        print(
            f"{family}: {len(font.getBestCmap())} characters, {output.stat().st_size} bytes"
        )
    css_path = destination.parent / "fonts.css"
    css = css_path.read_text(encoding="utf-8")
    for family in ("Inter", "NotoSansSC", "JetBrainsMono"):
        version = hashlib.sha256((destination / (family + ".woff2")).read_bytes()).hexdigest()[:16]
        css = re.sub(r"fonts/" + family + r".woff2(?:?v=[a-f0-9]+)?", "fonts/" + family + ".woff2?v=" + version, css)
    css_path.write_text(css, encoding="utf-8")


if __name__ == "__main__":
    main()
