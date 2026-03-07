# Beta Release Checklist — Överblick v0.1.0

This checklist ensures all security and quality gates are met before external beta release.

## Security

- [x] **Safe-by-default mode enabled** — `SafeLLMPipeline(strict=True)` by default
- [x] **Skip flags documented as internal-only** — `skip_preflight` and `skip_output_safety` never exposed to untrusted input paths
- [x] **Raw LLM client access disabled by default** — `OVERBLICK_RAW_LLM=0` raises RuntimeError; plugins must use `ctx.llm_pipeline`
- [x] **Plugin capability system operational** — Warnings logged for missing grants; `OVERBLICK_STRICT_CAPABILITIES=1` raises PermissionError
- [x] **Non‑empty deflection when safety components fail** — Preflight AI‑analysis crash returns a deflection; output‑safety crash returns neutral deflection
- [x] **Fail‑closed enforcement** — Pipeline crashes block requests (not pass through)
- [x] **Boundary markers** — External content wrapped with `<<<EXTERNAL_*_START>>>`
- [x] **Audit logging** — All security decisions logged with structured JSON

## Code Quality

- [x] **CI enforcement** — GitHub Actions run ruff, black, mypy, pytest on push/PR
- [x] **Pre‑commit hooks** — `.pre-commit-config.yaml` installed with ruff, black, mypy, pytest
- [x] **Ruff global ignores reduced** — Removed UP007 and UP035; remaining ignores justified
- [x] **Exception types aligned** — Docs mention ConfigError, RuntimeError, PermissionError matching code
- [x] **Mutable defaults fixed** — All Pydantic `list[...] = []` / `dict[...] = {}` replaced with `Field(default_factory=...)`
- [x] **LSP errors reviewed** — No critical LSP errors in core security modules

## Testing

- [x] **Unit tests pass** — `pytest tests/ -v -m "not llm and not e2e"` passes
- [x] **LLM personality tests pass** — `pytest tests/personalities/ -v -s -m llm` passes with qwen3:8b
- [x] **Security pipeline tests** — `tests/core/security/` cover preflight, output safety, rate limiting, input sanitizer
- [x] **Plugin tests** — All plugin directories have test coverage
- [x] **Integration tests** — `tests/integration/` verify multi‑component interactions

## Documentation

- [x] **SECURITY.md** — Threat model, security guarantees, limitations, responsible disclosure process
- [x] **AGENTS.md** — Framework overview, plugin classification, latest security updates
- [x] **CHANGELOG.md** — Breaking changes, migration notes, security updates clearly marked
- [x] **DEVELOPER_ONBOARDING.md** — Complete contributor guide with security checklist
- [x] **API documentation** — PluginBase, PluginContext, SafeLLMPipeline, capability system
- [x] **Issue templates** — `.github/ISSUE_TEMPLATE/` for bug reports and feature requests

## Release Readiness

- [x] **Version pinned** — `pyproject.toml` version set to `0.1.0`
- [x] **Dependencies up‑to‑date** — `requirements.txt` synchronized with `pyproject.toml`
- [x] **Cross‑platform CI** — Tests run on Ubuntu, macOS, Windows
- [x] **Secrets encryption verified** — Master key in macOS Keychain or file with `0o600`
- [x] **Dashboard wizard functional** — 9‑step setup works end‑to‑end
- [x] **Gateway priority queue operational** — Multi‑backend routing works

## Post‑Beta Actions

- [ ] Monitor audit logs for false positives
- [ ] Gather feedback on plugin capability warnings
- [ ] Evaluate need for stricter capability enforcement (`OVERBLICK_STRICT_CAPABILITIES=1` default?)
- [ ] Consider adding more preflight patterns based on real‑world attacks
- [ ] Update voice tuning for additional LLM models (Llama 3, Mistral, etc.)

---

*Last updated: March 2025*  
*Överblick Security Team*