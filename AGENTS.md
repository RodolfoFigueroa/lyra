# Agent instructions

## Authoring guidance

Skills, bundled references, and plugin authoring guides must describe the current
supported contract. When removing a capability, remove its author-facing prose
and examples instead of adding historical comparisons, lists of retired features,
or migration notes. State supported behavior directly. Include historical or
migration guidance only when the user explicitly requests it; keep it separate
from the instructions agents use to author plugins. Preserve necessary current
validation rules and operational guidance.

## Verification scope

Do not perform wheel or package-distribution smoke checks unless the user
explicitly requests them. This includes building or installing wheels for
verification, creating temporary package-installation environments, inspecting
wheel contents or entry points, and testing commands from installed wheels.

Use the existing project environment and relevant project checks to verify changes.

## Test-suite sandbox escalation

Run the full test suite with sandbox escalation (`sandbox_permissions="require_escalated"`):

```sh
uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml
```

The restricted sandbox blocks asyncio's internal socket wakeups with
`PermissionError: [Errno 1] Operation not permitted`. Tests using thread executors
can hang as a result, including database-runtime tests and asynchronous file I/O.
This is an environment restriction, not a test failure. Focused tests that do not
need these wakeups can run in the sandbox; escalate them if the same issue occurs.
Do not change application code or weaken tests to work around this restriction.
