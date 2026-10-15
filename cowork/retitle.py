# -*- coding: utf-8 -*-
"""retitle.py - 브리프 헤드라인을 '링크가 가리키는 실제 기사 제목'으로 맞춘다.

## 왜 만들었나 (2026-08-27 사용자 지시)
후보 목록의 헤드라인이 링크 속 기사 제목과 다른 일이 있다. 특히 지면
스크랩(NewsClips)은 조간 **지면 PDF** 에서 딴 제목이라 웹 기사 제목과
자주 어긋난다.
    지면 '내년 800조원+ a 슈퍼예산 AI·3대 메가에 집중 투입'
    실제 '2027년 800조원+α 슈퍼예산 AI·3대 메가에 집중 투입'
    지면 '네이버·카카오, AI 생성물 규제 장벽 낮춰'
    실제 '플랫폼 업계, 'AI 생성물 표시' 규제 적정선 찾기…판매자 부담 완화에 초점'
둘 다 **같은 기사**다. 사람이 링크를 열면 다른 제목이 떠서 헷갈린다.

사용자 지시: "제목이 달라도 동일한 내용의 기사면 상관 없고, 후보 목록 값
그대로 쓸 필요는 없다. 링크에 있는 기사 제목을 최종 제목으로 쓰면 된다."

## 종전 규칙과의 관계 - 헷갈리기 쉬우니 분명히 해 둔다
'헤드라인을 임의로 손질하지 말 것'(2026-08-25)은 **여전히 유효하다.**
그 규칙은 내가 눈대중으로 전각 따옴표를 바꾸고 시리즈 태그를 떼다가
원문에서 오히려 멀어진 사고에서 나왔다(30건 중 13건을 그렇게 고쳤다).
여기서 쓰는 값은 내 짐작이 아니라 **linkcheck 가 그 URL 에서 실제로
받아온 <title>/og:title** 이다. 출처가 다르므로 충돌하지 않는다.
  - 사람이 손으로 고치는 것: 금지 (종전 그대로)
  - 링크에서 받아온 제목으로 기계가 맞추는 것: 이 스크립트 (신규)

## 안전장치
  - linkcheck 결과가 있어야 돈다. 없으면 아무것도 안 하고 경고한다.
  - verdict 가 '실패'인 항목은 건드리지 않는다(링크 자체가 틀린 것).
  - 페이지 제목을 못 받은 항목(미확인·봇차단)도 건드리지 않는다.
  - 겹침이 아주 낮으면(--min-overlap 미만) **바꾸지 않고 경고만** 한다.
    링크가 엉뚱한 기사일 가능성이 있는데 그 제목으로 덮으면 오류가 숨는다.
  - 바꾼 건을 전부 찍는다. --dry 로 미리 볼 수 있다.

## 쓰는 법
    bash cowork/run.sh linkcheck      # 먼저 (제목을 받아 놔야 한다)
    bash cowork/run.sh retitle dry    # 미리보기
    bash cowork/run.sh retitle        # 적용 (brief 와 brief20 둘 다)
"""
import argparse
import datetime
import html
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(BASE, "out")

RE_WS = re.compile(r"\s+")
# [주의] linkcheck 의 RE_TAIL 을 그대로 쓰면 안 된다. 거기서는 '비교용'이라
#   과하게 떼어도 무해했지만 여기서는 **제목을 실제로 덮어쓰므로** 파괴적이다.
#   실측 08-27: 그 정규식이 구분자 목록에 '·' 를 포함해
#     '2027년 800조원+α 슈퍼예산 AI·3대 메가에 집중 투입' -> '...슈퍼예산 AI'
#     '우리은행-기보, 반도체·AI 미래산업에 1조원 금융 지원' -> '우리은행-기보, 반도체'
#   처럼 제목 본문을 잘라 먹었다. 여기서는 매체명을 아는 상태로 그것만 뗀다.
# 뉴시스: '제목 :: 공감언론 뉴시스 ::'
RE_COLON_TAIL = re.compile(r"\s*::[^:]{0,30}::\s*$")
# AI타임스 등: '제목 < 산업일반 < AI산업 < 기사본문'
RE_NAV_TAIL = re.compile(r"\s+<\s+.{1,30}$")
# **1\. 헤드라인** (매체, [원문보기](url))
RE_ITEM = re.compile(
    r"^(\*\*\d+\\\.\s*)(.+?)(\*\*\s*\([^()]*\[원문보기\]\()(https?://[^)]+)(\)\))$",
    re.M)


def log(m):
    print("[retitle] %s" % m)


