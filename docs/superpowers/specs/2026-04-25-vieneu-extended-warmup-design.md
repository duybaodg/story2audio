# VieNeu Extended Model Warmup

**Date:** 2026-04-25
**Status:** Design Approved

## Problem Statement

The first real TTS request after startup produces poor quality audio compared to subsequent requests. The current single-inference warmup (`model.infer("warmup")`) is insufficient for the model to stabilize.

## Root Cause

Neural TTS models need multiple forward passes to fully initialize:
- **KV cache:** Attention mechanism builds state across inferences
- **Memory allocation:** First inference triggers allocation; subsequent ones reuse it
- **Model stabilization:** Internal state reaches steady state after several passes

## Solution

Perform 5 warmup inferences at startup instead of 1.

## Implementation

**File:** `vieneu_model.py`

**Change in `initialize_model_pool()`:**

```python
# Before
with lock:
    model.infer("warmup")

# After
WARMUP_ITERATIONS = 5
with lock:
    for i in range(WARMUP_ITERATIONS):
        model.infer(f"warmup {i}")
```

## Trade-offs

| Factor | Impact |
|--------|--------|
| Startup time | +5-10 seconds |
| First-request quality | Consistent with rest |
| Complexity | Minimal |

## Testing

1. Start application and verify warmup completes without errors
2. Generate TTS with multiple chunks
3. Verify first chunk sounds consistent with subsequent chunks
4. Measure startup time impact

## Files to Modify

- `vieneu_model.py` — Update `initialize_model_pool()` function
