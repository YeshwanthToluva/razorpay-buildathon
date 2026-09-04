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

def main() -> None:
    data = DATA.read_text()
    # The payload sits in a JSON script block; only "</script>" could break out.
    html = TEMPLATE.read_text().replace("__CONSOLE_DATA__", data.replace("</", "<\\/"))
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size/1024:,.0f} KB)")
    print(f"open: file://{OUT}")

if __name__ == "__main__":
    main()
