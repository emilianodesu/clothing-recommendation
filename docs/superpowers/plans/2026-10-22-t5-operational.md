# T5 operational continuity implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Deliver a separately versioned, measurable T5 rehearsal and an exact-commit mac24 handoff, preserving historical failures.

**Architecture:** A host-only operation monitor combines the existing Timeline/AwakeGuard with bounded Docker/HTTP calls. An opt-in runner reuses the existing replay and cgroup capture, with a separate strict `events-operational-v1` envelope. Existing events-v1/events-memory-v1 validators retain their behavior and bytes.

**Tech Stack:** Standard Python 3.9+, local Docker/Compose, existing FastAPI/PostgreSQL/Streamlit. No new dependencies.

**Spec:** [approved operational proposal](../../clock-diagnostics.md#propuesta-del-siguiente-contrato-operativo), [received audit and next sequence](../../../evidence/events-clock-review-mac24.json). User requested continuation of this sequence; execution stays native as in prior deliveries.

## Global Constraints

- Existing H, windows, cut, datasets, memory and CPU limits stay fixed. No H&M execution here, training, test, T6 or repeated pmset collection.
- New protocol only: lifetime peak/max/pressure are retained as diagnostics, not a fitted overshoot allowance. Exact limits, zero captured swap/OOM, complete functional checks, source integrity and expected generations are acceptance gates. Capacity/latency remain T8.
- Continuity tolerance 2 s per interval and cumulatively; total budget 7200 s; record both clocks and current implementation. Supervised caffeinate owns only its child; no persistent sleep settings. Suspend can be detected only after execution resumes.
- Record each HTTP, Docker, seed, sample and restart operation with safe labels and bounded remaining timeout. No tokens, customer rows, bodies, raw commands or power messages in reports.
- Failed/interrupted attempts close with partial evidence, including observed discontinuity, operation failure, cleanup and guard lifecycle. No automatic retry except bounded readiness polling.
- Preserve PostgreSQL identity/counters and all originals; only the intentional API restart and unique seed container cleanup. GitHub private is the authorized exchange, SSL_CERT_FILE=/etc/ssl/cert.pem.

## Review Focus

- A clock jump/expired budget or dead guard during blocking work must prevent acceptance and remain readable in a failed ZIP.
- Slow response bodies, readiness polling, cgroup subcommands and cleanup must not escape timing or hide a child process.
- Incomplete/reordered/tampered traces or protocol downgrade must be rejected even for failed reports; free text/requests must not leak through attachments.
- Lifetime peak exceeds limit but resources/swap/OOM/generations and functionality are otherwise correct: new protocol may pass, old protocols must still fail. No observations may be normalized or overwritten to achieve this.
- Full run must preserve service identity except intentional API restart; early/preflight failure may have no scientific imports, observations or commit file and still produce an honest closed report.

### Task 1: Timed operations and transport

**Files:** create `scripts/operational_timing.py`, `scripts/operational_io.py`, `tests/host/test_operational_timing.py`; modify `scripts/events_memory.py` only to allow a bounded output callback, preserving defaults.

**Interfaces:** `Monitor(guard=None, sample=None).start()/perform(label, limit, action)/finish()`; action receives a callable remaining budget. `.evidence()` returns policy/clock metadata, marks, operation indices, guard lifecycle and recomputable assessment. `OperationalIO(root,values,monitor)` provides compose, output, inspect, request and UI health. No private paths or payloads in trace labels.

- [x] RED: coherent timing and decreasing budget, UTC/monotonic regressions and gap, lost guard, timeout, interrupted child, invalid sample and oversized trace; real localhost HTTP read/redirect/non-JSON/slow response behavior and no exported request data. Expected missing module/feature failures.
- [x] Implement monitor using Timeline/AwakeGuard; preserve finite failed marks and structured unavailable samples. Bound and observe all transport boundaries, including cgroup capture callback. Recompute assessment independently when reading evidence.
- [x] GREEN: targeted host tests and full host suite. Commit and ledger.

### Task 2: New acceptance envelope and one-shot runner

**Files:** create `scripts/operational_handoff.py`, `scripts/operational_check.py`, `tests/host/test_operational_handoff.py`; modify `scripts/handoff.py` routing and `scripts/project.py` opt-in flags for `events-check`/synthetic `demo`.

**Interfaces:** protocol `events-operational-v1`; `events-check --operational` mutually exclusive with memory diagnostics. `run(root,machine_id,expected_commit,source_run=None)` reuses replay but the new IO. `validate_operational(report,result,env,names)` validates full or partial evidence; `validate_operational_files(report,bundle)` restricts aggregate attachments. `final_validation` requires new gates with all raw observations unchanged.

- [x] RED: historical peak retained and accepted only in new protocol; OOM/swap/limits/generation/counters still fail; corrupted trace, skipped required phase/operations, fabricated failure, free text and downgrade rejected through real ZIP reader/upload boundary. Preflight, clock and cleanup failure close as failed evidence. CLI opt-in reaches new runner without changing legacy paths.
- [x] Implement strict aggregate envelope and timed runner; fixed phase order and source/identity checks, bounded readiness polling; retain exact observed metrics. Keep old validators unchanged. Synthetic path uses separate project only.
- [x] GREEN: host suite and legacy closed ZIP compatibility. Commit and ledger.

### Task 3: Real local rehearsal and mac24 handoff

**Files:** `docs/events-operational.md`, `docs/mac24-t5-operational-prompt.md`, `evidence/events-operational-local.json`, current README/AGENTS/plan status.

- [x] Commit code, prepare isolated local synthetic environment with its own .env/volumes/ports; reuse local Docker context without altering unrelated services. Run verify --suite events and one clean-commit synthetic operational rehearsal; retain failures and closed reports.
- [x] Validate/publish the closed synthetic report with exact source commit, explicitly separating local evidence from mac24. Preserve historical reports and resource gates.
- [x] Write mac24 prompt: clean exact commit, jobs-only rebuild/tests, preserve active PostgreSQL/API/UI and private originals, one `events-check --operational`, publish valid failure too, no retries/train/test/T6.
- [x] One independent whole-branch review per executing-plans; fix Important/Critical in one RED→GREEN pass. Publish verified code via existing authorized Git flow and deliver exact-SHA prompt. Keep T5 open pending remote audit.
