"""The HTML report: one self-contained file, inline CSS + inline SVG.

No JavaScript, no network references, no fonts, no images, no external CSS —
the page has to render the same from a USB stick, a ``file://`` URL on a box
with no internet, and a browser ten years older than this one. That is the
whole reason the charts are generated as SVG paths here instead of pulling in
a charting library: the artifact is the deliverable, and a chart that needs a
CDN is not a report.
"""
from __future__ import annotations

from html import escape

from .chart import MIN_LABEL_PCT, merge_other, pie, treemap
from .model import Filesystem, ScanResult, human, percent
from .report import level_nodes, top_files
from . import TOOL, VERSION

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: #f6f7f9; color: #16202b;
  font: 14px/1.45 -apple-system, "Segoe UI", Roboto, "DejaVu Sans", sans-serif; }
@media (prefers-color-scheme: dark) { body { background: #14181d; color: #e6e9ee; }
  .card { background: #1c2229 !important; border-color: #2b333c !important; }
  table, th, td { border-color: #2b333c !important; } tr:nth-child(even) td { background:#20262e; }
  .muted { color: #99a3ad !important; } .bar span { background:#3d4a57 !important; } }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 12px; }
.sub { color: #5b6672; margin: 0 0 20px; }
.card { background: #fff; border: 1px solid #e2e5ea; border-radius: 8px;
  padding: 18px; margin: 0 0 18px; }
.row { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #e2e5ea; }
th { font-weight: 600; color: #5b6672; font-size: 12px; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 7px; vertical-align: baseline; }
.bar { display: inline-block; width: 120px; height: 9px; background: #e8ebef;
  border-radius: 3px; overflow: hidden; vertical-align: middle; }
.bar span { display: block; height: 100%; }
.muted { color: #6b7683; }
.mono { font-family: ui-monospace, "DejaVu Sans Mono", Menlo, Consolas, monospace; }
.warn { color: #8a4b12; }
footer { color: #6b7683; font-size: 12px; margin-top: 8px; }
svg { max-width: 100%; height: auto; }
text { font-family: -apple-system, "Segoe UI", Roboto, "DejaVu Sans", sans-serif; }
"""


def _bar_html(fraction: float, color: str) -> str:
    pct = max(0.0, min(1.0, float(fraction))) * 100.0
    return f'<span class="bar"><span style="width:{pct:.1f}%;background:{color}"></span></span>'


def _filesystem_block(fs: Filesystem | None) -> str:
    if not fs or not fs.size:
        return ""
    who = " · ".join(p for p in (fs.device, fs.fstype) if p)
    return (
        '<div class="card"><h2>Filesystem</h2>'
        f'<div class="mono">{escape(fs.mountpoint)}'
        + (f" — {escape(who)}" if who else "")
        + "</div>"
        f'<div style="margin-top:8px">{_bar_html(fs.used / fs.size, "#2f6f9f")} '
        f'{fs.used_pct:.1f}% used — {human(fs.used)} of {human(fs.size)}, '
        f"{human(fs.free)} free</div></div>"
    )


def _svg_pie(slices, cx: float, cy: float, r: float, inner: float, total_bytes: int) -> str:
    parts = [
        f'<svg viewBox="0 0 {int(cx * 2)} {int(cy * 2)}" width="{int(cx * 2)}" '
        f'height="{int(cy * 2)}" role="img" aria-label="Share of used space">'
    ]
    for s in slices:
        parts.append(f'<path d="{s.path}" fill="{s.color}" stroke="#ffffff" stroke-width="1"/>')
    if inner > 0:
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{inner:.0f}" fill="#ffffff"/>')
        parts.append(
            f'<text x="{cx:.0f}" y="{cy - 4:.0f}" text-anchor="middle" font-size="17" '
            f'font-weight="600">{escape(human(total_bytes))}</text>'
        )
        parts.append(
            f'<text x="{cx:.0f}" y="{cy + 16:.0f}" text-anchor="middle" font-size="12" '
            f'fill="#6b7683">total</text>'
        )
    for s in slices:
        if s.label_xy and s.percent >= MIN_LABEL_PCT:
            x, y = s.label_xy
            parts.append(
                f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-size="12" '
                f'fill="#ffffff" font-weight="600">{s.percent:.0f}%</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _legend(slices) -> str:
    rows = []
    for s in slices:
        rows.append(
            "<tr>"
            f'<td><span class="sw" style="background:{s.color}"></span>'
            f'<span class="mono">{escape(s.label)}</span></td>'
            f'<td class="n">{escape(human(s.value))}</td>'
            f'<td class="n">{s.percent:.1f}%</td>'
            "</tr>"
        )
    return (
        '<table><thead><tr><th>Entry</th><th class="n">Size</th>'
        f'<th class="n">Share</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _svg_treemap(rects, w: float, h: float) -> str:
    parts = [
        f'<svg viewBox="0 0 {int(w)} {int(h)}" width="{int(w)}" height="{int(h)}" '
        'role="img" aria-label="Treemap of used space, area is bytes">'
    ]
    for r in rects:
        parts.append(
            f'<rect x="{r.x:.1f}" y="{r.y:.1f}" width="{r.w:.1f}" height="{r.h:.1f}" '
            f'fill="{r.color}" fill-opacity="0.88" stroke="#ffffff" stroke-width="1"/>'
        )
        if r.label_xy is not None:
            x, y = r.label_xy
            parts.append(
                f'<text x="{x + 6:.1f}" y="{y + 16:.1f}" font-size="12" fill="#ffffff" '
                f'font-weight="600">{escape(r.label)}</text>'
            )
            if r.h >= 34:
                parts.append(
                    f'<text x="{x + 6:.1f}" y="{y + 31:.1f}" font-size="11" '
                    f'fill="#ffffff" fill-opacity="0.85">{escape(human(r.value))} '
                    f'({r.percent:.1f}%)</text>'
                )
    parts.append("</svg>")
    return "".join(parts)


def _depth_table(result: ScanResult, level: int, top: int, min_size: int, total: int) -> str:
    nodes = level_nodes(result, level)
    nodes = [n for n in nodes if n.bytes >= min_size]
    if not nodes:
        return ""
    shown = nodes[:top]
    biggest = shown[0].bytes or 1
    rows = []
    for i, node in enumerate(shown):
        color = _palette(i)
        rows.append(
            "<tr>"
            f'<td><span class="sw" style="background:{color}"></span>'
            f'<span class="mono">{escape(node.relpath)}</span>'
            + (" <span class=\"muted\">[mount]</span>" if node.mount else "")
            + "</td>"
            f'<td class="n">{escape(human(node.bytes))}</td>'
            f'<td class="n">{percent(node.bytes, total):.1f}%</td>'
            f'<td>{_bar_html(node.bytes / biggest, color)}</td>'
            f'<td class="n muted">{node.total_files:,} files</td>'
            "</tr>"
        )
    rest = nodes[top:]
    if rest:
        rows.append(
            '<tr><td class="muted">+'
            f"{len(rest)} more entr{'y' if len(rest) == 1 else 'ies'}</td>"
            f'<td class="n muted">{escape(human(sum(n.bytes for n in rest)))}</td>'
            f'<td class="n muted">{percent(sum(n.bytes for n in rest), total):.1f}%</td>'
            "<td></td><td></td></tr>"
        )
    return (
        f'<h2>Depth {level} <span class="muted">— {len(nodes)} entr'
        f'{"y" if len(nodes) == 1 else "ies"}</span></h2>'
        '<table><thead><tr><th>Path</th><th class="n">Size</th><th class="n">Share</th>'
        f'<th>Bar</th><th class="n">Files</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )


def _palette(i: int) -> str:
    from .chart import PALETTE

    return PALETTE[i % len(PALETTE)]


def _files_table(result: ScanResult, top: int) -> str:
    files = top_files(result, top)
    if not files:
        return ""
    biggest = files[0][1] or 1
    rows = []
    for i, (path, size) in enumerate(files):
        color = _palette(i)
        rows.append(
            "<tr>"
            f'<td><span class="mono">{escape(path)}</span></td>'
            f'<td class="n">{escape(human(size))}</td>'
            f'<td>{_bar_html(size / biggest, color)}</td>'
            "</tr>"
        )
    return (
        f'<h2>Top {len(files)} files</h2>'
        '<table><thead><tr><th>File</th><th class="n">Size</th><th>Bar</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def render_html(
    result: ScanResult,
    *,
    depth: int = 3,
    top: int = 15,
    min_size: int = 0,
    pie_size: int = 340,
    treemap_w: int = 920,
    treemap_h: int = 340,
) -> str:
    """A complete, standalone HTML document for one scan."""
    root = result.root_node
    total = root.bytes
    mode = "disk usage (blocks)" if result.mode == "blocks" else "apparent size"

    # The charts show depth-1 entries: the top `top`, the tail folded into Other.
    entries = [(n.relpath if n.relpath != "." else n.name, n.bytes) for n in level_nodes(result, 1)]
    chart_items = merge_other(entries, top)

    inner = pie_size / 2.0 * 0.37
    slices = pie(chart_items, cx=pie_size / 2.0, cy=pie_size / 2.0,
                 r=pie_size / 2.0 - 20, inner=inner)
    rects = treemap(chart_items, 0, 0, treemap_w, treemap_h, gap=2)

    body: list[str] = [
        '<header class="card">',
        f"<h1>Storage — {escape(result.root)}</h1>",
        f'<p class="sub">{escape(human(total))} in {root.total_files:,} files across '
        f"{root.total_dirs:,} directories · {escape(mode)} · scanned "
        f"{escape(result.generated)}",
    ]
    if result.excluded_bytes:
        body.append(f" · {escape(human(result.excluded_bytes))} excluded")
    body.append("</p></header>")

    body.append(_filesystem_block(result.filesystem))

    body.append('<div class="card"><h2>Where the space is</h2><div class="row">')
    body.append(_svg_pie(slices, pie_size / 2.0, pie_size / 2.0, pie_size / 2.0 - 20, inner, total))
    body.append("<div>" + _legend(slices) + "</div>")
    body.append("</div></div>")

    body.append(f'<div class="card"><h2>Treemap <span class="muted">— area is bytes</span></h2>'
                f"{_svg_treemap(rects, treemap_w, treemap_h)}</div>")

    for level in range(1, max(depth, 1) + 1):
        block = _depth_table(result, level, top, min_size, total)
        if block:
            body.append(f'<div class="card">{block}</div>')

    files_block = _files_table(result, top)
    if files_block:
        body.append(f'<div class="card">{files_block}</div>')

    if result.warnings:
        items = "".join(f'<div class="warn mono">{escape(w)}</div>' for w in result.warnings[:50])
        more = len(result.warnings) - 50
        if more > 0:
            items += f'<div class="muted">… {more} more</div>'
        body.append(f'<div class="card"><h2>Warnings ({len(result.warnings)})</h2>{items}</div>')

    body.append(
        f'<footer>Generated by {TOOL} {VERSION} · read-only scan · this page is '
        "self-contained: no scripts, no network, no external assets.</footer>"
    )

    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Storage — {escape(result.root)}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


def render_disks_html(filesystems: list[Filesystem]) -> str:
    """The ``disks`` view as the same kind of standalone page."""
    rows = []
    biggest = max((f.size for f in filesystems), default=1) or 1
    for i, fs in enumerate(filesystems):
        color = _palette(i)
        rows.append(
            "<tr>"
            f'<td><span class="mono">{escape(fs.device)}</span></td>'
            f'<td><span class="mono">{escape(fs.mountpoint)}</span></td>'
            f'<td class="muted">{escape(fs.fstype)}</td>'
            f'<td class="n">{escape(human(fs.size))}</td>'
            f'<td class="n">{escape(human(fs.used))}</td>'
            f'<td class="n">{escape(human(fs.free))}</td>'
            f'<td>{_bar_html(fs.used / fs.size if fs.size else 0, color)} '
            f'{fs.used_pct:.1f}%</td>'
            "</tr>"
        )
    total = sum(f.size for f in filesystems)
    used = sum(f.used for f in filesystems)
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Filesystems</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        '<header class="card"><h1>Filesystems</h1>'
        f'<p class="sub">{len(filesystems)} mounted · {escape(human(used))} used of '
        f"{escape(human(total))} ({percent(used, total):.1f}%)</p></header>\n"
        '<div class="card"><table><thead><tr><th>Device</th><th>Mount</th><th>Type</th>'
        '<th class="n">Size</th><th class="n">Used</th><th class="n">Free</th>'
        f'<th>Used</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>\n'
        f'<footer>Generated by {TOOL} {VERSION} · read-only.</footer>\n'
        "</body>\n</html>\n"
    )
