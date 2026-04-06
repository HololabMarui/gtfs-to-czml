#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GTFS-JP → CZML 変換スクリプト（モデル走行・gltf既定・運行日判定＆フォールバック制御つき）

主な機能:
  ① GTFS-JP の routes / trips / stop_times / stops / shapes を読んで CZML を出力
  ② 外部3DCG（GLB/GLTF）を shape ルート上で走行（model.gltf / model.uri 互換）
  ③ stop_times の時刻をサービス日タイムゾーン基準で UTC に変換
  ④ routes.txt の route_color を採用（CLIで一括上書き可、不透明度も反映）
  ⑤ calendar(.txt) / calendar_dates(.txt) に基づいて **その日の service_id のみ**に自動フィルタ
  ⑥ shapes.txt が無い / shape_id が無い trip は、オプションで
        --fallback-mode stops  : 停留所順に擬似shapeを生成（直線補完）
        --fallback-mode none   : その trip は描画しない（直線を出さない）
  ⑦ 互換オプション: position epoch/iso、orientation無効化、Point併記、trailTime、clampToGround など

▼使い方1（直線を出したくない＝shape無し便を非表示）
python gtfsjp_to_czml.py \
  --gtfs-dir ./GTFS_JP_FEED \
  --service-date 2025-09-22 \
  --output bus.czml \
  --model-url https://storage.googleapis.com/<bucket>/bus.glb \
  --fallback-mode none \
  --with-point --trail 60 --debug


▼使い方2（shape が無い便も暫定で走らせる＝停留所直線）
python gtfsjp_to_czml.py \
  --gtfs-dir ./GTFS_JP_FEED \
  --service-date 2025-09-22 \
  --output bus.czml \
  --model-url https://storage.googleapis.com/<bucket>/bus.glb \
  --fallback-mode stops --sample-every 10 \
  --with-point --trail 60 --debug


▼使い方3
python3 gtfsjp_to_czml.py \
  --gtfs-dir ./yanbaru \
  --service-date 2025-09-22 \
  --output yanbaru.czml \
  --line-opacity 1.0 \
  --model-height-reference RELATIVE_TO_GROUND \
  --model-url https://storage.googleapis.com/hogehoge/004_bus.glb \
  --fallback-mode none \
  --clamp-to-ground            # ← ルート線が地表に張り付く

