'use strict';
const $ = id => document.getElementById(id);
let socket, retry = 0, timer, elapsed = 0, usage = 0, running = false, connected = false;
let currentState = 'idle', currentReviewId = null;
let quoteData, quoteTimer, quoteRequest = 0, heartbeat;
let quoteFailures = 0;
let keysReady = false, answersEnabled = true;
const safetyFields = ['live-minutes', 'live-cap', 'keyterms'];
let interviewRan = false;
const client = sessionStorage.iaClient || (sessionStorage.iaClient = crypto.randomUUID());
const rows = new Map();
const liveMetrics = [];
let answerDraft;
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
  $('clear').disabled = !ready;
  $('clear').title = idle ? '' : 'Stop the session before deleting its review.';
  const reasons = [];
  if (!connected) reasons.push('Connect to the local backend.');
  if (!idle) reasons.push(currentState === 'testing' ? 'Wait for the audio test to finish, or press STOP ALL.' : 'A session is already active.');
  if (!keysReady) reasons.push('Configure both API keys and restart the backend.');
  if (!$('mic').value || !$('system').value) reasons.push('Select both audio inputs.');
  else if ($('mic').value === $('system').value) reasons.push('Select different microphone and system audio inputs.');
  if (!quoteData?.quote_id) {
    reasons.push(...(quoteData?.blockers?.length ? quoteData.blockers
      : ['Check the local session settings under Live session safety.']));
  }
  $('start').disabled = reasons.length > 0;
  $('start-blocker').textContent = idle ? reasons.join(' ') : reasons[0];
  $('review-safety').hidden = !idle || Boolean(quoteData?.quote_id);
  $('test-mic').disabled = !ready || !$('mic').value;
  $('test-system').disabled = !ready || !$('system').value;
  $('stop').disabled = idle;
  $('answer').disabled = !connected || currentState !== 'running' || !answersEnabled;
  $('mic').disabled = $('system').disabled = !idle;
  $('refresh').disabled = !idle;
  for (const id of safetyFields) $(id).disabled = !idle;
  for (const id of ['interview-cv','job-title','job-company','job-description','job-notes','job-research','keyterms']) $(id).disabled = !idle;
}
function clock() {
  $('duration').textContent = `${Math.floor(elapsed / 60).toString().padStart(2,'0')}:${Math.floor(elapsed % 60).toString().padStart(2,'0')}`;
  $('cost').textContent = `AssemblyAI usage: ${(usage/60).toFixed(1)} channel-min · maximum user expenditure UGX 0`;
}
function idleMode(mode) { return mode === 'idle' || mode === 'testing'; }
function state(s) {
  currentState = s.state;
  currentReviewId = s.review_id || currentReviewId;
  $('wrapup-notice').hidden = !s.wrapup || s.state !== 'running';
  answersEnabled = s.answer_enabled !== false;
  if (s.answer_status?.phase === 'failed') {
    error(s.answer_status.message);
    $('provider-error-diagnostic').textContent = JSON.stringify(s.answer_status.diagnostic, null, 2);
  }
  if (!idleMode(s.state)) hideAudioDiagnostics();
  if (s.state === 'idle') { answerDraft?.remove(); answerDraft = null; }
  running = s.state === 'running'; elapsed = s.duration; usage = s.channel_seconds;
  if (running || s.state === 'starting') interviewRan = true;
  if (currentState === 'idle' && interviewRan) {
    interviewRan = false;
    document.dispatchEvent(new Event('interview-ended'));
    refreshQuote();
  }
  keysReady = Boolean(s.configured.assemblyai && s.configured.groq);
  $('live-mode').textContent = idleMode(s.state) ? 'LOCAL-ONLY' : 'FREE USAGE ONLY';
  $('live-minutes').max = s.max_minutes;
  clearInterval(timer);
  if (running) timer = setInterval(() => { elapsed++; usage += s.active_channels; clock(); }, 1000);
  $('notice').textContent = s.reason;
  $('length').value = s.length;
  $('limit').textContent = `Session limit: ${idleMode(s.state) ? $('live-minutes').value : s.limit_minutes} min`;
  $('assembly').textContent = `AssemblyAI: ${s.configured.assemblyai ? (running ? '2 streams connected' : s.state === 'starting' ? 'connecting…' : 'disconnected') : 'key missing'}`;
  $('groq').textContent = `Groq: ${s.configured.groq ? (!answersEnabled ? 'suggestions paused' : s.groq_verified ? 'model verified' : 'not checked') : 'key missing'} · ${s.model}`;
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
  if (!['me', 'interviewer'].includes(x.role)) return;
  if (x.duplicate_of) { rows.get(x.key)?.remove(); rows.delete(x.key); $('speaker-note').textContent = 'Possible interviewer echo on your microphone. Original source text is retained in the full review.'; return; }
  const source = $(`transcript-${x.role}`);
  let row = rows.get(x.key);
  if (!row) {
    row = document.createElement('p');
    rows.set(x.key, row);
    source.append(row);
  }
  row.dataset.source = x.source || x.role;
  row.className = `utterance ${x.role} ${x.final ? '' : 'partial'} ${x.continuation ? 'continuation' : ''}`;
  row.textContent = x.text;
  row.dataset.text = x.text;
  if (x.role === 'interviewer' && x.final) {
    const button = document.createElement('button'); button.className = 'quiet'; button.textContent = 'Answer this';
    button.onclick = () => command({cmd:'answer', question:x.text}); row.append(button);
  }
  while (rows.size > 122) { const key = rows.keys().next().value; rows.get(key).remove(); rows.delete(key); }
  source.scrollTop = source.scrollHeight;
}
function answer(x) {
  answerDraft?.remove(); answerDraft = null;
  $('answers').querySelector('.empty')?.remove();
  $('answers').prepend(answerCard(x));
  while ($('answers').children.length > 12) $('answers').lastChild.remove();
  $('answers').scrollTop = 0;
}
function clear() { answerDraft?.remove(); answerDraft = null; liveMetrics.length = 0; renderLatency(liveMetrics); rows.clear(); $('transcript-interviewer').replaceChildren(); $('transcript-me').replaceChildren(); $('answers').replaceChildren(); }
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
    buttons(); refreshQuote();
  };
  socket.onmessage = e => {
    const received = performance.now();
    const x = JSON.parse(e.data);
    if (x.type === 'heartbeat') { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({cmd: 'heartbeat'})); }
    else if (x.type === 'snapshot') { clear(); state(x.state); [...x.history, ...x.partials].forEach(transcript); x.answers.forEach(answer);
      $('current-question').textContent = x.state.latest ? 'Current interviewer question: ' + x.state.latest : '';
      for (const saved of x.answers) requestAnimationFrame(() => command({cmd:'rendered',revision:saved.revision,render_ms:performance.now()-received,replay:true})); }
    else if (x.type === 'state') state(x);
    else if (x.type === 'transcript') transcript(x);
    else if (x.type === 'answer') {
      answer(x);
      requestAnimationFrame(() => command({cmd:'rendered',revision:x.revision,render_ms:performance.now()-received,replay:false}));
    }
    else if (x.type === 'question_target') $('current-question').textContent = 'Current interviewer question: ' + x.question;
    else if (x.type === 'answer_delta') {
      if (!answerDraft) { answerDraft = document.createElement('article'); answerDraft.className = 'answer-card response'; $('answers').prepend(answerDraft); }
      answerDraft.textContent = 'AI draft · still generating\n' + x.text;
    }
    else if (x.type === 'answer_superseded') { answerDraft?.remove(); answerDraft = null; }
    else if (x.type === 'latency') { liveMetrics.push(x); if (liveMetrics.length > 80) liveMetrics.shift(); renderLatency(liveMetrics); }
    else if (x.type === 'audio_diagnostic') audioDiagnostic(x);
    else if (x.type === 'level') $(`level-${x.role}`).value = x.value;
    else if (x.type === 'error') { clearTimeout(audioAckTimer); error(x.message); if (currentState === 'idle') refreshQuote(); }
    else if (x.type === 'clear') clear();
    else if (x.type === 'groq') { $('groq').textContent = `Groq: ${x.status}`; if (x.status === 'error') answersEnabled = false; $('answer').disabled = !answersEnabled || x.status === 'generating' || currentState !== 'running'; }
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
    const mic = data.devices?.find(d => /macbook pro microphone/i.test(d.name))
      || data.devices?.find(d => /macbook.*microphone|built-in.*microphone/i.test(d.name))
      || data.devices?.find(d => !/blackhole|ideashare|teams|parrot/i.test(d.name));
    if (mic && !$('mic').value) $('mic').value = String(mic.id);
    if (data.error) error(data.error);
    buttons();
  } catch { error('Unable to list devices. Check the backend and reload.'); }
}
function liveOptions() {
  return {minutes: $('live-minutes').valueAsNumber, cap_ugx: 0};
}
function showQuote(data) {
  quoteData = data;
  $('live-quote').textContent = data?.allowance_status || 'Checking local session settings…';
  const reasons = data?.blockers || [];
  $('live-blockers').replaceChildren(...reasons.map(reason => {
    const item = document.createElement('li');
    item.textContent = reason;
    return item;
  }));
  if (currentState === 'idle') $('limit').textContent = 'Session limit: ' + ($('live-minutes').value || '—') + ' min';
  buttons();
}
function durationProblem() {
  const field = $('live-minutes');
  const maximum = Math.min(Number(field.max) || 120, 120);
  if (!Number.isInteger(field.valueAsNumber) || field.valueAsNumber < 5 || field.valueAsNumber > maximum) {
    return `Choose a whole-number session duration from 5 to ${maximum} minutes. The current value is ${field.value || 'empty'}. Use 60 minutes for a normal interview.`;
  }
  return '';
}
function settingsFailure(message, retry = false) {
  showQuote({allowance_status: 'Live session settings need attention.', blockers: [message]});
  if (retry && currentState === 'idle') {
    quoteTimer = setTimeout(refreshQuote, Math.min(2000 * 2 ** quoteFailures++, 10000));
  }
}
async function refreshQuote() {
  clearTimeout(quoteTimer);
  const request = ++quoteRequest;
  const problem = durationProblem();
  if (problem) { settingsFailure(problem); return null; }
  const options = liveOptions();
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 5000);
  try {
    const response = await fetch('/api/live-quote', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, signal: abort.signal,
      body: JSON.stringify({options, keyterms: $('keyterms').checked}),
    });
    const data = await response.json();
    if (request !== quoteRequest) return null;
    if (!response.ok) {
      let message;
      if (response.status === 422) {
        const durationError = Array.isArray(data.detail) && data.detail.find(x => x.loc?.includes('minutes'));
        message = durationError ? 'The backend rejected the session duration: ' + durationError.msg + '. Choose 5–120 minutes.'
          : 'The backend rejected the local session settings (HTTP 422). Review the highlighted session fields.';
      } else if (response.status === 403) {
        message = 'The local app connection expired (HTTP 403). Refreshing its connection and checking again…';
        await fetch('/', {cache: 'no-store'});
      } else message = `The local session-settings service returned HTTP ${response.status}. Retrying automatically; no provider requests are being made.`;
      settingsFailure(message, response.status !== 422);
      return null;
    }
    quoteFailures = 0;
    showQuote(data);
    return data;
  } catch (error) {
    if (request === quoteRequest) settingsFailure(error.name === 'AbortError'
      ? 'The local session-settings check timed out after 5 seconds. Retrying automatically; no live session has started.'
      : 'The local app connection is temporarily unavailable. Reconnecting and checking session settings automatically; no live session has started.', true);
    return null;
  } finally { clearTimeout(timeout); }
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
  const consentOptions = liveOptions();
  const consentKeyterms = $('keyterms').checked;
  const recordAudio = $('record-review-audio').checked;
  const consentFingerprint = JSON.stringify({live: consentOptions, keyterms: consentKeyterms, record_audio:recordAudio});
  if (!window.confirm(
    'Start a ' + data.minutes + '-minute live session using AssemblyAI trial credits and Groq Free capacity? '
    + 'Maximum user expenditure is UGX 0. The session stops if a provider rejects access or reports exhausted credits or a rate limit.'
    + (recordAudio ? '\nLocal audio recording is enabled for both sources and stays on this Mac.' : '\nLocal audio recording is off.')
    + (rows.size || $('answers').querySelector('article') ? '\n\nStarting a new interview resets the live display. The previous session remains in the local review archive.' : '')
  )) return;
  if (consentFingerprint !== JSON.stringify({live: liveOptions(), keyterms: $('keyterms').checked, record_audio:$('record-review-audio').checked})) {
    showQuote(null);
    return error('Session settings changed. Review them again.');
  }
  hideAudioDiagnostics();
  command({cmd: 'start', mic: Number($('mic').value), system: Number($('system').value),
    length: $('length').value, record_audio:recordAudio, quote_id: data.quote_id, live: {...consentOptions, confirmed: true},
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
$('wrapup-stop').onclick = () => $('stop').onclick();
$('test-mic').onclick = () => testAudio('me', 'mic');
$('test-system').onclick = () => testAudio('interviewer', 'system');
$('answer').onclick = () => command({cmd:'answer'});
$('clear').onclick = () => {
  if (window.confirm('Clear the transcript and suggested answers from this Mac and the paired phone? The saved local review will also be deleted. This cannot be undone.')) command({cmd:'clear'});
};
$('download-review').onclick = async () => {
  try {
    if (!currentReviewId) throw new Error('Wait for the local session review to load.');
    const response = await fetch('/api/reviews/' + encodeURIComponent(currentReviewId));
    if (!response.ok) throw new Error('The full review could not be downloaded. The visible transcript has been left untouched.');
    const review = await response.json();
    const text = ['INTERVIEW REVIEW', JSON.stringify(review.metadata, null, 2),
      'TRANSCRIPT', ...review.history.map(row => row.role.toUpperCase() + (row.duplicate_of ? ' [suspected echo]' : '') + ': ' + row.text),
      'UNFINISHED TURNS', ...(review.partials || []).map(row => row.role.toUpperCase() + ': ' + row.text),
      'SUGGESTED ANSWERS', ...review.answers.map(item => 'QUESTION: ' + item.question + '\nANSWER: ' + item.text + (item.code ? '\nCODE: ' + item.code : '')),
      'REVIEW COVERAGE AND TIMING', JSON.stringify(review.analysis, null, 2)].join('\n\n');
    const url = URL.createObjectURL(new Blob([text], {type:'text/plain;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url; link.download = 'interview-review-' + new Date().toISOString().slice(0,10) + '.txt';
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
  } catch (err) { error(err.message); }
};
$('length').onchange = () => command({cmd:'length',length:$('length').value});
$('mic').onchange = $('system').onchange = buttons;
$('refresh').onclick = refresh;
$('review-safety').onclick = () => {
  const panel = document.querySelector('.safety-panel');
  panel.open = true;
  panel.scrollIntoView({block: 'nearest', behavior: 'smooth'});
};
// Browser form restoration must not turn a fresh launch into an invalid old duration.
$('live-minutes').value = $('live-minutes').defaultValue || '60';
window.addEventListener('pageshow', event => {
  if (currentState !== 'idle') return;
  if (!event.persisted) $('live-minutes').value = $('live-minutes').defaultValue || '60';
  refreshQuote();
});
$('reset-live-duration').onclick = () => {
  $('live-minutes').value = '60';
  refreshQuote();
};
async function loadReviews() {
  try {
    const response = await fetch('/api/reviews');
    if (!response.ok) throw new Error('Saved reviews could not be loaded.');
    const reviews = await response.json();
    $('review-select').replaceChildren(...reviews.map(x => new Option((x.metadata.company || 'Interview') + ' · ' + x.created_at + ' · ' + x.turns + ' turns', x.id)));
    $('review-message').textContent = reviews.length ? 'Choose a review to inspect or download.' : 'No saved interviews yet.';
  } catch (err) { $('review-message').textContent = err.message; }
}
async function selectedReview() {
  const id = $('review-select').value;
  if (!id) throw new Error('Choose a saved interview first.');
  const response = await fetch('/api/reviews/' + encodeURIComponent(id));
  if (!response.ok) throw new Error('Review could not be loaded.');
  return response.json();
}
$('review-refresh').onclick = loadReviews;
$('review-open').onclick = async () => {
  try {
    const review = await selectedReview();
    $('review-content').textContent = JSON.stringify(review.analysis, null, 2) + '\n\n' + review.history.map(x => x.role.toUpperCase() + (x.duplicate_of ? ' [suspected echo]' : '') + ': ' + x.text).join('\n\n') + '\n\nSUGGESTIONS\n\n' + review.answers.map(x => 'QUESTION: ' + x.question + '\nANSWER: ' + x.text + (x.code ? '\nCODE: ' + x.code : '')).join('\n\n');
    $('review-message').textContent = 'Full review loaded. Download JSON for all diagnostics and links.';
    $('review-audio').replaceChildren();
    if (review.metadata.audio_recorded) for (const role of ['me','interviewer']) {
      const link = document.createElement('a'); link.href = '/api/reviews/' + review.id + '/audio/' + role; link.textContent = 'Download ' + role + ' local audio '; link.download = ''; $('review-audio').append(link);
    }
  } catch (err) { $('review-message').textContent = err.message; }
};
$('review-export').onclick = async () => {
  try {
    const review = await selectedReview(), url = URL.createObjectURL(new Blob([JSON.stringify(review, null, 2)], {type:'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'interview-review-' + review.id + '.json'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) { $('review-message').textContent = err.message; }
};
$('review-delete').onclick = async () => {
  const id = $('review-select').value;
  if (!id || !window.confirm('Delete this saved review and any local audio? This cannot be undone.')) return;
  const response = await fetch('/api/reviews/' + encodeURIComponent(id), {method:'DELETE'});
  if (!response.ok) { $('review-message').textContent = 'Stop its session before deleting the review.'; return; }
  $('review-content').textContent = ''; $('review-audio').replaceChildren(); loadReviews();
};
connect(); refresh(); refreshQuote();
