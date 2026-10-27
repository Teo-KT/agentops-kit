# Headless 운영 가이드 (구독 없이, API 키로)

Claude Code는 구독 없이도 **API 키(종량제)**로 동작한다. 그리고 headless 모드(`claude -p`)와 OS 스케줄러를 조합하면 **앱을 열어 두지 않아도** 루틴이 돈다. 이 문서는 킷의 Claude Code 에디션을 그 방식으로 운영하는 절차다.

## 누구를 위한 것인가

- Claude 구독은 없지만 API 키(pay-as-you-go)는 만들 수 있는 사람
- 구독자이지만 데스크톱 앱을 항상 켜 둘 수 없는 사람 (headless 실행은 앱과 무관하게 터미널에서 돈다)

## 1. 설치와 인증 (10분)

```bash
# Claude Code CLI 설치 (없다면)
npm install -g @anthropic-ai/claude-code

# API 키 발급: platform.claude.com → API Keys
# 키는 셸 프로필이 아니라 OS 자격증명 저장소에 두는 것을 권장한다.
# macOS 예시 (키체인에 저장):
security add-generic-password -a "$USER" -s anthropic-api-key -w "sk-ant-..."
```

실행 스크립트에서 키를 꺼내 환경변수로 주입한다(아래 3단계 예시 참조). **키를 스크립트나 설정 파일에 평문으로 적지 않는다.**

## 2. 루틴 준비

킷의 QUICKSTART 1~3단계를 그대로 따른다(홈 폴더, AGENTS.md, 루틴 프롬프트). 차이는 실행 방식뿐이다. 루틴 프롬프트는 스케줄 태스크 대신 파일로 둔다:

```
~/AgentOps/routines/
├── daily-scout.md      # example-pack의 SKILL.md 내용 (치환 완료본)
└── morning-brief.md    # skills/morning-brief/SKILL.md 치환 완료본
```

## 3. 실행 스크립트

```bash
#!/bin/bash
# ~/AgentOps/bin/run-routine.sh <루틴이름>
set -e
ROUTINE="$1"
export AGENTOPS_HOME="$HOME/AgentOps"
export ANTHROPIC_API_KEY="$(security find-generic-password -a "$USER" -s anthropic-api-key -w)"

cd "$AGENTOPS_HOME"
claude -p "$(cat "$AGENTOPS_HOME/routines/$ROUTINE.md")" \
  --allowedTools "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Bash(mkdir:*),Bash(date:*)" \
  >> "$AGENTOPS_HOME/logs/$ROUTINE.log" 2>&1
```

핵심은 `--allowedTools`다. headless에서는 권한 프롬프트에 답할 사람이 없으므로 **루틴에 필요한 도구만 허용목록으로 미리 지정**한다. 전체 우회 플래그(`--dangerously-skip-permissions`)는 이름 그대로 위험하니 쓰지 않는다. 허용 범위는 루틴별로 다르게 두는 것이 안전하다(수집 루틴에 Write는 필요하지만 임의 Bash는 불필요).

비용 제어: 루틴별로 모델을 지정할 수 있다(`--model claude-haiku-4-5` 등). 수집·분류는 Haiku, 취합·판단은 Sonnet/Opus로 섞으면 월 비용이 크게 준다. 프롬프트 캐싱은 자동 적용된다.

## 4. 스케줄 등록

### macOS (launchd 권장)

`~/Library/LaunchAgents/com.agentops.daily-scout.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.agentops.daily-scout</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string>
    <string>/Users/YOUR_NAME/AgentOps/bin/run-routine.sh</string>
    <string>daily-scout</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>6</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
</dict></plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.agentops.daily-scout.plist
launchctl kickstart -k gui/$(id -u)/com.agentops.daily-scout   # 지금 즉시 1회 발화해 종료 코드 확인
```

launchd는 cron과 달리 **잠들었던 시각의 작업을 깨어난 뒤 실행**해 준다(전원이 꺼져 있었다면 다음 부팅 후). 브리핑 루틴은 수집 루틴보다 30분 뒤로 잡는다.

> ⚠️ **macOS 권한 함정.** launchd가 띄운 셸에는 `~/Documents`·`~/Desktop`·`~/Downloads` 접근 권한이 없다. 실행 스크립트와 원장을 그 안에 두면 **파일을 열지도 못하고 `exit 127`로 죽는다.** 원장 홈을 `~/AgentOps`로, 스크립트를 `~/.claude/scripts/`로 두면 이 문제가 통째로 사라진다. 자세한 실패 사례와 해법은 [`LEVEL2_스케줄_상주화.md`](LEVEL2_스케줄_상주화.md).

### Linux

```bash
crontab -e
# 매일 06:00 수집, 06:30 브리핑
0 6 * * * /home/YOU/AgentOps/bin/run-routine.sh daily-scout
30 6 * * * /home/YOU/AgentOps/bin/run-routine.sh morning-brief
```

cron은 꺼진 시간대 작업을 건너뛴다. 노트북이면 `anacron`을 쓰거나 launchd식 catch-up을 스크립트에 직접 넣는다(오늘 핸드오프가 없으면 실행).

### Windows

```powershell
schtasks /Create /TN "AgentOps daily-scout" /TR "wsl bash ~/AgentOps/bin/run-routine.sh daily-scout" /SC DAILY /ST 06:00
```

## 5. 헤드리스에서 달라지는 것

`claude -p`는 데스크톱 앱과 **쓸 수 있는 도구가 다르다.** 앱 안에서만 제공되는 기능(예: 앱의 스케줄 태스크를 코드로 조작하는 도구)은 헤드리스에 없다.

원 시스템은 전환 과정에서 이걸 뒤늦게 발견해 루틴 프롬프트 세 곳을 고쳐야 했다. 스케줄 재장전 → 서버 API 호출로, 스케줄 목록 대조 → `launchctl list` 대조로 바꾸는 식이었다.

**전환 전 점검**: 루틴 프롬프트가 앱 전용 기능에 기대는 곳이 있는가. 있으면 셸 명령이나 파일 기반으로 먼저 바꾼다. 웹 검색·파일 조작·Bash 같은 기본 도구는 그대로 쓸 수 있다.

## 6. 확인

- 첫 실행은 손으로: `~/AgentOps/bin/run-routine.sh daily-scout` → `handoffs/{오늘}/`에 보고 생성 확인
- 다음 날 `briefs/{오늘}_brief.md`에 그 루틴이 나타나면 무인 운영 성립
- 로그는 `logs/`에 쌓인다. 브리핑 루틴이 결측 감지를 대신해 주므로 로그를 매일 볼 필요는 없다

## 비용 감각 (종량제, 모델 단가는 수시로 바뀌니 첫 주에 직접 확인할 것)

일간 루틴 4개 + 캐싱 기준 대략: Haiku 위주 월 $5~15, Sonnet 위주 $15~50, Opus 포함 $40~120. 루틴별 모델 혼합이 정석이다. 첫 주는 platform.claude.com의 사용량 대시보드를 확인하며 조정하라.

## 안전 원칙 (재확인)

- 허용목록은 최소로. 특히 headless에서 광범위한 `Bash` 허용은 금물
- API 키는 자격증명 저장소에만. 로그에 키가 찍히지 않는지 첫 실행 때 확인
- 발송·결제·삭제는 루틴에 넣지 않는다. AI는 초안까지, 클릭은 사람이
- 원장(events, handoffs)은 append-only. 이 원칙은 실행 방식과 무관하게 유지된다
