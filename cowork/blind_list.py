#!/usr/bin/env python3
"""후보 전수 판정용 '점수 가림' 목록을 만든다.

[2026-09-01 사용자 지시]
  "기계는 553건을 뽑는 역할만 하고, 553건에서 고/중/저 판단은
   기계 채점 결과 없이 온전히 니가 했으면 하거든."

왜 필요한가:
  기존 프롬프트의 후보 목록은 score/signals 를 그대로 보여주고 **점수
  내림차순**으로 나열된다. 그 상태로 판정하면 점수에 닻이 내려서, 실측상
  하위 구간을 사실상 안 보게 된다(08-11: 30건이 차서 멈춰 정답지 4건을
  판정조차 안 함 / 08-31: 놓친 5건의 점수가 3·5·5·7점).
  사람 정답지의 45%가 9점 이하인데 기계 순위를 따라가면 그 절반을 놓친다.

무엇을 가리나:
  - score            : 완전 제거
  - signals          : 완전 제거 (딜+3·보도수+3·사람큐레이션+5 가 점수를 그대로 노출)
  - 섹션 이름         : [주 후보]/[구제 후보]/[링크 미확보] 구분 제거
                       ('구제'라는 이름 자체가 '점수가 낮다'는 뜻이다)
  - 정렬             : 점수 내림차순 -> **통로 그룹 + 발행일** 순으로 재배치

무엇을 남기나(판정에 실제로 쓰는 사실만):
  - 통로  : tier0 은 전량 검토 의무가 있고, 삼성/NewsClips 는 사람 큐레이션이라
            '검토 우선순위'가 다르다. 채택 여부를 정하는 값이 아니라 **훑는
            순서**를 정하는 값이므로 남긴다.
  - 매체  : F7(언론사 자체 기사는 기본 중), E(사람이 반복해서 고른 매체) 판정용
  - 발행일: G(신규성) 판정용
  - 보도수: **구간으로만** 표시(단독/소수/다수). 원래 보도수+3 은 채점 신호지만,
            '몇 개 매체가 받았나'는 사건 크기를 가리키는 사실이기도 하다.
            숫자를 그대로 주면 점수가 새므로 3구간으로 뭉갠다.
  - 중복의심: dup_suspect 는 판정에 필요한 정보라 남긴다.
  - 링크유무: 링크가 없으면 실을 수 없으므로 표시한다.

쓰는 법:
  bash cowork/run.sh blind            # out/candidates-blind-<날짜>.txt 생성
  -> 이 파일만 보고 out/verdict-<날짜>.csv (idx,grade,reason,rank) 를 채운다
     rank 는 '고' 에만 1부터 매긴다. 등급을 매길 때 쓴 사유를 그대로
     순위 가중치로 쓰라는 뜻이다(2026-09-03 사용자 지시. brief.py 2단계 참조).
  -> bash cowork/run.sh verdict       # 판정 검증 + 30건 후보 산출
"""
import argparse
import datetime
import hashlib
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")

# 통로 그룹: 훑는 순서를 정한다. tier0(사람·봇이 거른 입력)을 맨 앞에 둔다.
# 종전에는 점수 상위가 맨 앞이었고, 그래서 tier0 하위 구간이 늘 마지막에 왔다.
GROUP_ORDER = ["경쟁사속보", "반향후보", "tier0-사람큐레이션", "tier0-봇큐레이션", "tier0+교차",
               "커뮤니티", "tier1", "tier2", "링크미확보"]
# [2026-09-23] '반향후보' = buzz.py 가 잡은 낱말이 붙은 건(refine 의 반향+3 신호).
#   '커뮤니티' = HN·GitHub·HF 에서 온 건. 둘 다 점수가 아니라 **통로**라 blind 원칙에 맞다.


def group_of(d):
    tier = d.get("tier") or ""
    sig = " ".join(d.get("signals") or [])
    # [2026-10-27] run.sh late 가 붙인 오전 수집 이후 경쟁사 속보. 맨 앞에서 본다.
    if (d.get("tier") or "") == "late":
        return "경쟁사속보"
    if "반향+3(" in sig:
        return "반향후보"
    if d.get("_nolink"):
        return "링크미확보"
    if "tier0" in tier:
        # 삼성·NewsClips 는 사람이 직접 고른 입력이다.
        if "사람큐레이션" in sig:
            return "tier0-사람큐레이션"
        if (d.get("outlet") or "") in ("HN", "GitHub", "HuggingFace"):
            return "커뮤니티"
        if "+" in tier:          # tier0 이 tier1/2 와 교차한 건
            return "tier0+교차"
        return "tier0-봇큐레이션"
    if "tier1" in tier:
        return "tier1"
    return "tier2"


