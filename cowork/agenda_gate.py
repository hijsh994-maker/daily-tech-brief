# -*- coding: utf-8 -*-
"""[2026-09-17 신설] 어젠다 밖인데 '고' 로 올라온 건을 기계로 잡는다.

왜 만들었나 — 09-17 에 편집자가 뺀 2건이 **둘 다 이미 있던 규칙**이었다.
  (1) 제네락·아마존 발전기 공급  -> D축 배제(7) 'AI DC 기자재 납품'.
      규칙은 프롬프트 366~377행에 발전기·변압기·전선까지 낱말로 적혀 있었는데
      나는 그날 프롬프트를 열지 않았다. 심지어 내 판정 사유에 '발전설비를
      직접 조달한 계약'이라고 **배제 어휘를 직접 써 놓고** 올렸다.
  (2) 카카오 GPU 256장 배정      -> 09-14 지시 '단순 GPU·컴퓨팅 물량 확보는
      금액이 커도 중'. newfact 게이트는 '새 사실이 본문에 있는가'만 보고
      '그 새 사실이 중요한가'는 안 본다. 물량 수치뿐인 새 사실이 통과했다.

그래서 문서를 늘리지 않고 **해명 강제 게이트**로 만들었다(09-15 중복의심과 같은 방식).
자동으로 빼지 않는다 - 정당한 예외가 있기 때문이다.
  - 기자재 어휘가 있어도 '전력계약·요금·부지·자금조달' 이면 [핵심]이다
  - GPU 물량이라도 '국산 NPU 해외 첫 공급' 처럼 판도 기사면 고다
그러나 **사유에 한 줄 적지 않으면 통과하지 못한다.**
"""
import csv, json, os, re, sys, argparse

# D축 배제(7) 기자재 어휘. editor_rubric.py 366~377행과 같은 목록.
EQUIP = ["발전기", "발전설비", "발전 터빈", "발전엔진", "변압기", "버스덕트",
         "배전반", "차단기", "냉각탑", "항온항습", "버스바",
         "generator", "transformer", "switchgear", "busduct"]
# [2026-09-17] 아래는 **헤드라인에서만** 본다. 본문까지 훑으면 '전선(戰線)'·
#   '공조(共助)'·'철강' 같은 동음이의·부수 언급에 걸린다(회귀검사 09-15:
#   '트럼프·젠슨 황 통화' 기사가 본문의 '전선' 때문에 잡혔다).
EQUIP_HEAD_ONLY = ["전선", "케이블", "터빈", "공조", "강구조", "철강",
                   "아스콘", "레미콘", "기자재", "송전"]

# 기자재 어휘가 있어도 이쪽이면 [핵심] 이므로 게이트에서 뺀다.
EQUIP_OK = ["전력계약", "전력 계약", "요금제", "전기료", "전기 요금", "PPA", "부지",
            "인허가", "자금조달", "대출", "펀딩", "시행령", "특별법", "계통"]

# 09-14 지시: 단순 물량 확보는 금액이 커도 중.
VOLUME = [r"GPU\s*\d", r"\d+\s*장\s*(배정|확보|공급|도입)", r"(GPU|칩|컴퓨팅)\s*(물량|배정|확보|조달|구매)",
          r"H100|B200|B300|GB200", r"\d+\s*대\s*(확보|도입|구매)"]
VOLUME_OK = ["첫 공급", "해외", "수출", "국산", "상용", "판도", "구조"]

def load(day):
    cand = {}
    for line in open("out/prompt-%s.txt" % day, encoding="utf-8"):
        if line.startswith('{"idx"'):
            o = json.loads(line); cand[str(o["idx"])] = o
        elif line.startswith('{"nolink_idx"'):
            o = json.loads(line); cand["N%s" % o["nolink_idx"]] = o
    return cand

