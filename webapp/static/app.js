/* ================================================================
   app.js — 自动化解题 Agent 前端逻辑
   ================================================================ */

// =============================================================================
// 1. State & DOM References
// =============================================================================

// ---- Upload State (only tracks current batch of files) ----
const uploadState = {
  files: [],
};

// ---- Multi-Task Registry ----
const taskRegistry = new Map();   // taskId -> TaskState
let taskCounter = 0;

// ---- DOM: Upload Area ----
const $ = (sel) => document.querySelector(sel);
const dropArea   = $('#dropArea');
const dropZone   = $('#dropZone');
const fileInput  = $('#fileInput');
const previewArea = $('#previewArea');
const previewList = $('#previewList');
const previewCount = $('#previewCount');
const clearBtn   = $('#clearBtn');
const solveBtn   = $('#solveBtn');

// ---- DOM: Tasks Section ----
const tasksSection = $('#tasksSection');
const tasksList    = $('#tasksList');

// ---- DOM: Animation Panel ----
const animOverlay    = $('#animOverlay');
const animCanvasWrap = $('#animCanvasWrap');
const animCanvas     = $('#animCanvas');
const animCloseBtn   = $('#animCloseBtn');
const animStepCounter = $('#animStepCounter');
const animEmpty      = $('#animEmpty');
const animPlayBtn    = $('#animPlayBtn');
const animPrevBtn    = $('#animPrevBtn');
const animNextBtn    = $('#animNextBtn');
const animResetBtn   = $('#animResetBtn');
const animSpeed      = $('#animSpeed');
const animSpeedVal   = $('#animSpeedVal');

// ---- DOM: Lightbox ----
const lightbox        = $('#lightbox');
const lightboxImg     = $('#lightboxImg');
const lightboxCounter = $('#lightboxCounter');
let lightboxIdx = 0;

// ---- Configure marked.js ----
marked.setOptions({ breaks: true, gfm: true });

// ---- QR Code toggle ----
const qrBtn = document.getElementById('qrBtn');
const qrOverlay = document.getElementById('qrOverlay');
const qrCloseBtn = document.getElementById('qrCloseBtn');
const qrUrl = document.getElementById('qrUrl');
if (qrBtn) {
  qrBtn.addEventListener('click', (e) => {
    e.preventDefault();
    qrOverlay.hidden = false;
    if (qrUrl && !qrUrl.textContent) {
      qrUrl.textContent = window.location.origin;
    }
  });
}
if (qrCloseBtn) {
  qrCloseBtn.addEventListener('click', () => { qrOverlay.hidden = true; });
}
if (qrOverlay) {
  qrOverlay.addEventListener('click', (e) => { if (e.target === qrOverlay) qrOverlay.hidden = true; });
}

// ---- Page load: restore active tasks from server ----
(async function restoreTasks() {
  try {
    const resp = await fetch('/api/tasks?limit=20');
    if (!resp.ok) return;
    const { tasks } = await resp.json();
    if (!tasks || !tasks.length) return;
    for (const t of tasks) {
      // 只恢复正在进行的任务；已完成/失败的在历史记录页面查看
      if (t.status === 'pending') {
        createTaskCard(t.id);
        connectTaskSSE(t.id);
      }
      // 处理中状态的任务尝试重连，后端会自动判断是否可以恢复
      if (t.status === 'processing') {
        createTaskCard(t.id);
        connectTaskSSE(t.id);
      }
    }
  } catch (_) { /* ignore */ }
})();


// =============================================================================
// 2. File Handling (mostly unchanged from original)
// =============================================================================

dropArea.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => addFiles(e.target.files));

dropArea.addEventListener('dragover', (e) => { e.preventDefault(); dropArea.classList.add('drag-over'); });
dropArea.addEventListener('dragleave', () => dropArea.classList.remove('drag-over'));
dropArea.addEventListener('drop', (e) => {
  e.preventDefault();
  dropArea.classList.remove('drag-over');
  addFiles(e.dataTransfer.files);
});

// Clipboard paste
document.addEventListener('paste', (e) => {
  const items = e.clipboardData?.items;
  if (!items) return;
  const imageItems = [];
  for (const item of items) {
    if (item.type.startsWith('image/')) imageItems.push(item.getAsFile());
  }
  if (imageItems.length) {
    e.preventDefault();
    addFiles(imageItems);
  }
});

clearBtn.addEventListener('click', () => {
  uploadState.files = [];
  renderPreview();
});

solveBtn.addEventListener('click', startSolve);

function addFiles(fileList) {
  const newFiles = Array.from(fileList).filter(f => f.type.startsWith('image/'));
  uploadState.files = [...uploadState.files, ...newFiles];
  renderPreview();
}

