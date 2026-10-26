# -*- coding: utf-8 -*-
"""[2026-10-26 신설] 삼성 글로벌 AI/SW 뉴스 텔레그램(t.me/s/Samsung_Global_AI_SW) — 다이제스트 + 개별 뉴스 메시지.

왜 생겼나 — ② 는 매일 아침 다이제스트(`[삼성 이영진] 글로벌 AI/SW 뉴스 🤖 (26/M/D)`)의 ■ 항목만 썼다.
  사용자: *"다이제스트 메세지만 가져오는데, 다른 메세지에도 뉴스들이 있긴 하거든. 메세지 하나당 1개의 뉴스 내용이 있는데,
  중복일 순 있는데 이것도 같이 가져오면 좋을것 같은데 … 뉴스 링크는 니가 찾아야해"*
  실측(10-07~10-26, 20일): 개별 메시지는 하루 3~13건, 그중 다이제스트 ■ 와 겹치는 것은 일부(10-22: Claude Duet·오픈AI 구글클라우드 등)이고
  나머지는 다이제스트에 없는 소식(네오클라우드 Firmus 투자, DeepInfer 공개, Gemini 4 Argon 정식 출시, 네비우스 대출 …)이다.
  다이제스트가 안 올라온 날(10-23·10-26 아침 등)에도 개별 메시지는 있다.

무엇을 하나 —
  1) 웹 미리보기를 ?before=<번호> 로 거슬러 올라가며 받는다(한 쪽 20건 안팎, 최대 MAX_PAGES 쪽).
  2) 수집 창 = (직전 다이제스트 게시 시각, 지금]. 직전 다이제스트 = 괄호 날짜가 오늘보다 앞선 가장 최근 다이제스트.
     어제 ② 가 이미 쓴 구간을 다시 쓰지 않으려는 것이다. 직전 다이제스트를 못 찾으면 지금부터 30시간.
  3) 오늘 날짜 다이제스트가 있으면 ■ 항목을 뽑는다(종전 ② 와 같은 대상).
  4) 개별 메시지 = 다이제스트가 아니고 본문이 있는 글. 정리:
       - 미리보기 사본: 같은 제목의 긴 글이 따로 있으면 짧은 사본(250자 안팎, '  : ' 요약형)은 버린다.
       - 같은 제목 반복은 하나만. '[삼성 이영진] … 실적 요약' 같은 비(非)다이제스트 글도 개별로 둔다(판정은 ④ 가 한다).
       - 다이제스트 ■ 항목과 낱말이 많이 겹치면(겹침 비율 ≥ DUP_RATIO) '다이제스트 중복' 으로 표시만 하고 목록에서는 뺀다.
  5) 개별 메시지 본문 안의 원천 링크(x.com·공식 블로그·깃허브 등, t.me 제외)를 함께 적어 링크 찾기에 쓴다.
     **이 링크는 기사 링크가 아니다.** 슬랙 게시에는 검증한 기사 링크만 쓴다(x.com 은 로그인 매체라 채택 금지,
     공식 블로그·뉴스룸은 로그인 없이 열리면 해외 원문으로 채택 가능).

출력 — cowork/raw/samsung-tme-<날짜>.md (사람이 읽는 목록) + .json
종료코드 — 0 다이제스트 또는 개별 메시지가 있음 / 2 둘 다 없음 / 1 받기 실패
사용법 — bash cowork/run.sh samsung-tme [--date YYYY-MM-DD]
"""
import os, re, sys, json, html, datetime as dt, urllib.request

KST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CHANNEL = os.environ.get("SAMSUNG_TG_CHANNEL", "Samsung_Global_AI_SW")
MAX_PAGES = 8
DUP_RATIO = 0.5
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RE_DIGEST = re.compile(r"글로벌\s*AI/SW\s*뉴스.*\((\d{2})/(\d{1,2})/(\d{1,2})\)")


