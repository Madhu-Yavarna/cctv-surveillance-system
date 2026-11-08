// Police Response Dashboard Logic - Context-Aware Video Surveillance

let currentPolicePage = 'dashboard';
let polDashMapInstance = null;
let polFullMapInstance = null;
let latestIncidentObj = null;
let policeSocket = null;
let modalIncidentCache = null;

function getAuthMediaUrl(url) {
  if (!url) return '';
  const token = getAuthToken();
  if (!token) return url;
  return url.includes('?') ? `${url}&token=${encodeURIComponent(token)}` : `${url}?token=${encodeURIComponent(token)}`;
}

// Audio Chime Generator using Web Audio API (Zero external file dependencies)
function playAlertChime() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    const ctx = new AudioContext();

    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gainNode = ctx.createGain();

    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(880, ctx.currentTime); // A5
    osc1.frequency.setValueAtTime(660, ctx.currentTime + 0.15); // E5

    osc2.type = 'triangle';
    osc2.frequency.setValueAtTime(440, ctx.currentTime);
    osc2.frequency.setValueAtTime(330, ctx.currentTime + 0.15);

    gainNode.gain.setValueAtTime(0.3, ctx.currentTime);
    gainNode.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.4);

    osc1.connect(gainNode);
    osc2.connect(gainNode);
    gainNode.connect(ctx.destination);

    osc1.start();
    osc2.start();
    osc1.stop(ctx.currentTime + 0.45);
    osc2.stop(ctx.currentTime + 0.45);
  } catch (e) {
    console.log('Audio chime unavailable', e);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  if (!checkAuth('police')) return;

  const user = getUserProfile();
  const nameLabel = document.getElementById('policeOfficerName');
  const setPolName = document.getElementById('setPolName');
  if (nameLabel) nameLabel.innerText = user.full_name || 'Inspector R. Sharma';
  if (setPolName) setPolName.innerText = user.full_name || 'Inspector R. Sharma';

  startClock('policeDate', 'policeTime');
  initPoliceWebSocket();
  loadPoliceStats();
  loadLatestAlert();
  loadPoliceAlertNotifications();
  initPoliceDashboardMap();

  setInterval(() => {
    loadPoliceStats();
    loadPoliceAlertNotifications();
  }, 10000);
});

