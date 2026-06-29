import Reveal from 'reveal.js';
import RevealNotes from 'reveal.js/plugin/notes/notes.esm.js';
import RevealMath from 'reveal.js/plugin/math/math.esm.js';
import RevealHighlight from 'reveal.js/plugin/highlight/highlight.esm.js';

import 'reveal.js/dist/reveal.css';
import 'reveal.js/dist/theme/white.css';
import 'reveal.js/plugin/highlight/monokai.css';

import '../css/slides.css';
import '../js/bibliography.js';

Reveal.initialize({
  hash: false,
  history: false,
  slideNumber: false,
  transition: 'slide',
  overview: true,
  controls: false,
  progress: true,
  plugins: [RevealNotes, RevealMath.KaTeX, RevealHighlight],
});

// -----------------------------
// Slide chrome: banner + footer
// -----------------------------

let footerNavLocked = false;
let footerUnlockTimer = null;
let footerPointerIsDown = false;
let slideChangedUnlockTimer = null;

const FOOTER_NAV_DEBUG = true;

function debugFooterNav(label, data = {}) {
  if (!FOOTER_NAV_DEBUG) return;

  const indices = Reveal.getIndices();
  const routes = Reveal.availableRoutes();

  console.log(`[footer-nav] ${label}`, {
    time: Math.round(performance.now()),
    locked: footerNavLocked,
    h: indices.h,
    v: indices.v,
    routes,
    ...data,
  });
}

function beginFooterPointerNav(button, event) {
  footerPointerIsDown = true;
  Reveal.configure({ keyboard: false });

  if (button && event?.pointerId != null) {
    button.setPointerCapture?.(event.pointerId);
  }
}

function endFooterPointerNav() {
  footerPointerIsDown = false;
  Reveal.configure({ keyboard: true });

  const footerPrev = document.getElementById("footer-prev");
  const footerNext = document.getElementById("footer-next");

  if (footerPrev) footerPrev.blur();
  if (footerNext) footerNext.blur();
}

function getCurrentSlideInfo(event) {
  const slide = event?.currentSlide || Reveal.getCurrentSlide();

  if (!slide) {
    return null;
  }

  const isTitleSlide = slide.classList.contains("title-slide");
  const section = slide.dataset.section || "";
  const title = slide.dataset.title || "";

  const indices = Reveal.getIndices(slide);
  const slideNumber = indices.h + 1;
  const formattedSlideNumber = String(slideNumber).padStart(2, "0");

  return {
    slide,
    isTitleSlide,
    section,
    title,
    formattedSlideNumber,
  };
}

function updateFooterArrowState() {
  const footerPrev = document.getElementById("footer-prev");
  const footerNext = document.getElementById("footer-next");

  const routes = Reveal.availableRoutes();

  if (footerPrev) {
    footerPrev.disabled = footerNavLocked || !routes.left;
  }

  if (footerNext) {
    footerNext.disabled = footerNavLocked || !routes.right;
  }
}

function updateSlideChrome(event) {
  const info = getCurrentSlideInfo(event);

  if (!info) {
    return;
  }

  document.body.classList.toggle("hide-section-banner", info.isTitleSlide);
  document.body.classList.toggle("hide-slide-footer", info.isTitleSlide);

  if (info.isTitleSlide) {
    return;
  }

  const bannerSection = document.getElementById("banner-section");
  const bannerTitle = document.getElementById("banner-title");

  const footerSlideNumber = document.getElementById("footer-slide-number");
  const footerSection = document.getElementById("footer-section");
  const footerTitle = document.getElementById("footer-title");

  if (bannerSection) {
    bannerSection.textContent = info.section;
  }

  if (bannerTitle) {
    bannerTitle.textContent = info.title;
  }

  if (footerSlideNumber && document.activeElement !== footerSlideNumber) {
    footerSlideNumber.value = info.formattedSlideNumber;
  }

  if (footerSection) {
    footerSection.textContent = info.section;
  }

  if (footerTitle) {
    footerTitle.textContent = info.title;
  }

  updateFooterArrowState();
}

function lockFooterNav(reason = "unknown") {
  debugFooterNav("lock requested", { reason });

  footerNavLocked = true;
  updateFooterArrowState();

  if (footerUnlockTimer) {
    window.clearTimeout(footerUnlockTimer);
  }

  footerUnlockTimer = window.setTimeout(() => {
    debugFooterNav("unlock fallback timer fired");
    unlockFooterNav("fallback-timer");
  }, 450);
}

function unlockFooterNav(reason = "unknown") {
  debugFooterNav("unlock requested", { reason });

  footerNavLocked = false;

  if (footerUnlockTimer) {
    window.clearTimeout(footerUnlockTimer);
    footerUnlockTimer = null;
  }

  updateFooterArrowState();
}

