# Rules-Only Baseline Calibration Log — Checkpoint C11

*eval/calibrate_baseline.py, run against the DEV SET ONLY (eval/dataset_loader.py's hard split guard) -- the locked test set was never touched. Per eval-design.md §5: "`threshold_T` is chosen by sweeping candidate values on the dev set and picking the value that maximizes dev-set F1 ... recorded explicitly so the choice is reproducible, not eyeballed."*

Dev-set legitimate/drift cases scored: 14 legitimate, 14 drift (ambiguous cases excluded from F1, per eval-design §7/§12).

Rule swept (eval-design §5, exact): `IF velocity == elevated AND category_shift_ratio >= threshold_T THEN HOLD ELSE ALLOW`.

## Full sweep table

| threshold_T | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 0.05 **(chosen)** | 4 | 4 | 10 | 10 | 0.5000 | 0.2857 | 0.3636 |
| 0.10 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.15 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.20 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.25 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.30 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.35 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.40 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.45 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.50 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |

## Chosen value: `threshold_T = 0.05` (dev-set F1 = 0.3636)

TP=4, FP=4, FN=10, TN=10, Precision=0.5000, Recall=0.2857.

This value is read by `eval/run.py` when scoring the rules-only baseline -- not re-swept or hardcoded a second time.
