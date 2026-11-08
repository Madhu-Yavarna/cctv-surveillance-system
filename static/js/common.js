// Common Utilities for Context-Aware Video Surveillance System

function getAuthToken() {
  return localStorage.getItem('surveillance_token');
}

function getUserProfile() {
  try {
    return JSON.parse(localStorage.getItem('surveillance_user') || '{}');
  } catch (e) {
    return {};
  }
}

function getAuthHeaders() {
  const token = getAuthToken();
  return {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  };
}

function checkAuth(requiredRole) {
  const token = getAuthToken();
  const user = getUserProfile();

  if (!token || !user.role) {
    window.location.href = '/login';
    return false;
  }

  if (requiredRole && user.role !== requiredRole) {
    alert(`Access denied: Required role is '${requiredRole}' but current role is '${user.role}'`);
    window.location.href = user.role === 'admin' ? '/admin' : '/police';
    return false;
  }

  return true;
}

function logout() {
  localStorage.removeItem('surveillance_token');
  localStorage.removeItem('surveillance_user');
  window.location.href = '/login';
}

function startClock(dateElementId, timeElementId) {
  function tick() {
    const now = new Date();
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const dateStr = `${now.getDate()} ${months[now.getMonth()]} ${now.getFullYear()}`;
    const timeStr = now.toTimeString().split(' ')[0];

    const dEl = document.getElementById(dateElementId);
    const tEl = document.getElementById(timeElementId);
    if (dEl) dEl.innerText = dateStr;
    if (tEl) tEl.innerText = timeStr;
  }
  tick();
  setInterval(tick, 1000);
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type === 'danger' ? 'alert-high' : ''}`;
  
  let icon = 'ℹ️';
  if (type === 'success') icon = '✅';
  if (type === 'danger') icon = '🚨';
  if (type === 'warning') icon = '⚠️';

  toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(100%)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

function formatHash(hash) {
  if (!hash || hash.length < 16) return hash || 'N/A';
  return `${hash.substring(0, 8)}...${hash.substring(hash.length - 6)}`;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, function(m) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
  });
}
