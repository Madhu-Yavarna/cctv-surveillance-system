// Central Admin Dashboard Logic - Full Geospatial & Immediate AI Analysis

let currentActivePage = 'dashboard';
let dashMapInstance = null;
let fullMapInstance = null;
let currentIncidentInModal = null;
let currentEvidenceData = null;
let activeMediaMode = 'clip';
let adminSocket = null;

// Map markers tracking
let dashUserMarker = null;
let dashPoliceMarkers = [];
let allAdminIncidentsCache = [];
let allCamerasCache = [];
let dashActiveCamMarker = null;
let lastKnownLocation = { lat: 17.3850, lon: 78.4667, name: 'Surveillance Command Grid' };

window.addEventListener('DOMContentLoaded', () => {
  if (!checkAuth('admin')) return;

  const user = getUserProfile();
  const nameLabel = document.getElementById('userNameLabel');
  if (nameLabel) nameLabel.innerText = user.full_name || 'Admin';

  startClock('liveDate', 'liveTime');
  initWebSocket();
  loadAdminStats();
  initDashboardMap();
  loadRecentIncidents();
  loadConnectedCameras();

  // Attempt automatic browser geolocation upon page load
  triggerBrowserGeolocation(false);

  // Periodic stats refresher
  setInterval(loadAdminStats, 10000);
});

// WebSocket for real-time alerts
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = getAuthToken();
  if (!token) return;
  const wsUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`;

  try {
    adminSocket = new WebSocket(wsUrl);
    adminSocket.onopen = () => console.log('Admin WebSocket connected.');
    adminSocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWebSocketMessage(msg);
      } catch (e) {
        console.error('Failed to parse WS msg', e);
      }
    };
    adminSocket.onclose = () => {
      setTimeout(initWebSocket, 4000);
    };
  } catch (e) {
    console.error('WebSocket init error', e);
  }
}

function handleWebSocketMessage(msg) {
  if (msg.type === 'incident' || msg.type === 'NEW_INCIDENT_ALERT' || msg.type === 'NEW_INCIDENT_CANDIDATE') {
    const inc = msg.incident || {};
    showToast(`🚨 High-Risk Alert: ${inc.incident_id || inc.code || 'Anomaly Detected'}!`, 'danger');
    loadAdminStats();
    loadRecentIncidents();
    if (currentActivePage === 'incidents') loadIncidentsTable();
  } else if (msg.type === 'STATUS_UPDATED' || msg.type === 'RESPONSE_STATUS_UPDATED') {
    showToast(`Police Response Update: ${msg.incident_id || msg.code} is now ${msg.status}`, 'info');
    loadAdminStats();
    loadRecentIncidents();
    if (currentActivePage === 'incidents') loadIncidentsTable();
  } else if (msg.type === 'INCIDENT_DELETED' || msg.type === 'incident_deleted') {
    showToast(`Incident #${msg.incident_id || msg.code} was deleted.`, 'info');
    loadAdminStats();
    loadRecentIncidents();
    if (currentActivePage === 'incidents') loadIncidentsTable();
  }
}

// Subpage Switching
function switchPage(page) {
  currentActivePage = page;
  document.querySelectorAll('.subpage').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));

  const targetSection = document.getElementById(`page-${page}`);
  if (targetSection) targetSection.classList.add('active');

  const links = document.querySelectorAll('.nav-link');
  links.forEach(l => {
    if (l.innerText.toLowerCase().includes(page) || (page === 'dashboard' && l.innerText.includes('Dashboard'))) {
      l.classList.add('active');
    }
  });

  const subtitle = document.getElementById('pageHeaderSubtitle');
  if (subtitle) {
    const pageTitles = {
      dashboard: 'Central Admin Dashboard',
      cameras: 'Camera Management',
      upload: 'Video Upload & AI Analysis',
      incidents: 'Incident Management',
      settings: 'System Configuration'
    };
    subtitle.innerText = pageTitles[page] || 'Central Admin';
  }

  if (page === 'incidents') {
    loadIncidentsTable();
  } else if (page === 'cameras') {
    loadConnectedCameras();
  } else if (page === 'dashboard') {
    if (dashMapInstance) {
      setTimeout(() => dashMapInstance.invalidateSize(), 200);
    }
  }
}

// 1. Dashboard Stats
async function loadAdminStats() {
  try {
    const res = await fetch('/api/admin/stats', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const stats = await res.json();
    const elCams = document.getElementById('statTotalCameras');
    const elIncs = document.getElementById('statTotalIncidents');
    const elVids = document.getElementById('statVideosAnalyzed');
    const elPend = document.getElementById('statPendingIncidents');
    if (elCams) elCams.innerText = stats.total_cameras;
    if (elIncs) elIncs.innerText = stats.total_incidents;
    if (elVids) elVids.innerText = stats.videos_analyzed !== undefined ? stats.videos_analyzed : 0;
    if (elPend) elPend.innerText = stats.pending_active_incidents !== undefined ? stats.pending_active_incidents : 0;
  } catch (e) {
    console.error('Failed to load stats', e);
  }
}

function onDashCameraChange(camId) {
  const img = document.getElementById('liveFeedImg');
  const title = document.getElementById('dashCamTitle');
  const placeholder = document.getElementById('liveFeedPlaceholder');
  const liveBadge = document.getElementById('liveBadge');
  const resBadge = document.getElementById('resBadge');
  const statusBadge = document.getElementById('dashCamStatusBadge');

  const nameIdEl = document.getElementById('dashCamMetaNameId');
  const locEl = document.getElementById('dashCamMetaLocation');
  const coordsEl = document.getElementById('dashCamMetaCoords');

  if (!camId) {
    if (img) img.style.display = 'none';
    if (placeholder) placeholder.style.display = 'block';
    if (liveBadge) liveBadge.style.display = 'none';
    if (resBadge) resBadge.style.display = 'none';
    if (statusBadge) {
      statusBadge.innerText = '● Standby';
      statusBadge.className = 'badge badge-offline';
    }
    if (title) title.innerText = 'No Camera Selected';
    if (nameIdEl) nameIdEl.innerText = 'None Selected';
    if (locEl) locEl.innerText = 'Location Unavailable';
    if (coordsEl) coordsEl.innerText = 'Location Unavailable';
    if (dashActiveCamMarker && dashMapInstance) {
      dashMapInstance.removeLayer(dashActiveCamMarker);
      dashActiveCamMarker = null;
    }
    return;
  }

  const cam = allCamerasCache.find(c => String(c.id) === String(camId));

  if (img) {
    img.src = `/api/cameras/${camId}/feed`;
    img.style.display = 'block';
  }
  if (placeholder) placeholder.style.display = 'none';
  if (liveBadge) liveBadge.style.display = 'block';
  if (resBadge) resBadge.style.display = 'block';
  if (statusBadge) {
    statusBadge.innerText = '● Online';
    statusBadge.className = 'badge badge-online';
  }

  if (cam) {
    if (title) title.innerText = `${cam.name} (${cam.camera_id})`;
    if (nameIdEl) nameIdEl.innerText = `${cam.name} (${cam.camera_id})`;
    if (locEl) locEl.innerText = cam.location || 'Location Unavailable';

    const hasCoords = (cam.latitude !== null && cam.latitude !== undefined && cam.longitude !== null && cam.longitude !== undefined && !isNaN(cam.latitude) && !isNaN(cam.longitude));
    if (coordsEl) {
      coordsEl.innerText = hasCoords 
        ? `${Number(cam.latitude).toFixed(5)}° N, ${Number(cam.longitude).toFixed(5)}° E` 
        : 'Location Unavailable';
    }

    if (dashMapInstance && hasCoords) {
      if (dashActiveCamMarker) {
        dashMapInstance.removeLayer(dashActiveCamMarker);
      }
      const camIcon = L.divIcon({
        html: '<div style="background:#1877f2;color:#fff;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 16px #00d2ff;border:2.5px solid #fff;font-size:18px;">📹</div>',
        className: '',
        iconSize: [36, 36]
      });
      dashActiveCamMarker = L.marker([cam.latitude, cam.longitude], { icon: camIcon })
        .addTo(dashMapInstance)
        .bindPopup(`<strong>${cam.name} (${cam.camera_id})</strong><br><strong>Location:</strong> ${cam.location}<br><strong>GPS:</strong> ${cam.latitude.toFixed(5)}, ${cam.longitude.toFixed(5)}`)
        .openPopup();
      dashMapInstance.setView([cam.latitude, cam.longitude], 15);

      const targetLabel = document.getElementById('dashMapTargetLabel');
      if (targetLabel) targetLabel.innerText = `Camera: ${cam.name}`;
      fetchNearbyPolice(cam.latitude, cam.longitude);
    }
  } else {
    const sel = document.getElementById('dashCameraSelect');
    if (sel && sel.selectedOptions.length > 0 && title) {
      title.innerText = sel.selectedOptions[0].text;
    }
  }
}

async function loadRecentIncidents() {
  try {
    const res = await fetch('/api/incidents', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const incidents = await res.json();
    allAdminIncidentsCache = incidents;

    const list = document.getElementById('recentIncidentsList');
    if (!list) return;

    if (incidents.length === 0) {
      list.innerHTML = `
        <div style="color: var(--text-muted); font-size: 13px; text-align: center; padding: 40px 20px;">
          <div style="font-size: 36px; margin-bottom: 10px;">🛡️</div>
          <div><strong>Zero Active Risks Detected</strong></div>
          <div style="margin-top: 6px; font-size: 12px;">Every displayed record is strictly verified. Connect a CCTV stream or upload a video recording to detect security incidents.</div>
        </div>
      `;
      return;
    }

    list.innerHTML = '';
    incidents.slice(0, 5).forEach(inc => {
      const isRisk = (inc.risk_category === 'RISK' || inc.risk_score >= 50);
      const riskBadgeHtml = isRisk 
        ? `<span class="badge-risk">RISK</span>` 
        : `<span class="badge-norisk">NO RISK</span>`;

      const card = document.createElement('div');
      card.className = 'cam-item-card';
      card.style.marginBottom = '12px';
      card.style.padding = '12px 14px';
      card.innerHTML = `
        <div style="flex: 1;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <strong style="color: #fff; font-size: 13.5px;">${inc.incident_id}</strong>
            ${riskBadgeHtml}
          </div>
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px;">
            📍 ${inc.location || 'Location Unavailable'} • 🕒 ${inc.time_str} (${inc.date_str})
          </div>
          <div style="font-size: 11.5px; color: #a0aec0; margin-bottom: 8px;">
            Source: <span style="color: #fff;">${inc.camera_name}</span>
          </div>
          <div style="display: flex; justify-content: flex-end; gap: 8px;">
            <button class="btn btn-outline btn-sm" onclick="focusIncidentOnMap(${inc.id})">📍 Focus Map</button>
            <button class="btn btn-primary btn-sm" onclick="openIncidentModal(${inc.id})">👁 View Details</button>
          </div>
        </div>
      `;
      list.appendChild(card);
    });
  } catch (e) {
    console.error('Failed to load recent incidents', e);
  }
}

function focusIncidentOnMap(incId) {
  const inc = allAdminIncidentsCache.find(i => String(i.id) === String(incId));
  if (!inc) return;
  const iLat = inc.latitude || inc.lat;
  const iLon = inc.longitude || inc.lon;
  if (!iLat || !iLon) {
    showToast('Incident has no verified GPS coordinates (Location Unavailable)', 'info');
    return;
  }
  updateDashboardLocation(iLat, iLon, `${inc.incident_id || inc.code} (${inc.location || inc.location_text || 'Incident'})`);
}

// 2. Leaflet Maps & Real Geolocation
function initDashboardMap() {
  const el = document.getElementById('dashMap');
  if (!el || dashMapInstance) return;

  dashMapInstance = L.map('dashMap', { zoomControl: false }).setView([lastKnownLocation.lat, lastKnownLocation.lon], 13);
  L.control.zoom({ position: 'topright' }).addTo(dashMapInstance);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18
  }).addTo(dashMapInstance);

  fetchNearbyPolice(lastKnownLocation.lat, lastKnownLocation.lon);
}

