// Shared helpers for Svoi Pravila k6 load tests. Payloads are synthetic; no real user text.
import http from 'k6/http';
import { check } from 'k6';

export const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
export const USER_PREFIX = __ENV.USER_PREFIX || 'loadtest-user-';
export const USERS = parseInt(__ENV.USERS || '50', 10);

const TEXTS = {
  soften: ['Ты опять опоздал, меня это злит.', 'Почему ты снова не позвонил?', 'Ты вообще меня не слушаешь.'],
  decode: ['Ну ладно, делай как хочешь.', 'Мне всё равно, решай сам.', 'Хорошо. Понятно.'],
  'help-say': ['Хочу сказать, что мне обидно, когда ты не отвечаешь.', 'Хочу попросить больше времени вместе.', 'Хочу сказать, что устал от ссор.'],
};

// soften 40%, help-say 40%, decode 20%
export function pickWorkflow() {
  const r = Math.random();
  if (r < 0.4) return 'soften';
  if (r < 0.8) return 'help-say';
  return 'decode';
}

export function assist(level) {
  const workflow = pickWorkflow();
  const texts = TEXTS[workflow];
  const body = JSON.stringify({
    user_id: `${USER_PREFIX}${Math.floor(Math.random() * USERS)}`,
    text: texts[Math.floor(Math.random() * texts.length)],
    language: 'ru',
  });
  const res = http.post(`${BASE_URL}/v1/assist/${workflow}`, body, {
    headers: { 'Content-Type': 'application/json' },
    tags: { level: String(level), workflow },
    timeout: __ENV.HTTP_TIMEOUT || '120s',
  });
  let status = 'http_error';
  if (res.status === 200) {
    try { status = res.json('status'); } catch (e) { status = 'bad_json'; }
  }
  check(res, { 'http 200': (r) => r.status === 200, 'workflow ok': () => status === 'ok' }, { level: String(level) });
  return res;
}

// Build sequential constant-arrival-rate scenarios: [{rps, duration(s)}]
export function stagedScenarios(levels, gapSeconds = 5, maxVUs = 200) {
  const scenarios = {};
  const thresholds = {};
  let offset = 0;
  for (const { rps, duration } of levels) {
    const name = `rps_${String(rps).replace('.', '_')}`;
    const isFractional = rps < 1;
    scenarios[name] = {
      executor: 'constant-arrival-rate',
      rate: isFractional ? Math.round(rps * 60) : rps,
      timeUnit: isFractional ? '1m' : '1s',
      duration: `${duration}s`,
      preAllocatedVUs: Math.min(maxVUs, Math.max(2, Math.ceil(rps * 4))),
      maxVUs,
      startTime: `${offset}s`,
      exec: 'run',
      env: { LEVEL: String(rps) },
      tags: { level: String(rps) },
    };
    offset += duration + gapSeconds;
    // Dummy thresholds force per-level sub-metrics into the summary.
    thresholds[`http_req_duration{level:${rps}}`] = ['max>=0'];
    thresholds[`http_req_failed{level:${rps}}`] = ['rate>=0'];
    thresholds[`http_reqs{level:${rps}}`] = ['count>=0'];
    thresholds[`checks{level:${rps}}`] = ['rate>=0'];
  }
  return { scenarios, thresholds };
}

export const SUMMARY_TREND_STATS = ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'];
