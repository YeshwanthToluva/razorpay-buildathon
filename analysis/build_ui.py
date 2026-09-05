"""Generate the local evaluation console. READ-ONLY with respect to the experiment.

Injects analysis/console_data.json into ui/template.html so ui/index.html opens
straight from the filesystem with no server and no network calls for data.

    PYTHONPATH=src python analysis/build_ui.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "analysis" / "console_data.json"
TEMPLATE = ROOT / "ui" / "template.html"
OUT = ROOT / "ui" / "index.html"

PAGES = [
    (ROOT / "ui" / "template.html", ROOT / "ui" / "index.html"),
    (ROOT / "ui" / "rulebook_template.html", ROOT / "ui" / "rulebook.html"),
]


def main() -> None:
    data = DATA.read_text().replace("</", "<\\/")
    shared = (ROOT / "ui" / "_shared.css").read_text()
    for template, out in PAGES:
        if not template.exists():
            continue
        html = template.read_text()
        html = html.replace("__SHARED_CSS__", shared).replace("__CONSOLE_DATA__", data)
        out.write_text(html)
        print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size/1024:,.0f} KB)")

if __name__ == "__main__":
    main()
