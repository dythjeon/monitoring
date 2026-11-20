"""GitHub Pages 용 정적 사이트 생성.

server.py 와 같은 수집 코드를 한 번만 돌려 site/data.json 을 만들고 index.html 을 옆에 복사한다.
GitHub Actions 가 5분마다 이 파일을 실행하고 site/ 폴더를 Pages 에 올린다.
index.html 은 github.io 에서 열리면 /api/data 대신 ./data.json 을 읽는다(정적 모드).

사용:  python build_static.py            -> site/ 폴더 생성
"""
import base64
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from server import collect, REFRESH_SEC          # noqa: E402  (server import 는 부작용 없음)
from scraper import SOURCES                      # noqa: E402
from surcharge import fetch_locations            # noqa: E402
import changes                                   # noqa: E402
import history                                   # noqa: E402

OUT = os.path.join(HERE, "site")
SUR_ENABLED = False      # Basic Surcharge 탭 보류(2026-11-19). index.html 의 SUR_ENABLED 와 같이 켠다.

# 비밀번호 잠금. 환경변수 SITE_PASSWORD (GitHub 저장소 Secret) 가 있으면 data.json 을 AES-GCM 으로
# 암호화해서 올린다. 저장소와 사이트가 공개여도 비밀번호 없이는 내용을 읽을 수 없다.
# 비밀번호가 없으면 예전처럼 평문으로 올라가고 잠금 화면도 뜨지 않는다.
ENC_SALT = "dys-monitoring-v1"
ENC_ITER = 200000


def encrypt_payload(payload, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM   # 잠금을 쓸 때만 필요
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), ENC_SALT.encode("utf-8"), ENC_ITER)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"enc": 1, "salt": ENC_SALT, "iter": ENC_ITER, "iv": b64(iv), "ct": b64(ct)}



CARRY_HOURS = 6          # 조회에 실패한 터미널은 이 시간까지 직전 수집값을 대신 보여 준다


