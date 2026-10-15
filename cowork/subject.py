# -*- coding: utf-8 -*-
"""주체 체급 — 같은 사건이라도 누가 하느냐로 임팩트가 갈린다 (2026-09-15 신설)

왜 만들었나
-----------
2026-09-15 편집자가 같은 날 '신규 출시' 두 건을 서로 반대로 처리했다.

| | 편집자 판단 | 이유(원문) |
|---|---|---|
| 한컴, 세계 최초 AI 워크포스 플랫폼 '노마디안' 출시 | **제외** | *"세계 최초라는 말은 있지만 만든 회사가 한컴이라 AI 업계에서 유명한 회사가 아니기 때문에 임팩트가 적다. AI 에이전트가 붙었다고 무조건 기사로 넣으면 안 된다"* |
| 오픈AI, 휴머노이드 로봇 직접 만든다 | **유지** | *"세계 제1~2위 프런티어 AI 기업이 하는 상징성이 있다"* |

그때 나는 "이건 기계로 못 막는다"고 답했고, 사용자가 되물었다 —
*"기사의 주체 관점에서 충분히 판단할 수 있는 거 아냐? 오픈AI와 한컴이 같은
가중치를 가지지는 않을 것 아냐. 프론티어 AI 기업, 빅테크, 경쟁사, 대기업,
유망스타트업, 그외 일반 기업의 가중치가 같아?"*

**맞다. 체급은 판단이 아니라 상태값이라 표로 둘 수 있다.**
`config/entities.yaml` 이 이미 있었지만 **올려 잡는 명단만** 있었다
(국가사업참여·유명스타트업·SKT 투자사). 프런티어·빅테크·경쟁사 등급이 없었고,
'그 밖'을 표시할 방법도 없어서 한컴이 그대로 통과했다.

정본은 `config/entities.yaml` 의 `tiers:` 블록 하나다. 여기에 명단을
복붙하지 말 것 — [[dtb-single-source-of-settings]].

체급을 쓰는 법 — 등급이 아니라 **기울기**
------------------------------------------
체급은 '고/중/저'를 정하지 않는다. **같은 사건 유형끼리 비교할 때 기울기**를 준다.

    프런티어  : 진출·검토 선언만으로도 고 (상징성 자체가 사건)
    빅테크    : 출시·인수는 고, 기능 추가는 중
    경쟁사    : 신규 서비스·전략·수주는 고 (사업현황 7절)
    대기업    : 사건의 크기로 판단. 이름값으로 올리지 않는다
    유망스타트업: 기술·자금 규모가 실체면 고
    일반      : **'세계 최초'·'국내 최초' 수식어로 올리지 않는다.**
                  파급이 본문에 숫자로 드러나야 검토 대상

**`체급?`(명단에 없음)은 '낮음'이 아니다.** 2026-08-27 지시를 그대로 지킨다 —
*"명단에 없다고 '무명'으로 확정하지 말 것. 명단은 올려 잡는 근거이지 내려
잡는 근거가 아니다."* `체급?` 이 뜨면 **그 회사가 이 바닥에서 누구인지 한 번
묻는다.** 그 질문을 안 한 것이 한컴 건의 실제 원인이었다.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ORDER = ["프런티어", "빅테크", "경쟁사", "대기업", "유망스타트업", "일반"]

_CACHE = None


def _load():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    import yaml
    p = os.path.join(ROOT, "config", "entities.yaml")
    d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    tiers = d.get("tiers") or {}
    table = []          # (정규화이름, 체급)
    for t in ORDER:
        for name in (tiers.get(t) or []):
            table.append((_norm(name), t))
    # 기존 명단 재사용 — 유망스타트업은 startups 블록이 정본이다.
    for names in (d.get("startups") or {}).values():
        for name in names or []:
            table.append((_norm(name), "유망스타트업"))
    for grp in (d.get("govt_projects") or {}).values():
        for key in ("주관", "관련기업"):
            for name in (grp or {}).get(key) or []:
                table.append((_norm(name), "유망스타트업"))
    # 긴 이름부터 맞춰야 '네이버클라우드' 가 '네이버' 로 먼저 안 잡힌다.
    table.sort(key=lambda x: -len(x[0]))
    _CACHE = table
    return table


def _norm(s):
    return re.sub(r"[\s·\-_.]+", "", (s or "")).lower()


def tier_of(headline):
    """헤드라인에서 찾은 주체의 체급. 못 찾으면 None."""
    h = _norm(headline)
    for name, t in _load():
        if name and name in h:
            return t
    return None


def tag(headline):
    """blind 목록에 붙일 짧은 표시."""
    return tier_of(headline) or "체급?"


def main():
    args = sys.argv[1:]
    if args:
        for a in args:
            print("%-8s %s" % (tag(a), a))
        return
    # 자가진단 — 09-15 두 건이 제대로 갈리는가
    cases = [
        ("오픈AI, 휴머노이드 로봇 직접 만든다…올트먼 \"누구나 개인용 로봇 가져야\"", "프런티어"),
        ("AI 에이전트로 '팀' 구성…한컴, 세계 최초 AI 워크포스 플랫폼 '노마디안' 출시", "일반"),
        ("애플, 차세대 인텔리전스 결합한 iOS 27 공개…한국어 10월 지원", "빅테크"),
        ("카카오페이, AI 구매 넘어 판매까지…에이전트 AI 결제 생태계 구축", "경쟁사"),
        ("KT클라우드, 2031년까지 20여곳에 1GW 이상 AI 데이터센터 공급", "경쟁사"),
        ("\"국내 최초 앤트로픽 품어\"…삼성SDS, 원스톱AI 도입 체계 강화", "대기업"),
        ("유니트리, 휴머노이드 '범용 두뇌' 공개…60억 파라미터 모델 오픈소스화", "유망스타트업"),
    ]
    bad = 0
    for h, want in cases:
        got = tag(h)
        ok = "OK  " if got == want else "틀림"
        if got != want:
            bad += 1
        print("[%s] %-12s (기대 %-12s) %s" % (ok, got, want, h[:46]))
    print("\n[subject] 체급 명단: %d개 이름" % len(_load()))
    if bad:
        print("[subject] **%d건이 기대와 다릅니다. entities.yaml tiers 를 고치세요.**" % bad)
        sys.exit(1)
    print("[subject] 자가진단 통과")


if __name__ == "__main__":
    main()
