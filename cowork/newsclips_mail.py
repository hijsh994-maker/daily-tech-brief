# -*- coding: utf-8 -*-
"""newsclips_mail.py - tier0 소스 'SKT NewsClips' 를 지메일 본문에서 적재.

## 왜 바꿨나 (2026-08-25 사용자 지시)
종전에는 GitHub `logs/YYYY-MM-DD.md` 의 지면 스크랩을 받아 썼다. 그런데
그 문서는 **원문 링크가 없다**('원문 링크: (제공되지 않음)'). 제목+매체로
링크를 되짚어야 해서 확보율이 들쭉날쭉했다(실측 08-25: 15건 중 5건, 33%).

지메일로 오는 「News Clips PDF 분석(N월 N일)」 메일은 같은 조간 스크랩을
**원문 URL 과 함께** 준다. 08-25 실측으로 27건 전건에 링크가 붙어 있었다.
링크 해결 단계(초당 0.4건 고정)의 부하도 그만큼 줄어든다.

## 입력 형식
    1. SKT AI 전문가들 서울대 강단에 선다 (한국경제)
    o 링크: https://www.google.com/url?q=https://www.hankyung.com/...&source=gmail&ust=...

- `번호. 제목 (매체)` 한 줄 + `o 링크:` 한 줄이 한 항목.
- URL 은 지메일이 `google.com/url?q=<원본>` 으로 감싸고 `%3D`·`%26` 를
  퍼센트 인코딩한다. 벗겨서 원본만 남긴다.
- 본문에 제어문자(\\x17 등)가 섞여 들어온다. 미리 턴다.

## 필터링 (2026-08-25 사용자 지시)
메일은 **조간 전체**라 AI 무관 기사가 섞인다(골프·스마트폰 출하량·3G 종료 등).
GitHub 문서는 이미 AI 만 걸러진 상태였으므로 그 몫을 여기서 한다.
  - AI/로봇/디지털트윈/데이터센터 등 관심 주제 키워드가 없으면 제외
  - 사설·칼럼류는 키워드가 있어도 제외
    (정답지 635건 중 사설·칼럼 채택 **0건**. 실을 일이 없다)
제외한 건은 전부 화면에 찍는다. 필터가 과했는지 사람이 바로 볼 수 있어야 한다.

## 사용법
    bash cowork/run.sh newsclips-mail <<'RAW'
    <지메일 본문 원문 그대로>
    RAW
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "cowork"))

OUTDIR = os.path.join(BASE, "out")
RAWDIR = os.path.join(BASE, "cowork", "raw")
SRC = "newsclips"
LABEL = "SKT NewsClips"

# [2026-08-25] **주제 필터를 두지 않는다. 파싱된 것을 전량 주입한다.**
#   (사용자 지시) 처음에는 이 스크립트에서 AI 무관 기사를 걸러내려 했다.
#   화이트리스트로 짰다가 정답지에 실린 기사가 탈락해 블랙리스트로 뒤집었는데,
#   그 역시 키워드 목록을 사람이 유지해야 하는 구조라는 점은 같았다.
#
#   실측으로 드러난 문제:
#     - 화이트리스트: 'KT, 전사 IT 현대화 넥스트 IT 가동'(08-24 정답지 채택분),
#       '호남 팹 건설… 총수들 李와 연쇄 독대'(08-25 브리프 채택분)가
#       'AX'·'팹' 이 키워드에 없다는 이유로 탈락
#     - 블랙리스트: '스마트폰'·'주가' 같은 말이 제목에 스쳐도 탈락. AI 반도체
#       수요를 스마트폰 출하로 설명한 기사가 함께 죽는다
#
#   tier0 은 **전량 검토가 원칙**이다(선별 기준 13). 여기서 미리 빼면 검토
#   기회 자체가 사라지고, 그 손실은 로그를 봐야만 알 수 있다. 무엇을 뺄지는
#   편집자 루브릭(주체 x 주제 x 확정성)을 들고 있는 브리프 작성 단계에서
#   판단한다. 그 단계는 본문·점수·다른 후보와의 관계를 함께 보므로
#   제목 키워드보다 훨씬 정확하다.

# [2026-10-08] 항목 줄이 '*1. 제목 (매체)*' 처럼 별표(볼드)로 감싸여 왔다(0건 파싱).
#   앞뒤 별표를 허용한다.
RE_ITEM = re.compile(r"^\s*\*{0,2}\s*(\d{1,3})[.)]\s*(.+?)\s*\*{0,2}\s*$")
# [2026-09-17] URL 을 **다음 줄에서도** 받는다. 뉴스클립 소스가 지메일 ->
#   슬랙(이메일 게시)으로 바뀌면서 메일 클라이언트가 긴 URL 을 줄바꿈해
#   보낸다. 실측 09-17: 26건 중 3건(6·11·26)이 이 모양이라 통째로 탈락했다.
RE_LINK = re.compile(r"^\s*[o•∙*-]?\s*링크\s*[:：]\s*(\S*)")
RE_BARE_URL = re.compile(r"^\s*<?(https?://\S+?)>?\s*$")
# [2026-08-26] 상한을 16자 -> 40자로 올렸다.
#   삼성 지메일에는 `(The Information / Reuters)`(25자) 처럼 긴 괄호가 온다.
#   16자로 막아 두면 매체를 못 잡는 데서 끝나지 않고 **괄호가 제목에 그대로
#   남아** 브리프 헤드라인까지 오염된다(실측 08-26: 17건 중 1건).
RE_OUTLET = re.compile(r"^(?P<t>.+?)\s*\((?P<o>[^()]{2,40})\)\s*$")

PREFIX = "newsclips-mail"


def log(m):
    print("[%s] %s" % (PREFIX, m))


def norm_outlet(o):
    """매체명 정규화 - 슬래시 앞(원 매체)만 취한다.

    [2026-08-26 사용자 지시] 삼성 지메일은 `디지털데일리 / Daum뉴스`,
    `SBS Biz / Daum뉴스` 처럼 '원 매체 / 재게재처' 를 함께 준다.
    뒤엣것을 그대로 두면 수집통로 매칭이 어긋난다(매체 일치가 1급 신호다).
    """
    o = clean(o)
    if not o:
        return None
    o = re.split(r"\s*[/|]\s*", o)[0].strip()
    return o or None


def clean(s):
    """제어문자·중복 공백 제거."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def unwrap(url):
    """지메일 리다이렉트·마크다운 링크를 벗겨 원본 URL 만 남긴다."""
    u = clean(url)
    # [2026-09-15] 삼성 소스가 지메일 -> 슬랙 #dtb-tier0 로 바뀌었다.
    #   slack_to_log 가 슬랙의 <url|text> 를 마크다운 [text](url) 로 바꿔 놓는다.
    #   그래서 '링크:' 뒤 토큰이 통째로 '[text](url)' 로 들어온다.
    m = re.search(r"\[[^\]]*\]\((https?://[^)]+)\)", u)
    if m:
        u = m.group(1)
    u = u.rstrip(".,)]}’”")
    if "google.com/url" in u:
        try:
            qs = urllib.parse.urlparse(u).query
            q = urllib.parse.parse_qs(qs).get("q")
            if q:
                u = q[0]
        except Exception:
            pass
    # q= 안의 값이 한 번 더 인코딩돼 오는 일이 있다(%3D -> =, %26 -> &).
    if "%3D" in u or "%26" in u or "%2F" in u:
        u = urllib.parse.unquote(u)
    # 뒤에 붙어 오는 지메일 추적 파라미터를 턴다.
    u = re.split(r"&(?:source=gmail|ust=|usg=|sa=)", u)[0]
    return u.strip()


