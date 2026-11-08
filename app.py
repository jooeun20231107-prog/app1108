"""
💌 하루 한 질문 — 우리 가족이 매일 같은 질문에 답하는 앱
Streamlit + GitHub 저장소(JSON/사진/음성) · 단일 파일 app.py

[Streamlit Cloud > Settings > Secrets]
GITHUB_TOKEN  = "github_pat_..."        # Fine-grained token (이 저장소만, Contents: Read and write)
GITHUB_REPO   = "내아이디/저장소이름"
GITHUB_BRANCH = "main"
APP_URL       = "https://내앱.streamlit.app"   # (선택) 가족 초대 QR용
FAMILY_PIN    = "1234"                         # (선택) 가족 비밀번호
Secrets가 없으면 local_data/ 폴더에 임시 저장합니다.
"""
import base64
import io
import json
import os
import random
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import requests
import streamlit as st
from PIL import Image, ImageOps

st.set_page_config(page_title="하루 한 질문", page_icon="💌", layout="centered")

# ═════════════════════════ 저장소 (GitHub / 로컬) ═════════════════════════
DATA_PATH = "data/family.json"
LOCAL_ROOT = Path("local_data")
EMPTY = {"members": {}, "entries": {}, "settings": {}, "events": {}, "moods": {}}


def _norm(d):
    d.setdefault("members", {})
    d.setdefault("entries", {})
    d.setdefault("settings", {})
    d.setdefault("events", {})
    d.setdefault("moods", {})
    return d


class Conflict(Exception):
    pass


def secret(key, default=None):
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def _cfg():
    return secret("GITHUB_TOKEN"), secret("GITHUB_REPO"), secret("GITHUB_BRANCH", "main")


def using_github() -> bool:
    token, repo, _ = _cfg()
    return bool(token and repo)


def _url(path):
    _, repo, _ = _cfg()
    return f"https://api.github.com/repos/{repo}/contents/{path}"


def _headers(raw=False):
    token, _, _ = _cfg()
    accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
    return {"Authorization": f"Bearer {token}", "Accept": accept}


# ---------- 바이너리 (사진/음성) ----------
def put_bytes(path: str, content: bytes):
    if not using_github():
        p = LOCAL_ROOT / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return
    _, _, branch = _cfg()
    sha = None
    r = requests.get(_url(path), headers=_headers(), params={"ref": branch}, timeout=20)
    if r.status_code == 200:
        sha = r.json().get("sha")
    body = {"message": f"add {path}", "content": base64.b64encode(content).decode(), "branch": branch}
    if sha:
        body["sha"] = sha
    r = requests.put(_url(path), headers=_headers(), json=body, timeout=40)
    r.raise_for_status()


@st.cache_data(ttl=3600, show_spinner=False)
def get_bytes(path: str):
    if not using_github():
        p = LOCAL_ROOT / path
        return p.read_bytes() if p.exists() else None
    _, _, branch = _cfg()
    r = requests.get(_url(path), headers=_headers(raw=True), params={"ref": branch}, timeout=30)
    return r.content if r.status_code == 200 else None


# ---------- JSON 데이터 ----------
def _load_with_sha():
    if not using_github():
        p = LOCAL_ROOT / DATA_PATH
        if p.exists():
            return json.loads(p.read_text("utf-8")), None
        return json.loads(json.dumps(EMPTY)), None
    _, _, branch = _cfg()
    r = requests.get(_url(DATA_PATH), headers=_headers(), params={"ref": branch}, timeout=20)
    if r.status_code == 404:
        return json.loads(json.dumps(EMPTY)), None
    r.raise_for_status()
    j = r.json()
    return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]


def _save(data, sha):
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if not using_github():
        p = LOCAL_ROOT / DATA_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
        return
    _, _, branch = _cfg()
    body = {"message": "update family data", "content": base64.b64encode(text.encode()).decode(), "branch": branch}
    if sha:
        body["sha"] = sha
    r = requests.put(_url(DATA_PATH), headers=_headers(), json=body, timeout=30)
    if r.status_code in (409, 422):
        raise Conflict()
    r.raise_for_status()


@st.cache_data(ttl=8, show_spinner=False)
def load():
    data, _ = _load_with_sha()
    return _norm(data)


def update(mutator, retries=4):
    """최신 데이터를 읽어 mutator로 수정 후 저장 (동시 저장 충돌 시 재시도)."""
    for i in range(retries):
        data, sha = _load_with_sha()
        _norm(data)
        mutator(data)
        try:
            _save(data, sha)
            load.clear()
            return data
        except Conflict:
            time.sleep(0.6 * (i + 1))
    raise RuntimeError("저장 충돌이 계속되고 있어요. 잠시 후 다시 시도해주세요.")

storage = SimpleNamespace(secret=secret, using_github=using_github, put_bytes=put_bytes,
                          get_bytes=get_bytes, load=load, update=update)

