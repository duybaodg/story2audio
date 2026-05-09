# VieNeu Extended Model Warmup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend VieNeu model warmup from 1 to 5 inferences to ensure first real request produces consistent audio quality.

**Architecture:** Modify the `initialize_model_pool()` function in `vieneu_model.py` to perform multiple warmup inferences per model instance at startup.

**Tech Stack:** Python 3.12+, llama.cpp (via vieneu library), threading

---

### Task 1: Create feature branch

**Files:**
- None (git operation)

- [ ] **Step 1: Create and checkout new branch**

```bash
git checkout -b feature/vieneu-extended-warmup
```

Expected: Branch created and checked out, output shows `Switched to a new branch 'feature/vieneu-extended-warmup'`

---

### Task 2: Update warmup to use multiple inferences

**Files:**
- Modify: `vieneu_model.py:70-95` (the `initialize_model_pool()` function)

- [ ] **Step 1: Add WARMUP_ITERATIONS constant**

Add constant at top of file after imports (around line 28):

```python
# Warmup configuration
WARMUP_ITERATIONS = 5  # Number of inferences to stabilize model quality
```

- [ ] **Step 2: Update warmup loop in initialize_model_pool()**

Replace the warmup section (lines 78-88) with:

```python
            # Warm up each model (using the model's lock)
            logger.info("Warming up VieNeu models...")
            print(f"[STARTUP] Warming up {pool_size} VieNeu models ({WARMUP_ITERATIONS} iterations each)...")
            for i, (model, lock) in enumerate(_model_pool):
                try:
                    with lock:  # Acquire lock for warmup
                        for iteration in range(WARMUP_ITERATIONS):
                            model.infer(f"warmup {iteration}")
                    print(f"[STARTUP] Model {i + 1}/{pool_size} warmed up ({WARMUP_ITERATIONS} iterations)")
                    logger.info(f"Model {i + 1}/{pool_size} warmed up ({WARMUP_ITERATIONS} iterations)")
                except Exception as e:
                    print(f"[STARTUP] Model {i + 1} warmup failed: {e}")
                    logger.warning(f"Model {i + 1} warmup failed: {e}")
```

- [ ] **Step 3: Verify the changes**

```bash
cat vieneu_model.py | grep -A 5 "WARMUP_ITERATIONS"
```

Expected: Shows the constant and its usage in the warmup loop

- [ ] **Step 4: Commit**

```bash
git add vieneu_model.py
git commit -m "feat: extend VieNeu warmup to 5 iterations for consistent audio quality"
```

---

### Task 3: Verify the change works

**Files:**
- None (verification only)

- [ ] **Step 1: Start the application and observe warmup**

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Expected: Console output shows:
```
[STARTUP] Warming up 1 VieNeu models (5 iterations each)...
[STARTUP] Model 1/1 warmed up (5 iterations)
```

Wait for startup to complete (should take 5-10 seconds longer than before).

- [ ] **Step 2: Test TTS generation**

```bash
curl -X POST "http://localhost:8000/tts/generate" \
  -H "Content-Type: application/json" \
  -d '{"text": "Đây là bài kiểm tra chất lượng âm thanh.", "voice": "vieneu-v2", "format": "mp3"}' \
  --output test_first_chunk.mp3
```

Expected: Audio file downloads successfully.

- [ ] **Step 3: Generate second chunk for comparison**

```bash
curl -X POST "http://localhost:8000/tts/generate" \
  -H "Content-Type: application/json" \
  -d '{"text": "Đây là đoạn âm thanh thứ hai để so sánh.", "voice": "vieneu-v2", "format": "mp3"}' \
  --output test_second_chunk.mp3
```

- [ ] **Step 4: Listen to both audio files**

```bash
# On macOS:
afplay test_first_chunk.mp3
afplay test_second_chunk.mp3

# On Linux:
mpg123 test_first_chunk.mp3
mpg123 test_second_chunk.mp3
```

Expected: Both audio chunks sound consistent in quality (no noticeable degradation in first chunk).

- [ ] **Step 5: Clean up test files**

```bash
rm test_first_chunk.mp3 test_second_chunk.mp3
```

---

### Task 4: Update documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-04-25-vieneu-extended-warmup-design.md`

- [ ] **Step 1: Update spec status to implemented**

Add to the header:

```markdown
**Date:** 2026-04-25
**Status:** Implemented ✅
```

- [ ] **Step 2: Commit documentation update**

```bash
git add docs/superpowers/specs/2026-04-25-vieneu-extended-warmup-design.md
git commit -m "docs: mark VieNeu extended warmup as implemented"
```

---

### Task 5: Merge to main

**Files:**
- None (git operation)

- [ ] **Step 1: Switch to main branch**

```bash
git checkout main
```

- [ ] **Step 2: Merge feature branch**

```bash
git merge feature/vieneu-extended-warmup --no-ff
```

Expected: Merge commits created successfully.

- [ ] **Step 3: Push to remote**

```bash
git push origin main
```

- [ ] **Step 4: Delete feature branch (optional)**

```bash
git branch -d feature/vieneu-extended-warmup
```
