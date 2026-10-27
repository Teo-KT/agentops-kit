// AgentOps 관제 웹 UI
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const api = async (p, opt) => { const r = await fetch(p, opt); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status); return r.json(); };

const STATE_LABEL = { ok: '정상', partial: '부분', fail: '실패', missing: '결측', pending: '대기', idle: '휴무', 'ok-y': '어제 정상(격일)' };

// 페르소나 캐시 (slug → {name, title, trait})
let WHO = {};
let ORG = null;
async function loadOrg() {
  if (ORG) return ORG;
  ORG = await api('/api/org');
  for (const g of ORG.groups) for (const m of g.members) WHO[m.slug] = m;
  for (const e of ORG.external) WHO[e.slug] = e;
  return ORG;
}
const whoName = slug => WHO[slug]?.name ? `${WHO[slug].name}` : slug;
const whoFull = slug => WHO[slug]?.name ? `${WHO[slug].name} (${WHO[slug].title})` : slug;

// ── 탭 ──
$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button[data-tab]');
  if (!b) return;
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.panel').forEach(x => x.hidden = x.id !== `tab-${b.dataset.tab}`);
  LOADERS[b.dataset.tab]?.();
});

// ── 오늘 ──
// 피드백 상자: 우선순위 항목과 직원 카드 양쪽에서 같은 모양으로 쓴다 (2026-08-04)
function fbBox(kind, id, label, quick, existing) {
  const done = existing
    ? `<div class="fb-done">✅ ${esc(existing.action)}${existing.text ? ' · ' + esc(existing.text) : ''}
         <span class="mut">${esc(String(existing.ts).slice(11, 16))} 반영 요청됨</span></div>`
    : '';
  return `<div class="fb" data-kind="${kind}" data-id="${esc(id)}" data-label="${esc(label)}">
    ${done}
    <button class="fb-open">${existing ? '피드백 더 남기기' : '피드백'}</button>
    <div class="fb-box" hidden>
      <div class="fb-quick">${quick.map(q => `<button class="fbq" data-a="${esc(q)}">${esc(q)}</button>`).join('')}</div>
      <textarea class="fb-text" rows="2" placeholder="처리 결과나 바꿀 점을 적어 주세요. 담당 직원이 바로 반영합니다."></textarea>
      <div class="fb-row"><button class="fb-send">반영 요청</button><span class="fb-msg mut"></span></div>
    </div>
  </div>`;
}

