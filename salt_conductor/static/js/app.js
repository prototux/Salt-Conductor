/* Salt Conductor - shared front-end helpers */
(function () {
    'use strict';

    const SW = (window.SW = {});
    const html = document.documentElement;
    const csrf = document.querySelector('meta[name=csrf-token]')?.content || '';

    // ------------------------------------------------------------------ utils
    SW.esc = function (s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    };

    SW.timeago = function (value) {
        if (!value) return 'never';
        const d = value instanceof Date ? value : new Date(value);
        const s = Math.floor((Date.now() - d.getTime()) / 1000);
        if (s < 60) return s + 's ago';
        if (s < 3600) return Math.floor(s / 60) + 'm ago';
        if (s < 86400) return Math.floor(s / 3600) + 'h ago';
        return Math.floor(s / 86400) + 'd ago';
    };

    SW.jidTime = function (jid) {
        const m = String(jid).match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/);
        return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : '';
    };

    SW.debounce = function (fn, ms) {
        let t;
        return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
    };

    // --------------------------------------------------------------- network
    SW.api = async function (url, opts = {}) {
        const init = {
            method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'),
            headers: { 'Accept': 'application/json', 'X-CSRF-Token': csrf },
            credentials: 'same-origin',
        };
        if (opts.body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(opts.body);
        }
        let resp;
        try {
            resp = await fetch(url, init);
        } catch (e) {
            if (!opts.quiet) SW.toast('Network error: ' + e.message, 'danger');
            throw e;
        }
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; }
        if (!resp.ok) {
            if (resp.status === 401 && (data?.relogin || data?.error === 'Not authenticated')) {
                window.location = '/login?next=' + encodeURIComponent(location.pathname + location.search);
            }
            const msg = data?.error || `HTTP ${resp.status}`;
            if (!opts.quiet) SW.toast(msg, 'danger');
            const err = new Error(msg);
            err.status = resp.status;
            err.data = data;
            throw err;
        }
        return data;
    };

    // ------------------------------------------------------------ feedback
    SW.toast = function (message, type = 'primary', delay = 4500) {
        const box = document.getElementById('toasts');
        if (!box) return alert(message);
        const icon = { primary: 'circle-check', success: 'circle-check', danger: 'alert-circle', warning: 'alert-triangle', info: 'info-circle' }[type] || 'info-circle';
        const el = document.createElement('div');
        el.className = `toast align-items-center border-0 text-bg-${type}`;
        el.role = 'alert';
        el.innerHTML = `<div class="d-flex"><div class="toast-body"><i class="ti ti-${icon} me-1"></i> ${SW.esc(message)}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
        box.appendChild(el);
        const t = new bootstrap.Toast(el, { delay });
        el.addEventListener('hidden.bs.toast', () => el.remove());
        t.show();
    };

    SW.confirm = function (title, body, okLabel = 'Confirm', okClass = 'btn-danger') {
        return new Promise(resolve => {
            const modalEl = document.getElementById('confirm-modal');
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            modalEl.querySelector('#confirm-title').textContent = title;
            modalEl.querySelector('#confirm-body').textContent = body || '';
            const ok = modalEl.querySelector('#confirm-ok');
            ok.textContent = okLabel;
            ok.className = 'btn ' + okClass;
            let result = false;
            const onOk = () => { result = true; modal.hide(); };
            ok.addEventListener('click', onOk, { once: true });
            modalEl.addEventListener('hidden.bs.modal', () => { ok.removeEventListener('click', onOk); resolve(result); }, { once: true });
            modal.show();
        });
    };

    SW.busy = function (btn, on) {
        if (!btn) return;
        if (on) {
            btn.dataset.html = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> ' + (btn.dataset.busy || 'Working...');
        } else {
            btn.disabled = false;
            if (btn.dataset.html) btn.innerHTML = btn.dataset.html;
        }
    };

    SW.loading = function (el, on) { el?.classList.toggle('card-loading', !!on); };

    // ------------------------------------------------------------ renderers
    SW.jsonTree = function (data, opts = {}) {
        const root = document.createElement('div');
        root.className = 'json-tree';
        const depthOpen = opts.open ?? 2;

        function scalar(v) {
            if (v === null) return '<span class="jt-null">null</span>';
            if (typeof v === 'boolean') return `<span class="jt-bool">${v}</span>`;
            if (typeof v === 'number') return `<span class="jt-num">${v}</span>`;
            if (v === '**********') return '<span class="jt-masked">••••••••</span>';
            const s = String(v);
            if (s.includes('\n')) return `<span class="jt-str">"${SW.esc(s.length > 3000 ? s.slice(0, 3000) + '…' : s).replace(/\n/g, '<br>')}"</span>`;
            return `<span class="jt-str">"${SW.esc(s)}"</span>`;
        }

        function build(value, depth) {
            const ul = document.createElement('ul');
            const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
            if (!Array.isArray(value) && opts.sort !== false) entries.sort((a, b) => String(a[0]).localeCompare(String(b[0])));
            for (const [k, v] of entries) {
                const li = document.createElement('li');
                const keyHtml = Array.isArray(value) ? `<span class="text-muted">-</span> ` : `<span class="jt-key">${SW.esc(k)}</span>: `;
                if (v !== null && typeof v === 'object' && Object.keys(v).length) {
                    const n = Object.keys(v).length;
                    li.innerHTML = `<span class="jt-toggle">▾</span>${keyHtml}<span class="jt-summary">${Array.isArray(v) ? `[${n}]` : `{${n}}`}</span>`;
                    li.appendChild(build(v, depth + 1));
                    if (depth >= depthOpen) li.classList.add('jt-collapsed');
                } else if (v !== null && typeof v === 'object') {
                    li.innerHTML = `<span class="jt-toggle"></span>${keyHtml}<span class="jt-summary">${Array.isArray(v) ? '[]' : '{}'}</span>`;
                } else {
                    li.innerHTML = `<span class="jt-toggle"></span>${keyHtml}${scalar(v)}`;
                }
                ul.appendChild(li);
            }
            return ul;
        }

        if (data !== null && typeof data === 'object') {
            root.appendChild(build(data, 0));
        } else {
            root.innerHTML = scalar(data);
        }
        root.addEventListener('click', e => {
            const t = e.target.closest('.jt-toggle, .jt-summary');
            if (!t) return;
            const li = t.closest('li');
            li.classList.toggle('jt-collapsed');
            const tog = li.querySelector(':scope > .jt-toggle');
            if (tog && tog.textContent) tog.textContent = li.classList.contains('jt-collapsed') ? '▸' : '▾';
        });
        root.querySelectorAll('li.jt-collapsed > .jt-toggle').forEach(t => { if (t.textContent) t.textContent = '▸'; });
        return root;
    };

    SW.renderDiff = function (text) {
        const pre = document.createElement('pre');
        pre.className = 'diff';
        pre.innerHTML = String(text || '').split('\n').map(line => {
            let cls = '';
            if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) cls = 'd-file';
            else if (line.startsWith('@@')) cls = 'd-hunk';
            else if (line.startsWith('+')) cls = 'd-add';
            else if (line.startsWith('-')) cls = 'd-del';
            return `<span class="${cls}">${SW.esc(line) || ' '}</span>`;
        }).join('');
        return pre;
    };

    SW.isStateReturn = function (v) {
        if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
        const keys = Object.keys(v);
        return keys.length > 0 && keys[0].includes('_|-') && typeof v[keys[0]] === 'object' && 'result' in v[keys[0]];
    };

    SW.stateSummary = function (ret) {
        const s = { total: 0, ok: 0, changed: 0, failed: 0, pending: 0, duration: 0 };
        for (const item of Object.values(ret)) {
            s.total++;
            s.duration += parseFloat(item.duration || 0);
            if (item.result === false) s.failed++;
            else if (item.result === null) s.pending++;
            else if (item.changes && Object.keys(item.changes).length) s.changed++;
            else s.ok++;
        }
        s.duration = (s.duration / 1000).toFixed(2);
        return s;
    };

    SW.summaryBadges = function (s) {
        return `<span class="badge badge-soft-primary">${s.ok} ok</span>
            ${s.changed ? `<span class="badge badge-soft-secondary">${s.changed} changed</span>` : ''}
            ${s.pending ? `<span class="badge badge-soft-warning">${s.pending} pending</span>` : ''}
            ${s.failed ? `<span class="badge badge-soft-danger">${s.failed} failed</span>` : ''}
            <span class="text-muted fs-xs ms-1">${s.total} states &middot; ${s.duration}s</span>`;
    };

    SW.renderHighstate = function (ret, opts = {}) {
        const wrap = document.createElement('div');
        const items = Object.entries(ret).map(([key, item]) => {
            const [mod, id, name, fn] = key.split('_|-');
            return { key, mod, id, name, fn, ...item };
        }).sort((a, b) => (a.__run_num__ ?? 0) - (b.__run_num__ ?? 0));
        const onlyChanges = opts.onlyChanges;
        for (const it of items) {
            const hasChanges = it.changes && Object.keys(it.changes).length;
            const cls = it.result === false ? 'failed' : it.result === null ? 'pending' : hasChanges ? 'changed' : '';
            if (onlyChanges && !cls) continue;
            const icon = it.result === false ? 'circle-x text-danger' : it.result === null ? 'clock text-warning' : hasChanges ? 'refresh text-secondary' : 'circle-check text-primary';
            const div = document.createElement('div');
            div.className = `state-item ${cls}`;
            if (it.result === false) div.classList.add('open');
            let changesHtml = '';
            if (hasChanges) {
                const ch = it.changes;
                if (typeof ch.diff === 'string') {
                    changesHtml = `<dt>Changes</dt><dd>${SW.renderDiff(ch.diff).outerHTML}</dd>`;
                    const rest = Object.assign({}, ch); delete rest.diff;
                    if (Object.keys(rest).length) changesHtml += `<dd><pre class="output">${SW.esc(JSON.stringify(rest, null, 2))}</pre></dd>`;
                } else {
                    changesHtml = `<dt>Changes</dt><dd><pre class="output">${SW.esc(JSON.stringify(ch, null, 2))}</pre></dd>`;
                }
            }
            div.innerHTML = `
                <div class="si-head">
                    <i class="ti ti-${icon}"></i>
                    <span class="si-id">${SW.esc(it.id)}</span>
                    <span class="badge badge-soft-dark font-monospace">${SW.esc(it.mod)}.${SW.esc(it.fn)}</span>
                    ${it.name && it.name !== it.id ? `<span class="text-muted text-truncate fs-xs">${SW.esc(it.name)}</span>` : ''}
                    <span class="ms-auto text-muted fs-xs">${it.duration ? (parseFloat(it.duration)).toFixed(1) + ' ms' : ''}</span>
                </div>
                <div class="si-body"><dl class="mb-0">
                    <dt>Comment</dt><dd><pre class="output">${SW.esc(Array.isArray(it.comment) ? it.comment.join('\n') : it.comment)}</pre></dd>
                    ${changesHtml}
                    ${it.__sls__ ? `<dt>SLS</dt><dd class="font-monospace">${SW.esc(it.__sls__)}</dd>` : ''}
                    ${it.start_time ? `<dt>Started</dt><dd>${SW.esc(it.start_time)}</dd>` : ''}
                </dl></div>`;
            div.querySelector('.si-head').addEventListener('click', () => div.classList.toggle('open'));
            wrap.appendChild(div);
        }
        if (!wrap.children.length) wrap.innerHTML = '<div class="text-muted fs-xs">No changes.</div>';
        return wrap;
    };

    SW.renderValue = function (value, opts = {}) {
        if (SW.isStateReturn(value)) return SW.renderHighstate(value, opts);
        if (Array.isArray(value) && value.length && value.every(v => typeof v === 'string') && opts.stateFun) {
            const d = document.createElement('div');
            d.innerHTML = `<div class="alert alert-danger mb-0"><strong>State compilation failed</strong><pre class="output mt-2 bg-transparent border-0 p-0">${SW.esc(value.join('\n'))}</pre></div>`;
            return d;
        }
        if (typeof value === 'string') {
            const pre = document.createElement('pre');
            pre.className = 'output';
            pre.textContent = value;
            return pre;
        }
        return SW.jsonTree(value, opts);
    };

    /* result: {minion: return} ; opts.fullReturn when values are {ret, retcode} */
    SW.renderResults = function (container, result, opts = {}) {
        container.innerHTML = '';
        if (result === null || result === undefined) {
            container.innerHTML = '<div class="empty-state"><i class="ti ti-inbox"></i>No data returned</div>';
            return;
        }
        if (typeof result !== 'object' || Array.isArray(result) || opts.single) {
            container.appendChild(SW.renderValue(result, opts));
            return;
        }
        const minions = Object.keys(result).sort();
        if (!minions.length) {
            container.innerHTML = '<div class="empty-state"><i class="ti ti-server-off"></i>No minion returned. Check the target or the minions\' status.</div>';
            return;
        }
        for (const m of minions) {
            let value = result[m];
            let retcode = null;
            if (opts.fullReturn && value && typeof value === 'object' && 'ret' in value) {
                retcode = value.retcode;
                value = value.ret;
            }
            const card = document.createElement('div');
            card.className = 'minion-result';
            let badges = '';
            let failed = retcode !== null && retcode !== 0;
            if (SW.isStateReturn(value)) {
                const s = SW.stateSummary(value);
                failed = failed || s.failed > 0;
                badges = SW.summaryBadges(s);
            } else if (typeof value === 'string' && /^(Minion did not return|No minions matched)/.test(value)) {
                failed = true;
            } else if (value === false) {
                failed = true;
            }
            if (retcode !== null) badges += ` <span class="badge ${retcode === 0 ? 'badge-soft-primary' : 'badge-soft-danger'}">retcode ${retcode}</span>`;
            card.innerHTML = `
                <div class="mr-head">
                    <span class="status-dot ${failed ? 'down' : 'up'}"></span>
                    <a class="fw-semibold" href="/minions/${encodeURIComponent(m)}" onclick="event.stopPropagation()">${SW.esc(m)}</a>
                    <span class="ms-2 d-flex flex-wrap gap-1 align-items-center">${badges}</span>
                    <button class="btn btn-sm btn-light ms-auto copy-btn" title="Copy JSON"><i class="ti ti-copy"></i></button>
                    <i class="ti ti-chevron-down text-muted"></i>
                </div>
                <div class="mr-body"></div>`;
            card.querySelector('.mr-body').appendChild(SW.renderValue(value, opts));
            card.querySelector('.mr-head').addEventListener('click', () => card.classList.toggle('collapsed'));
            card.querySelector('.copy-btn').addEventListener('click', e => {
                e.stopPropagation();
                navigator.clipboard?.writeText(JSON.stringify(value, null, 2));
                SW.toast('Copied to clipboard', 'primary', 1500);
            });
            if (minions.length > 8 && !failed) card.classList.add('collapsed');
            container.appendChild(card);
        }
    };

    // ------------------------------------------------------------- layout
    function saveLayout(patch) {
        let c = {};
        try { c = JSON.parse(localStorage.getItem('conductor.layout') || '{}'); } catch (e) { /* ignore */ }
        Object.assign(c, patch);
        localStorage.setItem('conductor.layout', JSON.stringify(c));
    }

    document.querySelector('.sidenav-toggle-button')?.addEventListener('click', () => {
        const size = html.getAttribute('data-sidenav-size');
        if (size === 'offcanvas') {
            html.classList.toggle('sidebar-enable');
            if (html.classList.contains('sidebar-enable')) {
                const bd = document.createElement('div');
                bd.className = 'offcanvas-backdrop-custom';
                bd.addEventListener('click', () => { html.classList.remove('sidebar-enable'); bd.remove(); });
                document.body.appendChild(bd);
            }
            return;
        }
        const next = size === 'condensed' ? 'default' : 'condensed';
        html.setAttribute('data-sidenav-size', next);
        saveLayout({ size: next });
    });
    document.querySelector('.button-close-offcanvas')?.addEventListener('click', () => {
        html.classList.remove('sidebar-enable');
        document.querySelector('.offcanvas-backdrop-custom')?.remove();
    });
    window.addEventListener('resize', SW.debounce(() => {
        if (window.innerWidth <= 767) html.setAttribute('data-sidenav-size', 'offcanvas');
        else if (html.getAttribute('data-sidenav-size') === 'offcanvas') html.setAttribute('data-sidenav-size', 'default');
    }, 150));

    document.getElementById('theme-toggle')?.addEventListener('click', () => {
        const next = html.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-bs-theme', next);
        saveLayout({ theme: next });
        document.dispatchEvent(new CustomEvent('sw:theme', { detail: next }));
    });

    document.querySelectorAll('[data-layout]').forEach(a => a.addEventListener('click', e => {
        e.preventDefault();
        const key = a.dataset.layout, value = a.dataset.value;
        html.setAttribute(key === 'menu' ? 'data-menu-color' : 'data-topbar-color', value);
        saveLayout({ [key]: value });
    }));

    document.querySelector('[data-toggle=fullscreen]')?.addEventListener('click', () => {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
        else document.exitFullscreen?.();
    });

    // card actions: collapse / close
    document.addEventListener('click', e => {
        const btn = e.target.closest('.card-action-item[data-action]');
        if (!btn) return;
        const card = btn.closest('.card');
        if (btn.dataset.action === 'collapse') card.classList.toggle('card-collapsed');
        if (btn.dataset.action === 'close') card.remove();
    });

    // forms / buttons with data-confirm
    document.addEventListener('submit', async e => {
        const form = e.target;
        if (!form.dataset.confirm || form.dataset.confirmed) return;
        e.preventDefault();
        if (await SW.confirm(form.dataset.confirmTitle || 'Are you sure?', form.dataset.confirm, form.dataset.confirmOk || 'Confirm')) {
            form.dataset.confirmed = '1';
            form.requestSubmit ? form.requestSubmit(e.submitter) : form.submit();
        }
    });

    // quick client-side table filter: <input data-filter-table="#tbl">
    document.querySelectorAll('[data-filter-table]').forEach(input => {
        const table = document.querySelector(input.dataset.filterTable);
        input.addEventListener('input', () => {
            const q = input.value.toLowerCase();
            table?.querySelectorAll('tbody tr').forEach(tr => {
                tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
        });
    });

    // check-all helper: <input type=checkbox data-check-all=".row-check">
    document.addEventListener('change', e => {
        const all = e.target.closest('[data-check-all]');
        if (!all) return;
        document.querySelectorAll(all.dataset.checkAll).forEach(cb => {
            if (cb.closest('tr')?.style.display !== 'none') cb.checked = all.checked;
        });
    });

    // relative times: <time data-ago="iso">
    SW.refreshTimes = function () {
        document.querySelectorAll('[data-ago]').forEach(el => { el.textContent = SW.timeago(el.dataset.ago); });
    };
    setInterval(SW.refreshTimes, 30000);

    // tooltips
    document.querySelectorAll('[data-bs-toggle=tooltip]').forEach(el => new bootstrap.Tooltip(el));
})();
