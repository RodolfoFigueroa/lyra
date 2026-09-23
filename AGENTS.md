# Agent instructions

## Verification scope

Do not perform wheel or package-distribution smoke checks unless the user
explicitly requests them. This includes building or installing wheels for
verification, creating temporary package-installation environments, inspecting
wheel contents or entry points, and testing commands from installed wheels.

Use the existing project environment and relevant project checks to verify changes.
