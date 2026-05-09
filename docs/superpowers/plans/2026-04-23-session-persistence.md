# Session Persistence & State Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session persistence so users don't lose work on page refresh - text, settings, and audio generation state all restore automatically.

**Architecture:** Client-side localStorage for UI state + new server endpoint to verify cache still exists. Prominent restoration banner with "Continue Working" or "Start Fresh" options.

**Tech Stack:** localStorage API, FastAPI, existing cache system

---

## File Structure

| File | Purpose |
|------|---------|
| `main.py` | Add `/tts/session/{cache_id}` endpoint for cache verification |
| `templates/index.html` | Add session management JS and restoration banner UI |
| `tests/test_session_api.py` | Tests for new endpoint |

---

## Task 1: Create Feature Branch

**Files:**
- Create: new branch `feature/session-persistence`

- [ ] **Step 1: Create and checkout new branch**

```bash
git checkout -b feature/session-persistence
```

Expected: Branch created and checked out

- [ ] **Step 2: Verify branch**

```bash
git branch --show-current
```

Expected: `feature/session-persistence`

---

## Task 2: Backend - Add Session Verification Endpoint

**Files:**
- Modify: `main.py` (after line 100, near other endpoints)
- Create: `tests/test_session_api.py`

- [ ] **Step 1: Write the failing test first**

Create `tests/test_session_api.py`:

```python
import pytest
import json
from pathlib import Path
from fastapi.testclient import TestClient
from main import app, CACHE_DIR, save_cache_meta, get_meta_path, get_audio_path

client = TestClient(app)


def test_verify_session_exists():
    """Test that /tts/session/{cache_id} returns 200 for valid cache."""
    # Create a mock cache file
    cache_id = "test_cache_123"
    audio_path = Path(CACHE_DIR) / f"{cache_id}.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_text("mock audio data")

    # Create metadata
    metadata = {
        "duration": 120.5,
        "text_length": 1500,
        "voice": "vi-VN-HoaiMyNeural",
        "created_at": "2026-04-23T10:00:00Z"
    }
    save_cache_meta(cache_id, metadata)

    response = client.get(f"/tts/session/{cache_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True
    assert data["cache_id"] == cache_id
    assert data["duration"] == 120.5

    # Cleanup
    audio_path.unlink(missing_ok=True)
    get_meta_path(cache_id).unlink(missing_ok=True)


def test_verify_session_not_found():
    """Test that /tts/session/{cache_id} returns 404 for missing cache."""
    response = client.get("/tts/session/nonexistent_cache")
    assert response.status_code == 404
    data = response.json()
    assert data["detail"] == "Cache not found"


def test_verify_session_with_wav():
    """Test that endpoint works with lossless WAV files."""
    cache_id = "test_wav_cache"
    wav_path = Path(CACHE_DIR) / f"{cache_id}.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_text("mock wav data")

    response = client.get(f"/tts/session/{cache_id}")
    assert response.status_code == 200
    assert response.json()["exists"] is True

    # Cleanup
    wav_path.unlink(missing_ok=True)


def test_verify_session_no_metadata():
    """Test that endpoint works when only audio file exists (no metadata)."""
    cache_id = "test_no_meta"
    audio_path = Path(CACHE_DIR) / f"{cache_id}.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_text("audio data")

    response = client.get(f"/tts/session/{cache_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["exists"] is True
    assert data["duration"] is None

    # Cleanup
    audio_path.unlink(missing_ok=True)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_session_api.py -v
```

Expected: FAIL with "404 Not Found" or endpoint not defined

- [ ] **Step 3: Implement the endpoint in main.py**

Add after line ~100 (after rate limiter middleware):

```python
# ---------------------------------------------------------------------------
# Session Verification Endpoint
# ---------------------------------------------------------------------------
@app.get("/tts/session/{cache_id}")
async def verify_session(cache_id: str):
    """
    Verify if a cached TTS result still exists.
    Returns cache metadata if found, 404 if not.
    Used by frontend for session restoration after page refresh.
    """
    cache_dir = Path(CACHE_DIR)

    # Check if audio file exists (try .mp3 first, then .wav for lossless)
    audio_path = get_audio_path(cache_id, "mp3")
    if not audio_path.exists():
        audio_path = get_audio_path(cache_id, "wav")

    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Cache not found")

    # Load metadata if available
    metadata = load_cache_meta(cache_id) or {}

    return {
        "exists": True,
        "cache_id": cache_id,
        "duration": metadata.get("duration"),
        "text_length": metadata.get("text_length"),
        "voice": metadata.get("voice"),
        "created_at": metadata.get("created_at")
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_session_api.py -v
```

Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_session_api.py main.py
git commit -m "feat: add session verification endpoint

