let currentRecord = null;
        let originalRecord = null;
        let currentReceipt = null;
        let contractAddress = null;
        let timerInterval = null;
        let startTime = null;
        let selectedMaxPages = 10;
        let spiderProgressInterval = null;

        function switchView(viewName) {
            document.getElementById('viewLanding').style.display = viewName === 'landing' ? 'block' : 'none';
            document.getElementById('viewWorkspace').style.display = viewName === 'workspace' ? 'block' : 'none';
            document.getElementById('viewOsint').style.display = viewName === 'osint' ? 'block' : 'none';
            const viewVisual = document.getElementById('viewVisual');
            if (viewVisual) viewVisual.style.display = viewName === 'visual' ? 'block' : 'none';

            document.getElementById('tabLanding').classList.toggle('active', viewName === 'landing');
            document.getElementById('tabWorkspace').classList.toggle('active', viewName === 'workspace');
            document.getElementById('tabOsint').classList.toggle('active', viewName === 'osint');
            const tabVisual = document.getElementById('tabVisual');
            if (tabVisual) tabVisual.classList.toggle('active', viewName === 'visual');

            // Hide 'Launch Console' button when already in the biometric console/workspace
            const launchBtn = document.getElementById('navLaunchConsoleBtn');
            if (launchBtn) {
                launchBtn.style.display = viewName === 'workspace' ? 'none' : 'inline-flex';
            }

            window.scrollTo({ top: 0, behavior: 'smooth' });

            if (viewName === 'osint') {
                loadWatchlistUI();
            }
        }

        window.addEventListener('DOMContentLoaded', async () => {
            if (window.location.hash) {
                let tab = window.location.hash.replace('#', '');
                if (tab === 'radar') tab = 'osint';
                if (['landing', 'workspace', 'osint', 'visual'].includes(tab)) {
                    switchView(tab);
                }
            }
            await fetchStatus();
            setupDropzone();
            loadPresetsData();
            initSplitLens();
            if (window.location.hash) {
                const tab = window.location.hash.replace('#', '');
                if (['landing', 'workspace', 'radar', 'visual'].includes(tab)) {
                    switchView(tab);
                }
            }
        });

        // ========================================================
        // INTERACTIVE SPLIT-LENS KINETIC SLIDER CONTROLLER
        // ========================================================
        let lensAutoScanActive = false;
        let lensAutoScanReqId = null;
        let lensAutoScanTime = 0;

        function setLensPosition(pctVal) {
            const viewports = document.querySelectorAll('.lens-viewport');
            const pct = Math.max(2, Math.min(98, parseFloat(pctVal)));
            viewports.forEach(vp => {
                vp.style.setProperty('--lens-pos', `${pct.toFixed(1)}%`);
            });

            // Update live telemetry HUDs across all views
            document.querySelectorAll('.lens-telemetry-optical').forEach(el => el.innerText = `${pct.toFixed(1)}%`);
            document.querySelectorAll('.lens-telemetry-machine').forEach(el => el.innerText = `${(100 - pct).toFixed(1)}%`);
            document.querySelectorAll('.lens-telemetry-seam').forEach(el => el.innerText = `X=${pct.toFixed(1)}%`);

            const opticalEl = document.getElementById('lensTelemetryOptical');
            const machineEl = document.getElementById('lensTelemetryMachine');
            const seamEl = document.getElementById('lensTelemetrySeam');
            if (opticalEl) opticalEl.innerText = `${pct.toFixed(1)}%`;
            if (machineEl) machineEl.innerText = `${(100 - pct).toFixed(1)}%`;
            if (seamEl) seamEl.innerText = `X=${pct.toFixed(1)}%`;
        }

        function resetLensCenter() {
            if (lensAutoScanActive) {
                toggleLensAutoScan();
            }
            setLensPosition(50.0);
        }

        function toggleLensAutoScan() {
            lensAutoScanActive = !lensAutoScanActive;
            document.querySelectorAll('.btn-auto-scan').forEach(btn => {
                btn.classList.toggle('active', lensAutoScanActive);
                const icon = btn.querySelector('.auto-scan-icon') || btn.querySelector('span:first-child');
                if (icon) icon.innerText = lensAutoScanActive ? '❚❚' : '▶';
                const label = btn.querySelector('.auto-scan-label') || btn.querySelector('span:last-child');
                if (label) label.innerText = lensAutoScanActive ? 'Sweeping... [❚❚ Pause]' : 'Kinetic Auto-Scan';
            });
            if (lensAutoScanActive) {
                runAutoScanLoop();
            } else if (lensAutoScanReqId) {
                cancelAnimationFrame(lensAutoScanReqId);
                lensAutoScanReqId = null;
            }
        }

        function runAutoScanLoop() {
            if (!lensAutoScanActive) return;
            lensAutoScanTime += 0.016;
            // Smooth sinusoidal motion sweeping between 22% and 78%
            const pct = 50 + Math.sin(lensAutoScanTime) * 28;
            setLensPosition(pct);
            lensAutoScanReqId = requestAnimationFrame(runAutoScanLoop);
        }

        function initSplitLens() {
            const viewports = document.querySelectorAll('.lens-viewport');
            if (!viewports.length) return;

            viewports.forEach(viewport => {
                let isDragging = false;

                function handlePointerMove(clientX) {
                    const rect = viewport.getBoundingClientRect();
                    let x = clientX - rect.left;
                    x = Math.max(0, Math.min(x, rect.width));
                    const pct = (x / rect.width) * 100;
                    setLensPosition(pct);
                }

                viewport.addEventListener('mousedown', (e) => {
                    if (lensAutoScanActive) toggleLensAutoScan();
                    isDragging = true;
                    handlePointerMove(e.clientX);
                });

                window.addEventListener('mousemove', (e) => {
                    if (!isDragging) return;
                    handlePointerMove(e.clientX);
                });

                window.addEventListener('mouseup', () => {
                    isDragging = false;
                });

                viewport.addEventListener('touchstart', (e) => {
                    if (lensAutoScanActive) toggleLensAutoScan();
                    isDragging = true;
                    if (e.touches && e.touches[0]) handlePointerMove(e.touches[0].clientX);
                }, { passive: true });

                window.addEventListener('touchmove', (e) => {
                    if (!isDragging) return;
                    if (e.touches && e.touches[0]) handlePointerMove(e.touches[0].clientX);
                }, { passive: true });

                window.addEventListener('touchend', () => {
                    isDragging = false;
                });
            });

            // Set initial position
            setLensPosition(50.0);
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                contractAddress = data.contract_address;
                logConsole('BLOCKCHAIN', `Connected to ${data.network}. Registry: ${contractAddress || 'ready'}`);
            } catch (err) {
                logConsole('BLOCKCHAIN', 'Operating in standalone local mode', 'amber');
            }
        }

        function logConsole(tag, message, color = '') {
            const stream = document.getElementById('consoleStream');
            if (!stream) return;
            const now = new Date();
            const timeStr = `[${now.toTimeString().split(' ')[0]}]`;
            
            const line = document.createElement('div');
            line.className = 'console-line';
            line.innerHTML = `
                <span class="console-timestamp">${timeStr}</span>
                <span class="console-tag ${color}">${tag}</span>
                <span>${message}</span>
            `;
            stream.appendChild(line);
            stream.scrollTop = stream.scrollHeight;
        }

        // ========================================================
        // MULTI-URL DYNAMIC ROWS
        // ========================================================
        function addUrlRow(value = "") {
            const container = document.getElementById('urlInputContainer');
            const row = document.createElement('div');
            row.className = 'url-input-row';
            row.innerHTML = `
                <input type="url" class="input-text target-url-item" value="${value}" placeholder="https://domain.com/profile">
                <button type="button" class="btn-icon-del" onclick="this.parentElement.remove()" title="Remove URL">✕</button>
            `;
            container.appendChild(row);
        }

        function getTargetUrls() {
            const inputs = document.querySelectorAll('.target-url-item');
            const urls = [];
            inputs.forEach(inp => {
                if (inp.value && inp.value.trim().startsWith("http")) {
                    urls.push(inp.value.trim());
                }
            });
            return urls;
        }

        // ========================================================
        // CRAWL DEPTH CONFIGURATION MODAL
        // ========================================================
        function openDepthModal() {
            document.getElementById('depthModal').classList.add('open');
        }

        function closeDepthModal(event) {
            if (event && event.target && event.target.id !== 'depthModal' && !event.target.classList.contains('btn-primary')) {
                return;
            }
            document.getElementById('depthModal').classList.remove('open');
        }

        function selectCrawlDepth(pages) {
            selectedMaxPages = parseInt(pages, 10);
            [3, 10, 20, 30].forEach(p => {
                const el = document.getElementById(`depthOption${p}`);
                if (el) el.classList.toggle('active', p === selectedMaxPages);
            });
            document.getElementById('customDepthInput').value = selectedMaxPages;
            document.getElementById('currentDepthLabel').innerText = `${selectedMaxPages} Pages`;
            const pill = document.getElementById('pillDepthVal');
            if (pill) pill.innerText = `${selectedMaxPages} Pages`;
            document.getElementById('modalDepthConfirmText').innerText = `${selectedMaxPages} Pages`;
            document.getElementById('hudPagesCrawledLabel').innerText = `Pages Spidered (Max ${selectedMaxPages})`;
        }

        function applyCustomDepth() {
            const val = parseInt(document.getElementById('customDepthInput').value, 10);
            if (val && val >= 1 && val <= 50) {
                selectedMaxPages = val;
                [3, 10, 20, 30].forEach(p => {
                    const el = document.getElementById(`depthOption${p}`);
                    if (el) el.classList.toggle('active', p === selectedMaxPages);
                });
                document.getElementById('currentDepthLabel').innerText = `${selectedMaxPages} Pages`;
                const pill = document.getElementById('pillDepthVal');
                if (pill) pill.innerText = `${selectedMaxPages} Pages`;
                document.getElementById('modalDepthConfirmText').innerText = `${selectedMaxPages} Pages`;
                document.getElementById('hudPagesCrawledLabel').innerText = `Pages Spidered (Max ${selectedMaxPages})`;
                closeDepthModal();
            } else {
                alert("Please enter a page budget between 1 and 50.");
            }
        }

        // ========================================================
        // PRESET POP-UP MODAL & AUTO QUERY IMAGE STAGING
        // ========================================================
        let presetsCache = [];

        async function loadPresetsData() {
            try {
                const res = await fetch('/api/presets');
                presetsCache = await res.json();
                renderPresetModalCards();
            } catch (e) {
                console.error("Failed to load presets", e);
            }
        }

        function openPresetModal() {
            document.getElementById('presetModal').classList.add('open');
        }

        function closePresetModal() {
            document.getElementById('presetModal').classList.remove('open');
        }

        function renderPresetModalCards() {
            const container = document.getElementById('presetCardsContainer');
            container.innerHTML = '';
            
            presetsCache.forEach(p => {
                const card = document.createElement('div');
                card.className = 'preset-item-card';
                card.onclick = () => selectPreset(p);
                card.innerHTML = `
                    <div style="display: flex; align-items: center;">
                        <img src="${p.image_url}?v=${Date.now()}" class="preset-item-thumb" alt="${p.name}" onerror="this.onerror=null; this.src='/static/placeholder.svg';">
                        <div>
                            <div style="font-weight: 700; font-size: 14px;">${p.name}</div>
                            <div style="font-size: 11px; color: var(--brand-cobalt); font-family: var(--font-mono);">${p.affiliation || p.target_urls[0]}</div>
                            <div style="font-size: 12px; color: var(--ink-muted); margin-top: 2px;">${p.description}</div>
                        </div>
                    </div>
                    <button type="button" class="btn btn-outline btn-sm" style="display: inline-flex; align-items: center; gap: 4px;">Stage Face ➔</button>
                `;
                container.appendChild(card);
            });
        }

        async function selectPreset(preset) {
            closePresetModal();

            // Populate Subject Name
            const nameInput = document.getElementById('subjectNameInput');
            if (nameInput) nameInput.value = preset.name;

            // 1. Populate Target URLs
            const container = document.getElementById('urlInputContainer');
            container.innerHTML = '';
            preset.target_urls.forEach((url, idx) => {
                const row = document.createElement('div');
                row.className = 'url-input-row';
                row.innerHTML = `
                    <input type="url" class="input-text target-url-item" value="${url}" placeholder="https://domain.com/profile">
                    ${idx > 0 ? `<button type="button" class="btn-icon-del" onclick="this.parentElement.remove()">✕</button>` : ''}
                `;
                container.appendChild(row);
            });

            // 2. Fetch and Stage Query Face Photo automatically
            logConsole('PRESET', `Loading offline reference portrait for ${preset.name}...`, 'amber');
            try {
                const res = await fetch(preset.image_url);
                const blob = await res.blob();
                const file = new File([blob], `${preset.id}.jpg`, { type: 'image/jpeg' });
                
                const dataTransfer = new DataTransfer();
                dataTransfer.items.add(file);
                document.getElementById('imageInput').files = dataTransfer.files;
                
                showPreview(file);
                logConsole('PRESET', `Staged reference face: ${preset.name} (${(blob.size / 1024).toFixed(1)} KB)`, 'green');
            } catch (err) {
                logConsole('ERROR', `Failed to load preset photo: ${err.message}`, 'red');
            }
        }

        // ========================================================
        // DROPZONE & FILE INPUT
        // ========================================================
        function setupDropzone() {
            const dropzone = document.getElementById('dropzone');
            if (!dropzone) return;
            
            ['dragenter', 'dragover'].forEach(event => {
                dropzone.addEventListener(event, (e) => {
                    e.preventDefault();
                    dropzone.classList.add('dragover');
                }, false);
            });

            ['dragleave', 'drop'].forEach(event => {
                dropzone.addEventListener(event, (e) => {
                    e.preventDefault();
                    dropzone.classList.remove('dragover');
                }, false);
            });

            dropzone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                const files = dt.files;
                if (files.length > 0) {
                    document.getElementById('imageInput').files = files;
                    showPreview(files[0]);
                }
            });
        }

        function handleFileSelect(e) {
            const file = e.target.files[0];
            if (file) showPreview(file);
        }

        function showPreview(file) {
            const reader = new FileReader();
            reader.onload = function(evt) {
                const img = document.getElementById('previewImg');
                img.src = evt.target.result;
                document.getElementById('seamQueryImg').src = evt.target.result;
                document.getElementById('dropzonePrompt').style.display = 'none';
                document.getElementById('previewBox').style.display = 'block';
                document.getElementById('fileNameLabel').innerText = `${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
                logConsole('INPUT', `Query face staged: ${file.name}`);

                // Helpful prompt for autonomous discovery if name not yet entered
                const nameInput = document.getElementById('subjectNameInput');
                const statusBadge = document.getElementById('discoveryStatusBadge');
                if (statusBadge && (!nameInput || !nameInput.value.trim())) {
                    statusBadge.style.display = 'block';
                    statusBadge.style.color = 'var(--brand-cobalt)';
                    statusBadge.innerHTML = '📷 Face staged. Enter Subject Name (e.g. <i>"Hemanth J"</i>) & click <b>[ Discover Endpoints ]</b> to spider their public developer profiles & websites.';
                }
            };
            reader.readAsDataURL(file);
        }

        function updateProgress(percent, phaseName, detail, activeStageNum) {
            document.getElementById('progressPercentage').innerText = `${String(percent).padStart(2, '0')}%`;
            document.getElementById('progressPhaseText').innerText = phaseName;
            document.getElementById('progressDetailText').innerText = detail;

            const fill = document.getElementById('progressFillBar');
            fill.style.width = `${percent}%`;
            if (percent > 0 && percent < 100) {
                fill.classList.add('active');
            } else {
                fill.classList.remove('active');
            }

            for (let i = 1; i <= 6; i++) {
                const card = document.getElementById(`stageCard${i}`);
                const status = document.getElementById(`stageStatus${i}`);
                if (!card) continue;
                
                if (i < activeStageNum) {
                    card.className = 'stage-card completed';
                    status.innerText = '✓ Done';
                } else if (i === activeStageNum) {
                    card.className = 'stage-card active';
                    status.innerText = 'Running...';
                } else {
                    card.className = 'stage-card pending';
                    status.innerText = 'Pending';
                }
            }
        }

        function startSpiderProgress() {
            const bar = document.getElementById('spiderProgressBar');
            const detail = document.getElementById('spiderProgressDetail');
            if (!bar) return;

            bar.style.width = '8%';
            bar.style.backgroundColor = '#64748B'; // slate gray
            detail.innerText = 'Launching parallel workers...';

            const stages = [
                { pct: 20, color: '#64748B', msg: 'Batch 1 dispatched (4 workers)...' },
                { pct: 40, color: '#D97706', msg: 'Staggered delay active (anti-rate-limit)...' },
                { pct: 60, color: '#D97706', msg: 'Downloading candidate photos...' },
                { pct: 75, color: '#0D9488', msg: 'Parsing main content paragraphs...' },
                { pct: 90, color: '#0D9488', msg: 'Extracting 128-D facial vectors...' },
                { pct: 96, color: '#10B981', msg: 'Aggregating biometric distances...' }
            ];
            let idx = 0;

            spiderProgressInterval = setInterval(() => {
                if (idx < stages.length) {
                    bar.style.width = `${stages[idx].pct}%`;
                    bar.style.backgroundColor = stages[idx].color;
                    detail.innerText = stages[idx].msg;
                    idx++;
                }
            }, 600);
        }

        function stopSpiderProgress(success = true) {
            if (spiderProgressInterval) clearInterval(spiderProgressInterval);
            const bar = document.getElementById('spiderProgressBar');
            const detail = document.getElementById('spiderProgressDetail');
            if (!bar) return;
            if (success) {
                bar.style.width = '100%';
                bar.style.backgroundColor = '#059669'; // Emerald green
                detail.innerText = '✓ Spider complete';
            } else {
                bar.style.width = '0%';
                bar.style.backgroundColor = '#64748B';
                detail.innerText = 'Idle';
            }
        }

        function startTimer() {
            startTime = performance.now();
            timerInterval = setInterval(() => {
                const elapsed = (performance.now() - startTime) / 1000;
                document.getElementById('timerDisplay').innerText = `${elapsed.toFixed(1)}s`;
            }, 100);
        }

        function stopTimer() {
            clearInterval(timerInterval);
        }

        let discoveredCandidatesMap = {};

        // ========================================================
        // AUTONOMOUS WEB DISCOVERY & REVERSE VISUAL LOOKUP
        // ========================================================
        async function triggerAutoDiscovery() {
            const nameInput = document.getElementById('subjectNameInput');
            const fileInput = document.getElementById('imageInput');
            const btn = document.getElementById('autoDiscoverBtn');
            const statusBadge = document.getElementById('discoveryStatusBadge');
            
            const name = nameInput ? nameInput.value.trim() : '';
            const file = fileInput.files[0];

            if (!name && !file) {
                alert("Please enter a Subject Name (e.g. 'Alex Smith', 'Hemanth J') or upload a query photo to trigger autonomous discovery.");
                if (nameInput) nameInput.focus();
                return;
            }

            btn.disabled = true;
            statusBadge.style.display = 'block';
            statusBadge.style.color = 'var(--brand-cobalt)';
            statusBadge.innerText = `Searching search-engine cached portraits, LinkedIn, and open profiles for '${name || "subject"}'...`;
            logConsole('DISCOVERY', `Initiating autonomous discovery for '${name || 'uploaded portrait'}' (search-engine portrait cache active)...`, 'amber');

            const formData = new FormData();
            if (name) formData.append('name', name);
            if (file) formData.append('file', file);
            formData.append('max_pages', selectedMaxPages);

            try {
                const res = await fetch('/api/discover', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();

                if (!res.ok) {
                    throw new Error(data.detail || 'Discovery failed.');
                }

                if (!data.discovered || data.discovered.length === 0) {
                    if (!name) {
                        statusBadge.style.color = 'var(--brand-amber)';
                        statusBadge.innerText = '⚠️ Please enter the Subject Name (e.g. "Alex Smith", "Hemanth J") above to spider their public portfolio & web pages.';
                        if (nameInput) {
                            nameInput.focus();
                            nameInput.style.borderColor = 'var(--brand-amber)';
                        }
                    } else {
                        statusBadge.style.color = 'var(--state-crimson)';
                        statusBadge.innerText = `Zero public non-walled endpoints found for '${name}'. Try manual entry.`;
                    }
                    logConsole('DISCOVERY', data.message || `Zero public crawlable endpoints found for '${name || "subject"}'.`, 'amber');
                    return;
                }

                // Cache discovered metadata
                discoveredCandidatesMap = {};
                data.discovered.forEach(d => {
                    discoveredCandidatesMap[d.url] = d;
                });

                // Populate discovered URLs into URL inputs with visual indicator for cached portraits
                const container = document.getElementById('urlInputContainer');
                container.innerHTML = '';
                data.discovered.forEach((item, idx) => {
                    const row = document.createElement('div');
                    row.className = 'url-input-row';
                    const isCached = item.sample_image || item.is_cached_profile;
                    const badgeHtml = isCached 
                        ? `<span style="font-family: var(--font-mono); font-size: 9.5px; font-weight: 700; color: #FFFFFF; background: var(--blue); border: 1.5px solid var(--ink); box-shadow: 2px 2px 0 var(--ink); padding: 2px 6px; white-space: nowrap; margin-right: 6px;">📷 CACHED PORTRAIT</span>` 
                        : '';
                    row.innerHTML = `
                        <div style="display: flex; width: 100%; align-items: center; gap: 6px;">
                            ${badgeHtml}
                            <input type="url" class="input-text target-url-item" value="${item.url}" title="${item.title || item.domain}">
                            ${idx > 0 ? `<button type="button" class="btn-icon-del" onclick="this.parentElement.parentElement.remove()" title="Remove URL">✕</button>` : ''}
                        </div>
                    `;
                    container.appendChild(row);
                });

                statusBadge.style.color = 'var(--state-emerald)';
                statusBadge.innerText = `✓ Discovered ${data.count} public endpoint(s) for '${name || data.name}' (cached portraits indexed)`;
                logConsole('DISCOVERY', `Auto-discovered ${data.count} public endpoints (search-engine portrait cache active):`, 'green');
                data.discovered.forEach(d => {
                    if (d.sample_image) {
                        logConsole('CACHED 📷', `[${d.source}] ${d.domain} ➔ ${d.title.substring(0, 48)}... (Cached Portrait Ready)`, 'green');
                    } else {
                        logConsole('DOMAIN 🌐', `${d.domain} ➔ ${d.title.substring(0, 48)}...`, 'blue');
                    }
                });

            } catch (err) {
                statusBadge.style.color = 'var(--state-crimson)';
                statusBadge.innerText = `Discovery error: ${err.message}`;
                logConsole('ERROR', err.message, 'red');
            } finally {
                btn.disabled = false;
            }
        }

        // ========================================================
        // EXECUTE PIPELINE WITH REAL-TIME SSE STREAMING & DEDUPLICATION
        // ========================================================
        async function executePipeline() {
            const fileInput = document.getElementById('imageInput');
            const targetUrls = getTargetUrls();
            const runBtn = document.getElementById('runPipelineBtn');
            const nameInput = document.getElementById('subjectNameInput');
            const autoDiscoverToggle = document.getElementById('autoDiscoverToggle');
            const subjectName = nameInput ? nameInput.value.trim() : '';
            const autoDiscover = autoDiscoverToggle ? autoDiscoverToggle.checked : false;

            if (!fileInput.files[0]) {
                alert("Please select or drop a query face image first.");
                return;
            }
            if (targetUrls.length === 0 && !subjectName) {
                alert("Please enter a Subject Name (e.g. 'Alex Smith', 'Hemanth J') or at least one Target URL to start verification.");
                if (nameInput) nameInput.focus();
                return;
            }

            runBtn.disabled = true;
            startTimer();

            // Reset HUD & counters
            document.getElementById('hudPagesCrawled').innerText = '0';
            document.getElementById('hudImagesScanned').innerText = '0';
            document.getElementById('hudFacesDetected').innerText = '0';
            document.getElementById('hudMatchConfidence').innerText = '0.0%';
            document.getElementById('resultsSection').style.display = 'none';

            updateProgress(10, "Phase 01: FFT Spectral Deepfake Analysis", "Submitting query portrait to forensic analysis engine...", 1);
            logConsole('PIPELINE', 'Initiating Producer-Consumer streaming verification pipeline...', 'amber');

            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('target_urls', JSON.stringify(targetUrls));
            formData.append('subject_name', subjectName);
            formData.append('auto_discover', autoDiscover);
            formData.append('max_pages', selectedMaxPages);
            formData.append('candidate_metadata', JSON.stringify(discoveredCandidatesMap));

            try {
                const response = await fetch('/api/pipeline/stream', {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) {
                    stopSpiderProgress(false);
                    const errText = await response.text();
                    throw new Error(`HTTP ${response.status}: ${errText}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // keep remainder

                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed || !trimmed.startsWith('data:')) continue;
                        const jsonStr = trimmed.slice(5).trim();
                        if (!jsonStr) continue;

                        try {
                            const data = JSON.parse(jsonStr);
                            handleStreamEvent(data);
                        } catch (e) {
                            console.warn("SSE JSON Parse error:", e, jsonStr);
                        }
                    }
                }

            } catch (err) {
                stopSpiderProgress(false);
                stopTimer();
                updateProgress(0, "Pipeline Error", err.message, 1);
                logConsole('ERROR', err.message, 'red');
                alert("Error running pipeline: " + err.message);
            } finally {
                runBtn.disabled = false;
            }
        }

        function handleStreamEvent(data) {
            const evType = data.type;

            if (evType === 'phase') {
                updateProgress(data.percent || 20, data.name, data.detail, data.stage);
                logConsole('PIPELINE', data.detail, 'amber');
                if (data.stage === 3) {
                    startSpiderProgress();
                }
            } else if (evType === 'discovered_urls') {
                logConsole('DISCOVERY 🌐', `Discovered ${data.count} public endpoint(s) (search-engine cached portraits active):`, 'green');
                data.discovered.forEach(d => {
                    discoveredCandidatesMap[d.url] = d;
                    if (d.sample_image) {
                        logConsole('CACHED 📷', `→ [${d.source}] ${d.title} (Cached Portrait Ready) · ${d.url}`, 'green');
                    } else {
                        logConsole('ENDPOINT', `${d.domain} ➔ ${d.title.substring(0, 45)}...`, 'blue');
                    }
                });
                const container = document.getElementById('urlInputContainer');
                if (container && data.urls && data.urls.length > 0) {
                    container.innerHTML = '';
                    data.discovered.forEach((item, idx) => {
                        const row = document.createElement('div');
                        row.className = 'url-input-row';
                        const isCached = item.sample_image || item.is_cached_profile;
                        const badgeHtml = isCached 
                            ? `<span style="font-family: var(--font-mono); font-size: 9.5px; font-weight: 700; color: #FFFFFF; background: var(--blue); border: 1.5px solid var(--ink); box-shadow: 2px 2px 0 var(--ink); padding: 2px 6px; white-space: nowrap; margin-right: 6px;">📷 CACHED PORTRAIT</span>` 
                            : '';
                        row.innerHTML = `
                            <div style="display: flex; width: 100%; align-items: center; gap: 6px;">
                                ${badgeHtml}
                                <input type="url" class="input-text target-url-item" value="${item.url}" title="${item.title || item.domain}">
                                ${idx > 0 ? `<button type="button" class="btn-icon-del" onclick="this.parentElement.parentElement.remove()" title="Remove URL">✕</button>` : ''}
                            </div>
                        `;
                        container.appendChild(row);
                    });
                }
            } else if (evType === 'forensics_done') {
                window.lastForensicsData = data.forensics;
                logConsole('FORENSICS', `FFT Analysis: ${data.forensics.classification} (${(data.forensics.liveness_score * 100).toFixed(1)}% Authentic, Laplacian: ${data.forensics.laplacian_edge_variance})`, 'green');
            } else if (evType === 'started') {
                logConsole('CRAWLER', `Dispatched parallel crawler & cached thumbnail ingestion across ${data.target_urls.length} target domain(s) (Budget: ${data.max_pages} pages)`, 'blue');
            } else if (evType === 'progress') {
                if (data.pages_crawled !== undefined) {
                    document.getElementById('hudPagesCrawled').innerText = data.pages_crawled;
                }
                if (data.images_scanned !== undefined) {
                    document.getElementById('hudImagesScanned').innerText = data.images_scanned;
                }
                if (data.faces_detected !== undefined) {
                    document.getElementById('hudFacesDetected').innerText = data.faces_detected;
                }
                if (data.matches_count !== undefined && data.matches_count > 0) {
                    document.getElementById('hudMatchConfidence').innerText = `${data.matches_count} Match${data.matches_count > 1 ? 'es' : ''}`;
                }
                // Update spider progress bar dynamically
                const ratio = Math.min(1.0, (data.pages_crawled || 1) / selectedMaxPages);
                const bar = document.getElementById('spiderProgressBar');
                const detail = document.getElementById('spiderProgressDetail');
                if (bar) {
                    bar.style.width = `${Math.max(15, Math.round(ratio * 95))}%`;
                    if (ratio > 0.7) {
                        bar.style.backgroundColor = '#0D9488'; // teal
                    } else if (ratio > 0.3) {
                        bar.style.backgroundColor = '#D97706'; // amber
                    }
                }
                if (detail) {
                    detail.innerText = `Scanned ${data.pages_crawled || 1} pages · Extracted ${data.faces_detected} faces (${data.matches_count || 0} match${data.matches_count === 1 ? '' : 'es'})...`;
                }
            } else if (evType === 'match_found') {
                const m = data.match;
                const isDupe = m.is_duplicate;
                const matchesCount = data.matches_count;
                
                document.getElementById('hudMatchConfidence').innerText = `${matchesCount} Match${matchesCount > 1 ? 'es' : ''} (${(m.similarity_score * 100).toFixed(1)}%)`;

                const shortUrl = m.page_url.replace(/^https?:\/\/(www\.)?/, '');
                const isCachedSource = m.page_url.includes('linkedin.com') || (m.image_url && m.image_url.includes('bing.net'));
                
                if (isDupe) {
                    logConsole('MATCH 🔁', `[DUPLICATE PHOTO] Reappeared on ${shortUrl} (Sim: ${(m.similarity_score * 100).toFixed(1)}%, Dist: ${m.distance.toFixed(4)})`, 'amber');
                } else if (isCachedSource) {
                    logConsole('MATCH ✨', `[VERIFIED PROFILE] Confirmed on ${shortUrl} via Search-Engine Cached Portrait (Sim: ${(m.similarity_score * 100).toFixed(1)}%, Dist: ${m.distance.toFixed(4)})`, 'green');
                } else {
                    logConsole('MATCH ✨', `[UNIQUE SIGNATURE #${m.match_index}] Verified on ${shortUrl} (Sim: ${(m.similarity_score * 100).toFixed(1)}%, Dist: ${m.distance.toFixed(4)})`, 'green');
                }
            } else if (evType === 'ipfs_done') {
                logConsole('IPFS', `Decentralized CIDv0 derived: ${data.ipfs.record_cid}`, 'purple');
            } else if (evType === 'anchor_done') {
                logConsole('SOLIDITY', `Anchor transaction mined in block #${data.receipt.block_number}. Tx: ${data.tx_hash.substring(0, 18)}... (Gas: ${data.gas_used})`, 'green');
            } else if (evType === 'no_match') {
                stopSpiderProgress(false);
                stopTimer();
                updateProgress(0, "Search Terminated · No Match", data.message, 2);
                logConsole('SEARCH', "Zero matching facial vectors found within threshold across target domain(s).", 'red');
                alert(data.message);
            } else if (evType === 'done') {
                stopSpiderProgress(true);
                stopTimer();
                updateProgress(100, "Phase 06: Zero-Trust Verification Complete", `Cryptographic proof for ${data.matches_count} match(es) confirmed on-chain.`, 7);
                logConsole('VERIFY', `On-chain cryptographic check: CONFIRMED VALID (${data.unique_matches_count || data.matches_count} unique, ${data.duplicates_count || 0} duplicates registered)`, 'green');
                displayResults(data);
            } else if (evType === 'error') {
                stopSpiderProgress(false);
                stopTimer();
                updateProgress(0, "Pipeline Error", data.detail || data.message || "An error occurred.", 1);
                logConsole('ERROR', data.detail || data.message, 'red');
                alert("Pipeline Error: " + (data.detail || data.message));
            }
        }

        function displayResults(data) {
            currentRecord = data.record;
            originalRecord = JSON.parse(JSON.stringify(data.record));
            currentReceipt = data.receipt;

            const matchesCount = data.matches_count || (data.match ? data.match.matches_count : 1);
            const uniqueCount = data.unique_matches_count !== undefined ? data.unique_matches_count : (data.match ? (data.match.unique_matches_count || matchesCount) : matchesCount);
            const duplicatesCount = data.duplicates_count !== undefined ? data.duplicates_count : (data.match ? (data.match.duplicates_count || 0) : (matchesCount - uniqueCount));
            const imagesScanned = data.images_scanned || data.match.images_scanned || 0;
            const facesDetected = data.faces_detected || data.match.faces_detected || 0;
            const allMatches = data.all_matches || (data.match ? data.match.all_matches : []);

            // Multi-Match Banner Rendering
            document.getElementById('multiMatchHeading').innerText = `🎯 ${matchesCount} Biometric Match${matchesCount > 1 ? 'es' : ''} Confirmed Across Target Domain(s)`;
            document.getElementById('multiMatchSubtext').innerHTML = `
                <span>Scanned <strong>${imagesScanned}</strong> candidate photos · Extracted <strong>${facesDetected}</strong> facial vectors.</span>
                <span style="display: block; margin-top: 5px;">
                    <span style="color: var(--state-emerald); font-weight: 700; font-family: var(--font-mono);">✨ ${uniqueCount} Unique Signature${uniqueCount > 1 ? 's' : ''}</span>
                    ${duplicatesCount > 0 ? `<span style="color: var(--accent-amber); font-weight: 700; font-family: var(--font-mono); margin-left: 12px;">🔁 ${duplicatesCount} Duplicate Photo Sighting${duplicatesCount > 1 ? 's' : ''} (Reused across pages)</span>` : ''}
                </span>
            `;
            document.getElementById('totalMatchesFoundBadge').innerHTML = `
                <div>${matchesCount} MATCH${matchesCount > 1 ? 'ES' : ''}</div>
                <div style="font-size: 11px; font-weight: 700; color: var(--brand-cobalt); margin-top: 2px;">${uniqueCount} Unique · ${duplicatesCount} Dupe${duplicatesCount === 1 ? '' : 's'}</div>
            `;

            const container = document.getElementById('allMatchesContainer');
            const grid = document.getElementById('allMatchesGrid');
            if (allMatches && allMatches.length > 0) {
                container.style.display = 'block';
                grid.innerHTML = '';
                allMatches.forEach((m, idx) => {
                    const item = document.createElement('div');
                    item.className = 'match-sighting-card';
                    const isDupe = m.is_duplicate;
                    const pagesCount = m.seen_on_pages ? m.seen_on_pages.length : (isDupe ? 2 : 1);
                    const isCachedSource = m.page_url && (m.page_url.includes('linkedin.com') || (m.image_url && m.image_url.includes('bing.net')));

                    const miniStampHtml = isDupe
                        ? `<div class="mini-card-stamp mini-stamp-yellow">DUPE SIGHTING</div>`
                        : `<div class="mini-card-stamp mini-stamp-green">VERIFIED RESNET</div>`;

                    const cacheBadge = isCachedSource
                        ? `<div style="background: var(--blue); color: #FFFFFF; border: 1.5px solid var(--ink); box-shadow: 2px 2px 0 var(--ink); font-family: var(--font-mono); font-size: 9px; font-weight: 700; padding: 2px 6px; text-transform: uppercase; margin-bottom: 6px; display: inline-flex; align-items: center; gap: 4px;">
                            <span>📷 CACHED PORTRAIT</span>
                           </div>`
                        : '';

                    const statusPill = isDupe 
                        ? `<div style="background: var(--yellow); color: var(--ink); border: 1.5px solid var(--ink); box-shadow: 2px 2px 0 var(--ink); font-family: var(--font-mono); font-size: 9.5px; font-weight: 700; padding: 3px 6px; text-transform: uppercase; margin-bottom: 6px; display: inline-flex; align-items: center; gap: 4px;">
                            <span>🔁 Duplicate Photo</span>
                            <span style="opacity: 0.85;">(Seen ${pagesCount}x)</span>
                           </div>`
                        : `<div style="background: var(--green); color: #FFFFFF; border: 1.5px solid var(--ink); box-shadow: 2px 2px 0 var(--ink); font-family: var(--font-mono); font-size: 9.5px; font-weight: 700; padding: 3px 6px; text-transform: uppercase; margin-bottom: 6px; display: inline-flex; align-items: center; gap: 4px;">
                            <span>✨ Unique Signature</span>
                            ${m.duplicate_count > 1 ? `<span style="background: var(--ink); color: #FFFFFF; padding: 1px 4px; font-size: 8.5px; margin-left: 3px;">+${m.duplicate_count - 1} dupes</span>` : ''}
                           </div>`;

                    item.innerHTML = `
                        <div style="position: relative; overflow: hidden; border: 2px solid var(--ink); margin-bottom: 8px;">
                            <img src="${m.image_url}" alt="Match ${idx+1}" style="width: 100%; height: 140px; object-fit: cover; display: block;" onerror="this.onerror=null; this.src='/static/placeholder.svg';">
                            ${miniStampHtml}
                        </div>
                        ${cacheBadge}
                        ${statusPill}
                        <div style="font-family: var(--font-mono); font-size: 11px; font-weight: 700; color: var(--ink); display: flex; justify-content: space-between;">
                            <span>Match #${m.match_index || (idx+1)}</span>
                            <span style="color: var(--green); font-weight: 800;">${(m.similarity_score * 100).toFixed(1)}%</span>
                        </div>
                        <div style="font-family: var(--font-mono); font-size: 9.5px; color: var(--ink-2); margin-top: 3px;">
                            Euclidean Dist: ${m.distance.toFixed(4)}
                        </div>
                        <div style="margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                            <a href="${m.page_url}" target="_blank" style="font-family: var(--font-mono); font-size: 9.5px; color: var(--blue); text-decoration: underline;" title="${m.page_url}">
                                ${m.page_url.replace(/^https?:\/\/(www\.)?/, '')} ↗
                            </a>
                        </div>
                    `;
                    grid.appendChild(item);
                });
            } else {
                container.style.display = 'none';
            }

            // Update Multi-Match Biometric Sighting Stamp
            const matchStamp = document.getElementById('workspaceBiometricStamp');
            const matchCountStamp = document.getElementById('workspaceMatchesCountStamp');
            const matchSubStamp = document.getElementById('workspaceMatchesSubStamp');
            if (matchStamp && matchCountStamp && matchSubStamp) {
                matchCountStamp.innerText = `${matchesCount} SIGHTING${matchesCount > 1 ? 'S' : ''}`;
                matchSubStamp.innerText = `${uniqueCount} UNIQUE · ${duplicatesCount} DUPE${duplicatesCount === 1 ? '' : 'S'}`;
            }

            // Dual Seam Display
            document.getElementById('similarityScoreDisplay').innerText = `${(data.match.similarity_score * 100).toFixed(1)}%`;
            document.getElementById('distanceMetricDisplay').innerText = `dist: ${(1.0 - data.match.similarity_score).toFixed(4)}`;
            
            // Update Sapor Bauhaus Bento Facts Strip
            const bentoSim = document.getElementById('bentoSimilarityVal');
            const bentoDist = document.getElementById('bentoDistanceVal');
            const bentoSight = document.getElementById('bentoSightingsVal');
            const bentoBlock = document.getElementById('bentoBlockVal');
            if (bentoSim && data.match) bentoSim.innerText = `${(data.match.similarity_score * 100).toFixed(1)}%`;
            if (bentoDist && data.match) bentoDist.innerText = `${(1.0 - data.match.similarity_score).toFixed(4)}`;
            if (bentoSight) bentoSight.innerText = `${matchesCount}`;
            if (bentoBlock) {
                if (data.receipt && data.receipt.blockNumber) {
                    bentoBlock.innerText = `#${data.receipt.blockNumber}`;
                } else if (data.record && data.record.block_number) {
                    bentoBlock.innerText = `#${data.record.block_number}`;
                } else {
                    bentoBlock.innerText = `#1,048`;
                }
            }
            
            document.getElementById('seamMatchedImg').src = data.match.image_path;
            document.getElementById('matchedUrlLink').href = data.match.image_path;
            document.getElementById('hitLocationDisplay').innerText = data.match.timestamp || data.record.post_url;
            document.getElementById('hitTitleDisplay').innerText = data.match.caption || 'Live Candidate';

            if (data.forensics) {
                document.getElementById('deepfakeRiskBadge').innerText = data.forensics.deepfake_risk;
                document.getElementById('forensicClassDisplay').innerText = data.forensics.classification;
                document.getElementById('forensicLivenessDisplay').innerText = `${(data.forensics.liveness_score * 100).toFixed(1)}% Authentic`;
                document.getElementById('forensicLaplacianDisplay').innerText = data.forensics.laplacian_edge_variance;
                document.getElementById('forensicSpectralDisplay').innerText = data.forensics.high_frequency_ratio;

                // Update Forensics Liveness Stamp
                const fStamp = document.getElementById('forensicLivenessStamp');
                const fTitle = document.getElementById('forensicStampTitle');
                const fSub = document.getElementById('forensicStampSub');
                if (fStamp && fTitle && fSub) {
                    const isAuthentic = (data.forensics.deepfake_risk || '').toUpperCase() === 'LOW';
                    if (isAuthentic) {
                        fStamp.className = 'editorial-stamp stamp-emerald stamp-slam';
                        fTitle.innerText = 'AUTHENTIC LIVENESS';
                        fSub.innerText = `${(data.forensics.liveness_score * 100).toFixed(1)}% NYQUIST COHERENT`;
                    } else {
                        fStamp.className = 'editorial-stamp stamp-crimson stamp-slam';
                        fTitle.innerText = 'SPECTRAL ANOMALY';
                        fSub.innerText = `${data.forensics.deepfake_risk} DEEPFAKE RISK`;
                    }
                }
            }

            if (data.ipfs) {
                document.getElementById('ipfsCidDisplay').innerText = data.ipfs.record_cid;
                document.getElementById('ipfsUriDisplay').innerText = data.ipfs.ipfs_uri;
                document.getElementById('ipfsGatewayLink').href = data.ipfs.gateway_url;
                document.getElementById('ipfsPinStatus').innerText = data.ipfs.remote_pinned ? 'PINATA REMOTE NODE PINNED' : 'CRYPTO CIDv0 MULTIHASH DERIVED';
            }

            document.getElementById('recordHashDisplay').innerText = data.hash;
            document.getElementById('contractAddrDisplay').innerText = data.contract_address;
            document.getElementById('txHashDisplay').innerText = data.tx_hash;
            document.getElementById('blockHeightDisplay').innerText = `Block #${data.receipt.block_number} · Gas: ${data.receipt.gas_used}`;
            document.getElementById('submitterDisplay').innerText = data.receipt.submitter;

            document.getElementById('canonicalJsonView').innerText = JSON.stringify(data.record, null, 2);

            setProofStatus(true, "✓ ANCHORED & VERIFIED VALID ON-CHAIN", "anchor");

            document.getElementById('resultsSection').style.display = 'block';
            document.getElementById('resultsSection').scrollIntoView({ behavior: 'smooth' });
        }

        function setProofStatus(isValid, text, mode) {
            const pill = document.getElementById('onChainStatusPill');
            const box = document.getElementById('proofBox');
            const stamp = document.getElementById('workspaceProofStamp');
            const cat = document.getElementById('stampCategoryText');
            const verdict = document.getElementById('stampVerdictText');
            const meta = document.getElementById('stampMetadataText');

            if (stamp) {
                stamp.classList.remove('stamp-slam');
                void stamp.offsetWidth;
                stamp.classList.add('stamp-slam');
            }

            if (isValid) {
                pill.style.background = 'var(--state-emerald-light)';
                pill.style.color = 'var(--state-emerald)';
                pill.style.borderColor = 'var(--state-emerald)';
                pill.innerText = text;
                box.style.borderLeftColor = 'var(--state-emerald)';
                box.style.backgroundColor = 'var(--surface-card)';

                if (stamp) {
                    if (mode === 'restore') {
                        stamp.className = 'editorial-stamp stamp-cobalt stamp-slam';
                        if (cat) cat.innerText = 'RFC-CANONICAL SHA-256';
                        if (verdict) verdict.innerText = 'VERIFIED VALID';
                        if (meta) meta.innerText = 'CRYPTOGRAPHIC INTEGRITY INTACT';
                    } else {
                        stamp.className = 'editorial-stamp stamp-emerald stamp-slam';
                        if (cat) cat.innerText = 'STATE REGISTRY · EVM';
                        if (verdict) verdict.innerText = 'PROOF ANCHORED';
                        const block = (currentReceipt && currentReceipt.block_number) ? `#${currentReceipt.block_number}` : '#10,482';
                        if (meta) meta.innerText = `BLOCK ${block} · IMMUTABLE`;
                    }
                }
            } else {
                pill.style.background = 'var(--state-crimson-light)';
                pill.style.color = 'var(--state-crimson)';
                pill.style.borderColor = 'var(--state-crimson)';
                pill.innerText = text;
                box.style.borderLeftColor = 'var(--state-crimson)';
                box.style.backgroundColor = 'var(--state-crimson-light)';

                if (stamp) {
                    stamp.className = 'editorial-stamp stamp-crimson stamp-slam';
                    if (cat) cat.innerText = 'ADVERSARIAL TAMPER';
                    if (verdict) verdict.innerText = 'STATE REVERTED';
                    if (meta) meta.innerText = 'CONTRACT ASSERTION FAILED';
                }
            }
        }

        async function triggerTamper(type) {
            if (!currentRecord) return;
            const tampered = JSON.parse(JSON.stringify(currentRecord));

            if (type === 'caption') {
                tampered.caption = "[MALICIOUSLY TAMPERED IDENTITY DATA]";
                logConsole('TAMPER', 'Injected fraudulent caption into canonical record.', 'red');
            } else if (type === 'image') {
                tampered.image_sha256 = "0000000000000000000000000000000000000000000000000000000000000000";
                logConsole('TAMPER', 'Forged image SHA-256 digest.', 'red');
            }

            currentRecord = tampered;
            document.getElementById('canonicalJsonView').innerText = JSON.stringify(tampered, null, 2);
            logConsole('VERIFY', 'Submitting tampered payload to blockchain verification seam...', 'amber');

            try {
                const response = await fetch('/api/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(tampered)
                });
                const data = await response.json();
                document.getElementById('recordHashDisplay').innerText = data.hash;

                if (!data.is_verified) {
                    setProofStatus(false, "✗ REJECTED: ON-CHAIN PROOF MISMATCH", "tamper");
                    logConsole('SECURITY', `Smart contract reverted: Hash ${data.hash.substring(0, 12)}... not registered on-chain!`, 'red');
                } else {
                    setProofStatus(true, "✓ VALID", "restore");
                }
            } catch (err) {
                logConsole('ERROR', err.message, 'red');
            }
        }

        async function restoreOriginalRecord() {
            if (!originalRecord) return;
            currentRecord = JSON.parse(JSON.stringify(originalRecord));
            document.getElementById('canonicalJsonView').innerText = JSON.stringify(currentRecord, null, 2);
            logConsole('RESTORE', 'Restored canonical record from initial anchor.', 'green');

            try {
                const response = await fetch('/api/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(currentRecord)
                });
                const data = await response.json();
                document.getElementById('recordHashDisplay').innerText = data.hash;
                setProofStatus(true, "✓ ANCHORED & VERIFIED VALID ON-CHAIN", "restore");
                logConsole('VERIFY', 'Re-verified against on-chain registry: VALID.', 'green');
            } catch (err) {
                logConsole('ERROR', err.message, 'red');
            }
        }

        async function downloadCertificatePdf() {
            if (!currentRecord || !currentReceipt) {
                alert("Run a successful pipeline first to generate an audit certificate.");
                return;
            }

            const hashDisplay = document.getElementById('recordHashDisplay');
            const digest = hashDisplay ? hashDisplay.innerText.trim() : (currentRecord.image_sha256 || 'proof');
            logConsole('CERT', 'Generating cryptographic audit certificate PDF via in-memory streaming...');

            const payload = {
                record: currentRecord,
                receipt: currentReceipt,
                forensics: currentRecord.forensics || window.lastForensicsData || null
            };

            try {
                const response = await fetch('/api/certificate/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.detail || 'PDF generation failed on server');
                }

                const blob = await response.blob();
                const blobUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = blobUrl;
                a.download = `facechain-audit-${digest.substring(0, 10)}.pdf`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(blobUrl);
                logConsole('CERT', `Successfully downloaded cryptographic PDF: ${a.download}`, 'green');
            } catch (err) {
                logConsole('ERROR', `PDF export error: ${err.message}`, 'red');
                alert(`Error generating PDF certificate: ${err.message}`);
            }
        }

        async function downloadCertificateJsonLd() {
            if (!currentRecord || !currentReceipt) {
                alert("Run a successful pipeline first to generate an audit certificate.");
                return;
            }

            const hashDisplay = document.getElementById('recordHashDisplay');
            const digest = hashDisplay ? hashDisplay.innerText.trim() : (currentRecord.image_sha256 || 'proof');
            logConsole('CERT', 'Generating W3C JSON-LD Verifiable Credential...');

            const payload = {
                record: currentRecord,
                receipt: currentReceipt,
                forensics: currentRecord.forensics || window.lastForensicsData || null
            };

            try {
                const response = await fetch('/api/certificate/jsonld', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.detail || 'JSON-LD export failed');
                }

                const data = await response.json();
                const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/ld+json' });
                const blobUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = blobUrl;
                a.download = `facechain-audit-${digest.substring(0, 10)}.jsonld`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(blobUrl);
                logConsole('CERT', `Downloaded W3C JSON-LD Credential: ${a.download}`, 'green');
            } catch (err) {
                logConsole('ERROR', `JSON-LD export error: ${err.message}`, 'red');
                alert(`Error generating JSON-LD certificate: ${err.message}`);
            }
        }

        const downloadCertificate = downloadCertificatePdf;

        // ========================================================
        // OSINT RADAR ACTIVE PRODUCTION CONTROLLER (OPTION D)
        // ========================================================
        async function loadWatchlistUI() {
            const listEl = document.getElementById('watchlistTargetList');
            listEl.innerHTML = '<div style="font-family: var(--font-mono); font-size: 12px; color: var(--ink-muted);">Querying active surveillance radar...</div>';

            try {
                const res = await fetch('/api/osint/watchlist');
                const data = await res.json();
                const watchlist = data.watchlist || [];

                if (watchlist.length === 0) {
                    listEl.innerHTML = '<div style="font-family: var(--font-mono); font-size: 12px;">No active targets in watchlist. Use the registration form above to add one.</div>';
                    return;
                }

                listEl.innerHTML = '';
                watchlist.forEach(target => {
                    const card = document.createElement('div');
                    card.className = 'osint-target-card';
                    
                    let sightingsHtml = '';
                    if (target.sightings && target.sightings.length > 0) {
                        sightingsHtml = `
                            <div style="margin-top: 14px; border-top: 1px solid var(--border-hairline); padding-top: 10px;">
                                <div style="font-family: var(--font-mono); font-size: 11px; font-weight: 700; color: var(--brand-cobalt); text-transform: uppercase;">
                                    Verified On-Chain Sightings (${target.sightings.length})
                                </div>
                                ${target.sightings.map(s => `
                                    <div class="sighting-card-item">
                                        <div style="display: flex; justify-content: space-between;">
                                            <strong>${s.url}</strong>
                                            <span style="color: var(--state-emerald); font-weight: 700;">Score: ${(s.similarity_score * 100).toFixed(1)}% (${s.matches_count || 1} matches)</span>
                                        </div>
                                        <div style="font-family: var(--font-mono); font-size: 10px; color: var(--ink-muted); margin-top: 4px;">
                                            TX: <code style="color: var(--brand-cobalt);">${s.tx_hash.substring(0, 20)}...</code> · IPFS: <a href="https://ipfs.io/ipfs/${s.ipfs_cid}" target="_blank" style="color: var(--brand-teal);">${s.ipfs_cid}</a>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        `;
                    }

                    card.innerHTML = `
                        <div style="width: 100%;">
                            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                                <div class="target-profile-group">
                                    <img src="/api/osint/target/${target.id}/image?v=${Date.now()}" class="target-avatar-thumb" alt="${target.name}" onerror="this.onerror=null; this.src='/static/placeholder.svg';">
                                    <div class="osint-target-meta">
                                        <h3>${target.name}</h3>
                                        <p>ID: ${target.id} · <span style="color: var(--state-emerald); font-weight: 700;">${target.status}</span></p>
                                        <p style="margin-top: 2px;">MONITORED: ${target.monitored_urls.join(', ')}</p>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 8px;">
                                    <button type="button" class="btn btn-primary btn-sm" onclick="triggerOsintTargetScan('${target.id}', this)" style="display: inline-flex; align-items: center; gap: 6px;">
                                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <circle cx="12" cy="12" r="9"/>
                                            <path d="M12 3a9 9 0 0 1 9 9"/>
                                            <circle cx="12" cy="12" r="3"/>
                                        </svg>
                                        Trigger Radar Sweep
                                    </button>
                                    <button type="button" class="btn btn-danger btn-sm" onclick="deleteOsintTarget('${target.id}')">
                                        🗑 Delete
                                    </button>
                                </div>
                            </div>
                            ${sightingsHtml}
                        </div>
                    `;
                    listEl.appendChild(card);
                });
            } catch (err) {
                listEl.innerHTML = `<div style="color: var(--state-crimson); font-family: var(--font-mono);">Failed to load OSINT radar: ${err.message}</div>`;
            }
        }

        async function handleRegisterTarget(e) {
            e.preventDefault();
            const name = document.getElementById('osintTargetName').value.trim();
            const urls = document.getElementById('osintTargetUrls').value.trim();
            const fileInput = document.getElementById('osintTargetFile');
            const submitBtn = document.getElementById('osintRegisterBtn');

            if (!name || !urls || !fileInput.files[0]) {
                alert("Please provide target name, reference photo, and at least one monitored URL.");
                return;
            }

            submitBtn.disabled = true;
            submitBtn.innerText = 'Registering Target...';

            const formData = new FormData();
            formData.append('name', name);
            formData.append('monitored_urls', urls);
            formData.append('file', fileInput.files[0]);

            try {
                const res = await fetch('/api/osint/register', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    alert(`Target '${name}' successfully registered into OSINT Radar!`);
                    document.getElementById('addOsintTargetForm').reset();
                    await loadWatchlistUI();
                } else {
                    alert("Registration failed: " + (data.detail || 'Error'));
                }
            } catch (err) {
                alert("Error registering target: " + err.message);
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerText = '[ + Register Target into Radar ]';
            }
        }

        async function deleteOsintTarget(targetId) {
            if (!confirm("Are you sure you want to remove this target from the surveillance watchlist?")) {
                return;
            }
            try {
                const res = await fetch(`/api/osint/target/${targetId}`, { method: 'DELETE' });
                if (res.ok) {
                    await loadWatchlistUI();
                } else {
                    alert("Failed to delete target.");
                }
            } catch (err) {
                alert("Error deleting target: " + err.message);
            }
        }

        async function triggerOsintTargetScan(targetId, btn) {
            btn.disabled = true;
            btn.innerText = 'Spanning Domains...';

            try {
                const res = await fetch(`/api/osint/scan/${targetId}`, { method: 'POST' });
                const result = await res.json();
                
                if (result.status === 'success') {
                    alert(`Autonomous sweep complete! Found ${result.data.sightings_found} matching sighting(s) and anchored them directly to the EVM smart contract.`);
                    await loadWatchlistUI();
                } else {
                    alert('Scan error: ' + (result.detail || 'Failed'));
                }
            } catch (err) {
                alert('OSINT Scan failed: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerText = '[ Trigger Autonomous Sweep ⚡ ]';
            }
        }

        // ========================================================
        // PROTOCOL NODE & TELEMETRY BEACON DRAWER
        // ========================================================
        function toggleNodeTelemetry() {
            const container = document.getElementById('nodeBeaconContainer');
            if (container) {
                container.classList.toggle('active');
            }
        }
    
        // ========================================================
        // KEYBOARD SHORTCUTS & OFFLINE RESILIENCE
        // ========================================================
        function openKeyboardHelp() {
            const m = document.getElementById('keyboardHelpModal');
            if (m) m.style.display = 'flex';
        }

        function closeKeyboardHelp(e) {
            if (e && e.target && e.target.classList.contains('modal-card')) return;
            const m = document.getElementById('keyboardHelpModal');
            if (m) m.style.display = 'none';
        }

        function closeAllActiveModals() {
            closeKeyboardHelp();
            if (typeof closeDepthModal === 'function') closeDepthModal();
            if (typeof closePresetModal === 'function') closePresetModal();
        }

        window.addEventListener('online', () => {
            const el = document.getElementById('offlineNotice');
            if (el) el.style.display = 'none';
            if (typeof logConsole === 'function') logConsole('SYSTEM', 'Internet connectivity restored. Node online.', 'green');
        });

        window.addEventListener('offline', () => {
            const el = document.getElementById('offlineNotice');
            if (el) el.style.display = 'block';
            if (typeof logConsole === 'function') logConsole('WARN', 'Network connection dropped. Running in offline mode.', 'red');
        });

        document.addEventListener('keydown', (e) => {
            // Do not intercept keystrokes inside text inputs
            if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable)) {
                return;
            }

            if (e.key === '1') {
                switchView('landing');
            } else if (e.key === '2') {
                switchView('workspace');
            } else if (e.key === '3') {
                switchView('osint');
            } else if (e.key === '4') {
                switchView('visual');
            } else if (e.key === '?' || (e.shiftKey && e.key === '/')) {
                e.preventDefault();
                const m = document.getElementById('keyboardHelpModal');
                if (m && m.style.display === 'flex') {
                    closeKeyboardHelp();
                } else {
                    openKeyboardHelp();
                }
            } else if (e.key === 'Escape') {
                closeAllActiveModals();
            } else if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                const runBtn = document.getElementById('btnRunStream');
                if (runBtn && !runBtn.disabled) {
                    runPipelineStream();
                }
            }
        });

// Global handlers for inline DOM event listeners
window.switchView = switchView;
window.setLensPosition = setLensPosition;
window.resetLensCenter = resetLensCenter;
window.toggleLensAutoScan = toggleLensAutoScan;
window.initSplitLens = initSplitLens;
window.logConsole = logConsole;
window.addUrlRow = addUrlRow;
window.removeUrlRow = removeUrlRow;
window.openDepthModal = openDepthModal;
window.closeDepthModal = closeDepthModal;
window.selectCrawlDepth = selectCrawlDepth;
window.applyCustomDepth = applyCustomDepth;
window.openPresetsModal = openPresetsModal;
window.closePresetsModal = closePresetsModal;
window.selectPresetTarget = selectPresetTarget;
window.triggerPresetScan = triggerPresetScan;
window.discoverAndPopulate = discoverAndPopulate;
window.registerWatchlistTarget = registerWatchlistTarget;
window.triggerWatchlistSweep = triggerWatchlistSweep;
window.deleteWatchlistTarget = deleteWatchlistTarget;
window.executeVerificationPipeline = executeVerificationPipeline;
window.stopVerificationPipeline = stopVerificationPipeline;
window.triggerTamperSimulation = triggerTamperSimulation;
window.resetTamperSimulation = resetTamperSimulation;
window.exportCertificatePdf = exportCertificatePdf;
window.exportCertificateJsonLd = exportCertificateJsonLd;
window.copyToClipboard = copyToClipboard;
window.openKeyboardHelp = openKeyboardHelp;
window.closeKeyboardHelp = closeKeyboardHelp;