# ═════════════════════════ 질문 은행 ═════════════════════════
QUESTIONS = [
    # 추억
    ("추억", "내가 어릴 때 가장 좋아했던 음식은 뭐였는지 기억나요?"),
    ("추억", "우리 가족이 함께 갔던 여행 중 제일 기억에 남는 곳은 어디예요?"),
    ("추억", "어릴 적 나(자녀)에 대해 아직도 웃음이 나는 에피소드가 있어요?"),
    ("추억", "학창 시절 가장 좋아했던 과목과 싫어했던 과목은 뭐였어요?"),
    ("추억", "처음 월급(첫 용돈)을 받았을 때 뭘 했는지 기억나세요?"),
    ("추억", "예전에 즐겨 듣던 노래 한 곡만 꼽는다면요?"),
    ("추억", "어릴 때 살던 동네에서 가장 생각나는 장소는 어디예요?"),
    ("추억", "우리 집에서 오래 간직하고 있는 물건 중 가장 애착 가는 건?"),
    ("추억", "제일 맛있게 먹었던 도시락(혹은 간식)은 어떤 거였어요?"),
    ("추억", "가족사진 중 가장 좋아하는 한 장은 언제, 어디서 찍은 거예요?"),
    ("추억", "처음 서울(또는 큰 도시)에 갔던 날이 기억나요?"),
    ("추억", "어릴 적 가장 무서웠던 것은 뭐였어요?"),
    # 오늘
    ("오늘", "오늘 하루 중 가장 좋았던 순간은 언제였어요?"),
    ("오늘", "오늘 점심(또는 저녁)은 뭘 드셨어요? 맛은 어땠어요?"),
    ("오늘", "오늘 날씨를 한 단어로 표현한다면?"),
    ("오늘", "오늘 누군가에게 들은 말 중 기억에 남는 말이 있어요?"),
    ("오늘", "오늘 가장 웃었던 일은 뭐예요?"),
    ("오늘", "지금 창밖에 보이는 풍경을 말해줄래요?"),
    ("오늘", "오늘 기분을 색깔로 표현하면 무슨 색이에요? 왜요?"),
    ("오늘", "오늘 처음 해본 일이 있다면 뭐예요?"),
    ("오늘", "오늘 하루를 점수로 매긴다면 10점 만점에 몇 점?"),
    ("오늘", "지금 제일 먹고 싶은 음식은 뭐예요?"),
    # 감사/마음
    ("마음", "요즘 감사했던 일 하나만 말해주세요."),
    ("마음", "서로에게 고마웠던 순간이 있다면 언제예요?"),
    ("마음", "요즘 걱정되는 일이 있다면 살짝 털어놓아 볼까요?"),
    ("마음", "내가(자녀가) 이럴 때 힘이 된다 싶은 순간은 언제예요?"),
    ("마음", "서로에게 해주고 싶었는데 못 한 말이 있어요?"),
    ("마음", "요즘 나를 웃게 하는 작은 것들은 뭐예요?"),
    ("마음", "우리 가족의 가장 좋은 점 하나만 꼽는다면?"),
    ("마음", "최근에 스스로 칭찬해 주고 싶은 일이 있어요?"),
    # 상상
    ("상상", "일주일간 휴가가 생긴다면 어디로 가고 싶으세요?"),
    ("상상", "복권에 당첨된다면 제일 먼저 뭘 하고 싶어요?"),
    ("상상", "하루 동안 다른 사람이 될 수 있다면 누구가 되고 싶어요?"),
    ("상상", "타임머신이 있다면 과거로 갈래요, 미래로 갈래요? 왜요?"),
    ("상상", "무인도에 딱 세 가지만 가져간다면 뭘 가져갈래요?"),
    ("상상", "10년 뒤 우리 가족은 어떤 모습일까요?"),
    ("상상", "우리 가족이 함께 가게를 연다면 무슨 가게가 좋을까요?"),
    ("상상", "내일 하루 아무 일정이 없다면 뭘 하고 싶어요?"),
    # 취향
    ("취향", "요즘 즐겨 보는 TV 프로그램이나 유튜브 채널이 있어요?"),
    ("취향", "좋아하는 계절과 그 이유는 뭐예요?"),
    ("취향", "가장 좋아하는 꽃이나 식물은 뭐예요?"),
    ("취향", "인생 영화 한 편을 꼽는다면?"),
    ("취향", "아침형 인간이에요, 저녁형 인간이에요?"),
    ("취향", "가장 좋아하는 우리 가족 음식(집밥) 메뉴는 뭐예요?"),
    ("취향", "요즘 새로 배우고 싶은 것이 있어요?"),
    ("취향", "커피, 차, 주스… 요즘 즐겨 마시는 건 뭐예요?"),
    # 삶의 지혜
    ("지혜", "살면서 가장 잘했다고 생각하는 선택은 뭐예요?"),
    ("지혜", "젊은 시절의 나에게 한마디 해준다면 뭐라고 하시겠어요?"),
    ("지혜", "가족에게 꼭 물려주고 싶은 습관이나 마음가짐이 있다면?"),
    ("지혜", "살면서 가장 큰 도움을 준 사람은 누구예요?"),
    ("지혜", "행복이란 뭐라고 생각하세요? 한 문장으로!"),
    ("지혜", "우리 집 가훈을 새로 만든다면 뭐라고 쓸래요?"),
    ("지혜", "최근 가장 잘 먹히는(?) 건강 비결이 있어요?"),
    ("지혜", "내가 가장 자랑스러웠던 순간은 언제였어요?"),
    # 가볍게
    ("가볍게", "라면에 계란 넣어요, 안 넣어요? 파는요?"),
    ("가볍게", "여름 vs 겨울, 하나만 고른다면?"),
    ("가볍게", "치킨은 양념 vs 후라이드?"),
    ("가볍게", "지금 가장 가지고 싶은 물건은 뭐예요?"),
    ("가볍게", "나를 동물에 비유하면 무슨 동물 같아요?"),
    ("가볍게", "가장 최근에 찍은 사진은 뭐예요? (사진으로 보여줘요 📷)"),
    ("가볍게", "오늘 점심 메뉴를 사진으로 찍어 보내주세요 🍚"),
    ("가볍게", "지금 내 곁에 있는 물건 중 가장 오래된 건 뭐예요?"),
]

# 가족 구성원을 지목하는 질문 ({m} = 구성원 호칭, 조사는 에게/의/랑만 사용)
MEMBER_TEMPLATES = [
    ("고마움", "{m}에게 요즘 고마웠던 일을 말해줘요 💛"),
    ("칭찬", "{m}의 가장 멋진 점 하나만 꼽는다면?"),
    ("추억", "{m}랑 함께한 순간 중 가장 기억에 남는 건 뭐예요?"),
    ("상상", "{m}에게 선물을 하나 한다면 뭘 주고 싶어요?"),
    ("마음", "{m}에게 평소 못 한 말 한마디, 오늘 해볼까요?"),
    ("가볍게", "{m}의 은근한 습관 하나를 폭로해 봐요 😆"),
    ("오늘", "지금 {m}은(는) 뭘 하고 있을 것 같아요?"),
]

_ORDER = list(range(len(QUESTIONS)))
random.Random(2026).shuffle(_ORDER)


def question_for(day: date, members: dict | None = None) -> dict:
    """members: {id: {"name": 호칭}} — 2명 이상이면 5일에 한 번 '가족 지목' 질문."""
    members = members or {}
    if len(members) >= 2 and day.toordinal() % 5 == 4:
        ids = sorted(members)
        rnd = random.Random(day.toordinal())
        cat, tpl = rnd.choice(MEMBER_TEMPLATES)
        target = rnd.choice(ids)
        return {"text": tpl.replace("{m}", members[target]["name"]), "category": cat, "by": "bank"}
    idx = _ORDER[(day - date(2026, 1, 1)).days % len(_ORDER)]
    cat, text = QUESTIONS[idx]
    return {"text": text, "category": cat, "by": "bank"}

# ═════════════════════════ 앱 ═════════════════════════
KST = timezone(timedelta(hours=9))
EMOJIS = ["❤️", "😂", "🥹", "👏", "🤗"]
MOODS = [("😊", "좋아요"), ("😐", "그냥 그래요"), ("😢", "속상해요"), ("😡", "화나요"), ("😴", "피곤해요")]
KINDS = {"가족": ("🏠", "#E8743B"), "약속": ("🤝", "#4C9F70"), "학교": ("🏫", "#4A7FD0"),
         "병원": ("🏥", "#C0507A"), "기념일": ("🎉", "#B8860B"), "기타": ("📌", "#7A6A5A")}
RSVP = {"yes": "👍 갈게요", "maybe": "🤔 미정", "no": "🙅 못 가요"}
TITLES = ["엄마", "아빠", "나", "언니", "오빠", "누나", "형", "동생", "할머니", "할아버지"]
TREE = [(0, "🌱", "씨앗"), (3, "🌿", "새싹"), (7, "🪴", "어린 나무"), (14, "🌳", "든든한 나무"),
        (30, "🌸", "꽃 핀 나무"), (60, "🍎", "열매 맺은 나무")]

# ───────────────────────── 유틸 ─────────────────────────
def today() -> date:
    return datetime.now(KST).date()


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_day(d: date):
    return f"{d.year}년 {d.month}월 {d.day}일 ({'월화수목금토일'[d.weekday()]})"


