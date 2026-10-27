#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전수 판정 결과(verdict-<날짜>.csv)를 엑셀로 만든다.

2026-09-04 사용자 지시:
  '앞으로 뉴스 리스트는 본문으로 안 보여줘도 되고, 대신 고/중/저 판단 결과를
   엑셀로 만들어서 항상 같이 줘.'

입력: out/verdict-<날짜>.csv, out/candidates-blind-<날짜>.txt,
      out/prompt-<날짜>.txt, out/brief-<날짜>.md
출력: out/판정-<날짜>.xlsx   (시트: 요약 / 전수판정 / 발송본)

사용: python3 cowork/verdict_xlsx.py --date 2026-09-04
"""
import argparse, csv, datetime, json, os, re, sys
from collections import Counter, OrderedDict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

FONT = "Arial"
GRADE_FILL = {
    "고": PatternFill("solid", fgColor="FFC7CE"),
    "중": PatternFill("solid", fgColor="FFEB9C"),
    "저": PatternFill("solid", fgColor="EEEEEE"),
}
HDR_FILL = PatternFill("solid", fgColor="D9D9D9")


def parse_blind(path):
    """idx -> (통로, 매체, 발행일, 보도, 헤드라인, 링크없음, 중복의심, 체급, 반복후보)

    [2026-09-22] 헤드라인에 섞여 있던 두 가지를 열로 분리한다.
      - `⟨프런티어⟩`·`⟨빅테크⟩` 같은 **체급 표시**. 사용자 지시로 분리.
        주체가 이 바닥에서 누구인가를 나타내는 사실값이지 제목이 아니다.
      - `[반복후보 N건·미발송 MM-DD~MM-DD]` — timeline_marks 가 붙인 표시.
        과거 여러 날 후보였는데 한 번도 안 실린 이야기라는 뜻이다.
    """
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        if not re.match(r"^[NL]?\d+ \| ", line):
            continue
        parts = [p.strip() for p in line.rstrip("\n").split(" | ", 5)]
        if len(parts) < 6:
            continue
        idx, route, outlet, pub, cnt, head = parts
        nolink = "[링크없음]" in head
        m = re.search(r"\[중복의심: (.+?)\]\s*$", head)
        dup = m.group(1) if m else ""
        head = re.sub(r"\s*\[중복의심: .+?\]\s*$", "", head)
        head = head.replace("[링크없음]", "").strip()
        mrep = re.search(r"\[(?:반복후보|후보만) ([^\]]+)\]", head)
        rep = mrep.group(1) if mrep else ""
        head = re.sub(r"\s*\[(?:반복후보|후보만) [^\]]+\]", "", head).strip()
        mt = re.match(r"^\u27e8(.+?)\u27e9\s*", head)
        tier = mt.group(1) if mt else ""
        if mt:
            head = head[mt.end():].strip()
        if head.startswith("[") and head.endswith("]"):
            head = head[1:-1].strip()
        out[idx] = (route, outlet, pub, cnt, head, "Y" if nolink else "", dup,
                    tier, rep)
    return out


def parse_prompt(path):
    """idx -> url, category, 기계점수(참고용)"""
    urls, cats, mscore = {}, {}, {}
    if not os.path.exists(path):
        return urls, cats, mscore
    for line in open(path, encoding="utf-8"):
        if line.startswith('{"idx"'):
            try:
                o = json.loads(line)
            except Exception:
                continue
            urls[str(o["idx"])] = o.get("url") or ""
            cats[str(o["idx"])] = o.get("category") or ""
            mscore[str(o["idx"])] = o.get("score", "")
        elif line.startswith('{"nolink_idx"'):
            try:
                o = json.loads(line)
            except Exception:
                continue
            cats["N%s" % o["nolink_idx"]] = o.get("category") or ""
            mscore["N%s" % o["nolink_idx"]] = o.get("score", "")
    return urls, cats, mscore


def load_rankscore(d):
    """out/rankscore-<날짜>.csv -> {idx: {...}}  [2026-09-10 신설]

    스키마가 두 번 바뀌었으므로 둘 다 받는다.
      신(09-09~) : rank,idx,score,route,spread,cat,tags,headline
      구(~09-08) : idx,W,I,S,C,D,total,tags   (손으로 매긴 배점. 폐지됨)

    신 스키마는 `score` 하나만 주므로, 그 점수가 **어디서 왔는지**를
    config/rankweights.json 으로 되짚어 통로·보도폭·태그 기여로 쪼갠다.
    엑셀에서 순위를 재현할 수 있어야 '왜 42위인가'를 눈으로 따진다.
    """
    p = "out/rankscore-%s.csv" % d
    if not os.path.exists(p):
        return {}
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    if not rows:
        return {}
    W = {}
    try:
        W = json.load(open("config/rankweights.json", encoding="utf-8"))["weights"]
    except Exception:
        pass
    out = {}
    if "score" in rows[0]:                      # 신 스키마
        for r in rows:
            route, spread = r.get("route", ""), r.get("spread", "")
            tags = r.get("tags", "")
            w_route = W.get("route", {}).get(route, 0.0)
            w_spread = W.get("spread", {}).get(spread, 0.0)
            try:
                total = float(r.get("score") or 0.0)
            except ValueError:
                total = 0.0
            ntag = len([t for t in tags.split("+") if t])
            out[r["idx"]] = {
                "total": round(total, 3),
                "route_w": round(w_route, 3),
                "spread_w": round(w_spread, 3),
                "tag_w": round(total - w_route - w_spread, 3),
                "ntag": ntag,
                "tags": tags,
            }
    else:                                        # 구 스키마(폐지된 수기 배점)
        for r in rows:
            out[r["idx"]] = {
                "total": r.get("total", ""), "route_w": "", "spread_w": "",
                "tag_w": "", "ntag": "", "tags": r.get("tags", ""),
            }
    return out


def parse_brief(path):
    """발송본에 실린 URL 집합과 (카테고리, 번호, 헤드라인, 매체, url) 목록"""
    rows, used = [], set()
    if not os.path.exists(path):
        return rows, used
    cat = ""
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.match(r"^\*\*■\s*(.+?)\*\*$", line)
        if m:
            cat = m.group(1).strip()
            continue
        m = re.match(r"^\*\*(\d+)\\\.\s*(.+?)\*\*\s*\((.+?),\s*\[원문보기\]\((.+?)\)\)\s*$", line)
        if m:
            num, head, outlet, url = m.groups()
            rows.append((cat, int(num), head, outlet, url))
            used.add(url)
            continue
        m = re.match(r"^[📌▪•] \*\*주요 내용:\*\*\s*(.+)$", line)
        if m and rows:
            rows[-1] = rows[-1] + (m.group(1),)
            continue
        m = re.match(r"^[📌▪•] \*\*시사점:\*\*\s*(.+)$", line)
        if m and rows:
            rows[-1] = rows[-1] + (m.group(1),)
    return rows, used


def autosize(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def style_header(ws, ncol):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(name=FONT, bold=True)
        cell.fill = HDR_FILL
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d = a.date

    vpath = "out/verdict-%s.csv" % d
    if not os.path.exists(vpath):
        print("[verdict-xlsx] %s 없음. 전수 판정을 먼저 하세요." % vpath)
        return 1

    blind = parse_blind("out/candidates-blind-%s.txt" % d)
    urls, cats, mscore = parse_prompt("out/prompt-%s.txt" % d)

    # [2026-09-10 수정] 순위 근거 열이 09-08~09-10 사흘간 통째로 비어 있었다.
    #   원인: 09-09 에 rankscore.py 를 학습모델로 전면 교체하면서 `load()` 를
    #   지웠는데, 여기 `except Exception: RSC={}` 가 ImportError 를 조용히
    #   삼켜 경고 한 줄 없이 빈 칸으로 나갔다. 사용자가 엑셀 D~J 열이
    #   비어 있는 것을 보고 발견했다.
    #   이제 out/rankscore-<날짜>.csv 를 직접 읽고, 실패하면 **경고를 찍는다.**
    RSC = load_rankscore(d)
    # [2026-09-22] 파일이 '있어도' 판정보다 낡았으면 그 순위는 지금 판정의
    #   순위가 아니다. 09-22 에 재판정 뒤 rank 를 안 돌려 D~I 가 통째로 비었다.
    _vp, _rp = "out/verdict-%s.csv" % d, "out/rankscore-%s.csv" % d
    if (RSC and os.path.exists(_vp) and os.path.exists(_rp)
            and os.path.getmtime(_vp) > os.path.getmtime(_rp) + 1):
        print("[verdict-xlsx] **[경고] out/rankscore-%s.csv 가 판정 CSV 보다 "
              "오래됐습니다 — 순위 근거가 지금 판정과 다를 수 있습니다. "
              "`run.sh rank` 를 다시 돌리세요.**" % d)
    if not RSC:
        print("[verdict-xlsx] [경고] out/rankscore-%s.csv 를 못 읽었습니다 "
              "- 순위 근거 열이 빈 칸으로 나갑니다" % d)
    # [2026-09-22] 판정할 때 본 신호(화면에만 찍히고 사라지던 것)를 읽어 열로 편다.
    SIG = {}
    try:
        sys.path.insert(0, os.path.join(os.getcwd(), "cowork"))
        import signals as _sig
        SIG = _sig.load(".", d)
    except Exception:
        SIG = {}
    PEEK = set()
    _pp = "out/peeked-%s.json" % d
    if os.path.exists(_pp):
        try:
            PEEK = set(json.load(open(_pp, encoding="utf-8")).get("peeked", []))
        except ValueError:
            pass
    if not SIG.get("hist"):
        print("[verdict-xlsx] [경고] out/signals-%s.json 에 hist 기록이 없습니다 "
              "- '발송이력' 열이 빈 칸으로 나갑니다. `run.sh hist` 를 돌리세요." % d)

    brief_rows, used_urls = parse_brief("out/brief-%s.md" % d)

    # portalize 가 발송본 URL 을 네이버 정적링크로 바꾸므로 URL 만으로는 못 맞춘다.
    # 제목 토큰 겹침으로 보조 매칭한다.
    def toks(s):
        return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", (s or "").lower()))
    brief_toks = [toks(r[2]) for r in brief_rows]

    verdict = list(csv.DictReader(open(vpath, encoding="utf-8")))

    wb = Workbook()

    # ---------- 시트 1: 요약 ----------
    ws = wb.active
    ws.title = "요약"
    cnt = Counter(r["grade"] for r in verdict)
    route_cnt = OrderedDict()
    for r in verdict:
        b = blind.get(r["idx"])
        route = b[0] if b else "?"
        route_cnt.setdefault(route, Counter())[r["grade"]] += 1

    ws.append(["Daily Tech Brief 전수 판정 요약", d])
    ws["A1"].font = Font(name=FONT, bold=True, size=14)
    ws.append([])
    ws.append(["항목", "값"])
    hdr_row = ws.max_row
    ws.append(["후보 총계", len(verdict)])
    ws.append(["고", cnt.get("고", 0)])
    ws.append(["중", cnt.get("중", 0)])
    ws.append(["저", cnt.get("저", 0)])
    ws.append(["발송본 기사 수", len(brief_rows)])
    ws.append([])
    ws.append(["순위 점수 — 학습 모델 (2026-09-09~). '고' 에만 매긴다", ""])
    for a_, b_ in [
        ("최종점수", "통로기여 + 보도폭기여 + 태그기여. "
         "config/rankweights.json 의 학습 가중치로 run.sh rank 가 계산한다. "
         "손으로 매기지 않는다(09-09 지시). 값은 로그-lift 합이라 "
         "0 이 평균, 양수면 평균보다 실릴 확률이 높다는 뜻"),
        ("통로기여", "수집 통로의 로그-lift. 09-10 학습분 기준 "
         "tier0+교차 +0.75 / tier0-사람큐레이션 +0.56 / tier1 +0.00 / "
         "링크미확보 -0.87 / tier2 -1.97. **가장 크게 가르는 축이고, "
         "tier2 단독 기사는 태그가 좋아도 하위로 밀린다**"),
        ("보도폭기여", "다수 +0.32 / 소수 +0.20 / 단독 -0.90"),
        ("태그기여", "판정 사유에서 뽑은 태그 9종(F1·지정학·소버린·국내정책·"
         "F10경쟁사·신규출시·휴머노이드로봇·AI규범사고·프런티어)과 태그개수의 "
         "기여 합계. 최종점수에서 통로·보도폭을 뺀 나머지"),
        ("가중태그", "판정 사유 문자열에서 뽑힌 태그. 사유를 짧게 쓰되 축 이름을 "
         "남겨야 여기 잡힌다"),
        ("검증", "leave-one-day-out(7일치 '고' 300건). top16 적중 — "
         "무작위 39.1% / 손으로 매긴 옛 rank 44.4% / 학습 모델 66.7%"),
        ("[2026-09-10 한계]", "이 모델은 '무슨 사건인가'를 안 보고 "
         "'어디에 실렸나'만 본다. 09-10 에 정답지 8건 중 4건이 "
         "tier2·단독이라 42~46위로 밀려 top16 적중이 3/8 로 무너졌다. "
         "감점을 완화하는 변형 14종을 회귀검증했으나 전부 현행보다 나빴다"),
        ("기계점수(참고)",
         "refine.py 채점값. **판정·순위에 쓰지 않는다**(2026-09-01 지시)"),
        ("", ""),
        ("[2026-09-22 신설] 판정 근거 열", "사용자 질문 '백데이터는 R열 판정사유 "
         "하나뿐이냐'에 대한 답. 판정을 바꾸는 신호가 화면에만 찍히고 사라지고 "
         "있었다. 이제 out/signals-<날짜>.json 에 남기고 여기 펼친다"),
        ("체급", "종전에는 헤드라인 앞에 ⟨프런티어⟩처럼 붙어 있던 것을 분리했다. "
         "주체가 이 바닥에서 누구인가를 나타내는 사실값이다"),
        ("본문확인", "'고' 중 리드를 실제로 연 건은 O. '-' 는 안 연 것. "
         "out/peeked-<날짜>.json 기준(2026-09-22 신설, run.sh rank 가 검사)"),
        ("반복후보", "timeline 에서 '과거 여러 날 후보였는데 한 번도 안 실린 "
         "이야기'에 붙는다. 새 사실이 없으면 올리지 않는다"),
        ("발송이력", "run.sh hist 결과. '과거발송 N건'은 최근 30일 발송본과 "
         "사건어가 겹친 횟수, ★는 3건 이상. '주체겹침'은 사건어는 달라도 같은 "
         "주체가 최근 발송에 있었다는 뜻"),
        ("게이트", "그 건이 걸린 게이트와 종류(agenda:물량, repeat:재집계, "
         "newfact:본문근거없음 …). 비어 있으면 안 걸린 것"),
        ("사례집", "판정 사유에 '사례N' 이라고 적은 것을 뽑는다. docs/사례집.md "
         "의 비교쌍 번호. **적지 않으면 빈 칸이다 — 적는 것은 내 몫이다**"),
    ]:
        ws.append([a_, b_])
    ws.append([])
    ws.append(["수집 통로", "고", "중", "저", "합계"])
    rhdr = ws.max_row
    for route, c in route_cnt.items():
        ws.append([route, c.get("고", 0), c.get("중", 0), c.get("저", 0),
                   c.get("고", 0) + c.get("중", 0) + c.get("저", 0)])
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=5):
        for cell in row:
            if cell.font is None or not cell.font.bold:
                cell.font = Font(name=FONT)
    for c in range(1, 3):
        ws.cell(row=hdr_row, column=c).font = Font(name=FONT, bold=True)
        ws.cell(row=hdr_row, column=c).fill = HDR_FILL
    for c in range(1, 6):
        ws.cell(row=rhdr, column=c).font = Font(name=FONT, bold=True)
        ws.cell(row=rhdr, column=c).fill = HDR_FILL
    autosize(ws, [26, 14, 10, 10, 10])

    # ---------- 시트 2: 전수판정 ----------
    ws = wb.create_sheet("전수판정")
    # [2026-09-10] 열 교체. W/I/C/S/D 는 09-09 에 폐지된 수기 배점이라
    #   09-08 이후 값이 들어올 수 없었다. 신 모델의 실제 기여도로 바꾼다.
    # [2026-09-22] 체급을 헤드라인에서 분리하고, 판정 근거 5열을 신설했다.
    # [2026-10-06 사용자 지시] '순위'(C열)는 학습 모델 값이다. 발송본을 실제로
    #   고른 편집 순위와 그 사유를 out/handrank-<날짜>.csv 에서 읽어 별도 열로 둔다.
    #   (10-06: 편집 순위로 발송했는데 엑셀엔 모델 순위만 보여 '19위'를 찾을 수 없었다)
    HR = {}
    _hp = "out/handrank-%s.csv" % d
    if os.path.exists(_hp):
        for _r in csv.DictReader(open(_hp, encoding="utf-8")):
            HR[_r["idx"]] = (_r.get("발송순위", ""), _r.get("발송순위 사유", ""))
    elif any(r["grade"] == "고" for r in verdict):
        print("[verdict-xlsx] [경고] out/handrank-%s.csv 없음 — 발송순위(편집) 열이 빈 칸으로 나갑니다" % d)
    head = ["idx", "판정", "순위(모델)", "발송순위(편집)", "발송순위 사유", "최종점수", "통로기여", "보도폭기여",
            "태그기여", "태그개수", "가중태그", "기계점수(참고)", "발송", "수집통로",
            "카테고리", "매체", "발행일", "보도", "체급", "헤드라인", "판정 사유",
            "본문확인", "반복후보", "발송이력", "게이트", "사례집",
            "링크없음", "중복의심", "URL"]
    ws.append(head)
    matched = set()   # 제목 보조매칭을 1:1 로 묶어 같은 사건의 다른 후보가
                      # 중복으로 '발송' 표시되는 것을 막는다.
    order = {"고": 0, "중": 1, "저": 2}
    def sortkey(r):
        rk = r.get("rank") or ""
        return (order.get(r["grade"], 9), int(rk) if str(rk).isdigit() else 9999, r["idx"])
    for r in sorted(verdict, key=sortkey):
        i = r["idx"]
        b = blind.get(i, ("", "", "", "", "", "", "", "", ""))
        url = urls.get(i, "")
        sent = bool(url) and url in used_urls
        if not sent and b[4]:
            t = toks(b[4])
            best, bj = 0.0, -1
            for j, bt in enumerate(brief_toks):
                if j in matched or not bt or not t:
                    continue
                s = len(t & bt) / max(1, min(len(t), len(bt)))
                if s > best:
                    best, bj = s, j
            if best >= 0.6:
                sent = True
                matched.add(bj)
        sc = RSC.get(i)
        hv = (SIG.get("hist") or {}).get(i) or {}
        histtxt = ""
        if hv:
            st = int(hv.get("star") or 0)
            parts = []
            if st:
                parts.append(("★" if st >= 3 else "") + "과거발송 %d건" % st)
            if hv.get("overlap"):
                parts.append("주체겹침")
            histtxt = " / ".join(parts) or "이력없음"
        gatetxt = " ".join((SIG.get("gates") or {}).get(i, []))
        mcase = re.findall(r"사례\s*#?(\d+)", r.get("reason", ""))
        casetxt = " ".join("사례%s" % x for x in mcase)
        peektxt = "O" if i in PEEK else ("" if r["grade"] != "고" else "-")
        hr = HR.get(i, ("", ""))
        ws.append([i, r["grade"], r.get("rank") or "",
                   int(hr[0]) if str(hr[0]).isdigit() else hr[0], hr[1],
                   sc["total"] if sc else "", sc["route_w"] if sc else "",
                   sc["spread_w"] if sc else "", sc["tag_w"] if sc else "",
                   sc["ntag"] if sc else "", sc["tags"] if sc else "",
                   mscore.get(i, ""),
                   "○" if sent else "",
                   b[0], cats.get(i, ""), b[1], b[2], b[3], b[7], b[4],
                   r.get("reason", ""),
                   peektxt, b[8], histtxt, gatetxt, casetxt,
                   b[5], b[6], url])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(head)):
        for cell in row:
            cell.font = Font(name=FONT)
            cell.alignment = Alignment(vertical="center")
        g = row[1].value
        if g in GRADE_FILL:
            row[1].fill = GRADE_FILL[g]
    style_header(ws, len(head))
    autosize(ws, [7, 7, 9, 11, 50, 10, 10, 8, 9, 10, 8, 24, 12, 7, 18, 20, 14, 10, 7,
                  12, 62, 40, 9, 22, 16, 22, 10, 9, 30, 60])

    # ---------- 시트 3: 발송본 ----------
    ws = wb.create_sheet("발송본")
    head = ["카테고리", "번호", "헤드라인", "매체", "주요 내용", "시사점", "URL"]
    ws.append(head)
    for r in brief_rows:
        cat, num, h, outlet, url = r[:5]
        body = r[5] if len(r) > 5 else ""
        impl = r[6] if len(r) > 6 else ""
        ws.append([cat, num, h, outlet, body, impl, url])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=len(head)):
        for cell in row:
            cell.font = Font(name=FONT)
            cell.alignment = Alignment(vertical="center")
    style_header(ws, len(head))
    autosize(ws, [26, 6, 62, 16, 52, 46, 60])

    out = a.out or ("out/판정-%s.xlsx" % d)
    wb.save(out)
    print("[verdict-xlsx] 저장: %s" % out)
    print("[verdict-xlsx] 후보 %d건 (고 %d / 중 %d / 저 %d) / 발송본 %d건"
          % (len(verdict), cnt.get("고", 0), cnt.get("중", 0), cnt.get("저", 0), len(brief_rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
