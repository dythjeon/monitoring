import re
import threading
import tkinter as tk
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from scraper import (fetch_all, fetch_bptc_work_status, fetch_bptc_empty_status,
                     fetch_gwct_empty_status, fetch_e1ct_empty_status, fetch_hjit_empty_status)
from assets import LOGO_PNG_B64

matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False

REFRESH_INTERVAL_SEC = 1800  # 30분마다 자동 갱신
GANTT_WINDOW_DAYS = 7        # 간트 차트에 표시할 최대 기간(고정값)

# ---- surff.kr 느낌의 라이트/미니멀 팔레트 ----
COLOR_BG = "#f4f5f8"          # 페이지 배경 (아주 옅은 회색)
COLOR_CARD = "#ffffff"        # 카드 배경
COLOR_BORDER = "#edeef2"      # 카드 테두리 (그림자와 함께 아주 옅게)
COLOR_TEXT = "#0b0f19"        # 본문/제목 텍스트
COLOR_MUTED = "#8a8f98"       # 보조 텍스트
COLOR_PRIMARY = "#2f6fed"     # 포인트 블루
COLOR_PRIMARY_DARK = "#1d4fc4"  # 포인트 블루(진한 톤, 그라디언트/그림자용)
COLOR_PRIMARY_SOFT = "#eaf1fe"
COLOR_TRACK = "#eef0f4"       # 세그먼트 탭 트랙 배경
COLOR_ROW_ALT = "#f7f8fb"     # 간트 행 줄무늬(짝수 행)
COLOR_GRID_MAJOR = "#dfe2e8"  # 날짜 경계 세로선
COLOR_GRID_MINOR = "#eef0f4"  # 6시간 보조 세로선
COLOR_SHADOW = "#c3c8d4"      # 카드 아래 그림자 베이스 톤


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _blend(color_a, color_b, t):
    """color_a와 color_b를 t(0~1) 비율로 섞은 색을 반환한다. t=0이면 color_a."""
    a, b = _hex_to_rgb(color_a), _hex_to_rgb(color_b)
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))

REGION_COLOR = {"부산": "#2f6fed", "인천": "#f5a524", "광양": "#12b886"}
REGION_LABEL = {"전체": "전체", "부산": "부산항 · BPTC·HPNT",
                "인천": "인천항 · HJIT·SNCT·E1CT", "광양": "광양항 · GWCT"}
WORK_TAB_KEY = "작업현황"
EMPTY_TAB_KEY = "공컨현황"
# 탭 pill은 폭이 좁아 항구 이름만 짧게 표기한다 (소스 목록은 통계 카드에서 보여준다).
TAB_LABEL = {"전체": "전체", "부산": "부산항", "인천": "인천항", "광양": "광양항",
             WORK_TAB_KEY: "본선작업현황", EMPTY_TAB_KEY: "국내공컨현황"}

TAB_KEYS = ["전체", "부산", "인천", "광양"]
ALL_TAB_KEYS = TAB_KEYS + [WORK_TAB_KEY, EMPTY_TAB_KEY]
EMPTY_CARRIER_COLOR = {"DYS": COLOR_PRIMARY}
# 국내공컨현황 표의 컬럼 구성: (그룹명, [(scraper counts 키, 하위 라벨), ...]).
# 2단 헤더로 그려진다: 1행엔 그룹명(DC/OT/FR)이 colspan으로, 2행엔 하위
# 사이즈 라벨이 온다. DC 그룹의 "4H"는 40'HC(HQ40)를 뜻한다 — 하이큐브는
# 드라이밴의 변형이라 관행상 DC 라인에 같이 묶어서 본다. HQ20/HQ45,
# 냉동컨테이너(RF)는 이 페이지에 데이터가 없거나(RF) 사용자가 표에서
# 뺀 항목이라 표시하지 않는다.
EMPTY_TABLE_GROUPS = [
    ("DC", [("GP20", "20'"), ("GP40", "40'"), ("HQ40", "4H")]),
    ("OT", [("OT20", "20'"), ("OT40", "40'")]),
    ("FR", [("FL20", "20'"), ("FL40", "40'")]),
]
FONT = "맑은 고딕"
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# ---- 항로(Route) 코드 -> 다음 기항 국가/권역 분류 ----
# 사용자가 직접 알려준 매핑. NCK는 항차 번호 끝 알파벳으로 방향을 구분한다
# (예: "2618E/2619W"의 마지막 글자 W=중국행, E=일본행).
ROUTE_REGION = {
    "KHX": "동남아", "KHP": "동남아", "BIH": "동남아",
    "IHS": "동남아", "IHP": "동남아", "IQH": "동남아",
    "ISS": "중국", "BNJ": "중국",
    "KRX": "러시아", "KRV": "러시아",
}
COUNTRY_KEYS = ["일본", "중국", "동남아", "러시아"]
COUNTRY_COLOR = {"일본": "#e64980", "중국": "#fa5252", "동남아": "#12b886", "러시아": "#7048e8"}