Add GET /tts/session/{cache_id} endpoint to verify cached TTS
results still exist. Used by frontend for session restoration.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Frontend - Add Session Management Core Functions

**Files:**
- Modify: `templates/index.html` (in the `<script>` section, after line ~1080 where variables are declared)

- [ ] **Step 1: Add session storage key constant**

After the `let subtitleCursor = 0;` line (~line 1078), add:

```javascript
// ---------------------------------------------------------------------------
// Session Persistence
// ---------------------------------------------------------------------------
const SESSION_STORAGE_KEY = 'story2audio_session';
const SESSION_EXPIRY_MS = 24 * 60 * 60 * 1000; // 24 hours
```

- [ ] **Step 2: Add session save/load functions**

```javascript
/**
 * Save current session state to localStorage
 */
function saveSession(state) {
    try {
        const sessionData = {
            ...state,
            timestamp: Date.now()
        };
        localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessionData));
    } catch (e) {
        console.warn('Failed to save session:', e);
    }
}

/**
 * Load session from localStorage, checking expiry
 * @returns {Object|null} Session data or null if expired/missing
 */
function loadSession() {
    try {
        const data = localStorage.getItem(SESSION_STORAGE_KEY);
        if (!data) return null;

        const session = JSON.parse(data);

        // Check expiry
        if (Date.now() - session.timestamp > SESSION_EXPIRY_MS) {
            clearSession();
            return null;
        }

        return session;
    } catch (e) {
        console.warn('Failed to load session:', e);
        clearSession();
        return null;
    }
}

/**
 * Clear saved session from localStorage
 */
function clearSession() {
    try {
        localStorage.removeItem(SESSION_STORAGE_KEY);
    } catch (e) {
        console.warn('Failed to clear session:', e);
    }
}

/**
 * Get current session state from DOM and variables
 */
function getCurrentSessionState() {
    return {
        cache_id: currentCacheId,
        status: currentMode === 'cached' ? 'completed' :
                (currentMode === 'mse' || currentMode === 'fallback') && pollingInterval ? 'generating' :
                'draft',
        text: textInput.value,
        voice: voiceSel.value,
        engine: voiceSel.value.startsWith('vieneu:') ? 'vieneu' :
               voiceSel.value.startsWith('gtts:') ? 'gtts' : 'edge',
        language: currentLang,
        audio_quality: document.getElementById('audioQuality').value,
        add_natural_pauses: document.getElementById('naturalPauses').value === 'true',
        pause_duration_ms: 300,
        progress: lastStatus ? (lastStatus.progress || 0) / (lastStatus.total || 1) * 100 : null,
        document_source: window.documentSource || null
    };
}
```

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add session save/load core functions

Add localStorage-based session persistence functions.
Includes saveSession, loadSession, clearSession, and
getCurrentSessionState helper.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Frontend - Add Cache Verification Function

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add verifyCacheId function**

After the session functions from Task 3, add:

