"""The command line: exit codes, formats, the ``--json``-in-both-positions rule.

The CLI is the contract other tools and humans depend on, so it is tested
through ``main(argv)`` — the same entry point the console script uses — and
against a real on-disk tree, not the fake one.
"""
from __future__ import annotations

import json

import pytest

from pengyos_storage.cli import build_parser, main


def _run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# ---------------------------------------------------------------------------
# Basic CLI shape
# ---------------------------------------------------------------------------


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "pengyos-storage 0.1.0" in capsys.readouterr().out


def test_no_subcommand_prints_help_and_exits_2(capsys):
    code, out, _ = _run([], capsys)
    assert code == 2
    assert "usage" in out


def test_parser_advertises_the_three_subcommands():
    text = build_parser().format_help()
    for cmd in ("disks", "scan", "du"):
        assert cmd in text


def test_help_lists_examples():
    text = build_parser().format_help()
    assert "scan /data -f html -o storage.html" in text


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def test_scan_text_writes_a_report_to_stdout(real_tree, capsys):
    code, out, _ = _run(["scan", str(real_tree)], capsys)
    assert code == 0
    assert "scan of" in out and "files" in out and "█" in out


def test_scan_json_flag_after_the_subcommand(real_tree, capsys):
    code, out, _ = _run(["scan", str(real_tree), "--json"], capsys)
    assert code == 0
    doc = json.loads(out)
    assert doc["tool"] == "pengyos-storage"
    assert doc["schema"] == 1
    assert doc["root"] == str(real_tree)
    assert doc["tree"]["children"]


def test_scan_json_flag_before_the_subcommand_is_the_same_document(real_tree, capsys):
    _, after, _ = _run(["scan", str(real_tree), "--json"], capsys)
    _, before, _ = _run(["--json", "scan", str(real_tree)], capsys)
    a, b = json.loads(before), json.loads(after)
    # `generated` is the only field allowed to differ between two scans.
    assert "generated" in a and "generated" in b
    a.pop("generated"), b.pop("generated")
    assert a == b


def test_scan_json_document_closes_its_arithmetic(real_tree, capsys):
    _, out, _ = _run(["scan", str(real_tree), "--json"], capsys)
    doc = json.loads(out)
    tree = doc["tree"]
    assert tree["bytes"] == doc["total_bytes"]
    assert tree["bytes"] >= sum(c["bytes"] for c in tree["children"])


def test_scan_html_writes_a_self_contained_file(real_tree, capsys, tmp_path):
    dest = tmp_path / "report.html"
    code, out, _ = _run(["scan", str(real_tree), "-f", "html", "-o", str(dest)], capsys)
    assert code == 0
    page = dest.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>") and "<svg" in page
    assert "wrote" in out  # the tool says where it put the file


def test_scan_html_refuses_two_paths(real_tree, capsys):
    code, _, err = _run(["scan", str(real_tree), str(real_tree), "-f", "html"], capsys)
    assert code == 2
    assert "one PATH" in err


def test_scan_writes_json_to_a_file(real_tree, capsys, tmp_path):
    dest = tmp_path / "scan.json"
    code, _, _ = _run(["scan", str(real_tree), "-f", "json", "-o", str(dest)], capsys)
    assert code == 0
    assert json.loads(dest.read_text(encoding="utf-8"))["total_bytes"] > 0


def test_scan_two_paths_emits_a_scans_list(real_tree, capsys):
    code, out, _ = _run(["scan", str(real_tree), str(real_tree), "-f", "json"], capsys)
    assert code == 0
    doc = json.loads(out)
    assert len(doc["scans"]) == 2


def test_scan_exclusion_and_min_size_flags(real_tree, capsys):
    code, out, _ = _run(["scan", str(real_tree), "--exclude", "*.bin",
                         "--min-size", "1K", "-d", "2"], capsys)
    assert code == 0
    assert "excluded" in out


def test_scan_apparent_and_one_file_system_flags(real_tree, capsys):
    code, out, _ = _run(["scan", str(real_tree), "--apparent", "-x"], capsys)
    assert code == 0
    assert "apparent size" in out