function initFullMap() {
  const el = document.getElementById('fullMap');
  if (!el) return;

  if (fullMapInstance) {
    fullMapInstance.invalidateSize();
    return;
  }

  fullMapInstance = L.map('fullMap').setView([lastKnownLocation.lat, lastKnownLocation.lon], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 18
  }).addTo(fullMapInstance);

  fetchMapMarkers(fullMapInstance);
}

// Browser Geolocation API
function triggerBrowserGeolocation(interactive = true) {
  const pill = document.getElementById('gpsStatusPill');
  if (pill) pill.innerText = 'GPS: Requesting...';

  if (!navigator.geolocation) {
    if (pill) pill.innerText = 'GPS: Not Supported';
    if (interactive) showToast('Geolocation is not supported by your browser.', 'warning');
    return;
  }

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;
      lastKnownLocation = { lat, lon, name: 'Current Location' };

      // Reverse geocode to get locality name
      let areaName = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
      try {
        const revRes = await fetch(`/api/geo/reverse?lat=${lat}&lon=${lon}`);
        if (revRes.ok) {
          const revData = await revRes.json();
          areaName = revData.area || areaName;
        }
      } catch (e) {}

      if (pill) {
        pill.innerText = `GPS Active: ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
        pill.style.color = 'var(--accent-green)';
        pill.style.borderColor = 'rgba(0, 230, 118, 0.4)';
      }

      const searchInput = document.getElementById('dashLocationSearchInput');
      if (searchInput) searchInput.value = areaName;

      updateDashboardLocation(lat, lon, `My Current Location (${lat.toFixed(4)}, ${lon.toFixed(4)})`);
      if (interactive) showToast(`Location detected: ${areaName}`, 'success');
    },
    (err) => {
      console.warn('Geolocation error:', err.message);
      if (pill) {
        pill.innerText = 'GPS: Permission Denied (Use Search)';
        pill.style.color = 'var(--accent-orange)';
        pill.style.borderColor = 'rgba(255, 149, 0, 0.4)';
      }
      if (interactive) {
        showToast('Location permission denied. You can search any college/city above.', 'info');
      }
    },
    { timeout: 8000, enableHighAccuracy: true }
  );
}

// Center map, update marker, and fetch real nearby police stations
let dashPolicePolylines = [];

async function updateDashboardLocation(lat, lon, label) {
  lastKnownLocation = { lat, lon, name: label };

  const targetLabel = document.getElementById('dashMapTargetLabel');
  const activeAreaBadge = document.getElementById('activeAreaBadge');
  if (targetLabel) targetLabel.innerText = label;
  if (activeAreaBadge) activeAreaBadge.innerText = label.split(',')[0].substring(0, 24);

  if (dashMapInstance) {
    dashMapInstance.setView([lat, lon], 14);

    // Update User/Search Location Marker
    if (dashUserMarker) dashMapInstance.removeLayer(dashUserMarker);

    const userIcon = L.divIcon({
      html: '<div style="background:#00e676;border-radius:50%;width:22px;height:22px;border:3px solid #fff;box-shadow:0 0 16px rgba(0,230,118,0.85);"></div>',
      className: '',
      iconSize: [22, 22],
      iconAnchor: [11, 11]
    });

    dashUserMarker = L.marker([lat, lon], { icon: userIcon })
      .addTo(dashMapInstance)
      .bindPopup(`<strong>My Current Location (${lat.toFixed(4)}, ${lon.toFixed(4)})</strong><br>Coordinates: ${lat.toFixed(5)}°, ${lon.toFixed(5)}°`)
      .openPopup();
  }

  // Fetch verified nearby police stations
  await fetchNearbyPolice(lat, lon);
}

// Query real nearby police stations via backend OpenStreetMap service
let nearbyPoliceAbortController = null;

async function fetchNearbyPolice(lat, lon) {
  const listEl = document.getElementById('nearbyPoliceStationsList');
  const countEl = document.getElementById('nearbyStationsCount');
  const nearestBox = document.getElementById('dashMapNearestStation');

  if (nearbyPoliceAbortController) {
    nearbyPoliceAbortController.abort();
  }
  nearbyPoliceAbortController = new AbortController();

  if (countEl) countEl.innerText = 'Searching...';

  if (listEl) {
    listEl.innerHTML = '<div style="color:var(--accent-cyan);font-size:12.5px;text-align:center;padding:12px;">🔍 Finding real nearby police stations...</div>';
  }

  // Clear previous police markers and connecting polylines
  dashPoliceMarkers.forEach(m => {
    if (dashMapInstance) dashMapInstance.removeLayer(m);
  });
  dashPoliceMarkers = [];

  dashPolicePolylines.forEach(pl => {
    if (dashMapInstance) dashMapInstance.removeLayer(pl);
  });
  dashPolicePolylines = [];

  try {
    const res = await fetch(`/api/geo/nearby-police?lat=${lat}&lon=${lon}&radius=25`, { signal: nearbyPoliceAbortController.signal,
      headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error('Failed to fetch stations');
    const data = await res.json();
    const stations = Array.isArray(data) ? data : (data.stations || []);

    if (countEl) countEl.innerText = `${stations.length} stations found`;

    if (stations.length === 0) {
      if (listEl) {
        listEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px;text-align:center;padding:16px;">No verified police stations found within 15 km. Search a city or college above.</div>';
      }
      if (nearestBox) nearestBox.innerText = 'Nearest: None found in 15 km';
      return;
    }

    const nearest = stations[0];
    if (nearestBox) {
      nearestBox.innerText = `Nearest: ${nearest.name} (${nearest.distance_km} km)`;
    }

    if (listEl) {
      listEl.innerHTML = '';
      stations.forEach((st, idx) => {
        const sLat = st.latitude || st.lat;
        const sLon = st.longitude || st.lon;
        const item = document.createElement('div');
        item.className = 'police-station-card';
        item.onclick = () => focusPoliceStation(sLat, sLon, st.name, st.address, st.distance_km, item);
        item.innerHTML = `
          <div class="police-station-info">
            <div class="station-name">
              <span class="station-number">${idx + 1}.</span>
              <span>${st.name}</span>
            </div>
            <div class="station-address">${st.address || 'Address verified on map'}</div>
          </div>
          <div class="police-dist-badge">${st.distance_km} km</div>
        `;
        listEl.appendChild(item);

        if (dashMapInstance && sLat && sLon) {
          const policeIcon = L.divIcon({
            html: '<div style="background:#1877f2;color:#fff;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;box-shadow:0 0 12px rgba(24,119,242,0.8);border:2px solid #fff;font-size:15px;">🛡️</div>',
            className: '',
            iconSize: [32, 32],
            iconAnchor: [16, 16]
          });

          const m = L.marker([sLat, sLon], { icon: policeIcon })
            .addTo(dashMapInstance)
            .bindPopup(`<strong>${st.name}</strong><br>${st.address || ''}<br><strong>Distance:</strong> ${st.distance_km} km<br><strong>Contact:</strong> ${st.phone || '112 / 100'}`);
          dashPoliceMarkers.push(m);

          // Connecting Polyline with Visual Distance Indicator
          const polyline = L.polyline([[lat, lon], [sLat, sLon]], {
            color: '#ff3b30',
            weight: 2,
            dashArray: '5, 8',
            opacity: 0.75
          }).addTo(dashMapInstance);
          dashPolicePolylines.push(polyline);
        }
      });
    }
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    console.error('Nearby police fetch error:', err);
    if (countEl) countEl.innerText = 'Service Offline';
    if (listEl) {
      listEl.innerHTML = '<div style="color:var(--accent-red);font-size:13px;text-align:center;padding:12px;display:flex;align-items:center;justify-content:center;gap:6px;"><span>🛑</span> Failed to reach police geocoding service.</div>';
    }
  }
}

function focusPoliceStation(lat, lon, name, addr, dist, cardElement) {
  document.querySelectorAll('.police-station-card').forEach(c => c.classList.remove('selected'));
  if (cardElement) cardElement.classList.add('selected');
  if (dashMapInstance) {
    dashMapInstance.setView([lat, lon], 15);
    const m = dashPoliceMarkers.find(marker => {
      const ll = marker.getLatLng();
      return Math.abs(ll.lat - lat) < 0.0001 && Math.abs(ll.lng - lon) < 0.0001;
    });
    if (m) m.openPopup();
  }
}

// Location Autocomplete Search on Admin Dashboard
let dashSearchDebounceTimer = null;
function onDashLocationSearch(query) {
  clearTimeout(dashSearchDebounceTimer);
  const dropdown = document.getElementById('dashLocationDropdown');
  if (!dropdown) return;

  if (!query || query.trim().length < 2) {
    dropdown.style.display = 'none';
    return;
  }

  dashSearchDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/geo/search?q=${encodeURIComponent(query.trim())}`);
      if (!res.ok) return;
      const results = await res.json();

      if (results.length === 0) {
        dropdown.innerHTML = '<div style="padding:10px 14px;color:var(--text-muted);font-size:12px;">No matching places found</div>';
        dropdown.style.display = 'block';
        return;
      }

      dropdown.innerHTML = '';
      results.forEach(place => {
        const item = document.createElement('div');
        item.className = 'autocomplete-item';
        item.innerHTML = `
          <div class="autocomplete-title">📍 ${place.name}</div>
          <div class="autocomplete-desc">${place.display_name}</div>
        `;
        item.onclick = () => {
          dropdown.style.display = 'none';
          document.getElementById('dashLocationSearchInput').value = place.name;
          updateDashboardLocation(place.latitude, place.longitude, place.name);
        };
        dropdown.appendChild(item);
      });
      dropdown.style.display = 'block';
    } catch (e) {
      console.error('Search error', e);
    }
  }, 350);
}

