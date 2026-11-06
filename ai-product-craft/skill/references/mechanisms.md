# Mechanisms, evidence and counterexamples

All external sources are pinned in `../../research/source-lock.json`; browsed 2026-11-04. Repositories are study sources, not required installed plugins. Observed instructions are not proof their authors' effectiveness claims hold here. This candidate uses original wording and implementation; it does not bundle or execute those workflows.

| Decision mechanism | Upstream study material | Observable effect in this candidate | Limit / counterexample |
| --- | --- | --- | --- |
| Tie a completion claim to executed evidence | obra/superpowers, verification-before-completion | run records real argv, result and output | A passing meaningless command still says nothing about business correctness |
| Tie evidence to consumed inputs | garrytan/gstack, verify and its exact-tree readiness contract | source-content and contract fingerprints invalidate stale results | Unlisted dependencies, runtime environment and remote state are not fingerprinted |
| Compare promised behavior separately from standards | mattpocock/skills, code-review; Trail of Bits, spec-to-code-compliance | acceptance IDs, observable scenarios and separate regression review | The source spec may be wrong; confirm user outcome before enforcing it |
| Reproduce, repair and repeat the original path | gstack qa; mattpocock diagnosing-bugs; existing ub-mode | failure-specific check before repair; adjacent flow after | Import/dependency errors do not prove the reported bug; tiny visual edits need visual evidence |
| Challenge author conclusions independently | Superpowers requesting-code-review; Trail of Bits spec-to-code workflow | substantial changes request reviewer with original artifacts | Another model's agreement is not authority; self-review must be labelled |
| Operate the real rendered interface | Anthropic webapp-testing; gstack qa/browser verification sections | browser path plus underlying state observation | network-idle is unreliable on streaming apps; screenshots don't prove data integrity |
| Test decision domains, not only examples | Trail of Bits property-based-testing | add invariant or property check only when its algebraic shape fits | A property mirroring implementation or generating no valid cases is weak evidence |
| Keep capability and regression evaluations distinct | ECC eval-harness | positive, near-miss and holdout evaluation lanes | Three examples cannot establish pass@k reliability or a numerical improvement claim |
| Keep local lessons scoped and retrievable | ECC continuous-learning-v2; gstack learn | one project-local record per lesson, provenance and bounded recall | No hooks means no capture outside adopted sessions; prevalence is not truth |
| Promote mechanisms only after evaluation | personal-meta-skill-orchestrator evidence-led creation + observed local failures | propose writes a candidate, never silently changes active instructions | One severe incident can justify a proposal; frequency isn't a safety threshold |

## Conflicts resolved explicitly

- Upstream aggressive refactoring vs small fixes: retain task scope, widen only when necessary for the user outcome. Historical unrelated debt is reported separately.
- Universal TDD vs honest evidence: require a meaningful reproduction where feasible; do not manufacture a test for a static wording change or an inaccessible system.
- Quality metric vs business outcome: complexity/coverage/duplication are optional diagnostic lanes, never substitutes for authorization, persistence or failure recovery.
- Mandatory independent agents vs cost/availability: use for substantial risk when authorized and available, label self-review otherwise. Do not misrepresent an unavailable reviewer as a pass.
- Automated learning vs pollution: capture observations, demand proof for validation, retain counterexamples and supersession, gate permanent rules by evaluated diff.
- Completion vs deployment: state local check readiness separately from target-environment acceptance.

## Local empirical input

The preceding ub-mode setup in this conversation showed two environment assumptions fail on this Windows machine: parser-cache download rename across volumes (os error 17), and Mutator's symlink overlays (WinError 1314). The first was remedied by configuring cache location; the second was explicitly left unavailable at the user's choice. These support reporting runtime limitations rather than claiming installation equals verified capability. They do not justify a universal ban on Windows or symbolic links.

The original tools in this candidate are tested for accidental false-green and lesson lifecycle failures. That is B/C-style implementation evidence for those mechanisms, not A-level evidence of improvement on the user's real business projects. Future user rework will supply that evidence; do not invent a history to fill the ledger.
