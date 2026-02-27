/**
 * Överblick Dashboard — main application JavaScript.
 *
 * All interactive behavior for the dashboard lives here to comply with
 * Content-Security-Policy (script-src 'self') which blocks inline scripts.
 */
(function () {
    "use strict";

    // ── Hamburger menu toggle ───────────────────────────────────────────
    var hamburger = document.querySelector(".nav-hamburger");
    var navLinks = document.getElementById("nav-links");

    if (hamburger && navLinks) {
        hamburger.addEventListener("click", function () {
            var expanded = hamburger.getAttribute("aria-expanded") === "true";
            hamburger.setAttribute("aria-expanded", !expanded);
            navLinks.classList.toggle("nav-links-open");
        });

        // Close menu when a link is clicked
        navLinks.addEventListener("click", function (e) {
            if (e.target.classList.contains("nav-link")) {
                hamburger.setAttribute("aria-expanded", "false");
                navLinks.classList.remove("nav-links-open");
            }
        });

        // Close menu on Escape key
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && hamburger.getAttribute("aria-expanded") === "true") {
                hamburger.setAttribute("aria-expanded", "false");
                navLinks.classList.remove("nav-links-open");
            }
        });
    }

    // ── htmx error handler ──────────────────────────────────────────────
    document.body.addEventListener("htmx:responseError", function () {
        var flash = document.createElement("div");
        flash.className = "flash flash-error";
        flash.setAttribute("role", "alert");
        flash.textContent = "Request failed. Please try again.";
        var main = document.getElementById("main-content");
        if (main) main.prepend(flash);
        setTimeout(function () { flash.remove(); }, 5000);
    });

    // ── Toggle detail rows (audit + LLM tables) ────────────────────────
    // Uses event delegation so it works with htmx-swapped content.
    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".btn-expand");
        if (!btn) return;
        var detailRow = btn.closest("tr").nextElementSibling;
        if (detailRow && detailRow.classList.contains("audit-detail-row")) {
            var hidden = detailRow.hidden;
            detailRow.hidden = !hidden;
            btn.setAttribute("aria-expanded", hidden ? "true" : "false");
            btn.textContent = hidden ? "\u25B2" : "\u25BC";
        }
    });

    // ── IRC feed auto-scroll ────────────────────────────────────────────
    // Scroll feed to bottom after htmx swap
    document.body.addEventListener("htmx:afterSwap", function (e) {
        if (e.detail.target && e.detail.target.id === "irc-feed") {
            e.detail.target.scrollTop = e.detail.target.scrollHeight;
        }
    });
    // Initial scroll to bottom
    var ircFeed = document.getElementById("irc-feed");
    if (ircFeed) ircFeed.scrollTop = ircFeed.scrollHeight;

    // ── Conversations identity filter ───────────────────────────────────
    var convFilter = document.querySelector('select[data-action="filter-conversations"]');
    if (convFilter) {
        convFilter.addEventListener("change", function () {
            window.location.href = "/conversations?identity=" + this.value;
        });
    }

    // ── Pause htmx polling when tab is hidden ────────────────────────
    // Saves bandwidth and reduces server load when user switches tabs.
    var _htmxPollingPaused = false;
    document.addEventListener("visibilitychange", function () {
        if (document.hidden) {
            // Pause all htmx polling by adding a class that triggers stop
            _htmxPollingPaused = true;
            // htmx listens for htmx:beforeRequest — cancel if hidden
        } else {
            _htmxPollingPaused = false;
            // Re-trigger polling elements to resume immediately
            document.querySelectorAll("[hx-trigger*='every']").forEach(function (el) {
                htmx.trigger(el, "htmx:poll");
            });
        }
    });

    // Cancel outgoing htmx requests when tab is hidden
    document.body.addEventListener("htmx:beforeRequest", function (evt) {
        if (_htmxPollingPaused && evt.detail.elt &&
            evt.detail.elt.getAttribute("hx-trigger") &&
            evt.detail.elt.getAttribute("hx-trigger").indexOf("every") !== -1) {
            evt.preventDefault();
        }
    });
})();
