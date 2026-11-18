# -*- coding: utf-8 -*-
"""태풍 발생·경로 수집.

여러 기관 자료를 합쳐 하나의 목록으로 만든다. 화면(index.html 태풍 탭)이 지도에 그린다.
  - IBTrACS ACTIVE (NOAA NCEI): 현재/최근 활동 열대저기압의 위치 이력. JTWC·JMA 분석값을 한 파일로 준다.
  - JTWC (미 합동태풍경보센터): RSS → 경보문(web.txt)에서 현재 위치와 12~120시간 예보 위치.
  - JMA (일본 기상청 RSMC 도쿄): targetTc.json 에 현재 열대저기압 목록. 구조가 공개 문서화돼
    있지 않아 방어적으로 읽고, 실패해도 다른 소스는 유지한다.
반환 형식:
  {"storms": [{id, name, name_ko, basin, status, category, lat, lon, wind_kt, pres_hpa, move_dir, move_kt,
               updated, track: [{t, lat, lon, wind, pres, cat}], forecast: [{t, lat, lon, wind, tau}],
               sources: [...], links: {...}}],
   "fetched_at": "...", "errors": [...], "links": {...}}
"""
import csv
import io
import re
from datetime import datetime, timezone

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BerthMonitor/1.0"}
TIMEOUT = 20

IBTRACS_ACTIVE = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
                  "v04r01/access/csv/ibtracs.ACTIVE.list.v04r01.csv")
JTWC_RSS = "https://www.metoc.navy.mil/jtwc/rss/jtwc.rss"
JTWC_PRODUCTS = "https://www.metoc.navy.mil/jtwc/products/"
JMA_TARGET = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"

# 관심 영역: 베트남~러시아 극동, 서태평양. 이 밖(인도양·동태평양·남반구)은 버린다.
LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 95.0, 180.0, 0.0, 60.0
RECENT_DAYS = 10            # 마지막 위치가 이보다 오래된 저기압은 목록에서 뺀다

# 참고 사이트 (화면의 바로가기)
LINKS = {
    "기상청 태풍": "https://www.weather.go.kr/w/typhoon/typ-cur.do",
    "JMA RSMC 도쿄": "https://www.jma.go.jp/bosai/map.html#contents=typhoon",
    "JTWC": "https://www.metoc.navy.mil/jtwc/jtwc.html",
    "Digital Typhoon": "https://agora.ex.nii.ac.jp/digital-typhoon/",
    "Zoom Earth": "https://zoom.earth/",
    "Windy": "https://www.windy.com/?126.0,35.0,5",
}


def _f(s):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def _in_area(lat, lon):
    return lat is not None and lon is not None and LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def category_of(wind_kt):
    """최대풍속(kt, 10분 평균 기준)으로 등급. 기상청/JMA 기준에 가깝게."""
    if wind_kt is None:
        return "TD"
    if wind_kt < 34:
        return "TD"       # 열대저압부
    if wind_kt < 48:
        return "TS"       # 열대폭풍
    if wind_kt < 64:
        return "STS"      # 강한 열대폭풍
    if wind_kt < 85:
        return "TY"       # 태풍(중)
    if wind_kt < 105:
        return "STY"      # 강
    return "VTY"          # 매우 강/초강력


# ----------------------------------------------------------------- IBTrACS

