# Changelog

Notable changes to Parsival — features added, behavior changes, and fixes
(`STANDARDS.md` DOC-009). Newest first.

Sections are milestone tags where one exists, and otherwise the date the work
landed. Tagging began at `v0.1-standards`; everything below it predates the
first tag, so those sections stay dated. Per VCS-003 these are landmarks for
tracing behaviour to when it entered, not semver releases.

Everything before `v0.1-standards` was reconstructed from git history when
this file was introduced, summarised at feature granularity. From here on,
entries are written in the same change set as the change they describe.

## [v0.1-standards] - 2026-08-01

Day-0 engineering standards adoption (`chore/gatehouse-standards`). No
runtime behavior changes — the service is untouched; this is tooling,
documentation, and CI.

### Added
- **Code map generated from docstrings** (DOC-006/007). `docs/code_map.md`
  indexes the package and carries a hand-written layer map of all 23 `api/`
  modules; `api/README.md` — which did not previously exist, leaving DOC-006
  half-met — gains a narrative header plus a generated Public API section.
  `scripts/gen_code_map.py` is vendored from gatehouse; it parses with `ast`
  and never imports the code it documents.
- **`--flat` mode for the generator.** Upstream discovers packages as the
  subdirectories of `--src-root` containing `__init__.py`; parsival is a
  single flat package, so that found nothing and produced an empty map. Four
  fixture tests cover the new mode, including one asserting the flag is
  load-bearing.
- **Code-map enforcement** (DOC-008): a pre-commit hook that regenerates on
  any change to `api/*.py`, and a CI step running `--check` so documentation
  drift fails the build.
- **Docstrings for 44 previously undocumented look-ahead functions** (DOC-002)
  — 26 routes in `api/app.py` and 18 helpers in `api/db.py`.
- **Pinned lint/type toolchain** (CODE-003/004): `pyproject.toml` declares
  PEP 8 and PEP 257, pinning ruff 0.15.14 and mypy 2.1.0 exactly.
  `pre-commit` 4.6.1 pinned alongside them.

- **Lint and format gates in CI** (CODE-003): `ruff check` and
  `ruff format --check` both fail the build, with ruff pinned to an exact
  version so the gate cannot drift between a developer's machine and CI.

### Changed
- **mypy now actually runs in CI.** The workflow previously ran pytest and
  nothing else, so the type gate existed only on paper —
  `pyproject.toml`'s ratchet comment claimed a regression "fails CI" when it
  could not. `api/` is enforced (24 files clean); eight modules are listed as
  named, shrinking debt. `scripts/` stays out for now with 7 pre-existing
  errors.
- CI gates run after the tests under `!cancelled()`, so one failing gate never
  masks the others or the test results.
- **Formatted the whole tree** — 54 of 58 files, ~10,000 lines. Held back
  until the look-ahead document-chip PR merged, since reformatting first
  would have re-conflicted all of that branch's commits.
- **Lint findings taken from 650 to zero**, in stages: 507 safe autofixes, the
  formatter absorbing ~30 more, then the remainder by hand.
- **Every docstring now opens with a one-line summary** (PEP 257 / D205, 34
  sites). Not cosmetic: the code map renders that first line as each symbol's
  description, so a wrapped summary published half a sentence.
- **`api/` logs through the logging module instead of `print`** (LOG-001, 33
  calls). Matches the existing convention — lazy `%`-formatting, levels by
  content, with `seeder` and `correlator` gaining the loggers they lacked.
- **Renamed the last Squire references** (DOC-004): six module docstrings and
  24 `squire#NN` issue references. The rename carried the GitHub issue numbers
  over unchanged, so `squire#31` and `parsival#31` are the same issue.

### Fixed
- **The Ollama health probe could truncate its own URL.** It built the URL
  with `OLLAMA_URL.rstrip('/generate')`, and `rstrip` takes a character *set*,
  not a suffix — so it strips any trailing run of `{/,g,e,n,r,a,t}`. It
  returns the right answer for the default URL, which is why it went
  unnoticed, but `http://ollama.example/generate` becomes
  `http://ollama.exampl`. Now `removesuffix`.
- **All of `app.py`'s logging was being rerouted to the root logger.** A
  module-level `import logging as _log` inside an `if` block rebound the
  global `_log` from the `parsival` Logger to the `logging` module, so every
  later `_log` call resolved to `logging.info`/`.error`. It triggered on the
  documented default of `CREDENTIALS_KEY` being unset.
