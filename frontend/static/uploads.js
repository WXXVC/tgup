import { TASK_STATUS_ORDER } from "./constants.js";
import { getTaskById, isRetryableTask, state } from "./store.js";
import {
  api,
  escapeHtml,
  formatDateTime,
  initOverflowMarquee,
  labeledBadge,
  pushToast,
  setPanelFeedback,
  statusLabel,
  taskSkeleton,
} from "./utils.js";
import { submitJson } from "./settings.js";

function emptyStats() {
  return {
    total: 0,
    pending: 0,
    uploading: 0,
    uploaded: 0,
    failed: 0,
    locked: 0,
  };
}

export async function loadUploads() {
  state.ui.loading.uploads = true;
  state.ui.errors.uploads = "";
  renderUploads();
  try {
    const [uploads, stats] = await Promise.all([
      api("/api/uploads"),
      api("/api/uploads/stats"),
    ]);
    state.uploads = uploads;
    state.uploadStats = stats;
    state.selectedUploadTaskIds = new Set(
      [...state.selectedUploadTaskIds].filter((taskId) => uploads.some((item) => item.id === taskId)),
    );
  } catch (error) {
    state.ui.errors.uploads = error.message;
  } finally {
    state.ui.loading.uploads = false;
    renderUploads();
  }
}

export async function loadUploadStats() {
  try {
    state.uploadStats = await api("/api/uploads/stats");
    renderUploadStats();
  } catch {
    state.uploadStats = state.uploadStats || emptyStats();
    renderUploadStats();
  }
}

export function hasActiveUploadTasks() {
  return state.uploads.some((task) => task.status === "uploading" || task.status === "pending");
}

function renderUploadStats() {
  const stats = state.uploadStats || emptyStats();
  const markup = `
    <article class="top-stat-card">
      <strong>${stats.total}</strong>
      <span>总任务</span>
    </article>
    <article class="top-stat-card is-active">
      <strong>${stats.uploading}</strong>
      <span>上传中</span>
    </article>
    <article class="top-stat-card">
      <strong>${stats.pending}</strong>
      <span>待处理</span>
    </article>
    <article class="top-stat-card is-danger">
      <strong>${stats.failed}</strong>
      <span>失败</span>
    </article>
    <article class="top-stat-card is-success">
      <strong>${stats.uploaded}</strong>
      <span>已完成</span>
    </article>
  `;
  const topStats = document.getElementById("top-upload-stats");
  if (topStats) {
    topStats.innerHTML = markup;
  }
}

function compareTasks(left, right) {
  if (state.taskSort === "created_desc") {
    return (right.created_at || 0) - (left.created_at || 0);
  }
  if (state.taskSort === "progress_desc") {
    return (right.progress || 0) - (left.progress || 0);
  }
  if (state.taskSort === "name_asc") {
    return (left.relative_path || "").localeCompare(right.relative_path || "");
  }
  return (right.updated_at || 0) - (left.updated_at || 0);
}

function filteredUploads() {
  const statusRank = new Map(TASK_STATUS_ORDER.map((status, index) => [status, index]));
  return [...state.uploads]
    .filter((task) => {
      if (state.taskFolderFilter !== "all" && task.folder_id !== state.taskFolderFilter) {
        return false;
      }
      if (state.taskStatusFilter !== "all" && task.status !== state.taskStatusFilter) {
        return false;
      }
      if (state.taskSearch) {
        const query = state.taskSearch.toLowerCase();
        const haystack = `${task.relative_path} ${task.caption || ""} ${task.error_message || ""}`.toLowerCase();
        if (!haystack.includes(query)) {
          return false;
        }
      }
      return true;
    })
    .sort((left, right) => {
      const rankDiff = (statusRank.get(left.status) ?? 999) - (statusRank.get(right.status) ?? 999);
      if (rankDiff !== 0) return rankDiff;
      return compareTasks(left, right);
    });
}

function paginatedUploads(tasks) {
  const pageSize = [10, 20, 50, 100].includes(state.uploadPageSize) ? state.uploadPageSize : 10;
  const totalPages = Math.max(1, Math.ceil(tasks.length / pageSize));
  const page = Math.min(Math.max(1, state.uploadPage), totalPages);
  const start = (page - 1) * pageSize;
  const end = start + pageSize;
  state.uploadPage = page;
  state.uploadPageSize = pageSize;
  return {
    items: tasks.slice(start, end),
    page,
    pageSize,
    totalPages,
    totalItems: tasks.length,
    start: tasks.length ? start + 1 : 0,
    end: Math.min(end, tasks.length),
  };
}