def age_of(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


BLOOD_OPTS = ["A", "B", "O", "AB", "모름"]
CHARS = {
    "A": ("🐰", "몽실이", "섬세한 토끼", "#FFB3C7", "#FFE3EC",
          "꼼꼼하고 배려심 많은 아이. 마음이 여려서 따뜻한 한마디에 쑥쑥 자라요."),
    "B": ("🐱", "자유냥", "자유로운 고양이", "#FFD65C", "#FFF1BE",
          "호기심 많고 솔직한 아이. 재밌는 이야기를 제일 좋아해요."),
    "O": ("🐻", "듬직곰", "든든한 곰", "#FFA46B", "#FFD9BD",
          "넉넉하고 따뜻한 아이. 가족이 모이면 제일 신나요."),
    "AB": ("🦊", "반전여우", "반전 매력 여우", "#B79CFF", "#E4D9FF",
           "엉뚱하고 개성 넘치는 아이. 예상 못 한 대답을 잘해요."),
    "?": ("🥚", "알쏭이", "비밀의 알", "#CFCFCF", "#F0F0F0",
          "아직 정체를 모르는 알! 혈액형을 알려주면 깨어날지도 몰라요."),
}
LEVELS = [(0, "새내기"), (3, "새싹"), (7, "단짝"), (14, "베테랑"), (30, "왕")]
SAY_WAIT = {
    "A": "오늘 질문, 천천히 생각해도 괜찮아요 🌷",
    "B": "오늘 질문 완전 재밌어요! 빨리 답해봐요 ✨",
    "O": "오늘도 가족 이야기 들려주세요 🍀",
    "AB": "오늘은 어떤 반전 대답이 나올까요? 👀",
    "?": "…(알이 꿈틀거려요) 오늘 질문에 답해주세요!",
}
SAY_DONE = {
    "A": "고마워요! 마음이 전해졌을 거예요 💗",
    "B": "야호! 오늘 미션 완료~ 🎉",
    "O": "든든해요! 오늘도 수고했어요 🤗",
    "AB": "역시 센스 있어요! 😎",
    "?": "알이 따뜻해졌어요 🥚💛",
}


def blood_of(m):
    b = m.get("blood", "?")
    return b if b in CHARS else "?"


def avatar(m) -> str:
    return CHARS[blood_of(m)][0]


def member_days(data, mid):
    return sum(1 for e in data["entries"].values() if mid in e.get("answers", {}))


def level_of(days):
    lv = 1
    for i, (need, _) in enumerate(LEVELS):
        if days >= need:
            lv = i + 1
    return lv


@st.cache_data(show_spinner=False)
def char_uri(blood: str, happy: bool, level: int) -> str:
    body, belly = CHARS[blood][3], CHARS[blood][4]
    dk = "#3B2F2F"
    ears = ""
    extra = ""
    if blood == "A":
        for cx, rot in ((34, -8), (66, 8)):
            ears += (f"<ellipse cx='{cx}' cy='24' rx='8' ry='19' fill='{body}' transform='rotate({rot} {cx} 40)'/>"
                     f"<ellipse cx='{cx}' cy='26' rx='4' ry='12' fill='#FF9DB5' transform='rotate({rot} {cx} 40)'/>")
    elif blood == "B":
        ears = (f"<polygon points='22,44 27,12 47,30' fill='{body}'/><polygon points='78,44 73,12 53,30' fill='{body}'/>"
                "<polygon points='28,36 30,20 40,30' fill='#FF9DB5'/><polygon points='72,36 70,20 60,30' fill='#FF9DB5'/>")
        extra = (f"<g stroke='{dk}' stroke-width='1.6' stroke-linecap='round'>"
                 "<line x1='20' y1='62' x2='32' y2='64'/><line x1='20' y1='69' x2='32' y2='67'/>"
                 "<line x1='80' y1='62' x2='68' y2='64'/><line x1='80' y1='69' x2='68' y2='67'/></g>")
    elif blood == "O":
        for cx in (27, 73):
            ears += (f"<circle cx='{cx}' cy='32' r='11' fill='{body}'/><circle cx='{cx}' cy='33' r='5.5' fill='#FFD9BD'/>")
    elif blood == "AB":
        ears = (f"<polygon points='20,46 26,6 48,30' fill='{body}'/><polygon points='80,46 74,6 52,30' fill='{body}'/>"
                f"<polygon points='26,38 28,16 40,30' fill='{dk}' opacity='.75'/><polygon points='74,38 72,16 60,30' fill='{dk}' opacity='.75'/>")
        extra = (f"<ellipse cx='88' cy='78' rx='9' ry='14' fill='{body}' transform='rotate(25 88 78)'/>"
                 "<ellipse cx='93' cy='88' rx='4' ry='6' fill='#fff' transform='rotate(25 93 88)'/>")
    if blood == "?":
        body_shape = f"<ellipse cx='50' cy='58' rx='30' ry='36' fill='{body}'/>"
        belly_shape = ""
    else:
        body_shape = f"<circle cx='50' cy='58' r='33' fill='{body}'/>"
        belly_shape = f"<ellipse cx='50' cy='72' rx='19' ry='14' fill='{belly}'/>"
    if happy:
        eyes = (f"<path d='M32 54 Q37 46 42 54' stroke='{dk}' stroke-width='3.2' fill='none' stroke-linecap='round'/>"
                f"<path d='M58 54 Q63 46 68 54' stroke='{dk}' stroke-width='3.2' fill='none' stroke-linecap='round'/>")
        mouth = "<path d='M43 63 Q50 74 57 63 Z' fill='#C94B4B'/>"
        cheek_op = ".75"
    else:
        eyes = (f"<circle cx='38' cy='54' r='4' fill='{dk}'/><circle cx='62' cy='54' r='4' fill='{dk}'/>"
                "<circle cx='39.4' cy='52.6' r='1.4' fill='#fff'/><circle cx='63.4' cy='52.6' r='1.4' fill='#fff'/>")
        mouth = f"<path d='M46 65 Q50 68 54 65' stroke='{dk}' stroke-width='2.2' fill='none' stroke-linecap='round'/>"
        cheek_op = ".45"
    cheeks = (f"<ellipse cx='29' cy='63' rx='6' ry='4' fill='#FF8FA3' opacity='{cheek_op}'/>"
              f"<ellipse cx='71' cy='63' rx='6' ry='4' fill='#FF8FA3' opacity='{cheek_op}'/>")
    nose = f"<ellipse cx='50' cy='60' rx='2.6' ry='1.9' fill='{dk}'/>"
    acc = ""
    if level >= 5:
        acc += ("<polygon points='38,30 39,16 45,23 50,12 55,23 61,16 62,30' fill='#FFC933' stroke='#E0A100' stroke-width='1.5'/>"
                "<circle cx='50' cy='20' r='1.8' fill='#E8553B'/>")
    elif level == 4:
        acc += ("<g transform='translate(50 23)'>" + "".join(
            f"<circle cx='{x}' cy='{y}' r='4.2' fill='#FF7DA0'/>" for x, y in ((0, -5), (5, -1.5), (3, 4.5), (-3, 4.5), (-5, -1.5)))
            + "<circle r='3' fill='#FFE066'/></g>")
    elif level >= 2:
        acc += ("<path d='M50 28 L50 21' stroke='#4C9A3F' stroke-width='2.4' stroke-linecap='round'/>"
                "<ellipse cx='44' cy='19' rx='6' ry='3.4' fill='#6CC15A' transform='rotate(-25 44 19)'/>"
                "<ellipse cx='56' cy='19' rx='6' ry='3.4' fill='#6CC15A' transform='rotate(25 56 19)'/>")
    if level >= 3:
        acc += "<path d='M29 84 Q50 96 71 84 L72 92 Q50 104 28 92 Z' fill='#E8553B'/><rect x='56' y='90' width='8' height='12' rx='3' fill='#C93F27'/>"
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 106'>"
           "<ellipse cx='50' cy='100' rx='26' ry='4' fill='#000' opacity='.08'/>"
           f"{extra}{ears}{body_shape}{belly_shape}{cheeks}{eyes}{nose}{mouth}{acc}</svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def char_img(data, mid, happy=False, size=64, extra_cls=""):
    m = data["members"][mid]
    lv = level_of(member_days(data, mid))
    return f"<img class='cimg {extra_cls}' src='{char_uri(blood_of(m), happy, lv)}' style='width:{size}px;height:{round(size*1.06)}px'>"


def label(data, mid, me=None):
    m = data["members"].get(mid)
    if not m:
        return "(떠난 사람)"
    return "나" if mid == me else m["name"]


def next_birthday(birth: date, on: date) -> date:
    def mk(y):
        try:
            return birth.replace(year=y)
        except ValueError:
            return date(y, 3, 1)
    nb = mk(on.year)
    return nb if nb >= on else mk(on.year + 1)


def valid_answers(data, e):
    return {k: v for k, v in (e or {}).get("answers", {}).items() if k in data["members"]}


def day_done(data, e):
    need = min(2, max(1, len(data["members"])))
    return len(valid_answers(data, e)) >= need


def stats(data):
    done = {k for k, v in data["entries"].items() if day_done(data, v)}
    d = today()
    if d.isoformat() not in done:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in done:
        n += 1
        d -= timedelta(days=1)
    return n, len(done)


def tree_state(total):
    cur = TREE[0]
    nxt = None
    for i, t in enumerate(TREE):
        if total >= t[0]:
            cur = t
            nxt = TREE[i + 1] if i + 1 < len(TREE) else None
    prog = 1.0 if not nxt else (total - cur[0]) / (nxt[0] - cur[0])
    return cur, nxt, prog


def compress_image(file) -> bytes:
    img = ImageOps.exif_transpose(Image.open(file)).convert("RGB")
    img.thumbnail((1000, 1000))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80, optimize=True)
    return buf.getvalue()


