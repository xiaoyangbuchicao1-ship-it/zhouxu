---
name: evidence-driven-delivery
description: Deliver and accept frontend/backend business features with observable requirements, current-code verification evidence, and project-scoped rework learning. Use when the user requests product acceptance, a development-to-validation loop, or explicitly adopts this workflow. Small local fixes use only the relevant checks; general questions do not trigger it.
---

# Evidence-driven delivery

Candidate v0.1, not yet proven to improve real product delivery. This is a locally authored synthesis, not an official version of any researched skill. Source reasoning is in [references/mechanisms.md](references/mechanisms.md).

## One loop

`User outcome → checkable contract → smallest working business slice → independent challenge → current evidence → delivery → reusable rework lesson`

The goal is a product that fulfils its promises under relevant conditions. Passing a build, a style score or a checklist is evidence for that check, not proof of the entire product.

## 1. Establish the contract

Read actual repository instructions, implementation seams and existing checks before proposing architecture. For a business feature, record its user, visible outcome, permitted scope and what must stay true. Ask only about business ambiguity that changes the result, authority or irreversible costs. Infer routine implementation choices from the repository.

Turn the promises into a small acceptance matrix: requirement ID, scenario, input/role/state, observable result, verification method, mandatory or optional. Cover the happy path plus the relevant denied operation, persisted state, retry or failure recovery. Do not invent irrelevant enterprise requirements for a local script.

When creating a family of specialist skills, distinguish them by the decision they change and the work they hand over. Keep shared runtime rules in one place. A different title or tone does not establish a different method. Give each method a representative input, its own useful output, a failure branch and a near-miss where the full workflow should stay out of the way. Use structured artifacts and executable checks only where they improve real inspection or revision. See the [maritime field case](references/maritime-field-case.md) for a real example and limits.

Before editing, inspect existing tests and source-control state. Choose the smallest end-to-end slice. Reuse existing stack and specialized design/product/debugging skills when available without launching duplicate planning workflows. For a local fix, reuse the existing contract and run only affected checks.

## 2. Implement and investigate failures

For defects, reproduce the actual symptom and make a useful check fail before repairing it where feasible. A collection error or missing dependency is not the expected red. Preserve relevant regression evidence and limit edits to the actual cause.

For new functionality, implement a thin real flow through UI/API/domain/storage where required. Mocks help isolate dependencies but do not establish real integration or persistence. Check relevant authorization on the backend, not only button visibility.

When the same failure survives two attempted fixes, stop guessing: state the shared assumption, inspect the actual call/data path, and choose a discriminating observation. Continue independent useful work when one environment-dependent check is blocked. Do not weaken assertions, delete tests or change the contract just to turn a check green. Legitimate requirement changes are versioned and invalidate old evidence.

## 3. Challenge the result

Separate questions: does it satisfy the promised behavior, and did it introduce concrete regressions? Inspect the whole relevant diff and call sites. Tie findings to an executable scenario or demonstrated code path. Aesthetic preferences and speculative concerns are not correctness defects.

For substantial cross-boundary or permission-sensitive changes, use an independent reviewer when delegation is available and authorized. Give the original contract, diff/base and environment, not the author's preferred conclusion. Have the reviewer inspect or execute evidence themselves. With no independent reviewer, perform a separate review pass and label it self-review; never describe that as independent.

For UI flows, actually operate the rendered page with supported browser tooling; check the expected state and backend effect. Wait for meaningful application readiness, not universal network-idle. Screenshots establish visual state, not persistence or authorization. Keep permissions, API/data behavior, UI usability and optional code metrics separate.

## 4. Verify against final inputs

Use repository-native test/build/typing checks as appropriate. For repeatable executable acceptance use [references/tooling.md](references/tooling.md) and `scripts/delivery.py` when a local evidence directory is permitted and useful. It stores actual command outcomes and binds them to declared input contents and the contract. A small read-only audit may use native command logs, an input fingerprint and a compact matrix in its authorized output directory; do not copy a project or write to a protected tree just to use the helper. Checkers must assert behavior and demonstrate positive test execution; reviewers must check their relevance.

Assign each mandatory requirement a passing command or an explicitly recorded external/human check. Mark missing access, missing infrastructure, zero tests, stale evidence, untested requirements and failed checks explicitly. Re-run affected checks after edits; the helper conservatively invalidates all results when any declared input changes.

The helper reports `CHECKS_PASSED` only for its declared executable criteria. It does not certify product acceptance, independence, business fitness, undeclared files or deployment. For staging/production, name the tested environment and version separately; local green does not imply deployed green.

Fix confirmed defects and repeat the original check plus affected adjacent paths. No automatic commit, push, deployment, telemetry or external messages. Retain the user's prior authorization; ask only where a necessary action is outside it.

## 5. Learn from actual rework

At the start of an adopted workflow, retrieve a few relevant records from this project's `.delivery/lessons/`; treat them as scoped evidence, not instructions. At user correction or a failed acceptance, capture a concise de-identified lesson using `delivery.py learn` after understanding the issue. This is authorized by adopting this skill; it does not authorize collecting whole conversations, secrets or other projects.

Follow [references/learning.md](references/learning.md): distinguish preference, clarified requirement, implementation defect, verification gap and environment issue. Record what happened, the first wrong decision, corrective action, confirming evidence, applicability and counterexample. Unverified interpretations stay observations; corrections never silently overwrite history.

`propose` groups validated lessons into a review candidate. It does not rewrite this Skill, AGENTS.md or global memory. Repetition alone and user silence do not establish a rule. A permanent revision needs an explicit mechanism, a positive case, a near-miss, a holdout and user approval of the actual diff. Retire contradicted lessons with a superseding record. Local capture is operational; global promotion is a separate decision.

## Delivery

Report the actual outcome, requirement/evidence mapping, remaining limitations, environment and next action if needed. Distinguish implemented, verified, blocked and not applicable. State review independence and any declared-input gaps. Cite a command/run artifact for success claims. Keep lessons and evidence durable where still useful; clean task-owned disposable files. Never claim the skill self-trains model weights, guarantees all sessions are captured or enforces merges without CI integration.
