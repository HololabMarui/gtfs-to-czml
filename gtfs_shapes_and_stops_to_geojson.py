#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GTFS-JP から
1) バスルートGeoJSON（LineString）
2) バス停GeoJSON（Point, marker-*付き）
を別ファイルで同時出力するスクリプト

主な仕様:
- shapes.txt がある場合は shape ベースでルート線を生成
- shape_id が無い / shapes.txt が無い trip は、必要に応じて stops ベースの擬似線を生成
- route_id 指定で対象路線を絞り込み可能
- 停留所GeoJSONには marker-color / marker-symbol / marker-size を付与
- route_color を stroke に反映（必要ならCLIで上書き可能）

使い方例:
python3 gtfs_shapes_and_stops_to_geojson.py \
  --gtfs-dir ./work/gtfs_feed \
  --route-output ./out/routes.geojson \
  --stops-output ./out/stops.geojson \
  --fallback-mode stops \
  --marker-color "#e11d48" \
  --marker-symbol bus \
  --marker-size medium
"""

import argparse
import csv
import json
import math
import os
from typing import Dict, List, Tuple, Optional, Set


def read_csv_dict(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_csv_if_exists(path: str) -> List[Dict[str, str]]:
    return read_csv_dict(path) if os.path.exists(path) else []


def parse_hex_color(s: Optional[str], default=(58, 143, 255, 255)) -> Tuple[int, int, int, int]:
    if not s:
        return default
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (r, g, b, 255)
    if len(s) == 8:
        r, g, b, a = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
        return (r, g, b, a)
    return default


def rgba_to_hex(rgba: Tuple[int, int, int, int]) -> str:
    r, g, b, _ = rgba
    return "#{:02X}{:02X}{:02X}".format(r, g, b)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class ShapePoint:
    def __init__(self, seq: int, lat: float, lon: float, dist_m: float):
        self.seq = seq
        self.lat = lat
        self.lon = lon
        self.dist_m = dist_m


def load_routes(gtfs_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(gtfs_dir, "routes.txt")
    if not os.path.exists(path):
        raise FileNotFoundError("routes.txt が見つかりません")
    return {r["route_id"]: r for r in read_csv_dict(path)}


def load_trips(gtfs_dir: str, route_filter: Optional[List[str]]) -> List[Dict[str, str]]:
    path = os.path.join(gtfs_dir, "trips.txt")
    if not os.path.exists(path):
        raise FileNotFoundError("trips.txt が見つかりません")
    trips = read_csv_dict(path)
    if route_filter:
        route_set = set(route_filter)
        trips = [t for t in trips if t.get("route_id") in route_set]
    return trips


def load_stops(gtfs_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(gtfs_dir, "stops.txt")
    if not os.path.exists(path):
        raise FileNotFoundError("stops.txt が見つかりません")
    return {r["stop_id"]: r for r in read_csv_dict(path)}


def load_stop_times(gtfs_dir: str) -> List[Dict[str, str]]:
    path = os.path.join(gtfs_dir, "stop_times.txt")
    if not os.path.exists(path):
        raise FileNotFoundError("stop_times.txt が見つかりません")
    rows = read_csv_dict(path)
    rows.sort(key=lambda r: (r.get("trip_id", ""), int(r.get("stop_sequence", "0") or "0")))
    return rows


def build_stop_times_by_trip(gtfs_dir: str) -> Dict[str, List[Dict[str, str]]]:
    rows = load_stop_times(gtfs_dir)
    out: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        tid = r.get("trip_id")
        if not tid:
            continue
        out.setdefault(tid, []).append(r)
    return out


def load_shapes(gtfs_dir: str) -> Dict[str, List[ShapePoint]]:
    path = os.path.join(gtfs_dir, "shapes.txt")
    rows = read_csv_if_exists(path)
    if not rows:
        return {}

    groups: Dict[str, List[Tuple[int, float, float, Optional[float]]]] = {}
    for r in rows:
        sid = r.get("shape_id")
        if not sid:
            continue
        seq = int(r["shape_pt_sequence"])
        lat = float(r["shape_pt_lat"])
        lon = float(r["shape_pt_lon"])
        dist = r.get("shape_dist_traveled")
        dist_m = float(dist) if dist and dist.strip() != "" else None
        groups.setdefault(sid, []).append((seq, lat, lon, dist_m))

    shapes: Dict[str, List[ShapePoint]] = {}
    for sid, items in groups.items():
        items.sort(key=lambda x: x[0])
        pts: List[ShapePoint] = []
        cum = 0.0
        prev = None
        for seq, lat, lon, dist_m in items:
            if dist_m is None:
                if prev is None:
                    cum = 0.0
                else:
                    cum += haversine_m(prev[1], prev[2], lat, lon)
                pts.append(ShapePoint(seq, lat, lon, cum))
            else:
                pts.append(ShapePoint(seq, lat, lon, dist_m))
            prev = (seq, lat, lon)
        if len(pts) >= 2:
            shapes[sid] = pts
    return shapes


def build_shape_from_stops(stop_times: List[Dict[str, str]], stops_by_id: Dict[str, Dict[str, str]]) -> List[ShapePoint]:
    pts: List[ShapePoint] = []
    cum = 0.0
    prev = None
    sorted_rows = sorted(stop_times, key=lambda r: int(r.get("stop_sequence", "0") or "0"))
    for r in sorted_rows:
        stop_id = r.get("stop_id")
        st = stops_by_id.get(stop_id or "")
        if not st:
            continue
        if not st.get("stop_lat") or not st.get("stop_lon"):
            continue
        lat = float(st["stop_lat"])
        lon = float(st["stop_lon"])
        if prev is not None:
            cum += haversine_m(prev[0], prev[1], lat, lon)
        pts.append(ShapePoint(int(r.get("stop_sequence", "0") or "0"), lat, lon, cum))
        prev = (lat, lon)
    return pts


def make_route_feature(
    route_id: str,
    shape_id: str,
    shape_points: List[ShapePoint],
    route_row: Dict[str, str],
    is_fallback: bool,
    stroke_override: Optional[str],
    stroke_width: float,
    stroke_opacity: float,
) -> Dict:
    coordinates = [[p.lon, p.lat] for p in shape_points]
    route_color_hex = stroke_override or rgba_to_hex(parse_hex_color(route_row.get("route_color")))
    props = {
        "route_id": route_id,
        "shape_id": shape_id,
        "route_short_name": route_row.get("route_short_name"),
        "route_long_name": route_row.get("route_long_name"),
        "route_desc": route_row.get("route_desc"),
        "route_color": route_row.get("route_color"),
        "route_text_color": route_row.get("route_text_color"),
        "agency_id": route_row.get("agency_id"),
        "route_type": route_row.get("route_type"),
        "is_fallback": is_fallback,
        "stroke": route_color_hex,
        "stroke-opacity": float(stroke_opacity),
        "stroke-width": float(stroke_width),
    }
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
        "properties": props,
    }


def make_stop_feature(
    stop: Dict[str, str],
    marker_color: str,
    marker_symbol: str,
    marker_size: str,
) -> Dict:
    lon = float(stop["stop_lon"])
    lat = float(stop["stop_lat"])
    props = {
        "stop_id": stop.get("stop_id"),
        "stop_code": stop.get("stop_code"),
        "stop_name": stop.get("stop_name"),
        "stop_desc": stop.get("stop_desc"),
        "zone_id": stop.get("zone_id"),
        "stop_url": stop.get("stop_url"),
        "location_type": stop.get("location_type"),
        "parent_station": stop.get("parent_station"),
        "wheelchair_boarding": stop.get("wheelchair_boarding"),
        "platform_code": stop.get("platform_code"),
        "marker-color": marker_color,
        "marker-symbol": marker_symbol,
        "marker-size": marker_size,
    }
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
        "properties": props,
    }


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def main():
    ap = argparse.ArgumentParser(description="GTFS-JP からルート線GeoJSONと停留所GeoJSONを別出力")
    ap.add_argument("--gtfs-dir", required=True, help="GTFS ディレクトリ")
    ap.add_argument("--route-output", required=True, help="出力ルートGeoJSON")
    ap.add_argument("--stops-output", required=True, help="出力停留所GeoJSON")
    ap.add_argument("--route-id", action="append", help="対象 route_id（複数指定可）")
    ap.add_argument(
        "--fallback-mode",
        choices=["stops", "none"],
        default="stops",
        help="shape が無い trip の扱い",
    )

    ap.add_argument("--marker-color", default="#3a8fff", help="停留所マーカー色")
    ap.add_argument("--marker-symbol", default="bus", help="停留所マーカー種別")
    ap.add_argument(
        "--marker-size",
        default="medium",
        choices=["small", "medium", "large"],
        help="停留所マーカーサイズ",
    )

    ap.add_argument("--route-stroke", default=None, help="ルート線色を一括上書き（#RRGGBB）")
    ap.add_argument("--route-width", type=float, default=3.0, help="ルート線幅")
    ap.add_argument("--route-opacity", type=float, default=1.0, help="ルート線不透明度")
    ap.add_argument("--debug", action="store_true", help="デバッグ表示")

    args = ap.parse_args()

    routes = load_routes(args.gtfs_dir)
    trips = load_trips(args.gtfs_dir, args.route_id)
    stops = load_stops(args.gtfs_dir)
    stop_times_by_trip = build_stop_times_by_trip(args.gtfs_dir)
    shapes = load_shapes(args.gtfs_dir)

    emitted_route_keys: Set[str] = set()
    used_stop_ids: Set[str] = set()
    route_features: List[Dict] = []

    for trip in trips:
        trip_id = trip.get("trip_id")
        route_id = trip.get("route_id")
        shape_id = trip.get("shape_id")
        if not trip_id or not route_id:
            continue

        st_rows = stop_times_by_trip.get(trip_id, [])
        for r in st_rows:
            sid = r.get("stop_id")
            if sid:
                used_stop_ids.add(sid)

        use_shape: List[ShapePoint]
        feature_shape_id: str
        is_fallback = False

        if shape_id and shape_id in shapes and len(shapes[shape_id]) >= 2:
            use_shape = shapes[shape_id]
            feature_shape_id = shape_id
        else:
            if args.fallback_mode == "none":
                if args.debug:
                    print(f"[skip] trip={trip_id} shapeなし")
                continue
            use_shape = build_shape_from_stops(st_rows, stops)
            if len(use_shape) < 2:
                if args.debug:
                    print(f"[skip] trip={trip_id} 擬似shape不足")
                continue
            feature_shape_id = f"pseudo-{trip_id}"
            is_fallback = True

        route_key = f"{route_id}:{feature_shape_id}"
        if route_key in emitted_route_keys:
            continue

        route_row = routes.get(route_id, {})
        route_features.append(
            make_route_feature(
                route_id=route_id,
                shape_id=feature_shape_id,
                shape_points=use_shape,
                route_row=route_row,
                is_fallback=is_fallback,
                stroke_override=args.route_stroke,
                stroke_width=args.route_width,
                stroke_opacity=args.route_opacity,
            )
        )
        emitted_route_keys.add(route_key)

    stop_features: List[Dict] = []
    target_stop_ids = used_stop_ids if args.route_id else set(stops.keys())

    for stop_id in sorted(target_stop_ids):
        st = stops.get(stop_id)
        if not st:
            continue
        if not st.get("stop_lat") or not st.get("stop_lon"):
            continue
        stop_features.append(
            make_stop_feature(
                stop=st,
                marker_color=args.marker_color,
                marker_symbol=args.marker_symbol,
                marker_size=args.marker_size,
            )
        )

    route_geojson = {"type": "FeatureCollection", "features": route_features}
    stops_geojson = {"type": "FeatureCollection", "features": stop_features}

    ensure_parent_dir(args.route_output)
    ensure_parent_dir(args.stops_output)

    with open(args.route_output, "w", encoding="utf-8") as f:
        json.dump(route_geojson, f, ensure_ascii=False, indent=2)

    with open(args.stops_output, "w", encoding="utf-8") as f:
        json.dump(stops_geojson, f, ensure_ascii=False, indent=2)

    print(f"Wrote route GeoJSON: {args.route_output} (features={len(route_features)})")
    print(f"Wrote stops GeoJSON: {args.stops_output} (features={len(stop_features)})")


if __name__ == "__main__":
    main()