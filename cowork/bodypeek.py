# -*- coding: utf-8 -*-
"""bodypeek.py - 판정 중 본문 앞부분만 꺼내 본다.

## 왜 만들었나 (2026-09-02)
전수 판정은 `candidates-blind-<날짜>.txt` 한 줄(제목·통로·매체·발행일·보도수)
만 보고 한다. 그런데 **E축(사건의 알맹이)** 은 제목만으로 안 보이는 경우가
많고, 그 상태로 '중'을 주면 자주 틀린다.

실측 09-02: '퍼플렉시티, 맥앱에 하이브리드 컴퓨트 도입'을 '기능 추가뿐'으로
보고 중으로 내렸다. 본문은 클라우드 모델과 로컬 LLM(젬마 E4B·큐원 35B,
1종은 자체 추가학습)을 데이터 민감도로 분기하고 **로컬 생성 토큰은 과금하지
않는** 설계였다. 온디바이스 추론의 상용 레퍼런스라 SKT 관련성이 올라간다.
그 본문이 **이미 bodycache 에 3,691자로 들어 있었는데 안 열었다.**

## 핵심: 추가 네트워크 비용이 0이다
`run.sh body`(본문 재채점)가 상위 300건 본문을 이미 받아
`out/bodycache-<날짜>.json` 에 URL 키로 저장해 둔다. 이 스크립트는 그 캐시를
읽기만 한다. 새로 받지 않는다.

## 대상 범위를 좁힌 이유
'중' 전체 중 캐시 보유분은 98건(앞 700자만 해도 66K자)이다. 다 읽으면
뒤로 갈수록 판정이 형식적으로 변한다. 그래서 **tier0 통로이거나 보도가
'다수'인 건**으로 좁혔다. 실측 09-02 에 37건이고 2~3분이면 된다.
놓치면 아픈 유형(사람·봇이 한 번 거른 입력 + 여러 매체가 동시에 받은 사건)을
정확히 덮는다.

## 왜 700자인가
리드 문단에 알맹이(주체·수치·무엇이 새로운가)가 다 있다. 퍼플렉시티 건도
로컬 모델 이름과 과금 구조가 앞 1/3 에 나온다.

사용법:
  python3 cowork/bodypeek.py --date 2026-09-02              # 대상 목록
  python3 cowork/bodypeek.py --date 2026-09-02 267 92 18    # 앞 700자
"""

import argparse
import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")

HEAD_CHARS = 700          # 리드 문단이면 충분하다. 늘리지 말 것.

# 캐시 본문 앞에 붙어 오는 네비게이션·공유 버튼 문구. 실측 09-02 에
# 디지털투데이 건이 250자를 잡아먹어 700자 중 1/3 이 날아갔다.
_NAV = re.compile(
    r"(바로가기|복사하기|본문 글씨 (?:줄이기|키우기)|스크롤 이동 상태바|"
    r"댓글 \d+|다른 공유 찾기|이 기사를 공유합니다|"
    r"(?:페이스북|카카오톡|네이버블로그|트위터|X)\(으\)로 기사보내기|"
    r"Google 선호 뉴스 추가|무단전재 및 재배포 금지|"
    r"기사공유하기|프린트|메일보내기|글씨키우기|글씨줄이기|닫기|"
    r"-->|<!--)")

# 리드 문단의 시작점. '[매체명 홍길동 기자]' 형태가 가장 흔하다.
_LEAD = re.compile(r"\[[^\[\]]{0,30}?기자\]|기자 =|기자입니다\]")