function footerNavigate(direction, sourceEvent = null) {
  debugFooterNav("footerNavigate called", {
    direction,
    eventType: sourceEvent?.type || null,
    pointerType: sourceEvent?.pointerType || null,
    button: sourceEvent?.button ?? null,
    detail: sourceEvent?.detail ?? null,
  });

  if (footerNavLocked) {
    debugFooterNav("navigation ignored: locked", { direction });
    return;
  }

  const routes = Reveal.availableRoutes();

  if (direction === "prev" && !routes.left) {
    debugFooterNav("navigation ignored: no left route", { direction });
    return;
  }

  if (direction === "next" && !routes.right) {
    debugFooterNav("navigation ignored: no right route", { direction });
    return;
  }

  lockFooterNav(`footer-${direction}`);

  const before = Reveal.getIndices();

  if (direction === "prev") {
    debugFooterNav("calling Reveal.prev()", { before });
    Reveal.prev();
  } else {
    debugFooterNav("calling Reveal.next()", { before });
    Reveal.next();
  }

  const after = Reveal.getIndices();
  debugFooterNav("Reveal call returned", { before, after });
}

function preventRevealKeyboardHandling(element) {
  if (!element) return;

  const stop = (event) => {
    event.stopPropagation();
  };

  element.addEventListener("keydown", stop);
  element.addEventListener("keyup", stop);
  element.addEventListener("keypress", stop);
}

function getHorizontalSlideCount() {
  return Reveal.getHorizontalSlides().length;
}

function resetFooterSlideNumber() {
  const footerSlideNumber = document.getElementById("footer-slide-number");
  if (!footerSlideNumber) return;

  const indices = Reveal.getIndices();
  footerSlideNumber.value = String(indices.h + 1).padStart(2, "0");
}

function goToFooterSlideNumber() {
  const footerSlideNumber = document.getElementById("footer-slide-number");
  if (!footerSlideNumber) return;

  const requestedSlide = Number.parseInt(footerSlideNumber.value.trim(), 10);
  const maxSlide = getHorizontalSlideCount();

  if (
    Number.isNaN(requestedSlide) ||
    requestedSlide < 1 ||
    requestedSlide > maxSlide
  ) {
    resetFooterSlideNumber();
    return;
  }

  Reveal.slide(requestedSlide - 1);
  footerSlideNumber.blur();
}

function setupFooterNavigation() {
  const footerPrev = document.getElementById("footer-prev");
  const footerNext = document.getElementById("footer-next");
  const footerSlideNumber = document.getElementById("footer-slide-number");

  if (footerPrev) {
    preventRevealKeyboardHandling(footerPrev);
  
    footerPrev.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("prev pointerdown", {
        pointerType: event.pointerType,
        button: event.button,
      });
  
      beginFooterPointerNav(footerPrev, event);
      footerNavigate("prev", event);
    });
  
    footerPrev.addEventListener("pointerup", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("prev pointerup", {
        pointerType: event.pointerType,
        button: event.button,
      });
  
      endFooterPointerNav();
    });
  
    footerPrev.addEventListener("pointercancel", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("prev pointercancel");
  
      endFooterPointerNav();
    });
  
    footerPrev.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("prev click ignored", {
        detail: event.detail,
      });
    });
  }
  
  if (footerNext) {
    preventRevealKeyboardHandling(footerNext);
  
    footerNext.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("next pointerdown", {
        pointerType: event.pointerType,
        button: event.button,
      });
  
      beginFooterPointerNav(footerNext, event);
      footerNavigate("next", event);
    });
  
    footerNext.addEventListener("pointerup", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("next pointerup", {
        pointerType: event.pointerType,
        button: event.button,
      });
  
      endFooterPointerNav();
    });
  
    footerNext.addEventListener("pointercancel", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("next pointercancel");
  
      endFooterPointerNav();
    });
  
    footerNext.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
  
      debugFooterNav("next click ignored", {
        detail: event.detail,
      });
    });
  }

  if (footerSlideNumber) {
    footerSlideNumber.addEventListener("focus", () => {
      footerSlideNumber.select();
    });

    footerSlideNumber.addEventListener("keydown", (event) => {
      event.stopPropagation();

      if (event.key === "Enter") {
        event.preventDefault();
        goToFooterSlideNumber();
      }

      if (event.key === "Escape") {
        event.preventDefault();
        resetFooterSlideNumber();
        footerSlideNumber.blur();
      }
    });

    footerSlideNumber.addEventListener("change", () => {
      goToFooterSlideNumber();
    });
  }

  updateFooterArrowState();

  window.addEventListener("pointerup", () => {
    if (!footerPointerIsDown) return;
  
    debugFooterNav("window pointerup: keyboard restored");
    endFooterPointerNav();
  });
}

Reveal.on("ready", (event) => {
  debugFooterNav("Reveal ready");
  updateSlideChrome(event);
  setupFooterNavigation();
});

Reveal.on("slidechanged", (event) => {
  debugFooterNav("Reveal slidechanged", {
    previous: event.previousSlide?.dataset?.title || null,
    current: event.currentSlide?.dataset?.title || null,
  });

  updateSlideChrome(event);

  if (slideChangedUnlockTimer) {
    window.clearTimeout(slideChangedUnlockTimer);
  }

  slideChangedUnlockTimer = window.setTimeout(() => {
    slideChangedUnlockTimer = null;
    debugFooterNav("slidechanged unlock timeout fired");
    unlockFooterNav("slidechanged-timeout");
  }, 300);
});
