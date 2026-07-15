"""터미널 선석배정 현황 크롤러 모음.

각 터미널/항만 사이트마다 fetch_* 함수를 하나씩 만들고,
SOURCES 리스트에 등록하면 GUI가 자동으로 합쳐서 보여줍니다.
새 사이트를 추가하려면 같은 형태의 fetch_* 함수를 추가한 뒤
SOURCES 리스트에 이름과 함께 등록하세요.
"""

import re
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


def fetch_bptc(time_range="week", line_code="DYS"):
    """BPTC(부산항터미널, 신선대/감만) 선석배정 현황(T)을 가져온다.

    time_range: '3days' | 'week' | 'month'
    line_code: 선사코드 (예: 'DYS'). 빈 문자열/None이면 전체 선사.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]
    """
    url = "https://info.bptc.co.kr/Berth_status_text_servlet_sw_kr"
    referer = "https://info.bptc.co.kr/content/sw/frame/berth_status_text_frame_sw_kr.jsp"
    body = {
        "v_time": time_range,
        "ROCD": "ALL",
        "ORDER": "item1",
        "v_gu": "A",
        "v_oper_cd": line_code or "",
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
    특정 선사로 필터하지 않고 전체 작업현황을 반환한다(전체 혼잡도 파악용).
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


def fetch_hjit(period_days=14, line_code="DYS", base_date=None):
    """HJIT(한진인천컨테이너터미널) 선석현황을 가져온다.

    period_days: 조회 기간(일)
    line_code: 선사코드 (예: 'DYS'). 빈 문자열/None이면 전체 선사.
    base_date: 'YYYY-MM-DD' 기준일. None이면 오늘 날짜 사용.
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
        "nowPage": "1",
        "scPeriod": str(period_days),
        "val1": "",
        "menuID": "01",
        "subMenuID": "1",
        "pgID": "01",
        "scBaseDate": base_date or datetime.now().strftime("%Y-%m-%d"),
        "theCode": "",
        "theSeq": "",
        "theYear": "",
        "thePtnr": line_code or "",
        "theLane": "",
    }
    resp = session.post(post_url, data=body, headers={"Referer": get_url}, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", class_="ta2")
        if len(tds) < 12:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        voyage, ship_name, line, _in_voy, _out_voy, _cct, etb_atb, etd_atd, _dis, _load, _tp, berth = cells[:12]
        berth = berth.split("(")[0].strip()
        rows.append({
            "source": "HJIT",
            "region": "인천",
            "terminal": "HJIT",
            "berth": berth,
            "voyage": voyage,
            "ship_name": ship_name,
            "line": line,
            "route": "",  # HJIT 선석현황 페이지에는 항로 코드가 없음
            "eta": _parse_dt(etb_atb),
            "etd": _parse_dt(etd_atd),
        })
    return rows


def fetch_gwct(time_range="week", line_code="DYS"):
    """GWCT(광양항서부컨테이너터미널) 선석배정 목록을 가져온다.

    time_range: '3days' | 'week' | 'month'
    line_code: 선사코드 (예: 'DYS'). 빈 문자열/None이면 전체 선사.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]

    이 사이트는 선사코드로 서버측 필터링을 지원하지 않아(폼에 필드 없음)
    전체 목록을 받아온 뒤 클라이언트에서 line_code로 걸러낸다.
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
        if line_code and line != line_code:
            continue
        rows.append({
            "source": "GWCT",
            "region": "광양",
            "terminal": "GWCT",
            "berth": berth,
            "voyage": voyage,
            "ship_name": ship_name,
            "line": line,
            "route": route,
            "eta": _parse_dt(eta.replace("/", "-")),
            "etd": _parse_dt(etd.replace("/", "-")),
        })
    return rows


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
            if line_code and route != line_code:
                continue

            ship_name = fields.get("Name of Vessel", "")
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
                "terminal": "SNCT",
                "berth": berth,
                "voyage": voyage,
                "ship_name": ship_name,
                "line": route,
                "route": route,
                "eta": eta,
                "etd": etd,
            })
    return rows


