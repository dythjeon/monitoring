"""터미널 선석배정 현황 크롤러 모음.

각 터미널/항만 사이트마다 fetch_* 함수를 하나씩 만들고,
SOURCES 리스트에 등록하면 GUI가 자동으로 합쳐서 보여줍니다.
새 사이트를 추가하려면 같은 형태의 fetch_* 함수를 추가한 뒤
SOURCES 리스트에 이름과 함께 등록하세요.
"""

import io
import os
import re
import tempfile

import certifi
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}


_DT_FORMATS = ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M",
               "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _parse_dt(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# 선사코드가 DYS로 안 찍혀도 실제로는 DYS 소속/파트너 선박이라 항상 추적해야 하는 선명.
# LANE 편성이 아직 안 정해진 배를 여기에 둔다. 편성이 정해지면 VESSEL_LANES로 옮긴다.
EXTRA_SHIP_NAMES = ("SM TOKYO",)


# ---- 서비스 편성표: 선명 -> (LANE, ROUTE, 운항선사) ----
# 왜 선명이 기준인가: 터미널이 주는 필드로는 편성을 알 수 없다.
#  - 선사코드(line)는 터미널마다 다르게 찍힌다. HONOR GLORY는 신선대에서 NSL,
#    광양서부에서 DYS로 나온다. 운항선사 코드라서 용선 관계에 따라 달라진다.
#  - 항로코드(route)는 BPTC와 HJIT 페이지에 아예 없고(빈 문자열), 있는 곳도
#    DONGJIN FIDES가 'BKS1'처럼 편성표(BKS)와 미묘하게 다르게 찍힌다.
# 선명은 어느 터미널에서나 같으므로 이것만 믿는다. 예전에 route 코드로
# 행선지를 추정했다가 "맞는 배와 안 맞는 배가 섞이는" 문제로 걷어냈다.
#
# 이 표에 이름을 올리면 두 가지가 동시에 된다: (1) 추적 대상 등록,
# (2) LANE 기준 행선지 분류(gui._classify_country). 새 배는 한 줄만 추가하면 된다.
VESSEL_LANES = {
    # LANE KJK — 이하 전 ROUTE 일본행
    "DONGJIN FORTUNE":      ("KJK", "NC1", "동진상선"),
    "HEUNG-A XIAMEN":       ("KJK", "NC2", "흥아라인"),
    "HONOR GLORY":          ("KJK", "DHA", "동영해운"),
    "HONOR OCEAN":          ("KJK", "HS2", "팬오션"),
    "DONGJIN FIDES":        ("KJK", "BKS", "동진상선"),
    # LANE KCK — 이하 전 ROUTE 중국행
    "PEGASUS GLORY":        ("KCK", "ISS", "동영해운"),
    "ZHONG SHENG TUO HAI":  ("KCK", "IS2", "고려해운"),
    "PANCON GLORY":         ("KCK", "IS3", "범주해운"),
    "HE YANG":              ("KCK", "BNJ", "동영해운"),
    "YANGTZE TRADER":       ("KCK", "NKX", "장금상선"),
    # LANE CKJ — 배가 아니라 그 항차의 바운드로 갈린다(LANE_BY_BOUND 참고)
    "PEGASUS TERA":         ("CKJ", "NCK", "동영해운"),
    "PEGASUS PETA":         ("CKJ", "NCK", "동영해운"),
}

# LANE -> 행선지. 이 표에 없는 LANE은 분류하지 않는다.
LANE_COUNTRY = {"KJK": "일본", "KCK": "중국"}
# 배마다 고정이 아니라 항차 바운드(E/W)로 갈리는 LANE.
LANE_BY_BOUND = ("CKJ",)


def _norm_name(s):
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def lane_of(ship_name):
    """선명으로 (LANE, ROUTE, 운항선사)를 찾는다. 편성표에 없으면 None.

    터미널에 따라 선명 뒤에 항차나 접미사가 붙는 경우가 있어 부분일치로 본다.
    """
    name = _norm_name(ship_name)
    if not name:
        return None
    for key, info in VESSEL_LANES.items():
        if key in name:
            return info
    return None


def _is_tracked(line, ship_name, extra_codes=()):
    """선사코드가 DYS이거나, 그 터미널에서 DYS와 동일하게 취급하는 컨소시엄/파트너
    항로 코드(extra_codes)이거나, VESSEL_LANES 편성표 또는 EXTRA_SHIP_NAMES에
    있는 선명이면 추적 대상으로 본다."""
    line = (line or "").strip().upper()
    if line == "DYS" or line in extra_codes:
        return True
    if lane_of(ship_name):
        return True
    ship = _norm_name(ship_name)
    return any(_norm_name(name) in ship for name in EXTRA_SHIP_NAMES)


