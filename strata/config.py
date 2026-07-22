# config.py — shipped tunables. Scan roots are NOT here: they're machine data.
# Pass them on the command line, or list them in ./roots.txt (git-ignored) so
# this file stays editable without colliding with anyone's local paths.

import os

# Resolved against the working directory, not this module: inside a .pyz bundle
# there is no editable file next to the code.
ROOTS_FILE: str = "roots.txt"
DEFAULT_REPORT: str = "outputs/audit.html"


def load_roots(path: str = ROOTS_FILE) -> list[str]:
    """One directory path per line; blank lines and #-comments ignored."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        lines = (line.strip() for line in fh)
        return [line for line in lines if line and not line.startswith("#")]

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

# Files below this size are not checked for duplicates. Hashing is dominated by
# per-file opens rather than bytes, and tiny files collide on size constantly —
# on a 300k-file profile the sub-4KB ones were 64% of all candidates and ~140s of
# work, while together they could hide at most ~200 MB of waste. Set 0 to hash
# everything; raise it if you only care about duplicates that free real space.
DUP_MIN_BYTES: int = 4096

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

# Empty & Junk view. OS clutter matched by exact filename (case-insensitive).
JUNK_FILENAMES: set[str] = {"thumbs.db", "desktop.ini", ".ds_store"}

