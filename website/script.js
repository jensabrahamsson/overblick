/* Överblick Marketing Site — Minimal JS */

(function () {
  'use strict';

  var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── 1. Hamburger menu ── */
  var hamburger = document.querySelector('.nav-hamburger');
  var navLinks = document.querySelector('.nav-links');

  function closeMenu() {
    if (hamburger && navLinks) {
      hamburger.setAttribute('aria-expanded', 'false');
      navLinks.classList.remove('nav-links--open');
    }
  }

  if (hamburger && navLinks) {
    hamburger.addEventListener('click', function () {
      var expanded = hamburger.getAttribute('aria-expanded') === 'true';
      hamburger.setAttribute('aria-expanded', String(!expanded));
      navLinks.classList.toggle('nav-links--open');
    });

    /* Close on Escape */
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && navLinks.classList.contains('nav-links--open')) {
        closeMenu();
        hamburger.focus();
      }
    });

    /* Close on outside click */
    document.addEventListener('click', function (e) {
      if (navLinks.classList.contains('nav-links--open') &&
          !navLinks.contains(e.target) &&
          !hamburger.contains(e.target)) {
        closeMenu();
      }
    });

    /* Close when a nav link is clicked (mobile in-page navigation) */
    navLinks.querySelectorAll('.nav-link[href^="#"]').forEach(function (link) {
      link.addEventListener('click', function () { closeMenu(); });
    });
  }

  /* ── 2. Trait bar animation (IntersectionObserver) ── */
  var fills = document.querySelectorAll('.trait-fill');
  if (fills.length && 'IntersectionObserver' in window) {
    var traitObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var el = entry.target;
          el.style.transform = 'scaleX(' + (el.dataset.w / 100) + ')';
          traitObs.unobserve(el);
        }
      });
    }, { threshold: 0.2 });
    fills.forEach(function (el) { traitObs.observe(el); });
  }

  /* ── 3. Stat counter animation ── */
  function animateCounter(el) {
    if (reducedMotion) {
      el.textContent = parseInt(el.dataset.target, 10).toLocaleString() + (el.dataset.plus === 'true' ? '+' : '');
      return;
    }
    var target = parseInt(el.dataset.target, 10);
    var plus = el.dataset.plus === 'true';
    var duration = 600;
    var start = performance.now();
    function step(now) {
      var progress = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var current = Math.round(eased * target);
      el.textContent = current.toLocaleString() + (plus ? '+' : '');
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  var statNums = document.querySelectorAll('.stat-num[data-target]');
  if (statNums.length && 'IntersectionObserver' in window) {
    var statObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          animateCounter(entry.target);
          statObs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.5 });
    statNums.forEach(function (el) { statObs.observe(el); });
  }

  /* ── 4. Active nav link on scroll ── */
  var sections = document.querySelectorAll('section[id]');
  var navAnchors = document.querySelectorAll('.nav-link[href^="#"]');
  if (sections.length && navAnchors.length && 'IntersectionObserver' in window) {
    var current = '';
    var navObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) current = entry.target.id;
      });
      navAnchors.forEach(function (a) {
        a.classList.toggle('active', a.getAttribute('href') === '#' + current);
      });
    }, { rootMargin: '-30% 0px -60% 0px' });
    sections.forEach(function (s) { navObs.observe(s); });
  }

  /* ── 5. Hero fade-in ── */
  var hero = document.querySelector('.hero-content');
  if (hero && !reducedMotion) {
    hero.classList.add('hero-loading');
    requestAnimationFrame(function () {
      hero.classList.remove('hero-loading');
      hero.classList.add('hero-loaded');
    });
  }

  /* ── 6. Scroll reveal animations ── */
  if ('IntersectionObserver' in window) {
    var revealObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          if (reducedMotion) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'none';
          }
          entry.target.classList.add('revealed');
          revealObs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });

    /* Reveal section headers */
    document.querySelectorAll('.section-header').forEach(function (el) { revealObs.observe(el); });

    /* Reveal personality cards */
    document.querySelectorAll('.personality-card').forEach(function (el) { revealObs.observe(el); });

    /* Reveal plugin cards */
    document.querySelectorAll('.plugin-card').forEach(function (el) { revealObs.observe(el); });

    /* Reveal architecture columns */
    document.querySelectorAll('.arch-col').forEach(function (el) { revealObs.observe(el); });

    /* Reveal pipeline SVG */
    var pipelineSvg = document.querySelector('.pipeline-svg');
    if (pipelineSvg) revealObs.observe(pipelineSvg);

    /* Reveal IRC screenshot */
    var ircImg = document.querySelector('.irc-showcase-img');
    if (ircImg) revealObs.observe(ircImg);

    /* Reveal build-your-own */
    var buildOwn = document.querySelector('.build-your-own');
    if (buildOwn) revealObs.observe(buildOwn);

    /* Reveal get-started steps */
    document.querySelectorAll('.step').forEach(function (el) { revealObs.observe(el); });
  }
})();