def clean(body):
    """캐시 본문에서 리드 문단부터 남긴다.

    앞머리 네비게이션을 걷어내지 않으면 700자 중 상당수가 버튼 이름으로
    채워진다. 리드 시작 표지가 앞쪽 60% 안에 있으면 거기서부터 자른다.
    """
    t = body.replace("&#39;", "'").replace("&quot;", '"')
    t = t.replace("&amp;", "&").replace("&nbsp;", " ")
    m = _LEAD.search(t[:max(400, int(len(t) * 0.6))])
    if m:
        t = t[m.end():]
    t = _NAV.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def load(day):
    """blind 목록·프롬프트 JSONL·본문 캐시를 한 번에 읽는다."""
    blind = os.path.join(OUT, "candidates-blind-%s.txt" % day)
    prompt = os.path.join(OUT, "prompt-%s.txt" % day)
    cachep = os.path.join(OUT, "bodycache-%s.json" % day)
    for p in (blind, prompt):
        if not os.path.exists(p):
            raise SystemExit("[bodypeek] %s 없음. blind/brief 를 먼저 돌리세요." % p)
    if not os.path.exists(cachep):
        raise SystemExit("[bodypeek] %s 없음. **run.sh body 를 먼저 돌리세요.**\n"
                         "           본문 캐시가 없으면 이 단계는 의미가 없습니다."
                         % cachep)

    meta = {}
    for line in open(blind, encoding="utf-8"):
        m = re.match(r"^([NLC]?\d+) \| ([^|]+)\| ([^|]*)\| ([^|]*)\| ([^|]*)\| (.*)$",
                     line)
        if not m:
            continue
        idx, route, outlet, date, cov, head = [x.strip() for x in m.groups()]
        head = re.sub(r"\s*\[중복의심:.*?\]", "", head)
        head = re.sub(r"\s*\[링크없음\]", "", head).strip()
        if head.startswith("[") and head.endswith("]"):
            head = head[1:-1]
        meta[idx] = {"route": route, "outlet": outlet, "date": date,
                     "cov": cov, "head": head}

    urls = {}
    for line in open(prompt, encoding="utf-8"):
        if line.startswith('{"idx"'):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            urls[str(d["idx"])] = d.get("url") or ""

    cache = json.load(open(cachep, encoding="utf-8"))
    return meta, urls, cache


def go_set(day):
    """verdict CSV 의 '고' idx 목록."""
    import csv
    vp = os.path.join(OUT, "verdict-%s.csv" % day)
    if not os.path.exists(vp):
        return None
    return [r["idx"].strip() for r in csv.DictReader(open(vp, encoding="utf-8"))
            if r.get("grade", "").strip() == "고" and r.get("idx", "").strip()]


def peek_path(day):
    return os.path.join(OUT, "peeked-%s.json" % day)


