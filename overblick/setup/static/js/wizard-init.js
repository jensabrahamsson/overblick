/**
 * Wizard step initialization — runs after every htmx content swap.
 *
 * htmx hx-boost replaces the <body> via innerHTML which does NOT execute
 * inline <script> tags, and inline event handlers (onclick, onchange) may
 * not fire reliably. This external file binds all event listeners via JS.
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

/* ── Master initializer ────────────────────────────────── */

function wizardInit() {
    // Step 5: Security toggle — bind change listener (inline onchange may not fire)
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

    // Step 8: Provision form
    if (document.getElementById('provision-form')) initProvisionForm();
}

// Run on initial page load
wizardInit();

// Run after every htmx content swap
if (typeof htmx !== 'undefined') {
    htmx.onLoad(wizardInit);
}
