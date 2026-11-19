'use strict';
const $ = id => document.getElementById(id);
let socket, retry = 0, timer, elapsed = 0, usage = 0, rate = .15, running = false, connected = false;
let currentState = 'idle';
let quoteData, quoteTimer, quoteRequest = 0, heartbeat;
let keysReady = false;
const safetyFields = ['live-minutes', 'live-cap', 'live-answers', 'live-fx', 'live-fx-date', 'live-fx-source',
  'live-assembly-check', 'live-groq-check', 'keyterms', 'free-assembly-plan', 'free-assembly-usd',
  'free-assembly-rate', 'free-assembly-keyterms', 'free-assembly-source', 'free-assembly-protected',
  'free-groq-plan', 'free-groq-tokens', 'free-groq-requests', 'free-groq-source', 'free-groq-protected',
  'free-valid-until', 'free-exclusive', 'free-confirm-now'];
let balancesCheckedAt = null;
let interviewRan = false;
const client = sessionStorage.iaClient || (sessionStorage.iaClient = crypto.randomUUID());
const rows = new Map();
let audioAckTimer;
const audioDiagnostics = [];
let showAudioDiagnostics = false;
function hideAudioDiagnostics() {
  showAudioDiagnostics = false;
  clearTimeout(audioAckTimer);
  audioDiagnostics.length = 0;
  $('audio-diagnostic-log').replaceChildren();
  $('audio-diagnostic-summary').textContent = '';
  $('audio-diagnostics').open = false;
  $('audio-diagnostics').hidden = true;
}
function audioDiagnostic(item) {
  if (!showAudioDiagnostics || !idleMode(currentState)) return;
  clearTimeout(audioAckTimer);
  const role = item.role === 'me' ? 'Microphone' : 'System audio';
  audioDiagnostics.push(item);
  while (audioDiagnostics.length > 24) audioDiagnostics.shift();
  $('audio-diagnostic-log').replaceChildren(...audioDiagnostics.map(entry => {
    const row = document.createElement('li');
    row.textContent = (entry.role === 'me' ? 'Microphone' : 'System audio')
      + ' · ' + entry.stage + ' · ' + entry.message;
    return row;
  }));
  $('audio-diagnostic-summary').textContent = role + ' · ' + item.stage;
  if (item.stage === 'error') {
    $('audio-diagnostics').open = true;
    $(item.role === 'me' ? 'status-me' : 'status-interviewer').textContent = 'Test failed — see Audio diagnostics';
  }
}
function testAudio(role, selectId) {
  const select = $(selectId);
  const device = select.value;
  const name = select.selectedOptions[0]?.textContent || 'No device selected';
  showAudioDiagnostics = true;
  audioDiagnostics.length = 0;
  $('audio-diagnostic-log').replaceChildren();
  $('audio-diagnostic-summary').textContent = role === 'me' ? 'Microphone' : 'System audio';
  $('audio-diagnostics').hidden = false;
  $('audio-diagnostics').open = true;
  $('audio-browser-status').textContent = 'Test clicked for ' + name
    + '. Local connection: ' + (socket?.readyState === WebSocket.OPEN ? 'connected' : 'disconnected') + '.';
  if (device === '') return error('Select an input device first.');
  clearTimeout(audioAckTimer);
  audioAckTimer = setTimeout(() => {
    $('audio-browser-status').textContent += ' Backend has not acknowledged this test. Check the local connection and reload this tab.';
  }, 2000);
  command({cmd: 'test', role, device: Number(device)});
}
function error(message) { $('error').textContent = message; $('error').hidden = false; }
function command(data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return error('Local connection is unavailable. Reconnecting…');
  $('error').hidden = true;
  socket.send(JSON.stringify(data));
}
function buttons() {
  const idle = currentState === 'idle', ready = connected && idle;
  const reasons = [];
  if (!connected) reasons.push('Connect to the local backend.');
  if (!idle) reasons.push(currentState === 'testing' ? 'Wait for the audio test to finish, or press STOP ALL.' : 'A session is already active.');
  if (!keysReady) reasons.push('Configure both API keys and restart the backend.');
  if (!$('mic').value || !$('system').value) reasons.push('Select both audio inputs.');
  else if ($('mic').value === $('system').value) reasons.push('Select different microphone and system audio inputs.');
  if (!quoteData?.quote_id) {
    reasons.push(...(quoteData?.blockers?.length ? quoteData.blockers
      : ['Review Live session safety to obtain a current cost estimate and complete the billing checks.']));
  }
  $('start').disabled = reasons.length > 0;
  $('start-blocker').textContent = idle ? reasons.join(' ') : reasons[0];
  $('review-safety').hidden = !idle || Boolean(quoteData?.quote_id);
  $('test-mic').disabled = !ready || !$('mic').value;
  $('test-system').disabled = !ready || !$('system').value;
  $('stop').disabled = idle;
  $('answer').disabled = !connected || currentState !== 'running';
  $('mic').disabled = $('system').disabled = !idle;
  $('refresh').disabled = !idle;
  for (const id of safetyFields) $(id).disabled = !idle;
  for (const id of ['interview-cv','job-title','job-company','job-description','job-notes','job-research','keyterms']) $(id).disabled = !idle;
}
function clock() {
  $('duration').textContent = `${Math.floor(elapsed / 60).toString().padStart(2,'0')}:${Math.floor(elapsed % 60).toString().padStart(2,'0')}`;
  $('cost').textContent = `Estimated AssemblyAI usage: $${(usage / 3600 * rate).toFixed(4)} · ${(usage/60).toFixed(1)} channel-min`;
}
function idleMode(mode) { return mode === 'idle' || mode === 'testing'; }
function state(s) {
  currentState = s.state;
  if (!idleMode(s.state)) hideAudioDiagnostics();
  running = s.state === 'running'; elapsed = s.duration; usage = s.channel_seconds;
  if (running || s.state === 'starting') interviewRan = true;
  if (currentState === 'idle' && interviewRan) {
    interviewRan = false;
    document.dispatchEvent(new Event('interview-ended'));
    $('live-assembly-check').checked = $('live-groq-check').checked = $('free-confirm-now').checked = false;
    balancesCheckedAt = null;
    refreshQuote();
  }
  keysReady = Boolean(s.configured.assemblyai && s.configured.groq);
  $('live-mode').textContent = idleMode(s.state) ? 'LOCAL-ONLY' : 'FREE USAGE ONLY';
  $('live-minutes').max = s.max_minutes;
  rate = s.rate_usd || 0;
  $('free-session-usage').hidden = !s.free_usage;
  $('free-session-usage').textContent = s.free_usage
    ? 'Free session answer budget remaining: ' + s.free_usage.remaining_answers
      + '. Expected user payment UGX 0. Remaining token and request reservations are local estimates.'
    : '';
  clearInterval(timer);
  if (running) timer = setInterval(() => { elapsed++; usage += s.active_channels; clock(); }, 1000);
  $('notice').textContent = s.reason + (s.free_usage ? ' · ' + s.free_usage.remaining_answers + ' reserved free answers remaining · expected payment UGX 0' : '');
  $('length').value = s.length;
  $('limit').textContent = `Session limit: ${idleMode(s.state) ? $('live-minutes').value : s.limit_minutes} min`;
  $('assembly').textContent = `AssemblyAI: ${s.configured.assemblyai ? (running ? '2 streams connected' : s.state === 'starting' ? 'connecting…' : 'disconnected') : 'key missing'}`;
  $('groq').textContent = `Groq: ${s.configured.groq ? (s.groq_verified ? 'model verified' : 'not checked') : 'key missing'} · ${s.model}`;
  for (const role of ['me','interviewer']) {
    const diagnostic = (showAudioDiagnostics ? s.audio_diagnostics || [] : []).filter(item => item.role === role).at(-1);
    const label = diagnostic?.stage === 'error' ? 'Test failed — see Audio diagnostics'
      : diagnostic?.stage === 'finished' ? 'Test finished — see Audio diagnostics'
      : 'Not capturing';
    $(`status-${role}`).textContent = running ? 'Capturing'
      : s.testing && diagnostic?.stage === 'opening' ? 'Opening input…'
      : s.testing && s.sources.includes(role) ? 'Local test active' : label;
    if (!running && !s.testing) $(`level-${role}`).value = 0;
  }
  for (const item of showAudioDiagnostics ? s.audio_diagnostics || [] : []) {
    if (!audioDiagnostics.some(existing => existing === item ||
      JSON.stringify(existing) === JSON.stringify(item))) audioDiagnostic(item);
  }
  buttons(); clock();
}
function transcript(x) {
  const source = $(`transcript-${x.role}`);
  let row = rows.get(x.key);
  if (!row) {
    row = document.createElement('p');
    rows.set(x.key, row);
    source.append(row);
  }
  row.className = `utterance ${x.role} ${x.final ? '' : 'partial'} ${x.continuation ? 'continuation' : ''}`;
  row.textContent = x.text;
  while (rows.size > 122) { const key = rows.keys().next().value; rows.get(key).remove(); rows.delete(key); }
  source.scrollTop = source.scrollHeight;
}
function answer(x) {
  $('answers').querySelector('.empty')?.remove();
  $('answers').prepend(answerCard(x));
  while ($('answers').children.length > 12) $('answers').lastChild.remove();
  $('answers').scrollTop = 0;
}
function clear() { rows.clear(); $('transcript-interviewer').replaceChildren(); $('transcript-me').replaceChildren(); $('answers').replaceChildren(); }
function connect() {
  socket = new WebSocket(`ws://${location.host}/ws?client=${encodeURIComponent(client)}`);
  socket.onopen = () => {
    retry = 0;
    connected = true;
    $('connection').textContent = 'Connected locally';
    clearInterval(heartbeat);
    heartbeat = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({cmd: 'heartbeat'}));
    }, 3000);
    buttons();
  };
  socket.onmessage = e => {
    const x = JSON.parse(e.data);
    if (x.type === 'snapshot') { clear(); state(x.state); [...x.history, ...x.partials].forEach(transcript); x.answers.forEach(answer); }
    else if (x.type === 'state') state(x);
    else if (x.type === 'transcript') transcript(x);
    else if (x.type === 'answer') answer(x);
    else if (x.type === 'audio_diagnostic') audioDiagnostic(x);
    else if (x.type === 'level') $(`level-${x.role}`).value = x.value;
    else if (x.type === 'error') { clearTimeout(audioAckTimer); error(x.message); if (currentState === 'idle') refreshQuote(); }
    else if (x.type === 'clear') clear();
    else if (x.type === 'groq') { $('groq').textContent = `Groq: ${x.status}`; $('answer').disabled = x.status === 'generating' || currentState !== 'running'; }
  };
  socket.onclose = e => {
    connected = false; clearInterval(timer); clearInterval(heartbeat); $('connection').textContent = 'Disconnected'; buttons();
    if (e.code === 4001 || e.code === 1008) return error(e.reason || 'Connection refused. Reload the application in its controlling tab.');
    $('notice').textContent = 'Reconnecting to the local app. Disconnection ends the live session.';
    setTimeout(connect, Math.min(500 * 2 ** retry++, 4000));
  };
}
async function refresh() {
  try {
    const response = await fetch('/api/devices');
    const data = await response.json();
    for (const id of ['mic','system']) {
      const select = $(id), previous = select.value;
      select.replaceChildren(new Option('Select an input…', ''));
      for (const d of data.devices || []) select.add(new Option(`${d.name} · ${d.channels}ch`, String(d.id)));
      if (previous && [...select.options].some(o => o.value === previous)) select.value = previous;
    }
    const blackhole = data.devices?.find(d => /blackhole|ideashare/i.test(d.name));
    if (blackhole && !$('system').value) $('system').value = String(blackhole.id);
    const mic = data.devices?.find(d => !/blackhole|ideashare|teams|parrot/i.test(d.name));
    if (mic && !$('mic').value) $('mic').value = String(mic.id);
    if (data.error) error(data.error);
    buttons();
  } catch { error('Unable to list devices. Check the backend and reload.'); }
}
function liveOptions() {
  const number = id => $(id).value === '' ? null : $(id).valueAsNumber;
  const until = $('free-valid-until').value;
  return {
    minutes: $('live-minutes').valueAsNumber, cap_ugx: 0,
    max_answers: $('live-answers').valueAsNumber,
    fx_ugx_per_usd: $('live-fx').value || null, fx_date: $('live-fx-date').value || null,
    fx_source: $('live-fx-source').value,
    assembly_billing_checked: $('live-assembly-check').checked,
    groq_billing_checked: $('live-groq-check').checked,
    free: {
      assembly_credits_usd: $('free-assembly-usd').value || null,
      assembly_hourly_usd: $('free-assembly-rate').value || null,
      assembly_keyterms_usd: $('free-assembly-keyterms').value || null,
      assembly_plan: $('free-assembly-plan').value,
      assembly_no_paid_fallback: $('free-assembly-protected').checked,
      assembly_source: $('free-assembly-source').value,
      groq_plan: $('free-groq-plan').value,
      groq_remaining_requests: number('free-groq-requests'),
      groq_remaining_tokens: number('free-groq-tokens'),
      groq_no_paid_fallback: $('free-groq-protected').checked,
      groq_source: $('free-groq-source').value,
      checked_at: $('free-confirm-now').checked ? balancesCheckedAt : null,
      valid_until: until ? new Date(until).toISOString() : null,
      exclusive_balances: $('free-exclusive').checked,
    },
  };
}
function showQuote(data) {
  quoteData = data;
  const covered = data?.expected_payment_ugx === 0;
  const details = data ? [
    'AssemblyAI gross usage USD ' + (data.total_usd ?? 'unknown')
      + '; free-credit reserve USD ' + (data.assembly_reserved_usd ?? 'unknown')
      + '; confirmed available USD ' + (data.assembly_available_usd ?? 'unknown') + '.',
    'Groq gross usage estimate USD ' + (data.groq_gross_usd ?? 'unknown')
      + '; reserve ' + data.groq_reserved_tokens + ' tokens and ' + data.groq_reserved_requests
      + ' requests for up to ' + data.max_answers + ' answers. Confirmed available: '
      + (data.groq_available_tokens ?? 'unknown') + ' tokens, ' + (data.groq_available_requests ?? 'unknown') + ' requests.',
    ...(data.gross_transcription_ugx_reference == null ? [] : [
      'Optional gross transcription reference UGX ' + data.gross_transcription_ugx_reference
        + '; the free-only expected payment is assessed separately.']),
    covered ? 'Expected user payment UGX 0. Both free allowances cover the reserved session.'
      : 'Expected payment cannot be confirmed as UGX 0. Live session blocked.',
  ] : ['Free allowance details incomplete — live session blocked.'];
  $('live-quote').textContent = details.join(' ');
  $('live-quote').classList.toggle('safety-warning', !covered);
  $('live-price-date').textContent = data ? '· checked ' + data.pricing_date + ' · USD prices; AssemblyAI rate supplied by you' : '';
  $('live-billing').textContent = data?.account_verification
    || 'Free balances and zero-billing protection have not been confirmed for both providers.';
  const reasons = data?.blockers || ['Confirm each provider’s free balance and zero-billing protection.'];
  $('live-blockers').replaceChildren(...reasons.map(reason => {
    const item = document.createElement('li');
    item.textContent = reason;
    return item;
  }));
  if (currentState === 'idle') $('limit').textContent = 'Session limit: ' + ($('live-minutes').value || '—') + ' min';
  buttons();
}
async function refreshQuote() {
  clearTimeout(quoteTimer);
  const request = ++quoteRequest;
  const options = liveOptions();
  try {
    const response = await fetch('/api/live-quote', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({options, keyterms: $('keyterms').checked}),
    });
    const data = response.ok ? await response.json() : null;
    if (request === quoteRequest) showQuote(data);
    return request === quoteRequest ? data : null;
  } catch {
    if (request === quoteRequest) showQuote(null);
    return null;
  }
}
for (const id of safetyFields) {
  $(id).addEventListener('input', () => {
    if (id === 'free-confirm-now') {
      balancesCheckedAt = $(id).checked ? new Date().toISOString() : null;
    } else if (!['live-fx', 'live-fx-date', 'live-fx-source'].includes(id)) {
      $('free-confirm-now').checked = false;
      balancesCheckedAt = null;
    }
    ++quoteRequest;
    showQuote(null);
    clearTimeout(quoteTimer);
    quoteTimer = setTimeout(refreshQuote, 150);
  });
}
$('start').onclick = async () => {
  $('start').disabled = true;
  const data = await refreshQuote();
  if (!data?.quote_id || currentState !== 'idle' || !connected) return;
  const consentOptions = liveOptions();
  const consentKeyterms = $('keyterms').checked;
  const consentFingerprint = JSON.stringify({live: consentOptions, keyterms: consentKeyterms});
  if (!window.confirm(
    'Start this ' + data.minutes + '-minute FREE USAGE ONLY session? '
    + 'Expected user payment UGX 0, based on the balances and non-billable account protections you confirmed. '
    + 'AssemblyAI reserves USD ' + data.assembly_reserved_usd + ' of free credit for both audio streams. '
    + 'Groq reserves ' + data.groq_reserved_tokens + ' free tokens and '
    + data.groq_reserved_requests + ' requests for at most ' + data.max_answers + ' answers. '
    + 'Provider billing state is controlled by the providers. Do not change the confirmed account settings during this session.'
  )) return;
  if (consentFingerprint !== JSON.stringify({live: liveOptions(), keyterms: $('keyterms').checked})) {
    showQuote(null);
    return error('Free allowance settings changed. Review the estimate again.');
  }
  hideAudioDiagnostics();
  command({cmd: 'start', mic: Number($('mic').value), system: Number($('system').value),
    length: $('length').value, quote_id: data.quote_id, live: {...consentOptions, confirmed: true},
    interview: {
      cv_id: $('interview-cv').dataset.loaded ? $('interview-cv').value : null,
      title: $('job-title').value, company: $('job-company').value, description: $('job-description').value,
      notes: $('job-notes').value, research: $('job-research').value, keyterms: $('keyterms').checked,
    }});
  showQuote({...data, quote_id: null});
};
$('stop').onclick = async () => {
  try {
    const response = await fetch('/api/stop-all', {method: 'POST'});
    if (!response.ok) throw new Error();
  } catch {
    command({cmd: 'stop'});
  }
};
$('test-mic').onclick = () => testAudio('me', 'mic');
$('test-system').onclick = () => testAudio('interviewer', 'system');
$('answer').onclick = () => command({cmd:'answer'});
$('clear').onclick = () => command({cmd:'clear'});
$('length').onchange = () => command({cmd:'length',length:$('length').value});
$('mic').onchange = $('system').onchange = buttons;
$('refresh').onclick = refresh;
$('review-safety').onclick = () => {
  const panel = document.querySelector('.safety-panel');
  panel.open = true;
  panel.scrollIntoView({block: 'nearest', behavior: 'smooth'});
};
connect(); refresh(); refreshQuote();