- `dismiss_situation` shared a single mutable `{}` default across every
  request. Nothing mutated it, so it was harmless today and a trap tomorrow.
- **Exceptions are chained when converted to `HTTPException`** (13 sites).
  The response was always correct; the server log lost the cause, so a 502
  "merLLM unreachable" recorded *that* it was unreachable but not why.

- **`.gitattributes` was forcing LF onto Windows scripts** (CTR-001). The
  blanket `eol=lf` rule caught the new `scripts/run_outlook_sidecar.cmd`;
  `*.cmd`, `*.bat`, and `*.ps1` are now `eol=crlf`. Binary attributes
  completed for `woff2`, which `git check-attr` reported as unspecified
  (CTR-003).
- **Main's CI was red from a stale test.** The resilient-ingest feature made a
  single persistent 500 skippable, but a test still required `SystemExit`.
  Replaced with two tests matching the documented contract, including first
  coverage of the systemic-abort path.
- **Two breakages when main merged back in**, both from this branch's
  autofixes meeting code written against the pre-autofix tree. `app.py`
  conflicted only in its import block, where taking either side wholesale
  would have dropped `JSONResponse` — used by an error path that runs only
  when lancellmot is unreachable. `db.py` merged *cleanly* and was still
  broken: `Optional` had been rewritten to `X | None` here while new code
  arrived using `Optional[dict]`, so the module raised `NameError` at import
  with no conflict to warn anyone.

### Known gaps
- `scripts/` is not under the mypy gate — 7 pre-existing errors, mostly in
  `thunderbird_sidecar.py`. `api/` is enforced.
- Eight `api/` modules remain on the mypy ratchet's exemption list. The list
  only ever shrinks.
