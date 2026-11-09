# DTB 실행 절차 — 체크리스트 (2026-11-09 개정, 이 문서 하나만 따른다)

**규칙 다섯 개**
1. 실행 중에는 **이 문서만** 따른다. 판정 기준은 `docs/판정기준-요약.md` 한 장. 변경이력(`docs/운영지침-변경이력.md`)은 근거 기록이라 실행 중 읽지 않아도 된다.
   이전 절차서 전문은 `docs/보관/실행지침-DTB-20261109-이전.md` (참고용, 따르지 않는다).
2. 단계마다 **흔적**(파일·게이트 결과·확인 메모)을 남긴다. `out/checklist-notes-<날짜>.md` 에 `M1: …` ~ `M5: …` 를 그때그때 적는다.
3. 게이트는 실패해도 발송을 막지 않는다(09-22 사용자 선택). 대신 **실패 게이트와 체크리스트 미완 항목을 알림·보고 첫머리에 그대로** 적는다.
4. `run.sh send final` 금지. 같은 날 두 번째 발송 금지(발송 기록이 있으면 send 가 거부 — 헷갈리면 `run.sh sent`). `send` 출력은 자르지 않는다.
5. 파이썬을 직접 부르지 말고 `bash cowork/run.sh <명령>` 으로. tier2·links·body·linkcheck·bodygo 는 timeout 175000 으로 **한 호출에 한 번씩**.

경로: 클라우드에서는 REPO 가 마운트(MNT), 작업본(W)은 run.sh 가 잡는다. 산출물은 W 의 `out/` 에 생기고 `run.sh push` 가 REPO 로 복사한다.

## 0. 준비
| # | 할 일 | 확인 |
|---|---|---|
| 0-1 | `run.sh workday` | 3 이면 아무것도 하지 않고 끝(알림 없음) |
| 0-2 | `run.sh sync` (맨 처음 한 번) | |
| 0-3 | `out/brief-<오늘>.md` 와 `out/prompt-<오늘>.txt` 가 이미 있으면 재실행·재발송하지 않고 상태만 보고 | |
| 0-4 | `run.sh persona` (전일 채점·backtest·scorecard·report 는 하지 않는다) | |

## 1. tier0 수집
| # | 할 일 | 확인 |
|---|---|---|
| 1-1 | Slack `slack_read_channel` 로 #3_트렌드 `C04KU1PBFEG`, #dtb-tier0 `C0BRP6UPGG6` (limit 10, detailed, oldest = `TZ=Asia/Seoul date -d "today 00:00" +%s`) | |
| 1-2 | 응답 **원문 그대로** `run.sh slack-save 3trend <<'RAW' … RAW` / `run.sh slack-save tier0 <<'RAW' … RAW` | `기사 링크: N건` 기록 |
| 1-3 | `run.sh log` | `출처: latest.txt`(에이닷 ③ 파일). ③ 실패일이면 에이닷 0건 정상 |
| 1-4 | `run.sh collect` (timeout 120000) → `run.sh tier0` | |
| 1-5 | #dtb-tier0 의 Slackbot `[YYYY년 M월 D일] 조간 뉴스 클리핑` 파일을 `slack_read_file` → `run.sh newsclips-slack <<'RAW' … RAW` (tier0 뒤) | `링크 보유 N/M`, 90% 미만이면 보고. 원문은 `cowork/raw/newsclips-mail-<날짜>.txt` 에 남으니 다시 넣을 땐 `run.sh newsclips-mail < 그 파일` |
| 1-6 | `run.sh samsung-slack` | 미게시면 '삼성 슬랙 미게시' 보고 |
| 1-7 | `run.sh linkcheck tier0` | OK/의심/실패/미확인·커버리지 기록 |
| 1-8 | 국책과제는 ① 결과를 쓴다('대기업 참여 가능' 공고만, 0건이면 섹션 없음) | |