// WebSocket Connection
function initPoliceWebSocket() {
  const token = getAuthToken();
  if (!token) return;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`;

  try {
    policeSocket = new WebSocket(wsUrl);
    policeSocket.onopen = () => console.log('Police WebSocket connected to live dispatch hub');
    policeSocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handlePoliceWebSocketMessage(msg);
      } catch (e) {
        console.error('Failed to parse police WS message', e);
      }
    };
    policeSocket.onclose = () => {
      setTimeout(initPoliceWebSocket, 4000);
    };
  } catch (e) {
    console.error('WebSocket error', e);
  }
}

function handlePoliceWebSocketMessage(msg) {
  if (msg.type === 'incident' || msg.type === 'NEW_INCIDENT_ALERT' || msg.type === 'incident_update') {
    if (localStorage.getItem('police_pref_sound') !== 'false') {
      playAlertChime();
    }
    const inc = msg.incident || {};
    const incId = inc.id || inc.incident_id;
    const code = inc.incident_id || inc.code || 'Emergency Alert';
    const loc = inc.location || inc.location_text || 'Monitored Sector';
    showToast(`🚨 NEW EMERGENCY ALERT ASSIGNED: ${code} at ${loc}!`, 'danger');
    
    // Send ack if notification_id present
    if (msg.notification_id && policeSocket && policeSocket.readyState === WebSocket.OPEN) {
      try {
        policeSocket.send(JSON.stringify({ type: 'ack', notification_id: msg.notification_id }));
      } catch(e) {}
    }

    // Auto-open immediate risk alert popup with complete details!
    if (localStorage.getItem('police_pref_auto_modal') !== 'false' && incId) {
      openPoliceDetailsModal(incId);
    }

    triggerDesktopNotification(`🚨 Emergency Alert: ${code}`, {
      body: `Risk: ${inc.risk_level || 'HIGH RISK'} at ${loc}`
    });

    loadPoliceStats();
    loadLatestAlert();
    loadPoliceAlertNotifications();
    if (currentPolicePage === 'response-center') loadPoliceResponseCenter();
    if (polDashMapInstance) fetchPoliceMapMarkers(polDashMapInstance);
  } else if (msg.type === 'STATUS_UPDATED' || msg.type === 'RESPONSE_STATUS_UPDATED' || msg.type === 'INCIDENT_ACKNOWLEDGED' || msg.type === 'INCIDENT_DELETED') {
    loadPoliceStats();
    loadLatestAlert();
    loadPoliceAlertNotifications();
    if (currentPolicePage === 'response-center') loadPoliceResponseCenter();
    if (polDashMapInstance) fetchPoliceMapMarkers(polDashMapInstance);
  }
}

// Unified Police Navigation Controller
function switchPolicePage(page) {
  currentPolicePage = page;
  document.querySelectorAll('.subpage').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));

  const targetSection = document.getElementById(`police-page-${page}`);
  if (targetSection) targetSection.classList.add('active');

  const links = document.querySelectorAll('.nav-link');
  links.forEach(l => {
    const text = l.innerText.toLowerCase();
    if (
      (page === 'dashboard' && text.includes('dashboard')) ||
      (page === 'response-center' && (text.includes('response') || text.includes('center'))) ||
      (page === 'settings' && text.includes('setting'))
    ) {
      l.classList.add('active');
    }
  });

  const subtitle = document.getElementById('policeHeaderSubtitle');
  if (subtitle) {
    const titles = {
      dashboard: 'Dashboard Overview',
      'response-center': 'Police Response Center',
      settings: 'Officer Profile & Station Details'
    };
    subtitle.innerText = titles[page] || 'Police Response';
  }

  // Load relevant module data
  if (page === 'dashboard') {
    loadPoliceStats();
    loadLatestAlert();
    setTimeout(() => { if (polDashMapInstance) polDashMapInstance.invalidateSize(); }, 200);
  } else if (page === 'response-center') {
    loadPoliceResponseCenter();
  } else if (page === 'settings') {
    loadPoliceSettings();
  }
}

// 1. Stats & Latest Alert Card
async function loadPoliceStats() {
  try {
    const res = await fetch('/api/police/stats', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const stats = await res.json();

    document.getElementById('polStatTotal').innerText = stats.total_alerts || 0;
    document.getElementById('polStatNew').innerText = stats.new_alerts || 0;
    document.getElementById('polStatPending').innerText = stats.pending || 0;
    document.getElementById('polStatReview').innerText = stats.under_review || 0;
    document.getElementById('polStatResolved').innerText = stats.resolved || 0;
    document.getElementById('polStatStation').innerText = stats.assigned_station_code || 'CPS-01';
  } catch (e) {
    console.error('Failed to load police stats', e);
  }
}

async function loadLatestAlert() {
  try {
    const res = await fetch('/api/police/latest-alert', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const alertData = await res.json();

    const snapImg = document.getElementById('polLatestSnapshot');
    const snapPlaceholder = document.getElementById('polSnapshotPlaceholder');
    const videoEl = document.getElementById('polEvidenceVideo');
    const videoPlaceholder = document.getElementById('polVideoPlaceholder');
    const badgeEl = document.getElementById('polLatestBadge');

    if (!alertData) {
      latestIncidentObj = null;
      document.getElementById('polLatestId').innerText = 'None';
      document.getElementById('polLatestTime').innerText = 'No active alerts in queue';
      document.getElementById('polLatestCamera').innerText = 'None';
      document.getElementById('polLatestLocation').innerText = 'None';
      document.getElementById('polLatestRisk').innerText = 'Safe';
      document.getElementById('polLatestRisk').className = 'risk-level-badge low';
      if (badgeEl) {
        badgeEl.innerText = '● Standby';
        badgeEl.style.color = 'var(--text-muted)';
      }
      if (snapImg) snapImg.style.display = 'none';
      if (snapPlaceholder) snapPlaceholder.style.display = 'block';
      if (videoEl) videoEl.style.display = 'none';
      if (videoPlaceholder) videoPlaceholder.style.display = 'block';
      document.getElementById('polHashVal').innerText = '-';
      document.getElementById('polHashBadge').style.display = 'none';
      return;
    }

    latestIncidentObj = alertData;

    document.getElementById('polLatestId').innerText = alertData.incident_id;
    document.getElementById('polLatestTime').innerText = `${alertData.time_str}, ${alertData.date_str}`;
    document.getElementById('polLatestCamera').innerText = alertData.camera_name;
    document.getElementById('polLatestLocation').innerText = alertData.location;

    const riskBadge = document.getElementById('polLatestRisk');
    riskBadge.innerText = alertData.risk_level;
    riskBadge.className = `risk-level-badge ${alertData.risk_level === 'High Risk' ? 'high' : (alertData.risk_level === 'Medium Risk' ? 'medium' : 'low')}`;

    if (badgeEl) {
      badgeEl.innerText = alertData.status === 'Pending' ? '● New' : `● ${alertData.status}`;
      badgeEl.style.color = alertData.status === 'Pending' ? 'var(--accent-red)' : 'var(--accent-cyan)';
    }

    const evList = Array.isArray(alertData.evidence) ? alertData.evidence : [];
    const snapEv = evList.find(e => e.kind === 'snapshot');
    const clipEv = evList.find(e => e.kind === 'clip');
    const snapUrl = getAuthMediaUrl((alertData.evidence && alertData.evidence.snapshot_url) || alertData.snapshot_url || (snapEv ? snapEv.url : ''));
    const clipUrl = getAuthMediaUrl((alertData.evidence && alertData.evidence.video_clip_url) || alertData.video_clip_url || (clipEv ? clipEv.url : ''));
    const snapHash = (alertData.evidence && alertData.evidence.snapshot_hash) || (alertData.evidence_details && alertData.evidence_details.snapshot_hash) || (snapEv ? snapEv.sha256 : 'N/A');
    const isVer = (alertData.evidence && alertData.evidence.is_verified) || (alertData.evidence_details && alertData.evidence_details.is_verified) || (snapEv && snapEv.verify_result === 'match');

    if (snapUrl) {
      if (snapImg) {
        snapImg.src = snapUrl;
        snapImg.style.display = 'block';
      }
      if (snapPlaceholder) snapPlaceholder.style.display = 'none';
    } else {
      if (snapImg) snapImg.style.display = 'none';
      if (snapPlaceholder) snapPlaceholder.style.display = 'block';
    }

    if (clipUrl) {
      if (videoEl) {
        videoEl.src = clipUrl;
        videoEl.style.display = 'block';
      }
      if (videoPlaceholder) videoPlaceholder.style.display = 'none';
    } else {
      if (videoEl) videoEl.style.display = 'none';
      if (videoPlaceholder) videoPlaceholder.style.display = 'block';
    }

    const hashValEl = document.getElementById('polHashVal');
    if (hashValEl) hashValEl.innerText = formatHash(snapHash);

    const hashBadge = document.getElementById('polHashBadge');
    if (hashBadge) {
      hashBadge.style.display = 'inline-block';
      if (isVer) {
        hashBadge.innerText = '✓ Hash Verified';
        hashBadge.className = 'badge badge-resolved';
      } else {
        hashBadge.innerText = '● Unverified';
        hashBadge.className = 'badge badge-pending';
      }
    }

    const statSelect = document.getElementById('polResponseStatusSelect');
    if (statSelect && alertData.status) {
      statSelect.value = alertData.status === 'Pending' ? 'Acknowledged' : alertData.status;
    }

    // Update map view to incident
    if (polDashMapInstance && alertData.latitude && alertData.longitude) {
      polDashMapInstance.setView([alertData.latitude, alertData.longitude], 14);
    }

  } catch (e) {
    console.error('Failed to load latest alert', e);
  }
}

// 2. Incident Alerts Table (Module 2)
async function loadPoliceAlerts() {
  try {
    const res = await fetch('/api/incidents', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const incidents = await res.json();
    const tbody = document.getElementById('polAlertsTableBody');
    if (!tbody) return;

    if (incidents.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 30px;">Zero Active Emergency Alerts Assigned</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    incidents.forEach(inc => {
      let riskBadge = 'risk-level-badge low';
      if (inc.risk_level === 'High Risk') riskBadge = 'risk-level-badge high';
      else if (inc.risk_level === 'Medium Risk') riskBadge = 'risk-level-badge medium';

      let statusBadge = 'badge-pending';
      if (inc.status === 'Resolved') statusBadge = 'badge-resolved';
      else if (inc.status === 'Responding') statusBadge = 'badge-online';

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-weight: 700; color: #fff;">${inc.incident_id}</td>
        <td>${inc.time_str}, ${inc.date_str}</td>
        <td>${inc.location}</td>
        <td><span class="${riskBadge}">${inc.risk_level}</span></td>
        <td><strong style="color: var(--accent-cyan);">${inc.risk_score}%</strong></td>
        <td><span class="badge ${statusBadge}">${inc.status}</span></td>
        <td style="display: flex; gap: 6px;">
          <button class="btn btn-primary btn-sm" onclick="openPoliceDetailsModal(${inc.id})">👁 View Details</button>
          <button class="btn btn-success btn-sm" onclick="quickAcknowledge(${inc.id})">✓ Ack</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load alerts', e);
  }
}

// Police Alert Notifications Stream (Replaces Evidence Viewer)
async function loadPoliceAlertNotifications() {
  const container = document.getElementById('polAlertsListContainer');
  const countBadge = document.getElementById('polAlertsCountBadge');
  if (!container) return;

  try {
    const res = await fetch('/api/incidents', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const incidents = await res.json();

    const activeList = incidents.filter(i => {
      const s = (i.response_status || i.status || '').toUpperCase();
      return s !== 'SOLVED' && s !== 'RESOLVED' && s !== 'REJECTED';
    });

    if (countBadge) {
      countBadge.innerText = `${activeList.length} Active`;
      countBadge.className = activeList.length > 0 ? 'badge badge-high' : 'badge badge-resolved';
    }

    if (incidents.length === 0) {
      container.innerHTML = `
        <div style="color: var(--text-muted); font-size: 13.5px; text-align: center; padding: 40px 16px;">
          <div style="font-size: 26px; margin-bottom: 8px;">🛡️</div>
          <div>All monitored sectors secure. Zero emergency alerts logged.</div>
        </div>`;
      return;
    }

    container.innerHTML = '';
    incidents.slice(0, 15).forEach(inc => {
      // Find snapshot thumbnail if available
      const evList = Array.isArray(inc.evidence) ? inc.evidence : [];
      const snap = evList.find(e => e.kind === 'snapshot');
      const thumbUrl = getAuthMediaUrl(snap ? snap.url : (inc.thumbnail || inc.snapshot_url || ''));

      let riskColor = '#00e676';
      if (inc.risk_level === 'High Risk' || (inc.risk_score && inc.risk_score >= 70)) riskColor = '#ff3b30';
      else if (inc.risk_level === 'Medium Risk' || (inc.risk_score && inc.risk_score >= 40)) riskColor = '#ff9500';

      let statusBadgeClass = 'badge-pending';
      const statusText = inc.response_status || inc.status || 'Pending';
      const normStatus = (statusText || '').toUpperCase();
      if (normStatus === 'SOLVED' || normStatus === 'RESOLVED') statusBadgeClass = 'badge-resolved';
      else if (normStatus === 'WORKING' || normStatus === 'RESPONDING') statusBadgeClass = 'badge-review';

      const item = document.createElement('div');
      item.className = 'police-alert-item';
      item.onclick = () => openPoliceDetailsModal(inc.id);

      const thumbHtml = thumbUrl
        ? `<img src="${thumbUrl}" alt="Evidence" onerror="this.onerror=null;this.parentElement.innerHTML='<span style=\\'font-size:22px;color:var(--text-muted);\\'>📹</span>';">`
        : `<span style="font-size:22px;color:var(--text-muted);">📹</span>`;

      item.innerHTML = `
        <div class="police-alert-thumb">
          ${thumbHtml}
        </div>
        <div class="police-alert-meta">
          <div class="police-alert-meta-top">
            <span class="police-alert-id">${inc.incident_id || 'INC-' + inc.id}</span>
            <span class="police-alert-time">${inc.time_str || ''} ${inc.date_str || ''}</span>
          </div>
          <div class="police-alert-loc" title="${inc.location || 'Monitored Sector'}">
            📍 ${inc.location || 'Monitored Sector'}
          </div>
          <div class="police-alert-sub">
            <span style="font-weight: 700; color: ${riskColor}; font-size: 11.5px;">
              ● ${inc.risk_level || 'RISK'} (${inc.risk_score || 0}%)
            </span>
            <span class="badge ${statusBadgeClass}" style="font-size: 11px; padding: 2px 8px;">
              ${statusText}
            </span>
          </div>
        </div>
      `;
      container.appendChild(item);
    });
  } catch (e) {
    console.error('Failed to load police alert notifications', e);
  }
}

// ==================== UNIFIED POLICE RESPONSE CENTER ====================
let responseCenterCache = {
  incidents: [],
  videos: [],
  cameras: []
};
let currentRespStatusFilter = 'ALL';
let currentRespCategoryFilter = 'ALL';

async function loadPoliceResponseCenter() {
  try {
    const [incRes, jobsRes, camRes] = await Promise.all([
      fetch('/api/incidents', { headers: getAuthHeaders() }),
      fetch('/api/jobs', { headers: getAuthHeaders() }),
      fetch('/api/cameras', { headers: getAuthHeaders() })
    ]);

    const incidents = incRes.ok ? await incRes.json() : [];
    const videos = jobsRes.ok ? await jobsRes.json() : [];
    const cameras = camRes.ok ? await camRes.json() : [];

    responseCenterCache = { incidents, videos, cameras };
    updateResponseCenterKPIs();
    renderPoliceResponseCenterTable();
  } catch (e) {
    console.error('Failed to load Police Response Center data', e);
  }
}

function updateResponseCenterKPIs() {
  const incs = responseCenterCache.incidents || [];
  const vids = responseCenterCache.videos || [];
  const cams = responseCenterCache.cameras || [];

  const activeInc = incs.filter(i => {
    const s = (i.status || '').toUpperCase();
    return s !== 'SOLVED' && s !== 'RESOLVED' && s !== 'REJECTED';
  });
  const pendingInc = incs.filter(i => {
    const s = (i.status || '').toUpperCase();
    return s === 'PENDING' || s === 'NEW';
  });

  const elActive = document.getElementById('respStatActiveIncidents');
  const elPending = document.getElementById('respStatPending');
  const elVideos = document.getElementById('respStatVideos');
  const elCameras = document.getElementById('respStatCameras');

  if (elActive) elActive.innerText = activeInc.length;
  if (elPending) elPending.innerText = pendingInc.length;
  if (elVideos) elVideos.innerText = vids.length;
  if (elCameras) elCameras.innerText = cams.length;
}

function filterResponseStatus(status) {
  currentRespStatusFilter = status;
  ['All', 'Pending', 'Working', 'Solved'].forEach(t => {
    const btn = document.getElementById(`tabResp${t}`);
    if (btn) {
      if (t.toUpperCase() === status) btn.classList.add('active');
      else btn.classList.remove('active');
    }
  });
  renderPoliceResponseCenterTable();
}

function filterResponseCategory(category) {
  currentRespCategoryFilter = category;
  renderPoliceResponseCenterTable();
}

function normalizeRecordStatus(statusStr) {
  const s = (statusStr || '').toUpperCase();
  if (s === 'PENDING' || s === 'NEW') return 'PENDING';
  if (s === 'WORKING' || s === 'RESPONDING' || s === 'UNDER REVIEW' || s === 'ACKNOWLEDGED') return 'WORKING';
  if (s === 'SOLVED' || s === 'RESOLVED' || s === 'CLOSED' || s === 'DONE') return 'SOLVED';
  return s || 'PENDING';
}

function renderPoliceResponseCenterTable() {
  const tbody = document.getElementById('policeResponseCenterTableBody');
  if (!tbody) return;

  const records = [];

  // 1. Incidents
  if (currentRespCategoryFilter === 'ALL' || currentRespCategoryFilter === 'INCIDENT') {
    (responseCenterCache.incidents || []).forEach(inc => {
      const normStatus = normalizeRecordStatus(inc.status);
      if (currentRespStatusFilter === 'ALL' || normStatus === currentRespStatusFilter) {
        records.push({
          type: 'INCIDENT',
          id: inc.id,
          code: inc.incident_id || inc.code || `INC-${inc.id}`,
          node: inc.camera_name || 'Forensic Upload',
          location: inc.location || 'Monitored Sector',
          coords: (inc.latitude && inc.longitude) ? `${Number(inc.latitude).toFixed(4)}, ${Number(inc.longitude).toFixed(4)}` : 'Location Unavailable',
          category: inc.risk_level || 'HIGH RISK',
          score: inc.risk_score || inc.risk_percentage || 85,
          timestamp: `${inc.time_str || ''} ${inc.date_str || ''}`.trim(),
          duration: inc.activity_duration || inc.duration || 0,
          status: normStatus,
          rawStatus: inc.status,
          raw: inc
        });
      }
    });
  }

  // 2. Videos
  if (currentRespCategoryFilter === 'ALL' || currentRespCategoryFilter === 'VIDEO') {
    (responseCenterCache.videos || []).forEach(j => {
      let vidNormStatus = 'SOLVED';
      if (j.status === 'queued' || j.status === 'processing') vidNormStatus = 'WORKING';
      else if (j.status === 'uploaded') vidNormStatus = 'PENDING';
      else if (j.status === 'failed') vidNormStatus = 'SOLVED';

      if (currentRespStatusFilter === 'ALL' || vidNormStatus === currentRespStatusFilter) {
        records.push({
          type: 'VIDEO',
          id: j.id,
          code: `VID-${j.id}`,
          node: j.filename || 'Uploaded Clip',
          location: j.location || 'Forensic Repository',
          coords: (j.lat && j.lon) ? `${Number(j.lat).toFixed(4)}, ${Number(j.lon).toFixed(4)}` : 'Metadata GPS',
          category: j.analysis_result || j.category || 'Forensic Stream',
          score: j.risk_score || 0,
          timestamp: j.uploaded_at ? new Date(j.uploaded_at).toLocaleString() : 'Uploaded',
          duration: j.duration || 0,
          status: vidNormStatus,
          rawStatus: j.status,
          raw: j
        });
      }
    });
  }

  // 3. Cameras
  if (currentRespCategoryFilter === 'ALL' || currentRespCategoryFilter === 'CCTV') {
    (responseCenterCache.cameras || []).forEach(c => {
      const camNormStatus = c.status === 'online' ? 'WORKING' : 'PENDING';
      if (currentRespStatusFilter === 'ALL' || camNormStatus === currentRespStatusFilter) {
        records.push({
          type: 'CCTV',
          id: c.id,
          code: c.camera_id || c.code || `CAM-${c.id}`,
          node: c.name,
          location: c.location || 'Monitored Node',
          coords: (c.latitude && c.longitude) ? `${Number(c.latitude).toFixed(4)}, ${Number(c.longitude).toFixed(4)}` : 'Station Sector',
          category: c.status === 'online' ? 'Real-Time Stream' : 'Connection Standby',
          score: c.status === 'online' ? 100 : 0,
          timestamp: c.last_checked ? new Date(c.last_checked).toLocaleTimeString() : 'Active Node',
          duration: '-',
          status: camNormStatus,
          rawStatus: c.status,
          raw: c
        });
      }
    });
  }

  if (records.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 40px 20px;">
          <div style="font-size: 32px; margin-bottom: 8px;">📋</div>
          <div><strong>No records available.</strong></div>
          <div style="font-size: 12.5px; margin-top: 4px;">Adjust status or category filters above to view other records.</div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = '';
  records.forEach(rec => {
    let typeBadge = '<span class="badge" style="background: rgba(27,115,232,0.2); color: var(--accent-cyan); border: 1px solid rgba(27,115,232,0.4);">🚨 Incident</span>';
    if (rec.type === 'VIDEO') {
      typeBadge = '<span class="badge" style="background: rgba(0,230,118,0.2); color: var(--accent-green); border: 1px solid rgba(0,230,118,0.4);">📹 Video</span>';
    } else if (rec.type === 'CCTV') {
      typeBadge = '<span class="badge" style="background: rgba(157,78,221,0.2); color: var(--accent-purple); border: 1px solid rgba(157,78,221,0.4);">📷 CCTV</span>';
    }

    let statusBadgeClass = 'badge-pending';
    if (rec.status === 'WORKING') statusBadgeClass = 'badge-review';
    else if (rec.status === 'SOLVED') statusBadgeClass = 'badge-resolved';

    // Lifecycle Action Buttons
    let actionButtonsHtml = '';
    if (rec.type === 'INCIDENT') {
      let lifecycleBtn = '';
      if (rec.status === 'PENDING') {
        lifecycleBtn = `<button class="btn btn-warning btn-sm" style="font-size: 12px; padding: 5px 10px;" onclick="updateIncidentResponseStatus(${rec.id}, 'WORKING')">⚡ Dispatch</button>`;
      } else if (rec.status === 'WORKING') {
        lifecycleBtn = `<button class="btn btn-success btn-sm" style="font-size: 12px; padding: 5px 10px;" onclick="updateIncidentResponseStatus(${rec.id}, 'SOLVED')">✓ Solve</button>`;
      } else {
        lifecycleBtn = `<span class="badge badge-resolved" style="font-size: 11.5px;">✓ Solved</span>`;
      }

      actionButtonsHtml = `
        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
          <button class="btn btn-primary btn-sm" style="font-size: 12px; padding: 5px 10px;" onclick="openPoliceDetailsModal(${rec.id})">👁️ View Details</button>
          ${lifecycleBtn}
          <button class="btn btn-outline btn-sm" style="color:var(--accent-red);border-color:rgba(255,59,48,0.4);font-size:12px;padding:5px 10px;" onclick="confirmDeleteRecord('INCIDENT', ${rec.id}, '${rec.code}')">🗑 Delete</button>
        </div>
      `;
    } else if (rec.type === 'VIDEO') {
      actionButtonsHtml = `
        <div style="display: flex; gap: 6px; align-items: center;">
          <button class="btn btn-outline btn-sm" style="font-size: 12px; padding: 5px 10px;" onclick="window.open('/api/jobs/${rec.id}/download', '_blank')">⬇ Download</button>
          <button class="btn btn-outline btn-sm" style="color:var(--accent-red);border-color:rgba(255,59,48,0.4);font-size:12px;padding:5px 10px;" onclick="confirmDeleteRecord('VIDEO', ${rec.id}, '${rec.code}')">🗑 Delete</button>
        </div>
      `;
    } else if (rec.type === 'CCTV') {
      actionButtonsHtml = `
        <div style="display: flex; gap: 6px; align-items: center;">
          <button class="btn btn-outline btn-sm" style="font-size: 12px; padding: 5px 10px;" onclick="switchPolicePage('dashboard')">🗺 Map Node</button>
        </div>
      `;
    }

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-weight: 700; color: #fff;">${rec.code}</td>
      <td>${typeBadge}</td>
      <td style="color: #e2e8f0; font-weight: 500;">${rec.node}</td>
      <td>
        <div style="font-weight: 600; color: #fff;">${rec.location}</div>
        <div style="font-size: 11px; color: var(--accent-cyan); font-family: monospace;">GPS: ${rec.coords}</div>
      </td>
      <td>
        <span style="font-weight: 700; color: ${rec.category.includes('High') || rec.category.includes('RISK') ? 'var(--accent-red)' : 'var(--accent-green)'};">${rec.category}</span>
        ${rec.score > 0 ? `<div style="font-size: 11px; color: var(--text-muted);">Confidence: ${rec.score}%</div>` : ''}
      </td>
      <td>
        <div style="color: #fff;">${rec.timestamp}</div>
        ${rec.duration !== '-' ? `<div style="font-size: 11px; color: var(--text-muted);">Duration: ${rec.duration}s</div>` : ''}
      </td>
      <td><span class="badge ${statusBadgeClass}">● ${rec.status}</span></td>
      <td>${actionButtonsHtml}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function updateIncidentResponseStatus(incId, targetStatus) {
  try {
    const res = await fetch(`/api/police/incidents/${incId}/respond`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ status: targetStatus, remarks: `Status updated to ${targetStatus} via Police Response Center.` })
    });
    if (res.ok) {
      showToast(`Incident #${incId} marked as ${targetStatus}!`, 'success');
      loadPoliceResponseCenter();
      loadPoliceStats();
      loadLatestAlert();
      loadPoliceAlertNotifications();
    } else {
      showToast('Failed to update status', 'danger');
    }
  } catch (e) {
    showToast('Network error while updating status', 'danger');
  }
}