def fetch(before=None):
    url = f"https://t.me/s/{CHANNEL}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def parse_page(page):
    out = []
    for b in page.split("tgme_widget_message_wrap")[1:]:
        pid = re.search(r'data-post="[^/"]+/(\d+)"', b)
        t = re.search(r'<time datetime="([^"]+)"', b)
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        if not (pid and t):
            continue
        raw = m.group(1) if m else ""
        links = [html.unescape(u) for u in re.findall(r'<a href="([^"]+)"', raw)]
        txt = re.sub(r"<br\s*/?>", "\n", raw)
        txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
        out.append(dict(id=int(pid.group(1)), when=dt.datetime.fromisoformat(t.group(1)).astimezone(KST),
                        text=txt, links=links))
    return out


def digest_date(text):
    m = RE_DIGEST.search(text.split("\n", 1)[0])
    if not m:
        return None
    try:
        return dt.date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def title_of(text):
    first = text.split("\n", 1)[0].strip()
    # 미리보기 사본은 '제목  : 요약…' 한 줄로 온다
    first = re.split(r"\s{2,}|\s:\s", first, maxsplit=1)[0].strip()
    first = re.sub(r"https?://\S+", "", first).strip()
    return first[:120]


# 겹침 계산에서 빼는 흔한 낱말(이것만 겹쳐서는 같은 소식이 아니다 — 10-22 실측: 'Gemini 4 Argon 정식 출시'가
# '구글 클라우드 … A5X 정식 출시 예정'과 구글·정식·출시만으로 겹쳐 중복으로 오판)
GENERIC = {"ai", "the", "and", "for", "with", "출시", "공개", "정식", "발표", "추진", "도입", "확대", "기반", "신규", "예정",
           "보도", "규모", "달러", "억", "지원", "강화", "관련", "주요", "내용", "개발", "기능", "모델", "서비스", "기업", "구글",
           "오픈", "메타", "엔비디아", "앤스로픽", "마이크로소프트", "아마존", "애플", "스페이스x", "딥시크"}


def toks(s):
    s = re.sub(r"\[삼성 이영진\]", " ", s)
    return {w.lower() for w in re.findall(r"[0-9A-Za-z][0-9A-Za-z.\-]+|[가-힣]{2,}", s)} - GENERIC


