'use strict';
const $ = id => document.getElementById(id);
let socket, retry = 0, timer, elapsed = 0, usage = 0, rate = .15, running = false, connected = false;
let currentState = 'idle';
let quoteData, quoteTimer, quoteRequest = 0, heartbeat;
let keysReady = false;
const safetyFields = ['live-minutes', 'live-cap', 'live-fx', 'live-fx-date', 'live-fx-source',
                      'live-assembly-check', 'live-groq-check', 'keyterms'];
let interviewRan = false;
const client = sessionStorage.iaClient || (sessionStorage.iaClient = crypto.randomUUID());
const rows = new Map();
function error(message) { $('error').textContent = message; $('error').hidden = false; }
function command(data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return error('Local connection is unavailable. Reconnecting…');
  $('error').hidden = true;
  socket.send(JSON.stringify(data));
}
function buttons() {
  const idle = currentState === 'idle', ready = connected && idle;
  $('start').disabled = !ready || !keysReady || !quoteData?.quote_id || !$('mic').value || !$('system').value;
  $('test-mic').disabled = !ready || !$('mic').value;
  $('test-system').disabled = !ready || !$('system').value;
  $('stop').disabled = idle;
  $('answer').disabled = !connected || currentState !== 'running';
  $('mic').disabled = $('system').disabled = !idle;
  $('refresh').disabled = !idle;
  for (const id of safetyFields) $(id).disabled = !idle;
  for (const id of ['interview-cv','job-title','job-company','job-description','job-notes','keyterms']) $(id).disabled = !idle;
}
function clock() {
  $('duration').textContent = `${Math.floor(elapsed / 60).toString().padStart(2,'0')}:${Math.floor(elapsed % 60).toString().padStart(2,'0')}`;
  $('cost').textContent = `Estimated AssemblyAI usage: $${(usage / 3600 * rate).toFixed(4)} · ${(usage/60).toFixed(1)} channel-min`;
}
function idleMode(mode) { return mode === 'idle' || mode === 'testing'; }
function state(s) {
  currentState = s.state; running = s.state === 'running'; elapsed = s.duration; usage = s.channel_seconds;
  if (running || s.state === 'starting') interviewRan = true;
  if (currentState === 'idle' && interviewRan) {
    interviewRan = false;
    document.dispatchEvent(new Event('interview-ended'));
    $('live-assembly-check').checked = $('live-groq-check').checked = false;
    refreshQuote();
  }
  keysReady = Boolean(s.configured.assemblyai && s.configured.groq);
  $('live-mode').textContent = idleMode(s.state) ? 'LOCAL-ONLY' : 'LIVE SESSION';
  $('live-minutes').max = s.max_minutes;
  rate = s.rate_usd || 0;
  clearInterval(timer);
  if (running) timer = setInterval(() => { elapsed++; usage += s.active_channels; clock(); }, 1000);
  $('notice').textContent = s.reason;
  $('length').value = s.length;
  $('limit').textContent = `Session limit: ${idleMode(s.state) ? $('live-minutes').value : s.limit_minutes} min`;
  $('assembly').textContent = `AssemblyAI: ${s.configured.assemblyai ? (running ? '2 streams connected' : s.state === 'starting' ? 'connecting…' : 'disconnected') : 'key missing'}`;
  $('groq').textContent = `Groq: ${s.configured.groq ? (s.groq_verified ? 'model verified' : 'not checked') : 'key missing'} · ${s.model}`;
  for (const role of ['me','interviewer']) {
    $(`status-${role}`).textContent = running ? 'Capturing' : s.testing && s.sources.includes(role) ? 'Local test active' : 'Not capturing';
    if (!running && !s.testing) $(`level-${role}`).value = 0;
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
  const card = document.createElement('article'); card.className = 'answer-card';
  const q = document.createElement('p'); q.className = 'question'; q.textContent = x.question;
  const p = document.createElement('p'); p.className = 'response'; p.textContent = x.text;
  const meta = document.createElement('p');
  meta.className = 'answer-meta';
  meta.textContent = [x.category, x.experience === 'Not applicable' ? '' : x.experience].filter(Boolean).join(' · ');
  card.append(q, meta, p);
  if (x.example) {
    const example = document.createElement('p');
    example.className = 'answer-example';
    example.textContent = 'Personal example — ' + x.example;
    card.append(example);
  }
  if (x.code || x.details) {
    const more = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = x.code ? ['Code', x.language].filter(Boolean).join(' · ') : 'Technical detail';
    more.append(summary);
    if (x.code) {
      const block = document.createElement('pre');
      const code = document.createElement('code');
      code.textContent = x.code;
      block.append(code);
      more.append(block);
    }
    if (x.details) {
      const detail = document.createElement('p');
      detail.textContent = x.details;
      more.append(detail);
    }
    card.append(more);
  }
  $('answers').prepend(card);
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
    else if (x.type === 'level') $(`level-${x.role}`).value = x.value;
    else if (x.type === 'error') { error(x.message); if (currentState === 'idle') refreshQuote(); }
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
  return {
    minutes: $('live-minutes').valueAsNumber,
    cap_ugx: $('live-cap').valueAsNumber,
    fx_ugx_per_usd: $('live-fx').value || null,
    fx_date: $('live-fx-date').value || null,
    fx_source: $('live-fx-source').value,
    assembly_billing_checked: $('live-assembly-check').checked,
    groq_billing_checked: $('live-groq-check').checked,
  };
}
function showQuote(data) {
  quoteData = data;
  const cost = $('live-quote');
  const unknown = data?.total_ugx == null;
  const published = data?.hourly_usd
    ? `US$${data.hourly_usd}/hour for ${data.channels} streams · US$${data.total_usd} for ${data.minutes} min · `
      + `US$${data.base_per_channel_hour_usd} base + US$${data.keyterms_per_channel_hour_usd} keyterms per channel-hour`
    : '';
  cost.textContent = [unknown ? 'COST UNKNOWN — LIVE SESSION BLOCKED' : `Estimated UGX ${data.total_ugx.toLocaleString()}`,
    published].filter(Boolean).join(' · ');
  cost.classList.toggle('safety-warning', unknown);
  $('live-price-date').textContent = data ? `· checked ${data.pricing_date} · published in ${data.currency}` : '';
  const options = liveOptions();
  $('live-billing').textContent = [
    `AssemblyAI billing and credits: ${options.assembly_billing_checked ? 'checked by you' : 'unknown'}.`,
    `Groq billing and credits: ${options.groq_billing_checked ? 'checked by you' : 'unknown'}.`,
    'The app does not verify either account’s billing or free allowance.',
  ].join(' ');
  $('live-billing').classList.toggle('safety-warning', !options.assembly_billing_checked || !options.groq_billing_checked);
  const reasons = data?.blockers || ['Review the duration, cap and exchange rate to obtain an estimate.'];
  $('live-blockers').replaceChildren(...reasons.filter(reason => !reason.startsWith('COST UNKNOWN')).map(reason => {
    const item = document.createElement('li');
    item.textContent = reason;
    return item;
  }));
  if (currentState === 'idle') $('limit').textContent = `Session limit: ${$('live-minutes').value || '—'} min`;
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
  if (!window.confirm(
    `Authorise this ${data.minutes}-minute live session?

AssemblyAI will receive both audio streams. `
    + `Estimated transcription cost: US$${data.total_usd} (UGX ${data.total_ugx.toLocaleString()}). `
    + 'Groq will receive relevant interview context and may charge separately. '
    + 'The transcription cap is a local estimate and cannot limit your provider bill.'
  )) return;
  command({cmd: 'start', mic: Number($('mic').value), system: Number($('system').value),
    length: $('length').value, quote_id: data.quote_id, live: {...liveOptions(), confirmed: true},
    interview: {
      cv_id: $('interview-cv').dataset.loaded ? $('interview-cv').value : null,
      title: $('job-title').value, company: $('job-company').value, description: $('job-description').value,
      notes: $('job-notes').value, keyterms: $('keyterms').checked,
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
$('test-mic').onclick = () => command({cmd:'test',role:'me',device:Number($('mic').value)});
$('test-system').onclick = () => command({cmd:'test',role:'interviewer',device:Number($('system').value)});
$('answer').onclick = () => command({cmd:'answer'});
$('clear').onclick = () => command({cmd:'clear'});
$('length').onchange = () => command({cmd:'length',length:$('length').value});
$('mic').onchange = $('system').onchange = buttons;
$('refresh').onclick = refresh;
connect(); refresh(); refreshQuote();
