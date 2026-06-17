# GTFS-JP → CZML 変換ツール

GTFS-JP の ZIP ファイルをブラウザ上で処理し、Cesium 用 CZML と停留所 GeoJSON を生成するクライアントサイドツールです。サーバー不要、ファイルのアップロードは一切行いません。

## 出力ファイル

| ファイル | 形式 | 内容 |
|---|---|---|
| `*.czml` | CZML | ルート線ポリライン＋時系列移動点アニメーション |
| `stops.geojson` | GeoJSON | 停留所ポイント（`marker-color` / `marker-symbol` / `marker-size` プロパティ付き） |

## 使い方

### 1. ZIPファイルを選択

GTFS-JP 形式の ZIP ファイルをドラッグ＆ドロップするか、クリックして選択します。  
以下のファイルが含まれていることを確認してください。

| ファイル | 必須 | 備考 |
|---|---|---|
| `routes.txt` | ✅ | |
| `trips.txt` | ✅ | |
| `stop_times.txt` | ✅ | |
| `stops.txt` | ✅ | |
| `shapes.txt` | ⚠️ 推奨 | ない場合は停留所直線で代替 |
| `calendar.txt` | ⚠️ 推奨 | なければ全便対象 |
| `calendar_dates.txt` | 任意 | |

### 2. 設定

- **サービス日**（必須）: アニメーション対象の運行日を選択します
- **対象路線**: 特定路線に絞り込む場合は選択（省略で全路線）
- **路線色**: 路線ごとに色をカスタマイズできます
- **停留所マーカー**: CZML の点サイズ・色と GeoJSON の Maki シンボルを設定します
- **詳細オプション**: 3D モデル URL、ライン幅・不透明度、トレイル秒数など

### 3. 変換・ダウンロード

「変換する」ボタンを押すと変換が始まります。完了後、CZML と GeoJSON をそれぞれダウンロードします。

---

## Cesium での読み込み方

### CZML の読み込み

```javascript
viewer.dataSources.add(Cesium.CzmlDataSource.load('your_output.czml'));
```

走行アニメーションの時間軸は CZML に埋め込まれています。`viewer.clock.shouldAnimate = true` で再生が始まります。

### GeoJSON（停留所）の読み込み

停留所を地表面に正しく配置するには、`clampToGround: true` が必要です。

```javascript
viewer.dataSources.add(
  Cesium.GeoJsonDataSource.load('stops.geojson', {
    clampToGround: true   // ← 必須。省略すると地中に埋まります
  })
);
```

> **注意**: Cesium は GeoJSON の `marker-color` / `marker-symbol` / `marker-size` プロパティを自動的に解釈しますが、`clampToGround` はデフォルト `false` のため、必ず明示してください。

---

## GitHub Pages へのデプロイ

1. このリポジトリをフォーク（または `gtfs-to-czml/` フォルダを含むリポジトリを作成）します
2. リポジトリの **Settings → Pages** を開きます
3. **Source** を `Deploy from a branch` に設定し、ブランチ `main`（または `master`）、フォルダ `/` または `/gtfs-to-czml` を指定します
4. 数分後に `https://<username>.github.io/<repo>/` でアクセスできます

> ライブラリは `lib/` フォルダにバンドルされているため、CDN へのアクセスは不要です。

## 技術スタック

- [JSZip](https://stuk.github.io/jszip/) — ブラウザ内 ZIP 展開
- [PapaParse](https://www.papaparse.com/) — CSV ストリーミングパース
- Cesium（別途用意）— CZML / GeoJSON の可視化

## ライセンス

MIT
