/* ============================================
   Earmark - SPA Frontend
   Vanilla JS with hash-based routing
   ============================================ */

(function () {
    'use strict';

    // ── Constants ──────────────────────────────────────────────
    const HC_STATUSES = {
        1: 'Want to Read',
        2: 'Currently Reading',
        3: 'Read',
        5: 'DNF',
    };

    const DIRECTION_LABELS = {
        hc_to_abs: 'HC \u2192 ABS',
        abs_to_hc: 'ABS \u2192 HC',
        bidirectional: 'Bidirectional',
    };

    const ACTION_ICONS = {
        added_to_collection: '\u2705',
        removed_from_collection: '\u274c',
        progress_updated: '\ud83d\udcca',
        status_updated: '\ud83d\udcdd',
        progress_synced_to_abs: '\ud83d\udce4',
        match_found: '\ud83d\udd17',
        match_failed: '\u26a0\ufe0f',
        error: '\ud83d\udea8',
    };

    // ── State ──────────────────────────────────────────────────
    let currentView = 'dashboard';
    let usersCache = [];
    let healthData = null;

    // ── Utilities ──────────────────────────────────────────────

    async function api(method, path, body = null) {
        const opts = {
            method,
            headers: {},
        };
        if (body && !(body instanceof FormData)) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        } else if (body instanceof FormData) {
            opts.body = body;
        }
        const res = await fetch(`/api${path}`, opts);
        if (!res.ok) {
            let msg;
            try {
                const data = await res.json();
                msg = data.detail || data.message || res.statusText;
            } catch {
                msg = res.statusText;
            }
            if (res.status === 409) {
                throw new ApiError(msg || 'Sync already in progress', 409);
            }
            throw new ApiError(msg, res.status);
        }
        if (res.status === 204) return null;
        const ct = res.headers.get('content-type') || '';
        if (ct.includes('application/json')) return res.json();
        return res;
    }

    class ApiError extends Error {
        constructor(message, status) {
            super(message);
            this.status = status;
        }
    }

    function $(sel, parent = document) {
        return parent.querySelector(sel);
    }

    function $$(sel, parent = document) {
        return Array.from(parent.querySelectorAll(sel));
    }

    function el(tag, attrs = {}, children = []) {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs)) {
            if (k === 'className') node.className = v;
            else if (k === 'innerHTML') node.innerHTML = v;
            else if (k === 'textContent') node.textContent = v;
            else if (k.startsWith('on')) node.addEventListener(k.slice(2).toLowerCase(), v);
            else node.setAttribute(k, v);
        }
        for (const child of Array.isArray(children) ? children : [children]) {
            if (typeof child === 'string') node.appendChild(document.createTextNode(child));
            else if (child) node.appendChild(child);
        }
        return node;
    }

    function timeAgo(dateStr) {
        if (!dateStr) return 'Never';
        const d = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    }

    function formatDate(dateStr) {
        if (!dateStr) return '-';
        const d = new Date(dateStr.endsWith('Z') ? dateStr : dateStr + 'Z');
        return d.toLocaleString();
    }

    function escapeHtml(str) {
        if (!str) return '';
        const d = document.createElement('div');
        d.textContent = str;
        return d.innerHTML;
    }

    // ── Toast Notifications ────────────────────────────────────

    function toast(message, type = 'info', duration = 4000) {
        const container = $('#toast-container');
        const icons = {
            success: '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>',
            error: '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>',
            warning: '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>',
            info: '<svg class="toast-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        };

        const t = el('div', { className: `toast toast-${type}` });
        t.innerHTML = `
            ${icons[type] || icons.info}
            <span class="flex-1">${escapeHtml(message)}</span>
            <button class="toast-close" aria-label="Close">&times;</button>
        `;
        container.appendChild(t);

        const close = () => {
            t.classList.add('removing');
            setTimeout(() => t.remove(), 250);
        };

        t.querySelector('.toast-close').addEventListener('click', close);
        if (duration > 0) setTimeout(close, duration);
    }

    // ── Confirm Dialog ─────────────────────────────────────────

    function confirm(title, message) {
        return new Promise((resolve) => {
            const modal = $('#confirm-modal');
            $('#confirm-title').textContent = title;
            $('#confirm-message').textContent = message;
            modal.classList.remove('hidden');

            const cleanup = (result) => {
                modal.classList.add('hidden');
                resolve(result);
            };

            $('#confirm-ok').onclick = () => cleanup(true);
            $('#confirm-cancel').onclick = () => cleanup(false);
            $('#confirm-overlay').onclick = () => cleanup(false);
        });
    }

    // ── Loading Skeletons ──────────────────────────────────────

    function skeleton(count = 3, height = '1rem') {
        return Array.from({ length: count }, () =>
            `<div class="skeleton" style="height:${height};margin-bottom:0.75rem"></div>`
        ).join('');
    }

    function loadingState() {
        return `<div class="animate-fade-in">${skeleton(5, '3rem')}</div>`;
    }

    // ── Router ─────────────────────────────────────────────────

    const routes = {
        '/': { title: 'Dashboard', render: renderDashboard },
        '/users': { title: 'Users', render: renderUsers },
        '/rules': { title: 'Sync Rules', render: renderRules },
        '/mappings': { title: 'Book Mappings', render: renderMappings },
        '/log': { title: 'Sync Log', render: renderLog },
        '/stats': { title: 'Stats', render: renderStats },
        '/settings': { title: 'Settings', render: renderSettings },
    };

    function navigate() {
        const hash = window.location.hash.slice(1) || '/';
        const route = routes[hash] || routes['/'];
        currentView = hash;

        // Update nav
        $$('.nav-link').forEach((link) => {
            const href = link.getAttribute('href').slice(1);
            link.classList.toggle('active', href === hash);
        });

        // Update title
        $('#page-title').textContent = route.title;

        // Render view
        const content = $('#content');
        content.innerHTML = loadingState();
        route.render(content);
    }

    // ── Health Check ───────────────────────────────────────────

    async function checkHealth() {
        try {
            healthData = await api('GET', '/health');
            const dot = $('#health-dot');
            const text = $('#health-text');
            dot.className = 'w-2 h-2 rounded-full bg-emerald-500';
            text.textContent = `v${healthData.version || '?'} | Last sync: ${timeAgo(healthData.last_sync)}`;
            text.className = 'text-xs text-gray-400';
        } catch {
            const dot = $('#health-dot');
            const text = $('#health-text');
            dot.className = 'w-2 h-2 rounded-full bg-red-500';
            text.textContent = 'API unreachable';
            text.className = 'text-xs text-red-400';
        }
    }

    async function loadUsers() {
        try {
            usersCache = await api('GET', '/users');
        } catch {
            usersCache = [];
        }
    }

    // ── Dashboard View ─────────────────────────────────────────

    async function renderDashboard(container) {
        try {
            const [users, mappings, logEntries, health] = await Promise.all([
                api('GET', '/users'),
                api('GET', '/mappings'),
                api('GET', '/log?limit=20'),
                api('GET', '/health').catch(() => null),
            ]);

            usersCache = users;
            const needsRefresh = users.some((u) => u.needs_token_refresh);
            const totalUsers = users.length;
            const totalMappings = Array.isArray(mappings) ? mappings.length : 0;
            const lastSync = health?.last_sync;
            const logs = Array.isArray(logEntries) ? logEntries : logEntries?.items || [];

            container.innerHTML = `
                <div class="animate-fade-in space-y-6">
                    ${needsRefresh ? `
                        <div class="alert alert-warning">
                            <svg class="w-5 h-5 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>
                            <div>
                                <p class="font-medium">Token Refresh Required</p>
                                <p class="text-sm opacity-80 mt-1">One or more users need their Hardcover token refreshed. Go to <a href="#/users" class="underline">Users</a> to update.</p>
                            </div>
                        </div>
                    ` : ''}

                    <!-- Stats Cards -->
                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                        <div class="stat-card">
                            <p class="text-sm text-gray-400 mb-1">Total Users</p>
                            <p class="text-3xl font-bold text-white">${totalUsers}</p>
                            <p class="text-xs text-gray-500 mt-1">${users.filter((u) => u.enabled).length} active</p>
                        </div>
                        <div class="stat-card">
                            <p class="text-sm text-gray-400 mb-1">Synced Books</p>
                            <p class="text-3xl font-bold text-white">${totalMappings}</p>
                            <p class="text-xs text-gray-500 mt-1">Matched book pairs</p>
                        </div>
                        <div class="stat-card">
                            <p class="text-sm text-gray-400 mb-1">Last Sync</p>
                            <p class="text-3xl font-bold text-white">${timeAgo(lastSync)}</p>
                            <p class="text-xs text-gray-500 mt-1">${lastSync ? formatDate(lastSync) : 'No sync yet'}</p>
                        </div>
                    </div>

                    <!-- Actions -->
                    <div class="flex items-center gap-3">
                        <button id="sync-now-btn" class="btn btn-primary">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                            Sync Now
                        </button>
                    </div>

                    <!-- Recent Log -->
                    <div class="card">
                        <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Recent Activity</h3>
                        ${logs.length === 0 ? `
                            <p class="text-gray-500 text-sm py-4">No sync activity yet. Configure users and rules, then run a sync.</p>
                        ` : `
                            <div class="overflow-x-auto custom-scrollbar">
                                <table class="data-table">
                                    <thead>
                                        <tr>
                                            <th>Status</th>
                                            <th>Action</th>
                                            <th>Direction</th>
                                            <th>Details</th>
                                            <th>Time</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${logs.map((entry) => {
                                            const icon = ACTION_ICONS[entry.action] || '\u2139\ufe0f';
                                            const details = entry.details ? (typeof entry.details === 'string' ? JSON.parse(entry.details) : entry.details) : {};
                                            return `
                                                <tr>
                                                    <td><span class="text-lg">${icon}</span></td>
                                                    <td><span class="badge badge-${entry.action === 'error' ? 'error' : entry.action === 'match_failed' ? 'warning' : 'info'}">${escapeHtml(entry.action)}</span></td>
                                                    <td class="text-gray-400">${entry.direction ? DIRECTION_LABELS[entry.direction] || entry.direction : '-'}</td>
                                                    <td class="text-gray-300 max-w-xs truncate">${escapeHtml(details.title || details.message || JSON.stringify(details).slice(0, 80))}</td>
                                                    <td class="text-gray-500 whitespace-nowrap">${timeAgo(entry.created_at)}</td>
                                                </tr>
                                            `;
                                        }).join('')}
                                    </tbody>
                                </table>
                            </div>
                        `}
                    </div>
                </div>
            `;

            // Bind sync button
            $('#sync-now-btn').addEventListener('click', async (e) => {
                const btn = e.currentTarget;
                btn.disabled = true;
                btn.innerHTML = '<svg class="w-4 h-4 animate-spin-slow" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Syncing...';
                try {
                    await api('POST', '/sync');
                    toast('Sync started successfully', 'success');
                    setTimeout(() => renderDashboard(container), 2000);
                } catch (err) {
                    if (err.status === 409) {
                        toast('A sync is already in progress', 'warning');
                    } else {
                        toast(`Sync failed: ${err.message}`, 'error');
                    }
                } finally {
                    btn.disabled = false;
                    btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg> Sync Now';
                }
            });
        } catch (err) {
            container.innerHTML = `<div class="alert alert-error">Failed to load dashboard: ${escapeHtml(err.message)}</div>`;
        }
    }

    // ── Users View ─────────────────────────────────────────────

    async function renderUsers(container) {
        try {
            const users = await api('GET', '/users');
            usersCache = users;

            container.innerHTML = `
                <div class="animate-fade-in space-y-6">
                    <div class="flex items-center justify-between">
                        <p class="text-gray-400 text-sm">${users.length} user${users.length !== 1 ? 's' : ''} configured</p>
                        <button id="add-user-btn" class="btn btn-primary btn-sm">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                            Add User
                        </button>
                    </div>

                    <div id="user-form-container"></div>

                    <div id="users-list" class="space-y-3">
                        ${users.length === 0 ? '<p class="text-gray-500 text-sm py-8 text-center">No users configured. Add one to get started.</p>' : ''}
                        ${users.map((u) => userCard(u)).join('')}
                    </div>
                </div>
            `;

            $('#add-user-btn').addEventListener('click', () => showUserForm());
            bindUserActions();
        } catch (err) {
            container.innerHTML = `<div class="alert alert-error">Failed to load users: ${escapeHtml(err.message)}</div>`;
        }
    }

    function userCard(u) {
        return `
            <div class="card animate-slide-up" data-user-id="${u.id}">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-3 mb-2">
                            <h3 class="text-white font-semibold text-base">${escapeHtml(u.name)}</h3>
                            <span class="badge ${u.enabled ? 'badge-success' : 'badge-neutral'}">${u.enabled ? 'Active' : 'Disabled'}</span>
                            ${u.needs_token_refresh ? '<span class="badge badge-warning">Token Expired</span>' : ''}
                        </div>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-sm">
                            <div class="text-gray-400">HC: <span class="text-gray-300">${u.hardcover_username ? escapeHtml(u.hardcover_username) : '<span class="text-yellow-500">Test connection to resolve</span>'}</span></div>
                            <div class="text-gray-400">ABS: <span class="text-gray-300">${u.abs_username ? escapeHtml(u.abs_username) : '<span class="text-yellow-500">Test connection to resolve</span>'}</span></div>
                        </div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        <button class="btn btn-ghost btn-sm user-edit-btn" data-id="${u.id}" title="Edit">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                        </button>
                        <button class="btn btn-ghost btn-sm user-delete-btn text-red-400" data-id="${u.id}" data-name="${escapeHtml(u.name)}" title="Delete">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    function showUserForm(user = null) {
        const formContainer = $('#user-form-container');
        const isEdit = !!user;

        formContainer.innerHTML = `
            <div class="card animate-fade-in border-brand-600/30">
                <h3 class="text-white font-semibold mb-4">${isEdit ? 'Edit' : 'Add'} User</h3>
                <form id="user-form" class="space-y-4">
                    <input type="hidden" name="id" value="${user?.id || ''}">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="md:col-span-2">
                            <label class="block text-sm text-gray-400 mb-1">Display Name</label>
                            <input type="text" name="name" value="${escapeHtml(user?.name || '')}" required placeholder="e.g. Toby">
                        </div>
                        <div class="space-y-1">
                            <div class="flex items-center justify-between">
                                <label class="block text-sm text-gray-400">Hardcover API URL</label>
                                <a href="https://hardcover.app/account/api" target="_blank" rel="noopener" class="text-xs text-brand-400 hover:text-brand-300 transition-colors">Get token &rarr;</a>
                            </div>
                            <input type="text" value="https://api.hardcover.app/v1/graphql" disabled class="opacity-60 cursor-not-allowed">
                            <div class="flex gap-2">
                                <input type="password" name="hardcover_token" value="" class="flex-1" placeholder="${isEdit ? 'Leave blank to keep current' : 'Paste token here'}">
                                <button type="button" id="test-hc-btn" class="btn btn-secondary btn-sm whitespace-nowrap" title="Test Hardcover token">Test</button>
                            </div>
                            <div id="hc-test-result" class="text-xs mt-1"></div>
                        </div>
                        <div class="space-y-1">
                            <div class="flex items-center justify-between">
                                <label class="block text-sm text-gray-400">Audiobookshelf URL</label>
                                <span class="text-xs text-gray-500">Settings &rarr; Users &rarr; API Keys</span>
                            </div>
                            <input type="url" name="abs_url" value="${escapeHtml(user?.abs_url || '')}" required placeholder="https://abs.example.com">
                            <div class="flex gap-2">
                                <input type="password" name="abs_api_key" value="" class="flex-1" placeholder="${isEdit ? 'Leave blank to keep current' : 'Paste API key here'}">
                                <button type="button" id="test-abs-btn" class="btn btn-secondary btn-sm whitespace-nowrap" title="Test ABS connection">Test</button>
                            </div>
                            <div id="abs-test-result" class="text-xs mt-1"></div>
                        </div>
                    </div>
                    <div class="flex items-center gap-2">
                        <input type="checkbox" name="enabled" id="user-enabled" ${user?.enabled !== false ? 'checked' : ''}>
                        <label for="user-enabled" class="text-sm text-gray-300">Enabled</label>
                    </div>
                    <div class="flex items-center gap-3 pt-2">
                        <button type="submit" class="btn btn-primary">${isEdit ? 'Update' : 'Create'} User</button>
                        <button type="button" id="cancel-user-form" class="btn btn-secondary">Cancel</button>
                        ${isEdit ? `<button type="button" id="test-conn-btn" class="btn btn-secondary" data-id="${user.id}">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
                            Test Connection
                        </button>` : ''}
                    </div>
                    <div id="test-result" class="hidden"></div>
                </form>
            </div>
        `;

        $('#cancel-user-form').addEventListener('click', () => {
            formContainer.innerHTML = '';
        });

        if (isEdit && $('#test-conn-btn')) {
            $('#test-conn-btn').addEventListener('click', () => testConnection(user.id));
        }

        // Individual test buttons (work for both create and edit)
        $('#test-hc-btn').addEventListener('click', async () => {
            const token = $('[name="hardcover_token"]').value.trim();
            if (!token && !isEdit) { toast('Enter a Hardcover token first', 'warning'); return; }
            const btn = $('#test-hc-btn');
            const resultDiv = $('#hc-test-result');
            btn.disabled = true; btn.textContent = 'Testing...';
            try {
                const tokenToTest = token || '__use_saved__';
                let result;
                if (isEdit && !token) {
                    // Use saved user test endpoint
                    result = await api('POST', `/users/${user.id}/test`);
                    if (result.hardcover_ok) {
                        resultDiv.innerHTML = `<span class="text-green-400">Connected as <strong>${escapeHtml(result.hardcover_username || 'unknown')}</strong></span>`;
                    } else {
                        resultDiv.innerHTML = `<span class="text-red-400">${escapeHtml(result.errors?.find(e => e.includes('Hardcover')) || 'Failed')}</span>`;
                    }
                } else {
                    result = await api('POST', '/test/hardcover', { token });
                    if (result.ok) {
                        resultDiv.innerHTML = `<span class="text-green-400">Connected as <strong>${escapeHtml(result.username || 'unknown')}</strong></span>`;
                    } else {
                        resultDiv.innerHTML = `<span class="text-red-400">${escapeHtml(result.error || 'Failed')}</span>`;
                    }
                }
            } catch (err) {
                resultDiv.innerHTML = `<span class="text-red-400">${escapeHtml(err.message)}</span>`;
            } finally {
                btn.disabled = false; btn.textContent = 'Test';
            }
        });

        $('#test-abs-btn').addEventListener('click', async () => {
            const url = $('[name="abs_url"]').value.trim();
            const key = $('[name="abs_api_key"]').value.trim();
            if (!url) { toast('Enter an ABS URL first', 'warning'); return; }
            if (!key && !isEdit) { toast('Enter an ABS API key first', 'warning'); return; }
            const btn = $('#test-abs-btn');
            const resultDiv = $('#abs-test-result');
            btn.disabled = true; btn.textContent = 'Testing...';
            try {
                let result;
                if (isEdit && !key) {
                    result = await api('POST', `/users/${user.id}/test`);
                    if (result.abs_ok) {
                        resultDiv.innerHTML = `<span class="text-green-400">Connected as <strong>${escapeHtml(result.abs_username || 'unknown')}</strong> ${result.abs_is_admin ? '(admin)' : '(non-admin)'} &mdash; ${result.abs_libraries?.length || 0} libraries</span>`;
                    } else {
                        resultDiv.innerHTML = `<span class="text-red-400">${escapeHtml(result.errors?.find(e => e.includes('Audiobookshelf')) || 'Failed')}</span>`;
                    }
                } else {
                    result = await api('POST', '/test/abs', { url, api_key: key });
                    if (result.ok) {
                        resultDiv.innerHTML = `<span class="text-green-400">Connected as <strong>${escapeHtml(result.username || 'unknown')}</strong> ${result.is_admin ? '(admin)' : '(non-admin)'} &mdash; ${result.libraries?.length || 0} libraries</span>`;
                    } else {
                        resultDiv.innerHTML = `<span class="text-red-400">${escapeHtml(result.error || 'Failed')}</span>`;
                    }
                }
            } catch (err) {
                resultDiv.innerHTML = `<span class="text-red-400">${escapeHtml(err.message)}</span>`;
            } finally {
                btn.disabled = false; btn.textContent = 'Test';
            }
        });

        $('#user-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = e.target;
            const data = {
                name: form.name.value.trim(),
                abs_url: form.abs_url.value.trim(),
                enabled: form.enabled.checked,
            };

            // Only include tokens if provided (for edits, blank means keep current)
            const hcToken = form.hardcover_token.value.trim();
            const absKey = form.abs_api_key.value.trim();
            if (hcToken) data.hardcover_token = hcToken;
            if (absKey) data.abs_api_key = absKey;

            // For create, tokens are required
            if (!isEdit && (!hcToken || !absKey)) {
                toast('Both tokens are required for new users', 'error');
                return;
            }

            const submitBtn = form.querySelector('[type="submit"]');
            submitBtn.disabled = true;
            submitBtn.textContent = isEdit ? 'Saving...' : 'Creating...';

            try {
                let savedUser;
                if (isEdit) {
                    savedUser = await api('PUT', `/users/${user.id}`, data);
                } else {
                    savedUser = await api('POST', '/users', data);
                }
                toast(isEdit ? 'User saved. Testing connections...' : 'User created. Testing connections...', 'info');
                // Auto-test connections after save
                try {
                    const test = await api('POST', `/users/${savedUser.id}/test`);
                    const hcOk = test.hardcover_ok;
                    const absOk = test.abs_ok;
                    const libs = test.abs_libraries || [];
                    let msg = '';
                    if (hcOk) msg += `HC: ${test.hardcover_username || 'connected'}`;
                    else msg += 'HC: failed';
                    msg += ' | ';
                    if (absOk) msg += `ABS: ${test.abs_username || 'connected'} (${libs.length} libraries)`;
                    else msg += 'ABS: failed';
                    toast(msg, hcOk && absOk ? 'success' : 'warning');
                } catch (testErr) {
                    toast('Saved but connection test failed: ' + testErr.message, 'warning');
                }
                renderUsers($('#content'));
            } catch (err) {
                toast(`Failed to save user: ${err.message}`, 'error');
                submitBtn.disabled = false;
                submitBtn.textContent = isEdit ? 'Update User' : 'Create User';
            }
        });
    }

    async function testConnection(userId) {
        const resultDiv = $('#test-result');
        const btn = $('#test-conn-btn');
        btn.disabled = true;
        btn.innerHTML = '<svg class="w-4 h-4 animate-spin-slow" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9"/></svg> Testing...';
        resultDiv.classList.remove('hidden');
        resultDiv.innerHTML = '<div class="skeleton" style="height:3rem"></div>';

        try {
            const result = await api('POST', `/users/${userId}/test`);
            resultDiv.innerHTML = `
                <div class="alert alert-info mt-3">
                    <div>
                        <p class="font-medium mb-2">Connection Test Results</p>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                            <div>HC Username: <strong>${escapeHtml(result.hardcover_username || 'N/A')}</strong></div>
                            <div>ABS Username: <strong>${escapeHtml(result.abs_username || 'N/A')}</strong></div>
                            <div>ABS Admin: <strong>${result.abs_is_admin ? 'Yes' : 'No'}</strong></div>
                            <div>ABS Libraries: <strong>${result.abs_libraries?.length || 0}</strong></div>
                        </div>
                        ${result.abs_libraries?.length ? `
                            <div class="mt-2 text-sm">
                                <p class="text-gray-400 mb-1">Libraries:</p>
                                <ul class="list-disc list-inside text-gray-300">
                                    ${result.abs_libraries.map((lib) => `<li>${escapeHtml(lib.name)} (${escapeHtml(lib.id)})</li>`).join('')}
                                </ul>
                            </div>
                        ` : ''}
                    </div>
                </div>
            `;
        } catch (err) {
            resultDiv.innerHTML = `<div class="alert alert-error mt-3">Connection test failed: ${escapeHtml(err.message)}</div>`;
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg> Test Connection';
        }
    }

    function bindUserActions() {
        $$('.user-edit-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                try {
                    const user = usersCache.find((u) => u.id === id) || (await api('GET', `/users/${id}`));
                    showUserForm(user);
                } catch (err) {
                    toast(`Failed to load user: ${err.message}`, 'error');
                }
            });
        });

        $$('.user-delete-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                const name = btn.dataset.name;
                const ok = await confirm('Delete User', `Are you sure you want to delete user "${name}"? This will also delete their sync rules and mappings.`);
                if (!ok) return;
                try {
                    await api('DELETE', `/users/${id}`);
                    toast('User deleted', 'success');
                    renderUsers($('#content'));
                } catch (err) {
                    toast(`Failed to delete user: ${err.message}`, 'error');
                }
            });
        });
    }

    // ── Rules View ─────────────────────────────────────────────

    async function renderRules(container) {
        try {
            const [rules, users] = await Promise.all([
                api('GET', '/rules'),
                api('GET', '/users'),
            ]);
            usersCache = users;

            container.innerHTML = `
                <div class="animate-fade-in space-y-6">
                    <div class="flex items-center justify-between">
                        <p class="text-gray-400 text-sm">${rules.length} rule${rules.length !== 1 ? 's' : ''} configured</p>
                        <button id="add-rule-btn" class="btn btn-primary btn-sm">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                            Add Rule
                        </button>
                    </div>

                    <div id="rule-form-container"></div>

                    <div id="rules-list" class="space-y-3">
                        ${rules.length === 0 ? '<p class="text-gray-500 text-sm py-8 text-center">No sync rules configured. Add one to define how books should sync.</p>' : ''}
                        ${rules.map((r) => ruleCard(r, users)).join('')}
                    </div>
                </div>
            `;

            $('#add-rule-btn').addEventListener('click', () => showRuleForm(null, users));
            bindRuleActions(users);
        } catch (err) {
            container.innerHTML = `<div class="alert alert-error">Failed to load rules: ${escapeHtml(err.message)}</div>`;
        }
    }

    function ruleCard(r, users) {
        const user = users.find((u) => u.id === r.user_id);
        return `
            <div class="card animate-slide-up" data-rule-id="${r.id}">
                <div class="flex items-start justify-between gap-4">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-3 mb-2 flex-wrap">
                            <span class="badge badge-info">${DIRECTION_LABELS[r.direction] || r.direction}</span>
                            <span class="badge ${r.enabled ? 'badge-success' : 'badge-neutral'}">${r.enabled ? 'Active' : 'Disabled'}</span>
                            <span class="text-xs text-gray-500">User: ${escapeHtml(user?.name || r.user_id)}</span>
                        </div>
                        <div class="grid grid-cols-1 sm:grid-cols-3 gap-x-6 gap-y-1 text-sm">
                            <div class="text-gray-400">HC Status: <span class="text-gray-300">${HC_STATUSES[r.hc_status_id] || 'Any'}</span></div>
                            <div class="text-gray-400">ABS Target: <span class="text-gray-300">${escapeHtml(r.abs_target_name)} (${r.abs_target_type})</span></div>
                            <div class="text-gray-400">Library: <span class="text-gray-300">${escapeHtml(r.abs_library_id || 'N/A')}</span></div>
                        </div>
                        ${r.remove_stale !== undefined ? `<div class="text-xs text-gray-500 mt-1">Remove stale: ${r.remove_stale ? 'Yes' : 'No'}</div>` : ''}
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        <button class="btn btn-ghost btn-sm rule-edit-btn" data-id="${r.id}" title="Edit">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                        </button>
                        <button class="btn btn-ghost btn-sm rule-delete-btn text-red-400" data-id="${r.id}" title="Delete">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                        </button>
                    </div>
                </div>
            </div>
        `;
    }

    function showRuleForm(rule = null, users = []) {
        const formContainer = $('#rule-form-container');
        const isEdit = !!rule;

        formContainer.innerHTML = `
            <div class="card animate-fade-in border-brand-600/30">
                <h3 class="text-white font-semibold mb-4">${isEdit ? 'Edit' : 'Add'} Sync Rule</h3>
                <form id="rule-form" class="space-y-4">
                    <input type="hidden" name="id" value="${rule?.id || ''}">
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">User</label>
                            <select name="user_id" required>
                                <option value="">Select user...</option>
                                ${users.map((u) => `<option value="${u.id}" ${rule?.user_id === u.id ? 'selected' : ''}>${escapeHtml(u.name)}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">Direction</label>
                            <select name="direction" required>
                                <option value="hc_to_abs" ${rule?.direction === 'hc_to_abs' ? 'selected' : ''}>HC \u2192 ABS</option>
                                <option value="abs_to_hc" ${rule?.direction === 'abs_to_hc' ? 'selected' : ''}>ABS \u2192 HC</option>
                                <option value="bidirectional" ${rule?.direction === 'bidirectional' ? 'selected' : ''}>Bidirectional</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">HC Status</label>
                            <select name="hc_status_id">
                                <option value="">Any</option>
                                ${Object.entries(HC_STATUSES).map(([id, label]) => `<option value="${id}" ${rule?.hc_status_id == id ? 'selected' : ''}>${label}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">ABS Target Type</label>
                            <select name="abs_target_type" required>
                                <option value="collection" ${rule?.abs_target_type === 'collection' ? 'selected' : ''}>Collection</option>
                                <option value="playlist" ${rule?.abs_target_type === 'playlist' ? 'selected' : ''}>Playlist</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">ABS Target Name</label>
                            <input type="text" name="abs_target_name" value="${escapeHtml(rule?.abs_target_name || '')}" required placeholder="e.g. Want to Read">
                        </div>
                        <div>
                            <label class="block text-sm text-gray-400 mb-1">ABS Library</label>
                            <select name="abs_library_id" required>
                                <option value="">Select user first...</option>
                                ${rule?.abs_library_id ? `<option value="${escapeHtml(rule.abs_library_id)}" selected>${escapeHtml(rule.abs_library_id)}</option>` : ''}
                            </select>
                        </div>
                    </div>
                    <div class="flex items-center gap-6">
                        <div class="flex items-center gap-2">
                            <input type="checkbox" name="remove_stale" id="rule-remove-stale" ${rule?.remove_stale !== false ? 'checked' : ''}>
                            <label for="rule-remove-stale" class="text-sm text-gray-300">Remove stale items</label>
                        </div>
                        <div class="flex items-center gap-2">
                            <input type="checkbox" name="enabled" id="rule-enabled" ${rule?.enabled !== false ? 'checked' : ''}>
                            <label for="rule-enabled" class="text-sm text-gray-300">Enabled</label>
                        </div>
                    </div>
                    <div class="flex items-center gap-3 pt-2">
                        <button type="submit" class="btn btn-primary">${isEdit ? 'Update' : 'Create'} Rule</button>
                        <button type="button" id="cancel-rule-form" class="btn btn-secondary">Cancel</button>
                    </div>
                </form>
            </div>
        `;

        $('#cancel-rule-form').addEventListener('click', () => {
            formContainer.innerHTML = '';
        });

        // Auto-populate ABS library dropdown when user is selected
        async function loadLibraries(userId, preselectId) {
            const libSelect = $('[name="abs_library_id"]');
            libSelect.innerHTML = '<option value="">Loading libraries...</option>';
            try {
                const libs = await api('GET', `/abs/${userId}/libraries`);
                libSelect.innerHTML = '<option value="">Select library...</option>';
                for (const lib of libs) {
                    const opt = document.createElement('option');
                    opt.value = lib.id;
                    opt.textContent = `${lib.name} (${lib.id})`;
                    if (lib.id === preselectId) opt.selected = true;
                    libSelect.appendChild(opt);
                }
                if (libs.length === 0) {
                    libSelect.innerHTML = '<option value="">No libraries found</option>';
                }
            } catch (err) {
                libSelect.innerHTML = '<option value="">Failed to load libraries</option>';
            }
        }

        $('[name="user_id"]').addEventListener('change', (e) => {
            const userId = e.target.value;
            if (userId) {
                loadLibraries(userId, rule?.abs_library_id || '');
            } else {
                $('[name="abs_library_id"]').innerHTML = '<option value="">Select user first...</option>';
            }
        });

        // If editing or user is pre-selected, load libraries immediately
        const initialUserId = $('[name="user_id"]').value;
        if (initialUserId) {
            loadLibraries(initialUserId, rule?.abs_library_id || '');
        }

        $('#rule-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const form = e.target;
            const data = {
                user_id: form.user_id.value,
                direction: form.direction.value,
                hc_status_id: form.hc_status_id.value ? parseInt(form.hc_status_id.value) : null,
                abs_target_type: form.abs_target_type.value,
                abs_target_name: form.abs_target_name.value.trim(),
                abs_library_id: form.abs_library_id.value.trim(),
                remove_stale: form.remove_stale.checked,
                enabled: form.enabled.checked,
            };

            const submitBtn = form.querySelector('[type="submit"]');
            submitBtn.disabled = true;

            try {
                if (isEdit) {
                    await api('PUT', `/rules/${rule.id}`, data);
                    toast('Rule updated', 'success');
                } else {
                    await api('POST', '/rules', data);
                    toast('Rule created', 'success');
                }
                renderRules($('#content'));
            } catch (err) {
                toast(`Failed to save rule: ${err.message}`, 'error');
                submitBtn.disabled = false;
            }
        });
    }

    function bindRuleActions(users) {
        $$('.rule-edit-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                try {
                    const rules = await api('GET', '/rules');
                    const rule = rules.find((r) => r.id === id);
                    if (rule) showRuleForm(rule, users);
                    else toast('Rule not found', 'error');
                } catch (err) {
                    toast(`Failed to load rule: ${err.message}`, 'error');
                }
            });
        });

        $$('.rule-delete-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                const ok = await confirm('Delete Rule', 'Are you sure you want to delete this sync rule?');
                if (!ok) return;
                try {
                    await api('DELETE', `/rules/${id}`);
                    toast('Rule deleted', 'success');
                    renderRules($('#content'));
                } catch (err) {
                    toast(`Failed to delete rule: ${err.message}`, 'error');
                }
            });
        });
    }

    // ── Mappings View ──────────────────────────────────────────

    async function renderMappings(container) {
        try {
            const [mappings, users] = await Promise.all([
                api('GET', '/mappings'),
                api('GET', '/users'),
            ]);
            usersCache = users;

            const methods = [...new Set(mappings.map((m) => m.match_method))].sort();

            container.innerHTML = `
                <div class="animate-fade-in space-y-6">
                    <!-- Filters -->
                    <div class="flex flex-wrap items-center gap-3">
                        <div>
                            <select id="filter-user" class="text-sm">
                                <option value="">All Users</option>
                                ${users.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <select id="filter-method" class="text-sm">
                                <option value="">All Methods</option>
                                ${methods.map((m) => `<option value="${m}">${m}</option>`).join('')}
                            </select>
                        </div>
                        <p class="text-gray-400 text-sm ml-auto">${mappings.length} mapping${mappings.length !== 1 ? 's' : ''}</p>
                    </div>

                    <!-- Table -->
                    <div class="card p-0 overflow-hidden">
                        <div class="overflow-x-auto custom-scrollbar">
                            <table class="data-table" id="mappings-table">
                                <thead>
                                    <tr>
                                        <th>Title</th>
                                        <th>ABS Item ID</th>
                                        <th>Method</th>
                                        <th>Confidence</th>
                                        <th>User</th>
                                        <th>Created</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody id="mappings-tbody">
                                    ${mappings.length === 0 ? '<tr><td colspan="7" class="text-center text-gray-500 py-8">No book mappings yet. Run a sync to discover matches.</td></tr>' : ''}
                                    ${mappings.map((m) => mappingRow(m, users)).join('')}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;

            // Filter handlers
            const filterUser = $('#filter-user');
            const filterMethod = $('#filter-method');

            const applyFilters = () => {
                const uid = filterUser.value;
                const method = filterMethod.value;
                const filtered = mappings.filter((m) => {
                    if (uid && m.user_id !== uid) return false;
                    if (method && m.match_method !== method) return false;
                    return true;
                });
                $('#mappings-tbody').innerHTML = filtered.length === 0
                    ? '<tr><td colspan="7" class="text-center text-gray-500 py-8">No mappings match filters.</td></tr>'
                    : filtered.map((m) => mappingRow(m, users)).join('');
                bindMappingActions();
            };

            filterUser.addEventListener('change', applyFilters);
            filterMethod.addEventListener('change', applyFilters);
            bindMappingActions();
        } catch (err) {
            container.innerHTML = `<div class="alert alert-error">Failed to load mappings: ${escapeHtml(err.message)}</div>`;
        }
    }

    function mappingRow(m, users) {
        const user = users.find((u) => u.id === m.user_id);
        const conf = m.match_confidence != null ? (m.match_confidence * 100).toFixed(0) + '%' : '-';
        const confClass = m.match_confidence >= 0.9 ? 'text-emerald-400' : m.match_confidence >= 0.7 ? 'text-yellow-400' : 'text-red-400';
        return `
            <tr>
                <td class="text-gray-200 font-medium">${escapeHtml(m.title || `HC Book #${m.hardcover_book_id}`)}</td>
                <td class="text-gray-400 font-mono text-xs">${escapeHtml(m.abs_library_item_id)}</td>
                <td><span class="badge badge-info">${escapeHtml(m.match_method)}</span></td>
                <td class="${confClass} font-medium">${conf}</td>
                <td class="text-gray-400">${escapeHtml(user?.name || m.user_id)}</td>
                <td class="text-gray-500 whitespace-nowrap">${timeAgo(m.created_at)}</td>
                <td>
                    <button class="btn btn-ghost btn-sm mapping-delete-btn text-red-400" data-id="${m.id}" title="Delete mapping">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                    </button>
                </td>
            </tr>
        `;
    }

    function bindMappingActions() {
        $$('.mapping-delete-btn').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const id = btn.dataset.id;
                const ok = await confirm('Delete Mapping', 'Are you sure you want to delete this book mapping? It will be rediscovered on the next sync if the match still exists.');
                if (!ok) return;
                try {
                    await api('DELETE', `/mappings/${id}`);
                    toast('Mapping deleted', 'success');
                    renderMappings($('#content'));
                } catch (err) {
                    toast(`Failed to delete mapping: ${err.message}`, 'error');
                }
            });
        });
    }

    // ── Log View ───────────────────────────────────────────────

    let logPage = 0;
    const LOG_LIMIT = 50;

    async function renderLog(container) {
        logPage = 0;
        try {
            const [users] = await Promise.all([api('GET', '/users')]);
            usersCache = users;

            container.innerHTML = `
                <div class="animate-fade-in space-y-6">
                    <!-- Filters -->
                    <div class="flex flex-wrap items-center gap-3">
                        <div>
                            <select id="log-filter-user" class="text-sm">
                                <option value="">All Users</option>
                                ${users.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('')}
                            </select>
                        </div>
                        <div>
                            <select id="log-filter-action" class="text-sm">
                                <option value="">All Actions</option>
                                <option value="added_to_collection">Added to Collection</option>
                                <option value="removed_from_collection">Removed from Collection</option>
                                <option value="progress_updated">Progress Updated</option>
                                <option value="status_updated">Status Updated</option>
                                <option value="match_found">Match Found</option>
                                <option value="match_failed">Match Failed</option>
                                <option value="error">Error</option>
                            </select>
                        </div>
                        <div>
                            <select id="log-filter-direction" class="text-sm">
                                <option value="">All Directions</option>
                                <option value="hc_to_abs">HC \u2192 ABS</option>
                                <option value="abs_to_hc">ABS \u2192 HC</option>
                            </select>
                        </div>
                        <button id="clear-log-btn" class="btn btn-danger btn-sm ml-auto">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                            Clear Log
                        </button>
                    </div>

                    <!-- Table -->
                    <div class="card p-0 overflow-hidden">
                        <div class="overflow-x-auto custom-scrollbar">
                            <table class="data-table">
                                <thead>
                                    <tr>
                                        <th>Status</th>
                                        <th>User</th>
                                        <th>Action</th>
                                        <th>Direction</th>
                                        <th>Details</th>
                                        <th>Time</th>
                                    </tr>
                                </thead>
                                <tbody id="log-tbody">
                                    <tr><td colspan="6" class="text-center py-8"><div class="skeleton" style="height:2rem;max-width:200px;margin:0 auto"></div></td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Pagination -->
                    <div class="flex items-center justify-between">
                        <button id="log-prev" class="btn btn-secondary btn-sm" disabled>Previous</button>
                        <span id="log-page-info" class="text-sm text-gray-400"></span>
                        <button id="log-next" class="btn btn-secondary btn-sm">Next</button>
                    </div>
                </div>
            `;

            const loadLog = async () => {
                const params = new URLSearchParams();
                params.set('limit', LOG_LIMIT);
                params.set('offset', logPage * LOG_LIMIT);

                const userId = $('#log-filter-user').value;
                const action = $('#log-filter-action').value;
                const direction = $('#log-filter-direction').value;
                if (userId) params.set('user_id', userId);
                if (action) params.set('action', action);
                if (direction) params.set('direction', direction);

                try {
                    const result = await api('GET', `/log?${params}`);
                    const logs = Array.isArray(result) ? result : result?.items || [];
                    const total = result?.total ?? logs.length;

                    const tbody = $('#log-tbody');
                    if (logs.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-gray-500 py-8">No log entries found.</td></tr>';
                    } else {
                        tbody.innerHTML = logs.map((entry) => {
                            const icon = ACTION_ICONS[entry.action] || '\u2139\ufe0f';
                            const user = users.find((u) => u.id === entry.user_id);
                            const details = entry.details ? (typeof entry.details === 'string' ? (() => { try { return JSON.parse(entry.details); } catch { return { raw: entry.details }; } })() : entry.details) : {};
                            return `
                                <tr>
                                    <td><span class="text-lg">${icon}</span></td>
                                    <td class="text-gray-300">${escapeHtml(user?.name || entry.user_id || '-')}</td>
                                    <td><span class="badge badge-${entry.action === 'error' ? 'error' : entry.action === 'match_failed' ? 'warning' : 'info'}">${escapeHtml(entry.action)}</span></td>
                                    <td class="text-gray-400">${entry.direction ? DIRECTION_LABELS[entry.direction] || entry.direction : '-'}</td>
                                    <td class="text-gray-300 max-w-sm truncate" title="${escapeHtml(JSON.stringify(details))}">${escapeHtml(details.title || details.message || details.raw || JSON.stringify(details).slice(0, 100))}</td>
                                    <td class="text-gray-500 whitespace-nowrap">${formatDate(entry.created_at)}</td>
                                </tr>
                            `;
                        }).join('');
                    }

                    // Pagination state
                    $('#log-prev').disabled = logPage === 0;
                    $('#log-next').disabled = logs.length < LOG_LIMIT;
                    $('#log-page-info').textContent = `Page ${logPage + 1}${total ? ` (${total} total)` : ''}`;
                } catch (err) {
                    $('#log-tbody').innerHTML = `<tr><td colspan="6" class="text-center text-red-400 py-8">Failed to load: ${escapeHtml(err.message)}</td></tr>`;
                }
            };

            // Bind events
            $('#log-filter-user').addEventListener('change', () => { logPage = 0; loadLog(); });
            $('#log-filter-action').addEventListener('change', () => { logPage = 0; loadLog(); });
            $('#log-filter-direction').addEventListener('change', () => { logPage = 0; loadLog(); });
            $('#log-prev').addEventListener('click', () => { if (logPage > 0) { logPage--; loadLog(); } });
            $('#log-next').addEventListener('click', () => { logPage++; loadLog(); });

            $('#clear-log-btn').addEventListener('click', async () => {
                const ok = await confirm('Clear Sync Log', 'Are you sure you want to delete all sync log entries? This cannot be undone.');
                if (!ok) return;
                try {
                    await api('DELETE', '/log');
                    toast('Sync log cleared', 'success');
                    logPage = 0;
                    loadLog();
                } catch (err) {
                    toast(`Failed to clear log: ${err.message}`, 'error');
                }
            });

            loadLog();
        } catch (err) {
            container.innerHTML = `<div class="alert alert-error">Failed to load log: ${escapeHtml(err.message)}</div>`;
        }
    }

    // ── Stats View ─────────────────────────────────────────────

    async function renderStats(container) {
        try {
            await loadUsers();
            if (usersCache.length === 0) {
                container.innerHTML = '<div class="animate-fade-in"><p class="text-gray-500 text-sm py-8 text-center">No users configured. Add one first.</p></div>';
                return;
            }

            const userId = usersCache[0].id;

            // User selector + placeholder
            container.innerHTML = `
                <div class="animate-fade-in space-y-6">
                    <div class="flex items-center gap-4">
                        <label class="text-sm text-gray-400">User:</label>
                        <select id="stats-user-select" class="bg-surface-700 border border-surface-600 rounded-lg px-3 py-1.5 text-sm text-white">
                            ${usersCache.map(u => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('')}
                        </select>
                    </div>
                    <div id="stats-content">${loadingState()}</div>
                </div>
            `;

            async function loadStats(uid) {
                const statsDiv = $('#stats-content');
                statsDiv.innerHTML = loadingState();
                try {
                    const [stats, ratings, sessions] = await Promise.all([
                        api('GET', `/stats/${uid}`),
                        api('GET', `/ratings/summary?user_id=${uid}`),
                        api('GET', `/stats/${uid}/sessions`).catch(() => []),
                    ]);

                    const statusEntries = Object.entries(stats.hc_status_counts || {});
                    const totalHcBooks = statusEntries.reduce((s, [, c]) => s + c, 0);
                    const statusColors = {
                        'Want to Read': 'bg-blue-500',
                        'Currently Reading': 'bg-yellow-500',
                        'Read': 'bg-emerald-500',
                        'DNF': 'bg-red-500',
                    };

                    // Ratings distribution chart
                    const dist = ratings.distribution || {};
                    const distEntries = Object.entries(dist).sort((a, b) => parseFloat(a[0]) - parseFloat(b[0]));
                    const maxCount = Math.max(...Object.values(dist), 1);

                    statsDiv.innerHTML = `
                        <!-- Stats Cards -->
                        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                            <div class="stat-card">
                                <p class="text-sm text-gray-400 mb-1">Listening Time</p>
                                <p class="text-2xl font-bold text-white">${stats.listening_time_hours}h</p>
                                <p class="text-xs text-gray-500 mt-1">${Math.round(stats.listening_time_hours / 24)} days</p>
                            </div>
                            <div class="stat-card">
                                <p class="text-sm text-gray-400 mb-1">ABS Finished</p>
                                <p class="text-2xl font-bold text-emerald-400">${stats.abs_books_finished}</p>
                                <p class="text-xs text-gray-500 mt-1">${stats.abs_books_in_progress} in progress</p>
                            </div>
                            <div class="stat-card">
                                <p class="text-sm text-gray-400 mb-1">Mapped Books</p>
                                <p class="text-2xl font-bold text-white">${stats.total_mapped_books}</p>
                                <p class="text-xs text-gray-500 mt-1">HC / ABS pairs</p>
                            </div>
                            <div class="stat-card">
                                <p class="text-sm text-gray-400 mb-1">Avg Rating</p>
                                <p class="text-2xl font-bold text-yellow-400">${ratings.avg != null ? ratings.avg + ' / 5' : '-'}</p>
                                <p class="text-xs text-gray-500 mt-1">${ratings.total || 0} rated</p>
                            </div>
                        </div>

                        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
                            <!-- HC Status Distribution -->
                            <div class="card">
                                <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Hardcover Library</h3>
                                ${totalHcBooks === 0 ? '<p class="text-gray-500 text-sm">No books on Hardcover yet.</p>' : `
                                    <div class="space-y-3">
                                        ${statusEntries.map(([name, count]) => {
                                            const pct = Math.round((count / totalHcBooks) * 100);
                                            const color = statusColors[name] || 'bg-gray-500';
                                            return `
                                                <div>
                                                    <div class="flex justify-between text-sm mb-1">
                                                        <span class="text-gray-300">${escapeHtml(name)}</span>
                                                        <span class="text-gray-400">${count} (${pct}%)</span>
                                                    </div>
                                                    <div class="w-full bg-surface-700 rounded-full h-2.5">
                                                        <div class="${color} h-2.5 rounded-full transition-all duration-500" style="width: ${pct}%"></div>
                                                    </div>
                                                </div>
                                            `;
                                        }).join('')}
                                        <p class="text-xs text-gray-500 mt-2">${totalHcBooks} total books</p>
                                    </div>
                                `}
                            </div>

                            <!-- Ratings Distribution -->
                            <div class="card">
                                <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Ratings Distribution</h3>
                                ${distEntries.length === 0 ? '<p class="text-gray-500 text-sm">No ratings yet.</p>' : `
                                    <div class="space-y-2">
                                        ${distEntries.map(([star, count]) => {
                                            const pct = Math.round((count / maxCount) * 100);
                                            return `
                                                <div class="flex items-center gap-3">
                                                    <span class="text-sm text-yellow-400 w-10 text-right">${star}</span>
                                                    <div class="flex-1 bg-surface-700 rounded-full h-2">
                                                        <div class="bg-yellow-500 h-2 rounded-full transition-all duration-500" style="width: ${pct}%"></div>
                                                    </div>
                                                    <span class="text-xs text-gray-400 w-6">${count}</span>
                                                </div>
                                            `;
                                        }).join('')}
                                    </div>
                                `}
                            </div>
                        </div>

                        <!-- Recent Listening Sessions -->
                        <div class="card">
                            <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Recent Listening Sessions</h3>
                            ${!sessions || sessions.length === 0 ? '<p class="text-gray-500 text-sm">No listening sessions found.</p>' : `
                                <div class="overflow-x-auto custom-scrollbar">
                                    <table class="data-table">
                                        <thead>
                                            <tr>
                                                <th>Book</th>
                                                <th>Duration</th>
                                                <th>Date</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            ${sessions.slice(0, 20).map(s => {
                                                const title = s.displayTitle || s.mediaMetadata?.title || 'Unknown';
                                                const dur = s.timeListening || 0;
                                                const mins = Math.round(dur / 60);
                                                const hrs = Math.floor(mins / 60);
                                                const durStr = hrs > 0 ? hrs + 'h ' + (mins % 60) + 'm' : mins + 'm';
                                                const date = s.updatedAt ? new Date(s.updatedAt).toLocaleDateString() : '-';
                                                return '<tr><td class="text-gray-300 max-w-xs truncate">' + escapeHtml(title) + '</td><td class="text-gray-400">' + durStr + '</td><td class="text-gray-500">' + date + '</td></tr>';
                                            }).join('')}
                                        </tbody>
                                    </table>
                                </div>
                            `}
                        </div>
                    `;
                } catch (err) {
                    statsDiv.innerHTML = '<div class="alert alert-error">Failed to load stats: ' + escapeHtml(err.message) + '</div>';
                }
            }

            loadStats(userId);
            $('#stats-user-select').addEventListener('change', (e) => loadStats(e.target.value));
        } catch (err) {
            container.innerHTML = '<div class="alert alert-error">Failed to load stats: ' + escapeHtml(err.message) + '</div>';
        }
    }

    // ── Settings View ──────────────────────────────────────────

    async function renderSettings(container) {
        try {
            const settings = await api('GET', '/settings');

            container.innerHTML = `
                <div class="animate-fade-in space-y-6 max-w-2xl">
                    <div class="card">
                        <h3 class="text-white font-semibold mb-4">Sync Settings</h3>
                        <form id="settings-form" class="space-y-5">
                            <!-- Sync Interval (read-only) -->
                            <div>
                                <label class="block text-sm text-gray-400 mb-1">Sync Interval</label>
                                <div class="flex items-center gap-3">
                                    <span class="text-gray-200 font-mono text-sm bg-surface-800 border border-surface-700 rounded-lg px-3 py-2">${escapeHtml(settings.sync_interval || '*/15 * * * *')}</span>
                                    <span class="text-xs text-gray-500">(cron expression)</span>
                                </div>
                                <p class="text-xs text-gray-500 mt-1">Requires container restart to change. Edit the crontab file or SYNC_INTERVAL env var.</p>
                            </div>

                            <!-- Dry Run -->
                            <div class="flex items-center gap-3">
                                <input type="checkbox" name="dry_run" id="settings-dry-run" ${settings.dry_run ? 'checked' : ''}>
                                <div>
                                    <label for="settings-dry-run" class="text-sm text-gray-300 font-medium">Dry Run Mode</label>
                                    <p class="text-xs text-gray-500">Log all changes but don't execute writes to Hardcover or Audiobookshelf</p>
                                </div>
                            </div>

                            <!-- Log Retention -->
                            <div>
                                <label class="block text-sm text-gray-400 mb-1">Log Retention (days)</label>
                                <input type="number" name="log_retention_days" value="${settings.log_retention_days ?? 30}" min="1" max="365" class="max-w-xs">
                                <p class="text-xs text-gray-500 mt-1">Sync log entries older than this are automatically deleted</p>
                            </div>

                            <!-- Fuzzy Match Threshold -->
                            <div>
                                <label class="block text-sm text-gray-400 mb-1">Fuzzy Match Threshold</label>
                                <input type="number" name="fuzzy_threshold" value="${settings.fuzzy_threshold ?? 0.85}" min="0" max="1" step="0.01" class="max-w-xs">
                                <p class="text-xs text-gray-500 mt-1">Minimum confidence score (0-1) for title/author fuzzy matching. Higher = stricter.</p>
                            </div>

                            <!-- Sync Ratings to ABS Tags -->
                            <div class="flex items-center gap-3">
                                <input type="checkbox" name="sync_ratings_to_abs_tags" id="settings-ratings-tags" ${settings.sync_ratings_to_abs_tags ? 'checked' : ''}>
                                <div>
                                    <label for="settings-ratings-tags" class="text-sm text-gray-300 font-medium">Sync Ratings to ABS Tags</label>
                                    <p class="text-xs text-gray-500">Write Hardcover ratings as tags (e.g. "rating:4.5") on ABS library items</p>
                                </div>
                            </div>

                            <div class="pt-2">
                                <button type="submit" class="btn btn-primary">Save Settings</button>
                            </div>
                        </form>
                    </div>

                    <!-- Import / Export -->
                    <div class="card">
                        <h3 class="text-white font-semibold mb-4">Configuration Backup</h3>
                        <div class="space-y-4">
                            <div>
                                <p class="text-sm text-gray-400 mb-2">Export your users, rules, and settings as a JSON backup file. Tokens are redacted.</p>
                                <button id="export-btn" class="btn btn-secondary">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
                                    Export Config
                                </button>
                            </div>
                            <div class="border-t border-surface-700 pt-4">
                                <p class="text-sm text-gray-400 mb-2">Import a previously exported configuration file. This will overwrite current settings.</p>
                                <div class="flex items-center gap-3">
                                    <input type="file" id="import-file" accept=".json" class="text-sm">
                                    <button id="import-btn" class="btn btn-secondary" disabled>
                                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
                                        Import Config
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;

            // Save settings
            $('#settings-form').addEventListener('submit', async (e) => {
                e.preventDefault();
                const form = e.target;
                const data = {
                    dry_run: form.dry_run.checked,
                    log_retention_days: parseInt(form.log_retention_days.value),
                    fuzzy_threshold: parseFloat(form.fuzzy_threshold.value),
                    sync_ratings_to_abs_tags: form.sync_ratings_to_abs_tags.checked,
                };

                const btn = form.querySelector('[type="submit"]');
                btn.disabled = true;
                btn.textContent = 'Saving...';

                try {
                    await api('PUT', '/settings', data);
                    toast('Settings saved', 'success');
                } catch (err) {
                    toast(`Failed to save settings: ${err.message}`, 'error');
                } finally {
                    btn.disabled = false;
                    btn.textContent = 'Save Settings';
                }
            });

            // Export
            $('#export-btn').addEventListener('click', async () => {
                try {
                    const resp = await fetch('/api/export');
                    if (!resp.ok) throw new Error('Export failed');
                    const blob = await resp.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `earmark-config-${new Date().toISOString().slice(0, 10)}.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                    toast('Config exported', 'success');
                } catch (err) {
                    toast(`Export failed: ${err.message}`, 'error');
                }
            });

            // Import
            const importFile = $('#import-file');
            const importBtn = $('#import-btn');

            importFile.addEventListener('change', () => {
                importBtn.disabled = !importFile.files.length;
            });

            importBtn.addEventListener('click', async () => {
                const file = importFile.files[0];
                if (!file) return;

                const ok = await confirm('Import Configuration', 'This will overwrite your current users, rules, and settings. Are you sure?');
                if (!ok) return;

                importBtn.disabled = true;
                importBtn.textContent = 'Importing...';

                try {
                    const formData = new FormData();
                    formData.append('file', file);
                    await api('POST', '/import', formData);
                    toast('Config imported successfully', 'success');
                    renderSettings($('#content'));
                } catch (err) {
                    toast(`Import failed: ${err.message}`, 'error');
                } finally {
                    importBtn.disabled = false;
                    importBtn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg> Import Config';
                }
            });
        } catch (err) {
            container.innerHTML = `<div class="alert alert-error">Failed to load settings: ${escapeHtml(err.message)}</div>`;
        }
    }

    // ── Mobile Menu ────────────────────────────────────────────

    function setupMobileMenu() {
        const btn = $('#mobile-menu-btn');
        const sidebar = $('#sidebar');

        if (btn) {
            btn.addEventListener('click', () => {
                sidebar.classList.toggle('open');
            });

            // Close sidebar when clicking a nav link on mobile
            $$('.nav-link').forEach((link) => {
                link.addEventListener('click', () => {
                    sidebar.classList.remove('open');
                });
            });

            // Close sidebar when clicking outside
            document.addEventListener('click', (e) => {
                if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                    sidebar.classList.remove('open');
                }
            });
        }
    }

    // ── Init ───────────────────────────────────────────────────

    function init() {
        // Remove preload class after a tick to allow transitions
        document.body.classList.add('preload');
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                document.body.classList.remove('preload');
            });
        });

        // Setup routing
        window.addEventListener('hashchange', navigate);
        navigate();

        // Setup mobile menu
        setupMobileMenu();

        // Health check on load and every 30s
        checkHealth();
        setInterval(checkHealth, 30000);

        // Preload users cache
        loadUsers();
    }

    // Start the app
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