```javascript
/**
 * Verify with server if a cache_id still exists
 * @param {string} cacheId - The cache ID to verify
 * @returns {Promise<Object>} { exists: boolean, ...metadata }
 */
async function verifyCacheId(cacheId) {
    if (!cacheId) return { exists: false };

    try {
        const res = await fetch(`/tts/session/${cacheId}`);
        if (res.status === 404) {
            return { exists: false };
        }
        return await res.json();
    } catch (e) {
        console.warn('Failed to verify cache:', e);
        // On network error, assume cache exists and attempt restoration
        return { exists: true };
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add templates/index.html
git commit -m "feat: add server cache verification function

Add verifyCacheId() to check if cached audio still exists
on server before restoring session.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Frontend - Add Restoration Banner UI

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add restoration banner CSS**

In the `<style>` section (find `.toast-container` and add after it, around line 600-700):

```css
/* Session Restoration Banner */
.session-banner {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    background: linear-gradient(135deg, #10b981, #059669);
    color: white;
    padding: 16px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    z-index: 1000;
    transform: translateY(-100%);
    transition: transform 0.3s ease;
}

.session-banner.visible {
    transform: translateY(0);
}

.session-banner-content {
    display: flex;
    align-items: center;
    gap: 12px;
}

.session-banner-icon {
    font-size: 24px;
}

.session-banner-text {
    flex: 1;
}

.session-banner-title {
    font-weight: 700;
    font-size: 16px;
    margin-bottom: 2px;
}

.session-banner-desc {
    font-size: 13px;
    opacity: 0.9;
}

.session-banner-actions {
    display: flex;
    gap: 8px;
}

.session-banner-btn {
    padding: 10px 20px;
    border-radius: 8px;
    font-weight: 600;
    font-size: 14px;
    cursor: pointer;
    border: none;
    transition: all 0.2s;
}

.session-banner-btn.primary {
    background: white;
    color: #059669;
}

.session-banner-btn.primary:hover {
    background: #f0fdf4;
}

.session-banner-btn.secondary {
    background: rgba(255,255,255,0.2);
    color: white;
}

.session-banner-btn.secondary:hover {
    background: rgba(255,255,255,0.3);
}

/* Add top padding to body when banner is visible */
body.has-session-banner {
    padding-top: 80px;
}

@media (max-width: 600px) {
    .session-banner {
        flex-direction: column;
        align-items: stretch;
        padding: 12px 16px;
    }

    .session-banner-actions {
        justify-content: stretch;
    }

    .session-banner-btn {
        flex: 1;
    }

    body.has-session-banner {
        padding-top: 120px;
    }
}
```

- [ ] **Step 2: Add restoration banner HTML**

After the toast container (after line ~1048), add:

```html
<!-- Session Restoration Banner -->
<div id="sessionBanner" class="session-banner">
    <div class="session-banner-content">
        <span class="session-banner-icon">✓</span>
        <div class="session-banner-text">
            <div class="session-banner-title">Session Restored</div>
            <div class="session-banner-desc" id="sessionBannerDesc">Your previous work has been restored.</div>
        </div>
    </div>
    <div class="session-banner-actions">
        <button id="sessionFreshBtn" class="session-banner-btn secondary">Start Fresh</button>
        <button id="sessionContinueBtn" class="session-banner-btn primary">Continue Working</button>
    </div>
</div>
```

- [ ] **Step 3: Add banner JavaScript functions**

```javascript
/**
 * Show the session restoration banner
 * @param {string} description - Optional custom description
 */
function showSessionBanner(description) {
    const banner = document.getElementById('sessionBanner');
    const descEl = document.getElementById('sessionBannerDesc');
    // Use textContent for security (prevents XSS from stored sessions)
    descEl.textContent = description || 'Your previous work has been restored.';
    banner.classList.add('visible');
    document.body.classList.add('has-session-banner');
}

/**
 * Hide the session restoration banner
 */
function hideSessionBanner() {
    const banner = document.getElementById('sessionBanner');
    banner.classList.remove('visible');
    document.body.classList.remove('has-session-banner');
}

/**
 * Setup banner event listeners
 */
function setupSessionBanner() {
    document.getElementById('sessionContinueBtn').addEventListener('click', async () => {
        hideSessionBanner();
        await restoreSession();
    });

    document.getElementById('sessionFreshBtn').addEventListener('click', () => {
        clearSession();
        hideSessionBanner();
        resetUI();
        textInput.value = '';
        window.documentSource = null;
    });
}
```

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat: add session restoration banner UI

Add prominent green banner at top of page with
'Continue Working' and 'Start Fresh' buttons.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Frontend - Add Session Restoration Logic

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add restoreSession and helper functions**

```javascript
/**
 * Restore UI state from session data
 */
async function restoreSession() {
    const session = loadSession();
    if (!session) return;

    // Restore text
    if (session.text) {
        textInput.value = session.text;
    }

    // Restore language
    if (session.language) {
        currentLang = session.language;
        // Update language buttons
        document.querySelectorAll('.lang-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.lang === session.language);
        });
        // Reload voices for this language
        await loadVoicesForLanguage(session.language);
    }

    // Restore voice selection
    if (session.voice) {
        voiceSel.value = session.voice;
        updateEngineIndicator();
    }

    // Restore audio quality settings
    if (session.audio_quality) {
        document.getElementById('audioQuality').value = session.audio_quality;
    }
    if (session.add_natural_pauses !== undefined) {
        document.getElementById('naturalPauses').value = session.add_natural_pauses.toString();
    }

    // Restore document source indicator
    if (session.document_source && session.document_source.filename) {
        window.documentSource = session.document_source;
        showSourceIndicator(session.document_source.filename, session.document_source.chapterCount);
    }

    // Handle audio state based on status
    if (session.status === 'completed' && session.cache_id) {
        // Verify cache still exists
        const verification = await verifyCacheId(session.cache_id);
        if (verification.exists) {
            currentCacheId = session.cache_id;
            currentMode = 'cached';
            playerWrap.style.display = 'block';
            showDownloadButtons(session.cache_id, true);
            setStatus('Đã khôi phục audio từ phiên trước.');
            setBadge('cached');
        } else {
            // Cache was deleted, restore as draft
            showSessionBanner('Previous audio no longer available. Your text has been restored.');
            currentCacheId = null;
            currentMode = null;
        }
    } else if (session.status === 'generating' && session.cache_id) {
        // Resume in-progress generation
        const verification = await verifyCacheId(session.cache_id);
        if (verification.exists) {
            currentCacheId = session.cache_id;
            playerWrap.style.display = 'block';
            startPolling(session.cache_id);
            setStatus('Đang khôi phục phiên làm việc...');
            // Update session status to generating
            saveSession(getCurrentSessionState());
        } else {
            showSessionBanner('Audio generation was interrupted. Please try again.');
        }
    }
    // For 'draft' status, UI is already restored above
}

