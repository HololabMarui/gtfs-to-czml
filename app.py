from __future__ import annotations

import shlex
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, render_template, request

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

REQUIRED_FILES = ["routes.txt", "trips.txt", "stops.txt", "stop_times.txt"]


def resolve_gtfs_root(extract_dir: Path) -> Path:
    """
    Zip直下に txt がある場合と、1階層下にまとまっている場合の両方に対応
    """
    if list(extract_dir.glob("*.txt")):
        return extract_dir

    for child in extract_dir.iterdir():
        if child.is_dir() and list(child.glob("*.txt")):
            return child

    return extract_dir


def parse_route_ids(raw: str) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def build_commands(
    gtfs_dir: str,
    service_date: str,
    tz: str,
    route_ids: list[str],
    model_url: str,
    fallback_mode: str,
    sample_every: str,
    trail: str,
    marker_color: str,
    marker_symbol: str,
    marker_size: str,
    with_point: bool,
    clamp_to_ground: bool,
    debug: bool,
) -> dict[str, str]:
    stops_script = BASE_DIR / "gtfs_stops_to_geojson.py"
    czml_script = BASE_DIR / "gtfsjp_to_czml.py"

    stops_cmd = [
        "python3",
        str(stops_script),
        "--gtfs-dir", gtfs_dir,
        "--output", "stops.geojson",
        "--marker-color", marker_color,
        "--marker-symbol", marker_symbol,
        "--marker-size", marker_size,
    ]

    for rid in route_ids:
        stops_cmd.extend(["--route-id", rid])

    czml_cmd = [
        "python3",
        str(czml_script),
        "--gtfs-dir", gtfs_dir,
        "--service-date", service_date,
        "--tz", tz,
        "--output", "animation.czml",
        "--fallback-mode", fallback_mode,
        "--sample-every", sample_every,
        "--trail", trail,
    ]

    if model_url:
        czml_cmd.extend(["--model-url", model_url])

    if with_point:
        czml_cmd.append("--with-point")

    if clamp_to_ground:
        czml_cmd.append("--clamp-to-ground")

    if debug:
        czml_cmd.append("--debug")

    for rid in route_ids:
        czml_cmd.extend(["--route-id", rid])

    return {
        "stops_command": shell_join(stops_cmd),
        "czml_command": shell_join(czml_cmd),
        "routes_note": "routes.geojson は次の段階で shapes.txt から生成する処理を追加予定です。",
    }


@app.route("/", methods=["GET"])
def index():
    index_path = BASE_DIR / "docs" / "index.html"
    return index_path.read_text(encoding="utf-8")


@app.route("/inspect", methods=["POST"])
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

    work_dir = Path(tempfile.mkdtemp(prefix="gtfsjp_"))
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
    if all_ok:
        commands = build_commands(
            gtfs_dir=str(gtfs_root),
            service_date=service_date,
            tz=tz,
            route_ids=route_ids,
            model_url=model_url,
            fallback_mode=fallback_mode,
            sample_every=sample_every,
            trail=trail,
            marker_color=marker_color,
            marker_symbol=marker_symbol,
            marker_size=marker_size,
            with_point=with_point,
            clamp_to_ground=clamp_to_ground,
            debug=debug,
        )

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
    )


if __name__ == "__main__":
    app.run(debug=True)