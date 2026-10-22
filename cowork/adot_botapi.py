# -*- coding: utf-8 -*-
"""[2026-10-22 신설] 에이닷비즈뉴스레터를 텔레그램 Bot API 로 **직접** 받는다 (GitHub Actions 우회).

왜 생겼나 — 10-22 ③ 이 adot-telegram-fetch 의 예약 실행 미작동으로 09:52 까지 미수집이었다.
사용자: *"니가 직접 텔레그램 채널에서 가져오면 좋은데 왜 안되는건지 원인 분석하고 … 내가 이 채널의
관리자니까 필요한게 있으면 이야기해줘."*

왜 지금까지 못 가져왔나(10-22 실측) —
  * 채널이 비공개라 t.me/s/<채널> 미리보기(삼성 채널 방식)가 없다.
  * 사람 계정 세션(Telethon, MTProto)은 이 컨테이너에서 TCP 는 붙지만 핸드셰이크가 타임아웃된다(프록시가 HTTPS 외 트래픽 차단).
  * Bot API(api.telegram.org, HTTPS)는 열려 있다 — run.sh send 가 이미 이 길로 발송한다.
  → 방 관리자가 **전용 봇**을 관리자로 넣어 주면, 그 봇이 새 글을 받는다(채널=channel_post, 그룹=message).
  * [10-22 추가] @dailytechbrief 는 공개지만 **그룹**이라(멤버 2명, t.me/s/ 미리보기가 본 페이지로 되돌아감)
    삼성 채널처럼 웹 미리보기로는 못 읽는다. 그룹에서는 봇이 관리자여야 모든 메시지를 받는다(프라이버시 모드 무관).
    (기존 tech_collaboration_team_bot 은 GCP 포워딩이 getUpdates 를 쓰고 있어 같이 쓰면 업데이트를 가로채게 된다 → 새 봇.)

제약 — 봇은 관리자로 들어간 **뒤** 올라온 글만 받고, Telegram 은 미확인 업데이트를 24시간만 보관한다.
       매일 아침 한 번 읽으면 충분하다. 이 스크립트는 업데이트를 확인(offset)하지 않아 같은 날 다시 읽어도 남아 있다.

비밀키(cowork/secrets.env): TELEGRAM_ADOT_BOT_TOKEN=<새 봇 토큰>  (선택) TELEGRAM_ADOT_CHAT_ID=-100…  TELEGRAM_ADOT_CHAT_TITLE=에이닷비즈뉴스레터
출력 — adot-telegram-fetch 의 out/latest.txt 와 같은 형식(DATE/SOURCE/FETCHED_AT/COUNT + =====MSG=====/TIME=HH:MM + 본문).
       그대로 `run.sh adot-latest <파일>` 에 넘긴다(run.sh adot-bot 이 이어서 부른다).
종료코드: 0 오늘자 메시지 있음 / 2 오늘자 없음(파일은 COUNT=0 으로 남김) / 1 토큰 없음·API 오류
"""
import os, sys, json, datetime as dt, urllib.request, urllib.parse

KST = dt.timezone(dt.timedelta(hours=9))


def api(token, method, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in params.items()}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=40) as r:
        o = json.loads(r.read().decode("utf-8"))
    if not o.get("ok"):
        raise RuntimeError(o.get("description", "api error"))
    return o["result"]


def render(text, entities):
    """Bot API text+entities -> 마크다운(**굵게**, [제목](url)). offset/length 는 UTF-16 단위."""
    u16 = text.encode("utf-16-le")
    def seg(a, b): return u16[2 * a:2 * b].decode("utf-16-le", "ignore")
    marks = []  # (pos, order, string)
    for e in entities or []:
        t, a, b = e.get("type"), e["offset"], e["offset"] + e["length"]
        if t == "bold":
            marks.append((a, 1, "**")); marks.append((b, 0, "**"))
        elif t == "text_link" and e.get("url"):
            marks.append((a, 1, "[")); marks.append((b, 0, f"]({e['url']})"))
    out, last = [], 0
    for pos, _, s in sorted(marks, key=lambda x: (x[0], x[1])):
        out.append(seg(last, pos)); out.append(s); last = pos
    out.append(seg(last, len(u16) // 2))
    return "".join(out)


def main():
    token = os.environ.get("TELEGRAM_ADOT_BOT_TOKEN", "").strip()
    if not token:
        print("[adot-bot] TELEGRAM_ADOT_BOT_TOKEN 이 없습니다 — 채널 관리자가 전용 봇을 만들어 채널 관리자로 추가하고 토큰을 secrets.env 에 넣어야 합니다")
        sys.exit(1)
    want_id = os.environ.get("TELEGRAM_ADOT_CHAT_ID", "").strip()
    want_title = os.environ.get("TELEGRAM_ADOT_CHAT_TITLE", "에이닷비즈뉴스레터").strip()
    username = os.environ.get("TELEGRAM_ADOT_USERNAME", "dailytechbrief").strip().lstrip("@")
    if not want_id and username:
        # [10-22] 방이 공개(@dailytechbrief)라 아이디를 봇 API 로 바로 알 수 있다.
        try:
            chat = api(token, "getChat", chat_id="@" + username)
            want_id = str(chat["id"])
            print(f'[adot-bot] @{username} -> {chat.get("type")} "{chat.get("title")}" ({want_id})')
        except Exception as e:
            print(f"[adot-bot] getChat(@{username}) 실패: {e} — 제목으로 고른다")
    out_path = sys.argv[1] if len(sys.argv) > 1 else "adot-botapi.txt"
    today = dt.datetime.now(KST).date()
    try:
        # 채널이면 channel_post, 그룹(슈퍼그룹)이면 message 로 온다. 둘 다 받는다.
        ups = api(token, "getUpdates", limit=100, timeout=0, allowed_updates=["channel_post", "message"])
    except Exception as e:
        print("[adot-bot] API 오류:", e); sys.exit(1)
    msgs, titles = [], set()
    for u in ups:
        m = u.get("channel_post") or u.get("message")
        if not m:
            continue
        chat = m.get("chat", {})
        if chat.get("type") not in ("channel", "supergroup", "group"):
            continue
        titles.add(f'{chat.get("title")}({chat.get("id")})')
        if want_id and str(chat.get("id")) != want_id:
            continue
        if not want_id and want_title and want_title not in (chat.get("title") or ""):
            continue
        when = dt.datetime.fromtimestamp(m["date"], KST)
        if when.date() != today:
            continue
        text = m.get("text") or m.get("caption") or ""
        msgs.append((when, render(text, m.get("entities") or m.get("caption_entities"))))
    msgs.sort()
    now = dt.datetime.now(KST).isoformat(timespec="seconds")
    lines = [f"DATE={today.isoformat()}", "SOURCE=telegram-bot-api(channel_post)", f"FETCHED_AT={now}", f"COUNT={len(msgs)}"]
    if msgs:
        for when, body in msgs:
            lines += ["=====MSG=====", f"TIME={when.strftime('%H:%M')}", body]
    else:
        lines += ["=====MSG=====", "NO_MESSAGES"]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[adot-bot] 업데이트 {len(ups)}건 / 채널 {sorted(titles) or '없음'} / 오늘자 메시지 {len(msgs)}건 -> {out_path}")
    if not msgs:
        print("[adot-bot] 오늘자 글이 아직 없습니다(또는 봇이 채널 관리자가 아니어서 channel_post 를 못 받습니다)")
        sys.exit(2)


if __name__ == "__main__":
    main()
