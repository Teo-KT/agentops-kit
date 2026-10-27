// server.js — AgentOps 웹 관제 서버 (Node 무의존, 127.0.0.1 전용)
// 실행: node server/server.js   (launchd: com.agentops.server)
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as L from './ledger.js';
import * as D from './dispatcher.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' };

function json(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}

async function readBody(req) {
  let buf = '';
  for await (const chunk of req) { buf += chunk; if (buf.length > 1e6) throw new Error('본문 과대'); }
  return buf ? JSON.parse(buf) : {};
}

// ── 결정 기록: decided_needs.json append (유일한 원장측 쓰기, append-only 의미 유지) ──
function recordDecision({ key, decision, question, routine }) {
  const p = path.join(cfg.agentops, 'dashboard', 'decided_needs.json');
  const data = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (!data.items.some(i => i.key === key)) {
    data.items.push({ key, resolved: L.todayKST(), result: `${decision} (웹 관제실 즉시 처리)` });
    fs.writeFileSync(p, JSON.stringify(data, null, 1));
  }
  // 감사용 로컬 큐 (agentops-web 소유 파일, 원장 아님) = 결정 이력 탭의 1차 출처.
  // question(정제본 질문)·routine을 함께 남겨야 당직이 쓴 처리 문서와 짝지을 수 있다.
  fs.mkdirSync(cfg.queueDir, { recursive: true });
  fs.appendFileSync(path.join(cfg.queueDir, 'decisions.jsonl'),
    JSON.stringify({ ts: new Date().toISOString(), key, decision, question: question || null, routine: routine || null }) + '\n');
}

