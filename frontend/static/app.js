import { state } from "./store.js";
import { api, debounce, pushToast, setGlobalBanner, setText } from "./utils.js";
import {
  clearFileSelection,
  handlePreview,
  loadFiles,
  renderFiles,
  resetCurrentSubdir,
  selectSubdir,
  selectVisibleFiles,
  stepPreview,
} from "./files.js";
import {
  fillChannelForm,
  fillFolderForm,
  loadSettings,
  resetChannelForm,
  resetFolderForm,
  setSettingsFormDirty,
  submitJson,
} from "./settings.js";
import {
  clearTaskSelection,
  clearUploads,
  copyTaskField,
  deleteSelectedUploads,
  loadUploads,
  renderUploads,
  retrySelectedUploads,
  retryUploadTask,
  selectVisibleTasks,
  showTaskDetail,
  toggleTaskGroup,
} from "./uploads.js";

let refreshTimer = null;

function setBrowserSidebarCollapsed(collapsed) {
  const layout = document.querySelector(".browser-layout");
  if (!layout) return;
  layout.classList.toggle("is-collapsed", collapsed);

  const labels = collapsed
    ? { text: "展开目录", expanded: "false" }
    : { text: "收起目录", expanded: "true" };

  ["toggle-browser-sidebar", "toggle-browser-sidebar-toolbar"].forEach((id) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.textContent = labels.text;
    button.setAttribute("aria-expanded", labels.expanded);
  });
}

function initBrowserSidebarToggle() {
  const toolbar = document.querySelector(".file-toolbar");
  const sidebarToggle = document.getElementById("toggle-browser-sidebar");
  if (!toolbar || !sidebarToggle) return;

  let toolbarToggle = document.getElementById("toggle-browser-sidebar-toolbar");
  if (!toolbarToggle) {
    toolbarToggle = sidebarToggle.cloneNode(true);
    toolbarToggle.id = "toggle-browser-sidebar-toolbar";
    toolbarToggle.classList.remove("browser-sidebar-toggle");
    toolbar.insertBefore(toolbarToggle, document.getElementById("browser-refresh"));
  }

  const handleToggle = () => {
    const layout = document.querySelector(".browser-layout");
    if (!layout) return;
    setBrowserSidebarCollapsed(!layout.classList.contains("is-collapsed"));
  };

  sidebarToggle.addEventListener("click", handleToggle);
  toolbarToggle.addEventListener("click", handleToggle);
  setBrowserSidebarCollapsed(false);
}

function initFileColumnControl() {
  const control = document.getElementById("file-columns");
  if (!control) return;

  const saved = window.localStorage.getItem("tgup:file-columns");
  const normalized = ["3", "4", "5", "6"].includes(saved || "") ? Number(saved) : state.fileColumns;
  state.fileColumns = normalized;
  control.value = String(normalized);

  control.addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.fileColumns = [3, 4, 5, 6].includes(value) ? value : 4;
    window.localStorage.setItem("tgup:file-columns", String(state.fileColumns));
    renderFiles();
  });
}

function renderAccessScreen() {
  const screen = document.getElementById("access-screen");
  const shouldLock = state.access.enabled && !state.access.authorized;
  screen.classList.toggle("hidden", !shouldLock);
}

async function loadAccessStatus() {
  const payload = await api("/api/access/status");
  state.access.enabled = payload.enabled;
  state.access.authorized = payload.authorized;
  state.access.checked = true;
  renderAccessScreen();
  return payload;
}

function renderTabs() {
  document.querySelectorAll("[data-tab-trigger]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tabTrigger === state.activeTab);
  });
  document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.tabPanel === state.activeTab);
  });
}

function renderSettingsTabs() {
  document.querySelectorAll("[data-settings-tab-trigger]").forEach((button) => {
    button.classList.toggle("active", button.dataset.settingsTabTrigger === state.activeSettingsTab);
  });
  document.querySelectorAll("[data-settings-tab-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.settingsTabPanel === state.activeSettingsTab);
  });
}

function setActiveTab(tab) {
  state.activeTab = tab;
  renderTabs();
  refreshPolling();
}

function setActiveSettingsTab(tab) {
  state.activeSettingsTab = tab;
  renderSettingsTabs();
}

async function refreshDashboard() {
  if (state.access.enabled && !state.access.authorized) {
    return;
  }
  setText("global-status", "正在同步页面数据…");
  try {
    await Promise.all([loadSettings(), loadUploads()]);
    if (state.selectedFolderId) {
      await loadFiles(state.selectedFolderId, false);
    }
    if (!state.ui.errors.settings && !state.ui.errors.uploads && !state.ui.errors.files) {
      setGlobalBanner("");
    }
  } finally {
    setText("global-status", "已同步");
  }
}

