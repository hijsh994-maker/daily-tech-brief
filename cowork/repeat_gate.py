#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반복 게이트 — 같은 기사를 또 올리는 것을 기계로 막는다.  [2026-09-21 신설]

[왜 만들었나]
  09-21 사용자 검토에서 뺀 5건 중 **3건이 09-18 에 이미 뺀 건**이었다.
    · 신한은행 '모두의 AI' 컨소시엄  -> 09-18 rank 1 로 올렸다가 제외됨
                                       -> 09-21 에 또 rank 1 로 올림
    · 피규어 헬릭스 2.5              -> 09-18 에 Figure 공식 게시물로 발송됨
                                       -> 09-21 에 국문 기사로 또 올림(3번째)
    · 리벨리온 자본 이벤트           -> 09-18 '코스닥 상장 추진' 제외됨
                                       -> 09-21 'K-NPU 벤처자금' 으로 또 올림
  사용자 원문: *"이렇게 반복으로 넣는것은 선별 알고리즘이 전혀 업데이트가
  안되고 있다고 생각할수 밖에 없어"*

[원인 셋 — 전부 판단 이전의 문제였다]
  (1) **제외 이력이 어디에도 없다.** hist 는 '발송된 것'만 본다. 발송 전에
      사용자가 뺀 건은 구조적으로 안 남는다. 게다가 09-18 제외 5건은
      docs/사례집.md 에도 기록되지 않았다(당일 run.sh case 미실행).
  (2) **hist 가 국문↔영문을 못 잇는다.** 09-18 'Helix 2.5: Zero-Shot
      30-Home Generalization' 과 09-21 '피규어 AI, 로봇 모델 헬릭스 2.5
      공개' 는 제목 토큰이 0개 겹쳐 '처음 나온 이야기'로 찍혔다.
  (3) hist 는 경고만 찍고 통과시킨다. 안 보면 그대로 나간다.

[무엇을 하나]
  '고' 전량을 두 가지와 대조한다.
    A. config/rejected.jsonl — 사용자가 과거에 뺀 건
    B. 과거 발송본(out/brief-*.md) 의 URL·헤드라인
  헤드라인은 config/aliases.yaml 로 국문/영문을 대표어로 바꾼 뒤 토큰화한다.
  걸리면 **종료코드 1**. 자동으로 빼지 않는다 — 사유에 한 줄 적으면 통과한다.
    · 과거 제외 건과 겹침  -> `재심:<그때와 무엇이 달라졌나>`
    · 과거 발송분과 겹침    -> `중복검토:<왜 새 사건인가>`
  근거를 못 쓰겠으면 그 건은 '고'가 아니다.

사용법
  python3 cowork/repeat_gate.py --date <날짜> --mnt <마운트>          # 검사
  python3 cowork/repeat_gate.py --log  --date <날짜> --mnt <마운트>   # 제외 기록
       stdin:  헤드라인 | url | 사유          (한 줄에 한 건, url 은 비워도 됨)
  python3 cowork/repeat_gate.py --audit --mnt <마운트>                # 기록 누락 점검
