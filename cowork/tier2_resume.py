# -*- coding: utf-8 -*-
"""tier2_resume.py - tier2 를 45초 제한 안에서 나눠 돌리는 재개형 러너.

## 왜 필요한가
Cowork 샌드박스는 bash 호출 1건당 45초 제한이고, 호출이 끝나면 백그라운드
프로세스도 함께 죽는다(PID 네임스페이스가 --die-with-parent 로 묶여 있어
nohup/setsid 도 소용없다). 그런데 tier2 는 구글뉴스 쿼리 89개라
tier2_par(workers=8)로도 60~90초가 걸려 한 번에 완주할 수 없다.
workers 를 20 으로 올려도 마찬가지였다(2026-08-03 실측).

## 무엇을 하는가
--deadline 초 안에 끝낸 쿼리 결과만 /tmp/t2cache-DATE.json 에 누적 저장한다.
다시 실행하면 캐시에 없는 쿼리만 돈다. 3~4회 반복하면 89개가 다 찬다.
--finalize 는 tier2_par 과 완전히 동일한 취합·필터 로직을 태워
out/tier2-DATE.json 을 만든다(스키마 동일. refine.py 는 차이를 모른다).

## 사용
  bash cowork/run.sh tier2        # 내부에서 이 스크립트를 반복 호출한다
  # 수동:
  python3 cowork/tier2_resume.py --date 2026-08-03 --window 1d --deadline 33
  ...  (남음 0 이 될 때까지 반복)
  python3 cowork/tier2_resume.py --date 2026-08-03 --finalize
"""
import argparse, datetime, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import truststore; truststore.inject_into_ssl()
import yaml
import samsung_resolve as SR
import ingest_tier2 as T2
import datewin as DW

OUTDIR = os.path.join(BASE, "out")
KW = os.path.join(BASE, "config", "keywords.yaml")


def log(m):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), m))
    sys.stdout.flush()


# 구글뉴스 RSS 는 쿼리당 최대 100건만 준다. 최신순이라 창을 넓히면
# 오래된 날짜가 통째로 잘려 나간다.
#   실측 2026-08-03(창 3일): 'AI 데이터센터' 100건의 날짜분포가
#   08-01:3 / 08-02:24 / 08-03:73 이고 07-31 은 0건.
#   그 결과 사람이 실은 '중국도 우주 AI 데이터센터 눈 돌려'(07-31)가
#   후보에 아예 오르지 못했다. 92개 쿼리 중 36개가 상한에 걸려 있었다.
# 대응: 상한에 걸렸고 가장 오래된 결과가 창 시작보다 늦은 쿼리에 대해서만
#       빠진 날짜를 'after:/before:' 로 하루씩 다시 긁는다(적응형 백필).
#       전 쿼리를 일자별로 쪼개면 92 x N 이라 45초 예산에 안 맞는다.
GNEWS_CAP = 100


