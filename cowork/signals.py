#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""판정할 때 본 신호를 건별로 남긴다. [2026-09-22 신설]

[왜] 사용자 질문: *"고중저 판단에 대한 백데이터는 R열 판정사유 하나밖에
  없는 것 같은데 그게 맞아?"* — 맞았다. 판정을 바꾸는 신호 중 상당수가
  **화면에만 찍히고 사라지고 있었다.**
    - `hist.py` 는 ★(3회 이상 나간 이야기)·[주체겹침]을 찍지만 쓰기 호출이
      하나도 없었다. 그날 어떤 건에 그 표시가 떴는지 아무 데도 안 남는다.
    - agenda·newfact·repeat 게이트도 마찬가지다. 어떤 건이 왜 걸렸는지
      로그로만 흘러갔다.
  그래서 "대표님이 뺀 건에 어떤 신호가 떠 있었나"를 통계로 낼 수 없었다.
  매번 사람이 눈으로 잡아내는 수밖에 없었던 이유다.

[무엇을] out/signals-<날짜>.json 한 파일에 모은다.
    {"date": "...",
     "hist":  {"187": {"star": 3, "overlap": true}},
     "gates": {"187": ["agenda:물량", "repeat:재집계"]}}
  verdict_xlsx 가 이걸 읽어 '발송이력'·'게이트' 열로 펼친다.

[주의] 덮어쓰지 않고 **섹션 단위로 병합**한다. hist 와 각 게이트는 서로
  다른 시점에 돌기 때문이다. 판정을 다시 하면 그날 파일을 지우고 다시 쌓는다.
"""
import json, os

def path(base, day):
    return os.path.join(base, "out", "signals-%s.json" % day)

def load(base, day):
    p = path(base, day)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except ValueError:
        return {}

def record(base, day, section, mapping):
    """mapping: {idx: 값}. 같은 섹션은 통째로 갈아끼운다(그 단계를 다시 돌린 것)."""
    d = load(base, day)
    d["date"] = day
    d[section] = {str(k): v for k, v in mapping.items()}
    p = path(base, day)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def add_gate(base, day, gate, items):
    """items: [(idx, 종류)]. 게이트별로 누적한다."""
    d = load(base, day)
    d["date"] = day
    g = d.setdefault("gates", {})
    # 같은 게이트의 옛 기록은 지우고 다시 넣는다
    for k in list(g):
        g[k] = [x for x in g[k] if not x.startswith(gate + ":")]
    for idx, kind in items:
        g.setdefault(str(idx), []).append("%s:%s" % (gate, kind))
    d["gates"] = {k: v for k, v in g.items() if v}
    p = path(base, day)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