async function loadToday() {
  const o = await api('/api/overview');
  $('#mast-date').textContent = o.date;
  $('#mast-tally').textContent = `정상 ${o.tally.ok} · 부분 ${o.tally.partial} · 실패 ${o.tally.fail} · 결측 ${o.tally.missing}`;
  $('#needs-badge').textContent = o.needsCount || '';
  const pr = o.priorities;
  let html = '';
  if (pr) {
    const stale = pr.date !== o.date;
    html += `<div class="card">${stale ? `<div class="overload">이 우선순위는 ${esc(pr.date)} 기준입니다 (오늘 브리핑 미갱신).</div>` : ''}
      <p class="headline">${esc(pr.headline)}</p>
      ${pr.overload ? `<div class="overload">${esc(pr.overload)}</div>` : ''}
      ${(pr.sections || []).map(s => `<div class="prio-sec"><h3>${esc(s.title)}</h3><ol>
        ${s.items.map(i => `<li class="${i.urgent ? 'urgent' : ''}${i.feedback ? ' fb-marked' : ''}"><b>${esc(i.label)}</b>
          ${i.due ? `<span class="chip due">${esc(i.due)}</span>` : ''}${i.eta ? `<span class="chip">${esc(i.eta)}</span>` : ''}
          ${i.why ? `<p class="why">${esc(i.why)}</p>` : ''}
          ${fbBox('priority', i.key, i.label, ['완료', '이미 끝냄', '오늘 안 함', '내용 수정'], i.feedback)}</li>`).join('')}
      </ol></div>`).join('')}
      <p class="mut" style="font-size:.72rem">피드백은 즉시 기록되고 담당 직원에게 전달됩니다. 완료 표시한 항목은 내일 우선순위에서 빠집니다.</p></div>`;
  }
  const SEV = { missing: 0, fail: 1, partial: 2, pending: 3, ok: 4, 'ok-y': 5, idle: 6 };
  const sorted = [...o.routines].sort((a, b) => (SEV[a.state] ?? 9) - (SEV[b.state] ?? 9));
  html += `<div class="card"><h2>직원(루틴) 상태 — 문제 순 정렬</h2><div class="grid">
    ${sorted.map(r => `<div class="rcard s-${r.state}">
      <header><b>${esc(r.who?.name || r.slug)}</b><span class="pill ${r.state}">${STATE_LABEL[r.state] || r.state}</span></header>
      <div class="slug">${esc(r.who?.title || '')} · ${esc(r.slug)}</div>
      <div class="slug">${esc(r.cadence)} · 기대 ${esc(r.expectedAt)}${r.runAt ? ` · 보고 ${esc(String(r.runAt).slice(11, 16))}` : ''}</div>
      ${r.findings[0] ? `<p class="find">${esc(r.findings[0].slice(0, 140))}</p>` : ''}
      ${fbBox('routine', r.slug, r.who?.name || r.slug, ['이렇게 바꿔 주세요', '오늘은 건너뛰세요', '지금 다시 실행', '보고가 부정확합니다'], null)}
    </div>`).join('')}</div>
    <p class="mut" style="font-size:.72rem">지시는 해당 직원의 다음 실행이 읽도록 핸드오프로 전달됩니다. 즉시 실행이 필요하면 그 자리에서 재디스패치합니다.</p></div>`;
  $('#tab-today').innerHTML = html;
  bindFeedback();
}