// 3. Incident Management Lifecycle & 3-Tier Transitions
let currentMgmtFilter = 'ALL';
let allPoliceIncidentsCache = [];

function filterPoliceIncidents(filter) {
  currentMgmtFilter = filter;
  ['All', 'Pending', 'Working', 'Solved'].forEach(t => {
    const btn = document.getElementById(`tabMgmt${t}`);
    if (btn) {
      if (t.toUpperCase() === filter) btn.classList.add('active');
      else btn.classList.remove('active');
    }
  });
  renderPoliceIncidentsTable();
}

async function transitionIncidentStatus(incId, targetStatus) {
  try {
    const res = await fetch(`/api/police/incidents/${incId}/respond`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ status: targetStatus, remarks: `Status updated to ${targetStatus} by Police Officer` })
    });
    if (res.ok) {
      showToast(`Incident #${incId} transitioned to ${targetStatus}!`, 'success');
      loadPoliceStats();
      loadLatestAlert();
      loadPoliceIncidents();
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || 'Failed to update incident status', 'danger');
    }
  } catch (e) {
    showToast('Failed to update incident status', 'danger');
  }
}

async function loadPoliceIncidents() {
  try {
    const res = await fetch('/api/incidents', { headers: getAuthHeaders() });
    if (!res.ok) return;
    allPoliceIncidentsCache = await res.json();
    renderPoliceIncidentsTable();
  } catch (e) {
    console.error('Failed to load incidents', e);
  }
}

