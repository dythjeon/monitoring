import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.ticker as mticker
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.transforms import offset_copy
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from scraper import (fetch_all, fetch_bptc_work_status, fetch_bptc_empty_status,
                     fetch_gwct_empty_status, fetch_e1ct_empty_status, fetch_hjit_empty_status)
from assets import LOGO_PNG_B64


def _pick_font():
    """설치된 것 중 가장 앞선 후보를 쓴다. Pretendard가 기본이지만 이 exe가
    다른 PC에서 실행될 수도 있어 맑은 고딕까지 폴백을 둔다."""
    available = {f.name for f in fm.fontManager.ttflist}
    for candidate in ("Pretendard", "Pretendard Variable", "Noto Sans KR", "Malgun Gothic"):
        if candidate in available:
            return candidate
    return "Malgun Gothic"


FONT = _pick_font()
matplotlib.rcParams["font.family"] = FONT
matplotlib.rcParams["axes.unicode_minus"] = False

REFRESH_INTERVAL_SEC = 1800  # 30분마다 자동 갱신
GANTT_WINDOW_DAYS = 7        # 간트 차트에 표시할 최대 기간(고정값)
MIN_GANTT_ROWS = 5           # 행이 이보다 적어도 막대 두께는 이 기준으로 유지

# ---- surff.kr 느낌의 라이트/미니멀 팔레트 ----
COLOR_BG = "#f4f5f8"          # 페이지 배경 (아주 옅은 회색)
COLOR_CARD = "#ffffff"        # 카드 배경
COLOR_BORDER = "#edeef2"      # 카드 테두리 (그림자와 함께 아주 옅게)
COLOR_TEXT = "#0b0f19"        # 본문/제목 텍스트
COLOR_MUTED = "#8a8f98"       # 보조 텍스트
COLOR_PRIMARY = "#2f6fed"     # 포인트 블루
COLOR_PRIMARY_DARK = "#1d4fc4"  # 포인트 블루(진한 톤, 그라디언트/그림자용)
COLOR_PRIMARY_SOFT = "#eaf1fe"
COLOR_TRACK = "#e4e8f0"       # 진행률 트랙 배경 (줄무늬 행 위에서도 보이도록)
COLOR_ROW_ALT = "#eff1f6"     # 간트 행 줄무늬(짝수 행)
COLOR_GRID_MAJOR = "#ccd2dc"  # 날짜 경계 세로선 (보조선보다 확실히 진하게)
COLOR_GRID_MINOR = "#edf0f5"  # 6시간 보조 세로선
COLOR_SHADOW = "#c3c8d4"      # 카드 아래 그림자 베이스 톤
COLOR_NOW = "#ef4444"         # 간트 차트의 현재 시각 표시선
COLOR_SAT = "#2f6fed"         # 토요일 날짜 라벨
COLOR_SUN = "#e8474c"         # 일요일 날짜 라벨
COLOR_HOVER_ROW = "#eef4ff"   # 표 행 마우스오버 하이라이트


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
TOTAL_KEY = "__total__"   # 통계 카드 전용 키 (탭이 아니라 합계 카드)
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
# 항구 색(REGION_COLOR)과 겹치지 않아야 한다. 통계 카드의 점 색이 곧 차트
# 안의 색이라, 같은 색이 두 가지를 뜻하면 범례가 깨진다. 예전엔 동남아가
# 광양과 같은 #12b886이었다.
COUNTRY_COLOR = {"일본": "#e64980", "중국": "#fa5252", "동남아": "#00b8d4", "러시아": "#7048e8"}


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


def _gantt_window():
    """간트 차트와 통계 카드가 함께 쓰는 표시 구간 (오늘 00:00 ~ +7일).

    기준을 "지금"이 아니라 "오늘 자정"으로 잡아야 현재 시각 표시선이 차트
    왼쪽 경계에 붙어버리지 않고 하루 중 어디쯤인지 보인다. 오늘 이미 출항한
    선박도 맥락으로 함께 보인다.
    """
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=GANTT_WINDOW_DAYS)


def _in_window(d, win_start, win_end):
    return (d.get("eta") and d.get("etd")
            and d["etd"] >= win_start and d["eta"] <= win_end)


def _fmt_dt(dt):
    return f"{dt.month}/{dt.day}({_WEEKDAY_KR[dt.weekday()]}) {dt.strftime('%H:%M')}"


def _weekday_color(dt):
    return {5: COLOR_SAT, 6: COLOR_SUN}.get(dt.weekday(), COLOR_TEXT)


BAR_RADIUS_PX = 5    # 간트/진행률 막대 모서리 반경(픽셀)
DOT_INSET_PT = 10    # 막대 왼쪽 안쪽에 찍는 행선지 점의 들여쓰기(포인트)
DOT_MIN_BAR_PX = 120  # 이보다 좁은 막대에는 점을 찍지 않는다(선명을 밀어내 버린다)


def _add_bar(ax, y, x0, x1, height, facecolor, edgecolor="none", linewidth=0, zorder=2):
    """둥근 모서리 가로 막대. 반경은 그린 뒤 _round_bars()가 픽셀 기준으로 맞춘다."""
    patch = FancyBboxPatch((x0, y - height / 2), max(x1 - x0, 1e-9), height,
                            boxstyle="round,pad=0,rounding_size=0",
                            facecolor=facecolor, edgecolor=edgecolor,
                            linewidth=linewidth, zorder=zorder, mutation_aspect=1)
    ax.add_patch(patch)
    return patch


