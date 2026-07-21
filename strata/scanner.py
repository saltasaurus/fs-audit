"""Single-pass, read-only filesystem scan feeding the Strata dashboard.

One walk of the given roots collects everything the GUI shows: category totals, a
drill-down folder tree, content-identical duplicate sets, large files, and the
old/unused list. Nothing is moved, deleted, renamed, or modified.
"""

import os
import sys
import time
import heapq
import hashlib
import logging
import shutil
from collections import defaultdict

import neardup
from config import (
    SKIP_PATHS, CATEGORY_EXTENSIONS, LARGE_FILE_BYTES, OLD_FILE_DAYS,
    NEAR_DUP_THRESHOLD, NEAR_DUP_MAX_BYTES, NEAR_DUP_EXTENSIONS, JUNK_FILENAMES,
)

logger = logging.getLogger(__name__)

# Payload bounds and rendering internals. These exist to stop a whole-filesystem
# scan from producing a report the browser can't open — they are not user knobs,
# which is why they live here rather than in config.py.
OLD_LIST_FLOOR_DAYS: int = 30   # Old & Unused slider floor; younger files never listed
OLD_LIST_MAX: int = 500         # largest N old entries inlined
AGE_TOLERANCE_DAYS: int = 1     # same-folder files this close in age collapse to one row
MAP_MIN_FRACTION: float = 0.001  # Storage Map: prune folders below this share of the scan
MAP_MAX_CHILDREN: int = 40      # Storage Map: tiles per level
FILE_LIST_MAX: int = 50         # largest N files listed per folder leaf
DUP_LIST_MAX: int = 500         # duplicate sets inlined (headline counts stay complete)
EMPTY_JUNK_MAX: int = 1000      # cap per Empty & Junk list

# Fixed by the Strata design.
CATEGORY_COLORS: dict[str, str] = {
    "Photos": "#5B9DFF", "Videos": "#C084FC", "Documents": "#FBBF24",
    "Apps": "#34D399", "Audio": "#F472B6", "Archives": "#FB923C",
    "Developer": "#22D3EE", "System": "#94A3B8", "Other": "#64748B",
}

# extension (no dot, lowercase) → category, inverted from config once at import.
_EXT_TO_CATEGORY: dict[str, str] = {
    ext: cat for cat, exts in CATEGORY_EXTENSIONS.items() for ext in exts
}


def _progress(message: str, *, final: bool = False) -> None:
    """Redraw the stderr status line. Padded so a shorter message fully
    overwrites a longer previous one; `final` ends the line instead of holding
    the carriage return."""
    print("\r  " + message.ljust(58), end="\n" if final else "",
          file=sys.stderr, flush=True)


def _should_skip(path: str) -> bool:
    return any(skip in path for skip in SKIP_PATHS)


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lstrip(".").lower()


def _category(filename: str) -> str:
    return _EXT_TO_CATEGORY.get(_ext(filename), "Other")


def _color(category: str) -> str:
    return CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])


def _sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
    except OSError as exc:
        logger.warning("hash failed for '%s': %s", path, exc)
        return None
    return h.hexdigest()


def _effective_roots(roots: list[str]) -> list[str]:
    """Drop roots nested inside another root, so overlapping paths aren't walked
    twice (which would double-count sizes and fabricate self-duplicates)."""
    seen: dict[str, str] = {}
    for r in roots:
        seen.setdefault(os.path.normcase(os.path.abspath(r)), r)  # also dedupes exact repeats
    paths = list(seen)
    kept = []
    for p in paths:
        if any(p != q and p.startswith(q + os.sep) for q in paths):
            logger.warning("skipping nested root %r (inside another scan root)", seen[p])
            continue
        kept.append(seen[p])
    return kept


