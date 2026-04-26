import { state } from "./store.js";
import { api, debounce, escapeHtml, pushToast, setGlobalBanner, setText } from "./utils.js";
import {
  closePreview,
  clearFileSelection,
  handlePreview,
  initPreviewSize,
  loadFiles,
  renderFiles,
  resetCurrentSubdir,
  selectSubdir,
  setPreviewSize,
  selectVisibleFiles,
  syncVisibleFileSelectionUI,
  stepPreview,
  syncPreviewOnDialogClose,
  toggleDirectoryCollapse,
} from "./files.js";
import {
  fillBotApiAccountForm,
  fillChannelForm,
  fillFolderForm,
  loadSettings,
  resetChannelForm,
  resetBotApiAccountForm,
  resetFolderForm,
  setSettingsFormDirty,
  submitJson,
  syncBotDispatchControls,
  syncProxyControls,
  syncFolderMediaGroupControls,
  syncFolderUploadLimitControls,
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
  syncVisibleTaskSelectionUI,
  toggleTaskGroup,
} from "./uploads.js";

let refreshTimer = null;
let uploadSyncInFlight = false;
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

function openDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog && !dialog.open) {
    dialog.showModal();
  }
}

function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (dialog?.open) {
    dialog.close();
  }
}

function showBotSetupResultDialog({ title, summary, results = [] }) {
  const titleNode = document.getElementById("bot-setup-result-title");
  const summaryNode = document.getElementById("bot-setup-result-summary");
  const bodyNode = document.getElementById("bot-setup-result-body");
  if (!titleNode || !summaryNode || !bodyNode) return;
  titleNode.textContent = title || "Bot 接入结果";
  summaryNode.textContent = summary || "";
  bodyNode.innerHTML = results.length
    ? results.map((item) => `
      <article class="detail-subtask-item ${item.ok ? "detail-subtask-item-uploaded" : "detail-subtask-item-failed"}">
        <div class="detail-subtask-head">
          <strong>${escapeHtml(item.bot_name || item.bot_username || item.bot_api_account_id || "未知 Bot")}</strong>
          <span class="badge ${item.ok ? "uploaded" : "failed"}">${item.ok ? "成功" : "失败"}</span>
        </div>
        <div class="meta upload-task-meta">
          ${item.bot_username ? `<span class="settings-item-pill">@${escapeHtml(item.bot_username)}</span>` : ""}
          ${item.target_kind ? `<span class="settings-item-pill">${escapeHtml(item.target_kind)}</span>` : ""}
          ${item.invited !== undefined ? `<span class="settings-item-pill">${item.invited ? "已邀请/加入" : "已在目标内"}</span>` : ""}
          ${item.promoted !== undefined ? `<span class="settings-item-pill">${item.promoted ? "已授予管理员" : "未授予管理员"}</span>` : ""}
        </div>
        ${item.error ? `<p class="detail-error-line"><span class="danger">${escapeHtml(item.error)}</span></p>` : ""}
      </article>
    `).join("")
    : `<p class="muted">没有可展示的结果。</p>`;
  openDialog("bot-setup-result-dialog");
}

function rememberChannelBotSetupSummary(channelId, payload = {}) {
  if (!channelId) return;
  const failedNames = (payload.results || [])
    .filter((item) => !item.ok)
    .map((item) => item.bot_name || item.bot_username || item.bot_api_account_id || "未知 Bot");
  state.channelBotSetupSummaryByChannel[channelId] = {
    total: payload.total || (payload.results || []).length || 0,
    success_count: payload.success_count || ((payload.results || []).filter((item) => item.ok).length),
    failed_names: failedNames,
    at: new Date().toLocaleString(),
  };
}