- The lancellmot alias save/delete path still swallows errors, so a failed
  save is invisible ([#96](https://github.com/rcanterberryhall/hexcaliper-parsival/issues/96)).

## 2026-07 — Sidecar resilience

### Added
- **Outlook sidecar high-water-mark tracking and resilient ingest.** A batch
  that fails is retried and then bisected to isolate a poison item, which is
  skipped rather than aborting the run, so one bad email cannot stall
  ingestion. Drops beyond `_MAX_SKIPPED_ITEMS` are treated as systemic: the
  run aborts and the high-water mark is *not* advanced, so the whole window is
  retried next run instead of being silently written off.

### Fixed
- Forced LF line endings on exec-critical files to stop a Windows checkout
  corrupting a shebang and killing the container ENTRYPOINT with exit 255.

## 2026-05 — Card parity follow-ups

### Added
- Engineering knowledge graph (EKG) concept stub and a written rationale for
  the graph approach.

### Fixed
- The synthesized `manual_<doc_id>` items row is dropped when its todo is
  deleted, instead of lingering as an orphan (#94).

## 2026-04 — Look-ahead board, attention model, and the Parsival rename

The project's largest period of change: 140 commits.

### Added
- **Look-ahead board** (#48) — a two-week planning view with a 14-day
  Sun–Sat grid, drag-to-reschedule, per-project shift schedules, a typed
  global resource catalog (BOM) with `needed`/`secured`/`consumed` status, and
  card dependencies.
- **Look-ahead templates** (#49) — repeatable task graphs with relative
  offsets, dependencies, and resource requirements; instantiate, reschedule,
  detach, auto-complete, and opt-in version upgrade for existing instances.
- **Cross-system linking** (#50) — procedure-doc URLs, item links into the
  analyses table, and an LLM annotator proposing related items. Acceptance is
  always user-confirmed; rejections are remembered so the annotator does not
  re-propose them.
- **Cards ↔ todos round-trip** (#71) — every look-ahead card is mirrored by a
  linked action item; completing either side flips the other.
- **Per-task `work_days` mask** (#73), so the workweek is per-task rather than
  per-template.
- **Adaptive attention model** (B7) — a learned ranking built from observed
  behavior (opened, tagged, noised, dismissed) using incrementally maintained
  attended/ignored centroids, replacing the fixed priority hierarchy.
- **Situation lifecycle workflow** (B5) — `new` / `investigating` / `waiting`
  / `resolved` / `dismissed` with transition history, follow-up dates, a
  stale-decay advisory flag, and manual split/merge (#40).
- **Pre-scan noise filters** (B6), evaluated before the LLM so
  known-irrelevant items never cost an inference cycle.
- **Scheduled auto-scans** (B1) with per-connector intervals.
- **Contacts directory** (#24) built from email headers, keyed by a stable
  serial id rather than an email address, plus a **signature parser** (#31)
  that enriches contacts from message bodies and never overwrites
  hand-edited fields.
- **Passdown HTML email generation** (#39) and a **priority override feedback
  loop** (#38) that prompt-injects recent corrections at inference time.
- **Configurable analysis provider** — local Ollama, Ollama Cloud, or the
  Claude API, behind the `llm.py` abstraction.
- **Background batch processing via merLLM**, with re-analysis submitted to
  the background priority bucket and polled for results.
- **Mobile UX pass** (#41) — Right Now panel, single-column item cards, and
  swipe gestures.
- Deep analysis from situation cards (#1), batch status proxy, and GitHub
  Actions CI.

### Changed
- **Renamed Squire → Parsival** across branding, service names, and the
  database path; the domain moved to `parsival.hexcaliper.com`.
- **merLLM owns GPU concurrency** (#33) — the parsival-side throttle was
  removed, and every LLM call routes to the appropriate priority bucket (#34).
- Re-analysis is durable-only and aborts when merLLM is unavailable rather
  than silently falling back to a synchronous proxy (#47).
- Re-analysis processes items in hierarchy order (`user` → `project` →
  `topic` → `general`), newest first within each tier.

### Fixed
- **OAuth tokens are encrypted at rest** (#5), OAuth flows gained CSRF `state`
  validation and configurable redirect URIs (#4), and GitHub notification
  fetching follows pagination instead of silently truncating (#6).
- Batch job polling: wrong URL, wrong response field, 409 handling, and
  startup resume (#3).
- `db.lock` made re-entrant (`RLock`) so nested locked helpers cannot
  self-deadlock; pytest-timeout added; the Dockerfile switched to a glob so a
  new module can no longer be silently omitted from the image (#32).
- Thread-aware analysis via a prior-message todos hint (#79, #82) and wider
  todo dedup across Outlook reply chains (#77).
- Recipient-scope-aware action item extraction, restoring delegated-work
  tracking (#25).
- Numerous look-ahead board fixes: browser-portable drag-and-drop, overlapping
  cards packed into sub-rows, and BOM status persisting immediately.

## 2026-03 — Initial service, situation layer, and the SQLite migration

### Added
- Initial FastAPI companion service with a pytest suite, Slack and Outlook
  ingestion, and a host sidecar authenticating via a Cloudflare Access service
  token with credentials held in Windows Credential Manager.
- **Situation layer** — cross-source correlation, scoring, and LLM synthesis
  of related items.
- **Knowledge graph (GraphRAG)** — items indexed into typed nodes and
  weighted edges, with related items retrieved into the analysis prompt.
- **Microsoft Teams connector** with an OAuth flow.
- **Seed workflow** for bootstrapping project intelligence from existing data,
  converted into a state machine with an automated ingest watch and run as a
  background job to avoid nginx 504s.
- Context hierarchy (`user`/`project`/`topic`/`general`), project and noise
  keyword learning, sender signals, and passdown detection.
- Embedding-based project classification with subdivision centroids.
- Item detail panel, full-text search with date/hierarchy/project filters,
  scan cancel, reply detection, and a mobile-responsive layout.
- Project briefing generation, per-category keyword learning, and assignee
  tracking.
- GPU meter with multi-GPU support, plus a CPU/RAM system meter.
- `DESIGN.md` and `INSTRUCTIONS.md`.

### Changed
- **Migrated storage from TinyDB to SQLite** (WAL), alongside the knowledge
  graph tables.
- **Extracted `situation_manager.py`, `seeder.py`, and `orchestrator.py` out
  of `app.py`**, guarded by unit tests written before the refactor.
- Overhauled the category schema to the current four categories.
- nginx bound to loopback only, reached via the Cloudflare tunnel, with
  security headers added.

### Fixed
- `correlator.py` was missing from the Docker image, causing silent save
  failures.
- nginx 403 on SPA routes; detail-panel scrolling; several search-bar
  credential-autofill and chip-CSS collisions.
- Ollama calls throttled with a semaphore to prevent GPU overload (later
  superseded by merLLM owning concurrency).
