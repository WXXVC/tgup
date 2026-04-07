import { state } from "./store.js";
import {
  api,
  escapeHtml,
  fileSkeleton,
  fileTypeLabel,
  formatBytes,
  initOverflowMarquee,
  labeledBadge,
  setPanelFeedback,
} from "./utils.js";

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

export function filteredFiles() {
  return state.files.filter((file) => {
    const fileDir = file.relative_path.includes("/") ? file.relative_path.split("/").slice(0, -1).join("/") : "";
    if (state.currentSubdir) {
      const prefix = `${state.currentSubdir}/`;
      if (state.fileScopeFilter === "direct") {
        if (fileDir !== state.currentSubdir) {
          return false;
        }
      } else if (!(file.relative_path === state.currentSubdir || file.relative_path.startsWith(prefix))) {
        return false;
      }
    } else if (state.fileScopeFilter === "direct" && fileDir) {
      return false;
    }
    if (state.fileTypeFilter !== "all" && file.file_type !== state.fileTypeFilter) {
      return false;
    }
    if (state.fileStatusFilter !== "all" && file.status !== state.fileStatusFilter) {
      return false;
    }
    if (state.fileSearch) {
      const query = state.fileSearch.toLowerCase();
      const searchSource = `${file.relative_path} ${file.absolute_path}`.toLowerCase();
      if (!searchSource.includes(query)) {
        return false;
      }
    }
    return true;
  });
}

function paginatedFiles(files) {
  const pageSize = [10, 20, 50, 100].includes(state.filePageSize) ? state.filePageSize : 10;
  const totalPages = Math.max(1, Math.ceil(files.length / pageSize));
  const page = Math.min(Math.max(1, state.filePage), totalPages);
  const start = (page - 1) * pageSize;
  const end = start + pageSize;
  state.filePage = page;
  state.filePageSize = pageSize;
  return {
    items: files.slice(start, end),
    page,
    pageSize,
    totalPages,
    totalItems: files.length,
    start: files.length ? start + 1 : 0,
    end: Math.min(end, files.length),
  };
}

