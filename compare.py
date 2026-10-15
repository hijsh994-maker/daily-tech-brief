# -*- coding: utf-8 -*-
"""
compare.py - 실제 브리프와 파이프라인 결과를 대조한다.

임계점수(--min-score)를 감으로 정하면 안 되므로, 실제로 실린 기사가
우리 랭킹에서 몇 점·몇 위였는지 측정해서 근거를 만든다.

모든 비율의 분모는 '그날 사람이 실제로 발송한 브리프의 기사 수'로 같다.
분모를 통일해야 수집 실패와 채점 실패를 한 줄에서 비교할 수 있기 때문이다.
(임계 통과율의 분모를 '수집된 것'으로 잡으면, 수집을 적게 할수록 통과율이
올라가는 이상한 지표가 된다.)

  수집 재현율   브리프 기사 중 파이프라인 결과에 존재하는 비율
  임계 통과율   브리프 기사 중 '존재 + 그 클러스터 점수 >= min-score' 비율
  링크 확정     매칭 중 원문 URL 일치로 확정된 비율 (나머지는 어휘·엔티티 추정)
  LLM 가시성    브리프 기사 중 실제로 프롬프트에 실린 비율
  순위 분포     놓친 것은 점수가 낮아서인지, 아예 수집이 안 된 것인지

정의상 임계 통과율 <= 수집 재현율 이다(매칭된 것 중에서만 점수를 따지므로).

사용:
  python compare.py --date 2026-07-21 --brief brief-2026-07-21.txt
  (정답지는 링크가 든 truth-YYYY-MM-DD.json 을 우선 쓰고, 없으면 --brief 의
   제목 한 줄씩 텍스트를 쓴다)
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml                        # noqa: E402
import samsung_resolve as SR      # noqa: E402
import refine as R                 # noqa: E402  (엔티티 정규화 재사용)

BASE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(BASE, "out")
KW = os.path.join(BASE, "config", "keywords.yaml")


def load_all_clusters(day):
    """refined 는 컷 이후만 담고 있으므로 전체 랭킹을 다시 만든다."""
    p = os.path.join(OUTDIR, "ranked-%s.json" % day)
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))["items"]
    p = os.path.join(OUTDIR, "refined-%s.json" % day)
    d = json.load(open(p, encoding="utf-8"))
    return d["items"] + d.get("own_track", [])


def load_visible(day):
    """LLM 프롬프트에 실제로 실린 항목의 제목 -> 트랙 이름.

    [왜 필요한가] 최종 선별이 LLM 으로 넘어간 뒤로 'min-score 6 통과'는 더 이상
    합격선이 아니다. 컷 밖이라도 링크만 있으면 구제 후보로 프롬프트에 실려 LLM 이
    볼 수 있다. 그래서 임계 통과율과 별개로 '후보로 보여지긴 했는가'(가시성)를
    따로 잰다. 임계 통과율은 여전히 '기계가 얼마나 잘 집었나'의 지표로 남긴다.
    """
    p = os.path.join(OUTDIR, "refined-%s.json" % day)
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    vis = {}
    # [추가 2026-07-31] rescue_nolink 는 url 조건을 걸지 않는다.
    #   이 트랙은 '링크가 없어서 버려지던' 항목을 cowork/prompt_extend.py 가
    #   '[링크 미확보 후보]' 섹션으로 프롬프트에 실어 보내는 경로다. LLM 이
    #   고르면 그때 웹검색으로 링크를 확보한다. 따라서 '봤는가' 기준으로는
    #   노출된 것이 맞다. url 을 요구하면 이 경로의 효과를 영원히 0으로 잰다.
    #   단 우선순위는 가장 낮다(뒤에 오는 트랙이 덮어쓴다).
    for key, track in (("rescue_nolink", "링크미확보"), ("rescue", "구제"),
                       ("own_track", "자사"), ("items", "주후보")):
        need_url = key != "rescue_nolink"
        for x in d.get(key) or []:
            if x.get("title") and (x.get("url") or not need_url):
                vis[x["title"]] = track      # 주후보가 뒤에 와서 덮어쓴다(우선)
    return vis


def parse_brief(path):
    out = []
    for l in open(path, encoding="utf-8"):
        s = l.strip()
        if not s or s.startswith(("#", "//")):
            continue
        s = re.sub(r"^\s*\d+[.)]\s*", "", s)
        s = re.sub(r"\s*\([^)]*원문[^)]*\)\s*$", "", s)
        if len(s) > 6:
            out.append(s)
    return out


def norm_url(u):
    """같은 기사인지 비교하기 위한 URL 정규화.

    스킴·www·프래그먼트·추적 파라미터(utm_*)만 걷어낸다. 국내 매체는 기사 번호가
    쿼리에 있는 경우가 많아(zdnet ?no=, sedaily ?id=) 쿼리를 통째로 버리면 안 된다.
    """
    if not u:
        return ""
    u = u.strip().split("#")[0]
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = re.sub(r"[?&]utm_[^&]*", "", u)
    return u.rstrip("/&?").lower()


def load_truth(day, brief_path):
    """정답지 로드. 링크가 있는 truth-*.json 이 있으면 그것을 쓴다.

    반환: [{"title":…, "url":…}]  (url 은 없을 수 있음 = 사람이 만든 txt)
    """
    p = os.path.join(BASE, "truth-%s.json" % day)
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        return [{"title": x.get("title") or "", "url": x.get("url") or ""}
                for x in d.get("items") or []]
    return [{"title": t, "url": ""} for t in parse_brief(brief_path)]


def url_index(rows):
    """정규화 URL -> 클러스터 인덱스. 멤버가 수집 때 갖고 있던 링크로 만든다."""
    idx = {}
    for i, r in enumerate(rows):
        for u in list(r.get("member_urls") or []) + [r.get("url")]:
            k = norm_url(u)
            if k and k not in idx:
                idx[k] = i
    return idx


# 영문 불용어. samsung_resolve.GENERIC 은 국문 기준으로 만들어져 영문 기능어를
# 걸러내지 못한다. 그 탓에 'Over 1,100 AI workers sign letter...' 가 무관한
# 'One Sign the AI Power Trade Is Running Out of Juice' 와 {of, the, sign} 을
# 공유해 포함도 0.30 을 얻는 식의 허위 매칭이 생겼다.
STOP_EN = set("""
a an the of to in on for and or at by with from as is are was were be been being
it its this that these those there here after before over under up down out into
about more most new first last next its his her their our your not no than then
so such can could will would may might must should have has had do does did
us out via amid ahead across among between during against toward towards
say says said report reports reported plan plans planned
""".split())


def distinctive(s):
    return {t for t in SR.distinctive(s) if t not in STOP_EN}


def match(brief_title, rows, canon):
    """브리프 한 줄과 같은 사건인 클러스터를 찾는다. (매칭, 포함도, 엔티티수) 반환.

    자카드가 아니라 포함도(containment)로 본다. 매체마다 표기가 달라(한/영 혼재,
    길이차) 자카드가 낮게 나오기 때문이다. 예: 'MS, AI 취약점 탐지 도구 Project
    Perception' vs 'Microsoft Project Perception: AI Bug Hunter'는 자카드 0.2.

    [증거 규칙 2026-07-31 개정] 예전 기준(포함도 0.25 이상 '또는' 엔티티 2개
    공유)이 너무 느슨해, 수집조차 안 된 기사를 무관한 클러스터에 크레딧했다.
    재현율은 부풀고 채점 품질은 억울하게 낮아 보였다. 실제 오매칭 사례:

      - Virtuix/Optimus -> 'HK Market Movers'  (포함도 1.00)
        원인: 후보 제목의 고유토큰이 2개뿐이라 inter/len(ct)=1.00. 짧은 제목이면
        아무 브리프 줄에나 1.00 이 나온다.
      - Disney/Copilot -> '앤트로픽 인터뷰'     (포함도 0.00, 엔티티 2)
        원인: 엔티티만 2개 겹치면 어휘 근거가 0이어도 통과. 게다가 후보를
        '점수 높은 순'으로 골라, 무관한 고득점 클러스터가 크레딧을 가져갔다.
      - 'Over 1,100 AI workers sign letter' -> 무관 12점 클러스터
        원인: 토크나이저가 영문 기능어를 안 걸러 {of, the, sign} 만으로 0.30.

    그래서 세 가지를 건다.
      1) 영문 불용어를 걸러낸 뒤 토큰을 센다(위 distinctive).
      2) 짧은 후보 제목으로 나눈 포함도는 인정하지 않는다(고유토큰 4개 이상일 때만).
      3) 엔티티 2개를 공유해도 어휘 근거(포함도>0)가 없으면 후보에 올리지 않고,
         후보 중에서는 점수가 아니라 어휘 근거가 큰 쪽을 고른다.

    [검증] 링크가 일치해 '같은 기사'가 확정된 13건을 오라클로 삼아 이 휴리스틱을
    돌린 결과 정탐 12 / 오탐 1 / 미탐 0. 유일한 오탐도 같은 사건의 국문 대표 대신
    영문 대표를 고른 것(분절된 클러스터)이라 사실상 오답이 아니다.
    """
    bt = distinctive(brief_title)
    be = R.entity_ids(brief_title, canon)
    if not bt and not be:
        return None, 0.0, 0
    ent_cands = []          # 엔티티 2개 + 어휘 근거: (cont, score, i, r)
    tok_best = None         # 어휘 근거만: (cont, i, r)
    for i, r in enumerate(rows):
        # 엔티티 매칭은 '대표 제목'만 본다(요약·멤버 제외). 요약/멤버까지 쓰면
        # 큰 클러스터가 온갖 엔티티를 언급해, 무관한 브리프 줄이 그 대형 클러스터에
        # 오매칭된다(예: Senate 회동 -> '삼성·SK 9500억' 25점 오크레딧).
        rent = R.entity_ids(r.get("title") or "", canon)
        ent_ov = len(be & rent)
        cont, inter_best, best_c = 0.0, 0, None
        for c in [r.get("title") or "", r.get("summary") or ""] + \
                 list(r.get("members") or []):
            ct = distinctive(c)
            if not ct:
                continue
            inter = len(bt & ct)
            if inter < 2:
                continue
            v = inter / len(bt)
            if len(ct) >= 4:            # 짧은 제목은 분모로 쓰지 않는다
                v = max(v, inter / len(ct))
            if v > cont:
                cont, inter_best, best_c = v, inter, c
        # [수정 2026-07-31] 어휘 경로의 엔티티 관문은 '실제로 맞은 문자열' 기준으로 본다.
        #   어휘 비교는 멤버까지 훑는데, 관문인 ent_ov 는 대표 제목만 보고 있었다.
        #   그래서 '멤버와는 정확히 일치하는데 대표 제목에 공통 엔티티가 없는' 경우가
        #   통째로 탈락했다. 실측 사례(07-30):
        #     브리프  "OpenAI's Sam Altman meets senators, Jensen Huang too"
        #     클러스터 24위 대표 "The Hugging Face AI break-in, as told through..."
        #     멤버    "OpenAI's Sam Altman discusses rogue agent with US senators"
        #   멤버 포함도는 0.5 를 넘는데 대표 제목에 OpenAI/Nvidia 엔티티가 없어
        #   ent_ov=0 -> 탈락 -> '미수집'으로 집계됐다. 실제로는 24위 14점으로
        #   프롬프트에 실려 있었다. 측정이 파이프라인을 억울하게 깎고 있었던 것이다.
        #
        #   엔티티 '경로'(ent_cands)는 그대로 대표 제목만 쓴다. 그쪽까지 멤버를
        #   허용하면 큰 클러스터가 온갖 엔티티를 흡수해 무근거 크레딧이 생긴다.
        #   여기서 넓히는 것은 어휘 근거가 이미 강한 경우의 '관문'뿐이다.
        ent_ov_lex = ent_ov
        if best_c is not None and ent_ov == 0:
            ent_ov_lex = len(be & R.entity_ids(best_c, canon))
        if ent_ov >= 2 and cont > 0.0:
            ent_cands.append((cont, r["score"], i, r))
        # 어휘 단독 근거는 '고유토큰 3개 이상 + 포함도 0.4 이상'을 요구한다.
        # 예전엔 2개/0.25 였는데, 그러면 후보 제목이 짧을 때 아무 기사에나 붙었다
        # (Virtuix -> 'HK Market Movers' 1.00, Palantir -> 'ONDS Stock Slips' 0.25).
        if inter_best >= 3 and cont >= 0.4 and (not be or ent_ov_lex >= 1):
            if tok_best is None or cont > tok_best[0]:
                tok_best = (cont, i, r)
    # 엔티티 근거가 있는 후보 중에서 고른다. 예전엔 '점수 높은 순'이라 어휘 근거가
    # 0 인 무관한 고득점 클러스터가 크레딧을 채갔다(Disney -> 앤트로픽 인터뷰).
    # 이제 어휘 근거를 먼저 보고, 같으면 점수로 가른다 -- 한/영으로 쪼개진 같은
    # 사건의 국문 고점 대표는 살리되 무근거 크레딧은 막는다.
    if ent_cands:
        ent_cands.sort(reverse=True)
        cont, sc, i, r = ent_cands[0]
        return (i, r), cont, 2
    if tok_best:
        cont, i, r = tok_best
        return (i, r), cont, 0
    return None, 0.0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--threshold", type=float, default=0.25,
                    help="어휘 경로 최종 확인용 포함도 하한(자카드 아님). "
                         "match() 내부가 이미 0.40 을 요구하므로 사실상 상시 통과")
    ap.add_argument("--min-score", type=int, default=6)
    # [추가 2026-07-31] 기계 판독용 출력.
    #   텍스트 리포트는 제목을 46자로 잘라 쓰기 때문에, 다른 스크립트가 결과를
    #   다시 쓰려면 잘린 제목으로 퍼지 매칭을 해야 했다. 그러면 '구제 장치가
    #   실제로 그 기사를 되살렸는가' 같은 질문에 답할 수 없다(실측에서 이 재매칭이
    #   신뢰할 수준이 아니었다). 그래서 매칭된 클러스터의 '온전한 제목'과 순위를
    #   그대로 남긴다. 이후 측정은 이 값으로 정확 비교하면 된다.
    ap.add_argument("--json", action="store_true",
                    help="out/compare-YYYY-MM-DD.json 에 기계 판독용 결과를 함께 저장")
    # [추가 2026-08-01] 한↔영 사건 동일성은 토큰으로 판정할 수 없다.
    #
    #   사람 정답  "Samsung Electronics, SK Group seal $950B deals"
    #   파이프라인 "SK, 빅테크와 7500억달러 반도체 협력…[한·미 AI 동맹]"
    #   -> 같은 사건인데 공통 토큰이 사실상 없다. 숫자 표기도 950B vs 7500억으로 다르다.
    #
    #   07-27 실측: 자동 매칭 3/15 인데 사람이 눈으로 보면 10/15 였다. 측정이 실제를
    #   3배 이상 깎고 있었고, 그 결과 '미수집'으로 잡힌 것 상당수가 실은 국문으로
    #   수집돼 1~9위에 있었다. 이걸 고치지 않으면 어떤 개선도 효과를 잴 수 없다.
    #
    #   그래서 판정을 LLM 에게 넘긴다. 스크립트는 후보를 좁혀 주고(--adjudicate),
    #   LLM 이 내린 판정(out/verdicts-DATE.json)이 있으면 자동으로 반영한다.
    ap.add_argument("--adjudicate", action="store_true",
                    help="자동 매칭이 실패한 정답 기사에 대해 LLM 판정용 후보 목록을 출력")
    a = ap.parse_args()

    rows = load_all_clusters(a.date)
    rows = sorted(rows, key=lambda r: -r["score"])
    truth = load_truth(a.date, a.brief)
    canon = R.build_entity_canon(yaml.safe_load(open(KW, encoding="utf-8")))
    visible = load_visible(a.date)
    uidx = url_index(rows)
    has_url = sum(1 for t in truth if t["url"])

    # LLM 이 내린 사건 동일성 판정. {정답기사 제목: 순위(1-based) 또는 null}
    # null 은 '정말 후보에 없다'는 확정이라 자동 매칭보다 우선한다.
    vpath = os.path.join(OUTDIR, "verdicts-%s.json" % a.date)
    verdicts = {}
    if os.path.exists(vpath):
        verdicts = json.load(open(vpath, encoding="utf-8"))

    out = ["실제 브리프 %d건 vs 파이프라인 %d클러스터 (정답지 링크 %d건, 후보 링크 %d개)"
           % (len(truth), len(rows), has_url, len(uidx)), "=" * 70]
    if verdicts:
        out.append("LLM 사건판정 %d건 반영 (%s)" % (len(verdicts), os.path.basename(vpath)))
    found = passed = seen = confirmed = 0
    scores = []
    records = []          # --json 용. 텍스트 리포트와 1:1 대응하는 원자료.
    for t in truth:
        b = t["title"]
        # [1순위] 링크 일치 = 같은 기사임이 확정. 어휘 추정과 달리 반증 불가.
        i = uidx.get(norm_url(t["url"])) if t["url"] else None
        if b in verdicts:
            # LLM 이 사람 눈으로 확인한 판정. 링크·어휘보다 우선한다.
            v = verdicts[b]
            if v is None:
                m, sim, ent, how = None, 0.0, 0, "LLM판정:없음"
            else:
                k = int(v) - 1
                m, sim, ent, how = (k, rows[k]), 1.0, 0, "LLM판정"
        elif i is not None:
            m, sim, ent, how = (i, rows[i]), 1.0, 0, "링크확정"
            confirmed += 1
        else:
            m, sim, ent = match(b, rows, canon)
            how = "엔티티" if ent >= 2 else "어휘"
            if not (m and (sim >= a.threshold or ent >= 2)):
                m = None
        if m:
            found += 1
            idx, r = m
            scores.append(r["score"])
            ok = r["score"] >= a.min_score
            passed += ok
            track = (visible or {}).get(r.get("title") or "")
            seen += bool(track)
            out.append("%s [%2d점 / %3d위]%s %s"
                       % ("PASS" if ok else "미달", r["score"], idx + 1,
                          (" <%s>" % track) if track else (" <미노출>" if visible else ""),
                          b[:46]))
            out.append("        매칭: %s (%s, 포함도 %.2f, 엔티티 %d)"
                       % ((r.get("title") or "")[:44], how, sim, ent))
            out.append("        근거: %s" % " ".join(r.get("why") or []))
            records.append({
                "truth_title": b,
                "truth_url": t.get("url") or None,
                "matched": True,
                "rank": idx + 1,                       # ranked-*.json 안에서의 순위
                "cluster_title": r.get("title"),       # 자르지 않은 원본 제목(정확 비교용)
                "cluster_summary": r.get("summary"),
                "score": r["score"],
                "passed_threshold": bool(ok),
                "track": track or None,                # 주후보/자사/구제, None 이면 미노출
                "visible_to_llm": bool(track),
                "cluster_has_url": bool(r.get("url")),
                "cluster_url": r.get("url"),
                "cluster_size": r.get("size"),
                "match_method": how,                   # 링크확정/엔티티/어휘
                "match_sim": round(sim, 3),
                "match_entity": ent,
                "why": r.get("why") or [],
            })
        else:
            out.append("없음                    %s" % b[:46])
            n, s2, e2 = match(b, rows, canon)
            if n:
                out.append("        최근접: %s (포함도 %.2f, 엔티티 %d)"
                           % ((n[1].get("title") or "")[:44], s2, e2))
            records.append({
                "truth_title": b,
                "truth_url": t.get("url") or None,
                "matched": False,                      # 수집 자체가 안 된 기사
                "rank": None, "cluster_title": None, "score": None,
                "passed_threshold": False, "track": None, "visible_to_llm": False,
                "cluster_has_url": False, "cluster_url": None,
                "match_method": None,
                "nearest_title": (n[1].get("title") if n else None),
                "nearest_sim": round(s2, 3) if n else None,
                "nearest_entity": e2 if n else None,
            })
    out += ["", "-" * 70,
            "수집 재현율   %d/%d (%.0f%%)" % (found, len(truth), found / len(truth) * 100),
            "임계 통과율   %d/%d (%.0f%%)  (min-score=%d)"
            % (passed, len(truth), passed / len(truth) * 100, a.min_score)]
    if has_url:
        out.append("링크 확정     %d/%d (%.0f%%)  (나머지 %d건은 어휘·엔티티 추정)"
                   % (confirmed, has_url, confirmed / has_url * 100, len(truth) - confirmed))
    if visible is not None:
        out.append("LLM 가시성    %d/%d (%.0f%%)  (주후보+자사+구제 = 프롬프트에 실제로 실린 것)"
                   % (seen, len(truth), seen / len(truth) * 100))
    if scores:
        scores.sort()
        out.append("실린 기사 점수: 최저 %d / 중앙 %d / 최고 %d"
                   % (scores[0], scores[len(scores) // 2], scores[-1]))
        for th in range(3, 12):
            n = sum(1 for s in scores if s >= th)
            out.append("   임계 %2d점이면 %d/%d 통과 (%.0f%%)"
                       % (th, n, len(truth), n / len(truth) * 100))
    txt = "\n".join(out)
    open(os.path.join(OUTDIR, "compare-%s.txt" % a.date), "w",
         encoding="utf-8").write(txt)
    print(txt)

    if a.adjudicate:
        # 자동 매칭이 실패했고 아직 판정도 없는 기사에 대해, LLM 이 볼 후보를 좁힌다.
        #
        # 좁히는 기준은 '엔티티 1개 이상 공유'다. 토큰 겹침은 한↔영에서 0 이 되지만
        # 엔티티 정규화(엔비디아=NVIDIA)는 언어를 건너뛴다. 다만 1개만으로는
        # 근거가 약해 자동 판정에는 못 쓴다 - 그래서 '후보 추림'에만 쓰고
        # 최종 판단은 LLM 이 한다. 엔티티가 아예 없으면 점수 상위로 채운다.
        # 언어를 건너뛰는 신호 두 가지를 쓴다.
        #   (1) 엔티티 정규화  - 엔비디아 = NVIDIA
        #   (2) 숫자·영문 토큰 - 번역돼도 살아남는다.
        #       "Doubled To 50" 과 "참여 기업 50곳으로 확대" 는 {50} 을 공유한다.
        # 엔티티만으로 점수순 정렬하면 대형 클러스터가 앞을 차지해, 정작 맞는
        # 저점수 클러스터(위 사례에서 110위)가 목록 밖으로 밀린다.
        def xling(s):
            return set(re.findall(r"\d{2,}", s or "")) | \
                {w.lower() for w in re.findall(r"[A-Za-z]{3,}", s or "")
                 if w.lower() not in STOP_EN}

        need = [rec for rec in records
                if not rec["matched"] and rec["truth_title"] not in verdicts]
        book = []
        for rec in need:
            be = R.entity_ids(rec["truth_title"], canon)
            bx = xling(rec["truth_title"])
            scored = []
            for i, r in enumerate(rows[:1200]):
                ov = len(be & R.entity_ids(r.get("title") or "", canon))
                xv = len(bx & xling(r.get("title") or ""))
                if ov or xv:
                    scored.append((ov + xv, ov, r["score"], i, r))
            scored.sort(reverse=True)
            picks = [(a2, b2, i, r) for a2, b2, _, i, r in scored[:12]] or \
                    [(0, 0, i, r) for i, r in enumerate(rows[:8])]
            book.append({
                "truth_title": rec["truth_title"],
                "truth_url": rec["truth_url"],
                "candidates": [{"rank": i + 1, "score": r["score"],
                                "title": r.get("title"),
                                "outlet": r.get("outlet"),
                                "signal": sg, "entity_overlap": ov}
                               for sg, ov, i, r in picks],
            })
        ap2 = os.path.join(OUTDIR, "adjudicate-%s.json" % a.date)
        json.dump(book, open(ap2, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print()
        print("[adjudicate] 판정 필요 %d건 -> %s" % (len(book), ap2))
        for e in book:
            print()
            print("  정답: %s" % e["truth_title"][:78])
            for c in e["candidates"][:6]:
                print("     %4d위 %2d점 (신호%d) %s"
                      % (c["rank"], c["score"], c["signal"],
                         (c["title"] or "")[:56]))
        print()
        print("[adjudicate] 같은 사건인 후보의 '위' 숫자를, 없으면 null 을")
        print("             out/verdicts-%s.json 에 {정답제목: 순위} 로 저장하세요."
              % a.date)

    if a.json:
        # 손실을 한 가지 사유로 분류한다. 순서가 곧 우선순위다.
        #   미수집    -> 애초에 후보에 없었다 (수집 커버리지 문제)
        #   링크없음  -> 후보에는 있는데 링크가 없어 프롬프트에서 제외됐다
        #                (brief.py 와 rescue 가 모두 url 보유를 요구한다)
        #   순위밖    -> 링크는 있는데 컷/순위에 밀렸다
        #   노출됨    -> LLM 이 실제로 봤다
        for rec in records:
            if not rec["matched"]:
                rec["loss_reason"] = "미수집"
            elif rec["visible_to_llm"]:
                rec["loss_reason"] = "노출됨"
            elif not rec["cluster_has_url"]:
                rec["loss_reason"] = "링크없음"
            else:
                rec["loss_reason"] = "순위밖"
        agg = {}
        for rec in records:
            agg[rec["loss_reason"]] = agg.get(rec["loss_reason"], 0) + 1
        jp = os.path.join(OUTDIR, "compare-%s.json" % a.date)
        json.dump({"date": a.date, "min_score": a.min_score,
                   "truth_count": len(truth), "found": found,
                   "passed": passed, "visible": seen,
                   "loss_breakdown": agg, "items": records},
                  open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print()
        print("[json] 손실 분류: %s" % agg)
        print("[json] 저장: %s" % jp)


if __name__ == "__main__":
    main()
