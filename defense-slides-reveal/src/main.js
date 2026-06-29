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
  slideNumber: true,
  transition: 'slide',
  overview: true,
  plugins: [RevealNotes, RevealMath.KaTeX, RevealHighlight],
});

function updateSectionBanner(event) {
  const slide = event.currentSlide || Reveal.getCurrentSlide();

  const banner = document.getElementById('section-banner');
  const section = document.getElementById('banner-section');
  const title = document.getElementById('banner-title');

  if (!slide || slide.classList.contains('title-slide')) {
    document.body.classList.add('hide-section-banner');
    return;
  }

  document.body.classList.remove('hide-section-banner');
  section.textContent = slide.dataset.section || '';
  title.textContent = slide.dataset.title || '';
}

Reveal.on('ready', updateSectionBanner);
Reveal.on('slidechanged', updateSectionBanner);
