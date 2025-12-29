// Dashboard JavaScript

const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes in milliseconds
let refreshTimer = null;

// Format timestamp for display
function formatTimestamp(isoString) {
    if (!isoString) return 'Never';
    
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
    if (diffDays < 7) return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
    
    return date.toLocaleString();
}

// Format date/time for display
function formatDateTime(isoString) {
    if (!isoString) return 'N/A';
    return new Date(isoString).toLocaleString();
}

// Get status badge class
function getStatusClass(status) {
    const statusMap = {
        'healthy': 'status-healthy',
        'degraded': 'status-degraded',
        'unhealthy': 'status-unhealthy',
        'error': 'status-error',
    };
    return statusMap[status] || 'status-error';
}

// Get container status class
function getContainerStatusClass(status) {
    if (status === 'running') return 'container-running';
    if (status === 'not_found' || status === 'docker_unavailable') return 'container-not-found';
    return 'container-stopped';
}

// Render service card
function renderServiceCard(serviceData) {
    const { service_name, container_status, last_successful_run, recent_errors, health_status, log_stats } = serviceData;
    
    const containerRunning = container_status.running;
    const containerStatusText = container_status.status || 'unknown';
    const errorCount = recent_errors ? recent_errors.length : 0;

    return `
        <div class="service-card">
            <div class="service-header">
                <div class="service-name">${service_name}</div>
                <span class="status-badge ${getStatusClass(health_status)}">${health_status}</span>
            </div>

            <div class="info-item">
                <span class="info-label">Container:</span>
                <span class="info-value">
                    <span class="container-status ${getContainerStatusClass(containerStatusText)}">
                        ${containerRunning ? 'Running' : containerStatusText.replace('_', ' ').toUpperCase()}
                    </span>
                </span>
            </div>

            <div class="info-item">
                <span class="info-label">Last Run:</span>
                <span class="info-value">${formatTimestamp(last_successful_run)}</span>
            </div>

            ${last_successful_run ? `
                <div class="info-item">
                    <span class="info-label">Last Run Time:</span>
                    <span class="info-value">${formatDateTime(last_successful_run)}</span>
                </div>
            ` : ''}

            ${log_stats && log_stats.exists ? `
                <div class="info-item">
                    <span class="info-label">Log Lines:</span>
                    <span class="info-value">${log_stats.line_count.toLocaleString()}</span>
                </div>
            ` : ''}

            <div class="errors-section">
                <div class="errors-header" onclick="toggleErrors(this)">
                    <span class="errors-title">Recent Errors</span>
                    <span class="errors-count">${errorCount}</span>
                </div>
                <div class="errors-list">
                    ${errorCount > 0 ? recent_errors.map(error => `
                        <div class="error-item">
                            <div class="error-timestamp">${formatDateTime(error.timestamp)}</div>
                            <div class="error-message">${escapeHtml(error.message)}</div>
                        </div>
                    `).join('') : '<div class="no-errors">No recent errors</div>'}
                </div>
            </div>
        </div>
    `;
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Toggle errors section
function toggleErrors(header) {
    const errorsList = header.nextElementSibling;
    errorsList.classList.toggle('expanded');
}

// Fetch and render health data
async function fetchHealthData() {
    const loadingEl = document.getElementById('loading');
    const errorEl = document.getElementById('error');
    const servicesEl = document.getElementById('services');
    const lastUpdateEl = document.getElementById('lastUpdate');

    try {
        loadingEl.style.display = 'block';
        errorEl.style.display = 'none';

        const response = await fetch('/api/health');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        
        if (data.error) {
            throw new Error(data.error);
        }

        // Render services
        const services = Object.values(data.services || {});
        if (services.length === 0) {
            servicesEl.innerHTML = '<div class="no-data">No services found</div>';
        } else {
            servicesEl.innerHTML = services.map(service => renderServiceCard(service)).join('');
        }

        // Update last update time
        lastUpdateEl.textContent = `Last updated: ${formatTimestamp(data.timestamp)}`;

        loadingEl.style.display = 'none';
    } catch (error) {
        console.error('Error fetching health data:', error);
        loadingEl.style.display = 'none';
        errorEl.textContent = `Error loading health data: ${error.message}`;
        errorEl.style.display = 'block';
    }
}

// Setup refresh button
document.getElementById('refreshBtn').addEventListener('click', () => {
    fetchHealthData();
});

// Auto-refresh
function startAutoRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
    }
    refreshTimer = setInterval(fetchHealthData, REFRESH_INTERVAL);
}

// Initial load
fetchHealthData();
startAutoRefresh();

