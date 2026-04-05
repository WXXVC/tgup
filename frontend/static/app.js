import { state } from "./store.js";
import { api, debounce, pushToast, setGlobalBanner, setText } from "./utils.js";
import {
  clearFileSelection,
  handlePreview,
  initPreviewSize,
  loadFiles,
  renderFiles,
  resetCurrentSubdir,
  selectSubdir,
  setPreviewSize,
  selectVisibleFiles,
  stepPreview,
  toggleDirectoryCollapse,
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
  hasActiveUploadTasks,
  deleteSelectedUploads,
  loadUploadStats,
  loadUploads,
  renderUploads,
  retrySelectedUploads,
  retryUploadTask,
  selectVisibleTasks,
  showTaskDetail,
  syncUploadProgress,
  toggleTaskGroup,
} from "./uploads.js";

let refreshTimer = null;
const UPLOAD_POLL_INTERVAL_MS = 2500;

const TAB_ROUTE_MAP = {
  settings: "/setting",
  files: "/dir",
  uploads: "/upload",
};

function normalizePath(pathname = "/") {
  if (!pathname || pathname === "/") return "/";
  const normalized = pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return ["/login", "/dir", "/setting", "/upload"].includes(normalized) ? normalized : "/";
}

function getTabForPath(pathname) {
  const path = normalizePath(pathname);
  if (path === "/dir") return "files";
  if (path === "/upload") return "uploads";
  return "settings";
}

function pushRoute(pathname, { replace = false } = {}) {
  const nextPath = normalizePath(pathname);
  if (window.location.pathname !== nextPath) {
    window.history[replace ? "replaceState" : "pushState"]({}, "", nextPath);
  }
  state.routePath = nextPath;
}

function syncTabWithRoute(pathname = window.location.pathname) {
  const path = normalizePath(pathname);
  state.routePath = path;
  if (path !== "/login") {
    state.activeTab = getTabForPath(path);
  }
}

function applyRouteVisibility() {
  const isLoginRoute = state.routePath === "/login";
  document.querySelector(".topbar")?.classList.toggle("hidden", isLoginRoute);
  document.querySelector(".shell")?.classList.toggle("hidden", isLoginRoute);
}

function resolveRouteAfterAccess() {
  const currentPath = normalizePath(window.location.pathname);
  const requiresLogin = state.access.enabled && !state.access.authorized;

  if (currentPath === "/") {
    if (requiresLogin) {
      pushRoute("/login", { replace: true });
      return true;
    }
    pushRoute("/dir", { replace: true });
    syncTabWithRoute("/dir");
    return false;
  }

  if (!state.access.enabled && currentPath === "/login") {
    pushRoute("/dir", { replace: true });
    syncTabWithRoute("/dir");
    return false;
  }

  if (requiresLogin && currentPath !== "/login") {
    pushRoute("/login", { replace: true });
    return true;
  }

  if (!requiresLogin && currentPath === "/login") {
    pushRoute("/dir", { replace: true });
    syncTabWithRoute("/dir");
    return false;
  }

  syncTabWithRoute(currentPath);
  return currentPath === "/login";
}

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

function initTaskColumnControl() {
  const control = document.getElementById("task-columns");
  if (!control) return;

  const saved = window.localStorage.getItem("tgup:task-columns");
  const normalized = ["3", "4", "5", "6"].includes(saved || "") ? Number(saved) : state.taskColumns;
  state.taskColumns = normalized;
  control.value = String(normalized);

  control.addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.taskColumns = [3, 4, 5, 6].includes(value) ? value : 3;
    window.localStorage.setItem("tgup:task-columns", String(state.taskColumns));
    renderUploads();
  });
}

