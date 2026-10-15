#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
out/brief-YYYY-MM-DD.md 를 텔레그램 채널로 발송한다.

왜 HTML parse_mode 인가
  브리프 원문은 텔레그램 '앱에 붙여넣기'용으로 만들어져 있다(앱이 마크다운을
  자체 해석한다). 봇 API 는 그 해석을 안 해주므로 서식을 직접 지정해야 한다.
  MarkdownV2 는 . - ( ) ! + = | 등을 전부 이스케이프해야 해서 본문 문장이
  깨지기 쉽다. HTML 은 & < > 세 글자만 처리하면 되므로 이쪽을 쓴다.
  결과 화면은 지금 손으로 붙여넣을 때와 동일하다.

분할
  텔레그램 한 메시지 상한은 4096자다. 카테고리(■) 경계에서만 자르고,
  한 덩어리가 상한을 넘으면 기사 경계('ㅤ')에서 다시 자른다.
  기사 하나가 잘려서 두 메시지에 걸치는 일은 없다.

길이를 어떻게 세는가  ★2026-09-04 전면 수정★
  텔레그램의 4096 은 **entities 파싱 후의 표시 텍스트** 길이다
  (Bot API 원문: "1-4096 characters after entities parsing").
  따라서 아래는 **전혀 세지 않는다**:
    - <b> </b> <a href="..."> </a> 같은 서식 태그
    - **URL 자체** (링크 entity 의 url 속성으로 빠진다)
  세는 것은 화면에 보이는 글자뿐이고, 단위는 **UTF-16 코드유닛**이다.
  한글·한자는 1, 이모지(📌 U+1F4CC)는 서로게이트 쌍이라 **2** 로 센다.

  [왜 이 주석이 필요한가 - 2026-09-04 사고 기록]
  09-01 에 "텔레그램이 세는 것은 HTML 변환 후 길이"라고 잘못 진단하고
  LIMIT 을 HTML 문자열 길이에 걸었다. 실제보다 약 1.6배 빡빡한 기준이라
  09-02~09-04 사흘간 '고'로 판정한 기사를 분량 때문에 잘라냈다.
  실측 09-04: 사람이 실제로 보낸 18건은 HTML 4,986자였지만 파싱 후는
  3,391자로 한 메시지에 들어갔다. 같은 날 내 14건은 2,492자로 예산의
  61%만 쓰고도 "메시지 2개가 된다"는 잘못된 경고 때문에 4건을 뺐다.
  그날 놓친 정답지 5건 중 4건이 이 잘못된 컷에서 나왔다.
  **len(html) 로 재지 말 것. tg_len() 을 쓸 것.**

팬아웃(타깃 방 복제)  ★2026-10-07 신설★
  GCP 의 포워딩 봇(tech_collaboration_team_bot)은 'DTB 업로드' 방에 올라온
  글을 타깃 방들로 copy_message 한다. 그런데 **텔레그램 봇은 다른 봇(및 자기
  자신)이 보낸 메시지를 업데이트로 받지 못한다**(Bot FAQ: 봇끼리 무한 루프
  방지를 위해 privacy mode 와 무관하게 차단). 그래서 이 스크립트가 올린
  브리프는 포워딩 봇 눈에 보이지 않고, 사람이 붙여넣은 글만 전달된다.
  해결: **발송한 쪽이 직접** sendMessage 응답의 message_id 로 copyMessage 를
  타깃 방마다 호출한다. 결과물은 포워딩 봇이 했을 때와 동일하다(원본 1건 +
  타깃마다 복제본). 사람이 손으로 올리는 경우는 GCP 봇이 계속 처리하므로
  두 경로가 공존한다.
    - 발송 대상 chat 이 TELEGRAM_FANOUT_SOURCE_CHAT_ID(기본 DTB 업로드)와
      같을 때만 팬아웃한다. 초안 방 등 다른 방으로 보낼 때는 안 한다.
    - 타깃 목록은 secrets.env 의 TELEGRAM_FANOUT_CHAT_IDS(쉼표 구분).
      비어 있으면 아래 FANOUT_DEFAULT_TARGETS 를 쓴다.
    - 전제: 이 봇이 타깃 방들의 멤버여야 copyMessage 가 된다.
    - 복제 실패는 경고만 찍고 계속 간다(원본은 이미 올라갔으므로 exit 1 로
      끊으면 run.sh 가 '발송 실패'로 오판한다).
    - --no-fanout 으로 끌 수 있다.

