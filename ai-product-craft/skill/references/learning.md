# Project-scoped learning

No background collector is installed. Capture occurs only during a task adopting this skill, when the agent actually calls the local tool. If it is not invoked, there is no guaranteed record. Project adoption or explicit invocation is necessary for later sessions to follow this workflow.

## States and evidence

1. Observation: a correction or failure happened; proposed explanation may be wrong.
2. Validated local lesson: the remedy has a concrete confirming observation; applicable to this project and stated conditions.
3. Rule proposal: enough validated observations expose a recurring decision mechanism. It is a proposal even when only one severe incident warrants action.
4. Accepted revision: reviewed actual Skill diff plus representative, negative and holdout behavior evidence. Publication requires the user's approval.
5. Superseded/retired: append a correction referring to the old lesson ID; do not apply the contradicted claim.

The helper enforces fields and evidence-file presence, not the truth of an explanation. The reviewer must read evidence. `validated` is a recorded human/agent judgement, not a statistically calculated confidence score.

## Input for `learn`

```json
{
  "category": "verification-gap",
  "trigger": "Claiming a tested API fix",
  "observation": "The regression ran no test cases but returned zero",
  "wrong_decision": "Used exit status as the only acceptance signal",
  "correction": "Require a positive test count and inspect the exercised behavior",
  "scope": "Projects with this runner and selection mode",
  "counterexample": "A formatter has no test count; use its appropriate completion signal",
  "validation": "validated",
  "evidence": [".delivery/runs/actual-run-id.json"],
  "tags": ["test-selection", "acceptance"],
  "supersedes": null
}
```

Use actual paths. Allowed categories: preference, requirement-change, implementation-defect, verification-gap, environment. `observation` entries may have no evidence but are never eligible for `propose`. Omit personal quotations and raw client data; summarize the decision. Use project-relative paths, no network upload. Do not read centralized credential directories or full chat histories to gather lessons.

Retrieve by relevant words, inspect counterexamples and supersession links, apply only within scope. Global lesson transfer requires reviewing sensitivity and a separate applicability test. The tool returns bounded results, so a search is not proof no historical lesson exists.

## Regression after learning

Measure prevented repeat failures, valid findings, time to accepted outcome, number of rework cycles and avoidable interruptions. Keep task/model/environment comparable; publish raw outcomes and costs. A rule that adds ceremony without changing a decision should be removed or kept as reference. A million stars is an aspiration, not an evaluation criterion.
