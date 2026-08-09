#!/usr/bin/env python3
"""Turn a flight's telemetry (+ the LLM's command audit) into a 3D Google Earth track.

This is the headline visual for Plan 18 (§C): the vehicle's trajectory drawn in
true 3D (absolute altitude), coloured by flight mode, with an arrow dropped at
every timestamp the language model sent a command. Open the resulting ``.kmz``
in Google Earth Pro, tilt the view, and you can literally watch the AI fly the
aircraft - each arrow is one tool call, coloured by the mode the vehicle was in
when the command landed.

Inputs (both CSV, schemas fixed by Plan 19):

* ``telemetry.csv`` - the position + mode time series (>=10 Hz). Columns used:
  ``t_iso`` (ISO-8601 timestamp), ``lat_deg``, ``lon_deg``, ``abs_alt_m``
  (altitude above mean sea level, metres), ``flight_mode``. ``t_rel_s``,
  ``rel_alt_m``, ``armed`` and any other columns are ignored but tolerated.
* ``audit_slice.csv`` (optional) - the server-side audit rows, one per command
  the LLM sent. Columns used: ``ts`` (ISO-8601), ``tool``, ``verdict``. Extra
  columns are folded into the placemark description.

Output: a single ``.kmz`` (a zip of one ``doc.kml``) - or a bare ``doc.kml`` with
``--no-kmz``. Everything is hand-written from stdlib string templates and
``zipfile``; there are NO heavy dependencies (``simplekml`` is used only if it
happens to be importable, and even then this module never requires it).

Examples::

    # the common case: a per-trial directory from the benchmark runner
    uv run python scripts/telemetry_to_kml.py \\
        --telemetry benchmark_runs/20260808T.../telemetry.csv \\
        --audit     benchmark_runs/20260808T.../audit_slice.csv \\
        --out       flight.kmz --name "T7 orbit, GPT-4o"

    # telemetry only, emit a plain doc.kml instead of a kmz
    uv run python scripts/telemetry_to_kml.py \\
        --telemetry telemetry.csv --out doc.kml --no-kmz
"""

from __future__ import annotations

import argparse
import csv
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

# ---------------------------------------------------------------------------
# Flight-mode -> colour legend.
#
# Colours are KML "aabbggrr" hex (alpha, blue, green, red - NOT rgba!), fully
# opaque. Chosen to be distinct and, where possible, intuitive: RTL/LAND warm
# (the vehicle is coming home / down), GUIDED the AI-driving blue, AUTO the
# mission green, holds cyan/purple. Any mode not listed falls back to DEFAULT.
# ---------------------------------------------------------------------------
MODE_COLORS: dict[str, str] = {
    "GUIDED": "ffff7800",  # azure blue   - the mode the LLM flies in
    "AUTO": "ff32c832",  # green        - running a stored mission
    "RTL": "ff0000ff",  # red          - return-to-launch
    "SMART_RTL": "ff0060ff",  # orange-red   - smart return-to-launch
    "LAND": "ff0090ff",  # orange       - descending to land
    "TAKEOFF": "ff00d7ff",  # amber        - auto take-off climb
    "LOITER": "ffff9900",  # cyan-blue    - GPS position hold w/ stick
    "POSHOLD": "ffffcc00",  # cyan         - pilot-assisted position hold
    "ALT_HOLD": "ffcccc00",  # teal         - altitude hold only
    "BRAKE": "ff8080ff",  # salmon       - emergency stop
    "CIRCLE": "ffff00cc",  # magenta      - orbit a point
    "STABILIZE": "ff909090",  # grey         - manual, self-levelling
    "ACRO": "ff606060",  # dark grey    - manual, rate
    "DRIFT": "ffcc66ff",  # pink-purple  - coordinated-turn manual
    "FOLLOW": "ffccff00",  # spring       - follow-me
    "GUIDED_NOGPS": "ffffaa44",  # lighter blue - guided without GPS
}
DEFAULT_COLOR = "ffffffff"  # opaque white for any unrecognised mode

# A stock Google Earth heading-arrow icon; IconStyle <color> tints it per mode.
ARROW_ICON_HREF = "http://maps.google.com/mapfiles/kml/shapes/arrow.png"