def fetch_ibtracs():
    r = requests.get(IBTRACS_ACTIVE, headers=HEADERS, timeout=40)
    r.raise_for_status()
    rows = list(csv.reader(io.StringIO(r.text)))
    hdr = rows[0]
    ix = {h: i for i, h in enumerate(hdr)}
    storms = {}
    for row in rows[2:]:
        if len(row) < len(hdr) - 5:
            continue
        lat, lon = _f(row[ix["LAT"]]), _f(row[ix["LON"]])
        if lon is not None and lon < 0:
            lon += 360
        if not _in_area(lat, lon):
            continue
        sid = row[ix["SID"]]
        name = row[ix["NAME"]].strip() or "NOT_NAMED"
        wind = _f(row[ix["WMO_WIND"]]) or _f(row[ix["TOKYO_WIND"]]) or _f(row[ix["USA_WIND"]])
        pres = _f(row[ix["WMO_PRES"]]) or _f(row[ix["TOKYO_PRES"]]) or _f(row[ix["USA_PRES"]])
        atcf = row[ix["USA_ATCF_ID"]].strip() if "USA_ATCF_ID" in ix else ""
        t = row[ix["ISO_TIME"]].strip()
        s = storms.setdefault(sid, {
            "id": sid, "name": name, "basin": row[ix["BASIN"]], "atcf": atcf, "track": [],
            "number": row[ix["NUMBER"]],
        })
        s["track"].append({
            "t": t.replace(" ", "T"), "lat": lat, "lon": lon, "wind": wind, "pres": pres,
            "cat": category_of(wind), "nature": row[ix["NATURE"]].strip(),
            "spd": _f(row[ix["STORM_SPEED"]]), "dir": _f(row[ix["STORM_DIR"]]),
        })
    return list(storms.values())


# ------------------------------------------------------------------- JTWC

_POS_RE = re.compile(r"(\d{6})Z\s*---\s*(?:NEAR\s*)?(\d+\.\d)([NS])\s+(\d+\.\d)([EW])")
_WIND_RE = re.compile(r"MAX SUSTAINED WINDS\s*-\s*(\d+)\s*KT")
_TAU_RE = re.compile(r"(\d+)\s*HRS?,\s*VALID AT:")
_MOVE_RE = re.compile(r"MOVEMENT PAST SIX HOURS\s*-\s*(\d+)\s*DEGREES AT\s*(\d+)\s*KTS")
_HEAD_RE = re.compile(r"(?:TROPICAL (?:CYCLONE|STORM|DEPRESSION)|SUPER TYPHOON|TYPHOON)\s+(\d{2}[A-Z])\s*"
                      r"\(([A-Z0-9\- ]+)\)\s*WARNING NR\s*(\d+)")


def _ddhhmm_to_iso(ddhhmm, ref=None):
    """'171800' (일/시/분, UTC) → ISO. 월은 기준 시각(UTC)에서 가장 가까운 것으로 추정한다."""
    ref = ref or datetime.now(timezone.utc)
    dd, hh, mm = int(ddhhmm[:2]), int(ddhhmm[2:4]), int(ddhhmm[4:6])
    cands = []
    for k in (-1, 0, 1):
        y, m = ref.year, ref.month + k
        if m < 1:
            y, m = y - 1, 12
        if m > 12:
            y, m = y + 1, 1
        try:
            cands.append(datetime(y, m, dd, hh, mm, tzinfo=timezone.utc))
        except ValueError:
            pass
    best = min(cands, key=lambda d: abs((d - ref).total_seconds()))
    return best.strftime("%Y-%m-%dT%H:%M:%S")


def parse_jtwc_warning(text):
    """JTWC 경보문(web.txt)에서 현재 위치와 예보 위치를 뽑는다."""
    out = {"forecast": []}
    m = _HEAD_RE.search(text)
    if m:
        out["jtwc_id"], out["name"], out["warning_no"] = m.group(1), m.group(2).strip(), int(m.group(3))
    # 경보 위치 블록과 예보 블록들이 '---' 줄로 나뉘어 있다
    for b in re.split(r"\n\s*---\s*\n", text):
        pm = _POS_RE.search(b)
        if not pm:
            continue
        t = _ddhhmm_to_iso(pm.group(1))
        lat = float(pm.group(2)) * (1 if pm.group(3) == "N" else -1)
        lon = float(pm.group(4)) * (1 if pm.group(5) == "E" else -1)
        if lon < 0:
            lon += 360
        wm = _WIND_RE.search(b)
        wind = int(wm.group(1)) if wm else None
        tau = _TAU_RE.search(b)
        if "WARNING POSITION" in b:
            out.update(lat=lat, lon=lon, wind_kt=wind, t=t)
            mv = _MOVE_RE.search(b)
            if mv:
                out["move_dir"], out["move_kt"] = int(mv.group(1)), int(mv.group(2))
        elif tau:
            out["forecast"].append({"t": t, "lat": lat, "lon": lon, "wind": wind, "tau": int(tau.group(1)),
                                    "cat": category_of(wind * 0.88 if wind else None)})   # 1분→10분 평균 근사
    return out


