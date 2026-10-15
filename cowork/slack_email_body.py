# -*- coding: utf-8 -*-
"""[2026-09-17 신설] 슬랙 '이메일 게시' 파일에서 본문만 꺼낸다.

왜 필요한가 — 뉴스클립 소스를 지메일에서 슬랙 채널로 옮기면 메일이
**메시지 텍스트가 아니라 text/html 파일 첨부**로 들어온다.
실측 09-17 #dtb-tier0:
    === Message from Slackbot ... ===
    Files: [2026년 9월 17일] 조간 뉴스 클리핑 (ID: F0C2AHG0X3P, text/html, 40.0 KB)
메시지 본문은 **빈 문자열**이다. 그래서 `run.sh slack-save` 경로로는
0건이 파싱된다(삼성 소스처럼 사람이 붙여 쓴 메시지와 구조가 다르다).

파일을 `slack_read_file <file_id>` 로 읽으면 JSON 이 나오고 그 안의
`body-plain` 이 지메일 본문과 **같은 형식**이다. 이 스크립트는 그 JSON 을
표준입력으로 받아 `body-plain` 만 뽑아 표준출력으로 흘린다.
따라서 파서는 새로 만들지 않고 `newsclips_mail.py` 를 그대로 쓴다
(2026-09-15 교훈: 파서가 둘이 되면 또 어긋난다).

사용법:
    bash cowork/run.sh newsclips-slack <<'RAW'
    <slack_read_file 응답 전체>
    RAW
"""
import json, re, sys, html


def extract(text):
    """slack_read_file 응답(헤더·<file_content> 래퍼 포함)에서 본문을 꺼낸다."""
    t = text or ""
    # <file_content_xxx> ... </file_content_xxx> 래퍼를 벗긴다
    m = re.search(r"<file_content_[0-9a-f]+>\s*(.*?)\s*</file_content_[0-9a-f]+>",
                  t, re.S)
    if m:
        t = m.group(1)
    # JSON 덩어리만 남긴다(앞의 File ID/Title/MIME 헤더 줄 제거)
    i = t.find("{")
    if i > 0:
        t = t[i:]
    try:
        o = json.loads(t)
    except Exception as e:
        raise SystemExit("[newsclips-slack] JSON 파싱 실패: %s\n"
                         "  slack_read_file 응답을 **그대로** 넘겼는지 확인하세요." % e)

    for key in ("body-plain", "body_plain", "text"):
        body = o.get(key)
        if body and body.strip():
            return body, o.get("subject") or "", key

    # 평문이 없으면 HTML 에서 만든다(최후 수단)
    for key in ("body-html", "simplified_html", "preview"):
        h = o.get(key)
        if h and h.strip():
            h = re.sub(r"<br\s*/?>", "\n", h)
            h = re.sub(r"</p\s*>", "\n\n", h)
            h = re.sub(r"<[^>]+>", "", h)
            return html.unescape(h), o.get("subject") or "", key + "(HTML변환)"
    raise SystemExit("[newsclips-slack] 본문 필드를 못 찾았습니다(body-plain 없음).")


def main():
    body, subject, src = extract(sys.stdin.read())
    sys.stderr.write("[newsclips-slack] 제목: %s\n" % (subject or "(없음)"))
    sys.stderr.write("[newsclips-slack] 본문 출처: %s / %d자\n" % (src, len(body)))
    # '본문 없이 링크만 지운 판본'을 잘못 집으면 링크가 전부 빈다 - 경고.
    if body.count("링크:") and not re.search(r"링크\s*[:：]\s*\S", body):
        sys.stderr.write("[newsclips-slack] [경고] '링크:' 뒤가 모두 비었습니다."
                         " body-plain-without-links 를 집었을 수 있습니다\n")
    sys.stdout.write(body)


if __name__ == "__main__":
    main()
