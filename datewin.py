# -*- coding: utf-8 -*-
"""datewin.py - 발행일 창(window) 계산을 한 곳으로 모은다.

## 왜 있나
date_window_days 를 읽는 곳이 5군데(ingest_tier2 / refine x2 / tier2_par /
tier2_resume)로 흩어져 있어, 요일 보정 같은 규칙을 넣으면 한 군데만 고치고
나머지를 놓치기 쉽다. 계산을 여기 하나로 모은다.

## 규칙 (2026-08-18 사용자 지시로 교체)
기본은 policy.date_window_days(=1). 여기에 **공휴일 보정**만 얹는다.

  트리거: 실행일의 **전날이 한국 공휴일이거나 일요일**이면 창을 늘린다.
          (2026-08-18 2차 지시로 일요일을 추가했다. 일요일이 트리거되면
           평일 월요일이 자동으로 3일이 되어 종전 월요일 특례를 대신한다.)
  크기  : 트리거되면 **직전 영업일까지** 거슬러 올라가 창을 잡는다.
          쉰 구간을 되짚을 때는 주말도 함께 건너뛴다. 연휴 사이에
          낀 토·일을 안 넘으면 거기서 창이 끊긴다.
          단 사용자가 요청한 최소값 3일은 보장한다.

      window = max(3, (실행일 - 직전 영업일).days)

  전날만 공휴일(앞뒤가 평일)  -> 3
  평일 월요일(전날 일요일)     -> 3   [금, 토, 일, 월]
  광복절 연휴(금 영업 후 토·일·월 휴무) -> 4
  설·추석 연휴(수 실행)        -> 6

  **왜 3일 고정이 아니라 '최소 3일'인가.** 연휴가 주말에 붙으면 쉰
  구간이 3일을 넘는다. 08-18 이 그랬다(08-14 금 발송 없음 + 08-15
  광복절 + 08-16 일 + 08-17 대체공휴일). 3일 고정이면 창이 08-15 까지만
  닿아 08-14 기사 3건을 여전히 놓친다. 실제로 그날 놓친 5건 중 3건이
  08-14 발행분이었다.

  토요일은 트리거에 넣지 않았다. 전날이 토요일이면 실행일이 일요일인데
  그날은 브리프를 내지 않으므로 실질적으로 같은 규칙이다.

'직전 영업일'까지 덮는 이유: 그날 저녁에 나온 기사는 그날 아침 브리프에
못 실렸고, 이후 브리프가 없었으므로 다음 브리프가 주워야 한다.
이 계산은 종전 '월요일=3일' 특례와도 값이 같다(월 - 금 = 3일).

### 종전 '월요일=3일' 특례는 삭제했다
2026-08-03 에 넣었던 요일 기반 보정(date_window_days_by_weekday)을
사용자 지시로 뺐다. 판단 기준을 요일이 아니라 '실제로 쉬었는가'로 옮긴 것이다.
config 에 date_window_days_by_weekday 가 남아 있어도 **읽지 않는다**.
(평일 월요일은 창이 1일이 되어 금·토·일 기사를 안 본다. 의도된 동작이다.)

## 왜 공휴일 기준인가
2026-08-18(화) 실측. 전날 08-17 이 광복절 대체공휴일이라 사람은 그날 쉬고,
08-18 브리프에 08-14~15 기사까지 실었다. 그런데 창이 1일이라 후보에
아예 못 올라와 정답지 17건 중 5건을 구조적으로 놓쳤다
(Grok 4.6 깃허브 코파일럿, GLM-5.3 공개 보류, 앤트로픽 Model 2,
 씨티그룹 2.8조달러 전망, 알리바바 30억 다운로드).

## 공휴일 판정
`holidays` 패키지(KR)를 쓴다. 음력 기반(설·추석·부처님오신날)과
대체공휴일까지 계산해 주므로 표를 손으로 관리하지 않는다.
패키지가 없으면 아래 붙박이 표로 물러서고 **경고를 찍는다**.
조용히 base 로 떨어지면 연휴 다음날 브리프가 소리 없이 반쪽이 된다.
"""
import datetime
import sys

# holidays 패키지가 없을 때 쓰는 최소 표(양력 고정일만).
# 음력 기반(설·추석·부처님오신날)과 대체공휴일은 여기 없다.
# 어디까지나 비상용이라, 이 표가 쓰이면 경고를 찍어 사람이 알게 한다.
_FALLBACK_FIXED = {
    (1, 1): "신정",
    (3, 1): "삼일절",
    (5, 5): "어린이날",
    (6, 6): "현충일",
    (8, 15): "광복절",
    (10, 3): "개천절",
    (10, 9): "한글날",
    (12, 25): "성탄절",
}

_kr_cache = {}
_warned = [False]


def _kr_holidays(year):
    """그 해의 한국 공휴일 집합(date). 실패하면 붙박이 표로 물러선다."""
    if year in _kr_cache:
        return _kr_cache[year]
    got = None
    try:
        import holidays as _h
        got = set(_h.country_holidays("KR", years=[year]).keys())
    except Exception as e:                          # 패키지 없음/버전 문제
        if not _warned[0]:
            _warned[0] = True
            sys.stderr.write(
                "[datewin] [경고] holidays 패키지를 못 읽었습니다(%s). "
                "양력 고정 공휴일 표로만 판정합니다. 설·추석·부처님오신날·"
                "대체공휴일은 반영되지 않으니 "
                "`pip install holidays --break-system-packages` 하세요.\n"
                % str(e)[:60])
        got = {datetime.date(year, m, d) for (m, d) in _FALLBACK_FIXED}
    _kr_cache[year] = got
    return got


def is_holiday(day):
    """한국 공휴일인가. 주말은 공휴일로 보지 않는다(사용자 지시)."""
    return day in _kr_holidays(day.year)


def is_rest(day):
    """쉬는 날인가. 공휴일 또는 주말. '직전 영업일'을 찾을 때 쓴다."""
    return day.weekday() >= 5 or is_holiday(day)


def is_trigger(day):
    """이 날의 **전날**이 창을 늘릴 조건인가.

    전날이 한국 공휴일이거나 일요일이면 참(2026-08-18 사용자 지시).
    토요일은 넣지 않는다 - 전날이 토요일이면 실행일이 일요일인데
    그날은 브리프를 내지 않아 실질적으로 차이가 없다.
    """
    prev = day - datetime.timedelta(days=1)
    return is_holiday(prev) or prev.weekday() == 6


def last_business_day(day):
    """실행일 직전의 영업일(주말도 공휴일도 아닌 날)을 찾는다."""
    d = day - datetime.timedelta(days=1)
    for _ in range(21):                    # 폭주 방지 상한
        if not is_rest(d):
            return d
        d -= datetime.timedelta(days=1)
    return d


def window_days(cfg_policy, day):
    """policy dict 와 기준일(date)로 창 길이를 돌려준다."""
    base = int(cfg_policy.get("date_window_days", 1))
    if not is_trigger(day):
        return base
    span = (day - last_business_day(day)).days
    # 사용자가 요청한 최소 3일을 보장한다. 주중에 공휴일 하루만 낀 경우
    # span 이 2 로 나오는데, 지시는 '전날이 공휴일이면 3일'이었다.
    return max(base, 3, span)


def window(cfg_policy, day):
    """(lo, hi) 튜플. hi 는 기준일 당일."""
    n = window_days(cfg_policy, day)
    return day - datetime.timedelta(days=n), day