def scan(roots: list[str]) -> dict:
    """Walk the given roots once and return the dashboard data dict."""
    now = time.time()
    old_cutoff = now - OLD_FILE_DAYS * 86400
    list_cutoff = now - OLD_LIST_FLOOR_DAYS * 86400

    category_bytes: dict[str, int] = defaultdict(int)
    size_to_paths: dict[int, list[str]] = defaultdict(list)  # for duplicate detection
    path_meta: dict[str, tuple[str, int, float]] = {}        # path → (category, size, last_touch)
    near_candidates: list[str] = []                          # text files for near-dup detection

    # Per-directory accumulators for the drill-down tree.
    dir_own_bytes: dict[str, int] = defaultdict(int)
    dir_cat_bytes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    child_dirs: dict[str, list[str]] = defaultdict(list)
    dir_files_top: dict[str, list] = defaultdict(list)           # bounded min-heap of (size, name)
    dir_files_more: dict[str, list] = defaultdict(lambda: [0, 0])  # [count, bytes] of the rest

    large_files: list[dict] = []
    zero_byte_files: list[dict] = []
    junk_files: list[dict] = []
    dir_file_count: dict[str, int] = defaultdict(int)  # non-skipped files directly in a dir
    dir_has_hidden: set[str] = set()                    # dirs holding skipped files/subdirs
    dir_old_files: dict[str, list[dict]] = defaultdict(list)
    old_count = 0
    old_bytes = 0
    scanned = 0
    roots = _effective_roots(roots)

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if _should_skip(dirpath):
                dirnames.clear()
                continue

            # Record (and prune) non-skipped subdirectories so the tree and the
            # walk agree on structure.
            kept = [d for d in dirnames if not _should_skip(os.path.join(dirpath, d))]
            # A dir holding skipped content (node_modules, .git, ...) only *looks*
            # empty to the audit — never flag it deletable.
            if len(kept) != len(dirnames) or any(
                _should_skip(os.path.join(dirpath, f)) for f in filenames):
                dir_has_hidden.add(dirpath)
            child_dirs[dirpath] = [os.path.join(dirpath, d) for d in kept]
            dirnames[:] = kept

            for filename in filenames:
                path = os.path.join(dirpath, filename)
                if _should_skip(path):
                    continue
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError as exc:
                    logger.warning("stat failed for '%s': %s", path, exc)
                    continue

                scanned += 1
                if scanned % 500 == 0:
                    _progress(f"scanned {scanned:,} files...")

                size = st.st_size
                category = _category(filename)
                # mtime, not atime: access time is unreliable (often disabled, or
                # bumped by scans/indexers), so it's a poor "untouched" signal.
                last_touch = st.st_mtime

                category_bytes[category] += size
                dir_own_bytes[dirpath] += size
                dir_cat_bytes[dirpath][category] += size
                dir_file_count[dirpath] += 1

                if filename.lower() in JUNK_FILENAMES:
                    junk_files.append({"name": filename, "path": path,
                                       "category": category, "color": _color(category), "bytes": size})
                elif size == 0:
                    zero_byte_files.append({"name": filename, "path": path,
                                            "category": category, "color": _color(category)})

                # Keep only the largest FILE_LIST_MAX files per folder; the rest
                # roll into a running "(+N more)" total so the payload stays bounded.
                heap = dir_files_top[dirpath]
                heapq.heappush(heap, (size, filename))
                if len(heap) > FILE_LIST_MAX:
                    dropped_size, _ = heapq.heappop(heap)
                    more = dir_files_more[dirpath]
                    more[0] += 1
                    more[1] += dropped_size

                if size > 0:
                    size_to_paths[size].append(path)
                    path_meta[path] = (category, size, last_touch)
                    if _ext(filename) in NEAR_DUP_EXTENSIONS and size <= NEAR_DUP_MAX_BYTES:
                        near_candidates.append(path)

                if size >= LARGE_FILE_BYTES:
                    large_files.append({
                        "name": filename, "path": path, "category": category,
                        "color": _color(category), "bytes": size, "lastOpened": last_touch,
                    })

                if last_touch < old_cutoff:
                    old_count += 1
                    old_bytes += size
                if last_touch < list_cutoff:
                    dir_old_files[dirpath].append({
                        "name": filename, "path": path, "category": category,
                        "color": _color(category), "bytes": size, "mtime": last_touch,
                    })

    _progress(f"scanned {scanned:,} files.", final=True)
    duplicates, path_hash = _find_duplicates(size_to_paths, path_meta)
    _progress(f"duplicate scan complete - {len(duplicates):,} sets found.", final=True)
    near_dupes = neardup.find_near_duplicates(
        near_candidates, path_meta, NEAR_DUP_THRESHOLD, NEAR_DUP_MAX_BYTES, path_hash)
    _progress(f"near-duplicate scan complete - {len(near_dupes):,} sets found.", final=True)
    large_files.sort(key=lambda f: f["bytes"], reverse=True)

    scanned_total = sum(category_bytes.values())
    tree = _build_tree(dir_own_bytes, dir_cat_bytes, child_dirs,
                       dir_files_top, dir_files_more, scanned_total, roots)

    old_entries = _collapse_old(dir_old_files)
    old_capped = len(old_entries) > OLD_LIST_MAX

    empty_folders = _find_empty_folders(child_dirs, dir_file_count, dir_has_hidden, roots)
    zero_byte_files.sort(key=lambda f: f["path"])
    junk_files.sort(key=lambda f: f["bytes"], reverse=True)

    categories = [
        {"name": cat, "bytes": b, "color": _color(cat)}
        for cat, b in sorted(category_bytes.items(), key=lambda kv: kv[1], reverse=True)
        if b > 0
    ]

    try:
        # Roots are validated before scan() is called, so the first one is real.
        # disk_usage can still fail on odd mounts, hence the guard.
        disk = shutil.disk_usage(roots[0])
        disk_info = {"total": disk.total, "used": disk.used, "free": disk.free}
    except (OSError, ValueError):
        disk_info = None

    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "roots": roots,
        "disk": disk_info,
        "scannedTotal": scanned_total,
        "categories": categories,
        "tree": tree,
        "duplicates": {
            "sets": duplicates[:DUP_LIST_MAX],
            "setCount": len(duplicates),                 # true total (headline stays honest)
            "shown": min(len(duplicates), DUP_LIST_MAX),
            "capped": len(duplicates) > DUP_LIST_MAX,
            "totalWasted": sum(d["wasted"] for d in duplicates),  # sums every set, not just shown
            "near": {
                "sets": near_dupes[:DUP_LIST_MAX],
                "setCount": len(near_dupes),
                "shown": min(len(near_dupes), DUP_LIST_MAX),
                "capped": len(near_dupes) > DUP_LIST_MAX,
                "threshold": NEAR_DUP_THRESHOLD,
            },
        },
        "largeFiles": {
            "files": large_files,
            "count": len(large_files),
            "totalBytes": sum(f["bytes"] for f in large_files),
        },
        "oldFiles": {"files": old_entries[:OLD_LIST_MAX], "capped": old_capped},
        "oldUnused": {"count": old_count, "totalBytes": old_bytes},
        "emptyJunk": {
            "emptyFolders": [{"name": os.path.basename(p.rstrip("/\\")) or p, "path": p}
                             for p in empty_folders[:EMPTY_JUNK_MAX]],
            "zeroByteFiles": zero_byte_files[:EMPTY_JUNK_MAX],
            "junkFiles": junk_files[:EMPTY_JUNK_MAX],
            "counts": {
                "folders": len(empty_folders),
                "zero": len(zero_byte_files),
                "junk": len(junk_files),
            },
            "junkBytes": sum(f["bytes"] for f in junk_files),
        },
    }