function renderPoliceIncidentsTable() {
  const tbody = document.getElementById('polIncidentsTableBody');
  if (!tbody) return;

  let filtered = allPoliceIncidentsCache;
  if (currentMgmtFilter === 'PENDING') {
    filtered = allPoliceIncidentsCache.filter(i => {
      const s = (i.status || '').toUpperCase();
      return s === 'PENDING' || s === 'NEW' || s === 'UNDER REVIEW';
    });
  } else if (currentMgmtFilter === 'WORKING') {
    filtered = allPoliceIncidentsCache.filter(i => {
      const s = (i.status || '').toUpperCase();
      return s === 'WORKING' || s === 'RESPONDING' || s === 'ACKNOWLEDGED';
    });
  } else if (currentMgmtFilter === 'SOLVED') {
    filtered = allPoliceIncidentsCache.filter(i => {
      const s = (i.status || '').toUpperCase();
      return s === 'SOLVED' || s === 'RESOLVED';
    });
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 30px;">No incidents matching '${currentMgmtFilter}' status found</td></tr>`;
    return;
  }

  tbody.innerHTML = '';
  filtered.forEach(inc => {
    const isRisk = (inc.risk_category === 'RISK' || inc.risk_score >= 50);
    const riskBadgeHtml = isRisk 
      ? `<span class="badge-risk">RISK</span>` 
      : `<span class="badge-norisk">NO RISK</span>`;

    let statusBadgeClass = 'badge-pending';
    const upperStatus = (inc.status || '').toUpperCase();
    if (upperStatus === 'SOLVED' || upperStatus === 'RESOLVED') statusBadgeClass = 'badge-resolved';
    else if (upperStatus === 'WORKING' || upperStatus === 'RESPONDING' || upperStatus === 'UNDER REVIEW' || upperStatus === 'ACKNOWLEDGED') statusBadgeClass = 'badge-review';

    let transitionButtons = '';
    if (upperStatus === 'PENDING' || upperStatus === 'NEW') {
      transitionButtons = `
        <button class="btn btn-outline btn-sm" onclick="transitionIncidentStatus(${inc.id}, 'WORKING')">▶ Start Working</button>
        <button class="btn btn-success btn-sm" onclick="transitionIncidentStatus(${inc.id}, 'SOLVED')">✓ Solve</button>
      `;
    } else if (upperStatus === 'WORKING' || upperStatus === 'RESPONDING' || upperStatus === 'UNDER REVIEW' || upperStatus === 'ACKNOWLEDGED') {
      transitionButtons = `
        <button class="btn btn-success btn-sm" onclick="transitionIncidentStatus(${inc.id}, 'SOLVED')">✓ Mark Solved</button>
      `;
    } else if (upperStatus === 'SOLVED' || upperStatus === 'RESOLVED') {
      transitionButtons = `
        <span class="badge badge-resolved" style="padding: 6px 10px;">✓ Solved / Closed</span>
      `;
    }

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-weight: 700; color: #fff;">${inc.incident_id}</td>
      <td>${inc.time_str}, ${inc.date_str}</td>
      <td>${inc.camera_name}</td>
      <td>${inc.location || 'Location Unavailable'}</td>
      <td>${riskBadgeHtml}</td>
      <td><span class="badge ${statusBadgeClass}">${inc.status}</span></td>
      <td>
        <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
          <button class="btn btn-primary btn-sm" onclick="openPoliceDetailsModal(${inc.id})">👁 View Details</button>
          ${transitionButtons}
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// 4. Video Records (Module 4)
async function loadPoliceVideoRecords() {
  try {
    const res = await fetch('/api/videos/jobs', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const jobs = await res.json();
    const tbody = document.getElementById('polVideoRecordsTableBody');
    if (!tbody) return;

    if (jobs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 30px;">No Video Analysis Jobs Recorded</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    jobs.forEach(j => {
      let resultsObj = {};
      try { resultsObj = JSON.parse(j.results || '{}'); } catch(e){}

      const outcome = resultsObj.classification_outcome || 'NO_RISK_DETECTED';
      let outcomeBadge = '<span class="badge badge-resolved">No Risk Detected</span>';
      if (outcome === 'RISK_DETECTED') {
        outcomeBadge = `<span class="risk-level-badge high">${resultsObj.risk_category || 'Risk Detected'}</span>`;
      } else if (outcome === 'UNCERTAIN') {
        outcomeBadge = `<span class="risk-level-badge medium">Uncertain</span>`;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-weight: 700; color: #fff;">${j.job_id}</td>
        <td>${j.filename}</td>
        <td>${resultsObj.explanation || 'Completed successfully'}</td>
        <td>${outcomeBadge}</td>
        <td>${j.completed_at || j.created_at}</td>
        <td>
          ${resultsObj.incident_created 
            ? `<button class="btn btn-outline btn-sm" onclick="switchPolicePage('alerts')">View Alert</button>` 
            : `<span style="color: var(--text-muted); font-size: 11px;">Preserved</span>`}
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load video records', e);
  }
}

// 5. CCTV Records (Module 5)
async function loadPoliceCCTVRecords() {
  try {
    const res = await fetch('/api/cameras', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const cameras = await res.json();
    const container = document.getElementById('polCCTVContainer');
    if (!container) return;

    if (cameras.length === 0) {
      container.innerHTML = '<div style="grid-column: 1 / -1; color: var(--text-muted); font-size: 13px; text-align: center; padding: 30px;">Zero CCTV Cameras Currently Registered</div>';
      return;
    }

    container.innerHTML = '';
    cameras.forEach(cam => {
      const isOnline = cam.status === 'online';
      const card = document.createElement('div');
      card.className = 'dash-card';
      card.innerHTML = `
        <div class="dash-card-header">
          <div class="dash-card-title">${cam.name} (${cam.camera_id})</div>
          <span class="badge ${isOnline ? 'badge-online' : 'badge-offline'}">● ${cam.status.toUpperCase()}</span>
        </div>
        <div class="dash-card-body">
          <div style="width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 6px; overflow: hidden; margin-bottom: 10px;">
            <img src="/api/cameras/${cam.id}/feed" style="width: 100%; height: 100%; object-fit: cover;" onerror="this.src='/static/images/marker-icon.png'" alt="${cam.name}">
          </div>
          <div style="font-size: 12px; color: var(--text-muted); line-height: 1.8;">
            <strong>Location:</strong> ${cam.location}<br>
            <strong>Coordinates:</strong> ${cam.latitude}, ${cam.longitude}<br>
            <strong>Jurisdiction:</strong> Central Police Station Zone
          </div>
        </div>
      `;
      container.appendChild(card);
    });
  } catch (e) {
    console.error('Failed to load CCTV records', e);
  }
}

// 6. Forensic Evidence Module (Module 6)
function loadPoliceFullEvidence() {
  if (latestIncidentObj && latestIncidentObj.evidence) {
    const player = document.getElementById('polFullEvidencePlayer');
    if (player) player.src = latestIncidentObj.evidence.video_clip_url;
    document.getElementById('polEvIncId').innerText = latestIncidentObj.incident_id;
    document.getElementById('polEvChecksum').innerText = formatHash(latestIncidentObj.evidence.snapshot_hash);
    document.getElementById('polEvStation').innerText = latestIncidentObj.station_name || 'Central Police Station';
    const badge = document.getElementById('polEvStatusBadge');
    if (badge) {
      badge.innerText = latestIncidentObj.evidence.is_verified ? 'Bitwise Verified' : 'Pending Verification';
    }
  } else {
    document.getElementById('polEvIncId').innerText = 'None';
    document.getElementById('polEvChecksum').innerText = '-';
  }
}

// 7. Full-Featured Popup Modal with Complete Proof (Parts 6 & 9)
async function openPoliceDetailsModal(incId) {
  const targetId = incId || (latestIncidentObj ? latestIncidentObj.id : null);
  if (!targetId) {
    showToast('No active incident selected.', 'warning');
    return;
  }

  try {
    const res = await fetch(`/api/incidents/${targetId}`, { headers: getAuthHeaders() });
    if (!res.ok) {
      showToast('Failed to load incident details.', 'danger');
      return;
    }
    const inc = await res.json();
    modalIncidentCache = inc;

    const modalTitle = document.getElementById('polModalTitle');
    if (modalTitle) {
      modalTitle.innerText = `Incident Details – ${inc.code || inc.incident_id || inc.id}`;
    }

    const body = document.getElementById('polModalBody');
    if (!body) return;

    // Resolve evidence URLs
    const mEvList = Array.isArray(inc.evidence) ? inc.evidence : [];
    const mSnapEv = mEvList.find(e => e.kind === 'snapshot');
    const mClipEv = mEvList.find(e => e.kind === 'clip');
    const rawSnapUrl = inc.snapshot_url || (mSnapEv ? mSnapEv.url : '') || (inc.evidence_details && inc.evidence_details.snapshot_url) || '';
    const rawClipUrl = inc.video_clip_url || (mClipEv ? mClipEv.url : '') || (inc.evidence_details && inc.evidence_details.video_clip_url) || '';

    const authSnapUrl = rawSnapUrl ? getAuthMediaUrl(rawSnapUrl) : '';
    const authClipUrl = rawClipUrl ? getAuthMediaUrl(rawClipUrl) : '';

    // Date & Time formatting
    let dateTimeDisplay = 'Not available';
    if (inc.date_str && inc.time_str) {
      dateTimeDisplay = `${inc.date_str} at ${inc.time_str}`;
    } else if (inc.detected_at) {
      try {
        dateTimeDisplay = new Date(inc.detected_at).toLocaleString();
      } catch (e) {
        dateTimeDisplay = inc.detected_at;
      }
    }

    // Location
    const locationDisplay = (inc.location_text && inc.location_text.trim() && inc.location_text !== 'None')
      ? inc.location_text
      : ((inc.location && inc.location.trim() && inc.location !== 'None') ? inc.location : 'Location unavailable');

    // Duration
    let durationDisplay = 'Not available';
    if (inc.duration !== null && inc.duration !== undefined && inc.duration !== '') {
      durationDisplay = `${Number(inc.duration).toFixed(1)}s`;
    } else if (inc.activity_duration !== null && inc.activity_duration !== undefined) {
      durationDisplay = `${Number(inc.activity_duration).toFixed(1)}s`;
    }

    // Risk Category
    const categoryDisplay = inc.category || inc.risk_type || 'Unspecified';

    body.innerHTML = `
      <!-- 1. Incident Information Summary Card -->
      <div class="modal-section-box" style="margin-bottom: 16px; padding: 14px 18px;">
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; font-size: 13.5px; line-height: 1.6;">
          <div>
            <div style="color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Incident ID</div>
            <div style="font-weight: 700; color: #fff; font-size: 15px; margin-top: 2px;">
              ${inc.code || inc.incident_id || inc.id}
            </div>
          </div>
          <div>
            <div style="color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Risk Category</div>
            <div style="font-weight: 700; color: var(--accent-red); font-size: 14px; margin-top: 2px;">
              ${categoryDisplay}
            </div>
          </div>
          <div>
            <div style="color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Date & Time</div>
            <div style="font-weight: 600; color: #e2e8f0; font-size: 13.5px; margin-top: 2px;">
              ${dateTimeDisplay}
            </div>
          </div>
          <div>
            <div style="color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Location</div>
            <div style="font-weight: 600; color: #e2e8f0; font-size: 13.5px; margin-top: 2px;">
              ${locationDisplay}
            </div>
          </div>
          <div>
            <div style="color: var(--text-muted); font-size: 11px; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Duration</div>
            <div style="font-weight: 700; color: var(--accent-cyan); font-size: 14px; margin-top: 2px;">
              ${durationDisplay}
            </div>
          </div>
        </div>
      </div>

      <!-- 2. Forensic Visual Proof (Playable Video Player & Clear Snapshot) -->
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px;">
        <!-- Playable Video Clip -->
        <div class="modal-section-box" style="margin-bottom: 0; padding: 12px 14px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-weight: 700; font-size: 13px; color: #fff;">
            <span>🎬 Video Clip</span>
            ${authClipUrl ? `<a href="${authClipUrl}" download class="btn btn-outline btn-sm" style="font-size: 11px; padding: 3px 8px;">⬇ Download</a>` : ''}
          </div>
          <div style="width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color); display: flex; align-items: center; justify-content: center; position: relative;">
            ${authClipUrl ? `
              <video id="modalVideoPlayer" controls playsinline preload="metadata" style="width: 100%; height: 100%; object-fit: contain; background: #000;" onerror="this.style.display='none'; document.getElementById('modalVideoFallback').style.display='flex';">
                <source src="${authClipUrl}" type="video/mp4">
                Your browser does not support HTML5 video playback.
              </video>
              <div id="modalVideoFallback" style="display: none; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); padding: 30px 15px; text-align: center;">
                <span style="font-size: 32px; margin-bottom: 6px;">⚠️</span>
                <span style="font-size: 13px; font-weight: 500;">Video evidence unavailable</span>
              </div>
            ` : `
              <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); padding: 30px 15px; text-align: center;">
                <span style="font-size: 32px; margin-bottom: 6px;">📹</span>
                <span style="font-size: 13px; font-weight: 500;">Video evidence unavailable</span>
              </div>
            `}
          </div>
        </div>

        <!-- Forensic Snapshot -->
        <div class="modal-section-box" style="margin-bottom: 0; padding: 12px 14px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-weight: 700; font-size: 13px; color: #fff;">
            <span>📸 Forensic Snapshot</span>
            ${authSnapUrl ? `<a href="${authSnapUrl}" download class="btn btn-outline btn-sm" style="font-size: 11px; padding: 3px 8px;">⬇ Download</a>` : ''}
          </div>
          <div style="width: 100%; aspect-ratio: 16/9; background: #000; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color); display: flex; align-items: center; justify-content: center;">
            ${authSnapUrl ? `
              <img src="${authSnapUrl}" alt="Forensic Snapshot" style="width: 100%; height: 100%; object-fit: contain; cursor: pointer;" onclick="window.open('${authSnapUrl}', '_blank')" onerror="this.style.display='none'; document.getElementById('modalSnapFallback').style.display='flex';" title="Click to open full-size image in new tab">
              <div id="modalSnapFallback" style="display: none; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); padding: 30px 15px; text-align: center;">
                <span style="font-size: 32px; margin-bottom: 6px;">🖼️</span>
                <span style="font-size: 13px; font-weight: 500;">Snapshot unavailable</span>
              </div>
            ` : `
              <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-muted); padding: 30px 15px; text-align: center;">
                <span style="font-size: 32px; margin-bottom: 6px;">🖼️</span>
                <span style="font-size: 13px; font-weight: 500;">Snapshot unavailable</span>
              </div>
            `}
          </div>
        </div>
      </div>
    `;

    document.getElementById('policeIncidentModal').classList.add('active');
  } catch (e) {
    console.error('Failed to open modal', e);
    showToast('Error opening modal: ' + e.message, 'danger');
  }
}

function closePoliceModal() {
  document.getElementById('policeIncidentModal').classList.remove('active');
  const videoPlayer = document.getElementById('modalVideoPlayer');
  if (videoPlayer) {
    try { videoPlayer.pause(); } catch(e) {}
  }
}

async function modalSubmitStatusUpdate() {
  if (!modalIncidentCache) return;
  const status = document.getElementById('modalStatusSelect').value;
  const remarks = document.getElementById('modalRemarksInput').value.trim();

  try {
    const res = await fetch(`/api/police/incidents/${modalIncidentCache.id}/respond`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ status, remarks })
    });
    if (res.ok) {
      showToast(`Incident status updated to '${status}'! Synchronized with Admin.`, 'success');
      loadPoliceStats();
      loadLatestAlert();
      loadPoliceAlertNotifications();
      if (currentPolicePage === 'response-center') loadPoliceResponseCenter();
      if (currentPolicePage === 'alerts') loadPoliceAlerts();
      if (currentPolicePage === 'management') loadPoliceIncidents();
    } else {
      showToast('Failed to update status', 'danger');
    }
  } catch (e) {
    showToast('Failed to update status', 'danger');
  }
}

async function modalAcknowledgeIncident() {
  if (!modalIncidentCache) return;
  try {
    const res = await fetch(`/api/police/incidents/${modalIncidentCache.id}/acknowledge`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      showToast(`Incident ${modalIncidentCache.incident_id || modalIncidentCache.code} acknowledged!`, 'success');
      closePoliceModal();
      loadPoliceStats();
      loadLatestAlert();
      if (currentPolicePage === 'alerts') loadPoliceAlerts();
      if (currentPolicePage === 'management') loadPoliceIncidents();
    }
  } catch (e) {
    showToast('Failed to acknowledge alert', 'danger');
  }
}

function modalShowOnMap() {
  if (!modalIncidentCache) return;
  closePoliceModal();
  switchPolicePage('map');
  setTimeout(() => {
    if (polFullMapInstance && modalIncidentCache.latitude && modalIncidentCache.longitude) {
      polFullMapInstance.setView([modalIncidentCache.latitude, modalIncidentCache.longitude], 15);
    }
  }, 300);
}

function modalViewFullSnapshot() {
  if (!modalIncidentCache) return;
  const evList = Array.isArray(modalIncidentCache.evidence) ? modalIncidentCache.evidence : [];
  const snapEv = evList.find(e => e.kind === 'snapshot');
  const url = (snapEv ? snapEv.url : '') || modalIncidentCache.snapshot_url;
  if (url) {
    window.open(getAuthMediaUrl(url), '_blank');
  } else {
    showToast('Snapshot URL not available', 'warning');
  }
}

function modalDownloadEvidence(kind = 'clip') {
  if (!modalIncidentCache) return;
  const evList = Array.isArray(modalIncidentCache.evidence) ? modalIncidentCache.evidence : [];
  if (kind === 'package') {
    window.open(getAuthMediaUrl(`/api/incidents/${modalIncidentCache.id}/package`), '_blank');
    return;
  }
  const targetEv = evList.find(e => e.kind === kind);
  if (targetEv) {
    window.open(getAuthMediaUrl(`/api/evidence/${targetEv.id}/file?download=true`), '_blank');
  } else {
    window.open(getAuthMediaUrl(`/api/incidents/${modalIncidentCache.id}/download?file_type=${kind}`), '_blank');
  }
}

async function modalVerifyHash() {
  if (!modalIncidentCache) return;
  try {
    const evList = Array.isArray(modalIncidentCache.evidence) ? modalIncidentCache.evidence : [];
    const snapEv = evList.find(e => e.kind === 'snapshot');
    const endpoint = snapEv ? `/api/evidence/${snapEv.id}/verify` : `/api/incidents/${modalIncidentCache.id}/verify`;
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      const result = await res.json();
      if (result.is_match || result.result === 'match' || result.overall === 'match') {
        const hashDisplay = result.recalculated_hash || result.actual || result.expected || '';
        showToast(`✓ SHA-256 Bitwise Match Verified: ${hashDisplay ? hashDisplay.substring(0, 16) + '...' : 'Integrity Preserved'}`, 'success');
        const badge = document.getElementById('modalHashStatusBadge');
        if (badge) {
          badge.innerText = '✓ Bitwise Verified Match';
          badge.className = 'badge badge-resolved';
        }
        return;
      }
    }
    showToast('Integrity Warning: SHA-256 verification mismatch', 'warning');
  } catch (e) {
    showToast('Verification failed: ' + e.message, 'danger');
  }
}

async function quickAcknowledge(incId) {
  try {
    const res = await fetch(`/api/police/incidents/${incId}/acknowledge`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      showToast('Alert acknowledged!', 'success');
      loadPoliceStats();
      loadPoliceAlerts();
    }
  } catch (e) {
    showToast('Failed to acknowledge alert', 'danger');
  }
}

async function acknowledgeLatestAlert() {
  if (!latestIncidentObj) return;
  await quickAcknowledge(latestIncidentObj.id);
  loadLatestAlert();
}

async function submitPoliceResponse() {
  if (!latestIncidentObj) return;
  const status = document.getElementById('polResponseStatusSelect').value;
  const remarks = document.getElementById('polRemarksInput').value.trim();

  try {
    const res = await fetch(`/api/police/incidents/${latestIncidentObj.id}/respond`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ status, remarks })
    });
    if (res.ok) {
      showToast(`Status updated to '${status}'. Reflected on Admin Dashboard.`, 'success');
      document.getElementById('polRemarksInput').value = '';
      loadPoliceStats();
      loadLatestAlert();
    }
  } catch (e) {
    showToast('Failed to update status', 'danger');
  }
}

