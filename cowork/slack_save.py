#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
슬랙 MCP 응답을 tier0 raw 파일로 저장한다.  [2026-08-21 신설]

[왜 만들었나]
  종전에는 에이전트가 Slack MCP 응답을 눈으로 읽고 raw 파일 두 개(각 14KB)를
  손으로 다시 타이핑했다. 문제가 두 가지였다.
    1) 느리다. 08-21 실측으로 이 단계에만 2분 20초가 들었다.
    2) 위험하다. MCP 응답은 JSON 이라 URL 이 https:\\/\\/... 로 이스케이프돼
       있고 한글이 \\uXXXX 로 온다. 이걸 손으로 되돌리다 빠뜨리면 tier0 파서가
       링크를 못 알아보고 그 채널이 통째로 사라진다.
       (실측 08-12: #3_트렌드 12건 전량 유실. '0건 파싱' 경고만 남았다.)

[무엇을 하나]
  stdin 으로 받은 MCP 응답 원문을 그대로 받아서
    - \\uXXXX  -> 실제 글자
    - \\/      -> /
    - \\n \\t \\" \\\\ -> 실제 문자
  로 되돌리고, 마운트와 작업본 **양쪽**에 같은 파일을 쓴다(push 누락 방지).
  마지막에 기사로 보이는 <url|제목> 패턴이 몇 건인지 세어 바로 알려준다.
  0건이면 종료코드 1 로 실패시킨다 -- 조용히 넘어가지 않게 하려는 것이다.

[쓰는 법]
  bash cowork/run.sh slack-save 3trend  <<'RAW'
  ...MCP 응답 붙여넣기...
  RAW
  bash cowork/run.sh slack-save personal <<'RAW'
  ...
  RAW

  채널 별칭은 3trend | personal 두 가지다.
  저장 경로: cowork/raw/slack-<별칭>-<날짜>.txt
"""
import argparse
import codecs
import json
import datetime
import os
import re
import sys

# 별칭 -> (파일 스템, 표시 이름)
# [2026-08-22] 에이닷·삼성·사업공고 입력이 #개인테스트 -> #dtb-tier0 (C0BRP6UPGG6)
#   로 옮겨졌다. 별칭도 'personal' -> 'tier0' 으로 바꾸고 파일 스템을
#   slack-tier0-<날짜>.txt 로 간다. 'personal' 은 옛 이름으로 계속 받아주되
#   같은 스템(slack-tier0)으로 저장한다 -- 과거 raw 파일(slack-personal-*)은
#   그대로 두고 slack_to_log.py 가 둘 다 읽는다(백테스트 재현용).
ALIASES = {
    "3trend": ("slack-3trend", "#3_트렌드"),
    "tier0": ("slack-tier0", "#dtb-tier0"),
    "personal": ("slack-tier0", "#dtb-tier0"),   # 옛 별칭
}

def _manual_unescape(text: str) -> str:
    """JSON 파싱이 안 될 때 쓰는 폴백. 표준 이스케이프만 손으로 되돌린다."""
    def _u(m):
        try:
            return codecs.decode(m.group(0), "unicode_escape").encode(
                "utf-16", "surrogatepass").decode("utf-16")
        except Exception:
            return m.group(0)

    text = re.sub(r"(?:\\u[0-9a-fA-F]{4})+", _u, text)
    # \\ 는 맨 마지막에 풀어야 겹치지 않는다.
    for a, b in (("\\/", "/"), ('\\"', '"'), ("\\n", "\n"), ("\\t", "\t")):
        text = text.replace(a, b)
    return text.replace("\\\\", "\\")


def decode(text: str):
    """MCP 응답 원문 -> 사람이 읽는 슬랙 덤프 텍스트.

    응답은 {"messages":"...본문...","pagination_info":"..."} 형태의 **정상
    JSON** 이다. 그러니 정규식으로 껍데기를 벗기고 이스케이프를 손으로
    되돌리지 말고 json.loads 로 한 번에 처리한다. \\uXXXX·\\/·\\"·\\n 이
    전부 규칙대로 풀리고, 껍데기 경계를 잘못 잡을 여지도 없다.
    (실측 08-21: 정규식판이 greedy 매칭 때문에 꼬리의
     '","pagination_info":"...' 를 본문에 남겼다.)

    붙여넣다 잘렸거나 스키마가 바뀌어 파싱이 안 되면 폴백으로 넘어가고,
    그 사실을 호출부에 알린다.
    """
    s = text.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and isinstance(obj.get("messages"), str):
            return obj["messages"], "json"
        return _manual_unescape(s), "json-스키마다름"
    except ValueError:
        return _manual_unescape(s), "폴백(JSON 파싱 실패)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, choices=sorted(ALIASES))
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--mnt", required=True, help="마운트(사용자 폴더) 경로")
    ap.add_argument("--work", required=True, help="작업본 경로")
    a = ap.parse_args()

    raw = sys.stdin.read()
    if not raw.strip():
        print("[slack-save] stdin 이 비었습니다. 히어독으로 원문을 넘기세요.")
        return 2

    body, how = decode(raw)
    text = body.strip() + "\n"
    stem, label = ALIASES[a.channel]
    name = "%s-%s.txt" % (stem, a.date)

    written = []
    for base in (a.mnt, a.work):
        d = os.path.join(base, "cowork", "raw")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        written.append(p)

    # 기사 링크 개수. tier0 파서가 보는 것과 같은 패턴이다.
    links = re.findall(r"<(https?://[^|>]+)\|", text)
    uniq = len(set(links))
    left = text.count("\\/")  # 남은 이스케이프가 있으면 되돌리기가 실패한 것

    print("[slack-save] %s %s (%s)" % (label, a.date, how))
    for p in written:
        print("  저장: %s (%d자)" % (p, len(text)))
    print("  기사 링크: %d건 (고유 %d건)" % (len(links), uniq))

    # [2026-08-21] 건수 범위 경고는 뺐다(사용자 지시). 채널이 조용한 날이
    #   흔한데 매번 경고가 떠서 진짜 사고와 구분이 안 됐다.
    #   **0건일 때만** 실패시킨다. 그게 08-12 유실 사고의 실제 증상이다.
    if left:
        print("  [경고] 안 풀린 '\\/' 가 %d개 남았습니다. 파서가 링크를 "
              "못 읽습니다." % left)
        return 1
    if uniq == 0:
        print("  [실패] 링크를 한 건도 못 찾았습니다. 붙여넣은 원문이 "
              "MCP 응답이 맞는지 확인하세요. (08-12 유실 사고와 같은 증상)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
