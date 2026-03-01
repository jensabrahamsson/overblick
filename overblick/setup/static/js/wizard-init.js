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
        { name: 'local_enabled', target: 'local-config' },
        { name: 'cloud_enabled', target: 'cloud-config' },
        { name: 'deepseek_enabled', target: 'deepseek-config' },
    ];
    backends.forEach(function(b) {
        var cb = document.querySelector('input[name="' + b.name + '"]');
        if (cb) {
            _bindOnce(cb, 'change', function() {
                toggleSection(b.target, this.checked);
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

    // Step 6: Use case counter
    if (document.getElementById('selection-count')) initUseCaseCounter();

    // Step 7: Assignment temperature ranges
    initAssignmentRanges();

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
