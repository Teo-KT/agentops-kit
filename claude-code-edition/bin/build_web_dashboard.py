#!/usr/bin/env python3
"""AgentOps 관제실 — 웹 대시보드(Claude Artifact용) 빌더.

입력(모두 읽기 전용):
  - AgentOps/AGENTS.md    §1 로스터 (slug, 주기, 기대시각, handoff)
  - AgentOps/PERSONAS.md  직원 명단 (slug → 이름·직함·성격)
  - AgentOps/handoffs/    최근 8일 핸드오프 frontmatter

출력:
  - AgentOps/dashboard/web/index.html  (Artifact-ready: doctype/head/body 없음)

Artifact 발행은 별도(모닝 브리핑 루틴이 Artifact 도구로 재배포).
stdlib 전용, python3.9 호환, 실패는 관대하게.
"""
import datetime
import json
import os
import re
import html
import subprocess

TICKTICK = os.environ.get("AGENTOPS_TICKTICK_DIR", "")  # 없으면 일정 탭은 캘린더 스냅샷만 사용

AGENTOPS = os.environ.get("AGENTOPS_HOME", os.path.expanduser("~/AgentOps"))
OUT = os.path.join(AGENTOPS, "dashboard", "web", "index.html")
CMD_PAGE_ID = os.environ.get("AGENTOPS_CMD_PAGE_ID", "")  # Notion 명령함 페이지 ID (선택, 없으면 명령 콘솔 비활성)
CMD_PAGE_URL = os.environ.get("AGENTOPS_CMD_PAGE_URL", "")
OPS_PAGE_URL = os.environ.get("AGENTOPS_OPS_PAGE_URL", "")

STATUS_META = {
    "ok":      ("정상",   "ok"),
    "partial": ("부분",   "warn"),
    "fail":    ("실패",   "fail"),
    "missing": ("결측",   "fail"),
    "wait":    ("대기",   "idle"),
    "off":     ("휴무",   "idle"),
}


def esc(s):
    return html.escape(str(s), quote=True)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def parse_roster():
    """AGENTS.md §1 표 → [{slug, freq, time, handoff}]"""
    txt = read(os.path.join(AGENTOPS, "AGENTS.md"))
    rows = []
    in_sec = False
    for line in txt.splitlines():
        if line.startswith("## 1."):
            in_sec = True
            continue
        if in_sec and line.startswith("## "):
            break
        if in_sec and line.startswith("|") and "---" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 5 and cells[0] not in ("슬러그",):
                rows.append({"slug": cells[0], "freq": cells[1],
                             "time": cells[2], "handoff": cells[4]})
    return rows


def parse_personas():
    """PERSONAS.md 직원 명단 표 → {slug: (이름, 직함, 성격)}

    파일에는 표가 둘이다(직원 명단·외부 인력). 표 머리행(슬러그/식별자)은 걸러내고,
    외부 인력(hermes)은 조직도의 전용 칸에서 그리므로 직원 명단에 넣지 않는다.
    """
    txt = read(os.path.join(AGENTOPS, "PERSONAS.md"))
    out = {}
    for line in txt.splitlines():
        if line.startswith("|") and "---" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 4 and cells[0] not in ("슬러그", "식별자", "hermes"):
                out[cells[0]] = (cells[1], cells[2], cells[3])
    return out


def parse_handoff(path):
    """frontmatter 7키 관대 파싱 → dict"""
    txt = read(path)
    m = re.match(r"^---\n(.*?)\n---", txt, re.S)
    if not m:
        return {}
    fm = m.group(1)
    d = {"outputs": [], "key_findings": [], "needs_user": []}
    cur = None
    for line in fm.splitlines():
        km = re.match(r"^(routine|run_at|status|outputs|key_findings|for|needs_user):\s*(.*)$", line)
        if km:
            key, val = km.group(1), km.group(2).strip()
            if key in ("outputs", "key_findings", "needs_user"):
                cur = key
                if val and val not in ("", "[]"):
                    d[key].append(val.strip("\"' "))
            else:
                cur = None
                d[key] = val.strip("\"' ")
        elif cur and re.match(r"^\s+-\s+", line):
            item = re.sub(r"^\s+-\s+", "", line).strip().strip("\"' ")
            if item:
                d[cur].append(item)
    return d


def load_handoffs(days=8):
    """최근 N일 핸드오프 → {date: {slug: dict}}"""
    out = {}
    today = datetime.date.today()
    for i in range(days):
        day = today - datetime.timedelta(days=i)
        folder = os.path.join(AGENTOPS, "handoffs", day.isoformat())
        if not os.path.isdir(folder):
            continue
        m = {}
        for fn in sorted(os.listdir(folder)):
            if fn.endswith(".md"):
                d = parse_handoff(os.path.join(folder, fn))
                slug = d.get("routine") or fn[:-3]
                m[slug] = d
        out[day.isoformat()] = m
    return out


def routine_today_status(r, handoffs, now):
    """로스터 행 → (status_key, latest_handoff|None, date_str)"""
    today = datetime.date.today()
    t_iso = today.isoformat()
    y_iso = (today - datetime.timedelta(days=1)).isoformat()
    slug, freq = r["slug"], r["freq"]
    h_today = handoffs.get(t_iso, {}).get(slug)
    h_yest = handoffs.get(y_iso, {}).get(slug)

    # 수집창(8일) 전체에 이력이 전무한 신규 루틴은 결측이 아니라 첫 실행 대기다
    if not any(slug in m for m in handoffs.values()):
        return "wait", None, t_iso

    def st(h):
        return h.get("status", "ok") if h else "missing"

    if freq.startswith("weekly"):
        wd_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        due_wd = wd_map.get(freq.split(":")[-1], 0)
        if today.weekday() == due_wd:
            if h_today:
                return st(h_today), h_today, t_iso
            return ("wait" if _before(r["time"], now) else "missing"), None, t_iso
        # 평일: 최근 실행분 표시
        for d in sorted(handoffs.keys(), reverse=True):
            if slug in handoffs[d]:
                return "off", handoffs[d][slug], d
        return "off", None, ""
    if freq == "every2days":
        if h_today:
            return st(h_today), h_today, t_iso
        if h_yest:
            return st(h_yest), h_yest, y_iso
        return "missing", None, t_iso
    # daily
    if h_today:
        return st(h_today), h_today, t_iso
    return ("wait" if _before(r["time"], now) else "missing"), None, t_iso


def _before(tstr, now):
    m = re.match(r"^(\d{1,2}):(\d{2})", tstr or "")
    if not m:
        return False
    return (now.hour, now.minute) < (int(m.group(1)), int(m.group(2)))