function downloadPoliceEvidence() {
  if (!latestIncidentObj) return;
  window.open(`/api/evidence/${latestIncidentObj.id}/download?file_type=clip`, '_blank');
}

async function verifyPoliceEvidenceHash(isFullPage = false) {
  if (!latestIncidentObj) return;
  try {
    const res = await fetch(`/api/evidence/${latestIncidentObj.id}/verify?file_type=snapshot`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    const result = await res.json();
    if (result.is_match) {
      showToast('SHA-256 Verified! Forensic integrity confirmed.', 'success');
      const hashBadge = document.getElementById('polHashBadge');
      if (hashBadge) {
        hashBadge.style.display = 'inline-block';
        hashBadge.innerText = '✓ Hash Verified';
        hashBadge.className = 'badge badge-resolved';
      }
      if (isFullPage) {
        const box = document.getElementById('polHashRecalcBox');
        box.style.display = 'block';
        box.innerHTML = `
          <strong style="color: var(--accent-green);">✓ ${result.status}</strong><br>
          Computed: <code>${result.recalculated_hash}</code><br>
          Timestamp: ${result.verification_time}
        `;
      }
    } else {
      showToast('Integrity Alert: Hash mismatch!', 'danger');
    }
  } catch (e) {
    showToast('Verification failed', 'danger');
  }
}

// 8. Leaflet Maps
function initPoliceDashboardMap() {
  const el = document.getElementById('policeDashMap');
  if (!el || polDashMapInstance) return;

  polDashMapInstance = L.map('policeDashMap', { zoomControl: false }).setView([17.3850, 78.4667], 14);
  L.control.zoom({ position: 'topright' }).addTo(polDashMapInstance);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18
  }).addTo(polDashMapInstance);

  fetchPoliceMapMarkers(polDashMapInstance);
}