def body_of(day, idx):
    for p in ("out/bodycache-%s.json" % day,):
        if os.path.exists(p):
            try:
                b = json.load(open(p, encoding="utf-8"))
            except Exception:
                return ""
            v = b.get(str(idx)) or b.get(idx)
            if isinstance(v, dict):
                return v.get("body", "") or v.get("text", "")
            if isinstance(v, str):
                return v
    return ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    day = a.date
    path = "out/verdict-%s.csv" % day
    if not os.path.exists(path):
        print("[agenda] verdict 파일 없음"); return 2
    cand = load(day)
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r.get("idx")]
    gos = [r for r in rows if (r.get("grade") or "").strip() == "고"]

    fail = []
    # --- (1) 기자재 게이트
    for r in gos:
        i = r["idx"].strip()
        head = (cand.get(i, {}).get("headline") or "")
        reason = r.get("reason") or ""
        text = head + " " + reason + " " + body_of(day, i)[:600]
        hit = [w for w in EQUIP if w in text]
        hit += [w for w in EQUIP_HEAD_ONLY if w in head]
        if not hit:
            continue
        if any(w in text for w in EQUIP_OK) and "기자재검토:" in reason:
            continue
        if "기자재검토:" not in reason:
            fail.append(("기자재", i, head[:46], "·".join(hit[:3])))
    # --- (2) 물량 게이트
    for r in gos:
        i = r["idx"].strip()
        head = (cand.get(i, {}).get("headline") or "")
        reason = r.get("reason") or ""
        # '새사실:' 문장만 본다 - 기사의 알맹이가 물량 수치뿐인가를 묻는 것이므로
        m = re.search(r"새사실:(.*?)(?:·\s*사업현황:|$)", reason)
        newfact = m.group(1) if m else ""
        probe = head + " " + newfact
        if not any(re.search(p, probe) for p in VOLUME):
            continue
        if any(w in probe for w in VOLUME_OK):
            continue
        if "물량검토:" not in reason:
            fail.append(("물량", i, head[:46], "새 사실이 물량 수치뿐"))
    # --- (3) 같은 날 같은 주체 2건 게이트
    def orgs(s):
        return set(re.findall(r"[가-힣A-Za-z]{2,}(?=[,·\s]|$)", s))
    KNOWN = ["카카오", "네이버", "KT", "LG", "SKT", "SK텔레콤", "LGU+", "LG유플러스",
             "삼성SDS", "삼성전자", "오픈AI", "앤트로픽", "구글", "메타", "엔비디아",
             "애플", "아마존", "MS", "마이크로소프트", "세일즈포스"]
    # 주체만 겹치는 것으로는 안 잡는다 - KT 다크팩토리 vs 공공AX 처럼
    # 같은 회사의 다른 사건이 흔하다(09-15 역전 게이트가 분류기 오류를 재던 실패).
    # **주체 + 사업/어젠다 낱말이 함께 겹칠 때만** 해명을 요구한다.
    AGENDA = ["모두의 AI", "모두의AI", "독파모", "AIDC", "데이터센터", "휴머노이드",
              "피지컬AI", "피지컬 AI", "에이전트", "다크팩토리", "공공 AX", "공공AX",
              "IPO", "인적분할", "자체 칩", "로봇", "신약", "광고"]
    bysub = {}
    for r in gos:
        i = r["idx"].strip()
        head = (cand.get(i, {}).get("headline") or "")
        for k in KNOWN:
            if k in head:
                ag = frozenset(w for w in AGENDA if w in head)
                bysub.setdefault(k, []).append((i, head, ag))
    for k, lst in sorted(bysub.items()):
        if len(lst) < 2:
            continue
        for a in range(len(lst)):
            for b in range(a + 1, len(lst)):
                shared = lst[a][2] & lst[b][2]
                if not shared:
                    continue
                pair = [lst[a][0], lst[b][0]]
                un = [i for i in pair
                      if "동일주체:" not in (next(r for r in gos if r["idx"].strip() == i).get("reason") or "")]
                if len(un) == len(pair):
                    fail.append(("동일주체", ",".join(pair), "%s + %s" % (k, "·".join(sorted(shared))),
                                 "같은 주체·같은 사업 2건이 모두 '고'"))

    # [2026-09-22] 걸린 건을 out/signals-<날짜>.json 에 남긴다. 화면 로그만으로는
    #   "대표님이 뺀 건에 어떤 게이트가 떠 있었나"를 나중에 셀 수 없다.
    #   게이트는 전부 cwd=작업본 에서 돌므로 base 는 "." 로 충분하다.
    try:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.join(_o.getcwd(), "cowork"))
        import signals as _sig
        _sig.add_gate(".", day, "agenda", [(i, kind) for kind, i, _h, _w in fail])
    except Exception as _e:
        print("[agenda] [경고] 신호 기록 실패: %s" % _e)
    print("[agenda] '고' %d건 점검 — 기자재/물량/동일주체" % len(gos))
    if not fail:
        print("[agenda] 걸린 항목 없음")
        return 0
    print("[agenda] **해명이 필요한 %d건**" % len(fail))
    tagmap = {"기자재": "기자재검토:", "물량": "물량검토:", "동일주체": "동일주체:"}
    for kind, i, head, why in fail:
        print("   [%s] %s  %s" % (kind, i, head))
        print("        %s  -> 사유에 `%s<한 줄>` 을 적으세요" % (why, tagmap[kind]))
    print("[agenda] 자동으로 빼지 않습니다. **어젠다 안이라는 근거를 사유에 남기면 통과합니다.**")
    print("[agenda] 근거를 못 쓰겠으면 그 건은 '고' 가 아닙니다.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
