const state = { sessions: [], paused: false, analyticsHours: 24, refreshTimer: null, sensorRunning: null, sensorStopArmed: false, sensorStopTimer: null };
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const when = (iso) => new Date(iso).toLocaleString([], {month:'short', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'});
const scoreColor = (score) => score >= 70 ? '#ef5350' : score <= 30 ? '#42d392' : '#ffad55';

async function json(url) {
  const response = await fetch(url, {cache: 'no-store'});
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function refresh() {
  const [summary, sessionData, eventData, analytics, sensor] = await Promise.all([
    json('/api/summary'),
    json('/api/sessions?limit=200'),
    json('/api/events?limit=160'),
    json(`/api/analytics?hours=${state.analyticsHours}`),
    json('/api/sensor/status')
  ]);

  $('metric-sessions').textContent = summary.sessions;
  $('metric-active').textContent = summary.active;
  $('metric-commands').textContent = `${summary.commands} commands captured`;
  $('metric-automated').textContent = summary.likely_automated;
  $('metric-ips').textContent = summary.distinct_ips;
  $('bar-command-value').textContent = summary.commands.toLocaleString();
  $('bar-auth-value').textContent = summary.auth_attempts.toLocaleString();
  const volumeMax = Math.max(1, summary.commands, summary.auth_attempts);
  $('command-bar').style.width = `${Math.max(1, summary.commands / volumeMax * 100)}%`;
  $('auth-bar').style.width = `${Math.max(1, summary.auth_attempts / volumeMax * 100)}%`;

  state.sessions = sessionData.sessions;
  renderSessions();
  renderChart(summary.hourly);
  renderFeed(eventData.events);
  renderAnalytics(analytics);
  renderSensor(sensor);
}

function renderSensor(sensor) {
  const running = Boolean(sensor.running);
  state.sensorRunning = running;
  state.sensorStopArmed = false;
  if (state.sensorStopTimer) clearTimeout(state.sensorStopTimer);
  state.sensorStopTimer = null;
  const status = $('sensor-state');
  const button = $('sensor-toggle');
  status.className = `sensor-state ${running ? 'running' : 'stopped'}`;
  status.innerHTML = `<i></i> Sensor ${running ? 'running' : 'stopped'}`;
  status.title = running ? `${sensor.active_connections || 0} active SSH connections` : 'No new SSH connections are accepted';
  button.className = `sensor-toggle ${running ? 'running' : 'stopped'}`;
  button.textContent = running ? '■ Stop sensor' : '▶ Start sensor';
  button.title = running ? 'Pause the public SSH decoy listener' : 'Resume the public SSH decoy listener';
  button.disabled = false;
  button.setAttribute('aria-pressed', String(running));
}

async function controlSensor() {
  const button = $('sensor-toggle');
  const action = state.sensorRunning ? 'stop' : 'start';
  if (action === 'stop' && !state.sensorStopArmed) {
    state.sensorStopArmed = true;
    button.classList.add('armed');
    button.textContent = 'Confirm stop';
    button.title = 'Click again within 5 seconds. Active decoy sessions will disconnect; the dashboard stays online.';
    state.sensorStopTimer = setTimeout(() => renderSensor({running: true}), 5000);
    return;
  }

  if (state.sensorStopTimer) clearTimeout(state.sensorStopTimer);
  state.sensorStopTimer = null;
  state.sensorStopArmed = false;
  button.disabled = true;
  button.textContent = action === 'stop' ? 'Stopping…' : 'Starting…';
  const expectedRunning = action === 'start';
  const reconcileTimer = setTimeout(() => {
    json('/api/sensor/status')
      .then(sensor => {
        if (Boolean(sensor.running) === expectedRunning) renderSensor(sensor);
      })
      .catch(console.error);
  }, 1000);
  try {
    const response = await fetch(`/api/sensor/${action}`, {
      method: 'POST',
      cache: 'no-store',
      headers: {'X-HoneyView-Action': 'sensor-control'}
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Sensor ${action} failed`);
    renderSensor(payload);
    scheduleRefresh();
  } catch (error) {
    button.disabled = false;
    button.classList.remove('armed');
    button.textContent = 'Retry sensor control';
    $('sensor-state').title = error.message;
    console.error(error);
  } finally {
    clearTimeout(reconcileTimer);
  }
}

function scheduleRefresh() {
  if (state.refreshTimer) return;
  state.refreshTimer = setTimeout(() => {
    state.refreshTimer = null;
    refresh().catch(console.error);
  }, 900);
}

function renderChart(hourly) {
  const map = new Map(hourly.map(row => [new Date(row.hour).getTime(), Number(row.count)]));
  const now = new Date();
  const currentHour = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), now.getUTCHours());
  const points = Array.from({length: 24}, (_, index) => {
    const timestamp = currentHour - (23 - index) * 3600000;
    return {timestamp, count: map.get(timestamp) || 0};
  });
  const width = 720, height = 190, left = 30, right = 10, top = 8, bottom = 24;
  const innerWidth = width - left - right, innerHeight = height - top - bottom;
  const max = Math.max(1, ...points.map(point => point.count));
  const x = index => left + index * innerWidth / (points.length - 1);
  const y = value => top + innerHeight - value / max * innerHeight;
  const path = points.map((point, index) => `${x(index)},${y(point.count)}`).join(' ');
  const area = `${left},${top + innerHeight} ${path} ${left + innerWidth},${top + innerHeight}`;
  const grids = Array.from({length: 5}, (_, index) => {
    const value = Math.round(max * (4 - index) / 4);
    const gy = top + innerHeight * index / 4;
    return `<line class="chart-grid" x1="${left}" y1="${gy}" x2="${width-right}" y2="${gy}"></line><text class="chart-axis" x="0" y="${gy+3}">${value}</text>`;
  }).join('');
  const labels = points.map((point, index) => index % 4 === 0 ? `<text class="chart-axis" x="${x(index)}" y="${height-5}" text-anchor="middle">${new Date(point.timestamp).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</text>` : '').join('');
  const dots = points.map((point, index) => point.count ? `<circle class="chart-dot" cx="${x(index)}" cy="${y(point.count)}" r="2"><title>${new Date(point.timestamp).toLocaleString()} — ${point.count} sessions</title></circle>` : '').join('');
  $('chart').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Attack sessions by hour">${grids}<polygon class="chart-area" points="${area}"></polygon><polyline class="chart-line" points="${path}"></polyline>${dots}${labels}</svg>`;
}

function commandLine(event, sessionMap) {
  if (event.event_type !== 'command') return '';
  const session = sessionMap.get(event.session_id) || {};
  const score = event.classification?.score ?? session.automation_score ?? 0;
  return `<p><time>${new Date(event.occurred_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'})}</time><span class="ip">${escapeHtml(session.source_ip || 'live')}</span><code title="${escapeHtml(event.payload.command)}">$ ${escapeHtml(event.payload.command)}</code><span class="score">${score}%</span></p>`;
}

function renderFeed(events) {
  if (state.paused) return;
  const sessionMap = new Map(state.sessions.map(session => [session.id, session]));
  const lines = events.map(event => commandLine(event, sessionMap)).filter(Boolean).slice(-100);
  $('command-feed').innerHTML = lines.length ? lines.join('') : '<p class="empty">Waiting for attacker activity…</p>';
  $('command-feed').scrollTop = $('command-feed').scrollHeight;
}

function renderSessions() {
  const needle = $('session-search').value.toLowerCase();
  const classFilter = $('class-filter').value;
  const rows = state.sessions.filter(session => {
    const matchesText = !needle || `${session.source_ip} ${session.username} ${session.client_version}`.toLowerCase().includes(needle);
    return matchesText && (!classFilter || session.actor_class === classFilter);
  });
  $('session-result-count').textContent = `${rows.length} result${rows.length === 1 ? '' : 's'}`;
  $('session-rows').innerHTML = rows.map(session => `<tr data-id="${session.id}"><td>${when(session.started_at)}</td><td>${escapeHtml(session.source_ip)}:${session.source_port}</td><td>${escapeHtml(session.username)}</td><td>${escapeHtml(session.client_version || 'unknown')}</td><td>${session.command_count}</td><td><div class="scorebar"><i style="--score:${session.automation_score}%;--score-color:${scoreColor(session.automation_score)}"></i><span>${session.automation_score}%</span></div></td><td><span class="badge ${session.status}">${session.status}</span></td></tr>`).join('') || '<tr><td colspan="7">No matching sessions.</td></tr>';
  document.querySelectorAll('#session-rows tr[data-id]').forEach(row => row.addEventListener('click', () => showSession(row.dataset.id)));
}

function rankList(items, valueKey, labelKey, formatter = value => value) {
  if (!items.length) return '<p class="empty">No activity in this period.</p>';
  const max = Math.max(1, ...items.map(item => Number(item[valueKey]) || 0));
  return items.map(item => `<div class="rank-item"><div><span title="${escapeHtml(item[labelKey])}">${escapeHtml(item[labelKey])}</span><b>${escapeHtml(formatter(item[valueKey], item))}</b></div><span class="rank-track"><i style="width:${(Number(item[valueKey]) || 0) / max * 100}%"></i></span></div>`).join('');
}

function renderAnalytics(data) {
  $('average-duration').textContent = `avg. ${data.average_session_seconds}s/session`;
  $('top-sources').innerHTML = rankList(data.top_sources, 'sessions', 'source_ip', (value, item) => `${value} · ${item.commands || 0}`);
  $('top-users').innerHTML = rankList(data.top_users, 'attempts', 'username');
  $('top-commands').innerHTML = data.top_commands.length ? data.top_commands.map(item => `<div class="rank-item"><div><code title="${escapeHtml(item.command)}">$ ${escapeHtml(item.command)}</code><b>×${item.count}</b></div></div>`).join('') : '<p class="empty">No commands in this period.</p>';

  const labels = {downloaders:'Downloader use', persistence:'Persistence', recon:'Reconnaissance', execution:'Execution'};
  $('indicators').innerHTML = Object.entries(data.indicators).map(([key, value]) => `<div class="indicator"><strong>${value}</strong><span>${labels[key] || key}</span></div>`).join('');

  const counts = Object.fromEntries(data.actor_classes.map(item => [item.actor_class, item.count]));
  const automated = counts.likely_automated || 0;
  const human = counts.likely_human || 0;
  const uncertain = (counts.uncertain || 0) + (counts.unknown || 0);
  const total = automated + human + uncertain;
  const autoDeg = total ? automated / total * 360 : 0;
  const uncertainDeg = total ? uncertain / total * 360 : 0;
  $('actor-donut').style.setProperty('--auto', `${autoDeg}deg`);
  $('actor-donut').style.setProperty('--uncertain', `${uncertainDeg}deg`);
  $('actor-donut').querySelector('strong').textContent = total;
  $('actor-legend').innerHTML = `<p><i style="background:var(--red)"></i><span>Automated</span><b>${automated}</b></p><p><i style="background:var(--orange)"></i><span>Uncertain</span><b>${uncertain}</b></p><p><i style="background:var(--green)"></i><span>Human-like</span><b>${human}</b></p>`;

  const heat = new Map(data.heatmap.map(item => [`${item.weekday}:${item.hour}`, item.count]));
  const maxHeat = Math.max(1, ...data.heatmap.map(item => item.count));
  const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  let cells = '<span></span>' + Array.from({length:24}, (_, hour) => `<span class="heatmap-label">${hour % 3 === 0 ? String(hour).padStart(2,'0') : ''}</span>`).join('');
  for (let day = 0; day < 7; day++) {
    cells += `<span class="heatmap-label">${days[day]}</span>`;
    for (let hour = 0; hour < 24; hour++) {
      const count = heat.get(`${day}:${hour}`) || 0;
      const alpha = count ? (0.18 + count / maxHeat * 0.82).toFixed(2) : '0.04';
      cells += `<span class="heat-cell" style="--alpha:${alpha}" title="${days[day]} ${String(hour).padStart(2,'0')}:00 UTC — ${count} sessions"></span>`;
    }
  }
  $('heatmap').innerHTML = cells;
}

async function showSession(id) {
  const data = await json(`/api/sessions/${encodeURIComponent(id)}`);
  const session = data.session;
  const commands = data.events.filter(event => event.event_type === 'command');
  $('session-detail').innerHTML = `<div class="detail-head"><p class="eyebrow">SESSION ${escapeHtml(session.id.slice(0,8))}</p><h2>${escapeHtml(session.source_ip)} → ${escapeHtml(session.username)}</h2><small>${when(session.started_at)} · ${escapeHtml(session.auth_method)} authentication · ${escapeHtml(session.client_version || 'unknown client')}</small></div><div class="detail-grid"><div><span>Automation score</span><strong style="color:${scoreColor(session.automation_score)}">${session.automation_score}%</strong></div><div><span>Classification</span><strong>${escapeHtml(session.actor_class.replaceAll('_',' '))}</strong></div><div><span>Commands</span><strong>${session.command_count}</strong></div></div><div class="reasons"><strong>Signals:</strong> ${escapeHtml(session.classification_reasons.join(' · ') || 'Awaiting evidence')}</div><div class="detail-events">${commands.length ? commands.map(event => `<p><time>${new Date(event.occurred_at).toLocaleTimeString()}</time> <code>$ ${escapeHtml(event.payload.command)}</code> <small>${event.payload.typed_ms} ms typing · ${event.payload.gap_ms ?? '—'} ms gap</small></p>`).join('') : '<p>No commands captured.</p>'}</div>`;
  $('session-dialog').showModal();
}

function connectStream() {
  const source = new EventSource('/api/stream');
  source.onopen = () => { $('live-dot').classList.add('live'); $('live-state').textContent = 'Live'; };
  source.onerror = () => { $('live-dot').classList.remove('live'); $('live-state').textContent = 'Reconnecting'; };
  source.addEventListener('activity', message => {
    const activity = JSON.parse(message.data);
    if (activity.type === 'command' && !state.paused) {
      const session = state.sessions.find(item => item.id === activity.event.session_id) || {};
      const line = commandLine(activity.event, new Map([[activity.event.session_id, session]]));
      if ($('command-feed').querySelector('.empty')) $('command-feed').innerHTML = '';
      $('command-feed').insertAdjacentHTML('beforeend', line);
      while ($('command-feed').children.length > 100) $('command-feed').firstElementChild.remove();
      $('command-feed').scrollTop = $('command-feed').scrollHeight;
    }
    if (activity.type === 'sensor_status') renderSensor(activity);
    scheduleRefresh();
  });
}

$('pause-feed').addEventListener('click', () => {
  state.paused = !state.paused;
  $('pause-feed').textContent = state.paused ? 'Resume' : 'Pause';
  if (!state.paused) refresh().catch(console.error);
});
$('refresh-dashboard').addEventListener('click', () => refresh().catch(console.error));
$('sensor-toggle').addEventListener('click', controlSensor);
$('session-search').addEventListener('input', renderSessions);
$('class-filter').addEventListener('change', renderSessions);
$('analytics-range').addEventListener('change', event => {
  state.analyticsHours = Number(event.target.value);
  document.querySelectorAll('.toolbar a').forEach(link => {
    const url = new URL(link.href);
    url.searchParams.set('hours', state.analyticsHours);
    link.href = url;
  });
  refresh().catch(console.error);
});
document.querySelector('.dialog-close').addEventListener('click', () => $('session-dialog').close());

const updateClock = () => { $('clock').textContent = new Date().toLocaleTimeString(); };
updateClock();
setInterval(updateClock, 1000);
setInterval(() => refresh().catch(console.error), 15000);
refresh().then(connectStream).catch(error => { $('live-state').textContent = 'Offline'; console.error(error); });