function taskBatchSummary(task) {
  const batchCount = Array.isArray(task.batch_paths) ? task.batch_paths.length : 0;
  if (batchCount > 1) {
    return `媒体组任务 · 共 ${batchCount} 个文件`;
  }
  return "单文件任务";
}

function isBatchTask(task) {
  return Array.isArray(task.batch_paths) && task.batch_paths.length > 1;
}

function taskCompletionText(task) {
  if (isBatchTask(task)) {
    const total = task.batch_paths.length;
    const done = Math.min(task.completed_count || 0, total);
    return `完成数: ${done}/${total}`;
  }
  if (task.status === "uploading") {
    return `进度: ${(task.progress || 0).toFixed(2)}%`;
  }
  if (task.status === "uploaded") {
    return "已完成";
  }
  if (task.status === "failed") {
    return "上传失败";
  }
  if (task.status === "locked") {
    return "文件占用中";
  }
  if (task.status === "pending") {
    return "等待上传";
  }
  return `进度: ${(task.progress || 0).toFixed(2)}%`;
}

function taskProgressMarkup(task) {
  if (isBatchTask(task)) {
    return `<div class="progress" data-progress-track="${task.id}"><span style="width:${task.progress || 0}%"></span></div>`;
  }
  return `<div class="progress" data-progress-track="${task.id}"><span style="width:${task.progress || 0}%"></span></div>`;
}

