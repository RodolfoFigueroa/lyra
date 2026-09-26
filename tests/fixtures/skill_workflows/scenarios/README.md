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
| Earth Engine | `earth_engine.py` as `workflow.py` | Adapt this workflow into a Lyra plugin using the normal Lyra worker runtime. Validate offline using fakes; no live service calls. |
| Earth Engine ambiguous output | `earth_engine_missing_information.py` as `workflow.py` | Adapt this workflow into a Lyra plugin using the normal Lyra worker runtime. Ask about material uncertainties and validate offline. |
| File report | `file_report.py` as `workflow.py` | Expose this report as a Lyra file metric for the supplied location's feature IDs. |

For new packages, use `trial-plugin` version `0.1.0` and register the table metric
as `zone_score` (file metric as `feature_report`). Package naming is provided by
the evaluator, not inferred from the calculation. No publishing or services are
required. Use the existing project environment; do not install or build wheels.

For Earth Engine trials register `mean_elevation` for the documented case and
`mean_signal` for the ambiguous-output case. Supply the corresponding unmodified
workflow. The ambiguous fixture deliberately uses a placeholder private asset;
its meaning and units cannot be recovered from a public dataset catalogue.
Do not supply the staged answer from the evaluator rubric until the agent asks.
