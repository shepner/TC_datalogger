// Dashboard JavaScript

const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 minutes in milliseconds
let refreshTimer = null;
const chartInstances = new Map(); // Store chart instances for cleanup

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
    const { service_name, container_status, last_successful_run, recent_errors, health_status, log_stats, record_summary, run_history } = serviceData;
    
    const containerRunning = container_status.running;
    const containerStatusText = container_status.status || 'unknown';
    const errorCount = recent_errors ? recent_errors.length : 0;
    const summary = record_summary || {};
    const history = run_history || [];

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

            ${summary.last_fetch_count !== null && summary.last_fetch_count !== undefined ? `
                <div class="info-item">
                    <span class="info-label">Records Fetched (Last):</span>
                    <span class="info-value">${summary.last_fetch_count.toLocaleString()}</span>
                </div>
            ` : ''}

            ${summary.last_inserted !== null && summary.last_inserted !== undefined ? `
                <div class="info-item">
                    <span class="info-label">Records Inserted (Last):</span>
                    <span class="info-value">${summary.last_inserted.toLocaleString()}</span>
                </div>
            ` : ''}

            ${summary.last_updated !== null && summary.last_updated !== undefined ? `
                <div class="info-item">
                    <span class="info-label">Records Updated (Last):</span>
                    <span class="info-value">${summary.last_updated.toLocaleString()}</span>
                </div>
            ` : ''}

            ${summary.last_total_records !== null && summary.last_total_records !== undefined ? `
                <div class="info-item">
                    <span class="info-label">Total Records (BQ):</span>
                    <span class="info-value">${summary.last_total_records.toLocaleString()}</span>
                </div>
            ` : ''}

            ${summary.last_unique_ids !== null && summary.last_unique_ids !== undefined ? `
                <div class="info-item">
                    <span class="info-label">Unique IDs (BQ):</span>
                    <span class="info-value">${summary.last_unique_ids.toLocaleString()}</span>
                </div>
            ` : ''}

            ${log_stats && log_stats.exists ? `
                <div class="info-item">
                    <span class="info-label">Log Lines:</span>
                    <span class="info-value">${log_stats.line_count.toLocaleString()}</span>
                </div>
            ` : ''}

            ${history.length > 0 ? `
                <div class="history-section">
                    <div class="history-header">
                        <span class="history-title">Run History</span>
                        <span class="history-count">${history.length} run${history.length !== 1 ? 's' : ''}</span>
                    </div>
                    <div class="history-chart-container">
                        <canvas id="chart-${serviceData.service_key}"></canvas>
                    </div>
                    <div class="history-list-toggle" onclick="toggleHistory(this)">
                        <span class="history-list-title">Detailed History</span>
                        <span class="history-list-arrow">▼</span>
                    </div>
                    <div class="history-list">
                        ${history.map(run => `
                            <div class="history-item">
                                <div class="history-timestamp">${formatDateTime(run.run_timestamp)}</div>
                                <div class="history-stats">
                                    ${run.fetch_count !== null ? `<span class="history-stat">Fetched: ${run.fetch_count.toLocaleString()}</span>` : ''}
                                    ${run.inserted !== null ? `<span class="history-stat">Inserted: ${run.inserted.toLocaleString()}</span>` : ''}
                                    ${run.updated !== null ? `<span class="history-stat">Updated: ${run.updated.toLocaleString()}</span>` : ''}
                                    ${run.total_processed !== null ? `<span class="history-stat">Processed: ${run.total_processed.toLocaleString()}</span>` : ''}
                                    ${run.total_records !== null ? `<span class="history-stat">Total (BQ): ${run.total_records.toLocaleString()}</span>` : ''}
                                </div>
                            </div>
                        `).join('')}
                    </div>
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

// Toggle history section
function toggleHistory(element) {
    const historyList = element.nextElementSibling;
    historyList.classList.toggle('expanded');
    const arrow = element.querySelector('.history-list-arrow');
    if (arrow) {
        arrow.textContent = historyList.classList.contains('expanded') ? '▲' : '▼';
    }
}

// Aggregate run history data with adaptive time compression
function aggregateRunHistory(runs) {
    if (!runs || runs.length === 0) {
        return { labels: [], datasets: [] };
    }

    const now = new Date();
    const aggregated = [];

    // Sort runs by timestamp (oldest first)
    const sortedRuns = [...runs].sort((a, b) => 
        new Date(a.run_timestamp) - new Date(b.run_timestamp)
    );

    for (const run of sortedRuns) {
        const runDate = new Date(run.run_timestamp);
        const ageMs = now - runDate;
        const ageHours = ageMs / (1000 * 60 * 60);
        const ageDays = ageMs / (1000 * 60 * 60 * 24);

        let label;
        let shouldInclude = true;

        // Adaptive time compression:
        // - Last 24 hours: show per run
        // - 1-7 days: show per day
        // - 1-4 weeks: show per week
        // - Older: show per month
        if (ageHours < 24) {
            // Per run - show exact time
            label = runDate.toLocaleTimeString('en-US', { 
                hour: '2-digit', 
                minute: '2-digit' 
            });
        } else if (ageDays < 7) {
            // Per day - group by day
            const dayKey = runDate.toLocaleDateString('en-US', { 
                month: 'short', 
                day: 'numeric' 
            });
            // Check if we already have this day
            const existing = aggregated.find(a => a.label === dayKey);
            if (existing) {
                // Aggregate with existing day data
                existing.fetch_count = (existing.fetch_count || 0) + (run.fetch_count || 0);
                existing.inserted = (existing.inserted || 0) + (run.inserted || 0);
                existing.updated = (existing.updated || 0) + (run.updated || 0);
                existing.total_processed = (existing.total_processed || 0) + (run.total_processed || 0);
                existing.total_records = run.total_records || existing.total_records;
                shouldInclude = false;
            } else {
                label = dayKey;
            }
        } else if (ageDays < 28) {
            // Per week - group by week
            const weekStart = new Date(runDate);
            weekStart.setDate(runDate.getDate() - runDate.getDay()); // Start of week
            const weekKey = weekStart.toLocaleDateString('en-US', { 
                month: 'short', 
                day: 'numeric' 
            });
            const existing = aggregated.find(a => a.label === weekKey);
            if (existing) {
                existing.fetch_count = (existing.fetch_count || 0) + (run.fetch_count || 0);
                existing.inserted = (existing.inserted || 0) + (run.inserted || 0);
                existing.updated = (existing.updated || 0) + (run.updated || 0);
                existing.total_processed = (existing.total_processed || 0) + (run.total_processed || 0);
                existing.total_records = run.total_records || existing.total_records;
                shouldInclude = false;
            } else {
                label = weekKey;
            }
        } else {
            // Per month - group by month
            const monthKey = runDate.toLocaleDateString('en-US', { 
                month: 'short', 
                year: 'numeric' 
            });
            const existing = aggregated.find(a => a.label === monthKey);
            if (existing) {
                existing.fetch_count = (existing.fetch_count || 0) + (run.fetch_count || 0);
                existing.inserted = (existing.inserted || 0) + (run.inserted || 0);
                existing.updated = (existing.updated || 0) + (run.updated || 0);
                existing.total_processed = (existing.total_processed || 0) + (run.total_processed || 0);
                existing.total_records = run.total_records || existing.total_records;
                shouldInclude = false;
            } else {
                label = monthKey;
            }
        }

        if (shouldInclude) {
            aggregated.push({
                label: label,
                timestamp: runDate,
                fetch_count: run.fetch_count || 0,
                inserted: run.inserted || 0,
                updated: run.updated || 0,
                total_processed: run.total_processed || 0,
                total_records: run.total_records || null
            });
        }
    }

    // Build chart data
    const labels = aggregated.map(a => a.label);
    
    return {
        labels: labels,
        datasets: [
            {
                label: 'Fetched',
                data: aggregated.map(a => a.fetch_count),
                borderColor: 'rgb(52, 152, 219)',
                backgroundColor: 'rgba(52, 152, 219, 0.1)',
                tension: 0.4,
                yAxisID: 'y'
            },
            {
                label: 'Inserted',
                data: aggregated.map(a => a.inserted),
                borderColor: 'rgb(46, 204, 113)',
                backgroundColor: 'rgba(46, 204, 113, 0.1)',
                tension: 0.4,
                yAxisID: 'y'
            },
            {
                label: 'Updated',
                data: aggregated.map(a => a.updated),
                borderColor: 'rgb(241, 196, 15)',
                backgroundColor: 'rgba(241, 196, 15, 0.1)',
                tension: 0.4,
                yAxisID: 'y'
            },
            {
                label: 'Total Records (BQ)',
                data: aggregated.map(a => a.total_records),
                borderColor: 'rgb(155, 89, 182)',
                backgroundColor: 'rgba(155, 89, 182, 0.1)',
                tension: 0.4,
                yAxisID: 'y1',
                hidden: true // Hidden by default since scale is different
            }
        ]
    };
}

// Create or update chart for a service
function createOrUpdateChart(serviceKey, canvasId, runHistory) {
    // Destroy existing chart if it exists
    if (chartInstances.has(serviceKey)) {
        chartInstances.get(serviceKey).destroy();
    }

    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const chartData = aggregateRunHistory(runHistory);
    
    if (chartData.labels.length === 0) {
        // No data to show
        return;
    }

    const ctx = canvas.getContext('2d');
    const chart = new Chart(ctx, {
        type: 'line',
        data: chartData,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: {
                        boxWidth: 12,
                        padding: 8,
                        font: {
                            size: 11
                        }
                    }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            if (context.parsed.y !== null) {
                                label += context.parsed.y.toLocaleString();
                            }
                            return label;
                        }
                    }
                }
            },
            scales: {
                x: {
                    display: true,
                    grid: {
                        display: false
                    },
                    ticks: {
                        maxRotation: 45,
                        minRotation: 45,
                        font: {
                            size: 10
                        }
                    }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return value.toLocaleString();
                        },
                        font: {
                            size: 10
                        }
                    },
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)'
                    }
                },
                y1: {
                    type: 'linear',
                    display: false,
                    position: 'right',
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return value.toLocaleString();
                        }
                    },
                    grid: {
                        drawOnChartArea: false
                    }
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    });

    chartInstances.set(serviceKey, chart);
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
            // Destroy all existing charts before re-rendering
            chartInstances.forEach((chart, key) => {
                chart.destroy();
            });
            chartInstances.clear();

            servicesEl.innerHTML = services.map(service => renderServiceCard(service)).join('');

            // Create charts for services with history
            services.forEach(service => {
                if (service.run_history && service.run_history.length > 0) {
                    const canvasId = `chart-${service.service_key}`;
                    // Use setTimeout to ensure DOM is ready
                    setTimeout(() => {
                        createOrUpdateChart(service.service_key, canvasId, service.run_history);
                    }, 100);
                }
            });
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