def _subject_tag(headline):
    """주체 체급 표시. subject.py 가 정본이고 여기서는 부르기만 한다."""
    try:
        from subject import tag
    except Exception:
        try:
            import importlib.util as _i
            _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subject.py")
            _s = _i.spec_from_file_location("subject", _p)
            _m = _i.module_from_spec(_s)
            _s.loader.exec_module(_m)
            return _m.tag(headline)
        except Exception:
            return "체급?"
    return tag(headline)


def outlets_band(n):
    """보도수를 3구간으로 뭉갠다. 숫자를 그대로 주면 점수가 샌다."""
    n = n or 0
    if n >= 6:
        return "다수"
    if n >= 2:
        return "소수"
    return "단독"


def load_candidates(day):
    path = os.path.join(OUT, "prompt-%s.txt" % day)
    if not os.path.exists(path):
        raise SystemExit("[blind] %s 없음. brief/extend 를 먼저 돌리세요." % path)
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith('{"idx"'):
            d = json.loads(line)
            d["_nolink"] = False
            out.append(d)
        elif line.startswith('{"nolink_idx"'):
            d = json.loads(line)
            d["idx"] = "N%s" % d.pop("nolink_idx")
            d["_nolink"] = True
            d.setdefault("outlet", d.get("outlet_guess"))
            out.append(d)
    return out


try:
    from agenda_gate import EQUIP as _EQUIP_WORDS   # 정본은 cowork/agenda_gate.py
