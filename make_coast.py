"""Natural Earth 50m 국가 경계(GeoJSON)를 동아시아 범위로 잘라 coast.json 으로 만든다.
사용: python make_coast.py <ne_50m_admin_0_countries.geojson>
태풍 지도 배경용. 아티팩트 CSP 가 외부 타일을 막아서 해안선을 직접 그린다."""
import json, sys, math, os

LON0, LON1, LAT0, LAT1 = 90, 175, -8, 62     # 베트남·남중국해 ~ 러시아 극동 ~ 서태평양
PAD = 3
TOL = 0.025                                  # 단순화 허용 오차(도)


def inside(p):
    return LON0 - PAD <= p[0] <= LON1 + PAD and LAT0 - PAD <= p[1] <= LAT1 + PAD


def _dp(pts, a, b, keep):
    ax, ay = pts[a]; bx, by = pts[b]
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    imax, dmax = -1, 0.0
    for i in range(a + 1, b):
        px, py = pts[i]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / L if L > 1e-12 else math.hypot(px - ax, py - ay)
        if d > dmax:
            imax, dmax = i, d
    if dmax > TOL:
        keep[imax] = True
        _dp(pts, a, imax, keep); _dp(pts, imax, b, keep)


def simplify(ring):
    if len(ring) < 4:
        return ring
    keep = [False] * len(ring); keep[0] = keep[-1] = True
    # 닫힌 고리는 시작점과 끝점이 같아 기준선이 0 길이가 된다. 시작점에서 가장 먼 점을 먼저 고정한다.
    far = max(range(len(ring)), key=lambda i: math.hypot(ring[i][0] - ring[0][0], ring[i][1] - ring[0][1]))
    keep[far] = True
    sys.setrecursionlimit(10000)
    _dp(ring, 0, far, keep); _dp(ring, far, len(ring) - 1, keep)
    return [p for p, k in zip(ring, keep) if k]


def clip_ring(ring):
    # 범위 밖의 긴 구간은 건너뛴다(화면 밖이라 보이지 않음). 경계 양옆 점은 남겨 직선으로 잇는다.
    n = len(ring); out = []
    for i, p in enumerate(ring):
        if inside(p) or (i and inside(ring[i - 1])) or (i + 1 < n and inside(ring[i + 1])):
            out.append(p)
    return out


def main(src):
    gj = json.load(open(src, encoding="utf-8"))
    feats, total = [], 0
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        rings = []
        for poly in polys:
            outer = poly[0]
            if not any(inside(p) for p in outer):
                continue
            r = simplify(clip_ring(outer))
            if len(r) >= 4:
                rings.append([[round(p[0], 2), round(p[1], 2)] for p in r])
        if rings:
            feats.append({"n": f["properties"].get("NAME") or f["properties"].get("ADMIN"), "r": rings})
            total += sum(len(r) for r in rings)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coast.json")
    json.dump({"bbox": [LON0, LON1, LAT0, LAT1], "countries": feats}, open(out, "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)
    print(f"countries {len(feats)} · points {total} · {os.path.getsize(out)//1024} KB")
    for c in sorted(feats, key=lambda c: -sum(len(r) for r in c["r"]))[:8]:
        print("  ", c["n"], sum(len(r) for r in c["r"]))


if __name__ == "__main__":
    main(sys.argv[1])
