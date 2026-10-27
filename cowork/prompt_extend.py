# -*- coding: utf-8 -*-
"""
prompt_extend.py - '링크 없어서 버려지던' 후보를 프롬프트에 되살린다.

## 왜 필요한가 (2026-07-31 실측)
사람이 실제로 발송한 브리프 41건(07-23/28/30)이 파이프라인에서 어디로 사라지는지
compare.py 로 추적한 결과:

    LLM 이 실제로 본 것        19건 (46%)
    링크가 없어 제외됨          12건 (29%)   <- 전부 이 사유
    아예 수집 안 됨            10건 (24%)   <- 10건 중 9건이 영문
    (나머지는 구제 후보로 노출)

핵심은 '미노출 12건이 전부 링크 없음'이었다는 것이다. 순위가 낮아서가 아니다.
27위 / 54위 / 70위 / 134위처럼 컷 안에 넉넉히 든 항목도 링크가 없다는 이유만으로
brief.py 의 `[x for x in items if x.get("url")]` 에서 걸러졌다.
구제 후보(rescue)도 링크 보유가 조건이라 이들은 어느 쪽으로도 못 들어간다.

당일(07-31) 기준 주후보 160건 중 76건(48%)이 링크가 없다. 후보의 절반을
LLM 이 보지도 못한 채 버리고 있었다는 뜻이다.

## 접근
링크를 미리 다 찾아두는 건 비현실적이다(하루 76건). 순서를 뒤집는다.

    기존: 링크 확보 -> 선별          (링크 못 찾으면 선별 기회조차 없음)
    변경: 선별 -> 고른 것만 링크 확보  (하루 3~5건이면 충분)

이 스크립트는 링크 없는 고득점 항목을 프롬프트 끝에 '[링크 미확보 후보]' 로 덧붙인다.
LLM 이 그중 실을 가치가 있는 것을 고르면, 그때 WebSearch 로 원문을 찾아
cowork/samsung_match.py 와 같은 방식으로 URL 실재성을 검증한 뒤 싣는다.

[불변 규칙은 그대로다] LLM 이 링크를 지어내지 않는다. 검색으로 찾고, 스크립트가
열어보고, 살아있는 것만 쓴다.

사용:
  python3 cowork/prompt_extend.py --date 2026-07-31 [--top 25]
"""
import argparse
import datetime
import json
import re
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--top", type=int, default=25,
                    help="링크 없는 항목 중 상위 몇 건을 노출할지")
    a = ap.parse_args()
    day = (datetime.date.fromisoformat(a.date) if a.date
           else datetime.date.today()).isoformat()

    # [소스는 refined 여야 한다 - ranked 를 보면 안 된다]
    #   ranked-*.json 은 링크 해결 '이전'에 저장되므로 url 보유가 0건이다.
    #   거기서 '링크 없는 항목'을 고르면 그냥 전체 상위 클러스터를 고르는 것과 같다.
    #   refined.items 만이 '해결을 시도했으나 실패한' 항목을 담고 있다.
    #
    # [이 도구의 한계 - 실측 2026-07-31]
    #   compare --json 으로 07-23/28/30 의 '링크없음' 손실 11건을 추적한 결과,
    #   9건은 refined.items 에 아예 없었다. 점수 2~7점이라 min-score(6) 또는
    #   max-items(160) 컷에서 링크 해결 단계에 도달하지도 못했기 때문이다.
    #   따라서 이 도구가 되살릴 수 있는 것은 11건 중 2건뿐이다.
    #   나머지 9건을 구하려면 컷을 넓혀야 하고, 그러려면 refine 의 링크 해결을
    #   먼저 병렬화해야 한다(현재 max-items 300 이면 42초 초과로 실행 불가).
    rp = os.path.join(OUTDIR, "refined-%s.json" % day)
    pp = os.path.join(OUTDIR, "prompt-%s.txt" % day)
    for p in (rp, pp):
        if not os.path.exists(p):
            print("[오류] %s 없음. refine/brief 를 먼저 실행" % p)
            sys.exit(1)

    data = json.load(open(rp, encoding="utf-8"))
    prompt = open(pp, encoding="utf-8").read()
    # [2026-08-12] 줄머리로만 매칭한다. 그냥 "in prompt" 로 검사하면
    #   brief.py 의 [선별 절차] 안내문("...[구제 후보]와 [링크 미확보 후보]의
    #   curated 항목까지 한 건도 빠짐없이 훑으며...")에 걸려 **항상** 오탐하고,
    #   미확보 150건이 통째로 빠진 프롬프트가 조용히 만들어진다.
    #   실측 08-12: 216KB 여야 할 프롬프트가 111KB 로 생성됐다.
    if "\n[링크 미확보 후보]" in prompt:
        print("[extend] 이미 덧붙여져 있음. 건너뜀")
        return

    # 두 갈래를 합친다.
    #   (1) items/own_track 안에서 링크 해결에 실패한 항목 (컷은 통과했다)
    #   (2) refine 이 새로 내보내는 rescue_nolink (컷 밖 저점수 + 링크 없음)
    # (2)가 없으면 컷 밖 항목은 어떤 경로로도 LLM 에게 닿지 못한다.
    pool = (data.get("items") or []) + (data.get("own_track") or [])
    nolink = [x for x in pool if not x.get("url")]
    seen = {x.get("title") for x in nolink}
    for x in (data.get("rescue_nolink") or []):
        if x.get("title") not in seen:
            nolink.append(x)
            seen.add(x.get("title"))
    # [2026-08-03] 큐레이션 출처를 기계 점수보다 앞세운다.
    #   실측: 08-03 사람 브리프 17건 중 3건이 #3_트렌드 채널에서 예고됐는데
    #   셋 다 링크 미확보였다. 그런데 이 목록이 순수 기계 점수순이라
    #     '앤트로픽, 미토스로 양자내성 암호 후보 뚫었다'  -> 150건 중 121위(5점)
    #   처럼 바닥에 깔려 그대로 놓쳤다.
    #   사람/봇이 이미 한 번 걸러 올린 항목은 기계 점수가 낮아도 우선 검토 대상이다.
    #   버리지 않고 '순서만' 올린다(정보 손실 없음).
    CURATED_SRC = ("slack", "adot", "samsung", "newsclips")

    def is_curated(r):
        # [2026-08-04] 'sources' 를 빠뜨리고 있었다.
        #   refine 이 내보내는 항목은 출처를 'sources'(리스트)에 담는데
        #   여기선 seen_in/src/why 만 봐서, 슬랙·삼성 출처 항목이 큐레이션으로
        #   판정되지 않고 기계 점수순 바닥으로 밀렸다.
        #   실측 08-04: 큐레이션 7건으로 잡혔어야 할 것이 14건이었고,
        #   'AI 환각 줄일 기술'(사람 브리프 게재)은 296건 중 294위로 밀려
        #   상위 150 컷에 걸려 프롬프트에 아예 실리지 못했다.
        srcs = set(r.get("seen_in") or []) | set(r.get("sources") or [])
        if r.get("src"):
            srcs.add(r["src"])
        for w in (r.get("why") or []):
            for s in CURATED_SRC:
                if s in str(w):
                    srcs.add(s)
        return bool(srcs & set(CURATED_SRC))

    for x in nolink:
        x["_curated"] = is_curated(x)
    nolink.sort(key=lambda r: (not r["_curated"], -r.get("score", 0)))

    # [2026-08-04] 직전에 사람이 발송한 브리프에 이미 실린 사건을 뺀다.
    #   여기를 빼먹으면 brief.py 에서 걸러낸 기사가 이 목록으로 되돌아온다.
    #   실측 08-04: 구글어스 AI 이미지 철회 건이 여기에 2건 남아 있었다.
    #   자르기(top) '전에' 빼야 그만큼 새 후보가 자리를 채운다.
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from dedup_prev import PrevFilter
        pf = PrevFilter(day)
        if pf.active:
            nolink, dropped = pf.split(nolink)
            if dropped:
                print("[extend] 전일(%s) 발송분과 같은 사건 %d건 제외"
                      % (pf.day, len(dropped)))
    except Exception as e:
        print("[extend] 전일 중복 제거 건너뜀: %s" % str(e)[:50])

    # [2026-10-27] 경쟁사(KT·LGU+·네이버·카카오·LG AI연구원 등)·큐레이션 건은 상한(top)
    #   밖이어도 싣는다. 실측 10-27: KT '토큰팩토리' 공개(경쟁사 신규 서비스)가 링크 미확보
    #   후보 141위였는데, 컷 통과·링크 없는 상위 후보와 합쳐 다시 줄 세우며 150위 밖으로 밀려
    #   판정 목록에 아예 없었다. 경쟁사 판정은 config/keywords.yaml 의 competitor 패턴.
    comp_pats = []
    try:
        import yaml
        _k = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "config", "keywords.yaml"), encoding="utf-8"))

        def _find(o):
            if isinstance(o, dict):
                for kk, v in o.items():
                    if kk == "competitor":
                        return v
                    r = _find(v)
                    if r:
                        return r
            return None
        comp_pats = [re.compile(str(p)) for p in (_find(_k) or [])]
    except Exception as e:
        print("[extend] 경쟁사 패턴 읽기 실패: %s" % str(e)[:60])

    AIREL = re.compile(r"AI|인공지능|에이전트|데이터센터|AIDC|GPU|LLM|모델|로봇|토큰|클라우드|엣지|반도체|피지컬")

    def is_comp(x):
        # 경쟁사 이름 + AI 관련 낱말(경쟁사 생활 서비스·주가 기사는 상한 예외에서 뺀다)
        t = x.get("title") or x.get("summary") or ""
        return any(p.search(t) for p in comp_pats) and bool(AIREL.search(t))

    for x in nolink:
        x["_comp"] = is_comp(x)
    picked = nolink[:a.top]
    keep = [x for x in nolink[a.top:] if x["_curated"] or x["_comp"]]
    if keep:
        print("[extend] 상한 %d건 밖이지만 경쟁사·큐레이션이라 추가로 싣는 건 %d건" % (a.top, len(keep)))
        picked += keep
    n_cur = sum(1 for x in picked if x["_curated"])
    if not picked:
        print("[extend] 링크 없는 후보가 없음. 그대로 둠")
        return

    L = ["", "", "[링크 미확보 후보] - **여기에 오늘 브리프의 핵심이 섞여 있을 수 있습니다.**",
         "이 항목들은 기계가 원문 링크를 못 찾아 위 후보 목록에서 빠진 것입니다.",
         "중요하지 않아서가 아닙니다. 실측상 사람이 실제로 발송한 브리프 기사의 29%가",
         "바로 이 사유로 사라졌고, 그중에는 27위·54위·70위처럼 상위권도 있었습니다.",
         "영문 기사와 자사(SKT) 기사가 특히 많이 걸립니다.",
         "",
         "실을 가치가 있다고 판단되면 **bash cowork/run.sh find \"<주체+숫자/핵심어>\" --body 2** 로",
         "원문 URL 을 찾아 브리프에 넣으세요(2026-10-27: WebSearch 는 미국 기준이라 국내 기사가 거의 안 나온다).",
         "제목·매체·날짜가 아래 값과 같은 기사인지 대조한 뒤 쓰세요.",
         "URL 을 못 찾으면 그 항목은 버립니다. 링크 없는 기사는 브리프에 싣지 않습니다.",
         "",
         "**\"curated\": true 인 항목(맨 앞 %d건)은 tier0 입니다. "
         "훑는 게 아니라 %d건 전부 WebSearch 를 돌리세요.**" % (n_cur, n_cur),
         "슬랙 큐레이션 채널(#3_트렌드·에이닷·삼성)과 지면 스크랩에서 온 것이라",
         "사람/봇이 이미 한 번 거른 입력입니다. 검색해 보고 링크가 안 나오면",
         "그때 버리되, **점수가 낮다는 이유로 검색을 건너뛰지 마세요.**",
         "  (실측 08-03: 사람 브리프 17건 중 3건이 이 채널에서 예고됐는데 링크 미확보로",
         "   전부 하위권에 묻혔고, 그중 '앤트로픽 미토스' 건을 실제로 놓쳤습니다.)",
         "  (실측 08-11: 큐레이션 5건의 점수가 11·9·8·5·3 이었는데 그중 11점",
         "   'LGU+ 익시오 통화음성 수집'과 8점 '삼성 AI-RAN'이 사람 정답지였습니다.",
         "   정답지 15건 중 12건(80%)이 tier0 통로였습니다.)",
         ""]
    for i, x in enumerate(picked, 1):
        d = {
            "nolink_idx": i,
            "curated": x["_curated"],
            "competitor": x.get("_comp", False),
            "category": x.get("category"),
            "headline": x.get("title") or x.get("summary"),
            "outlet_guess": x.get("outlet"),
            "pub_date": x.get("pub_date"),
            "score": x.get("score"),
            "n_outlets": x.get("size"),
            "signals": x.get("why"),
        }
        # [2026-08-27] 최근 발송분과 제목이 겹쳐 보이는 건. 기계가 자르지
        #   않고 플래그만 단다(dedup_prev.PrevFilter.split 참조).
        if x.get("dup_suspect"):
            d["dup_suspect"] = x["dup_suspect"]
        L.append(json.dumps(d, ensure_ascii=False))

    # [주의] 그냥 append 하면 brief.py 가 맨 끝에 붙여둔 '[전일 발송분으로 제외]'
    #   블록보다 뒤로 가서, 그 블록의 '위 후보에서 뺐습니다'라는 설명이
    #   가리키는 대상이 어긋난다. 그 블록이 있으면 그 앞에 끼워 넣는다.
    body = open(pp, encoding="utf-8").read()
    marker = "\n[전일 발송분으로 제외]"
    add = "\n".join(L)
    if marker in body:
        head, tail = body.split(marker, 1)
        body = head + add + marker + tail
        open(pp, "w", encoding="utf-8").write(body)
    else:
        open(pp, "a", encoding="utf-8").write(add)
    print("[extend] 링크 미확보 후보 %d건을 프롬프트에 추가 (전체 링크없음 %d건 중)"
          % (len(picked), len(nolink)))
    print("[extend] 그중 큐레이션 출처 %d건을 맨 앞으로 고정" % n_cur)
    print("[extend] 상위 8건:")
    for x in picked[:8]:
        print("   %s %2d점 %s" % ("[큐레이션]" if x["_curated"] else "        ",
                                  x.get("score", 0),
                                  (x.get("title") or x.get("summary"))[:52]))


if __name__ == "__main__":
    main()
