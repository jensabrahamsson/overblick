/**
 * Wizard step initialization — runs after every htmx content swap.
 *
 * htmx hx-boost replaces the <body> via innerHTML which does NOT execute
 * inline <script> tags, and inline event handlers (onclick, onchange) may
 * not fire reliably. CSP script-src 'self' also blocks inline handlers.
 * This external file binds ALL event listeners via JS to be CSP-safe.
 */

/* ── Step 3 + 4: Unified toggle ────────────────────────── */

function toggleSection(idOrName, show) {
    // Step 3 pattern: direct element ID (e.g. 'local-config')
    var el = document.getElementById(idOrName);

    if (el && el.classList.contains('settings-config-body')) {
        el.style.display = show ? 'block' : 'none';
        return;
    }

    // Step 4 pattern: name prefix → name-body element with .open class
    var body = el || document.getElementById(idOrName + '-body');
    if (!body) return;

    var toggle = document.getElementById(idOrName + '-toggle');
    var shouldOpen = show !== undefined ? show : !body.classList.contains('open');
    if (shouldOpen) {
        body.classList.add('open');
        if (toggle) toggle.checked = true;
    } else {
        body.classList.remove('open');
        if (toggle) toggle.checked = false;
    }
}

function updateLocalPort(type) {
    var portInput = document.getElementById('local_port');
    if (!portInput) return;
    if (type === 'lmstudio' && portInput.value === '11434') {
        portInput.value = '1234';
    } else if (type === 'ollama' && portInput.value === '1234') {
        portInput.value = '11434';
    }
}

/* ── Step 5: Security ──────────────────────────────────── */

function toggleNetwork(forceState) {
    var toggle = document.getElementById('network-toggle');
    var passwordSection = document.getElementById('password-section');
    var passwordInput = document.getElementById('password');
    var passwordConfirm = document.getElementById('password_confirm');
    if (!toggle || !passwordSection) return;

    var isOn = forceState !== undefined ? forceState : toggle.checked;
    toggle.checked = isOn;

    if (isOn) {
        passwordSection.style.display = '';
        if (passwordInput) passwordInput.required = true;
        if (passwordConfirm) passwordConfirm.required = true;
    } else {
        passwordSection.style.display = 'none';
        if (passwordInput) passwordInput.required = false;
        if (passwordConfirm) passwordConfirm.required = false;
    }
}

/* ── Step 6: Use Cases ─────────────────────────────────── */

var _ucUpdateCounter = null;

function initUseCaseCounter() {
    var checkboxes = document.querySelectorAll('.use-case-card input[type="checkbox"]');
    var countEl = document.getElementById('selection-count');
    var labelEl = document.getElementById('selection-label');
    if (!countEl || !labelEl || checkboxes.length === 0) return;

    _ucUpdateCounter = function() {
        var count = 0;
        checkboxes.forEach(function(cb) { if (cb.checked) count++; });
        countEl.textContent = count;
        labelEl.textContent = 'use case' + (count !== 1 ? 's' : '') + ' selected';
    };
    checkboxes.forEach(function(cb) {
        cb.addEventListener('change', _ucUpdateCounter);
    });
    _ucUpdateCounter();
}

/* ── Step 8: Review ────────────────────────────────────── */

function initProvisionForm() {
    var form = document.getElementById('provision-form');
    var btn = document.getElementById('provision-btn');
    if (!form || !btn || form._initDone) return;
    form._initDone = true;
    form.addEventListener('submit', function() {
        btn.disabled = true;
        btn.textContent = 'Saving...';
    });
}

/* ── Helper: bind listener once ───────────────────────── */

function _bindOnce(el, event, handler) {
    var key = '_wb_' + event;
    if (!el || el[key]) return;
    el[key] = true;
    el.addEventListener(event, handler);
}

/* ── Step 3: LLM backend toggles + range sliders ──────── */

