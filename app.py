from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

from flask import Flask, render_template, request

BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"
OUTPUTS_DIR = BASE_DIR / "outputs"

TMP_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

REQUIRED_FILES = ["routes.txt", "trips.txt", "stops.txt", "stop_times.txt"]


def resolve_gtfs_root(extract_dir: Path) -> Path:
    if list(extract_dir.glob("*.txt")):
        return extract_dir

    for child in extract_dir.iterdir():
        if child.is_dir() and list(child.glob("*.txt")):
            return child

    return extract_dir


def read_csv_dict(path: Path) -> list[dict[str, str]]:
    import csv
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_route_ids(raw: str) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def build_stops_command(
    gtfs_dir: str,
    output_path: str,
    marker_color: str,
    marker_symbol: str,
    marker_size: str,
    route_ids: list[str],
) -> list[str]:
    script_path = BASE_DIR / "gtfs_stops_to_geojson.py"

    cmd = [
        "python3",
        str(script_path),
        "--gtfs-dir", gtfs_dir,
        "--output", output_path,
        "--marker-color", marker_color,
        "--marker-symbol", marker_symbol,
        "--marker-size", marker_size,
    ]

    for rid in route_ids:
        cmd.extend(["--route-id", rid])

    return cmd


def build_czml_command_preview(
    gtfs_dir: str,
    service_date: str,
    tz: str,
    route_ids: list[str],
    model_url: str,
    fallback_mode: str,
    sample_every: str,
    trail: str,
    with_point: bool,
    clamp_to_ground: bool,
    debug: bool,
) -> str:
    script_path = BASE_DIR / "gtfsjp_to_czml.py"

    cmd = [
        "python3",
        str(script_path),
        "--gtfs-dir", gtfs_dir,
        "--service-date", service_date,
        "--tz", tz,
        "--output", "animation.czml",
        "--fallback-mode", fallback_mode,
        "--sample-every", sample_every,
        "--trail", trail,
    ]

    if model_url:
        cmd.extend(["--model-url", model_url])

    if with_point:
        cmd.append("--with-point")

    if clamp_to_ground:
        cmd.append("--clamp-to-ground")

    if debug:
        cmd.append("--debug")

    for rid in route_ids:
        cmd.extend(["--route-id", rid])

    return shell_join(cmd)


def run_command(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "command": shell_join(cmd),
    }