"""

import argparse, csv, json, math, os, sys
from dataclasses import dataclass
from datetime import datetime, timedelta, date, time, timezone
from typing import Dict, List, Tuple, Optional
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ============ ユーティリティ ============

def read_csv_dict(path: str) -> List[Dict[str, str]]:
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def read_csv_if_exists(path: str) -> List[Dict[str, str]]:
    return read_csv_dict(path) if os.path.exists(path) else []

def parse_hex_color(s: Optional[str], default=(0,128,255,255)) -> Tuple[int,int,int,int]:
    if not s:
        return default
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 6:
        r,g,b = int(s[0:2],16), int(s[2:4],16), int(s[4:6],16)
        return (r,g,b,255)
    if len(s) == 8:
        r,g,b,a = int(s[0:2],16), int(s[2:4],16), int(s[4:6],16), int(s[6:8],16)
        return (r,g,b,a)
    return default

def with_opacity(c: Tuple[int,int,int,int], alpha01: float) -> Tuple[int,int,int,int]:
    r,g,b,_ = c
    return (r,g,b,int(max(0,min(1,alpha01))*255))

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2
    return 2*R*math.asin(math.sqrt(a))

def parse_gtfs_time_hhmmss(s: str) -> Tuple[int,int,int]:
    hh,mm,ss = s.strip().split(":")
    return int(hh), int(mm), int(ss)

def to_datetime_on_service_day(hh:int, mm:int, ss:int, service_day:date, tz:ZoneInfo) -> datetime:
    extra_days, hour = divmod(hh, 24)
    return datetime.combine(service_day, time(hour,mm,ss)).replace(tzinfo=tz) + timedelta(days=extra_days)

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

@dataclass
class ShapePoint:
    seq: int
    lat: float
    lon: float
    dist_m: float

# ============ calendar / calendar_dates（運行判定） ============

def yyyymmdd(d: date) -> str:
    return f"{d:%Y%m%d}"

def load_calendar(gtfs_dir: str) -> List[Dict[str,str]]:
    return read_csv_if_exists(os.path.join(gtfs_dir, "calendar.txt"))

def load_calendar_dates(gtfs_dir: str) -> List[Dict[str,str]]:
    return read_csv_if_exists(os.path.join(gtfs_dir, "calendar_dates.txt"))

def services_active_on(gtfs_dir: str, service_day: date) -> set:
    cal = load_calendar(gtfs_dir)
    cdates = load_calendar_dates(gtfs_dir)
    ymd = yyyymmdd(service_day)

    wd = service_day.weekday()  # Mon=0 .. Sun=6
    wd_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]

    active = set()
    for r in cal:
        if not r.get("start_date") or not r.get("end_date"):
            continue
        if r["start_date"] <= ymd <= r["end_date"]:
            if r.get(wd_names[wd], "0") in ("1","true","TRUE","True"):
                active.add(r["service_id"])

    for r in cdates:
        if r.get("date") != ymd:
            continue
        sid = r["service_id"]
        et = r.get("exception_type")
        if et == "1":   # 追加運行
            active.add(sid)
        elif et == "2": # 運休
            active.discard(sid)
    return active

# ============ GTFS ロード ============

def load_shapes(gtfs_dir: str, assume_km=False) -> Dict[str, List[ShapePoint]]:
    """shapes を辞書に。shape_dist_traveled が無ければハバースインで累積距離化。"""
    path = os.path.join(gtfs_dir, "shapes.txt")
    rows = read_csv_if_exists(path)
    if not rows:
        return {}  # 無くてもOK（停留所フォールバックで走る）
    groups: Dict[str, List[Tuple[int,float,float,Optional[float]]]] = {}
    for r in rows:
        sid = r.get("shape_id")
        if not sid:
            continue
        seq = int(r["shape_pt_sequence"])
        lat = float(r["shape_pt_lat"]); lon = float(r["shape_pt_lon"])
        d = r.get("shape_dist_traveled")
        dist = float(d)*(1000.0 if assume_km else 1.0) if d and d.strip()!="" else None
        groups.setdefault(sid, []).append((seq,lat,lon,dist))
    shapes: Dict[str, List[ShapePoint]] = {}
    for sid, items in groups.items():
        items.sort(key=lambda x:x[0])
        pts: List[ShapePoint] = []
        cum = 0.0; prev = None
        for seq,lat,lon,dist in items:
            if dist is None:
                if prev is None: cum = 0.0
                else: cum += haversine_m(prev[1],prev[2],lat,lon)
                pts.append(ShapePoint(seq,lat,lon,cum))
            else:
                pts.append(ShapePoint(seq,lat,lon,dist))
            prev = (seq,lat,lon)
        if pts:
            shapes[sid] = pts
    return shapes

def load_routes(gtfs_dir: str) -> Dict[str, Dict[str,str]]:
    path = os.path.join(gtfs_dir, "routes.txt")
    if not os.path.exists(path):
        raise FileNotFoundError("routes.txt が見つかりません")
    return {r["route_id"]:r for r in read_csv_dict(path)}

def load_trips(gtfs_dir: str, route_filter: Optional[List[str]]) -> List[Dict[str,str]]:
    trips = read_csv_dict(os.path.join(gtfs_dir, "trips.txt"))
    if route_filter:
        trips = [t for t in trips if t["route_id"] in route_filter]
    return trips

def load_stops(gtfs_dir: str) -> Dict[str, Dict[str,str]]:
    return {r["stop_id"]:r for r in read_csv_dict(os.path.join(gtfs_dir, "stops.txt"))}

def load_stop_times_for_trip(gtfs_dir: str, trip_id: str) -> List[Dict[str,str]]:
    rows = read_csv_dict(os.path.join(gtfs_dir, "stop_times.txt"))
    rows = [r for r in rows if r["trip_id"] == trip_id]
    rows.sort(key=lambda r:int(r["stop_sequence"]))
    return rows

# ============ 形状補間 & 停留所からのフォールバック ============

def coord_on_shape_at_distance(shape: List[ShapePoint], target_m: float) -> Tuple[float,float]:
    d = [p.dist_m for p in shape]
    c = [(p.lat,p.lon) for p in shape]
    if not d:
        return (c[0][0], c[0][1])
    if target_m <= d[0]: return c[0]
    if target_m >= d[-1]: return c[-1]
    lo, hi = 0, len(d)-1
    while lo <= hi:
        mid = (lo+hi)//2
        if d[mid] < target_m: lo = mid+1
        else: hi = mid-1
    i = max(1, lo)
    d0,d1 = d[i-1], d[i]
    (lat0,lon0),(lat1,lon1) = c[i-1], c[i]
    t = 0.0 if d1==d0 else (target_m - d0) / (d1 - d0)
    return (lat0 + (lat1-lat0)*t, lon0 + (lon1-lon0)*t)

def nearest_shape_distance_for_stop(shape: List[ShapePoint], stop_lat: float, stop_lon: float) -> float:
    best = float("inf"); best_dist = shape[0].dist_m
    for i in range(1, len(shape)):
        a,b = shape[i-1], shape[i]
        ax,ay = a.lon,a.lat; bx,by = b.lon,b.lat; px,py = stop_lon,stop_lat
        abx,aby = (bx-ax),(by-ay); ab2 = abx*abx+aby*aby
        t = 0.0 if ab2==0 else ((px-ax)*abx + (py-ay)*aby)/ab2
        t = max(0.0, min(1.0, t))
        qx,qy = ax+abx*t, ay+aby*t
        d = haversine_m(py,px,qy,qx)
        if d < best:
            best = d
            best_dist = a.dist_m + (b.dist_m - a.dist_m)*t
    return best_dist

def build_shape_from_stops(stop_times: List[Dict[str,str]], stops_by_id: Dict[str,Dict[str,str]]) -> List[ShapePoint]:
    """停留所順（stop_sequence）に折れ線化し、累積距離を付与して擬似shapeを生成"""
    pts: List[ShapePoint] = []
    cum = 0.0
    prev = None
    for r in sorted(stop_times, key=lambda r: int(r["stop_sequence"])):
        st = stops_by_id.get(r["stop_id"])
        if not st:
            continue
        lat = float(st["stop_lat"]); lon = float(st["stop_lon"])
        if prev:
            cum += haversine_m(prev[0], prev[1], lat, lon)
        pts.append(ShapePoint(seq=int(r["stop_sequence"]), lat=lat, lon=lon, dist_m=cum))
        prev = (lat, lon)
    return pts

# ============ サンプル生成（時刻表→位置） ============

def build_samples(shape: List[ShapePoint], stop_times: List[Dict[str,str]],
                  stops_by_id: Dict[str,Dict[str,str]], service_day: date, tz: ZoneInfo,
                  sample_every_m: float, height_m: float,
                  prefer_shape_dist=True, debug=False) -> List[Tuple[datetime,float,float,float]]:
    if not shape:
        return []
    dmax = shape[-1].dist_m
    if dmax <= 0.0:
        return []

    # 時刻・距離のキーフレーム
    kps: List[Tuple[datetime,float]] = []
    for r in stop_times:
        arr = r.get("arrival_time") or r.get("departure_time")
        if not arr or arr.strip()=="":
            continue
        hh,mm,ss = parse_gtfs_time_hhmmss(arr)
        t_local = to_datetime_on_service_day(hh,mm,ss,service_day,tz)

        sd = None
        if prefer_shape_dist:
            sdt = r.get("shape_dist_traveled")
            if sdt and sdt.strip()!="":
                sd = float(sdt)
        if sd is None:
            st = stops_by_id.get(r["stop_id"])
            if st and st.get("stop_lat") and st.get("stop_lon"):
                sd = nearest_shape_distance_for_stop(shape, float(st["stop_lat"]), float(st["stop_lon"]))
            else:
                idx = int(r["stop_sequence"])
                ratio = (idx-1)/max(1,(len(stop_times)-1))
                sd = dmax*ratio

        sd = max(0.0, min(dmax, sd))
        kps.append((t_local, sd))

    if len(kps) < 2:
        # 最低限の直線移動（データ不足時の保険）
        t0 = to_datetime_on_service_day(8,0,0,service_day,tz)
        t1 = to_datetime_on_service_day(9,0,0,service_day,tz)
        kps = [(t0,0.0),(t1,dmax)]

    kps.sort(key=lambda x:x[0])

    samples: List[Tuple[datetime,float,float,float]] = []
    for i in range(1, len(kps)):
        t0,d0 = kps[i-1]; t1,d1 = kps[i]
        if d1 < d0: t0,d0,t1,d1 = t1,d1,t0,d0
        dist = d1-d0; secs = (t1 - t0).total_seconds()
        if dist <= 0 or secs <= 0:
            continue
        step = max(1.0, sample_every_m)
        dd = d0
        while dd < d1:
            ratio = (dd - d0)/dist
            tt = t0 + timedelta(seconds=secs*ratio)
            lat,lon = coord_on_shape_at_distance(shape, dd)
            samples.append((tt, lat, lon, height_m))
            dd += step
        lat1,lon1 = coord_on_shape_at_distance(shape, d1)
        samples.append((t1, lat1, lon1, height_m))

    samples.sort(key=lambda x:x[0])
    if debug:
        print(f"[DEBUG] availability_local={kps[0][0].isoformat()}..{kps[-1][0].isoformat()} "
              f"samples={len(samples)} dmax={dmax:.1f}", file=sys.stderr)
    return samples

# ============ CZML 構築 ============

def rgba(c: Tuple[int,int,int,int]) -> Dict[str,List[int]]:
    r,g,b,a = c; return {"rgba":[r,g,b,a]}

def build_doc(name: str) -> List[dict]:
    return [{
        "id":"document","name":name,"version":"1.0",
        "clock":{"interval":None,"currentTime":None,"multiplier":1,"range":"CLAMPED"}
    }]

def build_route_entity(route_key: str, shape: List[ShapePoint],
                       color: Tuple[int,int,int,int], width: float, clamp: bool) -> dict:
    pos=[]
    for p in shape: pos += [p.lon, p.lat, 0.0]
    e = {"id":f"route-{route_key}","name":f"route {route_key}",
         "polyline":{"positions":{"cartographicDegrees":pos},"width":width,
                     "material":{"solidColor":{"color":rgba(color)}}}}
    if clamp: e["polyline"]["clampToGround"] = True
    return e

def position_iso_interleaved(samples: List[Tuple[datetime,float,float,float]]) -> dict:
    arr=[]
    for t,lat,lon,h in samples:
        arr.append(iso_utc(t)); arr += [lon, lat, h]
    return {"cartographicDegrees": arr,
            "interpolationAlgorithm": "LAGRANGE", "interpolationDegree": 1}

def position_epoch_seconds(samples: List[Tuple[datetime,float,float,float]]) -> dict:
    epoch = samples[0][0].astimezone(timezone.utc)
    arr=[]
    for t,lat,lon,h in samples:
        dt = (t.astimezone(timezone.utc) - epoch).total_seconds()
        arr += [dt, lon, lat, h]
    return {"epoch": iso_utc(epoch), "cartographicDegrees": arr,
            "interpolationAlgorithm": "LAGRANGE", "interpolationDegree": 1}

def build_trip_entity(trip_id: str, samples: List[Tuple[datetime,float,float,float]],
                      model_url: Optional[str], model_scale: float, height_ref: Optional[str],
                      with_point: bool, trail_sec: float,
                      pos_encoding: str, model_key: str, no_orientation: bool) -> dict:
    pos = position_epoch_seconds(samples) if pos_encoding=="epoch" else position_iso_interleaved(samples)
    avail = f"{iso_utc(samples[0][0])}/{iso_utc(samples[-1][0])}"

    ent = {
        "id": f"trip-{trip_id}",
        "name": f"trip {trip_id}",
        "availability": avail,
        "show": True,
        "position": pos,
        "path": {"show": True, "leadTime": 0, "trailTime": int(trail_sec), "width": 2}
    }
    if not no_orientation:
        ent["orientation"] = {"velocityReference": "#position"}

    if model_url:
        ent["model"] = {
            (model_key): model_url,   # 'gltf'（既定）または 'uri'
            "scale": model_scale,
            "minimumPixelSize": 48,
            "shadows": "ENABLED"
        }
        if height_ref:
            ent["model"]["heightReference"] = height_ref

    if with_point:
        ent["point"] = {"pixelSize":12,"color":{"rgba":[255,255,255,255]},
                        "outlineWidth":2,"outlineColor":{"rgba":[0,0,0,255]}}
        if height_ref:
            ent["point"]["heightReference"] = height_ref
    return ent

def build_test_pin(t0_utc_str: str, lon: float, lat: float) -> dict:
    return {
        "id":"test-pin","name":"debug pin",
        "availability": f"{t0_utc_str}/{t0_utc_str}",
        "position":{"cartographicDegrees":[lon,lat,0]},
        "billboard":{"image":"https://cdn.jsdelivr.net/gh/AnalyticalGraphicsInc/cesium/Apps/Sandcastle/images/facility.gif",
                     "scale":1.0}
    }

# ============ メイン ============

def main():
    ap = argparse.ArgumentParser(description="GTFS-JP → CZML（モデル走行・gltf既定・運行日判定＆フォールバック制御）")
    ap.add_argument("--gtfs-dir", required=True, help="GTFS ディレクトリ（*.txt 群）")
    ap.add_argument("--output", required=True, help="出力 CZML パス")
    ap.add_argument("--service-date", required=True, help="サービス日 YYYY-MM-DD")
    ap.add_argument("--tz", default="Asia/Tokyo", help="基準タイムゾーン（既定: Asia/Tokyo）")
    ap.add_argument("--route-id", action="append", help="対象 route_id（複数指定可）。未指定は全ルート")
    ap.add_argument("--route-color", default=None, help="#RRGGBB または RRGGBBAA（全ルート一括上書き）")
    ap.add_argument("--line-width", type=float, default=3.0, help="ルート線の太さ")
    ap.add_argument("--line-opacity", type=float, default=1.0, help="ルート線の不透明度(0..1)")
    ap.add_argument("--clamp-to-ground", action="store_true", help="ルート線を地表追従させる")
    ap.add_argument("--model-url", default=None, help="走行モデルの URL（GLB/GLTF）")
    ap.add_argument("--model-scale", type=float, default=1.0, help="モデル拡大率")
    ap.add_argument("--model-height-reference",
                    choices=["NONE","CLAMP_TO_GROUND","RELATIVE_TO_GROUND"], default="RELATIVE_TO_GROUND",
                    help="モデル高さ参照（既定: RELATIVE_TO_GROUND）")
    ap.add_argument("--default-height-m", type=float, default=0.0, help="サンプル高さ[m]（0推奨）")
    ap.add_argument("--sample-every", type=float, default=50.0, help="形状に沿ったサンプル間隔[m]")
    ap.add_argument("--shape-dist-is-km", action="store_true", help="shapes.shape_dist_traveled を km とみなす")
    # 互換・デバッグ系
    ap.add_argument("--model-key", choices=["gltf","uri"], default="gltf",
                    help="CZML の model キー（既定: gltf）")
    ap.add_argument("--position-encoding", choices=["epoch","iso"], default="epoch",
                    help="position の書式（既定: epoch）")
    ap.add_argument("--no-orientation", action="store_true",
                    help="orientation を付加しない（互換切り分け）")
    ap.add_argument("--with-point", action="store_true",
                    help="モデルと同エンティティに Point を同時表示（見失い防止）")
    ap.add_argument("--trail", type=float, default=0.0,
                    help="Path の trailTime 秒（例: 60）")
    ap.add_argument("--test-pin", action="store_true",
                    help="ドキュメント開始時刻にビルボードのテストピンを出す")
    # 運行判定 & フォールバック制御
    ap.add_argument("--ignore-calendar", action="store_true",
                    help="calendar(.txt/.dates) を無視して全 trip を描画する（デバッグ用）")
    ap.add_argument("--fallback-mode", choices=["stops", "none"], default="stops",
                    help="shape が無い trip の扱い: 'stops'=停留所直線で擬似shape生成 / 'none'=skip（描画しない）")
    ap.add_argument("--debug", action="store_true", help="デバッグ出力を有効化")
    args = ap.parse_args()

    if ZoneInfo is None:
        raise RuntimeError("zoneinfo が利用できません（Python 3.9+ を使用してください）")

    tz = ZoneInfo(args.tz)
    service_day = datetime.strptime(args.service_date, "%Y-%m-%d").date()

    routes = load_routes(args.gtfs_dir)
    trips  = load_trips(args.gtfs_dir, args.route_id)
    stops  = load_stops(args.gtfs_dir)
    shapes = load_shapes(args.gtfs_dir, assume_km=args.shape_dist_is_km)  # 空でもOK

    # 運行日フィルタ
    if not args.ignore_calendar:
        active = services_active_on(args.gtfs_dir, service_day)
        trips = [t for t in trips if t.get("service_id") in active]
        if args.debug:
            print(f"[DEBUG] active services={len(active)} trips_after_calendar={len(trips)}", file=sys.stderr)

    czml = build_doc("GTFS-JP runs")
    emitted_route_shapes = set()

    # ルート色上書き（不透明度反映）
    route_color_cli = parse_hex_color(args.route_color) if args.route_color else None
    if route_color_cli and args.line_opacity < 1.0:
        route_color_cli = with_opacity(route_color_cli, args.line_opacity)

    doc_start: Optional[datetime] = None
    doc_end:   Optional[datetime] = None
    first_lonlat: Optional[Tuple[float,float]] = None

    for t in trips:
        trip_id  = t["trip_id"]
        route_id = t["route_id"]
        shape_id = t.get("shape_id")

        st = load_stop_times_for_trip(args.gtfs_dir, trip_id)
        if not st:
            if args.debug: print(f"[WARN] trip {trip_id} stop_times なし -> skip", file=sys.stderr)
            continue

        # shape 決定：1) shapes.txt に存在 2) 停留所から擬似生成 or skip
        if shape_id and shape_id in shapes and len(shapes[shape_id]) >= 2:
            use_shape = shapes[shape_id]
            route_shape_key = f"{route_id}:{shape_id}"
            if args.debug: print(f"[INFO] trip {trip_id} shape_id={shape_id} を使用", file=sys.stderr)
        else:
            if args.fallback_mode == "none":
                if args.debug: print(f"[WARN] trip {trip_id} shape なし -> skip（--fallback-mode none）", file=sys.stderr)
                continue
            use_shape = build_shape_from_stops(st, stops)
            if len(use_shape) < 2:
                if args.debug: print(f"[WARN] trip {trip_id} 擬似shape点不足 -> skip", file=sys.stderr)
                continue
            route_shape_key = f"{route_id}:pseudo-{trip_id}"
            if args.debug: print(f"[INFO] trip {trip_id} shapesなし -> 停留所から擬似shape生成({len(use_shape)}点)", file=sys.stderr)

        if first_lonlat is None:
            first_lonlat = (use_shape[0].lon, use_shape[0].lat)

        # ルート線（route_id × shape 単位で1回だけ描画）
        if route_shape_key not in emitted_route_shapes:
            color = route_color_cli or parse_hex_color(routes.get(route_id, {}).get("route_color"))
            if args.line_opacity < 1.0:
                color = with_opacity(color, args.line_opacity)
            czml.append(build_route_entity(route_shape_key, use_shape, color, args.line_width, args.clamp_to_ground))
            emitted_route_shapes.add(route_shape_key)

        # サンプル生成
        samples = build_samples(use_shape, st, stops, service_day, tz,
                                sample_every_m=args.sample_every,
                                height_m=args.default_height_m,
                                prefer_shape_dist=True, debug=args.debug)
        if len(samples) < 2:
            if args.debug: print(f"[WARN] trip {trip_id} samples < 2 -> skip", file=sys.stderr)
            continue

        # ドキュメントの再生範囲
        if doc_start is None or samples[0][0] < doc_start: doc_start = samples[0][0]
        if doc_end   is None or samples[-1][0] > doc_end: doc_end   = samples[-1][0]

        # モデル高さ参照
        height_ref = None if args.model_height_reference == "NONE" else args.model_height_reference

        # エンティティ
        ent = build_trip_entity(
            trip_id, samples, args.model_url, args.model_scale, height_ref,
            with_point=args.with_point, trail_sec=args.trail,
            pos_encoding=args.position_encoding, model_key=args.model_key,
            no_orientation=args.no_orientation
        )
        ent["properties"] = {
            "route_id": {"string": route_id},
            "shape_id": {"string": shape_id or ""},
            "service_date": {"string": service_day.isoformat()},
            "shape_fallback": {"boolean": not (shape_id and shape_id in shapes)}
        }
        czml.append(ent)

        if args.debug:
            print(f"[DEBUG] trip={trip_id} avail_utc={iso_utc(samples[0][0])}..{iso_utc(samples[-1][0])} "
                  f"samples={len(samples)}", file=sys.stderr)

    # clock 範囲とオプションのテストピン
    if doc_start and doc_end:
        czml[0]["clock"]["interval"]    = f"{iso_utc(doc_start)}/{iso_utc(doc_end)}"
        czml[0]["clock"]["currentTime"] = iso_utc(doc_start)
        czml[0]["clock"]["multiplier"]  = 1
        if args.test_pin and first_lonlat:
            czml.append(build_test_pin(iso_utc(doc_start), first_lonlat[0], first_lonlat[1]))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(czml, f, ensure_ascii=False, indent=2)
    print(f"Wrote: {args.output}")

if __name__ == "__main__":
    main()