def carry_over(prev, rows, empty, errors, stats, now):
    """이번에 조회가 실패한 터미널의 선석·공컨 자료를 직전 게시본에서 가져와 채운다.

    해외에 있는 GitHub 서버에서 국내 터미널 사이트 접속이 가끔 시간 초과되는데, 그때마다 그 터미널이 화면에서
    통째로 비면 '배가 없는 것'과 구분이 안 된다. 가져온 행에는 stale(원래 수집 시각)을 붙이고, CARRY_HOURS 가
    지나도록 계속 실패하면 더는 붙들지 않는다. stats 의 None(조회 실패)은 그대로 둔다."""
    if not prev:
        return rows, empty, errors
    prev_at = prev.get("updated_at") or ""
    limit = (now - timedelta(hours=CARRY_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    notes = []

    def pick(old_rows, have, key):
        out, names = [], {}
        for r in old_rows or []:
            since = r.get("stale") or prev_at
            if r.get(key) in have or not since or since < limit:
                continue
            out.append(dict(r, stale=since))
            names[r.get("terminal") or r.get(key)] = since
        return out, names

    failed = [n for n, c in stats if c is None]      # 예: "선광신항 (선광신컨테이너터미널)", "BPTC (신선대/감만)"
    if failed:
        # stats 의 이름과 행의 source 값은 서로 달라서, 터미널 이름이나 source 가 실패한 이름 안에 들어 있는지로 짝짓는다
        old = [r for r in prev.get("data") or []
               if any((r.get("terminal") or "#없음#") in n or (r.get("source") or "#없음#") in n for n in failed)]
        add, names = pick(old, {r.get("source") for r in rows}, "source")
        rows = list(rows) + add
        notes += [f"{t} 선석: 조회 실패로 {s[5:16]} 수집값을 표시 중" for t, s in names.items()]
    if any(str(e).startswith("국내공컨현황") for e in errors):
        add, names = pick(prev.get("empty"), {r.get("terminal") for r in empty}, "terminal")
        empty = list(empty) + add
        notes += [f"{t} 공컨: 조회 실패로 {s[5:16]} 수집값을 표시 중" for t, s in names.items()]
    return rows, empty, list(errors) + notes


def previous_payload(password):
    """직전에 게시한 data.json 전체. GitHub Actions 는 실행할 때마다 빈 상태로 시작하므로, 스케줄 변경의
    기준값·기록을 이어 가거나 실패한 터미널의 직전 값을 쓰려면 이미 올라가 있는 파일을 다시 읽어야 한다.
    못 읽으면 None (그러면 이번 수집을 새 기준으로 삼고 변경 기록은 처음부터 다시 쌓인다)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")          # "아이디/저장소"
    url = os.environ.get("SITE_URL") or (f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}" if "/" in repo else "")
    if not url:
        return None
    try:
        import requests
        j = requests.get(url.rstrip("/") + "/data.json", params={"t": int(datetime.now().timestamp())}, timeout=20).json()
        if j.get("enc"):
            if not password:
                return None
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), j["salt"].encode("utf-8"), int(j["iter"]))
            j = json.loads(AESGCM(key).decrypt(base64.b64decode(j["iv"]), base64.b64decode(j["ct"]), None))
        return j
    except Exception as e:
        print("   ! 직전 게시본을 읽지 못함:", e)
        return None


def update_history(rows, stats, password):
    """항차별 이력(history.py)을 hist/history.json 에 이어 쌓고 site/ 에 같이 올린다.

    GitHub Actions 에서는 hist/ 가 저장소의 history 브랜치를 받아 둔 폴더다(update.yml 이 받아 오고, 수집 뒤
    달라졌으면 다시 올린다). Pages 에 올라간 파일을 다시 읽는 방식과 달리 주소가 바뀌거나 배포가 한 번 실패해도
    이력이 끊기지 않는다. 기존 파일을 못 읽으면(비밀번호 불일치 등) 덮어쓰지 않고 그대로 둔다."""
    if os.environ.get("HISTORY_SKIP") == "1":
        print("   ! 항차 이력: history 브랜치를 받지 못해 이번에는 건너뜀")
        return
    hist_dir = os.environ.get("HISTORY_DIR") or os.path.join(HERE, "hist")
    path = os.path.join(hist_dir, "history.json")
    state = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                j = json.load(f)
            if j.get("enc"):
                if not password:
                    raise ValueError("암호화된 이력인데 SITE_PASSWORD 가 없음")
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), j["salt"].encode("utf-8"), int(j["iter"]))
                j = json.loads(AESGCM(key).decrypt(base64.b64decode(j["iv"]), base64.b64decode(j["ct"]), None))
            state = j
        except Exception as e:
            print("   ! 항차 이력을 읽지 못해 이번에는 기록하지 않음:", e)
            shutil.copyfile(path, os.path.join(OUT, "history.json"))
            return
    state, changed = history.update(state, rows, stats)
    if changed or not os.path.exists(path):
        os.makedirs(hist_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(encrypt_payload(state, password) if password else state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    shutil.copyfile(path, os.path.join(OUT, "history.json"))
    print(f"   항차 이력 {len(state['voy'])}건" + (" (갱신)" if changed else ""))


def main():
    rows, work, empty, errors, stats, typhoons, fx = collect()
    password = os.environ.get("SITE_PASSWORD", "").strip()
    state_file = os.path.join(HERE, "changes_state.json")
    prev = previous_payload(password)
    rows, empty, errors = carry_over(prev, rows, empty, errors, stats, datetime.now())
    chg = changes.update((prev or {}).get("changes") or changes.load_file(state_file), rows, stats)
    changes.save_file(state_file, chg)
    sur_locs = None
    if SUR_ENABLED:                       # Basic Surcharge 탭의 Select Location 목록
        try:
            sur_locs = fetch_locations()
        except Exception as e:
            errors = list(errors) + [f"부대비용 지역목록: {e}"]

    now = datetime.now()
    payload = {
        "generation": int(now.timestamp()),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "fetching": False,
        "data": rows, "work": work, "empty": empty,
        "errors": errors, "stats": stats, "typhoons": typhoons, "fx": fx, "sur_locs": sur_locs, "changes": chg,
        "refresh_sec": int(os.environ.get("BERTH_STATIC_REFRESH_SEC", "1800")),   # 아티팩트 재게시 주기(30분)
        "server_now": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "sources": [name for name, _fn in SOURCES],
        "static": True,
    }

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
        json.dump(encrypt_payload(payload, password) if password else payload, f, ensure_ascii=False)
    shutil.copyfile(os.path.join(HERE, "index.html"), os.path.join(OUT, "index.html"))
    shutil.copyfile(os.path.join(HERE, "coast.json"), os.path.join(OUT, "coast.json"))
    shutil.copyfile(os.path.join(HERE, "gate.jpg"), os.path.join(OUT, "gate.jpg"))          # 비밀번호 화면 배경
    if os.path.exists(os.path.join(HERE, "tariff.enc.json")):        # Barge 요율(make_tariff.py 가 암호화해 둔 것)
        shutil.copyfile(os.path.join(HERE, "tariff.enc.json"), os.path.join(OUT, "tariff.enc.json"))
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    try:
        update_history(rows, stats, password)
    except Exception as e:                      # 이력 때문에 사이트 갱신이 멈추면 안 된다
        print("   ! 항차 이력 오류:", e)
    # claude.ai 아티팩트용: 게시 시 문서 뼈대(doctype/html/head/body)를 호스트가 씌우므로 벗겨낸 본문만 둔다.
    with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = re.sub(r"<!DOCTYPE[^>]*>|</?html[^>]*>|</?head>|</?body[^>]*>|<meta charset[^>]*>|<meta name=.viewport.[^>]*>",
                  "", html, flags=re.I)
    # 아티팩트 CSP 는 스타일시트를 fonts.googleapis.com 에서만 허용한다. Pretendard 대신 Noto Sans KR.
    html = re.sub(r'<link rel="stylesheet" href="https://cdn\.jsdelivr\.net/gh/orioncactus/pretendard[^>]*>',
                  '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700&display=swap">',
                  html)
    html = html.replace("Pretendard", "'Noto Sans KR'")
    with open(os.path.join(OUT, "artifact.html"), "w", encoding="utf-8") as f:
        f.write(html.strip() + chr(10))

    ok = sum(1 for _n, c in stats if c is not None)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] site/ 생성: 선석 {len(rows)}건 · 작업 {len(work)}건 · "
          f"{'암호화됨' if password else '잠금 없음(평문)'} · 공컨 {len(empty)}건 · 조회 {ok}/{len(stats)} · 오류 {len(errors)}건")
    for e in errors:
        print("   !", e)


if __name__ == "__main__":
    main()
