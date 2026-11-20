# -*- coding: utf-8 -*-
"""samsung_check.py - ② 삼성 글로벌 AI/SW 뉴스의 '찾은 기사 링크'를 검증한다.

## 왜 만들었나 (2026-11-20 사용자 지시 — 검증 수단 A안)
② 는 항목마다 `run.sh find` 로 국내 기사를 찾고 WebFetch 로 열어 제목·매체·게재일·수치를
대조해 왔다. 그런데 WebFetch 는 '대화·검색 결과에 직접 나온 주소'만 열 수 있어서
`run.sh find`(네이버 API)가 돌려준 주소를 거부한다(PROVENANCE_REQUIRED). 국내 언론사로
범위를 좁힌 한국어 WebSearch 도 프록시가 400 으로 막아 우회할 길이 없다. 11-20 에는
WebFetch 가 거부한 뒤 find --body 로 본문을 받아 눈으로 대조했는데, 사용자가
검증 수단을 처음부터 파이프라인 도구로 정했다. ④ 의 body/linkcheck 가 매일 같은 기사들을
이미 받으므로 새로운 접근 경로가 아니다.

## 무엇을 보나 (linkcheck 와 body_rescore 를 그대로 쓴다)
1. 페이지를 받는다(linkcheck.fetch_title): HTTP 상태·페이지 제목·발행일.
   404/410·목록 페이지면 '실패'. 받기 실패·403 이면 '미확보'(채택 안 함 — 절차서 ② 3번).
2. 게재일: 페이지 발행일이 창(A: 오늘-2일~오늘 / B: 수집 창 시작일~오늘) 안이어야 한다.
   창 밖이면 '실패'(다른 날짜 기사를 끼워 맞추지 않는다). 발행일을 못 읽으면 대조 생략.
3. 본문을 받는다(body_rescore.fetch): 항목 요약의 숫자·고유명사가 제목+본문에 있는가.
   숫자가 하나도 안 맞거나 낱말 겹침이 낮으면 '의심' — 단위 환산(15억 달러=1.5B) 때문에
   자동으로 버리지 않고 사람이 리드를 보고 판단한다. 리드 300자를 같이 찍는다.

## 판정
    OK     : 받음 + 게재일 창 안(또는 미상) + 숫자 1개 이상 일치(요약에 숫자가 있을 때) + 낱말 겹침 40% 이상
    의심   : 받았지만 숫자 불일치·낱말 겹침 낮음 — 리드를 읽고 같은 사건이면 채택, 아니면 다시 찾기
    실패   : 404/목록 페이지/게재일 창 밖 — 채택 금지
    미확보 : 페이지·본문을 못 받음(403·시간초과 등) — 채택 금지, 다른 기사로 다시 찾기

## 사용법
  입력 TSV: cowork/raw/samsung-check-<날짜>.tsv  (헤더 없음, 탭 구분)
      번호<TAB>구분(A|B)<TAB>항목 요약(텔레그램 원문 한 줄)<TAB>URL<TAB>매체명
  bash cowork/run.sh samsung-check            # 위 TSV 를 읽어 판정, out/samsung-check-<날짜>.json
  bash cowork/run.sh samsung-check --file X   # 다른 TSV
  종료코드 0 = 전부 OK / 1 = OK 아닌 건 있음(의심은 리드 확인 후 채택 가능) / 2 = 입력 없음
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linkcheck      # noqa: E402
import body_rescore   # noqa: E402

BASE = os.path.dirname(HERE)

STOP = set("""그리고 대한 위한 통해 관련 이번 지난 올해 내년 계획 발표 공개 출시 보도 확대 추진 기록 기준 대비
규모 가능 전망 예정 제공 지원 강화 개선 사용 서비스 기업 회사 시장 업계 미국 중국 글로벌 최대 신규 이상 이하
the and for with from that this into over its are was has have""".split())

RE_NUM = re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)")


def nums(s):
    out = set()
    for m in RE_NUM.finditer(s or ""):
        v = m.group(1).replace(",", "")
        if len(v.replace(".", "")) >= 2 or "." in v:   # 한 자리 숫자는 변별력이 없다
            out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def words(s):
    s = re.sub(r"\$?\d[\d,.]*", " ", s or "")
    parts = re.split(r"[^0-9A-Za-z가-힣]+", s)
    out = set()
    for p in parts:
        p = p.lower()
        if len(p) < 2 or p in STOP:
            continue
        # 한국어 조사 꼬리 떼기(대략)
        p = re.sub(r"(은|는|이|가|을|를|의|에|에서|로|으로|와|과|도|만)$", "", p) if re.search(r"[가-힣]", p) and len(p) > 2 else p
        out.add(p)
    return out


def kmoney(text):
    """'1억5천만' -> '1.5억', '2조3천억' -> '2.3조' — 요약(1.5억 달러)과 기사(1억5천만달러) 표기 차이를 맞춘다."""
    t = re.sub(r"(\d+)억\s*(\d)천만", r"\1.\2억", text or "")
    return re.sub(r"(\d+)조\s*(\d)천억", r"\1.\2조", t)


def naver_date(url):
    """네이버 뉴스 페이지는 메타에 발행일이 없어 linkcheck 가 못 읽는다 — data-date-time 을 본다."""
    if "news.naver.com" not in (url or ""):
        return None
    try:
        m = re.search(r'data-date-time="(\d{4}-\d{2}-\d{2})', body_rescore._get(url))
        return m.group(1) if m else None
    except Exception:
        return None


def num_in(n, text):
    t = kmoney(text or "").replace(",", "")
    return re.search(r"(?<![\d.])%s(?![\d])" % re.escape(n), t) is not None


def window_start(day, kind, raw_md):
    if kind == "B" and os.path.exists(raw_md):
        m = re.search(r"수집 창:\s*(\d{4}-\d{2}-\d{2})", open(raw_md, encoding="utf-8").read())
        if m:
            return m.group(1)
    return str(day - datetime.timedelta(days=2))


def check(row, day, raw_md):
    no, kind, summary, url, outlet = (row + [""] * 5)[:5]
    r = dict(no=no, kind=kind, summary=summary, url=url, outlet=outlet)
    bad = linkcheck.shape_verdict(url)
    if bad:
        r.update(verdict="실패", reason=bad)
        return r
    status, ptitle, pdate, err = linkcheck.fetch_title(url)
    pdate = pdate or naver_date(url)
    r.update(status=status, page_title=ptitle, page_date=pdate)
    if status in (404, 410):
        r.update(verdict="실패", reason="HTTP %d" % status)
        return r
    lo = window_start(day, kind, raw_md)
    if pdate and not (lo <= pdate <= str(day)):
        r.update(verdict="실패", reason="게재일 창 밖(페이지 %s, 창 %s~%s)" % (pdate, lo, day))
        return r
    body, berr = body_rescore.fetch(url, ptitle or summary)
    if not body and not ptitle:
        r.update(verdict="미확보", reason="페이지·본문 못 받음(%s)" % (err or berr or "HTTP %s" % status))
        return r
    text = (ptitle or "") + " " + (body or "")
    want_n = nums(summary)
    hit_n = sorted(n for n in want_n if num_in(n, text))
    want_w = words(summary)
    low = text.lower()
    hit_w = sorted(w for w in want_w if w in low)
    ratio = len(hit_w) / float(len(want_w)) if want_w else 1.0
    r.update(nums_want=sorted(want_n), nums_hit=hit_n, words_hit=hit_w,
             words_miss=sorted(want_w - set(hit_w)), word_ratio=round(ratio, 2),
             body_len=len(body or ""), lead=re.sub(r"\s+", " ", (body or "")[:300]))
    probs = []
    if not body:
        probs.append("본문 못 받음(%s) — 제목만 대조" % berr)
    if want_n and not hit_n:
        probs.append("수치 불일치(요약 %s)" % ",".join(sorted(want_n)))
    if ratio < 0.4:
        probs.append("낱말 겹침 %.0f%%" % (ratio * 100))
    if not pdate:
        probs.append("게재일 못 읽음 — 리드로 확인")
    if (want_n and not hit_n) or ratio < 0.4 or not body:
        r.update(verdict="의심", reason=" / ".join(probs))
    else:
        r.update(verdict="OK", reason="게재일 %s · 수치 %s · 낱말 %.0f%%%s" % (
            pdate or "미상", ",".join(hit_n) or "-", ratio * 100,
            (" (" + probs[0] + ")") if probs else ""))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--file", default=None)
    a = ap.parse_args()
    day = datetime.date.fromisoformat(a.date) if a.date else datetime.date.today()
    tsv = a.file or os.path.join(BASE, "cowork", "raw", "samsung-check-%s.tsv" % day)
    raw_md = os.path.join(BASE, "cowork", "raw", "samsung-tme-%s.md" % day)
    if not os.path.exists(tsv):
        print("[samsung-check] 입력 없음: %s" % tsv)
        print("  형식(탭 구분, 헤더 없음): 번호<TAB>A|B<TAB>항목 요약<TAB>URL<TAB>매체명")
        sys.exit(2)
    rows = [l.rstrip("\n").split("\t") for l in open(tsv, encoding="utf-8") if l.strip()]
    res = [check(r, day, raw_md) for r in rows]
    cnt = {}
    for x in res:
        cnt[x["verdict"]] = cnt.get(x["verdict"], 0) + 1
        print("  [%s] %s(%s) %s" % (x["verdict"], x["no"], x["kind"], x["summary"][:50]))
        print("        %s | %s" % (x["url"], x.get("reason", "")))
        if x.get("page_title"):
            print("        제목: %s (%s)" % (x["page_title"][:90], x.get("page_date") or "날짜미상"))
        if x["verdict"] == "의심" and x.get("lead"):
            print("        리드: %s" % x["lead"][:220])
    out = os.path.join(BASE, "out", "samsung-check-%s.json" % day)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[samsung-check] %d건 — %s -> %s" % (len(res), " / ".join("%s %d" % kv for kv in sorted(cnt.items())), out))
    print("[samsung-check] 게시: OK 는 채택. 의심은 리드를 읽고 같은 사건·같은 수치면 채택, 아니면 다시 find."
          " 실패·미확보는 채택 금지(다른 기사로 다시 찾거나 '링크 미확보').")
    sys.exit(0 if cnt.get("OK", 0) == len(res) else 1)


if __name__ == "__main__":
    main()
