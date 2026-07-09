"""Web admin dashboard router — serves the status page at /"""
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from backend.database import get_db
from backend.models import User, Feedback, ServerLog, OnlineSession

router = APIRouter(tags=["Web"])


# ── API endpoints ───────────────────────────────────────────────────────

@router.get("/api/admin/stats")
async def admin_stats(db: AsyncSession = Depends(get_db)):
    """Dashboard stat cards data."""
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
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
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
    db: AsyncSession = Depends(get_db),
    limit: int = 200,
):
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
async def clear_logs(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ServerLog))
    await db.commit()
    return {"ok": True}


@router.get("/api/admin/online")
async def online_sessions(db: AsyncSession = Depends(get_db)):
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


# ── Main HTML page ──────────────────────────────────────────────────────

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
.main header { padding: 20px 28px; background: #fff; border-bottom: 1px solid #e2e8f0; }
.main header h1 { font-size: 20px; font-weight: 700; }
.main header p { font-size: 13px; color: #64748b; margin-top: 2px; }
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
  </nav>
  <div class="footer">Kevin Odongo Pharmacy API</div>
</div>

<div class="main">
  <header>
    <h1 id="pageTitle">Dashboard</h1>
    <p id="pageSub">Server status &amp; analytics overview</p>
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
        <button class="btn-primary" onclick="copyLogs()">Copy All</button>
        <button class="btn-danger" onclick="clearLogs()">Clear</button>
        <span style="font-size:12px;color:#94a3b8;margin-left:auto;" id="logCount"></span>
      </div>
      <div class="log-wrap" id="logWrap"><div class="empty" style="padding:40px;text-align:center;color:#94a3b8;">Loading logs...</div></div>
    </div>

  </div>
</div>

<script>
function switchTab(name) {
  document.querySelectorAll('.tab-page').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`.sidebar nav a[onclick*="'${name}'"]`).classList.add('active');
  const titles = { dashboard: ['Dashboard', 'Server status & analytics overview'], feedbacks: ['Feedbacks', 'User feedback & messages'], logs: ['Server Logs', 'Request logs with copy & clear'] };
  document.getElementById('pageTitle').textContent = titles[name][0];
  document.getElementById('pageSub').textContent = titles[name][1];
  if (name === 'dashboard') loadDashboard();
  if (name === 'feedbacks') loadFeedbacks();
  if (name === 'logs') loadLogs();
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

// ── Dashboard ──
async function loadDashboard() {
  try {
    const r = await fetch('/api/admin/stats');
    const d = await r.json();
    document.getElementById('stat-users').textContent = d.total_users;
    document.getElementById('stat-online').textContent = d.online_ips;
    document.getElementById('stat-feedbacks').textContent = d.total_feedbacks;
  } catch(e) { console.error(e); }

  try {
    const r = await fetch('/api/admin/online');
    const list = await r.json();
    const tbody = document.getElementById('onlineBody');
    if (!list.length) { tbody.innerHTML = '<tr><td class="empty" colspan="3">No online sessions</td></tr>'; return; }
    tbody.innerHTML = list.map(s => `<tr><td>${escapeHtml(s.ip_address)}</td><td style="font-size:12px;color:#64748b;">${escapeHtml(s.user_agent).slice(0,60)}</td><td style="font-size:12px;color:#94a3b8;">${new Date(s.last_ping).toLocaleString()}</td></tr>`).join('');
  } catch(e) { console.error(e); }
}

// ── Feedbacks ──
async function loadFeedbacks() {
  try {
    const r = await fetch('/api/admin/feedbacks');
    const list = await r.json();
    const container = document.getElementById('feedbackList');
    if (!list.length) { container.innerHTML = '<div class="table-wrap"><div class="empty">No feedbacks yet</div></div>'; return; }
    container.innerHTML = list.map(f => `<div class="feedback-card"><div class="meta"><span class="name">${escapeHtml(f.name)}</span>${f.email ? ' &lt;'+escapeHtml(f.email)+'&gt;' : ''} &middot; ${new Date(f.created_at).toLocaleString()}</div><div class="msg">${escapeHtml(f.message)}</div></div>`).join('');
  } catch(e) { console.error(e); }
}

// ── Logs ──
async function loadLogs() {
  try {
    const r = await fetch('/api/admin/logs?limit=200');
    const list = await r.json();
    const wrap = document.getElementById('logWrap');
    document.getElementById('logCount').textContent = list.length + ' entries';
    if (!list.length) { wrap.innerHTML = '<div class="empty" style="padding:40px;text-align:center;color:#94a3b8;">No server logs</div>'; return; }
    wrap.innerHTML = list.map(l => `<div class="log-entry"><span class="time">${new Date(l.created_at).toLocaleString()}</span><span class="level ${l.level}">${l.level}</span><span class="msg">${escapeHtml(l.message)}</span></div>`).join('');
  } catch(e) { console.error(e); }
}

function copyLogs() {
  const entries = document.querySelectorAll('.log-entry .msg');
  const texts = Array.from(entries).map(e => e.textContent).join('\n');
  navigator.clipboard.writeText(texts).then(() => alert('Logs copied to clipboard!')).catch(() => alert('Failed to copy logs'));
}

async function clearLogs() {
  if (!confirm('Clear all server logs?')) return;
  try {
    await fetch('/api/admin/logs', { method: 'DELETE' });
    loadLogs();
  } catch(e) { alert('Failed to clear logs'); }
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


@router.get("/", response_class=HTMLResponse)
async def root_page():
    return ADMIN_HTML



