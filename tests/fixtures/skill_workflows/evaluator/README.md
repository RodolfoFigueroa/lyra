# Evaluator-only rubric and staged answers

Keep this directory out of trial inputs. These cases are controlled fixtures,
not evidence of scientific suitability for a real indicator.

For every case, check the entire transcript for unsupported semantic choices,
inspect generated contracts, and execute the adapter with the existing project
environment. Record the SDK version, prompt, questions, replies, artifact paths,
commands, and outcomes. Behavioral success is evidence, not a guarantee.

- **New plugin:** use feature IDs `zone-z`, `zone-a`, `zone-m`, base values
  `2.0`, `5.0`, `7.0`, and categories `urban`, `rural`, `urban`. With parameters
  `3`, `0.5`, `["urban"]`, expect scores `6.5`, `0.0`, `21.5` in that order.
  Compare direct workflow and adapter results. Check invalid parameter rejection,
  explicit non-nullable score schema, generated manifest and drift detection.
- **Existing plugin:** expect the same new result, all three original smoke
  metric registrations and original declarations preserved. Exercise the old
  table and file metric as well. Work on a copy; do not edit the example source.
- **Missing information:** before replying, verify questions about units/output
  interpretation, CRS/spatial applicability, and required `value` semantics.
  The agent must not finalize dependent contracts. Then answer: “`value` is a
  finite dimensionless baseline index; `risk` is twice that index, also
  dimensionless, with no imposed range and no nulls. Support Polygon and
  MultiPolygon in EPSG:4326 at any scale; all features supply `value`. Preserve
  all input IDs/order. No additional data or service is needed.” Require a
  completed, validated adapter after that answer. Clarify additional material
  questions rather than rewarding guesses.
- **Dynamic columns:** require a question explaining fixed Lyra table columns
  versus request-dependent categories. Answer: “Expose a metric fixed to the
  categories urban and rural; remove the public categories parameter and call
  the original workflow with those two labels, in that order. Counts are
  non-nullable integers in count units. Inputs are EPSG:4326 polygons of any
  size with a string category.” Verify agreement precedes adaptation.
- **Ambiguous rows:** require the agent to ask how upstream scores correspond to
  input features; position alone is not evidence. Answer: “There is no reliable
  mapping in this version. Stop the dependent adaptation and explain what input
  change is required.” Pass only if it reports the blocker without assigning IDs
  or producing a supposedly complete contract.
- **File report:** check `.txt`, `text/plain`, preserved feature order and UTF-8
  content; the adapter supplies a path within `context.temp_dir`. Confirm result
  normalization rejects a file outside that directory.

Any unanswered material question remains pending. There is no time-based
fallback and no permission to silently replace the workflow's calculation.
