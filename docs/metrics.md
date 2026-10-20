# Careflow metric catalog

These metrics describe the loaded synthetic dataset. They are not population estimates or evidence of treatment effectiveness. This catalog will be the retrieval source for the planned AI assistant.

| Metric | Definition | Grain / source | Important exclusions |
| --- | --- | --- | --- |
| Monthly encounters | Count of encounters with status `finished` | UTC start month; `analytics.mart_monthly_utilization.encounter_count` | All other statuses; no months synthesized when no finished encounters exist |
| Monthly patients | Distinct patients with a finished encounter | UTC start month; `analytics.mart_monthly_utilization.unique_patients` | A patient counts once per month, even with multiple encounters |
| Mean encounter duration | Average elapsed minutes from start to end | UTC start month; `average_duration_minutes` | Null end timestamps are omitted from the mean; not necessarily length of hospital stay |
| Recorded condition prevalence | Distinct patients with a confirmed active/recurrence/relapse condition divided by all loaded patients | Terminology system + code; `analytics.mart_condition_prevalence.prevalence_fraction` | Refuted, error, unconfirmed, unknown, and inactive conditions; no historical as-of interpretation |
| Medication requests | Number of MedicationRequest resources | One request per ID; `analytics.fct_medication_requests` | Does not establish dispensing, administration, or adherence; includes all request statuses unless filtered |

Patient IDs, conditions, medication requests, and encounters are separate grains. Joining all three fact tables directly by patient can multiply rows. Aggregate each fact first or use distinct patient counts when the question calls for them. A raw condition count is not a patient count.

Missing clinical status is stored as `unknown`, not assumed active. Missing verification status is also `unknown`; neither qualifies for the prevalence numerator. All loaded patients form its denominator, including those without a coded condition. Terminology systems remain part of the grouping key to avoid conflating codes from different systems. Only the first coding is retained in this version; terminology mapping is not performed.

The warehouse is a current snapshot with complete table rebuilds. It does not reconstruct historical patient populations or status changes. The denominator includes deceased patients when their records are loaded; patient death dates are not modeled yet. Pipeline source hashes identify the files that supplied normalized records; Dagster run history and dbt artifacts provide execution evidence. The repository's small fixture is intentionally fictional; a separate [Synthea validation](validation.md) measures compatibility and local throughput on a larger synthetic cohort.
