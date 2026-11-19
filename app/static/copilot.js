function answerCard(x) {
  const card = document.createElement('article'); card.className = 'answer-card';
  card.dataset.revision = x.revision ?? '';
  const p = document.createElement('p'); p.className = 'response'; p.textContent = x.text;
  card.append(p);
  const more = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = 'Evidence, code and deeper explanation'; more.append(summary);
  for (const [label, text] of [
    ['Category', x.category], ['Experience', x.experience], ['Verified source example', x.example],
    ['Talking points', x.talking_points], ['Details', x.details],
    ['Competency', [x.competency, x.secondary_competencies].filter(Boolean).join(' · ')], ['Likely follow-ups', x.follow_ups],
  ]) {
    if (!text) continue;
    const info = document.createElement('p'); info.textContent = label + ' · ' + text; more.append(info);
  }
  if (x.code) {
    const block = document.createElement('pre'), code = document.createElement('code');
    code.textContent = x.code; block.append(code); more.append(block);
  }
  card.append(more); return card;
}

function renderLatency(metrics) {
  const target = document.getElementById('live-latency');
  if (!target) return;
  target.textContent = metrics.slice(-16).map(x => `${(x.at_ms / 1000).toFixed(3)}s · ${x.role || 'answer'} · ${x.stage.replaceAll('_', ' ')} · revision ${x.revision}`).join('\n');
}