function directoryTree() {
  const map = new Map();
  for (const file of state.files) {
    const parts = file.relative_path.split("/").slice(0, -1);
    let current = "";
    let parent = "";
    for (const part of parts) {
      current = current ? `${current}/${part}` : part;
      if (!map.has(current)) {
        map.set(current, {
          path: current,
          name: part,
          count: 0,
          depth: current.split("/").length - 1,
          parent,
          children: new Set(),
        });
      }
      map.get(current).count += 1;
      if (parent && map.has(parent)) {
        map.get(parent).children.add(current);
      }
      parent = current;
    }
  }
  return [...map.values()]
    .sort((left, right) => left.path.localeCompare(right.path))
    .map((node) => ({ ...node, children: [...node.children].sort((left, right) => left.localeCompare(right)) }));
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
  const nodes = visibleDirectoryNodes(directoryTree());
  const currentAncestors = new Set(ancestorPaths(state.currentSubdir));
  if (!state.selectedFolderId) {
    container.innerHTML = `<p class="muted">选择目录后可浏览子目录树。</p>`;
    document.getElementById("file-current-path").textContent = "";
    return;
  }
  document.getElementById("file-current-path").textContent = `${state.currentSubdir ? `当前目录: ${state.currentSubdir}` : "当前目录: 根目录"} · ${state.fileScopeFilter === "direct" ? "仅当前目录文件" : "包含子目录文件"}`;
  container.innerHTML = nodes.length
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
          <span class="tree-node-label" title="${escapeHtml(node.path)}">${escapeHtml(node.path.split("/").at(-1) || node.path)}</span>
        </span>
        <small>${node.count}</small>
      </button>
    `).join("")
    : `<p class="muted">当前目录没有子目录。</p>`;
}

function renderFileStats(files) {
  const stats = {
    total: files.length,
    pending: files.filter((item) => item.status === "pending").length,
    uploaded: files.filter((item) => item.status === "uploaded").length,
    locked: files.filter((item) => item.status === "locked").length,
    selected: files.filter((item) => state.selectedFiles.has(item.relative_path)).length,
  };

  document.getElementById("file-stats").innerHTML = `
    <div class="stat-card"><strong>${stats.total}</strong><span>当前结果</span></div>
    <div class="stat-card"><strong>${stats.pending}</strong><span>未上传</span></div>
    <div class="stat-card"><strong>${stats.uploaded}</strong><span>已上传</span></div>
    <div class="stat-card"><strong>${stats.locked}</strong><span>占用中</span></div>
    <div class="stat-card"><strong>${stats.selected}</strong><span>已选中</span></div>
  `;
}

function renderFilePagination(pagination) {
  const container = document.getElementById("file-pagination");
  if (!container) return;
  if (pagination.totalItems === 0) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML = `
    <div class="pagination-summary">第 ${pagination.page}/${pagination.totalPages} 页，显示 ${pagination.start}-${pagination.end} / ${pagination.totalItems}</div>
    <div class="pagination-actions">
      <button class="ghost" type="button" data-file-page="${pagination.page - 1}" ${pagination.page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="ghost" type="button" data-file-page="${pagination.page + 1}" ${pagination.page >= pagination.totalPages ? "disabled" : ""}>下一页</button>
    </div>
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
    renderFilePagination({ totalItems: 0 });
    setPanelFeedback("file-feedback", {
      visible: true,
      tone: "info",
      title: "正在加载文件",
      message: "文件列表与目录树正在同步，请稍候。",
    });
    container.innerHTML = fileSkeleton();
    summary.textContent = "正在读取文件列表…";
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
    container.innerHTML = "";
    summary.textContent = "文件列表加载失败";
    return;
  }

  const files = filteredFiles();
  const pagination = paginatedFiles(files);
  const pageItems = pagination.items;
  renderDirectoryTree();
  renderFileStats(files);
  renderFilePagination(pagination);
  setPanelFeedback("file-feedback", {
    visible: files.length === 0,
    tone: "empty",
    title: "当前筛选下没有文件",
    message: state.files.length ? "可以尝试清空筛选、切换目录或重新扫描。" : "这个目录暂时没有可展示的文件。",
    actionLabel: "立即扫描",
    actionId: "retry-scan-files",
  });
  summary.textContent = `共 ${state.files.length} 个文件，筛选后 ${files.length} 个，本页 ${pageItems.length} 个，已选 ${state.selectedFiles.size} 个`;
  container.innerHTML = files.length
    ? pageItems.map((file) => `
      <article class="file-card">
        <div class="file-card-status">${labeledBadge(file.status)}</div>
        <label class="toggle">
          <input type="checkbox" data-file-select="${escapeHtml(file.relative_path)}" ${state.selectedFiles.has(file.relative_path) ? "checked" : ""}>
          <span class="truncate-text" title="${escapeHtml(file.relative_path)}">${escapeHtml(file.relative_path)}</span>
        </label>
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
            <span class="truncate-text" title="${escapeHtml(file.relative_path.includes("/") ? file.relative_path.split("/").slice(0, -1).join(" / ") : "根目录")}">${escapeHtml(file.relative_path.includes("/") ? file.relative_path.split("/").slice(0, -1).join(" / ") : "根目录")}</span>
          </div>
        </div>
      </article>
    `).join("")
    : `<p class="muted">当前筛选条件下没有文件。</p>`;
}

export async function loadFiles(folderId, resetSelection = true) {
  if (!folderId) {
    state.selectedFolderId = "";
    state.currentSubdir = "";
    state.files = [];
    state.filePage = 1;
    if (resetSelection) {
      state.selectedFiles.clear();
    }
    renderFiles();
    return;
  }

  state.selectedFolderId = folderId;
  state.ui.loading.files = true;
  state.ui.errors.files = "";
  state.filePage = 1;
  if (resetSelection) {
    state.selectedFiles.clear();
    state.currentSubdir = "";
  }
  renderFiles();
  try {
    state.files = await api(`/api/folders/${folderId}/files`);
  } catch (error) {
    state.ui.errors.files = error.message;
    state.files = [];
  } finally {
    state.ui.loading.files = false;
    renderFiles();
  }
}

export function selectVisibleFiles() {
  for (const file of paginatedFiles(filteredFiles()).items) {
    state.selectedFiles.add(file.relative_path);
  }
  renderFiles();
}

export function clearFileSelection() {
  state.selectedFiles.clear();
  renderFiles();
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
  renderFiles();
}

export function resetCurrentSubdir() {
  state.currentSubdir = "";
  state.filePage = 1;
  renderFiles();
}

export function toggleDirectoryCollapse(path) {
  state.collapsedDirs[path] = !state.collapsedDirs[path];
  renderFiles();
}
