"""Tests for scanner.py. All paths are temporary (pytest tmp_path)."""

import time

import pytest

import config
import scanner


@pytest.fixture(autouse=True)
def _isolate_skip_paths(monkeypatch):
    # pytest's tmp_path lives under AppData, which the real SKIP_PATHS excludes;
    # clear it so tests aren't skipped. Tests that need skips set their own.
    monkeypatch.setattr(scanner, "SKIP_PATHS", [])


def _configure(monkeypatch, root, **overrides):
    """Point the scanner at a single temp root with default thresholds."""
    monkeypatch.setattr(config, "SCAN_ROOTS", [str(root)])
    monkeypatch.setattr(scanner, "SCAN_ROOTS", [str(root)])
    for name, value in overrides.items():
        monkeypatch.setattr(scanner, name, value)


class TestDuplicates:
    def test_identical_content_grouped_regardless_of_name(self, tmp_path, monkeypatch):
        """Two files with identical bytes but different names are one duplicate set."""
        _configure(monkeypatch, tmp_path)
        (tmp_path / "IMG_1234.jpg").write_bytes(b"same pixels")
        (tmp_path / "photo.jpg").write_bytes(b"same pixels")

        result = scanner.scan()

        assert result["duplicates"]["setCount"] == 1
        assert len(result["duplicates"]["sets"][0]["copies"]) == 2

    def test_same_size_different_content_is_not_a_duplicate(self, tmp_path, monkeypatch):
        """Files that collide on size but differ in bytes are not grouped."""
        _configure(monkeypatch, tmp_path)
        (tmp_path / "a.bin").write_bytes(b"AAAA")
        (tmp_path / "b.bin").write_bytes(b"BBBB")

        result = scanner.scan()

        assert result["duplicates"]["setCount"] == 0

    def test_wasted_bytes_counts_removable_copies(self, tmp_path, monkeypatch):
        """Wasted space = size × (copies − 1); one copy is the keeper."""
        _configure(monkeypatch, tmp_path)
        for name in ("one.dat", "two.dat", "three.dat"):
            (tmp_path / name).write_bytes(b"x" * 100)

        result = scanner.scan()

        assert result["duplicates"]["sets"][0]["wasted"] == 200


class TestDuplicateCap:
    def test_list_capped_but_headline_totals_stay_true(self, tmp_path, monkeypatch):
        """Only DUP_LIST_MAX sets are inlined, but setCount/totalWasted count all."""
        _configure(monkeypatch, tmp_path, DUP_LIST_MAX=1)
        # two independent duplicate sets of different sizes
        for name, size in (("a", 100), ("b", 300)):
            (tmp_path / f"{name}1.dat").write_bytes(b"x" * size)
            (tmp_path / f"{name}2.dat").write_bytes(b"x" * size)

        dup = scanner.scan()["duplicates"]

        assert dup["setCount"] == 2          # true total
        assert dup["shown"] == 1             # only one inlined
        assert dup["capped"] is True
        assert len(dup["sets"]) == 1
        assert dup["sets"][0]["wasted"] == 300   # biggest-wasted kept
        assert dup["totalWasted"] == 400         # sums both sets, not just shown


class TestNearDuplicates:
    def _draft(self, i, revised=False):
        lines = [f"line {n}: notes about section {n} of the report" for n in range(200)]
        if revised:
            lines[7] = "line 7: REVISED notes about section 7 of the report"
        return "\n".join(lines)

    def test_edited_drafts_group_as_near_but_not_exact(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "report_v1.md").write_text(self._draft(1))
        (tmp_path / "report_v2.md").write_text(self._draft(1, revised=True))

        result = scanner.scan()

        assert result["duplicates"]["setCount"] == 0            # not byte-identical
        assert result["duplicates"]["near"]["setCount"] == 1
        members = result["duplicates"]["near"]["sets"][0]["members"]
        assert len(members) == 2
        assert result["duplicates"]["near"]["sets"][0]["similarity"] >= 0.8

    def test_unrelated_text_files_do_not_group(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "a.md").write_text("\n".join(f"alpha {i} apples oranges" for i in range(200)))
        (tmp_path / "b.md").write_text("\n".join(f"zeta {i} database migration" for i in range(200)))

        result = scanner.scan()

        assert result["duplicates"]["near"]["setCount"] == 0

    def test_byte_identical_files_stay_exact_only(self, tmp_path, monkeypatch):
        """Identical copies are exact duplicates, never also reported as near."""
        _configure(monkeypatch, tmp_path)
        text = self._draft(1)
        (tmp_path / "one.md").write_text(text)
        (tmp_path / "two.md").write_text(text)

        result = scanner.scan()

        assert result["duplicates"]["setCount"] == 1
        assert result["duplicates"]["near"]["setCount"] == 0

    def test_binary_extensions_are_ignored(self, tmp_path, monkeypatch):
        """Non-text extensions are never near-compared even if content is similar."""
        _configure(monkeypatch, tmp_path)
        (tmp_path / "a.png").write_text(self._draft(1))
        (tmp_path / "b.png").write_text(self._draft(1, revised=True))

        result = scanner.scan()

        assert result["duplicates"]["near"]["setCount"] == 0