def history_dots(slug, handoffs, days=7):
    today = datetime.date.today()
    dots = []
    for i in range(days - 1, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        h = handoffs.get(d, {}).get(slug)
        if h:
            dots.append(h.get("status", "ok"))
        else:
            dots.append("none")
    return dots


def load_rewrites():
    """결정 요청 자연어 변환표 (원문 → 존댓말 요청문)."""
    try:
        with open(os.path.join(AGENTOPS, "dashboard", "needs_rewrite.json"), encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def load_decided():
    """처리 완료된 결정 대기 억제 목록 (decided_needs.json → 앞 48자 키 집합).

    결정을 처리한 세션(당직·아침 브리핑·메인)이 항목을 추가한다. 이 목록에 잡히는
    needs_user는 이미 결정·이행된 것이므로 화면에 다시 올리지 않는다(유령 대기 방지).
    """
    try:
        with open(os.path.join(AGENTOPS, "dashboard", "decided_needs.json"), encoding="utf-8") as f:
            data = json.load(f)
        return [it["key"][:48] for it in data.get("items", []) if it.get("key")]
    except (OSError, ValueError, KeyError):
        return []


def humanize(item, rewrites):
    """변환표 우선, 없으면 흔한 명사형 종결을 요청문으로 완화."""
    base = re.split(r"\s*[—–]\s*", item)[0].strip()
    if item in rewrites:
        return rewrites[item]
    if base in rewrites:
        return rewrites[base]
    t = item.rstrip(".")
    subs = [
        (r"승인 여부$", "승인해 주세요."),
        (r"진위 확인$", "맞는지 확인해 주세요."),
        (r"여부 확인.*$", "여부를 확인해 주세요."),
        (r"확인$", "확인해 주세요."),
        (r"결정$", "결정해 주세요."),
        (r"검토 후 발송$", "검토하시고 발송해 주세요."),
        (r"기입$", "기입 부탁드립니다."),
    ]
    for pat, rep in subs:
        t2 = re.sub(pat, rep, t)
        if t2 != t:
            return t2
    return item


def load_priorities():
    """'오늘 우선순위' 데이터 (today_priorities.json, 강단아가 매일 새벽 생성).

    파일이 없으면 None. date가 오늘이 아니어도 반환하되 stale로 표시(렌더 쪽 처리).
    """
    try:
        with open(os.path.join(AGENTOPS, "dashboard", "today_priorities.json"), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("sections"):
            return None
        return data
    except (OSError, ValueError):
        return None


def load_ticktick():
    """TickTick 스냅샷 갱신(브리지 CLI, 45초 상한, 실패 시 기존 스냅샷) 후 로드.

    읽기 전용. 개인 리스트 포함 전 리스트를 표시한다(2026-07-27 대표님 지시).
    쓰기는 여전히 lists.json의 봇 리스트 한정(§5-8).
    """
    cli = os.path.join(TICKTICK, "ticktick_cli.py")
    try:
        subprocess.run(["/usr/bin/python3", cli, "snapshot"],
                       capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        with open(os.path.join(TICKTICK, "snapshot.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _parse_due(s):
    """'2026-08-15T00:00:00.000+0000' → 로컬 date. 실패 시 None."""
    if not s:
        return None
    t = re.sub(r"\.\d+", "", s).replace("Z", "+0000")
    t = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", t)
    try:
        return datetime.datetime.fromisoformat(t).astimezone().date()
    except ValueError:
        return None


def build():
    now = datetime.datetime.now()
    today = datetime.date.today()
    wd = "월화수목금토일"[today.weekday()]
    roster = parse_roster()
    personas = parse_personas()
    handoffs = load_handoffs()
    rewrites = load_rewrites()
    decided = load_decided()

    active = [r for r in roster if r["handoff"] == "활성"]
    cards = []
    counts = {"ok": 0, "partial": 0, "fail": 0, "missing": 0}
    needs = []   # dict: name, title, slug, item, findings, date
    links = []   # (label, url)
    seen_needs = set()

    for r in active:
        skey, h, dstr = routine_today_status(r, handoffs, now)
        if skey in counts:
            counts[skey] += 1
        name, title, trait = personas.get(r["slug"], (r["slug"], "", ""))
        findings = h["key_findings"] if h else []
        if h:
            for nu in h.get("needs_user", []):
                # 이미 결정·이행된 항목은 다시 올리지 않는다 (decided_needs.json)
                if any(nu.strip().startswith(k) for k in decided):
                    continue
                # 승계·전달로 중복된 항목은 첫 보고자만 남긴다
                base = re.split(r"\s*[—–]\s*", nu)[0].strip()[:60]
                if base in seen_needs:
                    continue
                seen_needs.add(base)
                needs.append({"name": name, "title": title, "slug": r["slug"],
                              "item": nu, "findings": findings, "date": dstr})
            if dstr == today.isoformat():
                for o in h.get("outputs", []):
                    um = re.search(r"https?://\S+", o)
                    if um:
                        label = re.sub(r"https?://\S+", "", o).strip(" :\"()->")
                        links.append((label or r["slug"], um.group(0).rstrip('")')))
        label, cls = STATUS_META.get(skey, (skey, "idle"))
        dots = history_dots(r["slug"], handoffs)
        cards.append({
            "slug": r["slug"], "name": name, "title": title, "trait": trait,
            "freq": r["freq"], "time": r["time"], "status": skey,
            "label": label, "cls": cls, "findings": findings,
            "date": dstr, "dots": dots,
        })

    # ---- HTML ----
    def dot_html(dots):
        cls = {"ok": "d-ok", "partial": "d-warn", "fail": "d-fail", "none": "d-none"}
        return "".join('<i class="dot %s"></i>' % cls.get(x, "d-none") for x in dots)

    card_html = []
    for c in cards:
        opts_slug = esc(c["slug"])
        if c["findings"]:
            first = c["findings"][0]
            rest = "".join(f"<li>{esc(f)}</li>" for f in c["findings"])
            date_note = "" if c["date"] == today.isoformat() else f' <span class="mut">({esc(c["date"])} 보고)</span>'
            body = f'''<details class="report">
    <summary>{esc(first)}{date_note}</summary>
    <div class="report-full">
      <p class="rf-label">{esc(c["name"])} 님의 보고 전체</p>
      <ul class="flist">{rest}</ul>
      <p class="trait">"{esc(c["trait"])}"</p>
    </div>
  </details>'''
        else:
            body = '<p class="finding mut">오늘 보고 없음</p>'
        card_html.append(f'''
<article class="card s-{c["cls"]}">
  <header>
    <div class="who">
      <b>{esc(c["name"])}</b><span class="role">{esc(c["title"])}</span>
    </div>
    <span class="pill p-{c["cls"]}">{esc(c["label"])}</span>
  </header>
  <p class="slug">{opts_slug} · {esc(c["freq"])} {esc(c["time"])}</p>
  {body}
  <footer>
    <span class="dots" title="최근 7일">{dot_html(c["dots"])}</span>
    <button class="rerun" data-slug="{opts_slug}" data-name="{esc(c["name"])}">재실행 요청</button>
  </footer>
</article>''')

    need_html_parts = []
    for n in needs:
        ctx = "".join(f"<li>{esc(f)}</li>" for f in n["findings"])
        ctx_block = (f'<details class="need-ctx"><summary>배경 보기 ({esc(n["name"])} 님의 당일 보고)</summary>'
                     f'<ul class="flist">{ctx}</ul></details>') if ctx else ""
        spoken = humanize(n["item"], rewrites)
        item_short = esc(n["item"][:100])
        need_html_parts.append(f'''<li class="need">
  <div class="need-head">
    <span class="who-inline">{esc(n["name"])} ({esc(n["title"])})</span>
    <button class="reply" data-item="{item_short}" data-name="{esc(n["name"])}">결정 회신</button>
  </div>
  <p class="need-item">{esc(spoken)}</p>
  {ctx_block}
  <div class="reply-box" hidden>
    <div class="quick">
      <button type="button" class="qk">승인합니다</button>
      <button type="button" class="qk">권고안대로 진행하세요</button>
      <button type="button" class="qk">보류합니다</button>
      <button type="button" class="qk">진행하지 마세요</button>
    </div>
    <textarea class="rb-text" rows="2" placeholder="결정 내용을 적거나 위 버튼으로 채우세요"></textarea>
    <div class="rb-row">
      <button type="button" class="rb-send">이 결정 등록</button>
      <span class="rb-msg mut"></span>
    </div>
  </div>
</li>''')
    needs_html = "".join(need_html_parts) or '<li class="mut" style="padding:10px 0">대기 중인 결정이 없습니다.</li>'

    seen = set()
    links_dedup = []
    for label, url in links:
        if url not in seen:
            seen.add(url)
            links_dedup.append((label, url))
    links_html = "".join(
        f'<a class="lnk" href="{esc(u)}" target="_blank" rel="noopener">{esc(l) or "링크"}</a>'
        for l, u in links_dedup)

    total_line = f'정상 {counts["ok"]} · 부분 {counts["partial"]} · 실패 {counts["fail"]} · 결측 {counts["missing"]}'
    gen_at = now.strftime("%Y-%m-%d %H:%M")

    # ---- 오늘 우선순위 (today_priorities.json, 강단아 생성) ----
    prio = load_priorities()
    prio_count = 0
    if prio:
        prio_parts = []
        if prio.get("date") != today.isoformat():
            prio_parts.append(f'<p class="prio-stale">이 우선순위는 {esc(prio.get("date", "?"))} 기준입니다. '
                              '오늘 새벽 브리핑이 아직 갱신하지 않았습니다.</p>')
        if prio.get("headline"):
            prio_parts.append(f'<p class="prio-head">{esc(prio["headline"])}</p>')
        if prio.get("overload"):
            prio_parts.append(f'<p class="prio-warn">{esc(prio["overload"])}</p>')
        for sec in prio.get("sections", []):
            items = sec.get("items") or []
            if not items:
                continue
            rows = []
            for it in items:
                if not it.get("label"):
                    continue
                prio_count += 1
                chips = ""
                if it.get("due"):
                    chips += f' <span class="prio-chip due">{esc(it["due"])}</span>'
                if it.get("eta"):
                    chips += f' <span class="prio-chip">{esc(it["eta"])}</span>'
                why = f'<p class="prio-why">{esc(it["why"])}</p>' if it.get("why") else ""
                mark = ' class="prio-urgent"' if it.get("urgent") else ""
                rows.append(f'<li{mark}><b>{esc(it["label"])}</b>{chips}{why}</li>')
            prio_parts.append(f'<h3>{esc(sec.get("title", ""))}</h3><ol>{"".join(rows)}</ol>')
        prio_parts.append(f'<p class="prio-note mut">강단아가 새벽 브리핑 계산으로 매일 생성 · {esc(prio.get("generated_at", ""))}</p>')
        prio_html = '<div class="prio">' + "".join(prio_parts) + "</div>"
    else:
        prio_html = ('<div class="prio"><p class="mut">오늘 우선순위 데이터가 아직 없습니다. '
                     '새벽 브리핑(04:30)이 생성하며, 급하시면 메인 세션에 "오늘 우선순위 정리해줘"라고 요청하세요.</p></div>')

    # ---- 조직도 (PERSONAS.md 기반, 부서는 여기서 정의) ----
    ORG_GROUPS = [
        ("운영실", ["morning-ops-brief", "ops-command-poller"]),
        ("비서·일정실", ["inbox-triage", "deadline-watchdog"]),
        ("문헌·리서치센터", ["daily-paper-sensor", "arxiv-daily-summary-updater",
                     "literature-night-sensor", "ft50-weekly-sensor", "ft50-weekly-insight"]),
        ("지식인프라실", ["obsidian-vault-curator", "daily-embedding-update"]),
        ("연구기획실", ["weekly-research-tracker-sync", "biweekly-research-idea-generator"]),
        ("콘텐츠팀", ["brunch-pipeline-v2", "brunch-calling-series"]),
        ("연구지원실", ["grant-scout"]),
        ("강의개발팀", ["lecture-builder", "lecture-refresher"]),
        ("심사실", ["review-office", "review-archivist", "style-inspector"]),
        ("원고제작소", ["manuscript-studio", "style-inspector"]),
    ]
    ONDEMAND = {"review-office", "manuscript-studio", "style-inspector"}
    active_slugs = {r["slug"] for r in active}
    grouped = {s for _, slugs in ORG_GROUPS for s in slugs}
    org_parts = []
    for gname, slugs in ORG_GROUPS:
        members = []
        for s in slugs:
            if s not in personas:
                continue
            nm, ttl, _ = personas[s]
            if s in ONDEMAND:
                off = ' <span class="org-call">호출형</span>'
            elif s in active_slugs or s == "ops-command-poller":
                off = ""
            else:
                off = ' <span class="org-off">대기</span>'
            members.append(f'<li><b>{esc(nm)}</b> <span class="mut">{esc(ttl)}</span>{off}'
                           f'<span class="org-slug">{esc(s)}</span></li>')
        # 명부에 있는데 어느 그룹에도 없는 직원 방지용: 마지막 그룹 뒤에서 처리
        if members:
            org_parts.append(f'<div class="org-dept"><h3>{esc(gname)}</h3><ul>{"".join(members)}</ul></div>')
    leftovers = [s for s in personas if s not in grouped]
    if leftovers:
        members = []
        for s in leftovers:
            nm, ttl, _ = personas[s]
            members.append(f'<li><b>{esc(nm)}</b> <span class="mut">{esc(ttl)}</span>'
                           f'<span class="org-slug">{esc(s)}</span></li>')
        org_parts.append(f'<div class="org-dept"><h3>기타</h3><ul>{"".join(members)}</ul></div>')
    org_parts.append('<div class="org-dept org-ext"><h3>외부 인력</h3><ul>'
                     '<li><b>헤르메스</b> <span class="mut">야간 연구보조 (로컬 LLM 계약직)</span>'
                     '<span class="org-slug">hermes</span></li></ul></div>')
    org_html = ('<div class="org"><div class="org-head"><b>대표님</b>'
                '<span class="mut"> 대표 · 산업조직심리학자</span></div>'
                '<div class="org-grid">' + "".join(org_parts) + "</div></div>")

    # ---- 일정 (TickTick 전체 스냅샷, 읽기 전용) ----
    tt = load_ticktick()
    sched_secs = {"지연": [], "오늘": [], "이번 주": [], "이후 30일": [], "그 이후": [], "기한 미정": []}
    tt_fetched = ""
    if tt:
        tt_fetched = (tt.get("fetched_at") or "")[:16].replace("T", " ")
        for p in tt.get("projects", []):
            for tk in p.get("tasks", []):
                due = _parse_due(tk.get("dueDate"))
                if due is None:
                    sec = "기한 미정"
                elif due < today:
                    sec = "지연"
                elif due == today:
                    sec = "오늘"
                elif due <= today + datetime.timedelta(days=7):
                    sec = "이번 주"
                elif due <= today + datetime.timedelta(days=30):
                    sec = "이후 30일"
                else:
                    sec = "그 이후"
                sched_secs[sec].append((due, p.get("name", ""), p.get("color"), tk))
    # Google Calendar 병합 (gcal_snapshot.json, 읽기 전용. 갱신은 아침 브리핑 7.5단계)
    GCAL_COLORS = {"내 캘린더": "#4285F4", "대한민국의 휴일": "#E67C73"}
    gcal = None
    try:
        with open(os.path.join(TICKTICK, "gcal_snapshot.json"), encoding="utf-8") as f:
            gcal = json.load(f)
    except (OSError, ValueError):
        pass
    gcal_events = []
    if gcal:
        for ev in gcal.get("events", []):
            try:
                due = datetime.date.fromisoformat(ev.get("d", ""))
            except ValueError:
                continue
            cal = ev.get("cal", "캘린더")
            gcal_events.append((due, ev.get("t"), ev.get("title", ""), cal,
                                GCAL_COLORS.get(cal, "#33B679")))
            if due < today:
                continue  # 지난 캘린더 일정은 목록에 안 올린다(과제와 달리 밀린 일이 아님)
            if due == today:
                sec = "오늘"
            elif due <= today + datetime.timedelta(days=7):
                sec = "이번 주"
            elif due <= today + datetime.timedelta(days=30):
                sec = "이후 30일"
            else:
                sec = "그 이후"
            title = (f"{ev['t']} " if ev.get("t") else "") + ev.get("title", "")
            sched_secs[sec].append((due, f"📅 {cal}", GCAL_COLORS.get(cal, "#33B679"),
                                    {"title": title, "priority": 0}))
    sched_parts = []
    urgent_count = len(sched_secs["지연"]) + len(sched_secs["오늘"])
    for sec in ("지연", "오늘", "이번 주", "이후 30일", "그 이후", "기한 미정"):
        items = sorted(sched_secs[sec], key=lambda x: (x[0] or datetime.date.max))
        if not items:
            continue
        rows = []
        for due, pname, pcolor, tk in items:
            dot = f'<i class="sdot" style="background:{esc(pcolor)}"></i>' if pcolor and pcolor != "transparent" else '<i class="sdot" style="background:var(--idle)"></i>'
            pr = tk.get("priority", 0)
            pr_html = ' <span class="sched-pr">중요</span>' if pr == 5 else ""
            fmt = "%m-%d" if due and due.year == today.year else "%Y-%m-%d"
            dstr = (due.strftime(fmt) + f' ({"월화수목금토일"[due.weekday()]})') if due else ""
            dday = ""
            if due and sec == "지연":
                dday = f' <span class="sched-over">+{(today - due).days}일 경과</span>'
            rows.append(f'<li>{dot}<span class="sched-date">{esc(dstr)}</span> {esc(tk.get("title", ""))}{pr_html}{dday}'
                        f'<span class="sched-list">{esc(pname)}</span></li>')
        cls = " sched-urgent" if sec in ("지연", "오늘") else ""
        sched_parts.append(f'<div class="sched-sec{cls}"><h3>{sec} <span class="mut">{len(items)}</span></h3>'
                           f'<ul>{"".join(rows)}</ul></div>')
    if tt is None:
        sched_html = '<p class="mut">TickTick 스냅샷을 읽지 못했습니다. 브리지 연결(토큰)을 확인해 주세요.</p>'
    elif not sched_parts:
        sched_html = '<p class="mut">잡힌 일정이 없습니다.</p>'
    else:
        sched_html = "".join(sched_parts)
    gcal_note = ""
    sched_note = (f'TickTick 스냅샷 {esc(tt_fetched)} 기준(재빌드마다 자동 갱신, 인박스·개인 리스트 포함) · '
                  f'Google Calendar는 새벽 브리핑(04:30)이 매일 갱신{gcal_note} · 쓰기는 🧭 봇 리스트만')

    # 캘린더 뷰(일/주/월)용 데이터 임베드
    sched_data = []
    if tt:
        for p in tt.get("projects", []):
            pcolor = p.get("color") if p.get("color") and p.get("color") != "transparent" else "#8A948F"
            for tk in p.get("tasks", []):
                raw = tk.get("dueDate")
                if not raw:
                    continue
                t_norm = re.sub(r"\.\d+", "", raw).replace("Z", "+0000")
                t_norm = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", t_norm)
                try:
                    ldt = datetime.datetime.fromisoformat(t_norm).astimezone()
                except ValueError:
                    continue
                sched_data.append({"d": ldt.date().isoformat(),
                                   "t": None if tk.get("isAllDay", True) else ldt.strftime("%H:%M"),
                                   "title": tk.get("title", ""), "list": p.get("name", ""),
                                   "color": pcolor, "pr": tk.get("priority", 0)})
    for due, t_str, title, cal, color in gcal_events:
        sched_data.append({"d": due.isoformat(), "t": t_str, "title": title,
                           "list": f"📅 {cal}", "color": color, "pr": 0})
    sched_json = json.dumps(sched_data, ensure_ascii=False).replace("</", "<\\/")

    page = f'''<title>AgentOps 관제실</title>
<style>
:root {{
  --bg:#F5F4EF; --surface:#FFFFFF; --ink:#22302E; --mut:#66736F;
  --accent:#2F6D5F; --ok:#2E7D4F; --warn:#A8712B; --fail:#B14336;
  --idle:#8A948F; --line:#E3E1D8; --chip:#ECEAE1;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#111917; --surface:#18211F; --ink:#E4E8E2; --mut:#8FA098;
  --accent:#6FBFA6; --ok:#66B98A; --warn:#D9A45B; --fail:#E08072;
  --idle:#5E6B66; --line:#263230; --chip:#202B28;
}} }}
:root[data-theme="dark"] {{
  --bg:#111917; --surface:#18211F; --ink:#E4E8E2; --mut:#8FA098;
  --accent:#6FBFA6; --ok:#66B98A; --warn:#D9A45B; --fail:#E08072;
  --idle:#5E6B66; --line:#263230; --chip:#202B28;
}}
:root[data-theme="light"] {{
  --bg:#F5F4EF; --surface:#FFFFFF; --ink:#22302E; --mut:#66736F;
  --accent:#2F6D5F; --ok:#2E7D4F; --warn:#A8712B; --fail:#B14336;
  --idle:#8A948F; --line:#E3E1D8; --chip:#ECEAE1;
}}
* {{ box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--ink); margin:0;
  font-family:-apple-system,"Apple SD Gothic Neo",Pretendard,"Noto Sans KR",sans-serif;
  line-height:1.55; -webkit-font-smoothing:antialiased; }}
.wrap {{ max-width:1320px; margin:0 auto; padding:20px clamp(16px,2.5vw,36px) 56px; }}
.masthead {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:8px 14px;
  border-bottom:2px solid var(--ink); padding-bottom:14px; margin-bottom:20px; }}
.masthead h1 {{ font-size:1.35rem; margin:0; letter-spacing:-.01em; }}
.masthead .date {{ color:var(--mut); font-variant-numeric:tabular-nums; }}
.masthead .tally {{ margin-left:auto; font-size:.86rem; color:var(--mut);
  font-variant-numeric:tabular-nums; }}
h2 {{ font-size:.8rem; text-transform:uppercase; letter-spacing:.09em;
  color:var(--mut); margin:26px 0 10px; font-weight:600; }}
.needs {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:6px 16px; margin:0; }}
.needs li {{ margin:9px 0; font-size:.92rem; }}
.needs li.need {{ list-style:none; border-bottom:1px solid var(--line); padding:11px 0; margin:0; }}
.needs li.need:last-child {{ border-bottom:none; }}
.need-head {{ display:flex; align-items:center; justify-content:space-between; gap:10px; }}
.need-item {{ margin:5px 0 0; }}
.reply {{ font-size:.75rem; color:var(--accent); background:none; white-space:nowrap;
  border:1px solid var(--line); border-radius:7px; padding:3px 10px; cursor:pointer; }}
.reply:hover {{ border-color:var(--accent); }}
.reply.done, .rerun.done {{ background:color-mix(in srgb,var(--ok) 14%,transparent);
  color:var(--ok); border-color:var(--ok); }}
.need.decided .need-item {{ color:var(--mut); }}
.reply-box {{ margin-top:9px; background:var(--bg); border:1px solid var(--line);
  border-radius:8px; padding:10px; }}
.reply-box .quick {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }}
.qk {{ font-size:.76rem; background:var(--chip); border:none; border-radius:99px;
  padding:4px 11px; cursor:pointer; color:var(--ink); }}
.qk:hover {{ background:var(--accent); color:var(--bg); }}
.rb-text {{ width:100%; font:inherit; font-size:.88rem; background:var(--surface);
  color:var(--ink); border:1px solid var(--line); border-radius:7px; padding:8px;
  resize:vertical; }}
.rb-row {{ display:flex; gap:10px; align-items:center; margin-top:7px; }}
.rb-send {{ background:var(--accent); color:var(--bg); border:none; border-radius:7px;
  padding:6px 14px; font-size:.82rem; font-weight:600; cursor:pointer; }}
.rb-send:disabled {{ opacity:.5; cursor:default; }}
.rb-msg {{ font-size:.78rem; }}
.rb-msg.err {{ color:var(--fail); }} .rb-msg.good {{ color:var(--ok); }}
.org {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:16px; }}
.org-head {{ border-bottom:2px solid var(--accent); padding-bottom:10px;
  margin-bottom:14px; font-size:1.02rem; }}
.org-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr));
  gap:12px; }}
.org-dept {{ background:var(--bg); border-radius:8px; padding:11px 13px;
  border-left:3px solid var(--accent); }}
.org-dept h3 {{ margin:0 0 7px; font-size:.8rem; letter-spacing:.05em;
  color:var(--accent); }}
.org-dept ul {{ margin:0; padding:0; list-style:none; }}
.org-dept li {{ margin:5px 0; font-size:.88rem; }}
.org-slug {{ display:block; font-size:.68rem; color:var(--mut);
  font-family:ui-monospace,"SF Mono",Menlo,monospace; }}
.org-off {{ font-size:.7rem; color:var(--warn); border:1px solid var(--warn);
  border-radius:99px; padding:1px 7px; margin-left:4px; }}
.org-call {{ font-size:.7rem; color:var(--accent); border:1px solid var(--accent);
  border-radius:99px; padding:1px 7px; margin-left:4px; }}
.org-ext {{ border-left-color:var(--idle); }}
.org-ext h3 {{ color:var(--mut); }}
.tabs {{ display:flex; gap:4px; margin:26px 0 4px; border-bottom:2px solid var(--line);
  overflow-x:auto; }}
.tab {{ font-size:.88rem; font-weight:600; color:var(--mut); background:none; border:none;
  padding:9px 14px 7px; cursor:pointer; border-bottom:2px solid transparent;
  margin-bottom:-2px; white-space:nowrap; }}
.tab:hover {{ color:var(--ink); }}
.tab[aria-selected="true"] {{ color:var(--accent); border-bottom-color:var(--accent); }}
.tab-badge {{ font-size:.7rem; font-weight:600; background:var(--accent); color:var(--bg);
  border-radius:99px; padding:1px 7px; margin-left:3px; vertical-align:1px; }}
.panel > h2 {{ margin-top:16px; }}
.sched {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px; }}
.sched-sec h3 {{ font-size:.78rem; letter-spacing:.06em; color:var(--mut);
  margin:14px 0 6px; }}
.sched-sec:first-child h3 {{ margin-top:0; }}
.sched-sec.sched-urgent h3 {{ color:var(--fail); }}
.sched-sec ul {{ margin:0; padding:0; list-style:none; }}
.sched-sec li {{ font-size:.9rem; margin:6px 0; display:flex; align-items:baseline;
  gap:7px; flex-wrap:wrap; }}
.sdot {{ width:8px; height:8px; border-radius:50%; display:inline-block; flex:none;
  align-self:center; }}
.sched-date {{ font-variant-numeric:tabular-nums; color:var(--mut); font-size:.82rem;
  flex:none; }}
.sched-pr {{ font-size:.68rem; color:var(--fail); border:1px solid var(--fail);
  border-radius:99px; padding:0 6px; }}
.sched-over {{ font-size:.75rem; color:var(--fail); }}
.sched-list {{ margin-left:auto; font-size:.74rem; color:var(--mut); }}
.sched-note {{ font-size:.74rem; margin:12px 0 0; }}
.sv-bar {{ display:flex; justify-content:space-between; align-items:center; gap:10px;
  flex-wrap:wrap; margin-bottom:10px; }}
.sv-modes {{ display:flex; gap:5px; }}
.svm {{ font-size:.8rem; background:var(--chip); border:none; border-radius:99px;
  padding:5px 13px; cursor:pointer; color:var(--ink); }}
.svm[aria-pressed="true"] {{ background:var(--accent); color:var(--bg); }}
.sv-nav {{ display:flex; align-items:center; gap:6px; }}
.sv-nav button {{ font-size:.8rem; background:var(--surface); border:1px solid var(--line);
  border-radius:7px; padding:4px 10px; cursor:pointer; color:var(--ink); }}
.sv-nav button:hover {{ border-color:var(--accent); }}
#sv-title {{ font-size:.9rem; margin-left:6px; }}
#sv-cal {{ overflow-x:auto; }}
.cal-week {{ display:grid; grid-template-columns:repeat(7,minmax(150px,1fr)); gap:6px; }}
.cal-col {{ background:var(--bg); border:1px solid var(--line); border-radius:8px;
  padding:6px; min-height:96px; }}
.cal-col.cal-today, .cal-mcell.cal-today {{ border-color:var(--accent);
  border-width:2px; }}
.cal-head {{ font-size:.72rem; color:var(--mut); text-align:center; margin-bottom:5px; }}
.cal-item {{ font-size:.75rem; display:flex; gap:5px; align-items:baseline;
  margin:4px 0; line-height:1.35; }}
.cal-item .sdot {{ width:6px; height:6px; flex:none; align-self:center; }}
.cal-item.cal-past {{ opacity:.55; }}
.cal-time {{ color:var(--accent); font-variant-numeric:tabular-nums; font-size:.7rem;
  flex:none; }}
.cal-title {{ word-break:keep-all; overflow-wrap:break-word; }}
.cal-ilist {{ margin-left:auto; font-size:.68rem; color:var(--mut); flex:none; }}
.cal-month {{ display:grid; grid-template-columns:repeat(7,minmax(125px,1fr)); gap:4px; }}
.cal-dow {{ font-size:.7rem; color:var(--mut); text-align:center; padding:2px 0; }}
.cal-mcell {{ background:var(--bg); border:1px solid var(--line); border-radius:7px;
  padding:4px 5px; min-height:76px; font-size:.72rem; overflow:hidden; }}
.cal-mcell.cal-out {{ opacity:.4; }}
.cal-mnum {{ font-size:.72rem; color:var(--mut); margin-bottom:2px; }}
.cal-more {{ color:var(--mut); font-size:.68rem; }}
.day-allday {{ margin-bottom:10px; }}
.day-hour {{ display:flex; gap:12px; border-top:1px solid var(--line); padding:5px 0; }}
.day-hlabel {{ width:46px; color:var(--mut); font-size:.75rem; flex:none;
  text-align:right; font-variant-numeric:tabular-nums; }}
.day-empty {{ color:var(--mut); font-size:.8rem; padding:8px 0; }}
@media (max-width:640px) {{ .cal-week {{ grid-template-columns:1fr; }}
  .cal-col {{ min-height:0; }} }}
.prio {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:16px; }}
.prio-head {{ margin:0 0 4px; font-size:.95rem; font-weight:600; }}
.prio-warn {{ background:color-mix(in srgb,var(--warn) 13%,transparent); color:var(--warn);
  border-radius:8px; padding:9px 13px; font-size:.86rem; margin:10px 0 4px; }}
.prio-stale {{ background:color-mix(in srgb,var(--fail) 11%,transparent); color:var(--fail);
  border-radius:8px; padding:8px 13px; font-size:.84rem; margin:0 0 10px; }}
.prio h3 {{ font-size:.78rem; letter-spacing:.06em; color:var(--accent);
  margin:18px 0 6px; }}
.prio ol {{ margin:0; padding-left:24px; }}
.prio li {{ margin:10px 0; font-size:.92rem; }}
.prio li.prio-urgent::marker {{ color:var(--fail); font-weight:700; }}
.prio-why {{ margin:3px 0 0; color:var(--mut); font-size:.85rem; }}
.prio-chip {{ font-size:.7rem; border:1px solid var(--line); border-radius:99px;
  padding:1px 8px; margin-left:6px; color:var(--mut); white-space:nowrap;
  font-variant-numeric:tabular-nums; }}
.prio-chip.due {{ color:var(--fail); border-color:color-mix(in srgb,var(--fail) 55%,transparent); }}
.prio-note {{ font-size:.74rem; margin:16px 0 0; }}
details.need-ctx {{ margin-top:6px; }}
details.need-ctx summary, details.report summary {{ cursor:pointer; color:var(--mut);
  font-size:.8rem; }}
details.need-ctx summary::marker, details.report summary::marker {{ color:var(--accent); }}
.flist {{ margin:8px 0 4px; padding-left:18px; font-size:.84rem; }}
.flist li {{ margin:5px 0; }}
details.report {{ flex:1; }}
details.report summary {{ color:var(--ink); font-size:.86rem; line-height:1.5; }}
details.report[open] summary {{ color:var(--mut); }}
.report-full {{ background:var(--bg); border-radius:8px; padding:10px 12px; margin-top:8px; }}
.rf-label {{ font-size:.72rem; text-transform:none; letter-spacing:.02em;
  color:var(--accent); font-weight:600; margin:0 0 4px; }}
.trait {{ font-size:.78rem; color:var(--mut); font-style:italic; margin:8px 0 0; }}
.who-inline {{ font-weight:600; color:var(--accent); margin-right:4px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(255px,1fr)); gap:12px; }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:13px 14px 11px; display:flex; flex-direction:column; gap:6px;
  border-left-width:4px; }}
.card.s-ok {{ border-left-color:var(--ok); }}
.card.s-warn {{ border-left-color:var(--warn); }}
.card.s-fail {{ border-left-color:var(--fail); }}
.card.s-idle {{ border-left-color:var(--idle); }}
.card header {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
.who b {{ font-size:1rem; }}
.role {{ color:var(--mut); font-size:.8rem; margin-left:6px; }}
.pill {{ font-size:.72rem; font-weight:600; padding:2px 9px; border-radius:99px;
  white-space:nowrap; }}
.p-ok {{ background:color-mix(in srgb,var(--ok) 14%,transparent); color:var(--ok); }}
.p-warn {{ background:color-mix(in srgb,var(--warn) 16%,transparent); color:var(--warn); }}
.p-fail {{ background:color-mix(in srgb,var(--fail) 15%,transparent); color:var(--fail); }}
.p-idle {{ background:var(--chip); color:var(--mut); }}
.slug {{ font-size:.74rem; color:var(--mut); margin:0;
  font-family:ui-monospace,"SF Mono",Menlo,monospace; }}
.finding {{ font-size:.86rem; margin:0; flex:1; overflow-wrap:anywhere; }}
.mut {{ color:var(--mut); }}
.card footer {{ display:flex; align-items:center; justify-content:space-between; margin-top:2px; }}
.dots {{ display:inline-flex; gap:3px; }}
.dot {{ width:7px; height:7px; border-radius:50%; display:inline-block; }}
.d-ok {{ background:var(--ok); }} .d-warn {{ background:var(--warn); }}
.d-fail {{ background:var(--fail); }} .d-none {{ background:var(--line); }}
button {{ font:inherit; }}
.rerun {{ font-size:.75rem; color:var(--accent); background:none;
  border:1px solid var(--line); border-radius:7px; padding:3px 10px; cursor:pointer; }}
.rerun:hover {{ border-color:var(--accent); }}
.console {{ background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:16px; }}
.console .chips {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }}
.chip {{ font-size:.78rem; background:var(--chip); border:none; border-radius:99px;
  padding:5px 12px; cursor:pointer; color:var(--ink); }}
.chip[aria-pressed="true"] {{ background:var(--accent); color:var(--bg); }}
.console textarea {{ width:100%; min-height:64px; font:inherit; font-size:.9rem;
  background:var(--bg); color:var(--ink); border:1px solid var(--line);
  border-radius:8px; padding:10px; resize:vertical; }}
.console .row {{ display:flex; gap:10px; align-items:center; margin-top:10px; flex-wrap:wrap; }}
.send {{ background:var(--accent); color:var(--bg); border:none; border-radius:8px;
  padding:8px 18px; font-weight:600; cursor:pointer; }}
.send:disabled {{ opacity:.5; cursor:default; }}
#cmsg {{ font-size:.84rem; color:var(--mut); flex:1; min-width:200px; }}
#cmsg.err {{ color:var(--fail); }} #cmsg.good {{ color:var(--ok); }}
.queue {{ margin-top:12px; font-size:.84rem; color:var(--mut); }}
.queue ul {{ margin:6px 0 0; padding-left:18px; }}
.links {{ display:flex; flex-wrap:wrap; gap:8px; }}
.lnk {{ font-size:.82rem; color:var(--accent); text-decoration:none;
  border:1px solid var(--line); border-radius:7px; padding:4px 11px;
  background:var(--surface); }}
.lnk:hover {{ border-color:var(--accent); }}
.foot {{ margin-top:34px; padding-top:12px; border-top:1px solid var(--line);
  font-size:.76rem; color:var(--mut); display:flex; flex-wrap:wrap; gap:6px 16px; }}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
@media (prefers-reduced-motion: no-preference) {{
  .card {{ transition:border-color .15s; }}
}}
</style>
<div class="wrap">
  <div class="masthead">
    <h1>AgentOps 관제실</h1>
    <span class="date">{today.isoformat()} ({wd})</span>
    <span class="tally">{esc(total_line)}</span>
  </div>

  <h2>조직도</h2>
  {org_html}

  <nav class="tabs" role="tablist" aria-label="관제실 섹션">
    <button class="tab" data-panel="p-prio" role="tab" aria-selected="true">오늘 우선순위 <span class="tab-badge">{prio_count}</span></button>
    <button class="tab" data-panel="p-needs" role="tab" aria-selected="false">결정 대기 <span class="tab-badge">{len(needs)}</span></button>
    <button class="tab" data-panel="p-sched" role="tab" aria-selected="false">일정 <span class="tab-badge">{urgent_count}</span></button>
    <button class="tab" data-panel="p-staff" role="tab" aria-selected="false">직원 현황</button>
    <button class="tab" data-panel="p-console" role="tab" aria-selected="false">명령 콘솔</button>
    <button class="tab" data-panel="p-links" role="tab" aria-selected="false">오늘 산출물</button>
  </nav>

  <section id="p-prio" class="panel" role="tabpanel">
  <h2>오늘 무엇부터</h2>
  {prio_html}
  </section>

  <section id="p-needs" class="panel" role="tabpanel" hidden>
  <h2>대표님 결정 대기</h2>
  <ul class="needs">{needs_html}</ul>
  </section>

  <section id="p-sched" class="panel" role="tabpanel" hidden>
  <h2>일정 (TickTick)</h2>
  <div class="sv-bar">
    <div class="sv-modes" role="group" aria-label="보기 방식">
      <button class="svm" data-m="list" aria-pressed="true">목록</button>
      <button class="svm" data-m="day" aria-pressed="false">일</button>
      <button class="svm" data-m="week" aria-pressed="false">주</button>
      <button class="svm" data-m="month" aria-pressed="false">월</button>
    </div>
    <div class="sv-nav" hidden>
      <button id="sv-prev" aria-label="이전">&#9664;</button>
      <button id="sv-today">오늘</button>
      <button id="sv-next" aria-label="다음">&#9654;</button>
      <b id="sv-title"></b>
    </div>
  </div>
  <div class="sched">
  <div id="sv-list">{sched_html}</div>
  <div id="sv-cal" hidden></div>
  <p class="sched-note mut">{sched_note}</p></div>
  </section>

  <section id="p-staff" class="panel" role="tabpanel" hidden>
  <h2>직원 현황</h2>
  <div class="grid">{"".join(card_html)}</div>
  </section>

  <section id="p-console" class="panel" role="tabpanel" hidden>
  <h2>명령 콘솔</h2>
  <div class="console">
    <div class="chips" role="group" aria-label="명령 종류">
      <button class="chip" data-prefix="재실행: " aria-pressed="false">재실행</button>
      <button class="chip" data-prefix="신규 에이전트: " aria-pressed="false">신규 에이전트</button>
      <button class="chip" data-prefix="지시: " aria-pressed="true">지시</button>
      <button class="chip" data-prefix="위임: " aria-pressed="false">위임 (헤르메스)</button>
    </div>
    <textarea id="cmd" placeholder="예: 위임: ArX 요약 폴더의 이번 주 논문 요약 26건을 주제별로 묶어 한 페이지 메타요약 작성"></textarea>
    <div class="row">
      <button class="send" id="send">명령함에 등록</button>
      <span id="cmsg">명령은 Notion 관제 명령함에 기록되고, 새벽 사이클(브리핑 04:30·당직 04:45)이 실행합니다.</span>
    </div>
    <div class="queue" id="queue"></div>
    <div class="row" style="margin-top:6px">
      <a class="lnk" href="{CMD_PAGE_URL}" target="_blank" rel="noopener">명령함 직접 열기 (Notion)</a>
      <a class="lnk" href="{OPS_PAGE_URL}" target="_blank" rel="noopener">관제 페이지 (Notion)</a>
    </div>
  </div>
  </section>

  <section id="p-links" class="panel" role="tabpanel" hidden>
  <h2>오늘 산출물</h2>
  <div class="links">{links_html or '<span class="mut" style="font-size:.85rem">오늘 링크 산출물이 아직 없습니다.</span>'}</div>
  </section>

  <div class="foot">
    <span>생성 {gen_at} · morning-ops-brief가 매일 아침 재발행</span>
    <span>원본: 로컬 AgentOps (events · handoffs · AGENTS.md · PERSONAS.md)</span>
  </div>
</div>
<script>
(function () {{
  var PAGE_ID = "{CMD_PAGE_ID}";
  var cmd = document.getElementById("cmd");
  var send = document.getElementById("send");
  var msg = document.getElementById("cmsg");
  var queueEl = document.getElementById("queue");
  var prefix = "지시: ";
  var pendingBtn = null;
  var TODAY = "{today.isoformat()}";

  // 결정·요청 전달 상태 (localStorage, 14일 후 자동 정리)
  function markKey(kind, id) {{ return "agentops:" + kind + ":" + id; }}
  function loadMarks() {{
    var cutoff = Date.now() - 14 * 86400000;
    Object.keys(localStorage).forEach(function (k) {{
      if (k.indexOf("agentops:") !== 0) return;
      var ts = parseInt(localStorage.getItem(k), 10);
      if (!ts || ts < cutoff) {{ localStorage.removeItem(k); }}
    }});
    document.querySelectorAll(".reply").forEach(function (b) {{
      if (localStorage.getItem(markKey("decided", b.dataset.item))) {{
        b.classList.add("done"); b.textContent = "결정 전달됨 ✓";
        b.closest(".need").classList.add("decided");
      }}
    }});
    document.querySelectorAll(".rerun").forEach(function (b) {{
      if (localStorage.getItem(markKey("rerun", TODAY + ":" + b.dataset.slug))) {{
        b.classList.add("done"); b.textContent = "요청 전달됨 ✓";
      }}
    }});
  }}
  function markDone(btn) {{
    if (!btn) return;
    if (btn.classList.contains("reply")) {{
      localStorage.setItem(markKey("decided", btn.dataset.item), String(Date.now()));
      btn.classList.add("done"); btn.textContent = "결정 전달됨 ✓";
      btn.closest(".need").classList.add("decided");
    }} else {{
      localStorage.setItem(markKey("rerun", TODAY + ":" + btn.dataset.slug), String(Date.now()));
      btn.classList.add("done"); btn.textContent = "요청 전달됨 ✓";
    }}
  }}

  document.querySelectorAll(".chip").forEach(function (ch) {{
    ch.addEventListener("click", function () {{
      document.querySelectorAll(".chip").forEach(function (c) {{ c.setAttribute("aria-pressed", "false"); }});
      ch.setAttribute("aria-pressed", "true");
      prefix = ch.dataset.prefix;
      pendingBtn = null;
      cmd.focus();
    }});
  }});
  document.querySelectorAll(".rerun").forEach(function (b) {{
    b.addEventListener("click", function () {{
      pendingBtn = b;
      submit("재실행: " + b.dataset.slug, b.dataset.name + " 재실행 요청");
    }});
  }});
  // 결정 회신: 그 자리에서 바로 (인라인 폼, 2026-07-27 대표님 지시)
  document.querySelectorAll(".need").forEach(function (li) {{
    var replyBtn = li.querySelector(".reply");
    var box = li.querySelector(".reply-box");
    if (!replyBtn || !box) return;
    var ta = box.querySelector(".rb-text");
    var rmsg = box.querySelector(".rb-msg");
    var rsend = box.querySelector(".rb-send");
    replyBtn.addEventListener("click", function () {{
      if (replyBtn.classList.contains("done")) return;
      box.hidden = !box.hidden;
      if (!box.hidden) {{ ta.focus(); }}
    }});
    box.querySelectorAll(".qk").forEach(function (q) {{
      q.addEventListener("click", function () {{ ta.value = q.textContent; ta.focus(); }});
    }});
    rsend.addEventListener("click", function () {{
      var d = ta.value.trim();
      if (!d) {{ rmsg.textContent = "결정 내용을 입력해 주세요."; rmsg.className = "rb-msg err"; return; }}
      var text = "지시: [" + replyBtn.dataset.name + " 님 요청 건 결정] " + replyBtn.dataset.item + " => 결정: " + d;
      rsend.disabled = true;
      rmsg.textContent = "등록 중...";
      rmsg.className = "rb-msg";
      post(text).then(function () {{
        rsend.disabled = false;
        rmsg.textContent = "등록했습니다. 새벽 당직(04:45) 또는 아침 브리핑(04:30)이 실행합니다.";
        rmsg.className = "rb-msg good";
        markDone(replyBtn);
        setTimeout(function () {{ box.hidden = true; }}, 1800);
        loadQueue(true);
      }}).catch(function (e) {{
        rsend.disabled = false;
        rmsg.textContent = explainErr(e);
        rmsg.className = "rb-msg err";
      }});
    }});
  }});
  send.addEventListener("click", function () {{
    var t = cmd.value.trim();
    if (!t) {{ setMsg("명령 내용을 입력해 주세요.", "err"); return; }}
    var full = /^(재실행|신규 에이전트|승인|지시|위임):/.test(t) ? t : prefix + t;
    submit(full, "명령");
  }});

  function setMsg(t, cls) {{ msg.textContent = t; msg.className = cls || ""; }}

  function fallback(reasonText) {{
    setMsg(reasonText + " 아래 '명령함 직접 열기'에서 대기 명령에 한 줄로 추가해 주세요.", "err");
  }}

  function stamp() {{
    var d = new Date();
    function p(n) {{ return (n < 10 ? "0" : "") + n; }}
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
      " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }}

  function post(text) {{
    if (!window.claude || window.claude.mcp === undefined) {{
      return Promise.reject({{ code: "no_mcp" }});
    }}
    return window.claude.mcp.callTool("Notion", "notion-update-page", {{
      page_id: PAGE_ID,
      command: "insert_content",
      content: "- [ ] " + stamp() + " | " + text,
      position: {{ type: "end" }}
    }});
  }}

  function explainErr(e) {{
    var code = e && e.code;
    if (code === "no_mcp") return "이 화면에서는 Notion 직접 기록을 사용할 수 없습니다. '명령함 직접 열기'에서 추가해 주세요.";
    if (code === "needs_reauth") return "Notion 연결이 만료되었습니다. claude.ai 설정 → 커넥터에서 Notion을 다시 연결해 주세요.";
    if (code === "server_not_connected") return "Notion 커넥터가 연결되어 있지 않습니다. claude.ai 설정 → 커넥터에서 추가해 주세요.";
    if (code === "selection_required") return "Notion 커넥터가 여러 개입니다. 화면 안내에 따라 하나를 선택해 주세요.";
    if (code === "not_granted" || code === "capability_disabled" || code === "capability_removed") return "이 화면에 Notion 권한이 없습니다. '명령함 직접 열기'에서 추가해 주세요.";
    if (code === "tool_error") return "Notion이 오류를 반환했습니다: " + (e.message || "");
    return "기록에 실패했습니다(" + (code || "unknown") + "). '명령함 직접 열기'에서 추가해 주세요.";
  }}

  function submit(text, label) {{
    send.disabled = true;
    setMsg(label + " 등록 중...");
    post(text).then(function () {{
      send.disabled = false;
      // 이 등록이 특정 버튼(재실행)에서 시작됐으면 완료 표시
      if (pendingBtn && pendingBtn.classList.contains("rerun")) {{
        markDone(pendingBtn);
      }}
      pendingBtn = null;
      cmd.value = "";
      setMsg("등록했습니다. 다음 새벽 사이클(04:30 브리핑·04:45 당직)에 실행됩니다.", "good");
      loadQueue(true);
    }}).catch(function (e) {{
      send.disabled = false;
      pendingBtn = null;
      setMsg(explainErr(e), "err");
    }});
  }}

  function loadQueue(refresh) {{
    if (!window.claude || window.claude.mcp === undefined) return;
    var opts = refresh ? {{ cache: {{ refresh: true }} }} : undefined;
    window.claude.mcp.callTool("Notion", "notion-fetch", {{ id: PAGE_ID }}, opts)
      .then(function (r) {{
        var text = "";
        var p = r && r.payload;
        if (p && typeof p === "object" && typeof p.text === "string") text = p.text;
        else if (typeof p === "string") text = p;
        if (!text) return;
        var sec = text.split(/##\\s*대기 명령/)[1] || "";
        sec = sec.split(/##\\s*처리 완료/)[0] || sec;
        var items = sec.split("\\n").filter(function (l) {{ return /^\\s*-\\s*\\[ \\]/.test(l); }});
        if (items.length) {{
          queueEl.innerHTML = "<b>대기 중 " + items.length + "건</b><ul>" +
            items.slice(0, 8).map(function (l) {{
              return "<li>" + l.replace(/^\\s*-\\s*\\[ \\]\\s*/, "")
                .replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</li>";
            }}).join("") + "</ul>";
        }} else {{
          queueEl.textContent = "대기 중인 명령이 없습니다.";
        }}
      }})
      .catch(function () {{ /* 조회 실패는 조용히 무시 (등록 기능과 무관) */ }});
  }}
  // 일정 캘린더 뷰: 목록/일/주/월 (2026-07-27 대표님 지시)
  var SCHED = {sched_json};
  var svList = document.getElementById("sv-list");
  var svCal = document.getElementById("sv-cal");
  var svNav = document.querySelector(".sv-nav");
  var svTitle = document.getElementById("sv-title");
  if (svCal) {{
    var svMode = "list";
    var anchor = new Date(TODAY + "T00:00:00");
    var WD = ["일", "월", "화", "수", "목", "금", "토"];
    var byDate = {{}};
    SCHED.forEach(function (it) {{ (byDate[it.d] = byDate[it.d] || []).push(it); }});
    Object.keys(byDate).forEach(function (k) {{
      byDate[k].sort(function (a, b) {{ return (a.t || "") < (b.t || "") ? -1 : 1; }});
    }});
    function p2(n) {{ return (n < 10 ? "0" : "") + n; }}
    function iso(d) {{ return d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate()); }}
    function escJs(s) {{ return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }}
    function itemHtml(it, opts) {{
      opts = opts || {{}};
      return '<div class="cal-item' + (it.d < TODAY ? " cal-past" : "") + '" title="' + escJs(it.list) + '">' +
        '<i class="sdot" style="background:' + it.color + '"></i>' +
        (opts.time && it.t ? '<span class="cal-time">' + it.t + "</span>" : "") +
        '<span class="cal-title">' + escJs(it.title) + "</span>" +
        (it.pr === 5 ? ' <span class="sched-pr">중요</span>' : "") +
        (opts.list ? '<span class="cal-ilist">' + escJs(it.list) + "</span>" : "") +
        "</div>";
    }}
    function renderDay() {{
      var k = iso(anchor);
      svTitle.textContent = k + " (" + WD[anchor.getDay()] + ")";
      var items = byDate[k] || [];
      var allday = items.filter(function (i) {{ return !i.t; }});
      var timed = items.filter(function (i) {{ return i.t; }});
      var h = "";
      if (allday.length) {{
        h += '<div class="day-allday"><div class="cal-head">하루 종일</div>' +
          allday.map(function (i) {{ return itemHtml(i, {{ list: true }}); }}).join("") + "</div>";
      }}
      if (timed.length) {{
        var byHour = {{}};
        timed.forEach(function (i) {{ var hh = i.t.slice(0, 2); (byHour[hh] = byHour[hh] || []).push(i); }});
        Object.keys(byHour).sort().forEach(function (hh) {{
          h += '<div class="day-hour"><div class="day-hlabel">' + hh + ":00</div><div>" +
            byHour[hh].map(function (i) {{ return itemHtml(i, {{ time: true, list: true }}); }}).join("") + "</div></div>";
        }});
      }}
      if (!items.length) {{ h = '<p class="day-empty">이 날짜에 잡힌 일정이 없습니다.</p>'; }}
      svCal.innerHTML = h;
    }}
    function weekStart(d) {{
      var s = new Date(d);
      s.setDate(s.getDate() - ((s.getDay() + 6) % 7));
      return s;
    }}
    function renderWeek() {{
      var s = weekStart(anchor);
      var e = new Date(s); e.setDate(e.getDate() + 6);
      svTitle.textContent = iso(s) + " ~ " + iso(e);
      var h = '<div class="cal-week">';
      for (var i = 0; i < 7; i++) {{
        var d = new Date(s); d.setDate(d.getDate() + i);
        var k = iso(d);
        var items = byDate[k] || [];
        h += '<div class="cal-col' + (k === TODAY ? " cal-today" : "") + '">' +
          '<div class="cal-head">' + p2(d.getMonth() + 1) + "-" + p2(d.getDate()) + " (" + WD[d.getDay()] + ")</div>" +
          items.map(function (it) {{ return itemHtml(it, {{ time: true }}); }}).join("") + "</div>";
      }}
      svCal.innerHTML = h + "</div>";
    }}
    function renderMonth() {{
      var y = anchor.getFullYear(), m = anchor.getMonth();
      svTitle.textContent = y + "년 " + (m + 1) + "월";
      var first = new Date(y, m, 1);
      var s = weekStart(first);
      var h = '<div class="cal-month">' +
        ["월", "화", "수", "목", "금", "토", "일"].map(function (w) {{ return '<div class="cal-dow">' + w + "</div>"; }}).join("");
      for (var i = 0; i < 42; i++) {{
        var d = new Date(s); d.setDate(d.getDate() + i);
        if (i === 35 && d.getMonth() !== m) break;
        var k = iso(d);
        var items = byDate[k] || [];
        var cls = "cal-mcell" + (d.getMonth() !== m ? " cal-out" : "") + (k === TODAY ? " cal-today" : "");
        h += '<div class="' + cls + '"><div class="cal-mnum">' + d.getDate() + "</div>" +
          items.slice(0, 3).map(function (it) {{ return itemHtml(it, {{}}); }}).join("") +
          (items.length > 3 ? '<div class="cal-more">+' + (items.length - 3) + "건</div>" : "") + "</div>";
      }}
      svCal.innerHTML = h + "</div>";
    }}
    function renderCal() {{
      if (svMode === "day") renderDay();
      else if (svMode === "week") renderWeek();
      else if (svMode === "month") renderMonth();
    }}
    function setMode(m) {{
      svMode = m;
      document.querySelectorAll(".svm").forEach(function (b) {{
        b.setAttribute("aria-pressed", b.dataset.m === m ? "true" : "false");
      }});
      var isList = m === "list";
      svList.hidden = !isList;
      svCal.hidden = isList;
      svNav.hidden = isList;
      if (!isList) renderCal();
    }}
    document.querySelectorAll(".svm").forEach(function (b) {{
      b.addEventListener("click", function () {{ setMode(b.dataset.m); }});
    }});
    function shift(dir) {{
      if (svMode === "day") anchor.setDate(anchor.getDate() + dir);
      else if (svMode === "week") anchor.setDate(anchor.getDate() + dir * 7);
      else anchor.setMonth(anchor.getMonth() + dir);
      renderCal();
    }}
    document.getElementById("sv-prev").addEventListener("click", function () {{ shift(-1); }});
    document.getElementById("sv-next").addEventListener("click", function () {{ shift(1); }});
    document.getElementById("sv-today").addEventListener("click", function () {{
      anchor = new Date(TODAY + "T00:00:00"); renderCal();
    }});
  }}

  // 탭 내비게이션 (2026-07-27 대표님 지시: 조직도 상단 + 탭 GUI)
  var tabs = document.querySelectorAll(".tab");
  function selectTab(id) {{
    tabs.forEach(function (t) {{
      var on = t.dataset.panel === id;
      t.setAttribute("aria-selected", on ? "true" : "false");
      var p = document.getElementById(t.dataset.panel);
      if (p) {{ p.hidden = !on; }}
    }});
    try {{ localStorage.setItem("agentops:tab", id); }} catch (e) {{}}
  }}
  tabs.forEach(function (t) {{
    t.addEventListener("click", function () {{ selectTab(t.dataset.panel); }});
  }});
  var savedTab = null;
  try {{ savedTab = localStorage.getItem("agentops:tab"); }} catch (e) {{}}
  if (savedTab && document.getElementById(savedTab)) {{ selectTab(savedTab); }}

  loadMarks();
  loadQueue(false);
}})();
</script>
'''
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print("웹 대시보드 생성: %s (%d bytes)" % (OUT, len(page.encode("utf-8"))))
    print("상태: %s | 결정대기 %d건 | 카드 %d명" % (total_line, len(needs), len(cards)))


if __name__ == "__main__":
    build()