def record(day, idxs):
    """리드를 실제로 찍은 idx 를 남긴다. run.sh rank 가 이 기록을 검사한다."""
    p = peek_path(day)
    cur = []
    if os.path.exists(p):
        try:
            cur = json.load(open(p, encoding="utf-8")).get("peeked", [])
        except ValueError:
            cur = []
    for i in idxs:
        if i not in cur:
            cur.append(i)
    json.dump({"date": day, "peeked": cur}, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def check(day):
    """'고' 전량의 본문을 봤는가. [2026-09-22 신설]

    [왜 생겼나 - 실측 2026-09-22]
      사용자가 뺀 '정책금융 194.5조' 건은 본문 대부분이 AI DC 가 아니라
      다른 정책금융 얘기였고 AI DC 는 기사 말미 한 문단뿐이었다. 같은 사건을
      전문으로 다룬 다른 기사(아이뉴스24)가 같은 후보 풀에 있었는데 그쪽
      본문에는 "별도 재원이나 금리·한도 우대가 새로 생기는 것은 아니다"라고
      적혀 있었다. **둘 중 아무 본문이나 한 번만 열었어도 '고'가 아니었다.**
      그날 '고' 13건 중 리드를 연 것은 0건이었고, 이 건은 rank 3 으로 실렸다.
      제목만 보고 '고'를 주는 일을 구조적으로 막는다(2026-09-22 사용자 지시).

    [판정 기준] 본문 캐시가 있는 '고'는 전부 peeked 기록에 있어야 한다.
      본문을 못 받은 건(링크 미확보 등)은 막지 못한다. 경고만 남긴다.
    """
    gos = go_set(day)
    if gos is None:
        print("[bodypeek] 판정 파일이 없습니다.")
        return 1
    try:
        meta, urls, cache = load(day)
    except SystemExit as e:
        print(str(e))
        return 1
    have = [i for i in gos if cache.get(urls.get(i) or "")]
    seen = []
    if os.path.exists(peek_path(day)):
        try:
            seen = json.load(open(peek_path(day), encoding="utf-8")).get("peeked", [])
        except ValueError:
            pass
    miss = [i for i in have if i not in seen]
    nobody = [i for i in gos if i not in have]
    if miss:
        print("[bodypeek] **'고' %d건 중 %d건의 본문을 한 번도 열지 않았습니다: %s**"
              % (len(gos), len(miss), " ".join(miss)))
        print("[bodypeek] 제목만 보고 '고'를 줄 수 없습니다(2026-09-22 사용자 지시).")
        print("[bodypeek]   bash cowork/run.sh bodypeek --go")
        return 1
    print("[bodypeek] '고' 본문 확인 완료: %d/%d건" % (len(have), len(gos)))
    if nobody:
        print("[bodypeek]   본문 미확보 %d건: %s — 게이트가 막지 못합니다. "
              "링크를 찾아 넣으세요." % (len(nobody), " ".join(nobody)))
    return 0


def in_scope(m):
    """대상 범위: tier0 통로이거나 보도 '다수'.

    이 범위를 넓히지 말 것. '중' 전체로 넓히면 98건이 되어
    판정 후반이 형식적으로 변한다(2026-09-02 사용자 합의).
    """
    return m["route"].startswith("tier0") or m["cov"] == "다수"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--chars", type=int, default=HEAD_CHARS)
    ap.add_argument("--all", action="store_true",
                    help="범위 제한 없이 '중' 전체를 대상으로 본다(권장하지 않음)")
    ap.add_argument("--go", action="store_true",
                    help="판정 '고' 전량의 리드를 찍고 본 것을 기록한다")
    ap.add_argument("--check", action="store_true",
                    help="'고' 전량을 봤는지 검사한다(안 봤으면 종료코드 1)")
    ap.add_argument("idx", nargs="*", help="볼 idx. 없으면 대상 목록만 찍는다")
    a = ap.parse_args()

    if a.check:
        sys.exit(check(a.date))

    if a.go:
        gos = go_set(a.date)
        if gos is None:
            sys.exit("[bodypeek] 판정 파일이 없습니다. verdict 를 먼저 돌리세요.")
        if not a.idx:
            a.idx = gos
        print("[bodypeek] '고' %d건 전량의 리드를 찍습니다. **읽고 판정을 다시 보세요.**"
              % len(a.idx))
        print("[bodypeek]   본문이 헤드라인과 다른 이야기를 하면 '고'가 아닙니다.")
        print("[bodypeek]   같은 사건을 더 정면으로 다룬 기사가 후보에 있는지도 보세요.")

    meta, urls, cache = load(a.date)

    if a.idx:
        miss = []
        for i in a.idx:
            i = str(i).strip()
            m = meta.get(i)
            if not m:
                miss.append(i)
                continue
            body = cache.get(urls.get(i) or "") or ""
            print("=" * 72)
            print("[%s] %s" % (i, m["head"]))
            print("      %s / %s / %s / 보도 %s"
                  % (m["route"], m["outlet"], m["date"], m["cov"]))
            if not body:
                print("      (본문 캐시 없음 - 링크미확보이거나 상위 300건 밖)")
                continue
            txt = clean(body)
            print()
            print(txt[:a.chars] + ("…" if len(txt) > a.chars else ""))
            print()
        if miss:
            print("[bodypeek] 없는 idx: %s" % ", ".join(miss))
        record(a.date, [str(i).strip() for i in a.idx
                        if str(i).strip() not in miss])
        return

    # 인자가 없으면 대상 후보를 찍는다. verdict CSV 가 있으면 '중'만 추린다.
    vpath = os.path.join(OUT, "verdict-%s.csv" % a.date)
    jung = None
    if os.path.exists(vpath):
        import csv
        jung = {r["idx"].strip() for r in csv.DictReader(open(vpath, encoding="utf-8"))
                if r.get("grade", "").strip() == "중"}

    rows = []
    for i, m in meta.items():
        if jung is not None and i not in jung:
            continue
        if not cache.get(urls.get(i) or ""):
            continue
        if not a.all and not in_scope(m):
            continue
        rows.append((i, m))
    rows.sort(key=lambda x: (x[1]["route"], x[0]))

    if jung is None:
        print("[bodypeek] verdict-%s.csv 가 아직 없어 후보 전체에서 골랐습니다."
              % a.date)
        print("           판정을 1차로 마친 뒤 다시 부르면 '중'만 추립니다.")
    print("[bodypeek] 대상 %d건 (범위: %s / 본문 캐시 보유분만)"
          % (len(rows), "제한 없음" if a.all else "tier0 또는 보도 다수"))
    print("           앞 %d자 기준 약 %dK자입니다."
          % (a.chars, sum(min(a.chars, len(cache[urls[i]]))
                          for i, _ in rows) // 1000))
    for i, m in rows:
        print("  %-5s %-18s %-6s %s" % (i, m["route"], m["cov"], m["head"][:52]))
    if rows:
        print()
        print("다음처럼 부르세요:")
        print("  bash cowork/run.sh bodypeek %s"
              % " ".join(i for i, _ in rows[:12]))


if __name__ == "__main__":
    main()
