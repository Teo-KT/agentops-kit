// ledger.js — AgentOps 원장 읽기 전용 접근 계층
// 규칙: events/*.jsonl과 handoffs/*.md는 절대 쓰지 않는다.
// 쓰기가 허용된 파일은 dashboard/decided_needs.json 과 dashboard/today_feedback.json 둘뿐이며
// server.js가 담당한다(2026-08-04 피드백 기능으로 두 번째 파일 추가).
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const KST_OFFSET_MS = 9 * 3600 * 1000;

export function todayKST(offsetDays = 0) {
  const d = new Date(Date.now() + KST_OFFSET_MS + offsetDays * 86400 * 1000);
  return d.toISOString().slice(0, 10);
}

export function kstDay() {
  // 0=일 ... 6=토
  return new Date(Date.now() + KST_OFFSET_MS).getUTCDay();
}

function readIf(p) {
  try { return fs.readFileSync(p, 'utf8'); } catch { return null; }
}

// ── 핸드오프 frontmatter 파서 (7키 스키마, 관용적) ─────────────────
export function parseHandoff(text, file = '') {
  const out = { routine: null, run_at: null, status: null, outputs: [], key_findings: [], for: [], needs_user: [], _file: file, _parseError: null };
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  if (!m) { out._parseError = 'frontmatter 없음'; return out; }
  const lines = m[1].split('\n');
  let key = null;
  for (const line of lines) {
    const top = line.match(/^([a-z_]+):\s*(.*)$/);
    if (top) {
      key = top[1];
      const val = top[2].trim();
      if (val === '' ) continue;
      if (val === '[]') { if (Array.isArray(out[key])) out[key] = []; continue; }
      if (Array.isArray(out[key])) {
        // 인라인 리스트 [a, b] — 따옴표 안의 쉼표에서 자르면 안 된다 (2026-08-03 파편화 사건)
        const inl = val.match(/^\[(.*)\]$/);
        if (inl) out[key] = splitInline(inl[1]);
        else out[key] = [unquote(val)];
      } else {
        out[key] = unquote(val);
      }
      continue;
    }
    const item = line.match(/^\s+-\s+(.*)$/);
    if (item && key && Array.isArray(out[key])) { out[key].push(unquote(item[1])); continue; }
    if (key && Array.isArray(out[key])) {
      const t = line.trim();
      if (!t) continue;
      // for: 아래 중첩 맵 (예: "  유단원(lecture-builder): \"...\"")
      if (key === 'for') {
        const nested = t.match(/^(\S[^:]*):\s*(.*)$/);
        if (nested) { out[key].push(`${nested[1].trim()}: ${unquote(nested[2])}`); continue; }
      }
      // 그 외 들여쓴 연속행은 직전 항목의 이어진 문장이다 (여러 줄 따옴표 블록 병합)
      if (out[key].length) {
        out[key][out[key].length - 1] = unquote(`${out[key][out[key].length - 1]} ${t}`);
      }
    }
  }
  return out;
}

