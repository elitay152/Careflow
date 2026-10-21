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
season. The separate residual-band experiment below adds retrospective interval
diagnostics; results do not establish
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

## Prediction interval experiment

[config/flusight-interval-protocol.json](../config/flusight-interval-protocol.json)
fixes nominal levels of 80% and 95%, the calibration cutoff, quantile rule, rounding,
and metrics. It was committed before computing interval coverage, after the point
holdout results had already been inspected. The point-model protocol is unchanged.
This is a retrospective experiment, not a new untouched holdout test.

For each of the four fixed models and horizons, calibration uses 102 earlier
weekly absolute errors from the existing validation predictions, July 1, 2023
through June 7, 2025. The last three validation target weeks are excluded so the
cutoff is no later than the earliest holdout forecast origin (four weeks before
July 5). This timing constraint applies to calibration radii; it does not turn
the existing retrospective model-selection process into a historical deployment.
Earlier seasons also informed model selection, and errors are temporally dependent.
There is no exchangeability or guaranteed-coverage claim.

The radius is sorted absolute error at one-based rank `ceil((n+1)*level/100)`:
rank 83 for 80%, rank 98 for 95%. Radii do not adapt to holdout outcomes. Bounds
are `max(0, floor(point-radius))` and `ceil(point+radius)`; endpoints count as
covered. Width therefore varies when the lower bound is clipped at zero.

| Selected-model horizon | 80% coverage | Mean width | 95% coverage | Mean width |
| --- | ---: | ---: | ---: | ---: |
| 1 week | 78.8% | 4,940 | 90.4% | 13,826 |
| 2 weeks | 76.9% | 6,119 | 90.4% | 18,124 |
| 3 weeks | 73.1% | 6,639 | 88.5% | 20,055 |
| 4 weeks | 76.9% | 9,490 | 88.5% | 22,253 |

Widths are admissions; each row evaluates 52 correlated target weeks. The simple
bands under-cover their nominal levels, particularly around the peak. The website
exposes width, coverage, lower/upper misses, a persistence comparison, and peak-period
coverage instead of asserting the nominal level was achieved. Nominal levels do
not give a guaranteed probability for an individual week.

The export includes the interval protocol and SHA-256, calibration counts and
quantile ranks, radii, and pooled/peak-period metrics. The frontend contract
independently recomputes these from validated earlier predictions and holdout
observations. Mean interval score is width plus `2/alpha` times the distance
outside the appropriate bound, averaged across target weeks (`alpha=1-level/100`).
Lower scores are better at the same nominal level and on the same weeks. Empty
period metrics are null. Bands describe the rolling holdout predictions only;
no future intervals are exported and the existing publication gate is unchanged.

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
