# -*- coding: utf-8 -*-
"""[2026-10-26 신설] 에이닷비즈뉴스레터를 t.me 메시지 임베드 페이지로 읽는다 (봇이 올린 글 대응).

왜 생겼나 — 10-26 ③ 에서 09:30 에 올라온 뉴스레터 2건을 Bot API 가 한 건도 못 받았다.
  뉴스레터 발신자가 **봇(@adotbiz_news_bot)** 이고, Telegram Bot API 는 다른 봇이 보낸 메시지를
  어떤 봇에게도 업데이트로 주지 않는다(관리자 권한·프라이버시 모드와 무관한 플랫폼 규칙).
  그래서 getUpdates 에는 사람이 친 /info·/start 만 잡혔고, ③ 이 '미수집'으로 오판했다.
  사용자: *"에이닷뉴스레터방에 가면 9시30분에 메세지 2개 올라와있는데!!"* → *"에이닷뉴스 읽지 못하는 문제 해결해"*

어떻게 읽나 — @dailytechbrief 는 공개 슈퍼그룹이라 t.me/s/ 목록 미리보기는 없지만, **메시지 하나짜리
  임베드 페이지** `https://t.me/dailytechbrief/<id>?embed=1&mode=tme` 는 열린다(10-26 실측: 9·10번 = 뉴스레터,
  본문·링크·굵게·<time datetime> 전부 들어 있음, 없는 번호는 시각 없는 빈 껍데기 페이지).
  메시지 번호는 그룹 안에서 1씩 오르므로, 기준 번호(anchor)에서 앞뒤로 훑어 오늘자 글을 모은다.

기준 번호 —
  1) 오늘 Bot API 로 받은 이 방 메시지의 최대 message_id
  2) 지난 실행이 남긴 cowork/raw/adot-lastid.txt (저장소에 커밋됨)
  3) 둘 다 없으면 1 에서 두 배씩 올려(1,2,4,8,…) 존재하는 마지막 근처를 찾는다
  앞으로는 '없는 번호' 가 MISS_FWD 번 연속될 때까지, 뒤로는 어제 이전 글을 만나거나 MISS_BACK 번 연속 빈 번호일 때까지.

거르는 것 — 본문 없는 글(스티커·서비스 메시지), '/'로 시작하는 봇 명령(/info, /start@getidsbot).

출력 — adot_botapi.py 가 쓰는 msgs 와 같은 (when_kst, markdown_body, message_id) 목록.
       단독 실행:  python3 cowork/adot_embed.py [--anchor N] [--date YYYY-MM-DD]   (찾은 글 요약만 찍는다)
"""
import os, re, sys, html, json, datetime as dt, urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MISS_FWD = 8      # 삭제된 번호가 섞여도 넘어갈 만큼
MISS_BACK = 15
MAX_SCAN = 400    # 한 번 실행에서 여는 페이지 상한(안전장치)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "cowork", "raw", "adot-lastid.txt")


def fetch(username, mid, timeout=15):
    url = f"https://t.me/{username}/{mid}?embed=1&mode=tme"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def parse(page):
    """임베드 페이지 -> None(없는 번호) 또는 dict(when, text_md, author, has_text)."""
    t = re.search(r'<time datetime="([^"]+)"', page)
    if not t:
        return None
    when = dt.datetime.fromisoformat(t.group(1)).astimezone(KST)
    a = re.search(r'tgme_widget_message_author_name"[^>]*href="https://t\.me/([^"]+)"', page)
    m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page, re.S)
    md = ""
    if m:
        b = m.group(1)
        b = re.sub(r"<br\s*/?>", "\n", b)
        b = re.sub(r"<b>(.*?)</b>", r"**\1**", b, flags=re.S)
        b = re.sub(r"<strong>(.*?)</strong>", r"**\1**", b, flags=re.S)
        b = re.sub(r'<a href="([^"]+)"[^>]*>(.*?)</a>',
                   lambda x: "[" + re.sub(r"<[^>]+>", "", x.group(2)) + "](" + html.unescape(x.group(1)) + ")", b, flags=re.S)
        md = html.unescape(re.sub(r"<[^>]+>", "", b)).strip()
    return {"when": when, "text": md, "author": a.group(1) if a else ""}