# Anything outside this band is treated as a bad altitude sample and clamped, so
# a single garbage row can't send the track to the centre of the earth or space.
_ALT_MIN_M = -500.0
_ALT_MAX_M = 20000.0


def mode_color(mode: str | None) -> str:
    """Return the KML aabbggrr colour for ``mode`` (case-insensitive)."""
    if not mode:
        return DEFAULT_COLOR
    return MODE_COLORS.get(mode.strip().upper(), DEFAULT_COLOR)


# ---------------------------------------------------------------------------
# Parsing helpers - deliberately defensive: real logs have blank cells, NaNs
# and the odd malformed row, and none of those should crash the export.
# ---------------------------------------------------------------------------
def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        f = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _parse_iso_epoch(value: str | None) -> float | None:
    """ISO-8601 string -> POSIX seconds. Naive timestamps are assumed UTC."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # Tolerate a trailing "Z" (Python <3.11 fromisoformat rejects it).
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _clamp_alt(alt: float) -> float:
    return max(_ALT_MIN_M, min(_ALT_MAX_M, alt))


class _Fix:
    """One usable telemetry sample."""

    __slots__ = ("t", "lat", "lon", "alt", "mode", "t_iso")

    def __init__(self, t: float, lat: float, lon: float, alt: float, mode: str, t_iso: str):
        self.t = t
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.mode = mode
        self.t_iso = t_iso


def _read_telemetry(telemetry_csv: Path) -> list[_Fix]:
    """Load telemetry into time-ordered fixes, dropping unusable rows."""
    fixes: list[_Fix] = []
    with telemetry_csv.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            lat = _to_float(row.get("lat_deg"))
            lon = _to_float(row.get("lon_deg"))
            alt = _to_float(row.get("abs_alt_m"))
            t = _parse_iso_epoch(row.get("t_iso"))
            if lat is None or lon is None or alt is None or t is None:
                continue  # incomplete/NaN sample - skip it
            mode = (row.get("flight_mode") or "").strip().upper() or "UNKNOWN"
            fixes.append(_Fix(t, lat, lon, _clamp_alt(alt), mode, (row.get("t_iso") or "").strip()))
    fixes.sort(key=lambda f: f.t)
    return fixes


def _read_audit(audit_csv: Path) -> list[dict]:
    rows: list[dict] = []
    with audit_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if _parse_iso_epoch(row.get("ts")) is not None:
                rows.append(row)
    return rows


def _segments_by_mode(fixes: list[_Fix]) -> list[tuple[str, list[_Fix]]]:
    """Split the ordered fixes into contiguous runs of a single flight mode.

    Each run overlaps the next by one fix (the first sample of the next run is
    appended as the last coord of the current run) so the coloured tracks join
    up with no visible gap where the mode changes.
    """
    if not fixes:
        return []
    segments: list[tuple[str, list[_Fix]]] = []
    start = 0
    for i in range(1, len(fixes) + 1):
        if i == len(fixes) or fixes[i].mode != fixes[start].mode:
            run = fixes[start:i]
            if i < len(fixes):
                run = run + [fixes[i]]  # bridge to the next segment
            segments.append((fixes[start].mode, run))
            start = i
    return segments


def _interp_position(fixes: list[_Fix], t: float) -> tuple[float, float, float, str] | None:
    """Vehicle (lat, lon, alt, mode) at time ``t`` by linear time interpolation.

    ``t`` before the first / after the last fix clamps to that endpoint. Mode is
    categorical, so it takes the value of the temporally nearer bracketing fix.
    """
    if not fixes:
        return None
    if t <= fixes[0].t:
        f = fixes[0]
        return f.lat, f.lon, f.alt, f.mode
    if t >= fixes[-1].t:
        f = fixes[-1]
        return f.lat, f.lon, f.alt, f.mode
    # binary-search-free linear scan is fine at benchmark scale
    for i in range(1, len(fixes)):
        a, b = fixes[i - 1], fixes[i]
        if a.t <= t <= b.t:
            span = b.t - a.t
            frac = 0.0 if span <= 0 else (t - a.t) / span
            lat = a.lat + (b.lat - a.lat) * frac
            lon = a.lon + (b.lon - a.lon) * frac
            alt = a.alt + (b.alt - a.alt) * frac
            mode = a.mode if frac < 0.5 else b.mode
            return lat, lon, _clamp_alt(alt), mode
    return None


# ---------------------------------------------------------------------------
# KML emission (hand-written).
# ---------------------------------------------------------------------------
def _iso_utc(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _style_block(mode: str) -> str:
    color = mode_color(mode)
    sid = _style_id(mode)
    return (
        f'  <Style id="{sid}">\n'
        f"    <LineStyle><color>{color}</color><width>4</width></LineStyle>\n"
        f"    <PolyStyle><color>{color}</color></PolyStyle>\n"
        f"    <IconStyle>\n"
        f"      <color>{color}</color><scale>1.1</scale>\n"
        f"      <Icon><href>{escape(ARROW_ICON_HREF)}</href></Icon>\n"
        f"    </IconStyle>\n"
        f"    <LabelStyle><scale>0.8</scale></LabelStyle>\n"
        f"  </Style>\n"
    )


def _style_id(mode: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in (mode or "UNKNOWN"))
    return f"mode_{safe}"


def _track_placemark(mode: str, run: list[_Fix]) -> str:
    whens = "".join(f"      <when>{_iso_utc(f.t)}</when>\n" for f in run)
    coords = "".join(f"      <gx:coord>{f.lon:.8f} {f.lat:.8f} {f.alt:.3f}</gx:coord>\n" for f in run)
    return (
        f"    <Placemark>\n"
        f"      <name>{escape(mode)}</name>\n"
        f"      <styleUrl>#{_style_id(mode)}</styleUrl>\n"
        f"      <gx:Track>\n"
        f"        <altitudeMode>absolute</altitudeMode>\n"
        f"{whens}{coords}"
        f"      </gx:Track>\n"
        f"    </Placemark>\n"
    )


def _command_placemark(row: dict, lat: float, lon: float, alt: float, mode: str) -> str:
    tool = (row.get("tool") or "command").strip()
    verdict = (row.get("verdict") or "").strip()
    label = tool if not verdict else f"{tool} [{verdict}]"
    ts = (row.get("ts") or "").strip()
    detail = "".join(
        f"      <li><b>{escape(str(k))}</b>: {escape(str(v))}</li>\n" for k, v in row.items() if v not in (None, "")
    )
    desc = f"<![CDATA[<b>{escape(tool)}</b> at {escape(ts)}<br/>mode <b>{escape(mode)}</b><ul>{detail.strip()}</ul>]]>"
    return (
        f"    <Placemark>\n"
        f"      <name>{escape(label)}</name>\n"
        f"      <description>{desc}</description>\n"
        f"      <styleUrl>#{_style_id(mode)}</styleUrl>\n"
        f"      <Point>\n"
        f"        <altitudeMode>absolute</altitudeMode>\n"
        f"        <extrude>1</extrude>\n"
        f"        <coordinates>{lon:.8f},{lat:.8f},{alt:.3f}</coordinates>\n"
        f"      </Point>\n"
        f"    </Placemark>\n"
    )


def _legend_folder(modes_present: list[str]) -> str:
    items = ["    <Folder>\n      <name>Legend</name>\n"]
    for mode in modes_present:
        items.append(
            f"      <Placemark>\n"
            f"        <name>{escape(mode)} = {mode_color(mode)}</name>\n"
            f"        <styleUrl>#{_style_id(mode)}</styleUrl>\n"
            f"      </Placemark>\n"
        )
    items.append("    </Folder>\n")
    return "".join(items)


def _legend_description(modes_present: list[str], n_commands: int) -> str:
    rows = "".join(
        f"<li><span style='color:#{_aabbggrr_to_web(mode_color(m))}'>&#9632;</span> "
        f"<b>{escape(m)}</b> ({mode_color(m)})</li>"
        for m in modes_present
    )
    return (
        f"<![CDATA[3D flight track coloured by flight mode. "
        f"{n_commands} LLM command(s) marked with arrows.<br/>"
        f"<b>Mode legend</b><ul>{rows}</ul>]]>"
    )


def _aabbggrr_to_web(kml: str) -> str:
    """Convert a KML aabbggrr colour to web #rrggbb (best-effort, for the legend)."""
    if len(kml) == 8:
        bb, gg, rr = kml[2:4], kml[4:6], kml[6:8]
        return f"{rr}{gg}{bb}"
    return "ffffff"