function initLLMBackends() {
    // Backend enable/disable checkboxes
    var backends = [
        { name: 'local_enabled', target: 'local-config', key: 'local', label: 'Local' },
        { name: 'remote_enabled', target: 'remote-config', key: 'remote', label: 'Remote' },
        { name: 'deepseek_enabled', target: 'deepseek-config', key: 'deepseek', label: 'Deepseek' },
    ];
    backends.forEach(function(b) {
        var cb = document.querySelector('input[name="' + b.name + '"]');
        if (cb) {
            _bindOnce(cb, 'change', function() {
                toggleSection(b.target, this.checked);
                _updatePreferenceList();
            });
        }
    });

    // Local type select → updateLocalPort
    var localType = document.getElementById('local_type');
    if (localType) {
        _bindOnce(localType, 'change', function() {
            updateLocalPort(this.value);
        });
    }

    // Temperature range → update display span
    var tempRange = document.getElementById('default_temperature');
    var tempValue = document.getElementById('temp-value');
    if (tempRange && tempValue) {
        _bindOnce(tempRange, 'input', function() {
            tempValue.textContent = this.value;
        });
    }

    // Preference list arrow buttons
    _initPreferenceButtons();
}

/* ── Preference list management ────────────────────── */

var _BACKEND_LABELS = {
    local: 'Local',
    remote: 'Remote',
    deepseek: 'Deepseek',
};

function _getEnabledBackends() {
    var enabled = [];
    var checks = [
        { name: 'local_enabled', key: 'local' },
        { name: 'remote_enabled', key: 'remote' },
        { name: 'deepseek_enabled', key: 'deepseek' },
    ];
    checks.forEach(function(c) {
        var cb = document.querySelector('input[name="' + c.name + '"]');
        if (cb && cb.checked) enabled.push(c.key);
    });
    return enabled;
}

function _createPreferenceItem(key, idx, total) {
    var li = document.createElement('li');
    li.className = 'preference-item';
    li.setAttribute('data-backend', key);

    var rank = document.createElement('span');
    rank.className = 'preference-rank';
    rank.textContent = String(idx + 1);
    li.appendChild(rank);

    var name = document.createElement('span');
    name.className = 'preference-name';
    name.textContent = _BACKEND_LABELS[key] || key;
    li.appendChild(name);

    var upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'btn-icon pref-up';
    upBtn.setAttribute('aria-label', 'Move up');
    upBtn.textContent = '\u25B2';
    if (idx === 0) upBtn.disabled = true;
    li.appendChild(upBtn);

    var downBtn = document.createElement('button');
    downBtn.type = 'button';
    downBtn.className = 'btn-icon pref-down';
    downBtn.setAttribute('aria-label', 'Move down');
    downBtn.textContent = '\u25BC';
    if (idx === total - 1) downBtn.disabled = true;
    li.appendChild(downBtn);

    return li;
}

function _updatePreferenceList() {
    var list = document.getElementById('preference-list');
    var hidden = document.getElementById('backend_preference');
    if (!list || !hidden) return;

    var enabled = _getEnabledBackends();

    // Get current order from existing list items
    var currentOrder = [];
    list.querySelectorAll('.preference-item[data-backend]').forEach(function(li) {
        currentOrder.push(li.getAttribute('data-backend'));
    });

    // Keep enabled items in their current order, add new ones at end
    var newOrder = [];
    currentOrder.forEach(function(key) {
        if (enabled.indexOf(key) !== -1) newOrder.push(key);
    });
    enabled.forEach(function(key) {
        if (newOrder.indexOf(key) === -1) newOrder.push(key);
    });

    // Rebuild list using safe DOM methods
    while (list.firstChild) list.removeChild(list.firstChild);

    if (newOrder.length === 0) {
        var emptyLi = document.createElement('li');
        emptyLi.className = 'preference-item preference-item--empty';
        var hint = document.createElement('span');
        hint.className = 'form-hint';
        hint.textContent = 'Enable at least one backend above';
        emptyLi.appendChild(hint);
        list.appendChild(emptyLi);
    } else {
        newOrder.forEach(function(key, idx) {
            list.appendChild(_createPreferenceItem(key, idx, newOrder.length));
        });
    }

    hidden.value = newOrder.join(',');
    _initPreferenceButtons();
}

