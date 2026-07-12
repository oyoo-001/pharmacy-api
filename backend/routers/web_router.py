"""
Web admin dashboard router — PIN-protected status page at /
"""
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from starlette.responses import RedirectResponse

from backend.config import WEBPAGE_API_PIN, log
from backend.database import get_db
from backend.models import User, Feedback, ServerLog, OnlineSession

router = APIRouter(tags=["Web"])

# ── PIN protection helpers ────────────────────────────────────────────────────

_COOKIE_NAME = "web_session"
_COOKIE_MAX_AGE = 7200
_SERVER_SECRET = "pharmacy-web-dash-secret-2026"


def _sign_pin(pin: str) -> str:
    return hmac.new(_SERVER_SECRET.encode(), pin.encode(), hashlib.sha256).hexdigest()


def _verify_cookie(cookie_val: Optional[str]) -> bool:
    return bool(cookie_val) and cookie_val == _sign_pin(WEBPAGE_API_PIN)


def _set_auth_cookie(response: Response):
    response.set_cookie(
        key=_COOKIE_NAME, value=_sign_pin(WEBPAGE_API_PIN),
        max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
    )


def _clear_auth_cookie(response: Response):
    response.delete_cookie(_COOKIE_NAME, path="/")


async def require_web_access(request: Request) -> None:
    if not _verify_cookie(request.cookies.get(_COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Unauthorized — PIN required")


# ── PIN entry page ────────────────────────────────────────────────────────────

_PIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Access Restricted</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',sans-serif;background:#0f172a;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#1e293b;border-radius:20px;padding:48px 40px;width:380px;box-shadow:0 25px 60px rgba(0,0,0,.5);text-align:center}
.icon{font-size:48px;margin-bottom:12px}
h1{color:#f1f5f9;font-size:22px;font-weight:700;margin-bottom:6px}
p{color:#94a3b8;font-size:14px;margin-bottom:28px}
input[type=password]{width:100%;padding:14px 16px;border:1.5px solid #334155;border-radius:12px;background:#0f172a;color:#f1f5f9;font-size:16px;text-align:center;letter-spacing:8px;outline:none;transition:border-color .2s}
input[type=password]:focus{border-color:#6366f1}
input[type=password]::placeholder{letter-spacing:0;color:#475569}
.error{color:#f87171;font-size:13px;margin-top:12px;display:none}
button{width:100%;margin-top:20px;padding:14px;border:none;border-radius:12px;background:linear-gradient(135deg,#6366f1,#818cf8);color:#fff;font-size:15px;font-weight:700;cursor:pointer;transition:opacity .2s}
button:hover{opacity:.9}
button:disabled{opacity:.5;cursor:not-allowed}
</style>
</head>
<body>
<div class=card>
<div class=icon>🔒</div>
<h1>Access Restricted</h1>
<p>Enter the PIN to view the dashboard</p>
<form id=pinForm>
<input type=password id=pinInput placeholder="* * * *" maxlength=4 inputmode=numeric autocomplete=off>
<div class=error id=errorMsg>Incorrect PIN. Try again.</div>
<button type=submit id=submitBtn>Unlock</button>
</form>
</div>
<script>
const form=document.getElementById('pinForm');
const input=document.getElementById('pinInput');
const error=document.getElementById('errorMsg');
const btn=document.getElementById('submitBtn');
form.addEventListener('submit',async e=>{
e.preventDefault();error.style.display='none';btn.disabled=true;btn.textContent='Checking…';
const r=await fetch('/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:input.value})});
if(r.ok){window.location.href='/'}else{error.style.display='block';btn.disabled=false;btn.textContent='Unlock';input.value='';input.focus()}
});
</script>
</body>
</html>"""

# ── Main dashboard HTML (from deployed version) ───────────────────────────────

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pharmacy API Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f1f5f9; display: flex; min-height: 100vh; color: #1e293b; }

/* ── Sidebar ── */
.sidebar { width: 240px; background: #1e293b; color: #e2e8f0; display: flex; flex-direction: column; flex-shrink: 0; }
.sidebar .brand { padding: 24px 20px 16px; font-size: 16px; font-weight: 700; color: #f8fafc; border-bottom: 1px solid #334155; }
.sidebar .brand span { color: #818cf8; }
.sidebar nav { padding: 12px 0; flex: 1; }
.sidebar nav a { display: flex; align-items: center; gap: 12px; padding: 12px 20px; color: #94a3b8; text-decoration: none; font-size: 14px; font-weight: 500; border-left: 3px solid transparent; transition: all .15s; cursor: pointer; }
.sidebar nav a:hover, .sidebar nav a.active { background: #334155; color: #f1f5f9; border-left-color: #818cf8; }
.sidebar .footer { padding: 16px 20px; font-size: 11px; color: #475569; border-top: 1px solid #334155; }

/* ── Main ── */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.main header { padding: 20px 28px; background: #fff; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center; }
.main header h1 { font-size: 20px; font-weight: 700; }
.main header p { font-size: 13px; color: #64748b; margin-top: 2px; }
.logout-btn { background: transparent; border: 1px solid #ef4444; color: #ef4444; padding: 8px 18px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
.logout-btn:hover { background: #ef4444; color: #fff; }
.content { flex: 1; padding: 24px 28px; overflow-y: auto; }

/* ── Tab pages ── */
.tab-page { display: none; }
.tab-page.active { display: block; }

/* ── Stat cards ── */
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }
.stat-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 20px; box-shadow: 0 4px 6px -4px rgba(0,0,0,0.05); }
.stat-card .label { font-size: 11px; font-weight: 700; letter-spacing: .3px; text-transform: uppercase; color: #64748b; margin-bottom: 6px; }
.stat-card .value { font-size: 28px; font-weight: 800; }
.stat-card .sub { font-size: 12px; color: #94a3b8; margin-top: 4px; }

/* ── Tables ── */
.table-wrap { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; }
.table-wrap table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table-wrap th { background: #f8fafc; color: #64748b; font-weight: 700; font-size: 11px; letter-spacing: .3px; text-transform: uppercase; padding: 10px 14px; text-align: left; border-bottom: 2px solid #e2e8f0; }
.table-wrap td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; color: #334155; }
.table-wrap tr:last-child td { border-bottom: none; }
.table-wrap .empty { padding: 40px; text-align: center; color: #94a3b8; }

/* ── Feedback card ── */
.feedback-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; }
.feedback-card .meta { font-size: 12px; color: #94a3b8; margin-bottom: 4px; }
.feedback-card .name { font-weight: 600; color: #1e293b; }
.feedback-card .msg { font-size: 13px; color: #475569; margin-top: 4px; }

/* ── Logs ── */
.log-toolbar { display: flex; gap: 10px; margin-bottom: 14px; align-items: center; }
.log-toolbar button { padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; border: none; cursor: pointer; }
.btn-primary { background: #6366f1; color: #fff; }
.btn-primary:hover { background: #4f46e5; }
.btn-danger { background: #fee2e2; color: #ef4444; }
.btn-danger:hover { background: #fecaca; }
.log-entry { font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 12px; padding: 6px 10px; border-bottom: 1px solid #f1f5f9; display: flex; gap: 12px; }
.log-entry .time { color: #94a3b8; flex-shrink: 0; }
.log-entry .level { font-weight: 700; flex-shrink: 0; min-width: 50px; }
.log-entry .level.INFO { color: #10b981; }
.log-entry .level.WARN { color: #f59e0b; }
.log-entry .level.ERROR { color: #ef4444; }
.log-entry .msg { color: #334155; }
.log-wrap { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; max-height: 500px; overflow-y: auto; }

/* ── Update tab ── */
.upload-zone { border: 2px dashed #818cf8; border-radius: 14px; padding: 48px 24px; text-align: center; cursor: pointer; background: #f8fafc; transition: background .15s, border-color .15s; }
.upload-zone.drag-over { background: #eef2ff; border-color: #4f46e5; }
.upload-zone .icon { font-size: 40px; margin-bottom: 12px; }
.upload-zone .hint { font-size: 14px; color: #64748b; }
.upload-zone .hint strong { color: #4f46e5; cursor: pointer; }
.upload-zone input[type=file] { display: none; }
.release-notes { width: 100%; margin-top: 16px; padding: 10px 14px; border: 1px solid #e2e8f0; border-radius: 8px; font-size: 13px; font-family: inherit; resize: vertical; min-height: 70px; color: #334155; }
.release-notes:focus { outline: none; border-color: #818cf8; }
.upload-actions { display: flex; gap: 10px; margin-top: 12px; align-items: center; }
.file-chosen { font-size: 13px; color: #475569; flex: 1; }
.progress-bar-wrap { margin-top: 14px; background: #e2e8f0; border-radius: 99px; height: 8px; overflow: hidden; display: none; }
.progress-bar { height: 8px; background: #6366f1; width: 0%; transition: width .3s; border-radius: 99px; }
.current-release { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px; margin-top: 28px; }
.current-release h3 { font-size: 14px; font-weight: 700; margin-bottom: 14px; }
.release-row { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
.release-row:last-child { border-bottom: none; }
.badge-version { background: #eef2ff; color: #4f46e5; font-weight: 700; padding: 3px 10px; border-radius: 99px; font-size: 12px; }
.badge-current { background: #dcfce7; color: #16a34a; font-weight: 700; padding: 3px 10px; border-radius: 99px; font-size: 12px; }
.release-meta { color: #94a3b8; font-size: 12px; margin-left: auto; }
.btn-sm-danger { padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; border: none; cursor: pointer; background: #fee2e2; color: #ef4444; }
.btn-sm-danger:hover { background: #fecaca; }
.toast { position: fixed; bottom: 28px; right: 28px; background: #1e293b; color: #f8fafc; padding: 12px 20px; border-radius: 10px; font-size: 13px; font-weight: 500; opacity: 0; transform: translateY(10px); transition: opacity .25s, transform .25s; pointer-events: none; z-index: 9999; }
.toast.show { opacity: 1; transform: translateY(0); }
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }

/* ── Responsive ── */
@media (max-width: 768px) {
  .sidebar { width: 60px; }
  .sidebar .brand span, .sidebar nav a span, .sidebar .footer { display: none; }
  .sidebar nav a { justify-content: center; padding: 14px; }
}
</style>
</head>
<body>

<div class="sidebar">
  <div class="brand">Pharmacy<span>API</span></div>
  <nav>
    <a class="active" onclick="switchTab('dashboard')">&#x1F4CA; <span>Dashboard</span></a>
    <a onclick="switchTab('feedbacks')">&#x1F4AC; <span>Feedbacks</span></a>
    <a onclick="switchTab('logs')">&#x1F4BB; <span>Logs</span></a>
    <a onclick="switchTab('updates')">&#x1F4E6; <span>Updates</span></a>
  </nav>
  <div class="footer">Kevin Odongo Pharmacy API</div>
</div>

<div class="main">
  <header>
    <div>
      <h1 id="pageTitle">Dashboard</h1>
      <p id="pageSub">Server status &amp; analytics overview</p>
    </div>
    <button class="logout-btn" onclick="fetch('/logout',{method:'POST'}).then(()=>window.location.href='/')">🚪 Logout</button>
  </header>

  <div class="content">

    <!-- ═══ DASHBOARD ═══ -->
    <div id="tab-dashboard" class="tab-page active">
      <div class="stats-grid" id="statsGrid">
        <div class="stat-card"><div class="label">Total Users</div><div class="value" style="color:#6366f1" id="stat-users">-</div><div class="sub">Registered accounts</div></div>
        <div class="stat-card"><div class="label">Online Now</div><div class="value" style="color:#10b981" id="stat-online">-</div><div class="sub">Active IPs (5 min)</div></div>
        <div class="stat-card"><div class="label">Total Feedbacks</div><div class="value" style="color:#f59e0b" id="stat-feedbacks">-</div><div class="sub">Messages received</div></div>
      </div>
      <h3 style="font-size:15px;font-weight:600;margin-bottom:12px;">Online Sessions</h3>
      <div class="table-wrap" id="onlineTableWrap">
        <table><thead><tr><th>IP Address</th><th>User Agent</th><th>Last Ping</th></tr></thead><tbody id="onlineBody"><tr><td class="empty" colspan="3">No online sessions</td></tr></tbody></table>
      </div>
    </div>

    <!-- ═══ FEEDBACKS ═══ -->
    <div id="tab-feedbacks" class="tab-page">
      <div id="feedbackList"></div>
    </div>

    <!-- ═══ LOGS ═══ -->
    <div id="tab-logs" class="tab-page">
      <div class="log-toolbar">
        <button class="btn-primary" onclick="loadLogs()">&#x1F504; Refresh</button>
        <button class="btn-danger" onclick="clearLogs()">&#x1F5D1; Clear All</button>
      </div>
      <div class="log-wrap" id="logList"></div>
    </div>

    <!-- ═══ UPDATES ═══ -->
    <div id="tab-updates" class="tab-page">
      <div class="upload-zone" id="uploadZone">
        <div class="icon">&#x1F4E6;</div>
        <p style="font-size:16px;font-weight:700;color:#1e293b;margin-bottom:6px;">Upload Update Bundle</p>
        <p class="hint">Drag &amp; drop your <strong>.zip</strong> file here, or <strong onclick="document.getElementById('zipFileInput').click()">browse</strong></p>
        <input type="file" id="zipFileInput" accept=".zip">
      </div>
      <textarea class="release-notes" id="releaseNotes" placeholder="Release notes (optional) — what changed in this version?"></textarea>
      <div class="upload-actions">
        <span class="file-chosen" id="fileChosen">No file selected</span>
        <button class="btn-primary" onclick="uploadUpdate()">&#x2B06; Upload to Cloud</button>
      </div>
      <div class="progress-bar-wrap" id="progressWrap">
        <div class="progress-bar" id="progressBar"></div>
      </div>

      <div class="current-release" id="currentRelease">
        <h3>&#x1F4CB; Release History</h3>
        <div id="releaseList"><div style="color:#94a3b8;font-size:13px;">Loading...</div></div>
      </div>
    </div>

  </div>
</div>

<script>
// ── Tab switching ──
function switchTab(name) {
  document.querySelectorAll('.tab-page').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.sidebar nav a').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`.sidebar nav a[onclick*="'${name}'"]`).classList.add('active');
  const titles = {
    dashboard: ['Dashboard', 'Server status & analytics overview'],
    feedbacks: ['Feedbacks', 'User messages & inquiries'],
    logs: ['Server Logs', 'Request and error logs'],
    updates: ['Updates', 'Upload a new .zip release bundle to Backblaze B2 storage'],
  };
  document.getElementById('pageTitle').textContent = titles[name][0];
  document.getElementById('pageSub').textContent = titles[name][1];
  if (name === 'dashboard') loadDashboard();
  if (name === 'feedbacks') loadFeedbacks();
  if (name === 'logs') loadLogs();
  if (name === 'updates') loadUpdates();
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

// ── Toast ──
function showToast(msg, type = '') {
  let t = document.getElementById('globalToast');
  if (!t) { t = document.createElement('div'); t.id = 'globalToast'; t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg;
  t.className = 'toast show ' + type;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.className = 'toast', 3000);
}

// ── Dashboard ──
async function loadDashboard() {
  try {
    const r = await fetch('/api/admin/stats');
    if (!r.ok) { window.location.href = '/'; return; }
    const d = await r.json();
    document.getElementById('stat-users').textContent = d.total_users ?? '-';
    document.getElementById('stat-online').textContent = d.online_ips ?? '-';
    document.getElementById('stat-feedbacks').textContent = d.total_feedbacks ?? '-';
  } catch(e) {}
  try {
    const r = await fetch('/api/admin/online');
    if (!r.ok) return;
    const sessions = await r.json();
    const tbody = document.getElementById('onlineBody');
    if (!sessions || sessions.length === 0) { tbody.innerHTML = '<tr><td class="empty" colspan="3">No online sessions</td></tr>'; return; }
    tbody.innerHTML = sessions.map(s => `<tr><td>${s.ip_address}</td><td>${(s.user_agent||'').substring(0,60)}</td><td>${(s.last_ping||'').substring(0,19).replace('T',' ')}</td></tr>`).join('');
  } catch(e) {}
}

// ── Feedbacks ──
async function loadFeedbacks() {
  try {
    const r = await fetch('/api/admin/feedbacks');
    if (!r.ok) return;
    const items = await r.json();
    const el = document.getElementById('feedbackList');
    if (!items || items.length === 0) { el.innerHTML = '<div class="table-wrap"><table><tr><td class="empty">No feedbacks yet</td></tr></table></div>'; return; }
    el.innerHTML = items.map(f => `<div class="feedback-card"><div class="meta">${(f.created_at||'').substring(0,19).replace('T',' ')}</div><div class="name">${f.name}</div>${f.email ? '<div class="meta">'+f.email+'</div>' : ''}<div class="msg">${f.message}</div></div>`).join('');
  } catch(e) {}
}

// ── Logs ──
async function loadLogs() {
  try {
    const r = await fetch('/api/admin/logs');
    if (!r.ok) return;
    const items = await r.json();
    const el = document.getElementById('logList');
    if (!items || items.length === 0) { el.innerHTML = '<div class="table-wrap"><table><tr><td class="empty">No logs yet</td></tr></table></div>'; return; }
    el.innerHTML = items.map(l => `<div class="log-entry"><span class="time">${(l.created_at||'').substring(11,19)}</span><span class="level ${l.level}">${l.level}</span><span class="msg">${l.message}</span></div>`).join('');
  } catch(e) {}
}

async function clearLogs() {
  if (!confirm('Clear all server logs?')) return;
  try {
    await fetch('/api/admin/logs', { method: 'DELETE' });
    loadLogs();
  } catch(e) { alert('Failed to clear logs'); }
}

// ── Updates ──
let _selectedFile = null;

function loadUpdates() {
  _setupDropZone();
  _fetchReleaseMeta();
}

function _setupDropZone() {
  const zone = document.getElementById('uploadZone');
  if (zone._initDone) return;
  zone._initDone = true;

  const input = document.getElementById('zipFileInput');

  zone.addEventListener('click', (e) => {
    if (e.target.tagName === 'STRONG') return; // handled by inline onclick
    input.click();
  });

  input.addEventListener('change', () => {
    if (input.files[0]) _setFile(input.files[0]);
  });

  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const f = e.dataTransfer.files[0];
    if (f) _setFile(f);
  });
}

function _setFile(f) {
  if (!f.name.toLowerCase().endsWith('.zip')) {
    showToast('Only .zip files are accepted.', 'error');
    return;
  }
  _selectedFile = f;
  document.getElementById('fileChosen').textContent = f.name + ' (' + (f.size / 1024 / 1024).toFixed(2) + ' MB)';
  document.getElementById('uploadZone').style.borderColor = '#10b981';
}

async function uploadUpdate() {
  if (!_selectedFile) { showToast('Please select a .zip file first.', 'error'); return; }

  const notes = document.getElementById('releaseNotes').value.trim();
  const fd = new FormData();
  fd.append('file', _selectedFile);
  fd.append('release_notes', notes);

  const progressWrap = document.getElementById('progressWrap');
  const progressBar  = document.getElementById('progressBar');
  progressWrap.style.display = 'block';
  progressBar.style.width = '10%';

  try {
    // Simulate progress while uploading
    let pct = 10;
    const ticker = setInterval(() => {
      pct = Math.min(pct + 5, 85);
      progressBar.style.width = pct + '%';
    }, 300);

    const resp = await fetch('/api/admin/updates', { method: 'POST', body: fd });
    clearInterval(ticker);
    progressBar.style.width = '100%';

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || 'Upload failed');
    }

    const result = await resp.json();
    showToast('Version ' + result.version + ' uploaded successfully!', 'success');

    // Reset
    _selectedFile = null;
    document.getElementById('fileChosen').textContent = 'No file selected';
    document.getElementById('releaseNotes').value = '';
    document.getElementById('zipFileInput').value = '';
    document.getElementById('uploadZone').style.borderColor = '';

    setTimeout(() => { progressWrap.style.display = 'none'; progressBar.style.width = '0%'; }, 1200);
    _fetchReleaseMeta();

  } catch(e) {
    progressWrap.style.display = 'none';
    progressBar.style.width = '0%';
    showToast(e.message, 'error');
  }
}

async function _fetchReleaseMeta() {
  const container = document.getElementById('releaseList');
  try {
    const r = await fetch('/api/admin/updates');
    if (!r.ok) throw new Error(await r.text());
    const meta = await r.json();

    const rows = [];

    if (meta.version) {
      rows.push(`
        <div class="release-row">
          <span class="badge-version">${escapeHtml(meta.version)}</span>
          <span class="badge-current">current</span>
          <span style="font-size:13px;color:#334155;flex:1;">${escapeHtml(meta.release_notes || '—')}</span>
          <span class="release-meta">${meta.uploaded_at ? new Date(meta.uploaded_at).toLocaleString() : ''}</span>
          <button class="btn-sm-danger" onclick="deleteRelease('${escapeHtml(meta.version)}')">Delete</button>
        </div>`);
    }

    (meta.history || []).forEach(h => {
      rows.push(`
        <div class="release-row">
          <span class="badge-version">${escapeHtml(h.version)}</span>
          <span style="font-size:13px;color:#334155;flex:1;">${escapeHtml(h.release_notes || '—')}</span>
          <span class="release-meta">${h.uploaded_at ? new Date(h.uploaded_at).toLocaleString() : ''}</span>
          <button class="btn-sm-danger" onclick="deleteRelease('${escapeHtml(h.version)}')">Delete</button>
        </div>`);
    });

    container.innerHTML = rows.length
      ? rows.join('')
      : '<div style="color:#94a3b8;font-size:13px;padding:12px 0;">No releases uploaded yet.</div>';

  } catch(e) {
    container.innerHTML = `<div style="color:#ef4444;font-size:13px;">Failed to load releases: ${escapeHtml(e.message)}</div>`;
  }
}

async function deleteRelease(version) {
  if (!confirm('Delete release ' + version + '? This cannot be undone.')) return;
  try {
    const r = await fetch('/api/admin/updates/' + encodeURIComponent(version), { method: 'DELETE' });
    if (!r.ok) throw new Error((await r.json()).detail || 'Delete failed');
    showToast('Release ' + version + ' deleted.', 'success');
    _fetchReleaseMeta();
  } catch(e) {
    showToast(e.message, 'error');
  }
}

// ── Auto-refresh ──
setInterval(() => {
  if (document.getElementById('tab-dashboard').classList.contains('active')) loadDashboard();
  if (document.getElementById('tab-logs').classList.contains('active')) loadLogs();
}, 10000);

loadDashboard();
</script>
</body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def root_page(request: Request):
    if _verify_cookie(request.cookies.get(_COOKIE_NAME)):
        return ADMIN_HTML
    return _PIN_PAGE


@router.post("/unlock")
async def unlock(payload: dict, response: Response):
    pin = payload.get("pin", "")
    if pin == WEBPAGE_API_PIN:
        _set_auth_cookie(response)
        return {"ok": True}
    raise HTTPException(status_code=403, detail="Invalid PIN")


@router.post("/logout")
async def logout(response: Response):
    _clear_auth_cookie(response)
    return {"ok": True}


@router.get("/api/admin/stats")
async def admin_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_web_access(request)
    users_count = await db.execute(select(func.count()).select_from(User))
    feedbacks_count = await db.execute(select(func.count()).select_from(Feedback))

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    online_count = await db.execute(
        select(func.count()).select_from(OnlineSession).where(
            OnlineSession.last_ping >= cutoff
        )
    )

    return {
        "total_users": users_count.scalar() or 0,
        "online_ips": online_count.scalar() or 0,
        "total_feedbacks": feedbacks_count.scalar() or 0,
    }


@router.get("/api/admin/feedbacks")
async def admin_feedbacks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
    require_web_access(request)
    result = await db.execute(
        select(Feedback).order_by(Feedback.created_at.desc()).limit(limit)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "name": f.name,
            "email": f.email or "",
            "message": f.message,
            "created_at": f.created_at.isoformat() if f.created_at else "",
        }
        for f in items
    ]


@router.get("/api/admin/logs")
async def admin_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 200,
):
    require_web_access(request)
    result = await db.execute(
        select(ServerLog).order_by(ServerLog.created_at.desc()).limit(limit)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(l.id),
            "level": l.level,
            "message": l.message,
            "ip_address": l.ip_address or "",
            "path": l.path or "",
            "created_at": l.created_at.isoformat() if l.created_at else "",
        }
        for l in items
    ]


@router.delete("/api/admin/logs")
async def clear_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_web_access(request)
    await db.execute(delete(ServerLog))
    await db.commit()
    return {"ok": True}


@router.get("/api/admin/online")
async def online_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_web_access(request)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = await db.execute(
        select(OnlineSession).where(OnlineSession.last_ping >= cutoff)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "ip_address": s.ip_address,
            "user_agent": s.user_agent or "",
            "last_ping": s.last_ping.isoformat() if s.last_ping else "",
            "first_seen": s.first_seen.isoformat() if s.first_seen else "",
        }
        for s in items
    ]

# ── /addmedicine — mobile scan page ──────────────────────────────────────────

_ADD_MEDICINE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Add Medicine — Kevin Odongo Pharmacy</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#f1f5f9;min-height:100vh;
     display:flex;align-items:center;justify-content:center;padding:16px;}
.card{background:#fff;border-radius:20px;padding:32px 28px;width:100%;max-width:440px;
      box-shadow:0 8px 32px rgba(0,0,0,.1);}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:24px;}
.brand .dot{width:36px;height:36px;background:linear-gradient(135deg,#6366f1,#818cf8);
            border-radius:10px;display:flex;align-items:center;justify-content:center;
            font-size:18px;}
.brand h1{font-size:18px;font-weight:800;color:#0f172a;}
.brand p{font-size:12px;color:#64748b;}
label{display:block;font-size:12px;font-weight:700;color:#475569;margin-bottom:6px;
      text-transform:uppercase;letter-spacing:.4px;}
input,textarea,select{width:100%;padding:12px 14px;border:1.5px solid #e2e8f0;
      border-radius:10px;font-size:14px;color:#1e293b;background:#f8fafc;outline:none;
      transition:border-color .2s;}
input:focus,textarea:focus,select:focus{border-color:#6366f1;background:#fff;}
.field{margin-bottom:18px;}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.submit-btn{width:100%;padding:14px;border:none;border-radius:12px;
            background:linear-gradient(135deg,#6366f1,#818cf8);
            color:#fff;font-size:15px;font-weight:700;cursor:pointer;
            transition:opacity .2s;margin-top:8px;}
.submit-btn:hover{opacity:.9}
.submit-btn:disabled{opacity:.5;cursor:not-allowed}
.status{margin-top:16px;padding:14px;border-radius:10px;font-size:14px;
        font-weight:600;text-align:center;display:none;}
.status.success{background:#dcfce7;color:#166534;}
.status.error{background:#fee2e2;color:#991b1b;}
.status.info{background:#eef2ff;color:#3730a3;}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #fff;
         border-top-color:transparent;border-radius:50%;
         animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px;}
@keyframes spin{to{transform:rotate(360deg)}}
.hint{font-size:11px;color:#94a3b8;margin-top:4px;}

/* ── AI scan area ─────────────────────────────────────────────── */
.ai-section{margin-bottom:20px;padding:18px;border:2px dashed #c7d2fe;
            border-radius:14px;background:#f5f3ff;text-align:center;}
.ai-section p{font-size:13px;color:#6366f1;font-weight:600;margin-bottom:12px;}
.ai-btns{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;}
.ai-btn{display:inline-flex;align-items:center;gap:6px;padding:10px 18px;
        border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;
        border:2px solid transparent;transition:all .2s;}
.ai-btn.upload{background:#6366f1;color:#fff;}
.ai-btn.upload:hover{background:#4f46e5;}
.ai-btn.camera{background:#fff;color:#6366f1;border-color:#c7d2fe;}
.ai-btn.camera:hover{background:#eef2ff;border-color:#6366f1;}
/* Hide camera button on desktop — only show on phones */
@media(min-width:640px){.ai-btn.camera{display:none !important;}}
.ai-btn:disabled{opacity:.4;cursor:not-allowed;}
.ai-btn .loader{display:none;width:14px;height:14px;border:2px solid #fff;
                border-top-color:transparent;border-radius:50%;
                animation:spin .6s linear infinite;}
.ai-btn.loading .loader{display:inline-block;}
.ai-btn.loading .btn-icon{display:none;}
#previewImg{max-width:100%;max-height:120px;border-radius:8px;margin-top:10px;
            display:none;box-shadow:0 2px 8px rgba(0,0,0,.1);}
.ai-status{margin-top:10px;font-size:12px;color:#6366f1;font-weight:600;display:none;}

/* ── Camera modal ─────────────────────────────────────────── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;
               align-items:center;justify-content:center;z-index:999;}
.modal-overlay.show{display:flex;}
.modal-box{background:#fff;border-radius:16px;padding:0;width:92%;max-width:400px;
           text-align:center;box-shadow:0 12px 40px rgba(0,0,0,.3);
           max-height:75vh;display:flex;flex-direction:column;overflow:hidden;}
.modal-box h3{font-size:16px;color:#0f172a;margin:0;padding:16px 20px 12px;flex-shrink:0;
              border-bottom:1px solid #f1f5f9;}
.modal-box video{width:100%;background:#000;flex:1;min-height:0;object-fit:cover;}
.modal-btns{display:flex;gap:0;flex-shrink:0;}
.modal-btns button{flex:1;padding:14px 0;border:none;font-size:14px;font-weight:700;cursor:pointer;}
.modal-btns .capture{background:#6366f1;color:#fff;}
.modal-btns .capture:hover{background:#4f46e5;}
.modal-btns .cancel{background:#f1f5f9;color:#475569;}
.modal-btns .cancel:hover{background:#e2e8f0;}
</style>
</head>
<body>
<div class="card">
  <div class="brand">
    <div class="dot">💊</div>
    <div>
      <h1>Add Medicine</h1>
      <p>Upload a photo or type the drug name to auto-fill</p>
    </div>
  </div>

  <!-- ── AI image scan section ─────────────────────────────────── -->
  <div class="ai-section" id="aiSection">
    <p>📸 Scan medicine package with AI</p>
    <div class="ai-btns">
      <button type="button" class="ai-btn upload" id="uploadBtn" onclick="document.getElementById('fileInput').click()">
        <span class="btn-icon">📤</span><span class="loader"></span> Upload Photo
      </button>
      <button type="button" class="ai-btn camera" id="cameraBtn" onclick="openCamera()">
        <span class="btn-icon">📷</span><span class="loader"></span> Take Photo
      </button>
    </div>
    <input type="file" id="fileInput" accept="image/*" style="display:none">
    <img id="previewImg" alt="Preview">
    <div class="ai-status" id="aiStatus">🤖 Analyzing with AI...</div>
  </div>

  <form id="addForm">
    <div class="field">
      <label>Drug Name *</label>
      <input type="text" id="drugName" name="name" required
             placeholder="e.g. Amoxicillin 500mg" autocomplete="off">
      <div class="hint">Type the brand or generic name — we'll look it up on openFDA.</div>
    </div>

    <div class="field">
      <label>Category</label>
      <input type="text" id="category" name="category"
             placeholder="e.g. Antibiotics, Analgesic">
    </div>

    <div class="row">
      <div class="field">
        <label>Batch Number</label>
        <input type="text" id="batchNumber" name="batch_number"
               placeholder="BT-20250101">
      </div>
      <div class="field">
        <label>Expiry Date</label>
        <input type="date" id="expiryDate" name="expiry_date">
      </div>
    </div>

    <div class="row">
      <div class="field">
        <label>Buying Price (KES)</label>
        <input type="number" id="buyingPrice" name="buying_price"
               min="0" step="0.01" placeholder="0.00" value="0">
      </div>
      <div class="field">
        <label>Selling Price (KES) *</label>
        <input type="number" id="sellingPrice" name="selling_price"
               min="0" step="0.01" placeholder="0.00" value="0" required>
      </div>
    </div>

    <div class="row">
      <div class="field">
        <label>Quantity</label>
        <input type="number" id="quantity" name="quantity"
               min="0" placeholder="0" value="0">
      </div>
      <div class="field">
        <label>Reorder Level</label>
        <input type="number" id="reorderLevel" name="reorder_level"
               min="0" placeholder="10" value="10">
      </div>
    </div>

    <div class="field">
      <label>Description / Notes</label>
      <textarea id="description" name="description" rows="2"
                placeholder="Optional notes…"></textarea>
    </div>

    <button type="submit" class="submit-btn" id="submitBtn">
      💊 Add Medicine
    </button>
  </form>

  <div class="status" id="statusBox"></div>
</div>

<!-- ── Camera modal ──────────────────────────────────────────────── -->
<div class="modal-overlay" id="cameraModal">
  <div class="modal-box">
    <h3>📷 Take a Photo</h3>
    <video id="cameraVideo" autoplay playsinline></video>
    <canvas id="cameraCanvas" style="display:none"></canvas>
    <div class="modal-btns">
      <button type="button" class="cancel" onclick="closeCamera()">Cancel</button>
      <button type="button" class="capture" onclick="capturePhoto()">📸 Capture</button>
    </div>
  </div>
</div>

<script>
// ── Read token from URL: /addmedicine?token=<JWT> ─────────────────────────
const params   = new URLSearchParams(location.search);
const jwtToken = params.get('token') || '';
let sessionToken = '';
let pharmacyId   = '';
let cameraStream = null;

async function initSession() {
  if (!jwtToken) {
    showStatus('error', 'No authentication token. Open this page from the desktop app.');
    return;
  }
  try {
    const r = await fetch('/api/sync/session', {
      headers: { 'Authorization': 'Bearer ' + jwtToken }
    });
    if (!r.ok) throw new Error('Session init failed: ' + r.status);
    const d = await r.json();
    sessionToken = d.token;
    pharmacyId   = d.admin_id;
  } catch(e) {
    showStatus('error', 'Could not start session: ' + e.message);
  }
}

// ── AI image analysis ────────────────────────────────────────────────────
const fileInput  = document.getElementById('fileInput');
const previewImg = document.getElementById('previewImg');
const aiStatus   = document.getElementById('aiStatus');
const uploadBtn  = document.getElementById('uploadBtn');
const cameraBtn  = document.getElementById('cameraBtn');

fileInput.addEventListener('change', function(e) {
  const file = e.target.files[0];
  if (!file) return;
  showPreview(file);
  analyzeImage(file);
});

function showPreview(file) {
  const reader = new FileReader();
  reader.onload = function(ev) {
    previewImg.src = ev.target.result;
    previewImg.style.display = 'block';
  };
  reader.readAsDataURL(file);
}

async function analyzeImage(file) {
  const btn = uploadBtn;
  btn.classList.add('loading');
  btn.disabled = true;
  cameraBtn.disabled = true;
  aiStatus.style.display = 'block';
  aiStatus.textContent = '🤖 Analyzing with AI...';
  aiStatus.style.color = '#6366f1';

  try {
    const base64 = await fileToBase64(file);
    const mime   = file.type || 'image/jpeg';

    const r = await fetch('/api/medicine/analyze-image', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + jwtToken,
      },
      body: JSON.stringify({ image_base64: base64, mime_type: mime }),
    });

    const data = await r.json();

    if (!r.ok || data.error) {
      throw new Error(data.error || 'Analysis failed');
    }

    // Auto-fill form fields from Gemini response
    if (data.name)        document.getElementById('drugName').value       = data.name;
    if (data.category)    document.getElementById('category').value       = data.category;
    if (data.description) document.getElementById('description').value    = data.description;
    if (data.batch_number) document.getElementById('batchNumber').value  = data.batch_number;
    if (data.expiry_date)  document.getElementById('expiryDate').value   = data.expiry_date;
    if (data.buying_price)  document.getElementById('buyingPrice').value  = data.buying_price;
    if (data.selling_price) document.getElementById('sellingPrice').value = data.selling_price;
    if (data.quantity)      document.getElementById('quantity').value     = data.quantity;
    if (data.reorder_level) document.getElementById('reorderLevel').value = data.reorder_level;

    aiStatus.textContent = '✅ AI analysis complete — fields auto-filled!';
    aiStatus.style.color = '#16a34a';
  } catch(err) {
    aiStatus.textContent = '❌ ' + err.message;
    aiStatus.style.color = '#dc2626';
  } finally {
    btn.classList.remove('loading');
    btn.disabled = false;
    cameraBtn.disabled = false;
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      // Strip "data:image/...;base64," prefix
      const base64 = result.split(',')[1] || result;
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ── Camera ────────────────────────────────────────────────────────────────
async function openCamera() {
  const modal = document.getElementById('cameraModal');
  const video = document.getElementById('cameraVideo');
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } }
    });
    video.srcObject = cameraStream;
    modal.classList.add('show');
  } catch(err) {
    showStatus('error', 'Camera access denied or not available: ' + err.message);
  }
}

function closeCamera() {
  const modal = document.getElementById('cameraModal');
  const video = document.getElementById('cameraVideo');
  if (cameraStream) {
    cameraStream.getTracks().forEach(t => t.stop());
    cameraStream = null;
  }
  video.srcObject = null;
  modal.classList.remove('show');
}

async function capturePhoto() {
  const video  = document.getElementById('cameraVideo');
  const canvas = document.getElementById('cameraCanvas');
  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);

  canvas.toBlob(async function(blob) {
    closeCamera();
    if (!blob) return;
    showPreview(blob);
    // Rename the blob so it has a mime type
    const file = new File([blob], 'camera_capture.jpg', { type: 'image/jpeg' });
    await analyzeImage(file);
  }, 'image/jpeg', 0.92);
}

// ── Form submission (unchanged) ──────────────────────────────────────────
document.getElementById('addForm').addEventListener('submit', async function(e) {
  e.preventDefault();

  if (!sessionToken) {
    showStatus('error', 'Session not ready. Refresh and try again.');
    return;
  }

  const btn      = document.getElementById('submitBtn');
  const drugName = document.getElementById('drugName').value.trim();

  if (!drugName) {
    showStatus('error', 'Please enter the drug name.');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Looking up drug info…';
  showStatus('info', '🔍 Searching openFDA database…');

  const payload = {
    session_token:  sessionToken,
    pharmacy_id:    pharmacyId,
    extracted_text: drugName,
    category:       document.getElementById('category')?.value?.trim() || null,
    description:    document.getElementById('description')?.value?.trim() || null,
    batch_number:   document.getElementById('batchNumber')?.value?.trim() || null,
    expiry_date:    document.getElementById('expiryDate')?.value || null,
    buying_price:   parseFloat(document.getElementById('buyingPrice')?.value) || null,
    selling_price:  parseFloat(document.getElementById('sellingPrice')?.value) || null,
    quantity:       parseInt(document.getElementById('quantity')?.value) || null,
    reorder_level:  parseInt(document.getElementById('reorderLevel')?.value) || null,
  };

  try {
    const r = await fetch('/api/medicine/submit-scan', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + jwtToken },
      body: JSON.stringify(payload),
    });

    const data = await r.json();

    if (!r.ok) {
      throw new Error(data.detail || 'Submission failed');
    }

    showStatus('success',
      '✅ ' + data.message + '\n\nPlease update the pricing and stock details in the desktop app.');

    btn.innerHTML = '✅ Medicine Added!';
    btn.disabled  = false;

    setTimeout(() => {
      document.getElementById('addForm').reset();
      btn.innerHTML = '💊 Add Medicine';
      document.getElementById('statusBox').style.display = 'none';
      previewImg.style.display = 'none';
      aiStatus.style.display = 'none';
    }, 6000);

  } catch(err) {
    showStatus('error', '❌ ' + err.message);
    btn.innerHTML = '💊 Add Medicine';
    btn.disabled  = false;
  }
});

function showStatus(type, msg) {
  const box = document.getElementById('statusBox');
  box.className = 'status ' + type;
  box.textContent = msg;
  box.style.display = 'block';
}

// Initialise session on page load
initSession();
</script>
</body>
</html>"""


@router.get("/addmedicine", response_class=HTMLResponse)
async def add_medicine_page():
    """
    Mobile-friendly scan-to-add page.
    Opens with ?token=<JWT> appended by the desktop app.
    """
    return _ADD_MEDICINE_HTML
