"""Barge 요율 엑셀 2개 -> 암호화된 tariff.enc.json

요율은 사내 자료라 공개 저장소에 평문으로 올리면 안 된다. 그래서 이 스크립트가 PC 에서
엑셀을 읽어 사이트 비밀번호로 암호화한 파일(tariff.enc.json)만 만들고, 그 파일만 올린다.
엑셀 원본과 평문은 저장소에 들어가지 않는다(.gitignore).

사용:  tariff_update.bat 더블클릭  (비밀번호를 물어본 뒤 암호화·업로드까지 한다)
       python make_tariff.py [--out 경로]      비밀번호는 물어보거나 환경변수 TARIFF_PASSWORD

엑셀 파일 위치가 바뀌면 아래 HK_XLSX / CN_XLSX 만 고치면 된다.
"""
import getpass
import json
import os
import re
import sys
from datetime import datetime

import openpyxl

from build_static import ENC_ITER, ENC_SALT, encrypt_payload

HERE = os.path.dirname(os.path.abspath(__file__))
FEEDER_DIR = r"C:\Users\thjeon\Desktop\회사\FEEDER"
HK_XLSX = os.path.join(FEEDER_DIR, "BARGE RATE 20251201  by Barge company-DYS.xlsx")
CN_XLSX = os.path.join(FEEDER_DIR, "China Inland Port Cost 2024-re.xlsx")
LIVE_DATA_URL = "https://dythjeon.github.io/opsboard/data.json"

# 출발지(환적항). 검색창에서 코드나 이름 어느 쪽으로 쳐도 찾게 별칭을 같이 내려보낸다.
ORIGINS = {
    "HKHKG": ["HONG KONG", "HONGKONG", "홍콩"],
    "CNSHK": ["SHEKOU", "CHIWAN", "MAWAN", "셔코우", "서커우", "치완", "마완"],
    "CNSHA": ["SHANGHAI", "상해", "상하이"],
    "CNTAG": ["TAICANG", "태창", "타이창"],
    "CNNKG": ["NANJING", "난징", "남경"],
    "CNZJG": ["ZHANGJIAGANG", "장가항", "장자강"],
    "CNNGB": ["NINGBO", "닝보", "영파"],
}


def clean(v):
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def num(v):
    """숫자면 소수 2자리로, 'N/A' '/' 빈칸이면 None."""
    return round(float(v), 4) if isinstance(v, (int, float)) else None


