import { state } from "./store.js";
import {
  api,
  escapeHtml,
  fileSkeleton,
  fileTypeLabel,
  formatBytes,
  initOverflowMarquee,
  labeledBadge,
  marqueeText,
  setPanelFeedback,
} from "./utils.js";

let lastFileStatsMarkup = "";
let lastFileSummaryText = "";
let lastCurrentPathMarkup = "";
let lastDirectoryTreeMarkup = "";
let lastFileListMarkup = "";
let lastFilePaginationMarkup = "";

function applyPreviewSize() {
  const dialog = document.getElementById("preview-dialog");
  if (!dialog) return;
  dialog.dataset.previewSize = state.previewSize || "medium";
  document.querySelectorAll("[data-preview-size]").forEach((button) => {
    button.classList.toggle("active", button.dataset.previewSize === dialog.dataset.previewSize);
  });
}

function fitPreviewVideo(body) {
  const stage = body.querySelector(".preview-stage-video");
  const video = stage?.querySelector("video");
  if (!stage || !video) return;

  const stageRect = stage.getBoundingClientRect();
  const stageWidth = Math.max(1, stageRect.width - 24);
  const stageHeight = Math.max(1, stageRect.height - 24);
  const videoWidth = video.videoWidth || 1;
  const videoHeight = video.videoHeight || 1;
  const scale = Math.min(stageWidth / videoWidth, stageHeight / videoHeight);
  const renderWidth = Math.max(1, Math.floor(videoWidth * scale));
  const renderHeight = Math.max(1, Math.floor(videoHeight * scale));
  const isPortrait = videoHeight > videoWidth;

  stage.classList.toggle("is-portrait", isPortrait);
  stage.classList.toggle("is-landscape", !isPortrait);
  video.style.width = `${renderWidth}px`;
  video.style.height = `${renderHeight}px`;
}

function bindPreviewVideoLayout(body) {
  const video = body.querySelector(".preview-stage-video video");
  if (!video) return;

  const updateLayout = () => {
    window.requestAnimationFrame(() => fitPreviewVideo(body));
  };

  if (video.readyState >= 1) {
    updateLayout();
  } else {
    video.addEventListener("loadedmetadata", updateLayout, { once: true });
  }

  video.addEventListener("loadeddata", updateLayout, { once: true });
}

export function setPreviewSize(size) {
  state.previewSize = ["small", "medium", "large"].includes(size) ? size : "medium";
  window.localStorage.setItem("tgup:preview-size", state.previewSize);
  applyPreviewSize();
  const body = document.getElementById("preview-body");
  if (body?.querySelector(".preview-stage-video video")) {
    window.requestAnimationFrame(() => fitPreviewVideo(body));
  }
}

export function initPreviewSize() {
  const saved = window.localStorage.getItem("tgup:preview-size");
  state.previewSize = ["small", "medium", "large"].includes(saved || "") ? saved : "medium";
  applyPreviewSize();
}

function previewMarkup(file) {
  const previewUrl = `/api/files/preview?folder_id=${encodeURIComponent(state.selectedFolderId)}&relative_path=${encodeURIComponent(file.relative_path)}`;
  if (file.file_type === "image") {
    return `<img src="${previewUrl}" alt="${escapeHtml(file.relative_path)}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'图片加载失败'}))">`;
  }
  if (file.file_type === "video") {
    return `<video src="${previewUrl}" muted playsinline preload="metadata"></video>`;
  }
  return `<span>${fileTypeLabel(file.file_type)}</span>`;
}

function fileDisplayName(file) {
  const normalized = String(file.relative_path || "").replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || normalized || "未命名文件";
}

export function filteredFiles() {
  return state.files;
}

function visibleDirectoryNodes(nodes) {
  const byParent = new Map();
  nodes.forEach((node) => {
    const key = node.parent || "__root__";
    if (!byParent.has(key)) {
      byParent.set(key, []);
    }
    byParent.get(key).push(node);
  });
  byParent.forEach((items) => items.sort((left, right) => left.name.localeCompare(right.name)));

  const result = [];
  const walk = (parent = "") => {
    const key = parent || "__root__";
    const children = byParent.get(key) || [];
    children.forEach((node) => {
      const isCollapsed = !!state.collapsedDirs[node.path];
      result.push({ ...node, isCollapsed, hasChildren: node.children.length > 0 });
      if (!isCollapsed) {
        walk(node.path);
      }
    });
  };
  walk();
  return result;
}