def _build_kml(name: str, fixes: list[_Fix], audit_rows: list[dict]) -> str:
    segments = _segments_by_mode(fixes)

    # Modes present, in first-seen order, plus any modes only seen at commands.
    modes_present: list[str] = []
    for mode, _run in segments:
        if mode not in modes_present:
            modes_present.append(mode)

    # Resolve each command to a position before we know the full mode set.
    command_blocks: list[str] = []
    for row in audit_rows:
        t = _parse_iso_epoch(row.get("ts"))
        if t is None:
            continue
        pos = _interp_position(fixes, t)
        if pos is None:
            continue
        lat, lon, alt, mode = pos
        if mode not in modes_present:
            modes_present.append(mode)
        command_blocks.append(_command_placemark(row, lat, lon, alt, mode))

    styles = "".join(_style_block(m) for m in modes_present)
    track_placemarks = "".join(_track_placemark(mode, run) for mode, run in segments)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">\n',
        "<Document>\n",
        f"  <name>{escape(name)}</name>\n",
        f"  <description>{_legend_description(modes_present, len(command_blocks))}</description>\n",
        styles,
        "  <Folder>\n    <name>Track (by flight mode)</name>\n",
        track_placemarks,
        "  </Folder>\n",
    ]
    if command_blocks:
        parts.append("  <Folder>\n    <name>LLM commands</name>\n")
        parts.extend(command_blocks)
        parts.append("  </Folder>\n")
    parts.append(_legend_folder(modes_present))
    parts.append("</Document>\n</kml>\n")
    return "".join(parts)


