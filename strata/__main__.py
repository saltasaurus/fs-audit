"""Strata entry point: scan the given directories and open the dashboard.

Runs identically from a checkout (`python strata`) and from a zipapp bundle
(`python strata.pyz`) — both put this directory's root on sys.path and execute
this file.

Read-only. Produces a single self-contained HTML file with the scan data
inlined, then opens it in the default browser.
"""

import os
import sys
import json
import logging
import argparse
import webbrowser

import scanner
from config import load_roots, ROOTS_FILE, DEFAULT_REPORT

_PLACEHOLDER = "/*__SCAN_DATA__*/null"


def _read_template() -> str:
    """Read template.html from beside this module.

    open() works from a checkout but fails inside a .pyz, where there is no real
    filesystem path. The module loader's get_data() handles both: checkouts go
    through SourceFileLoader, bundles through zipimport.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
    loader = globals().get("__loader__")
    if hasattr(loader, "get_data"):
        return loader.get_data(path).decode("utf-8")
    with open(path, encoding="utf-8") as fh:  # pragma: no cover - defensive
        return fh.read()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="strata",
        description="Audit disk usage read-only and open a visual report.",
        epilog=f"With no DIRECTORY given, roots are read from ./{ROOTS_FILE}.",
    )
    parser.add_argument("roots", metavar="DIRECTORY", nargs="*",
                        help="directory to scan (repeatable)")
    parser.add_argument("-o", "--output", default=DEFAULT_REPORT, metavar="PATH",
                        help=f"where to write the report (default: {DEFAULT_REPORT})")
    parser.add_argument("--no-open", action="store_true",
                        help="write the report but don't open a browser")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="name every file the scan could not read")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    # `strata "C:\Users\Me\"` — the trailing backslash escapes the closing quote,
    # so Windows hands us a path ending in a stray `"`. Tab-completion appends
    # that backslash, so this is the common case, not the exotic one. A quote is
    # never legal in a Windows path, so stripping it can't discard a real one.
    roots = [r.rstrip('"') for r in (args.roots or load_roots())]
    if not roots:
        sys.exit(
            "Error: no directories to scan.\n"
            "Pass them as arguments:\n"
            "    python strata C:/Users/YourName/Documents D:/Projects\n"
            f"or list one per line in ./{ROOTS_FILE} (copy roots.example.txt)."
        )

    missing = [r for r in roots if not os.path.isdir(r)]
    if missing:
        sys.exit("Error: not an existing directory:\n"
                 + "".join(f"    {r}\n" for r in missing))

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("scanning %d root(s)...", len(roots))
    data = scanner.scan(roots)

    html = _read_template().replace(_PLACEHOLDER, json.dumps(data), 1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html)

    ej = data["emptyJunk"]["counts"]
    logging.info(
        "found %d duplicate set(s), %d near-duplicate set(s), %d large file(s), "
        # ASCII only: the Windows console is cp1252 and turns an em dash into '?'.
        "%d empty folder(s), %d junk/zero-byte file(s) - report at %s",
        data["duplicates"]["setCount"], data["duplicates"]["near"]["setCount"],
        data["largeFiles"]["count"], ej["folders"], ej["zero"] + ej["junk"], args.output,
    )
    if not args.no_open:
        webbrowser.open(os.path.abspath(args.output))


if __name__ == "__main__":
    main()
