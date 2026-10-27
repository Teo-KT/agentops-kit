// dispatcher.js — 엔진 4종 실행 계층
// 엔진: claude(구독 헤드리스, 기본) · hermes(로컬 LLM) · openai(코드 레인) · gemini(공개 문헌 레인)
// 라우팅 원칙(AGENTS.md §3 확장, 2026-07-31 대표님 승인):
//   기밀도 높음 → hermes / 공개 논문 대량 → gemini / 코드 → openai / 그 외 → claude
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { routineSkillPath, handoffsFor, todayKST, isMultiRun } from './ledger.js';

const jobs = new Map(); // id -> job
let seq = 0;

export function listJobs() {
  return [...jobs.values()].map(j => ({ ...j, proc: undefined })).sort((a, b) => b.id.localeCompare(a.id));
}
export function getJob(id) { return jobs.get(id) || null; }

function newJob(cfg, { kind, label, engine }) {
  const id = `${Date.now()}-${(++seq).toString(36)}`;
  const logFile = path.join(cfg.jobLogDir, `${id}.log`);
  fs.mkdirSync(cfg.jobLogDir, { recursive: true });
  const job = { id, kind, label, engine, status: 'running', startedAt: new Date().toISOString(), endedAt: null, exitCode: null, logFile, tail: '' };
  jobs.set(id, job);
  return job;
}

function logLine(job, line) {
  const stamped = `[${new Date().toISOString().slice(11, 19)}] ${line}\n`;
  fs.appendFileSync(job.logFile, stamped);
  job.tail = (job.tail + stamped).slice(-4000);
}

function finish(cfg, job, code, note = '') {
  job.status = code === 0 ? 'done' : 'fail';
  job.exitCode = code;
  job.endedAt = new Date().toISOString();
  if (note) logLine(job, note);
  if (job.status === 'fail' && cfg.discord?.enabled && cfg.discord.notifyOn?.includes('job_fail')) {
    notifyDiscord(cfg, `⚠️ [AgentOps] 작업 실패: ${job.label} (engine=${job.engine}, exit=${code})\n로그: ${job.logFile}`);
  }
}

// ── Discord 알림 (hermes send, 게이트웨이 불요) ─────────────────────
export function notifyDiscord(cfg, message) {
  try {
    execFileSync(cfg.engines.hermes.sendCli, ['send', '-t', cfg.discord.target, '-q', message], { timeout: 30000 });
    return true;
  } catch (e) {
    console.error('[discord notify 실패]', e.message?.slice(0, 200));
    return false;
  }
}

// ── claude 헤드리스 ─────────────────────────────────────────────────
export function runClaude(cfg, { prompt, label, kind = 'ask', extraArgs = [], onDone = null }) {
  const eng = cfg.engines.claude;
  const job = newJob(cfg, { kind, label, engine: 'claude' });
  const args = ['-p', prompt, '--output-format', 'text', ...extraArgs];
  logLine(job, `claude 헤드리스 시작: ${label}`);
  const proc = spawn(eng.cmd, args, { cwd: eng.cwd, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, AGENTOPS_JOB: job.id } });
  job.proc = proc;
  const timeout = setTimeout(() => {
    logLine(job, `타임아웃 ${eng.timeoutMin}분 초과, 종료 시도`);
    proc.kill('SIGTERM');
  }, eng.timeoutMin * 60 * 1000);
  proc.stdout.on('data', d => logLine(job, d.toString().trimEnd()));
  proc.stderr.on('data', d => logLine(job, `[err] ${d.toString().trimEnd()}`));
  proc.on('close', code => {
    clearTimeout(timeout);
    finish(cfg, job, code ?? 1);
    if (onDone) onDone(job);
  });
  return job;
}