def telemetry_to_kml(
    telemetry_csv: Path,
    out_path: Path,
    audit_csv: Path | None = None,
    name: str = "flight",
    kmz: bool = True,
) -> Path:
    """Render a flight track (mode-coloured 3D) + LLM-command arrows to KML/KMZ.

    Args:
        telemetry_csv: Plan-19 telemetry CSV (t_iso, lat_deg, lon_deg,
            abs_alt_m, flight_mode, ...).
        out_path: destination file. Written verbatim - the caller controls the
            extension (use ``.kmz`` with ``kmz=True``, ``.kml`` with
            ``kmz=False``).
        audit_csv: optional Plan-19 audit slice (ts, tool, verdict, ...); each
            row becomes one command arrow.
        name: document name shown in Google Earth.
        kmz: if True, write a zipped ``.kmz`` (a single ``doc.kml``); if False,
            write the raw ``doc.kml`` text.

    Returns:
        ``out_path``.
    """
    telemetry_csv = Path(telemetry_csv)
    out_path = Path(out_path)
    fixes = _read_telemetry(telemetry_csv)
    audit_rows = _read_audit(Path(audit_csv)) if audit_csv else []

    kml_text = _build_kml(name, fixes, audit_rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kmz:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("doc.kml", kml_text)
    else:
        out_path.write_text(kml_text, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Google Earth 3D KML/KMZ flight track (mode-coloured) with an arrow at every LLM command.",
    )
    parser.add_argument(
        "--telemetry",
        required=True,
        type=Path,
        help="Plan-19 telemetry.csv (t_iso, lat_deg, lon_deg, abs_alt_m, flight_mode)",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=None,
        help="optional Plan-19 audit_slice.csv (ts, tool, verdict) - one arrow per row",
    )
    parser.add_argument("--out", required=True, type=Path, help="output .kmz (or .kml with --no-kmz)")
    parser.add_argument("--name", default="flight", help="document name shown in Google Earth")
    parser.add_argument(
        "--no-kmz", dest="kmz", action="store_false", help="write a plain doc.kml instead of a zipped .kmz"
    )
    args = parser.parse_args()

    if not args.telemetry.exists():
        parser.error(f"telemetry file not found: {args.telemetry}")
    if args.audit is not None and not args.audit.exists():
        parser.error(f"audit file not found: {args.audit}")

    out = telemetry_to_kml(
        telemetry_csv=args.telemetry,
        out_path=args.out,
        audit_csv=args.audit,
        name=args.name,
        kmz=args.kmz,
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