async function runChannelBotSetup(channelId, { allBots = false, botApiAccountId = "", adminTitle = "Uploader Bot" } = {}) {
  const endpoint = allBots
    ? `/api/channels/${channelId}/setup-all-bots`
    : `/api/channels/${channelId}/setup-bot`;
  const result = await submitJson(endpoint, {
    bot_api_account_id: botApiAccountId,
    admin_title: adminTitle,
  });
  const results = allBots ? (result.results || []) : [result];
  const successCount = allBots
    ? (Array.isArray(result.results) ? result.results.filter((item) => item.ok).length : (result.success_count || 0))
    : 1;
  rememberChannelBotSetupSummary(channelId, {
    total: allBots ? (result.total || results.length) : 1,
    success_count: successCount,
    results,
  });
  await loadSettings();
  showBotSetupResultDialog({
    title: `${allBots ? "批量接入结果" : "频道接入结果"} · ${result.channel_name}`,
    summary: allBots
      ? `共处理 ${result.total || 0} 个 Bot，成功 ${successCount} 个。`
      : `${result.bot_name || result.bot_username} 已处理完成。`,
    results,
  });
  pushToast(
    allBots
      ? `已处理 ${result.total || 0} 个 Bot，成功 ${successCount} 个`
      : `Bot 已完成接入：${result.bot_username} -> ${result.channel_name}（${result.promoted ? "已授予管理员" : "未授予管理员"}）`,
    successCount ? "success" : "info",
  );
  return result;
}