function initPoliceFullMap() {
  const el = document.getElementById('policeFullMap');
  if (!el) return;

  if (polFullMapInstance) {
    polFullMapInstance.invalidateSize();
    return;
  }

  polFullMapInstance = L.map('policeFullMap').setView([17.3850, 78.4667], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18
  }).addTo(polFullMapInstance);

  fetchPoliceMapMarkers(polFullMapInstance);
}

function resetPoliceMapView() {
  if (polFullMapInstance) {
    polFullMapInstance.setView([17.3850, 78.4667], 13);
  }
}

async function fetchPoliceMapMarkers(mapObj) {
  try {
    const res = await fetch('/api/map/markers', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();

    const camIcon = L.divIcon({
      html: '<div style="background:#1877f2;color:#fff;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 10px #1877f2;border:2px solid #fff;">📷</div>',
      className: '',
      iconSize: [28, 28]
    });

    const incIcon = L.divIcon({
      html: '<div style="background:#ff3b30;color:#fff;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 12px #ff3b30;border:2px solid #fff;font-weight:bold;">!</div>',
      className: '',
      iconSize: [32, 32]
    });

    const policeIcon = L.divIcon({
      html: '<div style="background:#9d4edd;color:#fff;border-radius:50%;width:30px;height:30px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 10px #9d4edd;border:2px solid #fff;">🏛️</div>',
      className: '',
      iconSize: [30, 30]
    });

    data.cameras.forEach(cam => {
      L.marker([cam.latitude, cam.longitude], { icon: camIcon })
        .addTo(mapObj)
        .bindPopup(`<strong>Camera: ${cam.name}</strong><br>Status: ${cam.status.toUpperCase()}`);
    });

    data.police_stations.forEach(st => {
      L.marker([st.latitude, st.longitude], { icon: policeIcon })
        .addTo(mapObj)
        .bindPopup(`<strong>Your Station: ${st.name}</strong><br>Code: ${st.code}`);
    });

    data.incidents.forEach(inc => {
      L.marker([inc.latitude, inc.longitude], { icon: incIcon })
        .addTo(mapObj)
        .bindPopup(`<strong>Incident: ${inc.incident_id}</strong><br>Risk: ${inc.risk_level}<br>Distance: ${inc.distance_km} km`);
      
      if (data.police_stations.length > 0) {
        const st = data.police_stations[0];
        L.polyline([[inc.latitude, inc.longitude], [st.latitude, st.longitude]], {
          color: '#ff3b30',
          dashArray: '5, 8',
          weight: 2
        }).addTo(mapObj);
      }
    });

  } catch (e) {
    console.error('Failed to load map markers', e);
  }
}

// ==================== POLICE SETTINGS & PREFERENCES ====================
function loadPoliceSettings() {
  const user = getUserProfile();
  const setOfficerName = document.getElementById('setOfficerName');
  if (setOfficerName && user.full_name) {
    setOfficerName.value = user.full_name;
  }
  
  // Load saved preferences
  const soundPref = localStorage.getItem('police_pref_sound') !== 'false';
  const autoModalPref = localStorage.getItem('police_pref_auto_modal') !== 'false';
  const desktopPref = localStorage.getItem('police_pref_desktop') === 'true';
  const sensitivityPref = localStorage.getItem('police_pref_sensitivity') || 'ALL';
  const autoAckPref = localStorage.getItem('police_pref_auto_ack') || 'MANUAL';

  const elSound = document.getElementById('prefSoundEnabled');
  const elModal = document.getElementById('prefAutoOpenModal');
  const elDesk = document.getElementById('prefDesktopNotify');
  const elSens = document.getElementById('prefSensitivity');
  const elAck = document.getElementById('prefAutoAck');

  if (elSound) elSound.checked = soundPref;
  if (elModal) elModal.checked = autoModalPref;
  if (elDesk) elDesk.checked = desktopPref && ('Notification' in window && Notification.permission === 'granted');
  if (elSens) elSens.value = sensitivityPref;
  if (elAck) elAck.value = autoAckPref;
}

async function handleSavePoliceProfile(e) {
  if (e) e.preventDefault();
  const nameInput = document.getElementById('setOfficerName');
  const fullName = nameInput ? nameInput.value.trim() : '';
  if (!fullName) return;

  try {
    const res = await fetch('/api/auth/profile', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ full_name: fullName })
    });
    if (res.ok) {
      const user = getUserProfile();
      user.full_name = fullName;
      localStorage.setItem('cctv_user', JSON.stringify(user));
      
      const nameLabel = document.getElementById('policeOfficerName');
      if (nameLabel) nameLabel.innerText = fullName;
      showToast('Officer profile updated successfully!', 'success');
    } else {
      showToast('Failed to update profile', 'danger');
    }
  } catch (e) {
    showToast('Network error updating profile', 'danger');
  }
}