## 2. 후보 만들기
| # | 할 일 | 확인 |
|---|---|---|
| 2-1 | `run.sh tier2` 를 '전부 완료'까지 → `run.sh tier2-done` | |
| 2-2 | `run.sh community` → `run.sh buzz` (생략 금지) | HN/GitHub/HF 건수, buzz 상위 3 |
| 2-3 | `run.sh refine` (클라우드는 자동 --no-links) → `run.sh links` 를 '전부 시도 완료'까지 | 못찾음·네트워크성 합계 |
| 2-4 | `run.sh body` 를 '전부 완료'까지 | `본문 보유율` (60% 미만 경고 보고) |
| 2-5 | `run.sh brief` → `run.sh extend` → `grep -c "^\[링크 미확보 후보\]" out/prompt-<날짜>.txt` = 1 | 0 이면 그 프롬프트로 한 판정은 무효 |

## 3. 전수 판정
| # | 할 일 | 확인 |
|---|---|---|
| 3-1 | `run.sh carryover inject` → `run.sh late` → `run.sh blind` → `run.sh timeline` → **`run.sh prejudge`** | 자동 '저' 건수 |
| 3-2 | **판정 전에 `docs/판정기준-요약.md` 와 `docs/사례집.md` 최근 10건을 읽는다** | **M1** 적용 사례 번호 |
| 3-3 | `out/candidates-focus-<날짜>.txt`(남은 후보)를 50~80건씩 **실제로 읽고** `out/verdict-<날짜>.csv` 에 한 줄씩(`idx,grade,reason,rank`). 맨 위 경쟁사속보·반향·커뮤니티부터. 이월(C) 건은 등급 고정 | 키워드 스크립트로 사유를 찍지 않는다 |
| 3-4 | 고 사유·중 축·저 코드는 판정기준-요약 2절 형식대로 | |

## 4. '고' 확인 — 건마다, 손으로
| # | 할 일 | 확인 |
|---|---|---|
| 4-1 | `run.sh bodygo` → 찍힌 '고' 리드를 **전부 읽는다**(출력을 걸러 보지 않는다). 본문 없는 '고'는 `run.sh find … --body 2` 로 본문 확보. 끝에 `run.sh bodypeek --check` | **M2** |
| 4-2 | `run.sh hist` → `run.sh repeat` (해명 요구가 나오면 사유에 `재심:`·`중복검토:`) | |
| 4-3 | `run.sh gocheck --template` → 주체검색어(국문·영문 모두)·리드인용(본문 그대로) 기입 → `run.sh gocheck --search` → **검색결과·이전판정(최근 7일 같은 사건 판정)을 읽고** 흐름(신규/MM-DD)·판단(`새사실:`·`다른 사건:`·`재판정:`) 기입 | 같은 사건이면 '고'를 내린다 |
| 4-4 | `run.sh facts --template` → `out/facts-<날짜>.csv` 를 본문을 보고 채운다. '고' 행의 흐름은 gocheck 와 같게. **스크립트 기본값으로 채우지 않는다** | |
| 4-5 | 순위: 판정기준-요약 3절대로 '고'에 rank 1..N, 같은 값을 `out/handrank-<날짜>.csv`(idx,발송순위,발송순위 사유)에도 | |
| 4-6 | `run.sh verdict` 종료코드 0 (gocheck·facts·repeat·agenda·newfact·spread·novelty·rescue 포함) | 실패 게이트는 등급을 고쳐서 통과(문구로 통과 금지) |

## 5. 발송본 작성
| # | 할 일 | 확인 |
|---|---|---|
| 5-1 | `run.sh late` 한 번 더(새 L 건 → blind → 판정 → verdict 0) | |
| 5-2 | `run.sh config` → `run.sh budget` (건당 주요·시사점 글자 예산) | |
| 5-3 | rank 상위부터 최대 20건, '중'으로 채우지 않는다. 헤드라인·매체·링크는 후보 값 그대로(국문 우선, 커뮤니티 전재 링크는 원 매체로) | |
| 5-4 | 발송본 `out/brief-<날짜>.md` 를 쓰고 `run.sh cats --template` → 배치표 `out/placement-<날짜>.csv` 를 **기사마다 CATEGORY_RULES 0번부터** 채운 뒤 표대로 섹션을 맞춘다(9번은 `8번 아님:`, 10번은 `8번 아님:`·`9번 아님:`) | `run.sh cats` 0 |
| 5-5 | 문체 명사형 종결, 마침표 없음. `run.sh send dry` 로 메시지 1개(4080자) — 넘치면 압축, 그래도 넘치면 rank 최하위부터 뺀다 | 뺀 건의 rank 보고 |

