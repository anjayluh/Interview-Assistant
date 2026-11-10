'use strict';
const $ = id => document.getElementById(id);
let socket, retry = 0, timer, elapsed = 0, usage = 0, rate = .15, running = false, connected = false;
let currentState = 'idle';
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
  $('start').disabled = !ready || !$('mic').value || !$('system').value;
  $('test-mic').disabled = !ready || !$('mic').value;
  $('test-system').disabled = !ready || !$('system').value;
  $('stop').disabled = !connected || idle;
  $('answer').disabled = !connected || currentState !== 'running';
  $('mic').disabled = $('system').disabled = !idle;
  $('refresh').disabled = !idle;
}
function clock() {
  $('duration').textContent = `${Math.floor(elapsed / 60).toString().padStart(2,'0')}:${Math.floor(elapsed % 60).toString().padStart(2,'0')}`;
  $('cost').textContent = `Estimated AssemblyAI usage: $${(usage / 3600 * rate).toFixed(4)} · ${(usage/60).toFixed(1)} channel-min`;
}
function state(s) {
  currentState = s.state; running = s.state === 'running'; elapsed = s.duration; usage = s.channel_seconds;
  rate = usage > 0 ? s.estimate * 3600 / usage : .15;
  clearInterval(timer);
  if (running) timer = setInterval(() => { elapsed++; usage += s.active_channels; clock(); }, 1000);
  $('notice').textContent = s.reason;
  $('length').value = s.length;
  $('limit').textContent = `Maximum session: ${s.max_minutes} min`;
  $('assembly').textContent = `AssemblyAI: ${s.configured.assemblyai ? (running ? '2 streams connected' : s.state === 'starting' ? 'connecting…' : 'disconnected') : 'key missing'}`;
  $('groq').textContent = `Groq: ${s.configured.groq ? (s.groq_verified ? 'model verified' : 'not checked') : 'key missing'} · ${s.model}`;
  for (const role of ['me','interviewer']) {
    $(`status-${role}`).textContent = running ? 'Capturing' : s.testing && s.sources.includes(role) ? 'Local test active' : 'Not capturing';
    if (!running && !s.testing) $(`level-${role}`).value = 0;
  }
  buttons(); clock();
}
function transcript(x) {
  if (!rows.size) $('transcript').replaceChildren();
  let row = rows.get(x.key);
  if (!row) {
    row = document.createElement('div');
    const speaker = document.createElement('span'); speaker.className = 'speaker';
    speaker.textContent = x.role === 'me' ? 'You · microphone' : 'Interviewer · system audio';
    row.append(speaker, document.createElement('p'));
    rows.set(x.key, row); $('transcript').append(row);
  }
  row.className = `utterance ${x.role} ${x.final ? '' : 'partial'}`;
  row.lastChild.textContent = x.text;
  while (rows.size > 122) { const key = rows.keys().next().value; rows.get(key).remove(); rows.delete(key); }
  $('transcript').scrollTop = $('transcript').scrollHeight;
}
function answer(x) {
  $('answers').querySelector('.empty')?.remove();
  const card = document.createElement('article'); card.className = 'answer-card';
  const q = document.createElement('p'); q.className = 'question'; q.textContent = x.question;
  const p = document.createElement('p'); p.className = 'response'; p.textContent = x.text;
  card.append(q,p); $('answers').prepend(card);
  while ($('answers').children.length > 12) $('answers').lastChild.remove();
  $('answers').scrollTop = 0;
}
function clear() { rows.clear(); $('transcript').replaceChildren(); $('answers').replaceChildren(); }
function connect() {
  socket = new WebSocket(`ws://${location.host}/ws?client=${encodeURIComponent(client)}`);
  socket.onopen = () => { retry = 0; connected = true; $('connection').textContent = 'Connected locally'; buttons(); };
  socket.onmessage = e => {
    const x = JSON.parse(e.data);
    if (x.type === 'snapshot') { clear(); state(x.state); [...x.history, ...x.partials].forEach(transcript); x.answers.forEach(answer); }
    else if (x.type === 'state') state(x);
    else if (x.type === 'transcript') transcript(x);
    else if (x.type === 'answer') answer(x);
    else if (x.type === 'level') $(`level-${x.role}`).value = x.value;
    else if (x.type === 'error') error(x.message);
    else if (x.type === 'clear') clear();
    else if (x.type === 'groq') { $('groq').textContent = `Groq: ${x.status}`; $('answer').disabled = x.status === 'generating' || currentState !== 'running'; }
  };
  socket.onclose = e => {
    connected = false; clearInterval(timer); $('connection').textContent = 'Disconnected'; buttons();
    if (e.code === 4001 || e.code === 1008) return error(e.reason || 'Connection refused. Reload the application in its controlling tab.');
    $('notice').textContent = 'Reconnecting… capture stops after the browser recovery period if this fails.';
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
    const blackhole = data.devices?.find(d => /blackhole/i.test(d.name));
    if (blackhole && !$('system').value) $('system').value = String(blackhole.id);
    const mic = data.devices?.find(d => !/blackhole|teams|parrot/i.test(d.name));
    if (mic && !$('mic').value) $('mic').value = String(mic.id);
    if (data.error) error(data.error);
    buttons();
  } catch { error('Unable to list devices. Check the backend and reload.'); }
}
$('start').onclick = () => { command({cmd:'start', mic:Number($('mic').value), system:Number($('system').value), length:$('length').value}); $('start').disabled = true; };
$('stop').onclick = () => command({cmd:'stop'});
$('test-mic').onclick = () => command({cmd:'test',role:'me',device:Number($('mic').value)});
$('test-system').onclick = () => command({cmd:'test',role:'interviewer',device:Number($('system').value)});
$('answer').onclick = () => command({cmd:'answer'});
$('clear').onclick = () => command({cmd:'clear'});
$('length').onchange = () => command({cmd:'length',length:$('length').value});
$('mic').onchange = $('system').onchange = buttons;
$('refresh').onclick = refresh;
connect(); refresh();