// Close autocomplete dropdown on click outside
document.addEventListener('click', (e) => {
  const dashDropdown = document.getElementById('dashLocationDropdown');
  const dashInput = document.getElementById('dashLocationSearchInput');
  if (dashDropdown && e.target !== dashInput && !dashDropdown.contains(e.target)) {
    dashDropdown.style.display = 'none';
  }

  const camDropdown = document.getElementById('camLocationDropdown');
  const camInput = document.getElementById('cfgCamLoc');
  if (camDropdown && e.target !== camInput && !camDropdown.contains(e.target)) {
    camDropdown.style.display = 'none';
  }
});

// Full Map Markers
async function fetchMapMarkers(mapObj) {
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
        .bindPopup(`<strong>${cam.name} (${cam.camera_id})</strong><br>Status: ${cam.status.toUpperCase()}<br>Location: ${cam.location}`);
    });

    data.police_stations.forEach(st => {
      L.marker([st.latitude, st.longitude], { icon: policeIcon })
        .addTo(mapObj)
        .bindPopup(`<strong>${st.name}</strong><br>Code: ${st.code}<br>Contact: ${st.contact}`);
    });

    data.incidents.forEach(inc => {
      L.marker([inc.latitude, inc.longitude], { icon: incIcon })
        .addTo(mapObj)
        .bindPopup(`<strong>Incident: ${inc.incident_id}</strong><br>Risk: ${inc.risk_level}<br>Nearest Police: ${inc.nearest_station} (${inc.distance_km} km)`);
    });
  } catch (e) {
    console.error('Failed to load map markers', e);
  }
}

function detectAdminCurrentLocation() {
  triggerBrowserGeolocation(true);
}

function resetMapView() {
  if (fullMapInstance) {
    fullMapInstance.setView([lastKnownLocation.lat, lastKnownLocation.lon], 13);
  }
}

// 3. Camera Management
let camSearchDebounceTimer = null;
let lastGeocodedCamLocation = '';

function showAddCameraModal() {
  const nameInput = document.getElementById('cfgCamName');
  const idInput = document.getElementById('cfgCamId');
  const urlInput = document.getElementById('cfgCamUrl');
  const locInput = document.getElementById('cfgCamLoc');
  const latInput = document.getElementById('cfgCamLat');
  const lonInput = document.getElementById('cfgCamLon');
  const statusEl = document.getElementById('camGeoStatus');
  const testRes = document.getElementById('camTestResult');

  const count = allCamerasCache ? allCamerasCache.length : 0;
  if (count >= 2) {
    showToast('System limit reached: Maximum 2 CCTV cameras allowed. You can remove an existing camera to add a new one.', 'info');
    return;
  }

  const nextNum = count + 1;
  if (nameInput) nameInput.value = `CCTV Camera ${nextNum}`;
  if (idInput) idInput.value = `CAM-00${nextNum}`;
  if (urlInput) urlInput.value = nextNum === 1 ? 'sample_cctv_feed.mp4' : 'cctv_feed_2.mp4';
  if (locInput) {
    locInput.value = '';
    locInput.focus();
  }
  if (latInput) latInput.value = '';
  if (lonInput) lonInput.value = '';
  if (statusEl) {
    statusEl.style.display = 'none';
    statusEl.innerHTML = '';
  }
  if (testRes) testRes.style.display = 'none';

  showToast('Enter the camera location above (e.g. town, village, or address) to auto-detect GPS.', 'info');
}

