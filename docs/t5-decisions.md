# Decisiones de implementación y revisión T5

Registro de decisiones y sus límites. La implementación local está disponible; el primer intento H&M falló por memoria PostgreSQL y T5 sigue abierta. Los puntos de implementación siguientes conservan su contexto original.

- Revisión mac24: conservar el intento `events-mac24-20261022T043907Z-ddcd2e42` como fallido. El pico de PostgreSQL supera el criterio v1, aunque todas las comprobaciones funcionales reportadas terminan. Su contador acumulado no permite atribuir el exceso a T5. Coste: instrumentar fases/cgroup y diagnosticar antes de elegir una corrección; no relajar el validador ni aumentar memoria para cambiar el resultado. [Auditoría](../evidence/events-review-mac24.json).

- Ruling: Operational streams distinguish synthetic/simulation data and have explicit date plus reception watermark; no future H&M source rows are ingested. Cost: this demo is a simulation, not live current clothing data.

- Ruling: Rebuild stream profiles transactionally from frozen daily counts and eligible events; serialize inserts/refresh on stream row and retain raw events for retry. Cost: full refresh may need capacity measurement on mac24, not an incremental performance claim.

- Ruling: Preserve base package immutable; store daily counts and profiles in PostgreSQL with stream identity/revision, expose separate stream routes. Cost: consumers must select a stream to use operational feedback.

- Ruling: Isolated worktree with staged Docker source under Documents for Colima mounts, own secrets and volumes. Cost: final image source provenance must be checked in clean clone.

- Ruling: Source origin uses the frozen H&M SHA, not dataset_id prefix — synthetic generator also uses hm- IDs — cost if wrong: real and synthetic origins could be mislabeled; exact known source hash is enforced.

- Ruling: Seed job budget 3 GiB/1 CPU/2 threads — prior T4-A measured 2.248 GiB; a proposed 2 GiB budget was insufficiently grounded — cost: larger reserved VM budget, real H&M capacity still requires mac24 measurement.

- Ruling: Reuse private GitHub report exchange with a separate events-handoff protocol; one foreground run and controlled-failure ZIP, no automatic retries — cost: abrupt terminal/host loss can leave an incomplete report requiring diagnosis.

- Ruling: Close local T5 implementation here and await one mac24 H&M run before E4 closure — private full data/service are only on mac24 — cost: cannot claim remote reproducibility or performance yet.

- Final: Ruling: promote the review UI conflict finding from Minor to Important — a changed release can disable all later interactions; add explicit close of terminal attempt while keeping uncertain UUID retries — cost: closing an attempt does not undo a possibly committed event; UI says so.

- Final: Ruling: H&M correctness/capacity remains pending mac24 — full private state is not here — cost: measured local synthetic success does not establish full-scale fit.

- Final: Ruling: scheduled refresh and suspension catch-up stay T6 — this delivery is manual — cost: profiles remain stale until explicitly refreshed.

- Final: Ruling: incremental refresh and contention measurement remain mac24/T8 — transactionally rebuild this finite demo — cost: inserts may wait during full publication.

- Final: Ruling: sustained latency, images throughput and freshness targets remain T8 — current HTTP checks are functional — cost: no throughput/SLA claim.

- Final: Ruling: logical backup/restore and artifact transport remain T7 — preserve local PG/package sources now — cost: report ZIP alone cannot recover service state.

- Final: Ruling: physical AMD64 and another-user acceptance remain unverified — local clean-clone and mac24 ARM64 are current targets — cost: cross-architecture runtime differences may remain.

- Final: Ruling: no Q1-Q4 repeats, retraining or model reselection — closed quality protocol selected H — cost: no new claim that H is universally best.

- Final: Ruling: final test quality remains T8 — preserve held-out data — cost: quality_accepted stays false.

- Final: Ruling: local single-operator access only — loopback bindings, no public customer authorization — cost: public multiuser deployment unsupported.

- Final: Ruling: pending UI payload lifetime is the browser session — server receipts remain durable — cost: closing the session loses the retry ID if not saved elsewhere.

No quedaron observaciones menores aplazadas. La observación sobre conflictos de UI fue reclasificada por su efecto sobre el usuario y corregida con una prueba RED→GREEN.