function renderUploadPagination(pagination) {
  const container = document.getElementById("upload-pagination");
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
      <button class="ghost" type="button" data-task-page="${pagination.page - 1}" ${pagination.page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="ghost" type="button" data-task-page="${pagination.page + 1}" ${pagination.page >= pagination.totalPages ? "disabled" : ""}>下一页</button>
    </div>
  `;
}

export function renderUploads() {
  renderUploadStats();
  const tasks = filteredUploads();
  const pagination = paginatedUploads(tasks);
  const pageItems = pagination.items;
  const container = document.getElementById("upload-list");
  const summary = document.getElementById("upload-summary");
  const pageSizeControl = document.getElementById("upload-page-size");
  const selectedCount = tasks.filter((task) => state.selectedUploadTaskIds.has(task.id)).length;
  container.style.setProperty("--task-columns", String(state.taskColumns));
  if (pageSizeControl) {
    pageSizeControl.value = String(state.uploadPageSize);
  }

  if (summary) {
    summary.textContent = `总任务 ${state.uploads.length} 个，筛选后 ${tasks.length} 个，本页 ${pageItems.length} 个，已选中 ${selectedCount} 个`;
  }

  if (state.ui.loading.uploads) {
    renderUploadPagination({ totalItems: 0 });
    setPanelFeedback("upload-feedback", {
      visible: true,
      tone: "info",
      title: "正在加载任务列表",
      message: "任务状态和统计信息正在刷新。",
    });
    if (summary) {
      summary.textContent = "正在同步任务列表...";
    }
    container.innerHTML = taskSkeleton();
    return;
  }

  if (state.ui.errors.uploads) {
    renderUploadPagination({ totalItems: 0 });
    setPanelFeedback("upload-feedback", {
      visible: true,
      tone: "error",
      title: "任务列表加载失败",
      message: state.ui.errors.uploads,
      actionLabel: "重试",
      actionId: "retry-uploads",
    });
    if (summary) {
      summary.textContent = "任务列表加载失败";
    }
    container.innerHTML = "";
    return;
  }

  setPanelFeedback("upload-feedback", {
    visible: tasks.length === 0,
    tone: "empty",
    title: "没有匹配的上传任务",
    message: state.uploads.length
      ? "可以调整筛选条件，或者稍后等待新任务入队。"
      : "当前还没有上传任务，先去目录里发起手动上传吧。",
    actionLabel: state.uploads.length ? "" : "刷新",
    actionId: state.uploads.length ? "" : "retry-uploads",
  });
  renderUploadPagination(pagination);

  container.innerHTML = tasks.length
    ? `<div class="task-group-list upload-card-list">${pageItems.map((task) => `
      <article class="item upload-task-item upload-task-item-${task.status}" data-task-id="${task.id}">
        <div class="item-top">
          <div>
            <h3 class="truncate-text" title="${escapeHtml(task.relative_path)}">${escapeHtml(task.relative_path)}</h3>
            <p class="muted">${escapeHtml(taskBatchSummary(task))}</p>
          </div>
          <div class="upload-task-top-actions">
            ${labeledBadge(task.status)}
            <label class="inline-check upload-task-check">
              <input type="checkbox" data-task-select="${task.id}" ${state.selectedUploadTaskIds.has(task.id) ? "checked" : ""}>
            </label>
          </div>
        </div>
        ${taskProgressMarkup(task)}
        <div class="meta upload-task-meta upload-task-meta-strong">
          <span data-progress-text="${task.id}">${taskCompletionText(task)}</span>
          <span>模式: ${isBatchTask(task) ? "媒体组上传" : "单文件上传"}</span>
          ${task.error_message ? `<span class="danger">${escapeHtml(task.error_message)}</span>` : ""}
        </div>
        <div class="item-actions">
          <button data-action="task-detail" data-id="${task.id}" class="ghost" type="button">详情</button>
          ${isRetryableTask(task) ? `<button data-action="retry-upload" data-id="${task.id}" class="ghost" type="button">重试</button>` : ""}
        </div>
      </article>
    `).join("")}</div>`
    : `<p class="muted">当前筛选条件下没有任务。</p>`;
  initOverflowMarquee(container);
}

export async function syncUploadProgress() {
  const uploads = await api("/api/uploads");
  state.uploads = uploads;
  state.selectedUploadTaskIds = new Set(
    [...state.selectedUploadTaskIds].filter((taskId) => uploads.some((item) => item.id === taskId)),
  );

  const visibleTaskIds = new Set(
    paginatedUploads(filteredUploads()).items.map((task) => task.id),
  );

  uploads.forEach((task) => {
    if (!visibleTaskIds.has(task.id)) return;
    const progressBar = document.querySelector(`[data-progress-track="${task.id}"] span`);
    if (progressBar) {
      progressBar.style.width = `${task.progress || 0}%`;
    }
    const progressText = document.querySelector(`[data-progress-text="${task.id}"]`);
    if (progressText) {
      progressText.textContent = taskCompletionText(task);
    }
  });
}

export async function clearUploads(scope) {
  if (scope === "all" && !window.confirm("确认清理全部任务吗？这会移除当前任务列表中的所有任务记录。")) {
    return;
  }
  await api(`/api/uploads/clear?scope=${encodeURIComponent(scope)}`, {
    method: "DELETE",
    headers: {},
  });
  state.selectedUploadTaskIds.clear();
  await loadUploads();
  pushToast("任务清理已完成", "success");
}

export async function deleteSelectedUploads() {
  if (state.selectedUploadTaskIds.size === 0) {
    return;
  }
  const deletableIds = [...state.selectedUploadTaskIds].filter((taskId) => !!getTaskById(taskId));
  const includesUploading = deletableIds.some((taskId) => getTaskById(taskId)?.status === "uploading");
  if (deletableIds.length === 0) {
    pushToast("当前选中的任务里没有可清理项", "info");
    return;
  }
  if (includesUploading && !window.confirm("选中的任务中包含上传中的任务，删除后会中断当前上传。确认继续吗？")) {
    return;
  }
  await api("/api/uploads/delete-batch", {
    method: "DELETE",
    body: JSON.stringify({ task_ids: deletableIds }),
  });
  state.selectedUploadTaskIds.clear();
  await loadUploads();
  pushToast("已删除选中的任务", "success");
}

export async function retrySelectedUploads() {
  if (state.selectedUploadTaskIds.size === 0) {
    return;
  }
  const retryableIds = [...state.selectedUploadTaskIds].filter((taskId) => isRetryableTask(getTaskById(taskId)));
  if (retryableIds.length === 0) {
    pushToast("当前选中的任务里没有可重试项", "info");
    return;
  }
  await submitJson("/api/uploads/retry-batch", {
    task_ids: retryableIds,
  });
  state.selectedUploadTaskIds.clear();
  await loadUploads();
  pushToast("已将选中任务重新加入队列", "success");
}

export function selectVisibleTasks() {
  const eligible = paginatedUploads(filteredUploads()).items
    .filter((task) => ["pending", "uploading", "failed", "locked", "uploaded"].includes(task.status))
    .map((task) => task.id);
  state.selectedUploadTaskIds = new Set(eligible);
  renderUploads();
}

export function clearTaskSelection() {
  state.selectedUploadTaskIds.clear();
  renderUploads();
}

export function showTaskDetail(taskId) {
  const task = getTaskById(taskId);
  if (!task) {
    return;
  }
  state.activeTaskDetailId = taskId;
  const batchItems = Array.isArray(task.batch_items) && task.batch_items.length
    ? `<div class="detail-subtask-list">${task.batch_items.map((item, index) => `
        <article class="detail-subtask-item detail-subtask-item-${item.status}">
          <div class="detail-subtask-head">
            <strong>${index + 1}. ${escapeHtml(item.relative_path)}</strong>
            ${labeledBadge(item.status)}
          </div>
          <div class="detail-subtask-progress">
            <div class="progress"><span style="width:${item.progress || 0}%"></span></div>
            <span>${(item.progress || 0).toFixed(2)}%</span>
          </div>
          ${item.error_message ? `<p class="danger">${escapeHtml(item.error_message)}</p>` : ""}
        </article>
      `).join("")}</div>`
    : "<p>-</p>";
  const batchPaths = Array.isArray(task.batch_paths) && task.batch_paths.length
    ? `<ul class="detail-path-list">${task.batch_paths.map((path) => `<li>${escapeHtml(path)}</li>`).join("")}</ul>`
    : "<p>-</p>";
  const totalCount = Array.isArray(task.batch_paths) && task.batch_paths.length ? task.batch_paths.length : 1;
  const completedCount = Math.min(task.completed_count || 0, totalCount);
  document.getElementById("task-detail-body").innerHTML = `
    <div class="detail-grid">
      <div><strong>状态</strong><p>${statusLabel(task.status)}</p></div>
      <div><strong>相对路径</strong><p>${escapeHtml(task.relative_path)}</p></div>
      <div><strong>上传模式</strong><p>${isBatchTask(task) ? "媒体组上传" : "单文件上传"}</p></div>
      <div><strong>完成数</strong><p>${completedCount} / ${totalCount}</p></div>
      <div><strong>进度</strong><p>${(task.progress || 0).toFixed(2)}%</p></div>
      <div><strong>批量文件数</strong><p>${totalCount}</p></div>
      <div><strong>绝对路径</strong><p>${escapeHtml(task.absolute_path)}</p></div>
      <div><strong>更新时间</strong><p>${formatDateTime(task.updated_at)}</p></div>
      <div><strong>创建时间</strong><p>${formatDateTime(task.created_at)}</p></div>
      <div><strong>任务 ID</strong><p>${escapeHtml(task.id)}</p></div>
      <div><strong>目录 ID</strong><p>${escapeHtml(task.folder_id)}</p></div>
      <div><strong>频道 ID</strong><p>${escapeHtml(task.channel_id)}</p></div>
      <details class="detail-block detail-panel" ${task.batch_items?.length ? "open" : ""}>
        <summary>组内文件详情</summary>
        <div class="detail-panel-body">${batchItems}</div>
      </details>
      <details class="detail-block detail-panel">
        <summary>Caption</summary>
        <div class="detail-panel-body"><p>${escapeHtml(task.caption || "-")}</p></div>
      </details>
      <details class="detail-block detail-panel" ${task.error_message ? "open" : ""}>
        <summary>错误信息</summary>
        <div class="detail-panel-body"><p>${escapeHtml(task.error_message || "-")}</p></div>
      </details>
      <details class="detail-block detail-panel">
        <summary>批量路径</summary>
        <div class="detail-panel-body">${batchPaths}</div>
      </details>
    </div>
  `;
  document.getElementById("task-detail-dialog").showModal();
}

export async function copyTaskField(field) {
  const task = getTaskById(state.activeTaskDetailId);
  if (!task) return;

  const value = field === "id" ? task.id : task.absolute_path;
  try {
    await navigator.clipboard.writeText(value);
    pushToast(field === "id" ? "任务 ID 已复制" : "文件路径已复制", "success");
  } catch {
    pushToast("复制失败，请手动复制", "error");
  }
}

export async function retryUploadTask(taskId) {
  await api(`/api/uploads/${taskId}/retry`, { method: "POST", headers: {} });
  state.selectedUploadTaskIds.delete(taskId);
  await loadUploads();
  pushToast("任务已重新加入队列", "success");
}

export function toggleTaskGroup() {}