def test_scan_missing_path_is_exit_2(capsys):
    code, _, err = _run(["scan", "/definitely/not/here"], capsys)
    assert code == 2
    assert "cannot read" in err


def test_scan_bad_min_size_is_exit_2(real_tree, capsys):
    code, _, err = _run(["scan", str(real_tree), "--min-size", "10 bananas"], capsys)
    assert code == 2
    assert "size" in err


def test_scan_is_deterministic(real_tree, capsys):
    _, a, _ = _run(["scan", str(real_tree), "-d", "2"], capsys)
    _, b, _ = _run(["scan", str(real_tree), "-d", "2"], capsys)
    assert a == b


# ---------------------------------------------------------------------------
# du
# ---------------------------------------------------------------------------


def test_du_sorted_biggest_first(real_tree, capsys):
    code, out, _ = _run(["du", str(real_tree), "-d", "2"], capsys)
    assert code == 0
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].split()[-1] == "."
    assert "big" in lines[1]


def test_du_all_lists_files(real_tree, capsys):
    code, out, _ = _run(["du", str(real_tree), "-d", "1", "-a"], capsys)
    assert code == 0
    assert "b.bin" in out


def test_du_no_bar(real_tree, capsys):
    _, out, _ = _run(["du", str(real_tree), "--no-bar"], capsys)
    assert "█" not in out


def test_du_json(real_tree, capsys):
    code, out, _ = _run(["du", str(real_tree), "--json"], capsys)
    assert code == 0
    assert json.loads(out)["tool"] == "pengyos-storage"


# ---------------------------------------------------------------------------
# disks
# ---------------------------------------------------------------------------


@pytest.fixture
def mounts_file(tmp_path):
    """A mount table whose mountpoints exist, so disk_usage succeeds and a
    skipped mount is visibly *absent* rather than incidentally un-stat-able."""
    (tmp_path / "nfs").mkdir()
    (tmp_path / "backup").mkdir()
    f = tmp_path / "mounts"
    f.write_text(
        f"/dev/nvme1n1p1 {tmp_path} ext4 rw,relatime 0 0\n"
        f"/dev/sda1 {tmp_path}/backup ext4 rw 0 0\n"
        f"server:/srv {tmp_path}/nfs nfs4 rw 0 0\n"
        "proc /proc proc rw 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n",
        encoding="utf-8",
    )
    return f


def test_disks_skips_pseudo_and_network_mounts(mounts_file, capsys):
    code, out, _ = _run(["disks", "--mounts", str(mounts_file)], capsys)
    assert code == 0
    assert "/dev/nvme1n1p1" in out
    assert "server:/srv" not in out  # nfs: skipped by default, and it *is* stat-able
    assert "proc" not in out and "tmpfs" not in out
    assert "filesystem" in out


def test_disks_can_include_network_mounts(mounts_file, capsys):
    code, out, _ = _run(["disks", "--mounts", str(mounts_file), "--network", "--json"], capsys)
    assert code == 0
    doc = json.loads(out)
    devices = [f["device"] for f in doc["filesystems"]]
    assert "server:/srv" in devices
    assert doc["filesystems"][0]["mountpoint"] == str(mounts_file.parent)


def test_disks_json_document(mounts_file, capsys):
    code, out, _ = _run(["disks", "--mounts", str(mounts_file), "--json"], capsys)
    assert code == 0
    doc = json.loads(out)
    assert doc["schema"] == 1
    assert doc["filesystems"][0]["device"] == "/dev/nvme1n1p1"
    assert 0.0 <= doc["filesystems"][0]["used_pct"] <= 100.0


def test_disks_missing_mount_table_is_exit_2(capsys):
    code, _, err = _run(["disks", "--mounts", "/definitely/not/here"], capsys)
    assert code == 2
    assert "cannot read" in err


def test_disks_html(mounts_file, tmp_path, capsys):
    dest = tmp_path / "disks.html"
    code, _, _ = _run(["disks", "--mounts", str(mounts_file), "-f", "html",
                       "-o", str(dest)], capsys)
    assert code == 0
    page = dest.read_text(encoding="utf-8")
    assert "<svg" not in page and "/dev/nvme1n1p1" in page and "<script" not in page
