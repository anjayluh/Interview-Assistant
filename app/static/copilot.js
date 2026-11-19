function answerCard(x) {
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
  if (x.talking_points) {
    const points = document.createElement('p');
    points.className = 'answer-points';
    points.textContent = x.talking_points;
    card.append(points);
  }
  if (x.code || x.details || x.follow_ups || x.competency || x.secondary_competencies) {
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
    for (const [label, text] of [
      ['Competency', [x.competency, x.secondary_competencies].filter(Boolean).join(' · ')],
      ['Likely follow-ups', x.follow_ups],
    ]) {
      if (!text) continue;
      const info = document.createElement('p');
      info.textContent = label + ' · ' + text;
      more.append(info);
    }
    card.append(more);
  }
  return card;
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
    let rendered = '', polling = false;
    async function refresh() {
      if (!options.headers.Authorization || polling) return;
      polling = true;
      try {
        const state = await api('companion/state');
        get('phone-pairing').hidden = true;
        get('phone-status').textContent = state.state === 'running' ? 'Connected · Live session controlled on the Mac' : 'Connected · Local practice';
        get('phone-question').textContent = state.latest;
        const answers = JSON.stringify(state.answers);
        if (rendered !== answers) {
          rendered = answers;
          get('phone-answers').replaceChildren(...state.answers.slice().reverse().map(answerCard));
        }
      } catch (error) {
        get('phone-answers').replaceChildren(); get('phone-question').textContent = ''; rendered = '';
        if (error.status === 403) {
          sessionStorage.removeItem('iaDevice'); delete options.headers.Authorization;
          get('phone-pairing').hidden = false; get('phone-status').textContent = error.message;
        } else get('phone-status').textContent = 'Disconnected. Reconnecting automatically when the Mac is reachable…';
      } finally { polling = false; setTimeout(refresh, 1500); }
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
