# Contributing

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml). Include
your GPU model, Ollama version, Python version, and the paper's `metadata.json`
so we can see which stages completed before the failure.

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for known issues first.

## Proposing features

Use the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml).
Describe the use case — a concrete "I tried X and couldn't do Y" is more
actionable than an abstract "it would be nice if…".

## Making changes

### Branch naming

```
fix/<short-description>       # bug fixes
feat/<short-description>      # new features
docs/<short-description>      # documentation only
perf/<short-description>      # performance improvements
refactor/<short-description>  # no behaviour change
```

### Commit style

Follow the existing log (conventional commits, imperative mood):

```
feat: add --timeout flag for long-running models
fix: skip corrupted PDFs instead of crashing
docs: add chunking rationale to ARCHITECTURE.md
perf: raise token budget for papers > 30 pages
refactor: extract diagram parsing into helper
```

One subject line (≤ 72 chars). Body is optional — use it for "why", not "what".
The diff already shows what changed.

### Linting

Before opening a PR, run:

```bash
# Python
ruff check paper_processor.py
ruff format paper_processor.py

# Rust (wizard)
cd wizard && cargo clippy -- -D warnings
cd wizard && cargo fmt
```

CI enforces both. A PR with lint failures will not be merged.

### Pull request checklist

- [ ] Branch is up to date with `main`
- [ ] `ruff check` passes with no errors
- [ ] `cargo clippy` passes with no warnings
- [ ] `paper_processor.py --help` still works
- [ ] If adding a stage: `--reprocess <stage>` is wired up and tested
- [ ] If changing output format: `docs/ARCHITECTURE.md` is updated

### Development setup

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full local setup guide,
including how to add prompt stages, new backends, and wizard tabs.

## Licence

By contributing you agree that your changes will be licensed under the project's
[MIT Licence](LICENSE).