사용법
  python3 cowork/send_telegram.py --date 2026-08-12            # 실제 발송
  python3 cowork/send_telegram.py --date 2026-08-12 --dry-run  # 미리보기만
  python3 cowork/send_telegram.py --date 2026-08-12 --to final # 운영 채널로
  python3 cowork/send_telegram.py --date 2026-08-12 --no-fanout # 타깃 복제 생략
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# [2026-09-01] 3900 -> 4080.
#   종전 여유분 196자는 '[초안 …] 검토 후 발송하세요' 헤더 몫이었다.
#   그 헤더를 없앴으므로(아래 main 참조) 여유분도 돌려받는다. 남기는 16자는
#   메시지가 2개 이상일 때만 붙는 '(i/N)' 꼬리표 몫이다.
# [2026-09-04] 이 값의 **적용 대상**이 바뀌었다. 종전에는 HTML 문자열
#   길이에 걸었는데, 이제 tg_len()(entities 파싱 후 UTF-16 코드유닛)에 건다.
LIMIT = 4080          # 텔레그램 상한 4096. 남는 16자는 '(i/N)' 꼬리표.
API = "https://api.telegram.org/bot%s/sendMessage"
API_COPY = "https://api.telegram.org/bot%s/copyMessage"

# [2026-10-07] 팬아웃 기본값. GCP 포워딩 봇의 SOURCE / TARGETS 와 같은 값이다.
#   secrets.env 의 TELEGRAM_FANOUT_SOURCE_CHAT_ID / TELEGRAM_FANOUT_CHAT_IDS 가
#   있으면 그쪽이 우선한다. 방을 바꿀 때는 **GCP 봇 코드와 함께** 고칠 것.
FANOUT_DEFAULT_SOURCE = "-1004362418313"                  # DTB 업로드
FANOUT_DEFAULT_TARGETS = ["-1004470331681",               # DTB 동작확인
                          "-1004473772544"]               # Daily Tech Brief 공유

# HTML 태그를 걷어내 '표시 텍스트'만 남기는 패턴
_TAG_RE = re.compile(r"<a\s+href=\"[^\"]*\">|</a>|</?b>|</?i>|</?u>|</?s>|"
                     r"</?code>|</?pre>|</?tg-spoiler>|"
                     r"<span\s+class=\"[^\"]*\">|</span>")


def tg_len(html_text):
    """텔레그램이 실제로 세는 길이.

    = entities 파싱 후 표시 텍스트의 **UTF-16 코드유닛** 수.
      - 서식 태그와 URL 은 제외된다(링크는 '원문보기' 4자만 남는다).
      - &amp; &lt; &gt; 같은 엔티티는 1자로 되돌려 센다.
      - 이모지(📌)는 서로게이트 쌍이라 2로 센다.
    """
    s = _TAG_RE.sub("", html_text)
    s = html.unescape(s)
    return len(s.encode("utf-16-le")) // 2


