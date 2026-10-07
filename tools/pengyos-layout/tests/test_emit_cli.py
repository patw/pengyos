"""End-to-end tests for the ``emit`` CLI subcommand.

``emit`` reads a plan JSON and prints the ordered executor argv — one shell
command per line (``shlex.join``) by default, or a structured
``{"steps": [...]}`` object under ``--json``. Exit ``0`` on success, ``2`` on a
usage/IO error (missing file, malformed JSON).

No real device, network, root or hardware is touched — plans are in-memory data
or JSON written to a temp file.
"""

import json

import pytest

from pengyos_layout.cli import main


def _write_plan(tmp_path, plan_dict, name="plan.json"):
    path = tmp_path / name
    path.write_text(json.dumps(plan_dict))
    return str(path)


# --- valid ------------------------------------------------------------------
def test_cli_emit_valid_returns_0(golden_plan, tmp_path, capsys):
    """``emit`` on a valid plan prints the executor argv and exits 0."""
    path = _write_plan(tmp_path, golden_plan.to_dict())
    rc = main(["emit", "--plan", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sgdisk -n 1:0B:268435456B" in out
    assert "mkfs.vfat /dev/sda1" in out
    assert "mount /dev/sda1 /boot/efi" in out


def test_cli_emit_json(golden_plan, tmp_path, capsys):
    """``--json`` emits a structured {steps: [...]} object."""
    path = _write_plan(tmp_path, golden_plan.to_dict())
    rc = main(["emit", "--plan", path, "--json"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert "steps" in result
    assert result["steps"][0]["kind"] == "sgdisk"
    assert result["steps"][0]["argv"][0] == "sgdisk"
    # every step's argv round-trips through shlex
    for step in result["steps"]:
        import shlex

        assert shlex.join(step["argv"])  # non-empty, quotable


def test_cli_emit_plaintext_lines(golden_plan, tmp_path, capsys):
    """A plaintext plan emits sgdisk + mkfs + mount lines (no cryptsetup)."""
    path = _write_plan(tmp_path, golden_plan.to_dict())
    rc = main(["emit", "--plan", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cryptsetup" not in out
    assert "mkfs.ext4 /dev/sda2" in out
    assert "mkfs.btrfs /dev/sda4" in out


def test_cli_emit_luks_lines(probe, tmp_path, capsys):
    """A LUKS plan emits cryptsetup luksFormat/open + mkfs on the mapper."""
    from pengyos_layout import Options
    from pengyos_layout.plan import plan

    plan = plan(probe, Options(luks=True, swap_size=0, edition="server"))
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["emit", "--plan", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cryptsetup luksFormat --batch-mode /dev/sda2" in out
    assert "cryptsetup open --type luks --name rootA_crypt" in out
    assert "mkfs.ext4 /dev/mapper/rootA_crypt" in out
    # ESP stays plaintext
    esp_lines = [ln for ln in out.splitlines() if "/dev/sda1" in ln]
    assert not any("cryptsetup" in ln for ln in esp_lines)


def test_cli_emit_swap_lines(golden_plan, tmp_path, capsys):
    """A swap plan emits a mkswap line and no mount for swap."""
    import copy

    from pengyos_layout import Fs, Partition, Role

    plan = copy.deepcopy(golden_plan)
    plan.partitions.append(
        Partition(
            role=Role.SWAP,
            size=512 << 20,
            start=1 << 30,
            fs=Fs.SWAP,
        )
    )
    path = _write_plan(tmp_path, plan.to_dict())
    rc = main(["emit", "--plan", path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mkswap /dev/sda5" in out
    swap_mount = [ln for ln in out.splitlines() if "/dev/sda5" in ln and "mount" in ln]
    assert swap_mount == []


def test_cli_emit_offsets_match_plan(golden_plan, tmp_path, capsys):
    """The emitted sgdisk byte offsets match the plan's start/size."""
    path = _write_plan(tmp_path, golden_plan.to_dict())
    main(["emit", "--plan", path])
    out = capsys.readouterr().out
    # esp: start 0, size 256 MiB
    assert "sgdisk -n 1:0B:268435456B" in out
    # rootA: start 256 MiB, size 8 GiB -> end 256MiB + 8GiB
    expected_end = (256 << 20) + (8 << 30)
    assert f"sgdisk -n 2:268435456B:{expected_end}B" in out


# --- usage / IO errors ------------------------------------------------------
def test_cli_emit_missing_file_returns_2(capsys):
    """A missing plan file is a usage/IO error (exit 2)."""
    rc = main(["emit", "--plan", "/no/such/plan.json"])
    assert rc == 2
    assert "not found" in capsys.readouterr().err.lower()


def test_cli_emit_malformed_json_returns_2(tmp_path, capsys):
    """A malformed plan file is a usage/IO error (exit 2)."""
    path = tmp_path / "broken.json"
    path.write_text("{ not valid json")
    rc = main(["emit", "--plan", str(path)])
    assert rc == 2
    assert "cannot parse" in capsys.readouterr().err.lower()


def test_cli_emit_missing_plan_arg_exits_2(capsys):
    """A required --plan argument missing is a usage error (exit 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["emit"])
    assert excinfo.value.code == 2
