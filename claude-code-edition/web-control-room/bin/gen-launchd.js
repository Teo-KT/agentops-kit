// gen-launchd.js — AGENTS.md §1 로스터에서 launchd plist를 생성한다.
// 생성만 하고 설치하지 않는다(설치는 install-launchd.sh, 카나리아→전체 순).
// every2days는 launchd로 매일 트리거하되 디스패처의 dedup(어제·오늘 핸드오프 존재 시 스킵)이 격일을 보장한다.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { roster } from '../server/ledger.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config.json'), 'utf8'));
const OUT = path.join(ROOT, 'launchd');
const HOME = process.env.HOME;

const DOW = { 'weekly:sun': 0, 'weekly:mon': 1, 'weekly:tue': 2, 'weekly:wed': 3, 'weekly:thu': 4, 'weekly:fri': 5, 'weekly:sat': 6 };

// times: [{hour, minute}, …] — 하루 여러 회차면 StartCalendarInterval을 dict 배열로 낸다.
function plist(slug, times, weekday) {
  const one = t => {
    const rows = [`      <key>Hour</key><integer>${t.hour}</integer>`, `      <key>Minute</key><integer>${t.minute}</integer>`];
    if (weekday !== null) rows.push(`      <key>Weekday</key><integer>${weekday}</integer>`);
    return `    <dict>\n${rows.join('\n')}\n    </dict>`;
  };
  const cal = times.length > 1
    ? `  <array>\n${times.map(one).join('\n')}\n  </array>`
    : `  <dict>\n${one(times[0]).split('\n').slice(1, -1).join('\n')}\n  </dict>`;
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.agentops.routine.${slug}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>${HOME}/.claude/scripts/run-routine.sh</string>
    <string>${slug}</string>
  </array>
  <key>StartCalendarInterval</key>
${cal}
  <key>StandardOutPath</key><string>${HOME}/.claude/logs/launchd-${slug}.out</string>
  <key>StandardErrorPath</key><string>${HOME}/.claude/logs/launchd-${slug}.err</string>
</dict>
</plist>
`;
}

fs.mkdirSync(OUT, { recursive: true });
let n = 0;
for (const r of roster(cfg)) {
  const isDaily = r.cadence === 'daily' || r.cadence === 'every2days';
  const wd = DOW[r.cadence];
  if (!isDaily && wd === undefined) continue; // 수동·복구·호출형 제외
  // 기대시각은 'HH:MM' 하나 또는 '07:50·12:50·17:50·21:50'처럼 여러 개일 수 있다(AGENTS.md §1 표기 규칙)
  const times = (r.times || []).map(t => { const [h, mi] = t.split(':'); return { hour: Number(h), minute: Number(mi) }; });
  if (!times.length) { console.log(`시각 파싱 불가, 건너뜀: ${r.slug} (${r.expectedAt})`); continue; }
  fs.writeFileSync(path.join(OUT, `com.agentops.routine.${r.slug}.plist`), plist(r.slug, times, isDaily ? null : wd));
  n++;
}
console.log(`plist ${n}건 생성 → ${OUT}`);