def backfill_tasks(cache, qs, lo):
    """(group, base_q, 실제쿼리, 날짜문자열) 목록."""
    out = []
    for group, q in qs:
        rec = cache.get(q)
        if not rec or rec.get("err"):
            continue
        items = rec.get("items") or []
        if len(items) < GNEWS_CAP:
            continue                      # 상한에 안 걸렸으면 누락 없음
        ds = [datetime.date.fromisoformat(i["date"]) for i in items if i.get("date")]
        if not ds:
            continue
        oldest = min(ds)
        d = lo
        while d < oldest:
            key = "%s|%s" % (q, d.isoformat())
            if key not in cache:
                # 구글의 after:/before: 는 경계 배타적이라 하루를 양쪽으로 넓힌다.
                rq = "%s after:%s before:%s" % (
                    q, d - datetime.timedelta(days=1), d + datetime.timedelta(days=1))
                out.append((group, q, rq, d.isoformat()))
            d += datetime.timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--window", default="1d")
    ap.add_argument("--workers", type=int, default=8)
    # [2026-08-24] 35 -> 120. bash 호출 상한은 45초가 아니라 약 178초다.
    #   (run.sh links 분기 주석에 실측 근거. 오버슈트는 as_completed +
    #    shutdown(wait=False) 구조라 1~2초로 작다.)
    ap.add_argument("--deadline", type=float, default=120.0)
    ap.add_argument("--finalize", action="store_true")
    a = ap.parse_args()

    day = datetime.date.fromisoformat(a.date)
    cfg = yaml.safe_load(open(KW, encoding="utf-8"))
    pol = cfg.get("policy", {})
    # 창 길이는 datewin 이 공휴일 보정까지 계산한다(연휴 다음날 확대).
    win = DW.window_days(pol, day)
    lo, hi = DW.window(pol, day)
    allow_undated = pol.get("allow_undated_raw", False)
    qs = T2.all_queries(cfg)

    # [2026-09-14] /tmp 최상단 고정 이름은 다른 세션(uid 다름)이 만든 파일과
    #   충돌해 PermissionError 로 죽는다. 작업본 out/ 아래로 옮긴다.
    cache_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "out", "t2cache-%s.json" % day.isoformat())
    if not os.path.exists(cache_path):
        _legacy = "/tmp/t2cache-%s.json" % day.isoformat()
        if os.path.exists(_legacy):
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(_legacy, encoding="utf-8") as _f:
                    open(cache_path, "w", encoding="utf-8").write(_f.read())
            except Exception:
                pass
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}

    if not a.finalize:
        todo = [(g, q, q, None) for g, q in qs if q not in cache]
        # 기본 쿼리가 다 찬 뒤에야 '어느 쿼리가 상한에 걸렸는지' 알 수 있다.
        # 남은 기본 쿼리가 없을 때만 백필 대상을 계산한다.
        back = backfill_tasks(cache, qs, lo) if not todo else []
        todo += back
        log("전체 %d / 캐시 %d / 남은 %d%s"
            % (len(qs), len(cache), len(todo),
               " (일자별 백필 %d)" % len(back) if back else ""))
        t0 = time.time()
        done = 0

        def fetch(item):
            group, base_q, real_q, dstr = item
            loc = "en" if group == "en_track" else "ko"
            key = base_q if dstr is None else "%s|%s" % (base_q, dstr)
            # 백필(dstr!=None)은 쿼리에 after:/before: 가 들어 있으므로
            # when: 을 붙이면 안 된다. when: 이 이겨서 날짜 지정이 무시된다.
            win = a.window if dstr is None else None
            try:
                return key, group, loc, base_q, SR.gnews(real_q, win, locale=loc), None
            except Exception as e:
                return key, group, loc, base_q, [], str(e)[:60]

        ex = ThreadPoolExecutor(max_workers=a.workers)
        futs = [ex.submit(fetch, it) for it in todo]
        try:
            for f in as_completed(futs, timeout=max(a.deadline, 1)):
                try:
                    key, group, loc, base_q, items, err = f.result()
                except Exception:
                    continue
                cache[key] = {"group": group, "loc": loc, "err": err, "base_q": base_q,
                              "items": [{"headline": c["headline"], "outlet": c["outlet"],
                                         "date": str(c["date"]) if c["date"] else None}
                                        for c in items]}
                done += 1
                if time.time() - t0 > a.deadline:
                    break
        except Exception:
            pass
        for f in futs:
            f.cancel()
        ex.shutdown(wait=False)
        json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
        base_done = sum(1 for _, q in qs if q in cache)
        remain = (len(qs) - base_done) or len(backfill_tasks(cache, qs, lo))
        log("이번 회차 %d건 처리, 기본쿼리 %d / %d, 캐시 총 %d (%.0f초)"
            % (done, base_done, len(qs), len(cache), time.time() - t0))
        if remain:
            log("남음 %d개 - 다시 실행하세요" % remain)
        else:
            log("전부 완료 - --finalize 를 실행하세요")
        os._exit(0)

    # ---- finalize: tier2_par 과 동일한 취합 ----
    pool, errs = {}, []
    dropped_date = dropped_rel = dropped_src = 0
    for _g, _q in qs:
        if _q not in cache:
            errs.append((_q, "미수집"))
    # 캐시에는 기본 쿼리(키=q)와 일자별 백필(키='q|YYYY-MM-DD')이 섞여 있다.
    # 둘 다 같은 취합 로직을 타고, queries/groups 는 base_q 기준으로 묶는다.
    for key, rec in cache.items():
        if rec.get("err"):
            errs.append((key, rec["err"]))
            continue
        group = rec.get("group")
        q = rec.get("base_q") or key.split("|")[0]
        loc = rec["loc"]
        for c in rec["items"]:
            d = datetime.date.fromisoformat(c["date"]) if c["date"] else None
            if d is None:
                if not allow_undated:
                    dropped_date += 1
                    continue
            elif not (lo <= d <= hi):
                dropped_date += 1
                continue
            if SR.source_rank(c["outlet"]) < 0 or SR.is_roundup(c["headline"]):
                dropped_src += 1
                continue
            ok, _ = T2.relevant(c["headline"], cfg)
            if not ok:
                dropped_rel += 1
                continue
            k = T2.norm_key(c["headline"])
            if not k:
                continue
            if k in pool:
                if q not in pool[k]["queries"]:
                    pool[k]["queries"].append(q)
                    pool[k]["groups"] = sorted(set(pool[k]["groups"]) | {group})
            else:
                pool[k] = {"src": "gnews", "title": c["headline"],
                           "outlet": c["outlet"], "pub_date": c["date"],
                           "queries": [q], "groups": [group], "locale": loc,
                           "cluster": 1, "url": None, "confidence": "unresolved"}

    items = list(pool.values())
    multi = sum(1 for x in items if len(x["queries"]) > 1)
    log("완료: 고유 %d건 (복수쿼리 %d) | 날짜탈락 %d / 소스탈락 %d / 노이즈탈락 %d"
        % (len(items), multi, dropped_date, dropped_src, dropped_rel))
    if errs:
        log("실패/미수집 쿼리 %d개" % len(errs))
    os.makedirs(OUTDIR, exist_ok=True)
    p = os.path.join(OUTDIR, "tier2-%s.json" % day.isoformat())
    json.dump({"date": day.isoformat(), "query_count": len(qs), "items": items},
              open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("저장: %s" % p)
    os._exit(0)


if __name__ == "__main__":
    main()