function _initPreferenceButtons() {
    var list = document.getElementById('preference-list');
    if (!list) return;

    list.querySelectorAll('.pref-up').forEach(function(btn) {
        _bindOnce(btn, 'click', function() {
            var li = this.closest('.preference-item');
            var prev = li.previousElementSibling;
            if (prev && prev.classList.contains('preference-item')) {
                li.parentNode.insertBefore(li, prev);
                _refreshPreferenceState();
            }
        });
    });

    list.querySelectorAll('.pref-down').forEach(function(btn) {
        _bindOnce(btn, 'click', function() {
            var li = this.closest('.preference-item');
            var next = li.nextElementSibling;
            if (next && next.classList.contains('preference-item')) {
                li.parentNode.insertBefore(next, li);
                _refreshPreferenceState();
            }
        });
    });
}

function _refreshPreferenceState() {
    var list = document.getElementById('preference-list');
    var hidden = document.getElementById('backend_preference');
    if (!list || !hidden) return;

    var items = list.querySelectorAll('.preference-item[data-backend]');
    var order = [];
    items.forEach(function(li, idx) {
        li.querySelector('.preference-rank').textContent = String(idx + 1);
        li.querySelector('.pref-up').disabled = (idx === 0);
        li.querySelector('.pref-down').disabled = (idx === items.length - 1);
        order.push(li.getAttribute('data-backend'));
    });
    hidden.value = order.join(',');
}

/* ── Step 4: Communication toggle headers + checkboxes ── */

function initCommunicationToggles() {
    var sections = ['gmail', 'telegram'];
    sections.forEach(function(name) {
        var toggle = document.getElementById(name + '-toggle');
        var header = toggle ? toggle.closest('.toggle-section') : null;
        var headerDiv = header ? header.querySelector('.toggle-header') : null;

        if (toggle) {
            _bindOnce(toggle, 'change', function() {
                toggleSection(name, this.checked);
            });
        }
        if (headerDiv) {
            _bindOnce(headerDiv, 'click', function(e) {
                if (e.target.closest('.toggle-switch')) return;
                if (toggle) {
                    toggle.checked = !toggle.checked;
                    toggleSection(name, toggle.checked);
                }
            });
        }
    });
}

/* ── Step 7: Plugin config toggles ─────────────────────── */

function initPluginConfigToggles() {
    var filterSelect = document.getElementById('email_filter_mode');
    if (!filterSelect) return;

    var allowedSection = document.getElementById('email-allowed-section');
    var blockedSection = document.getElementById('email-blocked-section');
    if (!allowedSection || !blockedSection) return;

    function updateVisibility() {
        var isOptIn = filterSelect.value === 'opt_in';
        allowedSection.style.display = isOptIn ? '' : 'none';
        blockedSection.style.display = isOptIn ? 'none' : '';
    }

    _bindOnce(filterSelect, 'change', updateVisibility);
    updateVisibility();
}

/* ── Step 7: Assignment temperature ranges ────────────── */

function initAssignmentRanges() {
    var ranges = document.querySelectorAll('.assignment-section input[type="range"]');
    ranges.forEach(function(range) {
        var name = range.getAttribute('name') || '';
        var match = name.match(/^(.+)_temperature$/);
        if (!match) return;
        var spanId = match[1] + '-temp-val';
        var span = document.getElementById(spanId);
        if (span) {
            _bindOnce(range, 'input', function() {
                span.textContent = this.value;
            });
        }
    });
}

/* ── Focus management (moved from inline script) ──────── */

function initFocusManagement() {
    var heading = document.querySelector('.step-heading, .setup-title, .success-title');
    if (heading) {
        heading.setAttribute('tabindex', '-1');
        heading.focus({ preventScroll: true });
    }
}

/* ── Form state persistence (moved from inline script) ── */