function renderPreview() {
  if (!uploadState.files.length) {
    previewArea.hidden = true;
    solveBtn.disabled = true;
    return;
  }
  previewArea.hidden = false;
  previewCount.textContent = `${uploadState.files.length} 张图片`;
  solveBtn.disabled = false;

  previewList.innerHTML = '';
  uploadState.files.forEach((file, i) => {
    const url = URL.createObjectURL(file);
    const item = document.createElement('div');
    item.className = 'preview-item';
    item.innerHTML = `<img src="${url}" alt="preview"><button class="remove-btn" data-idx="${i}">&times;</button>`;
    item.querySelector('.remove-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      uploadState.files.splice(i, 1);
      URL.revokeObjectURL(url);
      renderPreview();
    });
    item.addEventListener('click', () => openLightbox(i));
    previewList.appendChild(item);
  });
}


// =============================================================================
// 3. Multi-Task Management
// =============================================================================

/**
 * Upload files for a new task, then immediately reset the upload area so the
 * user can queue another task.  A task card is created in the active-tasks
 * section and connected to its own SSE stream.
 */
async function startSolve() {
  if (!uploadState.files.length) return;

  // --- capture current files & reset form immediately ---
  const filesToUpload = [...uploadState.files];
  uploadState.files = [];
  renderPreview();
  fileInput.value = '';

  // --- upload ---
  solveBtn.disabled = true;
  solveBtn.textContent = '上传中…';

  const formData = new FormData();
  filesToUpload.forEach(f => formData.append('files', f));

  let resp;
  try {
    resp = await fetch('/api/tasks', { method: 'POST', body: formData });
  } catch (err) {
    alert('上传失败: ' + err.message);
    solveBtn.disabled = uploadState.files.length === 0;
    solveBtn.textContent = '开始解答';
    return;
  }
  solveBtn.disabled = uploadState.files.length === 0;
  solveBtn.textContent = '开始解答';

  if (!resp.ok) {
    const err = await resp.json();
    alert('上传失败: ' + (err.error || resp.statusText));
    return;
  }

  const { task_id } = await resp.json();

  // --- create card + SSE ---
  createTaskCard(task_id);
  connectTaskSSE(task_id);
}


/**
 * Build a DOM card for a new task and insert it into the active-tasks list.
 */
function createTaskCard(taskId) {
  taskCounter++;

  const ts = {
    taskId,
    eventSource: null,
    phase: 'connecting',
    accumulated: '',
    filename: '',
    status: 'processing',
    errorMessage: '',
  };

  const card = document.createElement('div');
  card.className = 'card task-card';
  card.dataset.taskId = taskId;
  card.innerHTML = `
    <div class="task-card-header">
      <div class="task-card-title">
        <span class="task-number">#${taskCounter}</span>
        <span class="task-id-label mono">${escapeHtml(taskId.substring(0, 8))}</span>
      </div>
      <div class="task-card-status">
        <span class="badge badge-processing">处理中</span>
      </div>
      <div class="task-card-actions">
        <button class="btn-icon collapse-btn" title="折叠/展开">
          <span class="collapse-icon">−</span>
        </button>
        <button class="task-close-btn" title="关闭此任务卡">&times;</button>
      </div>
    </div>
    <div class="task-card-body">
      <div class="progress-steps">
        <div class="step active" data-step="classifying"><span class="step-dot"></span>分类</div>
        <div class="step" data-step="ocr"><span class="step-dot"></span>OCR</div>
        <div class="step" data-step="solving"><span class="step-dot"></span>求解</div>
        <div class="step" data-step="done"><span class="step-dot"></span>完成</div>
      </div>
      <p class="progress-msg">连接中…</p>
      <div class="result-area" hidden>
        <div class="result-meta"></div>
        <div class="result-body markdown-body"></div>
      </div>
    </div>
  `;

  // --- cache sub-element refs on the card for easy updates ---
  card._stepEls = {
    classifying:  card.querySelector('[data-step="classifying"]'),
    ocr:          card.querySelector('[data-step="ocr"]'),
    solving:      card.querySelector('[data-step="solving"]'),
    done:         card.querySelector('[data-step="done"]'),
  };
  card._msgEl       = card.querySelector('.progress-msg');
  card._resultMeta  = card.querySelector('.result-meta');
  card._resultBody  = card.querySelector('.result-body');
  card._resultArea  = card.querySelector('.result-area');
  card._statusBadge = card.querySelector('.task-card-status .badge');
  card._collapseBtn = card.querySelector('.collapse-btn');
  card._collapseIcon = card.querySelector('.collapse-icon');
  card._closeBtn    = card.querySelector('.task-close-btn');
  card._bodyEl      = card.querySelector('.task-card-body');

  // --- collapse toggle ---
  card._collapseBtn.addEventListener('click', () => {
    const collapsed = card.classList.toggle('collapsed');
    card._collapseIcon.textContent = collapsed ? '+' : '−';
  });

  // --- close / dismiss ---
  card._closeBtn.addEventListener('click', () => {
    // Close SSE if still running
    if (ts.eventSource) {
      ts.eventSource.close();
      ts.eventSource = null;
    }
    card.remove();
    taskRegistry.delete(taskId);
    if (taskRegistry.size === 0) {
      tasksSection.hidden = true;
    }
  });

  // --- store references on the state object ---
  ts.card = card;

  // --- inject into DOM ---
  tasksList.prepend(card);
  tasksSection.hidden = false;
  taskRegistry.set(taskId, ts);

  // smooth scroll to new card
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}