def _get(username, mid, cache):
    if mid < 1:
        return None
    if mid not in cache:
        try:
            cache[mid] = parse(fetch(username, mid))
        except Exception as e:
            print(f"[adot-embed] {mid}번 받기 실패: {e}")
            cache[mid] = None
    return cache[mid]


def read_state():
    try:
        return int(open(STATE, encoding="utf-8").read().split()[0])
    except Exception:
        return 0


def write_state(mid):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            f.write(f"{mid}\n")
    except Exception as e:
        print(f"[adot-embed] 기준 번호 저장 실패: {e}")


def gallop(username, cache):
    """기준 번호가 전혀 없을 때: 1,2,4,8,… 로 올려 존재하는 가장 큰 번호 근처를 찾는다."""
    hi, last = 1, 0
    while hi <= 1 << 20 and len(cache) < MAX_SCAN:
        # 빈 번호 하나로 멈추지 않도록 hi..hi+3 중 하나라도 있으면 존재로 본다
        if any(_get(username, hi + k, cache) for k in range(4)):
            last = hi; hi *= 2
        else:
            break
    return last


def collect(username, day, anchor=0):
    """day(KST date) 의 글을 모은다. 반환: (msgs[(when, md, mid)], max_seen_id, opened_pages)."""
    cache = {}
    base = max(anchor, read_state())
    if base <= 0:
        base = gallop(username, cache)
        print(f"[adot-embed] 기준 번호 없음 -> 탐색으로 {base}번 근처에서 시작")
    found, max_seen = {}, 0
    # 앞으로
    mid, miss = base, 0
    while miss < MISS_FWD and len(cache) < MAX_SCAN:
        p = _get(username, mid, cache)
        if p is None:
            miss += 1
        else:
            miss = 0; max_seen = max(max_seen, mid)
            if p["when"].date() == day:
                found[mid] = p
        mid += 1
    # 뒤로 (기준 번호 이전에 올라온 오늘자 글: 뉴스레터 뒤에 사람이 글을 쓴 경우 등)
    mid, miss = base - 1, 0
    while mid >= 1 and miss < MISS_BACK and len(cache) < MAX_SCAN:
        p = _get(username, mid, cache)
        if p is None:
            miss += 1
        else:
            miss = 0; max_seen = max(max_seen, mid)
            if p["when"].date() < day:
                break
            if p["when"].date() == day:
                found[mid] = p
        mid -= 1
    msgs = []
    for mid in sorted(found):
        p = found[mid]
        body = p["text"]
        if not body:
            continue                      # 스티커·사진만·서비스 메시지
        if body.lstrip().startswith("/"):
            continue                      # 봇 명령(/info, /start@getidsbot)
        msgs.append((p["when"], body, mid))
    if max_seen:
        write_state(max_seen)
    return msgs, max_seen, len(cache)


def main():
    args = sys.argv[1:]
    anchor, day = 0, dt.datetime.now(KST).date()
    if "--anchor" in args:
        anchor = int(args[args.index("--anchor") + 1])
    if "--date" in args:
        day = dt.date.fromisoformat(args[args.index("--date") + 1])
    user = os.environ.get("TELEGRAM_ADOT_USERNAME", "dailytechbrief").strip().lstrip("@")
    msgs, mx, n = collect(user, day, anchor)
    print(f"[adot-embed] @{user} {day} / 페이지 {n}개 열람 / 최대 번호 {mx} / 오늘자 글 {len(msgs)}건")
    for when, body, mid in msgs:
        print(f"  #{mid} {when:%H:%M} 링크 {body.count('](')}건 | {body[:60]!r}")


if __name__ == "__main__":
    main()