async function refreshActiveTabData() {
  if (state.access.enabled && !state.access.authorized) {
    return;
  }
  if (document.hidden) {
    return;
  }

  setText("global-status", "正在同步页面数据…");
  try {
    if (state.activeTab === "settings") {
      return;
    }
    if (state.activeTab === "uploads") {
      await loadUploads();
      return;
    }
  } finally {
    setText("global-status", "已同步");
  }
}

function refreshPolling() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (state.activeTab !== "uploads") {
    return;
  }
  refreshTimer = window.setInterval(async () => {
    await refreshActiveTabData();
  }, 5000);
}

async function handlePanelAction(targetId) {
  if (targetId === "retry-files" && state.selectedFolderId) {
    await loadFiles(state.selectedFolderId, false);
    return;
  }
  if (targetId === "retry-scan-files" && state.selectedFolderId) {
    await api(`/api/folders/${state.selectedFolderId}/scan`, { method: "POST", headers: {} });
    await loadFiles(state.selectedFolderId, false);
    await loadUploads();
    return;
  }
  if (targetId === "retry-uploads") {
    await loadUploads();
  }
}

async function handleAction(event) {
  const button = event.target.closest("button[data-action]");
  if (button) {
    const { action, id } = button.dataset;
    if (action === "edit-channel") {
      fillChannelForm(id);
      return;
    }
    if (action === "delete-channel") {
      await api(`/api/channels/${id}`, { method: "DELETE", headers: {} });
      await loadSettings();
      pushToast("频道已删除", "success");
      return;
    }
    if (action === "edit-folder") {
      fillFolderForm(id);
      return;
    }
    if (action === "delete-folder") {
      await api(`/api/folders/${id}`, { method: "DELETE", headers: {} });
      if (state.selectedFolderId === id) {
        state.selectedFolderId = "";
        state.files = [];
        state.selectedFiles.clear();
      }
      await loadSettings();
      renderFiles();
      pushToast("目录配置已删除", "success");
      return;
    }
    if (action === "browse-folder") {
      document.getElementById("browser-folder").value = id;
      await loadFiles(id);
      return;
    }
    if (action === "scan-folder") {
      await api(`/api/folders/${id}/scan`, { method: "POST", headers: {} });
      await loadUploads();
      if (state.selectedFolderId === id) {
        await loadFiles(id, false);
      }
      pushToast("目录扫描已触发", "success");
      return;
    }
    if (action === "retry-upload") {
      await retryUploadTask(id);
      return;
    }
    if (action === "task-detail") {
      showTaskDetail(id);
      return;
    }
  }

  const groupToggle = event.target.closest("[data-group-toggle]");
  if (groupToggle) {
    toggleTaskGroup(groupToggle.dataset.groupToggle);
  }
}

