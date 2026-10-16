"""Text output for pengyos-storage: the terminal report and the sorted ``du``.

The point of both is ranking you can read without thinking: biggest first, a
bar that shows the share, and the numbers closing (total = listed + hidden +
excluded).
"""
from __future__ import annotations

from .model import Filesystem, ScanResult, bar, human, percent

_SEP = " "


def _path_label(node, root: str) -> str:
    """Show the scan root as ``.``-relative paths: stable and diff-friendly."""
    return node.relpath if node.relpath != "." else "."


def level_nodes(result: ScanResult, depth: int) -> list:
    """Every node at exactly ``depth`` levels below the root, biggest first."""
    level = [result.root_node]
    for _ in range(depth):
        nxt = []
        for node in level:
            nxt.extend(node.children)
        if not nxt:
            return []
        level = nxt
    return sorted(level, key=lambda n: n.sort_key())


def render_text(
    result: ScanResult,
    *,
    depth: int = 3,
    top: int = 15,
    min_size: int = 0,
    bar_width: int = 22,
    tool_line: str = "pengyos-storage",
) -> str:
    """The human report for one scan."""
    lines: list[str] = []
    root = result.root_node
    mode = "disk usage" if result.mode == "blocks" else "apparent size"

    lines.append(f"{tool_line} — scan of {result.root} ({mode})")
    lines.append(
        f"  {human(root.bytes)} in {root.total_files:,} files "
        f"across {root.total_dirs:,} directories"
        + (f", {result.elapsed_s:.2f} s" if result.elapsed_s >= 0.001 else "")
    )
    lines.append("")

    fs = result.filesystem
    if fs and fs.size:
        who = _SEP.join(p for p in (fs.device, fs.fstype) if p)
        lines.append(f"  filesystem {fs.mountpoint}" + (f" ({who})" if who else ""))
        pct = fs.used_pct
        lines.append(
            f"  {human(fs.used)} used of {human(fs.size)}  "
            f"{bar(pct / 100.0, bar_width)}  {pct:.1f}%"
        )
        lines.append("")

    total = root.bytes
    listed_total = 0
    for level in range(1, max(depth, 1) + 1):
        nodes = level_nodes(result, level)
        if not nodes:
            break
        shown = [n for n in nodes if n.bytes >= min_size][:top]
        if not shown:
            break
        biggest = shown[0].bytes or 1
        lines.append(f"  depth {level}  ({len(nodes)} entr{'y' if len(nodes) == 1 else 'ies'})")
        for node in shown:
            share = percent(node.bytes, total)
            lines.append(
                f"   {share:5.1f}%  {bar(node.bytes / biggest, bar_width)}  "
                f"{human(node.bytes):>9}  {_path_label(node, result.root)}"
                + ("  [mount]" if node.mount else "")
            )
            listed_total += node.bytes
        hidden = len(nodes) - len(shown)
        if hidden > 0:
            rest = sum(n.bytes for n in nodes[len(shown):])
            lines.append(f"          {' ' * bar_width}  {human(rest):>9}  "
                         f"+{hidden} more")
        lines.append("")

    files = top_files(result, top)
    if files:
        lines.append(f"  top {len(files)} files")
        biggest = files[0][1] or 1
        for path, size in files:
            lines.append(
                f"           {bar(size / biggest, bar_width)}  {human(size):>9}  {path}"
            )
        lines.append("")

    excluded = result.excluded_bytes
    hidden_bytes = total - listed_total
    lines.append(
        f"  total {human(total)}  ·  listed {human(listed_total)}  ·  "
        f"below the cut {human(max(0, hidden_bytes))}"
        + (f"  ·  excluded {human(excluded)}" if excluded else "")
    )

    if result.warnings:
        lines.append(f"  warnings ({len(result.warnings)})")
        for w in result.warnings[:20]:
            lines.append(f"    {w}")
        if len(result.warnings) > 20:
            lines.append(f"    … {len(result.warnings) - 20} more")
    return "\n".join(lines).rstrip() + "\n"


def top_files(result: ScanResult, top: int) -> list[tuple[str, int]]:
    """The biggest individual files anywhere in the tree."""
    out: list[tuple[str, int]] = []
    for node in result.root_node.walk():
        for f in node.files_here:
            if f.bytes > 0:
                rel = f"{node.relpath}/{f.name}" if node.relpath != "." else f.name
                out.append((rel, f.bytes))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out[:top]


def render_du(
    result: ScanResult,
    *,
    depth: int = 1,
    all_files: bool = False,
    min_size: int = 0,
    bar_width: int = 18,
    show_bar: bool = True,
) -> str:
    """``du`` output, sorted biggest first, with a share bar.

    The ``du | sort -n`` replacement: same numbers, already ranked.
    """
    rows: list[tuple[int, str]] = []
    for node in result.root_node.walk():
        if node is not result.root_node and node.relpath == ".":
            continue
        level = 0 if node is result.root_node else node.relpath.count("/") + 1
        if level > depth:
            continue
        if node.bytes >= min_size:
            rows.append((node.bytes, _path_label(node, result.root)))
        if all_files:
            for f in node.files_here:
                if f.bytes >= min_size:
                    rel = f"{node.relpath}/{f.name}" if node.relpath != "." else f.name
                    rows.append((f.bytes, rel))
    rows.sort(key=lambda t: (-t[0], t[1]))
    if not rows:
        return ""
    biggest = rows[0][0] or 1
    lines = []
    for size, path in rows:
        if show_bar:
            lines.append(f"{human(size):>9}  {bar(size / biggest, bar_width)}  {path}")
        else:
            lines.append(f"{human(size):>9}  {path}")
    return "\n".join(lines) + "\n"


def render_disks(filesystems: list[Filesystem], *, bar_width: int = 20) -> str:
    """The ``disks`` view: every real filesystem, biggest first, with a used bar."""
    if not filesystems:
        return "no filesystems to report\n"
    lines = [f"{'device':<24} {'mount':<22} {'type':<10} "
             f"{'size':>9} {'used':>9} {'free':>9}  used"]
    for fs in filesystems:
        lines.append(
            f"{fs.device:<24} {fs.mountpoint:<22} {fs.fstype:<10} "
            f"{human(fs.size):>9} {human(fs.used):>9} {human(fs.free):>9}  "
            f"{bar(fs.used_pct / 100.0, bar_width)} {fs.used_pct:5.1f}%"
        )
    total = sum(f.size for f in filesystems)
    used = sum(f.used for f in filesystems)
    lines.append("")
    lines.append(f"{len(filesystems)} filesystem(s), {human(used)} used of {human(total)} "
                 f"({percent(used, total):.1f}%)")
    return "\n".join(lines) + "\n"