// =============================================================================
// 4. SSE Connection (per task)
// =============================================================================

function connectTaskSSE(taskId) {
  const ts = taskRegistry.get(taskId);
  if (!ts) return;

  const es = new EventSource(`/api/tasks/${taskId}/stream`);
  ts.eventSource = es;
  let finished = false;

  es.onmessage = (e) => {
    let event;
    try { event = JSON.parse(e.data); } catch { return; }

    switch (event.type) {

      case 'status':
        ts.phase = event.phase || '';
        setTaskStep(ts.card, event.phase);
        ts.card._msgEl.textContent = event.message;
        break;

      case 'chunk':
        ts.accumulated += event.content;
        ts.card._resultArea.hidden = false;
        ts.card._resultBody.innerHTML = renderMarkdown(ts.accumulated);
        ts.card.scrollIntoView({ behavior: 'smooth', block: 'end' });
        break;

      case 'done':
        finished = true;
        ts.status = 'completed';
        ts.filename = event.filename || '';
        setTaskStep(ts.card, 'done');
        ts.card._msgEl.textContent = `解答完成 — ${escapeHtml(ts.filename)}`;
        updateTaskBadge(ts.card, 'completed', '完成');
        ts.card._resultMeta.innerHTML =
          `<span class="badge badge-completed">完成</span> <span class="text-sm">${escapeHtml(ts.filename)}</span>`;
        ts.card._resultBody.innerHTML = renderMarkdown(ts.accumulated);
        ts.card._resultArea.hidden = false;
        es.close();
        ts.eventSource = null;
        break;

      case 'error':
        finished = true;
        ts.status = 'failed';
        ts.errorMessage = event.message;
        setTaskStep(ts.card, 'done');
        ts.card._msgEl.textContent = '处理失败: ' + event.message;
        markStepsError(ts.card);
        updateTaskBadge(ts.card, 'failed', '失败');
        ts.card._resultMeta.innerHTML = '<span class="badge badge-failed">失败</span>';
        ts.card._resultBody.innerHTML =
          `<div class="error-card" style="padding:16px;"><p class="error-msg">${escapeHtml(event.message)}</p></div>`;
        ts.card._resultArea.hidden = false;
        es.close();
        ts.eventSource = null;
        break;
    }
  };

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      if (!finished) {
        // 如果收到了 status 但没有 chunk/done/error，说明任务在其他窗口处理中
        if (ts.accumulated === '' && ts.phase !== 'connecting') {
          ts.card._msgEl.textContent = '任务已在其他窗口处理中，请切换到对应窗口查看';
          updateTaskBadge(ts.card, 'processing', '同步中');
          ts.card._resultArea.hidden = true;
        } else {
          ts.status = 'interrupted';
          ts.card._msgEl.textContent = '连接中断 — 服务器可能已断开';
          markStepsError(ts.card);
          updateTaskBadge(ts.card, 'failed', '中断');
          ts.card._resultMeta.innerHTML = '<span class="badge badge-failed">连接中断</span>';
          if (ts.accumulated) {
            ts.card._resultBody.innerHTML =
              renderMarkdown(ts.accumulated) +
              '<div class="error-card" style="padding:16px;margin-top:12px;"><p class="error-msg">连接意外中断，以上为已接收的部分结果。</p></div>';
          } else {
            ts.card._resultBody.innerHTML =
              '<div class="error-card" style="padding:16px;"><p class="error-msg">连接意外中断，未收到任何结果。请重试。</p></div>';
          }
          ts.card._resultArea.hidden = false;
        }
        es.close();
        ts.eventSource = null;
      }
    }
  };
}


// ---- Per-task card helpers ----

function setTaskStep(card, phase) {
  const order = ['classifying', 'ocr', 'solving', 'done'];
  const idx = order.indexOf(phase);
  Object.entries(card._stepEls).forEach(([key, el]) => {
    const i = order.indexOf(key);
    el.classList.remove('active', 'done');
    if (i < idx) el.classList.add('done');
    if (i === idx) el.classList.add('active');
  });
}

function markStepsError(card) {
  Object.values(card._stepEls).forEach(el => {
    el.classList.remove('active');
    el.classList.add('done');
  });
  const dot = card._stepEls.done?.querySelector('.step-dot');
  if (dot) dot.style.background = 'var(--error)';
}

function updateTaskBadge(card, cssClass, text) {
  card._statusBadge.className = `badge badge-${cssClass}`;
  card._statusBadge.textContent = text;
}


// =============================================================================
// 5. Markdown Rendering (with animation button injection)
// =============================================================================

