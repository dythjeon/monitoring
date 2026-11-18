"""GitHub Pages 용 정적 사이트 생성.

server.py 와 같은 수집 코드를 한 번만 돌려 site/data.json 을 만들고 index.html 을 옆에 복사한다.
GitHub Actions 가 10분마다 이 파일을 실행하고 site/ 폴더를 Pages 에 올린다.
index.html 은 github.io 에서 열리면 /api/data 대신 ./data.json 을 읽는다(정적 모드).

사용:  python build_static.py            -> site/ 폴더 생성
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from server import collect, REFRESH_SEC          # noqa: E402  (server import 는 부작용 없음)
from scraper import SOURCES                      # noqa: E402

OUT = os.path.join(HERE, "site")



def main():
    rows, work, empty, errors, stats, typhoons = collect()

    now = datetime.now()
    payload = {
        "generation": int(now.timestamp()),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "fetching": False,
        "data": rows, "work": work, "empty": empty,
        "errors": errors, "stats": stats, "typhoons": typhoons,
        "refresh_sec": int(os.environ.get("BERTH_STATIC_REFRESH_SEC", "300")),   # 아티팩트 재게시 주기(5분)
        "server_now": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "sources": [name for name, _fn in SOURCES],
        "static": True,
    }

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    shutil.copyfile(os.path.join(HERE, "index.html"), os.path.join(OUT, "index.html"))
    shutil.copyfile(os.path.join(HERE, "coast.json"), os.path.join(OUT, "coast.json"))
    open(os.path.join(OUT, ".nojekyll"), "w").close()
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
          f"공컨 {len(empty)}건 · 조회 {ok}/{len(stats)} · 오류 {len(errors)}건")
    for e in errors:
        print("   !", e)


if __name__ == "__main__":
    main()