def fetch_jtwc():
    r = requests.get(JTWC_RSS, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"<item>.*?Northwest Pacific.*?</item>", r.text, re.S)
    if not m:
        return []
    # 북서태평양 항목 안의 경보문 링크(wpNNYYweb.txt)만
    ids = sorted(set(re.findall(r"products/(wp\d{4})web\.txt", m.group(0))))
    storms = []
    for wid in ids:
        try:
            t = requests.get(f"{JTWC_PRODUCTS}{wid}web.txt", headers=HEADERS, timeout=TIMEOUT)
            t.raise_for_status()
            p = parse_jtwc_warning(t.text)
            p["wid"] = wid
            p["links"] = {"JTWC 경보문": f"{JTWC_PRODUCTS}{wid}web.txt",
                          "JTWC 경로도": f"{JTWC_PRODUCTS}{wid}.gif"}
            storms.append(p)
        except Exception as e:  # 한 건 실패해도 나머지 계속
            storms.append({"wid": wid, "error": str(e)})
    return storms


# -------------------------------------------------------------------- JMA

def fetch_jma():
    """JMA 현재 열대저기압 목록. 구조를 모르면 이름/번호만이라도 돌려준다."""
    r = requests.get(JMA_TARGET, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for tc in data if isinstance(data, list) else []:
        if not isinstance(tc, dict):
            continue
        out.append({
            "tc": tc.get("tropicalCyclone"), "number": tc.get("typhoonNumber"),
            "name": (tc.get("name") or tc.get("nameEn") or "").upper() or None,
            "category": tc.get("category"),
        })
    return out


# ------------------------------------------------------------------- merge

def _match_key(atcf, wid):
    """IBTrACS 의 USA_ATCF_ID 'WP272026' 과 JTWC 의 'wp2726' 을 같은 열쇠로."""
    if atcf and len(atcf) >= 8:
        return (atcf[:2].lower(), atcf[2:4], atcf[-2:])    # ('wp','27','26')
    if wid and len(wid) == 6:
        return (wid[:2].lower(), wid[2:4], wid[4:6])
    return None


def fetch_typhoons():
    errors = []
    ib, jt, jm = [], [], []
    try:
        ib = fetch_ibtracs()
    except Exception as e:
        errors.append(f"IBTrACS: {e}")
    try:
        jt = fetch_jtwc()
    except Exception as e:
        errors.append(f"JTWC: {e}")
    try:
        jm = fetch_jma()
    except Exception as e:
        errors.append(f"JMA: {e}")

    now = datetime.now(timezone.utc)
    storms = {}
    for s in ib:
        key = _match_key(s.get("atcf"), None) or ("ib", s["id"])
        s["track"].sort(key=lambda p: p["t"])
        last = s["track"][-1]
        age_h = (now - datetime.fromisoformat(last["t"]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
        storms[key] = {
            "id": s["id"], "name": s["name"], "basin": s["basin"], "track": s["track"], "forecast": [],
            "lat": last["lat"], "lon": last["lon"], "wind_kt": last["wind"], "pres_hpa": last["pres"],
            "category": last["cat"], "move_dir": last.get("dir"), "move_kt": last.get("spd"),
            "updated": last["t"], "age_h": round(age_h, 1), "sources": ["IBTrACS"], "links": {},
            "status": "active" if age_h <= 30 else ("recent" if age_h <= RECENT_DAYS * 24 else "old"),
        }
    for p in jt:
        if "error" in p:
            errors.append(f"JTWC {p['wid']}: {p['error']}")
            continue
        key = _match_key(None, p["wid"])
        s = storms.get(key)
        if not s:
            if not _in_area(p.get("lat"), p.get("lon")):
                continue
            s = storms[key] = {"id": p["wid"], "name": p.get("name") or p["wid"].upper(), "basin": "WP",
                               "track": [], "forecast": [], "sources": [], "links": {}, "status": "active",
                               "wind_kt": None, "pres_hpa": None, "category": None, "updated": None}
        s["sources"].append("JTWC")
        s["links"].update(p.get("links", {}))
        s["forecast"] = p.get("forecast", [])
        s["jtwc_warning_no"] = p.get("warning_no")
        if p.get("lat") is not None and (not s.get("updated") or p["t"] > s["updated"]):
            # JTWC 경보 위치가 IBTrACS 마지막 위치보다 새로우면 현재 위치로 쓴다
            s.update(lat=p["lat"], lon=p["lon"], updated=p["t"], status="active",
                     move_dir=p.get("move_dir"), move_kt=p.get("move_kt"))
            if p.get("wind_kt"):
                s["jtwc_wind_kt"] = p["wind_kt"]
                s["wind_kt"] = round(p["wind_kt"] * 0.88)      # 1분 평균 → 10분 평균 근사
                s["category"] = category_of(s["wind_kt"])
    for j in jm:
        # JMA 는 이름으로만 맞춘다(번호 체계가 다름). 못 맞추면 목록에만 올린다.
        hit = next((s for s in storms.values() if j.get("name") and s["name"] == j["name"]), None)
        if hit:
            hit["sources"].append("JMA")
            hit["jma_number"] = j.get("number")
            hit["links"]["JMA 정보"] = LINKS["JMA RSMC 도쿄"]
        else:
            storms[("jma", j.get("tc") or j.get("name"))] = {
                "id": j.get("tc") or "", "name": j.get("name") or f"TD {j.get('number') or ''}".strip(),
                "basin": "WP", "track": [], "forecast": [], "lat": None, "lon": None, "wind_kt": None,
                "pres_hpa": None, "category": "TD", "updated": None, "sources": ["JMA"],
                "links": {"JMA 정보": LINKS["JMA RSMC 도쿄"]}, "status": "active", "jma_number": j.get("number"),
            }

    order = {"active": 0, "recent": 1, "old": 2}
    result = [s for s in storms.values() if s["status"] != "old"]
    result.sort(key=lambda s: (order[s["status"]], s.get("updated") or ""))
    for s in result:
        s["sources"] = sorted(set(s["sources"]))
        s["name_ko"] = KO_NAMES.get(s["name"], "")
    return {"storms": result, "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "errors": errors, "links": LINKS,
            "counts": {"ibtracs": len(ib), "jtwc": len([p for p in jt if "error" not in p]), "jma": len(jm)}}


# 태풍위원회 140개 이름의 한글 표기 (기상청 표기)
KO_NAMES = {
    "DAMREY": "담레이", "HAIKUI": "하이쿠이", "KIRONGI": "기러기", "YUN-YEUNG": "윈욍", "KOINU": "고이누",
    "BOLAVEN": "볼라벤", "SANBA": "산바", "JELAWAT": "즐라왓", "EWINIAR": "에위니아", "MALIKSI": "말릭시",
    "GAEMI": "개미", "PRAPIROON": "프라피룬", "MARIA": "마리아", "SON-TINH": "선띤", "AMPIL": "암필",
    "WUKONG": "우쿵", "JONGDARI": "종다리", "SHANSHAN": "산산", "YAGI": "야기", "LEEPI": "리피",
    "BEBINCA": "버빙카", "PULASAN": "풀라산", "SOULIK": "솔릭", "CIMARON": "시마론", "JEBI": "제비",
    "KRATHON": "끄라톤", "BARIJAT": "바리자트", "TRAMI": "짜미", "KONG-REY": "콩레이", "YINXING": "인싱",
    "TORAJI": "도라지", "MAN-YI": "마니", "USAGI": "우사기", "PABUK": "파북", "WUTIP": "우딥",
    "SEPAT": "스팟", "MUN": "문", "DANAS": "다나스", "NARI": "나리", "WIPHA": "위파", "FRANCISCO": "프란시스코",
    "CO-MAY": "꼬마이", "KROSA": "크로사", "BAILU": "바이루", "PODUL": "버들", "LINGLING": "링링",
    "KAJIKI": "가지키", "NONGFA": "농파", "PEIPAH": "페이파", "TAPAH": "다파", "MITAG": "미탁",
    "RAGASA": "라가사", "NEOGURI": "너구리", "BUALOI": "부알로이", "MATMO": "맛모", "HALONG": "할롱",
    "NAKRI": "나크리", "FENGSHEN": "펑선", "KALMAEGI": "갈매기", "FUNG-WONG": "풍웡", "KOTO": "고토",
    "NOKAEN": "노카엔", "PENHA": "페냐", "NURI": "누리", "SINLAKU": "실라코", "HAGUPIT": "하구핏",
    "JANGMI": "장미", "MEKKHALA": "메칼라", "HIGOS": "히고스", "BAVI": "바비", "MAYSAK": "마이삭",
    "HAISHEN": "하이선", "NOUL": "노을", "DOLPHIN": "돌핀", "KUJIRA": "구지라", "CHAN-HOM": "찬홈",
    "PEILOU": "페이러우", "NANGKA": "낭카", "SAUDEL": "사우델", "NARRA": "나라", "GAENARI": "개나리",
    "ATSANI": "아타우", "ETAU": "에따우", "BANG-LANG": "방랑", "KROVANH": "크로반", "DUJUAN": "두쥐안",
    "SURIGAE": "수리개", "CHOI-WAN": "초이완", "KOGUMA": "고구마", "CHAMPI": "참피", "IN-FA": "인파",
    "CEMPAKA": "쯤빠까", "NEPARTAK": "네파탁", "LUPIT": "루핏", "MIRINAE": "미리내", "NIDA": "니다",
    "OMAIS": "오마이스", "CONSON": "꼰선", "CHANTHU": "찬투", "DIANMU": "뎬무", "MINDULLE": "민들레",
    "LIONROCK": "라이언록", "KOMPASU": "곤파스", "NAMTHEUN": "남테운", "MALOU": "말로", "NYATOH": "냐토",
    "RAI": "라이", "MALAKAS": "말라카스", "MEGI": "메기", "CHABA": "차바", "AERE": "에어리",
    "SONGDA": "송다", "TRASES": "뜨라세", "MULAN": "무란", "MEARI": "메아리", "MA-ON": "망온",
    "TOKAGE": "도카게", "HINNAMNOR": "힌남노", "MUIFA": "무이파", "MERBOK": "므르복", "NANMADOL": "난마돌",
    "TALAS": "탈라스", "NORU": "노루", "KULAP": "꿀랍", "ROKE": "로키", "SONCA": "선까", "NESAT": "네삿",
    "HAITANG": "하이탕", "NALGAE": "날개", "BANYAN": "바냔", "YAMANEKO": "야마네코", "PAKHAR": "파카르",
    "SANVU": "산우", "MAWAR": "마와르", "GUCHOL": "구촐", "TALIM": "탈림", "DOKSURI": "독수리",
    "KHANUN": "카눈", "LAN": "란", "SAOLA": "사올라",
}


if __name__ == "__main__":
    import json
    d = fetch_typhoons()
    print(json.dumps({k: v for k, v in d.items() if k != "storms"}, ensure_ascii=False, indent=1))
    for s in d["storms"]:
        print(s["name"], s["name_ko"], s["status"], s["category"], s["lat"], s["lon"], s["wind_kt"], s["pres_hpa"],
              "track", len(s["track"]), "fcst", len(s["forecast"]), s["sources"], s["updated"])
