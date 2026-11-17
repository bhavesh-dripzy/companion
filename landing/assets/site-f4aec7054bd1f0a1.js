(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const desktop = matchMedia('(min-width: 901px)');
  const nav = document.querySelector('.site-nav');
  const readingBar = document.querySelector('.page-reading-progress');
  const chapters = [...document.querySelectorAll('[data-chapter]')];
  const frames = [...document.querySelectorAll('[data-story-frame]')];
  const links = [...document.querySelectorAll('[data-story-link]')];
  const screen = document.querySelector('[data-story-screen]');
  const progress = document.querySelector('.story-progress > div');
  const status = document.querySelector('[data-story-label]');
  const labels = ['01 / Assets in context', '02 / The issue worth opening', '03 / The business consequence', '04 / Inspect the supporting evidence'];
  const descriptions = [
    'Vaayu Assets view with fictional demo data.',
    'Vaayu Risks view with a synthetic cross-account invoice access issue highlighted.',
    'Vaayu risk overview explaining the fictional impact and remediation.',
    'Vaayu Evidence tab showing synthetic request and response data.'
  ];
  let current = -1;
  let framePending = false;
  let manualTarget = null;
  let manualRelease = null;
  function showChapter(index) {
    if (current === index) return;
    current = index;
    chapters.forEach((chapter, i) => chapter.classList.toggle('is-active', i === index));
    frames.forEach((frame, i) => frame.classList.toggle('is-current', i === index));
    links.forEach((link, i) => {
      link.classList.toggle('is-current', i === index);
      if (i === index) link.setAttribute('aria-current', 'step');
      else link.removeAttribute('aria-current');
    });
    screen.dataset.current = String(index);
    screen.setAttribute('aria-label', descriptions[index]);
    status.textContent = labels[index];
  }
  function update() {
    framePending = false;
    const total = Math.max(1, document.documentElement.scrollHeight - innerHeight);
    readingBar.style.transform = `scaleX(${Math.max(0, Math.min(1, scrollY / total))})`;
    nav.classList.toggle('is-scrolled', scrollY > 30);
    let index = 0;
    const trigger = innerHeight * (desktop.matches ? 0.46 : 0.36);
    chapters.forEach((chapter, i) => { if (chapter.getBoundingClientRect().top <= trigger) index = i; });
    if (manualTarget !== null) index = manualTarget;
    showChapter(index);
    const active = chapters[index].getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (trigger - active.top) / Math.max(1, active.height)));
    progress.style.transform = `scaleX(${(index + fraction) / chapters.length})`;
  }
  function onScroll() { if (!framePending) { framePending = true; requestAnimationFrame(update); } }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  // Anchor links use native browser scrolling. No wheel interception or scroll locking.
  links.forEach((link, index) => {
    link.addEventListener('click', () => {
      manualTarget = index;
      clearTimeout(manualRelease);
      showChapter(index);
      manualRelease = setTimeout(() => { manualTarget = null; update(); }, reduced.matches ? 80 : 1000);
    });
  });
  update();

  if (!reduced.matches) {
    document.body.classList.add('is-motion-ready');
    const reveal = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-entered');
        reveal.unobserve(entry.target);
      }
    }), { threshold: 0.12 });
    document.querySelectorAll('[data-enter]').forEach(node => {
      node.classList.add('enter-ready');
      reveal.observe(node);
    });
  }

  // A slow hero sequence uses captures of the real UI, not simulated testing output.
  const heroFrames = [...document.querySelectorAll('[data-hero-frame]')];
  const heroSteps = [...document.querySelectorAll('[data-hero-step]')];
  const heroContainer = document.querySelector('.hero-screens');
  const heroLabel = document.querySelector('.hero-sequence-label');
  const heroToggle = document.querySelector('.hero-motion-toggle');
  const heroLabels = ['01 / Risks in context', '02 / Understand the impact', '03 / Inspect the evidence'];
  let heroIndex = 0;
  let heroPaused = reduced.matches;
  let heroVisible = false;
  let heroTimer = null;
  function showHero(index) {
    heroIndex = index;
    heroFrames.forEach((frame, i) => frame.classList.toggle('active', i === index));
    heroSteps.forEach((step, i) => {
      step.classList.toggle('active', i === index);
      step.setAttribute('aria-pressed', String(i === index));
    });
    heroLabel.textContent = heroLabels[index];
    heroContainer.setAttribute('aria-label', `Actual Vaayu ${['Risks', 'Impact', 'Evidence'][index]} view with synthetic demo data.`);
  }
  function scheduleHero() {
    clearInterval(heroTimer);
    const running = !heroPaused && !reduced.matches && heroVisible && !document.hidden;
    if (running) heroTimer = setInterval(() => showHero((heroIndex + 1) % 3), 6500);
    heroToggle.textContent = heroPaused ? 'Play ▷' : 'Pause Ⅱ';
    heroToggle.setAttribute('aria-label', heroPaused ? 'Play hero animation' : 'Pause hero animation');
  }
  heroSteps.forEach((step, index) => {
    step.addEventListener('click', () => { heroPaused = true; showHero(index); scheduleHero(); });
  });
  heroToggle.addEventListener('click', () => { heroPaused = !heroPaused; scheduleHero(); });
  new IntersectionObserver(entries => { heroVisible = entries[0].isIntersecting; scheduleHero(); }, { threshold: 0.2 }).observe(heroContainer);
  document.addEventListener('visibilitychange', scheduleHero);
  reduced.addEventListener('change', () => {
    if (reduced.matches) { heroPaused = true; document.body.classList.remove('is-motion-ready'); }
    scheduleHero();
  });
  showHero(0);
  scheduleHero();

})();