def toks(s):
    return {p for p in re.split(r"[^0-9a-z가-힣]+", (s or "").lower())
            if len(p) >= 2}


def clean(title, outlet=None):
    """페이지 제목 -> 헤드라인으로 쓸 문자열.

    매체명을 알 때만 그 매체명을 떼고, 나머지는 사이트 네비게이션 꼬리
    (뉴시스 '::...::', AI타임스 '< 산업일반 < 기사본문')만 정확히 겨냥한다.
    **제목 본문을 자를 바에는 꼬리표를 남기는 편이 낫다.**
    """
    t = html.unescape(title or "")
    t = RE_WS.sub(" ", t).strip()
    orig = t

    while True:
        before = t
        t = RE_COLON_TAIL.sub("", t).strip()
        # ' < ' 로 시작하는 네비게이션은 통째로 뗀다(여러 단계일 수 있다).
        if " < " in t:
            t = t.split(" < ")[0].strip()
        t = RE_NAV_TAIL.sub("", t).strip() if " < " in t else t
        if outlet:
            # ' - 매체', ' | 매체', ' - 매체 (영문명)' 형태만.
            o = re.escape(outlet.strip())
            t = re.sub(r"\s*[\|\-—–]\s*%s(\s*\([^()]{0,24}\))?\s*$" % o,
                       "", t).strip()
        if t == before:
            break

    # 안전장치: 너무 많이 깎였으면 원문을 그대로 쓴다.
    if len(t) < 8 or len(t) < 0.5 * len(orig):
        return orig
    return t


def load_titles(day):
    """url -> 페이지 제목(원문). 매체명은 호출부에서 알기 때문에 그때 뗀다."""
    p = os.path.join(OUTDIR, "linkcheck-%s.json" % day)
    if not os.path.exists(p):
        return None
    out = {}
    for r in json.load(open(p, encoding="utf-8")):
        if r.get("verdict") == "실패":
            continue
        if (r.get("page_title") or "").strip():
            out[r["url"]] = r["page_title"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--dry", action="store_true")
    # 겹침이 이보다 낮으면 링크가 엉뚱한 기사일 수 있으므로 바꾸지 않는다.
    ap.add_argument("--min-overlap", type=float, default=0.2)
    a = ap.parse_args()
    day = datetime.date.fromisoformat(a.date).isoformat()

    titles = load_titles(day)
    if titles is None:
        log("[경고] linkcheck-%s.json 이 없습니다. "
            "`run.sh linkcheck` 를 먼저 돌리세요" % day)
        return 1
    log("링크 검사에서 받아온 제목 %d건" % len(titles))

    n_same = n_chg = n_skip = n_low = 0
    for name in ("brief-%s.md" % day, "brief20-%s.md" % day):
        p = os.path.join(OUTDIR, name)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8").read()
        changes = []

        def sub(m):
            nonlocal n_same, n_chg, n_skip, n_low
            pre, head, mid, url, tail = m.groups()
            raw = titles.get(url)
            if not raw:
                n_skip += 1
                return m.group(0)
            mo = re.search(r"\*\*\s*\(\s*([^,()]+)\s*,", mid)
            new = clean(raw, mo.group(1) if mo else None)
            if new == head:
                n_same += 1
                return m.group(0)
            a_t, b_t = toks(head), toks(new)
            ov = len(a_t & b_t) / max(len(a_t), 1)
            if ov < a.min_overlap:
                n_low += 1
                log("  [건너뜀] 겹침 %.0f%% - 링크가 다른 기사일 수 있습니다."
                    " 눈으로 확인하세요" % (ov * 100))
                log("      브리프: %s" % head[:66])
                log("      페이지: %s" % new[:66])
                return m.group(0)
            n_chg += 1
            changes.append((head, new, ov))
            return pre + new + mid + url + tail

        out = RE_ITEM.sub(sub, text)
        for old, new, ov in changes:
            log("  [교체] 겹침 %.0f%%" % (ov * 100))
            log("      전: %s" % old[:66])
            log("      후: %s" % new[:66])
        if not a.dry and out != text:
            open(p, "w", encoding="utf-8").write(out)
        if changes:
            log("%s: %d건 교체%s" % (name, len(changes),
                                     " (미리보기)" if a.dry else ""))

    log("결과 교체 %d / 동일 %d / 제목없음 %d / 겹침낮아 보류 %d%s"
        % (n_chg, n_same, n_skip, n_low, " (미리보기)" if a.dry else ""))
    if n_low:
        log("[주의] 보류 %d건은 링크가 다른 기사일 수 있습니다. 확인하세요"
            % n_low)
    return 0


if __name__ == "__main__":
    sys.exit(main())
