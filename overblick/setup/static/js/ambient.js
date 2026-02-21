/**
 * Ambient Music Controller — Klaus Wunderlich-inspired Organ Synthesizer
 *
 * Generates warm, lush electronic organ sounds using Web Audio API.
 * Inspired by Klaus Wunderlich's space-age lounge organ recordings.
 *
 * Sound design:
 * - Hammond-style drawbar harmonics (16', 8', 5 1/3', 4', 2 2/3', 1 3/5')
 * - Chorus detuning across 3 unison voices per note (6-10 cent spread)
 * - Leslie speaker simulation (6 cent pitch wobble + 20% amplitude modulation)
 * - Feedback delay network reverb (4 taps, wet 0.50)
 * - Warm low-pass filtering (900Hz cutoff, Q 1.0)
 * - 8-chord progression with 18s duration and 6s crossfades
 *
 * Features:
 * - Autostart on first user interaction (click anywhere)
 * - Play/pause toggle with smooth fade-in/fade-out
 * - Volume slider (persisted in localStorage)
 * - Procedurally generated — no MP3 dependencies
 * - Respects prefers-reduced-motion (disables modulation effects)
 */

(function () {
    'use strict';

    var STORAGE_KEY = 'overblick_setup_volume';
    var FADE_DURATION = 1500; // ms
    var CHORD_DURATION = 4000; // ms — Klaus Wunderlich energetic pace (4-second chord changes)
    var CROSSFADE_TIME = 1.5; // seconds overlap between chords

    var audioCtx = null;
    var masterGain = null;
    var reverbGain = null;
    var isPlaying = false;
    var activeVoices = [];
    var chordTimer = null;
    var currentChordIndex = 0;
    var hasAutostarted = false;

    // Rich jazz voicings — spread across octaves for Wunderlich's lush sound
    var CHORDS = [
        // Cmaj9 — C2, G2, E3, B3, D4
        [65.41, 98.00, 164.81, 246.94, 293.66],
        // Fmaj7#11 — F2, A2, C3, E3, B3
        [87.31, 110.00, 130.81, 164.81, 246.94],
        // Am9 — A1, E2, G2, C3, B3
        [55.00, 82.41, 98.00, 130.81, 246.94],
        // Dm11 — D2, A2, C3, F3, G3
        [73.42, 110.00, 130.81, 174.61, 196.00],
        // Gmaj7 — G1, B2, D3, F#3, A3
        [49.00, 123.47, 146.83, 185.00, 220.00],
        // Ebmaj9 — Eb2, Bb2, G3, D4, F4
        [77.78, 116.54, 196.00, 293.66, 349.23],
        // Bbmaj9#11 — Bb1, F2, A2, D3, E3
        [58.27, 87.31, 110.00, 146.83, 164.81],
        // Abmaj7 — Ab1, Eb2, G2, C3, Bb3
        [51.91, 77.78, 98.00, 130.81, 233.08],
    ];

    // Hammond drawbar ratios — simulates 16', 8', 5 1/3', 4', 2 2/3', 1 3/5'
    var DRAWBAR_RATIOS = [0.5, 1.0, 1.5, 2.0, 2.6667, 3.2];
    var DRAWBAR_LEVELS = [0.3, 1.0, 0.25, 0.4, 0.15, 0.10];

    function init() {
        var playBtn = document.getElementById('music-toggle');
        var volumeSlider = document.getElementById('music-volume');

        var savedVolume = localStorage.getItem(STORAGE_KEY);
        var initialVolume = savedVolume !== null ? parseFloat(savedVolume) : 0.3;

        if (playBtn) {
            playBtn.addEventListener('click', togglePlay);
        }

        if (volumeSlider) {
            volumeSlider.value = Math.round(initialVolume * 100);
            volumeSlider.addEventListener('input', function (e) {
                var vol = parseInt(e.target.value) / 100;
                localStorage.setItem(STORAGE_KEY, vol.toString());
                if (masterGain && isPlaying) {
                    masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.1);
                }
            });
        }

        // Handle the <audio> element gracefully (not used — we synthesize)
        var audioEl = document.getElementById('ambient-audio');
        if (audioEl) {
            audioEl.removeAttribute('src');
            audioEl.style.display = 'none';
        }

        // Autostart with browser autoplay policy handling:
        // - Try to start immediately (works in Firefox/Safari)
        // - Chrome blocks autoplay; resume on first user interaction
        // - Visual indicator shows music is ready to play
        function attemptAutoStart() {
            try {
                createAudioContext();
                if (audioCtx && audioCtx.state === 'suspended') {
                    // Autoplay blocked; will resume on click/touch
                    var btn = document.getElementById('music-toggle');
                    if (btn) btn.style.animation = 'pulse 2s infinite';
                    return false;
                }
                // Autoplay succeeded — start music
                hasAutostarted = true;
                isPlaying = true;
                var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
                masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.8);
                updateButton();
                startChordCycle();
                return true;
            } catch (e) {
                return false;
            }
        }

        // Try immediate start (works in permissive browsers)
        if (!attemptAutoStart()) {
            // Chrome/strict mode: wait for user interaction
            var resumeHandler = function() {
                if (audioCtx && audioCtx.state === 'suspended') {
                    audioCtx.resume();
                }
                autoStart();
            };
            document.addEventListener('click', resumeHandler, { once: true });
            document.addEventListener('touchstart', resumeHandler, { once: true });
        }
    }

    function autoStart() {
        if (hasAutostarted || isPlaying) return;
        hasAutostarted = true;

        createAudioContext();

        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }

        var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
        masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.8);

        isPlaying = true;
        updateButton();
        startChordCycle();
    }

    function createAudioContext() {
        if (audioCtx) return;

        var AC = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AC();

        // Master gain
        masterGain = audioCtx.createGain();
        masterGain.gain.value = 0;

        // Create pseudo-reverb (4-tap feedback delay network)
        var reverbDelay1 = audioCtx.createDelay(1.0);
        reverbDelay1.delayTime.value = 0.12;
        var reverbDelay2 = audioCtx.createDelay(1.0);
        reverbDelay2.delayTime.value = 0.19;
        var reverbDelay3 = audioCtx.createDelay(1.0);
        reverbDelay3.delayTime.value = 0.27;
        var reverbDelay4 = audioCtx.createDelay(1.0);
        reverbDelay4.delayTime.value = 0.37;

        var feedback1 = audioCtx.createGain();
        feedback1.gain.value = 0.38;
        var feedback2 = audioCtx.createGain();
        feedback2.gain.value = 0.33;
        var feedback3 = audioCtx.createGain();
        feedback3.gain.value = 0.28;
        var feedback4 = audioCtx.createGain();
        feedback4.gain.value = 0.22;

        // Reverb filter — darken the reverb tail
        var reverbFilter = audioCtx.createBiquadFilter();
        reverbFilter.type = 'lowpass';
        reverbFilter.frequency.value = 2000;
        reverbFilter.Q.value = 0.5;

        // Reverb wet/dry — wetter for that Wunderlich hall sound
        reverbGain = audioCtx.createGain();
        reverbGain.gain.value = 0.50;

        // Wire up 4-tap feedback delay network
        masterGain.connect(reverbDelay1);
        masterGain.connect(reverbDelay2);
        masterGain.connect(reverbDelay3);
        masterGain.connect(reverbDelay4);

        reverbDelay1.connect(feedback1);
        feedback1.connect(reverbDelay1);
        reverbDelay2.connect(feedback2);
        feedback2.connect(reverbDelay2);
        reverbDelay3.connect(feedback3);
        feedback3.connect(reverbDelay3);
        reverbDelay4.connect(feedback4);
        feedback4.connect(reverbDelay4);

        feedback1.connect(reverbFilter);
        feedback2.connect(reverbFilter);
        feedback3.connect(reverbFilter);
        feedback4.connect(reverbFilter);

        reverbFilter.connect(reverbGain);
        reverbGain.connect(audioCtx.destination);

        // Dry signal
        masterGain.connect(audioCtx.destination);
    }

    /**
     * Create a single organ voice with Hammond-style drawbar harmonics,
     * chorus detuning, and Leslie speaker modulation.
     */
    function createOrganVoice(freq, voiceGain, attackTime) {
        var now = audioCtx.currentTime;
        var nodes = [];
        var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // Voice output gain (for crossfade control)
        var outputGain = audioCtx.createGain();
        outputGain.gain.value = 0;
        outputGain.gain.setTargetAtTime(voiceGain, now, attackTime);

        // Leslie speaker simulation — deeper wobble for lush Wunderlich sound
        var leslieSpeed = 0.8 + Math.random() * 0.4; // ~0.8-1.2 Hz (slow Leslie)
        var lesliePitchDepth = reducedMotion ? 0 : 6; // cents — deeper wobble
        var leslieTremoloDepth = reducedMotion ? 0 : voiceGain * 0.20; // 20% tremolo

        // Tremolo LFO (amplitude modulation)
        var tremoloLFO = audioCtx.createOscillator();
        tremoloLFO.type = 'sine';
        tremoloLFO.frequency.value = leslieSpeed;
        var tremoloDepth = audioCtx.createGain();
        tremoloDepth.gain.value = leslieTremoloDepth;
        tremoloLFO.connect(tremoloDepth);
        tremoloDepth.connect(outputGain.gain);
        tremoloLFO.start(now);
        nodes.push(tremoloLFO);

        // Warm low-pass filter — lower cutoff for that vintage sound
        var voiceFilter = audioCtx.createBiquadFilter();
        voiceFilter.type = 'lowpass';
        voiceFilter.frequency.value = 900 + Math.random() * 200;
        voiceFilter.Q.value = 1.0;
        voiceFilter.connect(outputGain);

        // For each drawbar partial...
        DRAWBAR_RATIOS.forEach(function (ratio, di) {
            var partialFreq = freq * ratio;
            var level = DRAWBAR_LEVELS[di];

            // Chorus: 3 detuned oscillators per partial
            var detuneSpread = [
                -6 - Math.random() * 2,  // ~-6 to -8 cents
                0,                        // center
                6 + Math.random() * 2,    // ~+6 to +8 cents
            ];
            var chorusLevels = [0.8, 1.0, 0.8]; // center louder

            detuneSpread.forEach(function (detuneCents, ci) {
                var osc = audioCtx.createOscillator();
                // Mix sine and triangle for organ character
                // Upper drawbars (2 2/3', 1 3/5') use sine for shimmer
                osc.type = di < 2 ? 'sine' : (di >= 4 ? 'sine' : 'triangle');
                osc.frequency.value = partialFreq;
                osc.detune.value = detuneCents;

                // Leslie pitch wobble via LFO
                if (!reducedMotion) {
                    var pitchLFO = audioCtx.createOscillator();
                    pitchLFO.type = 'sine';
                    pitchLFO.frequency.value = leslieSpeed + ci * 0.05;
                    var pitchDepthGain = audioCtx.createGain();
                    pitchDepthGain.gain.value = lesliePitchDepth;
                    pitchLFO.connect(pitchDepthGain);
                    pitchDepthGain.connect(osc.detune);
                    pitchLFO.start(now);
                    nodes.push(pitchLFO);
                }

                // Per-partial gain
                var partialGain = audioCtx.createGain();
                partialGain.gain.value = level * chorusLevels[ci] * 0.10;

                osc.connect(partialGain);
                partialGain.connect(voiceFilter);

                osc.start(now);
                nodes.push(osc);
            });
        });

        outputGain.connect(masterGain);

        return {
            outputGain: outputGain,
            nodes: nodes,
            release: function (releaseTime) {
                var t = audioCtx.currentTime;
                outputGain.gain.cancelScheduledValues(t);
                outputGain.gain.setTargetAtTime(0, t, releaseTime * 0.3);
                setTimeout(function () {
                    nodes.forEach(function (n) {
                        try { n.stop(); } catch (e) { /* already stopped */ }
                    });
                    try { outputGain.disconnect(); } catch (e) {}
                }, releaseTime * 1000 + 500);
            },
        };
    }

    function playChord(chordFreqs) {
        if (!audioCtx) return;

        var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
        var perNoteGain = (vol * 0.5) / Math.max(chordFreqs.length, 1);
        var voices = [];

        chordFreqs.forEach(function (freq) {
            var voice = createOrganVoice(freq, perNoteGain, 1.5);
            voices.push(voice);
        });

        // Release previous voices with long crossfade
        activeVoices.forEach(function (oldVoices) {
            oldVoices.forEach(function (v) {
                v.release(CROSSFADE_TIME);
            });
        });

        activeVoices.push(voices);

        // Keep only current + fading voices (prevent memory buildup)
        if (activeVoices.length > 3) {
            activeVoices.shift();
        }
    }

    function nextChord() {
        currentChordIndex = (currentChordIndex + 1) % CHORDS.length;
        playChord(CHORDS[currentChordIndex]);
    }

    function startChordCycle() {
        playChord(CHORDS[currentChordIndex]);
        chordTimer = setInterval(nextChord, CHORD_DURATION);
    }

    function stopChordCycle() {
        if (chordTimer) {
            clearInterval(chordTimer);
            chordTimer = null;
        }
    }

    function togglePlay() {
        if (isPlaying) {
            // Fade out and stop
            if (masterGain && audioCtx) {
                masterGain.gain.setTargetAtTime(0, audioCtx.currentTime, 0.5);
                setTimeout(function () {
                    stopChordCycle();
                    activeVoices.forEach(function (voices) {
                        voices.forEach(function (v) {
                            try { v.release(0.2); } catch (e) {}
                        });
                    });
                    activeVoices = [];
                    isPlaying = false;
                    hasAutostarted = false; // Allow re-autostart
                    updateButton();
                }, FADE_DURATION);
            } else {
                // Fallback if gain not ready
                isPlaying = false;
                updateButton();
            }
        } else {
            // Start music
            createAudioContext();

            if (audioCtx && audioCtx.state === 'suspended') {
                audioCtx.resume();
            }

            if (audioCtx && masterGain) {
                var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
                masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.8);

                isPlaying = true;
                // Remove pulse animation if it exists
                var btn = document.getElementById('music-toggle');
                if (btn) btn.style.animation = '';
                updateButton();
                startChordCycle();
            }
        }
    }

    function updateButton() {
        var btn = document.getElementById('music-toggle');
        if (btn) {
            btn.classList.toggle('playing', isPlaying);
            btn.setAttribute('aria-label', isPlaying ? 'Pause music' : 'Play music');
            btn.textContent = isPlaying ? '\u23F8' : '\u25B6';
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
