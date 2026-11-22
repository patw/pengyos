"""board/desktop/check-mime-icons.py: COSMIC Files must not draw a common file type blank.

On 2026-11-22 every PIE binary and .so in the Desktop (application/x-sharedlib) drew a
blank tile: Files tries the type's icon, then its generic icon, and the image had no
application-x-generic in any theme. The check replays that lookup over a target.
"""

from __future__ import annotations

import configparser
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_mime_icons", REPO / "buildroot-external/board/desktop/check-mime-icons.py")
cmi = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cmi
spec.loader.exec_module(cmi)

SVG = '<svg xmlns="http://www.w3.org/2000/svg"/>'


def _target(tmp_path: Path, types: list[str], *, generic: dict[str, str] | None = None,
            icons: dict[str, str] | None = None) -> Path:
    t = tmp_path / "target"
    mime = t / "usr/share/mime"
    mime.mkdir(parents=True)
    (mime / "types").write_text("".join(f"{m}\n" for m in types))
    (mime / "generic-icons").write_text("".join(f"{k}:{v}\n" for k, v in (generic or {}).items()))
    (mime / "icons").write_text("".join(f"{k}:{v}\n" for k, v in (icons or {}).items()))
    return t


def _theme(t: Path, name: str, inherits: str | None, icons: dict[str, list[str]]) -> Path:
    d = t / "usr/share/icons" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.theme").write_text(
        "[Icon Theme]\nName=%s\n%s" % (name, f"Inherits={inherits}\n" if inherits else ""))
    for sub, names in icons.items():
        (d / sub).mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / sub / n).write_text(SVG)
    return d


def test_lookup_order_is_xdg_mimes():
    names = cmi.icon_names("application/x-sharedlib", {}, {"application/x-sharedlib": "application-x-generic"})
    assert names == ["application-x-sharedlib", "application-x-generic"]
    assert cmi.icon_names("model/stl", {}, {}) == ["model-stl", "model-x-generic"]
    assert cmi.icon_names("a/b", {"a/b": "own"}, {})[0] == "own"


def test_the_2026_11_22_image_fails_on_binaries(tmp_path, capsys):
    t = _target(tmp_path, ["application/x-sharedlib", "text/plain"],
                generic={"application/x-sharedlib": "application-x-generic",
                         "text/plain": "text-x-generic"})
    _theme(t, "Cosmic", "Pop,hicolor", {"scalable/mimetypes": ["text-x-generic.svg"]})
    _theme(t, "Pop", "hicolor", {"scalable/mimetypes": ["text-x-generic-symbolic.svg"]})
    _theme(t, "hicolor", None, {})
    assert cmi.main(["x", str(t)]) == 1
    err = capsys.readouterr().err
    assert "application/x-sharedlib" in err and "text/plain" not in err


def test_an_inherited_generic_icon_is_enough(tmp_path, capsys):
    t = _target(tmp_path, ["application/x-sharedlib"],
                generic={"application/x-sharedlib": "application-x-generic"})
    _theme(t, "Cosmic", "Pop,hicolor", {})
    _theme(t, "Pop", "hicolor", {"64x64/mimetypes": ["application-x-generic.svg"]})
    _theme(t, "hicolor", None, {})
    assert cmi.main(["x", str(t)]) == 0
    assert "every common file type has an icon" in capsys.readouterr().out


def test_png_only_and_dangling_links_do_not_count(tmp_path):
    """Files looks up SVG only (prefer_svg -> force_svg); a dead link draws nothing."""
    t = _target(tmp_path, ["application/x-sharedlib"],
                generic={"application/x-sharedlib": "application-x-generic"})
    d = _theme(t, "Cosmic", "hicolor", {})
    (d / "48x48/mimetypes").mkdir(parents=True)
    (d / "48x48/mimetypes/application-x-generic.png").write_bytes(b"\x89PNG")
    (d / "48x48/mimetypes/application-x-sharedlib.svg").symlink_to("gone.svg")
    _theme(t, "hicolor", None, {})
    assert cmi.main(["x", str(t)]) == 1


def test_a_type_missing_from_the_database_is_not_required(tmp_path):
    t = _target(tmp_path, ["text/plain"], generic={"text/plain": "text-x-generic"})
    _theme(t, "Cosmic", None, {"scalable/mimetypes": ["text-x-generic.svg"]})
    assert cmi.main(["x", str(t)]) == 0


def test_no_mime_database_is_an_error(tmp_path):
    assert cmi.main(["x", str(tmp_path)]) == 1


def test_shipped_pop_index_gives_every_directory_a_section():
    """The theme lookup only searches listed directories that have a section."""
    p = REPO / "buildroot-external/package/pop-cursor-theme/Pop-index.theme"
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read_string(p.read_text())
    dirs = cp["Icon Theme"]["Directories"].split(",")
    assert [d for d in dirs if d not in cp] == []
    sized = [d for d in dirs if d.endswith("/mimetypes") and not d.startswith("scalable")]
    assert len(sized) == 7, "Pop's sized file-type icons (application-x-generic) are listed"


def test_post_build_runs_the_check():
    s = (REPO / "buildroot-external/board/desktop/post-build.sh").read_text()
    assert 'check-mime-icons.py" "${TARGET_DIR}"' in s