function renderMarkdown(text) {
  let html = marked.parse(text);

  // Wrap code blocks with action buttons (copy + animate)
  html = html.replace(/<pre><code class="language-(\w+)">/g, (_, lang) => {
    return `<pre><code class="language-${lang} hljs">`;
  });
  html = html.replace(/(<pre[^>]*>)/g,
    '<div class="code-block-wrapper">$1' +
    '<div class="code-actions">' +
    '<button class="animate-btn" onclick="openAnimation(this)" title="动画演示">动画演示</button>' +
    '<button class="copy-btn" onclick="copyCode(this)">复制</button>' +
    '</div>');

  return html;
}


// Apply KaTeX and highlight.js after DOM update
const observer = new MutationObserver(() => {
  document.querySelectorAll('.markdown-body pre code').forEach(block => {
    if (!block.classList.contains('hljs-applied')) {
      hljs.highlightElement(block);
      block.classList.add('hljs-applied');
    }
  });
  if (typeof renderMathInElement === 'function') {
    try {
      renderMathInElement(document.querySelector('.markdown-body'), {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '$', right: '$', display: false },
          { left: '\\[', right: '\\]', display: true },
          { left: '\\(', right: '\\)', display: false },
        ],
        throwOnError: false,
      });
    } catch (_) {}
  }
});
observer.observe(document.body, { childList: true, subtree: true });


// =============================================================================
// 6. Algorithm Animation Engine
// =============================================================================

/**
 * AlgorithmAnimator — Canvas-based visualisation of array algorithms.
 *
 * Call `openAnimation(btn)` from a code-block button to parse the code,
 * build an animation timeline, and display the control panel.
 */

// ---- 6a. Open animation from a button ----

function openAnimation(btn) {
  const wrapper = btn.closest('.code-block-wrapper');
  const codeEl = wrapper?.querySelector('code');
  if (!codeEl) return;
  const code = codeEl.textContent || '';

  // Parse code
  const parsed = parseAlgorithm(code);
  const { array, timeline, arrayName } = parsed;

  if (!timeline || timeline.length === 0) {
    alert('未能从此段代码中解析出可动画的算法步骤。\n\n请确保代码中包含数组初始化（如 arr = [...]）以及显式的数组操作（交换、赋值等）。');
    return;
  }

  // Set up animator
  window._animator = new AlgorithmAnimator(animCanvas, array, timeline);
  window._animator._arrayName = arrayName || 'arr';

  // Reset UI
  animEmpty.hidden = true;
  animCanvasWrap.style.display = 'flex';
  animCanvas.style.display = 'block';
  updateAnimUI();

  // Show overlay
  animOverlay.hidden = false;
  document.body.style.overflow = 'hidden';

  // Defer resize to after layout
  requestAnimationFrame(() => {
    window._animator.resize();
    window._animator.render();
  });
}

function closeAnimation() {
  if (window._animator) {
    window._animator.pause();
    window._animator = null;
  }
  animOverlay.hidden = true;
  document.body.style.overflow = '';
  animCanvas.style.display = 'none';
  animCanvasWrap.style.display = 'none';
  animEmpty.hidden = false;
}

animCloseBtn.addEventListener('click', closeAnimation);
animOverlay.addEventListener('click', (e) => {
  if (e.target === animOverlay) closeAnimation();
});

// ---- 6b. Animation controls ----

let _animPlaying = false;

function updateAnimUI() {
  const a = window._animator;
  if (!a) return;
  const step = a.currentStep;
  const total = a.timeline.length;
  animStepCounter.textContent = total > 0 ? `步骤 ${step + 1} / ${total}` : '';
  animEmpty.hidden = total > 0;
  animPlayBtn.textContent = _animPlaying ? '⏸ 暂停' : '▶ 播放';
}

animPlayBtn.addEventListener('click', () => {
  const a = window._animator;
  if (!a) return;
  if (_animPlaying) {
    a.pause();
    _animPlaying = false;
  } else {
    a.play();
    _animPlaying = true;
  }
  updateAnimUI();
});

animNextBtn.addEventListener('click', () => {
  const a = window._animator;
  if (!a) return;
  a.stepForward();
  updateAnimUI();
});

animPrevBtn.addEventListener('click', () => {
  const a = window._animator;
  if (!a) return;
  a.stepBackward();
  updateAnimUI();
});

animResetBtn.addEventListener('click', () => {
  const a = window._animator;
  if (!a) return;
  a.reset();
  _animPlaying = false;
  updateAnimUI();
});

animSpeed.addEventListener('input', () => {
  const v = parseFloat(animSpeed.value);
  animSpeedVal.textContent = v + 'x';
  if (window._animator) window._animator.setSpeed(v);
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  // Only when animation panel is open and lightbox is closed
  if (animOverlay.hidden && lightbox.hidden) return;

  if (!animOverlay.hidden) {
    if (e.key === 'Escape') { closeAnimation(); return; }
    if (e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      animPlayBtn.click();
      return;
    }
    if (e.key === 'ArrowLeft')  { animPrevBtn.click(); return; }
    if (e.key === 'ArrowRight') { animNextBtn.click(); return; }
  }

  // Lightbox keys (unchanged)
  if (!lightbox.hidden) {
    if (e.key === 'Escape') closeLightbox();
    if (e.key === 'ArrowLeft') lightboxPrev(e);
    if (e.key === 'ArrowRight') lightboxNext(e);
  }
});


