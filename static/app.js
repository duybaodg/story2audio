        const SAMPLE_TEXT = {
            vi: 'Ngày xửa ngày xưa, trong một khu rừng xanh thẳm, có một chú thỏ nhỏ rất thông minh và tốt bụng. Mỗi buổi sáng, chú thường chạy quanh hồ nước để chào những người bạn của mình.',
            en: 'Once upon a time, in a quiet green forest, there lived a small rabbit who was clever and kind. Every morning, the rabbit ran around the lake and greeted all of his friends.',
            ja: '昔々、静かな緑の森に、とても賢くて優しい小さなうさぎが住んでいました。毎朝、うさぎは湖のまわりを走りながら、友だちみんなに挨拶をしていました。',
            zh: '很久很久以前，在一片安静的绿色森林里，住着一只聪明又善良的小兔子。每天早晨，它都会绕着湖边奔跑，向所有朋友问好。',
            ko: '옛날 옛적, 조용하고 푸른 숲 속에 아주 영리하고 착한 작은 토끼가 살고 있었습니다. 토끼는 매일 아침 호수 둘레를 달리며 친구들에게 인사를 했습니다.',
            fr: 'Il était une fois, dans une forêt verte et paisible, un petit lapin très intelligent et très gentil. Chaque matin, il courait autour du lac pour saluer tous ses amis.',
            de: 'Es war einmal in einem ruhigen grünen Wald ein kleiner Hase, der klug und freundlich war. Jeden Morgen lief er um den See und begrüßte alle seine Freunde.'
        };

        let currentLang = 'vi';
        let voiceRegistry = {};
        let pollingInterval = null;
        let currentCacheId = null;
        let currentMode = null; // mse | fallback | cached
        let mediaSource = null;
        let sourceBuffer = null;
        let streamReader = null;
        let bytesReceived = 0;
        let streamFinished = false;
        let lastStatus = null;

        let subtitleSupported = false;
        let subtitleEventSource = null;
        let subtitleCues = [];
        let subtitleStreamDone = false;
        let subtitleRaf = null;
        let subtitleCursor = 0;

        // Tab switching
        const tabBtns = document.querySelectorAll('.tab-btn');
        const tabCards = document.querySelectorAll('.card[id$="-tab"]');

        function switchTab(tabId) {
            tabBtns.forEach(btn => {
                btn.classList.toggle('active', btn.dataset.tab === tabId);
            });
            tabCards.forEach(card => {
                const isActive = card.id === tabId + '-tab';
                card.classList.toggle('active', isActive);
                // Remove hidden class when activating a tab
                if (isActive) {
                    card.classList.remove('hidden');
                } else {
                    card.classList.add('hidden');
                }
                card.style.display = isActive ? 'block' : 'none';

                // Load queue when switching to queue tab
                if (tabId === 'queue' && isActive) {
                    loadQueue();
                }
            });
        }

        tabBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                switchTab(btn.dataset.tab);
            });
        });

        // Queue functions
        async function loadQueue() {
            try {
                const response = await fetch('/document/queue');
                if (!response.ok) {
                    console.error('Queue endpoint error:', response.status);
                    return;
                }
                const data = await response.json();
                queueState.documents = data.queue || [];
                renderQueue();
            } catch (error) {
                console.error('Failed to load queue:', error);
            }
        }

        function renderQueue() {
            const container = document.getElementById('queueList');

            if (queueState.documents.length === 0) {
                container.innerHTML = '<div class="queue-empty">Chưa có tài liệu nào trong hàng đợi.<br><br>Nhấn "Tải Tài Liệu" để thêm.</div>';
                return;
            }

            container.innerHTML = '';

            queueState.documents.forEach(doc => {
                const item = document.createElement('div');
                item.className = 'queue-item';
                item.dataset.docId = doc.document_id;

                const statusLabels = {
                    'uploading': 'Đã tải',
                    'extracting': 'Đang trích',
                    'ready': 'Đã sẵn',
                    'error': 'Lỗi',
                    'failed': 'Thất bại'
                };

                item.innerHTML = `
                    <div class="queue-item-info">
                        <div class="queue-filename">${escapeHtml(doc.filename)}</div>
                        <div class="queue-meta">${formatBytes(doc.file_size)} · ${doc.total_chapters || 0} chương</div>
                    </div>
                    <span class="queue-status ${doc.status}">${statusLabels[doc.status] || doc.status}</span>
                    <div class="queue-actions">
                        ${doc.status === 'uploading' ? `
                            <button class="queue-btn primary" data-action="extract" title="Trích xuất">▶</button>
                        ` : ''}
                        ${doc.status === 'ready' ? `
                            <button class="queue-btn" data-action="view" title="Xem chương">📄</button>
                        ` : ''}
                        ${doc.status === 'failed' || doc.status === 'error' ? `
                            <button class="queue-btn primary" data-action="retry" title="Thử lại">🔄</button>
                        ` : ''}
                        <button class="queue-btn danger" data-action="delete" title="Xóa">🗑</button>
                    </div>
                `;

                container.appendChild(item);
            });

            // Setup click handlers
            container.querySelectorAll('.queue-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    const action = e.target.dataset.action;
                    const docId = item.dataset.docId;

                    if (action === 'extract') {
                        e.stopPropagation();
                        startQueueExtraction(docId);
                    } else if (action === 'retry') {
                        e.stopPropagation();
                        retryFailedJob(docId);
                    } else if (action === 'delete') {
                        e.stopPropagation();
                        deleteQueueDocument(docId);
                    } else if (action === 'view') {
                        e.stopPropagation();
                        viewQueueDocument(docId);
                    }
                });
            });
        }

        async function startQueueExtraction(docId) {
            const doc = queueState.documents.find(d => d.document_id === docId);
            if (!doc) return;

            // Use new job-based extraction
            await startExtractionWithJob(docId, doc.filename);
        }

        // Job-based extraction functions
        // Track active extraction jobs with their toasts
        const activeExtractions = new Map(); // docId -> { jobId, toast, filename }

        async function startExtractionWithJob(docId, filename) {
            const doc = queueState.documents.find(d => d.document_id === docId);
            if (!doc) return;

            // Don't switch tabs - let user stay where they are
            // Don't hide upload zone - keep it available

            // Submit job
            try {
                const response = await fetch(`/document/job/${docId}/extract`, {
                    method: 'POST'
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to submit job');
                }

                const data = await response.json();

                // Show toast notification
                const toast = showToast({
                    variant: 'info',
                    title: 'Đang trích xuất...',
                    message: `${filename}: Đang xử lý`,
                    duration: 0  // Don't auto-hide
                });

                // Track this extraction
                activeExtractions.set(docId, {
                    jobId: data.job_id,
                    toast: toast,
                    filename: filename
                });

                pollJobStatus(data.job_id, docId, filename);
            } catch (error) {
                console.error('Job submission error:', error);
                showToast({
                    variant: 'error',
                    title: 'Lỗi',
                    message: error.message
                });
            }
        }

        function pollJobStatus(jobId, docId, filename) {
            const interval = setInterval(async () => {
                try {
                    const response = await fetch(`/document/job/${jobId}/status`);

                    if (!response.ok) {
                        clearInterval(interval);
                        const extraction = activeExtractions.get(docId);
                        if (extraction) {
                            hideToast(extraction.toast);
                            activeExtractions.delete(docId);
                        }
                        showToast({
                            variant: 'error',
                            title: 'Lỗi',
                            message: 'Failed to get job status'
                        });
                        return;
                    }

                    const status = await response.json();

                    // Update toast with progress
                    const extraction = activeExtractions.get(docId);
                    if (extraction && extraction.toast) {
                        const progressBar = extraction.toast.querySelector('.toast-progress-bar');
                        const messageEl = extraction.toast.querySelector('.toast-message');
                        if (progressBar) {
                            progressBar.style.width = `${status.progress * 100}%`;
                        }
                        if (messageEl) {
                            messageEl.textContent = `${filename}: ${status.message || Math.floor(status.progress * 100) + '%'}`;
                        }
                    }

                    // Refresh queue periodically to show updated status
                    if (Math.random() < 0.2) {  // 20% chance per poll
                        loadQueue();
                    }

                    if (status.status === 'completed') {
                        clearInterval(interval);
                        await loadJobResult(jobId, docId, filename);
                    } else if (status.status === 'failed') {
                        clearInterval(interval);
                        const extraction = activeExtractions.get(docId);
                        if (extraction) {
                            hideToast(extraction.toast);
                            activeExtractions.delete(docId);
                        }
                        showToast({
                            variant: 'error',
                            title: 'Trích xuất thất bại',
                            message: `${filename}: ${status.error || 'Unknown error'}`
                        });
                        await loadQueue();
                    }
                } catch (error) {
                    clearInterval(interval);
                    console.error('Polling error:', error);
                }
            }, 2000);
        }

        async function loadJobResult(jobId, docId, filename) {
            const extraction = activeExtractions.get(docId);

            try {
                const response = await fetch(`/document/job/${jobId}/result`);

                if (!response.ok) {
                    throw new Error('Failed to get job result');
                }

                const data = await response.json();

                // Update local document state from backend
                const docResponse = await fetch(`/document/${docId}`);
                if (docResponse.ok) {
                    const docData = await docResponse.json();
                    const doc = queueState.documents.find(d => d.document_id === docId);
                    if (doc) {
                        doc.status = docData.status;
                        doc.total_chapters = docData.total_chapters || data.chapters.length;
                        doc.extraction_progress = docData.extraction_progress || 1.0;
                    }
                }

                // Hide progress toast, show success toast
                if (extraction) {
                    hideToast(extraction.toast);
                    activeExtractions.delete(docId);
                }

                showToast({
                    variant: 'success',
                    title: 'Hoàn thành!',
                    message: `${filename}: ${data.chapters.length} chương đã trích xuất`,
                    duration: 3000
                });

                // Refresh queue to update status
                await loadQueue();
            } catch (error) {
                console.error('Load result error:', error);
                if (extraction) {
                    hideToast(extraction.toast);
                    activeExtractions.delete(docId);
                }
                showToast({
                    variant: 'error',
                    title: 'Lỗi',
                    message: error.message
                });
                await loadQueue();
            }
        }

        async function retryFailedJob(docId) {
            const doc = queueState.documents.find(d => d.document_id === docId);
            if (!doc) return;

            try {
                const response = await fetch(`/document/job/${docId}/retry`, {
                    method: 'POST'
                });

                if (!response.ok) {
                    throw new Error('Failed to retry job');
                }

                const data = await response.json();

                // Start polling the new job
                await startExtractionWithJob(docId, doc.filename);
            } catch (error) {
                console.error('Retry error:', error);
                showToast({
                    variant: 'error',
                    title: 'Thử lại thất bại',
                    message: error.message
                });
            }
        }

        async function deleteQueueDocument(docId) {
            const doc = queueState.documents.find(d => d.document_id === docId);
            if (!doc) return;

            showModal({
                variant: 'warning',
                title: 'Xác Nhận Xóa',
                message: `Bạn có chắc muốn xóa "${escapeHtml(doc.filename)}"?`,
                actions: [
                    { label: 'Hủy', callback: () => closeModal() },
                    { label: 'Xóa', primary: true, callback: async () => {
                        try {
                            const response = await fetch(`/document/${docId}`, { method: 'DELETE' });
                            if (response.ok) {
                                await loadQueue();
                                closeModal();
                            }
                        } catch (error) {
                            console.error('Delete error:', error);
                        }
                    }}
                ]
            });
        }

        async function viewQueueDocument(docId) {
            // Load document structure
            const response = await fetch(`/document/${docId}/structure`);
            const data = await response.json();

            if (data.status !== 'ready') {
                showToast({
                    variant: 'error',
                    title: 'Chưa Sẵn Sàng',
                    message: 'Tài liệu chưa trích xuất xong.'
                });
                return;
            }

            const doc = queueState.documents.find(d => d.document_id === docId);

            // Load chapters from document metadata
            const docResponse = await fetch(`/document/${docId}`);
            const docData = await docResponse.json();

            // Get chapters from document
            uploadState.documentId = docId;
            uploadState.chapters = docData.metadata?.chapters || [];

            // Show chapters in a modal instead of hiding upload zone
            showChapterModal(doc.filename, uploadState.chapters);
        }

        // New function to show chapters in a modal
        function showChapterModal(filename, chapters) {
            const overlay = document.getElementById('modalOverlay');
            const icon = document.getElementById('modalIcon');
            const title = document.getElementById('modalTitle');
            const body = document.getElementById('modalBody');
            const actions = document.getElementById('modalActions');
            const card = overlay.querySelector('.modal-card');

            // Set modal content for chapters
            icon.textContent = '📄';
            title.textContent = filename;

            // Build chapter list HTML
            let chaptersHtml = `
                <div style="max-height: 400px; overflow-y: auto; padding: 8px 0;">
                    <div style="margin-bottom: 12px; color: #64748b;">
                        ${chapters.length} chương đã trích xuất
                    </div>
                    <div class="chapter-tree-modal">
            `;

            chapters.forEach((ch, index) => {
                const chapterNum = ch.chapter_number || index + 1;
                const wordCount = ch.word_count || 0;
                chaptersHtml += `
                    <div class="chapter-item-modal">
                        <label class="chapter-label">
                            <input type="checkbox" class="chapter-checkbox" data-index="${index}"
                                   value="${ch.chapter_id}" data-word-count="${wordCount}">
                            <span class="chapter-title">${escapeHtml(ch.title || `Chương ${chapterNum}`)}</span>
                        </label>
                        <span class="chapter-wordcount">${wordCount.toLocaleString()} từ</span>
                    </div>
                `;
            });

            chaptersHtml += `
                    </div>
                </div>
                <div class="selection-summary" style="margin-top: 12px; padding-top: 12px; border-top: 1px solid #e2e8f0;">
                    <span class="summary-text" id="modalSummaryText">0 chương đã chọn</span>
                </div>
            `;

            body.innerHTML = chaptersHtml;

            // Add action buttons
            actions.innerHTML = `
                <button class="modal-btn" onclick="closeModal()">Đóng</button>
                <button class="modal-btn primary" id="modalSelectAllBtn">Chọn tất cả</button>
                <button class="modal-btn primary" id="modalConvertBtn" disabled>Chuyển đổi →</button>
            `;

            // Setup checkbox handlers
            const checkboxes = body.querySelectorAll('.chapter-checkbox');
            const summaryText = document.getElementById('modalSummaryText');
            const convertBtn = document.getElementById('modalConvertBtn');
            const selectAllBtn = document.getElementById('modalSelectAllBtn');

            function updateSummary() {
                const selected = body.querySelectorAll('.chapter-checkbox:checked');
                const totalWords = Array.from(selected).reduce((sum, cb) => {
                    return sum + parseInt(cb.dataset.wordCount || 0);
                }, 0);
                summaryText.textContent = `${selected.length} chương đã chọn · ${totalWords.toLocaleString()} từ`;
                convertBtn.disabled = selected.length === 0;
            }

            checkboxes.forEach(cb => {
                cb.addEventListener('change', updateSummary);
            });

            selectAllBtn.addEventListener('click', () => {
                const allChecked = Array.from(checkboxes).every(cb => cb.checked);
                checkboxes.forEach(cb => cb.checked = !allChecked);
                updateSummary();
                selectAllBtn.textContent = allChecked ? 'Chọn tất cả' : 'Bỏ chọn';
            });

            convertBtn.addEventListener('click', () => {
                const selected = Array.from(body.querySelectorAll('.chapter-checkbox:checked'));
                if (selected.length === 0) return;

                const selectedChapters = selected.map(cb => {
                    const idx = parseInt(cb.dataset.index);
                    return chapters[idx];
                });

                closeModal();
                startConversionFromChapters(selectedChapters, filename);
            });

            // Add modal-specific styles
            card.style.maxWidth = '500px';

            overlay.classList.remove('hidden');

            // Add CSS for modal chapter tree
            if (!document.getElementById('modalChapterStyles')) {
                const style = document.createElement('style');
                style.id = 'modalChapterStyles';
                style.textContent = `
                    .chapter-tree-modal {
                        display: flex;
                        flex-direction: column;
                        gap: 4px;
                    }
                    .chapter-item-modal {
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        padding: 8px 12px;
                        border-radius: 8px;
                        background: #f8fafc;
                    }
                    .chapter-item-modal:hover {
                        background: #f1f5f9;
                    }
                    .chapter-label {
                        display: flex;
                        align-items: center;
                        gap: 8px;
                        cursor: pointer;
                        flex: 1;
                    }
                    .chapter-checkbox {
                        width: 18px;
                        height: 18px;
                        cursor: pointer;
                    }
                    .chapter-wordcount {
                        font-size: 12px;
                        color: #94a3b8;
                    }
                    .modal-btn {
                        padding: 8px 16px;
                        border-radius: 8px;
                        border: 1px solid #e2e8f0;
                        background: #fff;
                        cursor: pointer;
                        font-size: 14px;
                    }
                    .modal-btn.primary {
                        background: #3b82f6;
                        color: #fff;
                        border-color: #3b82f6;
                    }
                    .modal-btn.primary:disabled {
                        background: #94a3b8;
                        border-color: #94a3b8;
                        cursor: not-allowed;
                    }
                `;
                document.head.appendChild(style);
            }
        }

        // Function to start conversion from modal chapter selection
        // Fetches content directly and switches to paste-text tab for conversion
        async function startConversionFromChapters(chapters, filename) {
            const chapterIds = chapters.map(ch => ch.chapter_id);

            // Store document source info for later reference
            window.documentSource = {
                documentId: uploadState.documentId,
                filename: filename,
                chapterIds: chapterIds,
                chapterCount: chapters.length
            };

            try {
                // Fetch content for selected chapters
                const response = await fetch(`/document/${uploadState.documentId}/content`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        chapter_ids: chapterIds
                    })
                });

                if (!response.ok) {
                    let errorMsg = 'Không thể tải nội dung';
                    try {
                        const errData = await response.json();
                        errorMsg = errData.detail || errorMsg;
                    } catch (e) {
                        errorMsg = `Lỗi ${response.status}: ${response.statusText}`;
                    }
                    throw new Error(errorMsg);
                }

                const data = await response.json();

                // Switch to paste-text tab
                switchTab('paste-text');

                // Populate textarea
                const textInput = document.getElementById('textInput');
                if (textInput) {
                    textInput.value = data.text || '';
                }

                // Show source indicator
                showSourceIndicator({
                    filename: filename,
                    chapter_count: chapters.length
                });

                // Show success toast
                const totalWords = chapters.reduce((sum, ch) => sum + (ch.word_count || 0), 0);
                showToast({
                    variant: 'success',
                    title: 'Đã tải nội dung',
                    message: `${chapters.length} chương · ${totalWords.toLocaleString()} từ · sẵn sàng chuyển đổi`,
                    duration: 3000
                });

            } catch (error) {
                console.error('Error loading chapter content:', error);
                showModal({
                    variant: 'error',
                    title: 'Không thể tải nội dung',
                    message: error.message,
                    actions: [
                        { label: 'OK', primary: true }
                    ]
                });
            }
        }

        // Helper function to escape HTML (XSS prevention)
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Initialize paste-text tab as active
        switchTab('paste-text');

        // Modal functions
        function showModal(options) {
            const overlay = document.getElementById('modalOverlay');
            const icon = document.getElementById('modalIcon');
            const title = document.getElementById('modalTitle');
            const body = document.getElementById('modalBody');
            const actions = document.getElementById('modalActions');
            const card = overlay.querySelector('.modal-card');

            // Set variant
            card.className = 'modal-card ' + (options.variant || 'info');

            // Set icon
            const icons = {
                error: '⚠️',
                warning: '⚠️',
                info: 'ℹ️',
                success: '✓'
            };
            icon.textContent = icons[options.variant] || icons.info;

            // Set title and body
            title.textContent = options.title || '';
            // Use textContent for security, handle line breaks safely
            body.textContent = '';
            if (options.message) {
                const lines = options.message.split('\n');
                lines.forEach((line, index) => {
                    if (index > 0) {
                        body.appendChild(document.createElement('br'));
                    }
                    body.appendChild(document.createTextNode(line));
                });
            }

            // Clear and add actions
            actions.innerHTML = '';

            if (options.actions) {
                options.actions.forEach(action => {
                    const btn = document.createElement('button');
                    btn.className = 'modal-btn' + (action.primary ? ' primary' : '');
                    btn.textContent = action.label;
                    btn.onclick = () => {
                        overlay.classList.add('hidden');
                        if (action.callback) action.callback();
                    };
                    actions.appendChild(btn);
                });
            }

            // Show modal
            overlay.classList.remove('hidden');
        }

        function closeModal() {
            document.getElementById('modalOverlay').classList.add('hidden');
        }

        // Toast notification functions
        function showToast(options) {
            const container = document.getElementById('toastContainer');
            const toast = document.createElement('div');
            toast.className = `toast ${options.variant || 'info'}`;

            const title = options.title || '';
            const message = options.message || '';
            const duration = options.duration || 4000;

            toast.innerHTML = `
                <div class="toast-content">
                    ${title ? `<div class="toast-title">${escapeHtml(title)}</div>` : ''}
                    ${message ? `<div class="toast-message">${escapeHtml(message)}</div>` : ''}
                    ${options.showProgress !== false ? `
                        <div class="toast-progress">
                            <div class="toast-progress-bar" style="width: 0%"></div>
                        </div>
                    ` : ''}
                </div>
                <button class="toast-close" onclick="this.parentElement.remove()">×</button>
            `;

            container.appendChild(toast);

            // Animate progress bar if present
            if (options.showProgress !== false) {
                const progressBar = toast.querySelector('.toast-progress-bar');
                setTimeout(() => {
                    progressBar.style.width = '100%';
                }, 50);
            }

            // Auto-remove after duration
            if (duration > 0) {
                setTimeout(() => {
                    toast.classList.add('removing');
                    setTimeout(() => toast.remove(), 300);
                }, duration);
            }

            return toast;
        }

        function hideToast(toast) {
            if (toast) {
                toast.classList.add('removing');
                setTimeout(() => toast.remove(), 300);
            }
        }

        // Escape key handler to close modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const overlay = document.getElementById('modalOverlay');
                if (overlay && !overlay.classList.contains('hidden')) {
                    closeModal();
                }
            }
        });

        const MSE_SUPPORTED = (
            typeof MediaSource !== 'undefined' &&
            MediaSource.isTypeSupported('audio/mpeg')
        );

        // Upload state
        const CHUNK_SIZE = 5 * 1024 * 1024; // 5MB
        const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
        const VALID_TYPES = ['application/pdf', 'application/epub+zip'];

        let uploadState = {
            uploadId: null,
            documentId: null,
            totalChunks: 0,
            uploadedChunks: 0,
            bytesSent: 0,
            totalBytes: 0,
            chapters: [],
            eventSource: null
        };

        // Queue state
        let queueState = {
            documents: [],
            selectedDocumentId: null
        };

        // Chapter selection state
        let selectedChapterIds = new Set();
        let expandedGroups = new Set();
        let chapterListenersSetup = false;

        function resetChapterState() {
            selectedChapterIds.clear();
            expandedGroups.clear();
            chapterListenersSetup = false;
        }

        function calculateDuration(wordCount) {
            if (!wordCount) return 0;
            return Math.ceil(wordCount / 150); // ~150 words per minute
        }

        function validateFile(file) {
            const errors = [];

            // Check file type
            if (!VALID_TYPES.includes(file.type) && !file.name.match(/\.(pdf|epub)$/i)) {
                errors.push('Chỉ hỗ trợ file PDF và EPUB');
            }

            // Check file size
            if (file.size > MAX_FILE_SIZE) {
                const mb = (file.size / (1024 * 1024)).toFixed(1);
                errors.push(`File là ${mb} MB. Kích thước tối đa là 50 MB. Vui lòng chia nhỏ file và tải từng phần.`);
            }

            // Check if file is empty
            if (file.size === 0) {
                errors.push('File trống');
            }

            return {
                valid: errors.length === 0,
                errors
            };
        }

        async function initiateUpload(file) {
            const checksum = await calculateChecksum(file);

            const formData = new FormData();
            formData.append('filename', file.name);
            formData.append('file_size', file.size.toString());
            formData.append('checksum', checksum);

            const response = await fetch('/document/upload/initiate', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Không thể bắt đầu tải lên');
            }

            return await response.json();
        }

        async function calculateChecksum(file) {
            // Calculate MD5 hash using pure JS implementation
            // Web Crypto API doesn't support MD5 in most browsers
            const arrayBuffer = await file.arrayBuffer();
            return md5ArrayBuffer(new Uint8Array(arrayBuffer));
        }

        // Complete MD5 implementation for binary data
        function md5ArrayBuffer(data) {
            function rotateLeft(value, shift) {
                return (value << shift) | (value >>> (32 - shift));
            }

            function addUnsigned(x, y) {
                const lsw = (x & 0xffff) + (y & 0xffff);
                const msw = (x >> 16) + (y >> 16) + (lsw >> 16);
                return (msw << 16) | (lsw & 0xffff);
            }

            function f(x, y, z) { return (x & y) | (~x & z); }
            function g(x, y, z) { return (x & z) | (y & ~z); }
            function h(x, y, z) { return x ^ y ^ z; }
            function i(x, y, z) { return y ^ (x | ~z); }

            function ff(a, b, c, d, x, s, ac) {
                a = addUnsigned(a, addUnsigned(addUnsigned(f(b, c, d), x), ac));
                return addUnsigned(rotateLeft(a, s), b);
            }

            function gg(a, b, c, d, x, s, ac) {
                a = addUnsigned(a, addUnsigned(addUnsigned(g(b, c, d), x), ac));
                return addUnsigned(rotateLeft(a, s), b);
            }

            function hh(a, b, c, d, x, s, ac) {
                a = addUnsigned(a, addUnsigned(addUnsigned(h(b, c, d), x), ac));
                return addUnsigned(rotateLeft(a, s), b);
            }

            function ii(a, b, c, d, x, s, ac) {
                a = addUnsigned(a, addUnsigned(addUnsigned(i(b, c, d), x), ac));
                return addUnsigned(rotateLeft(a, s), b);
            }

            function convertToWordArray(bytes) {
                const lMessageLength = bytes.length;
                const lNumberOfWordsTemp1 = lMessageLength + 8;
                const lNumberOfWordsTemp2 = (lNumberOfWordsTemp1 - (lNumberOfWordsTemp1 % 64)) / 64;
                const lNumberOfWords = (lNumberOfWordsTemp2 + 1) * 16;
                const lWordArray = new Array(lNumberOfWords - 1);
                let lBytePosition = 0;
                let lByteCount = 0;
                while (lByteCount < lMessageLength) {
                    lWordCount = (lByteCount - (lByteCount % 4)) / 4;
                    lBytePosition = (lByteCount % 4) * 8;
                    lWordArray[lWordCount] = (lWordArray[lWordCount] | (bytes[lByteCount] << lBytePosition));
                    lByteCount++;
                }
                lWordCount = (lByteCount - (lByteCount % 4)) / 4;
                lBytePosition = (lByteCount % 4) * 8;
                lWordArray[lWordCount] = lWordArray[lWordCount] | (0x80 << lBytePosition);
                lWordArray[lNumberOfWords - 2] = lMessageLength << 3;
                lWordArray[lNumberOfWords - 1] = lMessageLength >>> 29;
                return lWordArray;
            }

            function wordToHex(lValue) {
                let wordToHexValue = '', wordToHexValueTemp = '', lByte, lCount;
                for (lCount = 0; lCount <= 3; lCount++) {
                    lByte = (lValue >>> (lCount * 8)) & 255;
                    wordToHexValueTemp = '0' + lByte.toString(16);
                    wordToHexValue = wordToHexValue + wordToHexValueTemp.substr(wordToHexValueTemp.length - 2, 2);
                }
                return wordToHexValue;
            }

            const x = convertToWordArray(data);
            let a = 0x67452301, b = 0xefcdab89, c = 0x98badcfe, d = 0x10325476;

            const S11 = 7, S12 = 12, S13 = 17, S14 = 22;
            const S21 = 5, S22 = 9, S23 = 14, S24 = 20;
            const S31 = 4, S32 = 11, S33 = 16, S34 = 23;
            const S41 = 6, S42 = 10, S43 = 15, S44 = 21;

            for (let k = 0; k < x.length; k += 16) {
                const AA = a, BB = b, CC = c, DD = d;

                a = ff(a, b, c, d, x[k + 0], S11, 0xd76aa478);
                d = ff(d, a, b, c, x[k + 1], S12, 0xe8c7b756);
                c = ff(c, d, a, b, x[k + 2], S13, 0x242070db);
                b = ff(b, c, d, a, x[k + 3], S14, 0xc1bdceee);
                a = ff(a, b, c, d, x[k + 4], S11, 0xf57c0faf);
                d = ff(d, a, b, c, x[k + 5], S12, 0x4787c62a);
                c = ff(c, d, a, b, x[k + 6], S13, 0xa8304613);
                b = ff(b, c, d, a, x[k + 7], S14, 0xfd469501);
                a = ff(a, b, c, d, x[k + 8], S11, 0x698098d8);
                d = ff(d, a, b, c, x[k + 9], S12, 0x8b44f7af);
                c = ff(c, d, a, b, x[k + 10], S13, 0xffff5bb1);
                b = ff(b, c, d, a, x[k + 11], S14, 0x895cd7be);
                a = ff(a, b, c, d, x[k + 12], S11, 0x6b901122);
                d = ff(d, a, b, c, x[k + 13], S12, 0xfd987193);
                c = ff(c, d, a, b, x[k + 14], S13, 0xa679438e);
                b = ff(b, c, d, a, x[k + 15], S14, 0x49b40821);

                a = gg(a, b, c, d, x[k + 1], S21, 0xf61e2562);
                d = gg(d, a, b, c, x[k + 6], S22, 0xc040b340);
                c = gg(c, d, a, b, x[k + 11], S23, 0x265e5a51);
                b = gg(b, c, d, a, x[k + 0], S24, 0xe9b6c7aa);
                a = gg(a, b, c, d, x[k + 5], S21, 0xd62f105d);
                d = gg(d, a, b, c, x[k + 10], S22, 0x2441453);
                c = gg(c, d, a, b, x[k + 15], S23, 0xd8a1e681);
                b = gg(b, c, d, a, x[k + 4], S24, 0xe7d3fbc8);
                a = gg(a, b, c, d, x[k + 9], S21, 0x21e1cde6);
                d = gg(d, a, b, c, x[k + 14], S22, 0xc33707d6);
                c = gg(c, d, a, b, x[k + 3], S23, 0xf4d50d87);
                b = gg(b, c, d, a, x[k + 8], S24, 0x455a14ed);
                a = gg(a, b, c, d, x[k + 13], S21, 0xa9e3e905);
                d = gg(d, a, b, c, x[k + 2], S22, 0xfcefa3f8);
                c = gg(c, d, a, b, x[k + 7], S23, 0x676f02d9);
                b = gg(b, c, d, a, x[k + 12], S24, 0x8d2a4c8a);

                a = hh(a, b, c, d, x[k + 5], S31, 0xfffa3942);
                d = hh(d, a, b, c, x[k + 8], S32, 0x8771f681);
                c = hh(c, d, a, b, x[k + 11], S33, 0x6d9d6122);
                b = hh(b, c, d, a, x[k + 14], S34, 0xfde5380c);
                a = hh(a, b, c, d, x[k + 1], S31, 0xa4beea44);
                d = hh(d, a, b, c, x[k + 4], S32, 0x4bdecfa9);
                c = hh(c, d, a, b, x[k + 7], S33, 0xf6bb4b60);
                b = hh(b, c, d, a, x[k + 10], S34, 0xbebfbc70);
                a = hh(a, b, c, d, x[k + 13], S31, 0x289b7ec6);
                d = hh(d, a, b, c, x[k + 0], S32, 0xeaa127fa);
                c = hh(c, d, a, b, x[k + 3], S33, 0xd4ef3085);
                b = hh(b, c, d, a, x[k + 6], S34, 0x4881d05);
                a = hh(a, b, c, d, x[k + 9], S31, 0xd9d4d039);
                d = hh(d, a, b, c, x[k + 12], S32, 0xe6db99e5);
                c = hh(c, d, a, b, x[k + 15], S33, 0x1fa27cf8);
                b = hh(b, c, d, a, x[k + 2], S34, 0xc4ac5665);

                a = ii(a, b, c, d, x[k + 0], S41, 0xf4292244);
                d = ii(d, a, b, c, x[k + 7], S42, 0x432aff97);
                c = ii(c, d, a, b, x[k + 14], S43, 0xab9423a7);
                b = ii(b, c, d, a, x[k + 5], S44, 0xfc93a039);
                a = ii(a, b, c, d, x[k + 12], S41, 0x655b59c3);
                d = ii(d, a, b, c, x[k + 3], S42, 0x8f0ccc92);
                c = ii(c, d, a, b, x[k + 10], S43, 0xffeff47d);
                b = ii(b, c, d, a, x[k + 1], S44, 0x85845dd1);
                a = ii(a, b, c, d, x[k + 8], S41, 0x6fa87e4f);
                d = ii(d, a, b, c, x[k + 15], S42, 0xfe2ce6e0);
                c = ii(c, d, a, b, x[k + 6], S43, 0xa3014314);
                b = ii(b, c, d, a, x[k + 13], S44, 0x4e0811a1);
                a = ii(a, b, c, d, x[k + 4], S41, 0xf7537e82);
                d = ii(d, a, b, c, x[k + 11], S42, 0xbd3af235);
                c = ii(c, d, a, b, x[k + 2], S43, 0x2ad7d2bb);
                b = ii(b, c, d, a, x[k + 9], S44, 0xeb86d391);

                a = addUnsigned(a, AA);
                b = addUnsigned(b, BB);
                c = addUnsigned(c, CC);
                d = addUnsigned(d, DD);
            }

            return (wordToHex(a) + wordToHex(b) + wordToHex(c) + wordToHex(d)).toLowerCase();
        }

        // Wrapper for string input (for backward compatibility if needed)
        function md5(string) {
            const bytes = new Uint8Array(string.length);
            for (let i = 0; i < string.length; i++) {
                bytes[i] = string.charCodeAt(i);
            }
            return md5ArrayBuffer(bytes);
        }

        async function uploadChunk(file, chunkNumber, uploadId) {
            const start = chunkNumber * CHUNK_SIZE;
            const end = Math.min(start + CHUNK_SIZE, file.size);
            const chunk = file.slice(start, end);

            const formData = new FormData();
            formData.append('upload_id', uploadId);
            formData.append('chunk_number', chunkNumber.toString());
            formData.append('chunk', chunk);

            const response = await fetch('/document/upload/chunk', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`Không thể tải chunk ${chunkNumber + 1}`);
            }

            return await response.json();
        }

        async function uploadFileChunks(file, uploadId, totalChunks) {
            const MAX_RETRIES = 3;

            for (let i = 0; i < totalChunks; i++) {
                let retries = 0;
                let success = false;

                while (retries < MAX_RETRIES && !success) {
                    try {
                        const result = await uploadChunk(file, i, uploadId);

                        uploadState.uploadedChunks = result.chunks_received || i + 1;
                        uploadState.bytesSent = (i + 1) * CHUNK_SIZE;
                        if (uploadState.bytesSent > file.size) {
                            uploadState.bytesSent = file.size;
                        }

                        updateUploadProgress();
                        success = true;

                        // Check if all chunks received
                        if (result.chunks_received >= totalChunks) {
                            break;
                        }
                    } catch (error) {
                        retries++;
                        if (retries >= MAX_RETRIES) {
                            throw new Error(`Không thể tải chunk ${i + 1} sau ${MAX_RETRIES} lần thử`);
                        }
                        // Wait before retry (exponential backoff)
                        await new Promise(resolve => setTimeout(resolve, 1000 * retries));
                    }
                }
            }
        }

        function updateUploadProgress() {
            const pct = uploadState.totalChunks > 0
                ? (uploadState.uploadedChunks / uploadState.totalChunks) * 100
                : 0;

            document.getElementById('uploadProgressFill').style.width = pct + '%';
            document.getElementById('progressDetails').textContent =
                `Chunk ${uploadState.uploadedChunks}/${uploadState.totalChunks} · ${formatBytes(uploadState.bytesSent)} / ${formatBytes(uploadState.totalBytes)}`;
        }

        async function completeUpload(uploadId) {
            const formData = new FormData();
            formData.append('upload_id', uploadId);

            const response = await fetch('/document/upload/complete', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Không thể hoàn tất tải lên');
            }

            return await response.json();
        }

        // Upload UI event handlers
        const uploadZone = document.getElementById('uploadZone');
        const fileInput = document.getElementById('fileInput');
        const browseBtn = document.querySelector('.browse-btn');

        // Drag and drop handlers
        uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.classList.add('drag-over');
        });

        uploadZone.addEventListener('dragleave', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('drag-over');
        });

        uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('drag-over');

            const files = e.dataTransfer.files;
            if (files.length > 0) {
                handleFileSelection(files[0]);
            }
        });

        // File picker handlers
        browseBtn.addEventListener('click', () => fileInput.click());
        uploadZone.addEventListener('click', (e) => {
            if (e.target === uploadZone || e.target.closest('.upload-icon, .upload-text, .upload-subtext')) {
                fileInput.click();
            }
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleFileSelection(e.target.files[0]);
            }
        });

        // File selection handler
        async function handleFileSelection(file) {
            // Validate file
            const validation = validateFile(file);

            if (!validation.valid) {
                showValidationError(validation.errors);
                return;
            }

            // Reset state
            resetUploadState();

            // Show progress
            uploadZone.classList.add('hidden');
            document.getElementById('uploadProgress').classList.remove('hidden');
            document.getElementById('progressFilename').textContent = file.name;

            try {
                // Initiate upload
                const initResult = await initiateUpload(file);

                uploadState.uploadId = initResult.upload_id;
                uploadState.totalBytes = file.size;
                uploadState.totalChunks = Math.ceil(file.size / initResult.chunk_size);

                // Upload chunks
                await uploadFileChunks(file, uploadState.uploadId, uploadState.totalChunks);

                // Complete upload
                const completeResult = await completeUpload(uploadState.uploadId);
                uploadState.documentId = completeResult.document_id;

                // Reset and switch to queue tab
                resetUploadUI();
                switchTab('queue');
                await loadQueue();

            } catch (error) {
                console.error('Upload error:', error);
                showUploadError(error.message);
                resetUploadUI();
            }
        }

        function resetUploadState() {
            uploadState = {
                uploadId: null,
                documentId: null,
                totalChunks: 0,
                uploadedChunks: 0,
                bytesSent: 0,
                totalBytes: 0,
                chapters: []
            };
            resetChapterState();
        }

        function resetUploadUI() {
            uploadZone.classList.remove('hidden');
            document.getElementById('uploadProgress').classList.add('hidden');
            document.getElementById('extractionProgress').classList.add('hidden');
            document.getElementById('chapterContainer').classList.add('hidden');
            fileInput.value = '';
        }

        // Error display functions
        function showValidationError(errors) {
            showModal({
                variant: 'error',
                title: 'File Không Hợp Lệ',
                message: '• ' + errors.join('\n• '),
                actions: [
                    { label: 'OK', primary: true }
                ]
            });
        }

        function showUploadError(message) {
            showModal({
                variant: 'error',
                title: 'Tải Lên Thất Bại',
                message: message + '\n\nBạn có muốn thử lại?',
                actions: [
                    { label: 'Hủy', callback: () => resetUploadUI() },
                    { label: 'Thử lại', primary: true, callback: () => {
                        // User will need to select file again
                        resetUploadUI();
                    }}
                ]
            });
        }

        // Extraction progress streaming via SSE
        function startExtraction(documentId, filename) {
            // Close any existing event source
            if (uploadState.eventSource) {
                uploadState.eventSource.close();
                uploadState.eventSource = null;
            }

            // Switch to extraction progress display
            document.getElementById('uploadProgress').classList.add('hidden');
            document.getElementById('extractionProgress').classList.remove('hidden');

            uploadState.chapters = [];

            const eventSource = new EventSource(`/document/${documentId}/extract/stream`);

            eventSource.addEventListener('progress', (e) => {
                try {
                    const data = JSON.parse(e.data);
                    updateExtractionProgress(data);
                } catch (err) {
                    console.error('Invalid JSON in progress event:', err);
                }
            });

            eventSource.addEventListener('chapter', (e) => {
                try {
                    const data = JSON.parse(e.data);
                    uploadState.chapters.push(data.chapter);
                    updateExtractionProgress({
                        current: uploadState.chapters.length,
                        total: null,
                        message: `Đã tìm: ${data.chapter.title}`
                    });
                } catch (err) {
                    console.error('Invalid JSON in chapter event:', err);
                }
            });

            eventSource.addEventListener('complete', (e) => {
                try {
                    const data = JSON.parse(e.data);
                    eventSource.close();
                    extractionComplete(data, filename);
                } catch (err) {
                    console.error('Invalid JSON in complete event:', err);
                    eventSource.close();
                    showExtractionError('Dữ liệu không hợp lệ');
                    resetUploadUI();
                }
            });

            eventSource.addEventListener('error', (e) => {
                console.error('Extraction error:', e);
                eventSource.close();
                showExtractionError('Trích xuất thất bại. Vui lòng thử lại.');
                resetUploadUI();
            });

            uploadState.eventSource = eventSource;
        }

        function updateExtractionProgress(data) {
            const pct = data.total
                ? Math.min((data.current / data.total) * 100, 99)
                : null;

            if (pct !== null) {
                document.getElementById('extractionProgressFill').style.width = pct + '%';
            }

            document.getElementById('extractionDetails').textContent =
                data.message || `Đang xử lý ${data.current}/${data.total || '?'}`;
        }

        function extractionComplete(data, filename) {
            document.getElementById('extractionProgress').classList.add('hidden');

            if (uploadState.chapters.length === 0) {
                showNoChaptersDialog();
            } else {
                // Show chapter selection modal instead of inline tree
                showChapterModal(filename, uploadState.chapters);
            }

            // Refresh queue to update document status
            loadQueue();
        }

        function showExtractionError(message) {
            showModal({
                variant: 'error',
                title: 'Lỗi Trích Xuất',
                message: message,
                actions: [
                    { label: 'OK', primary: true, callback: () => resetUploadUI() }
                ]
            });
        }

        function showNoChaptersDialog() {
            showModal({
                variant: 'warning',
                title: 'Không Tìm Thấy Chương',
                message: 'Không thể phát hiện cấu trúc chương.\nToàn bộ tài liệu sẽ được xử lý như một chương.\n\nTiếp tục?',
                actions: [
                    { label: 'Hủy', callback: () => resetUploadUI() },
                    { label: 'Tiếp tục', primary: true, callback: () => {
                        // Create single chapter from full document
                        uploadState.chapters = [{
                            chapter_id: uploadState.documentId + '_full',
                            chapter_number: 1,
                            title: 'Tài liệu đầy đủ',
                            word_count: 0,
                            text_preview: 'Nội dung tài liệu đầy đủ'
                        }];
                        renderChapterTree('Tài liệu');
                    }}
                ]
            });
        }

        function renderChapterTree(filename) {
            const container = document.getElementById('chapterContainer');
            const tree = document.getElementById('chapterTree');

            document.getElementById('documentTitle').textContent =
                `✓ ${filename} — ${uploadState.chapters.length} chương đã tìm thấy`;

            // Clear existing tree
            tree.innerHTML = '';

            // Group chapters by hierarchy (simple flat grouping for MVP)
            const groups = groupChapters(uploadState.chapters);

            // Render tree
            groups.forEach((group, groupIndex) => {
                const groupElement = renderGroup(group, groupIndex);
                tree.appendChild(groupElement);
            });

            // Show container
            container.classList.remove('hidden');

            // Update summary
            updateSelectionSummary();

            // Set up event listeners
            setupChapterListeners();
        }

        function groupChapters(chapters) {
            // Simple grouping - all chapters in one group for MVP
            return [{
                id: 'all-chapters',
                title: 'Tất cả chương',
                wordCount: chapters.reduce((sum, ch) => sum + (ch.word_count || 0), 0),
                chapters: chapters
            }];
        }

        function renderGroup(group, groupIndex) {
            const groupDiv = document.createElement('div');
            groupDiv.className = 'chapter-group';
            groupDiv.dataset.groupId = group.id;

            const header = document.createElement('div');
            header.className = 'chapter-group-header';

            const toggle = document.createElement('span');
            toggle.className = 'chapter-toggle' + (expandedGroups.has(group.id) ? ' expanded' : '');
            toggle.textContent = '▶';

            const groupCheck = document.createElement('input');
            groupCheck.type = 'checkbox';
            groupCheck.className = 'chapter-checkbox group-checkbox';
            groupCheck.dataset.groupId = group.id;

            const title = document.createElement('span');
            title.className = 'chapter-title';
            title.textContent = group.title;

            const meta = document.createElement('span');
            meta.className = 'chapter-meta';
            meta.textContent = (group.wordCount?.toLocaleString() || 0) + ' từ';

            header.append(toggle, groupCheck, title, meta);

            const chaptersDiv = document.createElement('div');
            chaptersDiv.className = 'chapter-list';
            chaptersDiv.style.display = expandedGroups.has(group.id) ? 'block' : 'none';

            group.chapters.forEach(chapter => {
                const chapterEl = renderChapter(chapter);
                chaptersDiv.appendChild(chapterEl);
            });

            groupDiv.appendChild(header);
            groupDiv.appendChild(chaptersDiv);

            return groupDiv;
        }

        function renderChapter(chapter) {
            const div = document.createElement('div');
            div.className = 'chapter-item';
            div.dataset.chapterId = chapter.chapter_id;

            const duration = calculateDuration(chapter.word_count);
            const isSelected = selectedChapterIds.has(chapter.chapter_id);

            const check = document.createElement('input');
            check.type = 'checkbox';
            check.className = 'chapter-checkbox item-checkbox';
            check.dataset.chapterId = chapter.chapter_id;
            if (isSelected) check.checked = true;

            const title = document.createElement('span');
            title.className = 'chapter-title';
            title.textContent = chapter.title;

            const meta = document.createElement('span');
            meta.className = 'chapter-meta';
            meta.textContent = `${chapter.word_count?.toLocaleString() || 0} từ · ~${duration} phút`;

            div.append(check, title, meta);

            return div;
        }

        function setupButtonListeners() {
            // These listeners are set up once at page load
            document.getElementById('selectAllBtn').addEventListener('click', selectAllChapters);
            document.getElementById('clearSelectionBtn').addEventListener('click', clearAllChapters);
            document.getElementById('previewBtn').addEventListener('click', previewSelectedChapters);
        }

        function setupChapterListeners() {
            if (chapterListenersSetup) return;
            chapterListenersSetup = true;

            const tree = document.getElementById('chapterTree');

            // Toggle expand/collapse
            tree.querySelectorAll('.chapter-group-header').forEach(header => {
                header.addEventListener('click', (e) => {
                    if (e.target.classList.contains('chapter-checkbox')) return;

                    const groupDiv = header.parentElement;
                    const groupId = groupDiv.dataset.groupId;
                    const toggle = header.querySelector('.chapter-toggle');
                    const list = groupDiv.querySelector('.chapter-list');

                    if (expandedGroups.has(groupId)) {
                        expandedGroups.delete(groupId);
                        toggle.classList.remove('expanded');
                        list.style.display = 'none';
                    } else {
                        expandedGroups.add(groupId);
                        toggle.classList.add('expanded');
                        list.style.display = 'block';
                    }
                });
            });

            // Checkbox changes
            tree.querySelectorAll('.chapter-checkbox').forEach(checkbox => {
                checkbox.addEventListener('change', (e) => {
                    handleCheckboxChange(e.target);
                });
            });

            // Note: Button listeners are set up once in setupButtonListeners()
        }

        function handleCheckboxChange(checkbox) {
            if (checkbox.classList.contains('group-checkbox')) {
                const groupId = checkbox.dataset.groupId;
                const groupDiv = checkbox.closest('.chapter-group');
                const itemCheckboxes = groupDiv.querySelectorAll('.item-checkbox');

                itemCheckboxes.forEach(item => {
                    const chapterId = item.dataset.chapterId;
                    if (checkbox.checked) {
                        selectedChapterIds.add(chapterId);
                        item.checked = true;
                    } else {
                        selectedChapterIds.delete(chapterId);
                        item.checked = false;
                    }
                });
            } else {
                const chapterId = checkbox.dataset.chapterId;
                if (checkbox.checked) {
                    selectedChapterIds.add(chapterId);
                } else {
                    selectedChapterIds.delete(chapterId);
                }
            }

            updateSelectionSummary();
        }

        function selectAllChapters() {
            uploadState.chapters.forEach(ch => {
                selectedChapterIds.add(ch.chapter_id);
            });

            document.querySelectorAll('.item-checkbox').forEach(cb => cb.checked = true);
            updateSelectionSummary();
        }

        function clearAllChapters() {
            selectedChapterIds.clear();
            document.querySelectorAll('.item-checkbox').forEach(cb => cb.checked = false);
            document.querySelectorAll('.group-checkbox').forEach(cb => cb.checked = false);
            updateSelectionSummary();
        }

        function updateSelectionSummary() {
            const selected = uploadState.chapters.filter(ch => selectedChapterIds.has(ch.chapter_id));
            const totalWords = selected.reduce((sum, ch) => sum + (ch.word_count || 0), 0);
            const totalDuration = selected.reduce((sum, ch) => sum + calculateDuration(ch.word_count), 0);

            document.getElementById('summaryText').textContent =
                `${selected.length} chương đã chọn · ${totalWords.toLocaleString()} từ · ~${totalDuration} phút`;

            document.getElementById('previewBtn').disabled = selected.length === 0;
        }

        async function previewSelectedChapters() {
            if (selectedChapterIds.size === 0) {
                showModal({
                    variant: 'warning',
                    title: 'Chưa Chọn Chương',
                    message: 'Vui lòng chọn ít nhất một chương.',
                    actions: [
                        { label: 'OK', primary: true }
                    ]
                });
                return;
            }

            const btn = document.getElementById('previewBtn');
            btn.disabled = true;
            btn.textContent = 'Đang tải...';

            try {
                const response = await fetch(`/document/${uploadState.documentId}/content`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        chapter_ids: Array.from(selectedChapterIds)
                    })
                });

                if (!response.ok) {
                    // Get detailed error message from backend
                    let errorMsg = 'Không thể tải nội dung';
                    try {
                        const errData = await response.json();
                        errorMsg = errData.detail || errorMsg;
                    } catch (e) {
                        // If parsing error response fails, use status text
                        errorMsg = `Lỗi ${response.status}: ${response.statusText}`;
                    }
                    throw new Error(errorMsg);
                }

                const data = await response.json();
                showContentInTextarea(data);

            } catch (error) {
                console.error('Preview error:', error);
                showModal({
                    variant: 'error',
                    title: 'Không thể tải nội dung',
                    message: error.message,
                    actions: [
                        { label: 'OK', primary: true }
                    ]
                });
            } finally {
                btn.disabled = false;
                btn.textContent = 'Xem trước đã chọn → Chuyển đổi';
            }
        }

        function showContentInTextarea(data) {
            // Store document source info
            window.documentSource = {
                documentId: uploadState.documentId,
                filename: data.filename,
                chapterIds: Array.from(selectedChapterIds),
                chapterCount: data.chapter_count
            };

            // Switch to paste text tab
            switchTab('paste-text');

            // Populate textarea with null check
            if (textInput) {
                textInput.value = data.text;
            }

            // Add source indicator
            showSourceIndicator(data);
        }

        function showSourceIndicator(data) {
            // Remove existing indicator if present
            const existing = document.querySelector('.source-indicator');
            if (existing) existing.remove();

            // Create indicator
            const indicator = document.createElement('div');
            indicator.className = 'source-indicator';

            const textSpan = document.createElement('span');
            textSpan.textContent = `📋 Từ: ${data.filename} — ${data.chapter_count} chương đã chọn`;

            const changeBtn = document.createElement('button');
            changeBtn.className = 'change-selection-btn';
            changeBtn.textContent = 'Thay đổi lựa chọn';

            indicator.append(textSpan, changeBtn);

            // Insert before textarea
            if (textInput && textInput.parentNode) {
                textInput.parentNode.insertBefore(indicator, textInput);
            }

            // Add change selection handler
            changeBtn.addEventListener('click', () => {
                switchTab('upload-document');
            });
        }

        const textInput = document.getElementById('textInput');
        const voiceSel = document.getElementById('voice');
        const engineSel = document.getElementById('engine');
        const voiceGroup = document.getElementById('voiceGroup');
        const engineIndicator = document.getElementById('engineIndicator');
        const modelGroup = document.getElementById('modelGroup');
        const modelSel = document.getElementById('model');
        const speedSelect = document.getElementById('speedSelect');
        const convertBtn = document.getElementById('convertBtn');

        const statusWrap = document.getElementById('statusWrap');
        const statusLabel = document.getElementById('statusLabel');
        const progFill = document.getElementById('progFill');
        const progText = document.getElementById('progText');
        const fileSizeInfo = document.getElementById('fileSizeInfo');

        const playerWrap = document.getElementById('playerWrap');
        const player = document.getElementById('audioPlayer');
        const subtitlePanel = document.getElementById('subtitlePanel');
        const modeBadge = document.getElementById('modeBadge');

        const downloadAudioBtn = document.getElementById('downloadAudioBtn');
        const downloadSrtBtn = document.getElementById('downloadSrtBtn');
        const downloadVttBtn = document.getElementById('downloadVttBtn');
        const clearCacheBtn = document.getElementById('clearCacheBtn');

        function setSampleText(lang) {
            textInput.value = SAMPLE_TEXT[lang] || '';
        }

        function formatBytes(bytes) {
            if (!bytes || bytes < 0) return '0 KB';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1024 / 1024).toFixed(2) + ' MB';
        }

        function getRate() {
            return parseFloat(speedSelect.value || '1');
        }

        function applyPlaybackRate() {
            player.playbackRate = getRate();
        }

        async function loadVoiceRegistry() {
            const res = await fetch('/tts/voices', { cache: 'no-store' });
            if (!res.ok) throw new Error('Không tải được danh sách voice');
            const data = await res.json();
            voiceRegistry = data.voices || {};
        }

        function updateLanguageButtons() {
            const selectedEngine = engineSel.value;
            if (selectedEngine === 'vieneu' && currentLang !== 'vi') {
                currentLang = 'vi';
            }

            document.querySelectorAll('.lang-btn').forEach(btn => {
                const shouldHide = selectedEngine === 'vieneu' && btn.dataset.lang !== 'vi';
                btn.style.display = shouldHide ? 'none' : '';
                btn.disabled = shouldHide;
                btn.classList.toggle('active', btn.dataset.lang === currentLang);
            });
        }

        function updateVoiceDropdown() {
            const selectedEngine = engineSel.value;
            if (selectedEngine === 'vieneu' && currentLang !== 'vi') {
                currentLang = 'vi';
            }

            const allVoices = voiceRegistry[currentLang] || [];

            // Filter voices by selected engine
            const voices = allVoices.filter(v => v.engine === selectedEngine);

            voiceSel.innerHTML = '';

            voices.forEach(v => {
                const opt = document.createElement('option');
                opt.value = v.value;
                opt.textContent = v.label;
                voiceSel.appendChild(opt);
            });

            updateEngineIndicator(selectedEngine);
            updateVoiceVisibility();
            updateLanguageButtons();
        }

        function updateVoiceVisibility() {
            // Hide voice dropdown for gTTS (uses language instead)
            const selectedEngine = engineSel.value;
            voiceGroup.style.display = selectedEngine === 'gtts' ? 'none' : '';

            // Show audio quality selector only for VieNeu
            const audioQualityGroup = document.getElementById('audioQualityGroup');
            if (audioQualityGroup) {
                audioQualityGroup.style.display = selectedEngine === 'vieneu' ? '' : 'none';
            }
            modelGroup.style.display = selectedEngine === 'vieneu' ? '' : 'none';
        }

        function updateEngineIndicator(engine) {
            const engineNames = {
                'edge': 'Engine: Edge-TTS (có subtitle live)',
                'vieneu': 'Engine: VieNeu-TTS (chất lượng cao, không subtitle)',
                'gtts': 'Engine: Google TTS (không subtitle)'
            };
            engineIndicator.textContent = engineNames[engine] || engine;
        }

        function setStatus(text) {
            statusLabel.textContent = text;
        }

        function formatDuration(seconds) {
            if (!Number.isFinite(Number(seconds))) return '';
            const value = Math.max(0, Math.ceil(Number(seconds)));
            if (value < 60) return `${value} giây`;
            const minutes = Math.floor(value / 60);
            const remainder = value % 60;
            if (minutes < 60) return `${minutes} phút${remainder ? ` ${remainder} giây` : ''}`;
            const hours = Math.floor(minutes / 60);
            return `${hours} giờ ${minutes % 60} phút`;
        }

        function updateProgress(current, total, remainingSeconds = null) {
            const pct = total > 0 ? (current / total) * 100 : 0;
            progFill.style.width = pct + '%';
            const estimate = remainingSeconds > 0
                ? ` · còn khoảng ${formatDuration(remainingSeconds)}`
                : '';
            progText.textContent = `${current || 0} / ${total || 0} đoạn${estimate}`;
        }

        function setBadge(type) {
            modeBadge.className = 'badge ' + type;
            modeBadge.textContent = {
                live: '● LIVE',
                done: '✓ Xong',
                cached: '★ Cache',
                wait: '… Chờ',
                error: '⚠ Lỗi',
            }[type] || type;
        }

        function renderFileSize() {
            const finalSize = (lastStatus && lastStatus.file_size) ? lastStatus.file_size : 0;

            if (lastStatus && lastStatus.status === 'completed' && finalSize > 0) {
                fileSizeInfo.textContent = 'Kích thước file: ' + formatBytes(finalSize);
                return;
            }

            if (bytesReceived > 0 && finalSize > 0 && bytesReceived < finalSize) {
                fileSizeInfo.textContent = 'Đã nhận: ' + formatBytes(bytesReceived) + ' / ' + formatBytes(finalSize);
                return;
            }

            if (bytesReceived > 0) {
                fileSizeInfo.textContent = 'Đã nhận: ' + formatBytes(bytesReceived);
                return;
            }

            fileSizeInfo.textContent = '';
        }

        function cleanupMSE() {
            if (streamReader) {
                try { streamReader.cancel(); } catch (_) {}
                streamReader = null;
            }
            if (mediaSource && mediaSource.readyState === 'open') {
                try { mediaSource.endOfStream(); } catch (_) {}
            }
            if (player.src && player.src.startsWith('blob:')) {
                try { URL.revokeObjectURL(player.src); } catch (_) {}
            }
            mediaSource = null;
            sourceBuffer = null;
        }

        function cleanupSubtitleStream() {
            if (subtitleEventSource) {
                try { subtitleEventSource.close(); } catch (_) {}
                subtitleEventSource = null;
            }
            subtitleStreamDone = false;
            subtitleCues = [];
            subtitleCursor = 0;

            if (subtitleRaf) {
                cancelAnimationFrame(subtitleRaf);
                subtitleRaf = null;
            }

            subtitlePanel.classList.add('empty');
            subtitlePanel.textContent = 'Subtitle live sẽ hiện ở đây khi dùng Edge-TTS.';
            subtitlePanel.style.display = 'none';
        }

        function resetUI() {
            cleanupMSE();
            cleanupSubtitleStream();

            if (pollingInterval) {
                clearInterval(pollingInterval);
                pollingInterval = null;
            }

            currentCacheId = null;
            currentMode = null;
            bytesReceived = 0;
            streamFinished = false;
            lastStatus = null;
            subtitleSupported = false;

            player.pause();
            player.removeAttribute('src');
            player.load();

            statusWrap.style.display = 'block';
            playerWrap.style.display = 'none';
            updateProgress(0, 0);
            setStatus('Đang gửi yêu cầu...');
            renderFileSize();
            setBadge('wait');

            downloadAudioBtn.style.display = 'none';
            downloadSrtBtn.style.display = 'none';
            downloadVttBtn.style.display = 'none';
            clearCacheBtn.style.display = 'none';

            downloadAudioBtn.href = '';
            downloadSrtBtn.href = '';
            downloadVttBtn.href = '';
        }

        function waitSourceBufferIdle(sb) {
            if (!sb || !sb.updating) return Promise.resolve();
            return new Promise(resolve => {
                sb.addEventListener('updateend', resolve, { once: true });
            });
        }

        async function appendChunkToSourceBuffer(sb, chunk) {
            await waitSourceBufferIdle(sb);
            return new Promise((resolve, reject) => {
                const onUpdateEnd = () => { cleanup(); resolve(); };
                const onError = (e) => { cleanup(); reject(e); };
                const cleanup = () => {
                    sb.removeEventListener('updateend', onUpdateEnd);
                    sb.removeEventListener('error', onError);
                };
                sb.addEventListener('updateend', onUpdateEnd, { once: true });
                sb.addEventListener('error', onError, { once: true });
                try {
                    sb.appendBuffer(chunk);
                } catch (e) {
                    cleanup();
                    reject(e);
                }
            });
        }

        function showDownloadButtons(cacheId, showSubtitle) {
            downloadAudioBtn.href = '/tts/file/' + cacheId;
            downloadAudioBtn.download = 'audio_' + cacheId.slice(0, 8) + '.mp3';
            downloadAudioBtn.style.display = 'inline-flex';

            if (showSubtitle) {
                downloadSrtBtn.href = '/tts/subtitle/srt/' + cacheId;
                downloadVttBtn.href = '/tts/subtitle/vtt/' + cacheId;
                downloadSrtBtn.download = 'subtitle_' + cacheId.slice(0, 8) + '.srt';
                downloadVttBtn.download = 'subtitle_' + cacheId.slice(0, 8) + '.vtt';
                downloadSrtBtn.style.display = 'inline-flex';
                downloadVttBtn.style.display = 'inline-flex';
            }

            // Show per-audio delete button
            clearCacheBtn.style.display = 'inline-flex';
        }

        function startSubtitleSyncLoop() {
            if (!subtitleSupported) return;
            subtitlePanel.style.display = 'flex';

            const step = () => {
                if (!subtitleSupported) return;

                const t = player.currentTime || 0;

                while (
                    subtitleCursor > 0 &&
                    subtitleCues[subtitleCursor - 1] &&
                    t < subtitleCues[subtitleCursor - 1].start
                ) {
                    subtitleCursor -= 1;
                }

                while (
                    subtitleCues[subtitleCursor] &&
                    t > subtitleCues[subtitleCursor].end
                ) {
                    subtitleCursor += 1;
                }

                const cue = subtitleCues[subtitleCursor];

                if (cue && t >= cue.start && t <= cue.end) {
                    subtitlePanel.classList.remove('empty');
                    subtitlePanel.textContent = cue.text;
                } else {
                    subtitlePanel.classList.add('empty');
                    if (subtitleStreamDone || subtitleCues.length > 0) {
                        subtitlePanel.textContent = '...';
                    } else {
                        subtitlePanel.textContent = 'Đang chờ subtitle live từ Edge-TTS...';
                    }
                }

                subtitleRaf = requestAnimationFrame(step);
            };

            if (subtitleRaf) cancelAnimationFrame(subtitleRaf);
            subtitleRaf = requestAnimationFrame(step);
        }

        function startSubtitleEventStream(cacheId) {
            cleanupSubtitleStream();

            subtitleSupported = true;
            subtitlePanel.style.display = 'flex';
            subtitlePanel.classList.add('empty');
            subtitlePanel.textContent = 'Đang chờ subtitle live từ Edge-TTS...';

            subtitleEventSource = new EventSource('/tts/cues/stream/' + cacheId);

            subtitleEventSource.addEventListener('cue', (ev) => {
                try {
                    const cue = JSON.parse(ev.data);
                    subtitleCues.push(cue);
                    subtitleCues.sort((a, b) => a.start - b.start);
                } catch (err) {
                    console.error('Subtitle cue parse error:', err);
                }
            });

            subtitleEventSource.addEventListener('complete', () => {
                subtitleStreamDone = true;
                if (subtitleEventSource) {
                    subtitleEventSource.close();
                    subtitleEventSource = null;
                }
            });

            subtitleEventSource.addEventListener('error', (ev) => {
                console.warn('Subtitle SSE error', ev);
            });

            startSubtitleSyncLoop();
        }

        async function sleep(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }

        async function playDirect(cacheId) {
            playerWrap.style.display = 'block';
            const url = '/tts/file/' + cacheId + '?t=' + Date.now();
            player.src = url;

            player.addEventListener('canplay', function onCanPlay() {
                player.removeEventListener('canplay', onCanPlay);
                applyPlaybackRate();
                player.play().catch(() => {});
            }, { once: true });
        }

        async function startMSEPlayback(cacheId) {
            playerWrap.style.display = 'block';
            bytesReceived = 0;
            streamFinished = false;

            mediaSource = new MediaSource();
            player.src = URL.createObjectURL(mediaSource);

            mediaSource.addEventListener('sourceopen', async () => {
                try {
                    if (!mediaSource || mediaSource.readyState !== 'open') return;
                    sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');
                    sourceBuffer.mode = 'sequence';
                    await pumpLiveStream(cacheId);
                } catch (e) {
                    console.error('[MSE] sourceopen error:', e);
                    setStatus('Không stream live được. Chuyển sang chế độ chờ hoàn tất...');
                    setBadge('wait');
                    cleanupMSE();
                    currentMode = 'fallback';
                    startPolling(cacheId, { allowDirectPlayWhenCompleted: true });
                }
            }, { once: true });
        }

        async function pumpLiveStream(cacheId) {
            let response = null;

            for (let attempt = 0; attempt < 40; attempt++) {
                try {
                    const res = await fetch('/tts/stream/' + cacheId, { cache: 'no-store' });
                    if (res.ok) {
                        response = res;
                        break;
                    }

                    if (res.status === 503) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.detail || 'Generation failed');
                    }
                } catch (e) {
                    if (attempt === 39) throw e;
                }

                await sleep(300);
            }

            if (!response || !response.body) {
                throw new Error('Không mở được luồng audio live');
            }

            streamReader = response.body.getReader();
            let firstChunk = true;

            while (true) {
                const { done, value } = await streamReader.read();
                if (done) break;
                if (!value || value.byteLength === 0) continue;

                bytesReceived += value.byteLength;
                renderFileSize();

                if (!sourceBuffer || !mediaSource || mediaSource.readyState !== 'open') break;
                await appendChunkToSourceBuffer(sourceBuffer, value);

                if (firstChunk) {
                    firstChunk = false;
                    playerWrap.style.display = 'block';
                    applyPlaybackRate();
                    player.play().catch(() => {});
                }
            }

            if (bytesReceived === 0) {
                throw new Error('Luồng audio đóng trước khi nhận dữ liệu');
            }

            streamFinished = true;

            if (sourceBuffer) await waitSourceBufferIdle(sourceBuffer);
            if (mediaSource && mediaSource.readyState === 'open') {
                try { mediaSource.endOfStream(); } catch (_) {}
            }

            if (lastStatus && lastStatus.status === 'completed') {
                setBadge('done');
                showDownloadButtons(cacheId, subtitleSupported);
            }

            convertBtn.disabled = false;
        }

        function startPolling(cacheId, options = {}) {
            const { allowDirectPlayWhenCompleted = false } = options;

            if (pollingInterval) clearInterval(pollingInterval);

            pollingInterval = setInterval(async () => {
                try {
                    const res = await fetch('/tts/status/' + cacheId, { cache: 'no-store' });
                    if (!res.ok) throw new Error('Không lấy được trạng thái');

                    const data = await res.json();
                    lastStatus = data;

                    updateProgress(data.progress || 0, data.total || 0, data.remaining_seconds);
                    renderFileSize();

                    if (data.status === 'queued') {
                        setStatus('Đang xếp hàng xử lý...');
                        return;
                    }

                    if (data.status === 'processing' || data.status === 'generating') {
                        if (currentMode === 'mse' && subtitleSupported) {
                            setStatus(`Đang tạo audio + subtitle live... (${data.progress || 0}/${data.total || 0})`);
                            setBadge('live');
                        } else {
                            setStatus(`Đang tạo audio... (${data.progress || 0}/${data.total || 0})`);
                            setBadge('wait');
                        }
                        return;
                    }

                    if (data.status === 'failed') {
                        setStatus('Lỗi: ' + (data.error || 'unknown'));
                        setBadge('error');

                        clearInterval(pollingInterval);
                        pollingInterval = null;
                        convertBtn.disabled = false;
                        return;
                    }

                    if (data.status === 'completed') {
                        updateProgress(data.total || 1, data.total || 1);
                        setStatus('Hoàn tất.');

                        if (currentMode === 'mse') {
                            if (streamFinished) {
                                setBadge('done');
                                showDownloadButtons(cacheId, subtitleSupported);

                                clearInterval(pollingInterval);
                                pollingInterval = null;
                                convertBtn.disabled = false;
                            } else {
                                setBadge('live');
                            }
                        } else {
                            setBadge(currentMode === 'cached' ? 'cached' : 'done');
                            showDownloadButtons(cacheId, subtitleSupported);

                            clearInterval(pollingInterval);
                            pollingInterval = null;
                            if (allowDirectPlayWhenCompleted) {
                                playDirect(cacheId);
                            }
                            convertBtn.disabled = false;
                        }
                    }
                } catch (e) {
                    console.error('Polling error:', e);
                }
            }, 1000);
        }

        // Engine is now auto-detected from voice selection
        speedSelect.addEventListener('change', applyPlaybackRate);
        player.addEventListener('play', applyPlaybackRate);
        player.addEventListener('canplay', applyPlaybackRate);

        convertBtn.addEventListener('click', async () => {
            // Clear document source indicator when starting new conversion
            const sourceIndicator = document.querySelector('.source-indicator');
            if (sourceIndicator && !textInput.value.trim()) {
                sourceIndicator.remove();
                window.documentSource = null;
            }

            const text = textInput.value.trim();
            const voice = voiceSel.value;
            const engine = engineSel.value;

            if (!text) {
                showModal({
                    variant: 'warning',
                    title: 'Chưa Nhập Nội Dung',
                    message: 'Vui lòng nhập nội dung văn bản trước khi chuyển đổi.',
                    actions: [
                        { label: 'OK', primary: true }
                    ]
                });
                return;
            }

            resetUI();
            convertBtn.disabled = true;
            subtitleSupported = engine === 'edge';

            const body = { text, engine, language: currentLang };
            // Only send voice parameter for Edge and VieNeu engines
            if (engine !== 'gtts') {
                body.voice = voice;
            }
            // Add audio quality for VieNeu engine
            if (engine === 'vieneu') {
                body.audio_quality = document.getElementById('audioQuality').value;
                body.model = modelSel.value;
            }

            let data;
            try {
                const res = await fetch('/tts/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || 'Start TTS failed');
                }
            } catch (e) {
                setStatus('Lỗi kết nối: ' + (e.message || e));
                setBadge('error');
                convertBtn.disabled = false;
                return;
            }

            currentCacheId = data.cache_id;
            subtitleSupported = !!data.subtitle_supported;
            playerWrap.style.display = 'block';
            updateProgress(0, data.estimated_chunks || 0, data.estimated_seconds);

            if (engine === 'edge') {
                startSubtitleEventStream(currentCacheId);
            }

            if (data.status === 'completed') {
                currentMode = 'cached';
                lastStatus = { status: 'completed', file_size: 0, total: 1, progress: 1 };
                updateProgress(1, 1);
                setStatus('Đã tìm thấy trong cache.');
                setBadge('cached');

                await playDirect(currentCacheId);
                showDownloadButtons(currentCacheId, subtitleSupported);

                convertBtn.disabled = false;
                startPolling(currentCacheId, { allowDirectPlayWhenCompleted: false });
                return;
            }

            if (MSE_SUPPORTED) {
                currentMode = 'mse';

                if (engine === 'edge') {
                    setBadge('live');
                    setStatus('Đang tạo audio + subtitle live...');
                } else {
                    setBadge('live');
                    setStatus('Đang tạo audio live...');
                }

                startPolling(currentCacheId, { allowDirectPlayWhenCompleted: false });
                startMSEPlayback(currentCacheId);
            } else {
                currentMode = 'fallback';
                setBadge('wait');
                setStatus('Trình duyệt không hỗ trợ live stream MP3, sẽ phát khi xong file...');
                startPolling(currentCacheId, { allowDirectPlayWhenCompleted: true });
            }

        });

        // Delete only the current audio cache entry. The server cache is shared
        // across users, so normal UI must never call the global /tts/cache route.
        clearCacheBtn.addEventListener('click', async () => {
            if (!currentCacheId) return;

            if (!confirm('Bạn có chắc muốn xóa audio hiện tại? Hành động này không thể hoàn tác.')) {
                return;
            }

            clearCacheBtn.disabled = true;
            clearCacheBtn.textContent = 'Đang xóa...';

            try {
                const res = await fetch(`/tts/file/${currentCacheId}`, { method: 'DELETE' });
                if (!res.ok) throw new Error('Không thể xóa audio');

                const data = await res.json();
                showToast({
                    variant: 'success',
                    title: 'Đã xóa audio',
                    message: data.message || 'Audio hiện tại đã được xóa.',
                    duration: 3000
                });

                // Reset player state
                player.pause();
                player.removeAttribute('src');
                player.load();
                subtitlePanel.innerHTML = 'Subtitle live sẽ hiện ở đây khi dùng Edge-TTS.';
                subtitlePanel.classList.add('empty');
                playerWrap.style.display = 'none';
                statusWrap.style.display = 'block';
                updateProgress(0, 0);
                setStatus('Đã xóa audio hiện tại. Nhập văn bản để tạo audio mới.');
                setBadge('wait');

                // Hide download buttons
                downloadAudioBtn.style.display = 'none';
                downloadSrtBtn.style.display = 'none';
                downloadVttBtn.style.display = 'none';
                clearCacheBtn.style.display = 'none';

                currentCacheId = null;
                window.currentCacheId = null;
                clearSession();
            } catch (err) {
                console.error('[Clear Cache] Error:', err);
                showToast({
                    variant: 'error',
                    title: 'Lỗi',
                    message: 'Không thể xóa audio hiện tại. Vui lòng thử lại.',
                    duration: 5000
                });
            } finally {
                clearCacheBtn.disabled = false;
                clearCacheBtn.textContent = 'Xóa audio';
            }
        });

        document.querySelectorAll('.lang-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                if (btn.disabled) return;
                currentLang = btn.dataset.lang;
                updateLanguageButtons();
                updateVoiceDropdown();
                setSampleText(currentLang);
            });
        });

        engineSel.addEventListener('change', () => {
            if (engineSel.value === 'vieneu') {
                currentLang = 'vi';
            }
            updateLanguageButtons();
            updateVoiceDropdown();
        });

        (async () => {
            try {
                await loadVoiceRegistry();
                updateVoiceDropdown();
                updateLanguageButtons();
                setSampleText('vi');
                updateVoiceVisibility();
                setupButtonListeners();
            } catch (err) {
                console.error('[TTS] init failed:', err);
                statusWrap.style.display = 'block';
                setStatus('Khởi tạo thất bại. Vui lòng tải lại trang.');
                setBadge('error');
            }
        })();

        // ---------------------------------------------------------------------------
        // Session Management
        // ---------------------------------------------------------------------------
        const SESSION_STORAGE_KEY = 'ebook2audio_session';

        function clearSession() {
            localStorage.removeItem(SESSION_STORAGE_KEY);
        }

        window.addEventListener('DOMContentLoaded', () => {
            clearSession();
        });

        // ---------------------------------------------------------------------------
        // Generation Action Handlers
        // ---------------------------------------------------------------------------
        const btnStop = document.getElementById('btnStop');
        const btnDelete = document.getElementById('btnDelete');
        const generationActions = document.getElementById('generationActions');

        async function stopGeneration() {
            if (!window.currentCacheId) return;

            try {
                const res = await fetch(`/tts/file/${window.currentCacheId}`, { method: 'DELETE' });
                if (res.ok) {
                    // Transform stop button to continue button
                    if (btnStop) {
                        btnStop.textContent = '▶ Tiếp Tục';
                        btnStop.title = 'Tiếp tục tạo audio';
                        btnStop.classList.remove('btn-stop');
                        btnStop.classList.add('btn-continue');
                        btnStop.disabled = false;
                    }
                    if (btnDelete) btnDelete.disabled = false;
                }
            } catch (err) {
                console.error('Stop failed:', err);
            }
        }

        async function resumeGeneration() {
            if (!window.currentCacheId) return;

            // Navigate to the file page to continue/resume
            window.location.href = `/tts/file/${window.currentCacheId}`;
        }

        async function deleteGeneration() {
            if (!window.currentCacheId) return;

            if (!confirm('Bạn có chắc muốn xóa audio này?')) return;

            try {
                const res = await fetch(`/tts/file/${window.currentCacheId}`, { method: 'DELETE' });
                if (res.ok) {
                    clearSession();
                    window.location.reload();
                }
            } catch (err) {
                console.error('Delete failed:', err);
            }
        }

        if (btnStop) {
            btnStop.addEventListener('click', () => {
                if (btnStop.classList.contains('btn-continue')) {
                    resumeGeneration();
                } else {
                    stopGeneration();
                }
            });
        }
        if (btnDelete) btnDelete.addEventListener('click', deleteGeneration);

        // Show action buttons when generation starts
        function showGenerationActions(cacheId) {
            window.currentCacheId = cacheId;
            if (generationActions) {
                generationActions.style.display = 'flex';
                // Reset stop button to initial state
                if (btnStop) {
                    btnStop.textContent = '⏸ Dừng';
                    btnStop.title = 'Dừng tạo (lưu audio một phần)';
                    btnStop.classList.remove('btn-continue');
                    btnStop.classList.add('btn-stop');
                    btnStop.disabled = false;
                }
                if (btnDelete) btnDelete.disabled = true;
            }
        }

        // Hide action buttons when generation completes
        function hideGenerationActions() {
            if (generationActions) {
                generationActions.style.display = 'none';
            }
        }