def parse(text):
    """메일 본문 -> [{title, outlet, url}]"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    out, cur, pending = [], None, False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        lm = RE_LINK.match(line)
        if lm and cur:
            if lm.group(1):
                cur["url"] = unwrap(lm.group(1))
                out.append(cur)
                cur = None
            else:
                # '링크:' 만 있고 URL 은 다음 줄이다. 다음 URL 줄까지 기다린다.
                pending = True
            continue
        if pending and cur:
            bm = RE_BARE_URL.match(line)
            if bm:
                cur["url"] = unwrap(bm.group(1))
                out.append(cur)
                cur, pending = None, False
                continue
        im = RE_ITEM.match(line)
        if im:
            pending = False
            body = clean(im.group(2))
            if not body or body.startswith("링크"):
                continue
            om = RE_OUTLET.match(body)
            if om:
                cur = {"title": clean(om.group("t")),
                       "outlet": norm_outlet(om.group("o")), "url": None}
            else:
                cur = {"title": body, "outlet": None, "url": None}
    return [e for e in out if e.get("title")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--file", help="본문 파일. 없으면 stdin")
    ap.add_argument("--dry", action="store_true", help="주입 없이 파싱만")
    ap.add_argument("--mnt", help="마운트 폴더. 주면 원문을 양쪽에 저장한다")
    # [2026-08-26] tier0 소스명을 인자로 받는다.
    #   삼성 큐레이션도 같은 형식의 지메일로 오게 됐다(「글로벌 AI/SW 뉴스
    #   브리핑」). 파서·리다이렉트 해제·중복 방지가 전부 동일하므로 스크립트를
    #   새로 만들지 않고 소스명만 갈아 끼운다.
    ap.add_argument("--src", default=SRC, help="tier0 소스명 (newsclips|samsung)")
    a = ap.parse_args()

    global PREFIX
    src = (a.src or SRC).strip().lower()
    PREFIX = "%s-mail" % src

    text = (open(a.file, encoding="utf-8").read() if a.file
            else sys.stdin.read())
    if not text.strip():
        log("[실패] 입력이 비어 있습니다")
        return 1

    day = datetime.date.fromisoformat(a.date)
    # 원문 보관(슬랙과 같은 규칙). 파서가 틀렸을 때 되짚을 근거가 된다.
    #   [주의] 작업본(/tmp)에만 두면 세션이 끝날 때 사라진다. slack_save 와
    #   같이 **마운트에도** 남긴다.
    # [2026-09-17] --dry 는 부작용이 없어야 한다. 09-17 에 newsclips-slack 을
    #   dry 로 시험하다가 그날 실제 원문 감사 파일을 테스트 본문으로 덮어썼다.
    dests = [] if a.dry else [RAWDIR]
    if a.mnt and not a.dry:
        dests.append(os.path.join(a.mnt, "cowork", "raw"))
    if a.dry:
        log("dry - 원문 저장 건너뜀")
    for d in dests:
        try:
            os.makedirs(d, exist_ok=True)
            rp = os.path.join(d, "%s-mail-%s.txt" % (src, day.isoformat()))
            open(rp, "w", encoding="utf-8").write(text)
            log("원문 저장: %s (%d자)" % (rp, len(text)))
        except Exception as e:
            log("원문 저장 실패(무시): %s" % str(e)[:60])

    ents = parse(text)
    if not ents:
        log("[실패] 0건 파싱. 메일 형식이 바뀌었을 수 있음 - 파서 점검 필요")
        return 1

    kept = [(e, "") for e in ents]      # 전량 주입. 선별은 브리프 단계에서.
    n_link = sum(1 for e, _ in kept if e.get("url"))
    log("파싱 %d건 (전량 주입) | 링크 보유 %d/%d (%.0f%%)"
        % (len(ents), n_link, len(kept), 100.0 * n_link / max(len(kept), 1)))
    if n_link < len(kept) * 0.9:
        log("[경고] 링크 보유율이 90% 미만입니다. 메일 형식 변경을 의심하세요")
    if a.dry:
        for e, _ in kept:
            log("   + %-46s | %-8s | %s"
                % (e["title"][:46], e["outlet"] or "?", (e["url"] or "")[:52]))
        return 0

    p = os.path.join(OUTDIR, "tier0-%s.json" % day.isoformat())
    if not os.path.exists(p):
        log("[실패] %s 없음. tier0 를 먼저 실행" % p)
        return 1
    data = json.load(open(p, encoding="utf-8"))

    # [2026-08-26] 삼성은 지메일이 슬랙 껍데기를 **대체**한다.
    #   run.sh tier0 이 samsung_seed.py 로 슬랙 요약을 링크 없는 껍데기로
    #   미리 넣어 둔다(구 매칭 경로가 채울 자리). 지메일에는 같은 사건이
    #   제목·링크까지 갖춰 오므로, 그대로 두면 같은 사건이 두 번 실린다.
    #   제목이 서로 달라(슬랙=요약문, 지메일=기사 제목) 제목 기준 중복
    #   제거로는 안 걸린다. 그래서 **링크 없는 삼성 껍데기만** 걷어낸다.
    #   지메일이 안 온 날은 이 분기 자체를 안 부르므로 껍데기가 그대로
    #   남고 구 매칭 경로가 정상 동작한다.
    if src == "samsung":
        before = len(data["items"])
        data["items"] = [x for x in data["items"]
                         if not (x.get("src") == "samsung" and not x.get("url"))]
        dropped = before - len(data["items"])
        if dropped:
            log("슬랙 삼성 껍데기 %d건 제거(지메일이 대체)" % dropped)

    have = {x.get("title") for x in data["items"] if x.get("src") == src}
    seen_url = set()
    added = 0
    for e, _ in kept:
        if e["title"] in have:            # 재실행 시 중복 주입 방지
            continue
        u = e.get("url")
        # 같은 URL 이 두 항목에 붙어 오는 일이 있다(실측 08-25: 서울경제
        # 9번·10번이 같은 기사 URL). 뒤엣것은 링크만 비워 싣는다.
        if u and u in seen_url:
            u = None
        elif u:
            seen_url.add(u)
        data["items"].append({
            "src": src, "title": e["title"], "outlet": e["outlet"],
            "pub_date": day.isoformat(), "cluster": 1, "topic": None,
            "url": u, "url_via": "mail" if u else None,
            "confidence": "auto" if u else "unresolved",
            "seen_in": [src], "summary": None, "page": None,
        })
        added += 1

    data.setdefault("counts", {})[src] = len(kept)
    json.dump(data, open(p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    log("주입 %d건 (기존 중복 %d건 건너뜀)" % (added, len(kept) - added))
    left = sum(1 for x in data["items"]
               if x.get("src") == src and not x.get("url"))
    if left:
        log("링크 미보유 %d건은 refine 뒤 'run.sh links' 가 이어서 채운다" % left)
    return 0


if __name__ == "__main__":
    sys.exit(main())
