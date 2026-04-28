import { getTaskById, isRetryableTask, state } from "./store.js";
import {
  api,
  escapeHtml,
  formatDateTime,
  initOverflowMarquee,
  labeledBadge,
  marqueeText,
  pushToast,
  setPanelFeedback,
  statusLabel,
  taskSkeleton,
  parseUploadError,
  translateUploadError,
} from "./utils.js";
import { submitJson } from "./settings.js";

let lastUploadStatsMarkup = "";
let lastUploadPaginationMarkup = "";
let lastRenderedVisibleTaskIds = new Set();
let lastUploadSummaryText = "";

function setNodeHtmlIfChanged(node, html) {
  if (node && node.innerHTML !== html) {
    node.innerHTML = html;
  }
}

function setNodeTextIfChanged(node, text) {
  if (node && node.textContent !== text) {
    node.textContent = text;
  }
}

function emptyStats() {
  return {
    total: 0,
    pending: 0,
    uploading: 0,
    uploaded: 0,
    failed: 0,
    locked: 0,
    stabilizing: 0,
    upload_speed_bytes: 0,
  };
}

function formatUploadSpeed(bytesPerSecond = 0) {
  const value = Number(bytesPerSecond) || 0;
  if (value <= 0) {
    return "0 KB/s";
  }
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const decimals = size >= 100 || index === 0 ? 0 : size >= 10 ? 1 : 2;
  return `${size.toFixed(decimals)} ${units[index]}`;
}