def load_secrets(base):
    """cowork/secrets.env 를 읽는다. run.sh 를 안 거쳐도 동작하도록."""
    env = {}
    path = os.path.join(base, "cowork", "secrets.env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def md_to_html(text):
    """브리프 마크다운 -> 텔레그램 HTML.

    순서가 중요하다. 링크를 먼저 자리표시자로 빼놓지 않으면
    html.escape 가 URL 안의 &(쿼리 구분자)까지 건드려 링크가 깨진다.
    """
    links = []

    def stash(m):
        links.append((m.group(1), m.group(2)))
        return "\x00LINK%d\x00" % (len(links) - 1)

    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", stash, text)
    text = html.escape(text, quote=False)

    # **굵게** -> <b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)

    # '1\.' 의 백슬래시는 앱의 순서목록 해석을 막으려던 것.
    # HTML 모드에서는 목록 해석이 없으므로 백슬래시를 뗀다.
    text = text.replace("\\.", ".")

    for i, (label, url) in enumerate(links):
        tag = '<a href="%s">%s</a>' % (html.escape(url, quote=True),
                                       html.escape(label, quote=False))
        text = text.replace("\x00LINK%d\x00" % i, tag)
    return text


def split_blocks(body):
    """카테고리 -> (넘치면) 기사 단위로 쪼개 LIMIT 이하 덩어리 목록을 만든다.

    [2026-09-04] 길이 판정을 len() -> tg_len() 으로 바꿨다. 종전에는 HTML
    문자열 길이로 재서 URL·태그까지 예산에 넣었고, 그 탓에 안 쪼개도 될
    브리프를 쪼갠다고 알렸다(주석 상단 사고 기록 참조).
    """
    cats = re.split(r"\n(?=<b>■ )", body)
    out, buf = [], ""
    for cat in cats:
        cand = (buf + "\n" + cat) if buf else cat
        if tg_len(cand) <= LIMIT:
            buf = cand
            continue
        if buf:
            out.append(buf)
            buf = ""
        if tg_len(cat) <= LIMIT:
            buf = cat
            continue
        # 카테고리 하나가 통째로 넘친다 -> 기사 경계에서 자른다
        arts = cat.split("\nㅤ\n")
        sub = ""
        for a in arts:
            c2 = (sub + "\nㅤ\n" + a) if sub else a
            if tg_len(c2) <= LIMIT:
                sub = c2
            else:
                if sub:
                    out.append(sub)
                sub = a
        buf = sub
    if buf:
        out.append(buf)
    return out


def _post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def send(token, chat_id, text):
    return _post(API % token, {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def copy_message(token, to_chat, from_chat, message_id):
    """원본 방의 메시지를 타깃 방에 복제한다(서식·링크 그대로, '전달됨' 표시 없음).

    GCP 포워딩 봇이 쓰는 copy_message 와 같은 API 다. 봇이 타깃 방 멤버여야 한다.
    """
    return _post(API_COPY % token, {
        "chat_id": to_chat,
        "from_chat_id": from_chat,
        "message_id": message_id,
    })


def fanout_targets(chat):
    """이 chat 으로 보낼 때 복제해야 할 타깃 목록. 해당 없으면 빈 리스트.

    [2026-10-07] 발송 대상이 포워딩 SOURCE(DTB 업로드)일 때만 팬아웃한다.
    초안 방으로 보내는 --to draft 는 SOURCE 가 아니므로 자동으로 제외된다.
    """
    source = os.environ.get("TELEGRAM_FANOUT_SOURCE_CHAT_ID",
                            FANOUT_DEFAULT_SOURCE).strip()
    if str(chat).strip() != source:
        return []
    raw = os.environ.get("TELEGRAM_FANOUT_CHAT_IDS", "").strip()
    targets = [t.strip() for t in raw.split(",") if t.strip()] \
        if raw else list(FANOUT_DEFAULT_TARGETS)
    # 원본 방으로 다시 복제하는 실수 방지
    return [t for t in targets if t != source]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--to", choices=["draft", "final"], default="draft")
    ap.add_argument("--dry-run", action="store_true")
    # [2026-10-07] 팬아웃 끄기. 테스트 발송 때 타깃 방까지 퍼지는 걸 막는 용도.
    ap.add_argument("--no-fanout", action="store_true",
                    help="원본 방에만 보내고 타깃 방 복제(copyMessage)를 생략")
    # [2026-09-01 사용자 지시] 텔레그램은 **최종 20건만** 보낸다.
    #   종전에는 30건 초안(brief-<날짜>.md)을 보냈다. 30건은 검토용 여유분이라
    #   메시지가 3개로 쪼개졌고, 실제로 사람이 발송하는 건 15건 안팎이었다.
    #   30건을 보내려면 --stem brief 로 명시한다.
    # [2026-09-01 재개정] 30건 초안 / 20건 발송본 두 단계를 폐지했다.
    #   이제 brief-<날짜>.md 하나가 곧 발송본이다.
    ap.add_argument("--stem", default="brief",
                    help="기본 brief(발송본). brief20 은 폐지됨")
    a = ap.parse_args()

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_secrets(base)

    path = os.path.join(base, "out", "%s-%s.md" % (a.stem, a.date))
    if not os.path.exists(path):
        print("[오류] %s 없음. 브리프를 먼저 만드세요." % path)
        sys.exit(1)
    raw = open(path, encoding="utf-8").read().strip()

    n_articles = len(re.findall(r"^\*\*\d+\\\.", raw, re.M))
    body = md_to_html(raw)
    chunks = split_blocks(body)

    # [2026-09-04 전면 개정] 09-01 안내가 틀렸다.
    #   그때 "텔레그램이 세는 것은 HTML 변환 후 길이"라고 적었는데 오진이다.
    #   Bot API 는 "4096 characters **after entities parsing**" 이므로
    #   서식 태그와 URL 은 예산에 들어가지 않는다. 이제 tg_len() 으로 잰다.
    #   실측 09-04: 사람 발송본 18건 = HTML 4,986 / 파싱 후 3,391(1개).
    #                내 발송본 14건 = HTML 3,874 / 파싱 후 2,492(예산의 61%).
    tg = sum(tg_len(c) for c in chunks)
    html_len = sum(len(c) for c in chunks)
    per = tg / max(n_articles, 1)
    print("[길이] 텔레그램 %d자 / %d (여유 %d자, 약 %.1f건분) "
          "| 기사 %d건 / 메시지 %d개"
          % (tg, LIMIT, LIMIT - tg, (LIMIT - tg) / max(per, 1),
             n_articles, len(chunks)))
    print("[길이] 참고: 원문(마크다운) %d자 / HTML %d자 — **둘 다 판단 기준이 아니다**"
          % (len(raw), html_len))
    if len(chunks) > 1:
        over = tg - LIMIT
        print("[길이] 상한 초과 %+d자. 건당 평균 %.0f자이므로 "
              "**기사 %d건을 빼면** 1개가 된다."
              % (over, per, -(-over // max(int(per), 1))))
        print("[길이] 건수를 줄이기 전에 **본문을 먼저 압축**한다"
              " (2026-09-04 사용자 지시: 메시지 1개 유지 + 건수 우선).")
    elif LIMIT - tg > per:
        # [2026-09-09 임원 피드백 개정] 여백을 건수로 채우지 않는다.
        #   원문: '뉴스 숫자가 너무 많으면 읽기 힘들다 (…) 15개 내외로 하고
        #          주요 내용과 시사점을 일부 보강해야 함.'
        #   종전 문구('더 실을 것')는 09-04 지시였으나 이번에 폐기됐다.
        print("[길이] 여유 %.1f건분. **건수를 늘리지 말고 상위 건의 주요 내용·"
              "시사점을 보강할 것** (2026-09-09 임원 피드백)."
              % ((LIMIT - tg) / max(per, 1)))

    # [2026-09-10 사용자 스킬 개정] 건수 가드. 목표 15~20건, 20건 초과 금지.
    #   (종전 13~16건/최대 18건에서 상향)
    #   **분량과 교환 관계다.** 실측 09-09: 건당 고정비(헤드라인+매체+
    #   원문보기+라벨+구분줄)가 81자라, 20건을 한 메시지에 넣으려면 본문
    #   예산이 건당 123자뿐이다(= 주요 내용 70 + 시사점 50 이 상한).
    #     18건 x (81 + 주80 + 시65) = 4,075  <- 겨우 들어감
    #     20건 x (81 + 주80 + 시65) = 4,528  <- 초과
    #     20건 x (81 + 주70 + 시50) = 4,028  <- 들어감
    #   건수를 채우려고 본문을 깎을지, 본문을 지키고 건수를 줄일지는
    #   **분량이 우선**이다(메시지 1개 규칙이 상위 제약).
    if n_articles > 20:
        print("[건수] **%d건은 많습니다. 목표는 15~20건(최대 20건)입니다.**"
              " rank 하위 건을 빼세요. (2026-09-10 스킬 개정)" % n_articles)
    elif n_articles < 15:
        print("[건수] %d건. 15~20건이 목표입니다. '고' 판정이 적은 날이면"
              " 그대로 두되, 순위에서 밀린 '고'가 있는지 확인하세요." % n_articles)
    else:
        print("[건수] %d건 (목표 15~20건)" % n_articles)
    # [2026-09-28 사용자 결정] 09-10 '건수보다 분량 우선'을 뒤집었다.
    #   원문: "넣을 만한 기사가 있으면 빼지 않고 기사별 분량을 줄이는게 맞는것 같아."
    #   19건 이상에서 건당 본문이 얇은 것은 이제 의도된 상태다 - 참고로만 알린다.
    if n_articles >= 19 and per < 226:
        print("[건수] %d건에 건당 %d자 - 기사를 살리려 본문을 압축한 상태(09-28 결정상 정상)."
              " 여유가 생기면 상위 건부터 본문을 보강하세요." % (n_articles, per))
    if n_articles and per < 200:
        # [2026-09-09 재정정] 글자 수 자체가 목표가 아니다. 사용자 원문:
        #   '시사점을 단순히 글자수를 늘리는 게 중요한 게 아니고 경영층에게
        #    의미 있는 인사이트를 주는 데 노력하라는 의미였다.'
        #   -> 보강 대상은 **주요 내용(사실)** 이 먼저다. 시사점은 짧아도
        #      판단이 있으면 그대로 둔다.
        print("[본문] 건당 평균 %.0f자. 15건 기준 예산은 250~270자입니다."
              " **주요 내용(90자 안팎)에 헤드라인에 없는 사실을 더 담으세요.**"
              % per)
        print("[본문] 시사점은 40자 안팎이면 충분합니다. 글자 수를 늘리지 말고"
              " `run.sh insight` 로 동어반복 여부를 확인하세요.")

    # [2026-09-01 사용자 지시] 초안 채널 안내 문구를 붙이지 않는다.
    #   '[초안 …] 기계 선별 N건 — 검토 후 발송하세요' 헤더가 약 45자를 먹어
    #   메시지 1개 상한을 넘기는 데 기여했다. 초안/운영 채널 구분은
    #   TELEGRAM_DRAFT_CHAT_ID 로 이미 되므로 문구가 없어도 헷갈리지 않는다.
    for i, c in enumerate(chunks):
        if len(chunks) > 1:
            chunks[i] = c + ("\n<i>(%d/%d)</i>" % (i + 1, len(chunks)))

    key = "TELEGRAM_DRAFT_CHAT_ID" if a.to == "draft" else "TELEGRAM_FINAL_CHAT_ID"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get(key, "").strip()
    targets = [] if a.no_fanout else fanout_targets(chat)

    if a.dry_run:
        print("[dry-run] 기사 %d건 / 메시지 %d개" % (n_articles, len(chunks)))
        if chat:
            print("[dry-run] 발송 대상 %s=%s | 팬아웃 %s"
                  % (key, chat,
                     ("-> " + ", ".join(targets)) if targets else "없음"))
        for i, c in enumerate(chunks, 1):
            print("--- %d번째 메시지 (%d자) ---" % (i, len(c)))
            print(c[:600])
            print("   ...(생략)" if len(c) > 600 else "")
        return

    if not token or not chat:
        print("[오류] cowork/secrets.env 에 TELEGRAM_BOT_TOKEN 과 %s 가 필요합니다." % key)
        sys.exit(1)

    sent_ids = []
    for i, c in enumerate(chunks, 1):
        try:
            r = send(token, chat, c)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            print("[실패] %d/%d HTTP %s %s" % (i, len(chunks), e.code, detail))
            sys.exit(1)
        if not r.get("ok"):
            print("[실패] %d/%d %s" % (i, len(chunks), r))
            sys.exit(1)
        mid = r["result"]["message_id"]
        sent_ids.append(mid)
        print("[발송] %d/%d ok (message_id=%s)" % (i, len(chunks), mid))
        time.sleep(1.2)   # 채널 flood limit 회피

    # [2026-10-07] 타깃 방 복제. 원본이 전부 올라간 뒤, 타깃별로 메시지 순서를
    #   지켜 copyMessage 한다. 실패해도 중단하지 않는다(원본 발송은 이미 성공).
    n_fail = 0
    for t in targets:
        for i, mid in enumerate(sent_ids, 1):
            try:
                r = copy_message(token, t, chat, mid)
                ok, why = r.get("ok"), r.get("description")
            except urllib.error.HTTPError as e:
                ok, why = False, "HTTP %s %s" % (
                    e.code, e.read().decode("utf-8", "replace")[:200])
            except Exception as e:  # 네트워크 등
                ok, why = False, repr(e)
            if ok:
                print("[복제] %d/%d -> %s ok" % (i, len(sent_ids), t))
            else:
                n_fail += 1
                print("[복제 실패] %d/%d -> %s: %s" % (i, len(sent_ids), t, why))
                w = str(why or "").lower()
                if "not a member" in w or "chat not found" in w \
                        or "kicked" in w:
                    print("          봇이 %s 방의 멤버가 아닌 것 같습니다. 초대하세요." % t)
            time.sleep(0.6)   # 타깃 방마다 flood limit 회피

    tail = "" if not targets else " / 복제 %d방%s" % (
        len(targets), (" (실패 %d건)" % n_fail) if n_fail else "")
    print("[완료] %s / 기사 %d건 / 메시지 %d개 -> %s%s"
          % (a.date, n_articles, len(chunks), a.to, tail))


if __name__ == "__main__":
    main()
