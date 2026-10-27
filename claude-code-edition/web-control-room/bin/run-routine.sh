#!/bin/zsh
# launchd → 루틴 실행 진입점. 서버 디스패처 경유(로깅·dedup·타임아웃·재시도), 서버가 죽어 있으면 직접 실행 폴백.
# 사용: run-routine.sh <slug>
# 위치: ~/.claude/scripts/ (2026-08-01 웹결정: launchd가 ~/Documents TCC 차단으로
#        스크립트를 못 읽어 exit 127로 죽던 문제 해소. 로그도 Documents 밖으로 이전)
SLUG="$1"
[[ -z "$SLUG" ]] && { echo "usage: run-routine.sh <slug>"; exit 2; }
PORT=8787
LOG=~/.claude/logs/launchd-routines.log
echo "[$(date '+%F %T')] $SLUG 트리거" >> "$LOG"

# 1순위: 서버 디스패처
RES=$(curl -sf -X POST "http://127.0.0.1:$PORT/api/run/$SLUG" -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
if [[ $? -eq 0 ]]; then
  echo "[$(date '+%F %T')] $SLUG → 서버 디스패치: $RES" >> "$LOG"
  exit 0
fi
# 409(오늘 핸드오프 이미 있음)도 curl -f는 실패로 보므로 구분
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/api/run/$SLUG" -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
if [[ "$CODE" == "409" ]]; then
  echo "[$(date '+%F %T')] $SLUG → dedup 스킵(오늘 핸드오프 존재)" >> "$LOG"
  exit 0
fi

# 2순위: 서버 재기동 후 재시도 (직접 claude 실행은 하지 않는다)
# 이유: launchd가 띄운 셸에는 FDA가 없어 ~/Documents 접근이 막힌다(2026-08-01 exit 127 사건).
#       원장 접근이 되는 유일한 경로는 FDA를 가진 AgentOpsServer.app(서버)의 자식 프로세스뿐이다.
echo "[$(date '+%F %T')] $SLUG → 서버 불응(code=$CODE), 서버 재기동 시도" >> "$LOG"
launchctl kickstart -k "gui/$(id -u)/com.agentops.server" >> "$LOG" 2>&1
for i in 1 2 3 4 5 6; do
  sleep 5
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/api/run/$SLUG" -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
  case "$CODE" in
    200) echo "[$(date '+%F %T')] $SLUG → 재기동 후 디스패치 성공" >> "$LOG"; exit 0 ;;
    409) echo "[$(date '+%F %T')] $SLUG → dedup 스킵(재기동 후)" >> "$LOG"; exit 0 ;;
  esac
done
echo "[$(date '+%F %T')] $SLUG → 서버 복구 실패(code=$CODE). 이 회차는 미실행, 아침 브리핑이 결측으로 감지함" >> "$LOG"
# (선택) 알림 채널이 있으면 여기서 호출한다. 예: hermes/Discord, Slack webhook, osascript 알림
# ~/.local/bin/hermes send -t "discord:#일반" -q "⚠️ 관제 서버 불응으로 루틴 '$SLUG' 미실행"  2>/dev/null
exit 1