BPTC_EXTRA_CODES = ("KHP", "NKX")


def fetch_bptc(time_range="week"):
    """BPTC(부산항터미널, 신선대/감만) 선석배정 현황(T)을 가져온다.

    time_range: '3days' | 'week' | 'month'
    이 페이지는 선사코드 필드에 DYS 대신 컨소시엄/파트너 코드(KHP, NKX)가
    찍히는 경우가 있어, 서버 필터 없이 전체 선사를 받아 _is_tracked()로
    클라이언트에서 걸러낸다.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]
    """
    url = "https://info.bptc.co.kr/Berth_status_text_servlet_sw_kr"
    referer = "https://info.bptc.co.kr/content/sw/frame/berth_status_text_frame_sw_kr.jsp"
    body = {
        "v_time": time_range,
        "ROCD": "ALL",
        "ORDER": "item1",
        "v_gu": "A",
        "v_oper_cd": "",
    }
    headers = dict(HEADERS)
    headers["Referer"] = referer

    resp = requests.post(url, data=body, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="tabletypeC")
    if table is None or table.find("tbody") is None:
        return []

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 10:
            continue
        terminal, berth, voyage, ship_name, _side, line, eta_plan, eta, _work_done, etd = cells[:10]
        if not _is_tracked(line, ship_name, extra_codes=BPTC_EXTRA_CODES):
            continue
        rows.append({
            "source": "BPTC",
            "region": "부산",
            "terminal": terminal,
            "berth": berth,
            "voyage": voyage,
            "ship_name": ship_name,
            "line": line,
            "route": "",  # BPTC 선석배정 페이지에는 항로 코드가 없음
            "eta": _parse_dt(eta) or _parse_dt(eta_plan),
            "etd": _parse_dt(etd),
        })
    return rows


def fetch_bptc_work_status():
    """BPTC(신선대/감만) 본선작업현황(선석별 양적하 진행률)을 가져온다.

    info.bptc.co.kr 메인 페이지 자체에 선석별 현재 작업중인 선박과
    진행률(%)이 서버 렌더링되어 있어, 별도 조회 없이 그대로 파싱한다.
    fetch_bptc()와 동일하게 _is_tracked()로 걸러낸다.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd, progress,
            discharge, load}, ...]
    """
    url = "https://info.bptc.co.kr/content/index.jsp"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []
    for dl in soup.select("div.ship-core dl"):
        dt = dl.find("dt")
        terminal = dt.get_text(strip=True) if dt else ""
        for li in dl.select("li.ship-li"):
            order = li.select_one("span.order")
            berth = order.get_text(strip=True).replace("선석 No.", "").strip() if order else ""

            progress_span = li.select_one("span.progress")
            progress_text = progress_span.get_text(strip=True) if progress_span else "0"
            progress = int(re.sub(r"[^\d]", "", progress_text) or 0)

            title = li.select_one(".state-wr .title")
            voyage = title.get_text(strip=True) if title else ""

            ship_link = li.select_one(".txt1-wr a")
            ship_full = ship_link.get_text(strip=True) if ship_link else ""
            m = re.match(r"(.+?)\((\w+)\)\s*$", ship_full)
            ship_name, line = (m.group(1).strip(), m.group(2)) if m else (ship_full, "")

            times = li.select(".txt1-wr.shiptime span")
            eta = _parse_dt(times[0].get_text(strip=True)) if len(times) > 0 else None
            etd = _parse_dt(times[1].get_text(strip=True)) if len(times) > 1 else None

            counts = li.select(".txt2-wr.operator span")
            discharge = counts[0].get_text(strip=True) if len(counts) > 0 else ""
            load = counts[1].get_text(strip=True) if len(counts) > 1 else ""

            if not _is_tracked(line, ship_name, extra_codes=BPTC_EXTRA_CODES):
                continue

            rows.append({
                "terminal": terminal,
                "berth": berth,
                "voyage": voyage,
                "ship_name": ship_name,
                "line": line,
                "eta": eta,
                "etd": etd,
                "progress": progress,
                "discharge": discharge,
                "load": load,
            })
    return rows


HJIT_EXTRA_CODES = ("IQH", "IHP")
HJIT_PAGE_LIMIT = 20   # 페이지 루프 안전장치. Total 파싱이 틀려도 여기서 멈춘다.


