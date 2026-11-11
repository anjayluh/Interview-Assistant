'use strict';

(() => {
  const fields = ['name', 'identity', 'career_start', 'employment', 'skills', 'achievements', 'notes'];
  let data;
  let busy = false;

  async function api(path = '', options = {}) {
    const response = await fetch('/api/profile' + path, options);
    const result = await response.json();
    if (!response.ok) {
      throw new Error(typeof result.detail === 'string' ? result.detail : 'Check the required fields and length limits.');
    }
    return result;
  }

  function json(method, value) {
    return {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)};
  }

  function message(text, failed = false) {
    $('profile-message').hidden = !text;
    $('profile-message').textContent = text;
    $('profile-message').classList.toggle('profile-error', failed);
  }

  async function run(action) {
    if (busy) return;
    busy = true;
    $('profile-dialog').setAttribute('aria-busy', 'true');
    document.querySelector('.profile-content').inert = true;
    try {
      message('');
      await action();
    } catch (err) {
      message(err.message || 'Could not complete the profile operation.', true);
    } finally {
      busy = false;
      $('profile-dialog').removeAttribute('aria-busy');
      document.querySelector('.profile-content').inert = false;
    }
  }

  function populate(next, manage = $('manage-cv').value) {
    const interview = $('interview-cv').value;
    const wasLoaded = Boolean(data);
    data = next;
    $('experience-claim').textContent = next.experience.claim;
    for (const [id, label] of [['interview-cv','Master profile only'], ['manage-cv','New CV version']]) {
      const select = $(id);
      select.replaceChildren(new Option(label, ''));
      for (const cv of next.cvs) {
        const suffix = cv.id === next.default_cv ? ' (default)' : '';
        select.add(new Option(cv.name + suffix, cv.id));
      }
    }
    $('interview-cv').value = wasLoaded && (!interview || next.cvs.some(cv => cv.id === interview))
      ? interview : next.default_cv || '';
    $('interview-cv').dataset.loaded = 'true';
    $('manage-cv').value = next.cvs.some(cv => cv.id === manage) ? manage : '';
    showSelection();
  }

  function showSelection() {
    const select = $('interview-cv');
    $('interview-selection').textContent = select.selectedOptions[0]?.textContent || '';
  }

  function cvFields() {
    return {name: $('cv-name').value, facts: $('cv-facts').value};
  }

  function cvPath() {
    const id = $('manage-cv').value;
    return id ? '/cvs/' + encodeURIComponent(id) : '/cvs';
  }

  function comparison(items) {
    const box = $('cv-comparison');
    box.replaceChildren();
    box.hidden = !items;
    if (!items) return;
    const title = document.createElement('h3');
    title.textContent = 'Review differences';
    box.append(title);
    if (!items.length) {
      const text = document.createElement('p');
      text.textContent = 'No differences found in the compared lines.';
      box.append(text);
      return;
    }
    for (const item of items) {
      const text = document.createElement('p');
      const label = document.createElement('strong');
      label.textContent = item.kind;
      text.append(label, ' · ' + item.candidate);
      if (item.existing) {
        const prior = document.createElement('small');
        prior.textContent = [item.source, item.existing].join(' · ');
        text.append(prior);
      }
      box.append(text);
    }
  }

  async function loadCV() {
    const id = $('manage-cv').value;
    const cv = id ? await api(cvPath()) : {name: '', facts: ''};
    $('cv-name').value = cv.name;
    $('cv-facts').value = cv.facts;
    $('cv-file').value = '';
    $('default-cv').disabled = !id;
    $('default-cv').textContent = id && id === data.default_cv ? 'Clear default' : 'Set as default';
    $('remove-cv').disabled = !id;
    comparison(null);
  }

  document.addEventListener('interview-ended', () => {
    for (const id of ['job-title', 'job-company', 'job-description', 'job-notes']) $(id).value = '';
    $('interview-cv').value = data?.default_cv || '';
    showSelection();
  });
  $('interview-cv').onchange = showSelection;
  $('open-profile').onclick = () => {
    $('profile-dialog').showModal();
    run(async () => {
      populate(await api());
      for (const field of fields) $(`profile-${field}`).value = data.profile[field];
      await loadCV();
    });
  };
  $('close-profile').onclick = () => $('profile-dialog').close();
  $('manage-cv').onchange = () => run(loadCV);
  $('save-profile').onclick = () => run(async () => {
    const profile = Object.fromEntries(fields.map(field => [field, $(`profile-${field}`).value]));
    populate(await api('', json('PUT', profile)));
    message('Master professional profile saved.');
  });
  $('preview-cv').onclick = () => run(async () => {
    const file = $('cv-file').files[0];
    if (!file) throw new Error('Choose a PDF or DOCX file first.');
    const query = new URLSearchParams({filename: file.name});
    if ($('manage-cv').value) query.set('cv_id', $('manage-cv').value);
    const result = await api('/preview?' + query, {method: 'POST', body: file});
    $('cv-name').value ||= result.name;
    $('cv-facts').value = result.facts;
    comparison(result.comparison);
    message('Review the extracted text and remove contact details before saving.');
  });
  $('compare-cv').onclick = () => run(async () => {
    const id = $('manage-cv').value;
    const result = await api('/compare' + (id ? '?cv_id=' + encodeURIComponent(id) : ''), json('POST', cvFields()));
    comparison(result.comparison);
  });
  $('save-cv').onclick = () => run(async () => {
    const saved = await api(cvPath(), json($('manage-cv').value ? 'PUT' : 'POST', cvFields()));
    populate(saved, saved.id);
    await loadCV();
    message('CV version saved.');
  });
  $('default-cv').onclick = () => run(async () => {
    const id = $('manage-cv').value;
    if (!id) return;
    const next = await api('/default', json('PUT', {id: id === data.default_cv ? null : id}));
    populate(next, id);
    $('default-cv').textContent = next.default_cv ? 'Clear default' : 'Set as default';
    if (currentState === 'idle') {
      $('interview-cv').value = next.default_cv || '';
      showSelection();
    }
    message(next.default_cv ? 'Default CV saved.' : 'Default CV cleared.');
  });
  $('remove-cv').onclick = () => run(async () => {
    if (!$('manage-cv').value || !confirm('Delete this saved CV version?')) return;
    populate(await api(cvPath(), {method: 'DELETE'}), '');
    await loadCV();
    message('CV version deleted.');
  });

  api().then(populate).catch(() => error('Professional profile could not be loaded. Restart the app and try again.'));
})();