def _classify_country(d):
    route = (d.get("route") or "").strip().upper()
    if not route:
        return None
    if route == "NCK":
        voyage = (d.get("voyage") or "").strip().upper()
        last = voyage[-1:] if voyage else ""
        if last == "W":
            return "중국"
        if last == "E":
            return "일본"
        return None
    return ROUTE_REGION.get(route)


def _progress_color(pct):
    if pct < 30:
        return "#fa5252"
    elif pct < 70:
        return "#f5a524"
    return "#12b886"


def _round_rect(canvas, x1, y1, x2, y2, radius=14, **kwargs):
    r = radius
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Card:
    """둥근 모서리 카드. .inner 프레임에 위젯을 채우면 된다."""

    def __init__(self, parent, bg_page=COLOR_BG, radius=16, pad=16):
        self.radius = radius
        self.pad = pad
        # tk.Canvas의 lift()/lower()/tkraise()는 캔버스 아이템용으로 덮어써져 있어
        # 위젯 스태킹(탭 전환)에 쓸 수 없다. 그래서 스태킹 가능한 일반 Frame으로
        # 감싸고, 그 Frame을 widget()으로 노출한다.
        self.container = tk.Frame(parent, bg=bg_page)
        self.canvas = tk.Canvas(self.container, bg=bg_page, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=COLOR_CARD)
        self._win = None
        self.canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        self.canvas.delete("bg")
        self.canvas.delete("shadow")
        # 은은한 그림자: tk는 반투명 합성을 못 하므로, 그림자 톤에서 배경색으로
        # 점점 옅어지는 둥근 사각형을 몇 겹 겹쳐 그려 부드러운 그림자를 흉내낸다.
        bg_page = self.container["bg"]
        layers = 6
        for i in range(layers, 0, -1):
            t = (i / layers) ** 1.4
            offset = 7 * (i / layers)
            col = _blend(COLOR_SHADOW, bg_page, t)
            _round_rect(self.canvas, 1, 1 + offset * 0.6, w - 1, h - 1 + offset,
                        radius=self.radius + i * 0.5, fill=col, outline="", tags="shadow")
        _round_rect(self.canvas, 1, 1, w - 1, h - 1, radius=self.radius,
                    fill=COLOR_CARD, outline=COLOR_BORDER, width=1, tags="bg")
        self.canvas.tag_lower("bg")
        self.canvas.tag_lower("shadow")
        iw, ih = max(w - 2 * self.pad, 10), max(h - 2 * self.pad, 10)
        if self._win is None:
            self._win = self.canvas.create_window(self.pad, self.pad, anchor="nw", window=self.inner)
        self.canvas.coords(self._win, self.pad, self.pad)
        self.canvas.itemconfig(self._win, width=iw, height=ih)

    def widget(self):
        return self.container


