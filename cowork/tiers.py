# -*- coding: utf-8 -*-
"""tiers.py - 소스명 -> tier 매핑의 단일 정본.

## 왜 만들었나 (2026-08-25)
종전에는 이 매핑이 report.py / persona.py / brief.py 세 곳에 각각
하드코딩돼 있었고, 셋 다 **화이트리스트**였다:

    TIER_OF = {"slack": "tier0", "adot": "tier0", "samsung": "tier0",
               "newsclips": "tier0", "rss": "tier1", "gnews": "tier2"}

여기 없는 소스명은 tier 를 못 얻고, 호출부가 `if not tiers: continue`
로 그 항목을 통째로 버렸다. **조용히 버린다는 게 문제다.**

실측 2026-08-25: `curated`(옛 '#개인테스트 Daily AI Tech Trends')가
세 파일 어디에도 없었다. tier0-*.json 에 137건이 남아 있는데 전부
tier 없음으로 처리돼, 과거 날짜를 재처리하면 tier0 이 통째로 사라진다.
07-23 정답지 'How OpenAI's human mistake led to the AI-powered hack on
Hugging Face' 는 tier0 에 **URL 까지 완전히 같은** 항목이 있는데도
E열이 tier1+tier2 로 찍혔다.

## 그래서 방향을 뒤집었다
tier1 과 tier2 는 소스가 각각 하나뿐이고 앞으로 늘 일이 없다
(rss=collect.py, gnews=tier2_par.py). 반면 **tier0 입력 채널은 계속
바뀐다** - 08-22 에도 슬랙 채널이 #개인테스트에서 #dtb-tier0 으로
옮겼고, curated 가 빠지고 newsclips 가 들어왔다.

따라서 tier0 을 나열하지 않는다. **rss/gnews 가 아니면 전부 tier0.**
새 tier0 소스가 생겨도 코드를 고칠 필요가 없고, 무엇보다
'모르는 소스라서 조용히 사라지는' 실패가 구조적으로 불가능해진다.
tier0 유실은 이 파이프라인에서 가장 비싼 오류다(정답지의 58~80%가
tier0 통로로 들어온다).
"""

# tier1/tier2 는 소스가 고정이라 명시한다. 그 밖은 전부 tier0.
_EXPLICIT = {"rss": "tier1", "gnews": "tier2"}

TIER_ORDER = ("tier0", "tier1", "tier2")

# 관측된 적 있는 tier0 소스명. 판정에는 쓰지 않고, 처음 보는 이름이
# 나왔을 때 알려주기 위한 참고 목록이다.
KNOWN_TIER0 = {"slack", "adot", "samsung", "newsclips", "curated",
               "aibrief",   # aibrief: 08시 AI 뉴스 큐레이션 (2026-09-09)
               "community"}  # community: HN·GitHub·HF (2026-09-23, cowork/community.py)

_seen_new = set()


def tier_of(src, warn=True):
    """소스명 하나를 tier 로 접는다. 빈 값이면 None."""
    if not src:
        return None
    s = str(src).strip().lower()
    if s in _EXPLICIT:
        return _EXPLICIT[s]
    if warn and s not in KNOWN_TIER0 and s not in _seen_new:
        _seen_new.add(s)
        print("[tiers] 처음 보는 소스명 %r -> tier0 으로 처리합니다. "
              "새 입력 채널이면 KNOWN_TIER0 에 추가해 두세요." % s)
    return "tier0"


def tiers_of(srcs, warn=True):
    """소스명 목록을 tier 집합으로."""
    out = set()
    for s in (srcs or []):
        t = tier_of(s, warn=warn)
        if t:
            out.add(t)
    return out


def label(tiers):
    """tier 집합 -> 'tier0+tier2' 같은 표시 문자열."""
    return "+".join(t for t in TIER_ORDER if t in (tiers or set()))