function ancestorPaths(path) {
  if (!path) return [];
  const parts = path.split("/");
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

function ensureExpandedForCurrentSubdir() {
  ancestorPaths(state.currentSubdir).forEach((path) => {
    state.collapsedDirs[path] = false;
  });
}

function renderDirectoryTree() {
  const container = document.getElementById("file-tree");
  ensureExpandedForCurrentSubdir();
  const nodes = visibleDirectoryNodes(state.fileTreeNodes || []);
  const currentAncestors = new Set(ancestorPaths(state.currentSubdir));
  if (!state.selectedFolderId) {
    const emptyMarkup = `<p class="muted">选择目录后可浏览子目录树。</p>`;
    if (container.innerHTML !== emptyMarkup) {
      container.innerHTML = emptyMarkup;
    }
    document.getElementById("file-current-path").textContent = "";
    lastCurrentPathMarkup = "";
    lastDirectoryTreeMarkup = emptyMarkup;
    return;
  }
  const currentPath = state.currentSubdir || "根目录";
  const currentPathMarkup = `
    <span class="current-path-line">
      <span class="muted">当前目录</span>
      ${marqueeText(currentPath, "current-path-value")}
      <span class="current-path-scope">${escapeHtml(state.fileScopeFilter === "direct" ? "仅当前目录文件" : "包含子目录文件")}</span>
    </span>
  `;
  if (currentPathMarkup !== lastCurrentPathMarkup) {
    document.getElementById("file-current-path").innerHTML = currentPathMarkup;
    lastCurrentPathMarkup = currentPathMarkup;
  }
  const treeMarkup = nodes.length
    ? nodes.map((node) => `
      <button
        class="tree-node ${node.path === state.currentSubdir ? "active" : ""} ${node.path !== state.currentSubdir && currentAncestors.has(node.path) ? "ancestor" : ""}"
        data-subdir="${escapeHtml(node.path)}"
        type="button"
        style="--tree-depth:${node.depth}"
      >
        <span class="tree-node-row">
          ${node.hasChildren
            ? `<span class="tree-node-toggle ${node.isCollapsed ? "collapsed" : ""}" data-tree-toggle="${escapeHtml(node.path)}" aria-hidden="true"></span>`
            : `<span class="tree-node-toggle spacer" aria-hidden="true"></span>`}
          <span class="tree-node-folder" aria-hidden="true"></span>
          ${marqueeText(node.path.split("/").at(-1) || node.path, "tree-node-label", node.path)}
        </span>
        <small>${node.count}</small>
      </button>
    `).join("")
    : `<p class="muted">${state.ui.loading.fileTree ? "正在加载目录树…" : "当前目录没有子目录。"}</p>`;
  if (treeMarkup !== lastDirectoryTreeMarkup) {
    container.innerHTML = treeMarkup;
    lastDirectoryTreeMarkup = treeMarkup;
  }
  initOverflowMarquee(container);
}

function renderFileStats(files) {
  const stats = state.fileStats || {
    total: files.length,
    pending: 0,
    uploaded: 0,
    locked: 0,
    stabilizing: 0,
  };

  const markup = `
    <div class="stat-card"><strong>${stats.total}</strong><span>当前结果</span></div>
    <div class="stat-card"><strong>${stats.pending}</strong><span>未上传</span></div>
    <div class="stat-card"><strong>${stats.uploaded}</strong><span>已上传</span></div>
    <div class="stat-card"><strong>${stats.locked}</strong><span>占用中</span></div>
    <div class="stat-card"><strong>${stats.stabilizing || 0}</strong><span>等待稳定</span></div>
    <div class="stat-card"><strong>${state.selectedFiles.size}</strong><span>已选中</span></div>
  `;
  if (markup !== lastFileStatsMarkup) {
    document.getElementById("file-stats").innerHTML = markup;
    lastFileStatsMarkup = markup;
  }
}

function updateFileSelectionDependentUI(files, pageItems) {
  renderFileStats(files);
  const summary = document.getElementById("file-summary");
  if (summary) {
    const filteredTotal = state.filePagination?.total_items ?? files.length;
    const nextSummaryText = `共 ${state.fileTotalAll} 个文件，筛选后 ${filteredTotal} 个，本页 ${pageItems.length} 个，已选 ${state.selectedFiles.size} 个`;
    if (nextSummaryText !== lastFileSummaryText) {
      summary.textContent = nextSummaryText;
      lastFileSummaryText = nextSummaryText;
    }
  }
}

export function syncVisibleFileSelectionUI() {
  const files = filteredFiles();
  files.forEach((file) => {
    const checkbox = document.querySelector(`[data-file-select="${CSS.escape(file.relative_path)}"]`);
    if (checkbox) {
      checkbox.checked = state.selectedFiles.has(file.relative_path);
    }
  });
  updateFileSelectionDependentUI(files, files);
}

function renderFilePagination(pagination) {
  const container = document.getElementById("file-pagination");
  if (!container) return;
  const normalized = {
    page: pagination.page ?? 1,
    totalPages: pagination.totalPages ?? pagination.total_pages ?? 1,
    totalItems: pagination.totalItems ?? pagination.total_items ?? 0,
    start: pagination.start ?? 0,
    end: pagination.end ?? 0,
  };
  if (normalized.totalItems === 0) {
    container.classList.add("hidden");
    if (lastFilePaginationMarkup !== "") {
      container.innerHTML = "";
      lastFilePaginationMarkup = "";
    }
    return;
  }
  container.classList.remove("hidden");
  const markup = `
    <div class="pagination-summary">第 ${normalized.page}/${normalized.totalPages} 页，显示 ${normalized.start}-${normalized.end} / ${normalized.totalItems}</div>
    <div class="pagination-actions">
      <button class="ghost" type="button" data-file-page="${normalized.page - 1}" ${normalized.page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="ghost" type="button" data-file-page="${normalized.page + 1}" ${normalized.page >= normalized.totalPages ? "disabled" : ""}>下一页</button>
    </div>
  `;
  if (markup !== lastFilePaginationMarkup) {
    container.innerHTML = markup;
    lastFilePaginationMarkup = markup;
  }
}

function renderFileCard(file) {
  return `
    <article class="file-card">
      <div class="file-card-head">
        <label class="toggle file-card-toggle">
          <input type="checkbox" data-file-select="${escapeHtml(file.relative_path)}" ${state.selectedFiles.has(file.relative_path) ? "checked" : ""}>
          ${marqueeText(fileDisplayName(file), "file-card-title", file.relative_path)}
        </label>
        <div class="file-card-status">${labeledBadge(file.status)}</div>
      </div>
      <button class="preview" data-preview="${escapeHtml(file.relative_path)}">
        ${previewMarkup(file)}
      </button>
      <div class="file-meta-grid">
        <div>
          <strong>类型</strong>
          <span>${fileTypeLabel(file.file_type)}</span>
        </div>
        <div>
          <strong>大小</strong>
          <span>${formatBytes(file.size)}</span>
        </div>
        <div>
          <strong>修改时间</strong>
          <span>${escapeHtml(new Date(file.modified_at * 1000).toLocaleDateString())}</span>
        </div>
        <div>
          <strong>路径层级</strong>
          ${marqueeText(file.relative_path.includes("/") ? file.relative_path.split("/").slice(0, -1).join(" / ") : "根目录")}
        </div>
      </div>
    </article>
  `;
}

export function renderFiles() {
  const summary = document.getElementById("file-summary");
  const container = document.getElementById("file-list");
  const pageSizeControl = document.getElementById("file-page-size");
  container.style.setProperty("--file-columns", String(state.fileColumns));
  if (pageSizeControl) {
    pageSizeControl.value = String(state.filePageSize);
  }

  if (!state.selectedFolderId) {
    summary.textContent = "请选择监控目录";
    document.getElementById("file-stats").innerHTML = "";
    document.getElementById("file-current-path").textContent = "";
    document.getElementById("file-tree").innerHTML = "";
    lastFileListMarkup = "";
    lastFilePaginationMarkup = "";
    renderFilePagination({ totalItems: 0 });
    setPanelFeedback("file-feedback", {
      visible: true,
      tone: "empty",
      title: "还没有打开目录",
      message: "先选择一个监控目录，再浏览文件或手动上传。",
    });
    container.innerHTML = "";
    return;
  }

  if (state.ui.loading.files) {
    renderDirectoryTree();
    const hasExistingFiles = state.files.length > 0;
    renderFilePagination(hasExistingFiles ? (state.filePagination || { totalItems: 0 }) : { totalItems: 0 });
    setPanelFeedback("file-feedback", {
      visible: true,
      tone: "info",
      title: "正在加载文件",
      message: hasExistingFiles ? "文件列表正在后台刷新，当前内容先保持显示。" : "文件列表与目录树正在同步，请稍候。",
    });
    if (!hasExistingFiles) {
      const markup = fileSkeleton();
      if (markup !== lastFileListMarkup) {
        container.innerHTML = markup;
        lastFileListMarkup = markup;
      }
    }
    summary.textContent = hasExistingFiles ? "正在后台刷新文件列表…" : "正在读取文件列表…";
    return;
  }

  if (state.ui.errors.files) {
    renderDirectoryTree();
    renderFilePagination({ totalItems: 0 });
    setPanelFeedback("file-feedback", {
      visible: true,
      tone: "error",
      title: "文件列表加载失败",
      message: state.ui.errors.files,
      actionLabel: "重试",
      actionId: "retry-files",
    });
    if (lastFileListMarkup !== "") {
      container.innerHTML = "";
      lastFileListMarkup = "";
    }
    summary.textContent = "文件列表加载失败";
    return;
  }

  const files = filteredFiles();
  const pagination = state.filePagination || {
    page: 1,
    total_pages: 1,
    total_items: files.length,
    start: files.length ? 1 : 0,
    end: files.length,
  };
  const pageItems = files;
  renderDirectoryTree();
  renderFileStats(files);
  renderFilePagination(pagination);
  setPanelFeedback("file-feedback", {
    visible: files.length === 0,
    tone: "empty",
    title: "当前筛选下没有文件",
    message: state.fileTotalAll ? "可以尝试清空筛选、切换目录或重新扫描。" : "这个目录暂时没有可展示的文件。",
    actionLabel: "立即扫描",
    actionId: "retry-scan-files",
  });
  updateFileSelectionDependentUI(files, pageItems);
  const listMarkup = files.length
    ? pageItems.map((file) => renderFileCard(file)).join("")
    : `<p class="muted">当前筛选条件下没有文件。</p>`;
  if (listMarkup !== lastFileListMarkup) {
    container.innerHTML = listMarkup;
    lastFileListMarkup = listMarkup;
    initOverflowMarquee(container);
  }
  initOverflowMarquee(document.getElementById("file-current-path"));
}

export async function loadFiles(folderId, resetSelection = true) {
  const requestToken = ++state.requests.fileListToken;
  if (!folderId) {
    state.selectedFolderId = "";
    state.currentSubdir = "";
    state.files = [];
    state.fileTreeNodes = [];
    state.fileStats = null;
    state.fileTotalAll = 0;
    state.filePagination = {
      page: 1,
      page_size: 10,
      total_pages: 1,
      total_items: 0,
      start: 0,
      end: 0,
    };
    state.filePage = 1;
    state.ui.loading.fileTree = false;
    state.ui.errors.fileTree = "";
    if (resetSelection) {
      state.selectedFiles.clear();
    }
    renderFiles();
    return;
  }

  state.selectedFolderId = folderId;
  state.ui.loading.files = true;
  state.ui.errors.files = "";
  if (resetSelection) {
    state.selectedFiles.clear();
    state.currentSubdir = "";
    state.filePage = 1;
  }
  renderFiles();
  void loadFileTree(folderId, state.currentSubdir);
  try {
    const params = new URLSearchParams({
      page: String(state.filePage || 1),
      page_size: String(state.filePageSize || 10),
      subdir: state.currentSubdir || "",
      scope: state.fileScopeFilter || "direct",
      file_type: state.fileTypeFilter || "all",
      status: state.fileStatusFilter || "all",
      search: state.fileSearch || "",
    });
    const payload = await api(`/api/folders/${folderId}/files?${params.toString()}`);
    if (requestToken !== state.requests.fileListToken) {
      return;
    }
    state.files = payload.items || [];
    state.fileStats = payload.stats || null;
    state.filePagination = payload.pagination || state.filePagination;
    state.fileTotalAll = payload.total_all || 0;
    state.filePage = payload.pagination?.page || state.filePage;
    state.filePageSize = payload.pagination?.page_size || state.filePageSize;
  } catch (error) {
    if (requestToken !== state.requests.fileListToken) {
      return;
    }
    state.ui.errors.files = error.message;
    state.files = [];
    state.fileTreeNodes = [];
    state.fileStats = null;
    state.fileTotalAll = 0;
  } finally {
    if (requestToken !== state.requests.fileListToken) {
      return;
    }
    state.ui.loading.files = false;
    renderFiles();
  }
}

export async function loadFileTree(folderId, subdir = "") {
  const requestToken = ++state.requests.fileTreeToken;
  if (!folderId) {
    state.fileTreeNodes = [];
    state.ui.loading.fileTree = false;
    state.ui.errors.fileTree = "";
    renderDirectoryTree();
    return;
  }
  state.ui.loading.fileTree = true;
  state.ui.errors.fileTree = "";
  renderDirectoryTree();
  try {
    const params = new URLSearchParams({ subdir: subdir || "" });
    const payload = await api(`/api/folders/${folderId}/tree?${params.toString()}`);
    if (
      requestToken !== state.requests.fileTreeToken
      || state.selectedFolderId !== folderId
      || state.currentSubdir !== (subdir || "")
    ) {
      return;
    }
    state.fileTreeNodes = payload.items || [];
  } catch (error) {
    if (requestToken !== state.requests.fileTreeToken) {
      return;
    }
    state.ui.errors.fileTree = error.message;
  } finally {
    if (requestToken !== state.requests.fileTreeToken) {
      return;
    }
    state.ui.loading.fileTree = false;
    renderDirectoryTree();
  }
}

export function selectVisibleFiles() {
  for (const file of filteredFiles()) {
    state.selectedFiles.add(file.relative_path);
  }
  syncVisibleFileSelectionUI();
}

export function clearFileSelection() {
  state.selectedFiles.clear();
  syncVisibleFileSelectionUI();
}

function previewCandidates() {
  return filteredFiles().filter((file) => ["image", "video", "music", "document", "other"].includes(file.file_type));
}

export async function handlePreview(relativePath) {
  const file = state.files.find((item) => item.relative_path === relativePath);
  if (!file) return;

  state.previewRelativePath = relativePath;
  const previewUrl = `/api/files/preview?folder_id=${encodeURIComponent(state.selectedFolderId)}&relative_path=${encodeURIComponent(relativePath)}`;
  const body = document.getElementById("preview-body");
  body.className = "";

  if (file.file_type === "image") {
    body.className = "preview-media-shell";
    body.innerHTML = `<div class="preview-stage"><img src="${previewUrl}" alt="${escapeHtml(relativePath)}"></div>`;
  } else if (file.file_type === "video") {
    body.className = "preview-media-shell";
    body.innerHTML = `<div class="preview-stage preview-stage-video"><video src="${previewUrl}" controls autoplay playsinline preload="metadata"></video></div>`;
    bindPreviewVideoLayout(body);
  } else if (file.file_type === "music") {
    body.className = "preview-audio-shell";
    body.innerHTML = `<div class="preview-audio-card"><audio src="${previewUrl}" controls autoplay></audio></div>`;
  } else {
    body.className = "preview-doc-shell";
    body.innerHTML = `<div class="preview-stage preview-stage-doc"><iframe src="${previewUrl}" title="${escapeHtml(relativePath)}"></iframe></div>`;
  }

  applyPreviewSize();
  document.getElementById("preview-dialog").showModal();
  if (file.file_type === "video") {
    window.requestAnimationFrame(() => fitPreviewVideo(body));
  }
}

export function stepPreview(offset) {
  const candidates = previewCandidates();
  if (!candidates.length) {
    return;
  }
  const currentIndex = Math.max(0, candidates.findIndex((item) => item.relative_path === state.previewRelativePath));
  const nextIndex = (currentIndex + offset + candidates.length) % candidates.length;
  handlePreview(candidates[nextIndex].relative_path);
}

function cleanupPreviewMedia() {
  const body = document.getElementById("preview-body");
  if (!body) return;
  body.querySelectorAll("video, audio").forEach((media) => {
    try {
      media.pause();
      media.removeAttribute("src");
      media.load();
    } catch {
      // Ignore media cleanup failures and continue closing the dialog.
    }
  });
  body.innerHTML = "";
}

export function closePreview() {
  const dialog = document.getElementById("preview-dialog");
  if (!dialog) return;
  cleanupPreviewMedia();
  if (dialog.open) {
    dialog.close();
  }
}

export function syncPreviewOnDialogClose() {
  cleanupPreviewMedia();
}

export function selectSubdir(subdir) {
  state.currentSubdir = subdir;
  state.filePage = 1;
  ensureExpandedForCurrentSubdir();
  void loadFiles(state.selectedFolderId, false);
}

export function resetCurrentSubdir() {
  state.currentSubdir = "";
  state.filePage = 1;
  void loadFiles(state.selectedFolderId, false);
}

export function toggleDirectoryCollapse(path) {
  state.collapsedDirs[path] = !state.collapsedDirs[path];
  renderFiles();
}
