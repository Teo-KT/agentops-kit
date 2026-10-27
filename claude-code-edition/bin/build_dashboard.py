#!/usr/bin/env python3
"""AgentOps 대시보드 빌더.

입력(모두 읽기 전용):
  - AgentOps/events/*.jsonl        훅이 남긴 이벤트 원장
  - AgentOps/handoffs/YYYY-MM-DD/  루틴 자기보고(핸드오프)
  - AgentOps/AGENTS.md             로스터(§1)와 데이터 흐름 지도(§4)
  - ~/.claude/projects/            트랜스크립트 선두(제목)와 subagents/*.meta.json
  - ~/.hermes/state.db             Hermes 세션·토큰 (mode=ro)

출력:
  - AgentOps/dashboard/dashboard.html  자체완결 파생 뷰 (외부 요청 0)
  - stdout 한국어 요약 3~5줄

stdlib 전용, python3 3.9 호환. 실패는 항상 관대하게 처리한다(깨진 줄 건너뜀).
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import sqlite3

AGENTOPS_DEFAULT = os.environ.get("AGENTOPS_HOME", os.path.expanduser("~/AgentOps"))
HERMES_DB = os.environ.get("AGENTOPS_HERMES_DB", os.path.expanduser("~/.hermes/state.db"))  # 없으면 해당 레인은 자동 생략
SYSTEM_START = datetime.date(2026, 7, 18)  # 결측 판정 시작일 (이전 이력 백필 없음)
if os.environ.get("AGENTOPS_SYSTEM_START"):  # 테스트용 오버라이드
    try:
        SYSTEM_START = datetime.date.fromisoformat(os.environ["AGENTOPS_SYSTEM_START"])
    except ValueError:
        pass
SCHEDULED_CWD_SLUGS = []  # 스케줄 세션 cwd 슬러그, 첫 실측 후 기입 (현재는 시간창만 사용)
LOCAL_TZ = datetime.datetime.now().astimezone().tzinfo

STATUS_LABEL = {
    "ok": "정상",
    "partial": "부분",
    "fail": "실패",
    "noreport": "보고 없음",
    "missing": "결측",
    "missing_app": "결측(앱 미가동 추정)",
}


def esc(s):
    return html.escape(str(s), quote=True)


def parse_dt(s):
    """ISO 문자열을 aware datetime으로. 실패 시 None."""
    if not s or not isinstance(s, str):
        return None
    try:
        s2 = s.strip().replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt.astimezone(LOCAL_TZ)
    except Exception:
        return None


# ---------------------------------------------------------------- 입력 로드


def load_roster(agentops):
    """AGENTS.md §1 로스터 표를 파싱한다."""
    path = os.path.join(agentops, "AGENTS.md")
    roster, flows = [], []
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return roster, flows
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            if "루틴 로스터" in line:
                section = "roster"
            elif "데이터 흐름" in line:
                section = "flows"
            else:
                section = None
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: ") or cells[0] in ("슬러그", "엣지"):
            continue
        if section == "roster" and len(cells) >= 5:
            roster.append(
                {
                    "slug": cells[0].strip("`"),
                    "cadence": cells[1],
                    "time": cells[2],
                    "dest": cells[3],
                    "handoff": cells[4],
                }
            )
        elif section == "flows" and len(cells) >= 3 and "→" in cells[1]:
            left, _, right = cells[1].partition("→")
            src = re.sub(r"\(.*?\)", "", left).strip()
            dsts = [re.sub(r"\(.*?\)", "", d).strip() for d in right.split(",")]
            for d in dsts:
                if src and d:
                    flows.append((src, d))
    return roster, flows


def mini_frontmatter(text):
    """PyYAML 없이 핸드오프 frontmatter를 관대하게 파싱한다."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, current = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val == "":
                out[key] = []
                current = key
            elif val.startswith("[") and val.endswith("]"):
                items = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
                out[key] = [v for v in items if v]
                current = None
            else:
                out[key] = val.strip("'\"")
                current = None
        elif current and line.strip().startswith("- "):
            out[current].append(line.strip()[2:].strip().strip("'\""))
    return out


def load_handoffs(agentops, dates):
    handoffs = []
    for d in dates:
        folder = os.path.join(agentops, "handoffs", d.isoformat())
        for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
            try:
                fm = mini_frontmatter(open(path, encoding="utf-8").read())
            except Exception:
                fm = {}
            slug = fm.get("routine") or os.path.splitext(os.path.basename(path))[0]
            run_at = parse_dt(fm.get("run_at")) or datetime.datetime.combine(
                d, datetime.time(6, 0), tzinfo=LOCAL_TZ
            )
            status = fm.get("status") if fm.get("status") in ("ok", "partial", "fail") else "partial"
            handoffs.append(
                {
                    "date": d,
                    "slug": slug,
                    "run_at": run_at,
                    "status": status,
                    "outputs": fm.get("outputs") or [],
                    "key_findings": fm.get("key_findings") or [],
                    "for": fm.get("for") or [],
                    "needs_user": fm.get("needs_user") or [],
                    "path": path,
                    "schema_ok": bool(fm.get("routine") and fm.get("run_at")),
                }
            )
    return handoffs