class Segmented(tk.Frame):
    """rounded pill 스타일 탭 선택기. 트랙 전체를 하나의 캔버스로 그려
    둥근 알약 모양 컨테이너 느낌을 낸다."""

    PILL_W = 106
    PILL_H = 38
    PAD = 5
    GAP = 3

    def __init__(self, master, options, command):
        super().__init__(master, bg=COLOR_BG)
        self.command = command
        self.options = options
        self.active = options[0]

        w = self.PAD * 2 + self.PILL_W * len(options)
        h = self.PAD * 2 + self.PILL_H
        self.canvas = tk.Canvas(self, width=w, height=h, bg=COLOR_BG,
                                 highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self._render()

    def _on_click(self, event):
        idx = int((event.x - self.PAD) // self.PILL_W)
        if 0 <= idx < len(self.options) and event.y >= self.PAD:
            self.select(self.options[idx])

    def select(self, key):
        if key == self.active:
            return
        self.active = key
        self._render()
        self.command(key)

    def _render(self):
        self.canvas.delete("all")
        w = self.PAD * 2 + self.PILL_W * len(self.options)
        h = self.PAD * 2 + self.PILL_H
        _round_rect(self.canvas, 0, 0, w, h, radius=h / 2, fill=COLOR_TRACK, outline="")

        for i, key in enumerate(self.options):
            x1 = self.PAD + i * self.PILL_W + self.GAP / 2
            x2 = self.PAD + (i + 1) * self.PILL_W - self.GAP / 2
            cx, cy = (x1 + x2) / 2, h / 2
            if key == self.active:
                _round_rect(self.canvas, x1, self.PAD, x2, h - self.PAD,
                            radius=(self.PILL_H) / 2, fill=COLOR_PRIMARY, outline="")
                self.canvas.create_text(cx, cy, text=TAB_LABEL[key], fill="white",
                                         font=(FONT, 11, "bold"))
            else:
                self.canvas.create_text(cx, cy, text=TAB_LABEL[key], fill=COLOR_MUTED,
                                         font=(FONT, 11))


class Spinner(tk.Canvas):
    """새로고침 중임을 보여주는 회전하는 원호. idle일 때는 숨겨둔다."""

    SIZE = 16

    def __init__(self, parent):
        super().__init__(parent, width=self.SIZE, height=self.SIZE,
                          bg=COLOR_CARD, highlightthickness=0)
        self.angle = 0
        self._running = False
        self._arc = self.create_arc(2, 2, self.SIZE - 2, self.SIZE - 2, start=0, extent=110,
                                     style="arc", outline=COLOR_PRIMARY, width=2)
        self.itemconfigure(self._arc, state="hidden")

    def start(self):
        if self._running:
            return
        self._running = True
        self.itemconfigure(self._arc, state="normal")
        self._animate()

    def stop(self):
        self._running = False
        self.itemconfigure(self._arc, state="hidden")

    def _animate(self):
        if not self._running:
            return
        self.angle = (self.angle - 12) % 360
        self.itemconfig(self._arc, start=self.angle)
        self.after(45, self._animate)


class StatCard:
    def __init__(self, parent, label, dot_color):
        self.card = Card(parent, radius=10, pad=10)
        row = tk.Frame(self.card.inner, bg=COLOR_CARD)
        row.pack(fill="both", expand=True)
        dot = tk.Canvas(row, width=8, height=8, bg=COLOR_CARD, highlightthickness=0)
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")
        dot.pack(side="left")
        tk.Label(row, text=label, bg=COLOR_CARD, fg=COLOR_MUTED,
                 font=(FONT, 10, "bold")).pack(side="left", padx=(5, 0))

        self.value_label = tk.Label(row, text="-", bg=COLOR_CARD, fg=COLOR_TEXT,
                                     font=(FONT, 15, "bold"))
        self.value_label.pack(side="right")

    def widget(self):
        return self.card.widget()

    def set_value(self, value):
        self.value_label.config(text=str(value))


class BerthMonitorApp:
    def __init__(self, root):
        self.root = root
        root.title("Real-time Berth Monitoring")
        root.geometry("1360x860")
        root.configure(bg=COLOR_BG)

        self._build_header()
        self._build_summary()
        self._build_content()
        self._build_watermark()

        # 위젯들이 자연 크기를 요구하며 geometry()로 지정한 크기를 밀어낼 수 있어
        # 레이아웃 구성이 끝난 뒤 창 크기를 한번 더 확정한다.
        root.update_idletasks()
        root.geometry("1360x860")

        # __init__ 시점엔 아직 mainloop()가 시작되기 전이라, matplotlib 캔버스가
        # 실제 위젯 크기를 반영하는 <Configure> 처리가 완전히 끝나지 않은 상태다.
        # 여기서 바로 refresh()를 부르면 첫 그래프가 작게 눌린 캔버스 크기에
        # 맞춰 그려져버리므로, mainloop 시작 후로 한 틱 미룬다.
        root.after(200, self.refresh)
        self._schedule_auto_refresh()

    # ---------- 레이아웃 ----------

    def _build_header(self):
        accent = tk.Frame(self.root, bg=COLOR_PRIMARY, height=3)
        accent.pack(fill="x")

        header = tk.Frame(self.root, bg=COLOR_CARD, height=100)
        header.pack(fill="x")
        border = tk.Frame(self.root, bg=COLOR_BORDER, height=1)
        border.pack(fill="x")

        inner = tk.Frame(header, bg=COLOR_CARD)
        inner.pack(fill="both", expand=True, padx=28, pady=18)

        self.logo_img = tk.PhotoImage(data=LOGO_PNG_B64)
        tk.Label(inner, image=self.logo_img, bg=COLOR_CARD).pack(side="left", padx=(0, 16))

        left = tk.Frame(inner, bg=COLOR_CARD)
        left.pack(side="left")
        tk.Label(left, text="Real-time Berth Monitoring", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=(FONT, 25, "bold")).pack(anchor="w")
        tk.Label(left, text="부산항 · 인천항 · 광양항 실시간 선석 배정 현황  ·  선사코드 DYS",
                 bg=COLOR_CARD, fg=COLOR_MUTED, font=(FONT, 11)).pack(anchor="w", pady=(5, 0))

        right = tk.Frame(inner, bg=COLOR_CARD)
        right.pack(side="right")
        status_row = tk.Frame(right, bg=COLOR_CARD)
        status_row.pack(side="top", anchor="e")
        self.spinner = Spinner(status_row)
        self.spinner.pack(side="left", padx=(0, 6))
        self.status_label = tk.Label(status_row, text="불러오는 중...", bg=COLOR_CARD, fg=COLOR_MUTED,
                                      font=(FONT, 10))
        self.status_label.pack(side="left")

        btn = tk.Button(right, text="새로고침", command=self.refresh, bd=0,
                         relief="flat", bg=COLOR_PRIMARY, fg="white",
                         activebackground=COLOR_PRIMARY_DARK, activeforeground="white",
                         font=(FONT, 10, "bold"), padx=20, pady=8, cursor="hand2")
        btn.pack(side="top", anchor="e", pady=(10, 0))

    def _build_summary(self):
        self.stat_cards = {}

        region_keys = TAB_KEYS[1:]
        port_specs = [(key, TAB_LABEL[key], REGION_COLOR[key]) for key in region_keys]
        country_specs = [(key, key, COUNTRY_COLOR[key]) for key in COUNTRY_KEYS]
        self._build_stat_row(port_specs + country_specs, top_pad=14)

    def _build_stat_row(self, specs, top_pad):
        bar = tk.Frame(self.root, bg=COLOR_BG, height=58)
        bar.pack(fill="x", padx=28, pady=(top_pad, 8))
        bar.grid_propagate(False)
        for col in range(len(specs)):
            bar.grid_columnconfigure(col, weight=1, uniform="stat")
        bar.grid_rowconfigure(0, weight=1)

        for i, (key, label, color) in enumerate(specs):
            sc = StatCard(bar, label, color)
            sc.widget().grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            self.stat_cards[key] = sc

    def _build_content(self):
        outer = tk.Frame(self.root, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=28, pady=(0, 24))

        tab_row = tk.Frame(outer, bg=COLOR_BG)
        tab_row.pack(fill="x", pady=(0, 14))
        Segmented(tab_row, ALL_TAB_KEYS, self._on_tab_change).pack(side="left")

        self.chart_container = tk.Frame(outer, bg=COLOR_BG)
        self.chart_container.pack(fill="both", expand=True)

        self.cards = {}
        self.figs = {}
        self.axes = {}
        self.canvases = {}
        self.hover_targets = {}   # tab_key -> [(patch, tooltip_text), ...], 매 그리기마다 새로 채움
        self.hover_annot = {}     # tab_key -> 현재 활성 annotation (ax.clear()마다 새로 만들어짐)

        for key in ALL_TAB_KEYS:
            card = Card(self.chart_container, radius=18)
            card.widget().place(x=0, y=0, relwidth=1, relheight=1)
            self.cards[key] = card

            if key == EMPTY_TAB_KEY:
                # 공컨현황은 그래프가 아니라 표로 보여주는 게 더 명확해서
                # matplotlib 캔버스 없이 Card.inner에 직접 표를 그린다.
                continue

            fig = Figure(figsize=(11, 6), dpi=100, facecolor=COLOR_CARD)
            ax = fig.add_subplot(111)
            canvas = FigureCanvasTkAgg(fig, master=card.inner)
            canvas.get_tk_widget().pack(fill="both", expand=True)

            self.figs[key] = fig
            self.axes[key] = ax
            self.canvases[key] = canvas
            self.hover_targets[key] = []
            self.hover_annot[key] = None

            canvas.mpl_connect("motion_notify_event", lambda event, k=key: self._on_hover(k, event))
            canvas.mpl_connect("figure_leave_event", lambda event, k=key: self._hide_hover(k))

        self.current_tab = ALL_TAB_KEYS[0]
        self._raise_tab(self.current_tab)

    def _raise_tab(self, key):
        # 주의: tk.Canvas는 lift()/lower()를 캔버스 아이템용(tag_raise/tag_lower)으로
        # 덮어쓰므로, 위젯 스태킹 순서를 바꿀 때는 반드시 tkraise()를 써야 한다.
        self.cards[key].widget().tkraise()

    def _on_tab_change(self, key):
        self.current_tab = key
        self._raise_tab(key)

    # ---------- 마우스 오버 툴팁 ----------
    # ax.clear()가 이전 annotation을 함께 지우므로, 매번 그릴 때마다
    # _make_hover_annot()으로 새로 만들어 self.hover_annot[key]에 채워둔다.

    def _make_hover_annot(self, tab_key):
        ax = self.axes[tab_key]
        annot = ax.annotate(
            "", xy=(0, 0), xytext=(16, 16), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.5", fc="#111827", ec="none", alpha=0.92),
            color="white", fontsize=9.5, zorder=30, linespacing=1.4,
        )
        annot.set_visible(False)
        self.hover_annot[tab_key] = annot
        self.hover_targets[tab_key] = []

    def _on_hover(self, tab_key, event):
        annot = self.hover_annot.get(tab_key)
        if annot is None or event.inaxes != self.axes[tab_key]:
            self._hide_hover(tab_key)
            return
        for patch, text in self.hover_targets[tab_key]:
            contains, _ = patch.contains(event)
            if contains:
                annot.xy = (event.xdata, event.ydata)
                annot.set_text(text)
                annot.set_visible(True)
                self.canvases[tab_key].draw_idle()
                return
        self._hide_hover(tab_key)

    def _hide_hover(self, tab_key):
        annot = self.hover_annot.get(tab_key)
        if annot is not None and annot.get_visible():
            annot.set_visible(False)
            self.canvases[tab_key].draw_idle()

    def _build_watermark(self):
        # 레이아웃 흐름에 공간을 차지하지 않도록 place()로 우하단에 겹쳐 띄운다.
        label = tk.Label(self.root, text="Powered by THJEON", bg=COLOR_BG, fg=COLOR_MUTED,
                          font=(FONT, 8))
        label.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-6)
        label.lift()

    # ---------- 데이터 갱신 ----------

    def _schedule_auto_refresh(self):
        self.root.after(REFRESH_INTERVAL_SEC * 1000, self._auto_refresh)

    def _auto_refresh(self):
        self.refresh()
        self._schedule_auto_refresh()

    def refresh(self):
        self.status_label.config(text="불러오는 중...")
        self.spinner.start()
        threading.Thread(target=self._fetch_and_draw, daemon=True).start()

    def _fetch_and_draw(self):
        try:
            data, errors = fetch_all()
        except Exception as e:
            data, errors = [], [str(e)]
        try:
            work_data = fetch_bptc_work_status()
        except Exception as e:
            work_data = []
            errors = list(errors) + [f"본선작업현황: {e}"]
        try:
            empty_data = fetch_bptc_empty_status()
        except Exception as e:
            empty_data = []
            errors = list(errors) + [f"국내공컨현황: {e}"]
        try:
            empty_data = empty_data + fetch_gwct_empty_status()
        except Exception as e:
            errors = list(errors) + [f"국내공컨현황(광양서부): {e}"]
        try:
            empty_data = empty_data + fetch_e1ct_empty_status()
        except Exception as e:
            errors = list(errors) + [f"국내공컨현황(E1CT): {e}"]
        try:
            empty_data = empty_data + fetch_hjit_empty_status()
        except Exception as e:
            errors = list(errors) + [f"국내공컨현황(HJIT): {e}"]
        self.root.after(0, lambda: self._on_data(data, errors, work_data, empty_data))

    def _on_data(self, data, errors, work_data, empty_data):
        self.spinner.stop()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"마지막 갱신 {now}"
        if errors:
            msg += "  ·  오류: " + "; ".join(errors)
        self.status_label.config(text=msg)

        # 통계 카드는 간트 차트와 같은 "지금 ~ 지금+7일" 창으로 집계해야
        # 숫자가 일치한다. 소스마다 조회 기간이 제각각이라(HJIT 14일치,
        # SNCT 2주치 등) 필터 없이 data를 그대로 세면 차트에는 안 그려지는
        # 항목까지 카드 숫자에 섞여 들어가 서로 어긋나 보인다.
        win_start = datetime.now().replace(minute=0, second=0, microsecond=0)
        win_end = win_start + timedelta(days=GANTT_WINDOW_DAYS)
        visible = [d for d in data if d.get("eta") and d.get("etd")
                   and d["etd"] >= win_start and d["eta"] <= win_end]

        for region in TAB_KEYS[1:]:
            self.stat_cards[region].set_value(sum(1 for d in visible if d.get("region") == region))

        countries = [_classify_country(d) for d in visible]
        for key in COUNTRY_KEYS:
            self.stat_cards[key].set_value(countries.count(key))

        self._draw_gantt("전체", data)
        for region in TAB_KEYS[1:]:
            self._draw_gantt(region, [d for d in data if d.get("region") == region])
        self._draw_work_status(work_data)
        self._draw_empty_status(empty_data)

    def _draw_gantt(self, tab_key, data):
        ax = self.axes[tab_key]
        fig = self.figs[tab_key]
        canvas = self.canvases[tab_key]

        ax.clear()
        ax.set_facecolor(COLOR_CARD)
        self._make_hover_annot(tab_key)

        # 표시 구간은 항상 "지금 ~ 지금+7일"로 고정한다. 소스별로 2주치까지
        # 들어있는 데이터를 그대로 그리면 한 화면에 너무 많은 기간이 눌려
        # 들어가 글자가 작아지므로, 이 구간과 겹치는 항목만 그리고 나머지는 자른다.
        win_start = datetime.now().replace(minute=0, second=0, microsecond=0)
        win_end = win_start + timedelta(days=GANTT_WINDOW_DAYS)
        win_start_num = mdates.date2num(win_start)
        win_end_num = mdates.date2num(win_end)

        usable = [d for d in data if d.get("eta") and d.get("etd")
                  and d["etd"] >= win_start and d["eta"] <= win_end]
        rows = sorted({(d["region"], d["terminal"], d["berth"]) for d in usable})
        row_index = {key: i for i, key in enumerate(rows)}

        for i in range(len(rows)):
            if i % 2 == 1:
                ax.axhspan(i - 0.5, i + 0.5, color=COLOR_ROW_ALT, zorder=0)

        span = win_end_num - win_start_num
        for d in usable:
            y = row_index[(d["region"], d["terminal"], d["berth"])]
            start = max(mdates.date2num(d["eta"]), win_start_num)
            end = min(mdates.date2num(d["etd"]), win_end_num)
            width = max(end - start, 0.01)
            color = REGION_COLOR.get(d.get("region"), COLOR_PRIMARY)
            bar = ax.barh(y, width, left=start, height=0.62, color=color,
                           edgecolor=COLOR_CARD, linewidth=1.5, zorder=2)

            eta_str = f"{d['eta'].month}/{d['eta'].day}({_WEEKDAY_KR[d['eta'].weekday()]}) {d['eta'].strftime('%H:%M')}"
            etd_str = f"{d['etd'].month}/{d['etd'].day}({_WEEKDAY_KR[d['etd'].weekday()]}) {d['etd'].strftime('%H:%M')}"
            tooltip = d["ship_name"]
            if d.get("voyage"):
                tooltip += f" ({d['voyage']})"
            tooltip += f"\n작업예정: {eta_str} ~ {etd_str}"
            self.hover_targets[tab_key].append((bar.patches[0], tooltip))

            # 막대 폭에 비례해 표기 수준을 낮춘다: 넓으면 선명/항차 두 줄,
            # 좁으면 선명만 한 줄, 아주 좁으면 글자를 넣지 않아 넘침을 막는다.
            if width > span / 22:
                label = d["ship_name"]
                if d.get("voyage"):
                    label += f"\n{d['voyage']}"
                fontsize = 10.5
            elif width > span / 55:
                label = d["ship_name"]
                fontsize = 9
            else:
                label = ""
                fontsize = 9

            if label:
                ax.text(start + width / 2, y, label, va="center", ha="center",
                        fontsize=fontsize, color="white", fontweight="bold", zorder=3,
                        linespacing=1.4, clip_on=True)

        if rows:
            ax.set_yticks(range(len(rows)))
            ax.set_yticklabels([f"{t} {b}선석" for _, t, b in rows],
                                fontsize=13, color=COLOR_TEXT, fontweight="bold")
            ax.set_ylim(len(rows) - 0.5, -0.5)

            def _date_label(x, pos=None):
                d = mdates.num2date(x)
                return f"{d.month}/{d.day}({_WEEKDAY_KR[d.weekday()]})"

            ax.xaxis.set_major_locator(mdates.DayLocator())
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(_date_label))
            ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 12, 18]))
            ax.tick_params(axis="x", which="major", labelsize=12, colors=COLOR_TEXT, length=0, pad=10)
            ax.tick_params(axis="x", which="minor", length=0)
            ax.grid(axis="x", which="major", linestyle="-", linewidth=1, color=COLOR_GRID_MAJOR, zorder=1)
            ax.grid(axis="x", which="minor", linestyle="-", linewidth=0.7, color=COLOR_GRID_MINOR, zorder=1)
        else:
            ax.text(0.5, 0.5, "표시할 데이터가 없습니다", ha="center", va="center",
                    transform=ax.transAxes, color=COLOR_MUTED, fontsize=13)

        ax.set_xlim(win_start_num, win_end_num)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(COLOR_BORDER)
        ax.tick_params(axis="y", colors=COLOR_TEXT, length=0)
        fig.tight_layout()
        canvas.draw()

    def _draw_work_status(self, data):
        ax = self.axes[WORK_TAB_KEY]
        fig = self.figs[WORK_TAB_KEY]
        canvas = self.canvases[WORK_TAB_KEY]

        ax.clear()
        ax.set_facecolor(COLOR_CARD)
        self._make_hover_annot(WORK_TAB_KEY)

        def _berth_sort_key(d):
            m = re.search(r"\d+", d.get("berth") or "")
            num = int(m.group()) if m else 999
            return (d.get("terminal") or "", num, d.get("berth") or "")

        rows = sorted(data, key=_berth_sort_key)

        if rows:
            for i in range(len(rows)):
                if i % 2 == 1:
                    ax.axhspan(i - 0.5, i + 0.5, color=COLOR_ROW_ALT, zorder=0)
                track = ax.barh(i, 100, height=0.55, color=COLOR_TRACK, edgecolor="none", zorder=1)
                d = rows[i]
                if d.get("eta") and d.get("etd"):
                    eta_str = f"{d['eta'].month}/{d['eta'].day}({_WEEKDAY_KR[d['eta'].weekday()]}) {d['eta'].strftime('%H:%M')}"
                    etd_str = f"{d['etd'].month}/{d['etd'].day}({_WEEKDAY_KR[d['etd'].weekday()]}) {d['etd'].strftime('%H:%M')}"
                    time_line = f"\n작업예정: {eta_str} ~ {etd_str}"
                else:
                    time_line = ""
                tooltip = f"{d['ship_name']}({d.get('line', '')}) · {d.get('voyage', '')}{time_line}\n진행률 {d.get('progress', 0)}%"
                self.hover_targets[WORK_TAB_KEY].append((track.patches[0], tooltip))

            yticklabels = []
            for i, d in enumerate(rows):
                pct = d.get("progress", 0)
                color = _progress_color(pct)
                ax.barh(i, pct, height=0.55, color=color, edgecolor=COLOR_CARD,
                        linewidth=1.2, zorder=2)

                inner_label = f"{d['ship_name']}({d.get('line', '')})"
                if d.get("voyage"):
                    inner_label += f"\n{d['voyage']}"
                if pct >= 18:
                    ax.text(pct / 2, i, inner_label, va="center", ha="center",
                            fontsize=9.5, color="white", fontweight="bold", zorder=3,
                            linespacing=1.3, clip_on=True)
                else:
                    ax.text(pct + 2, i, inner_label, va="center", ha="left",
                            fontsize=9.5, color=COLOR_TEXT, fontweight="bold", zorder=3,
                            linespacing=1.3)

                ax.text(103, i, f"{pct}%", va="center", ha="left",
                        fontsize=11, color=COLOR_TEXT, fontweight="bold", zorder=3)

                yticklabels.append(f"{d['terminal']} {d['berth']}선석")

            ax.set_yticks(range(len(rows)))
            ax.set_yticklabels(yticklabels, fontsize=12, color=COLOR_TEXT, fontweight="bold")
            ax.set_ylim(len(rows) - 0.5, -0.5)
            ax.set_xlim(0, 122)
            ax.xaxis.set_major_locator(mticker.MultipleLocator(25))
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: f"{int(x)}%" if x <= 100 else ""))
            ax.tick_params(axis="x", labelsize=10, colors=COLOR_MUTED, length=0, pad=8)
            ax.grid(axis="x", linestyle="-", linewidth=1, color=COLOR_GRID_MAJOR, zorder=0)
        else:
            ax.text(0.5, 0.5, "표시할 데이터가 없습니다", ha="center", va="center",
                    transform=ax.transAxes, color=COLOR_MUTED, fontsize=13)

        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(COLOR_BORDER)
        ax.tick_params(axis="y", colors=COLOR_TEXT, length=0)
        fig.tight_layout()
        canvas.draw()

    def _draw_empty_status(self, data):
        # 이 탭은 그래프가 아니라 표라서(사용자 요청) Card.inner에 매 갱신마다
        # Label 그리드를 새로 그린다. 데이터가 4~9칸으로 적어 다시 그리는
        # 비용이 무시할 만하다.
        parent = self.cards[EMPTY_TAB_KEY].inner
        for w in parent.winfo_children():
            w.destroy()

        by_key = {(d["carrier"], d["terminal"]): d for d in data}
        rows = [(carrier, terminal)
                for carrier in EMPTY_CARRIER_COLOR
                for terminal in ("신선대", "감만", "광양서부", "E1CT", "HJIT")]

        # 컬럼 배치: 구분 | DC(3칸) | div | OT(2칸) | div | FR(2칸) | div | 합계.
        # 그룹 사이에 얇은 구분선을 넣어 DC/OT/FR 경계가 한눈에 들어오게 한다.
        LABEL_COL = 0
        group_cols = []  # [(group_label, [(counts_key, sub_label, col), ...]), ...]
        c = 1
        for group_label, cols in EMPTY_TABLE_GROUPS:
            entries = [(key, sub_label, c + i) for i, (key, sub_label) in enumerate(cols)]
            group_cols.append((group_label, entries))
            c += len(cols) + 1  # 그룹 칸 + 뒤따르는 구분선 칸
        divider_cols = [entries[-1][2] + 1 for _, entries in group_cols]
        total_col = c
        ncols = c + 1

        parent.grid_columnconfigure(LABEL_COL, weight=3, uniform="empty_col")
        for _, entries in group_cols:
            for _key, _label, col in entries:
                parent.grid_columnconfigure(col, weight=2, uniform="empty_col")
        for dc in divider_cols:
            parent.grid_columnconfigure(dc, weight=0, minsize=16)
        parent.grid_columnconfigure(total_col, weight=2, uniform="empty_col")

        def _divider(row, col, bg, rowspan=1):
            cell = tk.Frame(parent, bg=bg)
            cell.grid(row=row, column=col, rowspan=rowspan, sticky="nsew")
            tk.Frame(cell, bg=COLOR_BORDER, width=1).place(relx=0.5, rely=0, relheight=1, anchor="n")

        # 1행: 구분/합계는 두 헤더 행을 세로로 합쳐서(rowspan) 쓰고,
        # DC/OT/FR은 하위 사이즈 칸 개수만큼 가로로 합쳐(columnspan) 그린다.
        tk.Label(parent, text="구분", bg=COLOR_CARD, fg=COLOR_MUTED,
                 font=(FONT, 11, "bold")).grid(row=0, column=LABEL_COL, rowspan=2, sticky="nsew",
                                                padx=(16, 0), pady=(6, 12))
        tk.Label(parent, text="합계", bg=COLOR_PRIMARY_SOFT, fg=COLOR_PRIMARY_DARK,
                 font=(FONT, 11, "bold")).grid(row=0, column=total_col, rowspan=2, sticky="nsew",
                                                padx=(0, 16), pady=(6, 12))

        for group_label, entries in group_cols:
            tk.Label(parent, text=group_label, bg=COLOR_CARD, fg=COLOR_TEXT,
                     font=(FONT, 12, "bold")).grid(row=0, column=entries[0][2], columnspan=len(entries),
                                                    sticky="nsew", pady=(6, 2))
            for _key, sub_label, col in entries:
                tk.Label(parent, text=sub_label, bg=COLOR_CARD, fg=COLOR_MUTED,
                         font=(FONT, 10)).grid(row=1, column=col, sticky="nsew", pady=(0, 12))

        for dc in divider_cols:
            _divider(0, dc, COLOR_CARD, rowspan=2)

        tk.Frame(parent, bg=COLOR_PRIMARY, height=2).grid(
            row=2, column=0, columnspan=ncols, sticky="ew")

        for r, (carrier, terminal) in enumerate(rows):
            d = by_key.get((carrier, terminal))
            counts = d["counts"] if d else {}
            total = d["total"] if d else 0
            bg = COLOR_CARD if r % 2 == 0 else COLOR_ROW_ALT
            grid_row = r + 3

            label_cell = tk.Frame(parent, bg=bg)
            label_cell.grid(row=grid_row, column=LABEL_COL, sticky="nsew")
            dot = tk.Canvas(label_cell, width=8, height=8, bg=bg, highlightthickness=0)
            dot.create_oval(1, 1, 7, 7, fill=EMPTY_CARRIER_COLOR[carrier], outline="")
            dot.pack(side="left", padx=(16, 6), pady=15)
            tk.Label(label_cell, text=f"{carrier} · {terminal}", bg=bg, fg=COLOR_TEXT,
                     font=(FONT, 11, "bold")).pack(side="left")

            for _group_label, entries in group_cols:
                for key, _sub_label, col in entries:
                    val = counts.get(key, 0)
                    tk.Label(parent, text=str(val) if val else "–", bg=bg,
                             fg=COLOR_TEXT if val else COLOR_MUTED, font=(FONT, 11),
                             anchor="e", padx=10).grid(
                        row=grid_row, column=col, sticky="nsew", pady=15)

            for dc in divider_cols:
                _divider(grid_row, dc, bg)

            tk.Label(parent, text=str(total), bg=COLOR_PRIMARY_SOFT, fg=COLOR_PRIMARY_DARK,
                     font=(FONT, 13, "bold"), anchor="e", justify="right").grid(
                row=grid_row, column=total_col, sticky="nsew", padx=(0, 16), pady=15)


def main():
    root = tk.Tk()
    BerthMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