// ---- 6c. AlgorithmAnimator class ----

class AlgorithmAnimator {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {number[]} array       initial array
   * @param {Object[]} timeline    array of step descriptors
   */
  constructor(canvas, array, timeline) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.array = array ? [...array] : [];
    this.timeline = timeline;
    this.currentStep = -1;       // -1 means "before first step"
    this.playing = false;
    this.speed = 1;              // steps per second
    this._timer = null;
    this._width = 0;
    this._height = 0;
  }

  // ---- sizing ----

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    this._width = Math.max(400, rect.width - 16);
    this._height = 280;
    this.canvas.width = this._width * dpr;
    this.canvas.height = this._height * dpr;
    this.canvas.style.width = this._width + 'px';
    this.canvas.style.height = this._height + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.render();
  }

  // ---- rendering ----

  render() {
    const ctx = this.ctx;
    const w = this._width;
    const h = this._height;

    ctx.clearRect(0, 0, w, h);

    // Empty / before-first-step state
    if (this.currentStep < 0 || this.timeline.length === 0) {
      this._drawPlaceholder(ctx, w, h);
      return;
    }

    const step = this.timeline[this.currentStep];
    const arr = step.array || this.array;

    if (!arr || arr.length === 0) {
      this._drawPlaceholder(ctx, w, h);
      return;
    }

    // Layout constants
    const topPad = 36;
    const bottomPad = 72;
    const barAreaH = h - topPad - bottomPad;
    const n = arr.length;
    const maxBarW = Math.min(100, (w - 60) / n);
    const barW = Math.max(18, maxBarW);
    const gap = Math.max(2, Math.min(6, (w - barW * n) / (n + 1)));
    const totalW = barW * n + gap * (n - 1);
    const startX = (w - totalW) / 2;
    const barBottom = topPad + barAreaH;

    const maxVal = Math.max(...arr, 1);

    // Highlight indices for current step
    const hlIndices = new Set();
    const hlColor = {};  // index -> css colour
    if (step.type === 'compare' || step.type === 'swap') {
      (step.indices || []).forEach(i => { hlIndices.add(i); hlColor[i] = '#f59e0b'; });
    }
    if (step.type === 'swap') {
      (step.indices || []).forEach(i => { hlColor[i] = '#ef4444'; });
    }
    if (step.type === 'assign') {
      hlIndices.add(step.index);
      hlColor[step.index] = '#22c55e';
    }

    // ---- draw bars ----
    arr.forEach((val, idx) => {
      const x = startX + idx * (barW + gap);
      const barH = Math.max(4, (val / maxVal) * barAreaH);
      const y = barBottom - barH;

      // colour
      let fill = '#3b82f6';          // default blue
      if (hlIndices.has(idx)) fill = hlColor[idx] || fill;

      // bar body
      ctx.fillStyle = fill;
      this._roundRect(ctx, x, y, barW, barH, 4);
      ctx.fill();

      // subtle border
      ctx.strokeStyle = 'rgba(0,0,0,.12)';
      ctx.lineWidth = 1;
      this._roundRect(ctx, x, y, barW, barH, 4);
      ctx.stroke();

      // value label above
      ctx.fillStyle = '#1e293b';
      ctx.font = '600 12px -apple-system, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(String(val), x + barW / 2, y - 6);

      // index label below
      ctx.fillStyle = '#94a3b8';
      ctx.font = '11px -apple-system, sans-serif';
      ctx.fillText(String(idx), x + barW / 2, barBottom + 15);
    });

    // ---- draw pointers ----
    const pointers = step.pointers || {};
    const ptrEntries = Object.entries(pointers).filter(([, pos]) => pos >= 0 && pos < arr.length);
    ptrEntries.forEach(([name, pos], pi) => {
      const px = startX + pos * (barW + gap) + barW / 2;
      const py = barBottom + 22 + pi * 24;

      // arrow
      ctx.fillStyle = '#ef4444';
      ctx.beginPath();
      ctx.moveTo(px, barBottom + 16);
      ctx.lineTo(px - 5, barBottom + 24);
      ctx.lineTo(px + 5, barBottom + 24);
      ctx.closePath();
      ctx.fill();

      // label
      ctx.fillStyle = '#1e293b';
      ctx.font = 'bold 12px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`${name}=${pos}`, px, py);
    });

    // ---- code line ----
    if (step.line) {
      ctx.fillStyle = '#475569';
      ctx.font = '12px "JetBrains Mono", monospace';
      ctx.textAlign = 'left';
      const maxTextW = w - 20;
      let text = step.line;
      // trim to fit
      while (ctx.measureText(text).width > maxTextW && text.length > 3) {
        text = text.slice(0, -4) + '…';
      }
      ctx.fillText(text, 10, h - 10);
    }

    // ---- step counter ----
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px -apple-system, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`${this.currentStep + 1} / ${this.timeline.length}`, w - 10, h - 10);
  }

  _drawPlaceholder(ctx, w, h) {
    ctx.fillStyle = '#94a3b8';
    ctx.font = '15px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('点击 播放 开始动画演示', w / 2, h / 2);
  }

  _roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  // ---- playback ----

  stepForward() {
    if (this.currentStep < this.timeline.length - 1) {
      this.currentStep++;
      this.render();
      return true;
    }
    this.pause();
    return false;
  }

  stepBackward() {
    if (this.currentStep > 0) {
      this.currentStep--;
      this.render();
      return true;
    }
    return false;
  }

  play() {
    if (this.playing) return;
    if (this.currentStep >= this.timeline.length - 1) {
      // restart from beginning if at end
      this.currentStep = -1;
      this.render();
    }
    this.playing = true;
    this._tick();
  }

  pause() {
    this.playing = false;
    if (this._timer) {
      clearTimeout(this._timer);
      this._timer = null;
    }
  }

  _tick() {
    if (!this.playing) return;
    if (!this.stepForward()) {
      // reached end
      this.playing = false;
      if (typeof _animPlaying !== 'undefined') _animPlaying = false;
      updateAnimUI();
      return;
    }
    if (typeof _animPlaying !== 'undefined') updateAnimUI();
    const interval = Math.max(100, Math.round(1000 / this.speed));
    this._timer = setTimeout(() => this._tick(), interval);
  }

  reset() {
    this.pause();
    this.currentStep = -1;
    this.render();
  }

  setSpeed(s) {
    this.speed = Math.max(0.25, Math.min(10, s));
  }

  destroy() {
    this.pause();
    this.timeline = [];
    this.array = [];
  }
}