def _round_bars(ax, patches, radius_px=BAR_RADIUS_PX):
    """막대 모서리 반경을 픽셀 기준으로 맞춘다.

    boxstyle의 rounding_size는 데이터 좌표라, x가 '일'이고 y가 '행'인 이 차트에선
    그대로 두면 모서리가 납작한 타원으로 찌그러진다. mutation_aspect로 y 축척을
    x에 맞춘 뒤 반경을 픽셀→데이터 단위로 환산한다. 축 변환이 확정된 뒤
    (draw 이후) 호출해야 한다.
    """
    origin = ax.transData.transform((0, 0))
    kx = abs(ax.transData.transform((1, 0))[0] - origin[0])   # x 1단위당 픽셀
    ky = abs(ax.transData.transform((0, 1))[1] - origin[1])   # y 1단위당 픽셀
    if kx <= 0 or ky <= 0:
        return
    for patch in patches:
        # 짧은 막대에서 반경이 변의 절반을 넘으면 모양이 깨진다.
        limit_px = min(patch.get_width() * kx, patch.get_height() * ky) / 2
        patch.set_mutation_aspect(kx / ky)
        patch.set_boxstyle("round", pad=0, rounding_size=min(radius_px, limit_px) / kx)


def _shorten(line, ratio):
    core = line[:-1] if line.endswith("…") else line
    keep = int(len(core) * ratio) - 1
    if keep < 2:
        return ""
    return core[:keep] + "…"


def _fit_bar_labels(name_txt, code_txt, max_px, renderer):
    """막대 밖으로 삐져나오는 라벨을 실제 렌더 폭 기준으로 줄인다.

    폰트 폭을 글자 수로 추정하면 한글/영문·굵기에 따라 빗나가서 글자가 잘린
    채로 남는다. 그려진 뒤 renderer로 실제 폭을 재고, 넘치는 비율만큼 잘라
    다시 재는 식으로 수렴시킨다.

    좁을 때 우선순위는 선명 > 항차다. 둘 다 말줄임해서 "HE…/JH…"처럼 양쪽
    다 못 읽게 만드느니, 항차 줄을 버리고 선명에 폭을 몰아준다. 항차가
    빠지면 선명을 막대 한가운데로 다시 내린다.
    """
    def fits(artist):
        return artist.get_window_extent(renderer).width <= max_px

    if code_txt is not None and (max_px <= 0 or not fits(code_txt) or not fits(name_txt)):
        code_txt.set_visible(False)
        name_txt.set_verticalalignment("center")
        name_txt.xyann = (name_txt.xyann[0], 0)

    if max_px <= 0:
        name_txt.set_text("")
        return

    for _ in range(6):
        if fits(name_txt):
            return
        shorter = _shorten(name_txt.get_text(),
                           max_px / name_txt.get_window_extent(renderer).width)
        if not shorter:
            break
        name_txt.set_text(shorter)
    if not fits(name_txt):
        name_txt.set_text("")


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

    def __init__(self, parent, bg_page=COLOR_BG, radius=16, pad=16, bg_card=COLOR_CARD):
        self.radius = radius
        # pad는 스칼라 또는 (좌우, 상하) 튜플
        self.padx, self.pady = pad if isinstance(pad, tuple) else (pad, pad)
        self.bg_card = bg_card
        # tk.Canvas의 lift()/lower()/tkraise()는 캔버스 아이템용으로 덮어써져 있어
        # 위젯 스태킹(탭 전환)에 쓸 수 없다. 그래서 스태킹 가능한 일반 Frame으로
        # 감싸고, 그 Frame을 widget()으로 노출한다.
        self.container = tk.Frame(parent, bg=bg_page)
        self.canvas = tk.Canvas(self.container, bg=bg_page, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg_card)
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
                    fill=self.bg_card, outline=COLOR_BORDER, width=1, tags="bg")
        self.canvas.tag_lower("bg")
        self.canvas.tag_lower("shadow")
        iw, ih = max(w - 2 * self.padx, 10), max(h - 2 * self.pady, 10)
        if self._win is None:
            self._win = self.canvas.create_window(self.padx, self.pady, anchor="nw", window=self.inner)
        self.canvas.coords(self._win, self.padx, self.pady)
        self.canvas.itemconfig(self._win, width=iw, height=ih)

    def widget(self):
        return self.container


class _Tooltip:
    """마우스오버시 뜨는 작은 팝업 툴팁 (overrideredirect Toplevel)."""

    def __init__(self, master):
        self.master = master
        self.tip = None
        self.label = None

    def show(self, text, x, y):
        if self.tip is None:
            self.tip = tk.Toplevel(self.master)
            self.tip.overrideredirect(True)
            self.tip.attributes("-topmost", True)
            self.label = tk.Label(self.tip, text=text, bg="#111827", fg="white",
                                   font=(FONT, 9, "bold"), padx=8, pady=4, justify="left")
            self.label.pack()
        else:
            self.label.config(text=text)
            self.tip.deiconify()
        self.tip.geometry(f"+{x}+{y}")

    def hide(self):
        if self.tip is not None:
            self.tip.withdraw()


