# -*- coding: utf-8 -*-
"""항차별 이력.

changes.py 는 "최근 72시간 동안 무엇이 바뀌었나"(알림용)이고, 이 모듈은 "이 항차가 처음 예정에서
출항까지 얼마나 바뀌었나"(조회용)를 오래 보관한다.

항차 하나(터미널 + 선명 + 항차)마다 기록 하나:
  eta0/etd0/berth0 = 처음 목록에 올라왔을 때의 예정
  eta/etd/berth    = 가장 최근 값 (출항한 뒤에는 터미널이 마지막으로 보여 준 값 = 사실상 실제 시각)
  log              = 바뀔 때마다 [감지 시각, 접안, 출항, 선석]
  closed/end       = 목록에서 빠진 시각 / "departed"(출항) 또는 "removed"(접안 전에 일정이 없어짐)

파일이 5분마다 다시 쓰이지 않도록, 실제로 무언가 바뀐 때에만 상태가 달라진다
(그래서 '마지막으로 본 시각' 같은 값은 두지 않는다).
"""
from datetime import datetime, timedelta

KEEP_DAYS = 400          # 끝난 항차 보관 기간
MIN_SHIFT_MIN = 10       # 이보다 작은 시각 변동은 기록하지 않는다
GONE_WAIT_HOURS = 6      # 접안 전인데 목록에서 사라진 경우, 이만큼 계속 안 보여야 '빠짐'으로 확정
DEPART_GRACE_HOURS = 2   # 출항 예정 시각이 이만큼 지나고 목록에 없으면 출항으로 본다
REOPEN_DAYS = 30         # 끝난 항차가 이 기간 안에 같은 이름으로 다시 보이면 같은 항차로 이어 붙인다
MAX_LOG = 300
ISO = "%Y-%m-%dT%H:%M:%S"


def _key(terminal, ship, voyage):
    return "|".join([terminal or "", ship or "", voyage or ""])


def _dt(s):
    return datetime.strptime(s[:19], ISO)


def _minutes(a, b):
    return int(round((_dt(b) - _dt(a)).total_seconds() / 60))


def update(state, rows, stats, now=None):
    """rows/stats 는 changes.update() 와 같다. (새 상태, 달라졌는지) 를 돌려준다."""
    now = now or datetime.now()
    stamp = now.strftime(ISO)
    state = state if isinstance(state, dict) and isinstance(state.get("voy"), list) else {"v": 1, "since": stamp, "voy": []}
    voy = state["voy"]
    changed = False

    cur = {}
    for r in rows:
        if not r.get("eta") or not r.get("etd"):
            continue
        k = _key(r.get("terminal"), r.get("ship_name"), r.get("voyage"))
        if k not in cur or r["eta"] < cur[k]["eta"]:
            cur[k] = r

    # 키 -> 이어 붙일 기록(열려 있는 것, 없으면 최근에 닫힌 것)
    reopen_cut = (now - timedelta(days=REOPEN_DAYS)).strftime(ISO)
    index = {}
    for rec in voy:
        k = _key(rec.get("terminal"), rec.get("ship"), rec.get("voyage"))
        if rec.get("closed") and rec["closed"] < reopen_cut:
            continue
        old = index.get(k)
        if old is None or (old.get("closed") and not rec.get("closed")) or rec.get("first", "") > old.get("first", ""):
            index[k] = rec

    for k, r in cur.items():
        berth = str(r.get("berth") or "")
        rec = index.get(k)
        if rec is None:
            voy.append({"terminal": r.get("terminal"), "region": r.get("region"), "source": r.get("source"),
                        "ship": r.get("ship_name"), "voyage": r.get("voyage") or "", "line": r.get("line") or "",
                        "route": r.get("route_label") or r.get("route") or "",
                        "first": stamp, "eta0": r["eta"], "etd0": r["etd"], "berth0": berth,
                        "eta": r["eta"], "etd": r["etd"], "berth": berth, "log": []})
            changed = True
            continue
        if rec.get("closed") or rec.get("miss"):          # 다시 보인다
            rec.pop("closed", None); rec.pop("end", None); rec.pop("miss", None)
            changed = True
        if (abs(_minutes(rec["eta"], r["eta"])) >= MIN_SHIFT_MIN or abs(_minutes(rec["etd"], r["etd"])) >= MIN_SHIFT_MIN
                or rec.get("berth", "") != berth):
            rec["eta"], rec["etd"], rec["berth"] = r["eta"], r["etd"], berth
            rec["log"] = (rec.get("log") or [])[-(MAX_LOG - 1):] + [[stamp, r["eta"], r["etd"], berth]]
            changed = True
        route = r.get("route_label") or r.get("route") or ""
        if route and not rec.get("route"):
            rec["route"] = route
            changed = True

    # 목록에서 안 보이는 항차. stats 의 이름("BPTC (신선대/감만)")은 행의 source("BPTC")와 달라 짝지을 수 없으므로,
    # 어느 소스든 조회가 실패한 회차에는 이번에 한 건도 안 나온 소스의 항차를 판단하지 않는다.
    any_failed = any(c is None for _n, c in (stats or []))
    live = {r.get("source") for r in rows}
    depart_cut = (now - timedelta(hours=DEPART_GRACE_HOURS)).strftime(ISO)
    gone_cut = (now - timedelta(hours=GONE_WAIT_HOURS)).strftime(ISO)
    for rec in voy:
        if rec.get("closed"):
            continue
        if _key(rec.get("terminal"), rec.get("ship"), rec.get("voyage")) in cur or (any_failed and rec.get("source") not in live):
            continue
        if rec["etd"] <= depart_cut:                      # 출항 예정이 지났고 목록에서 빠짐 = 출항
            rec["closed"], rec["end"] = stamp, "departed"
            rec.pop("miss", None)
            changed = True
        elif rec["eta"] > stamp:                          # 접안 전인데 사라짐: 잠깐의 사이트 오류일 수 있어 기다린다
            if not rec.get("miss"):
                rec["miss"] = stamp
                changed = True
            elif rec["miss"] <= gone_cut:
                rec["closed"], rec["end"] = stamp, "removed"
                rec.pop("miss", None)
                changed = True
        # 접안 중인데 안 보이는 경우는 출항 예정 시각 + 여유가 지날 때까지 둔다

    keep_cut = (now - timedelta(days=KEEP_DAYS)).strftime(ISO)
    kept = [rec for rec in voy if not (rec.get("closed") and rec["closed"] < keep_cut)]
    if len(kept) != len(voy):
        changed = True
    state["voy"] = kept
    state.setdefault("since", stamp)
    if changed:
        state["updated"] = stamp
    return state, changed