## 6. 독립 검토
| # | 할 일 | 확인 |
|---|---|---|
| 6-1 | `run.sh indep --pack` → Agent 도구(general-purpose)에 `docs/독립검토-프롬프트.md` 의 '---' 사이를 날짜·경로만 바꿔 그대로 준다(내 사유는 주지 않는다) | `out/indep-review-<날짜>.csv` |
| 6-2 | `run.sh indep` → 불일치마다 **다시 보고** 처리 칸에 `반영:…` 또는 `유지:<이유>`. 반영이면 verdict·facts·gocheck·배치표·발송본을 함께 고치고 4-6 부터 다시 | **M5** |

## 7. 검증 (순서대로)
| # | 할 일 | 확인 |
|---|---|---|
| 7-1 | 후보목록 대조: 발송본 URL 이 prompt 후보에 있는지(밖이면 find 대체·국책과제·커뮤니티 원 매체 교체만 허용, 사유 보고). 카테고리별 번호 1부터 | |
| 7-2 | `run.sh linkcheck` — 실패는 고치거나 빼고 다음 rank 로 대체. 의심은 **눈으로 대조** | **M3** |
| 7-3 | `grep -nE "&#|hellip|middot|lsquo|rsquo|: 네이트 뉴스|- 머니투데이|\[시그널\]" out/brief-<날짜>.md` → 손으로 지움(retitle 이 되살린다) | |
| 7-4 | `run.sh kr-alt` — 영문 기사에 같은 사건 국내 보도가 있으면 국문으로 교체 후 linkcheck 재실행, 아니면 `out/kralt-ok-<날짜>.txt` 에 사유 | |
| 7-5 | `run.sh cats` → `run.sh insight` → `run.sh style` → `run.sh deals`(빠진 큰 딜마다 사유) | **M4** |
| 7-6 | `run.sh verdict` 0 → `run.sh carryover check` → `run.sh push` | |

## 8. 발송
| # | 할 일 | 확인 |
|---|---|---|
| 8-1 | `run.sh send dry` → `run.sh verdict-xlsx` → `run.sh push` | |
| 8-2 | `run.sh send` (초안 채널 'DTB 업로드', 출력 자르지 않기) | `[발송] i/N ok`·message_id |
| 8-3 | `run.sh sent` → `run.sh carryover done` → `run.sh push` → `run.sh cloud-push`(detached HEAD 거부면 `git checkout -B main && git push origin HEAD:main`) | 커밋 해시 |
| 8-4 | 매달 첫 영업일엔 `run.sh cloud-prune` 후 cloud-push | |

## 9. 보고
1. `run.sh stats --oneline` 과 **`run.sh checklist --oneline`** — 알림 둘째·셋째 줄, 채팅 보고 첫 문단에 그대로.
2. 알림 첫 문장: `[발송] i/N ok (message_id, 채널)`. 그다음 실패 게이트(이름·사유), 체크리스트 미완 항목, 단계 시작 시각, 클라우드(pull 해시·비밀키 출처·cloud-push).
3. 채팅: 발송본 목록(카테고리별 `번호. 헤드라인 (매체) [원문](URL)`) + 아래 숫자를 짧게 —
   tier0·후보 규모 / 삼성·NewsClips 건수·링크 보유율 / linkcheck(tier0·발송본) / retitle 교체 수 / 후보목록 밖 링크 사유 /
   전수 판정 고·중·저(자동 '저' 몇 건 포함) / 본문 보유율 / 분량 컷 rank / 반향 한 줄(buzz 상위 3·커뮤니티 건수) / 독립 검토 불일치와 처리 / 배치 애매.
4. `SendUserFile` 로 `out/brief-<날짜>.md` 와 `out/판정-<날짜>.xlsx`.

## 사용자가 발송본을 고치면
- 같은 턴에 `run.sh review <<'RAW'` (`제외|추가|배치 | 헤드라인 | url | 사용자 사유 | 일반화`). 사유를 지어내지 않는다(못 받았으면 `(사유 미제시)`).
- 재발송은 사용자가 시킬 때만 `run.sh send resend`. 확인용으로 `editMessageText`·`forwardMessage` 를 쓰지 않는다.
- 실제 발송분과 저장소 발송본이 달라지면 발송분을 `out/발송원본/brief-<날짜>-msg<id>.md` 로 보존한다.