def birthday_people(data, day):
    out = []
    for mid, m in data["members"].items():
        try:
            b = date.fromisoformat(m["birth"])
        except Exception:
            continue
        if next_birthday(b, day) == day:
            out.append(mid)
    return out


def get_question(data, day: date):
    e = data["entries"].get(day.isoformat())
    if e and e.get("question"):
        return e["question"]
    bp = birthday_people(data, day)
    if bp:
        n = data["members"][bp[0]]["name"]
        return {"text": f"🎂 오늘은 {n}의 생일! 축하 메시지와 함께 {n}에게 해주고 싶었던 말을 남겨요",
                "category": "생일", "by": "bank"}
    return question_for(day, data["members"])


# ───────────────────────── 스타일 ─────────────────────────
def inject_css(big: bool):
    base = "20px" if big else "17px"
    st.markdown(
        f"""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
html, body, [class*="css"], .stApp {{ font-family:'Pretendard',sans-serif; font-size:{base}; }}
.block-container {{ padding-top:1.2rem; padding-bottom:5rem; max-width:640px; }}
#MainMenu, footer, header {{ visibility:hidden; }}
.stApp {{ background:#FFF8EC; color:#3B2F2F; }}
.stApp p, .stApp label, .stApp span, .stApp li {{ color:#3B2F2F; }}
.hero, .hero * {{ color:#fff !important; }}
button[kind="primary"] {{ background:#E8743B !important; border-color:#E8743B !important; color:#fff !important; }}
button[kind="primary"] * {{ color:#fff !important; }}
.hero {{ background:linear-gradient(135deg,#FF9A5A,#E8743B); color:#fff; border-radius:26px;
        padding:26px 24px; box-shadow:0 8px 24px rgba(232,116,59,.28); margin:6px 0 14px; }}
.hero .cat {{ display:inline-block; background:rgba(255,255,255,.25); padding:3px 12px; border-radius:20px; font-size:.78em; font-weight:600; }}
.hero .q {{ font-size:1.45em; font-weight:800; line-height:1.45; margin-top:12px; word-break:keep-all; }}
.hero .by {{ margin-top:10px; font-size:.82em; opacity:.92; }}
.date {{ color:#9a7b64; font-weight:600; font-size:.9em; }}
.chips {{ display:flex; gap:8px; flex-wrap:wrap; margin:4px 0 6px; }}
.chip {{ background:#fff; border:1.5px solid #F4D9B8; border-radius:20px; padding:5px 14px; font-weight:700; font-size:.85em; }}
.faces {{ display:flex; gap:10px; justify-content:center; flex-wrap:wrap; margin:8px 0 14px; }}
.face {{ width:76px; text-align:center; opacity:.45; filter:grayscale(.7); }}
.face.done {{ opacity:1; filter:none; }}
.cbox {{ position:relative; width:64px; margin:auto; }}
.face .av {{ font-size:2.1em; background:#fff; border:2.5px solid #F4D9B8; border-radius:50%; width:62px; height:62px; line-height:62px; margin:auto; position:relative; }}
.face.done .av {{ border-color:#E8743B; }}
.face.done .cbox:after {{ content:'✓'; position:absolute; right:-2px; top:-2px; background:#E8743B; color:#fff; font-size:.7em; font-weight:800; width:20px; height:20px; line-height:20px; border-radius:50%; text-align:center; }}
.face.done .av:after {{ content:'✓'; position:absolute; right:-4px; bottom:-2px; background:#E8743B; color:#fff; font-size:.38em; font-weight:800; width:20px; height:20px; line-height:20px; border-radius:50%; }}
.face .nm {{ font-size:.78em; font-weight:700; margin-top:4px; word-break:keep-all; }}
.card {{ background:#fff; border-radius:20px; padding:16px 20px; margin:10px 0 4px; border:1.5px solid #F4D9B8; box-shadow:0 2px 10px rgba(120,80,40,.06); }}
.card .who {{ font-weight:800; color:#E8743B; margin-bottom:6px; }}
.card .txt {{ white-space:pre-wrap; line-height:1.65; word-break:keep-all; }}
.card .time {{ color:#b09a88; font-size:.75em; margin-top:8px; }}
.say {{ display:flex; align-items:center; gap:10px; margin:6px 0 10px; }}
.bubble {{ position:relative; background:#fff; border:1.5px solid #F4D9B8; border-radius:18px; padding:11px 14px; font-weight:700; line-height:1.5; word-break:keep-all; box-shadow:0 2px 10px rgba(120,80,40,.06); }}
.bubble:before {{ content:''; position:absolute; left:-8px; top:20px; width:12px; height:12px; background:#fff; border-left:1.5px solid #F4D9B8; border-bottom:1.5px solid #F4D9B8; transform:rotate(45deg); }}
.pet {{ background:linear-gradient(180deg,#FFF3DF,#fff); border:1.5px solid #F4D9B8; border-radius:24px; padding:16px; text-align:center; margin:8px 0; }}
.pet .nm {{ font-size:1.2em; font-weight:800; margin-top:4px; }}
.pet .ds {{ color:#7a6352; font-size:.88em; line-height:1.5; margin-top:4px; word-break:keep-all; }}
.lvbadge {{ display:inline-block; background:#E8743B; color:#fff !important; border-radius:12px; padding:2px 10px; font-size:.78em; font-weight:700; }}
.mrow img {{ flex:none; }}
.lock {{ background:#fff; border:2px dashed #F0C79C; border-radius:20px; padding:16px; text-align:center; margin:8px 0; line-height:1.7; }}
.lock .big {{ font-size:1.8em; }}
.tree {{ background:linear-gradient(180deg,#EAF7E0,#fff); border:1.5px solid #CFE8BC; border-radius:22px; padding:18px; text-align:center; margin:8px 0; }}
.tree .t {{ font-size:4em; line-height:1.1; }}
.bday {{ background:#FFF0F5; border:1.5px solid #F7C6D8; border-radius:18px; padding:12px 16px; margin:8px 0; font-weight:700; text-align:center; }}
.mrow {{ display:flex; align-items:center; gap:12px; background:#fff; border:1.5px solid #F4D9B8; border-radius:16px; padding:10px 14px; margin:6px 0; }}
.mrow .a {{ font-size:1.8em; }} .mrow .n {{ font-weight:800; }} .mrow .s {{ color:#9a7b64; font-size:.8em; }}
.mrow .d {{ margin-left:auto; font-weight:800; color:#E8743B; }}
.past {{ background:#FFF3DF; border-radius:18px; padding:14px 18px; margin:10px 0; }}
.stButton>button, .stDownloadButton>button {{ border-radius:16px; min-height:3rem; font-weight:700; font-size:1em; }}
.stTextArea textarea {{ font-size:1.05em; border-radius:14px; }}
.stTabs [data-baseweb="tab"] {{ font-weight:700; font-size:1em; padding:10px 12px; }}
</style>""",
        unsafe_allow_html=True,
    )


