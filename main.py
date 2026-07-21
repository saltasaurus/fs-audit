"""Entry point: scan the configured roots and open the Strata dashboard.

Read-only. Produces a single self-contained HTML file at REPORT_PATH with the
scan data inlined, then opens it in the default browser.
"""

import os
import sys
import json
import logging
import webbrowser

from config import SCAN_ROOTS, REPORT_PATH

_TEMPLATE = os.path.join(os.path.dirname(__file__), "gui", "template.html")
_PLACEHOLDER = "/*__SCAN_DATA__*/null"


def main() -> None:
    if not SCAN_ROOTS:
        sys.exit(
            "Error: no scan roots configured.\n"
            "Copy roots.example.txt to roots.txt, then list one directory per line, e.g.\n"
            "    C:/Users/YourName/Documents/"
        )

    missing = [r for r in SCAN_ROOTS if not os.path.isdir(r)]
    if missing:
        sys.exit(
            "Error: these roots.txt entries are not existing directories:\n"
            + "".join(f"    {r}\n" for r in missing)
            + "Edit roots.txt to point at real paths."
        )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from scanner import scan  # imported here so config errors surface first

    logging.info("scanning %d root(s)...", len(SCAN_ROOTS))
    data = scan()

    with open(_TEMPLATE, encoding="utf-8") as fh:
        template = fh.read()

    html = template.replace(_PLACEHOLDER, json.dumps(data), 1)

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)

    ej = data["emptyJunk"]["counts"]
    logging.info(
        "found %d duplicate set(s), %d near-duplicate set(s), %d large file(s), "
        "%d empty folder(s), %d junk/zero-byte file(s) — report at %s",
        data["duplicates"]["setCount"], data["duplicates"]["near"]["setCount"],
        data["largeFiles"]["count"], ej["folders"], ej["zero"] + ej["junk"], REPORT_PATH,
    )
    webbrowser.open(os.path.abspath(REPORT_PATH))


if __name__ == "__main__":
    main()
