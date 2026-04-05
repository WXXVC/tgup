import { TASK_STATUS_ORDER } from "./constants.js";
import { getTaskById, isRetryableTask, isTerminalTask, state } from "./store.js";
import {
  api,
  escapeHtml,
  formatDateTime,
  labeledBadge,
  pushToast,
  setPanelFeedback,
  statusLabel,
  taskSkeleton,
} from "./utils.js";
import { submitJson } from "./settings.js";

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

function renderUploadStats() {
  const stats = state.uploadStats || {
    total: 0,
    pending: 0,
    uploading: 0,
    uploaded: 0,
    failed: 0,
    locked: 0,
  };
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
    .sort(compareTasks);
}

function groupTasksByStatus(tasks) {
  return TASK_STATUS_ORDER
    .map((status) => ({
      status,
      tasks: tasks.filter((task) => task.status === status),
    }))
    .filter((group) => group.tasks.length > 0);
}

export function renderUploads() {
  renderUploadStats();
  const tasks = filteredUploads();
  const groups = groupTasksByStatus(tasks);
  const container = document.getElementById("upload-list");
  const summary = document.getElementById("upload-summary");
  const selectedCount = tasks.filter((task) => state.selectedUploadTaskIds.has(task.id)).length;

  if (summary) {
    summary.textContent = `鍏?${state.uploads.length} 涓换鍔★紝绛涢€夊悗 ${tasks.length} 涓紝宸查€?${selectedCount} 涓?`;
  }

  if (state.ui.loading.uploads) {
    setPanelFeedback("upload-feedback", {
      visible: true,
      tone: "info",
      title: "正在加载任务列表",
      message: "任务状态和统计信息正在刷新。",
    });
    if (summary) {
      summary.textContent = "姝ｅ湪鍚屾浠诲姟鍒楄〃鈥?";
    }
    container.innerHTML = taskSkeleton();
    return;
  }

  if (state.ui.errors.uploads) {
    setPanelFeedback("upload-feedback", {
      visible: true,
      tone: "error",
      title: "任务列表加载失败",
      message: state.ui.errors.uploads,
      actionLabel: "重试",
      actionId: "retry-uploads",
    });
    if (summary) {
      summary.textContent = "浠诲姟鍒楄〃鍔犺浇澶辫触";
    }
    container.innerHTML = "";
    return;
  }

  setPanelFeedback("upload-feedback", {
    visible: tasks.length === 0,
    tone: "empty",
    title: "没有匹配的上传任务",
    message: state.uploads.length ? "可以调整筛选条件，或者稍后等待新任务入队。" : "当前还没有上传任务，先去目录里发起手动上传吧。",
    actionLabel: state.uploads.length ? "" : "刷新",
    actionId: state.uploads.length ? "" : "retry-uploads",
  });

  container.innerHTML = tasks.length
    ? groups.map((group) => {
      const collapsed = !!state.collapsedTaskGroups[group.status];
      return `
      <section class="task-group">
        <button class="task-group-toggle" data-group-toggle="${group.status}" type="button">
          <span>${statusLabel(group.status)} (${group.tasks.length})</span>
          <span>${collapsed ? "展开" : "收起"}</span>
        </button>
        ${collapsed ? "" : `<div class="task-group-list">${group.tasks.map((task) => `
      <article class="item upload-task-item">
        <div class="item-top">
          <div>
            <h3>${escapeHtml(task.relative_path)}</h3>
            <p class="muted">${escapeHtml(task.caption || "-")}</p>
          </div>
          ${labeledBadge(task.status)}
        </div>
        <div class="meta upload-task-meta">
          <label class="inline-check">
            <input type="checkbox" data-task-select="${task.id}" ${state.selectedUploadTaskIds.has(task.id) ? "checked" : ""}>
            <span>选中</span>
          </label>
          <span>目录: ${escapeHtml(task.folder_id)}</span>
          <span>频道: ${escapeHtml(task.channel_id)}</span>
          <span>更新时间: ${new Date(task.updated_at * 1000).toLocaleString()}</span>
          <span>批量文件: ${Array.isArray(task.batch_paths) ? task.batch_paths.length : 1}</span>
        </div>
        <div class="progress"><span style="width:${task.progress || 0}%"></span></div>
        <div class="meta upload-task-meta upload-task-meta-strong">
          <span>进度: ${(task.progress || 0).toFixed(2)}%</span>
          ${task.error_message ? `<span class="danger">${escapeHtml(task.error_message)}</span>` : ""}
        </div>
        <div class="item-actions">
          <button data-action="task-detail" data-id="${task.id}" class="ghost" type="button">详情</button>
          ${isRetryableTask(task) ? `<button data-action="retry-upload" data-id="${task.id}" class="ghost" type="button">重试</button>` : ""}
        </div>
      </article>
    `).join("")}</div>`}
      </section>`;
    }).join("")
    : `<p class="muted">当前筛选条件下没有任务。</p>`;
}

export async function clearUploads(scope) {
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
  const deletableIds = [...state.selectedUploadTaskIds].filter((taskId) => isTerminalTask(getTaskById(taskId)));
  if (deletableIds.length === 0) {
    pushToast("当前选中的任务没有可清理项", "info");
    return;
  }
  await api("/api/uploads/delete-batch", {
    method: "DELETE",
    body: JSON.stringify({ task_ids: deletableIds }),
  });
  state.selectedUploadTaskIds.clear();
  await loadUploads();
  pushToast("已清理选中的终态任务", "success");
}

export async function retrySelectedUploads() {
  if (state.selectedUploadTaskIds.size === 0) {
    return;
  }
  const retryableIds = [...state.selectedUploadTaskIds].filter((taskId) => isRetryableTask(getTaskById(taskId)));
  if (retryableIds.length === 0) {
    pushToast("当前选中的任务没有可重试项", "info");
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
  const eligible = filteredUploads()
    .filter((task) => ["failed", "locked", "uploaded"].includes(task.status))
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
  document.getElementById("task-detail-body").innerHTML = `
    <div class="detail-grid">
      <div><strong>任务 ID</strong><p>${escapeHtml(task.id)}</p></div>
      <div><strong>状态</strong><p>${statusLabel(task.status)}</p></div>
      <div><strong>目录 ID</strong><p>${escapeHtml(task.folder_id)}</p></div>
      <div><strong>频道 ID</strong><p>${escapeHtml(task.channel_id)}</p></div>
      <div><strong>相对路径</strong><p>${escapeHtml(task.relative_path)}</p></div>
      <div><strong>绝对路径</strong><p>${escapeHtml(task.absolute_path)}</p></div>
      <div><strong>创建时间</strong><p>${formatDateTime(task.created_at)}</p></div>
      <div><strong>更新时间</strong><p>${formatDateTime(task.updated_at)}</p></div>
      <div><strong>进度</strong><p>${(task.progress || 0).toFixed(2)}%</p></div>
      <div><strong>批量文件数</strong><p>${Array.isArray(task.batch_paths) ? task.batch_paths.length : 1}</p></div>
      <div class="detail-block"><strong>Caption</strong><p>${escapeHtml(task.caption || "-")}</p></div>
      <div class="detail-block"><strong>错误信息</strong><p>${escapeHtml(task.error_message || "-")}</p></div>
      <div class="detail-block"><strong>批量路径</strong><p>${escapeHtml((task.batch_paths || []).join("\n") || "-")}</p></div>
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

export function toggleTaskGroup(status) {
  state.collapsedTaskGroups[status] = !state.collapsedTaskGroups[status];
  renderUploads();
}