function bindFeedback() {
  $('#tab-today').onclick = async e => {
    const wrap = e.target.closest('.fb');
    if (!wrap) return;
    const box = wrap.querySelector('.fb-box');
    const ta = wrap.querySelector('.fb-text');
    const msg = wrap.querySelector('.fb-msg');

    if (e.target.closest('.fb-open')) { box.hidden = !box.hidden; if (!box.hidden) ta.focus(); return; }
    const q = e.target.closest('.fbq');
    if (q) {
      // '완료'류는 본문 없이 바로 보낼 수 있고, 나머지는 문장을 받아야 의미가 있다
      wrap.dataset.action = q.dataset.a;
      wrap.querySelectorAll('.fbq').forEach(b => b.classList.toggle('on', b === q));
      if (!/완료|끝냄|건너뛰|다시 실행/.test(q.dataset.a)) ta.focus();
      return;
    }
    if (!e.target.closest('.fb-send')) return;

    const action = wrap.dataset.action || (wrap.dataset.kind === 'routine' ? '지시' : '메모');
    const text = ta.value.trim();
    if (!text && !/완료|끝냄|건너뛰|다시 실행/.test(action)) {
      msg.textContent = '내용을 적거나 위 버튼을 골라 주세요.'; msg.className = 'fb-msg err'; return;
    }
    msg.textContent = '기록·전달 중…'; msg.className = 'fb-msg';
    const body = wrap.dataset.kind === 'routine'
      ? { kind: 'routine', slug: wrap.dataset.id, label: wrap.dataset.label, action, text }
      : { kind: 'priority', key: wrap.dataset.id, label: wrap.dataset.label, action, text };
    try {
      const r = await api('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      msg.textContent = `✅ 전달했습니다. 작업 #${r.jobId || '없음'} (실행·작업 탭에서 로그 확인)`;
      msg.className = 'fb-msg good';
      ta.value = '';
      setTimeout(() => { box.hidden = true; loadToday(); }, 1600);
    } catch (err) { msg.textContent = `오류: ${err.message}`; msg.className = 'fb-msg err'; }
  };
}

// ── 결정 ──
function needCard(it, i) {
  return `<div class="need" data-i="${i}">
      <div class="need-head">
        <span class="need-who">${esc(it.whoLabel)}</span>
        <span class="mut" style="font-size:.75rem">${esc(it.meta || '')}</span>
      </div>
      <p class="need-text">${esc(it.text)}</p>
      ${it.recommend ? `<p class="mut" style="font-size:.82rem;margin:2px 0 0">권고: ${esc(it.recommend)}</p>` : ''}
      ${it.context ? `<details><summary class="mut" style="cursor:pointer;font-size:.78rem">배경 보기</summary><p class="mut" style="font-size:.84rem;white-space:pre-wrap">${esc(it.context)}</p></details>` : ''}
      <div class="qrow">
        <button class="qk" data-d="승인합니다">승인합니다</button>
        <button class="qk" data-d="권고안대로 진행하세요">권고안대로</button>
        <button class="qk" data-d="보류합니다">보류</button>
        <button class="qk danger" data-d="진행하지 마세요">진행 안 함</button>
        <button class="qk" data-d="__custom__">직접 입력…</button>
      </div>
      <div class="row" hidden><input type="text" placeholder="결정 내용"><button class="primary">등록</button></div>
      <span class="mut" style="font-size:.78rem" data-msg></span>
    </div>`;
}
// 결정 이력 — 그날 내가 무엇을 결정했고 그것이 어떻게 처리됐는지 (2026-08-03 대표님 지시)
let HIST = { days: 7, data: null, open: null };
const KO_DOW = ['일', '월', '화', '수', '목', '금', '토'];
function histDateLabel(d) {
  const today = kstToday();
  const dow = KO_DOW[new Date(d + 'T00:00:00Z').getUTCDay()];
  if (d === today) return `오늘 (${d} ${dow})`;
  if (d === addDays(today, -1)) return `어제 (${d} ${dow})`;
  return `${d} (${dow})`;
}
function histRow(it, id) {
  const BADGE = {
    done: '<span class="hchip done">처리 완료</span>',
    logged: '<span class="hchip logged">처리 기록 있음</span>',
    unknown: '<span class="hchip wait">처리 확인 안 됨</span>',
  };
  const badge = BADGE[it.state] || BADGE.unknown;
  return `<div class="hitem${HIST.open === id ? ' open' : ''}" data-h="${id}">
    <div class="hline">
      <span class="htime">${esc(it.time || '--:--')}</span>
      <span class="hq">${esc(it.question)}</span>
      ${badge}
    </div>
    <div class="hdec"><b>내 결정</b> ${esc(it.decision)}</div>
    <div class="hmeta mut">${esc(it.source)}${it.routine ? ' · 요청 ' + esc(it.routine) : ''}${it.docKind ? ' · ' + esc(it.docKind) : ''}</div>
    ${it.result ? `<div class="hbody"><b>처리 결과</b><p>${esc(it.result)}</p>
      ${it.docTitle ? `<p class="mut" style="font-size:.74rem">기록: ${esc(it.docTitle)}</p>` : ''}</div>` : ''}
  </div>`;
}
async function loadHistory() {
  const box = $('#dec-hist');
  if (!box) return;
  box.innerHTML = '<p class="empty">결정 이력을 불러오는 중…</p>';
  try { HIST.data = await api(`/api/decisions?days=${HIST.days}`); }
  catch (e) { box.innerHTML = `<p class="empty">이력을 불러오지 못했습니다: ${esc(e.message)}</p>`; return; }
  const dates = HIST.data.dates || [];
  const total = dates.reduce((n, d) => n + d.items.length, 0);
  if (!total) {
    box.innerHTML = `<p class="empty">최근 ${HIST.days}일 사이 기록된 결정이 없습니다.</p>`;
    return;
  }
  box.innerHTML = dates.map((d, di) => `<div class="hday">
      <h3>${esc(histDateLabel(d.date))} <span class="mut">${d.items.length}건</span></h3>
      ${d.items.map((it, ii) => histRow(it, `${di}-${ii}`)).join('')}
    </div>`).join('');
}
function historyShell() {
  return `<div class="card" id="hist-card">
    <h2>결정 이력 — 내가 내린 결정과 그 뒤 처리</h2>
    <div class="hbar">
      ${[1, 7, 14, 30].map(d => `<button class="hrange${HIST.days === d ? ' on' : ''}" data-days="${d}">${d === 1 ? '오늘' : `최근 ${d}일`}</button>`).join('')}
      <button class="ghost" id="hist-reload" style="margin-left:auto">새로고침</button>
    </div>
    <div id="dec-hist"></div>
  </div>`;
}
function bindHistory() {
  const card = $('#hist-card');
  if (!card) return;
  card.onclick = e => {
    const r = e.target.closest('.hrange');
    if (r) { HIST.days = Number(r.dataset.days); HIST.open = null; renderHistoryShell(); loadHistory(); return; }
    if (e.target.closest('#hist-reload')) { loadHistory(); return; }
    const it = e.target.closest('.hitem');
    if (it) { HIST.open = HIST.open === it.dataset.h ? null : it.dataset.h; it.classList.toggle('open'); }
  };
}
function renderHistoryShell() {
  const card = $('#hist-card');
  if (card) card.outerHTML = historyShell();
  bindHistory();
}

async function loadNeeds() {
  const [, nv] = await Promise.all([loadOrg(), api('/api/needs')]);
  const curated = nv.curated || [], fresh = nv.fresh || [];
  const total = curated.length + fresh.length;
  $('#needs-badge').textContent = total || '';
  if (!total) {
    $('#tab-needs').innerHTML = '<div class="card"><p class="empty">결정 대기가 없습니다 🎉</p></div>' + historyShell();
    bindHistory(); loadHistory();
    return;
  }
  // 통합 항목 배열: 정제본(keys 여러 개, 결정 시 출처 전부 억제) + 정제 전 신규(key 1개)
  const items = [
    ...curated.map(c => ({
      keys: c.liveKeys, routine: c.sources[0]?.routine, text: c.question,
      context: c.context, recommend: c.recommend,
      whoLabel: `강단아 (결정 데스크) · 출처: ${[...new Set(c.sources.map(s => whoName(s.routine)))].join(' · ')}`,
      meta: c.due ? `시한 ${c.due}` : '',
    })),
    ...fresh.map(n => ({
      keys: [n.key], routine: n.routine, text: n.text,
      whoLabel: whoFull(n.routine), meta: n.date,
    })),
  ];
  const curatedHtml = curated.length
    ? `<div class="card"><h2>결정 대기 ${curated.length}건 — 강단아가 중복 통합·검증했습니다. 버튼을 누르면 출처 전부가 함께 처리됩니다</h2>
        ${curated.map((c, i) => needCard(items[i], i)).join('')}</div>` : '';
  const freshHtml = fresh.length
    ? `<div class="card"><h2>정제 전 신규 ${fresh.length}건 — 데스크 정리 이후 올라온 원문입니다 (다음 새벽 통합)</h2>
        ${fresh.map((n, j) => needCard(items[curated.length + j], curated.length + j)).join('')}</div>` : '';
  $('#tab-needs').innerHTML = curatedHtml + freshHtml +
    (nv.curatedAt ? `<p class="mut" style="font-size:.72rem">결정 데스크 정리 시각: ${esc(nv.curatedAt)}</p>` : '') +
    historyShell();
  bindHistory();
  loadHistory();
  $('#tab-needs').onclick = async e => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const wrap = e.target.closest('.need');
    if (!wrap) return;
    const n = items[Number(wrap.dataset.i)];
    let decision = btn.dataset.d;
    if (decision === '__custom__') { wrap.querySelector('.row').hidden = false; wrap.querySelector('input').focus(); return; }
    if (btn.classList.contains('primary')) decision = wrap.querySelector('input').value.trim();
    if (!decision) return;
    const msg = wrap.querySelector('[data-msg]');
    msg.textContent = '기록·디스패치 중…';
    try {
      const r = await api('/api/decide', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: n.keys, who: n.routine, routine: n.routine, itemText: n.text, decision }) });
      msg.textContent = `✅ 기록됨(억제 ${r.keys}건). 즉시 처리 작업 #${r.jobId || '없음'} 시작 (실행·작업 탭에서 로그 확인)`;
      wrap.querySelectorAll('.qk').forEach(b => b.disabled = true);
      // 방금 내린 결정을 이력에 즉시 반영하고, 당직 처리가 끝날 무렵 한 번 더 갱신한다
      loadHistory();
      setTimeout(loadHistory, 90000);
    } catch (err) { msg.textContent = `오류: ${err.message}`; }
  };
}