function initManagedDialog(id, { onClose } = {}) {
  const dialog = document.getElementById(id);
  if (!dialog) return;

  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    const clickedBackdrop = event.target === dialog
      && (event.clientX < rect.left
        || event.clientX > rect.right
        || event.clientY < rect.top
        || event.clientY > rect.bottom);
    if (clickedBackdrop) {
      dialog.close();
    }
  });

  dialog.addEventListener("close", () => {
    onClose?.();
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

function syncTopbarOffset() {
  const topbar = document.querySelector(".topbar");
  const root = document.documentElement;
  if (!topbar || !root) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height);
  const offset = Math.max(154, height + 16);
  root.style.setProperty("--topbar-offset", `${offset}px`);
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
    if (uploadSyncInFlight) {
      return;
    }
    uploadSyncInFlight = true;
    try {
      await syncUploadProgress();
    } catch {
      // Keep the current UI stable; manual refresh remains available.
    } finally {
      uploadSyncInFlight = false;
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
      openDialog("channel-dialog");
      return;
    }
    if (action === "setup-channel-bot") {
      const channel = state.settings?.channels?.find((item) => item.id === id);
      const defaultTitle = "Uploader Bot";
      const adminTitle = window.prompt("请输入管理员称号（可留默认）", defaultTitle);
      if (adminTitle === null) {
        return;
      }
      await runChannelBotSetup(id, {
        allBots: false,
        botApiAccountId: channel?.bot_api_account_id || "",
        adminTitle: adminTitle.trim() || defaultTitle,
      });
      return;
    }
    if (action === "setup-channel-all-bots") {
      const defaultTitle = "Uploader Bot";
      const adminTitle = window.prompt("请输入管理员称号（将用于全部 Bot，可留默认）", defaultTitle);
      if (adminTitle === null) {
        return;
      }
      await runChannelBotSetup(id, {
        allBots: true,
        adminTitle: adminTitle.trim() || defaultTitle,
      });
      return;
    }
    if (action === "edit-bot-api-account") {
      fillBotApiAccountForm(id);
      openDialog("bot-api-dialog");
      return;
    }
    if (action === "test-bot-api-account") {
      const result = await api(`/api/settings/bot-api/accounts/${id}/test`, { method: "POST", body: "{}" });
      document.getElementById("bot-api-test-result").textContent = `连接成功：${result.first_name || result.username || "Bot"} (${result.id || "unknown"})`;
      pushToast("Bot API 连通性测试成功", "success");
      return;
    }
    if (action === "delete-bot-api-account") {
      await api(`/api/settings/bot-api/accounts/${id}`, { method: "DELETE", headers: {} });
      resetBotApiAccountForm();
      await loadSettings();
      pushToast("Bot API 账号已删除", "success");
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
      openDialog("folder-dialog");
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
  syncTopbarOffset();
  initBrowserSidebarToggle();
  initFileColumnControl();
  initTaskColumnControl();
  syncFolderMediaGroupControls();
  initPreviewSize();
  initManagedDialog("channel-dialog", { onClose: () => resetChannelForm() });
  initManagedDialog("bot-api-dialog", { onClose: () => resetBotApiAccountForm() });
  initManagedDialog("bot-setup-result-dialog");
  initManagedDialog("folder-dialog", { onClose: () => resetFolderForm() });

  const debouncedFileSearch = debounce((value) => {
    state.fileSearch = value.trim().toLowerCase();
    state.filePage = 1;
    if (state.selectedFolderId) {
      void loadFiles(state.selectedFolderId, false);
    } else {
      renderFiles();
    }
  });
  const debouncedTaskSearch = debounce((value) => {
    state.taskSearch = value.trim();
    state.uploadPage = 1;
    void loadUploads();
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
      syncTopbarOffset();
      refreshPolling();
      void refreshActiveTabData();
    }
  });

  window.addEventListener("resize", syncTopbarOffset);
  window.addEventListener("layout:topbar-sync", syncTopbarOffset);

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

  document.getElementById("save-api-settings").addEventListener("click", async () => {
    const payload = {
      api_id: document.getElementById("api-id").value.trim() ? Number(document.getElementById("api-id").value) : null,
      api_hash: document.getElementById("api-hash").value.trim(),
      phone_number: document.getElementById("phone-number").value.trim(),
    };
    await submitJson("/api/settings/api", payload);
    setSettingsFormDirty("api", false);
    await loadSettings();
    pushToast("登录配置已保存", "success");
  });

  document.getElementById("proxy-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      enabled: document.getElementById("proxy-enabled").checked,
      type: document.getElementById("proxy-type").value,
      host: document.getElementById("proxy-host").value.trim(),
      port: Number(document.getElementById("proxy-port").value) || 1080,
      username: document.getElementById("proxy-username").value.trim(),
      password: document.getElementById("proxy-password").value.trim(),
    };
    await submitJson("/api/settings/proxy", payload);
    setSettingsFormDirty("proxy", false);
    await loadSettings();
    pushToast(payload.enabled ? "代理已启用并保存" : "代理已关闭并保存", "success");
  });

  document.getElementById("bot-api-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const accountId = document.getElementById("bot-api-account-id").value;
    const payload = {
      name: document.getElementById("bot-api-account-name").value.trim(),
      bot_token: document.getElementById("bot-api-token").value.trim(),
      send_rate_limit_per_minute: Number(document.getElementById("bot-api-rate-limit").value) || 20,
      send_rate_limit_per_channel_per_minute: Number(document.getElementById("bot-api-channel-rate-limit").value) || 10,
      send_jitter_min_ms: Number(document.getElementById("bot-api-jitter-min").value) || 0,
      send_jitter_max_ms: Number(document.getElementById("bot-api-jitter-max").value) || 0,
      auto_slowdown_enabled: document.getElementById("bot-api-auto-slowdown-enabled").checked,
      auto_slowdown_factor_percent: Number(document.getElementById("bot-api-auto-slowdown-factor").value) || 50,
      auto_slowdown_duration_seconds: Number(document.getElementById("bot-api-auto-slowdown-duration").value) || 600,
      enabled: document.getElementById("bot-api-enabled").checked,
    };
    const resultNode = document.getElementById("bot-api-test-result");
    resultNode.textContent = "保存中...";
    try {
      const endpoint = accountId
        ? `/api/settings/bot-api/accounts/${accountId}`
        : "/api/settings/bot-api/accounts";
      const savedAccount = await submitJson(endpoint, payload, accountId ? "PUT" : "POST");
      resultNode.textContent = "测试中...";
      if (!savedAccount) {
        throw new Error("保存成功，但未找到对应账号");
      }
      const result = await api(`/api/settings/bot-api/accounts/${savedAccount.id}/test`, { method: "POST", body: "{}" });
      resetBotApiAccountForm();
      closeDialog("bot-api-dialog");
      setSettingsFormDirty("botApi", false);
      await loadSettings();
      resultNode.textContent = `连接成功：${result.first_name || result.username || "Bot"} (${result.id || "unknown"})`;
      pushToast(accountId ? "Bot API 账号已更新并测试成功" : "Bot API 账号已创建并测试成功", "success");
    } catch (error) {
      await loadSettings();
      resultNode.textContent = `连接失败：${error.message}`;
      pushToast(`Bot API 保存或测试失败：${error.message}`, "error");
    }
  });

  document.getElementById("test-bot-api-connection").addEventListener("click", async () => {
    const accountId = document.getElementById("bot-api-account-id").value;
    const resultNode = document.getElementById("bot-api-test-result");
    if (!accountId) {
      resultNode.textContent = "请先保存账号后再测试";
      pushToast("请先保存 Bot API 账号", "error");
      return;
    }
    resultNode.textContent = "测试中...";
    try {
      const result = await api(`/api/settings/bot-api/accounts/${accountId}/test`, { method: "POST", body: "{}" });
      resultNode.textContent = `连接成功：${result.first_name || result.username || "Bot"} (${result.id || "unknown"})`;
      pushToast("Bot API 连通性测试成功", "success");
    } catch (error) {
      resultNode.textContent = `连接失败：${error.message}`;
      pushToast(`Bot API 测试失败：${error.message}`, "error");
    }
  });

  document.getElementById("save-bot-dispatch").addEventListener("click", async () => {
    const payload = {
      mode: document.getElementById("bot-dispatch-mode").value,
      default_bot_api_account_id: document.getElementById("default-bot-api-account").value,
      smart_queue_scheduling_enabled: document.getElementById("smart-queue-scheduling-enabled").checked,
    };
    await submitJson("/api/settings/bot-api/dispatch", payload);
    setSettingsFormDirty("botApi", false);
    await loadSettings();
    syncTopbarOffset();
    pushToast("Bot 调度设置已保存", "success");
  });

  document.getElementById("reset-bot-api-account").addEventListener("click", () => {
    resetBotApiAccountForm();
  });

  document.getElementById("open-bot-api-dialog").addEventListener("click", () => {
    resetBotApiAccountForm();
    openDialog("bot-api-dialog");
  });

  document.getElementById("open-channel-dialog").addEventListener("click", () => {
    resetChannelForm();
    openDialog("channel-dialog");
  });

  document.getElementById("open-folder-dialog").addEventListener("click", () => {
    resetFolderForm();
    openDialog("folder-dialog");
  });

  document.getElementById("channel-setup-bound-bot").addEventListener("click", async () => {
    const channelId = document.getElementById("channel-id").value;
    const botApiAccountId = document.getElementById("channel-bot-api-account").value;
    if (!channelId) {
      pushToast("请先保存频道后再执行接入", "error");
      return;
    }
    const defaultTitle = "Uploader Bot";
    const adminTitle = window.prompt("请输入管理员称号（可留默认）", defaultTitle);
    if (adminTitle === null) {
      return;
    }
    await runChannelBotSetup(channelId, {
      allBots: false,
      botApiAccountId,
      adminTitle: adminTitle.trim() || defaultTitle,
    });
  });

  document.getElementById("channel-setup-all-bots").addEventListener("click", async () => {
    const channelId = document.getElementById("channel-id").value;
    if (!channelId) {
      pushToast("请先保存频道后再执行接入", "error");
      return;
    }
    const defaultTitle = "Uploader Bot";
    const adminTitle = window.prompt("请输入管理员称号（将用于全部 Bot，可留默认）", defaultTitle);
    if (adminTitle === null) {
      return;
    }
    await runChannelBotSetup(channelId, {
      allBots: true,
      adminTitle: adminTitle.trim() || defaultTitle,
    });
  });

  document.getElementById("channel-dialog-close").addEventListener("click", () => closeDialog("channel-dialog"));
  document.getElementById("bot-api-dialog-close").addEventListener("click", () => closeDialog("bot-api-dialog"));
  document.getElementById("bot-setup-result-close").addEventListener("click", () => closeDialog("bot-setup-result-dialog"));
  document.getElementById("folder-dialog-close").addEventListener("click", () => closeDialog("folder-dialog"));

  document.getElementById("normalize-folder-limits")?.addEventListener("click", async () => {
    const result = await api("/api/settings/folders/normalize-limits", { method: "POST", body: "{}" });
    await loadSettings();
    if (!result.changed) {
      pushToast("当前没有需要修正的目录", "success");
      return;
    }
    const suffix = result.changed > 1 ? `，共修正 ${result.changed} 个目录` : "";
    pushToast(`目录上传上限已按当前自动策略修正${suffix}`, "success");
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
      bot_api_account_id: document.getElementById("channel-bot-api-account").value,
    };
    const channelId = document.getElementById("channel-id").value;
    await submitJson(channelId ? `/api/channels/${channelId}` : "/api/channels", payload, channelId ? "PUT" : "POST");
    resetChannelForm();
    closeDialog("channel-dialog");
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
        excluded_subdirs: document.getElementById("folder-excluded-subdirs").value
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean),
        auto_upload: document.getElementById("folder-auto").checked,
        media_group_upload: document.getElementById("folder-media-group").checked,
        media_group_filename_similarity: document.getElementById("folder-media-group-similarity").checked,
        media_group_similarity_threshold: Number(document.getElementById("folder-media-group-threshold").value) || 80,
        split_large_video_upload: document.getElementById("folder-split-large-video").checked,
        upload_size_limit_mb: Number(document.getElementById("folder-upload-limit").value) || 2048,
        segment_target_size_mb: Number(document.getElementById("folder-segment-target").value) || 1900,
        scan_interval_seconds: Number(document.getElementById("folder-interval").value),
        min_stable_seconds: Number(document.getElementById("folder-min-stable-seconds").value) || 0,
        post_upload_action: document.getElementById("folder-action").value,
        move_target_path: document.getElementById("folder-move-target").value,
        enabled: document.getElementById("folder-enabled").checked,
    };
    const folderId = document.getElementById("folder-id").value;
    await submitJson(folderId ? `/api/folders/${folderId}` : "/api/folders", payload, folderId ? "PUT" : "POST");
    resetFolderForm();
    closeDialog("folder-dialog");
    setSettingsFormDirty("folder", false);
    await loadSettings();
    pushToast(folderId ? "目录已更新" : "目录已创建", "success");
  });

  document.getElementById("channel-reset").addEventListener("click", resetChannelForm);
  document.getElementById("folder-reset").addEventListener("click", resetFolderForm);
  document.getElementById("api-form").addEventListener("input", () => setSettingsFormDirty("api", true));
  document.getElementById("folder-upload-limit").addEventListener("change", syncFolderUploadLimitControls);
  document.getElementById("proxy-form").addEventListener("input", () => setSettingsFormDirty("proxy", true));
  document.getElementById("proxy-form").addEventListener("change", () => setSettingsFormDirty("proxy", true));
  document.getElementById("bot-api-form").addEventListener("input", () => setSettingsFormDirty("botApi", true));
  document.getElementById("bot-api-form").addEventListener("change", () => setSettingsFormDirty("botApi", true));
  document.getElementById("bot-dispatch-mode").addEventListener("change", syncBotDispatchControls);
  document.getElementById("channel-form").addEventListener("input", () => setSettingsFormDirty("channel", true));
  document.getElementById("channel-form").addEventListener("change", () => setSettingsFormDirty("channel", true));
  document.getElementById("folder-form").addEventListener("input", () => setSettingsFormDirty("folder", true));
  document.getElementById("folder-form").addEventListener("change", () => setSettingsFormDirty("folder", true));
  document.getElementById("folder-media-group").addEventListener("change", syncFolderMediaGroupControls);
  document.getElementById("folder-media-group-similarity").addEventListener("change", syncFolderMediaGroupControls);
  document.getElementById("proxy-enabled").addEventListener("change", syncProxyControls);
  document.getElementById("access-password-form").addEventListener("input", () => setSettingsFormDirty("access", true));

  document.getElementById("browser-folder").addEventListener("change", async (event) => {
    await loadFiles(event.target.value);
  });

  document.getElementById("file-type-filter").addEventListener("change", (event) => {
    state.fileTypeFilter = event.target.value;
    state.filePage = 1;
    if (state.selectedFolderId) {
      void loadFiles(state.selectedFolderId, false);
    }
  });

  document.getElementById("file-status-filter").addEventListener("change", (event) => {
    state.fileStatusFilter = event.target.value;
    state.filePage = 1;
    if (state.selectedFolderId) {
      void loadFiles(state.selectedFolderId, false);
    }
  });

  document.getElementById("file-scope-filter").addEventListener("change", (event) => {
    state.fileScopeFilter = event.target.value;
    state.filePage = 1;
    if (state.selectedFolderId) {
      void loadFiles(state.selectedFolderId, false);
    }
  });

  document.getElementById("file-search").addEventListener("input", (event) => {
    debouncedFileSearch(event.target.value);
  });

  document.getElementById("file-page-size").addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.filePageSize = [10, 20, 50, 100].includes(value) ? value : 10;
    state.filePage = 1;
    if (state.selectedFolderId) {
      void loadFiles(state.selectedFolderId, false);
    }
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
    void loadUploads();
  });

  document.getElementById("task-status-filter").addEventListener("change", (event) => {
    state.taskStatusFilter = event.target.value;
    state.uploadPage = 1;
    void loadUploads();
  });

  document.getElementById("task-error-filter").addEventListener("change", (event) => {
    state.taskErrorCategoryFilter = event.target.value;
    state.uploadPage = 1;
    void loadUploads();
  });

  document.getElementById("task-scheduling-filter").addEventListener("change", (event) => {
    state.taskSchedulingFilter = event.target.value;
    state.uploadPage = 1;
    void loadUploads();
  });

  document.getElementById("task-sort").addEventListener("change", (event) => {
    state.taskSort = event.target.value;
    state.uploadPage = 1;
    void loadUploads();
  });

  document.getElementById("task-search").addEventListener("input", (event) => {
    debouncedTaskSearch(event.target.value);
  });

  document.getElementById("upload-page-size").addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.uploadPageSize = [10, 20, 50, 100].includes(value) ? value : 10;
    state.uploadPage = 1;
    void loadUploads();
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
    state.selectedFiles.clear();
    renderFiles();
    await loadFiles(state.selectedFolderId, false);
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
    const scrollButton = event.target.closest("[data-scroll-target]");
    if (scrollButton) {
      const target = document.getElementById(scrollButton.dataset.scrollTarget);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

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
      syncVisibleFileSelectionUI();
    }
    if (target.matches("[data-file-page]")) {
      const value = Number(target.dataset.filePage);
      if (Number.isFinite(value) && value > 0) {
        state.filePage = value;
        if (state.selectedFolderId) {
          void loadFiles(state.selectedFolderId, false);
        }
      }
    }
    if (target.matches("[data-task-page]")) {
      const value = Number(target.dataset.taskPage);
      if (Number.isFinite(value) && value > 0) {
        state.uploadPage = value;
        void loadUploads();
      }
    }
    if (target.matches("[data-task-select]")) {
      const taskId = target.dataset.taskSelect;
      if (target.checked) {
        state.selectedUploadTaskIds.add(taskId);
      } else {
        state.selectedUploadTaskIds.delete(taskId);
      }
      syncVisibleTaskSelectionUI();
    }
  });

  document.getElementById("preview-close").addEventListener("click", () => {
    closePreview();
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

  document.getElementById("preview-dialog").addEventListener("close", () => {
    syncPreviewOnDialogClose();
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