function savePolicePreferences() {
  const elSound = document.getElementById('prefSoundEnabled');
  const elModal = document.getElementById('prefAutoOpenModal');
  const elSens = document.getElementById('prefSensitivity');
  const elAck = document.getElementById('prefAutoAck');

  if (elSound) localStorage.setItem('police_pref_sound', elSound.checked ? 'true' : 'false');
  if (elModal) localStorage.setItem('police_pref_auto_modal', elModal.checked ? 'true' : 'false');
  if (elSens) localStorage.setItem('police_pref_sensitivity', elSens.value);
  if (elAck) localStorage.setItem('police_pref_auto_ack', elAck.value);

  showToast('Preferences saved.', 'info');
}

function toggleDesktopNotifications(enable) {
  if (enable) {
    if (!('Notification' in window)) {
      showToast('This browser does not support desktop notifications.', 'warning');
      const elDesk = document.getElementById('prefDesktopNotify');
      if (elDesk) elDesk.checked = false;
      return;
    }
    Notification.requestPermission().then(permission => {
      if (permission === 'granted') {
        localStorage.setItem('police_pref_desktop', 'true');
        showToast('Desktop notifications enabled!', 'success');
        try {
          new Notification('Police Response Command Hub', {
            body: 'Emergency incident notifications are now active on your desktop.',
            icon: '/static/images/marker-icon.png'
          });
        } catch(e) {}
      } else {
        localStorage.setItem('police_pref_desktop', 'false');
        const elDesk = document.getElementById('prefDesktopNotify');
        if (elDesk) elDesk.checked = false;
        showToast('Notification permission denied in browser.', 'warning');
      }
    });
  } else {
    localStorage.setItem('police_pref_desktop', 'false');
    showToast('Desktop notifications disabled.', 'info');
  }
}

