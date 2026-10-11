# Measured results (simulated engine, held-out runs)

```json
{
  "window_level": {
    "health_mae": 0.79,
    "forecast_mae_120s": 3.82,
    "forecast_mae_persistence_baseline": 3.99,
    "forecast_mae_120s_active_faults": 4.29,
    "forecast_mae_persistence_active_faults": 9.57,
    "engine_fault_accuracy": 0.993,
    "engine_fault_precision": 0.999,
    "engine_fault_recall": 0.98,
    "sensor_fault_accuracy": 0.955,
    "sensor_fault_recall_by_class": {
      "none": 0.981,
      "temp": 0.978,
      "rpm": 0.886,
      "vib": 0.858,
      "press": 0.786
    },
    "sensor_confusion_matrix(rows=true,cols=pred;none,temp,rpm,vib,press)": [
      [
        11890,
        91,
        9,
        34,
        99
      ],
      [
        15,
        671,
        0,
        0,
        0
      ],
      [
        123,
        0,
        953,
        0,
        0
      ],
      [
        10,
        0,
        47,
        681,
        56
      ],
      [
        129,
        65,
        11,
        9,
        787
      ]
    ],
    "anomaly_auc": 0.854
  },
  "n_train_runs": 320,
  "n_test_runs": 80,
  "trained_on": "simulator",
  "trained_at": "2026-10-09 19:00:04",
  "train_seconds": 26.6,
  "window_s": 30,
  "forecast_horizon_s": 120,
  "system_level": {
    "runs": {
      "healthy": 8,
      "engine_fault": 8,
      "sensor_fault": 8
    },
    "false_alarm_rate_healthy_engine": 0.0,
    "engine_fault_missed_rate": 0.0,
    "median_detection_delay_s_after_10pct_damage": 75.5,
    "median_warning_lead_time_before_critical_s": 93.0,
    "min_warning_lead_time_before_critical_s": 38.0,
    "sensor_fault_false_engine_alarm_rate_WITH_validator": 0.0,
    "sensor_fault_false_engine_alarm_rate_NAIVE_no_validator": 0.125
  }
}
```

These numbers come from a physics **simulator**, not a real UAV. They show the pipeline works as designed; re-measure on real data before making real-world claims.
