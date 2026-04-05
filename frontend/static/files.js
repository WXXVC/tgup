import { state } from "./store.js";
import {
  api,
  escapeHtml,
  fileSkeleton,
  fileTypeLabel,
  formatBytes,
  labeledBadge,
  setPanelFeedback,
} from "./utils.js";

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

function directoryTree() {
  const map = new Map();
  for (const file of state.files) {
    const parts = file.relative_path.split("/").slice(0, -1);
    let current = "";
    for (const part of parts) {
      current = current ? `${current}/${part}` : part;
      map.set(current, (map.get(current) || 0) + 1);
    }
  }
  return [...map.entries()]
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([path, count]) => ({ path, count, depth: path.split("/").length - 1 }));
}

function renderDirectoryTree() {
  const container = document.getElementById("file-tree");
  const nodes = directoryTree();
  if (!state.selectedFolderId) {
    container.innerHTML = `<p class="muted">请选择目录后浏览子目录树。</p>`;
    document.getElementById("file-current-path").textContent = "";
    return;
  }
  document.getElementById("file-current-path").textContent = `${state.currentSubdir ? `当前目录: ${state.currentSubdir}` : "当前目录: 根目录"} · ${state.fileScopeFilter === "direct" ? "仅当前目录文件" : "包含子目录文件"}`;
  container.innerHTML = nodes.length
    ? nodes.map((node) => `
      <button
        class="tree-node ${node.path === state.currentSubdir ? "active" : ""}"
        data-subdir="${escapeHtml(node.path)}"
        type="button"
        style="padding-left:${14 + node.depth * 18}px"
      >
        <span>${escapeHtml(node.path.split("/").at(-1) || node.path)}</span>
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

export function renderFiles() {
  const summary = document.getElementById("file-summary");
  const container = document.getElementById("file-list");

  if (!state.selectedFolderId) {
    summary.textContent = "请选择监控目录";
    document.getElementById("file-stats").innerHTML = "";
    document.getElementById("file-current-path").textContent = "";
    document.getElementById("file-tree").innerHTML = "";
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
  renderDirectoryTree();
  renderFileStats(files);
  setPanelFeedback("file-feedback", {
    visible: files.length === 0,
    tone: "empty",
    title: "当前筛选下没有文件",
    message: state.files.length ? "可以尝试清空筛选、切换目录或重新扫描。" : "这个目录暂时没有可展示的文件。",
    actionLabel: "立即扫描",
    actionId: "retry-scan-files",
  });
  summary.textContent = `共 ${state.files.length} 个文件，筛选后 ${files.length} 个，已选 ${state.selectedFiles.size} 个`;
  container.innerHTML = files.length
    ? files.map((file) => `
      <article class="file-card">
        <div class="file-card-status">${labeledBadge(file.status)}</div>
        <label class="toggle">
          <input type="checkbox" data-file-select="${escapeHtml(file.relative_path)}" ${state.selectedFiles.has(file.relative_path) ? "checked" : ""}>
          <span>${escapeHtml(file.relative_path)}</span>
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
            <span>${escapeHtml(file.relative_path.includes("/") ? file.relative_path.split("/").slice(0, -1).join(" / ") : "根目录")}</span>
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
  for (const file of filteredFiles()) {
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

  if (file.file_type === "image") {
    body.innerHTML = `<img src="${previewUrl}" alt="${escapeHtml(relativePath)}">`;
  } else if (file.file_type === "video") {
    body.innerHTML = `<video src="${previewUrl}" controls autoplay playsinline></video>`;
  } else if (file.file_type === "music") {
    body.innerHTML = `<audio src="${previewUrl}" controls autoplay></audio>`;
  } else {
    body.innerHTML = `<iframe src="${previewUrl}" style="width:100%;height:70vh;border:none;"></iframe>`;
  }

  document.getElementById("preview-dialog").showModal();
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

export function selectSubdir(subdir) {
  state.currentSubdir = subdir;
  renderFiles();
}

export function resetCurrentSubdir() {
  state.currentSubdir = "";
  renderFiles();
}
