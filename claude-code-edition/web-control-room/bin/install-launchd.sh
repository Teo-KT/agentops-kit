#!/bin/zsh
# launchd 설치 스크립트
# 사용:
#   install-launchd.sh server            → 관제 서버 상주화
#   install-launchd.sh routine <slug>    → 루틴 1종 launchd 전환 (plist는 gen-launchd.js로 먼저 생성)
#   install-launchd.sh status            → 설치 현황
set -e
# 이 킷의 web-control-room 을 복사해 둔 위치. 환경변수로 덮어쓸 수 있다.
ROOT="${AGENTOPS_WEB:-$HOME/agentops-web}"
LA=~/Library/LaunchAgents
UID_N=$(id -u)

case "$1" in
  server)
    cp "$ROOT/launchd/com.agentops.server.plist" "$LA/"
    launchctl bootout "gui/$UID_N/com.agentops.server" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$LA/com.agentops.server.plist"
    launchctl kickstart "gui/$UID_N/com.agentops.server"
    echo "✅ com.agentops.server 상주 등록 완료 (http://127.0.0.1:8787)"
    ;;
  routine)
    SLUG="$2"
    [[ -z "$SLUG" ]] && { echo "usage: install-launchd.sh routine <slug>"; exit 2; }
    PL="$ROOT/launchd/com.agentops.routine.$SLUG.plist"
    [[ -f "$PL" ]] || { echo "plist 없음. 먼저: node $ROOT/bin/gen-launchd.js"; exit 1; }
    cp "$PL" "$LA/"
    launchctl bootout "gui/$UID_N/com.agentops.routine.$SLUG" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$LA/com.agentops.routine.$SLUG.plist"
    echo "✅ com.agentops.routine.$SLUG 등록 완료"
    echo "⚠️  Claude 앱 쪽 동일 스케줄을 비활성화해야 이중 실행이 없습니다 (dedup이 2차 방어)."
    ;;
  status)
    launchctl list | grep com.agentops || echo "(등록 없음)"
    ;;
  *)
    echo "usage: install-launchd.sh {server|routine <slug>|status}"; exit 2 ;;
esac
