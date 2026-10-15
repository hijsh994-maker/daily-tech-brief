# -*- coding: utf-8 -*-
"""
test_samsung.py - 삼성 글로벌 AI/SW 섹션(요약문) -> 기사 링크 복원 테스트.

이 섹션은 기사 제목이 아니라 작성자가 쓴 요약문이다.
  "■ 문샷 AI, 현재 315억 달러 규모 펀딩 라운드 마무리 이후 최대 500억 달러
     밸류로 프리 IPO 라운드 추진 보도"
원문에 없는 문장이라 제목 유사도 매칭이 원리적으로 불가능하다.

그래서 수작업으로 해오던 방식을 그대로 자동화한다:
  요약문 -> 핵심 키워드 추출 -> 네이버 검색 -> 후보 기사 -> 토큰 겹침으로 검증

여러 질의 전략을 동시에 돌려 어느 쪽이 잘 맞는지 측정한다.
"""
import json
import re
import sys

import naver_api

# 요약문에 흔한 서술어/조사/일반명사. 질의에서 빼야 검색이 좁혀진다.
STOP = set("""
공개 출시 발표 추진 검토 보도 확대 강화 진행 예정 계획 전망 관련 위해 통해
기반 대상 대비 이후 이전 현재 최대 최소 약 등 및 또한 그리고 하지만 대한
기업 회사 사업 서비스 기능 모델 시장 규모 수준 상황 경우 부분 내용 결과
지난해 올해 내년 상반기 하반기 분기 일환 해석 가능성 시사 논의 협의 체결
""".split())

# 한국 언론이 쓰는 표기로 바꿔야 검색이 된다. (원문은 삼성 채널 표기)
ALIAS = {
    "앤스로픽": "앤트로픽", "오픈AI": "오픈AI", "문샷 AI": "문샷",
    "스페이스X": "스페이스X", "구글": "구글", "메타": "메타",
    "코어위브": "코어위브", "데이터브릭스": "데이터브릭스",
    "팔로알토 네트웍스": "팔로알토", "베센트": "베센트",
    "마이크로소프트": "마이크로소프트", "미스트랄 AI": "미스트랄",
    "인텔": "인텔", "포티넷": "포티넷", "누뱅크": "누뱅크",
}


def parse_samsung(md):
    sec = md.split("## 텔레그램 - 삼성 글로벌 AI/SW 뉴스")
    if len(sec) < 2:
        return []
    body = sec[1].split("## ")[0]
    out = []
    for l in body.split("\n"):
        s = l.strip()
        if s.startswith("- ■"):
            out.append(s[3:].strip())
    return out


def tokens(s):
    """검증용 토큰. 한글 2자 이상 / 영문 2자 이상 / 숫자 포함 토큰."""
    t = re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9.\-]{1,}|\d+[a-zA-Z%]*", s)
    return [x for x in t if x not in STOP and len(x) >= 2]


def keywords(bullet, n=4):
    """요약문 -> 검색 키워드. 앞쪽 주체 + 고유명사/영문/숫자 우선."""
    head = bullet.split(",")[0].strip()
    head = ALIAS.get(head, head)
    rest = bullet[len(bullet.split(",")[0]):]
    # 영문/제품명/숫자를 우선 채택 (변별력이 높다)
    strong = re.findall(r"[A-Za-z][A-Za-z0-9.\-]{2,}|\d+[가-힣A-Za-z%]*", rest)
    strong = [s for s in strong if s not in STOP][:n]
    if len(strong) < 2:
        ko = [t for t in re.findall(r"[가-힣]{2,}", rest) if t not in STOP]
        strong += ko[:n - len(strong)]
    return head, strong


def variants(bullet):
    head, kw = keywords(bullet)
    v = []
    if kw:
        v.append((head + " " + " ".join(kw[:3])).strip())      # 주체 + 핵심3
        v.append((head + " " + " ".join(kw[:1])).strip())      # 주체 + 핵심1
    v.append(head)                                             # 주체만
    v.append(re.sub(r"\s+", " ", bullet)[:60])                 # 요약문 앞부분
    seen, out = set(), []
    for q in v:
        if q and len(q) >= 2 and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# 변별력이 없는 고빈도 토큰. 이것들만 겹쳐서는 같은 기사라고 볼 수 없다.
# (1차 실측에서 오탐 6건이 전부 이 토큰들로 매칭됐다:
#  '오픈AI 한국 첫 광고'가 '오픈AI 중소기업 챗GPT 출시'로 둔갑하는 식)
GENERIC = set("""
AI CEO IPO GPU CPU LLM MS IT API IP 오픈 모델 클라우드 보안 정부 미국 중국
달러 기술 투자 시장 서비스 플랫폼 데이터 반도체 인프라 파트너십 매출 주가
스페이스 네트웍스 코어위브 팔로알토 데이터브릭스 마이크로소프트 앤트로픽
구글 메타 인텔 삼성 엔비디아 테슬라
""".split())