(() => {
  const get = id => document.getElementById(id);
  const phone = document.body.dataset.companion === 'true';
  const hash = location.hash.slice(1);
  if (phone && hash) history.replaceState(null, '', location.pathname);
  const options = {headers: {}};
  if (phone && sessionStorage.getItem('iaDevice')) options.headers.Authorization = 'Bearer ' + sessionStorage.getItem('iaDevice');

  async function api(path, method = 'GET', body, binary = false) {
    const headers = {...options.headers};
    if (body && !binary) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api/' + path, {
      method, headers, body: binary ? body : body ? JSON.stringify(body) : undefined,
    });
    const result = await response.json();
    if (!response.ok) { const error = new Error(typeof result.detail === 'string' ? result.detail : 'Check the request fields.'); error.status = response.status; throw error; }
    return result;
  }

  function setup() {
    if (phone) return {};
    return {
      cv_id: get('interview-cv').dataset.loaded ? get('interview-cv').value : null,
      title: get('job-title').value, company: get('job-company').value,
      description: get('job-description').value, notes: get('job-notes').value,
      research: get('job-research').value, keyterms: get('keyterms').checked,
    };
  }

  let ocrBusy = false;
  async function extract(image) {
    if (!image) throw new Error('Choose an image.');
    const result = await api('copilot/ocr', 'POST', image, true);
    get('copilot-question').value = result.text;
    get('copilot-message').textContent = 'Review the extracted text before requesting an answer.';
  }
  async function capture(action) {
    if (ocrBusy) return;
    ocrBusy = true;
    get('copilot-message').textContent = 'Preparing local text extraction…';
    try { await action(); }
    catch (error) { get('copilot-message').textContent = error.message; }
    finally { ocrBusy = false; }
  }
  get('copilot-image').onchange = () => capture(async () => {
    const input = get('copilot-image');
    const image = input.files[0];
    input.value = '';
    await extract(image);
  });
  get('copilot-screen').hidden = !navigator.mediaDevices?.getDisplayMedia;
  get('copilot-screen').onclick = () => capture(async () => {
    const stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
    const video = document.createElement('video');
    video.muted = true;
    let image;
    try {
      video.srcObject = stream;
      await video.play();
      const scale = Math.min(1, 3200 / Math.max(video.videoWidth, video.videoHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      image = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .92));
    } finally {
      stream.getTracks().forEach(track => track.stop());
      video.srcObject = null;
    }
    await extract(image);
  });
  get('copilot-answer').onclick = async () => {
    const question = get('copilot-question').value.trim();
    const status = get('copilot-message');
    if (!question) {
      status.textContent = 'Enter a question first.';
      return;
    }
    try {
      if (phone) await api('companion/answer', 'POST', {question});
      else {
        if (currentState !== 'running') throw new Error('Authorise a live session first.');
        command({cmd: 'answer', question});
      }
      status.textContent = 'Response requested.';
    } catch (error) { status.textContent = error.message; }
  };

  let practiceState;
  let practiceBusy = false;
  async function practice(next) {
    if (practiceBusy) return;
    const response = get('practice-response');
    const answer = next ? response.value.trim() : '';
    const feedback = get('practice-feedback');
    if (next && (!practiceState || !answer)) {
      feedback.textContent = 'Enter your practice response first.';
      return;
    }
    practiceBusy = true;
    try {
      const current = next ? practiceState : {
        scenario: get('practice-scenario').value, interview: setup(),
      };
      const result = await api('copilot/practice', 'POST', {...current, answer});
      practiceState = {...current, index: result.index, stage: result.stage, phase: result.phase, visited: result.visited};
      get('practice-question').textContent = result.done ? 'Practice complete.' : result.prompt;
      get('practice-criteria').textContent = (result.criteria || []).join(' · ');
      feedback.textContent = (result.feedback || []).join(' ');
      get('practice-evidence').textContent = (result.hints || []).join('\n');
      get('practice-support').hidden = !result.hints?.length;
      get('practice-next').disabled = Boolean(result.done);
      response.value = '';
    } catch (error) { feedback.textContent = error.message; }
    finally { practiceBusy = false; }
  }

  get('practice-panel').addEventListener('toggle', async () => {
    if (!get('practice-panel').open || get('practice-scenario').options.length) return;
    try {
      const scenarios = await api('copilot/scenarios');
      get('practice-scenario').replaceChildren(...scenarios.map(name => new Option(name, name)));
    } catch (error) { get('practice-feedback').textContent = error.message; }
  });
  get('practice-start').onclick = () => practice(false);
  get('practice-next').onclick = () => practice(true);

  if (!phone) {
    let pendingId = '', pairingActive = false;
    function showPair(state) {
      pairingActive = Boolean(state.url || state.pending || state.device);
      get('phone-invite').hidden = !state.url;
      if (state.url) {
        if (get('copilot-link').href !== state.url) get('phone-qr').src = '/api/copilot/pair/qr?t=' + Date.now();
        get('copilot-link').href = state.url;
        get('copilot-link').textContent = state.url;
        get('phone-entry').href = state.entry_url;
        get('phone-entry').textContent = state.entry_url;
        get('phone-code').textContent = state.code;
      }
      pendingId = state.pending?.id || '';
      get('phone-pending').hidden = !pendingId;
      if (state.pending) get('phone-request').textContent = `${state.pending.name} (${state.pending.peer}) requests access. Check that ${state.pending.comparison} matches the phone before approving.`;
      get('copilot-pair-status').textContent = state.device
        ? `${state.device.name} · ${state.device.peer} · ${state.device.connected ? 'Connected' : 'Disconnected — waiting to reconnect'} · Access expires in ${Math.ceil(state.device.expires_in / 60)} minutes.`
        : state.pending ? 'Waiting for your approval on this Mac.'
        : state.url ? `Pairing expires in ${state.expires_in} seconds. Keep both devices on the same Wi-Fi.` : 'No active pairing invitation or approved device. Click Pair Phone.';
    }
    get('phone-networks').onclick = async () => {
      try {
        const choices = await api('copilot/pair/networks');
        get('phone-network').replaceChildren(new Option('Automatic', ''), ...choices.map(x => new Option(`${x.label || x.name} · ${x.address}`, x.address)));
      } catch (error) { get('copilot-pair-status').textContent = error.message; }
    };
    get('copilot-pair').onclick = async () => {
      try {
        showPair(await api('copilot/pair?address=' + encodeURIComponent(get('phone-network').value || ''), 'POST', setup()));
      } catch (error) { get('phone-invite').hidden = true; get('copilot-pair-status').textContent = error.message; }
    };
    get('copilot-unpair').onclick = async () => {
      try { await api('copilot/pair', 'DELETE'); showPair(await api('copilot/pair')); }
      catch (error) { get('copilot-pair-status').textContent = error.message; }
    };
    for (const decision of ['approve', 'reject']) get('phone-' + decision).onclick = async () => {
      try { showPair(await api(`copilot/pair/${pendingId}/${decision}`, 'POST')); }
      catch (error) { get('copilot-pair-status').textContent = error.message; }
    };
    async function pairingStatus() {
      try { const state = await api('copilot/pair'); if (state.url || state.pending || state.device || pairingActive) showPair(state); }
      catch (_) { /* The laptop connection indicator handles backend restarts. */ }
      setTimeout(pairingStatus, 1500);
    }
    pairingStatus();
  } else {
    let streaming = false, lastAnswerStatus = {phase: 'idle'};
    function answerStatus(status) {
      if (!status) return;
      lastAnswerStatus = status;
      if (status.diagnostic) get('provider-error-diagnostic').textContent = JSON.stringify(status.diagnostic, null, 2);
      else if (status.phase === 'idle') get('provider-error-diagnostic').textContent = '';
      const label = status.phase === 'failed' ? status.message
        : status.phase === 'generating' ? (get('phone-draft').hidden ? 'Generating a suggested answer…' : 'Generating · AI draft, not yet fully validated')
        : status.phase === 'ready' ? 'AI-generated suggested response'
        : 'Waiting for an interviewer question.';
      get('phone-answer-status').textContent = label;
      if (status.phase === 'failed') get('phone-draft').hidden = true;
    }
    const transcriptRows = new Map();
    let latency = [];
    function sourceTurn(x) {
      if (!['me', 'interviewer'].includes(x.role)) return;
      let row = transcriptRows.get(x.key);
      if (x.duplicate_of) {
        row?.remove(); transcriptRows.delete(x.key);
        get('phone-bleed').textContent = 'Likely interviewer speaker bleed was hidden from YOU. Original microphone source is retained in diagnostics.';
        return;
      }
      if (!row) { row = document.createElement('p'); transcriptRows.set(x.key, row); get('phone-' + x.role).append(row); }
      row.dataset.source = x.source || x.role;
      row.className = 'utterance ' + x.role + (x.final ? '' : ' partial');
      row.textContent = x.text;
      while (transcriptRows.size > 122) {
        const key = transcriptRows.keys().next().value; transcriptRows.get(key).remove(); transcriptRows.delete(key);
      }
    }
    function clearPhone() {
      transcriptRows.clear(); get('phone-interviewer').replaceChildren(); get('phone-me').replaceChildren();
      get('phone-answers').replaceChildren(); get('phone-draft').hidden = true;
      get('phone-bleed').textContent = ''; get('phone-answer-status').textContent = 'Waiting for an interviewer question.';
      latency = []; renderLatency(latency);
      if (lastAnswerStatus.phase === 'failed') answerStatus(lastAnswerStatus);
    }
    function sessionLabel(x) {
      if (x.state === 'running') return x.answer_enabled === false
        ? 'Phone connected · Transcription active, suggestions paused'
        : 'Phone connected · Live session controlled on the Mac';
      return 'Phone connected · ' + (x.reason || 'Local practice');
    }
    function handle(x) {
      if (x.type === 'companion_snapshot') {
        lastAnswerStatus = x.answer_status || {phase: 'idle'};
        clearPhone();
        [...x.history, ...x.partials].forEach(sourceTurn);
        get('phone-answers').replaceChildren(...x.answers.slice().reverse().map(answerCard));
        latency = x.metrics || []; renderLatency(latency);
        if (x.draft) handle(x.draft);
        if (x.answer_status) answerStatus(x.answer_status);
        get('phone-status').textContent = sessionLabel(x);
      } else if (x.type === 'transcript') sourceTurn(x);
      else if (x.type === 'groq') answerStatus({phase: x.status === 'error' ? 'failed' : x.status, message: x.message, diagnostic: x.diagnostic});
      else if (x.type === 'latency') { latency.push(x); latency = latency.slice(-80); renderLatency(latency); }
      else if (x.type === 'answer_delta') {
        get('phone-draft').hidden = false; get('phone-draft').textContent = x.text;
        get('phone-answer-status').textContent = 'AI draft · still generating and awaiting complete response validation';
      } else if (x.type === 'answer') {
        get('phone-draft').hidden = true; get('phone-answers').prepend(answerCard(x));
        while (get('phone-answers').children.length > 12) get('phone-answers').lastChild.remove();
        get('phone-answer-status').textContent = 'AI-generated suggested response';
      } else if (x.type === 'answer_superseded') {
        get('phone-draft').hidden = true;
        get('phone-answer-status').textContent = 'Previous suggestions remain below. Preparing an answer to the latest interviewer question and instructions…';
      } else if (x.type === 'state') {
        get('phone-status').textContent = sessionLabel(x);
        if (x.answer_status) answerStatus(x.answer_status);
      }
      else if (x.type === 'clear') clearPhone();
      else if (x.type === 'error') { get('phone-draft').hidden = true; get('phone-answer-status').textContent = x.message; }
      else if (x.type === 'revoked') { const error = new Error('Phone access was revoked or expired. Pair again on the Mac.'); error.status = 403; throw error; }
    }
    async function refresh() {
      if (!options.headers.Authorization || streaming) return;
      streaming = true;
      let reader;
      try {
        const started = performance.now();
        const response = await fetch('/api/companion/events', {headers: options.headers});
        if (!response.ok) { const detail = await response.json().catch(() => ({})); const error = new Error(detail.detail || 'Phone connection refused. Pair again on the Mac.'); error.status = response.status; throw error; }
        get('phone-pairing').hidden = true;
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '', first = true;
        while (true) {
          const {value, done} = await reader.read(); if (done) break;
          const received = performance.now();
          buffer += decoder.decode(value, {stream: true});
          if (buffer.length > 8 * 1024 * 1024) throw new Error('Companion update too large. Reconnecting.');
          let boundary;
          while ((boundary = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 1);
            if (!line) continue;
            const event = JSON.parse(line); handle(event);
            if (event.type !== 'keepalive') {
              const handshake = first ? `Connection to first snapshot: ${(performance.now() - started).toFixed(1)} ms. ` : '';
              first = false;
              requestAnimationFrame(() => { get('phone-display-latency').textContent = handshake + `Latest ${event.type}: received-to-render ${(performance.now() - received).toFixed(1)} ms. Phone time ${new Date().toLocaleTimeString()}.`; });
            }
          }
        }
        throw new Error('Connection ended.');
      } catch (error) {
        if (error.status === 403) {
          sessionStorage.removeItem('iaDevice'); delete options.headers.Authorization;
          clearPhone(); get('phone-pairing').hidden = false; get('phone-status').textContent = error.message;
        } else get('phone-status').textContent = 'Disconnected. Reconnecting automatically; Mac interview context is preserved…';
      } finally {
        if (reader) await reader.cancel().catch(() => {});
        streaming = false; setTimeout(refresh, 1500);
      }
    }
    let waiting = false;
    async function approval() {
      const secret = sessionStorage.getItem('iaPending');
      if (!secret || waiting) return;
      waiting = true;
      try {
        options.headers.Authorization = 'Bearer ' + secret;
        const result = await api('companion/approval');
        delete options.headers.Authorization;
        if (result.decision === 'approved') {
          sessionStorage.removeItem('iaPending'); sessionStorage.setItem('iaDevice', result.token);
          options.headers.Authorization = 'Bearer ' + result.token;
          get('phone-pairing').hidden = true; refresh();
        } else if (result.decision === 'rejected') {
          sessionStorage.removeItem('iaPending'); get('phone-status').textContent = 'The Mac rejected this request. Pair again to request access.';
        } else { get('phone-status').textContent = 'Waiting for approval on the Mac…'; setTimeout(approval, 1500); }
      } catch (error) {
        delete options.headers.Authorization;
        get('phone-status').textContent = error.message;
        if (error.status === 403) sessionStorage.removeItem('iaPending');
        else setTimeout(approval, 2000);
      } finally { waiting = false; }
    }
    get('phone-connect').onclick = async () => {
      try {
        delete options.headers.Authorization;
        const result = await api('companion/claim', 'POST', {code: get('phone-pair-code').value.trim(), name: get('phone-device-name').value.trim() || 'iPhone Safari'});
        sessionStorage.setItem('iaPending', result.secret);
        sessionStorage.setItem('iaComparison', result.comparison);
        get('phone-comparison').textContent = 'Compare this code on the Mac: ' + result.comparison;
        get('phone-pair-code').value = ''; approval();
      } catch (error) { get('phone-status').textContent = error.message; }
    };
    if (hash) { get('phone-pair-code').value = hash; get('phone-connect').click(); }
    else if (sessionStorage.getItem('iaDevice')) refresh();
    else if (sessionStorage.getItem('iaPending')) {
      get('phone-comparison').textContent = 'Compare this code on the Mac: ' + sessionStorage.getItem('iaComparison'); approval();
    }
  }
})();