export async function loadUploads() {
  state.ui.loading.uploads = true;
  state.ui.errors.uploads = "";
  renderUploads();
  try {
    const params = new URLSearchParams({
      page: String(state.uploadPage || 1),
      page_size: String(state.uploadPageSize || 10),
      folder_id: state.taskFolderFilter || "all",
      status: state.taskStatusFilter || "all",
      error_category: state.taskErrorCategoryFilter || "all",
      scheduling: state.taskSchedulingFilter || "all",
      search: state.taskSearch || "",
      sort: state.taskSort || "updated_desc",
    });
    const uploads = await api(`/api/uploads?${params.toString()}`);
    state.uploads = uploads.items || [];
    state.uploadPagination = uploads.pagination || state.uploadPagination;
    state.uploadPage = uploads.pagination?.page || state.uploadPage;
    state.uploadPageSize = uploads.pagination?.page_size || state.uploadPageSize;
    state.uploadTotalAll = uploads.total_all || 0;
    state.selectedUploadTaskIds = new Set(
      [...state.selectedUploadTaskIds].filter((taskId) => state.uploads.some((item) => item.id === taskId)),
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
  const stats = state.uploadStats || emptyStats();
  return (stats.uploading || 0) > 0 || (stats.pending || 0) > 0;
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
      <strong>${formatUploadSpeed(stats.upload_speed_bytes)}</strong>
      <span>总速度</span>
    </article>
    <article class="top-stat-card">
      <strong>${stats.pending}</strong>
      <span>待处理</span>
    </article>
    <article class="top-stat-card">
      <strong>${stats.stabilizing || 0}</strong>
      <span>等待稳定</span>
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
  if (topStats && markup !== lastUploadStatsMarkup) {
    setNodeHtmlIfChanged(topStats, markup);
    lastUploadStatsMarkup = markup;
  }
}

function taskBatchSummary(task) {
  const batchCount = Array.isArray(task.batch_paths) ? task.batch_paths.length : 0;
  if (task.task_kind === "split_video") {
    return `视频分段任务 · 共 ${batchCount} 段`;
  }
  if (batchCount > 1) {
    return `媒体组任务 · 共 ${batchCount} 个文件`;
  }
  return "单文件任务";
}

function taskDisplayName(task) {
  const path = String(task.relative_path || task.source_relative_path || "").trim();
  if (!path) {
    return task.task_kind === "split_video" ? "视频分段任务" : "未命名任务";
  }
  const normalized = path.replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || normalized;
}

function taskDisplayPath(task) {
  const path = String(task.relative_path || task.source_relative_path || "").trim();
  if (!path) {
    return "根目录";
  }
  const normalized = path.replaceAll("\\", "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 1) {
    return "根目录";
  }
  return parts.slice(0, -1).join(" / ");
}

function taskTypeBadge(task) {
  if (task.task_kind === "split_video") {
    return `<span class="upload-task-kind upload-task-kind-split">分段</span>`;
  }
  if (isBatchTask(task)) {
    return `<span class="upload-task-kind upload-task-kind-batch">媒体组</span>`;
  }
  return `<span class="upload-task-kind upload-task-kind-single">单文件</span>`;
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
  if (task.status === "stabilizing") {
    return "文件等待稳定";
  }
  if (task.status === "pending") {
    return "等待上传";
  }
  return `进度: ${(task.progress || 0).toFixed(2)}%`;
}

function taskProgressMarkup(task) {
  return `
    <div class="progress" data-progress-track="${task.id}"><span style="width:${task.progress || 0}%"></span></div>
    <div class="upload-task-progress-text muted" data-progress-text="${task.id}">${escapeHtml(taskCompletionText(task))}</div>
  `;
}

function errorTag(errorMessage) {
  const parsed = parseUploadError(errorMessage);
  if (!parsed.message) return "";
  const labelMap = {
    rate_limit: "限流",
    permission: "权限",
    not_found: "不存在",
    size_limit: "超上限",
    format: "格式",
    network: "网络",
    auth: "认证",
    server_error: "服务端错误",
    local_rate_limit: "本地限频",
    smart_skip: "智能后移",
    batch_error: "媒体组",
    session_error: "会话",
    upload_error: "上传失败",
    unknown: "错误",
  };
  return `<span class="error-tag error-tag-${parsed.category}">${escapeHtml(labelMap[parsed.category] || "错误")}</span>`;
}

function taskSchedulingBanner(task) {
  const parsed = parseUploadError(task.error_message || "");
  if (!["smart_skip", "local_rate_limit"].includes(parsed.category)) {
    return "";
  }
  const label = parsed.category === "smart_skip" ? "智能调度" : "Bot 冷却中";
  return `<div class="upload-task-banner upload-task-banner-${parsed.category}"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(parsed.message)}</span></div>`;
}

function taskBotLabel(task) {
  if (!task.bot_api_account_id) {
    return "默认";
  }
  const account = state.settings?.bot_api_accounts?.find((item) => item.id === task.bot_api_account_id);
  return account?.name || task.bot_api_account_id;
}

function taskUploaderLabel(task) {
  if (task.uploader_engine === "bot") {
    return "Bot";
  }
  if (task.uploader_engine === "telethon") {
    return "Telethon";
  }
  return "待分配";
}

function metaPill(label, value, tone = "") {
  return `<span class="upload-task-pill ${tone}">${escapeHtml(label)}: <strong>${escapeHtml(value)}</strong></span>`;
}

function taskMetaMarkup(task) {
  return `
    ${metaPill("进度", taskCompletionText(task))}
    ${metaPill("模式", task.task_kind === "split_video" ? "视频分段上传" : (isBatchTask(task) ? "媒体组上传" : "单文件上传"))}
    ${metaPill("引擎", taskUploaderLabel(task), "info")}
    ${task.bot_api_account_id ? metaPill("实际 Bot", taskBotLabel(task), "accent") : ""}
  `;
}

function taskErrorMarkup(task) {
  if (!task.error_message) {
    return "";
  }
  return `
    <div class="upload-task-error-line">
      ${errorTag(task.error_message)}
      ${marqueeText(translateUploadError(task.error_message), "danger upload-task-error-text")}
    </div>
  `;
}

function taskActionMarkup(task) {
  return `
    <button data-action="task-detail" data-id="${task.id}" class="ghost" type="button">详情</button>
    ${isRetryableTask(task) ? `<button data-action="retry-upload" data-id="${task.id}" class="ghost" type="button">重试</button>` : ""}
  `;
}

function renderTaskCard(task) {
  return `
    <article class="item upload-task-item upload-task-item-${task.status}" data-task-id="${task.id}">
      <div class="upload-task-head-row">
        <div class="upload-task-title-block">
          <div class="upload-task-title-top">
            ${taskTypeBadge(task)}
            <h3 class="upload-task-title">${marqueeText(taskDisplayName(task), "upload-task-title-text", task.relative_path)}</h3>
          </div>
          <div class="upload-task-head-meta">
            <div class="upload-task-subtitle-group">
              <p class="muted upload-task-subtitle">${escapeHtml(taskBatchSummary(task))}</p>
              <div class="upload-task-path-line">
                <span class="upload-task-path-label">路径</span>
                ${marqueeText(taskDisplayPath(task), "upload-task-path-text", task.relative_path)}
              </div>
            </div>
            <div class="upload-task-top-actions">
              <span data-task-status-badge="${task.id}">${labeledBadge(task.status)}</span>
              <label class="inline-check upload-task-check">
                <input type="checkbox" data-task-select="${task.id}" ${state.selectedUploadTaskIds.has(task.id) ? "checked" : ""}>
              </label>
            </div>
          </div>
        </div>
      </div>
      <div data-task-banner="${task.id}">${taskSchedulingBanner(task)}</div>
      ${taskProgressMarkup(task)}
      <div class="meta upload-task-meta upload-task-meta-strong" data-task-meta="${task.id}">
        ${taskMetaMarkup(task)}
      </div>
      <div data-task-error="${task.id}">${taskErrorMarkup(task)}</div>
      <div class="upload-task-footer">
        <div class="upload-task-time muted" data-task-updated-at="${task.id}">更新于 ${escapeHtml(formatDateTime(task.updated_at))}</div>
        <div class="item-actions upload-task-actions" data-task-actions="${task.id}">
          ${taskActionMarkup(task)}
        </div>
      </div>
    </article>
  `;
}

function updateTaskCard(task) {
  const card = document.querySelector(`[data-task-id="${task.id}"]`);
  if (!card) return;
  card.className = `item upload-task-item upload-task-item-${task.status}`;
  const badge = card.querySelector(`[data-task-status-badge="${task.id}"]`);
  const badgeMarkup = labeledBadge(task.status);
  setNodeHtmlIfChanged(badge, badgeMarkup);
  const progressBar = card.querySelector(`[data-progress-track="${task.id}"] span`);
  const progressWidth = `${task.progress || 0}%`;
  if (progressBar && progressBar.style.width !== progressWidth) {
    progressBar.style.width = progressWidth;
  }
  const progressText = card.querySelector(`[data-progress-text="${task.id}"]`);
  setNodeTextIfChanged(progressText, taskCompletionText(task));
  const banner = card.querySelector(`[data-task-banner="${task.id}"]`);
  setNodeHtmlIfChanged(banner, taskSchedulingBanner(task));
  const meta = card.querySelector(`[data-task-meta="${task.id}"]`);
  setNodeHtmlIfChanged(meta, taskMetaMarkup(task));
  const error = card.querySelector(`[data-task-error="${task.id}"]`);
  setNodeHtmlIfChanged(error, taskErrorMarkup(task));
  const updatedAt = card.querySelector(`[data-task-updated-at="${task.id}"]`);
  setNodeTextIfChanged(updatedAt, `更新于 ${formatDateTime(task.updated_at)}`);
  const actions = card.querySelector(`[data-task-actions="${task.id}"]`);
  setNodeHtmlIfChanged(actions, taskActionMarkup(task));
}

function renderUploadPagination(pagination) {
  const container = document.getElementById("upload-pagination");
  if (!container) return;
  const totalItems = pagination.total_items ?? pagination.totalItems ?? 0;
  const totalPages = pagination.total_pages ?? pagination.totalPages ?? 1;
  const page = pagination.page ?? 1;
  const start = pagination.start ?? 0;
  const end = pagination.end ?? 0;
  if (totalItems === 0) {
    container.classList.add("hidden");
    setNodeHtmlIfChanged(container, "");
    lastUploadPaginationMarkup = "";
    return;
  }
  container.classList.remove("hidden");
  const markup = `
    <div class="pagination-summary">第 ${page}/${totalPages} 页，显示 ${start}-${end} / ${totalItems}</div>
    <div class="pagination-actions">
      <button class="ghost" type="button" data-task-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="ghost" type="button" data-task-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>下一页</button>
    </div>
  `;
  if (markup !== lastUploadPaginationMarkup) {
    setNodeHtmlIfChanged(container, markup);
    lastUploadPaginationMarkup = markup;
  }
}

function uploadPaginationSignature(pagination = {}) {
  return [
    pagination.page ?? 1,
    pagination.page_size ?? pagination.pageSize ?? 10,
    pagination.total_pages ?? pagination.totalPages ?? 1,
    pagination.total_items ?? pagination.totalItems ?? 0,
    pagination.start ?? 0,
    pagination.end ?? 0,
    state.uploadTotalAll ?? 0,
  ].join("|");
}

function renderUploadSummary(tasks = state.uploads, pagination = state.uploadPagination) {
  const summary = document.getElementById("upload-summary");
  if (!summary) return;
  const pageInfo = pagination || { total_items: tasks.length };
  const selectedCount = tasks.filter((task) => state.selectedUploadTaskIds.has(task.id)).length;
  const nextSummaryText = `总任务 ${state.uploadTotalAll} 个，筛选后 ${pageInfo.total_items || tasks.length} 个，本页 ${tasks.length} 个，已选中 ${selectedCount} 个`;
  if (nextSummaryText !== lastUploadSummaryText) {
    summary.textContent = nextSummaryText;
    lastUploadSummaryText = nextSummaryText;
  }
}

export function syncVisibleTaskSelectionUI() {
  state.uploads.forEach((task) => {
    const checkbox = document.querySelector(`[data-task-select="${task.id}"]`);
    if (checkbox) {
      checkbox.checked = state.selectedUploadTaskIds.has(task.id);
    }
  });
  renderUploadSummary();
}

export function renderUploads() {
  renderUploadStats();
  const tasks = state.uploads;
  const pagination = state.uploadPagination || {
    page: state.uploadPage || 1,
    page_size: state.uploadPageSize || 10,
    total_pages: 1,
    total_items: tasks.length,
    start: tasks.length ? 1 : 0,
    end: tasks.length,
  };
  const pageItems = tasks;
  lastRenderedVisibleTaskIds = new Set(tasks.map((task) => task.id));
  const container = document.getElementById("upload-list");
  const pageSizeControl = document.getElementById("upload-page-size");
  container.style.setProperty("--task-columns", String(state.taskColumns));
  if (pageSizeControl) {
    pageSizeControl.value = String(state.uploadPageSize);
  }
  const errorFilterControl = document.getElementById("task-error-filter");
  if (errorFilterControl) {
    errorFilterControl.value = state.taskErrorCategoryFilter;
  }
  renderUploadSummary(pageItems, pagination);

  if (state.ui.loading.uploads) {
    const hasExistingTasks = pageItems.length > 0;
    renderUploadPagination(hasExistingTasks ? pagination : { totalItems: 0 });
    setPanelFeedback("upload-feedback", {
      visible: true,
      tone: "info",
      title: "正在加载任务列表",
      message: "任务列表正在刷新，当前内容先保持显示。",
    });
    const summary = document.getElementById("upload-summary");
    if (summary) {
      lastUploadSummaryText = hasExistingTasks ? "正在后台刷新任务列表…" : "正在同步任务列表...";
      summary.textContent = lastUploadSummaryText;
    }
    if (!hasExistingTasks) {
      setNodeHtmlIfChanged(container, taskSkeleton());
      return;
    }
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
    const summary = document.getElementById("upload-summary");
    if (summary) {
      lastUploadSummaryText = "任务列表加载失败";
      summary.textContent = lastUploadSummaryText;
    }
    setNodeHtmlIfChanged(container, "");
    return;
  }

  setPanelFeedback("upload-feedback", {
    visible: pageItems.length === 0,
    tone: "empty",
    title: "没有匹配的上传任务",
    message: state.uploadTotalAll
      ? "可以调整筛选条件，或者稍后等待新任务入队。"
      : "当前还没有上传任务，先去目录里发起手动上传吧。",
    actionLabel: state.uploadTotalAll ? "" : "刷新",
    actionId: state.uploadTotalAll ? "" : "retry-uploads",
  });
  renderUploadPagination(pagination);

  const listMarkup = pageItems.length
    ? `<div class="task-group-list upload-card-list">${pageItems.map((task) => renderTaskCard(task)).join("")}</div>`
    : `<p class="muted">当前筛选条件下没有任务。</p>`;
  setNodeHtmlIfChanged(container, listMarkup);
  initOverflowMarquee(container);
}

export async function syncUploadProgress() {
  const previousIds = state.uploads.map((task) => task.id).join("|");
  const previousPaginationSignature = uploadPaginationSignature(state.uploadPagination);
  const params = new URLSearchParams({
    page: String(state.uploadPage || 1),
    page_size: String(state.uploadPageSize || 10),
    folder_id: state.taskFolderFilter || "all",
    status: state.taskStatusFilter || "all",
    error_category: state.taskErrorCategoryFilter || "all",
    scheduling: state.taskSchedulingFilter || "all",
    search: state.taskSearch || "",
    sort: state.taskSort || "updated_desc",
  });
  const [uploads, stats] = await Promise.all([
    api(`/api/uploads?${params.toString()}`),
    api("/api/uploads/stats"),
  ]);
  state.uploads = uploads.items || [];
  state.uploadPagination = uploads.pagination || state.uploadPagination;
  state.uploadPage = uploads.pagination?.page || state.uploadPage;
  state.uploadPageSize = uploads.pagination?.page_size || state.uploadPageSize;
  state.uploadTotalAll = uploads.total_all || 0;
  state.uploadStats = stats;
  state.selectedUploadTaskIds = new Set(
    [...state.selectedUploadTaskIds].filter((taskId) => state.uploads.some((item) => item.id === taskId)),
  );
  renderUploadStats();
  const nextIds = state.uploads.map((task) => task.id).join("|");
  const nextPaginationSignature = uploadPaginationSignature(state.uploadPagination);
  if (previousIds !== nextIds || previousPaginationSignature !== nextPaginationSignature) {
    renderUploads();
    return;
  }

  state.uploads.forEach((task) => {
    if (!lastRenderedVisibleTaskIds.has(task.id)) return;
    updateTaskCard(task);
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
  const eligible = state.uploads
    .filter((task) => ["pending", "uploading", "failed", "locked", "stabilizing", "uploaded"].includes(task.status))
    .map((task) => task.id);
  state.selectedUploadTaskIds = new Set(eligible);
  syncVisibleTaskSelectionUI();
}

export function clearTaskSelection() {
  state.selectedUploadTaskIds.clear();
  syncVisibleTaskSelectionUI();
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
            <strong>${marqueeText(`${index + 1}. ${item.relative_path}`)}</strong>
            ${labeledBadge(item.status)}
          </div>
          <div class="detail-subtask-progress">
            <div class="progress"><span style="width:${item.progress || 0}%"></span></div>
            <span>${(item.progress || 0).toFixed(2)}%</span>
          </div>
           ${item.error_message ? `<p class="detail-error-line">${errorTag(item.error_message)}<span class="danger">${escapeHtml(translateUploadError(item.error_message))}</span></p>` : ""}
        </article>
      `).join("")}</div>`
    : "<p>-</p>";
  const batchPaths = Array.isArray(task.batch_paths) && task.batch_paths.length
    ? `<ul class="detail-path-list">${(task.task_kind === "split_video" && Array.isArray(task.batch_items) && task.batch_items.length
      ? task.batch_items.map((item) => item.relative_path)
      : task.batch_paths).map((path) => `<li>${marqueeText(path)}</li>`).join("")}</ul>`
    : "<p>-</p>";
  const totalCount = Array.isArray(task.batch_paths) && task.batch_paths.length ? task.batch_paths.length : 1;
  const completedCount = Math.min(task.completed_count || 0, totalCount);
  document.getElementById("task-detail-body").innerHTML = `
    <div class="detail-grid">
      <div><strong>状态</strong><p>${statusLabel(task.status)}</p></div>
      <div><strong>相对路径</strong><p>${marqueeText(task.source_relative_path || task.relative_path)}</p></div>
      <div><strong>上传模式</strong><p>${task.task_kind === "split_video" ? "视频分段上传" : isBatchTask(task) ? "媒体组上传" : "单文件上传"}</p></div>
      <div><strong>完成数</strong><p>${completedCount} / ${totalCount}</p></div>
      <div><strong>进度</strong><p>${(task.progress || 0).toFixed(2)}%</p></div>
      <div><strong>批量文件数</strong><p>${totalCount}</p></div>
      <div><strong>绝对路径</strong><p>${marqueeText(task.source_absolute_path || task.absolute_path)}</p></div>
      <div><strong>成组说明</strong><p>${escapeHtml(task.group_debug || "-")}</p></div>
      <div><strong>更新时间</strong><p>${formatDateTime(task.updated_at)}</p></div>
      <div><strong>创建时间</strong><p>${formatDateTime(task.created_at)}</p></div>
      <div><strong>任务 ID</strong><p>${escapeHtml(task.id)}</p></div>
      <div><strong>目录 ID</strong><p>${escapeHtml(task.folder_id)}</p></div>
      <div><strong>频道 ID</strong><p>${escapeHtml(task.channel_id)}</p></div>
      <div><strong>实际引擎</strong><p>${escapeHtml(taskUploaderLabel(task))}</p></div>
      <div><strong>实际 Bot</strong><p>${escapeHtml(taskBotLabel(task))}</p></div>
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
        <div class="detail-panel-body"><p class="detail-error-line">${task.error_message ? `${errorTag(task.error_message)}<span class="danger">${escapeHtml(translateUploadError(task.error_message))}</span>` : escapeHtml("-")}</p></div>
      </details>
      <details class="detail-block detail-panel">
        <summary>批量路径</summary>
        <div class="detail-panel-body">${batchPaths}</div>
      </details>
    </div>
  `;
  initOverflowMarquee(document.getElementById("task-detail-body"));
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