function selectCameraLocation(place) {
  const dropdown = document.getElementById('camLocationDropdown');
  const locInput = document.getElementById('cfgCamLoc');
  const latInput = document.getElementById('cfgCamLat');
  const lonInput = document.getElementById('cfgCamLon');
  const statusEl = document.getElementById('camGeoStatus');

  if (dropdown) dropdown.style.display = 'none';
  if (locInput) locInput.value = place.name;
  if (latInput) latInput.value = Number(place.latitude).toFixed(5);
  if (lonInput) lonInput.value = Number(place.longitude).toFixed(5);
  lastGeocodedCamLocation = place.name;

  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.style.color = 'var(--accent-green)';
    statusEl.innerHTML = `✓ <strong>GPS Coordinates Detected:</strong> ${Number(place.latitude).toFixed(5)}° N, ${Number(place.longitude).toFixed(5)}° E <span style="color:var(--text-muted);font-size:11px;">(${escapeHtml(place.display_name)})</span>`;
  }
  showToast(`Auto-detected GPS for ${place.name}: ${Number(place.latitude).toFixed(4)}, ${Number(place.longitude).toFixed(4)}`, 'success');
}

async function autoGeocodeCamLocation(force = false) {
  const locInput = document.getElementById('cfgCamLoc');
  const latInput = document.getElementById('cfgCamLat');
  const lonInput = document.getElementById('cfgCamLon');
  const statusEl = document.getElementById('camGeoStatus');

  if (!locInput) return false;
  const query = (locInput.value || '').trim();
  if (query.length < 2) return false;

  if (!force && lastGeocodedCamLocation === query && latInput.value && lonInput.value) {
    return true;
  }

  // Brief pause so dropdown click event can fire if user clicked an option
  await new Promise(r => setTimeout(r, 180));

  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.style.color = 'var(--accent-cyan)';
    statusEl.innerHTML = '<span>🔍</span> Auto-detecting real-world coordinates...';
  }

  try {
    const res = await fetch(`/api/geo/search?q=${encodeURIComponent(query)}&limit=1`, {
      headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error('Geocoding request failed');
    const results = await res.json();

    if (results.length > 0) {
      const place = results[0];
      if (latInput) latInput.value = Number(place.latitude).toFixed(5);
      if (lonInput) lonInput.value = Number(place.longitude).toFixed(5);
      lastGeocodedCamLocation = query;

      if (statusEl) {
        statusEl.style.display = 'block';
        statusEl.style.color = 'var(--accent-green)';
        statusEl.innerHTML = `✓ <strong>GPS Auto-detected:</strong> ${Number(place.latitude).toFixed(5)}° N, ${Number(place.longitude).toFixed(5)}° E <span style="color:var(--text-muted);font-size:11px;">(${escapeHtml(place.display_name)})</span>`;
      }
      return true;
    } else {
      if (statusEl) {
        statusEl.style.display = 'block';
        statusEl.style.color = 'var(--accent-orange)';
        statusEl.innerHTML = `⚠️ No GPS coordinates found for "${escapeHtml(query)}". Please refine the address or enter coordinates manually.`;
      }
      return false;
    }
  } catch (err) {
    console.error('Auto-geocode error', err);
    return false;
  }
}

function handleCamLocKeyDown(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    const dropdown = document.getElementById('camLocationDropdown');
    const firstItem = dropdown ? dropdown.querySelector('.autocomplete-item') : null;
    if (firstItem && dropdown.style.display === 'block') {
      firstItem.click();
    } else {
      autoGeocodeCamLocation(true);
    }
  }
}
function onCamLocationSearch(query) {
  clearTimeout(camSearchDebounceTimer);
  const dropdown = document.getElementById('camLocationDropdown');
  if (!dropdown) return;

  if (!query || query.trim().length < 2) {
    dropdown.style.display = 'none';
    return;
  }

  camSearchDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch(`/api/geo/search?q=${encodeURIComponent(query.trim())}`);
      if (!res.ok) return;
      const results = await res.json();

      if (results.length === 0) {
        dropdown.innerHTML = '<div style="padding:10px 14px;color:var(--text-muted);font-size:12px;">No matching locations found</div>';
        dropdown.style.display = 'block';
        return;
      }

      dropdown.innerHTML = '';
      results.forEach(place => {
        const item = document.createElement('div');
        item.className = 'autocomplete-item';
        item.innerHTML = `
          <div class="autocomplete-title">📍 ${place.name}</div>
          <div class="autocomplete-desc">${place.display_name}</div>
        `;
        item.onclick = () => {
          dropdown.style.display = 'none';
          document.getElementById('cfgCamLoc').value = place.name;
          document.getElementById('cfgCamLat').value = place.latitude.toFixed(5);
          document.getElementById('cfgCamLon').value = place.longitude.toFixed(5);
          selectCameraLocation(place);
        };
        dropdown.appendChild(item);
      });
      dropdown.style.display = 'block';
    } catch (e) {
      console.error('Camera location autocomplete error', e);
    }
  }, 350);
}

