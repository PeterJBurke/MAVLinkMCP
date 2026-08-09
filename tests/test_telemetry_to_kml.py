"""Unit tests for scripts/telemetry_to_kml.py (Plan 18 §C headline visual).

Synthesises a tiny telemetry track (mode switching GUIDED -> AUTO -> RTL) plus a
handful of LLM command rows, renders KML/KMZ, and asserts the output is
well-formed and carries the expected mode-coloured segments and command arrows.
Deterministic and fast - no network, no drone, no heavy deps.
"""

from __future__ import annotations

import csv
import importlib.util
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Load the CLI script as a module (it lives under scripts/, not an installed pkg).
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "telemetry_to_kml.py"
_spec = importlib.util.spec_from_file_location("telemetry_to_kml", _SCRIPT)
tkml = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(tkml)

KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"

# A GUIDED -> AUTO -> RTL sequence over 20 rows (indices 0-6 GUIDED, 7-13 AUTO,
# 14-19 RTL), 2 Hz starting at a fixed epoch so command timestamps land inside.
_T0 = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
_MODE_SEQUENCE = (["GUIDED"] * 7) + (["AUTO"] * 7) + (["RTL"] * 6)


def _write_telemetry(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_iso", "t_rel_s", "lat_deg", "lon_deg", "abs_alt_m", "rel_alt_m", "flight_mode", "armed"])
        for i, mode in enumerate(_MODE_SEQUENCE):
            t = _T0 + timedelta(seconds=0.5 * i)
            lat = 33.6400 + i * 0.0001
            lon = -117.8400 + i * 0.0001
            abs_alt = 100.0 + i  # climbing
            w.writerow(
                [
                    t.isoformat().replace("+00:00", "Z"),
                    0.5 * i,
                    f"{lat:.6f}",
                    f"{lon:.6f}",
                    f"{abs_alt:.1f}",
                    f"{abs_alt - 100.0:.1f}",
                    mode,
                    "True",
                ]
            )


def _write_audit(path: Path) -> None:
    # Three commands, each timestamp within the telemetry span, one per mode.
    stamps = [
        (_T0 + timedelta(seconds=1.0), "arm_drone", "allowed"),  # GUIDED
        (_T0 + timedelta(seconds=4.5), "start_mission", "allowed"),  # AUTO
        (_T0 + timedelta(seconds=8.0), "return_to_launch", "confirmation_required"),  # RTL
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "tool", "verdict"])
        for t, tool, verdict in stamps:
            w.writerow([t.isoformat().replace("+00:00", "Z"), tool, verdict])


@pytest.fixture()
def flight(tmp_path: Path):
    tel = tmp_path / "telemetry.csv"
    aud = tmp_path / "audit_slice.csv"
    _write_telemetry(tel)
    _write_audit(aud)
    return tel, aud


def test_kml_is_well_formed_and_has_track_segments(flight, tmp_path):
    tel, aud = flight
    out = tmp_path / "doc.kml"
    result = tkml.telemetry_to_kml(tel, out, audit_csv=aud, name="unit-flight", kmz=False)
    assert result == out and out.exists()

    text = out.read_text(encoding="utf-8")
    # Well-formed XML.
    root = ET.fromstring(text)
    assert root.tag == f"{{{KML_NS}}}kml"

    # Document name carries the requested name.
    doc = root.find(f"{{{KML_NS}}}Document")
    assert doc is not None
    assert doc.findtext(f"{{{KML_NS}}}name") == "unit-flight"

    # One gx:Track per contiguous mode segment: GUIDED, AUTO, RTL -> exactly 3.
    tracks = root.findall(f".//{{{GX_NS}}}Track")
    assert len(tracks) == 3, f"expected 3 mode segments, got {len(tracks)}"

    # Each track uses absolute altitude and has matching <when>/<gx:coord> counts.
    for track in tracks:
        assert track.findtext(f"{{{KML_NS}}}altitudeMode") == "absolute"
        whens = track.findall(f"{{{KML_NS}}}when")
        coords = track.findall(f"{{{GX_NS}}}coord")
        assert whens and coords and len(whens) == len(coords)


def test_track_placemarks_named_by_mode(flight, tmp_path):
    tel, aud = flight
    out = tmp_path / "doc.kml"
    tkml.telemetry_to_kml(tel, out, audit_csv=aud, kmz=False)
    root = ET.fromstring(out.read_text(encoding="utf-8"))

    track_folder = None
    for folder in root.iter(f"{{{KML_NS}}}Folder"):
        if folder.findtext(f"{{{KML_NS}}}name") == "Track (by flight mode)":
            track_folder = folder
            break
    assert track_folder is not None
    names = {pm.findtext(f"{{{KML_NS}}}name") for pm in track_folder.findall(f"{{{KML_NS}}}Placemark")}
    assert {"GUIDED", "AUTO", "RTL"} <= names


def test_colors_come_from_mode_colors(flight, tmp_path):
    tel, aud = flight
    out = tmp_path / "doc.kml"
    tkml.telemetry_to_kml(tel, out, audit_csv=aud, kmz=False)
    text = out.read_text(encoding="utf-8")
    for mode in ("GUIDED", "AUTO", "RTL"):
        assert tkml.MODE_COLORS[mode] in text


