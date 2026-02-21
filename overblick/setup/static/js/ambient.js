/**
 * Ambient Music — Bright Lounge Organ (Klaus Wunderlich homage)
 *
 * Happy, bouncy electronic organ in the style of 70s easy listening.
 * Major keys, swinging rhythm, cheerful melody. Web Audio API only.
 */

(function () {
    'use strict';

    var STORAGE_KEY = 'overblick_setup_volume';
    var BPM = 132;
    var BEAT = 60 / BPM;          // seconds per beat
    var EIGHTH = BEAT / 2;
    var BAR = BEAT * 4;

    var audioCtx = null;
    var masterGain = null;
    var isPlaying = false;
    var schedulerTimer = null;
    var hasAutostarted = false;
    var nextNoteTime = 0;
    var currentStep = 0;
    var TOTAL_STEPS = 0; // calculated from arrangement

    function midiToFreq(m) { return 440 * Math.pow(2, (m - 69) / 12); }

    // ── Arrangement: 16-bar loop ──────────────────────────────────
    // Bright bossa nova / lounge feel — all major & dominant chords
    //
    // Melody in right hand (organ), walking bass, comping chords

    // Chord data: [name, bass MIDI notes (4 beats), chord MIDI notes, melody MIDI notes (8 eighths)]
    var BARS = [
        // Section A — sunny and bright
        { bass: [60, 64, 67, 64], chord: [72, 76, 79],    mel: [84, 86, 88, 86, 84, 83, 84, 0] },  // C
        { bass: [65, 69, 72, 69], chord: [77, 81, 84],    mel: [86, 88, 89, 88, 86, 84, 86, 0] },  // F
        { bass: [67, 71, 74, 71], chord: [74, 77, 83],    mel: [88, 91, 88, 86, 84, 83, 84, 86] }, // G
        { bass: [60, 64, 67, 64], chord: [72, 76, 79],    mel: [84, 0, 84, 86, 88, 0, 84, 0] },    // C

        // Section B — lift
        { bass: [65, 69, 72, 69], chord: [77, 81, 84],    mel: [89, 88, 86, 84, 86, 88, 89, 0] },  // F
        { bass: [60, 64, 67, 64], chord: [72, 76, 79, 83],mel: [91, 89, 88, 86, 88, 89, 91, 0] },  // Cmaj7
        { bass: [62, 65, 69, 65], chord: [74, 77, 81],    mel: [86, 84, 83, 81, 83, 84, 86, 0] },  // Dm
        { bass: [67, 71, 74, 71], chord: [74, 77, 83],    mel: [88, 86, 84, 83, 81, 83, 84, 0] },  // G

        // Section A' — return with variation
        { bass: [60, 64, 67, 64], chord: [72, 76, 79],    mel: [84, 88, 91, 88, 84, 0, 84, 86] },  // C
        { bass: [65, 69, 72, 69], chord: [77, 81, 84],    mel: [88, 89, 88, 86, 84, 86, 88, 0] },  // F
        { bass: [67, 71, 74, 67], chord: [74, 77, 83],    mel: [91, 89, 88, 86, 88, 91, 93, 0] },  // G
        { bass: [60, 64, 67, 64], chord: [72, 76, 79],    mel: [91, 0, 88, 0, 84, 0, 0, 0] },      // C

        // Section C — bridge (brighter)
        { bass: [69, 72, 76, 72], chord: [81, 84, 88],    mel: [93, 91, 88, 91, 93, 0, 91, 0] },   // A (major!)
        { bass: [62, 65, 69, 65], chord: [74, 77, 81],    mel: [86, 88, 89, 88, 86, 84, 86, 0] },  // Dm
        { bass: [67, 71, 74, 71], chord: [74, 79, 83],    mel: [88, 91, 93, 91, 88, 86, 84, 83] }, // G7
        { bass: [60, 64, 67, 72], chord: [72, 76, 79],    mel: [84, 0, 0, 0, 84, 86, 88, 0] },     // C (home)
    ];

    TOTAL_STEPS = BARS.length * 8; // 8 eighths per bar

    // ── Synth engine ──────────────────────────────────────────────

    function playOrganNote(midi, time, dur, vel) {
        if (!audioCtx || midi === 0) return;
        var freq = midiToFreq(midi);

        var env = audioCtx.createGain();
        env.gain.value = 0;
        env.gain.setTargetAtTime(vel, time, 0.008);
        env.gain.setTargetAtTime(vel * 0.7, time + 0.03, 0.1);
        env.gain.setTargetAtTime(0, time + dur - 0.03, 0.025);

        // Bright Hammond: fundamental + 2nd + 3rd + 4th harmonics
        var ratios = [1, 2, 3, 4];
        var levels = [1.0, 0.6, 0.3, 0.15];

        ratios.forEach(function (r, i) {
            // Two voices, slightly detuned (chorus)
            [-2.5, 2.5].forEach(function (det) {
                var osc = audioCtx.createOscillator();
                osc.type = i < 2 ? 'sine' : 'triangle';
                osc.frequency.value = freq * r;
                osc.detune.value = det + (Math.random() - 0.5);
                var g = audioCtx.createGain();
                g.gain.value = levels[i] * 0.04;
                osc.connect(g);
                g.connect(env);
                osc.start(time);
                osc.stop(time + dur + 0.1);
            });
        });

        env.connect(masterGain);
    }

    function playBassNote(midi, time, dur) {
        if (!audioCtx || midi === 0) return;
        var freq = midiToFreq(midi - 12); // one octave lower

        var env = audioCtx.createGain();
        env.gain.value = 0;
        env.gain.setTargetAtTime(0.18, time, 0.015);
        env.gain.setTargetAtTime(0.12, time + 0.04, 0.1);
        env.gain.setTargetAtTime(0, time + dur - 0.04, 0.04);

        var filter = audioCtx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 600;
        filter.connect(env);

        [1, 2].forEach(function (r, i) {
            var osc = audioCtx.createOscillator();
            osc.type = 'sine';
            osc.frequency.value = freq * r;
            var g = audioCtx.createGain();
            g.gain.value = i === 0 ? 0.12 : 0.03;
            osc.connect(g);
            g.connect(filter);
            osc.start(time);
            osc.stop(time + dur + 0.1);
        });

        env.connect(masterGain);
    }

    function playChordHit(notes, time, dur, vel) {
        notes.forEach(function (n) {
            playOrganNote(n, time, dur, vel || 0.03);
        });
    }

    // ── Scheduler ─────────────────────────────────────────────────

    function scheduleBar(barIdx, barTime) {
        var bar = BARS[barIdx % BARS.length];

        // Walking bass — quarter notes
        bar.bass.forEach(function (note, beat) {
            playBassNote(note, barTime + beat * BEAT, BEAT * 0.85);
        });

        // Comping chords — bossa rhythm: beat 1 (short), "and" of 2 (accent), beat 4
        playChordHit(bar.chord, barTime, BEAT * 0.4, 0.025);
        playChordHit(bar.chord, barTime + BEAT * 1.5, BEAT * 0.8, 0.035);
        playChordHit(bar.chord, barTime + BEAT * 3, BEAT * 0.5, 0.02);

        // Melody — eighth notes
        bar.mel.forEach(function (note, eighth) {
            if (note === 0) return;
            var t = barTime + eighth * EIGHTH;
            var dur = EIGHTH * 0.8;
            // Slight swing feel
            if (eighth % 2 === 1) t += EIGHTH * 0.08;
            playOrganNote(note, t, dur, 0.07);
        });
    }

    var currentBar = 0;
    var LOOKAHEAD = 0.15;

    function scheduler() {
        if (!isPlaying) return;

        while (nextNoteTime < audioCtx.currentTime + LOOKAHEAD + BAR) {
            scheduleBar(currentBar, nextNoteTime);
            currentBar++;
            nextNoteTime += BAR;
        }
    }

    // ── Controls ──────────────────────────────────────────────────

    function createAudioContext() {
        if (audioCtx) return;
        var AC = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AC();

        masterGain = audioCtx.createGain();
        masterGain.gain.value = 0;

        // Simple reverb
        var delays = [0.09, 0.14, 0.21, 0.28];
        var fbs = [0.30, 0.25, 0.20, 0.15];
        var revMix = audioCtx.createGain();
        revMix.gain.value = 0.25;

        var revFilter = audioCtx.createBiquadFilter();
        revFilter.type = 'lowpass';
        revFilter.frequency.value = 4000;

        delays.forEach(function (dt, i) {
            var d = audioCtx.createDelay(1.0);
            d.delayTime.value = dt;
            var fb = audioCtx.createGain();
            fb.gain.value = fbs[i];
            masterGain.connect(d);
            d.connect(fb);
            fb.connect(d);
            fb.connect(revFilter);
        });

        revFilter.connect(revMix);
        revMix.connect(audioCtx.destination);
        masterGain.connect(audioCtx.destination);
    }

    function startMusic() {
        createAudioContext();
        if (audioCtx.state === 'suspended') audioCtx.resume();

        var vol = parseFloat(localStorage.getItem(STORAGE_KEY) || '0.3');
        masterGain.gain.setTargetAtTime(vol, audioCtx.currentTime, 0.3);

        isPlaying = true;
        currentBar = 0;
        nextNoteTime = audioCtx.currentTime + 0.1;

        var btn = document.getElementById('music-toggle');
        if (btn) btn.style.animation = '';

        updateButton();
        scheduler();
        schedulerTimer = setInterval(scheduler, 80);
    }

    function stopMusic() {
        isPlaying = false;
        if (schedulerTimer) { clearInterval(schedulerTimer); schedulerTimer = null; }
        if (masterGain && audioCtx) {
            masterGain.gain.setTargetAtTime(0, audioCtx.currentTime, 0.2);
        }
        updateButton();
    }

    function togglePlay() {
        if (isPlaying) { stopMusic(); } else { startMusic(); }
    }

    function updateButton() {
        var btn = document.getElementById('music-toggle');
        if (btn) {
            btn.classList.toggle('playing', isPlaying);
            btn.setAttribute('aria-label', isPlaying ? 'Stop music' : 'Play music');
            btn.textContent = isPlaying ? '\u23F9' : '\u25B6';
        }
    }

    function attemptAutoStart() {
        try {
            createAudioContext();
            if (audioCtx && audioCtx.state === 'suspended') {
                var btn = document.getElementById('music-toggle');
                if (btn) btn.style.animation = 'pulse 2s infinite';
                return;
            }
            hasAutostarted = true;
            startMusic();
        } catch (e) { /* no audio support */ }
    }

    function init() {
        var playBtn = document.getElementById('music-toggle');
        var volumeSlider = document.getElementById('music-volume');

        var saved = localStorage.getItem(STORAGE_KEY);
        var initVol = saved !== null ? parseFloat(saved) : 0.3;

        if (playBtn) playBtn.addEventListener('click', togglePlay);
        if (volumeSlider) {
            volumeSlider.value = Math.round(initVol * 100);
            volumeSlider.addEventListener('input', function (e) {
                var v = parseInt(e.target.value) / 100;
                localStorage.setItem(STORAGE_KEY, v.toString());
                if (masterGain && isPlaying) {
                    masterGain.gain.setTargetAtTime(v, audioCtx.currentTime, 0.1);
                }
            });
        }

        var audioEl = document.getElementById('ambient-audio');
        if (audioEl) { audioEl.removeAttribute('src'); audioEl.style.display = 'none'; }

        attemptAutoStart();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