function renderAccessScreen() {
  const screen = document.getElementById("access-screen");
  const shouldLock = state.routePath === "/login" || (state.access.enabled && !state.access.authorized);
  screen.classList.toggle("hidden", !shouldLock);
  applyRouteVisibility();
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

async function setActiveTab(tab, options = {}) {
  const { updateHistory = true } = options;
  state.activeTab = tab;
  if (updateHistory) {
    pushRoute(TAB_ROUTE_MAP[tab] || "/setting");
  } else {
    state.routePath = TAB_ROUTE_MAP[tab] || "/setting";
  }
  renderTabs();
  renderAccessScreen();
  refreshPolling();
  if (state.access.enabled && !state.access.authorized) {
    return;
  }
  if (tab === "uploads") {
    await loadUploads();
    return;
  }
  if (tab === "files" && state.selectedFolderId && state.files.length === 0) {
    await loadFiles(state.selectedFolderId, false);
  }
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
    const jobs = [loadSettings(), loadUploadStats()];
    if (state.activeTab === "uploads") {
      jobs.push(loadUploads());
    }
    if (state.activeTab === "files" && state.selectedFolderId) {
      jobs.push(loadFiles(state.selectedFolderId, false));
    }
    await Promise.all(jobs);
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
    if (document.hidden || state.access.enabled && !state.access.authorized) {
      return;
    }
    if (!hasActiveUploadTasks()) {
      return;
    }
    try {
      await syncUploadProgress();
    } catch {
      // Keep the current UI stable; manual refresh remains available.
    }
  }, UPLOAD_POLL_INTERVAL_MS);
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
      await setActiveTab("files");
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
  initTaskColumnControl();
  initPreviewSize();

  const debouncedFileSearch = debounce((value) => {
    state.fileSearch = value.trim().toLowerCase();
    state.filePage = 1;
    renderFiles();
  });
  const debouncedTaskSearch = debounce((value) => {
    state.taskSearch = value.trim();
    state.uploadPage = 1;
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
      pushRoute("/dir", { replace: true });
      syncTabWithRoute("/dir");
      renderTabs();
      renderAccessScreen();
      await refreshDashboard();
      pushToast("访问验证成功", "success");
    } catch (error) {
      setText("access-login-error", error.message);
    }
  });

  document.querySelectorAll("[data-tab-trigger]").forEach((button) => {
    button.addEventListener("click", async () => {
      await setActiveTab(button.dataset.tabTrigger);
    });
  });

  window.addEventListener("popstate", async () => {
    syncTabWithRoute(window.location.pathname);
    renderTabs();
    renderAccessScreen();
    if (state.access.enabled && !state.access.authorized) {
      return;
    }
    if (state.activeTab === "uploads") {
      await loadUploads();
      return;
    }
    if (state.activeTab === "files" && state.selectedFolderId) {
      await loadFiles(state.selectedFolderId, false);
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshPolling();
      void refreshActiveTabData();
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
      api_id: document.getElementById("api-id").value.trim() ? Number(document.getElementById("api-id").value) : null,
      api_hash: document.getElementById("api-hash").value.trim(),
      phone_number: document.getElementById("phone-number").value.trim(),
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
    resolveRouteAfterAccess();
    renderTabs();
    renderAccessScreen();
    await loadSettings();
    pushToast("访问密码已保存", "success");
  });

  document.getElementById("access-password-clear").addEventListener("click", async () => {
    await api("/api/access/password", { method: "DELETE", headers: {} });
    setSettingsFormDirty("access", false);
    await loadAccessStatus();
    resolveRouteAfterAccess();
    renderTabs();
    renderAccessScreen();
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
      media_group_upload: document.getElementById("folder-media-group").checked,
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
  document.getElementById("api-form").addEventListener("input", () => setSettingsFormDirty("api", true));
  document.getElementById("channel-form").addEventListener("input", () => setSettingsFormDirty("channel", true));
  document.getElementById("channel-form").addEventListener("change", () => setSettingsFormDirty("channel", true));
  document.getElementById("folder-form").addEventListener("input", () => setSettingsFormDirty("folder", true));
  document.getElementById("folder-form").addEventListener("change", () => setSettingsFormDirty("folder", true));
  document.getElementById("access-password-form").addEventListener("input", () => setSettingsFormDirty("access", true));

  document.getElementById("browser-folder").addEventListener("change", async (event) => {
    await loadFiles(event.target.value);
  });

  document.getElementById("file-type-filter").addEventListener("change", (event) => {
    state.fileTypeFilter = event.target.value;
    state.filePage = 1;
    renderFiles();
  });

  document.getElementById("file-status-filter").addEventListener("change", (event) => {
    state.fileStatusFilter = event.target.value;
    state.filePage = 1;
    renderFiles();
  });

  document.getElementById("file-scope-filter").addEventListener("change", (event) => {
    state.fileScopeFilter = event.target.value;
    state.filePage = 1;
    renderFiles();
  });

  document.getElementById("file-search").addEventListener("input", (event) => {
    debouncedFileSearch(event.target.value);
  });

  document.getElementById("file-page-size").addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.filePageSize = [10, 20, 50, 100].includes(value) ? value : 10;
    state.filePage = 1;
    renderFiles();
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
    state.uploadPage = 1;
    renderUploads();
  });

  document.getElementById("task-status-filter").addEventListener("change", (event) => {
    state.taskStatusFilter = event.target.value;
    state.uploadPage = 1;
    renderUploads();
  });

  document.getElementById("task-sort").addEventListener("change", (event) => {
    state.taskSort = event.target.value;
    state.uploadPage = 1;
    renderUploads();
  });

  document.getElementById("task-search").addEventListener("input", (event) => {
    debouncedTaskSearch(event.target.value);
  });

  document.getElementById("upload-page-size").addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.uploadPageSize = [10, 20, 50, 100].includes(value) ? value : 10;
    state.uploadPage = 1;
    renderUploads();
  });

  document.getElementById("browser-scan").addEventListener("click", async () => {
    if (!state.selectedFolderId) return;
    await api(`/api/folders/${state.selectedFolderId}/scan`, { method: "POST", headers: {} });
    await loadFiles(state.selectedFolderId, false);
    await loadUploads();
    pushToast("当前目录已重新扫描", "success");
  });

  document.getElementById("browser-refresh").addEventListener("click", async () => {
    if (!state.selectedFolderId) return;
    await loadFiles(state.selectedFolderId, false);
    pushToast("文件列表已刷新", "success");
  });

  document.getElementById("browser-upload").addEventListener("click", async () => {
    if (!state.selectedFolderId || state.selectedFiles.size === 0) return;
    const selectedFiles = state.files.filter((file) => state.selectedFiles.has(file.relative_path));
    const hasUploadedFiles = selectedFiles.some((file) => file.status === "uploaded");
    if (hasUploadedFiles && !window.confirm("确认重新上传已上传文件吗？")) {
      return;
    }
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

    const toggleButton = event.target.closest("[data-tree-toggle]");
    if (toggleButton) {
      toggleDirectoryCollapse(toggleButton.dataset.treeToggle);
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
    if (target.matches("[data-file-page]")) {
      const value = Number(target.dataset.filePage);
      if (Number.isFinite(value) && value > 0) {
        state.filePage = value;
        renderFiles();
      }
    }
    if (target.matches("[data-task-page]")) {
      const value = Number(target.dataset.taskPage);
      if (Number.isFinite(value) && value > 0) {
        state.uploadPage = value;
        renderUploads();
      }
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
  document.querySelectorAll("[data-preview-size]").forEach((button) => {
    button.addEventListener("click", () => {
      setPreviewSize(button.dataset.previewSize);
    });
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
    pushRoute("/login", { replace: true });
    renderAccessScreen();
    setText("access-login-error", "访问已失效，请重新输入密码");
    await loadAccessStatus().catch(() => {});
  });
}

async function boot() {
  syncTabWithRoute(window.location.pathname);
  renderTabs();
  renderSettingsTabs();
  renderUploads();
  wireEvents();
  await loadAccessStatus();
  const lockRoute = resolveRouteAfterAccess();
  renderTabs();
  renderAccessScreen();
  if (lockRoute || (state.access.enabled && !state.access.authorized)) {
    return;
  }
  await refreshDashboard();
  refreshPolling();
}

boot().catch((error) => {
  setText("login-error", error.message);
});