// ── 우선순위 피드백 겹장부 (서버가 쓰는 두 번째이자 마지막 원장측 파일) ──
// today_priorities.json은 강단아가 새벽에 생성하는 파생 뷰라 직접 고치지 않는다.
// 대신 여기에 '처리했다·이렇게 바꿔라'를 쌓고, 화면과 다음 새벽 브리핑이 합쳐서 읽는다.
function recordPriorityFeedback(key, rec) {
  const p = path.join(cfg.agentops, 'dashboard', L.FEEDBACK_FILE);
  let data = L.todayFeedback(cfg);
  if (data.date !== L.todayKST()) data = { date: L.todayKST(), items: {} };  // 날짜가 바뀌면 새 장부
  data._doc = "관제실 '오늘' 탭에서 대표님이 남긴 피드백. agentops-web 서버가 쓰고, 화면과 morning-ops-brief(6-0단계)가 읽는다. 키는 sha1(섹션제목|항목label)[0:12].";
  data.updated_at = new Date().toISOString();
  data.items[key] = rec;
  fs.writeFileSync(p, JSON.stringify(data, null, 1));
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const p = url.pathname;
  try {
    // ── API ──
    if (p === '/api/overview') {
      const status = L.todayStatus(cfg);
      const who = L.personas(cfg);
      return json(res, 200, {
        date: status.date, tally: status.tally, routines: status.rows.map(r => ({
          slug: r.slug, cadence: r.cadence, expectedAt: r.expectedAt, state: r.state,
          status: r.handoffData?.status || null, runAt: r.handoffData?.run_at || null,
          findings: r.handoffData?.key_findings?.slice(0, 2) || [],
          who: who[r.slug] || null,
        })),
        priorities: L.prioritiesView(cfg),
        needsCount: (() => { const nv = L.needsView(cfg); return nv.curated.length + nv.fresh.length; })(),
        jobs: D.listJobs().slice(0, 5),
      });
    }
    if (p === '/api/org') {
      const who = L.personas(cfg);
      const status = L.todayStatus(cfg);
      const stateBySlug = Object.fromEntries(status.rows.map(r => [r.slug, r.state]));
      return json(res, 200, {
        groups: L.ORG_GROUPS.map(([dept, slugs]) => ({
          dept,
          members: slugs.map(s => ({
            slug: s, ...(who[s] || { name: s, title: '', trait: '' }),
            state: stateBySlug[s] || null,
            ondemand: L.ONDEMAND.has(s), standby: L.STANDBY.has(s),
          })),
        })),
        external: [{ slug: 'hermes', ...(who.hermes || { name: '헤르메스', title: '야간 연구보조(로컬 상주 계약직)', trait: '' }) }],
      });
    }
    if (p === '/api/needs') return json(res, 200, L.needsView(cfg));
    if (p === '/api/needs/raw') return json(res, 200, L.pendingNeeds(cfg)); // 결정 데스크(강단아)가 정제 원료로 사용
    if (p === '/api/decisions') {
      const days = Math.min(60, Math.max(1, Number(url.searchParams.get('days') || 14)));
      return json(res, 200, L.decisionHistory(cfg, days));
    }
    if (p === '/api/handoffs') {
      const date = url.searchParams.get('date') || L.todayKST();
      return json(res, 200, L.handoffsFor(cfg, date));
    }
    if (p === '/api/events') return json(res, 200, L.eventsTail(cfg, Number(url.searchParams.get('tail') || 120)));
    if (p === '/api/schedule') {
      const meta = JSON.parse(fs.readFileSync(path.join(__dirname, 'routine-meta.json'), 'utf8'));
      const who = L.personas(cfg);
      const status = L.todayStatus(cfg);
      const stateBySlug = Object.fromEntries(status.rows.map(r => [r.slug, r.state]));
      const ros = L.roster(cfg);
      const rosBySlug = Object.fromEntries(ros.map(r => [r.slug, r]));
      const routines = L.listRoutines(cfg).map(s => ({
        slug: s,
        desc: meta[s] || '',
        who: who[s] || null,
        cadence: rosBySlug[s]?.cadence || 'manual',
        expectedAt: rosBySlug[s]?.expectedAt || '-',
        state: stateBySlug[s] || null,
        manual: !rosBySlug[s] || ['manual'].includes(rosBySlug[s]?.cadence) || rosBySlug[s]?.cadence.startsWith('recovery') || rosBySlug[s]?.cadence.startsWith('on-demand'),
      }));
      return json(res, 200, { routines });
    }
    if (p === '/api/calendar') return json(res, 200, { gcal: L.gcalSnapshot(cfg), ticktick: L.ticktickSnapshot(cfg) });
    if (p === '/api/jobs') return json(res, 200, D.listJobs());

    const jobLog = p.match(/^\/api\/jobs\/([\w-]+)\/log$/);
    if (jobLog) {
      const job = D.getJob(jobLog[1]);
      if (!job) return json(res, 404, { error: '작업 없음' });
      // SSE 라이브 테일
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive' });
      let pos = 0;
      const push = () => {
        try {
          const text = fs.readFileSync(job.logFile, 'utf8');
          if (text.length > pos) {
            res.write(`data: ${JSON.stringify(text.slice(pos))}\n\n`);
            pos = text.length;
          }
          if (job.status !== 'running') { res.write(`event: end\ndata: ${job.status}\n\n`); clearInterval(iv); res.end(); }
        } catch {}
      };
      const iv = setInterval(push, 1000);
      push();
      req.on('close', () => clearInterval(iv));
      return;
    }

    if (req.method === 'POST' && p === '/api/decide') {
      const b = await readBody(req);
      // 결정 데스크 통합 항목은 keys 배열로 오며, 출처 원문 전부를 한 번에 억제한다.
      const keys = Array.isArray(b.keys) && b.keys.length ? b.keys : (b.key ? [b.key] : []);
      if (!keys.length || !b.decision) return json(res, 400, { error: 'key(s)·decision 필수' });
      for (const key of keys) recordDecision({ key, decision: b.decision, question: b.itemText, routine: b.routine || b.who });
      let job = null;
      if (b.dispatch !== false) job = D.runDecision(cfg, { who: b.who, routine: b.routine, itemText: b.itemText || keys[0], decision: b.decision });
      return json(res, 200, { recorded: true, keys: keys.length, jobId: job?.id || null });
    }
    // ── 대표님 피드백: 오늘 우선순위 항목 · 직원(루틴) 지시 (2026-08-04) ──
    // 기록 즉시 화면에 반영하고, 실제 이행은 claude 헤드리스가 그 자리에서 처리한다.
    if (req.method === 'POST' && p === '/api/feedback') {
      const b = await readBody(req);
      const kind = b.kind === 'routine' ? 'routine' : 'priority';
      const text = (b.text || '').trim();
      const action = b.action || (kind === 'routine' ? '지시' : '메모');
      if (kind === 'priority' && !b.key) return json(res, 400, { error: 'key 필수' });
      if (kind === 'routine' && !b.slug) return json(res, 400, { error: 'slug 필수' });
      if (action !== '완료' && !text) return json(res, 400, { error: '내용을 적어 주세요' });

      const job = D.runFeedback(cfg, { kind, slug: b.slug, label: b.label || '', action, text });
      const rec = { action, text, label: b.label || '', ts: new Date().toISOString(), jobId: job?.id || null };

      if (kind === 'priority') recordPriorityFeedback(b.key, rec);
      fs.mkdirSync(cfg.queueDir, { recursive: true });
      fs.appendFileSync(path.join(cfg.queueDir, 'feedback.jsonl'),
        JSON.stringify({ kind, key: b.key || null, slug: b.slug || null, ...rec }) + '\n');
      return json(res, 200, { ok: true, jobId: job?.id || null, feedback: rec });
    }

    const run = p.match(/^\/api\/run\/([a-z0-9-]+)$/);
    if (req.method === 'POST' && run) {
      const b = await readBody(req).catch(() => ({}));
      const job = D.runRoutine(cfg, run[1], { force: !!b.force });
      if (job.error) return json(res, 409, job);
      return json(res, 200, { jobId: job.id });
    }
    if (req.method === 'POST' && p === '/api/ask') {
      const b = await readBody(req);
      if (!b.prompt) return json(res, 400, { error: 'prompt 필수' });
      const engine = b.engine || 'claude';
      let job;
      if (engine === 'claude') job = D.runClaude(cfg, { prompt: b.prompt, label: `질의: ${b.prompt.slice(0, 40)}` });
      else if (engine === 'hermes') job = D.runHermes(cfg, { slug: b.slug || 'web-ask', prompt: b.prompt });
      else if (engine === 'openai') job = await D.runOpenAI(cfg, { prompt: b.prompt });
      else if (engine === 'gemini') job = await D.runGemini(cfg, { prompt: b.prompt });
      else return json(res, 400, { error: `알 수 없는 엔진: ${engine}` });
      return json(res, 200, { jobId: job.id });
    }
    if (req.method === 'POST' && p === '/api/notify') {
      const b = await readBody(req);
      const ok = D.notifyDiscord(cfg, b.message || '(빈 메시지)');
      return json(res, ok ? 200 : 500, { sent: ok });
    }

    // ── 정적 파일 ──
    let file = p === '/' ? '/index.html' : p;
    const fp = path.join(ROOT, 'ui', path.normalize(file).replace(/^(\.\.[\/\\])+/, ''));
    if (fp.startsWith(path.join(ROOT, 'ui')) && fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      res.writeHead(200, { 'Content-Type': MIME[path.extname(fp)] || 'application/octet-stream' });
      return res.end(fs.readFileSync(fp));
    }
    json(res, 404, { error: 'not found' });
  } catch (e) {
    console.error(e);
    json(res, 500, { error: e.message });
  }
});

server.listen(cfg.port, cfg.bind, () => {
  console.log(`agentops-web 서버 가동: http://${cfg.bind}:${cfg.port} (${new Date().toISOString()})`);
});