def distinctive(s):
    """변별토큰만. 고유 제품명·숫자·사건어휘가 남는다."""
    return {t for t in tokens(s) if t not in GENERIC}


def overlap(bullet, title):
    """(변별토큰 겹침수, 전체 겹침수, 겹친 변별토큰)"""
    da, db = distinctive(bullet), distinctive(title)
    ta, tb = set(tokens(bullet)), set(tokens(title))
    d = da & db
    return len(d), len(ta & tb), sorted(d)


def entity_of(bullet):
    """요약문의 주체(첫 절)."""
    h = bullet.split(",")[0].strip()
    return ALIAS.get(h, h)


def parse_pubdate(s):
    """네이버 pubDate(RFC822) -> date. 실패 시 None."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        return None


def main():
    import datetime, re as _re
    md = open(sys.argv[1], encoding="utf-8").read()
    m = _re.search(r"(\d{4})-(\d{2})-(\d{2})", sys.argv[1])
    logdate = (datetime.date(*map(int, m.groups())) if m
               else datetime.date.today())
    MAXDAYS = 3   # 로그 날짜 기준 허용 발행일 범위

    bullets = parse_samsung(md)
    out = ["삼성 섹션 %d건 테스트  (로그일자 %s, 발행일 ±%d일)"
           % (len(bullets), logdate, MAXDAYS), ""]
    res = []
    hit = 0

    for i, b in enumerate(bullets, 1):
        ent = entity_of(b)
        best, rejected = None, 0
        for q in variants(b):
            try:
                items = naver_api.search(q, display=10, sort="sim")
            except naver_api.NotConfigured:
                print("NAVER 인증정보 없음")
                return
            except Exception as e:
                out.append("  %d ERR %s" % (i, str(e)[:60]))
                continue
            for it in items:
                if not (it["originallink"] or "").startswith("http"):
                    continue
                # 1) 발행일 제약: 같은 사건이면 로그 날짜 근처여야 한다
                pd = parse_pubdate(it.get("pubDate"))
                if pd and abs((pd - logdate).days) > MAXDAYS:
                    rejected += 1
                    continue
                nd, nt, sh = overlap(b, it["title"])
                # 2) 주체가 제목에 있어야 한다.
                #    '미-중' vs '미중' 처럼 하이픈/공백만 다른 경우가 있어 정규화.
                flat_title = _re.sub(r"[\s\-·]", "", it["title"])
                ent_ok = bool(ent) and any(
                    _re.sub(r"[\s\-·]", "", tok) in flat_title
                    for tok in _re.findall(r"[가-힣A-Za-z]{2,}", ent))
                sc = (nd, ent_ok, nt)
                if best is None or sc > best[0]:
                    best = (sc, it, q, sh, pd)
            if best and best[0][0] >= 2 and best[0][1]:
                break

        # 3) 신뢰도 3단계.
        #    auto   : 변별토큰 2+ AND 주체일치      -> 바로 사용
        #    review : 변별토큰 1  AND 주체일치 AND 당일 -> 사람이 눈으로 확인
        #    drop   : 그 외
        nd_ = best[0][0] if best else 0
        ent_ = best[0][1] if best else False
        same_day = bool(best and best[4] == logdate)
        if nd_ >= 2 and ent_:
            tier = "auto"
        elif nd_ >= 1 and ent_ and same_day:
            tier = "review"
        else:
            tier = "drop"
        ok = tier == "auto"
        hit += ok
        out.append("%2d. [%-6s] %s" % (i, tier, b[:62]))
        if best:
            out.append("      질의: %s" % best[2])
            out.append("      후보: %s  (%s)" % (best[1]["title"][:56], best[4]))
            out.append("      변별토큰 %d개 %s | 주체일치 %s | 날짜탈락 %d건"
                       % (best[0][0], best[3][:6], best[0][1], rejected))
            out.append("      URL : %s" % best[1]["originallink"][:78])
        else:
            out.append("      후보 없음 (날짜탈락 %d건)" % rejected)
        out.append("")
        res.append({"bullet": b, "ok": ok, "tier": tier,
                    "distinct": best[0][0] if best else 0,
                    "entity_ok": best[0][1] if best else False,
                    "query": best[2] if best else None,
                    "title": best[1]["title"] if best else None,
                    "pubdate": str(best[4]) if best and best[4] else None,
                    "url": best[1]["originallink"] if best else None})

    import collections as _c
    tc = _c.Counter(x["tier"] for x in res)
    out.insert(1, "  auto %d / review %d / drop %d  (auto+review = %d/%d, %.0f%%)"
               % (tc["auto"], tc["review"], tc["drop"],
                  tc["auto"] + tc["review"], len(bullets),
                  (tc["auto"] + tc["review"]) / len(bullets) * 100))
    open("samsung_result.txt", "w", encoding="utf-8").write("\n".join(out))
    json.dump(res, open("samsung_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n".join(out[:3]).encode("cp949", "replace").decode("cp949"))


if __name__ == "__main__":
    main()
