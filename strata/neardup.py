"""Near-duplicate text detection: MinHash + banded LSH, zero dependencies.

Finds text files whose content is similar-but-not-identical — edited copies,
drafts, versioned notes. Character-shingle MinHash signatures give each file a
fixed-length fingerprint; banded LSH buckets likely-similar files so we compare
only real candidates instead of every pair (O(n) instead of O(n^2)). Read-only:
returns groups, moves nothing.

Exact byte-for-byte copies are handled by scanner._find_duplicates; edges
between byte-identical files are dropped here so a file only ever appears in one
place.
"""

import os
import hashlib
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

_SHINGLE = 9          # character n-gram width for shingling
_PERMS = 64           # MinHash signature length
_ROWS = 4             # LSH rows per band → _PERMS // _ROWS = 16 bands
_MERSENNE = (1 << 61) - 1   # prime modulus for universal hashing
_MAX64 = (1 << 64) - 1

# ponytail: bottom-K shingle sample caps per-file work so a 5 MB file costs the
# same as a 50 KB one. Sampling the K smallest base-hashes is a uniform sample of
# the shingle set, so Jaccard survives. Raise if recall on big files matters.
_SHINGLE_CAP = 512
# ponytail: skip LSH buckets bigger than this — near-empty/boilerplate files can
# collide en masse and blow up all-pairs. These are low-signal anyway.
_BUCKET_CAP = 100


def _coeffs(n: int) -> tuple[list[int], list[int]]:
    """Deterministic (a, b) pairs for n universal hash functions h(x)=a*x+b."""
    a, b = [], []
    for i in range(n):
        d = hashlib.blake2b(f"minhash-perm-{i}".encode(), digest_size=16).digest()
        a.append(int.from_bytes(d[:8], "little") % (_MERSENNE - 1) + 1)  # nonzero
        b.append(int.from_bytes(d[8:], "little") % _MERSENNE)
    return a, b


_A, _B = _coeffs(_PERMS)


def _shingle_hashes(text: str) -> list[int]:
    """Bottom-K sample of distinct char-shingle base hashes for `text`."""
    norm = " ".join(text.lower().split())  # collapse whitespace, case-fold
    if len(norm) < _SHINGLE:
        return []
    seen: set[int] = set()
    for i in range(len(norm) - _SHINGLE + 1):
        sh = norm[i:i + _SHINGLE].encode()
        seen.add(int.from_bytes(hashlib.blake2b(sh, digest_size=8).digest(), "little"))
    return sorted(seen)[:_SHINGLE_CAP]


def _signature(bases: list[int]) -> list[int]:
    """MinHash signature: for each permutation, the min affine-hashed base."""
    return [min((_A[i] * h + _B[i]) % _MERSENNE for h in bases) for i in range(_PERMS)]


def _estimated_jaccard(s1: list[int], s2: list[int]) -> float:
    return sum(x == y for x, y in zip(s1, s2)) / _PERMS


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, x: str, y: str) -> None:
        self.parent[self.find(x)] = self.find(y)


def _read_text(path: str, max_bytes: int) -> str | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError as exc:
        logger.warning("near-dup read failed for '%s': %s", path, exc)
        return None
    if len(raw) > max_bytes:
        return None  # over the cap → skip (don't truncate; partial text is misleading)
    return raw.decode("utf-8", errors="ignore")


def find_near_duplicates(
    paths: list[str],
    path_meta: dict[str, tuple[str, int, float]],
    threshold: float,
    max_bytes: int,
    path_hash: dict[str, str] | None = None,
) -> list[dict]:
    """Group near-identical text files.

    `paths` are the candidate text files, `path_meta` maps path → (category,
    size, mtime), `path_hash` maps path → sha256 for files that were exact-hashed
    (used to drop byte-identical edges). Returns groups sorted by similarity, each
    {name, category, color, similarity, members:[{path, bytes, mtime}]}.
    """
    path_hash = path_hash or {}

    signatures: dict[str, list[int]] = {}
    for path in paths:
        text = _read_text(path, max_bytes)
        if text is None:
            continue
        bases = _shingle_hashes(text)
        if bases:
            signatures[path] = _signature(bases)

    # Banded LSH: files sharing a full band land in the same bucket → candidates.
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for path, sig in signatures.items():
        for band in range(0, _PERMS, _ROWS):
            buckets[(band, tuple(sig[band:band + _ROWS]))].append(path)

    candidate_pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > _BUCKET_CAP:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                candidate_pairs.add((members[i], members[j]) if members[i] < members[j]
                                    else (members[j], members[i]))

    uf = _UnionFind()
    edge_sims: dict[tuple[str, str], float] = {}
    for a, b in candidate_pairs:
        ha, hb = path_hash.get(a), path_hash.get(b)
        if ha is not None and ha == hb:
            continue  # byte-identical → belongs to the exact-duplicates view
        sim = _estimated_jaccard(signatures[a], signatures[b])
        if sim >= threshold:
            uf.union(a, b)
            edge_sims[(a, b)] = sim

    # Assemble connected components (size ≥ 2) into groups.
    groups: dict[str, list[str]] = defaultdict(list)
    for path in signatures:
        if path in uf.parent:
            groups[uf.find(path)].append(path)

    from scanner import _color  # local import avoids an import cycle at module load

    sets: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        member_set = set(members)
        sims = [s for (a, b), s in edge_sims.items() if a in member_set and b in member_set]
        similarity = sum(sims) / len(sims) if sims else threshold
        members.sort(key=lambda p: path_meta[p][2], reverse=True)  # newest draft first
        category = path_meta[members[0]][0]
        sets.append({
            "name": os.path.basename(members[0]),
            "category": category,
            "color": _color(category),
            "similarity": round(similarity, 3),
            "members": [
                {"path": p, "bytes": path_meta[p][1], "mtime": path_meta[p][2]}
                for p in members
            ],
        })

    sets.sort(key=lambda s: (s["similarity"], len(s["members"])), reverse=True)
    return sets


def demo() -> None:
    """Self-check: two edited drafts group; an unrelated file stays out."""
    base = "\n".join(f"line {i}: notes about section {i} of the report" for i in range(200))
    edited = base.replace("line 7:", "line 7: REVISED").replace("section 42", "section 99")
    other = "\n".join(f"row {i}: unrelated database migration step {i}" for i in range(200))

    sigs = {name: _signature(_shingle_hashes(t))
            for name, t in {"a": base, "b": edited, "c": other}.items()}
    ab = _estimated_jaccard(sigs["a"], sigs["b"])
    ac = _estimated_jaccard(sigs["a"], sigs["c"])
    assert ab > 0.8, f"edited drafts should be near-identical, got {ab:.2f}"
    assert ac < 0.2, f"unrelated files should not match, got {ac:.2f}"
    print(f"ok: edited={ab:.2f} unrelated={ac:.2f}")


if __name__ == "__main__":
    demo()