// ---- 6d. Algorithm code parser ----

/**
 * Parse a code string into an animation timeline.
 *
 * Returns { arrayName: string, array: number[]|null, timeline: Object[] }
 *
 * Each timeline entry:
 *   { type, array: number[], pointers: Object, line: string, lineNumber: number, ... }
 */
function parseAlgorithm(code) {
  const lines = code.split('\n');
  const timeline = [];
  let array = null;
  let arrayName = null;
  const pointers = {};

  // ---- helper: evaluate range expressions ----
  function evalRange(expr) {
    // "5" -> [0,1,2,3,4]
    const simple = expr.match(/^(\d+)$/);
    if (simple) return Array.from({ length: parseInt(simple[1]) }, (_, i) => i);
    // "2, 6" -> [2,3,4,5]
    const full = expr.match(/^(\d+)\s*,\s*(\d+)$/);
    if (full) {
      const s = parseInt(full[1]), e = parseInt(full[2]);
      if (e > s) return Array.from({ length: e - s }, (_, i) => s + i);
    }
    // "len(arr)" -> [0..len-1]
    const lenMatch = expr.match(/^len\((\w+)\)$/);
    if (lenMatch && lenMatch[1] === arrayName && array) {
      return Array.from({ length: array.length }, (_, i) => i);
    }
    return null;
  }

  // ---- expand simple for-loops (one level) ----
  function expand(linesIn) {
    const out = [];
    let i = 0;
    while (i < linesIn.length) {
      const raw = linesIn[i];
      const trimmed = raw.trim();
      const indent = raw.length - raw.trimStart().length;

      const forMatch = trimmed.match(/^for\s+(\w+)\s+in\s+range\((.+)\):/);
      if (forMatch) {
        const varName = forMatch[1];
        const rangeVals = evalRange(forMatch[2]);
        if (rangeVals && rangeVals.length > 0 && rangeVals.length <= 40) {
          // find body
          let j = i + 1;
          while (j < linesIn.length) {
            const l = linesIn[j];
            if (l.trim() === '') { j++; continue; }
            const lIndent = l.length - l.trimStart().length;
            if (lIndent <= indent) break;
            j++;
          }
          const body = linesIn.slice(i + 1, j);

          for (const val of rangeVals) {
            out.push({
              type: 'loop_iter',
              line: `for ${varName} in range(...):  # ${varName}=${val}`,
              rawLine: raw.trim(),
              lineNumber: i + 1,
              varName,
              varValue: val,
            });
            // substitute variable in body lines
            for (const bl of body) {
              if (bl.trim() === '') continue;
              const blTrimmed = bl.trim();
              const subbed = blTrimmed.replace(
                new RegExp(`(?<![a-zA-Z0-9_])${varName}(?![a-zA-Z0-9_])`, 'g'),
                String(val)
              );
              out.push({
                type: 'body',
                line: subbed,
                rawLine: blTrimmed,
                lineNumber: i + 1,
              });
            }
          }
          i = j;
          continue;
        }
      }

      if (trimmed) {
        out.push({
          type: 'line',
          line: trimmed,
          rawLine: trimmed,
          lineNumber: i + 1,
        });
      }
      i++;
    }
    return out;
  }

  const expanded = expand(lines);

  // ---- parse operations from expanded lines ----
  for (const item of expanded) {
    const raw = item.rawLine || item.line;
    const line = item.line || '';

    // Skip comments, defs, imports, returns, prints
    if (line.startsWith('#') || line.startsWith('def ') || line.startsWith('class ')) continue;
    if (line.startsWith('import ') || line.startsWith('from ')) continue;
    if (line.startsWith('return ') || line.startsWith('print(')) continue;

    // ---- detect array init ----
    if (!array) {
      const initMatch = line.match(/([a-zA-Z_]\w*)\s*=\s*\[([^\]]*)\]/);
      if (initMatch) {
        arrayName = initMatch[1];
        const vals = initMatch[2].split(',').map(s => {
          const v = Number(s.trim());
          return isNaN(v) ? 0 : v;
        });
        if (vals.length > 0 && vals.length <= 50) {
          array = vals;
          timeline.push({
            type: 'init',
            array: [...array],
            arrayName,
            line: raw,
            lineNumber: item.lineNumber,
            pointers: { ...pointers },
          });
        }
        continue;
      }
    }

    if (!array) continue;  // nothing to animate without array

    // ---- swap: a[i], a[j] = a[j], a[i] OR a[i], a[j] = a[j], a[i] ----
    const swapMatch = line.match(
      /([a-zA-Z_]\w*)\[(\d+)\]\s*,\s*([a-zA-Z_]\w*)\[(\d+)\]\s*=\s*([a-zA-Z_]\w*)\[(\d+)\]\s*,\s*([a-zA-Z_]\w*)\[(\d+)\]/
    );
    if (swapMatch) {
      const [, a1, i1s, a2, j1s, a3, i2s, a4, j2s] = swapMatch;
      if (a1 === arrayName && a2 === arrayName && a3 === arrayName && a4 === arrayName) {
        const i1 = parseInt(i1s), j1 = parseInt(j1s);
        const i2 = parseInt(i2s), j2 = parseInt(j2s);
        if (!isNaN(i1) && !isNaN(j1) && i1 >= 0 && i1 < array.length && j1 >= 0 && j1 < array.length) {
          // swap in array
          [array[i1], array[j1]] = [array[j1], array[i1]];
          timeline.push({
            type: 'swap',
            indices: [i1, j1],
            array: [...array],
            line: raw,
            lineNumber: item.lineNumber,
            pointers: { ...pointers },
          });
          continue;
        }
      }
    }

    // ---- assign: a[i] = value ----
    const assignMatch = line.match(/([a-zA-Z_]\w*)\[(\d+)\]\s*=\s*(.+)/);
    if (assignMatch) {
      const targetArr = assignMatch[1];
      const idx = parseInt(assignMatch[2]);
      const rhs = assignMatch[3].trim();
      if (targetArr === arrayName && !isNaN(idx) && idx >= 0 && idx < array.length) {
        let value;
        // check if rhs is arr[j]
        const refMatch = rhs.match(/^([a-zA-Z_]\w*)\[(\d+)\]$/);
        if (refMatch && refMatch[1] === arrayName) {
          const refIdx = parseInt(refMatch[2]);
          if (!isNaN(refIdx) && refIdx >= 0 && refIdx < array.length) {
            value = array[refIdx];
          }
        } else {
          // numeric or arithmetic
          try {
            // limited eval: only digits, spaces, +-*/
            if (/^[\d\s+\-*/()]+$/.test(rhs)) {
              value = Function('"use strict"; return (' + rhs + ')')();
            } else {
              value = parseInt(rhs);
            }
          } catch (_) {
            value = parseInt(rhs);
          }
        }
        if (!isNaN(value) && isFinite(value)) {
          value = Math.round(value);
          array[idx] = value;
          timeline.push({
            type: 'assign',
            index: idx,
            value,
            array: [...array],
            line: raw,
            lineNumber: item.lineNumber,
            pointers: { ...pointers },
          });
          continue;
        }
      }
    }

    // ---- pointer assign: i = N  OR  i = N + M  (arithmetic) ----
    const ptrAssign = line.match(/^([a-zA-Z_]\w*)\s*=\s*([\d\s+\-*]+)\s*$/);
    if (ptrAssign) {
      const vn = ptrAssign[1];
      const expr = ptrAssign[2].replace(/\s+/g, '');
      let val;
      try {
        val = Function('"use strict"; return (' + expr + ')')();
      } catch (_) {
        val = parseInt(expr);
      }
      if (!isNaN(val) && isFinite(val)) {
        val = Math.round(val);
        pointers[vn] = val;
        timeline.push({
          type: 'pointer',
          varName: vn,
          value: val,
          array: [...array],
          line: raw,
          lineNumber: item.lineNumber,
          pointers: { ...pointers },
        });
        continue;
      }
    }

    // ---- pointer assign with len(arr)-1: i = len(arr) - 1 ----
    const ptrLen = line.match(/^([a-zA-Z_]\w*)\s*=\s*len\((\w+)\)\s*-\s*(\d+)\s*$/);
    if (ptrLen) {
      const vn = ptrLen[1];
      const arrRef = ptrLen[2];
      const offset = parseInt(ptrLen[3]);
      if (arrRef === arrayName && array) {
        const val = array.length - offset;
        pointers[vn] = val;
        timeline.push({
          type: 'pointer',
          varName: vn,
          value: val,
          array: [...array],
          line: raw,
          lineNumber: item.lineNumber,
          pointers: { ...pointers },
        });
        continue;
      }
    }

    // ---- pointer assign: i = len(arr) ----
    const ptrLenSimple = line.match(/^([a-zA-Z_]\w*)\s*=\s*len\((\w+)\)\s*$/);
    if (ptrLenSimple) {
      const vn = ptrLenSimple[1];
      const arrRef = ptrLenSimple[2];
      if (arrRef === arrayName && array) {
        pointers[vn] = array.length;
        timeline.push({
          type: 'pointer',
          varName: vn,
          value: array.length,
          array: [...array],
          line: raw,
          lineNumber: item.lineNumber,
          pointers: { ...pointers },
        });
        continue;
      }
    }

    // ---- pointer inc/dec: i += N / i -= N ----
    const ptrInc = line.match(/^([a-zA-Z_]\w*)\s*\+=\s*(\d+)\s*$/);
    if (ptrInc) {
      const vn = ptrInc[1], inc = parseInt(ptrInc[2]);
      if (pointers[vn] !== undefined) {
        pointers[vn] += inc;
        timeline.push({
          type: 'pointer',
          varName: vn,
          value: pointers[vn],
          array: [...array],
          line: raw,
          lineNumber: item.lineNumber,
          pointers: { ...pointers },
        });
        continue;
      }
    }
    const ptrDec = line.match(/^([a-zA-Z_]\w*)\s*-=\s*(\d+)\s*$/);
    if (ptrDec) {
      const vn = ptrDec[1], dec = parseInt(ptrDec[2]);
      if (pointers[vn] !== undefined) {
        pointers[vn] -= dec;
        timeline.push({
          type: 'pointer',
          varName: vn,
          value: pointers[vn],
          array: [...array],
          line: raw,
          lineNumber: item.lineNumber,
          pointers: { ...pointers },
        });
        continue;
      }
    }

    // ---- compare: if arr[i] < arr[j] (and variants) ----
    const cmpMatch = line.match(
      /if\s+([a-zA-Z_]\w*)\[(\d+)\]\s*(<|>|<=|>=|==|!=)\s*([a-zA-Z_]\w*)\[(\d+)\]\s*:/
    );
    if (cmpMatch) {
      const [, a1, i1s, op, a2, i2s] = cmpMatch;
      if (a1 === arrayName && a2 === arrayName) {
        const i1 = parseInt(i1s), i2 = parseInt(i2s);
        if (!isNaN(i1) && !isNaN(i2)) {
          timeline.push({
            type: 'compare',
            indices: [i1, i2],
            operator: op,
            array: [...array],
            line: raw,
            lineNumber: item.lineNumber,
            pointers: { ...pointers },
          });
          continue;
        }
      }
    }

    // ---- loop iteration marker ----
    if (item.type === 'loop_iter') {
      if (item.varName && item.varValue !== undefined) {
        pointers[item.varName] = item.varValue;
      }
      timeline.push({
        type: 'marker',
        array: [...array],
        line: item.rawLine || raw,
        lineNumber: item.lineNumber,
        pointers: { ...pointers },
      });
      continue;
    }
  }

  return { arrayName: arrayName || 'arr', array, timeline };
}


