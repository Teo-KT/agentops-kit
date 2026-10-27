#!/usr/bin/env python3
"""AgentOps 이벤트 로거 (순수 관측자).

SessionStart / Stop / SubagentStop / SessionEnd / PostToolUse(Task) 훅에서 호출되어
stdin의 payload JSON을 ~/Documents/Claude/AgentOps/events/YYYY-MM.jsonl 에 1줄로 append한다.

불변 원칙:
- 어떤 경우에도 exit 0 (fail-open). 로깅 실패가 세션을 방해하면 안 된다.
- stdout/stderr에 아무것도 출력하지 않는다 (SessionStart stdout은 세션 컨텍스트로 주입됨).
- decision 등 훅 응답 JSON을 절대 반환하지 않는다.
- stdlib만 사용 (python3 3.9 호환).
"""
import sys
import os
import re
import json
import datetime

EVENTS_DIR = os.path.join(os.environ.get("AGENTOPS_HOME", os.path.expanduser("~/AgentOps")), "events")
MAX_STR = 2000      # 개별 문자열 절단 상한
MAX_LINE = 32768    # 레코드 1줄 상한 (bytes 근사)
MAX_STDIN = 4_000_000

# 스케줄 태스크 세션의 트랜스크립트 첫 엔트리 content가 이 태그로 시작한다.
SCHEDULED_TASK_RE = re.compile(r'^<scheduled-task\s+name="([A-Za-z0-9._-]+)"')
TRANSCRIPT_HEAD_LINES = 8  # 태그는 선두 엔트리에만 있음. 뒤쪽 툴 출력에 인용된 태그는 오탐이므로 앞부분만 본다.


def clip(obj, depth=0):
    """payload를 방어적으로 절단한다 (긴 프롬프트, tool_response 대비)."""
    if depth > 6:
        return "...[깊이 초과]"
    if isinstance(obj, str):
        return obj if len(obj) <= MAX_STR else obj[:MAX_STR] + "...[절단]"
    if isinstance(obj, dict):
        return {k: clip(v, depth + 1) for k, v in list(obj.items())[:80]}
    if isinstance(obj, list):
        return [clip(v, depth + 1) for v in obj[:40]]
    return obj


def _entry_texts(entry):
    """트랜스크립트 엔트리에서 검사할 content 문자열들을 뽑는다."""
    c = entry.get("content")
    if isinstance(c, str):
        yield c
    msg = entry.get("message")
    if isinstance(msg, dict):
        mc = msg.get("content")
        if isinstance(mc, str):
            yield mc
        elif isinstance(mc, list):
            for block in mc[:4]:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    yield block["text"]


def detect_routine_slug(transcript_path):
    """스케줄 태스크 세션이면 루틴 슬러그, 아니면 None (fail-open).

    스케줄 세션은 트랜스크립트 선두(ai-title 등 포함 5줄 내)의 queue-operation/user
    엔트리 content가 '<scheduled-task name="슬러그" ...>'로 시작한다(2026-07 실측).
    SessionStart 시점에 파일이 아직 없으면 None.
    """
    try:
        if not transcript_path or not os.path.isfile(transcript_path):
            return None
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(TRANSCRIPT_HEAD_LINES):
                line = f.readline(MAX_STDIN)
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                for text in _entry_texts(entry):
                    m = SCHEDULED_TASK_RE.match(text.lstrip())
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return None


def main():
    try:
        raw = sys.stdin.read(MAX_STDIN)
    except Exception:
        raw = ""
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {"_nondict": clip(data)}
    except Exception:
        data = {"_parse_error": True, "_raw": raw[:MAX_STR]}

    now = datetime.datetime.now().astimezone()
    rec = {
        "v": 1,
        "ts": now.isoformat(timespec="seconds"),
        "event": data.get("hook_event_name", "unknown"),
        "session_id": data.get("session_id"),
        "cwd": data.get("cwd"),
        "transcript_path": data.get("transcript_path"),
        "payload": clip(data),
    }
    # 스케줄 태스크 세션이면 루틴 슬러그를 병기한다 (2026-07-27 A16 승인, 식별 불가 시 null).
    rec["payload"]["routine_slug"] = detect_routine_slug(data.get("transcript_path"))
    line = json.dumps(rec, ensure_ascii=False)
    if len(line.encode("utf-8", errors="replace")) > MAX_LINE:
        rec["payload"] = {"_overflow": True,
                          "routine_slug": rec["payload"].get("routine_slug")}
        line = json.dumps(rec, ensure_ascii=False)

    path = os.path.join(EVENTS_DIR, now.strftime("%Y-%m") + ".jsonl")
    os.makedirs(EVENTS_DIR, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        # 단일 write 호출로 줄 단위 원자성 확보 (동시 세션 append 경합 대비)
        os.write(fd, (line + "\n").encode("utf-8", errors="replace"))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