def parse_hk(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = []

    # 시트1: 홍콩 <-> PRD.  A 선사 | B 코드 | C 터미널 | D-G HK$ | H-K US$ | L 유효기간 | M 벤더코드
    ws = wb.worksheets[0]
    company = valid = ""
    for r in ws.iter_rows(min_row=4, values_only=True):
        if clean(r[0]).lower().startswith("remark"):
            break
        if r[0] is not None:
            company, valid = clean(r[0]), clean(r[11])       # 선사·유효기간은 병합 셀이라 첫 줄에만 있다
        code, name = clean(r[1]), clean(r[2])
        if not code or not name or not any(isinstance(v, (int, float)) for v in r[3:11]):
            continue
        rows.append({"src": "HK", "origin": "HKHKG", "company": company, "code": code, "name": name,
                     "f20": num(r[7]), "f40": num(r[8]), "e20": num(r[9]), "e40": num(r[10]),
                     "hkd": [num(r[3]), num(r[4]), num(r[5]), num(r[6])], "valid": valid})

    # 시트2: 셔코우/치완/마완 <-> PRD.  A 선사 | B 코드 | C 출발 | D 터미널 | E-H HK$ | I-L US$ | M TS Mode
    ws = wb.worksheets[1]
    company = ""
    for r in ws.iter_rows(min_row=4, values_only=True):
        if clean(r[0]).lower().startswith("remark"):
            break
        if r[0] is not None:
            company = clean(r[0])
        code, frm, name = clean(r[1]), clean(r[2]), clean(r[3])
        if not code or not name or not any(isinstance(v, (int, float)) for v in r[4:12]):
            continue
        origin = "HKHKG" if "HONG" in frm.upper() else "CNSHK"
        if code == "HKHKG":                     # 홍콩 <-> 셔코우 구간이 코드 칸에 HKHKG 로 적혀 있다
            origin, code = "HKHKG", "CNSHK"
        rows.append({"src": "HK", "origin": origin, "company": company, "code": code, "name": name,
                     "f20": num(r[8]), "f40": num(r[9]), "e20": num(r[10]), "e40": num(r[11]),
                     "hkd": [num(r[4]), num(r[5]), num(r[6]), num(r[7])], "mode": clean(r[12])})
    return rows


def parse_cn(path):
    """양쯔강 내륙항. 시트(WEST/EAST)마다 위쪽은 USD 표, 아래쪽에 RMB 표가 한 번 더 있다."""
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = []
    for ws in wb.worksheets:
        bound = ws.title.strip()
        has_eq = "EQ" in " ".join(clean(c.value) for c in ws[1])       # EQ CHARGE 열은 WEST 시트에만 있다
        mty = 11 if has_eq else 9
        cur = "USD"
        for r in ws.iter_rows(min_row=1, values_only=True):
            if clean(r[0]).upper() == "PORT":                          # 머리글 줄 = 표의 시작. 통화를 여기서 읽는다
                cur = "RMB" if "RMB" in clean(r[5]).upper() else "USD"
                continue
            port, origin = clean(r[0]), clean(r[4])
            if not port or not origin:
                continue
            ts = [clean(r[7]), clean(r[8])]
            rows.append({"src": "CN", "origin": origin, "company": clean(r[2]), "code": "", "name": port,
                         "bound": bound, "cur": cur,
                         "f20": num(r[5]), "f40": num(r[6]), "e20": num(r[mty]), "e40": num(r[mty + 1]),
                         "ts": [t for t in ts if t],
                         "eq": [clean(r[9]), clean(r[10])] if has_eq else [],
                         "remark": clean(r[mty + 2]) if len(r) > mty + 2 else ""})
    return rows


def build():
    rows = parse_hk(HK_XLSX) + parse_cn(CN_XLSX)
    used = {r["origin"] for r in rows}
    return {
        "made_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "files": [os.path.basename(HK_XLSX), os.path.basename(CN_XLSX)],
        "origins": {k: v for k, v in ORIGINS.items() if k in used} | {k: [] for k in used if k not in ORIGINS},
        "rows": rows,
    }


def password_matches_site(password):
    """지금 사이트에 걸린 비밀번호와 같은지. 다르면 탭에서 요율을 풀 수 없으므로 올리기 전에 막는다.
    확인할 수 없으면(오프라인, 사이트가 평문) None."""
    try:
        import base64
        import hashlib
        import requests
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        j = requests.get(LIVE_DATA_URL, timeout=15, headers={"Cache-Control": "no-cache"}).json()
        if not j.get("enc"):
            return None
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), j["salt"].encode("utf-8"), j["iter"])
        try:
            AESGCM(key).decrypt(base64.b64decode(j["iv"]), base64.b64decode(j["ct"]), None)
            return True
        except Exception:
            return False
    except Exception:
        return None


def main():
    out = os.path.join(HERE, "tariff.enc.json")
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    payload = build()
    by = {}
    for r in payload["rows"]:
        by[r["origin"]] = by.get(r["origin"], 0) + 1
    print(f"요율 {len(payload['rows'])}건 읽음: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))

    password = os.environ.get("TARIFF_PASSWORD", "").strip()
    if not password:
        password = getpass.getpass("사이트 비밀번호(입력해도 화면에 보이지 않습니다): ").strip()
    if not password:
        sys.exit("비밀번호가 비어 있어 중단합니다.")
    if "--no-check" not in sys.argv:
        ok = password_matches_site(password)
        if ok is False:
            sys.exit("입력한 비밀번호가 사이트 비밀번호와 다릅니다. 올리지 않고 중단합니다.")
        print("비밀번호 확인: " + ("사이트와 일치" if ok else "사이트와 대조하지 못함(그대로 진행)"))

    assert ENC_SALT and ENC_ITER
    with open(out, "w", encoding="utf-8") as f:
        json.dump(encrypt_payload(payload, password), f)
    print("저장:", out)


if __name__ == "__main__":
    main()
