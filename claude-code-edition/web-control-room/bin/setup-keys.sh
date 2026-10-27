#!/bin/zsh
# API 키를 macOS Keychain에 저장한다. 키는 파일·환경변수에 남기지 않는다.
# 사용: bin/setup-keys.sh  (대화형으로 두 키를 물어봄. 비워두면 해당 키는 건너뜀)
set -e
echo "AgentOps 웹 관제 — API 키 셋업 (Keychain 저장, 파일에 남지 않음)"
echo

read -s "OPENAI_KEY?OpenAI API 키 (sk-..., 건너뛰려면 엔터): "
echo
if [[ -n "$OPENAI_KEY" ]]; then
  security delete-generic-password -s agentops-openai >/dev/null 2>&1 || true
  security add-generic-password -s agentops-openai -a agentops -w "$OPENAI_KEY"
  echo "  ✅ agentops-openai 저장 완료"
fi

read -s "GEMINI_KEY?Gemini API 키 (AIza..., 건너뛰려면 엔터): "
echo
if [[ -n "$GEMINI_KEY" ]]; then
  security delete-generic-password -s agentops-gemini >/dev/null 2>&1 || true
  security add-generic-password -s agentops-gemini -a agentops -w "$GEMINI_KEY"
  echo "  ✅ agentops-gemini 저장 완료"
fi

echo
echo "확인:"
for s in agentops-openai agentops-gemini; do
  security find-generic-password -s "$s" >/dev/null 2>&1 && echo "  $s: 있음" || echo "  $s: 없음"
done
echo
echo "서버 재시작 후 반영: launchctl kickstart -k gui/$(id -u)/com.agentops.server"