// ── 직원 (조직도 + 오늘 보고) ──
async function loadStaff() {
  const [org, hs] = await Promise.all([loadOrg(), api('/api/handoffs')]);
  const orgHtml = `<div class="card"><h2>조직도 — AgentOps</h2>
    <div class="org-head"><b>대표님</b> <span class="mut"><!-- 이름·직함을 여기서 바꾼다 --></span></div>
    <div class="org-grid">
      ${org.groups.map(g => `<div class="org-dept"><h3>${esc(g.dept)}</h3><ul>
        ${g.members.map(m => `<li>${m.state ? `<span class="odot s-${m.state}"></span>` : ''}<b>${esc(m.name)}</b> <span class="mut">${esc(m.title)}</span>
          ${m.ondemand ? '<span class="tagchip call">호출형</span>' : ''}${m.standby ? '<span class="tagchip off">대기</span>' : ''}
          <span class="org-slug">${esc(m.slug)}</span>
          ${m.trait ? `<span class="trait">${esc(m.trait)}</span>` : ''}</li>`).join('')}
      </ul></div>`).join('')}
      <div class="org-dept org-ext"><h3>외부 인력</h3><ul>
        ${org.external.map(e => `<li><b>${esc(e.name)}</b> <span class="mut">${esc(e.title)}</span><span class="org-slug">${esc(e.slug)}</span></li>`).join('')}
      </ul></div>
    </div></div>`;
  const cards = hs.length ? hs.map(h => `<div class="card">
    <header style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">
      <span><b>${esc(whoName(h.routine))}</b> <span class="mut" style="font-size:.8rem">${esc(WHO[h.routine]?.title || '')} · ${esc(h.routine)}</span></span>
      <span class="pill ${esc(h.status)}">${STATE_LABEL[h.status] || esc(h.status)}</span></header>
    <div class="slug mut" style="font-size:.76rem">${esc(h.run_at || '')}</div>
    ${(h.key_findings || []).map(f => `<p class="find" style="font-size:.86rem">· ${esc(f)}</p>`).join('')}
    ${h._bodyHead ? `<details><summary class="mut" style="cursor:pointer;font-size:.8rem">보고 전문 보기</summary><p style="white-space:pre-wrap;font-size:.86rem">${esc(h._bodyHead)}</p></details>` : ''}
  </div>`).join('') : '<div class="card"><p class="empty">오늘 핸드오프가 아직 없습니다</p></div>';
  $('#tab-staff').innerHTML = orgHtml + '<h2 style="font-size:.8rem;color:var(--mut);margin:18px 0 10px;text-transform:uppercase;letter-spacing:.08em">오늘 보고</h2>' + cards;
}

