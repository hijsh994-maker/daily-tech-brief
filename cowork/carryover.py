#!/usr/bin/env python3
"""전일 '고' 미발송분 이월 — 다음 영업일 발송본에 반드시 싣는다. [2026-10-27 신설]

[왜] 10-27 발송(message_id 124, 12건) 뒤 사용자 정정·rescue·late 재점검으로 '고' 9건이 늘었다
  (메타 950억달러·브로드컴 630억달러·MS 6만명 희망퇴직·통신사 토큰·도요타 휴머노이드·셀렉트스타 다비·
  LG 로봇·엔비디아 플로라·KT 토큰팩토리). 사용자: *"오늘 발송되지 않았지만 고로 판정된거는 내일 발송할떄 포함하구"*

[무엇을]
  save   <원래날짜>  : 그날 verdict '고' 중 발송본(brief)에 없는 건을 config/carryover.json 에 쌓는다.
                      헤드라인·매체·링크·발행일·카테고리·사유·**본문**(bodycache 는 커밋 안 되므로 같이 저장).
                      --override idx=url|헤드라인|매체 로 링크를 대체할 수 있다(find·late 로 찾은 원문).
  inject [오늘]     : 아직 안 실린 이월 건을 오늘 prompt 에 {"idx":"C1",…,"tier":"carryover"} 로 넣고 본문을
                      bodycache 에 넣는다. blind 목록 맨 앞 '=== 전일이월 ===' 그룹. run.sh blind 앞에 돈다.
  check  [오늘]     : 오늘 발송본에 이월 건이 전부 들어갔는지 링크로 대조. 빠지면 종료코드 1.
  done   [오늘]     : 발송 뒤 오늘 발송본에 실린 이월 건을 consumed 로 표시(다음 날 다시 안 나온다).

  판정 규칙: 이월 건은 사용자가 '고'로 확정한 건이라 **등급을 다시 내리지 않는다.** 사유에 `이월:<원래날짜>`.
  rank 는 오늘 '고'와 같이 매기되 **분량 컷 대상이 아니다**(메시지가 넘치면 오늘 건 하위부터 뺀다).
  단, 같은 사건이 오늘 더 새로운 기사로 왔으면 그 기사로 바꿔 싣고 이월 건은 '중복 대표<idx>' + 이월대체.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "out")
CO = os.environ.get("DTB_CO") or os.path.join(BASE, "config", "carryover.json")   # run.sh 가 마운트 쪽 경로를 준다(커밋 대상)


def load():
    return json.load(open(CO, encoding="utf-8")) if os.path.exists(CO) else []


def dump(items):
    with open(CO, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def brief_urls(day):
    p = os.path.join(OUT, "brief-%s.md" % day)
    if not os.path.exists(p):
        return set()
    return set(re.findall(r"\]\((https?://[^)]+)\)", open(p, encoding="utf-8").read()))


def prompt_meta(day):
    meta = {}
    p = os.path.join(OUT, "prompt-%s.txt" % day)
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line.startswith('{"idx"'):
            d = json.loads(line)
            meta[str(d["idx"])] = d
        elif line.startswith('{"nolink_idx"'):
            d = json.loads(line)
            d["outlet"] = d.get("outlet_guess")
            meta["N%s" % d["nolink_idx"]] = d
    return meta


def clean_head(h):
    h = (h or "").strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return re.sub(r"\s+-\s+(머니투데이|네이트 뉴스)$", "", h)


def cmd_save(day, overrides):
    meta = prompt_meta(day)
    sent = brief_urls(day)
    cache_p = os.path.join(OUT, "bodycache-%s.json" % day)
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    hr = {}
    hp = os.path.join(OUT, "handrank-%s.csv" % day)
    if os.path.exists(hp):
        for r in csv.DictReader(open(hp, encoding="utf-8")):
            hr[r["idx"]] = r.get("발송순위", "")
    items = load()
    have = {(x["from"], x["idx"]) for x in items}
    added = 0
    for r in csv.DictReader(open(os.path.join(OUT, "verdict-%s.csv" % day), encoding="utf-8")):
        if r["grade"].strip() != "고":
            continue
        i = r["idx"].strip()
        m = meta.get(i, {})
        url, head, outlet = m.get("url") or "", clean_head(m.get("headline")), m.get("outlet") or ""
        if i in overrides:
            url, head, outlet = overrides[i]
        if url and url in sent:
            continue
        if (day, i) in have:
            continue
        if not url:
            print("[carryover] %s 링크 없음 — --override 로 원문을 주세요: %s" % (i, head))
            continue
        items.append(dict(**{"from": day, "idx": i, "headline": head, "outlet": outlet, "url": url,
                             "pub_date": m.get("pub_date") or day, "category": m.get("category"),
                             "reason": r["reason"], "prev_rank": hr.get(i, r.get("rank", "")),
                             "body": cache.get(url) or cache.get(m.get("url") or "") or "",
                             "consumed": None}))
        added += 1
    dump(items)
    print("[carryover] %s 미발송 '고' %d건 저장 -> %s (대기 %d건)"
          % (day, added, CO, sum(1 for x in items if not x["consumed"])))
    for x in items:
        if not x["consumed"]:
            print("   %s/%s  %s (%s) 본문 %d자" % (x["from"], x["idx"], x["headline"][:50], x["outlet"], len(x["body"])))


def cmd_inject(day):
    items = [x for x in load() if not x["consumed"] and x["from"] < day]
    pp = os.path.join(OUT, "prompt-%s.txt" % day)
    if not items:
        print("[carryover] 이월 대기 0건")
        return
    prompt = open(pp, encoding="utf-8").read()
    if '"tier": "carryover"' in prompt or '"tier": "userpick"' in prompt:
        print("[carryover] 이미 넣었음. 건너뜀")
        return
    cache_p = os.path.join(OUT, "bodycache-%s.json" % day)
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    lines = []
    for k, x in enumerate(items, 1):
        x["today_idx"] = "C%d" % k
        opt = bool(x.get("optional"))   # [2026-10-29] 사용자 추천(여유시): 강제 포함·컷 면제 아님
        lines.append(json.dumps({"idx": "C%d" % k, "category": x["category"], "headline": x["headline"],
                                 "outlet": x["outlet"], "url": x["url"], "pub_date": x["pub_date"],
                                 "n_outlets": 1, "score": None, "tier": "userpick" if opt else "carryover",
                                 "signals": [("사용자추천(여유시):%s" if opt else "전일이월:%s/%s") % ((x["from"],) if opt else (x["from"], x["idx"]))]},
                                ensure_ascii=False))
        if x["body"]:
            cache[x["url"]] = x["body"]
    json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False)
    forced = [l for l in lines if '"tier": "carryover"' in l]
    optl = [l for l in lines if '"tier": "userpick"' in l]
    block = ""
    if forced:
        block += "\n[전일 이월 '고' — 사용자 지시로 오늘 발송본에 반드시 포함 (run.sh carryover)]\n" + "\n".join(forced) + "\n"
    if optl:
        block += ("\n[사용자 추천 — 여유가 있을 때만 싣는다. 강제 포함·분량 컷 면제 아님. 다른 후보와 똑같이 판정하고,"
                  " '고'면 rank 를 매겨 20건·한 메시지 안에 들어갈 때만 싣는다. 발송본에 이미 같은 사건이 있으면 중복 대표로 둔다]\n"
                  + "\n".join(optl) + "\n")
    marker = "\n\n[링크 미확보 후보]"
    prompt = (prompt.replace(marker, "\n" + block + marker, 1) if marker in prompt else prompt + "\n" + block)
    open(pp, "w", encoding="utf-8").write(prompt)
    all_items = load()
    for a in all_items:
        for x in items:
            if a["from"] == x["from"] and a["idx"] == x["idx"]:
                a["today_idx"] = x["today_idx"]
    dump(all_items)
    nf = sum(1 for x in items if not x.get("optional"))
    print("[carryover] %s 에 %d건 추가(C1~C%d) — 이월 '고' %d건(등급 유지·분량 컷 면제) / 사용자 추천(여유시) %d건(일반 판정·컷 대상)"
          % (day, len(items), len(items), nf, len(items) - nf))
    for x in items:
        print("   %s  %s%s (%s)" % (x["today_idx"], "[여유시] " if x.get("optional") else "", x["headline"][:60], x["outlet"]))


def cmd_check(day, mark=False):
    items = [x for x in load() if not x["consumed"] and x["from"] < day]
    sent = brief_urls(day)
    # [2026-10-28] 같은 사건의 더 새로운 기사로 대체한 이월 건(판정 사유 '이월대체')은 포함으로 본다.
    repl = set()
    vp = os.path.join(OUT, "verdict-%s.csv" % day)
    if os.path.exists(vp):
        for r in csv.DictReader(open(vp, encoding="utf-8")):
            if "이월대체" in (r.get("reason") or ""):
                repl.add(r["idx"])
    miss = [x for x in items if x["url"] not in sent and x.get("today_idx") not in repl and not x.get("optional")]
    print("[carryover] 이월 %d건 중 오늘 발송본 포함 %d건 / 누락 %d건"
          % (len(items), len(items) - len(miss), len(miss)))
    for x in miss:
        print("   [누락] %s %s/%s %s" % (x.get("today_idx", "?"), x["from"], x["idx"], x["headline"][:60]))
    if mark:
        allx = load()
        for a in allx:
            if not a["consumed"] and a["from"] < day and (a["url"] in sent or a.get("today_idx") in repl or a.get("optional")):   # 여유시 추천은 하루만 검토
                a["consumed"] = day
        dump(allx)
        print("[carryover] 발송 반영: consumed=%s 표시" % day)
    return 1 if (miss and not mark) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["save", "inject", "check", "done", "list"])
    ap.add_argument("day", nargs="?", default=None)
    ap.add_argument("--override", action="append", default=[],
                    help="idx=url|헤드라인|매체 (링크 대체)")
    a = ap.parse_args()
    day = a.day or datetime.date.today().isoformat()
    ov = {}
    for o in a.override:
        k, v = o.split("=", 1)
        u, h, m = (v.split("|") + ["", ""])[:3]
        ov[k] = (u, h, m)
    if a.mode == "save":
        cmd_save(day, ov)
    elif a.mode == "inject":
        cmd_inject(day)
    elif a.mode == "check":
        sys.exit(cmd_check(day))
    elif a.mode == "done":
        sys.exit(cmd_check(day, mark=True))
    else:
        for x in load():
            print(x["from"], x["idx"], x.get("today_idx", ""), x["consumed"], x["headline"][:60])


if __name__ == "__main__":
    main()
