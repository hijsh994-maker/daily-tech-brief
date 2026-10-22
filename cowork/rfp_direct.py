# -*- coding: utf-8 -*-
"""[2026-10-22 신설] 국책과제 ① — 사이트 직접 확인.

왜 생겼나 — 10-22 ① 이 ai-rfp-fetch 저장소의 수집본(10-21 11:13자)만 읽고 사이트는 열지
않았다. 사용자: *"지금 하나의 작업으로 통합한거라 니가 직접 국책과제 관련 사이트에 들어가서
확인을 해야해! 다른 작업에서 한 결과만 읽어오는게 아니고!!!"*
그날 기업마당에는 수집본 뒤에 올라온 10-21 등록분이 15건 더 있었다(수집본 + 사이트 둘 다 봐야 한다).

하는 일 — 과기정통부·IITP·IRIS 세 사이트의 **공고 목록**을 직접 받아 `--since` 이후 공고를 찍는다
(ai-rfp-fetch/fetch.py 와 같은 주소. 실측 10-22 클라우드에서 세 곳 모두 열림).
기업마당은 이 컨테이너의 직접 접속을 끊으므로(ConnectionReset) 여기서 받지 않는다 —
에이전트가 WebFetch 로 목록 1페이지(`selectSIIA200View.do?rbsIdx=128`)를 연다(실측 10-22 열림, 15건/페이지).

사용법:  bash cowork/run.sh rfp-direct            # since = 전일(월요일이면 금요일)
         bash cowork/run.sh rfp-direct 2026-10-20  # since 지정
종료코드: 0 정상 / 1 세 사이트 전부 실패
"""
import re, json, html, sys, datetime as dt
import requests

KST = dt.timezone(dt.timedelta(hours=9))
T = 40
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def strip(t):
    return re.sub(r"<[^>]+>", " ", t or "")


def msit(s, since):
    base = "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=121&mId=311&pageIndex={}"
    s.get(base.format(1), timeout=T)
    out = []
    for page in (1, 2):
        h = s.get(base.format(page), timeout=T).text
        ids = re.findall(r"\$\('#td_'\+'NTT_SJ'\+'_(\d+)'\)\.html\('<a [^']*?fn_detail\(\"?(\d+)\"?\)[^']*?<span>(.*?)</span>", h)
        dates = dict(re.findall(r"\$\('#td_'\+'REG_DT'\+'_(\d+)'\)\.html\('(\d{4}-\d{2}-\d{2})'\)", h))
        depts = dict(re.findall(r"\$\('#td_'\+'CHRG_DEPT_NM'\+'_(\d+)'\)\.html\('([^']+)'\)", h))
        oldest = None
        for idx, nid, title in ids:
            reg = dates.get(idx, "")
            oldest = reg or oldest
            if reg >= since:
                out.append((reg, nid, depts.get(idx, ""), html.unescape(strip(title)).strip(),
                            f"https://www.msit.go.kr/bbs/view.do?sCode=user&mPid=121&mId=311&nttSeqNo={nid}"))
        if oldest and oldest < since:
            break
    return out


def iitp(s, since):
    pg = "https://www.iitp.kr/web/lay1/program/S1T44C51/iris/list.do"
    page = s.get(pg, timeout=T).text
    tok = re.search(r'<meta name="_csrf" content="([^"]+)"', page)
    hdr = re.search(r'<meta name="_csrf_header" content="([^"]+)"', page)
    headers = {"Content-Type": "application/json", "Referer": pg}
    if tok and hdr:
        headers[hdr.group(1)] = tok.group(1)
    r = s.post("https://www.iitp.kr/link-svc/api/iris/list.do", headers=headers, timeout=T,
               data=json.dumps(dict(cms_menu_seq="51", cpage=1, rows=30, keyword="", condition="", sort="latest")))
    out = []
    for it in r.json().get("list") or []:
        ms = it.get("regist_date")
        reg = dt.datetime.fromtimestamp(ms / 1000, KST).strftime("%Y-%m-%d") if ms else ""
        if reg >= since:
            out.append((reg, it.get("id"), f'{it.get("receipt_begin_date", "")}~{it.get("receipt_end_date", "")}',
                        html.unescape(it.get("title", "")),
                        f'https://www.iitp.kr/web/lay1/program/S1T44C51/iris/view.do?id={it.get("id")}'))
    return out


def iris(s, since):
    s.get("https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do", timeout=T)
    out = []
    for prg, tab in (("ancmIng", "접수중"), ("ancmPre", "접수예정")):
        for page in (1, 2):
            r = s.post("https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do",
                       data=dict(ancmPrg=prg, pageIndex=page, pageUnit=50), timeout=T)
            lst = r.json().get("listBsnsAncmBtinSitu") or []
            oldest = None
            for it in lst:
                reg = it.get("ancmDe", "")
                oldest = reg or oldest
                if reg >= since:
                    out.append((tab, reg, it.get("ancmId"), f'{it.get("blngGovdSeNm")} / {it.get("sorgnNm")}',
                                f'{it.get("rcveStrDe")}~{it.get("rcveEndDe")}', it.get("ancmNo"),
                                html.unescape(it.get("ancmTl", "")),
                                f'https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={it.get("ancmId")}'))
            if not lst or (oldest and oldest < since):
                break
    return out


def main():
    today = dt.datetime.now(KST).date()
    if len(sys.argv) > 1 and sys.argv[1].strip():
        since = sys.argv[1].strip()
    else:
        d = today - dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        since = d.isoformat()
    print(f"[rfp-direct] {today} / 공고일 {since} 이후를 사이트에서 직접 확인 (과기정통부·IITP·IRIS)")
    print("[rfp-direct] 기업마당은 이 서버의 직접 접속을 끊음 — WebFetch 로 "
          "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do?rbsIdx=128 (목록 1페이지 15건)을 따로 연다")
    s = requests.Session()
    s.headers["User-Agent"] = UA
    fails = 0
    for name, fn in (("과기정통부", msit), ("IITP", iitp), ("IRIS", iris)):
        try:
            rows = fn(s, since)
            print(f"\n=== {name}: 범위 내 {len(rows)}건 ===")
            for r in rows:
                print("  " + " | ".join(str(x) for x in r))
        except Exception as e:
            fails += 1
            print(f"\n=== {name}: 확인 불가 ({type(e).__name__}: {e}) ===")
    print("\n[rfp-direct] 이 목록은 저장소 수집본(ai-rfp-fetch/out/latest/index.md)과 **합쳐서** 본다. "
          "한쪽에만 있는 공고가 있으면 그쪽이 빠진 것이다.")
    sys.exit(1 if fails == 3 else 0)


if __name__ == "__main__":
    main()