def _find_duplicates(
    size_to_paths: dict[int, list[str]],
    path_meta: dict[str, tuple[str, int, float]],
) -> tuple[list[dict], dict[str, str]]:
    """Group files with identical content. Only same-size files are hashed.

    Returns (duplicate sets, path→sha256 for every hashed file). The hash map lets
    near-dup detection drop byte-identical pairs already covered here.
    """
    hash_to_paths: dict[str, list[str]] = defaultdict(list)
    path_hash: dict[str, str] = {}
    total = sum(len(p) for p in size_to_paths.values() if len(p) >= 2)
    done = 0

    for size, paths in size_to_paths.items():
        if len(paths) < 2:
            continue  # a unique size cannot be a duplicate — skip the hashing
        for path in paths:
            digest = _sha256(path)
            done += 1
            if done % 20 == 0:
                _progress(f"hashing possible duplicates: {done:,}/{total:,}...")
            if digest is not None:
                hash_to_paths[digest].append(path)
                path_hash[path] = digest

    sets: list[dict] = []
    for paths in hash_to_paths.values():
        if len(paths) < 2:
            continue
        # Keeper = shortest path (most canonical location); rest are redundant.
        paths.sort(key=len)
        category, size, _ = path_meta[paths[0]]
        sets.append({
            "name": os.path.basename(paths[0]),
            "category": category,
            "color": _color(category),
            "copies": [{"path": p, "bytes": path_meta[p][1]} for p in paths],
            "wasted": size * (len(paths) - 1),
        })

    sets.sort(key=lambda d: d["wasted"], reverse=True)
    return sets, path_hash