def overlap(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb or len(ta & tb) < 2:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def collect(today):
    msgs, before = {}, None
    now = dt.datetime.now(KST)
    for _ in range(MAX_PAGES):
        page = parse_page(fetch(before))
        if not page:
            break
        for m in page:
            msgs[m["id"]] = m
        before = min(m["id"] for m in page)
        # 직전 다이제스트(괄호 날짜 < 오늘)를 만났으면 그만 거슬러 올라간다
        if any((digest_date(m["text"]) or today) < today for m in page):
            break
    allm = sorted(msgs.values(), key=lambda m: m["id"])
    prev = [m for m in allm if (digest_date(m["text"]) or today) < today]
    start = prev[-1]["when"] if prev else now - dt.timedelta(hours=30)
    todays = [m for m in allm if digest_date(m["text"]) == today]
    digest_items = []
    if todays:
        d = todays[-1]
        digest_items = [ln.lstrip("■").strip() for ln in d["text"].split("\n") if ln.strip().startswith("■")]
    # 지난 날짜로 되짚을 때는 그날 ② 시각(09:40)까지만 본다
    end = now if today >= now.date() else dt.datetime.combine(today, dt.time(9, 40), KST)
    singles = [m for m in allm if start < m["when"] <= end and digest_date(m["text"]) is None and m["text"].strip()]
    # 미리보기 사본·같은 제목 반복 정리: 제목별로 가장 긴 글 하나
    by_title = {}
    for m in singles:
        k = re.sub(r"\s+", "", title_of(m["text"]))
        if not k:
            continue
        if k not in by_title or len(m["text"]) > len(by_title[k]["text"]):
            by_title[k] = m
    kept = sorted(by_title.values(), key=lambda m: m["id"])
    uniq, dups = [], []
    for m in kept:
        t = title_of(m["text"])
        best = max(((overlap(t, it), i + 1) for i, it in enumerate(digest_items)), default=(0.0, 0))
        src = [u for u in m["links"] if "t.me/" not in u]
        body = m["text"].split("\n", 1)[1] if "\n" in m["text"] else re.split(r"\s:\s", m["text"], maxsplit=1)[-1]
        rec = dict(id=m["id"], time=m["when"].strftime("%m-%d %H:%M"), title=t,
                   summary=re.sub(r"\s+", " ", body).strip()[:300], source_links=src[:3])
        if best[0] >= DUP_RATIO:
            rec["dup_of_digest"] = best[1]; rec["overlap"] = round(best[0], 2); dups.append(rec)
        else:
            uniq.append(rec)
    return dict(date=today.isoformat(), channel=CHANNEL, window_start=start.isoformat(timespec="minutes"),
                fetched_at=end.isoformat(timespec="seconds"), digest_found=bool(todays),
                digest_items=digest_items, singles=uniq, singles_dup_of_digest=dups,
                singles_dropped_preview_or_repeat=len(singles) - len(kept))


def render(r):
    L = [f"# 삼성 글로벌 AI/SW 텔레그램 — {r['date']}", "",
         f"- 수집 창: {r['window_start']} ~ {r['fetched_at']} (직전 다이제스트 이후)",
         f"- 오늘자 다이제스트: {'있음 ' + str(len(r['digest_items'])) + '항목' if r['digest_found'] else '없음'}",
         f"- 개별 메시지: 다이제스트 외 {len(r['singles'])}건 / 다이제스트와 중복 {len(r['singles_dup_of_digest'])}건 / 미리보기 사본·반복 정리 {r['singles_dropped_preview_or_repeat']}건", ""]
    if r["digest_items"]:
        L += ["## A. 다이제스트 ■ 항목"] + [f"{i}. {t}" for i, t in enumerate(r["digest_items"], 1)] + [""]
    L += ["## B. 개별 메시지(다이제스트 외) — 기사 링크는 직접 찾아 검증할 것"]
    for i, s in enumerate(r["singles"], 1):
        L += [f"{i}. [{s['time']} #{s['id']}] {s['title']}", f"   요약: {s['summary']}"]
        if s["source_links"]:
            L += ["   원천 링크(기사 아님, 검색 단서): " + " ".join(s["source_links"])]
    if r["singles_dup_of_digest"]:
        L += ["", "## C. 다이제스트와 겹친 개별 메시지(게시 안 함 — 링크 찾을 때 단서로만)"]
        for s in r["singles_dup_of_digest"]:
            L += [f"- [{s['time']} #{s['id']}] {s['title']}  -> 다이제스트 {s['dup_of_digest']}번 (겹침 {s['overlap']})"
                  + (("  단서: " + " ".join(s["source_links"])) if s["source_links"] else "")]
    return "\n".join(L) + "\n"


def main():
    args = sys.argv[1:]
    today = dt.datetime.now(KST).date()
    if "--date" in args:
        today = dt.date.fromisoformat(args[args.index("--date") + 1])
    try:
        r = collect(today)
    except Exception as e:
        print(f"[samsung-tme] 받기 실패: {e}"); sys.exit(1)
    outd = os.path.join(REPO, "cowork", "raw")
    os.makedirs(outd, exist_ok=True)
    base = os.path.join(outd, f"samsung-tme-{today.isoformat()}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(render(r))
    print(f"[samsung-tme] {today} 수집 창 {r['window_start']} 이후 / 다이제스트 {'있음 ' + str(len(r['digest_items'])) + '항목' if r['digest_found'] else '없음'}"
          f" / 개별 {len(r['singles'])}건(다이제스트 중복 {len(r['singles_dup_of_digest'])}·사본정리 {r['singles_dropped_preview_or_repeat']}) -> {base}.md")
    sys.exit(0 if (r["digest_found"] or r["singles"]) else 2)


if __name__ == "__main__":
    main()