# ───────────────────────── 접속 / 가입 ─────────────────────────
def register_form(data, first: bool):
    st.markdown("### 💌 하루 한 질문")
    st.write("우리 가족 이야기를 시작해요 🌱" if first else "새 가족을 등록해요")
    st.caption("가족이 부르는 호칭(엄마, 아빠, 언니 …)이나 이름으로 적어주세요. 내 화면에는 '나'로 보여요.")
    pill = st.pills("자주 쓰는 호칭", TITLES, selection_mode="single", key="pill")
    name = st.text_input("호칭 / 이름", value=pill or "", key=f"nm_{pill}", max_chars=10)
    birth = st.date_input("생년월일", value=date(1985, 1, 1), min_value=date(1920, 1, 1),
                          max_value=today(), format="YYYY/MM/DD")
    g = st.radio("성별", ["여자", "남자", "선택 안 함"], horizontal=True)
    blood = st.radio("혈액형 (나만의 캐릭터가 정해져요 🐰🐱🐻🦊)", BLOOD_OPTS, horizontal=True, index=4)
    if st.button("시작하기", type="primary", use_container_width=True):
        name = name.strip()
        if not name:
            st.warning("호칭이나 이름을 적어주세요.")
            return
        if any(m["name"] == name for m in data["members"].values()):
            st.warning("이미 같은 호칭이 있어요. 구분되게 적어주세요 (예: 큰언니, 민지).")
            return
        mid = uuid.uuid4().hex[:8]

        def m(d):
            d["members"][mid] = {"name": name, "birth": birth.isoformat(),
                                 "gender": {"여자": "f", "남자": "m"}.get(g, "n"),
                                 "blood": "?" if blood == "모름" else blood,
                                 "joined": today().isoformat()}
        storage.update(m)
        st.session_state.me = mid
        st.query_params["m"] = mid
        st.rerun()


def gate():
    qp = st.query_params
    pin = storage.secret("FAMILY_PIN")
    if pin and not st.session_state.get("pin_ok"):
        if qp.get("k") == str(pin):
            st.session_state.pin_ok = True
        else:
            st.markdown("### 💌 하루 한 질문")
            v = st.text_input("가족 비밀번호를 입력해 주세요", type="password")
            if v and v == str(pin):
                st.session_state.pin_ok = True
                st.rerun()
            elif v:
                st.error("비밀번호가 달라요.")
            st.stop()

    data = storage.load()
    me = st.session_state.get("me") or qp.get("m")
    if me in data["members"]:
        st.session_state.me = me
        return data, me

    if not data["members"]:
        register_form(data, first=True)
        st.stop()
    if st.session_state.get("adding"):
        register_form(data, first=False)
        if st.button("← 돌아가기"):
            st.session_state.adding = False
            st.rerun()
        st.stop()
    st.markdown("### 💌 하루 한 질문")
    st.write("누구세요?")
    for mid, m in data["members"].items():
        if st.button(f"{avatar(m)}  {m['name']}", key=f"pick_{mid}", use_container_width=True):
            st.session_state.me = mid
            st.query_params["m"] = mid
            st.rerun()
    if st.button("➕ 처음이에요 (새로 등록)", use_container_width=True):
        st.session_state.adding = True
        st.rerun()
    st.stop()


# ───────────────────────── 조각 ─────────────────────────
def hero(data, q, day):
    by = ""
    if q.get("by") in data["members"]:
        by = f"<div class='by'>💌 {esc(data['members'][q['by']]['name'])}의 질문이에요</div>"
    st.markdown(
        f"<div class='date'>{fmt_day(day)}</div><div class='hero'><span class='cat'>{esc(q.get('category','오늘의 질문'))}</span>"
        f"<div class='q'>{esc(q['text'])}</div>{by}</div>", unsafe_allow_html=True)


def faces(data, e):
    done = valid_answers(data, e)
    h = "".join(
        f"<div class='face {'done' if mid in done else ''}'><div class='cbox'>{char_img(data, mid, mid in done, 62)}</div>"
        f"<div class='nm'>{esc(m['name'])}</div></div>"
        for mid, m in data["members"].items())
    st.markdown(f"<div class='faces'>{h}</div>", unsafe_allow_html=True)


def char_banner(data, me, answered):
    m = data["members"][me]
    b = blood_of(m)
    msg = (SAY_DONE if answered else SAY_WAIT)[b]
    st.markdown(f"<div class='say'>{char_img(data, me, answered, 84)}<div class='bubble'>{esc(msg)}</div></div>",
                unsafe_allow_html=True)


def answer_card(name, av, ans):
    st.markdown(
        f"<div class='card'><div class='who'>{av} {esc(name)}</div><div class='txt'>{esc(ans.get('text','') if not ans.get('pass') else '🤐 오늘은 패스했어요. 괜찮아요, 내일 또 만나요')}</div>"
        f"<div class='time'>{ans.get('at','')[11:16]}</div></div>", unsafe_allow_html=True)
    if ans.get("photo"):
        b = storage.get_bytes(ans["photo"])
        if b:
            st.image(b, use_container_width=True)
    if ans.get("audio"):
        b = storage.get_bytes(ans["audio"])
        if b:
            st.audio(b, format="audio/wav")


def reaction_bar(data, k, e, owner, me, interactive=True):
    rx = e.get("reactions", {}).get(owner, {})
    if interactive and owner != me:
        cols = st.columns(len(EMOJIS))
        for c, em in zip(cols, EMOJIS):
            if c.button(em, key=f"r_{k}_{owner}_{em}", use_container_width=True,
                        type="primary" if rx.get(me) == em else "secondary"):
                def m(d, em=em):
                    r = d["entries"][k].setdefault("reactions", {}).setdefault(owner, {})
                    if r.get(me) == em:
                        r.pop(me, None)
                    else:
                        r[me] = em
                storage.update(m)
                st.rerun()
    if rx:
        by = {}
        for who, em in rx.items():
            by.setdefault(em, []).append(label(data, who, me))
        st.caption("  ".join(f"{em} {', '.join(n)}" for em, n in by.items()))


