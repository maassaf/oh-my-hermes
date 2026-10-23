/*!
 * Oh My Hermes - site runtime.
 * i18n switching, scroll motion, pointer specular, memory ladder,
 * and the electric aura/spark FX layer. No dependencies, no network calls.
 */
(function () {
  "use strict";

  var STORE_KEY = "omh.lang";
  var FALLBACK = "en";
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* =============================================================== i18n */

  var I18N = window.OMH_I18N || { meta: {}, strings: {} };
  var SUPPORTED = Object.keys(I18N.meta);

  function normalize(tag) {
    if (!tag) return null;
    var lower = String(tag).toLowerCase();
    if (lower.indexOf("zh") === 0) return SUPPORTED.indexOf("zh") > -1 ? "zh" : null;
    var base = lower.split("-")[0];
    return SUPPORTED.indexOf(base) > -1 ? base : null;
  }

  // English by default. Localization is an explicit choice (?lang= or the
  // header switch), never inferred from the browser or OS locale.
  function initialLang() {
    var stored = null;
    try {
      stored = window.localStorage.getItem(STORE_KEY);
    } catch (err) {
      stored = null;
    }
    var fromQuery = new URLSearchParams(window.location.search).get("lang");
    var list = [fromQuery, stored];
    for (var i = 0; i < list.length; i += 1) {
      var hit = normalize(list[i]);
      if (hit) return hit;
    }
    return FALLBACK;
  }

  function translate(key, lang) {
    var entry = I18N.strings[key];
    if (!entry) return null;
    return entry[lang] != null ? entry[lang] : entry[FALLBACK];
  }

  function applyLang(lang) {
    var meta = I18N.meta[lang] || I18N.meta[FALLBACK];
    document.documentElement.lang = (meta && meta.htmlLang) || lang;

    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      var value = translate(node.getAttribute("data-i18n"), lang);
      if (value != null) node.textContent = value;
    });

    // Trusted, author-owned markup from i18n.js only.
    document.querySelectorAll("[data-i18n-html]").forEach(function (node) {
      var value = translate(node.getAttribute("data-i18n-html"), lang);
      if (value != null) node.innerHTML = value;
    });

    // "attr:key" pairs, comma separated.
    document.querySelectorAll("[data-i18n-attr]").forEach(function (node) {
      node.getAttribute("data-i18n-attr").split(",").forEach(function (pair) {
        var parts = pair.split(":");
        if (parts.length !== 2) return;
        var value = translate(parts[1].trim(), lang);
        if (value != null) node.setAttribute(parts[0].trim(), value);
      });
    });

    var current = document.querySelector("[data-lang-current]");
    if (current && meta) current.textContent = meta.short;

    document.querySelectorAll(".langswitch__menu [data-lang]").forEach(function (button) {
      var option = button.closest("[role='option']");
      if (option) option.setAttribute("aria-selected", String(button.dataset.lang === lang));
    });

    try {
      window.localStorage.setItem(STORE_KEY, lang);
    } catch (err) {
      /* storage unavailable (private mode); language still applies for this page */
    }
  }

  function initLangSwitch() {
    var root = document.querySelector("[data-lang-switch]");
    if (!root) return;

    var button = root.querySelector(".langswitch__button");
    var menu = root.querySelector(".langswitch__menu");
    if (!button || !menu) return;

    function close() {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    function open() {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      var selected = menu.querySelector("[aria-selected='true'] button") || menu.querySelector("button");
      if (selected) selected.focus();
    }

    button.addEventListener("click", function (event) {
      event.stopPropagation();
      if (menu.hidden) open();
      else close();
    });

    menu.addEventListener("click", function (event) {
      var choice = event.target.closest("[data-lang]");
      if (!choice) return;
      applyLang(choice.dataset.lang);
      close();
      button.focus();
    });

    document.addEventListener("click", function (event) {
      if (!root.contains(event.target)) close();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || menu.hidden) return;
      close();
      button.focus();
    });

    menu.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      var items = Array.prototype.slice.call(menu.querySelectorAll("button"));
      var index = items.indexOf(document.activeElement);
      var next = event.key === "ArrowDown" ? index + 1 : index - 1;
      items[(next + items.length) % items.length].focus();
    });
  }

  /* ============================================================= motion */

  function initReveal() {
    var targets = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
    if (!targets.length) return;

    targets.forEach(function (node) {
      var delay = node.getAttribute("data-reveal-delay");
      if (delay) node.style.setProperty("--reveal-delay", delay + "ms");
    });

    if (reduceMotion || !("IntersectionObserver" in window)) {
      targets.forEach(function (node) {
        node.classList.add("is-revealed");
      });
      targets.forEach(runCounters);
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-revealed");
          runCounters(entry.target);
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.12 }
    );

    targets.forEach(function (node) {
      observer.observe(node);
    });
  }

  function runCounters(scope) {
    scope.querySelectorAll("[data-count-to]").forEach(function (node) {
      if (node.dataset.counted === "true") return;
      node.dataset.counted = "true";

      var target = parseInt(node.getAttribute("data-count-to"), 10);
      if (isNaN(target)) return;
      if (reduceMotion || target === 0) {
        node.textContent = String(target);
        return;
      }

      var duration = 1100;
      var started = null;

      function tick(now) {
        if (started === null) started = now;
        var t = Math.min(1, (now - started) / duration);
        var eased = 1 - Math.pow(1 - t, 3);
        node.textContent = String(Math.round(target * eased));
        if (t < 1) window.requestAnimationFrame(tick);
      }

      window.requestAnimationFrame(tick);
    });
  }

  /* The memory ladder: the spine fills as the section scrolls past, and each
     layer lights up once its node reaches the reading line. */
  function initMemoryLadder() {
    var track = document.querySelector("[data-mem-track]");
    if (!track) return;

    var steps = Array.prototype.slice.call(track.querySelectorAll("[data-mem-step]"));
    if (!steps.length) return;

    if (reduceMotion) {
      track.style.setProperty("--mem-progress", "1");
      steps.forEach(function (step) {
        step.classList.add("is-active");
      });
      return;
    }

    var queued = false;

    function update() {
      queued = false;

      var box = track.getBoundingClientRect();
      var line = window.innerHeight * 0.58;
      var progress = (line - box.top) / Math.max(box.height, 1);
      track.style.setProperty("--mem-progress", String(Math.min(1, Math.max(0, progress))));

      steps.forEach(function (step) {
        var node = step.querySelector(".mem-step__node") || step;
        var top = node.getBoundingClientRect().top;
        step.classList.toggle("is-active", top < line);
      });
    }

    function onScroll() {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(update);
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    update();
  }

  /* Looping rails: the markup ships two copies; double until one copy
     is at least as wide as the viewport so translate(-50%) never shows a gap. */
  function initMarquees() {
    document.querySelectorAll(".marquee__track, .logo-rail__track").forEach(function (track) {
      var guard = 0;
      while (track.scrollWidth < window.innerWidth * 2 && guard < 4) {
        track.innerHTML += track.innerHTML;
        guard += 1;
      }
    });
  }

  function initSpecular() {
    if (reduceMotion || !window.matchMedia("(hover: hover)").matches) return;

    document.querySelectorAll(".glass-card").forEach(function (card) {
      card.addEventListener("pointermove", function (event) {
        var box = card.getBoundingClientRect();
        card.style.setProperty("--mx", ((event.clientX - box.left) / box.width) * 100 + "%");
        card.style.setProperty("--my", ((event.clientY - box.top) / box.height) * 100 + "%");
      });
    });
  }

  function initCopyButtons() {
    document.querySelectorAll("[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        var source = document.querySelector(button.getAttribute("data-copy"));
        if (!source || !navigator.clipboard) return;

        navigator.clipboard.writeText(source.textContent.trim()).then(function () {
          var label = button.querySelector("span") || button;
          var lang = normalize(document.documentElement.lang) || FALLBACK;
          var done = translate("hero.copied", lang) || "Copied";
          var idle = translate("hero.copy", lang) || "Copy";

          label.textContent = done;
          button.dataset.copied = "true";
          window.setTimeout(function () {
            label.textContent = idle;
            delete button.dataset.copied;
          }, 1600);
        });
      });
    });
  }

  /* ====================================================== electric FX */
  /* Blue sparks drifting over the whole page, plus lightning filaments
     whose energy follows scroll velocity. Canvas 2D, additive blending. */

  function initElectricFX() {
    var canvas = document.querySelector("[data-fx]");
    if (!canvas || reduceMotion) return;

    var ctx = canvas.getContext("2d");
    if (!ctx) return;

    var W = 0;
    var H = 0;
    var dpr = 1;
    var sparks = [];
    var bolts = [];
    var energy = 0;          // 0..1, driven by scroll velocity
    var lastScrollY = window.scrollY;
    var lastScrollT = performance.now();

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = canvas.clientWidth;
      H = canvas.clientHeight;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function spawnSpark() {
      return {
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.16,
        vy: -0.06 - Math.random() * 0.2,
        r: 0.6 + Math.random() * 1.5,
        life: 0,
        ttl: 340 + Math.random() * 460,
        hue: 200 + Math.random() * 26
      };
    }

    /* A lightning filament: jittered polyline with a couple of branches. */
    function spawnBolt(strength) {
      var startX = Math.random() * W;
      var startY = Math.random() * H * 0.85;
      var segments = 13 + Math.floor(Math.random() * 9);
      var length = (0.3 + Math.random() * 0.34) * H * (0.75 + strength);
      var angle = Math.PI / 2 + (Math.random() - 0.5) * 1.5;
      var points = [{ x: startX, y: startY }];
      var x = startX;
      var y = startY;

      for (var i = 1; i <= segments; i += 1) {
        var t = i / segments;
        x += Math.cos(angle) * (length / segments) + (Math.random() - 0.5) * 26;
        y += Math.sin(angle) * (length / segments) + (Math.random() - 0.5) * 14;
        points.push({ x: x, y: y });
        if (Math.random() < 0.2 && t > 0.25 && t < 0.85) {
          // short branch
          var bx = x;
          var by = y;
          var branch = [{ x: bx, y: by }];
          var bAngle = angle + (Math.random() < 0.5 ? -1 : 1) * (0.5 + Math.random() * 0.6);
          for (var j = 0; j < 4; j += 1) {
            bx += Math.cos(bAngle) * 22 + (Math.random() - 0.5) * 14;
            by += Math.sin(bAngle) * 22 + (Math.random() - 0.5) * 14;
            branch.push({ x: bx, y: by });
          }
          bolts.push({ points: branch, life: 0, ttl: 170 + Math.random() * 130, width: 1.0, alpha: 0.5 });
        }
      }

      bolts.push({
        points: points,
        life: 0,
        ttl: 190 + Math.random() * 170,
        width: 1.7 + strength * 1.3,
        alpha: 0.7 + strength * 0.28
      });
    }

    function drawBolt(bolt) {
      var t = bolt.life / bolt.ttl;
      var fade = t < 0.18 ? t / 0.18 : 1 - (t - 0.18) / 0.82;
      if (fade <= 0) return;

      ctx.strokeStyle = "rgba(150, 210, 255, " + (bolt.alpha * fade) + ")";
      ctx.lineWidth = bolt.width;
      ctx.shadowColor = "rgba(80, 160, 255, " + 0.9 * fade + ")";
      ctx.shadowBlur = 14;
      ctx.beginPath();
      bolt.points.forEach(function (p, i) {
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    var visible = !document.hidden;
    document.addEventListener("visibilitychange", function () {
      visible = !document.hidden;
    });

    window.addEventListener("scroll", function () {
      var now = performance.now();
      var dy = Math.abs(window.scrollY - lastScrollY);
      var dt = Math.max(now - lastScrollT, 16);
      var velocity = dy / dt; // px per ms
      energy = Math.min(1, energy + velocity * 0.16);
      lastScrollY = window.scrollY;
      lastScrollT = now;
    }, { passive: true });

    var last = performance.now();

    function frame(now) {
      window.requestAnimationFrame(frame);
      if (!visible) return;

      var dt = Math.min(now - last, 50);
      last = now;

      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";

      // idle shimmer + scroll surge
      energy *= 0.965;
      var targetCount = 46 + Math.round(energy * 40);
      while (sparks.length < targetCount) sparks.push(spawnSpark());

      for (var i = sparks.length - 1; i >= 0; i -= 1) {
        var s = sparks[i];
        s.life += dt;
        if (s.life > s.ttl || s.y < -12) {
          sparks.splice(i, 1);
          continue;
        }
        var drift = 1 + energy * 3.2;
        s.x += s.vx * dt * drift;
        s.y += s.vy * dt * drift;

        var lifeT = s.life / s.ttl;
        var alpha = (lifeT < 0.2 ? lifeT / 0.2 : 1 - (lifeT - 0.2) / 0.8) * (0.34 + energy * 0.5);
        ctx.fillStyle = "hsla(" + s.hue + ", 92%, 72%, " + alpha + ")";
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r * (1 + energy * 0.7), 0, Math.PI * 2);
        ctx.fill();
      }

      // lightning: rare at idle, frequent under fast scroll
      var boltChance = 0.0012 + energy * 0.022;
      if (Math.random() < boltChance && bolts.length < 4) {
        spawnBolt(energy);
      }

      for (var b = bolts.length - 1; b >= 0; b -= 1) {
        var bolt = bolts[b];
        bolt.life += dt;
        if (bolt.life > bolt.ttl) {
          bolts.splice(b, 1);
          continue;
        }
        drawBolt(bolt);
      }

      ctx.globalCompositeOperation = "source-over";
    }

    resize();
    window.addEventListener("resize", resize);
    window.requestAnimationFrame(frame);
  }

  /* =============================================================== boot */

  function boot() {
    applyLang(initialLang());
    initLangSwitch();
    initReveal();
    initMarquees();
    initMemoryLadder();
    initSpecular();
    initCopyButtons();
    initElectricFX();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