function initFormPersistence() {
    var STORE_KEY = 'overblick_settings_form';
    var form = document.querySelector('form[method="post"]');
    if (!form || form._persistBound) return;
    form._persistBound = true;

    var stepMatch = window.location.pathname.match(/\/step\/(\d+)/);
    if (!stepMatch) return;
    var stepKey = STORE_KEY + '_step' + stepMatch[1];

    // Restore saved values (only if server didn't provide them via form_data)
    try {
        var saved = JSON.parse(sessionStorage.getItem(stepKey) || '{}');
        Object.keys(saved).forEach(function(name) {
            var els = form.querySelectorAll('[name="' + name + '"]');
            els.forEach(function(el) {
                if (el.type === 'checkbox' || el.type === 'radio') {
                    if (el.value === saved[name] || (el.type === 'checkbox' && saved[name] === 'on')) {
                        el.checked = true;
                    }
                } else if (!el.value && saved[name]) {
                    el.value = saved[name];
                }
            });
        });
    } catch (e) { /* ignore parse errors */ }

    // Save on input changes
    function saveState() {
        var data = {};
        var formData = new FormData(form);
        formData.forEach(function(value, key) {
            data[key] = value;
        });
        try {
            sessionStorage.setItem(stepKey, JSON.stringify(data));
        } catch (e) { /* storage full — ignore */ }
    }

    form.addEventListener('input', saveState);
    form.addEventListener('change', saveState);

    // On final step submission, clear all wizard state
    form.addEventListener('submit', function() {
        var isLastStep = /\/step\/8$/.test(window.location.pathname);
        if (isLastStep) {
            try {
                Object.keys(sessionStorage).forEach(function(key) {
                    if (key.indexOf(STORE_KEY) === 0) sessionStorage.removeItem(key);
                });
            } catch (e) {}
        }
    });
}

/* ── Master initializer ────────────────────────────────── */

function wizardInit() {
    // Step 3: LLM backend toggles
    initLLMBackends();

    // Step 4: Communication toggles
    initCommunicationToggles();

    // Step 5: Security toggle — bind change listener
    var networkToggle = document.getElementById('network-toggle');
    if (networkToggle && !networkToggle._wizardBound) {
        networkToggle._wizardBound = true;
        networkToggle.addEventListener('change', function() {
            toggleNetwork(this.checked);
        });
        // Also bind the toggle header click
        var toggleHeader = networkToggle.closest('.toggle-section');
        if (toggleHeader) {
            var header = toggleHeader.querySelector('.toggle-header');
            if (header && !header._wizardBound) {
                header._wizardBound = true;
                header.addEventListener('click', function(e) {
                    if (e.target.closest('.toggle-switch')) return;
                    networkToggle.checked = !networkToggle.checked;
                    toggleNetwork(networkToggle.checked);
                });
            }
        }
        toggleNetwork(networkToggle.checked);
    }

    // Step 4: Toggle section headers (CSP-safe delegation)
    document.querySelectorAll('[data-toggle-section]').forEach(function(el) {
        _bindOnce(el, 'click', function() {
            toggleSection(el.getAttribute('data-toggle-section'));
        });
    });
    document.querySelectorAll('[data-toggle-checkbox]').forEach(function(el) {
        _bindOnce(el, 'change', function() {
            toggleSection(el.getAttribute('data-toggle-checkbox'), el.checked);
        });
    });
    document.querySelectorAll('[data-stop-propagation]').forEach(function(el) {
        _bindOnce(el, 'click', function(e) { e.stopPropagation(); });
    });

    // Step 6: Use case counter
    if (document.getElementById('selection-count')) initUseCaseCounter();

    // Step 7: Assignment temperature ranges + plugin config toggles
    initAssignmentRanges();
    initPluginConfigToggles();

    // Step 8: Provision form
    if (document.getElementById('provision-form')) initProvisionForm();

    // Focus management
    initFocusManagement();

    // Form state persistence
    initFormPersistence();
}

// Run on initial page load
wizardInit();

// Run after every htmx content swap
if (typeof htmx !== 'undefined') {
    htmx.onLoad(wizardInit);
}
