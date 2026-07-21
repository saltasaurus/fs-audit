# fs-audit — Strata Storage Auditor

A read-only filesystem auditor with a visual dashboard. Scans the directory
roots you configure, then opens a single self-contained HTML report — the
**Strata** dashboard — showing where your space goes, which files are exact
content duplicates, and which are large. It never moves, deletes, renames, or
modifies any file.

Pure Python standard library. **No third-party dependencies**, no build step,
no server.

## Run

```bash
python main.py
```

That's it. `main.py` scans the configured roots, writes
`outputs/audit.html` with the scan data inlined, and opens it in your browser.
Re-run any time to refresh.

## Configure

Copy `roots.example.txt` to `roots.txt` and list the directories to scan, one
per line:

```
C:/Users/YourName/Documents/
D:/Projects/
/mnt/c/Users/YourName/
```

Blank lines and `#` comments are ignored. Use forward slashes on all platforms.
`roots.txt` is git-ignored — your machine's paths stay out of the repo, and
`config.py` can be edited or updated without ever touching them.

Optional knobs in `config.py`: `SKIP_PATHS` (substrings never traversed),
`LARGE_FILE_BYTES` (default 1 GB), `OLD_FILE_DAYS` (default 365), and
`CATEGORY_EXTENSIONS` (extension → category mapping).

## Dashboard

All five views are wired to live scan data:

- **Overview** — disk-usage donut, a treemap of your biggest top-level folders,
  and headline duplicate / large-file / old-file numbers.
- **Storage Map** — a drill-down treemap of the whole folder tree. Click a
  folder tile to descend, use the breadcrumb to climb back. Tiles are sized by
  subtree bytes and coloured by dominant category.
- **Duplicates** — files with byte-identical content grouped into sets
  (matched by content hash, not filename). Flag copies for review and export a
  plain-text list. **Audit-only — nothing is ever deleted.**
- **Large Files** — every file at or above the threshold, sortable and
  searchable.
- **Old & Unused** — files not modified in a while, with a slider (30–730 days)
  and quick presets to change the age threshold live.

"Last touched" age is based on file **modification time** — access time is
unreliable (often disabled, or bumped by background scans and indexers).

## How duplicate detection works

Files are grouped by size first; only files that share a size are hashed
(SHA-256, streamed in chunks). A unique size can't be a duplicate, so the vast
majority of files are never read a second time. Within each set the copy in the
shortest path is marked the recommended keeper.

## Test

```bash
pytest
```

## Layout

```
fs-audit/
├── roots.txt            ← edit this (git-ignored; copy roots.example.txt)
├── config.py            ← shipped tunables: thresholds, categories, skips
├── scanner.py           ← single-pass read-only scan → data dict
├── main.py              ← scan, inline into template, open in browser
├── gui/template.html    ← the Strata dashboard (self-contained)
├── tests/test_scanner.py
└── outputs/             ← audit.html written here (git-ignored)
```

## Constraints

- The only file ever written is `outputs/audit.html`.
- Symlinks are never followed.
- Paths containing a `SKIP_PATHS` substring are never traversed.

## License

MIT — see [LICENSE](LICENSE).
