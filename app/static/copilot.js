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
  if (phone && hash) {
    sessionStorage.setItem('iaPair', hash);
    history.replaceState(null, '', location.pathname);
  }
  const pair = phone ? sessionStorage.getItem('iaPair') : '';
  const options = {headers: pair ? {Authorization: 'Bearer ' + pair} : {}};

  async function api(path, method = 'GET', body, binary = false) {
    const headers = {...options.headers};
    if (body && !binary) headers['Content-Type'] = 'application/json';
    const response = await fetch('/api/' + path, {
      method, headers, body: binary ? body : body ? JSON.stringify(body) : undefined,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check the request fields.');
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
    get('copilot-pair').onclick = async () => {
      try {
        get('copilot-link').value = (await api('copilot/pair', 'POST', setup())).url;
        get('copilot-pair-status').textContent = 'Open this private link on your phone. It expires in two hours.';
      } catch (error) { get('copilot-pair-status').textContent = error.message; }
    };
    get('copilot-unpair').onclick = async () => {
      try {
        await api('copilot/pair', 'DELETE');
        get('copilot-link').value = '';
        get('copilot-pair-status').textContent = 'Phone link revoked.';
      } catch (error) { get('copilot-pair-status').textContent = error.message; }
    };
  } else if (pair) {
    let rendered = '';
    async function refresh() {
      try {
        const state = await api('companion/state');
        get('phone-status').textContent = state.state === 'running' ? 'Live session on laptop' : 'Local practice';
        get('phone-question').textContent = state.latest;
        const answers = JSON.stringify(state.answers);
        if (rendered !== answers) {
          rendered = answers;
          get('phone-answers').replaceChildren(...state.answers.slice().reverse().map(answerCard));
        }
        setTimeout(refresh, 1500);
      } catch (error) {
        get('phone-answers').replaceChildren();
        get('phone-question').textContent = '';
        get('phone-status').textContent = error.message + ' Reload after pairing again.';
      }
    }
    refresh();
  }
})();