// ── 루틴 실행 (SKILL.md 래핑, agent-ops-logger가 슬러그를 인식하도록 태그 유지) ──
export function runRoutine(cfg, slug, { force = false } = {}) {
  const skillPath = routineSkillPath(cfg, slug);
  if (!skillPath) return { error: `루틴 없음: ${slug}` };

  // every2days 게이팅 + 당일 중복 방지 (dedup, force로 우회 가능)
  // 예외: 기대시각이 여러 개인 루틴(하루 다회차)은 핸드오프 1개를 갱신하며 도므로 dedup을 걸지 않는다.
  //       회차 간 중복 산출 방지는 그 루틴 자신의 처리 원장(예: mail-office/drafted.jsonl)이 맡는다.
  if (!force && !isMultiRun(cfg, slug)) {
    const today = handoffsFor(cfg, todayKST()).some(h => h.routine === slug);
    if (today) return { error: `오늘 핸드오프가 이미 있음: ${slug} (force로 재실행 가능)` };
  }
  const skill = fs.readFileSync(skillPath, 'utf8');
  const prompt = [
    `<scheduled-task name="${slug}" file="${skillPath}">`,
    `This is an automated run of a scheduled task (agentops-web dispatcher). The user is not present to answer questions. Execute autonomously, make reasonable choices and note them in your output. Only take "write" actions the task file asks for. When in doubt, producing a report is the correct output.`,
    '',
    skill,
    `</scheduled-task>`,
  ].join('\n');
  return runClaude(cfg, {
    prompt, label: `루틴: ${slug}`, kind: 'routine',
    onDone: job => {
      // 핸드오프 미생성 실패 감지 → Discord 통지
      const made = handoffsFor(cfg, todayKST()).some(h => h.routine === slug);
      if (!made && cfg.discord?.enabled && cfg.discord.notifyOn?.includes('routine_missing')) {
        notifyDiscord(cfg, `⚠️ [AgentOps] 루틴 ${slug} 종료(exit=${job.exitCode})했으나 오늘 핸드오프가 없습니다. 확인 필요.`);
      }
    },
  });
}

// ── 대표님 피드백 즉시 처리 (2026-08-04) ────────────────────────────
// 우선순위 항목의 '완료·변경' 또는 특정 직원(루틴)에 대한 지시를 그 자리에서 이행한다.
// 결정 처리(runDecision)와 같은 계약: 불변규칙은 우회되지 않고, 이행은 담당 루틴에 위임한다.
export function runFeedback(cfg, { kind, slug, label, action, text }) {
  const isRoutine = kind === 'routine';
  const head = isRoutine
    ? `대표님이 직원(루틴) '${slug}'에게 직접 지시를 남기셨다.`
    : `대표님이 오늘 우선순위 항목에 피드백을 남기셨다.`;
  const prompt = [
    `당신은 AgentOps 당직 오퍼레이터(한달음)다. ${head} ops-command-poller SKILL의 '지시:' 처리 규칙에 준해 즉시 처리하라.`,
    `불변규칙(AGENTS.md §5)은 피드백으로 우회되지 않는다. 외부 자동 발송·원자료 수정·자격증명 접근은 거부하고 사유를 기록하라.`,
    ``,
    `[피드백]`,
    isRoutine ? `- 대상 직원(루틴): ${slug}` : `- 대상 항목: ${label}`,
    `- 구분: ${action}`,
    `- 대표님 말씀: ${text || '(완료 표시만)'}`,
    ``,
    `[처리 절차]`,
    `1. 무엇을 뜻하는지 먼저 확인하라. 관련 핸드오프·원장·TickTick 스냅샷을 읽고 사실을 맞춘 뒤에 움직인다. 추측으로 쓰지 않는다.`,
    isRoutine
      ? `2. 그 루틴이 다음 실행에서 반드시 읽도록 \`$AGENTOPS/handoffs/${todayKST()}/ops-feedback_${slug}.md\`를 작성하라(frontmatter 7키, \`for: [${slug}]\`, needs_user는 []). 이미 있으면 body에 한 줄 누적한다. 지시가 즉시 실행을 요구하고 오늘 산출이 아직 없으면 서버 \`POST /api/run/${slug}\`로 재디스패치해도 좋다(당일 중복 산출은 피한다).`
      : `2. 항목의 담당 루틴을 판별해(우선순위 why 문구·핸드오프 대조) 그 루틴이 다음 실행에서 읽도록 \`$AGENTOPS/handoffs/${todayKST()}/ops-feedback_{담당슬러그}.md\`를 작성하라(frontmatter 7키, \`for: [{담당슬러그}]\`). 담당이 불분명하면 작성하지 말고 사유를 남긴다.`,
    `3. 일정 반영이 필요하면 TickTick 브리지의 **봇 리스트 2개(연구 마감·주요 일정)에 한해** 쓰기가 허용된다(§5-8). 그 밖의 리스트·캘린더는 읽기만 한다.`,
    `4. 연구 원장(RESEARCH_PROJECTS.md)은 직접 고치지 말고 weekly-research-tracker-sync에 위임하라.`,
    `5. 처리 결과를 \`$AGENTOPS/decisions/${todayKST()}_피드백.md\`에 append하라(파일 없으면 생성, 기존 내용 보존). 형식은 웹결정 문서와 같게: \`## [HH:MM] 제목\`, \`**대상**\`, \`**대표님 말씀**\`, \`### 판정·조치\`, \`**RESULT**: 한 줄\`.`,
    `6. 마지막 줄에 "RESULT: <한 줄 요약>"을 출력하라.`,
  ].join('\n');
  return runClaude(cfg, {
    prompt, label: `피드백: ${(isRoutine ? slug : label).slice(0, 34)}`, kind: 'feedback',
    onDone: job => {
      if (cfg.discord?.enabled && cfg.discord.notifyOn?.includes('decision_processed')) {
        const m = job.tail.match(/RESULT: (.*)/);
        notifyDiscord(cfg, `📝 [AgentOps] 피드백 ${job.status === 'done' ? '반영' : '실패'}: ${(isRoutine ? slug : label).slice(0, 50)}\n→ ${m ? m[1] : '(요약 없음)'}`);
      }
    },
  });
}

