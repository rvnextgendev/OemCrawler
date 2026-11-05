'use strict';
const $ = id => document.getElementById(id);
let apiKey = '', token = '', selected = null, timer = null, busy = false, generation = 0;
let availableSites = [];
const activeStates = new Set(['queued', 'running', 'deferred']);
function notice(message, error = false) {
  $('notice').hidden = false; $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function reset() {
  generation++; apiKey = ''; token = ''; selected = null; clearTimeout(timer);
  $('workspace').hidden = true; $('login-panel').hidden = false;
  $('details').hidden = true; $('results').replaceChildren(); $('raw-json').textContent = '';
  $('jobs-body').replaceChildren(); $('connection').textContent = 'Signed out';
  $('api-key').value = ''; $('password').value = ''; $('auto-refresh').checked = false;
}
async function request(path, method = 'GET', body) {
  const version = generation;
  const headers = {'X-API-Key': apiKey};
  if (token) headers.Authorization = 'Bearer ' + token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(35000)});
  let data; try { data = await response.json(); } catch { throw new Error('Unexpected server response'); }
  if (version !== generation) throw new Error('Session changed; please retry');
  if (!response.ok) {
    if (response.status === 401 && token) reset();
    throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  }
  return data;
}
function renderJobs(jobs) {
  $('jobs-body').replaceChildren(); $('empty').hidden = jobs.length > 0;
  $('total-count').textContent = jobs.length;
  $('done-count').textContent = jobs.filter(j => j.state === 'completed').length;
  $('active-count').textContent = jobs.filter(j => activeStates.has(j.state)).length;
  $('attention-count').textContent = jobs.filter(j => ['blocked','failed','login_required','no_content'].includes(j.state)).length;
  for (const job of jobs) {
    const row = node('tr'), source = node('td');
    source.append(node('div', job.id.slice(0, 10), 'job-name'));
    source.append(node('div', job.urls?.[0] || 'Source unavailable', 'source'));
    if (job.created_at) source.append(node('small', new Date(job.created_at * 1000).toLocaleString()));
    const state = node('td'); state.append(node('span', job.state.replaceAll('_',' '), 'badge ' + job.state));
    const action = node('td'), button = node('button', 'View', 'secondary');
    button.type = 'button'; button.setAttribute('aria-label', 'View job ' + job.id);
    button.addEventListener('click', () => openJob(job.id).catch(error => notice(error.message, true)));
    action.append(button); row.append(source, node('td', job.engine), state, action); $('jobs-body').append(row);
  }
  $('updated').textContent = 'Refreshed ' + new Date().toLocaleTimeString();
}
async function refreshJobs() { renderJobs((await request('/jobs')).jobs); }
function renderDetails(job) {
  selected = job; $('details').hidden = false; $('detail-title').textContent = 'Job ' + job.id.slice(0, 10);
  $('detail-meta').replaceChildren(node('span', job.state.replaceAll('_', ' '), 'badge ' + job.state), node('span', 'Engine: ' + job.engine), node('span', 'Pages saved: ' + job.results.length));
  const messages = {
    queued: 'Waiting for the acquisition worker.', running: 'Collection is in progress. Refresh to see the final result.',
    deferred: 'Waiting for the configured acquisition window.', completed: 'Extraction finished. Review the source text and any enrichment below.',
    blocked: 'The source blocked collection. Review permissions and site policy before creating another job.',
    login_required: 'Login could not be confirmed. Review the server-side login adapter.',
    no_content: 'No HTML content was saved. Inspect skipped URLs and coverage in the JSON record.',
    failed: 'Extraction failed. Inspect the worker logs for details.'
  };
  $('job-message').textContent = (messages[job.state] || job.state) + (job.error ? ' Error: ' + job.error : '') + (job.coverage ? ' ' + job.coverage : '') + (job.limit_reached ? ' Page/depth scope was limited; this is not a full-site export.' : '');
  $('results').replaceChildren();
  for (const [index, result] of job.results.entries()) {
    const card = node('article', undefined, 'result-card');
    card.append(node('h3', `Page ${index + 1} · ${result.url}`));
    const metadata = node('dl');
    for (const [label, value] of [['Cache', result.cached ? 'Served from cache' : 'Fresh acquisition'], ['SHA-256', result.sha256], ['Stored path', result.stored_path]]) {
      metadata.append(node('dt', label), node('dd', value));
    }
    card.append(metadata, node('h3', 'Extracted text'), node('pre', result.text || 'No extracted text.'));
    if (result.enrichment) card.append(node('h3', 'Enrichment · separate from source'), node('pre', JSON.stringify(result.enrichment, null, 2)));
    $('results').append(card);
  }
  $('raw-json').textContent = JSON.stringify(job, null, 2);
  schedule();
}
function schedule() {
  clearTimeout(timer);
  if (!$('auto-refresh').checked || !selected || !activeStates.has(selected.state) || !token || document.hidden) return;
  timer = setTimeout(async () => {
    if (busy) { schedule(); return; }
    busy = true;
    try { renderDetails(await request('/jobs/' + encodeURIComponent(selected.id))); await refreshJobs(); }
    catch(error) { $('auto-refresh').checked = false; notice(error.message, true); }
    finally { busy = false; }
  }, 15000);
}
async function openJob(id) {
  clearTimeout(timer);
  renderDetails(await request('/jobs/' + encodeURIComponent(id)));
  $('details').scrollIntoView({behavior:'smooth', block:'start'});
}
$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); $('login-button').disabled = true;
  apiKey = $('api-key').value.trim();
  try {
    const data = await request('/token', 'POST', {username: $('username').value, password: $('password').value});
    token = data.access_token; $('api-key').value = ''; $('password').value = '';
    $('login-panel').hidden = true; $('workspace').hidden = false;
    $('connection').textContent = 'Connected · ' + $('username').value;
    notice('Connected. Submit an extraction or inspect a recent job.'); await refreshJobs(); await loadSites();
  } catch(error) { notice(error.message, true); if (!token) apiKey = ''; }
  finally { $('login-button').disabled = false; }
});
$('job-form').addEventListener('submit', async event => {
  event.preventDefault();
  const urls = $('urls').value.split('\n').map(s => s.trim()).filter(Boolean);
  if (urls.length < 1 || urls.length > 10) { notice('Enter between 1 and 10 source URLs.', true); return; }
  $('submit-button').disabled = true;
  try {
    const created = await request('/jobs', 'POST', {urls, engine: $('engine').value, enrich: $('enrich').checked, max_pages: Number($('max-pages').value), max_depth: Number($('max-depth').value), site_id: $('preset').value === 'auto' ? null : $('preset').value});
    notice('Extraction queued. Select Refresh to follow progress, or enable auto-refresh.');
    await refreshJobs(); await openJob(created.job_id);
  } catch(error) { notice(error.message, true); }
  finally { $('submit-button').disabled = false; }
});
$('refresh').addEventListener('click', async () => {
  $('refresh').disabled = true;
  try { await refreshJobs(); if (selected) renderDetails(await request('/jobs/' + encodeURIComponent(selected.id))); }
  catch(error) { notice(error.message, true); }
  finally { $('refresh').disabled = false; }
});
$('lookup-form').addEventListener('submit', event => {
  event.preventDefault(); openJob($('job-id').value.trim()).catch(error => notice(error.message, true));
});
$('logout').addEventListener('click', async () => {
  $('logout').disabled = true;
  try { await request('/logout', 'POST'); reset(); notice('Signed out. Your token has been revoked.'); }
  catch(error) { reset(); notice('Cleared this tab. Server revocation was not confirmed; the token expires within one hour.', true); }
  finally { $('logout').disabled = false; }
});
$('auto-refresh').addEventListener('change', () => {
  if ($('auto-refresh').checked) notice('Auto-refresh checks the active job every 15 seconds. API quotas include refresh requests.');
  schedule();
});
document.addEventListener('visibilitychange', schedule);
$('download').addEventListener('click', () => {
  if (!selected) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(selected, null, 2)], {type:'application/json'}));
  const a = node('a'); a.href = url; a.download = 'oem-job-' + selected.id + '.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function loadSites() {
  const data = await request('/sites');
  availableSites = data.sites;
  $('preset').replaceChildren();
  const auto = node('option', 'Auto · match URL to a configured website'); auto.value = 'auto';
  $('preset').append(auto);
  for (const site of availableSites) {
    const option = node('option', site.name); option.value = site.id; $('preset').append(option);
  }
}
$('preset').addEventListener('change', () => {
  const site = availableSites.find(s => s.id === $('preset').value);
  $('engine').value = 'auto';
  if (!site) {
    $('source-hint').textContent = 'Paste a public HTTPS URL. Auto uses its public profile; choose an explicit profile for login sites. Unknown public origins require ALLOW_PUBLIC_URLS=true.';
    return;
  }
  $('urls').value = site.start_url;
  $('source-hint').textContent = site.mode === 'mock'
    ? 'Mock OEM uses the Playwright login adapter and server-side credentials.'
    : site.mode === 'form_login'
    ? 'Basic HTML form login using server-side credentials, CSRF hidden fields and encrypted cookie reuse. SSO, MFA and JavaScript login require a reviewed adapter.'
    : 'Public HTML text, headings, tables and links. No website login. Robots rules and pacing apply. JavaScript-only content and PDFs are excluded.';
});
// Pasting another website should not retain an unrelated explicit profile.
$('urls').addEventListener('input', () => { $('preset').value = 'auto'; $('engine').value = 'auto'; });
fetch('/ui/config', {cache:'no-store'}).then(response => {
  if (!response.ok) throw new Error('Configuration unavailable');
  return response.json();
}).then(config => {
  if (config.demo_mode) {
    $('api-key').required = false;
    $('api-key').placeholder = 'Optional in local demo mode';
    $('username').value = 'demo';
    $('password').value = 'demo123';
    $('login-hint').textContent = 'Local demo: username demo · password demo123 · API key optional. Do not expose demo mode publicly.';
  }
}).catch(() => { $('login-hint').textContent = 'Use API_KEY, APP_USER and APP_PASSWORD from .env. Could not load demo configuration.'; });
