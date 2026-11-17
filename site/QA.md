# Release validation

Local release validation, 17 November 2026:

- Deterministic static build and SHA-256/size verification: 32 public files.
- Real product UI rebuilt from pinned clean source, with TypeScript checking.
- All generated public text assets checked for disallowed punctuation.
- No preview switcher, pretend form, remote fonts, remote scripts, or missing assets.
- Eight desktop/mobile dimensions, each with normal and reduced motion:
  1440x1000, 1280x800, 1024x768, 900x900, 768x900, 390x844, 320x720, 1440x700.
- No horizontal overflow, console errors, or external requests in browser QA.
- Hero pause/resume, scroll chapters, demo CTA destination, and FAQ interaction.
- axe-core 4.10.3: zero detected WCAG 2 A/AA and 2.1 AA violations on the
  desktop and mobile company page. This is an automated audit, not certification.
- Keyboard skip link reaches the main landmark; FAQ works from the keyboard.
- Privacy page checked at 1440, 390, and 320 pixels.
- Product flow: asset, related risk, raw request/response, in-memory note.
  Reload clears the note; no request is sent to a production API or target.
- The four fictional risks have scenario-specific descriptions and HTTP fixtures.
- Actual demo screens recaptured for the page and animated recording.
- CloudFront directory routing, canonical redirect, and query preservation tested.
- Isolated CloudFormation template: 12 website resources, no VPC, database,
  product services, DNS delegation, or cyber-platform changes.

GitHub Actions could not start due to the repository owner's billing lock.
This is recorded explicitly in PR #123; no successful CI status was fabricated.

Production cutover and public verification must be recorded separately after
execution. A locally passing preview alone is not proof of a live deployment.