// ── 결정 즉시 처리 (ops-command-poller '지시:' 규칙 준용) ────────────
export function runDecision(cfg, { who, routine, itemText, decision }) {
  const prompt = [
    `당신은 AgentOps 당직 오퍼레이터(한달음)다. 웹 관제실에서 대표님이 방금 내린 결정 1건을 ops-command-poller SKILL의 '지시:' 처리 규칙에 준해 즉시 처리하라.`,
    `불변규칙(AGENTS.md §5)은 결정으로 우회되지 않는다. 외부 자동 발송·원자료 수정·자격증명 접근은 거부하고 사유를 기록하라.`,
    ``,
    `[결정]`,
    `- 요청자(루틴): ${who || routine}`,
    `- 원문 요청: ${itemText}`,
    `- 대표님 결정: ${decision}`,
    ``,
    `[처리 절차]`,
    `1. 결정을 이행하거나(범위 내), 담당 루틴에 위임이 필요하면 다음 실행이 읽도록 관련 파일·핸드오프 경로에 기록한다.`,
    `2. AgentOps/dashboard/decided_needs.json 억제 키는 이미 서버가 추가했다(중복 추가 금지).`,
    `3. 처리 결과를 AgentOps/decisions/${todayKST()}_웹결정.md 에 append한다(파일 없으면 생성, 기존 내용 보존).`,
    `4. 마지막 줄에 "RESULT: <한 줄 요약>"을 출력한다.`,
  ].join('\n');
  return runClaude(cfg, {
    prompt, label: `결정 처리: ${itemText.slice(0, 40)}...`, kind: 'decision',
    onDone: job => {
      if (cfg.discord?.enabled && cfg.discord.notifyOn?.includes('decision_processed')) {
        const m = job.tail.match(/RESULT: (.*)/);
        notifyDiscord(cfg, `🧭 [AgentOps] 결정 처리 ${job.status === 'done' ? '완료' : '실패'}: ${itemText.slice(0, 60)}\n→ ${m ? m[1] : '(요약 없음)'}`);
      }
    },
  });
}

