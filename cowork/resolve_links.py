# -*- coding: utf-8 -*-
"""
resolve_links.py - refined-*.json 의 빈 링크를 나눠서 채운다(재개형).

## 왜 필요한가 (2026-08-04 실측)
링크 해결은 1건당 1.2~1.6초(성공) / 4.8초(실패) 걸린다. 하루 140건이면
워커 12개를 써도 45초 제한을 넘긴다. 그래서 `run.sh refine` 이 타임아웃 나고,
그걸 피하려고 `python3 refine.py` 를 직접 부르면 run.sh 가 하던
secrets.env 로딩이 빠져 **네이버 키 없이** 돌게 된다.

키가 없으면 naver_api 가 not_configured 를 돌려주고 resolve_link 는 그냥
다음 경로로 넘어간다. 즉 **0초 만에 전량 실패**하는데 로그에는 '링크 확보
111/256' 처럼 그럴듯한 숫자만 찍힌다. 실제로 08-04 가 그 상태였고,
사람이 실은 'AI 환각 줄일 기술'·'NTT데이터 1.5조 엔'·'지방 AIDC 전력평가'
세 건 모두 키만 있으면 1초대에 정확히 해결되는 기사였다.

그래서 링크 해결을 refine 에서 떼어내 여기서 재개형으로 돈다.
  - 한 번 호출에 --deadline 초만 쓰고, 남은 건수를 찍는다.
  - refined-*.json 을 제자리에서 갱신하므로 여러 번 불러도 이어서 채운다.
  - 키가 없으면 아무것도 하지 않고 크게 경고한다(조용한 전량 실패 방지).

사용:
  bash cowork/run.sh links          # 남은 게 없을 때까지 여러 번
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")
sys.path.insert(0, BASE)

import samsung_resolve as SR          # noqa: E402
import naver_api                      # noqa: E402
import websearch                      # noqa: E402

# [2026-08-27] 네트워크성 실패를 몇 번까지 다시 볼 것인가.
#   쿼터가 천장이라 무한 재시도는 미시도분의 시간을 빼앗는다.
MAX_RETRY = 1


def log(m):
    print("[links] %s" % m)


def have_key():
    return bool(os.environ.get("NAVER_CLIENT_ID") and
                os.environ.get("NAVER_CLIENT_SECRET"))


_HAN = re.compile(r"[가-힣]")


def hangul_ratio(s):
    s = (s or "").strip()
    return len(_HAN.findall(s)) / max(len(s), 1)


CURATED_SRC = ("slack", "adot", "samsung", "newsclips")


def is_curated(r):
    s = set(r.get("seen_in") or []) | set(r.get("sources") or [])
    if r.get("src"):
        s.add(r["src"])
    return bool(s & set(CURATED_SRC))


def targets(data):
    """링크를 채워야 할 항목. 우선순위 순으로 돌려준다.

    rescue_nolink 까지 포함한다. refine 은 컷 통과분(cut+own)만 해결했는데,
    프롬프트에는 rescue_nolink 150건도 실린다. 거기에 링크가 붙으면
    LLM 이 웹검색으로 찾는 수고가 사라지고 오링크 위험도 준다.

    정렬은 prompt_extend 와 같게 '큐레이션 먼저, 그다음 점수'로 한다.
    링크가 없어 가장 아쉬운 건 사람·봇이 이미 골라 올린 항목이기 때문이다.
    """
    out = []
    for key in ("items", "own_track", "rescue_nolink"):
        for it in data.get(key) or []:
            if not it.get("url") and (it.get("title") or it.get("summary")):
                out.append(it)
    out.sort(key=lambda r: (not is_curated(r), -(r.get("score") or 0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    # [2026-08-24] 26 -> 120. bash 호출 상한은 45초가 아니라 약 178초다.
    #   26초로 끊으면 회당 왕복 오버헤드 8초가 붙어 30%가 포장비로 나간다.
    #   run.sh 의 links 분기 주석에 실측 근거가 있다.
    ap.add_argument("--deadline", type=float, default=120.0,
                    help="이번 회차에 쓸 최대 초 (호스트 상한 178초)")
    # [실측 2026-08-04] 워커를 12로 두면 네이버가 429 를 내고 재시도 백오프가
    #   걸려 12건 처리에 41초가 든다. 4로 낮추면 8건에 25초로 훨씬 안정적이다.
    # [2026-08-24] 기본을 8로 맞춘다(run.sh 가 늘 8을 넘겨 왔다).
    #   단 실효 동시성은 naver_api._SEM/websearch._SEM 이 3으로 묶는다.
    #   그 세마포어를 6으로 올리면 429 백오프로 오히려 2배 느려진다(실측).
    ap.add_argument("--workers", type=int, default=8)
    # [2026-08-10] 30 -> 150. 예전 주석은 '하위권은 영문이라 어차피 안 잡힌다'
    #   였는데 실측과 달랐다. 미확보 150건 중 국문+국내매체가 51%(76건)였고
    #   그 72%가 재시도만으로 해결됐다. 한 회차 분량은 --deadline 이 잡으므로
    #   limit 은 '이 순위까지만'이 아니라 '한 회차 최대 처리량'으로 쓴다.
    ap.add_argument("--limit", type=int, default=150,
                    help="한 회차에 시도할 최대 건수")
    # [2026-08-21] 기본 창 1 -> 2 (전전일까지).
    #   [왜] 후보의 pub_date 는 구글뉴스 **재게재일**인데 링크 해결은 네이버가
    #     주는 **원문 발행일**로 창을 검사한다. 둘이 하루씩 어긋나면 후보로는
    #     들어왔는데 링크 해결에서만 배제되는 모순이 생긴다.
    #     실측 08-21: '오픈AI 청소년용 챗GPT 출시'는 원문이 08-19 인데 후보
    #     pub_date 는 08-20 이었다. 창이 08-20~08-21 이라 정답 기사가 전부
    #     날짜 필터에 걸려 no_match 가 됐고, 사람은 그 기사를 실었다.
    #   [효과] 국문 미확보 12건 표본에서 0건 -> 2건 추가 해결(창원 AIDC
    #     1조894억 -> 노컷뉴스, 앤트로픽 IPO -> 지디넷). 둘 다 정상 매칭.
    #   [위험] 창이 넓어지면 과거 기사 오링크 가능성이 올라간다. 이 창은 원래
    #     'SKT 정재헌 MWC'가 3월 원문에 연결되던 버그를 막으려고 넣은 것이다.
    #     제목 유사도 임계(0.55/0.75)가 2차 방어선이므로 +1일까지만 허용한다.
    #     **3 이상으로 올리지 말 것.**
    ap.add_argument("--window", type=int, default=2,
                    help="발행일 창(일). 후보 pub_date 와 원문 발행일의 "
                         "하루 어긋남을 흡수하려고 refine 창보다 1일 넓다")
    a = ap.parse_args()

    p = os.path.join(OUTDIR, "refined-%s.json" % a.date)
    if not os.path.exists(p):
        log("refined 파일 없음: %s" % p)
        return 2
    data = json.load(open(p, encoding="utf-8"))

    todo = targets(data)
    if not todo:
        log("빈 링크 없음. 완료")
        return 0

    if not have_key():
        log("!! 네이버 API 키가 없습니다. 링크 해결을 건너뜁니다 !!")
        log("   cowork/secrets.env 를 읽는 run.sh 를 통해 실행하세요.")
        log("   (키 없이 돌리면 %d건이 조용히 전량 실패합니다)" % len(todo))
        return 3

    dd = datetime.date.fromisoformat(a.date)
    lo, hi = dd - datetime.timedelta(days=a.window), dd
    t0 = time.time()
    done = {"ok": 0, "ng": 0, "skip": 0, "net": 0}

    def work(it):
        head = (it.get("title") or it.get("summary") or "").strip()
        # 제목이 없거나 너무 짧으면 검색 자체가 무의미하다. 시도하면
        # 무관한 기사가 붙을 위험만 있고 5초를 버린다.
        if len(head) < 8:
            it["link_tried"] = True
            done["skip"] += 1
            return None
        # 영문 제목은 네이버 '뉴스' 경로를 건너뛴다(위 samsung_resolve 주석 참조).
        sk = hangul_ratio(head) < 0.15
        # [2026-08-24] 여기 있던 '예외 시 0.5초 쉬고 통째로 한 번 더' 재시도를
        #   없앴다. naver_api.search / websearch.search 가 이미 각각 3회
        #   재시도(백오프 0.4/0.8초)를 하므로 중복이었다. 한 건이 최악
        #   webkr 2질의 x3 + news 5질의 x3 = 21회 호출인데 그걸 다시 2배로
        #   부풀렸다. 실측 08-24: 건당 최대 68초짜리가 나왔고, 그 대기가
        #   세마포어(3)를 물고 있어 다른 건까지 굶겼다.
        # [2026-08-27] 실패 사유를 갈라 센다. 네트워크성 실패는 회복 가능한데
        #   종전에는 '못 찾음' 과 똑같이 link_tried 로 못박아 그날 영영
        #   재시도하지 않았다. 실측 08-26: 336건 중 104건(31%) 실패.
        websearch.clear_net_error()
        naver_api.clear_net_error()
        netfail = False
        try:
            r = SR.resolve_link(head, it.get("outlet") or "", lo, hi,
                                skip_naver=sk)
        except Exception:
            r = None
            netfail = True
        if not r:
            netfail = (netfail or websearch.net_error()
                       or naver_api.net_error())
        if r:
            it["url"], it["confidence"] = r["url"], "auto"
            it["url_via"] = r["via"]
            it.pop("link_fail", None)
            done["ok"] += 1
        elif netfail and it.get("link_retry", 0) < MAX_RETRY:
            # 이번 회차에서는 빼되(창을 물지 않게), 미시도분이 다 끝난 뒤
            # 마지막에 한 번 더 본다.
            it["link_tried"] = True
            it["link_fail"] = "net"
            done["net"] += 1
        else:
            # 정말 못 찾은 것. 재시도해도 같으므로 못박는다.
            # (실패 1건에 4.8초가 들어 재시도가 가장 비싸다)
            it["link_tried"] = True
            it["link_fail"] = "notfound" if not netfail else "net_exhausted"
            done["ng"] += 1
        return None

    # [2026-08-10] 창을 '미시도 건'에서만 잘라낸다.
    #   예전에는 targets(data)[:limit] 으로 잘랐는데, 실패한 건은 url 이
    #   끝내 안 붙어 targets() 에 영원히 남는다. 즉 실패가 상위 슬롯을
    #   계속 점유해 창이 '해결된 만큼만' 밀렸다. 실패가 limit 만큼 쌓이면
    #   그 뒤 항목은 영구히 도달 불가였다.
    #   실측 08-10: 링크 없는 167건 중 30건만 시도하고 17건 확보 후 정체.
    #   미시도 137건을 나중에 그대로 재시도하니 국문분 72%가 해결됐다.
    pending = [x for x in todo if not x.get("link_tried")]
    phase = "미시도"
    if not pending:
        # [2026-08-27] 미시도분을 다 소진한 뒤에만 네트워크성 실패를 다시 본다.
        #   먼저 재시도하면 아직 한 번도 안 본 건의 창을 빼앗는다(쿼터가
        #   천장이라 총량이 정해져 있다). 재시도는 MAX_RETRY 회까지만.
        pending = [x for x in targets(data)
                   if not x.get("url") and x.get("link_fail") == "net"
                   and x.get("link_retry", 0) < MAX_RETRY]
        for x in pending:
            x["link_retry"] = x.get("link_retry", 0) + 1
            x["link_tried"] = False
        phase = "네트워크성 실패 재시도"
    fresh = scope = pending[:a.limit]
    log("%s %d건 중 상위 %d건 대상 / 마감 %.0f초, 워커 %d"
        % (phase, len(pending), len(scope), a.deadline, a.workers))

    def save():
        json.dump(data, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    # [주의] ex.map 에 전량을 한 번에 넣으면 안 된다.
    #   naver_api/websearch 는 TIMEOUT 15초에 재시도 2회라 한 건이 최대
    #   40초 넘게 붙잡힐 수 있다. 마감을 넘겨 나머지가 즉시 반환돼도
    #   그 낙오 스레드를 기다리느라 전체가 안 끝난다(실측: 마감 5초인데 32초).
    #   그래서 워커 수만큼 끊어 돌리고, 덩어리마다 저장한다.
    #   호출이 중간에 잘려도 그때까지 채운 링크는 파일에 남는다.
    # [2026-08-24] 마감 검사를 '남은 시간이 한 덩어리를 감당하는가'로 바꿨다.
    #   종전 `> a.deadline` 은 마감 1초 전에도 새 덩어리를 시작해서, 그 덩어리가
    #   끝날 때까지 기다리느라 마감을 크게 넘겼다.
    #   실측 08-24: 마감 100초로 불렀는데 119초가 걸렸다(19초 초과). 실패 건이
    #   섞인 덩어리는 20초 가까이 붙잡히기 때문이다. 마감을 120으로 올린 뒤로는
    #   이 초과분이 호스트 상한(178초)을 위협한다.
    #   CHUNK_WORST 만큼 여유가 없으면 새 덩어리를 시작하지 않는다. 그러면
    #   --deadline 이 '이번 회차 총 소요 상한'이라는 뜻이 된다.
    CHUNK_WORST = 22.0
    for i in range(0, len(fresh), a.workers):
        if time.time() - t0 > a.deadline - CHUNK_WORST:
            break
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(work, fresh[i:i + a.workers]))
        save()
    save()
    # 남은 건수는 전체 미시도분으로 센다(창 크기로 자르지 않는다).
    # 창으로 자르면 뒤쪽 100여 건이 남아 있는데도 '전부 시도 완료'로 찍힌다.
    left = len([x for x in targets(data) if not x.get("link_tried")])
    # 아직 재시도 기회가 남은 네트워크성 실패도 '남음'에 센다. 안 그러면
    # '전부 시도 완료'로 찍혀 재시도 회차가 아예 안 돈다.
    retryable = len([x for x in targets(data)
                     if not x.get("url") and x.get("link_fail") == "net"
                     and x.get("link_retry", 0) < MAX_RETRY])
    log("확보 %d건 / 못찾음 %d건 / 네트워크성 %d건 / 건너뜀 %d건 "
        "/ 남음 %d건(재시도 대기 %d) (%.0f초)"
        % (done["ok"], done["ng"], done["net"], done["skip"],
           left + retryable, retryable, time.time() - t0))
    if left + retryable:
        log("남았습니다 - 다시 실행하세요")
    else:
        nf = len([x for x in targets(data)
                  if not x.get("url") and x.get("link_fail") == "net_exhausted"])
        log("전부 시도 완료%s"
            % (" (재시도까지 소진한 네트워크성 실패 %d건)" % nf if nf else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
