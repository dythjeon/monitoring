"""환율 띠(ticker)용 환율 수집.

1순위: 네이버 증권 API(api.stock.naver.com) - 하나은행 고시 매매기준율 + 전일 대비 등락.
2순위: open.er-api.com - 네이버가 막혔을 때(해외 서버 등). 하루 1회 갱신이고 등락 정보가 없다.

반환: {"source": "...", "updated": "YYYY-MM-DD HH:MM", "items": [
          {"code": "USD", "name": "미국 달러", "unit": 1, "rate": 1331.1, "change": -4.4, "pct": -0.33}, ...]}
unit 은 고시 단위(엔·동은 100 단위). change/pct 는 모르면 None.
"""
from datetime import datetime

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}

# (통화코드, 표시 이름, 고시 단위)
CURRENCIES = [
    ("USD", "미국 달러", 1),
    ("CNY", "중국 위안", 1),
    ("JPY", "일본 엔", 100),
    ("RUB", "러시아 루블", 1),
    ("VND", "베트남 동", 100),
    ("THB", "태국 바트", 1),
]


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _from_naver():
    items, traded = [], ""
    for code, name, unit in CURRENCIES:
        r = requests.get(f"https://api.stock.naver.com/marketindex/exchange/FX_{code}KRW", headers=HEADERS, timeout=10)
        r.raise_for_status()
        info = r.json()["exchangeInfo"]
        rate = _num(info.get("closePrice"))
        if rate is None:
            raise ValueError(f"{code} 환율 없음")
        items.append({"code": code, "name": name, "unit": unit, "rate": rate,
                      "change": _num(info.get("fluctuations")), "pct": _num(info.get("fluctuationsRatio"))})
        traded = max(traded, (info.get("localTradedAt") or "")[:16].replace("T", " "))
    return {"source": "하나은행 고시 (네이버 증권)", "updated": traded or datetime.now().strftime("%Y-%m-%d %H:%M"), "items": items}


def _from_er_api():
    j = requests.get("https://open.er-api.com/v6/latest/USD", headers=HEADERS, timeout=10).json()
    rates = j["rates"]
    krw = rates["KRW"]
    items = [{"code": code, "name": name, "unit": unit, "rate": round(krw / rates[code] * unit, 2), "change": None, "pct": None}
             for code, name, unit in CURRENCIES]
    return {"source": "open.er-api.com (일 1회 갱신)", "updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "items": items}


OIL_URL = "https://www.petronet.co.kr/v4/main.jsp"
OIL_NAMES = [("textDubai", "DUBAI", "두바이유"), ("textWti", "WTI", "서부텍사스유"), ("textBrent", "BRENT", "브렌트유")]


def fetch_oil():
    """국제 원유가격(한국석유공사 페트로넷 첫 화면의 '국제원유가격' 칸). 하루 한 번, 전일 종가 기준($/bbl).
    반환: {"source", "date": "YYYY.MM.DD", "items": [{"code", "name", "rate", "change", "pct"}]}
    등락은 화면의 gap_up/gap_down 표시로 부호를 정한다."""
    import re
    r = requests.get(OIL_URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    html = r.text
    items, pos = [], len(html)
    for elem, code, name in OIL_NAMES:
        m = re.search(r'id="%s".*?class="coast">\s*([\d.,]+)\s*</p>\s*<span class="gap_(\w*)[^"]*">\s*([\d.,]*)' % elem, html, re.S)
        if not m:
            continue
        rate, chg = _num(m.group(1)), _num(m.group(3)) or 0.0
        if rate is None:
            continue
        if m.group(2) == "down":
            chg = -chg
        prev = rate - chg
        items.append({"code": code, "name": name, "rate": rate, "change": chg,
                      "pct": round(chg / prev * 100, 2) if prev else None})
        pos = min(pos, m.start())
    if not items:
        raise ValueError("국제원유가격 칸을 찾지 못했습니다")
    dates = re.findall(r'class="date">\s*(\d{4}\.\d{2}\.\d{2})\s*\(\$/bbl\)', html[:pos])
    return {"source": "한국석유공사 페트로넷", "date": dates[-1] if dates else "", "items": items}


def fetch_fx():
    try:
        d = _from_naver()
    except Exception:
        d = _from_er_api()
    try:
        d["oil"] = fetch_oil()
    except Exception:
        d["oil"] = None            # 유가를 못 읽어도 환율 띠는 그대로 나온다
    return d


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    d = fetch_fx()
    print(d["source"], d["updated"])
    for it in d["items"]:
        print(f"  {it['code']} {it['name']:8} {it['unit']:>3} = {it['rate']:>10,.2f}원  {it['change']} ({it['pct']}%)")
    print(d.get("oil"))