class TestEmptyJunk:
    def test_outermost_empty_folder_only(self, tmp_path, monkeypatch):
        """An empty chain a/b/c reports just 'a', not every level."""
        _configure(monkeypatch, tmp_path)
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "keep.txt").write_bytes(b"x")  # keeps the root non-empty

        ej = scanner.scan()["emptyJunk"]
        paths = [f["path"] for f in ej["emptyFolders"]]

        assert ej["counts"]["folders"] == 1
        assert paths[0].endswith("a")

    def test_folder_with_a_file_is_not_empty(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "has").mkdir()
        (tmp_path / "has" / "f.txt").write_bytes(b"x")

        ej = scanner.scan()["emptyJunk"]

        assert ej["counts"]["folders"] == 0

    def test_folder_with_only_skipped_content_not_flagged_empty(self, tmp_path, monkeypatch):
        """A folder whose only child is skipped (node_modules) must not be 'empty'."""
        _configure(monkeypatch, tmp_path)
        monkeypatch.setattr(scanner, "SKIP_PATHS", ["node_modules"])
        (tmp_path / "proj" / "node_modules").mkdir(parents=True)
        (tmp_path / "proj" / "node_modules" / "dep.js").write_bytes(b"x" * 10)
        (tmp_path / "keep.txt").write_bytes(b"x")

        ej = scanner.scan()["emptyJunk"]
        paths = [f["path"] for f in ej["emptyFolders"]]

        assert not any(p.endswith("proj") for p in paths)

    def test_zero_byte_and_junk_files_classified(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "empty.dat").write_bytes(b"")
        (tmp_path / "Thumbs.db").write_bytes(b"junk")   # junk-by-name
        (tmp_path / "real.txt").write_bytes(b"x")

        ej = scanner.scan()["emptyJunk"]

        assert ej["counts"]["zero"] == 1
        assert ej["zeroByteFiles"][0]["name"] == "empty.dat"
        assert ej["counts"]["junk"] == 1
        assert ej["junkFiles"][0]["name"] == "Thumbs.db"

    def test_zero_byte_junk_counted_as_junk_only(self, tmp_path, monkeypatch):
        """A 0-byte junk file lands in junk, not double-counted in zero-byte."""
        _configure(monkeypatch, tmp_path)
        (tmp_path / "desktop.ini").write_bytes(b"")

        ej = scanner.scan()["emptyJunk"]

        assert ej["counts"]["junk"] == 1
        assert ej["counts"]["zero"] == 0


class TestLargeFiles:
    def test_flags_files_at_or_above_threshold(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path, LARGE_FILE_BYTES=1000)
        (tmp_path / "big.iso").write_bytes(b"x" * 1500)
        (tmp_path / "small.txt").write_bytes(b"x" * 10)

        result = scanner.scan()

        assert result["largeFiles"]["count"] == 1
        assert result["largeFiles"]["files"][0]["name"] == "big.iso"


class TestCategories:
    def test_extension_maps_to_category_and_color(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "clip.mp4").write_bytes(b"x" * 50)

        result = scanner.scan()
        cats = {c["name"]: c for c in result["categories"]}

        assert "Videos" in cats
        assert cats["Videos"]["color"] == scanner.CATEGORY_COLORS["Videos"]

    def test_unknown_extension_is_other(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "mystery.qzx").write_bytes(b"x" * 50)

        result = scanner.scan()

        assert any(c["name"] == "Other" for c in result["categories"])