except Exception:
    _EQUIP_WORDS = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()
    cands = load_candidates(a.date)

    # 그룹 안에서도 점수 순서가 남지 않도록 날짜 기반 고정 해시로 섞는다.
    # (매일 같은 순서를 쓰면 특정 위치가 계속 뒤로 밀린다. 재현은 가능해야
    #  하므로 난수가 아니라 날짜+idx 해시를 쓴다.)
    def sort_key(d):
        g = group_of(d)
        h = hashlib.md5(("%s|%s" % (a.date, d["idx"])).encode()).hexdigest()
        return (GROUP_ORDER.index(g), d.get("pub_date") or "", h)

    cands.sort(key=sort_key)

    lines = []
    lines.append("# 후보 전수 판정 목록 %s — 총 %d건" % (a.date, len(cands)))
    lines.append("# 기계 점수·신호는 **일부러 뺐습니다.** 헤드라인과 아래 사실만으로")
    lines.append("# 고/중/저를 매기세요. 판정 축은 프롬프트의 A~I 를 그대로 씁니다(I=사례집, 가장 최신).")
    lines.append("# 형식: idx | 통로 | 매체 | 발행일 | 보도 | 헤드라인")
    lines.append("#   보도 = 다수(6개 매체 이상) / 소수(2~5) / 단독(1)")
    lines.append("#   ⟨체급⟩ = 주체가 이 바닥에서 누구인가 (config/entities.yaml tiers)")
    lines.append("#     **등급이 아니라 기울기다. 같은 사건 유형끼리 비교할 때만 쓴다.**")
    lines.append("#       프런티어    : 진출·검토 선언만으로도 고 (상징성 자체가 사건)")
    lines.append("#       빅테크      : 출시·인수는 고, 기능 추가는 중")
    lines.append("#       경쟁사      : 신규 서비스·전략·수주는 고 (사업현황 7절)")
    lines.append("#       대기업      : 사건의 크기로 판단. 이름값으로 올리지 않는다")
    lines.append("#       유망스타트업: 기술·자금 규모가 실체면 고")
    lines.append("#       일반        : '세계 최초'·'국내 최초' 수식어로 올리지 않는다.")
    lines.append("#                     파급이 본문에 숫자로 드러나야 검토 대상")
    lines.append("#       체급?       : **'낮음'이 아니다.** 주체가 회사가 아니거나(정부·")
    lines.append("#                     정책·인물) 명단에 없는 것이다. 후자면 그 회사가")
    lines.append("#                     이 바닥에서 누구인지 한 번 묻고 넘어간다.")
    lines.append("#     실측 09-15: ⟨일반⟩한컴 '세계 최초 AI 워크포스'는 편집자가 뺐고,")
    lines.append("#       ⟨프런티어⟩오픈AI '휴머노이드 직접 제조'는 실었다. 같은 '신규")
    lines.append("#       출시'인데 갈린 것은 주체 체급이다.")
    lines.append("#   [기자재어휘] 는 **D축 배제(7) AI DC 기자재 납품** 후보입니다.")
    lines.append("#     발전기·변압기·전선·버스덕트·냉각·공조처럼 '데이터센터에")
    lines.append("#     납품되는 물건' 이야기면 금액이 커도 '저'입니다(F1 보다 우선).")
    lines.append("#     구분선: **무엇이 들어가나**(전력계약·부지·자금조달)는 핵심,")
    lines.append("#     **누가 물건을 파나**(기자재 납품)는 어젠다 밖.")
    lines.append("#     실측 09-17: '제네락·아마존 발전기 공급 계약'을 '빅테크가")
    lines.append("#     발전설비를 직접 조달했다'고 포장해 고로 올렸다가 편집자가 뺌.")
    lines.append("#   [반향: 낱말·매체N·D일·반응N·커뮤니티N] 은 buzz.py 가 잡은 **새 낱말의 확산**입니다.")
    lines.append("#     30일간 없던 이름이 여러 매체에 번지고 제3자 반응(돌풍·열광·오픈소스 구현")
    lines.append("#     잇따라·HN·GitHub)이 붙은 것. **점수가 아니라 사실**이고 등급이 아닙니다.")
    lines.append("#     E축 예외 (f) 커뮤니티 반향으로 판정합니다 — 주체가 무명이어도 제3자")
    lines.append("#     반응이 실체면 고. '세계 최초' 같은 자기 주장(사례29)과 가르는 선입니다.")
    lines.append("#     실측 09-23: '제브'(타입세이프AI)가 사흘간 8건 들어왔는데 무명 주체라")
    lines.append("#     저·저·중으로 걸렀고 임원이 '캐치 못해 아쉽다'고 했다.")
    lines.append("#   === 커뮤니티 === 는 HN·GitHub·HF 통로입니다. 영문·개발자 관점이라 국내")
    lines.append("#     매체보다 D+5~7 빠릅니다. 스타 속도·포인트는 signal 로 헤드라인 뒤에 붙습니다.")
    lines.append("#   [링크없음] 은 URL 을 못 찾은 건입니다. '고'로 판정하면")
    lines.append("#   WebSearch 로 원문을 찾아야 실을 수 있습니다.")
    lines.append("")
    cur = None
    for d in cands:
        g = group_of(d)
        if g != cur:
            cur = g
            n = sum(1 for x in cands if group_of(x) == g)
            lines.append("")
            lines.append("=== %s (%d건) ===" % (g, n))
        head = re.sub(r"\s+", " ", (d.get("headline") or "")).strip()
        flags = ""
        if d.get("_nolink"):
            flags += " [링크없음]"
        if d.get("dup_suspect"):
            flags += " [중복의심: %s]" % str(d["dup_suspect"])[:40]
        # [2026-09-17] D축 배제(7) 기자재 어휘를 **사실로** 표시한다.
        #   09-17 에 '제네락·아마존 발전기 공급'을 고로 올렸다. 규칙은
        #   프롬프트 366~377행에 발전기·변압기·전선까지 낱말로 있었지만
        #   그날 프롬프트를 열지 않았다. 규칙을 또 적는 대신, 판정할 때
        #   눈앞에 놓는다(⟨체급⟩ 과 같은 층위 — 점수가 아니라 사실).
        # [2026-09-23] 반향 낱말 표시(refine 의 반향+3 신호를 그대로 보여준다).
        for _s in (d.get("signals") or []):
            if _s.startswith("반향+3("):
                flags += " [반향: %s]" % _s[len("반향+3("):-1]
                break
        _h = head.replace("공공조달", "").replace("조달청", "")
        _eq = [w for w in _EQUIP_WORDS if w in _h]
        if _eq:
            flags += " [기자재어휘: %s]" % "·".join(_eq[:2])
        # [2026-09-15] 주체 체급. **점수가 아니라 사실**이라 blind 원칙에
        #   어긋나지 않는다(매체·발행일과 같은 층위). 09-15 에 한컴을
        #   '세계 최초' 수식만 보고 실었다가 편집자가 뺐다 — 사용자:
        #   "오픈AI와 한컴이 같은 가중치를 가지지는 않을 것 아냐."
        lines.append("%s | %s | %s | %s | %s | ⟨%s⟩ %s%s" % (
            d["idx"], g, (d.get("outlet") or "?")[:12],
            (d.get("pub_date") or "?")[5:], outlets_band(d.get("n_outlets")),
            _subject_tag(head), head, flags))

    path = os.path.join(OUT, "candidates-blind-%s.txt" % a.date)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("[blind] %d건 저장: %s" % (len(cands), path))
    from collections import Counter
    for g, n in sorted(Counter(group_of(d) for d in cands).items(),
                       key=lambda x: GROUP_ORDER.index(x[0])):
        print("   %-18s %4d건" % (g, n))
    print("[blind] 이제 이 파일만 보고 out/verdict-%s.csv 를 채우세요." % a.date)
    print("        형식: idx,grade,reason,rank   (grade = 고|중|저)")
    print("        rank 는 '고' 에만 1부터. 판정 사유를 그대로 순위 가중치로 쓸 것")


if __name__ == "__main__":
    main()
