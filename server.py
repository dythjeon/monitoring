# -*- coding: utf-8 -*-
"""BerthMonitor 웹 서버.

  1) 백그라운드 스레드가 scraper.fetch_all() 등을 주기적으로 돌려 메모리에 캐시
  2) 작은 HTTP 서버가 index.html + /api/data 를 내려주고,
     /api/events (SSE) 로 "새 데이터 있음"을 접속 중인 브라우저 전부에 밀어준다.

사용법:
  python server.py            ->  http://127.0.0.1:8790  (같은 네트워크 PC는 http://<이 PC IP>:8790)

환경변수:
  BERTH_PORT         포트 (기본 8790)
  BERTH_REFRESH_SEC  자동 갱신 주기(초, 기본 600 = 10분)
  BERTH_NO_BROWSER   1 이면 시작할 때 브라우저를 열지 않는다 (서버 전용 PC에서)
"""
import json
import os
import queue
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from scraper import (SOURCES, fetch_all, fetch_bptc_work_status, fetch_bptc_empty_status,
                     fetch_gwct_empty_status, fetch_e1ct_empty_status, fetch_hjit_empty_status,
                     lane_of, LANE_COUNTRY, LANE_BY_BOUND)
import re
from typhoon import fetch_typhoons

try:                                   # 한글 콘솔 출력 깨짐 방지
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
# 클라우드 호스트(Render 등)는 PORT 환경변수로 포트를 지정한다. 그때는 브라우저도 열지 않는다.
IN_CLOUD = bool(os.environ.get("PORT"))
PORT = int(os.environ.get("PORT") or os.environ.get("BERTH_PORT", "8790"))
REFRESH_SEC = int(os.environ.get("BERTH_REFRESH_SEC", "600"))
MIN_MANUAL_GAP_SEC = 45     # 새로고침 버튼 연타로 터미널 사이트를 두들기지 않게

# gui.py 와 같은 값. 간트 행 순서/지역은 화면(index.html)이 갖고 있고,
# 여기서는 행선지 분류만 서버에서 해서 내려준다(편성표가 scraper 에 있으므로).
_BOUND_RE = re.compile(r"\d\s*([EW])(?![A-Z])")


def _bound_country(voyage):
    m = _BOUND_RE.search((voyage or "").upper())
    return {"E": "일본", "W": "중국"}.get(m.group(1)) if m else None


def _classify_country(d):
    info = lane_of(d.get("ship_name"))
    if not info:
        return None
    lane = info[0]
    if lane in LANE_COUNTRY:
        return LANE_COUNTRY[lane]
    if lane in LANE_BY_BOUND:
        return _bound_country(d.get("voyage"))
    return None


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None


# ---------------------------------------------------------------- 캐시

STATE = {
    "generation": 0,
    "updated_at": None,
    "fetching": False,
    "data": [], "work": [], "empty": [], "errors": [], "stats": [],
    "typhoons": None,          # typhoon.fetch_typhoons() 결과 (태풍 탭)
}
LOCK = threading.Lock()
WAKE = threading.Event()           # 수동 새로고침 요청이 오면 깨운다
LISTENERS = set()                  # SSE 구독자 큐
LISTENERS_LOCK = threading.Lock()
LAST_FETCH_STARTED = 0.0


def _notify(event):
    with LISTENERS_LOCK:
        for q in list(LISTENERS):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