def load_events(agentops, start_dt):
    events, broken = [], 0
    for path in sorted(glob.glob(os.path.join(agentops, "events", "*.jsonl"))):
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    broken += 1
                    continue
                ts = parse_dt(rec.get("ts"))
                if ts is None or ts < start_dt:
                    continue
                rec["_ts"] = ts
                events.append(rec)
    return events, broken


def transcript_title(transcript_path):
    """트랜스크립트 선두 8KB에서 세션 제목을 추출한다."""
    try:
        with open(transcript_path, "rb") as fh:
            head = fh.read(8192).decode("utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r'"title"\s*:\s*"([^"\\]{1,120})"', head)
    if m:
        return m.group(1)
    m = re.search(r'"prompt"\s*:\s*"([^"\\]{1,80})"', head)
    if m:
        return m.group(1)
    return None


def subagent_types(transcript_path, session_id):
    """<세션ID>/subagents/agent-*.meta.json 에서 agentType 목록을 수집한다."""
    types = []
    if not transcript_path:
        return types
    base = os.path.join(os.path.dirname(transcript_path), session_id, "subagents")
    for meta in glob.glob(os.path.join(base, "agent-*.meta.json")):
        try:
            info = json.loads(open(meta, encoding="utf-8").read())
            types.append(info.get("agentType") or "agent")
        except Exception:
            types.append("agent")
    return types


def assemble_sessions(events):
    sessions = {}
    for ev in events:
        sid = ev.get("session_id")
        if not sid:
            continue
        s = sessions.setdefault(
            sid,
            {
                "id": sid,
                "start": ev["_ts"],
                "end": ev["_ts"],
                "turns": 0,
                "subagent_stops": 0,
                "task_types": [],
                "cwd": ev.get("cwd"),
                "transcript_path": ev.get("transcript_path"),
                "routine": None,
                "confidence": None,
            },
        )
        s["start"] = min(s["start"], ev["_ts"])
        s["end"] = max(s["end"], ev["_ts"])
        if ev.get("event") == "Stop":
            s["turns"] += 1
        elif ev.get("event") == "SubagentStop":
            s["subagent_stops"] += 1
        elif ev.get("event") == "PostToolUse":
            ti = (ev.get("payload") or {}).get("tool_input") or {}
            if isinstance(ti, dict) and ti.get("subagent_type"):
                s["task_types"].append(ti["subagent_type"])
        if ev.get("cwd"):
            s["cwd"] = ev["cwd"]
        if ev.get("transcript_path"):
            s["transcript_path"] = ev["transcript_path"]
    for s in sessions.values():
        s["title"] = transcript_title(s["transcript_path"]) if s["transcript_path"] else None
        s["meta_types"] = subagent_types(s["transcript_path"], s["id"])
    return sessions


def expected_on(cadence, day):
    if cadence == "daily":
        return True
    if cadence == "every2days":
        # 앱 스케줄러의 */2는 간격 기반이라 짝·홀 날짜가 고정되지 않는다(2026-07-18 실측).
        # 후보로는 매일 취급하고, 결측 판정은 detect_missing에서 이틀 연속 부재로만 본다.
        return True
    if cadence.startswith("weekly"):
        return day.weekday() == 0
    return False


def identify_routines(sessions, handoffs, roster, days):
    """3단 식별: 핸드오프 시간창 조인 → 기대시각 ±90분 → 제목."""
    slist = list(sessions.values())
    for h in handoffs:
        best, best_gap = None, None
        for s in slist:
            if s["routine"]:
                continue
            lo = s["start"] - datetime.timedelta(minutes=5)
            hi = s["end"] + datetime.timedelta(minutes=15)
            if lo <= h["run_at"] <= hi:
                gap = abs((h["run_at"] - s["start"]).total_seconds())
                if best is None or gap < best_gap:
                    best, best_gap = s, gap
        if best is not None:
            best["routine"], best["confidence"] = h["slug"], "확정"
            h["session"] = best["id"]
    for s in slist:
        if s["routine"]:
            continue
        if s["title"]:
            for r in roster:
                if r["slug"] in s["title"]:
                    s["routine"], s["confidence"] = r["slug"], "제목"
                    break
        if s["routine"]:
            continue
        best, best_gap = None, None
        for r in roster:
            if r["time"] in ("-", "") or not expected_on(r["cadence"], s["start"].date()):
                continue
            try:
                hh, mm = r["time"].split(":")
                exp = datetime.datetime.combine(
                    s["start"].date(), datetime.time(int(hh), int(mm)), tzinfo=LOCAL_TZ
                )
            except Exception:
                continue
            gap = abs((s["start"] - exp).total_seconds())
            if gap <= 5400 and (best is None or gap < best_gap):
                best, best_gap = r, gap
        if best is not None:
            s["routine"], s["confidence"] = best["slug"], "추정"


def detect_missing(roster, handoffs, sessions, events, days, now):
    """활성 루틴 기준 결측 판정. 오전 이벤트가 전무한 날은 앱 미가동으로 묶는다."""
    flags = []
    handoff_idx = {(h["date"], h["slug"]) for h in handoffs}
    session_idx = {}
    for s in sessions.values():
        if s["routine"]:
            session_idx.setdefault((s["start"].date(), s["routine"]), []).append(s)
    morning_events = set()
    for ev in events:
        t = ev["_ts"]
        if 5 <= t.hour < 12:
            morning_events.add(t.date())
    for day in days:
        if day < SYSTEM_START or day > now.date():
            continue
        app_silent = day not in morning_events
        day_missing = []
        for r in roster:
            if r["handoff"] != "활성" or not expected_on(r["cadence"], day):
                continue
            try:
                hh, mm = r["time"].split(":")
                exp = datetime.datetime.combine(
                    day, datetime.time(int(hh), int(mm)), tzinfo=LOCAL_TZ
                )
            except Exception:
                continue
            if now < exp + datetime.timedelta(hours=2):
                continue  # 아직 유예 시간
            if (day, r["slug"]) in handoff_idx:
                continue
            if r["cadence"] == "every2days" and (
                day - datetime.timedelta(days=1), r["slug"]) in handoff_idx:
                continue  # 격일은 간격 기반: 어제 실행이면 오늘 부재는 정상
            if (day, r["slug"]) in session_idx:
                flags.append(
                    {"date": day, "slug": r["slug"], "kind": "noreport",
                     "text": "%s %s 실행 흔적은 있으나 핸드오프 보고 없음" % (day.isoformat(), r["slug"])}
                )
                continue
            day_missing.append(r["slug"])
        if day_missing:
            if app_silent:
                flags.append(
                    {"date": day, "slug": None, "kind": "missing_app",
                     "text": "%s 오전 이벤트 없음, 결측 %d건은 앱 미가동 추정 (%s)"
                     % (day.isoformat(), len(day_missing), ", ".join(day_missing))}
                )
            else:
                for slug in day_missing:
                    flags.append(
                        {"date": day, "slug": slug, "kind": "missing",
                         "text": "%s %s 결측 (앱은 실행 중이었음)" % (day.isoformat(), slug)}
                    )
    return flags


def load_hermes(start_dt):
    """~/.hermes/state.db 를 읽기 전용으로 조회한다. 실패 시 None."""
    if not os.path.exists(HERMES_DB):
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % HERMES_DB, uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT id, source, title, display_name, model, started_at, ended_at,"
            " message_count, tool_call_count, input_tokens, output_tokens"
            " FROM sessions WHERE started_at >= ? ORDER BY started_at",
            (start_dt.timestamp(),),
        )
        sessions = []
        for row in cur.fetchall():
            st = datetime.datetime.fromtimestamp(row["started_at"], tz=LOCAL_TZ)
            en = (
                datetime.datetime.fromtimestamp(row["ended_at"], tz=LOCAL_TZ)
                if row["ended_at"]
                else st
            )
            sessions.append(
                {
                    "start": st,
                    "end": en,
                    "title": row["title"] or row["display_name"] or row["source"] or "hermes",
                    "model": row["model"] or "",
                    "messages": row["message_count"] or 0,
                    "tools": row["tool_call_count"] or 0,
                    "tokens": (row["input_tokens"] or 0) + (row["output_tokens"] or 0),
                }
            )
        con.close()
        return {
            "sessions": sessions,
            "messages": sum(s["messages"] for s in sessions),
            "tokens": sum(s["tokens"] for s in sessions),
        }
    except Exception:
        return None


# ---------------------------------------------------------------- SVG 헬퍼

PLOT_LEFT = 215
PLOT_W = 1060
LANE_H = 30
BLOCK_H = 18


def x_of(dt):
    frac = (dt.hour * 3600 + dt.minute * 60 + dt.second) / 86400.0
    return PLOT_LEFT + frac * PLOT_W


def timeline_day_svg(day, lanes, blocks):
    """lanes: [(lane_id, label)], blocks: {lane_id: [ {start,end,label,cls,tip} ]}"""
    height = 34 + len(lanes) * LANE_H + 26
    parts = []
    parts.append(
        '<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s 타임라인">'
        % (PLOT_LEFT + PLOT_W + 20, height, day.isoformat())
    )
    for h in range(0, 25, 2):
        x = PLOT_LEFT + h / 24.0 * PLOT_W
        parts.append(
            '<line x1="%.1f" y1="26" x2="%.1f" y2="%d" class="grid"/>' % (x, x, height - 24)
        )
        parts.append(
            '<text x="%.1f" y="%d" class="axis" text-anchor="middle">%02d시</text>'
            % (x, height - 8, h)
        )
    y = 34
    for i, (lane_id, label) in enumerate(lanes):
        if i % 2 == 0:
            parts.append(
                '<rect x="0" y="%d" width="%d" height="%d" class="lane-stripe"/>'
                % (y - 4, PLOT_LEFT + PLOT_W + 20, LANE_H)
            )
        parts.append(
            '<text x="%d" y="%d" class="lane-label" text-anchor="end">%s</text>'
            % (PLOT_LEFT - 10, y + 13, esc(label))
        )
        for b in blocks.get(lane_id, []):
            x1, x2 = x_of(b["start"]), x_of(b["end"])
            w = max(x2 - x1, 7.0)
            parts.append(
                '<g><rect x="%.1f" y="%d" width="%.1f" height="%d" rx="4" class="blk %s"/>'
                '<title>%s</title></g>' % (x1, y, w, BLOCK_H, b["cls"], esc(b["tip"]))
            )
            if w > 64 and b.get("label"):
                parts.append(
                    '<text x="%.1f" y="%d" class="blk-label">%s</text>'
                    % (x1 + 5, y + 13, esc(b["label"][:14]))
                )
        y += LANE_H
    parts.append("</svg>")
    return "".join(parts)


def bipartite_svg(left, right, edges, planned, title_left, title_right):
    """edges: {(l, r): count}, planned: set((l, r)). 노드는 고정 순서."""
    if not left or not right:
        return '<p class="empty">아직 데이터가 없습니다. 핸드오프가 쌓이면 채워집니다.</p>'
    row_h = 36
    height = 46 + max(len(left), len(right)) * row_h
    lx, rx, node_w = 12, 700, 250
    parts = ['<svg viewBox="0 0 970 %d" width="100%%" role="img">' % height]
    parts.append('<text x="%d" y="18" class="col-title">%s</text>' % (lx, esc(title_left)))
    parts.append('<text x="%d" y="18" class="col-title">%s</text>' % (rx, esc(title_right)))
    ly = {n: 34 + i * row_h for i, n in enumerate(left)}
    ry = {n: 34 + i * row_h for i, n in enumerate(right)}
    for (a, b) in sorted(set(list(edges.keys()) + list(planned))):
        if a not in ly or b not in ry:
            continue
        y1, y2 = ly[a] + 12, ry[b] + 12
        cnt = edges.get((a, b), 0)
        cls = "edge-actual" if cnt else "edge-planned"
        width = min(1.5 + cnt * 1.2, 6.0) if cnt else 1.2
        parts.append(
            '<path d="M %d %d C %d %d, %d %d, %d %d" class="%s" style="stroke-width:%.1f"/>'
            % (lx + node_w, y1, lx + node_w + 150, y1, rx - 150, y2, rx, y2, cls, width)
        )
        if cnt:
            parts.append(
                '<text x="%d" y="%d" class="edge-count" text-anchor="middle">%d</text>'
                % ((lx + node_w + rx) // 2, (y1 + y2) // 2 - 4, cnt)
            )
    for n, yy in ly.items():
        parts.append(
            '<rect x="%d" y="%d" width="%d" height="24" rx="6" class="node"/>'
            '<text x="%d" y="%d" class="node-label">%s</text>'
            % (lx, yy, node_w, lx + 10, yy + 16, esc(n))
        )
    for n, yy in ry.items():
        parts.append(
            '<rect x="%d" y="%d" width="%d" height="24" rx="6" class="node"/>'
            '<text x="%d" y="%d" class="node-label">%s</text>'
            % (rx, yy, node_w, rx + 10, yy + 16, esc(n))
        )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- HTML

CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b; --serious: #ec835a;
  --conv: #2a78d6; --hermes: #4a3aa7; --noreport: #898781;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --conv: #3987e5; --hermes: #9085e9;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
  --muted: #898781; --grid: #2c2c2a; --baseline: #383835;
  --border: rgba(255,255,255,0.10);
  --conv: #3987e5; --hermes: #9085e9;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 14px; }
main { max-width: 1340px; margin: 0 auto; padding: 24px 20px 60px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 28px 0 10px; }
.sub { color: var(--ink-2); font-size: 12.5px; margin-bottom: 18px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.stat .v { font-size: 22px; font-weight: 650; }
.stat .k { color: var(--ink-2); font-size: 12px; margin-top: 2px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px; overflow-x: auto; }
.flags { list-style: none; margin: 0; padding: 0; }
.flags li { padding: 7px 10px; border-left: 3px solid var(--serious); background: var(--surface);
  border-radius: 6px; margin-bottom: 6px; font-size: 13px; }
.flags li.noreport { border-left-color: var(--muted); }
.flags li.ok-empty { border-left-color: var(--good); }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.tabs button { border: 1px solid var(--border); background: var(--surface); color: var(--ink-2);
  border-radius: 8px; padding: 5px 11px; cursor: pointer; font-size: 12.5px; }
.tabs button[aria-selected="true"] { color: var(--ink); font-weight: 650; border-color: var(--ink-2); }
.grid { stroke: var(--grid); stroke-width: 1; }
.axis { fill: var(--muted); font-size: 10.5px; }
.lane-label { fill: var(--ink-2); font-size: 11.5px; }
.lane-stripe { fill: var(--grid); opacity: 0.25; }
.blk { stroke: var(--surface); stroke-width: 2; }
.blk.ok { fill: var(--good); } .blk.partial { fill: var(--warning); }
.blk.fail { fill: var(--critical); } .blk.noreport { fill: var(--noreport); }
.blk.conv { fill: var(--conv); } .blk.hermes { fill: var(--hermes); }
.blk-label { fill: #fff; font-size: 10px; pointer-events: none; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--ink-2); margin-top: 8px; }
.legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 5px; vertical-align: -1px; background: var(--c); }
.run-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
.run { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.run h3 { margin: 0 0 2px; font-size: 13.5px; }
.run .when { color: var(--muted); font-size: 11.5px; }
.badge { display: inline-block; font-size: 11px; border-radius: 6px; padding: 2px 8px; margin-left: 6px;
  color: #fff; vertical-align: 1px; }
.badge.ok { background: var(--good); } .badge.partial { background: var(--warning); color: #2b2103; }
.badge.fail { background: var(--critical); } .badge.noreport { background: var(--noreport); }
.run ul { margin: 8px 0 0; padding-left: 18px; }
.run li { margin: 2px 0; font-size: 12.5px; }
.run .outs { margin-top: 8px; font-size: 12px; }
.run .outs a { color: var(--conv); word-break: break-all; }
.run .outs code { color: var(--ink-2); word-break: break-all; font-size: 11.5px; }
.need { margin-top: 8px; padding: 6px 9px; border-left: 3px solid var(--warning);
  background: color-mix(in srgb, var(--warning) 8%, var(--surface)); font-size: 12.5px; border-radius: 5px; }
.col-title { fill: var(--muted); font-size: 11px; }
.node { fill: var(--surface); stroke: var(--baseline); }
.node-label { fill: var(--ink); font-size: 11.5px; }
.edge-actual { fill: none; stroke: var(--conv); opacity: 0.85; }
.edge-planned { fill: none; stroke: var(--muted); stroke-dasharray: 4 4; opacity: 0.5; }
.edge-count { fill: var(--ink-2); font-size: 11px; font-weight: 650; }
.empty { color: var(--muted); font-size: 13px; }
footer { margin-top: 34px; color: var(--muted); font-size: 11.5px; line-height: 1.6; }
"""

JS = """
function showDay(id) {
  document.querySelectorAll('.day-pane').forEach(function (el) { el.style.display = 'none'; });
  document.querySelectorAll('.tabs button').forEach(function (b) { b.setAttribute('aria-selected', 'false'); });
  var pane = document.getElementById('pane-' + id);
  var btn = document.getElementById('tab-' + id);
  if (pane) pane.style.display = 'block';
  if (btn) btn.setAttribute('aria-selected', 'true');
}
"""


def output_link(o):
    o = str(o)
    if o.startswith("http"):
        label = o if len(o) <= 64 else o[:61] + "..."
        return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(o), esc(label))
    return "<code>%s</code>" % esc(o)


def classify_output(o):
    o = str(o).lower()
    if "notion" in o:
        return "Notion"
    if o.startswith("http"):
        return "웹"
    if "cloudstorage" in o or "drive" in o:
        return "Drive"
    if o.startswith("/") or o.startswith("~"):
        return "로컬 파일"
    return "기타"


def build(args):
    agentops = args.dir
    now = datetime.datetime.now(LOCAL_TZ)
    days = [now.date() - datetime.timedelta(days=i) for i in range(args.days)][::-1]
    start_dt = datetime.datetime.combine(days[0], datetime.time(0, 0), tzinfo=LOCAL_TZ)

    roster, planned_flows = load_roster(agentops)
    handoffs = load_handoffs(agentops, days)
    events, broken = load_events(agentops, start_dt)
    sessions = assemble_sessions(events)
    identify_routines(sessions, handoffs, roster, days)
    flags = detect_missing(roster, handoffs, sessions, events, days, now)
    hermes = load_hermes(start_dt)

    handoff_by_key = {(h["date"], h["slug"]): h for h in handoffs}
    schema_bad = [h for h in handoffs if not h["schema_ok"]]

    # ---- 실행 목록 (카드용): 핸드오프 + 보고 없는 식별 세션
    runs = []
    for h in handoffs:
        runs.append(h)
    for s in sessions.values():
        if s["routine"] and (s["start"].date(), s["routine"]) not in handoff_by_key:
            runs.append(
                {
                    "date": s["start"].date(),
                    "slug": s["routine"],
                    "run_at": s["start"],
                    "status": "noreport",
                    "outputs": [],
                    "key_findings": ["세션은 관측됐으나 핸드오프 보고가 없습니다 (%s)" % s["confidence"]],
                    "for": [],
                    "needs_user": [],
                }
            )
    runs.sort(key=lambda r: r["run_at"], reverse=True)

    # ---- 타임라인 데이터
    lane_defs = [(r["slug"], r["slug"]) for r in roster if r["cadence"] != "manual"]
    lane_defs += [("_conv", "대화 세션"), ("_hermes", "hermes (로컬 LLM)")]
    lane_ids = {lid for lid, _ in lane_defs}
    day_blocks = {d: {} for d in days}

    def add_block(day, lane, blk):
        if day in day_blocks:
            day_blocks[day].setdefault(lane, []).append(blk)

    session_by_id = sessions
    for h in handoffs:
        lane = "_hermes" if h["slug"].startswith("hermes") else h["slug"]
        if lane not in lane_ids and lane != "_hermes":
            lane = "_conv"
        sess = session_by_id.get(h.get("session"))
        b_start = sess["start"] if sess else h["run_at"]
        b_end = sess["end"] if sess else h["run_at"] + datetime.timedelta(minutes=10)
        tip = "%s | %s | %s" % (
            h["slug"], h["run_at"].strftime("%H:%M"), STATUS_LABEL.get(h["status"], h["status"]))
        if h["key_findings"]:
            tip += "\n" + " / ".join(h["key_findings"][:2])
        add_block(h["date"], lane, {
            "start": b_start, "end": b_end, "label": STATUS_LABEL.get(h["status"], ""),
            "cls": h["status"], "tip": tip})
    for s in sessions.values():
        d = s["start"].date()
        if s["routine"]:
            if (d, s["routine"]) in handoff_by_key:
                continue
            lane = s["routine"] if s["routine"] in lane_ids else "_conv"
            add_block(d, lane, {
                "start": s["start"], "end": s["end"], "label": "보고없음",
                "cls": "noreport",
                "tip": "%s (%s 식별) | %s~%s | 핸드오프 없음" % (
                    s["routine"], s["confidence"], s["start"].strftime("%H:%M"), s["end"].strftime("%H:%M"))})
        else:
            title = s["title"] or (os.path.basename(s["cwd"]) if s["cwd"] else "세션")
            add_block(d, "_conv", {
                "start": s["start"], "end": s["end"], "label": title,
                "cls": "conv",
                "tip": "대화 세션: %s | %s~%s | 턴 %d, 서브에이전트 %d" % (
                    title, s["start"].strftime("%H:%M"), s["end"].strftime("%H:%M"),
                    s["turns"], max(s["subagent_stops"], len(s["meta_types"])))})
    if hermes:
        for hs in hermes["sessions"]:
            add_block(hs["start"].date(), "_hermes", {
                "start": hs["start"], "end": hs["end"], "label": hs["title"],
                "cls": "hermes",
                "tip": "hermes: %s | %s | 메시지 %d, 도구 %d, 토큰 %d" % (
                    hs["title"], hs["start"].strftime("%H:%M"), hs["messages"], hs["tools"], hs["tokens"])})

    # ---- 상호작용 그래프 데이터
    actual_edges = {}
    for h in handoffs:
        for c in h["for"]:
            key = (h["slug"], c)
            actual_edges[key] = actual_edges.get(key, 0) + 1
    flow_nodes_l = sorted({a for a, _ in planned_flows} | {a for a, _ in actual_edges})
    flow_nodes_r = sorted({b for _, b in planned_flows} | {b for _, b in actual_edges})

    sub_edges = {}
    for s in sessions.values():
        proj = os.path.basename(s["cwd"]) if s["cwd"] else "세션"
        types = s["meta_types"] or s["task_types"]
        if not types and s["subagent_stops"]:
            types = ["agent"] * s["subagent_stops"]
        for t in types:
            key = (proj, t)
            sub_edges[key] = sub_edges.get(key, 0) + 1
    dest_edges = {}
    for h in handoffs:
        for o in h["outputs"]:
            key = (h["slug"], classify_output(o))
            dest_edges[key] = dest_edges.get(key, 0) + 1

    # ---- 통계
    n_ok = sum(1 for h in handoffs if h["status"] == "ok")
    n_partial = sum(1 for h in handoffs if h["status"] == "partial")
    n_fail = sum(1 for h in handoffs if h["status"] == "fail")
    # 결측은 두 종류를 함께 센다. 사용자 관점에서는 결과가 같지만(핸드오프·산출물 없음)
    # 원인과 대응이 달라 라벨은 분리한다:
    #   missing*  = 실행 흔적 자체가 없음 (스케줄러·앱 미가동)
    #   noreport  = 세션은 떴으나 보고 없이 중단 (크래시·타임아웃)
    # 같은 날 재실행으로 핸드오프가 생긴 경우는 detect_missing에서 이미 제외된다.
    n_missing = sum(1 for f in flags if f["kind"].startswith("missing"))
    n_noreport = sum(1 for f in flags if f["kind"] == "noreport")
    n_gap = n_missing + n_noreport
    conv_sessions = [s for s in sessions.values() if not s["routine"]]
    n_subagents = sum(max(s["subagent_stops"], len(s["meta_types"])) for s in sessions.values())
    needs_user_all = [(h["slug"], item) for h in handoffs for item in h["needs_user"]]

    # ---- HTML 조립
    p = []
    p.append("<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">")
    p.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    p.append("<title>AgentOps 관제</title><style>%s</style></head><body><main>" % CSS)
    p.append("<h1>AgentOps 관제 대시보드</h1>")
    p.append('<div class="sub">생성 %s | 창 %s ~ %s (%d일) | 원본: events·handoffs·AGENTS.md (이 파일은 파생 뷰, 직접 편집 금지)</div>'
             % (now.strftime("%Y-%m-%d %H:%M"), days[0].isoformat(), days[-1].isoformat(), args.days))

    p.append('<div class="cards">')
    for v, k in [
        ("%d" % len([h for h in handoffs if not h["slug"].startswith("hermes")]), "루틴 실행 보고"),
        ("%d / %d / %d" % (n_ok, n_partial, n_fail), "정상 / 부분 / 실패"),
        ("%d" % n_gap, "결측 (미실행 %d · 중단 %d)" % (n_missing, n_noreport)),
        ("%d" % len(conv_sessions), "대화 세션"),
        ("%d" % n_subagents, "서브에이전트 호출"),
        ("%d · %s" % (len(hermes["sessions"]), format(hermes["tokens"], ",")) if hermes else "연결 안 됨",
         "hermes 세션 · 토큰"),
    ]:
        p.append('<div class="stat"><div class="v">%s</div><div class="k">%s</div></div>' % (esc(v), esc(k)))
    p.append("</div>")

    p.append("<h2>결측 · 실패 · 확인 필요</h2>")
    items = []
    for f in flags:
        cls = "noreport" if f["kind"] == "noreport" else ""
        items.append('<li class="%s">%s</li>' % (cls, esc(f["text"])))
    for h in handoffs:
        if h["status"] == "fail":
            items.append("<li>%s %s 실패 보고: %s</li>"
                         % (h["date"].isoformat(), esc(h["slug"]),
                            esc(" / ".join(h["key_findings"][:1]) or "상세는 핸드오프 참조")))
    for slug, item in needs_user_all:
        items.append('<li class="noreport">[사용자 결정 필요] %s: %s</li>' % (esc(slug), esc(item)))
    for h in schema_bad:
        items.append('<li class="noreport">스키마 위반 핸드오프: %s</li>' % esc(os.path.basename(h["path"])))
    if not items:
        items.append('<li class="ok-empty">플래그 없음. 모든 활성 루틴이 정상 범위입니다.</li>')
    p.append('<ul class="flags">%s</ul>' % "".join(items))

    p.append("<h2>타임라인</h2><div class=\"panel\">")
    p.append('<div class="tabs">')
    for d in days:
        sel = "true" if d == now.date() else "false"
        p.append('<button id="tab-%s" aria-selected="%s" onclick="showDay(\'%s\')">%s</button>'
                 % (d.isoformat(), sel, d.isoformat(), d.strftime("%m/%d")))
    p.append("</div>")
    for d in days:
        style = "" if d == now.date() else ' style="display:none"'
        p.append('<div class="day-pane" id="pane-%s"%s>%s</div>'
                 % (d.isoformat(), style, timeline_day_svg(d, lane_defs, day_blocks[d])))
    p.append('<div class="legend">'
             '<span style="--c: var(--good)">정상(ok)</span>'
             '<span style="--c: var(--warning)">부분(partial)</span>'
             '<span style="--c: var(--critical)">실패(fail)</span>'
             '<span style="--c: var(--noreport)">보고 없음</span>'
             '<span style="--c: var(--conv)">대화 세션</span>'
             '<span style="--c: var(--hermes)">hermes</span></div>')
    p.append("</div>")

    p.append("<h2>실행 카드 (최신순)</h2>")
    if runs:
        p.append('<div class="run-grid">')
        for r in runs[:60]:
            p.append('<div class="run"><h3>%s<span class="badge %s">%s</span></h3>'
                     % (esc(r["slug"]), r["status"], STATUS_LABEL.get(r["status"], r["status"])))
            p.append('<div class="when">%s</div>' % r["run_at"].strftime("%Y-%m-%d %H:%M"))
            if r["key_findings"]:
                p.append("<ul>%s</ul>" % "".join("<li>%s</li>" % esc(k) for k in r["key_findings"][:3]))
            if r["outputs"]:
                p.append('<div class="outs">%s</div>'
                         % "<br>".join(output_link(o) for o in r["outputs"][:5]))
            for item in r["needs_user"]:
                p.append('<div class="need">사용자 결정 필요: %s</div>' % esc(item))
            p.append("</div>")
        p.append("</div>")
    else:
        p.append('<p class="empty">아직 실행 보고가 없습니다. 내일 아침 루틴부터 채워집니다.</p>')

    p.append("<h2>상호작용: 핸드오프 흐름 (점선 = 설계상 엣지, 실선 = 기간 내 실제 전달)</h2>")
    p.append('<div class="panel">%s</div>'
             % bipartite_svg(flow_nodes_l, flow_nodes_r, actual_edges, set(planned_flows), "발신 루틴", "수신 루틴"))
    p.append("<h2>상호작용: 서브에이전트 위임</h2>")
    p.append('<div class="panel">%s</div>'
             % bipartite_svg(sorted({a for a, _ in sub_edges}), sorted({b for _, b in sub_edges}),
                             sub_edges, set(), "세션(cwd)", "서브에이전트"))
    p.append("<h2>상호작용: 산출 목적지</h2>")
    p.append('<div class="panel">%s</div>'
             % bipartite_svg(sorted({a for a, _ in dest_edges}), sorted({b for _, b in dest_edges}),
                             dest_edges, set(), "루틴", "목적지"))

    p.append("<footer>관측 시작일 %s (이전 이력은 백필하지 않음) | 이벤트 %d건 로드, 깨진 줄 %d | "
             "결측 판정은 AGENTS.md 로스터의 handoff 활성 항목만 대상 | "
             "생성기: AgentOps/bin/build_dashboard.py</footer>"
             % (SYSTEM_START.isoformat(), len(events), broken))
    p.append("<script>%s</script></main></body></html>" % JS)

    out_path = args.out or os.path.join(agentops, "dashboard", "dashboard.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("".join(p))

    # ---- stdout 요약 (오케스트레이터가 인용)
    print("AgentOps %s~%s: 루틴 보고 %d건(ok %d, partial %d, fail %d), 결측 %d(미실행 %d·중단 %d), 대화 세션 %d, 서브에이전트 %d"
          % (days[0].isoformat(), days[-1].isoformat(),
             len(handoffs), n_ok, n_partial, n_fail,
             n_gap, n_missing, n_noreport, len(conv_sessions), n_subagents))
    if hermes:
        print("hermes: 세션 %d, 메시지 %d, 토큰 %s"
              % (len(hermes["sessions"]), hermes["messages"], format(hermes["tokens"], ",")))
    for f in flags[:5]:
        print("플래그: " + f["text"])
    for slug, item in needs_user_all[:5]:
        print("사용자 결정 필요 [%s]: %s" % (slug, item))
    print("대시보드: " + out_path)


def main():
    ap = argparse.ArgumentParser(description="AgentOps 대시보드 빌더")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dir", default=AGENTOPS_DEFAULT, help="AgentOps 루트 (테스트용 오버라이드)")
    ap.add_argument("--out", default=None, help="출력 HTML 경로")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
