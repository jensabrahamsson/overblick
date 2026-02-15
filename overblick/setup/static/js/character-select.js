/**
 * Character Select Carousel Controller
 *
 * Video-game-style character selection with keyboard nav,
 * touch/swipe support, smooth CSS transitions, ARIA semantics,
 * and multi-select support.
 *
 * State:
 *   - focusedIndex: which character is centered
 *   - selected: Set of character names that are selected
 *
 * Reads personality data from a <script type="application/json" id="character-data"> tag.
 *
 * Accessibility (WAI-ARIA Listbox pattern):
 *   - role="listbox" on track with aria-activedescendant (roving focus)
 *   - role="option" on cards with unique IDs
 *   - aria-selected tracks selection state
 *   - aria-live region announces position changes
 *   - Keyboard: Arrow Left/Right to navigate, Space/Enter to toggle,
 *     Home/End to jump, roving tabindex on options
 *   - Touch: Swipe left/right on mobile
 *
 * Micro-interactions:
 *   - Selection sound via Web Audio API (subtle "ping")
 *   - Staggered card entrance animation on load
 */

(function () {
    'use strict';

    var characters = [];
    var focusedIndex = 0;
    var selected = new Set();

    // Previously selected characters (from wizard state)
    var preselected = [];

    // Touch tracking
    var touchStartX = 0;
    var touchStartY = 0;
    var SWIPE_THRESHOLD = 50; // px

    // Audio context for selection sounds
    var audioCtx = null;

    function init() {
        var dataEl = document.getElementById('character-data');
        if (!dataEl) return;

        try {
            characters = JSON.parse(dataEl.textContent);
        } catch (e) {
            console.error('Failed to parse character data:', e);
            return;
        }

        var preselectedEl = document.getElementById('preselected-data');
        if (preselectedEl) {
            try {
                preselected = JSON.parse(preselectedEl.textContent);
                preselected.forEach(function(name) { selected.add(name); });
            } catch (e) {
                // ignore
            }
        }

        // Start focused on the middle character
        focusedIndex = Math.floor(characters.length / 2);

        render();
        renderIndicator();
        bindEvents();
        updateCounter();
        updateHiddenInputs();
        announcePosition();
        animateEntrance();
    }

    function render() {
        var track = document.getElementById('carousel-track');
        if (!track) return;

        // Set ARIA attributes on track (WAI-ARIA Listbox pattern)
        track.setAttribute('role', 'listbox');
        track.setAttribute('aria-label', 'Select AI characters');
        track.setAttribute('aria-roledescription', 'character carousel');
        track.setAttribute('aria-multiselectable', 'true');
        track.setAttribute('tabindex', '0');

        // Clear existing content safely
        while (track.firstChild) {
            track.removeChild(track.firstChild);
        }

        characters.forEach(function(char, index) {
            var card = createCard(char, index);
            track.appendChild(card);
        });

        updatePositions();
        updateActiveDescendant();
    }

    function renderIndicator() {
        // Create dot indicator below carousel
        var container = document.querySelector('.carousel-container');
        if (!container) return;

        // Remove existing indicator
        var existing = container.querySelector('.carousel-indicator');
        if (existing) existing.remove();

        var indicator = document.createElement('div');
        indicator.className = 'carousel-indicator';
        indicator.setAttribute('role', 'tablist');
        indicator.setAttribute('aria-label', 'Character position');

        characters.forEach(function(char, index) {
            var dot = document.createElement('button');
            dot.className = 'carousel-dot';
            dot.type = 'button';
            dot.setAttribute('role', 'tab');
            dot.setAttribute('aria-label', char.display_name);
            dot.setAttribute('aria-selected', index === focusedIndex ? 'true' : 'false');
            dot.dataset.index = index;

            if (index === focusedIndex) dot.classList.add('active');
            if (selected.has(char.name)) dot.classList.add('selected');

            dot.addEventListener('click', function() {
                focusedIndex = index;
                updatePositions();
                updateIndicator();
                updateActiveDescendant();
                announcePosition();
            });

            indicator.appendChild(dot);
        });

        container.appendChild(indicator);
    }

    function updateIndicator() {
        var dots = document.querySelectorAll('.carousel-dot');
        dots.forEach(function(dot, index) {
            dot.classList.toggle('active', index === focusedIndex);
            dot.classList.toggle('selected', selected.has(characters[index].name));
            dot.setAttribute('aria-selected', index === focusedIndex ? 'true' : 'false');
        });
    }

    function createCard(char, index) {
        var card = document.createElement('div');
        card.className = 'character-card';
        card.id = 'character-option-' + char.name;
        card.dataset.index = index;
        card.dataset.name = char.name;

        // ARIA attributes (WAI-ARIA option with roving tabindex)
        card.setAttribute('role', 'option');
        card.setAttribute('aria-selected', selected.has(char.name) ? 'true' : 'false');
        card.setAttribute('aria-label', char.display_name + ' — ' + (char.role || char.description || ''));

        // Entrance animation: start invisible, animated in by animateEntrance()
        card.style.opacity = '0';
        card.style.transform = 'scale(0.85) translateY(20px)';

        if (selected.has(char.name)) {
            card.classList.add('selected');
        }

        // Avatar
        var avatar = document.createElement('img');
        avatar.className = 'character-avatar';
        avatar.src = '/static/img/personalities/' + encodeURIComponent(char.name) + '.svg';
        avatar.alt = char.display_name;
        avatar.setAttribute('aria-hidden', 'true');
        avatar.onerror = function () {
            this.style.display = 'none';
            var fallback = document.createElement('div');
            fallback.className = 'character-avatar character-avatar-fallback';
            fallback.textContent = char.display_name[0];
            fallback.setAttribute('aria-hidden', 'true');
            this.parentNode.insertBefore(fallback, this);
        };
        card.appendChild(avatar);

        // Name
        var name = document.createElement('div');
        name.className = 'character-name';
        name.textContent = char.display_name;
        card.appendChild(name);

        // Role
        var role = document.createElement('div');
        role.className = 'character-role';
        role.textContent = char.role || char.description || '';
        card.appendChild(role);

        // Trait bars
        var traits = document.createElement('div');
        traits.className = 'trait-bars';
        var traitEntries = Object.entries(char.traits || {}).slice(0, 3);
        traitEntries.forEach(function(entry) {
            var traitName = entry[0];
            var value = entry[1];
            var pct = Math.round(value * 100);

            var bar = document.createElement('div');
            bar.className = 'trait-bar';

            var label = document.createElement('span');
            label.className = 'trait-label';
            label.textContent = traitName;

            var trackEl = document.createElement('div');
            trackEl.className = 'trait-track';
            trackEl.setAttribute('role', 'meter');
            trackEl.setAttribute('aria-label', traitName + ': ' + pct + '%');
            trackEl.setAttribute('aria-valuenow', pct);
            trackEl.setAttribute('aria-valuemin', '0');
            trackEl.setAttribute('aria-valuemax', '100');

            var fill = document.createElement('div');
            fill.className = 'trait-fill';
            fill.style.width = pct + '%';

            var val = document.createElement('span');
            val.className = 'trait-value';
            val.textContent = pct + '%';

            trackEl.appendChild(fill);
            bar.appendChild(label);
            bar.appendChild(trackEl);
            bar.appendChild(val);
            traits.appendChild(bar);
        });
        card.appendChild(traits);

        // Plugin compatibility
        var plugins = document.createElement('div');
        plugins.className = 'plugin-list';
        var pluginEntries = Object.entries(char.plugins || {});
        if (pluginEntries.length > 0) {
            var pluginTitle = document.createElement('div');
            pluginTitle.className = 'plugin-title';
            pluginTitle.textContent = 'Best for:';
            plugins.appendChild(pluginTitle);

            pluginEntries.forEach(function(pEntry) {
                var pluginName = pEntry[0];
                var level = pEntry[1];
                var levelLabels = {great: 'Great', good: 'Good', na: 'N/A'};

                var item = document.createElement('div');
                item.className = 'plugin-item';

                var dot = document.createElement('span');
                dot.className = 'plugin-dot ' + level;
                dot.setAttribute('aria-hidden', 'true');

                var text = document.createElement('span');
                text.textContent = pluginName.replace(/_/g, ' ');

                var levelTag = document.createElement('span');
                levelTag.className = 'plugin-level';
                levelTag.textContent = levelLabels[level] || level;

                item.appendChild(dot);
                item.appendChild(text);
                item.appendChild(levelTag);
                plugins.appendChild(item);
            });
        }
        card.appendChild(plugins);

        // Sample quote
        if (char.sample_quote) {
            var quote = document.createElement('div');
            quote.className = 'character-quote';
            quote.textContent = '\u201C' + char.sample_quote + '\u201D';
            card.appendChild(quote);
        }

        // Select button
        var btn = document.createElement('button');
        btn.className = 'character-select-btn';
        btn.type = 'button';
        if (selected.has(char.name)) {
            btn.classList.add('active');
            btn.textContent = '\u2713 Selected';
        } else {
            btn.textContent = 'Select ' + char.display_name;
        }
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            toggleSelect(char.name);
        });
        card.appendChild(btn);

        // Click card to focus or toggle
        card.addEventListener('click', function() {
            if (index === focusedIndex) {
                toggleSelect(char.name);
            } else {
                focusedIndex = index;
                updatePositions();
                updateIndicator();
                updateActiveDescendant();
                announcePosition();
            }
        });

        return card;
    }

    /**
     * Staggered entrance animation for cards on initial load.
     * Each card fades in with a slight delay, creating a cascading reveal.
     */
    function animateEntrance() {
        // Respect reduced motion
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            document.querySelectorAll('.character-card').forEach(function(card) {
                card.style.opacity = '';
                card.style.transform = '';
            });
            return;
        }

        var cards = document.querySelectorAll('.character-card');
        cards.forEach(function(card, index) {
            var delay = 80 + index * 60; // stagger: 80ms, 140ms, 200ms, ...
            setTimeout(function() {
                card.style.transition = 'opacity 0.4s ease-out, transform 0.4s ease-out';
                card.style.opacity = '';
                card.style.transform = '';
            }, delay);
        });
    }

    /**
     * WAI-ARIA: Update aria-activedescendant on the listbox to point
     * to the currently focused option. This enables roving virtual focus
     * where the listbox keeps DOM focus but the screen reader follows
     * the active descendant.
     */
    function updateActiveDescendant() {
        var track = document.getElementById('carousel-track');
        if (!track || !characters[focusedIndex]) return;
        track.setAttribute('aria-activedescendant', 'character-option-' + characters[focusedIndex].name);
    }

    function updatePositions() {
        var cards = document.querySelectorAll('.character-card');
        cards.forEach(function(card, index) {
            card.classList.remove('focused');
            if (index === focusedIndex) {
                card.classList.add('focused');
            }
        });

        // Scroll the focused card into view
        var focusedCard = cards[focusedIndex];
        if (focusedCard) {
            focusedCard.scrollIntoView({
                behavior: 'smooth',
                block: 'nearest',
                inline: 'center',
            });
        }
    }

    /**
     * Play a subtle selection "ping" via Web Audio API.
     * Short, pleasant, non-intrusive. Only plays if audio context
     * is available (user has interacted with the page).
     */
    function playSelectSound(isSelect) {
        // Respect reduced motion (often correlates with motion sensitivity)
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        try {
            if (!audioCtx) {
                var AC = window.AudioContext || window.webkitAudioContext;
                if (!AC) return;
                audioCtx = new AC();
            }
            if (audioCtx.state === 'suspended') return; // No user gesture yet

            var osc = audioCtx.createOscillator();
            var gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);

            // Select: bright ascending ping. Deselect: soft descending tone.
            osc.type = 'sine';
            osc.frequency.value = isSelect ? 880 : 440;
            if (isSelect) {
                osc.frequency.setTargetAtTime(1320, audioCtx.currentTime, 0.03);
            } else {
                osc.frequency.setTargetAtTime(330, audioCtx.currentTime, 0.03);
            }

            gain.gain.value = 0.08; // Very quiet
            gain.gain.setTargetAtTime(0, audioCtx.currentTime + 0.08, 0.04);

            osc.start();
            osc.stop(audioCtx.currentTime + 0.15);
        } catch (e) {
            // Audio not available — fail silently
        }
    }

    function toggleSelect(name) {
        var isSelect = !selected.has(name);
        if (isSelect) {
            selected.add(name);
        } else {
            selected.delete(name);
        }

        playSelectSound(isSelect);

        // Update card visuals
        var cards = document.querySelectorAll('.character-card');
        cards.forEach(function(card) {
            var cardName = card.dataset.name;
            var btn = card.querySelector('.character-select-btn');
            var char = characters.find(function(c) { return c.name === cardName; });

            if (selected.has(cardName)) {
                card.classList.add('selected');
                card.setAttribute('aria-selected', 'true');

                // Trigger selection pulse animation
                card.classList.remove('just-selected');
                void card.offsetWidth; // force reflow
                card.classList.add('just-selected');

                if (btn) {
                    btn.classList.add('active');
                    btn.textContent = '\u2713 Selected';
                }
            } else {
                card.classList.remove('selected');
                card.classList.remove('just-selected');
                card.setAttribute('aria-selected', 'false');
                if (btn) {
                    btn.classList.remove('active');
                    btn.textContent = 'Select ' + (char ? char.display_name : cardName);
                }
            }
        });

        updateCounter();
        updateHiddenInputs();
        updateIndicator();
    }

    function updateCounter() {
        var counter = document.getElementById('selection-counter');
        if (counter) {
            var count = selected.size;
            var strong = document.createElement('strong');
            strong.textContent = count;
            while (counter.firstChild) counter.removeChild(counter.firstChild);
            counter.appendChild(strong);
            counter.appendChild(document.createTextNode(' character' + (count !== 1 ? 's' : '') + ' selected'));
        }
    }

    function updateHiddenInputs() {
        var container = document.getElementById('selected-inputs');
        if (!container) return;

        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }
        selected.forEach(function(name) {
            var input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'selected_characters';
            input.value = name;
            container.appendChild(input);
        });
    }

    function announcePosition() {
        // Update or create aria-live region for screen readers
        var announcer = document.getElementById('carousel-announcer');
        if (!announcer) {
            announcer = document.createElement('div');
            announcer.id = 'carousel-announcer';
            announcer.setAttribute('aria-live', 'polite');
            announcer.setAttribute('aria-atomic', 'true');
            announcer.className = 'sr-only';
            document.body.appendChild(announcer);
        }

        var char = characters[focusedIndex];
        if (char) {
            announcer.textContent = char.display_name + ', ' +
                (focusedIndex + 1) + ' of ' + characters.length +
                (selected.has(char.name) ? ', selected' : '');
        }
    }

    function bindEvents() {
        var track = document.getElementById('carousel-track');

        // Keyboard navigation (on the track element — roving virtual focus)
        if (track) {
            track.addEventListener('keydown', function(e) {
                switch (e.key) {
                    case 'ArrowLeft':
                        e.preventDefault();
                        if (focusedIndex > 0) {
                            focusedIndex--;
                            updatePositions();
                            updateIndicator();
                            updateActiveDescendant();
                            announcePosition();
                        }
                        break;
                    case 'ArrowRight':
                        e.preventDefault();
                        if (focusedIndex < characters.length - 1) {
                            focusedIndex++;
                            updatePositions();
                            updateIndicator();
                            updateActiveDescendant();
                            announcePosition();
                        }
                        break;
                    case ' ':
                    case 'Enter':
                        e.preventDefault();
                        if (characters[focusedIndex]) {
                            toggleSelect(characters[focusedIndex].name);
                        }
                        break;
                    case 'Home':
                        e.preventDefault();
                        focusedIndex = 0;
                        updatePositions();
                        updateIndicator();
                        updateActiveDescendant();
                        announcePosition();
                        break;
                    case 'End':
                        e.preventDefault();
                        focusedIndex = characters.length - 1;
                        updatePositions();
                        updateIndicator();
                        updateActiveDescendant();
                        announcePosition();
                        break;
                }
            });

            // Touch/swipe support
            track.addEventListener('touchstart', function(e) {
                var touch = e.touches[0];
                touchStartX = touch.clientX;
                touchStartY = touch.clientY;
            }, {passive: true});

            track.addEventListener('touchend', function(e) {
                var touch = e.changedTouches[0];
                var deltaX = touch.clientX - touchStartX;
                var deltaY = touch.clientY - touchStartY;

                // Only swipe if horizontal movement exceeds vertical
                if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > SWIPE_THRESHOLD) {
                    if (deltaX < 0 && focusedIndex < characters.length - 1) {
                        // Swipe left → next
                        focusedIndex++;
                        updatePositions();
                        updateIndicator();
                        updateActiveDescendant();
                        announcePosition();
                    } else if (deltaX > 0 && focusedIndex > 0) {
                        // Swipe right → previous
                        focusedIndex--;
                        updatePositions();
                        updateIndicator();
                        updateActiveDescendant();
                        announcePosition();
                    }
                }
            }, {passive: true});
        }

        // Also handle global arrow keys (for convenience when track isn't focused)
        document.addEventListener('keydown', function(e) {
            // Only handle when carousel is visible and track isn't focused
            if (!document.getElementById('carousel-track')) return;
            if (document.activeElement === track) return; // Track handles its own

            switch (e.key) {
                case 'ArrowLeft':
                    e.preventDefault();
                    if (focusedIndex > 0) {
                        focusedIndex--;
                        updatePositions();
                        updateIndicator();
                        updateActiveDescendant();
                        announcePosition();
                    }
                    break;
                case 'ArrowRight':
                    e.preventDefault();
                    if (focusedIndex < characters.length - 1) {
                        focusedIndex++;
                        updatePositions();
                        updateIndicator();
                        updateActiveDescendant();
                        announcePosition();
                    }
                    break;
                case ' ':
                case 'Enter':
                    // Only if not in a form input
                    if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].indexOf(e.target.tagName) !== -1) return;
                    e.preventDefault();
                    if (characters[focusedIndex]) {
                        toggleSelect(characters[focusedIndex].name);
                    }
                    break;
            }
        });

        // Nav buttons
        var prevBtn = document.getElementById('carousel-prev');
        var nextBtn = document.getElementById('carousel-next');

        if (prevBtn) {
            prevBtn.addEventListener('click', function() {
                if (focusedIndex > 0) {
                    focusedIndex--;
                    updatePositions();
                    updateIndicator();
                    updateActiveDescendant();
                    announcePosition();
                }
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener('click', function() {
                if (focusedIndex < characters.length - 1) {
                    focusedIndex++;
                    updatePositions();
                    updateIndicator();
                    updateActiveDescendant();
                    announcePosition();
                }
            });
        }
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
