/**
 * Överblick Onboarding Wizard — interactive form behavior.
 *
 * Handles provider switching, plugin counter, and secret row management.
 * External file to comply with CSP (script-src 'self').
 */
(function () {
    "use strict";

    // ── LLM provider switching (step 2) ─────────────────────────────────
    var providerRadios = document.querySelectorAll('input[name="provider"]');
    var cloudConfig = document.getElementById("cloud-config");

    if (providerRadios.length && cloudConfig) {
        providerRadios.forEach(function (radio) {
            radio.addEventListener("change", function () {
                cloudConfig.style.display = this.value === "cloud" ? "block" : "none";
            });
        });
    }

    // ── Plugin counter (step 4) ─────────────────────────────────────────
    var pluginCheckboxes = document.querySelectorAll('.plugin-card input[type="checkbox"]');
    var pluginCountEl = document.getElementById("plugin-count");

    if (pluginCheckboxes.length && pluginCountEl) {
        function updatePluginCount() {
            var n = 0;
            pluginCheckboxes.forEach(function (cb) { if (cb.checked) n++; });
            pluginCountEl.textContent = n;
        }
        pluginCheckboxes.forEach(function (cb) {
            cb.addEventListener("change", updatePluginCount);
        });
        updatePluginCount();
    }

    // ── Secret rows add/remove (step 5) ─────────────────────────────────
    var addSecretBtn = document.getElementById("add-secret-btn");
    var secretsContainer = document.getElementById("secrets-container");

    if (addSecretBtn && secretsContainer) {
        addSecretBtn.addEventListener("click", function () {
            var row = document.createElement("div");
            row.className = "form-row secret-row";

            var keyGroup = document.createElement("div");
            keyGroup.className = "form-group form-group-grow";
            var keyInput = document.createElement("input");
            keyInput.type = "text";
            keyInput.name = "secret_keys";
            keyInput.className = "form-input";
            keyInput.placeholder = "Key name";
            keyGroup.appendChild(keyInput);

            var valGroup = document.createElement("div");
            valGroup.className = "form-group form-group-grow";
            var valInput = document.createElement("input");
            valInput.type = "password";
            valInput.name = "secret_values";
            valInput.className = "form-input";
            valInput.placeholder = "Value (encrypted at rest)";
            valGroup.appendChild(valInput);

            var removeBtn = document.createElement("button");
            removeBtn.type = "button";
            removeBtn.className = "btn btn-ghost btn-sm secret-remove-btn";
            removeBtn.textContent = "Remove";

            row.appendChild(keyGroup);
            row.appendChild(valGroup);
            row.appendChild(removeBtn);
            secretsContainer.appendChild(row);
        });
    }

    // Event delegation for remove buttons (works for existing + dynamically added)
    if (secretsContainer) {
        secretsContainer.addEventListener("click", function (e) {
            var btn = e.target.closest(".secret-remove-btn");
            if (btn) {
                var row = btn.closest(".secret-row");
                if (row) row.remove();
            }
        });
    }
})();
