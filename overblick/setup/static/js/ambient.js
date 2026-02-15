/**
 * Ambient Music Controller — Web Audio API Synthesizer
 *
 * Generates dreamy ambient electronic organ sounds procedurally
 * using Web Audio API. No external audio files needed.
 *
 * Inspired by Klaus Wunderlich's electronic organ and space-age
 * lounge aesthetics — layered detuned oscillators, soft filtering,
 * and gentle LFO modulation create a warm, floating atmosphere.
 *
 * Features:
 * - Play/pause toggle with smooth fade-in/fade-out
 * - Volume slider (persisted in localStorage)
 * - Procedurally generated — no MP3 dependencies
 * - Multiple chord layers that slowly evolve
 */

(function () {
    'use strict';

    var STORAGE_KEY = 'overblick_setup_volume';
    var FADE_DURATION = 1200; // ms

    var audioCtx = null;
    var masterGain = null;
    var isPlaying = false;
    var oscillators = [];
    var fadeTimer = null;

    // Chord progression — dreamy ambient voicings (frequencies in Hz)
    // Cmaj9 → Fmaj7 → Am7 → Dm9 (slow cycling)
    var CHORDS = [
        [130.81, 164.81, 196.00, 246.94, 293.66],  // C E G B D (Cmaj9)
        [174.61, 220.00, 261.63, 329.63],            // F A C E (Fmaj7)
        [220.00, 261.63, 329.63, 392.00],            // A C E G (Am7)
        [146.83, 174.61, 220.00, 261.63, 329.63],   // D F A C E (Dm9)
    ];

    var currentChordIndex = 0;
    var chordTimer = null;

    function init() {
        var playBtn = document.getElementById('music-toggle');
        var volumeSlider = document.getElementById('music-volume');

        // Restore volume from localStorage
        var savedVolume = localStorage.getItem(STORAGE_KEY);
        var initialVolume = savedVolume !== null ? parseFloat(savedVolume) : 0.3;

        if (playBtn) {
            playBtn.addEventListener('click', togglePlay);
        }

        if (volumeSlider) {
            volumeSlider.value = Math.round(initialVolume * 100);
            volumeSlider.addEventListener('input', function(e) {
                var vol = parseInt(e.target.value) / 100;
                localStorage.setItem(STORAGE_KEY, vol.toString());
                if (masterGain && isPlaying) {
                    masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.1);
                }
            });
        }

        // Music enable button on welcome page
        var enableBtn = document.getElementById('music-enable');
        if (enableBtn) {
            enableBtn.addEventListener('click', function() {
                togglePlay();
                enableBtn.textContent = isPlaying ? 'Music: ON' : 'Music: OFF';
                enableBtn.classList.toggle('active', isPlaying);
            });
        }

        // Handle the <audio> element gracefully (not needed, but avoid errors)
        var audioEl = document.getElementById('ambient-audio');
        if (audioEl) {
            audioEl.removeAttribute('src');
            audioEl.style.display = 'none';
        }
    }

    function createAudioContext() {
        if (audioCtx) return;

        var AudioContext = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AudioContext();
        masterGain = audioCtx.createGain();
        masterGain.gain.value = 0;
        masterGain.connect(audioCtx.destination);
    }

    function createPad(frequencies) {
        if (!audioCtx) return;

        // Stop existing oscillators with gentle release
        stopOscillators(1.5);

        var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
        var perVoiceGain = vol / Math.max(frequencies.length, 1) * 0.6;

        frequencies.forEach(function(freq, i) {
            // Main oscillator — sine for organ warmth
            var osc = audioCtx.createOscillator();
            osc.type = 'sine';
            osc.frequency.value = freq;

            // Slight detuning for richness (+/- 2 cents)
            osc.detune.value = (i % 2 === 0 ? 1 : -1) * (1 + i * 0.5);

            // Per-voice gain
            var gain = audioCtx.createGain();
            gain.gain.value = 0;

            // Low-pass filter for warmth
            var filter = audioCtx.createBiquadFilter();
            filter.type = 'lowpass';
            filter.frequency.value = 800 + i * 100;
            filter.Q.value = 0.5;

            // LFO for subtle tremolo
            var lfo = audioCtx.createOscillator();
            lfo.type = 'sine';
            lfo.frequency.value = 0.15 + i * 0.05; // Very slow modulation

            var lfoGain = audioCtx.createGain();
            lfoGain.gain.value = perVoiceGain * 0.15; // Subtle modulation depth

            lfo.connect(lfoGain);
            lfoGain.connect(gain.gain);

            // Connect: osc → filter → gain → master
            osc.connect(filter);
            filter.connect(gain);
            gain.connect(masterGain);

            // Start with gentle fade-in
            osc.start();
            lfo.start();

            // Smooth attack over 2 seconds
            gain.gain.setTargetAtTime(perVoiceGain, audioCtx.currentTime, 0.8);

            oscillators.push({osc: osc, lfo: lfo, gain: gain, filter: filter});

            // Second layer — triangle wave an octave up, very quiet
            var osc2 = audioCtx.createOscillator();
            osc2.type = 'triangle';
            osc2.frequency.value = freq * 2;
            osc2.detune.value = (i % 2 === 0 ? -3 : 3);

            var gain2 = audioCtx.createGain();
            gain2.gain.value = 0;

            var filter2 = audioCtx.createBiquadFilter();
            filter2.type = 'lowpass';
            filter2.frequency.value = 600;
            filter2.Q.value = 0.3;

            osc2.connect(filter2);
            filter2.connect(gain2);
            gain2.connect(masterGain);

            osc2.start();
            gain2.gain.setTargetAtTime(perVoiceGain * 0.2, audioCtx.currentTime, 1.2);

            oscillators.push({osc: osc2, gain: gain2, filter: filter2});
        });
    }

    function stopOscillators(releaseTime) {
        var t = releaseTime || 0.1;
        var now = audioCtx ? audioCtx.currentTime : 0;

        oscillators.forEach(function(o) {
            if (o.gain) {
                o.gain.gain.setTargetAtTime(0, now, t * 0.3);
            }
            // Schedule stop after release
            setTimeout(function() {
                try {
                    o.osc.stop();
                    if (o.lfo) o.lfo.stop();
                } catch (e) {
                    // Already stopped
                }
            }, t * 1000 + 200);
        });

        oscillators = [];
    }

    function nextChord() {
        currentChordIndex = (currentChordIndex + 1) % CHORDS.length;
        createPad(CHORDS[currentChordIndex]);
    }

    function startChordCycle() {
        createPad(CHORDS[currentChordIndex]);

        // Change chord every 8 seconds
        chordTimer = setInterval(nextChord, 8000);
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
                var vol = masterGain.gain.value;
                masterGain.gain.setTargetAtTime(0, audioCtx.currentTime, 0.4);

                setTimeout(function() {
                    stopChordCycle();
                    stopOscillators(0.1);
                    isPlaying = false;
                    updateButton();
                }, FADE_DURATION);
            }
        } else {
            createAudioContext();

            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }

            var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
            masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.5);

            isPlaying = true;
            updateButton();
            startChordCycle();
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
