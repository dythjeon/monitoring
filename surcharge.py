# -*- coding: utf-8 -*-
"""기본 부대비용(Basic Surcharge) 조회.

자사 e-service(ebiz.pcsline.co.kr) 의 [운임/요율 > 기본 부대비용 조회] 화면(UIE0825)이 쓰는 것과 같은 주소를 부른다.
로그인 없이 열리는 화면이라 계정은 필요 없다.

  fetch_locations()                 -> Select Location 팝업 목록 (나라 + 항구)
  fetch_surcharge(date, por, ...)   -> 조회 결과 원본 두 묶음(col1 = 컨테이너 타입별, col2 = WHARFAGE 등)

Prepaid/Collect 로 나누고 타입(DC/RF/..)에 맞는 열을 고르는 일은 e-service 화면과 똑같이 index.html 이 한다.
"""
import re
import threading

import requests

BASE = "https://ebiz.pcsline.co.kr"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
BUKRS = "2000"          # 회사코드 (DY)
TYPES = "DROFTG"        # DC RF OT FR TC DG

_lock = threading.Lock()
_sess = None


def _session(fresh=False):
    """지역 목록(/main/hom/init)은 첫 화면을 한 번 열어 세션 쿠키를 받은 뒤에만 응답한다."""
    global _sess
    with _lock:
        if _sess is None or fresh:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get(BASE + "/", timeout=20).raise_for_status()
            _sess = s
        return _sess


def _post(path, body):
    resp = None
    for attempt in (0, 1):          # 세션이 만료됐으면 새로 받아 한 번만 더
        resp = _session(fresh=attempt == 1).post(BASE + path, json=body, timeout=20)
        if resp.status_code == 200:
            return resp.json()
    resp.raise_for_status()
    raise RuntimeError(f"e-service 응답 {resp.status_code}")


def fetch_locations():
    """반환: {"nations": [[나라코드, 나라이름], ...], "locs": [[항구코드, "BUSAN, KOREA (KRPUS)", 나라코드], ...]}"""
    j = _post("/main/hom/init", {"dma_search": {"param": ""}})
    locs = [[d["LOCCOD"], d["LOCDES"], d.get("LOCNAT") or ""] for d in j.get("dlt_codaloca", []) if d.get("LOCCOD")]
    if not locs:
        raise RuntimeError("지역 목록이 비어 있습니다")
    return {"nations": [[d["NATCOD"], d["NATDES"]] for d in j.get("dlt_codaznat", [])], "locs": locs}


_KEEP = ["surknd", "surcod", "surpcg", "wfgcur", "wfglc", "wfg20", "wfg40", "wfg4h"] + \
        [p + s for p in ("dc", "rf", "ot", "of", "tc", "dg") for s in ("20", "40", "4h")]
_CODE = re.compile(r"^[A-Z0-9]{5}$")


def fetch_surcharge(date, por, pol, pod, pvy, ctype="D"):
    """date = YYYYMMDD, 나머지는 항구코드 5자리, ctype = D/R/O/F/T/G 중 하나."""
    date = re.sub(r"\D", "", date or "")
    codes = [(c or "").upper() for c in (por, pol, pod, pvy)]
    if len(date) != 8 or not all(_CODE.match(c) for c in codes) or ctype not in TYPES or len(ctype) != 1:
        raise ValueError("조회 조건이 올바르지 않습니다")
    q = {"bukrs": BUKRS, "inpetas": date, "inptype": ctype,
         "inppor": codes[0], "inppol": codes[1], "inppod": codes[2], "inppvy": codes[3]}
    j = _post("/frt/selectSurchargeList", {"dma_search": q})
    trim = lambda rows: [{k: r.get(k) for k in _KEEP} for r in rows or []]
    return {"col1": trim(j.get("dlt_preCol1")), "col2": trim(j.get("dlt_preCol2"))}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    L = fetch_locations()
    print(f"나라 {len(L['nations'])} · 항구 {len(L['locs'])}")
    from datetime import date as _d
    r = fetch_surcharge(_d.today().strftime("%Y%m%d"), "KRPUS", "KRPUS", "JPTYO", "JPTYO")
    for x in r["col1"] + r["col2"]:
        print(" ", x["surpcg"], x["surcod"], x["surknd"], x["wfgcur"], x["dc20"] or x["wfg20"], x["dc40"] or x["wfg40"], x["wfglc"])
