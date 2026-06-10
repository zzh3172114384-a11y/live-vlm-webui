# Performance Regression Testing - Quick Reference

## 🚀 TL;DR

```bash
# First time: Establish baseline
./scripts/run_performance_tests.sh --save-baseline

# After changes: Check for regressions
./scripts/run_performance_tests.sh

# See baseline
./scripts/run_performance_tests.sh --show-baseline
```

## 📊 What You'll See

### ✅ No Regression (Good!)
```
📊 Frame Resize Performance:
   Mean:   2.28 ms
   P95:    3.05 ms

✅ Performance stable (within 20% of baseline)
   Mean: 2.34 → 2.28 ms (-2.6%)
   P95:  3.12 → 3.05 ms (-2.2%)
```

### ⚠️ Regression Detected (Investigate!)
```
📊 Frame Resize Performance:
   Mean:   3.21 ms
   P95:    4.15 ms

⚠️  PERFORMANCE REGRESSION DETECTED!
   Function: video_processor.resize_frame
   Mean: 2.34 → 3.21 ms (+37.2%)
   P95:  3.12 → 4.15 ms (+33.0%)
   Threshold: 20%

⚠️  Regression detected but not failing test
```

### 🎉 Improvement (Great!)
```
📊 Frame Resize Performance:
   Mean:   1.89 ms
   P95:    2.54 ms

🎉 PERFORMANCE IMPROVEMENT!
   Function: video_processor.resize_frame
   Mean: 2.34 → 1.89 ms (-19.2%)
   P95:  3.12 → 2.54 ms (-18.6%)
```

## 🔄 Workflow

```
┌─────────────────────┐
│ Establish Baseline  │  ./run_performance_tests.sh --save-baseline
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Make Changes       │  vim src/...
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Run Tests          │  ./run_performance_tests.sh
└──────────┬──────────┘
           ↓
      Is it slower?
           ├─ No → ✅ Done!
           │
           └─ Yes ↓
┌─────────────────────┐
│  Profile Code       │  ./profile_code.sh component
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Optimize           │  Fix bottleneck
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Re-test            │  ./run_performance_tests.sh
└──────────┬──────────┘
           ↓
      Fixed?
           ├─ Yes → Update baseline
           └─ No → Repeat optimization
```

## 📋 Commands

| Command | Purpose |
|---------|---------|
| `--save-baseline` | Save current performance as new baseline |
| `--show-baseline` | Show current baseline values |
| `--fail-on-regression` | Make tests fail on regression (for CI) |
| `--baseline-file FILE` | Use custom baseline file |

## 🎯 Two-Tier Protection

```python
# Tier 1: Regression Detection (Soft Warning)
# Warns if >20% slower than baseline
# Doesn't fail tests by default

# Tier 2: Hard Limits (Critical)
# Must be under 33ms for 30fps
# WILL fail tests
assert stats['p95'] < 33.33, "CRITICAL!"
```

## 🔍 When to Update Baseline

### ✅ Update When:
- Performance improved significantly
- Made intentional architectural changes
- After optimization work
- Switching to better algorithm

### ❌ Don't Update When:
- Just to "make tests pass"
- Regression is unintentional
- Haven't investigated why it's slower
- Performance got worse

## 💾 Baseline File

Location: `.performance_baseline.json`

```json
{
  "video_processor.resize_frame": {
    "mean_ms": 2.34,
    "p95_ms": 3.12,
    "timestamp": "2025-11-08T10:30:15",
    ...
  }
}
```

**Should you commit it?**
- ✅ Yes, for team consistency
- ✅ Track performance over time
- ✅ Compare across branches

## 🎓 Best Practices

1. **Establish baseline early** - Before optimizing
2. **Check regularly** - After every significant change
3. **Profile before optimizing** - Don't guess!
4. **Update baseline after improvements** - Track progress
5. **Commit baseline** - Share with team

## 🐛 Troubleshooting

### "No baseline found"
```bash
# Create one!
./scripts/run_performance_tests.sh --save-baseline
```

### Flaky results
```bash
# Increase iterations in test code
iterations = 50  # instead of 20
```

### Different results on CI
```bash
# Don't fail on regression in CI (warn only)
./scripts/run_performance_tests.sh  # no --fail-on-regression

# Or create CI-specific baseline
./scripts/run_performance_tests.sh --baseline-file .baseline-ci.json
```

## 📚 More Info

- **Full guide**: `docs/performance-regression-testing.md`
- **Test examples**: `tests/unit/test_video_processor.py`
- **Implementation**: `tests/utils/regression.py`

