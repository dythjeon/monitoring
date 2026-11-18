# BerthMonitor Web

exe(Tkinter) 버전과 같은 데이터를 브라우저로 보여주는 웹 버전. `scraper.py` 를 그대로 공유한다.

```
server.py      크롤링 캐시 + HTTP/SSE 서버  (포트 8790)
index.html     화면 (간트 / 본선작업현황 / 국내공컨현황)
run_web.bat    더블클릭 실행
```

## 실행

```
run_web.bat
```
또는 `venv\Scripts\python.exe server.py`

- 이 PC: http://127.0.0.1:8790  (시작하면 브라우저가 자동으로 열린다)
- 같은 네트워크의 다른 PC/휴대폰: 콘솔에 찍히는 `http://<이 PC IP>:8790`
  (Windows 방화벽이 물어보면 "허용". 안 열리면 방화벽 인바운드에 8790 포트 추가)

## 동작 방식

- 서버가 10분마다 6개 터미널 사이트를 한 번씩만 읽어 메모리에 캐시한다.
  브라우저가 몇 개 열려 있어도 터미널 사이트 요청은 서버 한 곳에서만 나간다.
- 새 데이터가 들어오면 SSE(`/api/events`)로 열려 있는 모든 브라우저에 알려 즉시 다시 그린다.
  헤더의 초록 점 = 서버와 실시간 연결 중. SSE가 끊겨도 3분마다 폴링으로 보정한다.
- [새로고침] 버튼은 서버에 즉시 조회를 요청한다 (45초 안에 또 누르면 거절).
- 현재 시각선은 1분마다 이동. 탭은 `?tab=부산`, `?tab=work`, `?tab=empty` 로 링크 지정 가능.

## 조정 포인트

| 항목 | 위치 |
|---|---|
| 포트 | `BERTH_PORT` 환경변수 (기본 8790) |
| 자동 갱신 주기 | `BERTH_REFRESH_SEC` 환경변수 (기본 600) |
| 브라우저 자동 열기 끄기 | `BERTH_NO_BROWSER=1` (서버 전용 PC) |
| 터미널 순서/지역/색 | `index.html` 상단 상수 (gui.py 와 동일하게 유지) |
| 추적 선박/편성표 | `scraper.py` 의 `VESSEL_LANES`, `EXTRA_SHIP_NAMES` (exe 와 공유) |

## API

- `GET /api/data` — 캐시 전체 (선석 `data`, 작업현황 `work`, 공컨 `empty`, 소스별 건수 `stats`, 오류 `errors`)
- `GET /api/events` — SSE. `fetching` / `update` 이벤트
- `POST /api/refresh` — 즉시 조회 요청

## 인터넷에 올리기 (내 PC가 꺼져 있어도 보이게)

어딘가 켜져 있는 컴퓨터가 하나는 있어야 한다. 사내 서버가 없으면 무료 클라우드(Render)에 올린다.
`cloud/` 폴더가 올릴 파일 묶음이다 (`make_cloud.bat` 이 원본에서 복사해 갱신).

1. https://github.com 가입 → New repository (이름 `berthmonitor`, Private) → "uploading an existing file" 로
   `cloud/` 안의 파일 6개를 드래그해서 올리고 Commit.
2. https://render.com 가입(GitHub 로 로그인) → New → Blueprint → 위 저장소 선택 → Apply.
   `render.yaml` 을 읽어 자동으로 설정된다. 5분쯤 뒤 `https://berthmonitor-xxxx.onrender.com` 주소가 생긴다.
3. 그 주소를 직원들에게 공유.

- 무료 플랜은 15분간 아무도 안 보면 잠든다. 다음 사람이 열 때 30초~1분 걸리고, 그때부터 다시 크롤링한다.
  항상 깨어 있게 하려면 Render 유료(월 7달러)로 올리거나 사내 PC/서버에서 `run_web.bat` 을 켜 둔다.
- 코드를 고친 뒤에는 `make_cloud.bat` 실행 → `cloud/` 파일들을 GitHub 에 다시 올리면 Render 가 자동 재배포한다.
- 해외 서버에서 국내 터미널 사이트가 막히면 특정 터미널만 "조회 실패"로 뜬다. 그 경우 국내 리전(예: Oracle Cloud 서울)으로 옮겨야 한다.
- `Dockerfile` 도 들어 있어 Railway / Koyeb / Fly.io 등 다른 호스트에도 그대로 올릴 수 있다.

## GitHub Pages 로 인터넷 주소 만들기 (PC 꺼져 있어도 동작)

GitHub Actions 가 5분마다 `build_static.py` 를 돌려 `site/data.json` 을 만들고 Pages 에 올린다.
서버가 없으므로 실시간 푸시/수동조회 버튼은 없고, 화면은 `./data.json` 을 읽는 정적 모드로 동작한다.

최초 1회:
1. https://github.com/new → 이름 `monitoring`, **Public**, 나머지 체크 해제 → Create
2. 이 폴더에서 `publish_github.bat` 실행 (저장소 주소 입력) → 처음엔 브라우저 로그인 창
3. 저장소 → Settings → Pages → Source 를 **GitHub Actions** 로
4. Actions 탭 → "선석현황 수집·게시" → Run workflow. 2~3분 뒤 주소 생성:
   `https://<아이디>.github.io/monitoring/`

이후 scraper.py / index.html 을 고치면 `publish_github.bat` 만 다시 실행하면 된다.
주의: 60일간 커밋이 없으면 GitHub 이 예약 실행을 멈춘다(Actions 탭에서 다시 켤 수 있음).
