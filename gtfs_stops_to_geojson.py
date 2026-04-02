#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GTFS-JP の停留所 (stops.txt) → GeoJSON（Point）変換
- Geolonia / Mapbox スタイル互換の marker-* プロパティを付与:
    marker-color, marker-symbol, marker-size
- route_id 指定がある場合は、そのルートで使われる停留所だけを抽出
- 日本語名なども properties に残す

使い方:
  python3 gtfs_stops_to_geojson.py \
    --gtfs-dir ./gtfs_naha \
    --output naha_stops.geojson \
    --marker-color "#3a8fff" --marker-symbol bus --marker-size medium

  # ルートIDで絞り込む（複数可）
  python3 gtfs_stops_to_geojson.py \
    --gtfs-dir ./gtfs_naha \
    --route-id 1 --route-id 3 \
    --output naha_stops_r1_r3.geojson
"""

import argparse
import csv
import json
import os
from typing import Dict, List, Set

def read_csv_dict(path: str) -> List[Dict[str, str]]:
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def load_stops(gtfs_dir: str) -> Dict[str, Dict[str, str]]:
    path = os.path.join(gtfs_dir, "stops.txt")
    if not os.path.exists(path):
        raise FileNotFoundError("stops.txt が見つかりません")
    stops = {}
    for r in read_csv_dict(path):
        sid = r["stop_id"]
        stops[sid] = r
    return stops

def load_trips_by_route(gtfs_dir: str, route_filter: List[str]) -> List[str]:
    """route_id のリストに属する trip_id を返す"""
    path = os.path.join(gtfs_dir, "trips.txt")
    if not os.path.exists(path):
        return []
    trips = read_csv_dict(path)
    route_set = set(route_filter)
    return [t["trip_id"] for t in trips if t.get("route_id") in route_set]

def load_stops_used_by_trips(gtfs_dir: str, trip_ids: List[str]) -> Set[str]:
    """与えた trip_id 群で使用される stop_id の集合を返す"""
    if not trip_ids:
        return set()
    tid_set = set(trip_ids)
    path = os.path.join(gtfs_dir, "stop_times.txt")
    if not os.path.exists(path):
        return set()
    used: Set[str] = set()
    for r in read_csv_dict(path):
        if r.get("trip_id") in tid_set:
            sid = r.get("stop_id")
            if sid:
                used.add(sid)
    return used

def to_float(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except Exception:
        return default

def build_feature(stop: Dict[str, str],
                  marker_color: str,
                  marker_symbol: str,
                  marker_size: str) -> Dict:
    lon = to_float(stop.get("stop_lon", "0"))
    lat = to_float(stop.get("stop_lat", "0"))
    props = {
        # 表示スタイル（Geolonia/Mapbox互換）
        "marker-color": marker_color,
        "marker-symbol": marker_symbol,
        "marker-size": marker_size,

        # 主要属性（必要に応じて拡張）
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
    }
    # GeoJSON Feature
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }

def main():
    ap = argparse.ArgumentParser(description="GTFS-JP stops.txt → GeoJSON（marker-*付き）")
    ap.add_argument("--gtfs-dir", required=True, help="GTFS ディレクトリ（*.txt）")
    ap.add_argument("--output", required=True, help="出力 GeoJSON パス")
    ap.add_argument("--marker-color", default="#3a8fff",
                    help="例: #3a8fff（既定）")
    ap.add_argument("--marker-symbol", default="bus",
                    help="例: bus（既定）")
    ap.add_argument("--marker-size", default="medium",
                    choices=["small", "medium", "large"],
                    help="small/medium/large（既定: medium）")
    ap.add_argument("--route-id", action="append",
                    help="対象の route_id（複数指定可）。未指定なら全停留所を出力")
    args = ap.parse_args()

    stops = load_stops(args.gtfs_dir)

    # route_id 指定があれば使用停留所でフィルタ
    if args.route_id:
        trip_ids = load_trips_by_route(args.gtfs_dir, args.route_id)
        used_stop_ids = load_stops_used_by_trips(args.gtfs_dir, trip_ids)
        # 該当しない stop は間引く
        stops = {sid: s for sid, s in stops.items() if sid in used_stop_ids}

    features = []
    for stop in stops.values():
        # 座標が欠落している行はスキップ
        if not stop.get("stop_lat") or not stop.get("stop_lon"):
            continue
        features.append(build_feature(stop, args.marker_color, args.marker_symbol, args.marker_size))

    geojson = {"type": "FeatureCollection", "features": features}

    # 出力
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    print(f"Wrote: {args.output} (features={len(features)})")

if __name__ == "__main__":
    main()