function wireEvents() {
  initBrowserSidebarToggle();
  initFileColumnControl();
  const debouncedFileSearch = debounce((value) => {
    state.fileSearch = value.trim().toLowerCase();
    renderFiles();
  });
  const debouncedTaskSearch = debounce((value) => {
    state.taskSearch = value.trim();
    renderUploads();
  });

  document.getElementById("refresh-all").addEventListener("click", refreshDashboard);
  document.getElementById("access-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    setText("access-login-error", "");
    try {
      await submitJson("/api/access/login", {
        password: document.getElementById("access-login-password").value,
      });
      document.getElementById("access-login-password").value = "";
      state.access.authorized = true;
      renderAccessScreen();
      await refreshDashboard();
      pushToast("访问验证成功", "success");
    } catch (error) {
      setText("access-login-error", error.message);
    }
  });
  document.querySelectorAll("[data-tab-trigger]").forEach((button) => {
    button.addEventListener("click", () => {
      setActiveTab(button.dataset.tabTrigger);
    });
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshPolling();
    }
  });
  document.querySelectorAll("[data-settings-tab-trigger]").forEach((button) => {
    button.addEventListener("click", () => {
      setActiveSettingsTab(button.dataset.settingsTabTrigger);
    });
  });

  document.getElementById("api-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      api_id: Number(document.getElementById("api-id").value),
      api_hash: document.getElementById("api-hash").value,
      phone_number: document.getElementById("phone-number").value,
    };
    await submitJson("/api/auth/start", payload);
    setSettingsFormDirty("api", false);
    await loadSettings();
    pushToast("登录请求已发送", "success");
  });

  document.getElementById("code-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitJson("/api/auth/code", { code: document.getElementById("code-input").value });
    document.getElementById("code-input").value = "";
    await loadSettings();
    pushToast("验证码已提交", "success");
  });

  document.getElementById("password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitJson("/api/auth/password", { password: document.getElementById("password-input").value });
    document.getElementById("password-input").value = "";
    await loadSettings();
    pushToast("二次验证密码已提交", "success");
  });
  document.getElementById("access-password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitJson("/api/access/password", {
      password: document.getElementById("access-password-input").value,
    });
    document.getElementById("access-password-input").value = "";
    setSettingsFormDirty("access", false);
    await loadAccessStatus();
    await loadSettings();
    pushToast("访问密码已保存", "success");
  });
  document.getElementById("access-password-clear").addEventListener("click", async () => {
    await api("/api/access/password", { method: "DELETE", headers: {} });
    setSettingsFormDirty("access", false);
    await loadAccessStatus();
    await loadSettings();
    pushToast("访问密码已清除", "success");
  });

  document.getElementById("channel-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: document.getElementById("channel-name").value,
      target: document.getElementById("channel-target").value,
      enabled: document.getElementById("channel-enabled").checked,
    };
    const channelId = document.getElementById("channel-id").value;
    await submitJson(channelId ? `/api/channels/${channelId}` : "/api/channels", payload, channelId ? "PUT" : "POST");
    resetChannelForm();
    setSettingsFormDirty("channel", false);
    await loadSettings();
    pushToast(channelId ? "频道已更新" : "频道已创建", "success");
  });

  document.getElementById("folder-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      name: document.getElementById("folder-name").value,
      path: document.getElementById("folder-path").value,
      channel_id: document.getElementById("folder-channel").value,
      auto_upload: document.getElementById("folder-auto").checked,
      scan_interval_seconds: Number(document.getElementById("folder-interval").value),
      post_upload_action: document.getElementById("folder-action").value,
      move_target_path: document.getElementById("folder-move-target").value,
      enabled: document.getElementById("folder-enabled").checked,
    };
    const folderId = document.getElementById("folder-id").value;
    await submitJson(folderId ? `/api/folders/${folderId}` : "/api/folders", payload, folderId ? "PUT" : "POST");
    resetFolderForm();
    setSettingsFormDirty("folder", false);
    await loadSettings();
    pushToast(folderId ? "目录已更新" : "目录已创建", "success");
  });

  document.getElementById("channel-reset").addEventListener("click", resetChannelForm);
  document.getElementById("folder-reset").addEventListener("click", resetFolderForm);
  document.getElementById("api-form").addEventListener("input", () => {
    setSettingsFormDirty("api", true);
  });
  document.getElementById("channel-form").addEventListener("input", () => {
    setSettingsFormDirty("channel", true);
  });
  document.getElementById("channel-form").addEventListener("change", () => {
    setSettingsFormDirty("channel", true);
  });
  document.getElementById("folder-form").addEventListener("input", () => {
    setSettingsFormDirty("folder", true);
  });
  document.getElementById("folder-form").addEventListener("change", () => {
    setSettingsFormDirty("folder", true);
  });
  document.getElementById("access-password-form").addEventListener("input", () => {
    setSettingsFormDirty("access", true);
  });

  document.getElementById("browser-folder").addEventListener("change", async (event) => {
    await loadFiles(event.target.value);
  });

  document.getElementById("file-type-filter").addEventListener("change", (event) => {
    state.fileTypeFilter = event.target.value;
    renderFiles();
  });

  document.getElementById("file-status-filter").addEventListener("change", (event) => {
    state.fileStatusFilter = event.target.value;
    renderFiles();
  });

  document.getElementById("file-scope-filter").addEventListener("change", (event) => {
    state.fileScopeFilter = event.target.value;
    renderFiles();
  });

  document.getElementById("file-search").addEventListener("input", (event) => {
    debouncedFileSearch(event.target.value);
  });

  document.getElementById("select-visible-files").addEventListener("click", () => {
    selectVisibleFiles();
  });

  document.getElementById("clear-file-selection").addEventListener("click", () => {
    clearFileSelection();
  });

  document.getElementById("reset-subdir").addEventListener("click", () => {
    resetCurrentSubdir();
  });

  document.getElementById("task-folder-filter").addEventListener("change", (event) => {
    state.taskFolderFilter = event.target.value;
    renderUploads();
  });

  document.getElementById("task-status-filter").addEventListener("change", (event) => {
    state.taskStatusFilter = event.target.value;
    renderUploads();
  });

  document.getElementById("task-sort").addEventListener("change", (event) => {
    state.taskSort = event.target.value;
    renderUploads();
  });

  document.getElementById("task-search").addEventListener("input", (event) => {
    debouncedTaskSearch(event.target.value);
  });

  document.getElementById("browser-scan").addEventListener("click", async () => {
    if (!state.selectedFolderId) return;
    await api(`/api/folders/${state.selectedFolderId}/scan`, { method: "POST", headers: {} });
    await loadFiles(state.selectedFolderId, false);
    await loadUploads();
    pushToast("正在刷新当前目录", "success");
  });
  document.getElementById("browser-refresh").addEventListener("click", async () => {
    if (!state.selectedFolderId) return;
    await loadFiles(state.selectedFolderId, false);
    pushToast("文件列表已刷新", "success");
  });

  document.getElementById("browser-upload").addEventListener("click", async () => {
    if (!state.selectedFolderId || state.selectedFiles.size === 0) return;
    await submitJson("/api/uploads/manual", {
      folder_id: state.selectedFolderId,
      relative_paths: Array.from(state.selectedFiles),
    });
    await loadUploads();
    pushToast("手动上传任务已创建", "success");
  });

  document.getElementById("clear-finished").addEventListener("click", async () => {
    await clearUploads("finished");
  });
  document.getElementById("upload-refresh").addEventListener("click", async () => {
    await loadUploads();
    pushToast("任务列表已刷新", "success");
  });

  document.getElementById("clear-failed").addEventListener("click", async () => {
    await clearUploads("failed");
  });

  document.getElementById("clear-all").addEventListener("click", async () => {
    await clearUploads("all");
  });

  document.getElementById("retry-selected").addEventListener("click", async () => {
    await retrySelectedUploads();
  });

  document.getElementById("delete-selected").addEventListener("click", async () => {
    await deleteSelectedUploads();
  });

  document.getElementById("select-visible-tasks").addEventListener("click", () => {
    selectVisibleTasks();
  });

  document.getElementById("clear-task-selection").addEventListener("click", () => {
    clearTaskSelection();
  });

  document.body.addEventListener("click", async (event) => {
    if (event.target.id && ["retry-files", "retry-scan-files", "retry-uploads"].includes(event.target.id)) {
      await handlePanelAction(event.target.id);
      return;
    }
    const subdirButton = event.target.closest("[data-subdir]");
    if (subdirButton) {
      selectSubdir(subdirButton.dataset.subdir);
      return;
    }
    if (event.target.closest("button[data-action]") || event.target.closest("[data-group-toggle]")) {
      await handleAction(event);
      return;
    }
    const preview = event.target.closest("[data-preview]");
    if (preview) {
      await handlePreview(preview.dataset.preview);
    }
  });

  document.body.addEventListener("mouseover", (event) => {
    const preview = event.target.closest(".preview");
    const video = preview?.querySelector("video");
    if (video) {
      video.play().catch(() => {});
    }
  });

  document.body.addEventListener("mouseout", (event) => {
    const preview = event.target.closest(".preview");
    const video = preview?.querySelector("video");
    if (video) {
      video.pause();
      video.currentTime = 0;
    }
  });

  document.body.addEventListener("change", (event) => {
    const target = event.target;
    if (target.matches("[data-file-select]")) {
      const filePath = target.dataset.fileSelect;
      if (target.checked) {
        state.selectedFiles.add(filePath);
      } else {
        state.selectedFiles.delete(filePath);
      }
      renderFiles();
    }
    if (target.matches("[data-task-select]")) {
      const taskId = target.dataset.taskSelect;
      if (target.checked) {
        state.selectedUploadTaskIds.add(taskId);
      } else {
        state.selectedUploadTaskIds.delete(taskId);
      }
      renderUploads();
    }
  });

  document.getElementById("preview-close").addEventListener("click", () => {
    document.getElementById("preview-dialog").close();
  });
  document.getElementById("preview-prev").addEventListener("click", () => {
    stepPreview(-1);
  });
  document.getElementById("preview-next").addEventListener("click", () => {
    stepPreview(1);
  });
  document.getElementById("task-detail-close").addEventListener("click", () => {
    document.getElementById("task-detail-dialog").close();
  });
  document.getElementById("copy-task-id").addEventListener("click", async () => {
    await copyTaskField("id");
  });
  document.getElementById("copy-task-path").addEventListener("click", async () => {
    await copyTaskField("path");
  });
  window.addEventListener("app-unauthorized", async () => {
    state.access.authorized = false;
    renderAccessScreen();
    setText("access-login-error", "访问已失效，请重新输入密码");
    await loadAccessStatus().catch(() => {});
  });
}

async function boot() {
  renderTabs();
  renderSettingsTabs();
  wireEvents();
  await loadAccessStatus();
  if (state.access.enabled && !state.access.authorized) {
    return;
  }
  await refreshDashboard();
  refreshPolling();
}

boot().catch((error) => {
  setText("login-error", error.message);
});