종료코드 0 정상 / 1 고쳐야 함 / 2 입력 없음
"""
import argparse, glob, json, os, re, sys, datetime

STOP = set("""
그 이 저 것 수 등 및 또 더 다 안 왜 뭐 첫 새 전 후 년 월 일 억 조 만 개 건 명 곳 위 중
기사 뉴스 단독 속보 종합 상보 특징주 인터뷰 기고 사설 칼럼 시론 포토 영상 오늘 내년 올해
ai 인공지능 the a an of to for in on and or with is are be as at by from into new news report
""".split())

# 아래는 **거의 모든 헤드라인에 나오는 말**이라 겹쳐도 '같은 사건'의 근거가 못 된다.
#   09-21 1차 시험에서 이 낱말들 때문에 '고' 33건 중 27건이 걸렸다.
#   게이트가 80% 를 걸면 쓰는 사람이 무시하게 된다 - 그게 고치려던 병이다.
GENERIC = set("""
투자 출시 공개 시장 사업 개발 확대 추진 도입 진출 강화 구축 협력 제휴 참여 합류 선정 계약
외부 내부 국산 도입 첫발 시동 복귀 주도 방침 계획 전망 관측 국면 흐름 가속 신설 영입
달러 조원 억원 규모 매출 수익 기업 회사 국내 해외 글로벌 최대 최초 최고 고도화 부상 전략
모델 플랫폼 서비스 기술 솔루션 인프라 컴퓨팅 데이터 반도체 전력 공장 로봇 에이전트 보안
상장 인수 합병 펀딩 조달 유치 발표 검토 논의 공급 수주 생산 운영 활용 지원 확보 나선다
launch launches release releases unveil unveils acquire acquires raises funding deal billion
million company startup model platform market business tech data center power robot agent
""".split())

# 어젠다 낱말은 '주체'가 아니다. 이것만 겹치는 것으로 같은 사건이라 하면 안 된다.
AGENDA_WORDS = set("데이터센터 휴머노이드 피지컬ai 모두의ai 독파모 소버린".split())

# 재집계·짜집기 신호. [2026-09-21 사용자 지시]
#   *"새로 펀딩을 받은게 아니고 과거에 받았던 사실들을 그저 짜집기한 기사기 때문에
#   새로운 내용도 없고 그렇다고 인사이트가 있는것도 아니라서 중요하지 않아.
#   짜집기한 기사라도 인사이트가 있어서 경영층에게 제공할 메세지가 있으면 괜찮은데"*
#   newfact 는 '본문에 있는 말인가'만 보지 '오늘 처음 생긴 일인가'는 안 본다.
RECAP = ["집계", "누적", "몰린", "몰렸", "결산", "돌아보", "총정리", "올해 들어", "한 해"]

# 거의 날마다 나오는 주체. 주체만 겹치는 것으로는 해명을 요구하지 않는다.
#   (agenda_gate 주석과 같은 이유: KT 다크팩토리 vs KT 공공AX 는 다른 사건이다)
MEGA = set("""google openai anthropic microsoft nvidia meta amazon apple samsungelec skhynix
kt 네이버 카카오 lg lguplus skt sk 삼성 정부 과기정통부 tesla aws alibaba huawei oracle intel
""".split())

def load_aliases(mnt):
    p = os.path.join(mnt, "config", "aliases.yaml")
    if not os.path.exists(p):
        return {}
    try:
        import yaml
        d = yaml.safe_load(open(p, encoding="utf-8")) or {}
        al = d.get("aliases") or {}
    except Exception:
        return {}
    # 긴 표기부터 치환해야 부분 치환이 먼저 먹지 않는다
    return dict(sorted(al.items(), key=lambda kv: -len(kv[0])))

def load_entities(mnt):
    p = os.path.join(mnt, "config", "entities.yaml")
    names = set()
    if os.path.exists(p):
        try:
            import yaml
            def walk(o):
                if isinstance(o, dict):
                    for v in o.values(): walk(v)
                elif isinstance(o, list):
                    for v in o: walk(v)
                elif isinstance(o, str):
                    names.add(o.strip())
            walk(yaml.safe_load(open(p, encoding="utf-8")) or {})
        except Exception:
            pass
    return names

def norm(s, al):
    s = s or ""
    for k, v in al.items():
        if k in s:
            s = s.replace(k, " %s " % v)
    return s.lower()

def toks(s, al):
    s = norm(s, al)
    out = set()
    for t in re.split(r"[^0-9A-Za-z가-힣\.]+", s):
        t = t.strip(".")
        if len(t) < 2 or t in STOP:
            continue
        if t.isdigit() and len(t) < 3:   # 1,2 같은 순번은 버린다
            continue
        out.add(t)
    return out

def norm_url(u):
    """URL 정규화. **쿼리는 떼지 않는다** — idxno=NNN 처럼 쿼리에 기사 번호가
    들어가는 국내 매체가 많아, 떼면 서로 다른 기사가 같은 URL 이 된다
    (09-21 1차 시험에서 aitimes.kr 기사 전부가 'URL 동일'로 잘못 걸렸다)."""
    u = (u or "").strip()
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"[?&]utm_[^&]*", "", u)
    return u.rstrip("/").lower()

def load_cand(day):
    cand = {}
    p = "out/prompt-%s.txt" % day
    if not os.path.exists(p):
        return cand
    for line in open(p, encoding="utf-8"):
        if line.startswith('{"idx"'):
            o = json.loads(line); cand[str(o["idx"])] = o
        elif line.startswith('{"nolink_idx"'):
            o = json.loads(line); cand["N%s" % o["nolink_idx"]] = o
    return cand

def load_rejected(mnt):
    p = os.path.join(mnt, "config", "rejected.jsonl")
    rows = []
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line:
                try: rows.append(json.loads(line))
                except Exception: pass
    return rows

def load_sent(mnt, today, days=30):
    """과거 발송본에서 (날짜, 헤드라인, url) 을 모은다. 오늘치는 뺀다."""
    out = []
    try:
        floor = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).isoformat()
    except Exception:
        floor = "0000-00-00"
    for p in sorted(glob.glob(os.path.join(mnt, "out", "brief-*.md"))):
        m = re.search(r"brief-(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        if not m or m.group(1) >= today or m.group(1) < floor:
            continue
        day = m.group(1)
        try: txt = open(p, encoding="utf-8").read()
        except Exception: continue
        for mm in re.finditer(r"^\*\*\d+\\?\.\s*(.+?)\*\*\s*\(([^,]+),\s*\[원문보기\]\((https?://[^)]+)\)", txt, re.M):
            out.append((day, mm.group(1).strip(), mm.group(3)))
    return out

def overlap(a, b, ents):
    """(주체 겹침, 구체 내용 겹침) — 일반어·어젠다 낱말은 세지 않는다."""
    sh = (a & b) - GENERIC - AGENDA_WORDS
    e = {t for t in sh if t in ents}
    c = sh - e
    return e, c

def same_event(T, RT, url, ru, ents):
    """같은 사건인가. URL 이 같거나, **주체 1개 + 구체 낱말이 더 있어** 총 2개 이상 겹칠 때."""
    if url and ru and url == ru:
        return True, "URL 동일"
    e, c = overlap(T, RT, ents)
    if e and len(e | c) >= 2:
        return True, ", ".join(sorted(e | c)[:3])
    return False, ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--mnt", required=True)
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    mnt = a.mnt
    al = load_aliases(mnt)
    ents_raw = load_entities(mnt)
    ENT = set()
    for n in ents_raw:
        ENT |= toks(n, al)
    ENT |= {v for v in al.values() if v not in AGENDA_WORDS}
    ENT -= GENERIC

    # ---------------- 제외 기록 ----------------
    if a.log:
        p = os.path.join(mnt, "config", "rejected.jsonl")
        n = 0
        with open(p, "a", encoding="utf-8") as f:
            for line in sys.stdin:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [x.strip() for x in line.split("|")]
                head = parts[0] if parts else ""
                if not head:
                    continue
                url = parts[1] if len(parts) > 1 else ""
                reason = parts[2] if len(parts) > 2 else ""
                f.write(json.dumps({"date": a.date, "headline": head,
                                    "url": norm_url(url), "reason": reason},
                                   ensure_ascii=False) + "\n")
                n += 1
        print("[repeat] 제외 %d건 기록 -> %s" % (n, p))
        return 0

    # ---------------- 기록 누락 점검 ----------------
    if a.audit:
        rej = load_rejected(mnt)
        logged = {r.get("date") for r in rej}
        bad = []
        for p in sorted(glob.glob(os.path.join(mnt, "out", "verdict-*.csv")))[-8:]:
            m = re.search(r"verdict-(\d{4}-\d{2}-\d{2})\.csv$", p)
            if not m: continue
            day = m.group(1)
            bp = os.path.join(mnt, "out", "brief-%s.md" % day)
            if not os.path.exists(bp): continue
            import csv
            cand = {}
            pp = os.path.join(mnt, "out", "prompt-%s.txt" % day)
            if not os.path.exists(pp): continue
            for line in open(pp, encoding="utf-8"):
                if line.startswith('{"idx"'):
                    o = json.loads(line); cand[str(o["idx"])] = o
                elif line.startswith('{"nolink_idx"'):
                    o = json.loads(line); cand["N%s" % o["nolink_idx"]] = o
            urls = {norm_url(u) for u in re.findall(r"\]\((https?://[^)]+)\)",
                                                    open(bp, encoding="utf-8").read())}
            gos = [r for r in csv.DictReader(open(p, encoding="utf-8"))
                   if r["grade"].strip() == "고" and (r.get("rank") or "").strip().isdigit()]
            # [2026-10-07] verdict-xlsx 가 rank 열을 모델 순위로 덮어쓰므로,
            #   발송본을 고른 기준인 편집 순위(handrank)가 있으면 그것으로 비교한다.
            #   모델 순위로 비교하면 분량 컷(편집 순위 하위)을 '사용자 제외'로 오인한다
            #   (10-06: 모델 rank 9·12·14·15·18 = 편집 19~25위, 사용자 제외 0건).
            hp = os.path.join(mnt, "out", "handrank-%s.csv" % day)
            if os.path.exists(hp):
                hr = {}
                for h in csv.DictReader(open(hp, encoding="utf-8")):
                    v = (h.get("발송순위") or "").strip()
                    if v.isdigit():
                        hr[(h.get("idx") or "").strip()] = v
                for r in gos:
                    if r["idx"].strip() in hr:
                        r["rank"] = hr[r["idx"].strip()]
            gos.sort(key=lambda r: int(r["rank"]))
            cut = len(urls)
            miss = [r for r in gos[:cut] if norm_url(cand.get(r["idx"].strip(), {}).get("url", "")) not in urls]
            if miss and day not in logged:
                bad.append((day, [(r["rank"], (cand.get(r["idx"].strip(), {}).get("headline") or "")[:44]) for r in miss]))
        if not bad:
            print("[repeat] 제외 기록 누락 없음")
            return 0
        print("[repeat] **제외 기록이 없는 날 %d일** — 그날 뺀 건이 내일 또 올라옵니다" % len(bad))
        for day, items in bad:
            print("   %s — 상위 rank 인데 발송본에 없는 건 %d개" % (day, len(items)))
            for rk, h in items[:6]:
                print("      rank%-3s %s" % (rk, h))
        print("[repeat] `bash cowork/run.sh reject-log <날짜>` 로 기록하세요.")
        return 1

    # ---------------- 본 검사 ----------------
    day = a.date
    import csv
    vp = "out/verdict-%s.csv" % day
    if not os.path.exists(vp):
        print("[repeat] verdict 파일 없음"); return 2
    cand = load_cand(day)
    gos = [r for r in csv.DictReader(open(vp, encoding="utf-8")) if r["grade"].strip() == "고"]
    rej = load_rejected(mnt)
    sent = load_sent(mnt, day, a.days)
    today = datetime.date.fromisoformat(day)

    rej_t = [(r, toks(r.get("headline", ""), al), norm_url(r.get("url", ""))) for r in rej]
    sent_t = [(d, h, norm_url(u), toks(h, al)) for d, h, u in sent]

    fail = []
    for r in gos:
        i = r["idx"].strip()
        o = cand.get(i, {})
        head = o.get("headline") or ""
        url = norm_url(o.get("url") or "")
        reason = r.get("reason") or ""
        T = toks(head, al)

        # A. 과거 사용자 제외 건
        for rr, RT, RU in rej_t:
            if rr.get("date") == day:
                continue
            hard, why = same_event(T, RT, url, RU, ENT)
            e, _ = overlap(T, RT, ENT)
            soft = (not hard) and bool(e - MEGA)
            if soft:
                try:
                    if (today - datetime.date.fromisoformat(rr.get("date", "1900-01-01"))).days > 14:
                        soft = False
                except Exception:
                    soft = False
            if (hard or soft) and "재심:" not in reason:
                fail.append(("과거제외", i, head[:46],
                             "%s 에 뺀 '%s' 와 %s" % (rr.get("date"), rr.get("headline", "")[:34],
                                                    "같은 사건" if hard else "같은 주체"),
                             "재심:"))
                break

        # B. 과거 발송분 중복
        if any(f[1] == i for f in fail):
            continue
        # [2026-10-21 개정] 종전에는 오래된 발송분부터 훑다 **첫 일치에서 멈춰** 그 하나만 보여줬다.
        #   10-21 '뮤즈 원더'는 09-22 '뮤즈 앱스토어 1위'(meta·뮤즈)가 대표로 찍히고, 같은 사건인
        #   10-13 '뮤즈 원더 공개'(meta·뮤즈·원더)는 화면에 안 나와 약한 일치만 반박하고 통과됐다.
        #   -> 일치를 전부 모아 **겹침 강도(URL 동일 > 겹친 낱말 수) -> 최신순**으로 정렬해 상위 3건을 보이고,
        #      `중복검토:` 해명에 **가장 강한 일치의 발송일(MM-DD)** 이 들어 있어야 통과시킨다.
        hits = []
        for d, h, u, HT in sent_t:
            hit, why = same_event(T, HT, url, u, ENT)
            if hit:
                e, c = overlap(T, HT, ENT)
                strength = 99 if why == "URL 동일" else len(e | c)
                hits.append((strength, d, h, why))
        if hits:
            hits.sort(key=lambda x: (x[0], x[1]), reverse=True)
            top = hits[0]
            mmdd = top[1][5:]
            ok = "재심:" in reason or ("중복검토:" in reason and mmdd in reason.split("중복검토:", 1)[1])
            if not ok:
                lines = ["%s 발송 '%s' 와 겹침(%s)%s" % (d, h[:34], why, "  <- 가장 강한 일치" if k == 0 else "")
                         for k, (_s, d, h, why) in enumerate(hits[:3])]
                if len(hits) > 3:
                    lines.append("... 외 %d건" % (len(hits) - 3))
                need = "중복검토:<%s 발송분과 왜 다른 사건인가 — 그 날짜(%s)를 적을 것>" % (mmdd, mmdd)
                fail.append(("중복발송", i, head[:46], "\n        ".join(lines), need))

        # C. 재집계·짜집기 기사 — 본문 사실이 과거에 일어난 것이면 '고'가 아니다
        if not any(f[1] == i for f in fail):
            if any(w in head for w in RECAP) and "인사이트:" not in reason:
                fail.append(("재집계", i, head[:46],
                             "이미 일어난 사실을 모아 놓은 기사로 보입니다",
                             "인사이트:"))

    # [2026-09-22] 걸린 건을 out/signals-<날짜>.json 에 남긴다. 화면 로그만으로는
    #   "대표님이 뺀 건에 어떤 게이트가 떠 있었나"를 나중에 셀 수 없다.
    #   게이트는 전부 cwd=작업본 에서 돌므로 base 는 "." 로 충분하다.
    try:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.join(_o.getcwd(), "cowork"))
        import signals as _sig
        _sig.add_gate(".", day, "repeat", [(i, kind) for kind, i, _h, _w, _t in fail])
    except Exception as _e:
        print("[repeat] [경고] 신호 기록 실패: %s" % _e)
    print("[repeat] '고' %d건 점검 — 과거제외 %d건 / 과거발송 %d건 대조"
          % (len(gos), len(rej), len(sent)))
    if not fail:
        print("[repeat] 걸린 항목 없음")
        return 0
    print("[repeat] **해명이 필요한 %d건**" % len(fail))
    for kind, i, head, why, tag in fail:
        print("   [%s] %s  %s" % (kind, i, head))
        print("        %s" % why)
        print("        -> 사유에 `%s` 을 적으세요" % (tag if "<" in tag else tag + "<한 줄>"))
    print("[repeat] 자동으로 빼지 않습니다. **왜 새 사건인지 한 줄 쓰면 통과합니다.**")
    print("[repeat] 근거를 못 쓰겠으면 그 건은 '고' 가 아닙니다.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
