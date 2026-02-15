/**
 * Character Select Carousel Controller — Multi-Instance
 *
 * Video-game-style character selection with keyboard nav,
 * touch/swipe support, smooth CSS transitions, ARIA semantics.
 *
 * Supports MULTIPLE independent carousel instances on one page
 * (one per agent/use-case). Each instance manages its own state
 * and writes its selection to a dedicated hidden input.
 *
 * Usage:
 *   <div class="carousel-instance" data-input-name="social_media_personality">
 *       <script type="application/json" class="carousel-data">[...]</script>
 *       <div class="carousel-container">
 *           <button class="carousel-nav carousel-nav-prev">...</button>
 *           <div class="carousel-track"></div>
 *           <button class="carousel-nav carousel-nav-next">...</button>
 *       </div>
 *       <input type="hidden" class="carousel-value" name="social_media_personality">
 *   </div>
 *
 * Accessibility (WAI-ARIA Listbox pattern):
 *   - role="listbox" on track with aria-activedescendant
 *   - role="option" on cards with unique IDs
 *   - Keyboard: Arrow Left/Right, Space/Enter, Home/End
 *   - Touch: Swipe left/right on mobile
 */

(function () {
    'use strict';

    var SWIPE_THRESHOLD = 50; // px

    // Shared audio context for selection sounds
    var audioCtx = null;

    /**
     * Play a subtle selection "ping" via Web Audio API.
     */
    function playSelectSound() {
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        try {
            if (!audioCtx) {
                var AC = window.AudioContext || window.webkitAudioContext;
                if (!AC) return;
                audioCtx = new AC();
            }
            if (audioCtx.state === 'suspended') return;

            var osc = audioCtx.createOscillator();
            var gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.type = 'sine';
            osc.frequency.value = 880;
            osc.frequency.setTargetAtTime(1320, audioCtx.currentTime, 0.03);
            gain.gain.value = 0.08;
            gain.gain.setTargetAtTime(0, audioCtx.currentTime + 0.08, 0.04);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.15);
        } catch (e) {
            // Audio not available
        }
    }

    /**
     * Initialize a single carousel instance within a container element.
     */
    function createCarousel(container) {
        var dataEl = container.querySelector('.carousel-data');
        if (!dataEl) return;

        var characters = [];
        try {
            characters = JSON.parse(dataEl.textContent);
        } catch (e) {
            console.error('Failed to parse carousel data:', e);
            return;
        }

        if (characters.length === 0) return;

        var inputName = container.dataset.inputName || '';
        var preselectedValue = container.dataset.preselected || '';
        var recommendedValue = container.dataset.recommended || '';
        var instanceId = container.dataset.instanceId || inputName;

        // State
        var focusedIndex = 0;
        var selectedName = preselectedValue || recommendedValue || characters[0].name;

        // Find the index of the preselected character to start focused on it
        for (var i = 0; i < characters.length; i++) {
            if (characters[i].name === selectedName) {
                focusedIndex = i;
                break;
            }
        }

        // DOM references
        var track = container.querySelector('.carousel-track');
        var hiddenInput = container.querySelector('.carousel-value');
        var prevBtn = container.querySelector('.carousel-nav-prev');
        var nextBtn = container.querySelector('.carousel-nav-next');

        if (!track) return;

        // Write initial value
        if (hiddenInput) {
            hiddenInput.value = selectedName;
        }

        render();
        renderIndicator();
        bindEvents();
        animateEntrance();

        function render() {
            track.setAttribute('role', 'listbox');
            track.setAttribute('aria-label', 'Select personality');
            track.setAttribute('aria-roledescription', 'character carousel');
            track.setAttribute('tabindex', '0');

            while (track.firstChild) {
                track.removeChild(track.firstChild);
            }

            characters.forEach(function (char, index) {
                var card = createCard(char, index);
                track.appendChild(card);
            });

            updatePositions();
            updateActiveDescendant();
        }

        function renderIndicator() {
            var carouselContainer = container.querySelector('.carousel-container');
            if (!carouselContainer) return;

            var existing = carouselContainer.querySelector('.carousel-indicator');
            if (existing) existing.remove();

            var indicator = document.createElement('div');
            indicator.className = 'carousel-indicator';
            indicator.setAttribute('role', 'tablist');
            indicator.setAttribute('aria-label', 'Character position');

            characters.forEach(function (char, index) {
                var dot = document.createElement('button');
                dot.className = 'carousel-dot';
                dot.type = 'button';
                dot.setAttribute('role', 'tab');
                dot.setAttribute('aria-label', char.display_name);
                dot.setAttribute('aria-selected', index === focusedIndex ? 'true' : 'false');
                dot.dataset.index = index;

                if (index === focusedIndex) dot.classList.add('active');
                if (char.name === selectedName) dot.classList.add('selected');

                dot.addEventListener('click', function () {
                    focusedIndex = index;
                    updatePositions();
                    updateIndicator();
                    updateActiveDescendant();
                    announcePosition();
                });

                indicator.appendChild(dot);
            });

            carouselContainer.appendChild(indicator);
        }

        function updateIndicator() {
            var dots = container.querySelectorAll('.carousel-dot');
            dots.forEach(function (dot, index) {
                dot.classList.toggle('active', index === focusedIndex);
                dot.classList.toggle('selected', characters[index].name === selectedName);
                dot.setAttribute('aria-selected', index === focusedIndex ? 'true' : 'false');
            });
        }

        function createCard(char, index) {
            var card = document.createElement('div');
            card.className = 'character-card';
            card.id = 'character-option-' + instanceId + '-' + char.name;
            card.dataset.index = index;
            card.dataset.name = char.name;

            card.setAttribute('role', 'option');
            card.setAttribute('aria-selected', char.name === selectedName ? 'true' : 'false');
            card.setAttribute('aria-label', char.display_name + ' — ' + (char.role || ''));

            // Entrance animation start state
            card.style.opacity = '0';
            card.style.transform = 'scale(0.85) translateY(20px)';

            if (char.name === selectedName) {
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
            role.textContent = char.role || '';
            card.appendChild(role);

            // Trait bars
            var traits = document.createElement('div');
            traits.className = 'trait-bars';
            var traitEntries = Object.entries(char.traits || {}).slice(0, 3);
            traitEntries.forEach(function (entry) {
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

            // Sample quote
            if (char.sample_quote) {
                var quote = document.createElement('div');
                quote.className = 'character-quote';
                quote.textContent = '\u201C' + char.sample_quote + '\u201D';
                card.appendChild(quote);
            }

            // Recommended badge (safe DOM construction — no innerHTML)
            if (char.name === recommendedValue) {
                var badgeWrapper = document.createElement('div');
                badgeWrapper.className = 'character-recommended';
                var badgeSpan = document.createElement('span');
                badgeSpan.className = 'badge badge-green';
                badgeSpan.textContent = 'Recommended';
                badgeWrapper.appendChild(badgeSpan);
                card.appendChild(badgeWrapper);
            }

            // Click to select
            card.addEventListener('click', function () {
                if (index === focusedIndex) {
                    selectCharacter(char.name);
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

        function animateEntrance() {
            if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                container.querySelectorAll('.character-card').forEach(function (card) {
                    card.style.opacity = '';
                    card.style.transform = '';
                });
                return;
            }

            var cards = container.querySelectorAll('.character-card');
            cards.forEach(function (card, index) {
                var delay = 80 + index * 60;
                setTimeout(function () {
                    card.style.transition = 'opacity 0.4s ease-out, transform 0.4s ease-out';
                    card.style.opacity = '';
                    card.style.transform = '';
                }, delay);
            });
        }

        function updateActiveDescendant() {
            if (!characters[focusedIndex]) return;
            track.setAttribute(
                'aria-activedescendant',
                'character-option-' + instanceId + '-' + characters[focusedIndex].name
            );
        }

        function updatePositions() {
            var cards = container.querySelectorAll('.character-card');
            cards.forEach(function (card, index) {
                card.classList.toggle('focused', index === focusedIndex);
            });

            var focusedCard = cards[focusedIndex];
            if (focusedCard) {
                focusedCard.scrollIntoView({
                    behavior: 'smooth',
                    block: 'nearest',
                    inline: 'center',
                });
            }
        }

        function selectCharacter(name) {
            selectedName = name;
            playSelectSound();

            // Update hidden input
            if (hiddenInput) {
                hiddenInput.value = name;
            }

            // Fire change event for form state persistence
            if (hiddenInput) {
                hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
            }

            // Update card visuals (single-select: only one active at a time)
            var cards = container.querySelectorAll('.character-card');
            cards.forEach(function (card) {
                var cardName = card.dataset.name;
                var isSelected = cardName === name;

                card.classList.toggle('selected', isSelected);
                card.setAttribute('aria-selected', isSelected ? 'true' : 'false');

                if (isSelected) {
                    card.classList.remove('just-selected');
                    void card.offsetWidth; // force reflow
                    card.classList.add('just-selected');
                } else {
                    card.classList.remove('just-selected');
                }
            });

            // Update selection label
            var label = container.querySelector('.carousel-selected-label');
            if (label) {
                var char = characters.find(function (c) { return c.name === name; });
                if (char) {
                    label.textContent = char.display_name;
                }
            }

            updateIndicator();
        }

        function announcePosition() {
            var announcerId = 'carousel-announcer-' + instanceId;
            var announcer = document.getElementById(announcerId);
            if (!announcer) {
                announcer = document.createElement('div');
                announcer.id = announcerId;
                announcer.setAttribute('aria-live', 'polite');
                announcer.setAttribute('aria-atomic', 'true');
                announcer.className = 'sr-only';
                document.body.appendChild(announcer);
            }

            var char = characters[focusedIndex];
            if (char) {
                announcer.textContent = char.display_name + ', ' +
                    (focusedIndex + 1) + ' of ' + characters.length +
                    (char.name === selectedName ? ', selected' : '');
            }
        }

        function navigate(delta) {
            var newIndex = focusedIndex + delta;
            if (newIndex >= 0 && newIndex < characters.length) {
                focusedIndex = newIndex;
                updatePositions();
                updateIndicator();
                updateActiveDescendant();
                announcePosition();
            }
        }

        function bindEvents() {
            // Keyboard navigation on the track
            track.addEventListener('keydown', function (e) {
                switch (e.key) {
                    case 'ArrowLeft':
                        e.preventDefault();
                        navigate(-1);
                        break;
                    case 'ArrowRight':
                        e.preventDefault();
                        navigate(1);
                        break;
                    case ' ':
                    case 'Enter':
                        e.preventDefault();
                        if (characters[focusedIndex]) {
                            selectCharacter(characters[focusedIndex].name);
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

            // Touch/swipe
            var touchStartX = 0;
            var touchStartY = 0;

            track.addEventListener('touchstart', function (e) {
                var touch = e.touches[0];
                touchStartX = touch.clientX;
                touchStartY = touch.clientY;
            }, { passive: true });

            track.addEventListener('touchend', function (e) {
                var touch = e.changedTouches[0];
                var deltaX = touch.clientX - touchStartX;
                var deltaY = touch.clientY - touchStartY;

                if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > SWIPE_THRESHOLD) {
                    navigate(deltaX < 0 ? 1 : -1);
                }
            }, { passive: true });

            // Nav buttons
            if (prevBtn) {
                prevBtn.addEventListener('click', function () { navigate(-1); });
            }
            if (nextBtn) {
                nextBtn.addEventListener('click', function () { navigate(1); });
            }
        }
    }

    /**
     * Initialize all carousel instances on the page.
     */
    function initAll() {
        var instances = document.querySelectorAll('.carousel-instance');
        instances.forEach(function (container) {
            createCarousel(container);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
})();
