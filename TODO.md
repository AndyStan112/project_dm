# Project TODO

## 1. Scraper Foundation

- [x] Define Pydantic schemas for brands, families, variants, and reviews.
- [x] Add initial repository functions using `DATABASE_URL_WRITE`.
- [x] Add brand creation from a slug or full eMAG listing URL.
- [x] Implement listing pagination and product URL discovery.
- [x] Parse product family metadata, description, rating, and variants.
- [x] Parse the paginated review JSON endpoint.
- [x] Upsert families, variants, and reviews by stable eMAG IDs/PNKs.
- [x] Save progress after every listing page.
- [x] Checkpoint each product and review page atomically.
- [x] Add conservative randomized delays between listing requests.
- [x] Add conservative randomized delays between product requests.
- [x] Detect `403`, `429`, CAPTCHA, and invalid listing/product responses.

## 2. Resumable Jobs

- [x] Implement initial job creation for brand listings.
- [x] Add product job creation during brand collection.
- [x] Add review job creation during product collection.
- [x] Claim one pending job safely with PostgreSQL row locking.
- [x] Resume review collection from `current_offset`.
- [x] Implement `pending`, `running`, `paused`, `completed`, `skipped`,
      `failed`, and `blocked` transitions.
- [ ] Retry transient failures with a bounded attempt count.
- [ ] Stop all scraping when eMAG blocks or rate-limits the session.
- [x] Add CLI commands to run, pause, resume, skip, and inspect jobs.

## 3. Minimal Web UI

- [x] Add FastAPI, Jinja2, forms, and Uvicorn dependencies.
- [x] Create a dashboard using `DATABASE_URL_READ`.
- [ ] Display continuously managed scraper, avatar, and NLP service states.
- [x] Add bounded listing, product, and review worker controls.
- [ ] Add start, pause, and stop controls for continuous services.
- [x] Add a brand form accepting `apple`, `samsung`, or a full URL.
- [x] Display job progress, current offset, failures, and last errors.
- [x] Add pause, resume/retry, and skip actions for individual jobs.
- [x] Add product, variant, and review browsing pages.
- [x] Add review text, rating, purchase, and helpfulness filters.

## 4. Avatar Processing

- [ ] Collect representative examples of each avatar category.
- [ ] Store hashes for known default Apple avatar images.
- [ ] Classify initials-only API metadata as `default_name`.
- [ ] Add SHA-256 and perceptual-hash comparison.
- [ ] Add OCR only for ambiguous image-based initials.
- [ ] Classify results as `default_apple`, `default_name`, `custom`, or
      `unknown`.
- [ ] Delete downloaded images after classification.
- [ ] Add avatar-worker progress and controls to the UI.

## 5. Text Preprocessing And NLP

- [ ] Remove HTML and normalize whitespace.
- [ ] Preserve Romanian diacritics.
- [ ] Handle empty, duplicate, and very short reviews.
- [ ] Add language detection.
- [ ] Select and document a Romanian or multilingual sentiment model.
- [ ] Calculate sentiment label and confidence.
- [ ] Flag sentiment/rating mismatches.
- [ ] Add NLP-worker progress and controls to the UI.

## 6. Analysis Topics

- [ ] Document data cleaning and preprocessing decisions.
- [ ] Create word and character TF-IDF features.
- [ ] Compare useful unigram and n-gram configurations.
- [ ] Perform sentiment-versus-rating analysis.
- [ ] Build EDA visualizations by brand, model, rating, and verification.
- [ ] Define a helpful-review target, such as `votes > 0`.
- [ ] Train and tune a supervised helpfulness model.
- [ ] Evaluate precision, recall, F1, ROC-AUC, and cross-validation.
- [ ] Analyze feature importance or model coefficients.

## 7. Quality And Delivery

- [x] Add listing and product parser tests from explored page structures.
- [x] Add job state-transition tests.
- [ ] Add database repository tests with transaction rollback.
- [ ] Add avatar-classification tests.
- [ ] Add formatting and linting tools.
- [ ] Document setup, migrations, Playwright installation, and commands.
- [ ] Export a cleaned dataset for notebooks and model training.
- [ ] Prepare final project report and presentation figures.

## Immediate Next Milestone

Build one complete resumable flow:

1. Add an Apple brand job.
2. Discover one product family. Done for eMAG family `3128331`.
3. Store its variants. Eight variants stored in the controlled probe.
4. Collect all review pages with checkpoints. Parser and worker complete;
   controlled collection is currently at `20/579`.
5. Pause and resume the job from the CLI. Verified on review job `211`.

Do not start the UI or NLP pipeline until this flow works reliably.
