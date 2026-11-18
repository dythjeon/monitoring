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


def fetch_fx():
    try:
        return _from_naver()
    except Exception:
        return _from_er_api()


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    d = fetch_fx()
    print(d["source"], d["updated"])
    for it in d["items"]:
        print(f"  {it['code']} {it['name']:8} {it['unit']:>3} = {it['rate']:>10,.2f}원  {it['change']} ({it['pct']}%)")