def fetch_e1ct(line_code="DYS"):
    """E1CT(인천 E1컨테이너터미널) 선석배정현황(T)을 가져온다.

    날짜를 지정하지 않으면 오늘부터 7일치를 그대로 보여주는 페이지라
    파라미터 없이 그대로 가져온다.

    이 사이트의 HTML은 </td> 닫는 태그가 군데군데 빠져 있어(불량 마크업)
    BeautifulSoup의 td 탐색만으로는 값이 밀려서, <td 여는 태그 기준으로
    직접 정규식으로 셀을 분리한다.
    반환: [{terminal, berth, voyage, ship_name, line, eta, etd}, ...]
    """
    url = "http://www.e1ct.co.kr/info/terminal/berthText"
    resp = requests.get(url, headers=HEADERS, timeout=15)
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
        if line_code and line != line_code:
            continue

        rows.append({
            "source": "E1CT",
            "region": "인천",
            "terminal": "E1CT",
            "berth": cells[1][0].split("(")[0].strip() if cells[1] else "",
            "voyage": " ".join(cells[2]) if cells[2] else "",
            "ship_name": cells[4][0] if cells[4] else "",
            "line": line,
            "route": "",  # E1CT 선석배정현황(T) 페이지에는 항로 코드가 없음
            "eta": _parse_dt(cells[5][0].strip("()")) if cells[5] else None,
            "etd": _parse_dt(cells[7][0].strip("()")) if cells[7] else None,
        })
    return rows


def _find_hpnt_table(soup):
    """결과 표를 찾는다. 페이지가 표를 여러 겹으로 감싸고 있어(레이아웃용
    바깥 표들도 안쪽 표의 텍스트를 그대로 포함), '선명' 헤더 셀을 정확히
    일치시켜 그 셀이 속한 가장 안쪽 tr/table을 찾아야 한다."""
    for td in soup.find_all("td"):
        if td.get_text(strip=True) == "선명":
            tr = td.find_parent("tr")
            return tr.find_parent("table") if tr else None
    return None


def fetch_hpnt(line_code="DYS", extra_ship_names=("SM TOKYO",)):
    """HPNT(부산항 허치슨터미널) 선석배정현황을 가져온다.

    line_code: 선사코드. 빈 문자열/None이면 필터하지 않음.
    extra_ship_names: 선사코드와 무관하게 항상 포함시킬 선명(부분일치, 대소문자 무시).
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

            is_line_match = bool(line_code) and line == line_code
            is_extra_ship = extra_ship_names and any(
                name.lower() in ship_name.lower() for name in extra_ship_names)
            if not (is_line_match or is_extra_ship):
                continue

            rows.append({
                "source": "HPNT",
                "region": "부산",
                "terminal": "HPNT",
                "berth": berth,
                "voyage": f"{voyage_code} {inout_voy}".strip(),
                "ship_name": ship_name,
                "line": line,
                "route": route,
                "eta": _parse_dt(eta),
                "etd": _parse_dt(etd),
            })
    return rows


# 여기에 사이트를 추가로 등록하세요. (이름, 함수)
SOURCES = [
    ("BPTC (신선대/감만)", fetch_bptc),
    ("HPNT (부산항허치슨터미널)", fetch_hpnt),
    ("HJIT (한진인천컨테이너터미널)", fetch_hjit),
    ("SNCT (선광신컨테이너터미널)", fetch_snct),
    ("E1CT (E1컨테이너터미널)", fetch_e1ct),
    ("GWCT (광양항서부컨테이너터미널)", fetch_gwct),
]


def fetch_all():
    """등록된 모든 소스를 크롤링해서 하나의 리스트로 합친다."""
    all_rows = []
    errors = []
    for name, fn in SOURCES:
        try:
            all_rows.extend(fn())
        except Exception as e:  # 한 사이트가 실패해도 나머지는 계속 진행
            errors.append(f"{name}: {e}")
    return all_rows, errors


if __name__ == "__main__":
    data, errors = fetch_all()
    print(f"총 {len(data)}건 수집")
    for d in data[:5]:
        print(d)
    if errors:
        print("오류:", errors)