class Segmented(tk.Frame):
    """언더라인 방식 탭. 전체 폭에 얇은 베이스라인을 깔고, 선택된 탭 아래만
    굵은 강조선을 얹는다. 예전 pill 스타일은 색 면적이 커서 그 아래 카드보다
    시선을 먼저 가져가버렸다.

    "전체"처럼 짧은 라벨과 "국내공컨현황"처럼 긴 라벨이 섞여 있어 탭 폭은
    고정하지 않고 텍스트 실측 너비로 잡는다.
    """

    TAB_H = 34          # 글자 영역 높이
    UNDERLINE_H = 3     # 선택 탭 강조선 두께
    TEXT_PAD_X = 16     # 글자 좌우 여백
    GAP = 6             # 탭 사이 간격

    def __init__(self, master, options, command, tooltips=None):
        super().__init__(master, bg=COLOR_BG)
        self.command = command
        self.options = options
        self.active = options[0]
        self.tooltips = tooltips or {}
        self.hover = None
        self._font_active = tkfont.Font(family=FONT, size=11, weight="bold")
        self._font_idle = tkfont.Font(family=FONT, size=11)
        self._tab_bounds = []  # [(x1, x2), ...], _render()가 채운다
        self._tooltip = _Tooltip(self)

        self.canvas = tk.Canvas(self, height=self.TAB_H + self.UNDERLINE_H, bg=COLOR_BG,
                                 highlightthickness=0, bd=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        # 베이스라인이 콘텐츠 폭 전체를 가로지르려면 폭이 바뀔 때마다 다시 그려야 한다.
        self.canvas.bind("<Configure>", lambda e: self._render())

    def _key_at(self, event):
        for key, (x1, x2) in zip(self.options, self._tab_bounds):
            if x1 <= event.x <= x2 and event.y <= self.TAB_H:
                return key
        return None

    def _on_motion(self, event):
        key = self._key_at(event)
        if key != self.hover:
            self.hover = key
            self.canvas.config(cursor="hand2" if key else "")
            self._render()
        text = self.tooltips.get(key) if key else None
        if text:
            self._tooltip.show(text,
                               self.canvas.winfo_rootx() + event.x + 14,
                               self.canvas.winfo_rooty() + event.y + 22)
        else:
            self._tooltip.hide()

    def _on_leave(self, _event):
        self._tooltip.hide()
        if self.hover is not None:
            self.hover = None
            self._render()

    def _tab_width(self, key):
        text_w = max(self._font_active.measure(TAB_LABEL[key]),
                     self._font_idle.measure(TAB_LABEL[key]))
        return text_w + self.TEXT_PAD_X * 2

    def _on_click(self, event):
        key = self._key_at(event)
        if key is not None:
            self.select(key)

    def select(self, key):
        if key == self.active:
            return
        self.active = key
        self._render()
        self.command(key)

    def _render(self):
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        base_y = self.TAB_H

        self.canvas.create_line(0, base_y + 1, max(w, 1), base_y + 1,
                                fill=COLOR_BORDER, width=1)

        self._tab_bounds = []
        x = 0
        for key in self.options:
            tab_w = self._tab_width(key)
            x1, x2 = x, x + tab_w
            if key == self.active:
                color, font = COLOR_PRIMARY, self._font_active
            elif key == self.hover:
                color, font = COLOR_TEXT, self._font_idle
            else:
                color, font = COLOR_MUTED, self._font_idle
            self.canvas.create_text((x1 + x2) / 2, self.TAB_H / 2,
                                     text=TAB_LABEL[key], fill=color, font=font)
            if key == self.active:
                self.canvas.create_rectangle(x1 + self.TEXT_PAD_X / 2, base_y,
                                              x2 - self.TEXT_PAD_X / 2,
                                              base_y + self.UNDERLINE_H,
                                              fill=COLOR_PRIMARY, outline="")
            self._tab_bounds.append((x1, x2))
            x = x2 + self.GAP


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
    """라벨(작고 연하게) 위 / 숫자(크고 굵게) 아래의 2단 구성.

    카드 배경은 그 카드가 대표하는 색을 아주 옅게(8%) 깐다. 점 하나로만
    구분하면 7장이 늘어섰을 때 어느 카드가 무슨 색인지 눈에 안 들어온다.
    """

    TINT = 0.08

    def __init__(self, parent, label, dot_color, emphasis=False):
        self.bg = _blend(COLOR_CARD, dot_color, self.TINT)
        self.card = Card(parent, radius=10, pad=(12, 8), bg_card=self.bg)
        box = tk.Frame(self.card.inner, bg=self.bg)
        box.pack(fill="both", expand=True)

        top = tk.Frame(box, bg=self.bg)
        top.pack(fill="x", anchor="w")
        dot = tk.Canvas(top, width=8, height=8, bg=self.bg, highlightthickness=0)
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")
        dot.pack(side="left", pady=(2, 0))
        tk.Label(top, text=label, bg=self.bg, fg=COLOR_MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(5, 0))

        bottom = tk.Frame(box, bg=self.bg)
        bottom.pack(fill="x", anchor="w", pady=(1, 0))
        self.value_label = tk.Label(bottom, text="-", bg=self.bg,
                                     fg=COLOR_PRIMARY_DARK if emphasis else COLOR_TEXT,
                                     font=(FONT, 21, "bold"))
        self.value_label.pack(side="left")
        # 단위("척")는 값보다 작고 연하게 붙여 숫자가 도드라지게 한다.
        tk.Label(bottom, text="척", bg=self.bg, fg=COLOR_MUTED,
                 font=(FONT, 9)).pack(side="left", padx=(3, 0), pady=(0, 3), anchor="s")

    def widget(self):
        return self.card.widget()

    def set_value(self, value):
        self.value_label.config(text=str(value))


class BerthMonitorApp:
    def __init__(self, root):
        self.root = root
        root.title("Real-time Berth/Empty stock Monitoring")
        root.geometry("1360x860")
        # 이보다 좁아지면 통계 카드 7장과 탭이 서로 밀려 레이아웃이 무너진다.
        root.minsize(1180, 720)
        root.configure(bg=COLOR_BG)
        root.iconphoto(True, tk.PhotoImage(data=LOGO_PNG_B64))

        self._last_draw = None
        self._resize_job = None

        self._build_header()
        self._build_summary()
        self._build_content()
        self._build_watermark()
        self.chart_container.bind("<Configure>", self._on_resize)

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
        tk.Label(left, text="Real-time Berth/Empty stock Monitoring", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=(FONT, 23, "bold")).pack(anchor="w")
        subtitle = (f"부산항 · 인천항 · 광양항 실시간 선석 배정 현황  ·  선사코드 DYS"
                    f"  ·  {GANTT_WINDOW_DAYS}일 구간  ·  {REFRESH_INTERVAL_SEC // 60}분마다 자동 갱신")
        tk.Label(left, text=subtitle,
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

        # 헤더에는 "오류 N건"만 띄우고, 어떤 소스가 실패했는지는 여기에 올려서 본다.
        self._status_detail = ""
        self._status_tip = _Tooltip(self.status_label)
        self.status_label.bind("<Enter>", self._show_status_detail)
        self.status_label.bind("<Leave>", lambda e: self._status_tip.hide())

        btn = tk.Button(right, text="새로고침", command=self.refresh, bd=0,
                         relief="flat", bg=COLOR_PRIMARY, fg="white",
                         activebackground=COLOR_PRIMARY_DARK, activeforeground="white",
                         font=(FONT, 10, "bold"), padx=20, pady=8, cursor="hand2",
                         highlightthickness=0)
        btn.pack(side="top", anchor="e", pady=(10, 0))
        # 눌리기 전에도 반응이 보이도록 호버 시 색을 한 단계 진하게.
        btn.bind("<Enter>", lambda e: btn.config(bg=COLOR_PRIMARY_DARK))
        btn.bind("<Leave>", lambda e: btn.config(bg=COLOR_PRIMARY))

    def _show_status_detail(self, event):
        if not self._status_detail:
            return
        self._status_tip.show(self._status_detail,
                              self.status_label.winfo_rootx(),
                              self.status_label.winfo_rooty() + 24)

    def _build_summary(self):
        self.stat_cards = {}

        # 합계 | 항구별 | 행선지별 — 성격이 다른 세 묶음이라 사이에 구분선을 둔다.
        # 예전엔 7개가 균등하게 한 줄로 붙어 있어 어디까지가 항구고 어디부터
        # 행선지인지 읽히지 않았다.
        groups = [
            [(TOTAL_KEY, "전체", COLOR_PRIMARY)],
            [(key, TAB_LABEL[key], REGION_COLOR[key]) for key in TAB_KEYS[1:]],
            [(key, key, COUNTRY_COLOR[key]) for key in COUNTRY_KEYS],
        ]
        # 헤더와 카드 사이를 넉넉히 띄워 상단 정보 블록이 서로 붙어 보이지 않게 한다.
        self._build_stat_row(groups, top_pad=26)

    def _build_stat_row(self, groups, top_pad):
        # 높이는 내용(라벨+숫자 약 61px)에 여유를 더해 잡는다. 딱 맞추면 DPI가
        # 다른 PC에서 숫자 아랫부분이 잘린다.
        bar = tk.Frame(self.root, bg=COLOR_BG, height=88)
        bar.pack(fill="x", padx=28, pady=(top_pad, 18))
        bar.grid_propagate(False)
        bar.grid_rowconfigure(0, weight=1)

        col = 0
        for gi, specs in enumerate(groups):
            if gi:
                sep = tk.Frame(bar, bg=COLOR_BG, width=25)
                sep.grid(row=0, column=col, sticky="ns")
                bar.grid_columnconfigure(col, weight=0, minsize=25)
                tk.Frame(sep, bg=COLOR_BORDER, width=1).place(
                    relx=0.5, rely=0.5, relheight=0.6, anchor="center")
                col += 1

            for i, (key, label, color) in enumerate(specs):
                bar.grid_columnconfigure(col, weight=1, uniform="stat")
                sc = StatCard(bar, label, color, emphasis=(key == TOTAL_KEY))
                sc.widget().grid(row=0, column=col, sticky="nsew",
                                 padx=(0 if i == 0 else 10, 0))
                self.stat_cards[key] = sc
                col += 1

    def _build_content(self):
        outer = tk.Frame(self.root, bg=COLOR_BG)
        outer.pack(fill="both", expand=True, padx=28, pady=(0, 24))

        # 언더라인 탭은 베이스라인이 콘텐츠 폭 전체를 가로질러야 해서 fill="x"로 깐다.
        Segmented(outer, ALL_TAB_KEYS, self._on_tab_change,
                  tooltips={WORK_TAB_KEY: "BPT Only"}).pack(fill="x", pady=(0, 16))

        self.chart_container = tk.Frame(outer, bg=COLOR_BG)
        self.chart_container.pack(fill="both", expand=True)

        self.cards = {}
        self.figs = {}
        self.axes = {}
        self.canvases = {}
        self.hover_targets = {}   # tab_key -> [(patch, tooltip_text), ...], 매 그리기마다 새로 채움
        self.hover_annot = {}     # tab_key -> 현재 활성 annotation (ax.clear()마다 새로 만들어짐)
        self.row_highlight = {}   # tab_key -> 마우스 올린 행을 덮는 사각형
        self.row_counts = {}      # tab_key -> 현재 그려진 행 수

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
            self.row_highlight[key] = None
            self.row_counts[key] = 0

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

        # 마우스 올린 행을 통째로 밝히는 띠. x는 축 비율(0~1), y는 데이터 좌표를
        # 쓰는 혼합 변환이라 폭 걱정 없이 행 높이만 맞추면 된다.
        highlight = Rectangle((0, -9), 1, 1, transform=ax.get_yaxis_transform(),
                              facecolor=COLOR_HOVER_ROW, edgecolor="none",
                              zorder=0.5, visible=False)
        ax.add_patch(highlight)
        self.row_highlight[tab_key] = highlight

    def _on_hover(self, tab_key, event):
        annot = self.hover_annot.get(tab_key)
        if annot is None or event.inaxes != self.axes[tab_key]:
            self._hide_hover(tab_key)
            return

        # 행 강조는 막대 위가 아니라 그 행 어디에 있어도 걸리게 한다.
        changed = self._update_row_highlight(tab_key, event)

        for patch, text in self.hover_targets[tab_key]:
            contains, _ = patch.contains(event)
            if contains:
                annot.xy = (event.xdata, event.ydata)
                annot.set_text(text)
                annot.set_visible(True)
                self.canvases[tab_key].draw_idle()
                return
        if annot.get_visible():
            annot.set_visible(False)
            changed = True
        if changed:
            self.canvases[tab_key].draw_idle()

    def _update_row_highlight(self, tab_key, event):
        """마우스가 있는 행으로 강조 띠를 옮긴다. 실제로 바뀌었으면 True."""
        highlight = self.row_highlight.get(tab_key)
        if highlight is None:
            return False
        row = round(event.ydata) if event.ydata is not None else -1
        inside = 0 <= row < self.row_counts.get(tab_key, 0)
        if not inside:
            if highlight.get_visible():
                highlight.set_visible(False)
                return True
            return False
        if highlight.get_visible() and highlight.get_y() == row - 0.5:
            return False
        highlight.set_y(row - 0.5)
        highlight.set_visible(True)
        return True

    def _hide_hover(self, tab_key):
        changed = False
        annot = self.hover_annot.get(tab_key)
        if annot is not None and annot.get_visible():
            annot.set_visible(False)
            changed = True
        highlight = self.row_highlight.get(tab_key)
        if highlight is not None and highlight.get_visible():
            highlight.set_visible(False)
            changed = True
        if changed:
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
            # 오류 문구를 그대로 붙이면 한 줄이 창 밖까지 늘어나 헤더 레이아웃을
            # 밀어버린다. 건수만 알리고 상세는 툴팁으로 넘긴다.
            msg += f"  ·  오류 {len(errors)}건"
        self.status_label.config(text=msg, fg=COLOR_NOW if errors else COLOR_MUTED)
        self._status_detail = "\n".join(errors) if errors else ""

        # 통계 카드는 간트 차트와 같은 "지금 ~ 지금+7일" 창으로 집계해야
        # 숫자가 일치한다. 소스마다 조회 기간이 제각각이라(HJIT 14일치,
        # SNCT 2주치 등) 필터 없이 data를 그대로 세면 차트에는 안 그려지는
        # 항목까지 카드 숫자에 섞여 들어가 서로 어긋나 보인다.
        win_start, win_end = _gantt_window()
        visible = [d for d in data if _in_window(d, win_start, win_end)]

        self.stat_cards[TOTAL_KEY].set_value(len(visible))
        for region in TAB_KEYS[1:]:
            self.stat_cards[region].set_value(sum(1 for d in visible if d.get("region") == region))

        countries = [_classify_country(d) for d in visible]
        for key in COUNTRY_KEYS:
            self.stat_cards[key].set_value(countries.count(key))

        # 창 크기가 바뀌면 막대 모서리 반경과 라벨 말줄임을 다시 계산해야 해서
        # (둘 다 픽셀 기준) 마지막 데이터를 들고 있다가 그대로 다시 그린다.
        self._last_draw = (data, work_data, empty_data)
        self._render_all()

    def _render_all(self):
        data, work_data, empty_data = self._last_draw
        self._draw_gantt("전체", data)
        for region in TAB_KEYS[1:]:
            self._draw_gantt(region, [d for d in data if d.get("region") == region])
        self._draw_work_status(work_data)
        self._draw_empty_status(empty_data)

    def _on_resize(self, _event=None):
        if self._last_draw is None:
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        # 드래그하는 동안 매 픽셀마다 다시 그리면 버벅이므로 멈춘 뒤에 한 번만.
        self._resize_job = self.root.after(250, self._redraw_after_resize)

    def _redraw_after_resize(self):
        self._resize_job = None
        self._render_all()

    def _draw_gantt(self, tab_key, data):
        ax = self.axes[tab_key]
        fig = self.figs[tab_key]
        canvas = self.canvases[tab_key]

        ax.clear()
        ax.set_facecolor(COLOR_CARD)
        self._make_hover_annot(tab_key)

        # 표시 구간은 항상 "오늘 00:00 ~ +7일"로 고정한다. 소스별로 2주치까지
        # 들어있는 데이터를 그대로 그리면 한 화면에 너무 많은 기간이 눌려
        # 들어가 글자가 작아지므로, 이 구간과 겹치는 항목만 그리고 나머지는 자른다.
        now = datetime.now()
        win_start, win_end = _gantt_window()
        win_start_num = mdates.date2num(win_start)
        win_end_num = mdates.date2num(win_end)

        usable = [d for d in data if _in_window(d, win_start, win_end)]
        rows = sorted({(d["region"], d["terminal"], d["berth"]) for d in usable})
        row_index = {key: i for i, key in enumerate(rows)}
        self.row_counts[tab_key] = len(rows)

        for i in range(len(rows)):
            if i % 2 == 1:
                ax.axhspan(i - 0.5, i + 0.5, color=COLOR_ROW_ALT, zorder=0)

        span = win_end_num - win_start_num
        # 라벨 말줄임과 모서리 반경은 그린 뒤에 실제 픽셀을 재서 처리한다(아래 참고).
        pending = []   # [(name 아티스트, code 아티스트, 점 아티스트, 좌, 우), ...]
        bars = []
        # 행선지 점은 막대 왼쪽 끝에서 일정 '포인트'만큼 안쪽에 찍는다. 데이터
        # 좌표로 잡으면 창 크기에 따라 위치가 흔들려서 offset 변환을 쓴다.
        dot_trans = offset_copy(ax.transData, fig=fig, x=DOT_INSET_PT, y=0, units="points")

        for d in usable:
            y = row_index[(d["region"], d["terminal"], d["berth"])]
            start = max(mdates.date2num(d["eta"]), win_start_num)
            end = min(mdates.date2num(d["etd"]), win_end_num)
            width = max(end - start, 0.01)
            color = REGION_COLOR.get(d.get("region"), COLOR_PRIMARY)
            # 지금 접안 중인 선박은 테두리를 진하게 줘서 한눈에 띄게 한다.
            in_progress = d["eta"] <= now <= d["etd"]
            bar = _add_bar(ax, y, start, start + width, 0.62, color,
                           edgecolor=_blend(color, "#000000", 0.3) if in_progress else COLOR_CARD,
                           linewidth=2 if in_progress else 1.2)
            bars.append(bar)

            tooltip = d["ship_name"]
            if d.get("voyage"):
                tooltip += f" ({d['voyage']})"
            tooltip += f"\n작업예정: {_fmt_dt(d['eta'])} ~ {_fmt_dt(d['etd'])}"
            country = _classify_country(d)
            if country:
                tooltip += f"\n행선지: {country}"
            self.hover_targets[tab_key].append((bar, tooltip))

            # 행선지 색 점 — 통계 카드의 점과 같은 색/모양이라 그대로 범례가 된다.
            dot = None
            if country:
                dot = ax.plot([start], [y], marker="o", linestyle="none", markersize=5,
                              markerfacecolor=COUNTRY_COLOR[country], markeredgecolor="white",
                              markeredgewidth=0.8, transform=dot_trans, zorder=4)[0]

            # 막대 폭에 따라 표기 수준을 낮춘다: 넓으면 선명+항차, 좁으면 선명만,
            # 아주 좁으면 글자를 넣지 않는다. 실제 넘침 여부는 그린 뒤 다시 본다.
            if width <= span / 55:
                if dot is not None:
                    pending.append((None, None, dot, start, start + width))
                continue

            two_line = width > span / 22 and d.get("voyage")
            # 선명은 굵고 크게, 항차는 작고 연하게 — 한 막대 안에서도 위계를 준다.
            name_txt = ax.annotate(d["ship_name"], xy=(start + width / 2, y),
                                   xytext=(0, 1.5 if two_line else 0), textcoords="offset points",
                                   ha="center", va="bottom" if two_line else "center",
                                   fontsize=10.5 if two_line else 9.5, fontweight="bold",
                                   color="white", zorder=3, clip_on=True)
            code_txt = None
            if two_line:
                code_txt = ax.annotate(d["voyage"], xy=(start + width / 2, y),
                                       xytext=(0, -2), textcoords="offset points",
                                       ha="center", va="top", fontsize=8.5, color="white",
                                       alpha=0.75, zorder=3, clip_on=True)
            pending.append((name_txt, code_txt, dot, start, start + width))

        if rows:
            ax.set_yticks(range(len(rows)))
            ax.set_yticklabels([f"{t} {b}선석" for _, t, b in rows],
                                fontsize=12, fontweight="bold")
            # 선석 라벨을 항구 색으로 칠해 "전체" 탭에서도 어느 항인지 바로 보이게 한다.
            for label, (region, _t, _b) in zip(ax.get_yticklabels(), rows):
                label.set_color(REGION_COLOR.get(region, COLOR_TEXT))
            # 선석이 한두 개뿐인 탭(광양 등)에서 막대가 화면 높이만큼 두꺼워지지
            # 않도록, 최소 행 수 기준으로 남는 만큼을 위아래에 나눠 비워둔다.
            slack = max(MIN_GANTT_ROWS - len(rows), 0) / 2
            ax.set_ylim(len(rows) - 0.5 + slack, -0.5 - slack)

            # 날짜 라벨은 날짜 경계선이 아니라 그 날 칸의 한가운데(정오)에 둔다.
            # 경계선에 붙으면 어느 쪽 날을 가리키는지 헷갈린다.
            ax.xaxis.set_major_locator(mdates.DayLocator())
            ax.xaxis.set_major_formatter(mticker.NullFormatter())
            ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=[12]))
            ax.xaxis.set_minor_formatter(mticker.FuncFormatter(
                lambda x, pos=None: (lambda dt: f"{dt.month}/{dt.day}({_WEEKDAY_KR[dt.weekday()]})")(mdates.num2date(x))))
            ax.tick_params(axis="x", which="minor", labelsize=11.5, length=0, pad=10)
            ax.tick_params(axis="x", which="major", length=0)
            ax.grid(axis="x", which="major", linestyle="-", linewidth=1,
                    color=COLOR_GRID_MAJOR, zorder=1)

            # 6/12/18시 보조선은 minor tick을 라벨용으로 써버렸으므로 직접 그린다.
            for day in range(GANTT_WINDOW_DAYS):
                for hour in (6, 12, 18):
                    x = mdates.date2num(win_start + timedelta(days=day, hours=hour))
                    ax.axvline(x, color=COLOR_GRID_MINOR, linewidth=0.7, zorder=1)

            # 토/일은 색으로, 오늘은 굵기로 구분한다.
            for label in ax.get_xticklabels(minor=True):
                dt = mdates.num2date(label.get_position()[0])
                label.set_color(_weekday_color(dt))
                if dt.date() == now.date():
                    label.set_fontweight("bold")
                    label.set_fontsize(12.5)

            # 현재 시각선 — 격자선을 진하게 올린 만큼 이쪽은 더 굵게 가서
            # 날짜 구분선과 헷갈리지 않게 한다.
            now_num = mdates.date2num(now)
            ax.axvline(now_num, color=COLOR_NOW, linewidth=2.2, zorder=6)
            # 배지는 axes 위쪽 안에 붙인다. y를 axes 비율로 잡아야 행 수가
            # 적어 위아래 여백(slack)이 생겨도 첫 행 높이를 따라 내려오지 않는다.
            # 축 밖으로 빼면 tight_layout이 자리를 잡아주지 않아 잘릴 수 있다.
            ax.annotate("NOW", xy=(now_num, 1), xycoords=("data", "axes fraction"),
                        xytext=(0, -3), textcoords="offset points", ha="center", va="top",
                        fontsize=8.5, fontweight="bold", color="white", zorder=7,
                        bbox=dict(boxstyle="round,pad=0.28", fc=COLOR_NOW, ec="none"))
        else:
            ax.text(0.5, 0.5, "이 기간에 예정된 선박이 없습니다", ha="center", va="center",
                    transform=ax.transAxes, color=COLOR_MUTED, fontsize=13)
            ax.set_yticks([])

        ax.set_xlim(win_start_num, win_end_num)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(COLOR_BORDER)
        ax.tick_params(axis="y", length=0)
        fig.tight_layout()
        canvas.draw()

        # 모서리 반경과 라벨 말줄임은 한 번 그린 뒤에 한다. 그래야 renderer와
        # 확정된 축 변환이 생겨서 "이 막대는 픽셀로 몇 칸인지"와 "이 글자는 몇
        # 픽셀인지"를 같은 기준으로 비교할 수 있다.
        if bars:
            _round_bars(ax, bars)
            renderer = canvas.get_renderer()
            for name_txt, code_txt, dot, start, end in pending:
                bar_px = abs(ax.transData.transform((end, 0))[0]
                             - ax.transData.transform((start, 0))[0])
                # 좁은 막대에서는 행선지 점이 글자를 덮으므로 점을 뺀다.
                if dot is not None and bar_px < DOT_MIN_BAR_PX:
                    dot.set_visible(False)
                    dot = None
                if name_txt is None:
                    continue
                # 점이 있으면 글자를 그만큼 오른쪽으로 밀고 쓸 수 있는 폭도 줄인다.
                shift = DOT_INSET_PT if dot is not None else 0
                for artist in (name_txt, code_txt):
                    if artist is not None:
                        artist.xyann = (shift, artist.xyann[1])
                _fit_bar_labels(name_txt, code_txt, bar_px - 8 - shift * 2, renderer)
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
        self.row_counts[WORK_TAB_KEY] = len(rows)
        bars = []

        if rows:
            yticklabels = []
            for i, d in enumerate(rows):
                if i % 2 == 1:
                    ax.axhspan(i - 0.5, i + 0.5, color=COLOR_ROW_ALT, zorder=0)
                track = _add_bar(ax, i, 0, 100, 0.55, COLOR_TRACK, zorder=1)
                bars.append(track)

                if d.get("eta") and d.get("etd"):
                    time_line = f"\n작업예정: {_fmt_dt(d['eta'])} ~ {_fmt_dt(d['etd'])}"
                else:
                    time_line = ""
                tooltip = f"{d['ship_name']}({d.get('line', '')}) · {d.get('voyage', '')}{time_line}\n진행률 {d.get('progress', 0)}%"
                self.hover_targets[WORK_TAB_KEY].append((track, tooltip))

                pct = d.get("progress", 0)
                color = _progress_color(pct)
                if pct > 0:
                    bars.append(_add_bar(ax, i, 0, pct, 0.55, color,
                                         edgecolor=COLOR_CARD, linewidth=1.2, zorder=2))

                # 막대 안에 다 안 들어가면(진행률이 낮으면) 오른쪽 바깥에 쓴다.
                inside = pct >= 18
                name = f"{d['ship_name']}({d.get('line', '')})"
                ax.annotate(name, xy=(pct / 2 if inside else pct + 2, i),
                            xytext=(0, 1.5 if d.get("voyage") else 0),
                            textcoords="offset points",
                            ha="center" if inside else "left",
                            va="bottom" if d.get("voyage") else "center",
                            fontsize=9.5, fontweight="bold",
                            color="white" if inside else COLOR_TEXT, zorder=3, clip_on=True)
                if d.get("voyage"):
                    ax.annotate(d["voyage"], xy=(pct / 2 if inside else pct + 2, i),
                                xytext=(0, -2), textcoords="offset points",
                                ha="center" if inside else "left", va="top", fontsize=8.5,
                                color="white" if inside else COLOR_MUTED,
                                alpha=0.75 if inside else 1.0, zorder=3, clip_on=True)

                ax.text(103, i, f"{pct}%", va="center", ha="left",
                        fontsize=11, color=color, fontweight="bold", zorder=3)

                yticklabels.append(f"{d['terminal']} {d['berth']}선석")

            ax.set_yticks(range(len(rows)))
            ax.set_yticklabels(yticklabels, fontsize=12, color=COLOR_TEXT, fontweight="bold")
            slack = max(MIN_GANTT_ROWS - len(rows), 0) / 2
            ax.set_ylim(len(rows) - 0.5 + slack, -0.5 - slack)
            ax.set_xlim(0, 122)
            ax.xaxis.set_major_locator(mticker.MultipleLocator(25))
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, pos: f"{int(x)}%" if x <= 100 else ""))
            ax.tick_params(axis="x", labelsize=10, colors=COLOR_MUTED, length=0, pad=8)
            ax.grid(axis="x", linestyle="-", linewidth=1, color=COLOR_GRID_MAJOR, zorder=0)
        else:
            ax.text(0.5, 0.5, "현재 작업 중인 본선이 없습니다", ha="center", va="center",
                    transform=ax.transAxes, color=COLOR_MUTED, fontsize=13)
            ax.set_yticks([])
            ax.set_xticks([])

        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(COLOR_BORDER)
        ax.tick_params(axis="y", colors=COLOR_TEXT, length=0)
        fig.tight_layout()
        canvas.draw()

        if bars:
            _round_bars(ax, bars)
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
                # 아래 숫자와 같은 우측 정렬로 맞춘다 (가운데 정렬이면 헤더와
                # 값이 서로 어긋나 보여 어느 열인지 눈으로 따라가기 어렵다).
                tk.Label(parent, text=sub_label, bg=COLOR_CARD, fg=COLOR_MUTED,
                         font=(FONT, 10), anchor="e", padx=10).grid(
                    row=1, column=col, sticky="nsew", pady=(0, 12))

        for dc in divider_cols:
            _divider(0, dc, COLOR_CARD, rowspan=2)

        tk.Frame(parent, bg=COLOR_PRIMARY, height=2).grid(
            row=2, column=0, columnspan=ncols, sticky="ew")

        def _bind_row_hover(cells):
            """행 전체를 함께 밝히는 마우스오버 효과. 셀마다 원래 배경색을
            기억해뒀다가 <Leave>에서 되돌린다."""
            def enter(_event=None):
                for w, _base in cells:
                    w.config(bg=COLOR_HOVER_ROW)

            def leave(_event=None):
                for w, base in cells:
                    w.config(bg=base)

            for w, _base in cells:
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)

        for r, (carrier, terminal) in enumerate(rows):
            d = by_key.get((carrier, terminal))
            counts = d["counts"] if d else {}
            total = d["total"] if d else 0
            bg = COLOR_CARD if r % 2 == 0 else COLOR_ROW_ALT
            grid_row = r + 3
            cells = []

            label_cell = tk.Frame(parent, bg=bg)
            label_cell.grid(row=grid_row, column=LABEL_COL, sticky="nsew")
            dot = tk.Canvas(label_cell, width=8, height=8, bg=bg, highlightthickness=0)
            dot.create_oval(1, 1, 7, 7, fill=EMPTY_CARRIER_COLOR[carrier], outline="")
            dot.pack(side="left", padx=(16, 6), pady=15)
            name = tk.Label(label_cell, text=f"{carrier} · {terminal}", bg=bg, fg=COLOR_TEXT,
                            font=(FONT, 11, "bold"))
            name.pack(side="left")
            cells += [(label_cell, bg), (dot, bg), (name, bg)]

            for _group_label, entries in group_cols:
                for key, _sub_label, col in entries:
                    val = counts.get(key, 0)
                    cell = tk.Label(parent, text=str(val) if val else "–", bg=bg,
                                    fg=COLOR_TEXT if val else COLOR_MUTED, font=(FONT, 11),
                                    anchor="e", padx=10)
                    cell.grid(row=grid_row, column=col, sticky="nsew", pady=15)
                    cells.append((cell, bg))

            for dc in divider_cols:
                _divider(grid_row, dc, bg)

            total_cell = tk.Label(parent, text=str(total), bg=COLOR_PRIMARY_SOFT,
                                  fg=COLOR_PRIMARY_DARK, font=(FONT, 13, "bold"),
                                  anchor="e", justify="right")
            total_cell.grid(row=grid_row, column=total_col, sticky="nsew",
                            padx=(0, 16), pady=15)
            cells.append((total_cell, COLOR_PRIMARY_SOFT))

            _bind_row_hover(cells)

        # 마지막 합계 행: 터미널을 다 더한 값이 궁금할 때 눈으로 세지 않아도 되게.
        sum_row = len(rows) + 3
        tk.Frame(parent, bg=COLOR_BORDER, height=1).grid(
            row=sum_row, column=0, columnspan=ncols, sticky="ew")
        sum_row += 1

        tk.Label(parent, text="합계", bg=COLOR_CARD, fg=COLOR_TEXT,
                 font=(FONT, 11, "bold"), anchor="w", padx=16).grid(
            row=sum_row, column=LABEL_COL, sticky="nsew", pady=14)
        for _group_label, entries in group_cols:
            for key, _sub_label, col in entries:
                val = sum((by_key.get(rk) or {}).get("counts", {}).get(key, 0) for rk in rows)
                tk.Label(parent, text=str(val) if val else "–", bg=COLOR_CARD,
                         fg=COLOR_TEXT if val else COLOR_MUTED, font=(FONT, 11, "bold"),
                         anchor="e", padx=10).grid(
                    row=sum_row, column=col, sticky="nsew", pady=14)
        for dc in divider_cols:
            _divider(sum_row, dc, COLOR_CARD)
        tk.Label(parent, text=str(sum((by_key.get(rk) or {}).get("total", 0) for rk in rows)),
                 bg=COLOR_PRIMARY, fg="white", font=(FONT, 13, "bold"),
                 anchor="e", justify="right").grid(
            row=sum_row, column=total_col, sticky="nsew", padx=(0, 16), pady=14)


def main():
    root = tk.Tk()
    BerthMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