// 따옴표를 존중하는 인라인 배열 분해: ["a, b", 'c'] → ["a, b", "c"]
function splitInline(s) {
  const out = [];
  let cur = '', q = null;
  for (const ch of s) {
    if (q) { if (ch === q) q = null; else cur += ch; continue; }
    if (ch === '"' || ch === "'") { q = ch; continue; }
    if (ch === ',') { if (cur.trim()) out.push(cur.trim()); cur = ''; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function unquote(s) {
  s = s.trim();
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) return s.slice(1, -1);
  return s;
}

export function handoffsFor(cfg, date) {
  const dir = path.join(cfg.agentops, 'handoffs', date);
  let files = [];
  try { files = fs.readdirSync(dir).filter(f => f.endsWith('.md')); } catch { return []; }
  return files.map(f => {
    const text = readIf(path.join(dir, f)) || '';
    const h = parseHandoff(text, path.join(dir, f));
    if (!h.routine) h.routine = f.replace(/\.md$/, '');
    h._bodyHead = text.split(/\n---\n/).slice(1).join('\n---\n').split('\n').slice(0, 25).join('\n').trim();
    return h;
  });
}

// ── 페르소나 (PERSONAS.md 직원 명부) ─────────────────────────────────
export function personas(cfg) {
  const text = readIf(path.join(cfg.agentops, 'PERSONAS.md')) || '';
  const map = {};
  for (const line of text.split('\n')) {
    const m = line.match(/^\|\s*([a-z0-9-]+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|/);
    if (m) map[m[1]] = { name: m[2].trim(), title: m[3].trim(), trait: m[4].trim() };
  }
  return map;
}

// 부서 편성 (build_web_dashboard.py ORG_GROUPS와 동일하게 유지)
export const ORG_GROUPS = [
  ['운영실', ['morning-ops-brief', 'ops-command-poller']],
  ['비서·일정실', ['inbox-triage', 'mail-steward', 'deadline-watchdog']],
  ['문헌·리서치센터', ['daily-paper-sensor', 'arxiv-daily-summary-updater', 'literature-night-sensor', 'ft50-weekly-sensor', 'ft50-weekly-insight']],
  ['지식인프라실', ['obsidian-vault-curator', 'daily-embedding-update']],
  ['연구기획실', ['weekly-research-tracker-sync', 'biweekly-research-idea-generator']],
  ['콘텐츠팀', ['brunch-pipeline-v2', 'brunch-calling-series']],
  ['연구지원실', ['grant-scout']],
  ['강의개발팀', ['lecture-builder', 'lecture-refresher']],
  ['심사실', ['review-office', 'review-archivist', 'style-inspector']],
  ['원고제작소', ['manuscript-studio', 'style-inspector']],
  ['기타', ['exec-brief-pipeline']],
];
export const ONDEMAND = new Set(['review-office', 'manuscript-studio', 'style-inspector']);
export const STANDBY = new Set(['brunch-calling-series']);

// ── 로스터 (AGENTS.md §1 표) ────────────────────────────────────────
export function roster(cfg) {
  const text = readIf(cfg.agentsMd) || '';
  const rows = [];
  const sec = text.split('## 1. 루틴 로스터')[1]?.split('\n## ')[0] || '';
  for (const line of sec.split('\n')) {
    const m = line.match(/^\|\s*([a-z0-9-]+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|/);
    if (m) {
      const expectedAt = m[3].trim();
      // 기대시각은 하루 여러 회차를 '·'나 ','로 나열할 수 있다(예: 07:40·12:40·17:40).
      // 결측 판정·plist 생성은 times를 쓰고, 화면 표기는 원문 expectedAt을 쓴다.
      const times = expectedAt.split(/[·,]/).map(s => s.trim()).filter(s => /^\d{1,2}:\d{2}$/.test(s));
      rows.push({ slug: m[1], cadence: m[2].trim(), expectedAt, times, dest: m[4].trim(), handoff: m[5].trim() });
    }
  }
  return rows;
}

// 하루 여러 회차로 도는 루틴(기대시각이 2개 이상)은 당일 dedup에서 제외한다.
export function isMultiRun(cfg, slug) {
  return (roster(cfg).find(r => r.slug === slug)?.times.length || 1) > 1;
}

const CADENCE_DOW = { 'weekly:mon': 1, 'weekly:tue': 2, 'weekly:wed': 3, 'weekly:thu': 4, 'weekly:fri': 5, 'weekly:sat': 6, 'weekly:sun': 0 };

// 오늘 예정 + 결측 판정 (AGENTS.md §관리규칙 2 준수: handoff '활성'만 결측 대상)
export function todayStatus(cfg) {
  const date = todayKST();
  const dow = kstDay();
  const nowHM = new Date(Date.now() + KST_OFFSET_MS).toISOString().slice(11, 16);
  const ros = roster(cfg);
  const today = handoffsFor(cfg, date);
  const yesterday = handoffsFor(cfg, todayKST(-1));
  const byRoutine = Object.fromEntries(today.map(h => [h.routine, h]));
  const yByRoutine = Object.fromEntries(yesterday.map(h => [h.routine, h]));

  const rows = [];
  for (const r of ros) {
    if (!['daily', 'every2days'].includes(r.cadence) && !r.cadence.startsWith('weekly:')) continue;
    const scheduledToday = r.cadence === 'daily' || r.cadence === 'every2days' || CADENCE_DOW[r.cadence] === dow;
    const h = byRoutine[r.slug] || null;
    let state = 'idle';
    if (h) state = h.status === 'ok' ? 'ok' : (h.status === 'partial' ? 'partial' : 'fail');
    else if (r.cadence === 'every2days') state = yByRoutine[r.slug] ? 'ok-y' : (r.handoff === '활성' ? 'missing' : 'idle');
    else if (scheduledToday && r.handoff === '활성' && (r.times[0] || r.expectedAt) < nowHM) state = 'missing';
    else if (scheduledToday) state = 'pending';
    rows.push({ ...r, scheduledToday, state, handoffData: h });
  }
  const tally = { ok: 0, partial: 0, fail: 0, missing: 0 };
  for (const r of rows) {
    if (r.state === 'ok' || r.state === 'ok-y') tally.ok++;
    else if (r.state === 'partial') tally.partial++;
    else if (r.state === 'fail') tally.fail++;
    else if (r.state === 'missing') tally.missing++;
  }
  return { date, rows, tally, handoffs: today };
}

// ── 결정 대기 취합 (최근 N일 needs_user - 억제 목록) ─────────────────
export function decidedKeys(cfg) {
  const p = path.join(cfg.agentops, 'dashboard', 'decided_needs.json');
  try { return JSON.parse(fs.readFileSync(p, 'utf8')).items.map(i => i.key); } catch { return []; }
}

export function needsRewrite(cfg) {
  const p = path.join(cfg.agentops, 'dashboard', 'needs_rewrite.json');
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { return {}; }
}

export function pendingNeeds(cfg) {
  // 루틴별 '최신 핸드오프'의 needs_user만 유효하다(루틴이 미해결 건을 매일 재상신하므로
  // 과거 회차를 합산하면 같은 안건의 어제 문구가 유령으로 쌓인다). 억제 목록(decided_needs)으로 2차 필터.
  const decided = decidedKeys(cfg);
  const suppressed = new Set(decided);
  // 억제 키는 명령함 원문("지시: [X 요청 건 결정] ...")인 경우가 많아 핸드오프 문구와 앞 60자가
  // 어긋난다. 접두를 벗긴 정규화 코어(32자)의 부분일치를 2차 필터로 쓴다.
  // 공백과 따옴표를 무시하고 비교한다. 과거 파서가 파편 키(따옴표 포함)를 억제 목록에
  // 남겼어도, 파서 수정 후 병합된 원문과 매칭되어 유령 재등장을 막는다 (2026-08-03).
  const norm = s => s.replace(/["'\s]+/g, '');
  const cores = decided
    .map(k => norm(k.replace(/^지시:\s*\[[^\]]*\]\s*/, '').replace(/^\((?:이월|신규)[^)]*\)\s*/, '')).slice(0, 32))
    .filter(c => c.length >= 14);
  const rewrite = needsRewrite(cfg);
  const latest = new Map(); // routine -> {date, needs}
  for (let d = 0; d < (cfg.needsWindowDays || 8); d++) {
    const date = todayKST(-d);
    for (const h of handoffsFor(cfg, date)) {
      if (!latest.has(h.routine)) latest.set(h.routine, { date, needs: h.needs_user || [] });
    }
  }
  const items = [];
  for (const [routine, { date, needs }] of latest) {
    for (const need of needs) {
      const key = need.slice(0, 60);
      if (suppressed.has(key)) continue;
      const nText = norm(need.replace(/^\((?:이월|신규)[^)]*\)\s*/, ''));
      if (cores.some(c => nText.includes(c))) continue;
      items.push({ key, routine, date, text: rewrite[need] || need });
    }
  }
  return items;
}

// ── 결정 데스크 (강단아 정제본) ─────────────────────────────────────
export function curatedNeeds(cfg) {
  try { return JSON.parse(fs.readFileSync(path.join(cfg.agentops, 'dashboard', 'needs_curated.json'), 'utf8')); } catch { return null; }
}

// 결정 탭 통합 뷰: 정제본(중복 통합·품질 검증) + 정제 이후 새로 올라온 원문(fresh).
// 정제 항목은 sources 중 하나라도 아직 미결(원문이 pending)일 때만 노출하고,
// 결정 시 sources의 모든 key를 억제해 원문 유령 재등장을 막는다.
export function needsView(cfg) {
  const raw = pendingNeeds(cfg);
  const cur = curatedNeeds(cfg);
  if (!cur || !Array.isArray(cur.items)) return { curated: [], fresh: raw, curatedAt: null };
  const rawKeys = new Set(raw.map(r => r.key));
  const covered = new Set();
  const curated = [];
  for (const it of cur.items) {
    const sources = (it.sources || []).filter(s => s && s.key);
    for (const s of sources) covered.add(s.key);
    const liveKeys = sources.filter(s => rawKeys.has(s.key)).map(s => s.key);
    if (!liveKeys.length) continue; // 모든 출처가 이미 결정·억제됨
    curated.push({ id: it.id || null, question: it.question, context: it.context || null,
      recommend: it.recommend || null, due: it.due || null, sources, liveKeys });
  }
  const fresh = raw.filter(r => !covered.has(r.key));
  return { curated, fresh, curatedAt: cur.generated_at || cur.date || null };
}

// ── 결정 이력 (대표님이 내린 결정 + 그 뒤 처리 결과) ────────────────
// 출처 3종을 하나로 합친다. 원장은 읽기 전용이고, 여기서도 읽기만 한다.
//   1) agentops-web/queue/decisions.jsonl  — 웹 관제실 결정의 정확한 시각·원문(자체 소유 감사 로그)
//   2) AgentOps/dashboard/decided_needs.json — 경로 무관 전체 억제 목록(당직·브리핑 처리분 포함)
//   3) AgentOps/decisions/{날짜}_{웹|대표}결정.md — 당직(한달음)·브리핑(강단아)의 처리 서술과 RESULT
const normKey = s => String(s || '').replace(/["'\s]+/g, '');

function decisionQueue(cfg) {
  const p = path.join(cfg.queueDir, 'decisions.jsonl');
  const text = readIf(p);
  if (!text) return [];
  const out = [];
  for (const line of text.trim().split('\n')) {
    try {
      const d = JSON.parse(line);
      if (!d.ts || !d.key) continue;
      // ts는 UTC ISO. KST 날짜·시각으로 환산해 하루 단위로 묶는다.
      const kst = new Date(new Date(d.ts).getTime() + KST_OFFSET_MS).toISOString();
      out.push({ date: kst.slice(0, 10), time: kst.slice(11, 16), ts: d.ts, key: d.key,
                 decision: d.decision || '', question: d.question || null, routine: d.routine || null });
    } catch { /* 깨진 줄은 건너뛴다 */ }
  }
  return out;
}

// decisions/*.md를 '## ' 블록으로 쪼개 처리 서술을 뽑는다. 머리말 형식이 회차마다
// 달라(`## [웹 결정 N건차] …`, `## 결정 #2 — …`, `## 08:08 KST | …`) 제목은 그대로 쓰고
// 본문에서 굵은 라벨만 관대하게 긁는다.
function decisionDocs(cfg, date) {
  const blocks = [];
  for (const kind of ['웹결정', '대표결정']) {
    const text = readIf(path.join(cfg.agentops, 'decisions', `${date}_${kind}.md`));
    if (!text) continue;
    for (const raw of text.split(/\n(?=## )/)) {
      if (!raw.startsWith('## ')) continue;
      const title = raw.slice(3).split('\n')[0].trim();
      const grab = re => (raw.match(re) || [])[1]?.trim().replace(/^["“]|["”]$/g, '') || null;
      const tm = title.match(/(\d{2}):(\d{2})/);
      blocks.push({
        kind, title,
        time: tm ? `${tm[1]}:${tm[2]}` : null,
        // 라벨 표기는 회차마다 흔들린다(`**RESULT**:` · `RESULT:` · `RESULT(중복 회수):`).
        asked: grab(/\*{0,2}원문 요청[^:*]*\*{0,2}:\s*(.+)/),
        decision: grab(/\*{0,2}대표님 결정\*{0,2}:\s*(.+)/),
        result: grab(/\*{0,2}RESULT[^:\n]*\*{0,2}:\s*([\s\S]+?)(?:\n\n|\n##|$)/),
        routine: grab(/\*{0,2}요청자\(루틴\)\*{0,2}:\s*(.+)/),
        norm: normKey(raw),
      });
    }
  }
  return blocks;
}

// 결정 1건에 붙는 처리 결과를 찾는다.
// 결정 데스크 정제본은 화면에 뜬 질문(question)과 억제 키(key)의 문구가 다르므로 둘 다 시도한다.
function matchDoc(blocks, { key, question, decision }) {
  const cores = [question, key]
    .filter(Boolean)
    .map(s => normKey(s).replace(/^지시:\[[^\]]*\]/, '').slice(0, 24))
    .filter(c => c.length >= 8);
  const dec = normKey(decision).slice(0, 16);
  for (const core of cores) {
    const hit = blocks.find(b => b.norm.includes(core) && (!dec || b.norm.includes(dec)))
             || blocks.find(b => b.norm.includes(core));
    if (hit) return hit;
  }
  return null;
}

// 처리 상태 3단: 결과 문장까지 있음 / 처리 문서만 있음 / 아직 확인 안 됨
function handledState(doc) {
  if (doc && doc.result) return 'done';
  if (doc) return 'logged';
  return 'unknown';
}

export function decisionHistory(cfg, days = 14) {
  const decided = (() => {
    try { return JSON.parse(fs.readFileSync(path.join(cfg.agentops, 'dashboard', 'decided_needs.json'), 'utf8')).items || []; }
    catch { return []; }
  })();
  const queue = decisionQueue(cfg);
  const dates = [];
  for (let d = 0; d < days; d++) dates.push(todayKST(-d));
  const rewrite = needsRewrite(cfg);

  const out = [];
  for (const date of dates) {
    const blocks = decisionDocs(cfg, date);
    const items = [];
    const seen = new Set();     // 이미 올린 질문의 정규화 코어들
    const usedDocs = new Set(); // 이미 어떤 항목에 붙은 처리 문서 제목
    // 같은 안건이 (웹 큐 · 억제 목록 · 처리 문서) 세 곳에 조금씩 다른 문구로 남으므로
    // 질문 코어 24자 겹침을 한 건으로 본다.
    const coreOf = s => normKey(s).replace(/^지시:\[[^\]]*\]/, '').replace(/^\((?:이월|신규)[^)]*\)/, '').slice(0, 24);
    const isDup = s => {
      const c = coreOf(s);
      if (c.length < 8) return false;
      for (const p of seen) if (p.includes(c) || c.includes(p)) return true;
      return false;
    };
    const remember = s => { const c = coreOf(s); if (c.length >= 8) seen.add(c); };

    // (1) 웹 관제실에서 내가 누른 결정 — 시각까지 정확
    for (const q of queue.filter(q => q.date === date)) {
      const label = q.question || q.key;
      // 정제본은 출처 키가 여러 개라 같은 결정이 여러 줄로 남는다
      if (isDup(label) && items.some(i => i.decision === q.decision)) continue;
      remember(label);
      const doc = matchDoc(blocks, q);
      if (doc) usedDocs.add(doc.title);
      items.push({
        time: q.time, source: '웹 관제실',
        question: q.question || rewrite[q.key] || q.key, decision: q.decision,
        routine: q.routine || doc?.routine || null,
        result: doc?.result || null,
        state: handledState(doc), docTitle: doc?.title || null, docKind: doc?.kind || null,
      });
    }
    // (2) 같은 날 억제 목록에 오른 건 중 (1)에 없는 것 — 명령함·아침 브리핑 경로로 처리된 결정
    for (const it of decided.filter(i => i.resolved === date)) {
      if (isDup(it.key)) continue;
      remember(it.key);
      const doc = matchDoc(blocks, { key: it.key, decision: '' });
      if (doc) usedDocs.add(doc.title);
      items.push({
        time: doc?.time || null, source: doc?.kind === '대표결정' ? '아침 브리핑' : '명령함·기타',
        question: rewrite[it.key] || it.key, decision: it.result || '(결정 문구 미기록)',
        routine: doc?.routine || null, result: doc?.result || null,
        state: handledState(doc), docTitle: doc?.title || null, docKind: doc?.kind || null,
      });
    }
    // (3) 처리 문서만 있고 위 둘에 안 잡힌 블록(당직이 직접 정리한 건)도 이력에 남긴다
    for (const b of blocks) {
      if (!b.decision || usedDocs.has(b.title)) continue;
      if (isDup(b.asked || b.title)) continue;
      remember(b.asked || b.title);
      items.push({
        time: b.time, source: b.kind === '대표결정' ? '아침 브리핑' : '당직 처리',
        question: b.asked || b.title, decision: b.decision, routine: b.routine,
        result: b.result, state: handledState(b), docTitle: b.title, docKind: b.kind,
      });
    }
    if (items.length) {
      items.sort((a, b) => (a.time || '99:99') < (b.time || '99:99') ? -1 : 1);
      out.push({ date, items });
    }
  }
  return { days, generatedAt: new Date().toISOString(), dates: out };
}

// ── 이벤트 tail ─────────────────────────────────────────────────────
export function eventsTail(cfg, n = 120) {
  const month = todayKST().slice(0, 7);
  const p = path.join(cfg.agentops, 'events', `${month}.jsonl`);
  const text = readIf(p);
  if (!text) return [];
  const lines = text.trim().split('\n').slice(-n);
  return lines.map(l => {
    try {
      const d = JSON.parse(l);
      return { ts: d.ts, event: d.event, slug: d.payload?.routine_slug || null, session: (d.session_id || '').slice(0, 8) };
    } catch { return null; }
  }).filter(Boolean);
}

// ── 파생 데이터 파일들 ───────────────────────────────────────────────
export function priorities(cfg) {
  try { return JSON.parse(fs.readFileSync(path.join(cfg.agentops, 'dashboard', 'today_priorities.json'), 'utf8')); } catch { return null; }
}

// ── 대표님 피드백 (2026-08-04) ──────────────────────────────────────
// today_priorities.json은 강단아가 새벽에 생성하는 파생 뷰라 직접 고치지 않는다(§5).
// 대신 서버가 소유하는 겹장부 today_feedback.json에 '처리했다·이렇게 바꿔라'를 쌓고,
// 화면은 둘을 합쳐 보여 준다. 다음 새벽 브리핑이 이 파일을 읽어 우선순위에 반영한다.
export const FEEDBACK_FILE = 'today_feedback.json';

export function itemKey(section, label) {
  return crypto.createHash('sha1').update(`${section}|${label}`).digest('hex').slice(0, 12);
}

export function todayFeedback(cfg) {
  const p = path.join(cfg.agentops, 'dashboard', FEEDBACK_FILE);
  try {
    const d = JSON.parse(fs.readFileSync(p, 'utf8'));
    return (d && typeof d === 'object' && d.items) ? d : { date: todayKST(), items: {} };
  } catch { return { date: todayKST(), items: {} }; }
}

// 우선순위 + 피드백 합본. 항목마다 안정적인 key를 붙여 화면이 그대로 되돌려 보낼 수 있게 한다.
export function prioritiesView(cfg) {
  const pr = priorities(cfg);
  if (!pr) return null;
  const fb = todayFeedback(cfg);
  const sections = (pr.sections || []).map(s => ({
    title: s.title,
    items: (s.items || []).map(it => {
      const key = itemKey(s.title, it.label);
      return { ...it, key, feedback: fb.items[key] || null };
    }),
  }));
  return { ...pr, sections, feedbackAt: fb.updated_at || null };
}
export function gcalSnapshot(cfg) {
  try { return JSON.parse(fs.readFileSync(path.join(cfg.ticktickBridge, 'gcal_snapshot.json'), 'utf8')); } catch { return null; }
}
export function ticktickSnapshot(cfg) {
  try { return JSON.parse(fs.readFileSync(path.join(cfg.ticktickBridge, 'snapshot.json'), 'utf8')); } catch { return null; }
}
export function routineSkillPath(cfg, slug) {
  if (!/^[a-z0-9-]+$/.test(slug)) return null;
  const p = path.join(cfg.scheduledTasksDir, slug, 'SKILL.md');
  return fs.existsSync(p) ? p : null;
}
export function listRoutines(cfg) {
  try {
    return fs.readdirSync(cfg.scheduledTasksDir).filter(s => fs.existsSync(path.join(cfg.scheduledTasksDir, s, 'SKILL.md')));
  } catch { return []; }
}