// =============================================================================
// 7. Lightbox (unchanged from original)
// =============================================================================

function openLightbox(idx) {
  lightboxIdx = idx;
  showLightboxImage();
  lightbox.hidden = false;
  document.body.style.overflow = 'hidden';
}
function showLightboxImage() {
  const file = uploadState.files[lightboxIdx];
  if (!file) return;
  const url = URL.createObjectURL(file);
  lightboxImg.src = url;
  lightboxCounter.textContent = (lightboxIdx + 1) + ' / ' + uploadState.files.length;
}
function closeLightbox() {
  lightbox.hidden = true;
  document.body.style.overflow = '';
}
function lightboxPrev(e) { e.stopPropagation(); if (lightboxIdx > 0) { lightboxIdx--; showLightboxImage(); } }
function lightboxNext(e) { e.stopPropagation(); if (lightboxIdx < uploadState.files.length - 1) { lightboxIdx++; showLightboxImage(); } }

document.getElementById('lightboxBg').addEventListener('click', closeLightbox);
document.getElementById('lightboxClose').addEventListener('click', closeLightbox);
document.getElementById('lightboxPrev').addEventListener('click', lightboxPrev);
document.getElementById('lightboxNext').addEventListener('click', lightboxNext);


// =============================================================================
// 8. Utilities
// =============================================================================

function copyCode(btn) {
  const wrapper = btn.closest('.code-block-wrapper');
  const code = wrapper?.querySelector('code')?.textContent || '';
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = '已复制';
    setTimeout(() => btn.textContent = '复制', 1500);
  });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
