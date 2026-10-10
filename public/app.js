const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = pence => new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP' }).format(pence / 100);
const labels = { booked: 'Booked', assigned: 'Assigned', collected: 'Collected', in_transit: 'In transit', delivered: 'Delivered', cancelled: 'Cancelled', standard: 'Standard', express: 'Express', same_day: 'Same day' };
const badge = status => `<span class="status ${esc(status)}">${esc(labels[status] || status)}</span>`;
const date = value => new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
let shipments = [], drivers = [], currentView = 'overview', signedIn = false, toastTimer;

async function api(path, method = 'GET', data) {
  const response = await fetch(`/api${path}`, { method, credentials: 'same-origin', headers: method === 'GET' ? {} : { 'Content-Type': 'application/json', 'X-Courier-Client': 'couriercloud' }, body: data === undefined ? undefined : JSON.stringify(data) });
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && signedIn) { signedIn = false; $('#workspace').hidden = true; $('#login-screen').hidden = false; }
    throw new Error(body.error || 'Something went wrong.');
  }
  return body;
}
function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => $('#toast').hidden = true, 4000); }
function view(name) {
  currentView = name;
  $$('.view').forEach(el => el.hidden = el.id !== `${name}-view`);
  $$('.nav-button').forEach(el => el.classList.toggle('active', el.dataset.view === name));
  const titles = { overview: ['Keep things moving.', 'A clear view of every delivery, from booking to doorstep.'], shipments: ['Every shipment, in sight.', 'Book, assign and move deliveries through each stage.'], drivers: ['The team behind every delivery.', 'Manage your drivers and their current workload.'], tracking: ['Follow every step.', 'Check the progress of a delivery using its tracking link.'] };
  $('#page-title').textContent = titles[name][0]; $('#page-description').textContent = titles[name][1];
  $('#breadcrumb').textContent = name[0].toUpperCase() + name.slice(1);
  $('#new-booking').hidden = name === 'tracking';
}
function table(rows) {
  if (!rows.length) return '<div class="empty">No shipments here yet. Create a booking to get started.</div>';
  return `<div class="table-wrap"><table><thead><tr><th>REFERENCE / CUSTOMER</th><th>DELIVERY</th><th>SERVICE</th><th>STATUS</th><th>DRIVER</th><th>CHARGE</th><th></th></tr></thead><tbody>${rows.map(s => `<tr><td><button class="reference" data-shipment="${esc(s.id)}">${esc(s.id)}</button><span class="cell-sub">${esc(s.customer)}</span></td><td>${esc(s.destination.split(',')[0])}<span class="cell-sub">${esc(s.destination)}</span></td><td>${esc(labels[s.service])}</td><td>${badge(s.status)}</td><td>${esc(s.driver_name || 'Unassigned')}</td><td>${money(s.price_pence)}</td><td><button class="text-button" data-shipment="${esc(s.id)}" aria-label="Open shipment ${esc(s.id)}">↗</button></td></tr>`).join('')}</tbody></table></div>`;
}
function filterTable() {
  const query = $('#search').value.toLowerCase(), status = $('#status-filter').value;
  $('#shipments-table').innerHTML = table(shipments.filter(s => (!status || s.status === status) && [s.id, s.customer, s.pickup, s.destination].some(v => v.toLowerCase().includes(query))));
}
async function refresh() {
  const [jobs, team, stats] = await Promise.all([api('/shipments'), api('/drivers'), api('/stats')]);
  shipments = jobs; drivers = team;
  $('#nav-count').textContent = stats.active;
  const cards = [['Active shipments', stats.active, 'In progress across your team', '↗'], ['Delivered today', stats.delivered_today, 'Confirmed deliveries · UTC day', '✓'], ['Awaiting assignment', stats.unassigned, 'Ready for a driver', '◎'], ['Booking value', money(stats.booked_value_pence), 'Non-cancelled bookings · excludes VAT', '£']];
  $('#stats').innerHTML = cards.map(([title, value, note, icon]) => `<article class="stat"><div class="stat-title">${title}<span class="stat-icon">${icon}</span></div><p class="stat-value">${value}</p><span class="stat-note">${note}</span></article>`).join('');
  $('#waiting-number').textContent = stats.unassigned;
  const max = Math.max(1, ...stats.daily.map(d => d.count));
  $('#volume-chart').innerHTML = stats.daily.map((d, i) => { const height = Math.max(2, Math.round(d.count / max * 130)); return `<div class="chart-column"><span class="chart-value">${d.count}</span><svg class="bar-graphic" viewBox="0 0 50 140" role="img" aria-label="${esc(d.date)}: ${d.count} bookings"><rect x="5" y="${140 - height}" width="40" height="${height}" rx="5" fill="${i === 6 ? '#afc758' : '#dce8bc'}"/></svg><span class="chart-label">${new Date(`${d.date}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'UTC' })}</span></div>`; }).join('');
  $('#recent-table').innerHTML = table(shipments.slice(0, 5)); filterTable(); renderDrivers();
}
function renderDrivers() {
  $('#drivers-grid').innerHTML = drivers.length ? drivers.map(d => `<article class="card driver-card"><span class="avatar">${esc(d.name.split(' ').map(n => n[0]).slice(0, 2).join(''))}</span><h3>${esc(d.name)}</h3><div class="driver-meta">${esc(d.vehicle)}<br>${esc(d.phone)}</div><div class="driver-footer"><span>${d.active_jobs} active job${d.active_jobs === 1 ? '' : 's'} · ${d.active ? 'Available' : 'Off duty'}</span><button class="driver-toggle" data-driver="${esc(d.id)}" data-active="${d.active ? 'false' : 'true'}">${d.active ? 'Set off duty' : 'Set available'}</button></div></article>`).join('') : '<div class="empty">Add your first driver to start assigning deliveries.</div>';
}
function timeline(events, privateView = false) {
  return `<ol class="timeline">${events.map(e => `<li><strong>${esc(labels[e.status])}</strong><small>${date(e.created_at)}</small>${privateView ? `<p>${esc(e.note)}</p>` : ''}</li>`).join('')}</ol>`;
}
async function detail(shipmentId, keepOpen = false) {
  const s = await api(`/shipments/${shipmentId}`);
  const trackingUrl = `${location.origin}/#track=${s.tracking_token}`;
  const next = { assigned: 'collected', collected: 'in_transit', in_transit: 'delivered' }[s.status];
  $('#shipment-detail').innerHTML = `<div class="dialog-heading"><div><p class="eyebrow">SHIPMENT DETAILS</p><h2>${esc(s.id)}</h2></div><button class="icon-button" data-close aria-label="Close">×</button></div>${badge(s.status)}<div class="detail-meta"><span>${esc(s.customer)}<br>${esc(s.email)}</span><span>${s.weight} kg · ${esc(labels[s.service])}<br>${money(s.price_pence)} excl. VAT</span></div><div class="detail-addresses"><div><small>COLLECTION</small><p>${esc(s.pickup)}</p></div><div><small>DELIVERY</small><p>${esc(s.destination)}</p></div>${s.notes ? `<div><small>NOTES</small><p>${esc(s.notes)}</p></div>` : ''}</div><div class="tracking-link-row"><input id="share-link" readonly value="${esc(trackingUrl)}" aria-label="Shareable tracking link"><button class="button secondary" id="copy-link">Copy tracking link</button></div>${timeline(s.events, true)}${s.recipient ? `<p>Received by <strong>${esc(s.recipient)}</strong></p>` : ''}<div class="detail-actions">${['booked', 'assigned'].includes(s.status) ? `<form id="assign-form"><label>Assign to driver<select name="driver_id" required><option value="">Select an available driver</option>${drivers.filter(d => d.active).map(d => `<option value="${esc(d.id)}" ${d.id === s.driver_id ? 'selected' : ''}>${esc(d.name)} · ${d.active_jobs} active</option>`).join('')}</select></label><button class="button primary" type="submit">${s.status === 'assigned' ? 'Reassign driver' : 'Assign driver'} →</button></form>` : ''}${next ? `<form id="status-form"><input type="hidden" name="status" value="${next}">${next === 'delivered' ? '<label>Received by<input name="recipient" required maxlength="100" placeholder="Recipient name"></label>' : ''}<label>Progress note (optional)<input name="note" maxlength="500" placeholder="Add a delivery update"></label><button type="submit" class="button primary">Mark ${esc(labels[next].toLowerCase())} →</button></form>` : ''}${['booked', 'assigned'].includes(s.status) ? '<button id="cancel-shipment" class="text-button">Cancel booking</button>' : ''}<p class="error" id="detail-error" role="alert"></p></div>`;
  $('#copy-link').addEventListener('click', async () => { try { await navigator.clipboard.writeText(trackingUrl); toast('Tracking link copied.'); } catch { $('#share-link').select(); toast('Select and copy the tracking link.'); } });
  const action = async (fn) => { try { await fn(); await refresh(); await detail(s.id, true); } catch (e) { $('#detail-error').textContent = e.message; } };
  $('#assign-form')?.addEventListener('submit', e => { e.preventDefault(); action(() => api(`/shipments/${s.id}/assign`, 'POST', Object.fromEntries(new FormData(e.currentTarget)))); });
  $('#status-form')?.addEventListener('submit', e => { e.preventDefault(); action(() => api(`/shipments/${s.id}/status`, 'POST', Object.fromEntries(new FormData(e.currentTarget)))); });
  $('#cancel-shipment')?.addEventListener('click', () => { if (confirm('Cancel this booking?')) action(() => api(`/shipments/${s.id}/status`, 'POST', { status: 'cancelled' })); });
  if (!keepOpen) $('#shipment-dialog').showModal();
}
async function enter(user) {
  signedIn = true; $('#login-screen').hidden = true; $('#workspace').hidden = false; $('#workspace').classList.remove('tracking-public');
  $('#public-signin').hidden = true;
  $('#username').textContent = user.username; $('#mode').textContent = user.demo ? 'LOCAL DEMO' : 'WORKSPACE';
  view('overview'); await refresh();
}
async function track(raw) {
  let token = raw.trim();
  if (token.includes('#track=')) token = token.split('#track=')[1];
  $('#tracking-result').textContent = 'Checking delivery…';
  try {
    const s = await api(`/track/${encodeURIComponent(token)}`);
    $('#tracking-result').innerHTML = `<div class="tracking-summary">${badge(s.status)}<h3>${esc(s.id)}</h3><p class="muted">${esc(labels[s.service])} service · Booked ${date(s.created_at)}</p>${timeline(s.events)}</div>`;
  } catch (e) { $('#tracking-result').innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}
function publicTracking() {
  $('#login-screen').hidden = true; $('#workspace').hidden = false; $('#workspace').classList.toggle('tracking-public', !signedIn); view('tracking');
  $('#public-signin').hidden = signedIn;
}
document.addEventListener('click', e => {
  const close = e.target.closest('[data-close]'); if (close) close.closest('dialog').close();
  const shipment = e.target.closest('[data-shipment]'); if (shipment) detail(shipment.dataset.shipment).catch(e => toast(e.message));
  const nav = e.target.closest('[data-view]'); if (nav) view(nav.dataset.view);
  const driver = e.target.closest('[data-driver]'); if (driver) api(`/drivers/${driver.dataset.driver}`, 'PATCH', { active: driver.dataset.active === 'true' }).then(refresh).catch(e => toast(e.message));
});
$('#login-form').addEventListener('submit', async e => {
  e.preventDefault(); const button = $('button[type=submit]', e.currentTarget); button.disabled = true; $('#login-error').textContent = '';
  try { await enter(await api('/login', 'POST', Object.fromEntries(new FormData(e.currentTarget)))); $('#login-form').elements.password.value = ''; }
  catch (error) { $('#login-error').textContent = error.message; } finally { button.disabled = false; }
});
$('#logout').addEventListener('click', async () => { try { await api('/logout', 'POST', {}); signedIn = false; $('#workspace').hidden = true; $('#login-screen').hidden = false; } catch (e) { toast(e.message); } });
$('#refresh').addEventListener('click', () => signedIn ? refresh().then(() => toast('Workspace refreshed.')).catch(e => toast(e.message)) : track($('#tracking-token').value));
$('#new-booking').addEventListener('click', () => { $('#booking-form').reset(); $('.form-error', $('#booking-form')).textContent = ''; updateQuote(); $('#booking-dialog').showModal(); });
$('#add-driver').addEventListener('click', () => { $('#driver-form').reset(); $('.form-error', $('#driver-form')).textContent = ''; $('#driver-dialog').showModal(); });
function updateQuote() {
  const form = $('#booking-form'); const base = { standard: 800, express: 1400, same_day: 2200 }[form.elements.service.value];
  $('#booking-quote').textContent = money(base + Math.max(0, Math.ceil(Number(form.elements.weight.value) - 1)) * 150);
}
$('#booking-form').addEventListener('input', updateQuote);
for (const [formId, endpoint, dialogId, message] of [['booking-form', '/shipments', 'booking-dialog', 'Shipment booked.'], ['driver-form', '/drivers', 'driver-dialog', 'Driver added.']]) {
  $(`#${formId}`).addEventListener('submit', async e => {
    e.preventDefault(); const form = e.currentTarget, button = $('button[type=submit]', form); button.disabled = true;
    try {
      const input = Object.fromEntries(new FormData(form)); if (input.weight) input.weight = Number(input.weight);
      const result = await api(endpoint, 'POST', input); $(`#${dialogId}`).close(); await refresh(); toast(message);
      if (endpoint === '/shipments') await detail(result.id);
    } catch (error) { $('.form-error', form).textContent = error.message; } finally { button.disabled = false; }
  });
}
$('#search').addEventListener('input', filterTable); $('#status-filter').addEventListener('change', filterTable);
$('#view-all').addEventListener('click', () => view('shipments'));
$('#view-unassigned').addEventListener('click', () => { $('#status-filter').value = 'booked'; $('#search').value = ''; filterTable(); view('shipments'); });
$('#tracking-form').addEventListener('submit', e => { e.preventDefault(); track($('#tracking-token').value); });
$('#login-track').addEventListener('click', e => { e.preventDefault(); publicTracking(); });
$('#public-signin').addEventListener('click', () => { $('#workspace').hidden = true; $('#login-screen').hidden = false; history.replaceState(null, '', location.pathname); });
async function init() {
  try { const user = await api('/me'); await enter(user); } catch { /* A signed-out visitor can still track a delivery. */ }
  if (location.hash.startsWith('#track=')) { publicTracking(); $('#tracking-token').value = location.hash.slice(7); await track($('#tracking-token').value); }
  else if (location.hash === '#tracking') publicTracking();
}
init();