def test_llm_command_arrows(flight, tmp_path):
    tel, aud = flight
    out = tmp_path / "doc.kml"
    tkml.telemetry_to_kml(tel, out, audit_csv=aud, kmz=False)
    root = ET.fromstring(out.read_text(encoding="utf-8"))

    cmd_folder = None
    for folder in root.iter(f"{{{KML_NS}}}Folder"):
        if folder.findtext(f"{{{KML_NS}}}name") == "LLM commands":
            cmd_folder = folder
            break
    assert cmd_folder is not None, "missing 'LLM commands' folder"

    placemarks = cmd_folder.findall(f"{{{KML_NS}}}Placemark")
    assert len(placemarks) == 3, "one arrow per audit command expected"

    # Each command arrow is a Point at absolute altitude and labelled by tool.
    labels = " ".join(pm.findtext(f"{{{KML_NS}}}name") or "" for pm in placemarks)
    for tool in ("arm_drone", "start_mission", "return_to_launch"):
        assert tool in labels
    for pm in placemarks:
        point = pm.find(f"{{{KML_NS}}}Point")
        assert point is not None
        assert point.findtext(f"{{{KML_NS}}}altitudeMode") == "absolute"
        coords = point.findtext(f"{{{KML_NS}}}coordinates")
        assert coords and len(coords.split(",")) == 3


def test_command_arrow_position_is_interpolated(flight, tmp_path):
    """The RTL command at t+8.0s must land at the interpolated track position."""
    tel, aud = flight
    out = tmp_path / "doc.kml"
    tkml.telemetry_to_kml(tel, out, audit_csv=aud, kmz=False)
    root = ET.fromstring(out.read_text(encoding="utf-8"))

    coords_by_tool = {}
    for folder in root.iter(f"{{{KML_NS}}}Folder"):
        if folder.findtext(f"{{{KML_NS}}}name") != "LLM commands":
            continue
        for pm in folder.findall(f"{{{KML_NS}}}Placemark"):
            name = pm.findtext(f"{{{KML_NS}}}name") or ""
            txt = pm.find(f"{{{KML_NS}}}Point").findtext(f"{{{KML_NS}}}coordinates")
            lon, lat, alt = (float(x) for x in txt.split(","))
            coords_by_tool[name.split()[0]] = (lon, lat, alt)

    # t+8.0s -> row index 16 exactly (2 Hz): lat 33.6400+16*1e-4, alt 100+16.
    lon, lat, alt = coords_by_tool["return_to_launch"]
    assert lat == pytest.approx(33.6400 + 16 * 0.0001, abs=1e-6)
    assert alt == pytest.approx(116.0, abs=1e-3)


def test_kmz_is_valid_zip_with_doc_kml(flight, tmp_path):
    tel, aud = flight
    out = tmp_path / "flight.kmz"
    tkml.telemetry_to_kml(tel, out, audit_csv=aud, name="kmz-flight", kmz=True)
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as zf:
        assert "doc.kml" in zf.namelist()
        inner = zf.read("doc.kml").decode("utf-8")
    ET.fromstring(inner)  # inner doc is well-formed
    assert "kmz-flight" in inner


def test_telemetry_only_still_produces_track(tmp_path):
    tel = tmp_path / "telemetry.csv"
    _write_telemetry(tel)
    out = tmp_path / "doc.kml"
    tkml.telemetry_to_kml(tel, out, audit_csv=None, kmz=False)
    root = ET.fromstring(out.read_text(encoding="utf-8"))
    assert root.findall(f".//{{{GX_NS}}}Track")
    # No audit -> no LLM commands folder.
    names = {f.findtext(f"{{{KML_NS}}}name") for f in root.iter(f"{{{KML_NS}}}Folder")}
    assert "LLM commands" not in names


def test_nan_and_blank_rows_are_skipped(tmp_path):
    tel = tmp_path / "telemetry.csv"
    with tel.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_iso", "t_rel_s", "lat_deg", "lon_deg", "abs_alt_m", "rel_alt_m", "flight_mode", "armed"])
        base = _T0
        w.writerow([base.isoformat(), 0, "33.64", "-117.84", "100", "0", "GUIDED", "True"])
        # blank lat, NaN alt, empty timestamp - all must be dropped, no crash.
        w.writerow([(base + timedelta(seconds=1)).isoformat(), 1, "", "-117.84", "101", "1", "GUIDED", "True"])
        w.writerow([(base + timedelta(seconds=2)).isoformat(), 2, "33.64", "-117.84", "NaN", "1", "GUIDED", "True"])
        w.writerow(["", 3, "33.64", "-117.84", "102", "2", "GUIDED", "True"])
        w.writerow([(base + timedelta(seconds=4)).isoformat(), 4, "33.641", "-117.841", "104", "4", "AUTO", "True"])
    out = tmp_path / "doc.kml"
    tkml.telemetry_to_kml(tel, out, kmz=False)
    root = ET.fromstring(out.read_text(encoding="utf-8"))
    # Only 2 valid fixes survive (GUIDED, AUTO) -> tracks still emitted.
    assert root.findall(f".//{{{GX_NS}}}Track")


def test_unknown_mode_gets_default_color():
    assert tkml.mode_color("BANANARAMA") == tkml.DEFAULT_COLOR
    assert tkml.mode_color(None) == tkml.DEFAULT_COLOR
    assert tkml.mode_color("guided") == tkml.MODE_COLORS["GUIDED"]