def answer_form(data, me, day, q):
    k = day.isoformat()
    st.markdown("#### ✍️ 나의 대답")
    text = st.text_area("대답", key=f"t_{k}", height=130, label_visibility="collapsed",
                        placeholder="편하게 한두 줄이면 충분해요 :)")
    c1, c2 = st.columns(2)
    with c1:
        photo = st.file_uploader("📷 사진 (선택)", type=["jpg", "jpeg", "png"], key=f"p_{k}")
    audio = None
    with c2:
        if hasattr(st, "audio_input"):
            audio = st.audio_input("🎙️ 목소리 (선택)", key=f"a_{k}")
    if st.button("💌 보내기", type="primary", use_container_width=True, key=f"s_{k}"):
        if not (text.strip() or photo or audio):
            st.warning("한 줄이라도 적어주세요 :)")
            return
        with st.spinner("전하는 중…"):
            ans = {"text": text.strip(), "at": datetime.now(KST).isoformat(timespec="seconds")}
            try:
                if photo:
                    p = f"data/media/{k}_{me}.jpg"
                    storage.put_bytes(p, compress_image(photo))
                    ans["photo"] = p
                if audio:
                    p = f"data/media/{k}_{me}.wav"
                    storage.put_bytes(p, audio.getvalue())
                    ans["audio"] = p
            except Exception as ex:
                st.error(f"사진/음성을 올리지 못했어요: {ex}")
                return

            def m(d):
                e = d["entries"].setdefault(k, {})
                e.setdefault("question", q)
                e.setdefault("answers", {})[me] = ans
            storage.update(m)
        st.balloons()
        st.rerun()
    st.caption("말하기 싫은 날도 있어요. 부담 갖지 말고 패스해도 돼요.")
    if st.button("오늘은 패스할래요 🤐", use_container_width=True, key=f"pass_{k}"):
        def mp(d):
            e = d["entries"].setdefault(k, {})
            e.setdefault("question", q)
            e.setdefault("answers", {})[me] = {"text": "", "pass": True,
                                               "at": datetime.now(KST).isoformat(timespec="seconds")}
        storage.update(mp)
        st.rerun()


def mood_bar(data, me, k):
    moods = {m: v for m, v in data["moods"].get(k, {}).items() if m in data["members"]}
    st.markdown("##### 오늘 기분은? <span style='font-weight:400;font-size:.8em;color:#9a7b64'>(눌러만 주세요, 말 안 해도 돼요)</span>",
                unsafe_allow_html=True)
    cols = st.columns(len(MOODS))
    for c, (em, lb) in zip(cols, MOODS):
        if c.button(em, key=f"mood_{k}_{em}", use_container_width=True, help=lb,
                    type="primary" if moods.get(me) == em else "secondary"):
            def mm(d, em=em):
                d["moods"].setdefault(k, {})[me] = em
            storage.update(mm)
            st.rerun()
    if moods:
        st.caption("  ·  ".join(f"{label(data, mid, me)} {em}" for mid, em in moods.items()))


# ───────────────────────── 탭: 오늘 ─────────────────────────
def tab_today(data, me):
    day = today()
    k = day.isoformat()
    q = get_question(data, day)
    e = data["entries"].get(k, {})
    answers = valid_answers(data, e)
    s, total = stats(data)
    cur, _, _ = tree_state(total)

    st.markdown(f"<div class='chips'><span class='chip'>🔥 {s}일 연속</span>"
                f"<span class='chip'>{cur[1]} {cur[2]}</span><span class='chip'>📖 함께한 {total}일</span></div>",
                unsafe_allow_html=True)
    for bid in birthday_people(data, day):
        st.markdown(f"<div class='bday'>🎂 오늘은 {esc(label(data, bid, me))}의 생일이에요! 축하해 주세요 🎉</div>",
                    unsafe_allow_html=True)
    hero(data, q, day)
    faces(data, e)
    char_banner(data, me, me in answers)
    mood_bar(data, me, k)

    if me not in answers:
        n = len(answers)
        if n:
            st.markdown(f"<div class='lock'><div class='big'>🔒</div>{n}명이 먼저 답했어요.<br>"
                        f"<b>내 대답을 보내면 바로 열어볼 수 있어요!</b></div>", unsafe_allow_html=True)
        answer_form(data, me, day, q)
    else:
        waiting = [m["name"] for mid, m in data["members"].items() if mid not in answers]
        if waiting:
            st.info(f"⏳ 아직 {', '.join(waiting)}의 대답을 기다리고 있어요.")
        else:
            st.success("🎉 우리 가족 모두 답했어요!")
        order = [me] + [x for x in answers if x != me]
        for mid in order:
            answer_card(label(data, mid, me), avatar(data["members"][mid]), answers[mid])
            reaction_bar(data, k, e, mid, me)
        if st.button("🔄 새로고침", use_container_width=True):
            storage.load.clear()
            st.rerun()

    # 1년 전 오늘
    for yrs in range(1, 6):
        try:
            pd = day.replace(year=day.year - yrs)
        except ValueError:
            continue
        pe = data["entries"].get(pd.isoformat())
        if pe and valid_answers(data, pe):
            with st.expander(f"📮 {yrs}년 전 오늘 ({pd.year}.{pd.month}.{pd.day})"):
                st.markdown(f"**{pe['question']['text']}**")
                for mid, a in valid_answers(data, pe).items():
                    answer_card(label(data, mid, me), avatar(data["members"][mid]), a)


# ───────────────────────── 탭: 캘린더 ─────────────────────────
CAL_CSS = """
<style>
.cal { width:100%; border-collapse:separate; border-spacing:3px; table-layout:fixed; }
.cal th { font-size:.75em; color:#9a7b64; padding:4px 0; }
.cal td { background:#fff; border:1.5px solid #F4D9B8; border-radius:10px; height:68px; vertical-align:top; padding:3px; overflow:hidden; }
.cal td.empty { background:transparent; border:none; }
.cal td.today { border:2.5px solid #E8743B; background:#FFF3E6; }
.cal .dn { font-size:.78em; font-weight:800; }
.cal .ev { font-size:.58em; border-radius:6px; padding:1px 3px; margin-top:2px; color:#fff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:700; }
.evcard { background:#fff; border:1.5px solid #F4D9B8; border-left:6px solid var(--c); border-radius:14px; padding:10px 14px; margin:8px 0 2px; }
.evcard .t { font-weight:800; } .evcard .s { color:#9a7b64; font-size:.8em; }
</style>
"""


def events_on(data, day):
    out = [(eid, ev) for eid, ev in data["events"].items() if ev["date"] == day.isoformat()]
    out.sort(key=lambda x: x[1].get("time") or "00:00")
    return out