/**
 * Show document source indicator in textarea
 * Uses safe DOM methods to prevent XSS
 */
function showSourceIndicator(filename, chapterCount) {
    let indicator = document.querySelector('.source-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.className = 'source-indicator';
        textInput.parentNode.insertBefore(indicator, textInput);
    }
    // Use textContent for filename to prevent XSS
    const small = document.createElement('small');
    small.textContent = `📄 ${sanitizeFilename(filename)} (${chapterCount} chương)`;
    indicator.innerHTML = '';
    indicator.appendChild(small);
}

/**
 * Basic filename sanitization to prevent XSS
 */
function sanitizeFilename(filename) {
    return filename.replace(/[<>"/']/g, '');
}
```

- [ ] **Step 2: Commit**

```bash
git add templates/index.html
git commit -m "feat: add session restoration logic

Add restoreSession() to restore text, voice, settings, and
audio state. Handles completed, in-progress, and draft states.
Uses safe DOM methods to prevent XSS.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Frontend - Add Auto-Save Triggers

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add debounced text input save**

```javascript
// Debounced save for text input
let saveTimeout = null;
textInput.addEventListener('input', () => {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
        saveSession(getCurrentSessionState());
    }, 500); // Debounce 500ms
});
```

- [ ] **Step 2: Add save triggers for settings changes**

```javascript
// Save session on voice change
voiceSel.addEventListener('change', () => {
    saveSession(getCurrentSessionState());
});

// Save session on audio quality change
document.getElementById('audioQuality').addEventListener('change', () => {
    saveSession(getCurrentSessionState());
});

// Save session on natural pauses change
document.getElementById('naturalPauses').addEventListener('change', () => {
    saveSession(getCurrentSessionState());
});
```

- [ ] **Step 3: Save session on generation start**

In the convertBtn click handler (around line 3273), after `currentCacheId = data.cache_id;`, add:

```javascript
// Save session state when generation starts
saveSession(getCurrentSessionState());
```

- [ ] **Step 4: Save session on generation complete**

In the polling logic where status becomes 'completed' (around line 3234-3260), add:

```javascript
if (data.status === 'completed') {
    // ... existing code ...

    // Save session as completed
    saveSession(getCurrentSessionState());
}
```

- [ ] **Step 5: Save session on document upload**

Where `window.documentSource` is set (around lines 1636 and 2698), add after each:

```javascript
saveSession(getCurrentSessionState());
```

- [ ] **Step 6: Commit**

```bash
git add templates/index.html
git commit -m "feat: add auto-save triggers for session persistence

Auto-save session on: text input (debounced), voice/quality changes,
generation start/complete, and document upload.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Frontend - Add Page Load Session Check

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add initialization function**

```javascript
/**
 * Check for existing session on page load
 */
async function initSessionRestore() {
    const session = loadSession();
    if (!session) return;

    // Verify cache if exists
    let showBanner = true;
    let description = 'Your previous work has been restored.';

    if (session.cache_id) {
        const verification = await verifyCacheId(session.cache_id);
        if (!verification.exists) {
            // Cache was deleted, update description
            description = 'Your text has been restored. Previous audio is no longer available.';
            // Update session to draft status
            session.cache_id = null;
            session.status = 'draft';
            saveSession(session);
        }
    }

    if (showBanner) {
        showSessionBanner(description);
    }

    // Setup banner handlers
    setupSessionBanner();
}
```

- [ ] **Step 2: Call init on page load**

Add to the DOMContentLoaded listener or at the end of script initialization:

```javascript
// Initialize session restore on page load
initSessionRestore();
```

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: add page load session check

Check for existing session on page load and show restoration
banner if found. Verify cache exists before restoring.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Frontend - Add Multi-Tab Sync Support

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add storage event listener**

```javascript
// Handle multi-tab sync via storage event
window.addEventListener('storage', (e) => {
    if (e.key === SESSION_STORAGE_KEY && e.newValue !== e.oldValue) {
        // Another tab updated the session
        // For simplicity, reload to get fresh state
        location.reload();
    }
});
```

- [ ] **Step 2: Commit**

```bash
git add templates/index.html
git commit -m "feat: add multi-tab session sync

Listen for storage events to sync session state across
multiple tabs. Reloads page when another tab updates session.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Testing & Verification

**Files:**
- No file changes

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests pass

- [ ] **Step 2: Start dev server**

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- [ ] **Step 3: Manual test - Draft persistence**

1. Open http://localhost:8000
2. Enter some text
3. Select a voice
4. Refresh page
5. Verify: Banner appears, text and voice are restored

- [ ] **Step 4: Manual test - Completed audio restoration**

1. Generate audio and wait for completion
2. Refresh page
3. Verify: Banner appears, audio player is restored

- [ ] **Step 5: Manual test - Start Fresh**

1. Have some text entered
2. Refresh page
3. Click "Start Fresh"
4. Verify: Page is cleared, no text

- [ ] **Step 6: Manual test - Cache deleted scenario**

1. Generate audio
2. Delete the cache file manually from `audio_cache/`
3. Refresh page
4. Verify: Banner shows "audio no longer available" message

- [ ] **Step 7: Manual test - Multi-tab**

1. Open app in two tabs
2. Enter text in tab A
3. Refresh tab B
4. Verify: Tab B shows restored session

- [ ] **Step 8: Manual test - Expiry**

1. Enter text
2. Modify localStorage to set timestamp > 24h ago
3. Refresh page
4. Verify: No banner appears (session expired)

- [ ] **Step 9: Manual test - XSS prevention**

1. Enter text with HTML/script tags: `<script>alert('xss')</script>`
2. Refresh page
3. Verify: No alert appears, text is displayed as plain text

---

## Task 11: Final Cleanup

**Files:**
- No file changes

- [ ] **Step 1: Run full test suite**

```bash
pytest --cov=. --cov-report=html
```

Expected: Coverage report generated

- [ ] **Step 2: Check for console errors**

Open browser DevTools and verify no errors during session save/restore

- [ ] **Step 3: Final commit**

```bash
git add .
git commit -m "chore: final cleanup for session persistence feature

All session persistence functionality complete.
Includes localStorage persistence, server verification,
restoration banner, and multi-tab sync.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
```

---

## Verification Summary

After completing all tasks, the following should work:

- [x] Draft state (text + settings) persists across refresh
- [x] Completed audio player restores after refresh
- [x] In-progress generation can be resumed
- [x] "Start Fresh" clears all persisted data
- [x] Server cache deletion is handled gracefully
- [x] 24-hour session expiry works
- [x] Multi-tab sync via storage events
- [x] All tests pass
- [x] XSS prevention via textContent and sanitization