def build_routes_geojson(
    gtfs_dir: Path,
    output_path: Path,
    route_filter: list[str],
) -> dict:
    shapes_path = gtfs_dir / "shapes.txt"
    routes_path = gtfs_dir / "routes.txt"
    trips_path = gtfs_dir / "trips.txt"

    if not shapes_path.exists():
        return {
            "ok": False,
            "message": "shapes.txt が無いため routes.geojson を生成できません。",
            "feature_count": 0,
            "output_path": str(output_path),
        }

    routes = {r["route_id"]: r for r in read_csv_dict(routes_path)}
    trips = read_csv_dict(trips_path)

    if route_filter:
        trips = [t for t in trips if t.get("route_id") in route_filter]

    shape_to_route: dict[str, str] = {}
    for t in trips:
        shape_id = t.get("shape_id")
        route_id = t.get("route_id")
        if shape_id and route_id and shape_id not in shape_to_route:
            shape_to_route[shape_id] = route_id

    grouped: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for row in read_csv_dict(shapes_path):
        shape_id = row.get("shape_id")
        if not shape_id or shape_id not in shape_to_route:
            continue

        try:
            seq = int(row["shape_pt_sequence"])
            lat = float(row["shape_pt_lat"])
            lon = float(row["shape_pt_lon"])
        except (KeyError, ValueError):
            continue

        grouped[shape_id].append((seq, lon, lat))

    features = []
    for shape_id, points in grouped.items():
        points.sort(key=lambda x: x[0])
        route_id = shape_to_route[shape_id]
        route = routes.get(route_id, {})

        color = (route.get("route_color") or "3388ff").strip()
        if color and not color.startswith("#"):
            color = f"#{color}"

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat] for _, lon, lat in points],
            },
            "properties": {
                "route_id": route_id,
                "shape_id": shape_id,
                "route_short_name": route.get("route_short_name"),
                "route_long_name": route.get("route_long_name"),
                "route_color": color,
                "stroke": color,
                "stroke-width": 4,
                "stroke-opacity": 1.0,
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    output_path.write_text(
        json.dumps(geojson, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "message": "routes.geojson を生成しました。",
        "feature_count": len(features),
        "output_path": str(output_path),
    }


@app.route("/gtfs-to-czml/", methods=["GET"])
def index():
    index_path = BASE_DIR / "docs" / "index.html"
    return index_path.read_text(encoding="utf-8")


@app.route("/gtfs-to-czml/inspect", methods=["POST"])
def inspect_zip():
    uploaded = request.files.get("gtfs_zip")

    if uploaded is None or uploaded.filename == "":
        return render_template(
            "inspect_result.html",
            zip_name="",
            save_path="",
            extract_path="",
            found_files=[],
            required_results=[],
            all_ok=False,
            error="Zipファイルが選択されていません。",
            form_data={},
            commands=None,
            stops_run=None,
            routes_run=None,
        )

    if not uploaded.filename.lower().endswith(".zip"):
        return render_template(
            "inspect_result.html",
            zip_name=uploaded.filename,
            save_path="",
            extract_path="",
            found_files=[],
            required_results=[],
            all_ok=False,
            error="Zipファイルをアップロードしてください。",
            form_data={},
            commands=None,
            stops_run=None,
            routes_run=None,
        )

    service_date = request.form.get("service_date", "").strip()
    tz = request.form.get("tz", "Asia/Tokyo").strip()
    route_ids_raw = request.form.get("route_ids", "").strip()
    route_ids = parse_route_ids(route_ids_raw)
    model_url = request.form.get("model_url", "").strip()
    fallback_mode = request.form.get("fallback_mode", "stops").strip()
    sample_every = request.form.get("sample_every", "50").strip()
    trail = request.form.get("trail", "60").strip()
    marker_color = request.form.get("marker_color", "#3a8fff").strip()
    marker_symbol = request.form.get("marker_symbol", "bus").strip()
    marker_size = request.form.get("marker_size", "medium").strip()
    with_point = bool(request.form.get("with_point"))
    clamp_to_ground = bool(request.form.get("clamp_to_ground"))
    debug = bool(request.form.get("debug"))

    form_data = {
        "service_date": service_date,
        "tz": tz,
        "route_ids_raw": route_ids_raw,
        "route_ids": route_ids,
        "model_url": model_url,
        "fallback_mode": fallback_mode,
        "sample_every": sample_every,
        "trail": trail,
        "marker_color": marker_color,
        "marker_symbol": marker_symbol,
        "marker_size": marker_size,
        "with_point": with_point,
        "clamp_to_ground": clamp_to_ground,
        "debug": debug,
    }

    work_dir = Path(tempfile.mkdtemp(prefix="gtfsjp_", dir=str(TMP_DIR)))
    zip_path = work_dir / uploaded.filename
    extract_dir = work_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    uploaded.save(zip_path)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        return render_template(
            "inspect_result.html",
            zip_name=uploaded.filename,
            save_path=str(zip_path),
            extract_path=str(extract_dir),
            found_files=[],
            required_results=[],
            all_ok=False,
            error="Zipの展開に失敗しました。壊れたZipの可能性があります。",
            form_data=form_data,
            commands=None,
            stops_run=None,
            routes_run=None,
        )

    gtfs_root = resolve_gtfs_root(extract_dir)
    found_files = sorted([p.name for p in gtfs_root.glob("*.txt")])

    required_results = []
    for name in REQUIRED_FILES:
        exists = (gtfs_root / name).exists()
        required_results.append({
            "name": name,
            "exists": exists,
        })

    all_ok = all(item["exists"] for item in required_results)

    commands = None
    stops_run = None
    routes_run = None

    if all_ok:
        stops_output = OUTPUTS_DIR / f"{work_dir.name}_stops.geojson"
        routes_output = OUTPUTS_DIR / f"{work_dir.name}_routes.geojson"

        stops_cmd = build_stops_command(
            gtfs_dir=str(gtfs_root),
            output_path=str(stops_output),
            marker_color=marker_color,
            marker_symbol=marker_symbol,
            marker_size=marker_size,
            route_ids=route_ids,
        )

        stops_result = run_command(stops_cmd)
        stops_run = {
            "command": stops_result["command"],
            "returncode": stops_result["returncode"],
            "stdout": stops_result["stdout"],
            "stderr": stops_result["stderr"],
            "output_path": str(stops_output),
            "output_exists": stops_output.exists(),
        }

        route_result = build_routes_geojson(
            gtfs_dir=gtfs_root,
            output_path=routes_output,
            route_filter=route_ids,
        )
        routes_run = {
            "ok": route_result["ok"],
            "message": route_result["message"],
            "feature_count": route_result["feature_count"],
            "output_path": route_result["output_path"],
            "output_exists": Path(route_result["output_path"]).exists(),
        }

        commands = {
            "stops_command": stops_result["command"],
            "czml_command": build_czml_command_preview(
                gtfs_dir=str(gtfs_root),
                service_date=service_date,
                tz=tz,
                route_ids=route_ids,
                model_url=model_url,
                fallback_mode=fallback_mode,
                sample_every=sample_every,
                trail=trail,
                with_point=with_point,
                clamp_to_ground=clamp_to_ground,
                debug=debug,
            ),
        }

    return render_template(
        "inspect_result.html",
        zip_name=uploaded.filename,
        save_path=str(zip_path),
        extract_path=str(gtfs_root),
        found_files=found_files,
        required_results=required_results,
        all_ok=all_ok,
        error=None,
        form_data=form_data,
        commands=commands,
        stops_run=stops_run,
        routes_run=routes_run,
    )


if __name__ == "__main__":
    app.run(debug=True)