def collect():
    """gui.py 의 _fetch_and_draw 와 같은 순서로 모든 소스를 읽는다."""
    try:
        data, errors, stats = fetch_all()
    except Exception as e:
        data, errors, stats = [], [str(e)], []
    try:
        work = fetch_bptc_work_status()
    except Exception as e:
        work = []
        errors = list(errors) + [f"본선작업현황: {e}"]
    empty = []
    for label, fn in (("국내공컨현황", fetch_bptc_empty_status),
                      ("국내공컨현황(광양서부)", fetch_gwct_empty_status),
                      ("국내공컨현황(E1CT)", fetch_e1ct_empty_status),
                      ("국내공컨현황(HJIT)", fetch_hjit_empty_status)):
        try:
            empty += fn()
        except Exception as e:
            errors = list(errors) + [f"{label}: {e}"]

    try:
        typhoons = fetch_typhoons()
        for e in typhoons.get("errors", []):
            errors = list(errors) + [f"태풍: {e}"]
    except Exception as e:
        typhoons = None
        errors = list(errors) + [f"태풍: {e}"]

    rows = []
    for d in data:
        info = lane_of(d.get("ship_name"))
        rows.append({
            "source": d.get("source"), "region": d.get("region"),
            "terminal": d.get("terminal"), "berth": d.get("berth"),
            "voyage": d.get("voyage"), "ship_name": d.get("ship_name"),
            "line": d.get("line"), "route": d.get("route"),
            "eta": _iso(d.get("eta")), "etd": _iso(d.get("etd")),
            "lane": list(info) if info else None,
            "country": _classify_country(d),
        })
    work_rows = [dict(w, eta=_iso(w.get("eta")), etd=_iso(w.get("etd"))) for w in work]
    return rows, work_rows, empty, errors, [list(s) for s in stats], typhoons


def refresh_once():
    global LAST_FETCH_STARTED
    with LOCK:
        if STATE["fetching"]:
            return
        STATE["fetching"] = True
        LAST_FETCH_STARTED = time.time()
    _notify({"type": "fetching"})
    t0 = time.time()
    try:
        rows, work, empty, errors, stats, typhoons = collect()
        with LOCK:
            STATE.update(data=rows, work=work, empty=empty, errors=errors, stats=stats, typhoons=typhoons,
                         updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            STATE["generation"] += 1
            gen = STATE["generation"]
        ok = sum(1 for _n, c in stats if c is not None)
        print(f"[{datetime.now():%H:%M:%S}] 갱신 #{gen}: 선석 {len(rows)}건 · 작업 {len(work)}건 · "
              f"공컨 {len(empty)}건 · 조회 {ok}/{len(stats)} · 오류 {len(errors)}건 · {time.time() - t0:.1f}s")
        for e in errors:
            print("   !", e)
    finally:
        with LOCK:
            STATE["fetching"] = False
    _notify({"type": "update", "generation": gen})


def refresh_loop():
    while True:
        refresh_once()
        WAKE.wait(REFRESH_SEC)
        WAKE.clear()


# ---------------------------------------------------------------- HTTP

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/coast.json":
            with open(os.path.join(HERE, "coast.json"), "rb") as f:
                self._send(200, f.read(), "application/json; charset=utf-8")
        elif path == "/api/data":
            with LOCK:
                payload = dict(STATE, refresh_sec=REFRESH_SEC,
                               server_now=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                               sources=[name for name, _fn in SOURCES])
            self._json(payload)
        elif path == "/api/events":
            self._sse()
        elif path == "/healthz":
            self._send(200, "ok", "text/plain; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/refresh":
            gap = time.time() - LAST_FETCH_STARTED
            with LOCK:
                busy = STATE["fetching"]
            if busy:
                self._json({"ok": True, "status": "already"})
            elif gap < MIN_MANUAL_GAP_SEC:
                self._json({"ok": False, "status": "cooldown",
                            "wait": int(MIN_MANUAL_GAP_SEC - gap)})
            else:
                WAKE.set()
                self._json({"ok": True, "status": "started"})
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def _sse(self):
        q = queue.Queue(maxsize=50)
        with LISTENERS_LOCK:
            LISTENERS.add(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            with LOCK:
                hello = {"type": "hello", "generation": STATE["generation"],
                         "fetching": STATE["fetching"]}
            self.wfile.write(f"data: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")       # 연결 유지용 주석 줄
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            with LISTENERS_LOCK:
                LISTENERS.discard(q)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    threading.Thread(target=refresh_loop, daemon=True).start()

    local = f"http://127.0.0.1:{PORT}"
    print("=" * 60)
    print("  BerthMonitor Web")
    print(f"  이 PC        : {local}")
    print(f"  같은 네트워크 : http://{lan_ip()}:{PORT}")
    print(f"  자동 갱신     : {REFRESH_SEC // 60}분마다   (Ctrl+C 로 종료)")
    print("=" * 60)
    if not IN_CLOUD and os.environ.get("BERTH_NO_BROWSER", "") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(local)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
