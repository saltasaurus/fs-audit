# config.py — shipped tunables. The scan roots are NOT here: they're machine
# data, so they live in roots.txt (git-ignored) and this file stays editable
# without colliding with anyone's local paths. Copy roots.example.txt to start.

import os

_ROOTS_FILE = os.path.join(os.path.dirname(__file__), "roots.txt")


def _load_roots() -> list[str]:
    """One directory path per line; blank lines and #-comments ignored."""
    if not os.path.exists(_ROOTS_FILE):
        return []
    with open(_ROOTS_FILE, encoding="utf-8") as fh:
        lines = (line.strip() for line in fh)
        return [line for line in lines if line and not line.startswith("#")]


SCAN_ROOTS: list[str] = _load_roots()

# Any path containing one of these substrings is never traversed.
SKIP_PATHS: list[str] = [
    "C:/Windows", "/mnt/c/Windows", "/mnt/c/Program Files",
    "/proc", "/sys", "/dev", "/run", "/snap",
    "node_modules", ".git", "__pycache__", ".venv", "site-packages", ".godot",
    "AppData",  # huge and noisy (app caches, browser profiles) — skip for a profile-wide scan
]

# Files at or above this size land in the "Large Files" view.
LARGE_FILE_BYTES: int = 1024 ** 3          # 1 GB

# Files untouched for at least this long count as "old & unused" (Overview stat).
OLD_FILE_DAYS: int = 365

# The Old & Unused view's slider floor: files younger than this never enter its
# list. Only the OLD_LIST_MAX largest are kept, to bound the inlined payload.
OLD_LIST_FLOOR_DAYS: int = 30
OLD_LIST_MAX: int = 500

# Old & Unused: files in one folder whose modification times fall within this
# window are treated as "the same age" and collapse into a single folder row.
AGE_TOLERANCE_DAYS: int = 1

# Storage Map: folders below this fraction of the total scan are pruned as noise,
# and each level shows at most MAP_MAX_CHILDREN tiles.
MAP_MIN_FRACTION: float = 0.001
MAP_MAX_CHILDREN: int = 40

# Storage Map file leaves: largest files listed per folder (rest summarised as
# "+N more"). Bounds the inlined payload on a whole-filesystem scan.
FILE_LIST_MAX: int = 50

# Extension → category. Anything unlisted falls into "Other".
# Category colours are fixed by the Strata design (see scanner.CATEGORY_COLORS).
CATEGORY_EXTENSIONS: dict[str, list[str]] = {
    "Photos":    ["jpg", "jpeg", "png", "gif", "heic", "webp", "bmp", "tiff", "raw", "svg"],
    "Videos":    ["mp4", "mov", "avi", "mkv", "webm", "m4v", "wmv", "flv"],
    "Audio":     ["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma"],
    "Documents": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md", "csv", "rtf", "odt"],
    "Archives":  ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "dmg"],
    "Apps":      ["exe", "msi", "app", "deb", "rpm", "appimage"],
    "Developer": ["py", "js", "ts", "tsx", "jsx", "c", "cpp", "h", "hpp", "java", "go",
                  "rs", "rb", "gd", "json", "yaml", "yml", "toml", "sh", "html", "css", "sql"],
    "System":    ["dll", "sys", "so", "dylib", "ini", "cfg", "log"],
}

# Near-duplicates: text files whose content is similar but not identical (drafts,
# edited copies). Files scoring at/above this Jaccard estimate group together.
# 0.8 = "basically the same"; lower toward 0.6 for looser "related drafts".
NEAR_DUP_THRESHOLD: float = 0.8

# Only these extensions are compared for near-duplicates (plain-text formats we
# can shingle directly — binary docs like .docx/.pdf would need extraction).
# Files above NEAR_DUP_MAX_BYTES are skipped (near-dup on huge text is rare/slow).
NEAR_DUP_MAX_BYTES: int = 5 * 1024 ** 2   # 5 MB
NEAR_DUP_EXTENSIONS: set[str] = {
    "txt", "md", "csv", "rtf", "tex", "org", "rst", "log",
    "py", "js", "ts", "tsx", "jsx", "c", "cpp", "h", "hpp", "java", "go",
    "rs", "rb", "gd", "json", "yaml", "yml", "toml", "sh", "html", "css", "sql",
}

# Duplicates: only the top N sets (by wasted space) are inlined into the report
# and rendered — a real scan can find tens of thousands of sets, which would
# freeze the page. Headline counts/totals still reflect every set found.
DUP_LIST_MAX: int = 500

# Empty & Junk view. OS clutter matched by exact filename (case-insensitive).
JUNK_FILENAMES: set[str] = {"thumbs.db", "desktop.ini", ".ds_store"}
# Each Empty & Junk list is capped to keep the inlined report payload bounded.
EMPTY_JUNK_MAX: int = 1000

REPORT_PATH: str = "outputs/audit.html"