def _collapse_old(dir_old_files: dict[str, list[dict]]) -> list[dict]:
    """Collapse old files that share a folder and age into one folder row.

    Within each folder, files whose mtimes fall in the same AGE_TOLERANCE window
    become a single 'folder' entry; files with a unique age stay as 'file' rows.
    This is what stops a bulk-written dataset folder from flooding the list.
    """
    tol = AGE_TOLERANCE_DAYS * 86400
    entries: list[dict] = []

    for dirpath, files in dir_old_files.items():
        buckets: dict[int, list[dict]] = defaultdict(list)
        for f in files:
            buckets[int(f["mtime"] // tol)].append(f)

        for group in buckets.values():
            if len(group) == 1:
                f = group[0]
                entries.append({
                    "type": "file", "name": f["name"], "path": f["path"],
                    "category": f["category"], "color": f["color"],
                    "bytes": f["bytes"], "lastOpened": f["mtime"],
                })
                continue
            cat_bytes: dict[str, int] = defaultdict(int)
            for f in group:
                cat_bytes[f["category"]] += f["bytes"]
            dominant = max(cat_bytes.items(), key=lambda kv: kv[1])[0]
            entries.append({
                "type": "folder",
                "name": os.path.basename(dirpath.rstrip("/\\")) or dirpath,
                "path": dirpath, "category": dominant, "color": _color(dominant),
                "bytes": sum(f["bytes"] for f in group),
                "lastOpened": max(f["mtime"] for f in group),  # newest touch in the group
                "count": len(group),
            })

    entries.sort(key=lambda e: e["bytes"], reverse=True)
    return entries


def _find_empty_folders(
    child_dirs: dict[str, list[str]],
    dir_file_count: dict[str, int],
    dir_has_hidden: set[str],
    roots: list[str],
) -> list[str]:
    """Return the outermost empty folders (no files anywhere in their subtree).

    Only the top of an empty chain is returned: if a/b/c are all empty, just 'a'
    is listed, so deleting one entry clears the whole subtree. Folders holding
    skipped content (node_modules, .git, ...) are never empty — deleting them
    would destroy real data the audit didn't traverse.
    """
    empties: list[str] = []

    def walk(dirpath: str) -> bool:
        kids = child_dirs.get(dirpath, [])
        kids_empty = [walk(k) for k in kids]  # recurse fully so nested empties surface
        self_empty = (dir_file_count.get(dirpath, 0) == 0
                      and dirpath not in dir_has_hidden
                      and all(kids_empty))
        if not self_empty:
            empties.extend(k for k, ke in zip(kids, kids_empty) if ke)
        return self_empty

    for root in roots:
        if walk(root):
            empties.append(root)  # the entire root is empty
    return empties


def _build_tree(
    dir_own_bytes: dict[str, int],
    dir_cat_bytes: dict[str, dict[str, int]],
    child_dirs: dict[str, list[str]],
    dir_files_top: dict[str, list],
    dir_files_more: dict[str, list],
    scanned_total: int,
    roots: list[str],
) -> dict | None:
    """Assemble the drill-down folder tree from the per-directory accumulators.

    Folders below MAP_MIN_FRACTION of the scan are pruned and each level keeps at
    most MAP_MAX_CHILDREN tiles. Leaf folders (and a mixed folder's own loose
    files) become 'file-leaf' nodes carrying a list of their largest files, so
    drilling the map always bottoms out in the actual files. Returns None if
    nothing was scanned.
    """
    if scanned_total <= 0:
        return None

    min_bytes = scanned_total * MAP_MIN_FRACTION

    def file_leaf(name: str, total_bytes: int, dirpath: str) -> dict:
        """Terminal node listing a folder's largest loose files."""
        heap = dir_files_top.get(dirpath, [])
        files = [{"name": n, "bytes": s} for s, n in sorted(heap, reverse=True)]
        cats = dir_cat_bytes.get(dirpath, {})
        dom = max(cats.items(), key=lambda kv: kv[1])[0] if cats else "Other"
        node: dict = {"name": name, "bytes": total_bytes, "category": dom,
                      "color": _color(dom), "files": files}
        more = dir_files_more.get(dirpath)
        if more and more[0]:
            node["filesMore"] = {"count": more[0], "bytes": more[1]}
        return node

    def build(dirpath: str) -> tuple[dict, dict[str, int]]:
        cat_total: dict[str, int] = defaultdict(int, dir_cat_bytes.get(dirpath, {}))
        own = dir_own_bytes.get(dirpath, 0)
        has_subdirs = bool(child_dirs.get(dirpath))

        children: list[dict] = []
        for sub in child_dirs.get(dirpath, []):
            child_node, child_cat = build(sub)
            for name, value in child_cat.items():
                cat_total[name] += value
            children.append(child_node)

        total = own + sum(c["bytes"] for c in children)
        children = [c for c in children if c["bytes"] >= min_bytes]
        children.sort(key=lambda c: c["bytes"], reverse=True)
        children = children[:MAP_MAX_CHILDREN]

        dominant = max(cat_total.items(), key=lambda kv: kv[1])[0] if cat_total else "Other"
        name = os.path.basename(dirpath.rstrip("/\\")) or dirpath

        if children:
            # mixed folder: its own loose files become a clickable leaf tile
            if own >= min_bytes:
                children.append(file_leaf("(loose files)", own, dirpath))
                children.sort(key=lambda c: c["bytes"], reverse=True)
            node: dict = {"name": name, "bytes": total, "category": dominant,
                          "color": _color(dominant), "children": children}
        elif not has_subdirs or own > 0:
            # a real leaf, or a folder whose mass is its own files → list them
            node = file_leaf(name, total, dirpath)
        else:
            # size is entirely in tiny pruned subfolders: plain, non-drillable tile
            node = {"name": name, "bytes": total,
                    "category": dominant, "color": _color(dominant)}
        return node, cat_total

    root_nodes: list[dict] = []
    agg_cat: dict[str, int] = defaultdict(int)
    for root in roots:
        node, cat = build(root)
        if node["bytes"] <= 0:
            continue
        root_nodes.append(node)
        for name, value in cat.items():
            agg_cat[name] += value

    if not root_nodes:
        return None
    if len(root_nodes) == 1:
        return root_nodes[0]

    root_nodes.sort(key=lambda n: n["bytes"], reverse=True)
    dominant = max(agg_cat.items(), key=lambda kv: kv[1])[0] if agg_cat else "Other"
    return {
        "name": "All roots",
        "bytes": sum(n["bytes"] for n in root_nodes),
        "category": dominant,
        "color": _color(dominant),
        "children": root_nodes,
    }