def fetch_hjit(period_days=14, base_date=None):
    """HJIT(한진인천컨테이너터미널) 선석현황을 가져온다.

    period_days: 조회 기간(일)
    base_date: 'YYYY-MM-DD' 기준일. None이면 오늘 날짜 사용.
    이 페이지도 선사코드 필드에 DYS 대신 컨소시엄/파트너 코드(IQH, IHP)가
    찍히는 경우가 있어, 서버 필터 없이 전체 선사를 받아 _is_tracked()로
    클라이언트에서 걸러낸다.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]
    """
    base = "http://59.17.254.10:9130/esvc"
    get_url = f"{base}/berth/BerthAction.do?cmd=BerthScheduleList&menuID=01&subMenuID=1&pgID=01"
    post_url = f"{base}/berth/BerthAction.do"

    session = requests.Session()
    session.headers.update(HEADERS)

    session.get(get_url, timeout=15)  # JSESSIONID 쿠키 확보

    body = {
        "cmd": "BerthScheduleList",
        "smode": "",
        "nowPage": "1",   # _load_page()가 페이지마다 덮어쓴다
        "scPeriod": str(period_days),
        "val1": "",
        "menuID": "01",
        "subMenuID": "1",
        "pgID": "01",
        "scBaseDate": base_date or datetime.now().strftime("%Y-%m-%d"),
        "theCode": "",
        "theSeq": "",
        "theYear": "",
        "thePtnr": "",
        "theLane": "",
    }
    def _load_page(page):
        body["nowPage"] = str(page)
        resp = session.post(post_url, data=body, headers={"Referer": get_url}, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser")

    def _parse_page(soup):
        # 직계 자식 td만 본다. 이 페이지는 표가 여러 겹으로 중첩돼 있어서
        # recursive 검색(기본값)을 쓰면 바깥쪽 tr이 안쪽 표의 td를 통째로
        # 주워와 cells[:12]가 첫 행이 되고, 결국 첫 행이 4번씩 중복된다.
        out = []
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td", class_="ta2", recursive=False)
            if len(tds) < 12:
                continue
            out.append([td.get_text(strip=True) for td in tds])
        return out

    # 이 페이지는 한 번에 10건씩만 준다(Total:60이면 6페이지). 예전엔 1페이지만
    # 읽어서 사흘치만 보였고, 그 뒤에 있는 우리 선박(PEGASUS TERA/PETA/HOPE,
    # SM TOKYO 등)이 통째로 누락됐다. 한진신항이 늘 0건이던 원인이었다.
    first = _load_page(1)
    cells_all = _parse_page(first)
    per_page = len(cells_all)
    m = re.search(r"Total\s*:\s*([\d,]+)", first.get_text(" ", strip=True))
    total = int(m.group(1).replace(",", "")) if m else 0
    if per_page and total > per_page:
        last_page = min(-(-total // per_page), HJIT_PAGE_LIMIT)
        for page in range(2, last_page + 1):
            page_cells = _parse_page(_load_page(page))
            if not page_cells:      # 빈 페이지가 나오면 더 볼 것이 없다
                break
            cells_all.extend(page_cells)

    rows = []
    for cells in cells_all:
        voyage, ship_name, line, _in_voy, _out_voy, _cct, etb_atb, etd_atd, _dis, _load, _tp, berth = cells[:12]
        berth = berth.split("(")[0].strip()
        if not _is_tracked(line, ship_name, extra_codes=HJIT_EXTRA_CODES):
            continue
        rows.append({
            "source": "HJIT",
            "region": "인천",
            "terminal": "한진신항",
            "berth": berth,
            "voyage": voyage,
            "ship_name": ship_name,
            "line": line,
            "route": "",  # HJIT 선석현황 페이지에는 항로 코드가 없음
            "eta": _parse_dt(etb_atb),
            "etd": _parse_dt(etd_atd),
        })
    return rows


def fetch_gwct(time_range="week"):
    """GWCT(광양항서부컨테이너터미널) 선석배정 목록을 가져온다.

    time_range: '3days' | 'week' | 'month'
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]

    이 사이트는 선사코드로 서버측 필터링을 지원하지 않아(폼에 필드 없음)
    전체 목록을 받아온 뒤 클라이언트에서 _is_tracked()로 걸러낸다.
    """
    base = "http://www.gwct.co.kr"
    page_url = f"{base}/sub/sub_B2"
    search_url = f"{base}/sub/sub_B2/search"

    session = requests.Session()
    session.headers.update(HEADERS)

    page_resp = session.get(page_url, timeout=15)
    page_resp.raise_for_status()
    csrf = re.search(r'name="_csrf" value="([^"]+)"', page_resp.text)
    if not csrf:
        return []
    csrf = csrf.group(1)

    body = {
        "_csrf": csrf,
        "page": "1",
        "pageSize": "200",
        "v_time": time_range,
        "range": "ETB",
    }
    resp = session.post(search_url, data=body,
                         headers={"X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest"},
                         timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="AA_list")
    if table is None:
        return []

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 9:
            continue
        _no, berth, voyage, ship_name, _line_voyage, _side, line, eta, etd = cells[:9]
        route = cells[16] if len(cells) > 16 else ""
        if not _is_tracked(line, ship_name):
            continue
        rows.append({
            "source": "GWCT",
            "region": "광양",
            "terminal": "광양서부",
            "berth": berth,
            "voyage": voyage,
            "ship_name": ship_name,
            "line": line,
            "route": route,
            "eta": _parse_dt(eta.replace("/", "-")),
            "etd": _parse_dt(etd.replace("/", "-")),
        })
    return rows


def fetch_gwct_empty_status(carrier_code="DYS"):
    """GWCT(광양항서부컨테이너터미널) On-Dock 선사별 컨테이너 SUMMARY에서
    DEPOT(장치장 보관) 칸의 GP(일반) 행만 뽑아온다.

    이 페이지는 선사코드로 서버측 필터링을 지원한다(ptnrCd). CSRF 토큰은
    <meta name="_csrf">에 있고, common.js가 모든 AJAX 요청에 X-CSRF-TOKEN
    헤더로 자동 첨부하는 걸 그대로 흉내낸다.
    표 헤더는 구분(행: GP/RF/OT/PL/TK/계) x [IMPORT/EXPORT/T·S/DEPOT](각
    20G/40G/40H x 2) 구조라, DEPOT 블록(뒤에서 3번째 6칸)만 골라
    20G/40G/40H 두 칸(예: S/D)을 더해 합계를 낸다.
    fetch_bptc_empty_status()와 같은 모양({carrier, terminal, counts, total})
    으로 반환해 공컨현황 표에 그대로 이어붙일 수 있게 한다.
    """
    base = "http://www.gwct.co.kr"
    page_url = f"{base}/sub/sub_F1"
    search_url = f"{base}/sub/sub_F1/search"

    session = requests.Session()
    session.headers.update(HEADERS)

    page_resp = session.get(page_url, timeout=15)
    page_resp.raise_for_status()
    csrf = re.search(r'name="_csrf" content="([^"]+)"', page_resp.text)
    if not csrf:
        return []
    csrf = csrf.group(1)

    resp = session.post(search_url, data={"ptnrCd": carrier_code},
                         headers={"X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest"},
                         timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if not tds or tds[0].get_text(strip=True) != "GP":
            continue
        cells = [td.get_text(strip=True) for td in tds]
        if len(cells) < 27:
            continue
        depot = [int(c) if c.isdigit() else 0 for c in cells[19:25]]  # 20G-S,D 40G-S,D 40H-S,D
        counts = {
            "GP20": depot[0] + depot[1],
            "GP40": depot[2] + depot[3],
            "HQ40": depot[4] + depot[5],
        }
        return [{
            "carrier": carrier_code.upper(),
            "terminal": "광양서부",
            "counts": counts,
            "total": sum(counts.values()),
        }]
    return []


def fetch_snct(line_code="KHP"):
    """SNCT(선광신컨테이너터미널) 선석배정현황(그래픽)을 가져온다.

    SNCT는 선사코드가 아니라 항로(Route) 코드로 구분되어 있어,
    DYS 선사가 속한 항로코드 'KHP'로 걸러낸다.
    조회 화면이 한 번에 7일 단위로만 보여줘서 이번 주/다음 주 두 번 요청해 합친다.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]
    """
    url = "https://snct.sun-kwang.co.kr/infoservice/webpage/vessel/vslScheduleChart.jsp"

    today = datetime.now()
    query_dates = [today.strftime("%Y-%m-%d"),
                   (today + timedelta(days=7)).strftime("%Y-%m-%d")]

    rows = []
    seen = set()
    for qdate in query_dates:
        resp = requests.get(url, params={"isSearch": "Y", "queryDate": qdate},
                             headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for info in soup.select("div[id^='vsl_info_']"):
            fields = {}
            for tr in info.select("table.vsl_info tr"):
                tds = tr.find_all("td")
                if len(tds) == 2:
                    fields[tds[0].get_text(strip=True)] = tds[1].get_text(strip=True)

            route = fields.get("Route", "")
            ship_name = fields.get("Name of Vessel", "")
            if not (route == line_code or _is_tracked(route, ship_name)):
                continue

            voyage = fields.get("In / Out Voyage", "")
            berth = fields.get("Berth No / (P/S)", "").split("/")[0].strip()
            eta = _parse_dt(fields.get("Estimated Time of Berth", ""))
            etd = _parse_dt(fields.get("Estimated Time of Departure", ""))

            key = (ship_name, voyage, eta, etd)
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "source": "SNCT",
                "region": "인천",
                "terminal": "선광신항",
                "berth": berth,
                "voyage": voyage,
                "ship_name": ship_name,
                "line": route,
                "route": route,
                "eta": eta,
                "etd": etd,
            })
    return rows


# --------------------------------------------------------------------------
# E1CT 는 2026-09 경 HTTPS 로 전환(http 요청은 301 리다이렉트)했는데, 서버가
# 중간 인증서를 함께 보내지 않는다. 브라우저는 AIA 로 알아서 받아오지만
# requests/OpenSSL 은 그러지 않아 "unable to get local issuer certificate" 로
# 검증이 실패한다. 그래서 발급자인 Sectigo 중간 인증서를 certifi 번들 뒤에
# 붙여서 쓴다 — verify=False 로 검증을 끄지 않기 위한 조치다.
#
# 이 인증서 유효기간: ~2036-03-21. 언젠가 E1CT 가 CA 를 바꾸면 다시 실패하는데,
# 그때는 http://crt.sectigo.com/... (leaf 인증서의 AIA 필드에 적힌 주소)에서
# 새 중간 인증서를 받아 아래 문자열만 교체하면 된다.
E1CT_INTERMEDIATE_PEM = """-----BEGIN CERTIFICATE-----
MIIGTDCCBDSgAwIBAgIQLBo8dulD3d3/GRsxiQrtcTANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNMzYwMzIxMjM1OTU5WjBgMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gQ0EgT1YgUjM2MIIBojANBgkqhkiG9w0B
AQEFAAOCAY8AMIIBigKCAYEApkMtJ3R06jo0fceI0M52B7K+TyMeGcv2BQ5AVc3j
lYt76TvHIu/nNe22W/RJXX9rWUD/2GE6GF5x0V4bsY7K3IeJ8E7+KzG/TGboySfD
u+F52jqQBbY62ofhYjMeiAbLI02+FqwHeM8uIrUtcX8b2RCxF358TB0NHVccAXZc
FYgZndZCeXxjuca7pJJ20LLUnXtgXcjAE1vY4WvbReW0W6mkeZyNGdmpTcFs5Y+s
yy6LtE5Zocji9J9NlNnReox2RWVyEXpA1ChZ4gqN+ZpVSIQ0HBorVFbBKyhdZyEX
gZgNSNtBRwxqwIzJePJhYd4ZUhO1vk+/uP3nwDk0p95q/j7naXNCSvESnrHPypaB
WRK066nKfPRPi9m9kIOhMdYfS8giFRTcdgL24Ycilj7ecAK9Trh0VbjwouJ4WH+x
bt47u68ZFCD/ac55I0DNHkCpaPruj6e9Rmr7K46wZDAYXuEAqB7tGG/jd6JAA+H2
O44CV98NRsU213f1kScIZntNAgMBAAGjggGBMIIBfTAfBgNVHSMEGDAWgBRWc1hk
lfmSGrASKgRieaFAFYghSTAdBgNVHQ4EFgQU42Z0u3BojSxdTg6mSo+bNyKcgpIw
DgYDVR0PAQH/BAQDAgGGMBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0lBBYwFAYI
KwYBBQUHAwEGCCsGAQUFBwMCMBsGA1UdIAQUMBIwBgYEVR0gADAIBgZngQwBAgIw
VAYDVR0fBE0wSzBJoEegRYZDaHR0cDovL2NybC5zZWN0aWdvLmNvbS9TZWN0aWdv
UHVibGljU2VydmVyQXV0aGVudGljYXRpb25Sb290UjQ2LmNybDCBhAYIKwYBBQUH
AQEEeDB2ME8GCCsGAQUFBzAChkNodHRwOi8vY3J0LnNlY3RpZ28uY29tL1NlY3Rp
Z29QdWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYucDdjMCMGCCsGAQUF
BzABhhdodHRwOi8vb2NzcC5zZWN0aWdvLmNvbTANBgkqhkiG9w0BAQwFAAOCAgEA
BZXWDHWC3cubb/e1I1kzi8lPFiK/ZUoH09ufmVOrc5ObYH/XKkWUexSPqRkwKFKr
7r8OuG+p7VNB8rifX6uopqKAgsvZtZsq7iAFw04To6vNcxeBt1Eush3cQ4b8nbQR
MQLChgEAqwhuXp9P48T4QEBSksYav7+aFjNySsLYlPzNqVM3RNwvBdvp6vgDtGwc
xlKQZVuuNVIaoYyls8swhxDeSHKpRdxRauTLZ+pl+wGvy0pnrLEJGSz9mOEmfbod
e/XopR2NGqaHJ6bIjyxPu6UtyQGI26En7UAEozACrHz06Nx2jTAY9E6NeB6XuobE
wLK025ZRmvglcURG1BrV24tGHHTgxCe8M3oGlpUSMTKQ2dkgljZVYt+gKdFtWELZ
MuRdi+X3XsrR8LFz+aLUiDRfQqhmw3RxjIyVKvvu9UPYY1nsvxYmFnUSeM+2q1z/
iPUry+xDY9MC6+IhleKT094VKdFVp7LXH42+wvU+17lRolQ2mK2N/nBLVBwaIhib
QXw4VYKwB86Bc6eS6iqsc94KEgD/U4VsjmgfhK+Xp4NM+VYzTTa3QeV3p8xOM0cw
q1p8oZFA+OBcz3FYWpDIe5j0NWKlw9hXsTyPY/HeZUV59akskSOSRSmDfe8wJDPX
58uB9/7lud0G3x0pxQAcffP0ayKavNwDTw4UfJ34cEw=
-----END CERTIFICATE-----
"""

_E1CT_CA_BUNDLE = None


def _e1ct_ca_bundle():
    """certifi 기본 번들 + E1CT 중간 인증서를 합친 임시 파일 경로를 돌려준다."""
    global _E1CT_CA_BUNDLE
    if _E1CT_CA_BUNDLE and os.path.exists(_E1CT_CA_BUNDLE):
        return _E1CT_CA_BUNDLE
    try:
        path = os.path.join(tempfile.gettempdir(), "berthmonitor_e1ct_ca.pem")
        with io.open(certifi.where(), encoding="ascii") as f:
            base = f.read()
        with io.open(path, "w", encoding="ascii") as f:
            f.write(base.rstrip() + "\n" + E1CT_INTERMEDIATE_PEM)
        _E1CT_CA_BUNDLE = path
        return path
    except OSError:
        return certifi.where()      # 임시파일을 못 쓰면 기본 검증으로 되돌린다


def fetch_e1ct():
    """E1CT(인천 E1컨테이너터미널) 선석배정현황(T)을 가져온다.

    날짜를 지정하지 않으면 오늘부터 7일치를 그대로 보여주는 페이지라
    파라미터 없이 그대로 가져온다.

    이 사이트의 HTML은 </td> 닫는 태그가 군데군데 빠져 있어(불량 마크업)
    BeautifulSoup의 td 탐색만으로는 값이 밀려서, <td 여는 태그 기준으로
    직접 정규식으로 셀을 분리한다.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]
    """
    url = "https://www.e1ct.co.kr/info/terminal/berthText"
    resp = requests.get(url, headers=HEADERS, timeout=15,
                        verify=_e1ct_ca_bundle())
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    tbody_match = re.search(r"<tbody>(.*?)</tbody>", resp.text, re.S)
    if not tbody_match:
        return []

    rows = []
    for tr_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", tbody_match.group(1), re.S):
        cell_chunks = re.split(r"<td[^>]*>", tr_match.group(1))[1:]
        cells = []
        for raw in cell_chunks:
            lines = [re.sub(r"<[^>]+>", "", part).strip()
                     for part in re.split(r"<br\s*/?>", raw)]
            cells.append([l for l in lines if l])
        if len(cells) < 9:
            continue

        line = cells[8][0] if cells[8] else ""
        ship_name = cells[4][0] if cells[4] else ""
        if not _is_tracked(line, ship_name):
            continue

        rows.append({
            "source": "E1CT",
            "region": "인천",
            "terminal": "E1CT",
            "berth": cells[1][0].split("(")[0].strip() if cells[1] else "",
            "voyage": " ".join(cells[2]) if cells[2] else "",
            "ship_name": ship_name,
            "line": line,
            "route": "",  # E1CT 선석배정현황(T) 페이지에는 항로 코드가 없음
            "eta": _parse_dt(cells[5][0].strip("()")) if cells[5] else None,
            "etd": _parse_dt(cells[7][0].strip("()")) if cells[7] else None,
        })
    return rows


def fetch_e1ct_empty_status(carrier_code="DYS"):
    """E1CT(인천 E1컨테이너터미널) 공컨테이너 반출 가능 수량 페이지에서
    선사코드에 해당하는 행만 뽑아온다.

    이 페이지는 파라미터 없이 GET하면 전체 선사 목록이 완성된 표로
    내려온다(서버측 필터 없음). 컬럼은 20GP/40GP/40HQ/45GP/합계인데,
    45GP는 실제로 쓰이지 않는 항목이라(늘 0) 가져오지 않는다.
    fetch_bptc_empty_status()와 같은 모양으로 반환해 공컨현황 표에
    그대로 이어붙일 수 있게 한다.
    """
    url = "https://www.e1ct.co.kr/info/terminal/emptyContainer"
    resp = requests.get(url, headers=HEADERS, timeout=15,
                        verify=_e1ct_ca_bundle())
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.select_one("div.tb_tmnl table")
    if table is None or table.find("tbody") is None:
        return []

    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 5 or cells[0].strip().upper() != carrier_code.upper():
            continue
        gp20, gp40, hq40 = (int(c) if c.isdigit() else 0 for c in cells[1:4])
        counts = {"GP20": gp20, "GP40": gp40, "HQ40": hq40}
        return [{
            "carrier": carrier_code.upper(),
            "terminal": "E1CT",
            "counts": counts,
            "total": sum(counts.values()),
        }]
    return []


def fetch_hjit_empty_status(carrier_code="DYS"):
    """HJIT(한진인천컨테이너터미널) 메인페이지의 "Empty 반출 가능 개수"에서
    선사코드에 해당하는 행만 뽑아온다.

    메인페이지가 /kor/main/prg2/getPrg2.json을 호출해 선사별 20/40/40HC/45
    반출가능 수량을 JSON으로 채워 넣는다(인증 불필요). 45(m45)는 실제로
    쓰이지 않는 항목이라 가져오지 않는다.
    fetch_bptc_empty_status()와 같은 모양으로 반환해 공컨현황 표에
    그대로 이어붙일 수 있게 한다.
    """
    url = "https://www.hjit.co.kr/kor/main/prg2/getPrg2.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    obj = resp.json()

    for o in obj.get("result", {}).get("data_list", []):
        if (o.get("opr") or "").strip().upper() != carrier_code.upper():
            continue
        counts = {
            "GP20": int(o.get("m20")) if str(o.get("m20")).isdigit() else 0,
            "GP40": int(o.get("m40")) if str(o.get("m40")).isdigit() else 0,
            "HQ40": int(o.get("m40hc")) if str(o.get("m40hc")).isdigit() else 0,
        }
        return [{
            "carrier": carrier_code.upper(),
            "terminal": "한진신항",
            "counts": counts,
            "total": sum(counts.values()),
        }]
    return []


def _find_hpnt_table(soup):
    """결과 표를 찾는다. 페이지가 표를 여러 겹으로 감싸고 있어(레이아웃용
    바깥 표들도 안쪽 표의 텍스트를 그대로 포함), '선명' 헤더 셀을 정확히
    일치시켜 그 셀이 속한 가장 안쪽 tr/table을 찾아야 한다."""
    for td in soup.find_all("td"):
        if td.get_text(strip=True) == "선명":
            tr = td.find_parent("tr")
            return tr.find_parent("table") if tr else None
    return None


def fetch_hpnt():
    """HPNT(부산항 허치슨터미널) 선석배정현황을 가져온다.

    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]

    결과가 페이지당 20건씩 나뉘어(currentPage) 있어, 첫 페이지에서
    "전체 조회 건수"를 읽어 전체 페이지를 순회하며 모두 모은다.
    """
    url = "https://custom.hktl.com/jsp/T01/sunsuk.jsp"
    session = requests.Session()
    session.headers.update(HEADERS)

    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "euc-kr"
    soup = BeautifulSoup(resp.text, "html.parser")

    total_match = re.search(r"전체\s*조회\s*건수:\s*(\d+)", soup.get_text())
    total_count = int(total_match.group(1)) if total_match else 20
    total_pages = max(1, -(-total_count // 20))  # 올림 나눗셈

    rows = []
    for page in range(1, total_pages + 1):
        if page == 1:
            page_soup = soup
        else:
            today = datetime.now()
            body = {"year": str(today.year), "month": f"{today.month:02d}",
                    "day": f"{today.day:02d}",
                    "currentPage": str(page), "startPage": "1"}
            resp = session.post(url, data=body, timeout=15)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "euc-kr"
            page_soup = BeautifulSoup(resp.text, "html.parser")

        table = _find_hpnt_table(page_soup)
        if table is None:
            continue

        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 14:
                continue
            (voyage_code, inout_voy, berth, _bitt, eta, _work, etd, _closing,
             _side, _qty, _qc, ship_name, line, route) = cells[:14]

            if not _is_tracked(line, ship_name):
                continue

            rows.append({
                "source": "HPNT",
                "region": "부산",
                "terminal": "허치슨",
                "berth": berth,
                "voyage": f"{voyage_code} {inout_voy}".strip(),
                "ship_name": ship_name,
                "line": line,
                "route": route,
                "eta": _parse_dt(eta),
                "etd": _parse_dt(etd),
            })
    return rows


BPTC_EMPTY_CARRIER_CODES = ("DYS",)
BPTC_EMPTY_COL_LABELS = ["GP20", "GP40", "OT20", "OT40", "FL20", "FL40", "HQ20", "HQ40", "HQ45"]


def fetch_bptc_empty_status(carrier_codes=BPTC_EMPTY_CARRIER_CODES):
    """BPTC(신선대/감만) On-Dock 공컨테이너 현황을 가져온다.

    이 페이지는 AJAX 없이 서버가 전체 선사 목록이 채워진 표를 그대로
    내려준다(필터 파라미터 없음). 한 행에 신선대 10칸 + 감만 10칸이
    나란히 붙어 있어(각 칸 = 선사코드 1 + 컨테이너 타입/사이즈별 수량 9),
    전체를 받아온 뒤 carrier_codes에 해당하는 선사만 골라낸다.
    활동이 없는 선사/터미널 조합은 원본 표에 행 자체가 없어 결과에서 빠질 수 있다.
    반환: [{carrier, terminal, counts: {GP20: n, ...}, total}, ...]
    """
    url = "https://info.bptc.co.kr/content/od/frame/yard_new_empty_frame_od_kr.jsp"
    params = {"p_id": "EMPT_NE_KR", "snb_num": "8", "snb_div": "service", "pop_ok": "Y"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "euc-kr"

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="tabletypeC")
    if table is None:
        return []

    # 이 페이지 마크업이 <tbody> 여는 태그 앞에 낙오된 <tr>가 하나 끼어 있는 등
    # 불량해서(E1CT와 비슷한 사례), BeautifulSoup가 데이터 행들을 <tbody> 안에
    # 제대로 넣지 못한다. table.find_all("tr")로 전체를 직접 순회하면
    # 헤더 행(<th>만 있어 td 0개)과 빈 낙오 행은 아래 개수 체크로 자연히 걸러진다.
    codes = tuple(c.upper() for c in carrier_codes)
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 20:
            continue
        for terminal, chunk in (("신선대", cells[0:10]), ("감만", cells[10:20])):
            carrier = chunk[0].strip().upper()
            if carrier not in codes:
                continue
            counts = {}
            total = 0
            for label, raw in zip(BPTC_EMPTY_COL_LABELS, chunk[1:]):
                val = int(raw) if raw.strip().isdigit() else 0
                counts[label] = val
                total += val
            rows.append({"carrier": carrier, "terminal": terminal, "counts": counts, "total": total})
    return rows


# 여기에 사이트를 추가로 등록하세요. (이름, 함수)
SOURCES = [
    ("BPTC (신선대/감만)", fetch_bptc),
    ("허치슨 (부산항 허치슨터미널·자성대/감만)", fetch_hpnt),
    ("한진신항 (한진인천컨테이너터미널)", fetch_hjit),
    ("선광신항 (선광신컨테이너터미널)", fetch_snct),
    ("E1CT (E1컨테이너터미널)", fetch_e1ct),
    ("광양서부 (광양항서부컨테이너터미널)", fetch_gwct),
]


def fetch_all():
    """등록된 모든 소스를 크롤링해서 하나의 리스트로 합친다.

    반환: (전체 행, 오류 문구 목록, [(소스 이름, 수집 건수), ...])

    소스별 건수를 같이 돌려주는 이유: "조회는 정상인데 해당 선박이 0건"과
    "조회 자체가 실패"가 화면에서는 똑같이 '아무것도 안 보임'으로 나타나
    구분할 방법이 없다. 실패한 소스는 건수를 None으로 준다.
    """
    all_rows = []
    errors = []
    stats = []
    for name, fn in SOURCES:
        try:
            rows = fn()
        except Exception as e:  # 한 사이트가 실패해도 나머지는 계속 진행
            errors.append(f"{name}: {e}")
            stats.append((name, None))
            continue
        all_rows.extend(rows)
        stats.append((name, len(rows)))
    return all_rows, errors, stats


if __name__ == "__main__":
    data, errors, _stats = fetch_all()
    print(f"총 {len(data)}건 수집")
    for d in data[:5]:
        print(d)
    if errors:
        print("오류:", errors)