def tab_calendar(data, me):
    import calendar as pycal
    st.markdown(CAL_CSS, unsafe_allow_html=True)
    day = today()
    y, mth = st.session_state.get("cal_ym", (day.year, day.month))
    c1, c2, c3 = st.columns([1, 3, 1])
    if c1.button("◀", key="cal_prev", use_container_width=True):
        st.session_state.cal_ym = (y - 1, 12) if mth == 1 else (y, mth - 1)
        st.rerun()
    c2.markdown(f"<div style='text-align:center;font-weight:800;font-size:1.2em;padding-top:6px'>{y}년 {mth}월</div>", unsafe_allow_html=True)
    if c3.button("▶", key="cal_next", use_container_width=True):
        st.session_state.cal_ym = (y + 1, 1) if mth == 12 else (y, mth + 1)
        st.rerun()

    bdays = {}
    for m in data["members"].values():
        try:
            b = date.fromisoformat(m["birth"])
            bdays.setdefault((b.month, b.day), []).append(m["name"])
        except Exception:
            pass
    rows = "<tr>" + "".join(f"<th>{w}</th>" for w in "일월화수목금토") + "</tr>"
    for week in pycal.Calendar(firstweekday=6).monthdayscalendar(y, mth):
        rows += "<tr>"
        for d in week:
            if d == 0:
                rows += "<td class='empty'></td>"
                continue
            dd = date(y, mth, d)
            items = "".join(f"<div class='ev' style='background:#E58AA8'>🎂{esc(n)}</div>" for n in bdays.get((mth, d), []))
            evs = events_on(data, dd)
            for eid, ev in evs[:2]:
                ico, col = KINDS.get(ev.get("kind", "기타"), KINDS["기타"])
                items += f"<div class='ev' style='background:{col}'>{ico}{esc(ev['title'])}</div>"
            if len(evs) > 2:
                items += f"<div class='ev' style='background:#bbb'>+{len(evs)-2}</div>"
            rows += f"<td class='{'today' if dd == day else ''}'><div class='dn'>{d}</div>{items}</td>"
        rows += "</tr>"
    st.markdown(f"<table class='cal'>{rows}</table>", unsafe_allow_html=True)

    st.markdown("#### 📅 날짜별 보기")
    sel = st.date_input("날짜", value=day if (day.year, day.month) == (y, mth) else date(y, mth, 1),
                        format="YYYY/MM/DD", label_visibility="collapsed", key="cal_sel")
    for n in bdays.get((sel.month, sel.day), []):
        st.markdown(f"<div class='bday'>🎂 {esc(n)}의 생일이에요!</div>", unsafe_allow_html=True)
    evs = events_on(data, sel)
    if not evs and not bdays.get((sel.month, sel.day)):
        st.caption("이 날은 일정이 없어요.")
    for eid, ev in evs:
        ico, col = KINDS.get(ev.get("kind", "기타"), KINDS["기타"])
        tm = f" · {ev['time']}" if ev.get("time") else ""
        note = f"<div class='s'>{esc(ev['note'])}</div>" if ev.get("note") else ""
        rs = ev.get("rsvp", {})
        rtxt = "  ".join(f"{RSVP[v].split()[0]} {label(data, mid, me)}" for mid, v in rs.items() if mid in data["members"])
        st.markdown(f"<div class='evcard' style='--c:{col}'><div class='t'>{ico} {esc(ev['title'])}</div>"
                    f"<div class='s'>{esc(label(data, ev.get('by'), me))} 등록{tm}</div>{note}</div>", unsafe_allow_html=True)
        if rtxt:
            st.caption(rtxt)
        mine = ev.get("by") == me
        cols = st.columns(len(RSVP) + (1 if mine else 0))
        for c, (key, lb) in zip(cols, RSVP.items()):
            if c.button(lb, key=f"rsvp_{eid}_{key}", use_container_width=True,
                        type="primary" if rs.get(me) == key else "secondary"):
                def mr(d, eid=eid, key=key):
                    d["events"][eid].setdefault("rsvp", {})[me] = key
                storage.update(mr)
                st.rerun()
        if mine and cols[-1].button("🗑", key=f"del_{eid}", use_container_width=True):
            def md(d, eid=eid):
                d["events"].pop(eid, None)
            storage.update(md)
            st.rerun()

    with st.expander("➕ 일정 추가", expanded=not data["events"]):
        title = st.text_input("무슨 일정이에요?", max_chars=30, key="ev_title", placeholder="예) 가족 저녁식사, 기말고사, 치과")
        d1, d2 = st.columns(2)
        edate = d1.date_input("날짜", value=sel, format="YYYY/MM/DD", key="ev_date")
        kind = d2.selectbox("종류", list(KINDS), format_func=lambda x: f"{KINDS[x][0]} {x}", key="ev_kind")
        use_t = st.checkbox("시간 정하기", key="ev_ut")
        etime = st.time_input("시간", value=datetime.strptime("18:00", "%H:%M").time(), key="ev_time") if use_t else None
        note = st.text_input("메모 (선택)", max_chars=60, key="ev_note")
        if st.button("일정 등록", type="primary", use_container_width=True, key="ev_save"):
            if not title.strip():
                st.warning("일정 이름을 적어주세요.")
            else:
                eid = uuid.uuid4().hex[:8]

                def ma(d):
                    d["events"][eid] = {"title": title.strip(), "date": edate.isoformat(), "kind": kind,
                                        "time": etime.strftime("%H:%M") if etime else "", "note": note.strip(),
                                        "by": me, "rsvp": {me: "yes"}}
                storage.update(ma)
                st.success("등록했어요")
                st.rerun()

    up = sorted((ev["date"], ev.get("time") or "", eid, ev) for eid, ev in data["events"].items()
                if day.isoformat() <= ev["date"] <= (day + timedelta(days=30)).isoformat())
    if up:
        st.markdown("#### ⏰ 앞으로 30일")
        for dstr, tm, eid, ev in up[:8]:
            d = date.fromisoformat(dstr)
            diff = (d - day).days
            ico = KINDS.get(ev.get("kind", "기타"), KINDS["기타"])[0]
            st.markdown(f"- **{'오늘' if diff == 0 else f'D-{diff}'}** · {d.month}/{d.day} {tm} {ico} {esc(ev['title'])}")


# ───────────────────────── 탭: 이야기책 ─────────────────────────
def build_book(data, days):
    parts = []
    for k in sorted(days):
        e = data["entries"][k]
        d = date.fromisoformat(k)
        b = f"<section><div class='d'>{fmt_day(d)}</div><h2>{esc(e['question']['text'])}</h2>"
        for mid, a in valid_answers(data, e).items():
            b += f"<div class='a'><b>{avatar(data['members'][mid])} {esc(data['members'][mid]['name'])}</b><p>{esc(a.get('text',''))}</p>"
            if a.get("photo"):
                raw = storage.get_bytes(a["photo"])
                if raw:
                    b += f"<img src='data:image/jpeg;base64,{base64.b64encode(raw).decode()}'>"
            b += "</div>"
        parts.append(b + "</section>")
    return ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>우리 가족 이야기책</title><style>body{font-family:'Pretendard',sans-serif;background:#FFF8EC;color:#3B2F2F;"
            "max-width:680px;margin:auto;padding:24px;line-height:1.7}h1{text-align:center}section{background:#fff;border-radius:20px;"
            "padding:20px;margin:18px 0;border:1.5px solid #F4D9B8;page-break-inside:avoid}.d{color:#9a7b64;font-size:.85em}"
            "h2{color:#E8743B;font-size:1.15em}.a{margin:10px 0}.a p{white-space:pre-wrap;margin:4px 0}img{max-width:100%;border-radius:12px}"
            "</style><h1>💌 우리 가족 이야기책</h1>" + "".join(parts)).encode("utf-8")


def tab_book(data, me):
    done = sorted((k for k, v in data["entries"].items() if day_done(data, v)), reverse=True)
    if not done:
        st.markdown("<div class='lock'><div class='big'>📖</div>아직 비어 있어요.<br>오늘 첫 질문에 함께 답하면 첫 장이 만들어져요.</div>",
                    unsafe_allow_html=True)
        return
    st.caption(f"지금까지 {len(done)}장의 이야기가 쌓였어요.")
    st.download_button("📥 이야기책 내려받기 (HTML)", build_book(data, done), file_name="우리가족_이야기책.html",
                       mime="text/html", use_container_width=True)
    for k in done:
        e = data["entries"][k]
        d = date.fromisoformat(k)
        with st.expander(f"{d.month}/{d.day} · {e['question']['text'][:26]}…"):
            st.markdown(f"**{e['question']['text']}**")
            for mid, a in valid_answers(data, e).items():
                answer_card(label(data, mid, me), avatar(data["members"][mid]), a)
                reaction_bar(data, k, e, mid, me, interactive=False)


# ───────────────────────── 탭: 가족 ─────────────────────────
def make_qr(url: str):
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(url).save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


def app_url(data):
    return storage.secret("APP_URL") or data["settings"].get("app_url", "")


