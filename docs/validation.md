# Synthea validation results

Validated on October 20, 2026 against PostgreSQL 16.13 and Python 3.12.14. This is a measured local run of the supported FHIR subset, not full FHIR conformance testing or a production capacity benchmark.

## Cohort and provenance

- Generator: [official Synthea 3.3.0 release](https://github.com/synthetichealth/synthea/releases/tag/v3.3.0), downloaded with HTTPS verification enabled.
- Observed JAR SHA-256: `8ba04f7d73abadd5a377e41edf24c5c83935a1cb07c6d982cd5db731ef1cf445`. The generation script pins this exact artifact; this is the hash of the official download used here, not a claim of a separately published signature or checksum.
- Population argument: 100; population and clinician seeds: 42; reference and end dates: January 1, 2025; two generator threads; Java 21; 2 GiB JVM heap limit.
- Output: **108 patients** (100 alive, 8 deceased), **110 JSON Bundles**, **120,521 resources**, **357,219,959 bytes** (340.67 MiB).
- Input manifest digest: `31c56487bb9d9fbd20e657205f76a02d0b65904b8d75d449ddc62b8a3f8c8d74`.

Seeds and dates make the generation parameters reproducible. Exact input replay is identified by the saved per-file checksums; byte-for-byte regeneration across runtimes is not assumed. Source files and generator binaries stay under ignored `data/`.

## A compatibility issue found and fixed

The initial pipeline loaded 2,948 medication requests and quarantined **1,204** more. Those requests used `medicationReference` pointing at separate Medication resources, rather than inline `medicationCodeableConcept`.

The loader now indexes Bundle medication codes before processing requests, resolves UUID full URLs and relative `Medication/id` references regardless of entry order, and retains terminology system/code on each medication request. Missing references are quarantined; conflicting reference definitions reject the file. No network lookup is performed. Cross-Bundle and contained medication references remain unsupported.

Regression tests cover successful UUID/relative resolution, missing references, and ambiguous definitions. Additional audit tests deliberately corrupt a valid patient link or medication code and confirm that source reconciliation catches the problem.

## Final measured result

| Resource | Source records | Loaded rows |
| --- | ---: | ---: |
| Patient | 108 | 108 |
| Encounter | 6,428 | 6,428 |
| Condition | 3,514 | 3,514 |
| MedicationRequest | 4,152 | 4,152 |
| **Supported total** | **14,202** | **14,202** |

- **Zero quarantined records**, zero orphan patient references, and no ID, patient-link, selected stored-value, or medication-code discrepancies.
- **106,319 resources outside the four persisted types** counted as ignored. These include observations, claims, procedures, and provider resources. Medication resources contribute lookup codes but are not persisted independently. The result does not claim that these other clinical domains were validated or loaded.
- All **110 files skipped** on unchanged rerun; database counts and report output stayed unchanged.
- All **10 dbt models built** and **32 data quality checks passed**, with no skipped dbt nodes.
- Independent calculations from raw source matched **463 monthly utilization groups** and **108 condition prevalence groups**, including distinct patient counts, mean durations, and prevalence denominators.
- Dashboard export succeeded. Validation used separate raw and analytics schemas; the original demo remained separate.

| Timing | Final local run |
| --- | ---: |
| Initial ingestion of all 110 files | 13.599 seconds |
| Supported records accepted per second | 1,044.3 |
| Unchanged rerun | 2.613 seconds |
| dbt build including checks | 2.698 seconds |

Ingestion timing includes input reads/parsing, validation, database writes, and commits. It excludes generation, independent source inventory/audits, and warehouse build. An earlier post-fix run measured 12.622 seconds; these short runs show local variability, not a throughput guarantee. Rejected records from the initial failing run are not included in successful throughput claims.

Machine-readable evidence is in [validation-results.json](validation-results.json). The full local run directory is `data/validation/synthea_v330_seed42_final/`, containing the input manifest, fresh dbt artifacts, results, and HTML dashboard. Database schemas are retained for inspection.

## Reproduce

Follow the PostgreSQL and analytics installation instructions in the root README, then run:

```bash
bash scripts/generate_synthea.sh
bash scripts/validate_synthea.sh
```

The first command refuses to overwrite existing generated data. If it already exists, skip generation and run validation again. Each validation uses a new schema/output name; explicit existing names are refused. To reuse the original input with a chosen fresh name:

```bash
bash scripts/validate_synthea.sh data/synthea-v3.3.0-seed42/fhir synthea_another_run
```

The manual **Synthea validation** GitHub Actions workflow runs generation and validation and uploads results after the repository changes are pushed. That remote workflow has not been executed here.

The next useful milestone is the AI analytics assistant, grounded in the metric catalog and validated warehouse. Before production-scale work, expand FHIR coverage, model death dates and historical populations, and measure larger/concurrent workloads separately.