// ── hermes (로컬 LLM 원샷) ──────────────────────────────────────────
export function runHermes(cfg, { slug, prompt }) {
  const job = newJob(cfg, { kind: 'hermes', label: `hermes: ${slug}`, engine: 'hermes' });
  const promptFile = path.join(cfg.queueDir, `hermes-${job.id}.txt`);
  fs.mkdirSync(cfg.queueDir, { recursive: true });
  fs.writeFileSync(promptFile, prompt);
  logLine(job, `hermes-task.sh ${slug} 시작`);
  const proc = spawn(cfg.engines.hermes.taskScript, [slug, promptFile], { env: process.env });
  job.proc = proc;
  proc.stdout.on('data', d => logLine(job, d.toString().trimEnd()));
  proc.stderr.on('data', d => logLine(job, `[err] ${d.toString().trimEnd()}`));
  proc.on('close', code => finish(cfg, job, code ?? 1));
  return job;
}

// ── API 키 (Keychain 전용, 파일·로그에 절대 남기지 않음) ─────────────
const keyCache = new Map();
function keychainKey(service) {
  if (keyCache.has(service)) return keyCache.get(service);
  try {
    const key = execFileSync('/usr/bin/security', ['find-generic-password', '-s', service, '-w'], { timeout: 10000 }).toString().trim();
    keyCache.set(service, key);
    return key;
  } catch { return null; }
}

// ── openai (코드 레인) ──────────────────────────────────────────────
export async function runOpenAI(cfg, { prompt, label = 'openai 작업' }) {
  const eng = cfg.engines.openai;
  const key = keychainKey(eng.keychainService);
  const job = newJob(cfg, { kind: 'api', label, engine: 'openai' });
  if (!key) { finish(cfg, job, 1, `키 미설정: keychain '${eng.keychainService}' (bin/setup-keys.sh 실행 필요)`); return job; }
  logLine(job, `OpenAI ${eng.model} 호출`);
  try {
    const res = await fetch(eng.endpoint, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: eng.model, input: prompt }),
      signal: AbortSignal.timeout(eng.timeoutSec * 1000),
    });
    const data = await res.json();
    if (!res.ok) { finish(cfg, job, 1, `API 오류 ${res.status}: ${JSON.stringify(data).slice(0, 500)}`); return job; }
    const text = data.output_text
      || data.output?.filter(o => o.type === 'message').flatMap(o => o.content || []).filter(c => c.type === 'output_text').map(c => c.text).join('\n')
      || JSON.stringify(data).slice(0, 2000);
    logLine(job, text);
    finish(cfg, job, 0);
  } catch (e) { finish(cfg, job, 1, `호출 실패: ${e.message}`); }
  return job;
}

// ── gemini (공개 문헌 전용 레인) ─────────────────────────────────────
const GEMINI_FORBIDDEN = [/설문\s*원자료/, /미공개\s*원고/, /Research_Cowork\/(?!ArX|FT50)/];
export async function runGemini(cfg, { prompt, label = 'gemini 작업' }) {
  const eng = cfg.engines.gemini;
  const job = newJob(cfg, { kind: 'api', label, engine: 'gemini' });
  // 데이터 경계 가드 (2026-07-31 대표님 확정: 공개 논문 전용)
  for (const rx of GEMINI_FORBIDDEN) {
    if (rx.test(prompt)) { finish(cfg, job, 1, `차단: 공개 논문 전용 레인에 비공개 데이터 패턴 감지 (${rx})`); return job; }
  }
  const key = keychainKey(eng.keychainService);
  if (!key) { finish(cfg, job, 1, `키 미설정: keychain '${eng.keychainService}' (bin/setup-keys.sh 실행 필요)`); return job; }
  logLine(job, `Gemini ${eng.model} 호출`);
  try {
    const url = `${eng.endpoint}/models/${eng.model}:generateContent?key=${key}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
      signal: AbortSignal.timeout(eng.timeoutSec * 1000),
    });
    const data = await res.json();
    if (!res.ok) { finish(cfg, job, 1, `API 오류 ${res.status}: ${JSON.stringify(data).slice(0, 500)}`); return job; }
    const text = data.candidates?.[0]?.content?.parts?.map(p => p.text).join('\n') || JSON.stringify(data).slice(0, 2000);
    logLine(job, text);
    finish(cfg, job, 0);
  } catch (e) { finish(cfg, job, 1, `호출 실패: ${e.message}`); }
  return job;
}