async function loadConnectedCameras() {
  try {
    const res = await fetch('/api/cameras', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const cameras = await res.json();
    allCamerasCache = cameras;
    const container = document.getElementById('connectedCamerasList');

    const dashSelect = document.getElementById('dashCameraSelect');
    if (dashSelect) {
      dashSelect.innerHTML = '';
      if (cameras.length === 0) {
        dashSelect.innerHTML = '<option value="">No Camera Connected</option>';
        onDashCameraChange('');
      } else {
        cameras.forEach(cam => {
          const opt = document.createElement('option');
          opt.value = cam.id;
          opt.innerText = `${cam.camera_id} (${cam.name})`;
          dashSelect.appendChild(opt);
        });
        onDashCameraChange(cameras[0].id);
      }
    }

    if (!container) return;

    if (cameras.length === 0) {
      container.innerHTML = `
        <div style="color: var(--text-muted); font-size: 13.5px; text-align: center; padding: 40px 20px;">
          <div style="font-size: 36px; margin-bottom: 10px;">📹</div>
          <div><strong>0 Cameras Connected</strong></div>
          <div style="font-size: 12px; margin-top: 4px;">System supports up to 2 cameras per user. Add a camera using the form.</div>
        </div>
      `;
      return;
    }

    container.innerHTML = '';
    cameras.forEach(cam => {
      const isOnline = cam.status === 'online';
      const item = document.createElement('div');
      item.className = 'cam-item-card';
      item.style.padding = '14px 16px';
      item.innerHTML = `
        <div class="cam-thumb" style="width: 110px; height: 75px;">
          <img src="/api/cameras/${cam.id}/feed" onerror="this.src='/static/images/marker-icon.png'" alt="${cam.name}">
        </div>
        <div style="flex: 1;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
            <strong style="color: #fff; font-size: 14px;">${cam.name} (${cam.camera_id})</strong>
            <span class="badge ${isOnline ? 'badge-online' : 'badge-offline'}">● ${cam.status.toUpperCase()}</span>
          </div>
          <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px;">📍 ${cam.location}</div>
          <div style="font-size: 11.5px; color: var(--accent-cyan); margin-bottom: 8px;">GPS: ${cam.latitude.toFixed(4)}, ${cam.longitude.toFixed(4)}</div>
          <div style="display: flex; gap: 8px; flex-wrap: wrap;">
            <button class="btn btn-primary btn-sm" onclick="viewCameraFeed(${cam.id}, '${cam.name}')">👁 Monitor</button>
            ${isOnline 
              ? `<button class="btn btn-danger btn-sm" onclick="stopCameraStream(${cam.id})">Stop</button>`
              : `<button class="btn btn-success btn-sm" onclick="startCameraStream(${cam.id})">Start</button>`
            }
            <button class="btn btn-outline btn-sm" style="color:#ff4d4f; border-color: rgba(255,77,79,0.4);" onclick="deleteCamera(${cam.id}, '${cam.name}')">🗑 Remove</button>
          </div>
        </div>
      `;
      container.appendChild(item);
    });
  } catch (e) {
    console.error('Failed to load cameras', e);
  }
}

async function handleSaveCamera(e) {
  e.preventDefault();
  const name = document.getElementById('cfgCamName').value.trim();
  const camId = document.getElementById('cfgCamId').value.trim();
  const rtspUrl = document.getElementById('cfgCamUrl').value.trim();
  const location = document.getElementById('cfgCamLoc').value.trim();
  const lat = parseFloat(document.getElementById('cfgCamLat').value);
  const lon = parseFloat(document.getElementById('cfgCamLon').value);

  let finalLat = lat;
  let finalLon = lon;
  if (isNaN(finalLat) || isNaN(finalLon)) {
    showToast('Resolving GPS coordinates for location...', 'info');
    const resolved = await autoGeocodeCamLocation(true);
    if (resolved) {
      finalLat = parseFloat(document.getElementById('cfgCamLat').value);
      finalLon = parseFloat(document.getElementById('cfgCamLon').value);
    }
  }

  if (isNaN(finalLat) || isNaN(finalLon)) {
    showToast(`Could not detect GPS coordinates for '${location}'. Please select from suggestions or enter coordinates.`, 'warning');
    const statusEl = document.getElementById('camGeoStatus');
    if (statusEl) {
      statusEl.style.display = 'block';
      statusEl.style.color = 'var(--accent-red)';
      statusEl.innerHTML = `🛑 Coordinates required. Please select a matching place from suggestions.`;
    }
    return;
  }

  try {
    const res = await fetch('/api/cameras', {
      method: 'POST',
      headers: getAuthHeaders(),

      body: JSON.stringify({
        camera_id: camId,
        name: name,
        rtsp_url: rtspUrl,
        location: location,
        latitude: finalLat,
        longitude: finalLon
      })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || 'Failed to save camera', 'danger');
      return;
    }

    showToast(`Camera '${name}' connected successfully with GPS: ${finalLat.toFixed(4)}, ${finalLon.toFixed(4)}!`, 'success');
    showAddCameraModal();
    loadConnectedCameras();
    loadAdminStats();
  } catch (err) {
    showToast('Network error while saving camera', 'danger');
  }
}

async function deleteCamera(camId, camName) {
  if (!confirm(`Are you sure you want to remove camera '${camName}'?`)) return;

  try {
    const res = await fetch(`/api/cameras/${camId}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || 'Camera removed successfully', 'success');
      loadConnectedCameras();
      loadAdminStats();
    } else {
      showToast(data.detail || 'Failed to delete camera', 'danger');
    }
  } catch (e) {
    showToast('Error removing camera', 'danger');
  }
}

async function testCameraConnection() {
  const resultEl = document.getElementById('camTestResult');
  const rtspUrl = (document.getElementById('cfgCamUrl')?.value || '').trim();
  resultEl.style.display = 'block';
  resultEl.innerText = 'Testing RTSP stream connection...';
  resultEl.style.color = 'var(--accent-cyan)';

  if (!rtspUrl) {
    resultEl.innerText = '✕ Please provide an RTSP stream URL or test filename';
    resultEl.style.color = 'var(--accent-red)';
    return;
  }

  try {
    const res = await fetch('/api/cameras/test', {
      method: 'POST',
      headers: getAuthHeaders(),

      body: JSON.stringify({ rtsp_url: rtspUrl })
    });
    const data = await res.json();
    if (data.ok) {
      resultEl.innerText = `✓ Connection Successful: Resolution ${data.width || 1920}x${data.height || 1080} @ ${Math.round(data.fps || 25)} FPS`;
      resultEl.style.color = 'var(--accent-green)';
    } else {
      resultEl.innerText = `✕ Connection Failed: ${data.err || data.detail || 'Could not open stream'}`;
      resultEl.style.color = 'var(--accent-red)';
    }
  } catch (e) {
    resultEl.innerText = `✕ Error testing connection: ${e.message || e}`;
    resultEl.style.color = 'var(--accent-red)';
  }
}

async function startCameraStream(camId) {
  try {
    const res = await fetch(`/api/cameras/${camId}/start`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      showToast('Camera stream worker started', 'success');
      loadConnectedCameras();
      loadAdminStats();
    }
  } catch (e) {
    showToast('Failed to start camera', 'danger');
  }
}

async function stopCameraStream(camId) {
  try {
    const res = await fetch(`/api/cameras/${camId}/stop`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    if (res.ok) {
      showToast('Camera stream worker stopped', 'info');
      loadConnectedCameras();
      loadAdminStats();
    }
  } catch (e) {
    showToast('Failed to stop camera', 'danger');
  }
}

function viewCameraFeed(camId, camName) {
  switchPage('dashboard');
  const sel = document.getElementById('dashCameraSelect');
  if (sel) sel.value = String(camId);
  onDashCameraChange(String(camId));
}

// 4. Immediate Video Upload & AI Analysis Flow
let currentUploadingJobId = null;

function handleImmediateVideoUpload(input) {
  if (!input.files || !input.files[0]) return;
  const file = input.files[0];

  const infoEl = document.getElementById('selectedFileInfo');
  const nameEl = document.getElementById('selectedFileName');
  const sizeEl = document.getElementById('selectedFileSize');

  if (nameEl) nameEl.innerText = file.name;
  if (sizeEl) sizeEl.innerText = `${(file.size / (1024 * 1024)).toFixed(2)} MB • ${file.type || 'Video'}`;
  if (infoEl) infoEl.style.display = 'block';

  // Automatically start upload and analysis immediately!
  executeVideoUploadAndAnalysis(file);
}

async function executeVideoUploadAndAnalysis(file) {
  const progressLabel = document.getElementById('uploadProgressLabel');
  const progressPercent = document.getElementById('uploadProgressPercent');
  const progressBar = document.getElementById('uploadProgressBar');
  const statusText = document.getElementById('analysisStatusText');

  progressLabel.innerText = 'Uploading video file...';
  progressBar.style.width = '15%';
  progressPercent.innerText = '15%';
  statusText.innerText = `Uploading ${file.name} to server...`;
  statusText.style.color = 'var(--accent-cyan)';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const token = getAuthToken();
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: formData
    });
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      statusText.innerText = `Upload failed: ${err.detail || 'Server error'}`;
      statusText.style.color = 'var(--accent-red)';
      progressBar.style.width = '0%';
      progressPercent.innerText = '0%';
      return;
    }
    
    const job = await res.json();
    const jobId = job.id;
    currentUploadingJobId = jobId;

    progressLabel.innerText = 'Starting AI Video Analysis...';
    progressBar.style.width = '30%';
    progressPercent.innerText = '30%';
    statusText.innerText = 'Executing YOLOv8 + ByteTrack + Context Engine...';

    // Start analysis with genuine location if known
    const startPayload = {};
    if (lastKnownLocation && lastKnownLocation.lat && lastKnownLocation.lon) {
      startPayload.lat = lastKnownLocation.lat;
      startPayload.lon = lastKnownLocation.lon;
    }

    const startRes = await fetch(`/api/jobs/${jobId}/start`, {
      method: 'POST',
      headers: getAuthHeaders(),

      body: JSON.stringify(startPayload)
    });

    if (!startRes.ok) {
      const startErr = await startRes.json().catch(() => ({}));
      statusText.innerText = `Could not start analysis: ${startErr.detail || 'Error'}`;
      statusText.style.color = 'var(--accent-red)';
      return;
    }

    // Poll status smoothly
    const pollInterval = setInterval(async () => {
      try {
        const sRes = await fetch(`/api/jobs/${jobId}`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!sRes.ok) return;
        const sData = await sRes.json();

        const pct = Math.max(30, Math.min(98, Math.round(sData.progress || 30)));
        progressBar.style.width = `${pct}%`;
        progressPercent.innerText = `${pct}%`;

        if (sData.status === 'done') {
          clearInterval(pollInterval);
          progressBar.style.width = '100%';
          progressPercent.innerText = '100%';
          progressLabel.innerText = 'Analysis Complete';
          statusText.innerText = 'AI Analysis completed successfully!';
          statusText.style.color = 'var(--accent-green)';

          // Update summary card
          const summaryCard = document.getElementById('analysisSummaryCard');
          const summaryDetails = document.getElementById('analysisSummaryDetails');
          if (summaryCard && summaryDetails) {
            summaryCard.style.display = 'block';
            summaryDetails.innerText = `Result: ${sData.analysis_result || 'Completed'} | Category: ${sData.category || 'Routine Activity'} | Progress: 100%`;
          }

          loadAdminStats();
          loadRecentIncidents();

          // Immediately pop up the Result Modal!
          openAnalysisResultModal(sData);

        } else if (sData.status === 'failed') {
          clearInterval(pollInterval);
          progressBar.style.width = '100%';
          progressLabel.innerText = 'Processing Failed';
          statusText.innerText = `Analysis failed: ${sData.error || 'Processing error'}`;
          statusText.style.color = 'var(--accent-red)';

          loadAdminStats();
          openAnalysisResultModal(sData);
        }
      } catch (err) {
        // network hiccup, continue polling
      }
    }, 800);

  } catch (e) {
    statusText.innerText = `Network error: ${e.message || e}`;
    statusText.style.color = 'var(--accent-red)';
  }
}

// Immediate Video Analysis Result Popup Modal
let lastAnalysisResultCache = null;

function openAnalysisResultModal(job) {
  lastAnalysisResultCache = job;
  const modal = document.getElementById('analysisResultModal');
  const body = document.getElementById('analysisResultBody');
  if (!modal || !body) return;

  let resData = job.result || job.results || {};
  if (typeof resData === 'string') {
    try { resData = JSON.parse(resData); } catch(e) { resData = {}; }
  }

  const isRisk = job.analysis_result === 'Risk Detected' || resData.risk_detected === true || resData.classification_outcome === 'RISK_DETECTED';
  const isFailed = job.status === 'failed' || job.analysis_result === 'Processing Failed';
  const isUncertain = job.analysis_result === 'Uncertain' || resData.classification_outcome === 'UNCERTAIN';

  let bannerClass = 'norisk';
  let bannerIcon = '✅';
  let bannerTitle = 'NO RISK DETECTED';
  let bannerSub = 'Normal activity confirmed within safe operating thresholds. No incident generated.';
  
  if (isFailed) {
    bannerClass = 'failed';
    bannerIcon = '✕';
    bannerTitle = 'PROCESSING FAILED';
    bannerSub = job.error || 'Video analysis encountered an unrecoverable decoding error.';
  } else if (isRisk) {
    bannerClass = 'risk';
    bannerIcon = '🚨';
    bannerTitle = 'RISK DETECTED';
    bannerSub = 'Security risk identified! Verified evidence generated and real-time alert dispatched to Police Dashboard.';
  } else if (isUncertain) {
    bannerClass = 'uncertain';
    bannerIcon = '⚠️';
    bannerTitle = 'UNCERTAIN – MANUAL REVIEW REQUIRED';
    bannerSub = 'Visual indicators require manual verification. Saved for review without triggering false emergency dispatch.';
  }

  const category = job.category || resData.category || (isRisk ? 'Security Anomaly' : 'Normal / Routine Activity');
  let scoreNum = 12;
  if (resData.risk_score !== undefined) {
    scoreNum = Math.round(resData.risk_score <= 1.0 ? resData.risk_score * 100 : resData.risk_score);
  } else if (job.risk_score !== undefined) {
    scoreNum = Math.round(job.risk_score <= 1.0 ? job.risk_score * 100 : job.risk_score);
  } else if (isRisk) {
    scoreNum = 88;
  }

  let evidencePreviewHtml = '';
  const incidentId = job.incident_id || resData.incident_created || resData.incident_id;
  if (isRisk && incidentId) {
    evidencePreviewHtml = `
      <div style="margin-top: 14px; padding: 14px; background: rgba(9, 18, 38, 0.95); border-radius: 8px; border: 1px solid var(--border-color);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <strong style="color: #fff; font-size: 13.5px;">Forensic Evidence Package Generated:</strong>
          <span class="badge badge-pending">Status: Dispatched to Police</span>
        </div>
        <div style="font-size: 12.5px; color: var(--text-muted); line-height: 1.8;">
          <strong>Incident ID:</strong> <span style="color: var(--accent-cyan); font-weight: 700;">${incidentId}</span><br>
          <strong>Severity:</strong> <span style="color: var(--accent-red); font-weight: 700;">${resData.risk_severity || 'HIGH RISK'} (${scoreNum}%)</span><br>
          <strong>Category:</strong> <span style="color: #fff;">${category}</span>
        </div>
      </div>
    `;
  }

  body.innerHTML = `
    <div class="result-header-banner ${bannerClass}">
      <div class="result-banner-icon">${bannerIcon}</div>
      <div>
        <div class="result-banner-title">${bannerTitle}</div>
        <div class="result-banner-sub">${bannerSub}</div>
      </div>
    </div>

    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px; font-size: 13.5px; line-height: 1.8; margin-bottom: 14px; background: rgba(14, 25, 52, 0.7); padding: 14px 18px; border-radius: 8px; border: 1px solid var(--border-color);">
      <div>
        <span style="color: var(--text-muted);">Video Filename:</span><br>
        <strong style="color: #fff;">${job.filename || 'Uploaded Video'}</strong>
      </div>
      <div>
        <span style="color: var(--text-muted);">Analysis Status:</span><br>
        <strong style="color: var(--accent-cyan); text-transform: uppercase;">${job.status} (100%)</strong>
      </div>
      <div>
        <span style="color: var(--text-muted);">Detected Risk Category:</span><br>
        <strong style="color: #fff;">${category}</strong>
      </div>
      <div>
        <span style="color: var(--text-muted);">Risk Confidence Score:</span><br>
        <strong style="color: #fff;">${scoreNum}%</strong>
      </div>
    </div>

    <div style="font-size: 13px; background: rgba(9, 18, 38, 0.8); padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border-color); color: #e2e8f0; margin-bottom: 12px;">
      <strong>AI Diagnostic Explanation:</strong><br>
      ${resData.summary || resData.explanation || (isRisk ? 'AI detection pipeline identified risk indicators above threshold.' : 'Full video analyzed. No suspicious or risky activity observed.')}
    </div>

    ${evidencePreviewHtml}
  `;

  modal.style.display = 'flex';
}

function closeAnalysisResultModal() {
  const modal = document.getElementById('analysisResultModal');
  if (modal) modal.style.display = 'none';
}

function reopenLastAnalysisModal() {
  if (lastAnalysisResultCache) {
    openAnalysisResultModal(lastAnalysisResultCache);
  } else {
    showToast('No recent video analysis result to display', 'info');
  }
}

// Incident Deletion Workflow
let pendingDeleteIncidentId = null;

function promptDeleteIncident(id, code, status) {
  pendingDeleteIncidentId = id;
  const modal = document.getElementById('deleteConfirmModal');
  const text = document.getElementById('deleteConfirmText');
  const warn = document.getElementById('deleteWarningBox');

  if (text) {
    text.innerHTML = `Are you sure you want to permanently delete incident <strong>${code}</strong> (Status: <span class="badge badge-pending">${status}</span>)?`;
  }
  if (warn) {
    if (status === 'Working' || status === 'Responding') {
      warn.innerHTML = `⚠️ <strong>Warning:</strong> An active police response is marked as in progress for this incident. Deletion requires administrative override.`;
      warn.style.color = 'var(--accent-red)';
    } else {
      warn.innerHTML = `Notice: Deletion is recorded in the system audit logs. All associated forensic evidence links and notifications will be decoupled cleanly.`;
      warn.style.color = 'var(--accent-orange)';
    }
  }
  if (modal) modal.style.display = 'flex';
}

function closeDeleteConfirmModal() {
  pendingDeleteIncidentId = null;
  const modal = document.getElementById('deleteConfirmModal');
  if (modal) modal.style.display = 'none';
}

async function confirmDeleteIncident() {
  if (!pendingDeleteIncidentId) return;
  const id = pendingDeleteIncidentId;
  const btn = document.getElementById('btnConfirmDelete');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`/api/incidents/${id}`, {
      method: 'DELETE',
      headers: getAuthHeaders()
    });
    const data = await res.json().catch(() => ({}));

    if (res.ok) {
      showToast(data.message || `Incident #${id} deleted successfully.`, 'success');
      closeDeleteConfirmModal();
      loadIncidentsTable();
      loadAdminStats();
    } else {
      // If error is due to active police investigation, give option to force
      if (res.status === 400 && data.detail && data.detail.includes('police response')) {
        const forceConfirm = confirm(`${data.detail}\n\nDo you want to FORCE deletion as Central Admin?`);
        if (forceConfirm) {
          const forceRes = await fetch(`/api/incidents/${id}?force=true`, {
            method: 'DELETE',
            headers: getAuthHeaders()
          });
          if (forceRes.ok) {
            showToast(`Incident #${id} force-deleted successfully.`, 'success');
            closeDeleteConfirmModal();
            loadIncidentsTable();
            loadAdminStats();
            return;
          }
        }
      }
      showToast(data.detail || 'Could not delete incident.', 'danger');
      closeDeleteConfirmModal();
    }
  } catch (e) {
    showToast('Failed to delete incident: network error', 'danger');
    closeDeleteConfirmModal();
  } finally {
    if (btn) btn.disabled = false;
  }
}
async function loadIncidentsTable() {
  const statusFilter = document.getElementById('filterStatus').value;
  const riskFilter = document.getElementById('filterRisk').value;

  let url = '/api/incidents?';
  if (statusFilter && statusFilter !== 'All Status') url += `status=${encodeURIComponent(statusFilter)}&`;
  if (riskFilter && riskFilter !== 'All Risk Levels') url += `risk_level=${encodeURIComponent(riskFilter)}&`;

  try {
    const res = await fetch(url, { headers: getAuthHeaders() });
    if (!res.ok) return;
    const incidents = await res.json();
    const tbody = document.getElementById('incidentsTableBody');
    if (!tbody) return;

    if (incidents.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 30px;">No Verified Incidents Found</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    incidents.forEach(inc => {
      // Risk Category must strictly be RISK or NO RISK
      const isRisk = (inc.risk_category === 'RISK' || inc.risk_score >= 50);
      const riskBadgeHtml = isRisk 
        ? `<span class="badge-risk">RISK</span>` 
        : `<span class="badge-norisk">NO RISK</span>`;

      let statusBadge = 'badge-pending';
      if (inc.status === 'Working' || inc.status === 'Under Review' || inc.status === 'Responding') statusBadge = 'badge-review';
      else if (inc.status === 'Solved' || inc.status === 'Resolved') statusBadge = 'badge-resolved';

      const locStr = inc.location || 'Location Unavailable';

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-weight: 700; color: #fff;">${inc.incident_id}</td>
        <td>${inc.time_str} (${inc.date_str})</td>
        <td>${inc.camera_name}</td>
        <td>${locStr}</td>
        <td>${riskBadgeHtml}</td>
        <td><span class="badge ${statusBadge}">${inc.status}</span></td>
        <td>${inc.station_name} (${inc.distance_km || 1.8} km)</td>
        <td>
          <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
            <button class="btn btn-primary btn-sm" onclick="openIncidentModal(${inc.id})">👁 View</button>
            ${!inc.confirmed_by_admin 
              ? `<button class="btn btn-success btn-sm" onclick="quickConfirmIncident(${inc.id})">Confirm</button>`
              : `<button class="btn btn-outline btn-sm" onclick="openIncidentModal(${inc.id})">Update</button>`
            }
            <button class="btn btn-danger btn-sm" onclick="promptDeleteIncident(${inc.id}, '${inc.incident_id}', '${inc.status}')">🗑 Delete</button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load incidents table', e);
  }
}

async function openIncidentModal(incId) {
  try {
    const res = await fetch(`/api/incidents/${incId}`, { headers: getAuthHeaders() });
    if (!res.ok) return;
    const inc = await res.json();
    currentIncidentInModal = inc;

    document.getElementById('incModalTitle').innerText = `Incident Review: ${inc.incident_id}`;
    
    let indicatorsHtml = '';
    try {
      const inds = JSON.parse(inc.indicators || '[]');
      indicatorsHtml = inds.map(i => `<span class="badge badge-pending" style="margin-right: 4px;">${i}</span>`).join('');
    } catch(e){}

    const isRisk = (inc.risk_category === 'RISK' || inc.risk_score >= 50);
    const riskBadgeHtml = isRisk 
      ? `<span class="badge-risk">RISK</span>` 
      : `<span class="badge-norisk">NO RISK</span>`;

    const body = document.getElementById('incModalBody');
    body.innerHTML = `
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; background: rgba(14, 25, 52, 0.7); padding: 12px 16px; border-radius: 8px;">
        <div>
          <p style="font-size: 12px; color: var(--text-muted);">Source / Camera:</p>
          <p style="font-weight: 600; color: #fff;">${inc.camera_name}</p>
        </div>
        <div>
          <p style="font-size: 12px; color: var(--text-muted);">Detection Time:</p>
          <p style="font-weight: 600; color: #fff;">${inc.time_str}, ${inc.date_str}</p>
        </div>
        <div>
          <p style="font-size: 12px; color: var(--text-muted);">Location:</p>
          <p style="font-weight: 600; color: #fff;">${inc.location || 'Location Unavailable'}</p>
        </div>
        <div>
          <p style="font-size: 12px; color: var(--text-muted);">Risk Category:</p>
          <p style="margin-top: 2px;">${riskBadgeHtml}</p>
        </div>
        <div>
          <p style="font-size: 12px; color: var(--text-muted);">Nearest Configured Station:</p>
          <p style="font-weight: 600; color: var(--accent-cyan);">${inc.station_name} (${inc.distance_km} km)</p>
        </div>
        <div>
          <p style="font-size: 12px; color: var(--text-muted);">Current Status:</p>
          <p style="font-weight: 600; color: #fff;">${inc.status}</p>
        </div>
      </div>

      <div style="margin-bottom: 12px;">
        <p style="font-size: 12px; color: var(--text-muted);">Context-Aware Indicators & Duration:</p>
        <div style="margin-top: 4px;">${indicatorsHtml} Duration: <strong>${inc.activity_duration}s</strong></div>
      </div>

      <div style="margin-bottom: 14px;">
        <p style="font-size: 12px; color: var(--text-muted);">AI Risk Summary:</p>
        <p style="font-size: 13px; color: #e2e8f0; background: rgba(9, 18, 38, 0.7); padding: 10px; border-radius: 6px;">${inc.summary || 'Context-aware temporal observation validated.'}</p>
      </div>

      ${inc.evidence ? `
        <div style="margin-bottom: 14px;">
          <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 6px;">Captured Snapshot Proof:</p>
          <img src="${inc.evidence.snapshot_url}" style="width: 100%; border-radius: 8px; max-height: 220px; object-fit: contain; background: #000;" alt="Snapshot">
        </div>
      ` : ''}

      <div class="form-group">
        <label class="form-label">Operator Remarks</label>
        <textarea id="modalOperatorRemarks" class="form-control" rows="2" placeholder="Add optional operator review notes...">${inc.remarks || ''}</textarea>
      </div>
    `;

    const btnConfirm = document.getElementById('btnConfirmTransfer');
    if (inc.confirmed_by_admin) {
      btnConfirm.innerText = 'Update Remarks';
      btnConfirm.onclick = () => updateIncidentStatusFromModal(inc.id);
    } else {
      btnConfirm.innerText = 'Confirm & Route to Police';
      btnConfirm.onclick = () => confirmIncidentTransfer(inc.id);
    }

    document.getElementById('incidentModal').classList.add('active');
  } catch (e) {
    console.error('Failed to open modal', e);
  }
}

function closeIncidentModal() {
  document.getElementById('incidentModal').classList.remove('active');
  currentIncidentInModal = null;
}

async function quickConfirmIncident(incId) {
  try {
    const res = await fetch(`/api/incidents/${incId}/confirm`, {
      method: 'POST',
      headers: getAuthHeaders(),

      body: JSON.stringify({ remarks: 'Admin verified and routed automatically.' })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Incident confirmed! Transferred to ${data.assigned_station}`, 'success');
      loadIncidentsTable();
      loadAdminStats();
    }
  } catch (e) {
    showToast('Failed to confirm incident', 'danger');
  }
}

async function confirmIncidentTransfer(incId) {
  const targetId = incId || (currentIncidentInModal ? currentIncidentInModal.id : null);
  if (!targetId) return;

  const remarks = document.getElementById('modalOperatorRemarks') ? document.getElementById('modalOperatorRemarks').value : '';

  try {
    const res = await fetch(`/api/incidents/${targetId}/confirm`, {
      method: 'POST',
      headers: getAuthHeaders(),

      body: JSON.stringify({ remarks })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`Incident confirmed and routed to ${data.assigned_station}`, 'success');
      closeIncidentModal();
      loadIncidentsTable();
      loadAdminStats();
    }
  } catch (e) {
    showToast('Error transferring incident', 'danger');
  }
}

async function updateIncidentStatusFromModal(incId) {
  const remarks = document.getElementById('modalOperatorRemarks') ? document.getElementById('modalOperatorRemarks').value : '';
  try {
    const res = await fetch(`/api/incidents/${incId}/status`, {
      method: 'POST',
      headers: getAuthHeaders(),

      body: JSON.stringify({ status: 'Under Review', remarks })
    });
    if (res.ok) {
      showToast('Status updated', 'info');
      closeIncidentModal();
      loadIncidentsTable();
    }
  } catch (e) {
    showToast('Update failed', 'danger');
  }
}

// 6. Evidence Management
async function loadLatestEvidence() {
  try {
    const res = await fetch('/api/incidents', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const incidents = await res.json();
    const highRisk = incidents.find(i => i.evidence) || incidents[0];
    if (!highRisk || !highRisk.evidence) return;

    currentEvidenceData = highRisk;
    document.getElementById('evDetailId').innerText = highRisk.incident_id;
    document.getElementById('evDetailTime').innerText = `${highRisk.time_str}, ${highRisk.date_str}`;
    document.getElementById('evDetailCamera').innerText = highRisk.camera_name;
    document.getElementById('evDetailLocation').innerText = highRisk.location || 'Location Unavailable';
    document.getElementById('evDetailRisk').innerText = highRisk.risk_category || 'RISK';
    document.getElementById('evDetailHash').innerText = formatHash(highRisk.evidence.snapshot_hash);

    const videoPlayer = document.getElementById('evidenceVideoPlayer');
    const snapViewer = document.getElementById('evidenceSnapshotViewer');
    if (videoPlayer) videoPlayer.src = highRisk.evidence.video_clip_url;
    if (snapViewer) snapViewer.src = highRisk.evidence.snapshot_url;

    const verifiedBadge = document.getElementById('evVerifiedBadge');
    if (verifiedBadge) {
      verifiedBadge.style.display = highRisk.evidence.is_verified ? 'inline-block' : 'none';
    }
  } catch (e) {
    console.error('Failed to load evidence', e);
  }
}

function toggleEvidenceMedia(mode) {
  activeMediaMode = mode;
  const vid = document.getElementById('evidenceVideoPlayer');
  const img = document.getElementById('evidenceSnapshotViewer');
  const btnClip = document.getElementById('btnEvClip');
  const btnSnap = document.getElementById('btnEvSnap');

  if (mode === 'clip') {
    if (vid) vid.style.display = 'block';
    if (img) img.style.display = 'none';
    if (btnClip) btnClip.classList.add('active');
    if (btnSnap) btnSnap.classList.remove('active');
  } else {
    if (vid) vid.style.display = 'none';
    if (img) img.style.display = 'block';
    if (btnSnap) btnSnap.classList.add('active');
    if (btnClip) btnClip.classList.remove('active');
  }
}

async function verifyEvidenceSHA256() {
  if (!currentEvidenceData) return;
  const box = document.getElementById('hashVerificationBox');
  if (!box) return;

  box.style.display = 'block';
  box.innerHTML = 'Recalculating 256-bit SHA checksum byte-by-byte on disk...';

  try {
    const res = await fetch(`/api/evidence/${currentEvidenceData.id}/verify?file_type=${activeMediaMode === 'clip' ? 'clip' : 'snapshot'}`, {
      method: 'POST',
      headers: getAuthHeaders()
    });
    const result = await res.json();

    if (result.is_match) {
      box.innerHTML = `
        <div style="color: var(--accent-green); font-weight: 700; margin-bottom: 4px;">✓ ${result.status}</div>
        <div><strong>Target:</strong> ${result.filename}</div>
        <div><strong>Stored Hash:</strong> <code style="color: var(--accent-cyan);">${result.recorded_hash}</code></div>
        <div><strong>Recalculated:</strong> <code style="color: var(--accent-green);">${result.recalculated_hash}</code></div>
        <div style="margin-top: 4px; color: var(--text-muted);">Verified at: ${result.verification_time}</div>
      `;
      const verifiedBadge = document.getElementById('evVerifiedBadge');
      if (verifiedBadge) verifiedBadge.style.display = 'inline-block';
      showToast('SHA-256 Bitwise Match Verified!', 'success');
    } else {
      box.innerHTML = `
        <div style="color: var(--accent-red); font-weight: 700;">✕ ${result.status}</div>
        <div>Stored: <code>${result.recorded_hash}</code></div>
        <div>Recalculated: <code>${result.recalculated_hash}</code></div>
      `;
      showToast('Integrity Hash Mismatch!', 'danger');
    }
  } catch (e) {
    box.innerHTML = `<span style="color: var(--accent-red);">Verification call failed: ${e}</span>`;
  }
}

function downloadEvidenceFile() {
  if (!currentEvidenceData) return;
  window.open(`/api/evidence/${currentEvidenceData.id}/download?file_type=${activeMediaMode === 'clip' ? 'clip' : 'snapshot'}`, '_blank');
}

// 7. Settings
async function handleSaveSettings(e) {
  e.preventDefault();
  const sysName = document.getElementById('setSystemName').value;
  const thresh = document.getElementById('setRiskThreshold').value;
  const dur = document.getElementById('setMinDuration').value;
  const cool = document.getElementById('setCooldown').value;

  try {
    const res = await fetch('/api/settings', {
      method: 'PUT',
      headers: getAuthHeaders(),

      body: JSON.stringify({
        system_name: sysName,
        risk_threshold: thresh,
        min_duration: dur,
        alert_cooldown: cool
      })
    });
    if (res.ok) {
      showToast('System configuration saved successfully', 'success');
    }
  } catch (e) {
    showToast('Failed to save settings', 'danger');
  }
}

// ==================== ADMIN VIDEO ANALYSIS HISTORY & PASSWORD MANAGEMENT ====================
async function loadAdminVideoHistory() {
  const tbody = document.getElementById('adminVideoJobsTableBody');
  if (!tbody) return;

  try {
    const res = await fetch('/api/jobs', { headers: getAuthHeaders() });
    if (!res.ok) return;
    const jobs = await res.json();

    if (!Array.isArray(jobs) || jobs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 30px;">No video processing jobs recorded yet. Upload a video above to begin AI analysis.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    jobs.forEach(job => {
      let statusBadge = 'badge-pending';
      const s = (job.status || '').toLowerCase();
      if (s === 'done' || s === 'completed') statusBadge = 'badge-resolved';
      else if (s === 'processing' || s === 'running') statusBadge = 'badge-working';
      else if (s === 'failed') statusBadge = 'badge-high';

      let riskHtml = '<span style="color:var(--text-muted);">-</span>';
      if (job.risk_score !== undefined && job.risk_score !== null) {
        const score = job.risk_score;
        let color = '#00e676';
        if (score >= 70) color = '#ff3b30';
        else if (score >= 40) color = '#ff9500';
        riskHtml = `<span style="font-weight:700; color:${color};">${job.analysis_result || (score >= 70 ? 'High Risk' : 'Normal')} (${score}%)</span>`;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td style="font-weight:700; color:#fff;">JOB-${job.id}</td>
        <td style="color:#e2e8f0; font-weight:600;">${job.filename || 'uploaded_video.mp4'}</td>
        <td style="color:var(--text-muted); font-size:13px;">${job.uploaded_at ? new Date(job.uploaded_at).toLocaleString() : 'Recent'}</td>
        <td><span class="badge ${statusBadge}">${(job.status || 'Done').toUpperCase()}</span></td>
        <td>${riskHtml}</td>
        <td>
          <div style="display:flex; gap:8px;">
            <button class="btn btn-outline btn-sm" onclick="window.open('/api/jobs/${job.id}/download', '_blank')">Download</button>
            <button class="btn btn-primary btn-sm" onclick="switchPage('incidents')">View Incidents</button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    console.error('Failed to load video history', e);
  }
}

async function handleAdminPasswordChange(e) {
  if (e) e.preventDefault();
  const cur = document.getElementById('admPwCurrent').value;
  const nw = document.getElementById('admPwNew').value;
  const conf = document.getElementById('admPwConfirm').value;

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
      showToast('Admin password changed successfully!', 'success');
      document.getElementById('adminPasswordForm').reset();
    } else {
      showToast(data.detail || 'Could not change password.', 'danger');
    }
  } catch (e) {
    showToast('Network error while changing password.', 'danger');
  }
}
