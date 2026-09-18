const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let state = null;
let busy = false;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = `show${error ? ' error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = '', 3500);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {'Content-Type':'application/json', ...(options.headers || {})},
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function duration(seconds) {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}

function render() {
  const server = state.server;
  const running = server.state === 'running';
  $('#server-state').textContent = server.health === 'healthy' ? 'Running & healthy' : server.state;
  $('#server-detail').textContent = server.status || (running ? 'Container is active.' : 'Container is stopped.');
  $('#server-indicator').className = `status-orb ${server.state}`;
  $('#live-dot').className = `dot ${running ? 'up' : ''}`;
  $('#live-label').textContent = running ? 'Local stack online' : 'Portal online';
  $('#metric-players').textContent = state.players.filter(player => ['starting','running','pausing','paused'].includes(player.runtime.state)).length;
  $('#metric-players-note').textContent = `active · ${state.players.length} configured`;
  $('#metric-backups').textContent = state.backups.length;
  $('#metric-version').textContent = state.configuration.environment.FACTORIO_VERSION || '—';

  const form = $('#configuration-form');
  const env = state.configuration.environment;
  if (!form.contains(document.activeElement)) {
    for (const key of ['FACTORIO_VERSION','FACTORIO_PORT','FACTORIO_RCON_PORT']) form.elements[key].value = env[key] || '';
    form.elements.DLC_SPACE_AGE.checked = env.DLC_SPACE_AGE === 'true';
    form.elements.server_settings.value = JSON.stringify(state.configuration.server_settings, null, 2);
  }

  $('#backup-list').innerHTML = state.backups.length ? state.backups.map(backup => `
    <div class="backup-item">
      <div><strong>${escapeHtml(backup.name)}</strong><small>${formatBytes(backup.size)} · ${new Date(backup.created_at * 1000).toLocaleString()}</small></div>
      <button data-restore="${escapeHtml(backup.name)}">Restore</button>
    </div>`).join('') : '<p class="muted">No backups yet.</p>';

  const playersList = $('#players-list');
  if (!playersList.contains(document.activeElement)) playersList.innerHTML = state.players.map(player => {
    const runtime = player.runtime;
    const agent = runtime.agent;
    const gameStats = runtime.game?.statistics || {};
    const actions = runtime.game?.actions || [];
    const events = player.statistics.events.slice(0, 8);
    return `<article class="card player-card" data-player="${escapeHtml(player.id)}">
      <div class="player-sidebar">
        <div class="player-title"><div class="avatar">${escapeHtml(player.name.slice(0,1).toUpperCase())}</div><div><h3>${escapeHtml(player.name)}</h3><span class="runtime-state ${runtime.state}">${escapeHtml(runtime.state)}${runtime.pid ? ` · PID ${runtime.pid}` : ''}</span></div></div>
        ${runtime.error ? `<p class="muted">${escapeHtml(runtime.error)}</p>` : ''}
        <div class="button-row">
          <button class="primary" data-player-action="start" ${['running','starting','pausing','paused'].includes(runtime.state) ? 'disabled' : ''}>Start player</button>
          <button data-player-action="${runtime.state === 'paused' ? 'resume' : 'pause'}" ${!agent || !['running','paused'].includes(runtime.state) ? 'disabled' : ''}>${runtime.state === 'paused' ? 'Resume' : 'Pause'}</button>
          <button data-player-action="stop" ${!['running','starting','pausing','paused','stopping'].includes(runtime.state) ? 'disabled' : ''}>Stop</button>
        </div>
        <div class="stats">
          <div><strong>${player.statistics.runs}</strong><span>RUNS</span></div>
          <div><strong>${duration(player.statistics.online_seconds)}</strong><span>ONLINE</span></div>
          <div><strong>${Math.round(gameStats.distance_tiles || 0)}</strong><span>TILES WALKED</span></div>
          <div><strong>${gameStats.moves_completed || 0}</strong><span>MOVES DONE</span></div>
          <div><strong>${gameStats.items_mined || 0}</strong><span>ITEMS MINED</span></div>
          <div><strong>${gameStats.items_crafted || 0}</strong><span>ITEMS CRAFTED</span></div>
          <div><strong>${gameStats.entities_placed || 0}</strong><span>PLACED</span></div>
          <div><strong>${gameStats.items_transferred || 0}</strong><span>TRANSFERRED</span></div>
        </div>
      </div>
      <div class="player-main">
        <div class="activity"><div class="card-label">AGENT STATUS</div>${agent ? `
          <div class="event"><time>${escapeHtml(agent.phase)}</time><span>${escapeHtml(agent.objective?.item || '')} ${escapeHtml(agent.verified?.count ?? '')}</span><code>${escapeHtml(agent.objective?.text || '')}</code></div>
          <div class="event"><time>${escapeHtml(agent.decisions)} decisions</time><span>${escapeHtml(agent.failures)} failures · ${escapeHtml(agent.recoveries)} recoveries</span><code>${escapeHtml(agent.last_action ? `${agent.last_action.action}: ${agent.last_action.reason}` : 'Waiting for the first decision')}</code></div>
        ` : '<p class="muted">Start an Ollama-backed player to see its current task and reasoning.</p>'}</div>
        <form class="player-form">
          <div class="player-grid">
            <label>Name<input name="name" value="${escapeHtml(player.name)}"></label>
            <label>Model / driver<input name="model" value="${escapeHtml(player.model)}" placeholder="scripted"></label>
            <label class="wide">Instructions<textarea name="instructions" rows="4">${escapeHtml(player.instructions)}</textarea></label>
            <label class="toggle-row wide"><input name="auto_start" type="checkbox" ${player.auto_start ? 'checked' : ''}><span>Start automatically with the portal</span></label>
          </div>
          <div class="button-row end"><button class="primary" type="submit">Save player</button></div>
        </form>
        <div class="activity"><div class="card-label">GAME ACTIONS</div>${actions.length ? actions.map(action => `
          <div class="event action"><time>#${escapeHtml(action.id.slice(0,8))}</time><span class="action-${escapeHtml(action.state)}">${escapeHtml(action.state)}</span><code>${escapeHtml(action.type)} → ${escapeHtml(JSON.stringify(action.target || {}))}${action.error ? ` · ${escapeHtml(action.error)}` : ''}</code></div>`).join('') : '<p class="muted">No game actions recorded yet.</p>'}</div>
        <div class="activity"><div class="card-label">PROCESS ACTIVITY</div>${events.length ? events.map(event => `
          <div class="event"><time>${new Date(event.created_at).toLocaleTimeString()}</time><span>${escapeHtml(event.kind)}</span><code>${escapeHtml(JSON.stringify(event.payload).slice(0,120))}</code></div>`).join('') : '<p class="muted">No activity recorded yet.</p>'}</div>
      </div>
    </article>`;
  }).join('');
}

async function load(showError = true) {
  try {
    state = await request('/api/state');
    render();
  } catch (error) {
    if (showError) toast(error.message, true);
    $('#live-label').textContent = 'Portal unavailable';
  }
}

async function operation(callback, success) {
  if (busy) return;
  busy = true;
  $$('button').forEach(button => button.disabled = true);
  try {
    await callback();
    toast(success);
    await load();
    await loadLogs();
  } catch (error) {
    toast(error.message, true);
  } finally {
    busy = false;
    if (state) render();
  }
}

function confirmAction(title, message) {
  const dialog = $('#confirm-dialog');
  $('#confirm-title').textContent = title;
  $('#confirm-message').textContent = message;
  dialog.showModal();
  return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true}));
}

async function serverAction(action, extra = {}) {
  await operation(() => request('/api/server/action', {method:'POST', body:JSON.stringify({action, ...extra})}), `${action[0].toUpperCase() + action.slice(1)} completed.`);
}

async function loadLogs() {
  try { $('#logs').textContent = (await request('/api/logs')).logs || 'No log output.'; }
  catch (error) { $('#logs').textContent = error.message; }
}

document.addEventListener('click', async event => {
  const serverButton = event.target.closest('[data-server-action]');
  if (serverButton) {
    const action = serverButton.dataset.serverAction;
    if (['stop','restart'].includes(action) && !await confirmAction(`${action} server?`, 'Active AI players will be stopped first.')) return;
    serverAction(action);
    return;
  }
  const restore = event.target.closest('[data-restore]');
  if (restore) {
    if (await confirmAction('Restore this backup?', 'Current data will be checkpointed, then replaced. Active AI players will stop.')) serverAction('restore', {backup:restore.dataset.restore});
    return;
  }
  const playerButton = event.target.closest('[data-player-action]');
  if (playerButton) {
    const card = playerButton.closest('[data-player]');
    const action = playerButton.dataset.playerAction;
    operation(() => request(`/api/players/${card.dataset.player}/${action}`, {method:'POST',body:'{}'}), `Player ${action} requested.`);
  }
});

$('#configuration-form').addEventListener('submit', event => {
  event.preventDefault();
  const form = event.currentTarget;
  let settings;
  try { settings = JSON.parse(form.elements.server_settings.value); }
  catch { return toast('Server settings must be valid JSON.', true); }
  const environment = {
    FACTORIO_VERSION: form.elements.FACTORIO_VERSION.value,
    FACTORIO_BIND: '127.0.0.1',
    FACTORIO_PORT: form.elements.FACTORIO_PORT.value,
    FACTORIO_RCON_PORT: form.elements.FACTORIO_RCON_PORT.value,
    DLC_SPACE_AGE: String(form.elements.DLC_SPACE_AGE.checked),
  };
  operation(() => request('/api/server/configuration', {method:'PUT',body:JSON.stringify({environment,server_settings:settings})}), 'Configuration saved.');
});

$('#players-list').addEventListener('submit', event => {
  const form = event.target.closest('.player-form');
  if (!form) return;
  event.preventDefault();
  const id = form.closest('[data-player]').dataset.player;
  const payload = {name:form.elements.name.value,model:form.elements.model.value,instructions:form.elements.instructions.value,auto_start:form.elements.auto_start.checked};
  operation(() => request(`/api/players/${id}`, {method:'PUT',body:JSON.stringify(payload)}), 'Player settings saved.');
});

$('#reset-world').addEventListener('click', async () => {
  if (!await confirmAction('Reset the test world?', 'A backup will be created automatically. The current world will be replaced on next start.')) return;
  serverAction('reset', {seed:$('#reset-seed').value.trim()});
});
$('#refresh').addEventListener('click', () => load());
$('#refresh-logs').addEventListener('click', loadLogs);

load();
loadLogs();
setInterval(() => { if (!busy && !document.querySelector('input:focus,textarea:focus')) load(false); }, 5000);
