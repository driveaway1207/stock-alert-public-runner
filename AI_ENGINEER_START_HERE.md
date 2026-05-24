# AI_ENGINEER_START_HERE

This file is the top-level engineering entry point for the employee system.

Default source of truth:

```text
driveaway1207/stock-alert-public-runner
```

The private repository `driveaway1207/stock-alert` is not the default source of truth unless the user explicitly asks to work on it.

## Mandatory read order

For any employee-system task, read in this order before changing code, workflow, docs, or reports:

1. `README.md`
2. `AI_ENGINEER_START_HERE.md`
3. `EMPLOYEE_SYSTEM_ROLES.md`
4. The relevant `EMPLOYEE*_OPERATION_RUNBOOK.md`
5. The relevant runner or report script
6. The relevant workflow
7. The relevant report or artifact directory

## File hygiene rules

The repository must stay clean, centralized, and layered.

Top-level rule files are limited to:

```text
README.md
AI_ENGINEER_START_HERE.md
EMPLOYEE_SYSTEM_ROLES.md
```

Employee runbooks must use the existing pattern:

```text
EMPLOYEE0_OPERATION_RUNBOOK.md
EMPLOYEE1_OPERATION_RUNBOOK.md
EMPLOYEE2_OPERATION_RUNBOOK.md
EMPLOYEE3_OPERATION_RUNBOOK.md
EMPLOYEE4_OPERATION_RUNBOOK.md
EMPLOYEE5_OPERATION_RUNBOOK.md
EMPLOYEE6_OPERATION_RUNBOOK.md
```

Do not create scattered files such as `FINAL`, `V2`, `TEMP`, `PATCH`, `NOTE`, `CHANGE_LOG`, or `DOCUMENT_MAP`. Long-term rules must be written back to the top-level docs or the relevant employee runbook.

## Hard rule: no standalone Python file for a single employee feature

For any already-landed employee, a new feature, score, report, attribution module, audit module, fix, patch, or enhancement must be merged into that employee's existing main script or existing report script by default.

Do not create a new `.py` file for one standalone feature.

Example: Employee 5 reason-hit scoring must be merged into the existing Employee 5 script/report script. Do not create files such as:

```text
employee5_reason_score.py
employee5_score.py
employee5_patch.py
```

A new employee-level Python file is allowed only when the user explicitly approves it, and only after updating `EMPLOYEE_SYSTEM_ROLES.md`, the relevant employee runbook, the workflow, and the validation notes.

If a standalone employee Python file is created without explicit approval, it is a file-governance violation and a monkey-code risk. It must be deleted and merged back into the existing employee script.

## Validation rules

Never say a change is done unless it is backed by GitHub evidence.

Always distinguish:

- Submitted: GitHub returned a commit SHA or PR.
- Rechecked: the GitHub file was read again and the content is actually present.
- Verified: workflow, artifact, logs, or Telegram output confirms the expected result.

If a tool errors, is blocked, conflicts, or does not return a commit SHA, say clearly that it did not land.

## Hard bans

- Do not use the private repo as the default source.
- Do not overwrite public-repo facts with private-repo facts.
- Do not edit workflow before reading the relevant runbook.
- Do not execute user requests without recording long-term rules in the proper docs.
- Do not create a standalone `.py` file for a single employee feature unless the user explicitly approves it.
- Do not claim submitted without a commit SHA or PR.
- Do not claim rechecked without reading the file back.
- Do not claim verified without workflow, artifact, logs, or Telegram evidence.

## One-line rule

The employee system has one default source of truth: `driveaway1207/stock-alert-public-runner`.

Keep files clean, centralized, and layered. Do not create scattered Python files for single employee features.
