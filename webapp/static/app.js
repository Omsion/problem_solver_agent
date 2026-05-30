/* ================================================================
   app.js — 自动化解题 Agent 前端逻辑
   ================================================================ */

// ---- State ----
const state = {
  files: [],
  taskId: null,
  eventSource: null,
};

// ---- DOM ----
const $ = (sel) => document.querySelector(sel);
const dropArea  = $('#dropArea');
const dropZone  = $('#dropZone');
const fileInput = $('#fileInput');
const previewArea = $('#previewArea');
const previewList = $('#previewList');
const previewCount = $('#previewCount');
const clearBtn   = $('#clearBtn');
const solveBtn   = $('#solveBtn');
const progressCard = $('#progressCard');
const progressSteps = $('#progressSteps');
const progressMsg = $('#progressMsg');
const resultCard  = $('#resultCard');
const resultMeta  = $('#resultMeta');
const resultBody  = $('#resultBody');

// ---- Configure marked.js ----
marked.setOptions({ breaks: true, gfm: true });

// ---- File Handling ----
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
  state.files = [];
  renderPreview();
});

solveBtn.addEventListener('click', startSolve);

function addFiles(fileList) {
  const newFiles = Array.from(fileList).filter(f => f.type.startsWith('image/'));
  state.files = [...state.files, ...newFiles];
  renderPreview();
}

function renderPreview() {
  if (!state.files.length) {
    previewArea.hidden = true;
    solveBtn.disabled = true;
    return;
  }
  previewArea.hidden = false;
  previewCount.textContent = `${state.files.length} 张图片`;
  solveBtn.disabled = false;

  previewList.innerHTML = '';
  state.files.forEach((file, i) => {
    const url = URL.createObjectURL(file);
    const item = document.createElement('div');
    item.className = 'preview-item';
    item.innerHTML = `<img src="${url}" alt="preview"><button class="remove-btn" data-idx="${i}">&times;</button>`;
    item.querySelector('.remove-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      state.files.splice(i, 1);
      URL.revokeObjectURL(url);
      renderPreview();
    });
    previewList.appendChild(item);
  });
}

// ---- SSE & Processing ----
async function startSolve() {
  if (!state.files.length) return;

  // Upload
  const formData = new FormData();
  state.files.forEach(f => formData.append('files', f));

  solveBtn.disabled = true;
  solveBtn.textContent = '上传中…';

  let resp;
  try {
    resp = await fetch('/api/tasks', { method: 'POST', body: formData });
  } catch (err) {
    alert('上传失败: ' + err.message);
    solveBtn.disabled = false;
    solveBtn.textContent = '开始解答';
    return;
  }

  if (!resp.ok) {
    const err = await resp.json();
    alert('上传失败: ' + (err.error || resp.statusText));
    solveBtn.disabled = false;
    solveBtn.textContent = '开始解答';
    return;
  }

  const { task_id } = await resp.json();
  state.taskId = task_id;

  // Show progress
  dropZone.querySelector('.card')?.classList.add('uploaded');
  progressCard.hidden = false;
  resultCard.hidden = true;
  resetSteps();
  resultBody.innerHTML = '';

  // Connect SSE
  connectSSE(task_id);
}

function resetSteps() {
  progressSteps.querySelectorAll('.step').forEach(s => { s.classList.remove('active', 'done'); });
  progressSteps.querySelector('[data-step="classifying"]')?.classList.add('active');
  progressMsg.textContent = '连接中…';
}

function setStep(phase) {
  const steps = progressSteps.querySelectorAll('.step');
  const order = ['classifying', 'ocr', 'solving', 'done'];
  const idx = order.indexOf(phase);
  steps.forEach((s, i) => {
    s.classList.remove('active', 'done');
    if (i < idx) s.classList.add('done');
    if (i === idx) s.classList.add('active');
  });
}

function connectSSE(taskId) {
  if (state.eventSource) state.eventSource.close();

  const es = new EventSource(`/api/tasks/${taskId}/stream`);
  state.eventSource = es;
  let accumulated = '';

  es.onmessage = (e) => {
    let event;
    try { event = JSON.parse(e.data); } catch { return; }

    switch (event.type) {

      case 'status':
        setStep(event.phase);
        progressMsg.textContent = event.message;
        break;

      case 'chunk':
        accumulated += event.content;
        resultCard.hidden = false;
        resultBody.innerHTML = renderMarkdown(accumulated);
        // Auto-scroll for streaming feel
        resultCard.scrollIntoView({ behavior: 'smooth', block: 'end' });
        break;

      case 'done':
        setStep('done');
        progressMsg.textContent = `解答完成 — ${event.filename || ''}`;
        resultMeta.innerHTML = `<span class="badge badge-completed">完成</span> <span class="text-sm">${event.filename || ''}</span>`;
        resultBody.innerHTML = renderMarkdown(accumulated);
        es.close();
        state.eventSource = null;
        solveBtn.textContent = '再来一题';
        solveBtn.disabled = false;
        solveBtn.onclick = resetAll;
        break;

      case 'error':
        progressMsg.textContent = '处理失败: ' + event.message;
        setStep('done');
        progressSteps.querySelectorAll('.step').forEach(s => { s.classList.remove('active'); s.classList.add('done'); });
        progressSteps.querySelector('[data-step="done"] .step-dot').style.background = 'var(--error)';
        resultMeta.innerHTML = '<span class="badge badge-failed">失败</span>';
        resultBody.innerHTML = `<div class="error-card" style="padding:16px;"><p class="error-msg">${escapeHtml(event.message)}</p></div>`;
        resultCard.hidden = false;
        es.close();
        state.eventSource = null;
        solveBtn.textContent = '重试';
        solveBtn.disabled = false;
        solveBtn.onclick = startSolve;
        break;
    }
  };

  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      // If we haven't received done/error, connection was lost
    }
  };
}

function resetAll() {
  state.files = [];
  state.taskId = null;
  if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
  previewArea.hidden = true;
  previewList.innerHTML = '';
  progressCard.hidden = true;
  resultCard.hidden = true;
  resultBody.innerHTML = '';
  solveBtn.textContent = '开始解答';
  solveBtn.disabled = true;
  solveBtn.onclick = startSolve;
  fileInput.value = '';
}

// ---- Markdown Rendering ----
function renderMarkdown(text) {
  // Render markdown → HTML
  let html = marked.parse(text);

  // Syntax highlighting
  html = html.replace(/<pre><code class="language-(\w+)">/g, (_, lang) => {
    return `<pre><code class="language-${lang} hljs">`;
  });
  // Add copy buttons to code blocks
  html = html.replace(/(<pre[^>]*>)/g, '<div class="code-block-wrapper">$1<button class="copy-btn" onclick="copyCode(this)">复制</button>');

  return html;
}

// Apply KaTeX and highlight.js after DOM update
const observer = new MutationObserver(() => {
  // Highlight code blocks
  document.querySelectorAll('.markdown-body pre code').forEach(block => {
    if (!block.classList.contains('hljs-applied')) {
      hljs.highlightElement(block);
      block.classList.add('hljs-applied');
    }
  });
  // Render math
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

// ---- Utilities ----
function copyCode(btn) {
  const code = btn.parentElement.querySelector('code')?.textContent || '';
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
