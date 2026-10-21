# Public influenza admission forecasting

The website's separate real-data track measures weekly reported U.S.
laboratory-confirmed influenza hospital admissions from CDC/NHSN FluSight target
data. These are admissions, not unique patients, occupancy, or synthetic FHIR
records. No source model submissions are used.

## Reproduce

```bash
.venv/bin/python -m careflow.flu_forecast --download
```

Without `--download`, the command uses the local pinned cache. `--input` accepts
an exact copy of the pinned CSV. The checksum must match before evaluation.
Raw input stays in ignored `data/external/flusight/`; the aggregate artifact is
`data/public/flusight.json`. Neither requires database credentials or model APIs.
The dashboard's `npm run flu:refresh` runs this command, validates and copies the
separate artifact, and builds the site; `-- --offline` uses the cached source.
Refreshing does not advance the source revision or evaluation protocol.

## Frozen evaluation

[config/flusight-protocol.json](../config/flusight-protocol.json) pins source
revision `ff95b0fd9883aa15f4ce3f6e46830973f905ffd1`, input SHA-256,
model settings, windows, and publication rule. The protocol was committed before
running the benchmark. The national series contains 246 consecutive Saturdays,
February 5, 2022–October 17, 2026. All 13,038 source rows across 53 locations
are checked; 36 missing state values remain missing. National values have no gaps.

Four fixed candidates forecast one through four weeks ahead. Selection uses
pooled validation MAE over 2023/24 and 2024/25 (105 target weeks, 420 forecasts
per candidate). The reserved 2025/26 season has 52 target weeks and 208 forecasts
per candidate. Each rolling origin sees only its available prefix; later origins
may learn from earlier held-out observations. Standardization and ridge fits use
that prefix alone. Seasonal autoregression uses lagged log counts, a recent mean,
and annual calendar terms.

| Model | Validation MAE | Holdout MAE | Holdout WAPE |
| --- | ---: | ---: | ---: |
| Last-week persistence | 3,391.92 | 3,461.05 | 53.4% |
| Last year's week | 5,159.17 | 5,402.73 | 83.4% |
| Damped recent growth | 3,080.56 | 3,822.36 | 59.0% |
| Seasonal autoregression | 2,588.97 | 4,343.17 | 67.0% |

Seasonal autoregression wins validation but loses to persistence on holdout.
The fixed publication rule requires the selected candidate to beat both baselines
on holdout. **Future projections are withheld.** The holdout does not select a
replacement model. The website shows observed counts, held-out predictions by
horizon, and the complete score comparison.

This is a retrospective benchmark of a pinned revised dataset, not historical
release vintages. Origins assume observations are available at week end;
publication delays are not replayed. Coverage and reporting changes can affect
counts. Overlapping horizons produce correlated errors, there is only one held-out
season, and no uncertainty intervals are estimated. Results do not establish
operational planning performance.

## Peak error analysis

The public artifact includes `holdout.peak_diagnostics`, computed from the existing
held-out predictions after scoring. For each fixed horizon and model it reports
MAE, mean signed error (prediction minus observation), and the share of full-season
absolute error before, within, and after a five-week window centered on the observed
peak. Window endpoints are inclusive; ties for peaks use the earliest week. Empty
period averages and error shares for a perfect forecast are null, not zero.

The observed peak is 42,500 admissions on January 3, 2026. The window is December
20–January 17. At one week, the selected model's MAE is 384 before the window,
14,612 within it, and 2,568 afterward; persistence scores 464, 10,827, and 889.
At four weeks, 68.2% of the selected model's absolute error falls after the window,
with mean overprediction of 8,899 admissions per week in that period.

Peak timing and height compare maxima of the observed series and the rolling
prediction curve at each horizon. That curve combines successive forecast origins;
it is not one season-long forecast. The selected model's peak is one week late at
the one-week horizon and four weeks late at the four-week horizon. The breakdown
locates errors without establishing their cause. It was added after inspecting
holdout results and is descriptive, not a fresh independent test or a tuned model
setting. Period lengths differ; error shares should be read alongside MAE and
week counts. No models, selection scores, or publication decisions are changed.

## Attribution and reuse basis

Source: [CDC FluSight forecast hub target data](https://github.com/cdcepi/FluSight-forecast-hub/tree/ff95b0fd9883aa15f4ce3f6e46830973f905ffd1/target-data),
reported by CDC/NHSN. The pinned repository distributes its contents with an
[MIT notice](licenses/FluSight-MIT.txt), retained in full in the public artifact
and displayed by the website. Only national aggregate observations and our own
benchmark outputs are exported. No CDC endorsement is implied.

The repository, target-data documentation, and repository license were inspected.
The separate CDC portal and agency reuse-policy pages were inaccessible (HTTP
403) in this environment; a separate data-specific CDC license was not verified.
The recorded reuse basis is the pinned repository's MIT distribution, without
claiming an independently verified CDC public-domain designation.
