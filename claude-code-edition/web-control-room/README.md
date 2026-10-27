# web-control-room — 로컬 웹 관제실 (참고 구현)

원 시스템에서 실제로 돌고 있는 관제 서버를 **개인 정보만 걷어내고 그대로** 옮긴 것이다. 제품이 아니라 참고 구현(reference implementation)이므로, 자기 환경에 맞게 고쳐 쓰는 것을 전제로 한다. Node 표준 라이브러리만 쓰고 외부 의존성이 하나도 없다(`npm install` 불필요).

## 이게 왜 필요한가

원장(AGENTS.md)과 핸드오프만으로도 시스템은 완전히 동작한다. 다만 루틴이 열 개를 넘어가면 세 가지가 불편해진다.

1. **지금 상태를 보려면 파일을 열어야 한다.** 오늘 뭐가 돌았고 뭐가 빠졌는지 한눈에 안 보인다.
2. **결정이 하루 늦는다.** 브리핑을 보고 "그렇게 해"라고 답해도, 그 답을 처리하는 건 다음 날 새벽 루틴이다.
3. **앱을 켜 둬야 한다.** 스케줄러가 앱에 묶여 있으면 노트북을 닫는 순간 조직이 멈춘다.

이 서버가 셋을 한꺼번에 해결한다. 화면으로 상태를 보여 주고, 결정 버튼을 누르면 그 자리에서 헤드리스 세션을 띄워 처리하고, launchd(macOS 상주 스케줄러)의 실행 대상이 되어 앱과 무관하게 돈다.

## 구성

```
config.template.json   → config.json 으로 복사해 경로 수정 (키는 넣지 않는다)
server/server.js       HTTP + SSE 서버 (표준 라이브러리만)
server/ledger.js       원장 파서 — 핸드오프 frontmatter·로스터·결측 판정·결정 대기·피드백
server/dispatcher.js   엔진 실행·작업 관리·알림·데이터 경계 가드
server/routine-meta.json  루틴 한 줄 설명 (화면 표시용, 자기 루틴으로 교체)
ui/                    정적 SPA (오늘·결정·직원·실행·일정·질의 탭)
bin/setup-keys.sh      선택 엔진의 API 키를 OS 키체인에 저장
bin/gen-launchd.js     AGENTS.md 로스터 → launchd plist 자동 생성
bin/install-launchd.sh {server | routine <slug> | status}
bin/run-routine.sh     launchd 진입점 (서버 디스패치 → 재기동 재시도 → 미실행 보고)
hermes-skill/          선택: 채팅 앱에서 관제 API를 부르게 하는 스킬 예시
```

## 원장 계약 (반드시 지킬 것)

서버는 **원장을 읽기만 한다.** `handoffs/`·`events/`는 절대 쓰지 않는다. 서버가 쓰는 파일은 셋뿐이다.

| 파일 | 용도 |
|---|---|
| `dashboard/decided_needs.json` | 결정 억제 키. 처리된 안건이 화면에 유령으로 남지 않게 한다 |
| `dashboard/today_feedback.json` | 오늘 우선순위 항목에 대한 사람의 피드백(완료·수정 등) |
| `queue/*.jsonl`, `logs/` | 서버 자체의 감사 로그 |

이 경계를 무너뜨리면(서버가 핸드오프를 고치기 시작하면) 원장이 더 이상 사실의 기록이 아니게 되고, 결측 감지와 이력 추적이 동시에 무너진다.

## 설치

```bash
cp -R web-control-room ~/agentops-web
cd ~/agentops-web && cp config.template.json config.json   # 경로를 자기 것으로 수정
node server/server.js                                      # http://127.0.0.1:8787
```

상주화(macOS)는 `../LEVEL2_스케줄_상주화.md`를 따른다. **macOS에서 `~/Documents` 아래에 두면 launchd가 파일을 못 읽는다**(TCC). 그 함정과 해법이 그 문서에 있다.

## 고칠 곳

| 무엇 | 어디 |
|---|---|
| 조직 이름·대표 호칭 | `ui/index.html`, `ui/app.js`(조직도 카드), `server/dispatcher.js`(알림 문구) |
| 루틴 설명 | `server/routine-meta.json` |
| 엔진 구성 | `config.json` — 쓰지 않는 엔진은 빈 값으로 두면 그 레인이 비활성된다 |
| 알림 채널 | `config.json`의 `discord` 블록 + `bin/run-routine.sh` 하단 주석 |

## 안전

- 바인딩은 `127.0.0.1` 고정이다. 0.0.0.0으로 바꾸지 마라. 외부에서 쓰고 싶으면 VPN(Tailscale 등)으로 붙는다.
- 구독 CLI를 다중 사용자 서비스로 프록시하지 않는다(약관 위반).
- API 키는 config.json이 아니라 OS 키체인에 둔다(`bin/setup-keys.sh`).
