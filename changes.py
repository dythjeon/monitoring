# -*- coding: utf-8 -*-
"""스케줄 변경 감지.

수집할 때마다 선석 배정(rows)을 "마지막으로 알린 값"(base)과 비교해서, 달라진 것을 사건(events)으로 쌓는다.

  - chg  : 접안/출항 시각이 MIN_SHIFT_MIN 분 이상 바뀌었거나 선석이 바뀜
  - new  : 처음 보이는 배
  - gone : 아직 접안 전인데 목록에서 사라짐

직전 수집이 아니라 "마지막으로 알린 값"과 비교하는 이유: 터미널이 10분씩 여러 번 미루면 직전 수집과는
매번 차이가 작아 한 번도 안 걸린다. 알린 값을 기준으로 두면 누적 1시간이 되는 순간 잡힌다.

상태(state) = {"base": {키: 스냅샷}, "events": [사건, ...]} 는 호출하는 쪽이 보관한다
(server.py 는 파일, build_static.py 는 직전에 게시한 data.json 안).
"""
import json
import os
from datetime import datetime, timedelta

KEEP_HOURS = 72          # 사건 보관 기간
MAX_EVENTS = 400
MIN_SHIFT_MIN = 30       # 이보다 작은 시각 변동은 알리지 않는다
ISO = "%Y-%m-%dT%H:%M:%S"


def _key(r):
    return "|".join([r.get("terminal") or "", r.get("ship_name") or "", r.get("voyage") or ""])


def _dt(s):
    return datetime.strptime(s[:19], ISO)


def _minutes(a, b):
    """b - a (분). 둘 다 ISO 문자열."""
    return int(round((_dt(b) - _dt(a)).total_seconds() / 60))


def update(state, rows, stats, now=None):
    """rows = server.collect() 가 돌려준 선석 행(eta/etd 는 ISO 문자열), stats = [(소스 이름, 건수 또는 None), ...]

    새 상태를 돌려준다. state 가 없으면(처음) 지금 값을 기준으로만 삼고 사건은 만들지 않는다
    (안 그러면 전부 '신규'로 쏟아진다).
    """
    now = now or datetime.now()
    stamp = now.strftime(ISO)
    first = not state or not isinstance(state.get("base"), dict)
    base = dict((state or {}).get("base") or {})
    events = list((state or {}).get("events") or [])

    cur = {}
    for r in rows:
        if not r.get("eta") or not r.get("etd"):
            continue
        k = _key(r)
        if k not in cur or r["eta"] < cur[k]["eta"]:
            cur[k] = r

    def snap(r):
        return {"eta": r["eta"], "etd": r["etd"], "berth": str(r.get("berth") or ""), "terminal": r.get("terminal"),
                "region": r.get("region"), "ship": r.get("ship_name"), "voyage": r.get("voyage") or "",
                "line": r.get("line") or "", "source": r.get("source")}

    def event(kind, s, **extra):
        e = {"t": stamp, "kind": kind, "terminal": s["terminal"], "region": s["region"], "ship": s["ship"],
             "voyage": s["voyage"], "line": s["line"]}
        e.update(extra)
        return e

    fresh = []
    for k, r in cur.items():
        s = snap(r)
        b = base.get(k)
        if b is None:
            if not first:
                fresh.append(event("new", s, eta1=s["eta"], etd1=s["etd"], berth1=s["berth"]))
            base[k] = s
            continue
        d_eta, d_etd = _minutes(b["eta"], s["eta"]), _minutes(b["etd"], s["etd"])
        if abs(d_eta) >= MIN_SHIFT_MIN or abs(d_etd) >= MIN_SHIFT_MIN or b.get("berth", "") != s["berth"]:
            fresh.append(event("chg", s, eta0=b["eta"], etd0=b["etd"], berth0=b.get("berth", ""),
                               eta1=s["eta"], etd1=s["etd"], berth1=s["berth"], d_eta=d_eta, d_etd=d_etd))
            base[k] = s

    # 사라진 배. 조회가 실패했거나(건수 None) 평소 여러 척 나오던 소스가 통째로 비면 터미널 사이트 문제일 수 있으니 판단을 미룬다.
    any_failed = any(c is None for _n, c in stats or [])
    live = {r.get("source") for r in rows}
    per_source = {}
    for b in base.values():
        per_source[b.get("source")] = per_source.get(b.get("source"), 0) + 1
    stale = (now - timedelta(days=1)).strftime(ISO)
    for k in [k for k in base if k not in cur]:
        b = base[k]
        if b["etd"] < stale:                      # 이미 끝난 지 하루 넘은 기준값은 조용히 지운다
            del base[k]
            continue
        if b.get("source") not in live and (any_failed or per_source.get(b.get("source"), 0) >= 3):
            continue
        if b["eta"] > stamp:                      # 아직 접안 전이었는데 없어짐. (접안 뒤에 없어지는 건 출항이라 정상)
            fresh.append(event("gone", b, eta0=b["eta"], etd0=b["etd"], berth0=b.get("berth", "")))
        del base[k]

    # 같은 내용이 이미 기록돼 있으면 다시 넣지 않는다(직전 상태를 못 읽어 한 번 더 비교하게 된 경우 대비)
    sig = lambda e: (e["kind"], e["terminal"], e["ship"], e["voyage"], e.get("eta0"), e.get("etd0"), e.get("berth0"),
                     e.get("eta1"), e.get("etd1"), e.get("berth1"))
    seen = {sig(e) for e in events}
    events += [e for e in fresh if sig(e) not in seen]
    cutoff = (now - timedelta(hours=KEEP_HOURS)).strftime(ISO)
    events = [e for e in events if e.get("t", "") >= cutoff][-MAX_EVENTS:]
    return {"base": base, "events": events}


def load_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_file(path, state):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
