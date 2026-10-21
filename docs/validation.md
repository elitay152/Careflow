# Synthetic cohort validation

The portfolio uses Synthea 3.3.0 with a 1,000-living-patient target, population and
clinician seeds 43, two threads, and a 2 GiB JVM heap. Reference and end dates are
January 1, 2025. Deceased histories increase the actual patient count.

| Persisted resource | Loaded rows |
| --- | ---: |
| Patient | 1,194 |
| Encounter | 95,852 |
| Condition | 48,406 |
| MedicationRequest | 85,333 |
| Organization | 852 |
| Selected Observation | 90,885 |
| **Total** | **322,522** |

The cohort has 1,196 JSON Bundles, 2,031,315 source resources, and approximately
5.41 GiB of raw files. Validation found zero quarantines or orphan references;
unchanged reruns skipped all files. The warehouse has 13 models and 49 checks.
Unsupported resource types are counted as ignored, not claimed as loaded or
clinically validated. Provider aliases cover all 57,819 finished encounters in
the latest ten-year modeling window, with complete provider ZIP assignment.
Selected Observations include 111,357 individual quantities because blood-pressure
panels contain two components. Among these, 6,349 eGFR values with non-indexed
units are preserved separately and excluded from indexed eGFR model features.
Provider organizations are reported separately from clinical table counts.

## Generate and validate

Requires Java 17+, PostgreSQL, and the Python analytics dependencies:

```bash
bash scripts/generate_synthea.sh data/synthea-v3.3.0-p1000-seed43 1000 43
bash scripts/validate_synthea.sh data/synthea-v3.3.0-p1000-seed43/fhir synthea_p1000_seed43
```

Generation verifies the pinned JAR checksum and refuses existing destinations.
If input already exists, reuse it and choose a fresh validation schema name.
The validator refuses existing schemas and output directories rather than
clearing them. It reads database configuration from `.env` or `DATABASE_URL`.

Validation reconciles resource IDs, selected values, patient links, medication
codes, input accounting, quarantines, and orphans. It checks duplicate-safe reruns,
builds/tests dbt, and compares aggregate counts and duration statistics to source.

## Inspect a run

- Raw inputs: `data/synthea-v3.3.0-p1000-seed43/fhir/`
- Run results and input checksums: `data/validation/<schema>/results.json` and `manifest.json`
- Run-specific dbt evidence: `data/validation/<schema>/dbt/`
- Shared detailed dbt log: `warehouse/logs/dbt.log`
- Database schemas: `<schema>` and `<schema>_analytics`

Generated inputs, binaries, logs, and run reports remain under ignored `data/`.
Seeds identify generation settings; per-file checksums identify exact replay
inputs. This is subset compatibility testing, not a production capacity or
clinical validation benchmark.

## Independent hospitalization cohort

The separate seed-44 run contains 1,143 patients and 216,182 supported resources,
with zero quarantines and all 39 dbt checks passing. Its schema is
`synthea_p1000_seed44`; it does not change the primary dashboard cohort.
The frozen hospitalization experiment evaluates 6,226 quarterly snapshots from
794 eligible adults, with 101 positive snapshots among 92 patients. These are
synthetic simulator-seed checks, not clinical validation.