class TestOldUnused:
    def test_counts_files_untouched_past_threshold(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path, OLD_FILE_DAYS=30)
        f = tmp_path / "ancient.log"
        f.write_bytes(b"x" * 20)
        old = time.time() - 60 * 86400
        import os
        os.utime(f, (old, old))

        result = scanner.scan()

        assert result["oldUnused"]["count"] == 1


def _child(node, name):
    return next(c for c in node.get("children", []) if c["name"] == name)


class TestTree:
    def test_single_root_rolls_up_nested_sizes(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "sub" / "deep").mkdir(parents=True)
        (tmp_path / "sub" / "a.txt").write_bytes(b"x" * 100)
        (tmp_path / "sub" / "deep" / "b.txt").write_bytes(b"x" * 50)

        tree = scanner.scan()["tree"]

        assert tree["name"] == tmp_path.name  # single root, not "All roots"
        assert tree["bytes"] == 150
        sub = _child(tree, "sub")
        assert sub["bytes"] == 150
        assert _child(sub, "deep")["bytes"] == 50

    def test_folder_dominant_category_colours_the_tile(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        (tmp_path / "media").mkdir()
        (tmp_path / "media" / "clip.mp4").write_bytes(b"x" * 900)
        (tmp_path / "media" / "note.txt").write_bytes(b"x" * 10)

        tree = scanner.scan()["tree"]
        media = _child(tree, "media")

        assert media["category"] == "Videos"
        assert media["color"] == scanner.CATEGORY_COLORS["Videos"]

    def test_tiny_folders_pruned_by_fraction(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path, MAP_MIN_FRACTION=0.1)
        (tmp_path / "big").mkdir()
        (tmp_path / "big" / "b.bin").write_bytes(b"x" * 10000)
        (tmp_path / "tiny").mkdir()
        (tmp_path / "tiny" / "t.bin").write_bytes(b"x" * 5)  # < 10% of total

        tree = scanner.scan()["tree"]
        names = [c["name"] for c in tree.get("children", [])]

        assert "big" in names
        assert "tiny" not in names

    def test_loose_files_shown_as_file_leaf(self, tmp_path, monkeypatch):
        """A mixed folder's loose files appear as a clickable '(loose files)' leaf."""
        _configure(monkeypatch, tmp_path, MAP_MIN_FRACTION=0.0)
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "s.bin").write_bytes(b"x" * 5000)
        (tmp_path / "loose.bin").write_bytes(b"x" * 5000)  # loose file at the root

        tree = scanner.scan()["tree"]
        names = [c["name"] for c in tree["children"]]

        assert "subdir" in names
        loose = _child(tree, "(loose files)")
        assert loose["bytes"] == 5000
        assert [f["name"] for f in loose["files"]] == ["loose.bin"]

    def test_leaf_folder_lists_its_files(self, tmp_path, monkeypatch):
        """A leaf folder becomes a file-leaf node listing its files, biggest first."""
        _configure(monkeypatch, tmp_path, MAP_MIN_FRACTION=0.0)
        (tmp_path / "big.bin").write_bytes(b"x" * 9000)
        (tmp_path / "small.bin").write_bytes(b"x" * 1000)

        tree = scanner.scan()["tree"]  # single leaf root → the tree is itself a file-leaf

        assert "files" in tree
        assert [f["name"] for f in tree["files"]] == ["big.bin", "small.bin"]

    def test_file_list_caps_and_summarises_rest(self, tmp_path, monkeypatch):
        """Only the largest FILE_LIST_MAX files are listed; the rest roll into +N more."""
        _configure(monkeypatch, tmp_path, MAP_MIN_FRACTION=0.0, FILE_LIST_MAX=2)
        for i in range(4):
            (tmp_path / f"f{i}.bin").write_bytes(b"x" * (100 + i))  # distinct sizes

        tree = scanner.scan()["tree"]

        assert len(tree["files"]) == 2
        assert tree["filesMore"]["count"] == 2

    def test_multiple_roots_get_synthetic_root(self, tmp_path, monkeypatch):
        root_a = tmp_path / "a"; root_b = tmp_path / "b"
        root_a.mkdir(); root_b.mkdir()
        (root_a / "f.txt").write_bytes(b"x" * 100)
        (root_b / "g.txt").write_bytes(b"x" * 100)
        monkeypatch.setattr(scanner, "SCAN_ROOTS", [str(root_a), str(root_b)])

        tree = scanner.scan()["tree"]

        assert tree["name"] == "All roots"
        assert len(tree["children"]) == 2

    def test_empty_scan_yields_null_tree(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        assert scanner.scan()["tree"] is None


class TestOldFilesList:
    def test_list_holds_files_past_floor_and_excludes_recent(self, tmp_path, monkeypatch):
        import os
        _configure(monkeypatch, tmp_path, OLD_LIST_FLOOR_DAYS=30)
        old = tmp_path / "stale.txt"; old.write_bytes(b"x" * 40)
        recent = tmp_path / "fresh.txt"; recent.write_bytes(b"x" * 40)
        long_ago = time.time() - 100 * 86400
        os.utime(old, (long_ago, long_ago))

        files = scanner.scan()["oldFiles"]["files"]
        names = [f["name"] for f in files]

        assert "stale.txt" in names
        assert "fresh.txt" not in names
        assert files[0]["lastOpened"] < time.time()

    def test_capped_flag_set_when_list_exceeds_max(self, tmp_path, monkeypatch):
        import os
        _configure(monkeypatch, tmp_path, OLD_LIST_FLOOR_DAYS=30, OLD_LIST_MAX=1)
        long_ago = time.time() - 100 * 86400
        # separate folders so they stay two singleton entries (no collapse)
        for name in ("a", "b"):
            d = tmp_path / name; d.mkdir()
            f = d / "f.txt"; f.write_bytes(b"x" * 40)
            os.utime(f, (long_ago, long_ago))

        old = scanner.scan()["oldFiles"]
        assert old["capped"] is True
        assert len(old["files"]) == 1


class TestOldCollapse:
    def test_same_age_folder_collapses_to_one_entry(self, tmp_path, monkeypatch):
        import os
        _configure(monkeypatch, tmp_path, OLD_LIST_FLOOR_DAYS=30, AGE_TOLERANCE_DAYS=1)
        long_ago = time.time() - 100 * 86400
        for i in range(4):
            f = tmp_path / f"img_{i}.png"; f.write_bytes(b"x" * 40)
            os.utime(f, (long_ago, long_ago))

        files = scanner.scan()["oldFiles"]["files"]

        assert len(files) == 1
        assert files[0]["type"] == "folder"
        assert files[0]["count"] == 4
        assert files[0]["bytes"] == 160

    def test_different_ages_split_into_separate_rows(self, tmp_path, monkeypatch):
        import os
        _configure(monkeypatch, tmp_path, OLD_LIST_FLOOR_DAYS=30, AGE_TOLERANCE_DAYS=1)
        now = time.time()
        a = tmp_path / "old.png"; a.write_bytes(b"x" * 40)
        b = tmp_path / "older.png"; b.write_bytes(b"x" * 40)
        os.utime(a, (now - 100 * 86400, now - 100 * 86400))
        os.utime(b, (now - 400 * 86400, now - 400 * 86400))  # different age bucket

        files = scanner.scan()["oldFiles"]["files"]

        assert len(files) == 2
        assert all(f["type"] == "file" for f in files)


class TestNestedRoots:
    def test_nested_root_not_double_counted(self, tmp_path, monkeypatch):
        """A root inside another root is dropped: sizes count once, no self-dupes."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "only.bin").write_bytes(b"x" * 100)
        # parent and its own child both listed as roots
        monkeypatch.setattr(scanner, "SCAN_ROOTS", [str(tmp_path), str(tmp_path / "sub")])

        result = scanner.scan()

        assert result["scannedTotal"] == 100          # counted once, not 200
        assert result["duplicates"]["setCount"] == 0   # the file is not a duplicate of itself
        assert len(result["roots"]) == 1               # nested root pruned


class TestSkipPaths:
    def test_skip_substring_prunes_directory(self, tmp_path, monkeypatch):
        _configure(monkeypatch, tmp_path)
        monkeypatch.setattr(scanner, "SKIP_PATHS", ["node_modules"])
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.js").write_bytes(b"x" * 10)
        (tmp_path / "app.js").write_bytes(b"x" * 10)

        result = scanner.scan()
        total = result["scannedTotal"]

        assert total == 10  # only app.js counted
