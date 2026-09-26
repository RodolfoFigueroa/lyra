# Inputs for independent skill trials

Give the agent only the installed skill, the selected workflow, this scenario's
prompt, and the existing project environment. Do not copy the evaluator directory.
Use a fresh isolated workspace and a fresh agent for each trial. Capture the
conversation, changes, commands, and results outside the distributed skill.

| Scenario | Files to copy | Prompt |
| --- | --- | --- |
| New plugin | `../workflow.py` as `workflow.py` | Adapt this existing calculation into a new Lyra plugin. Preserve its behavior and validate the integration locally. |
| Existing plugin | `../workflow.py` as `workflow.py` and a copy of `examples/lyra-plugin/` | Add this calculation to the existing plugin. Preserve existing metrics and validate the integration locally. |
| Missing information | `missing_information.py` as `workflow.py` | Adapt this workflow into a Lyra plugin. Ask me about any material information you cannot establish. |
| Dynamic columns | `dynamic_columns.py` as `workflow.py` | Adapt this workflow into a Lyra metric while preserving its calculation. |
| Ambiguous rows | `ambiguous_rows.py` as `workflow.py` | Adapt this workflow into a Lyra metric while preserving its calculation. |
| File report | `file_report.py` as `workflow.py` | Expose this report as a Lyra file metric for the supplied location's feature IDs. |

For new packages, use `trial-plugin` version `0.1.0` and register the table metric
as `zone_score` (file metric as `feature_report`). Package naming is provided by
the evaluator, not inferred from the calculation. No publishing or services are
required. Use the existing project environment; do not install or build wheels.