// ── 실행·작업 ──
let jobTimer = null;
function runCard(r) {
  return `<div class="runcard ${r.state ? 's-' + r.state : ''}">
    <div class="runcard-main">
      <b>${esc(r.who?.name || r.slug)}</b> <span class="mut" style="font-size:.8rem">${esc(r.who?.title || '')}</span>
      ${r.state ? `<span class="pill ${r.state}" style="margin-left:6px">${STATE_LABEL[r.state] || r.state}</span>` : ''}
      <div class="rundesc">${esc(r.desc || '')}</div>
      <div class="slug">${esc(r.slug)} · ${esc(r.cadence)}${r.expectedAt !== '-' ? ` · 기대 ${esc(r.expectedAt)}` : ''}</div>
    </div>
    <button class="ghost" data-run="${esc(r.slug)}">▶ 실행</button>
  </div>`;
}
async function loadRun() {
  const [sched, jobs] = await Promise.all([api('/api/schedule'), api('/api/jobs'), loadOrg()]);
  const regular = sched.routines.filter(r => !r.manual);
  const manual = sched.routines.filter(r => r.manual);
  $('#tab-run').innerHTML = `
    <div class="card"><h2>정규 루틴 (${regular.length}) — 오늘 이미 보고한 루틴은 중복 방지로 거부됩니다</h2>
      ${regular.map(runCard).join('')}
      <span class="mut" style="font-size:.78rem" id="run-msg"></span></div>
    <div class="card"><h2>수동·복구 (${manual.length})</h2>
      ${manual.map(runCard).join('')}</div>
    <div class="card"><h2>작업 (최근)</h2><div id="job-list">${renderJobs(jobs)}</div><pre class="joblog" id="job-log" hidden></pre></div>`;
  $('#tab-run').onclick = async e => {
    const runBtn = e.target.closest('[data-run]');
    if (runBtn) {
      const slug = runBtn.dataset.run;
      if (!confirm(`${whoFull(slug)} 의 루틴(${slug})을 지금 실행할까요?`)) return;
      $('#run-msg').textContent = `${whoName(slug)} 실행 요청…`;
      runBtn.disabled = true;
      try {
        const r = await api(`/api/run/${slug}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        $('#run-msg').textContent = `▶ ${whoName(slug)} 작업 #${r.jobId} 시작 — 아래 로그에서 실시간 확인`;
        watchJob(r.jobId);
        setTimeout(loadRunJobsOnly, 500);
      } catch (err) {
        $('#run-msg').textContent = `⚠ ${err.message}`;
        runBtn.disabled = false;
      }
    }
    const logBtn = e.target.closest('[data-log]');
    if (logBtn) watchJob(logBtn.dataset.log);
  };
  clearInterval(jobTimer);
  jobTimer = setInterval(loadRunJobsOnly, 5000);
}
const JOB_STATE = { running: '실행 중', done: '완료', fail: '실패' };
function renderJobs(jobs) {
  if (!jobs.length) return '<p class="empty">아직 실행한 작업이 없습니다. 위에서 루틴을 실행하거나, 결정·질의 탭에서 작업을 만들면 여기에 쌓입니다.</p>';
  return jobs.slice(0, 20).map(j => {
    const dur = j.endedAt ? `${Math.max(1, Math.round((new Date(j.endedAt) - new Date(j.startedAt)) / 1000))}초` : '진행 중';
    return `<div class="jobrow"><span class="dot ${j.status === 'running' ? 'running' : j.status}"></span>
    <b>${esc(j.label)}</b><span class="mut" style="font-size:.76rem">${esc(j.engine)} · ${JOB_STATE[j.status] || esc(j.status)} · ${dur}</span>
    <span class="mut" style="margin-left:auto;font-size:.76rem">${esc((j.startedAt || '').slice(11, 19))}</span>
    <button class="ghost" data-log="${esc(j.id)}">로그</button></div>`;
  }).join('');
}
async function loadRunJobsOnly() {
  if ($('#tab-run').hidden) { clearInterval(jobTimer); return; }
  const jobs = await api('/api/jobs').catch(() => null);
  if (jobs && $('#job-list')) $('#job-list').innerHTML = renderJobs(jobs);
}
let es = null;
function watchJob(id) {
  const log = $('#job-log') || $('#ask-log');
  log.hidden = false; log.textContent = `— 작업 ${id} 로그 —\n`;
  es?.close();
  es = new EventSource(`/api/jobs/${id}/log`);
  es.onmessage = e => { log.textContent += JSON.parse(e.data); log.scrollTop = log.scrollHeight; };
  es.addEventListener('end', e => { log.textContent += `\n— 종료: ${e.data} —`; es.close(); });
}

// ── 일정 (일/주/월 뷰) ──
function dueKST(iso) {
  // TickTick due는 UTC 표기이므로 KST로 환산해 날짜만 취한다
  const d = new Date(iso);
  return new Date(d.getTime() + 9 * 3600e3).toISOString().slice(0, 10);
}
const kstToday = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
const addDays = (isoDate, n) => {
  const d = new Date(isoDate + 'T00:00:00Z');
  return new Date(d.getTime() + n * 86400e3).toISOString().slice(0, 10);
};
const DOW_KO = ['일', '월', '화', '수', '목', '금', '토'];
const dowOf = iso => new Date(iso + 'T00:00:00Z').getUTCDay();

let CAL = { events: null, tt: null, mode: 'week', anchor: kstToday(), fetched: '' };

async function loadCal() {
  if (!CAL.events) {
    const c = await api('/api/calendar');
    CAL.events = [...(c.gcal?.events || [])];
    CAL.fetched = (c.gcal?.fetched_at || '').slice(5, 16);
    // TickTick 마감을 캘린더 항목으로 병합 (📋 마커)
    CAL.tt = [];
    for (const p of c.ticktick?.projects || []) {
      for (const t of p.tasks || []) {
        if (!t.dueDate) continue;
        const due = dueKST(t.dueDate);
        CAL.tt.push({ d: due, title: t.title, project: p.name, pr: t.priority || 0 });
        CAL.events.push({ d: due, t: null, title: `📋 ${t.title}`, cal: p.name });
      }
    }
  }
  renderCal();
}

function calEventsOn(date) {
  return CAL.events.filter(e => e.d === date).sort((a, b) => (a.t || '') < (b.t || '') ? -1 : 1);
}
function calItemHtml(e, compact = false) {
  return `<div class="cal-item"><span class="cal-time">${esc(e.t || '종일')}</span><span class="cal-title">${esc(compact ? e.title.slice(0, 26) : e.title)}</span></div>`;
}

function renderCal() {
  const today = kstToday();
  let title = '', body = '';

  if (CAL.mode === 'day') {
    const d = CAL.anchor;
    title = `${d} (${DOW_KO[dowOf(d)]})${d === today ? ' · 오늘' : ''}`;
    const evs = calEventsOn(d);
    body = evs.length
      ? `<div class="cal-day ${d === today ? 'today' : ''}">${evs.map(e =>
          `<div class="cal-item"><span class="cal-time">${esc(e.t || '종일')}</span><span>${esc(e.title)}</span><span class="mut" style="margin-left:auto;font-size:.72rem">${esc(e.cal || '')}</span></div>`).join('')}</div>`
      : '<p class="empty">이 날 일정이 없습니다</p>';
  }

  if (CAL.mode === 'week') {
    const start = addDays(CAL.anchor, -((dowOf(CAL.anchor) + 6) % 7)); // 월요일 시작
    const end = addDays(start, 6);
    title = `${start.slice(5)} ~ ${end.slice(5)}`;
    body = `<div class="cal-week">${[...Array(7)].map((_, i) => {
      const d = addDays(start, i);
      const evs = calEventsOn(d);
      return `<div class="cal-col ${d === today ? 'today' : ''}">
        <div class="cal-head">${d.slice(5)} (${DOW_KO[dowOf(d)]})</div>
        ${evs.map(e => calItemHtml(e, true)).join('') || '<div class="mut" style="font-size:.7rem;text-align:center">-</div>'}
      </div>`;
    }).join('')}</div>`;
  }

  if (CAL.mode === 'month') {
    const ym = CAL.anchor.slice(0, 7);
    title = `${ym.slice(0, 4)}년 ${Number(ym.slice(5))}월`;
    const first = ym + '-01';
    const gridStart = addDays(first, -((dowOf(first) + 6) % 7));
    let cells = '';
    for (let i = 0; i < 42; i++) {
      const d = addDays(gridStart, i);
      if (i === 35 && d.slice(0, 7) !== ym) break;
      const evs = calEventsOn(d);
      cells += `<div class="cal-mcell ${d.slice(0, 7) !== ym ? 'out' : ''} ${d === today ? 'today' : ''}">
        <div class="cal-mnum">${Number(d.slice(8))}</div>
        ${evs.slice(0, 3).map(e => `<div class="cal-mev">${esc((e.t ? e.t + ' ' : '') + e.title).slice(0, 16)}</div>`).join('')}
        ${evs.length > 3 ? `<div class="cal-more">+${evs.length - 3}</div>` : ''}
      </div>`;
    }
    body = `<div class="cal-month">${DOW_KO.slice(1).concat(DOW_KO[0]).map(w => `<div class="cal-dow">${w}</div>`).join('')}${cells}</div>`;
  }

  // 하단: 임박 마감 리스트 (TickTick)
  const ttRows = CAL.tt.map(t => ({ ...t, delta: Math.round((new Date(t.d) - new Date(today)) / 86400e3) }))
    .filter(t => t.delta >= -7 && t.delta <= 45).sort((a, b) => a.delta - b.delta || b.pr - a.pr);
  const ttHtml = ttRows.length ? `<div class="card"><h2>임박 마감 (TickTick)</h2>
    ${ttRows.slice(0, 20).map(t => {
      const cls = t.delta < 0 ? 'over' : t.delta <= 7 ? 'soon' : 'far';
      const mark = t.delta < 0 ? `D+${-t.delta}` : t.delta === 0 ? 'D-DAY' : `D-${t.delta}`;
      return `<div class="tt-item"><span class="dmark ${cls}">${mark}</span><span>${esc(t.title)}</span><span class="mut" style="font-size:.74rem;margin-left:auto;white-space:nowrap">${esc(t.d.slice(5))} · ${esc(t.project)}</span></div>`;
    }).join('')}</div>` : '';

  $('#tab-cal').innerHTML = `<div class="card">
    <div class="sv-bar">
      <div class="sv-modes">
        <button class="svm" data-mode="day" aria-pressed="${CAL.mode === 'day'}">일</button>
        <button class="svm" data-mode="week" aria-pressed="${CAL.mode === 'week'}">주</button>
        <button class="svm" data-mode="month" aria-pressed="${CAL.mode === 'month'}">월</button>
      </div>
      <div class="sv-nav">
        <button data-nav="-1">◀</button>
        <button data-nav="0">오늘</button>
        <button data-nav="1">▶</button>
        <b id="sv-title" style="font-size:.9rem;margin-left:6px">${esc(title)}</b>
      </div>
      <span class="mut" style="font-size:.72rem;margin-left:auto">📋=TickTick · 스냅샷 ${esc(CAL.fetched)}</span>
    </div>
    ${body}
  </div>${ttHtml}`;

  $('#tab-cal').onclick = e => {
    const m = e.target.closest('[data-mode]');
    if (m) { CAL.mode = m.dataset.mode; renderCal(); return; }
    const n = e.target.closest('[data-nav]');
    if (n) {
      const step = Number(n.dataset.nav);
      if (step === 0) CAL.anchor = kstToday();
      else if (CAL.mode === 'day') CAL.anchor = addDays(CAL.anchor, step);
      else if (CAL.mode === 'week') CAL.anchor = addDays(CAL.anchor, step * 7);
      else {
        const [y, mo] = CAL.anchor.slice(0, 7).split('-').map(Number);
        const nm = new Date(Date.UTC(y, mo - 1 + step, 1));
        CAL.anchor = nm.toISOString().slice(0, 10);
      }
      renderCal();
    }
  };
}

// ── 질의 ──
$('#ask-send').addEventListener('click', async () => {
  const prompt = $('#ask-text').value.trim();
  if (!prompt) return;
  $('#ask-msg').textContent = '전송 중…';
  $('#ask-send').disabled = true;
  try {
    const r = await api('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, engine: $('#ask-engine').value }) });
    $('#ask-msg').textContent = `작업 #${r.jobId}`;
    watchJob(r.jobId);
  } catch (e) { $('#ask-msg').textContent = `오류: ${e.message}`; }
  $('#ask-send').disabled = false;
});

const LOADERS = { today: loadToday, needs: loadNeeds, staff: loadStaff, run: loadRun, cal: loadCal, ask: () => {} };
loadToday();
loadNeeds();
setInterval(() => { if (!$('#tab-today').hidden) loadToday(); }, 60000);
$('#foot-ts').textContent = `로드 ${new Date().toLocaleTimeString('ko-KR')}`;