def tab_family(data, me):
    day = today()
    s, total = stats(data)
    cur, nxt, prog = tree_state(total)
    st.markdown(f"<div class='tree'><div class='t'>{cur[1]}</div><b>{cur[2]}</b><br>"
                f"<span style='color:#6b8f5a;font-size:.85em'>함께 답한 날이 쌓일수록 자라요 · 현재 {total}일</span></div>",
                unsafe_allow_html=True)
    if nxt:
        st.progress(prog, text=f"다음 단계 {nxt[1]} {nxt[2]}까지 {nxt[0]-total}일")

    my_days = member_days(data, me)
    lv = level_of(my_days)
    mb = blood_of(data["members"][me])
    ch = CHARS[mb]
    nxt_need = LEVELS[lv][0] if lv < len(LEVELS) else None
    st.markdown("#### 🐾 나의 캐릭터")
    st.markdown(
        f"<div class='pet'>{char_img(data, me, False, 130)}<div class='nm'>{ch[1]} <span class='lvbadge'>Lv.{lv} {LEVELS[lv-1][1]}</span></div>"
        f"<div class='ds'><b>{'모름' if mb=='?' else mb+'형'} · {ch[2]}</b><br>{ch[5]}</div></div>", unsafe_allow_html=True)
    if nxt_need:
        st.progress(min(1.0, my_days / nxt_need), text=f"내가 답한 날 {my_days}일 · 다음 레벨까지 {nxt_need-my_days}일")
    else:
        st.caption(f"👑 최고 레벨이에요! 내가 답한 날 {my_days}일")
    st.caption("레벨이 오르면 🌱새싹 → 🧣목도리 → 🌸꽃 → 👑왕관을 얻어요")

    st.markdown("#### 👨‍👩‍👧 우리 가족")
    rows = []
    for mid, m in data["members"].items():
        b = date.fromisoformat(m["birth"])
        nb = next_birthday(b, day)
        rows.append(((nb - day).days, mid, m, b, nb))
    for dd, mid, m, b, nb in sorted(rows, key=lambda r: r[0]):
        dtxt = "🎂 오늘!" if dd == 0 else f"D-{dd}"
        st.markdown(
            f"<div class='mrow'>{char_img(data, mid, False, 50)}<div><div class='n'>{esc(m['name'])}{' (나)' if mid==me else ''}</div>"
            f"<div class='s'>{CHARS[blood_of(m)][1]} Lv.{level_of(member_days(data, mid))} · {b.month}월 {b.day}일생 · 만 {age_of(b, day)}세</div></div><div class='d'>{dtxt}</div></div>",
            unsafe_allow_html=True)

    st.markdown("#### 💌 내일의 질문, 내가 정할래요")
    tomorrow = day + timedelta(days=1)
    k = tomorrow.isoformat()
    cur_q = data["entries"].get(k, {}).get("question")
    if cur_q and cur_q.get("by") in data["members"]:
        st.info(f"내일 질문이 정해져 있어요: “{cur_q['text']}” ({data['members'][cur_q['by']]['name']})")
    txt = st.text_area("질문", key="ask_txt", height=80, placeholder="예) 우리 가족이 같이 가보고 싶은 곳은 어디예요?")
    if st.button("내일의 질문으로 보내기", type="primary", use_container_width=True):
        if len(txt.strip()) < 3:
            st.warning("질문을 적어주세요.")
        else:
            def m(d):
                d["entries"].setdefault(k, {})["question"] = {"text": txt.strip(), "category": "특별 질문", "by": me}
            storage.update(m)
            st.success("전달했어요! 내일 열려요 🎁")
            st.rerun()

    st.markdown("#### 📲 가족 초대")
    url = app_url(data)
    if not url:
        v = st.text_input("앱 주소 (배포한 주소를 한 번만 넣어주세요)", placeholder="https://내앱.streamlit.app")
        if v and st.button("주소 저장"):
            def m(d):
                d["settings"]["app_url"] = v.strip().rstrip("/")
            storage.update(m)
            st.rerun()
    else:
        pin = storage.secret("FAMILY_PIN")
        link = url + (f"/?k={pin}" if pin else "")
        st.caption("이 QR을 찍으면 가족 누구나 바로 들어와요")
        img = make_qr(link)
        if img:
            st.image(img, width=200)
        st.code(link, language=None)


# ───────────────────────── 탭: 설정 ─────────────────────────
def tab_settings(data, me):
    m = data["members"][me]
    st.markdown("#### 내 정보")
    name = st.text_input("호칭 / 이름", m["name"], max_chars=10)
    birth = st.date_input("생년월일", value=date.fromisoformat(m["birth"]), min_value=date(1920, 1, 1),
                          max_value=today(), format="YYYY/MM/DD")
    gl = {"f": "여자", "m": "남자", "n": "선택 안 함"}
    opts = list(gl.values())
    g = st.radio("성별", opts, index=opts.index(gl[m.get("gender", "n")]), horizontal=True)
    cur_b = blood_of(m)
    blood = st.radio("혈액형", BLOOD_OPTS, index=BLOOD_OPTS.index("모름" if cur_b == "?" else cur_b), horizontal=True)
    if st.button("저장", use_container_width=True):
        if any(o["name"] == name.strip() for oid, o in data["members"].items() if oid != me) or not name.strip():
            st.warning("비어 있거나 이미 있는 호칭이에요.")
        else:
            def mu(d):
                d["members"][me].update(name=name.strip(), birth=birth.isoformat(),
                                        gender={"여자": "f", "남자": "m"}.get(g, "n"),
                                        blood="?" if blood == "모름" else blood)
            storage.update(mu)
            st.success("저장했어요")
            st.rerun()
    st.divider()
    url = app_url(data)
    if url:
        pin = storage.secret("FAMILY_PIN")
        mine = f"{url}/?m={me}" + (f"&k={pin}" if pin else "")
        with st.expander("📱 내 전용 QR (내 폰에서 바로 열기)"):
            img = make_qr(mine)
            if img:
                st.image(img, width=200)
            st.code(mine, language=None)
            st.caption("이 주소를 홈 화면에 추가해 두면 앱처럼 열려요.")
    if st.button("다른 사람으로 보기"):
        st.session_state.pop("me", None)
        st.query_params.pop("m", None)
        st.rerun()
    with st.expander("프로필 삭제"):
        if st.checkbox("정말 내 프로필을 삭제할게요 (작성한 대답은 숨겨져요)") and st.button("삭제"):
            def md(d):
                d["members"].pop(me, None)
            storage.update(md)
            st.session_state.pop("me", None)
            st.query_params.pop("m", None)
            st.rerun()
    st.caption("✅ GitHub 저장소에 안전하게 보관 중" if storage.using_github()
               else "⚠️ GitHub 연결 전: 임시 로컬 저장 중이에요 (README의 Secrets 설정 필요)")


# ───────────────────────── 메인 ─────────────────────────
def main():
    big = st.session_state.get("big", False)
    inject_css(big)
    data, me = gate()
    h1, h2 = st.columns([3, 1])
    h1.markdown("### 💌 하루 한 질문")
    st.session_state.big = h2.toggle("큰 글씨", value=big)
    st.caption(f"안녕하세요, {avatar(data['members'][me])} {data['members'][me]['name']}")
    t1, t2, t3, t4, t5 = st.tabs(["오늘", "캘린더", "이야기책", "가족", "설정"])
    with t1:
        tab_today(data, me)
    with t2:
        tab_calendar(data, me)
    with t3:
        tab_book(data, me)
    with t4:
        tab_family(data, me)
    with t5:
        tab_settings(data, me)


main()