function testDesktopNotification() {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    toggleDesktopNotifications(true);
    return;
  }
  try {
    new Notification('🚨 Police Alert System Test', {
      body: 'Priority alert: Real-time emergency notification channel verified active.',
      icon: '/static/images/marker-icon.png'
    });
    showToast('Test notification dispatched.', 'info');
  } catch(e) {
    showToast('Could not send notification', 'warning');
  }
}

function triggerDesktopNotification(title, options) {
  if (localStorage.getItem('police_pref_desktop') === 'true' && 'Notification' in window && Notification.permission === 'granted') {
    try {
      new Notification(title, {
        icon: '/static/images/marker-icon.png',
        ...options
      });
    } catch(e) {}
  }
}

async function handlePolicePasswordChange(e) {
  if (e) e.preventDefault();
  const cur = document.getElementById('pwCurrent').value;
  const nw = document.getElementById('pwNew').value;
  const conf = document.getElementById('pwConfirm').value;

  if (nw !== conf) {
    showToast('New passwords do not match.', 'danger');
    return;
  }
  if (nw.length < 8) {
    showToast('New password must be at least 8 characters.', 'danger');
    return;
  }

  try {
    const res = await fetch('/api/auth/password', {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ old: cur, new: nw })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast('Password changed successfully!', 'success');
      document.getElementById('policePasswordForm').reset();
    } else {
      showToast(data.detail || 'Could not change password.', 'danger');
    }
  } catch (e) {
    showToast('Network error changing password', 'danger');
  }
}

async function confirmDeleteRecord(type, id, code) {
  const label = type === 'INCIDENT' ? `Incident ${code}` : (type === 'VIDEO' ? `Video record ${code}` : `Record ${code}`);
  const ok = confirm(`Are you sure you want to delete ${label}?\n\nThis will permanently delete the record and its associated evidence files.`);
  if (!ok) return;

  try {
    let url = '';
    if (type === 'INCIDENT') {
      url = `/api/incidents/${id}?force=true`;
    } else if (type === 'VIDEO') {
      url = `/api/jobs/${id}`;
    } else if (type === 'CCTV') {
      url = `/api/cameras/${id}`;
    } else {
      return;
    }

    const res = await fetch(url, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });

    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showToast(`${label} was successfully deleted.`, 'success');
      loadPoliceResponseCenter();
      loadPoliceStats();
      loadLatestAlert();
      loadPoliceAlertNotifications();
    } else {
      const errMsg = data.detail || data.message || 'Deletion failed.';
      showToast(`Cannot delete: ${errMsg}`, 'danger');
    }
  } catch (err) {
    showToast(`Error deleting record: ${err.message}`, 'danger');
  }
}
