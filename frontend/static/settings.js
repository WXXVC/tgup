import { state } from "./store.js";
import {
  api,
  escapeHtml,
  labeledBadge,
  setGlobalBanner,
  setText,
  statusLabel,
} from "./utils.js";

let lastSettingsPayloadSignature = "";
const settingsMarkupCache = {
  loginMeta: "",
  defaultBotOptions: "",
  channelBotOptions: "",
  folderChannelOptions: "",
  browserFolderOptions: "",
  taskFolderFilterOptions: "",
  channelList: "",
  folderList: "",
  botAccountList: "",
};

function setHtmlIfChanged(id, html) {
  const node = document.getElementById(id);
  if (!node) return;
  if (node.innerHTML !== html) {
    node.innerHTML = html;
  }
}

function renderLoginMeta(login) {
  const botReady = Number(login.bot_ready_accounts || 0);
  const botEnabled = Number(login.bot_enabled_accounts || 0);
  const telethonReady = login.large_file_ready === "true";
  const markup = `
    <span class="settings-item-pill">小文件: ${botReady > 0 ? `可用 (${botReady}/${botEnabled} Bot)` : (telethonReady ? "由 Telethon 兜底" : "不可用")}</span>
    <span class="settings-item-pill">大文件: ${telethonReady ? "Telethon 已就绪" : "Telethon 未登录"}</span>
  `;
  if (markup !== settingsMarkupCache.loginMeta) {
    setHtmlIfChanged("login-meta", markup);
    settingsMarkupCache.loginMeta = markup;
  }
}

function renderBotApiSettingsSection(settings, { hydrateForm = true } = {}) {
  const accounts = settings.bot_api_accounts || [];
  const dispatchMode = settings.bot_dispatch_mode || "single";
  if (hydrateForm) {
    document.getElementById("bot-dispatch-mode").value = dispatchMode;
    const defaultBotOptions = `<option value="">请选择默认 Bot</option>${accounts
      .map((account) => `<option value="${account.id}">${escapeHtml(account.name)}</option>`)
      .join("")}`;
    if (defaultBotOptions !== settingsMarkupCache.defaultBotOptions) {
      setHtmlIfChanged("default-bot-api-account", defaultBotOptions);
      settingsMarkupCache.defaultBotOptions = defaultBotOptions;
    }
    document.getElementById("default-bot-api-account").value = settings.default_bot_api_account_id || "";
    document.getElementById("smart-queue-scheduling-enabled").checked = !!settings.smart_queue_scheduling_enabled;
    resetBotApiAccountForm(false);
    renderBotApiAccountList(accounts);
    syncBotDispatchControls();
  } else {
    renderBotApiAccountList(accounts);
    syncBotDispatchControls();
  }

  const botOptions = `<option value="">未绑定 / 由默认策略决定</option>${accounts
    .map((account) => `<option value="${account.id}">${escapeHtml(account.name)}${account.enabled ? "" : "（已停用）"}</option>`)
    .join("")}`;
  if (botOptions !== settingsMarkupCache.channelBotOptions) {
    setHtmlIfChanged("channel-bot-api-account", botOptions);
    settingsMarkupCache.channelBotOptions = botOptions;
  }

  if (shouldHydrateSettingsForm("channel")) {
    document.getElementById("channel-bot-api-account").value = "";
  }
}

export async function submitJson(url, payload, method = "POST") {
  return api(url, { method, body: JSON.stringify(payload) });
}

export function syncProxyControls() {
  const enabled = !!document.getElementById("proxy-enabled")?.checked;
  ["proxy-type", "proxy-host", "proxy-port", "proxy-username", "proxy-password"].forEach((id) => {
    const field = document.getElementById(id);
    if (field) {
      field.disabled = !enabled;
    }
  });
}

export function syncUploadEngineControls() {
  const codeForm = document.getElementById("code-form");
  const passwordForm = document.getElementById("password-form");
  const authHint = document.getElementById("auth-engine-hint");
  const botApiHint = document.getElementById("bot-api-engine-hint");
  if (authHint) {
    authHint.textContent = "系统处于自动混合上传模式：50 MB 及以下优先走 Bot Token；若未配置可用 Bot，则自动回退到 Telethon。超过 50 MB 或包含大文件的媒体组自动走 Telethon。";
  }
  if (botApiHint) {
    botApiHint.textContent = "这里的 Bot 账号会优先负责 50 MB 及以下文件上传；如果没有可用 Bot，小文件也会自动回退到 Telethon。超过 50 MB 的文件和含大文件媒体组会自动走 Telethon。";
  }
  codeForm?.classList.remove("hidden");
  passwordForm?.classList.remove("hidden");
  const proxyHint = document.getElementById("proxy-scope-hint");
  if (proxyHint) {
    proxyHint.textContent = "当前代理将同时用于官方 Bot API HTTP 通信和 Telethon 登录/大文件上传连接。";
  }
  syncBotDispatchControls();
  syncFolderUploadLimitControls();
}

export function syncBotDispatchControls() {
  const mode = document.getElementById("bot-dispatch-mode")?.value || "single";
  const defaultField = document.getElementById("default-bot-api-account")?.closest("label");
  const channelBindingField = document.getElementById("channel-bot-binding-field");
  if (defaultField) {
    defaultField.classList.toggle("hidden", mode !== "single");
  }
  if (channelBindingField) {
    channelBindingField.classList.toggle("hidden", mode !== "channel_bound");
  }
}

export function syncFolderUploadLimitControls() {
  const settings = state.settings;
  if (!settings) return;
  const limits = settings.engine_limits || {};
  const maxUploadSizeMb = limits.max_upload_size_mb || 4096;
  const defaultUploadSizeMb = limits.default_upload_size_mb || 2048;
  const defaultSegmentTargetMb = limits.default_segment_target_mb || 1900;
  const uploadLimitInput = document.getElementById("folder-upload-limit");
  const segmentTargetInput = document.getElementById("folder-segment-target");
  const hint = document.getElementById("folder-upload-limit-hint");
  if (uploadLimitInput) {
    uploadLimitInput.max = String(maxUploadSizeMb);
    if (!state.ui.dirtyForms.folder && !document.getElementById("folder-id")?.value) {
      uploadLimitInput.value = String(defaultUploadSizeMb);
    }
    if (Number(uploadLimitInput.value) > maxUploadSizeMb) {
      uploadLimitInput.value = String(maxUploadSizeMb);
    }
  }
  if (segmentTargetInput) {
    segmentTargetInput.max = String(maxUploadSizeMb);
    if (!state.ui.dirtyForms.folder && !document.getElementById("folder-id")?.value) {
      segmentTargetInput.value = String(defaultSegmentTargetMb);
    }
    if (Number(segmentTargetInput.value) >= maxUploadSizeMb) {
      segmentTargetInput.value = String(Math.max(100, Math.min(defaultSegmentTargetMb, maxUploadSizeMb - 1)));
    }
  }
  if (hint) {
    hint.textContent = limits.description || "当前自动上传策略会决定可设置的最大上传上限。";
  }
}

export function renderEngineCapabilityCard() {
  const settings = state.settings;
  if (!settings) return;
  const badge = document.getElementById("engine-capability-badge");
  if (!badge) {
    renderFolderEngineWarnings();
    return;
  }
  const limits = settings.engine_limits || {};
  const engine = limits.engine || "hybrid";
  setText("engine-capability-badge", engine === "hybrid" ? "自动混合模式" : engine);
  setText("engine-limit-max", `最大单文件: ${limits.max_upload_size_mb || "-"} MB`);
  setText("engine-limit-default", `默认上传上限: ${limits.default_upload_size_mb || "-"} MB`);
  setText("engine-limit-segment", `默认分段目标: ${limits.default_segment_target_mb || "-"} MB`);
  setText("engine-limit-description", limits.description || "当前引擎能力说明将在这里显示。");
  renderFolderEngineWarnings();
}

export function renderFolderEngineWarnings() {
  const warnings = state.settings?.folder_engine_warnings || { count: 0, items: [] };
  const banner = document.getElementById("engine-warning-banner");
  const actions = document.getElementById("engine-warning-actions");
  if (!banner) return;
  if (!warnings.count) {
    banner.classList.add("hidden");
    banner.textContent = "";
    actions?.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  banner.textContent = `当前自动上传策略下有 ${warnings.count} 个目录配置超出上传上限，建议尽快调整。`;
  actions?.classList.remove("hidden");
}

function folderEngineWarning(folderId) {
  const items = state.settings?.folder_engine_warnings?.items || [];
  return items.find((item) => item.folder_id === folderId) || null;
}

export function syncFolderMediaGroupControls() {
  const mediaGroupEnabled = !!document.getElementById("folder-media-group")?.checked;
  const similarityEnabled = mediaGroupEnabled && !!document.getElementById("folder-media-group-similarity")?.checked;
  const similarityToggle = document.getElementById("folder-media-group-similarity");
  const thresholdInput = document.getElementById("folder-media-group-threshold");

  if (similarityToggle) {
    similarityToggle.disabled = !mediaGroupEnabled;
  }
  if (thresholdInput) {
    thresholdInput.disabled = !similarityEnabled;
  }
}

export function setSettingsFormDirty(formName, dirty = true) {
  if (Object.hasOwn(state.ui.dirtyForms, formName)) {
    state.ui.dirtyForms[formName] = dirty;
  }
}

function shouldHydrateSettingsForm(formName) {
  return !state.ui.dirtyForms[formName];
}

export async function loadSettings(forceFull = false) {
  let shouldRenderAfterLoad = true;
  const needFull = forceFull || state.activeTab === "settings";
  state.ui.loading.settings = true;
  state.ui.errors.settings = "";
  if (!state.settings) {
    if (needFull) {
      renderSettings();
    } else {
      renderSettingsLite();
    }
  }
  try {
    const payload = await api(needFull ? "/api/settings" : "/api/settings/summary");
    const nextSignature = JSON.stringify({
      settings: payload.settings,
      login: payload.login,
    });
    const payloadChanged = nextSignature !== lastSettingsPayloadSignature;
    state.settings = needFull
      ? payload.settings
      : {
        ...(state.settings || {}),
        ...payload.settings,
      };
    state.login = payload.login;
    lastSettingsPayloadSignature = nextSignature;
    if (!payloadChanged && !needFull) {
      shouldRenderAfterLoad = false;
    }
  } catch (error) {
    state.ui.errors.settings = error.message;
    setGlobalBanner(`基础配置加载失败：${error.message}`, "error");
  } finally {
    state.ui.loading.settings = false;
    if (shouldRenderAfterLoad) {
      if (needFull) {
        renderSettings();
      } else {
        renderSettingsLite();
      }
    }
  }
}

export function applyBotApiSettings(payload = {}) {
  if (!state.settings) return;
  state.settings.bot_api_accounts = Array.isArray(payload.accounts) ? payload.accounts : [];
  state.settings.bot_api_runtime_status = payload.bot_api_runtime_status || { items: [] };
  state.settings.bot_dispatch_mode = payload.bot_dispatch_mode || state.settings.bot_dispatch_mode || "single";
  state.settings.default_bot_api_account_id = payload.default_bot_api_account_id ?? state.settings.default_bot_api_account_id ?? "";
  state.settings.smart_queue_scheduling_enabled = payload.smart_queue_scheduling_enabled ?? state.settings.smart_queue_scheduling_enabled ?? false;
  renderBotApiSettingsSection(state.settings, {
    hydrateForm: shouldHydrateSettingsForm("botApi"),
  });
  window.dispatchEvent(new CustomEvent("layout:topbar-sync"));
}

function renderSettingsLite() {
  const { settings, login } = state;
  if (!settings) return;

  const browserFolderOptions = `<option value="">请选择目录</option>${settings.folders
    .map((folder) => `<option value="${folder.id}" ${folder.id === state.selectedFolderId ? "selected" : ""}>${escapeHtml(folder.name)}</option>`)
    .join("")}`;
  if (browserFolderOptions !== settingsMarkupCache.browserFolderOptions) {
    setHtmlIfChanged("browser-folder", browserFolderOptions);
    settingsMarkupCache.browserFolderOptions = browserFolderOptions;
  }
  document.getElementById("browser-folder").value = state.selectedFolderId || "";

  const taskFolderFilterOptions = `<option value="all">全部目录</option>${settings.folders
    .map((folder) => `<option value="${folder.id}" ${folder.id === state.taskFolderFilter ? "selected" : ""}>${escapeHtml(folder.name)}</option>`)
    .join("")}`;
  if (taskFolderFilterOptions !== settingsMarkupCache.taskFolderFilterOptions) {
    setHtmlIfChanged("task-folder-filter", taskFolderFilterOptions);
    settingsMarkupCache.taskFolderFilterOptions = taskFolderFilterOptions;
  }
  document.getElementById("task-folder-filter").value = state.taskFolderFilter || "all";

  if (login) {
    setText(
      "login-stage",
      `${statusLabel(login.stage)} / ${login.engine === "hybrid" ? "自动混合模式" : (login.engine === "bot_api" ? "Bot API" : "Telethon")}`,
    );
    renderLoginMeta(login);
    setText("login-error", state.ui.errors.settings || login.last_error || "");
  }
}

export function renderSettings() {
  if (state.ui.loading.settings && !state.settings) {
    setText("login-stage", "加载中");
    const loginMeta = document.getElementById("login-meta");
    if (loginMeta) loginMeta.innerHTML = "";
    setText("login-error", "");
    return;
  }

  const { settings, login } = state;
  if (!settings) return;

  if (shouldHydrateSettingsForm("api")) {
    const apiIdInput = document.getElementById("api-id");
    const apiHashInput = document.getElementById("api-hash");
    const phoneInput = document.getElementById("phone-number");
    apiIdInput.value = "";
    apiHashInput.value = "";
    phoneInput.value = "";
    apiIdInput.placeholder = settings.api.api_id || "未填写则沿用已保存的 API ID";
    apiHashInput.placeholder = settings.api.api_hash || "未填写则沿用已保存的 API Hash";
    phoneInput.placeholder = settings.api.phone_number || "未填写则沿用已保存的手机号";
  }

  syncUploadEngineControls();

  if (shouldHydrateSettingsForm("proxy")) {
    const proxy = settings.proxy || {};
    document.getElementById("proxy-enabled").checked = !!proxy.enabled;
    document.getElementById("proxy-type").value = proxy.type || "http";
    document.getElementById("proxy-host").value = proxy.host || "";
    document.getElementById("proxy-port").value = String(proxy.port || 1080);
    document.getElementById("proxy-username").value = proxy.username || "";
    document.getElementById("proxy-password").value = "";
    document.getElementById("proxy-password").placeholder = proxy.password_saved
      ? proxy.password || "已保存密码，留空则沿用"
      : "没有密码可留空";
    syncProxyControls();
  }

  if (shouldHydrateSettingsForm("botApi")) {
    renderBotApiSettingsSection(settings, { hydrateForm: true });
  } else {
    renderBotApiSettingsSection(settings, { hydrateForm: false });
  }

  syncFolderUploadLimitControls();
  renderEngineCapabilityCard();

  setText("access-password-status", settings.access_password_enabled ? "已启用访问密码" : "未设置访问密码");
  setText(
    "login-stage",
    `${statusLabel(login.stage)} / ${login.engine === "hybrid" ? "自动混合模式" : (login.engine === "bot_api" ? "Bot API" : "Telethon")}`,
  );
  renderLoginMeta(login);
  setText("login-error", state.ui.errors.settings || login.last_error || "");
  document.getElementById("code-form").style.display = login.stage === "code_required" ? "flex" : "none";
  document.getElementById("password-form").style.display = login.stage === "password_required" ? "flex" : "none";

  const channelOptions = settings.channels
    .map((channel) => `<option value="${channel.id}">${escapeHtml(channel.name)}</option>`)
    .join("");

  const folderChannelOptions = `<option value="">请选择频道</option>${channelOptions}`;
  if (folderChannelOptions !== settingsMarkupCache.folderChannelOptions) {
    setHtmlIfChanged("folder-channel", folderChannelOptions);
    settingsMarkupCache.folderChannelOptions = folderChannelOptions;
  }
  const browserFolderOptions = `<option value="">请选择目录</option>${settings.folders
    .map((folder) => `<option value="${folder.id}" ${folder.id === state.selectedFolderId ? "selected" : ""}>${escapeHtml(folder.name)}</option>`)
    .join("")}`;
  if (browserFolderOptions !== settingsMarkupCache.browserFolderOptions) {
    setHtmlIfChanged("browser-folder", browserFolderOptions);
    settingsMarkupCache.browserFolderOptions = browserFolderOptions;
  }
  document.getElementById("browser-folder").value = state.selectedFolderId || "";
  const taskFolderFilterOptions = `<option value="all">全部目录</option>${settings.folders
    .map((folder) => `<option value="${folder.id}" ${folder.id === state.taskFolderFilter ? "selected" : ""}>${escapeHtml(folder.name)}</option>`)
    .join("")}`;
  if (taskFolderFilterOptions !== settingsMarkupCache.taskFolderFilterOptions) {
    setHtmlIfChanged("task-folder-filter", taskFolderFilterOptions);
    settingsMarkupCache.taskFolderFilterOptions = taskFolderFilterOptions;
  }
  document.getElementById("task-folder-filter").value = state.taskFolderFilter || "all";

  const channelListMarkup = settings.channels.length
    ? settings.channels.map((channel) => {
      const boundBot = (settings.bot_api_accounts || []).find((item) => item.id === channel.bot_api_account_id);
      const setupSummary = state.channelBotSetupSummaryByChannel?.[channel.id];
      return `
      <article class="item">
        <div class="settings-item-head">
          <div class="settings-item-title">
            <h3>${escapeHtml(channel.name)}</h3>
            <p class="muted">${escapeHtml(channel.target)}</p>
          </div>
          ${labeledBadge(channel.enabled ? "enabled" : "disabled")}
        </div>
        <section class="settings-item-section">
          <p class="settings-item-section-title">调度信息</p>
          <div class="settings-item-inline-list">
            <span class="settings-item-pill">频道目标：${escapeHtml(channel.target)}</span>
            <span class="settings-item-pill">Bot 绑定：${escapeHtml(channel.bot_api_account_id ? (boundBot ? boundBot.name : "未找到") : "默认策略")}</span>
          </div>
        </section>
        ${setupSummary ? `
          <section class="settings-item-section">
            <p class="settings-item-section-title">最近接入结果</p>
            <div class="settings-item-inline-list">
              <span class="settings-item-pill">成功：${escapeHtml(String(setupSummary.success_count || 0))}/${escapeHtml(String(setupSummary.total || 0))}</span>
              <span class="settings-item-pill">时间：${escapeHtml(setupSummary.at || "-")}</span>
            </div>
            ${setupSummary.failed_names?.length ? `<p class="warning-text">失败：${escapeHtml(setupSummary.failed_names.join("、"))}</p>` : `<p class="muted">最近一次接入操作全部成功。</p>`}
          </section>
        ` : ""}
        <div class="item-actions">
          <button data-action="setup-channel-bot" data-id="${channel.id}" class="ghost">接入 Bot</button>
          <button data-action="setup-channel-all-bots" data-id="${channel.id}" class="ghost">接入全部 Bot</button>
          <button data-action="edit-channel" data-id="${channel.id}" class="ghost">编辑</button>
          <button data-action="delete-channel" data-id="${channel.id}" class="ghost">删除</button>
        </div>
      </article>
    `;
    }).join("")
    : `<p class="muted">还没有频道配置。</p>`;
  if (channelListMarkup !== settingsMarkupCache.channelList) {
    setHtmlIfChanged("channel-list", channelListMarkup);
    settingsMarkupCache.channelList = channelListMarkup;
  }

  const folderListMarkup = settings.folders.length
    ? settings.folders.map((folder) => {
      const channel = settings.channels.find((item) => item.id === folder.channel_id);
      const warning = folderEngineWarning(folder.id);
      return `
      <article class="item">
        <div class="settings-item-head">
          <div class="settings-item-title">
            <h3>${escapeHtml(folder.name)}</h3>
            <p class="muted">${escapeHtml(folder.path)}</p>
          </div>
          ${labeledBadge(folder.enabled ? "enabled" : "disabled")}
        </div>
        ${warning ? `<section class="settings-item-section"><p class="settings-item-section-title">引擎提示</p><p class="warning-text">${escapeHtml(warning.message)}</p></section>` : ""}
        <section class="settings-item-section">
          <p class="settings-item-section-title">基础配置</p>
          <div class="settings-item-inline-list">
            <span class="settings-item-pill">频道：${escapeHtml(channel ? channel.name : "未找到")}</span>
            <span class="settings-item-pill">扫描：${escapeHtml(String(folder.scan_interval_seconds))}s</span>
            <span class="settings-item-pill">稳定：${escapeHtml(String(folder.min_stable_seconds ?? 30))}s</span>
            <span class="settings-item-pill">处理：${escapeHtml(folder.post_upload_action)}</span>
          </div>
        </section>
        <section class="settings-item-section">
          <p class="settings-item-section-title">上传策略</p>
          <div class="settings-item-inline-list">
            <span class="settings-item-pill">自动上传：${folder.auto_upload ? "开" : "关"}</span>
            <span class="settings-item-pill">媒体组：${folder.media_group_upload ? "开" : "关"}</span>
            <span class="settings-item-pill">相似度成组：${folder.media_group_filename_similarity ? `开 (${folder.media_group_similarity_threshold || 80}%)` : "关"}</span>
            <span class="settings-item-pill">超限分段：${folder.split_large_video_upload ? "开" : "关"}</span>
            <span class="settings-item-pill">排除目录：${folder.excluded_subdirs?.length ? `${folder.excluded_subdirs.length} 个` : "无"}</span>
          </div>
        </section>
        <div class="item-actions">
          <button data-action="browse-folder" data-id="${folder.id}" class="ghost">浏览文件</button>
          <button data-action="scan-folder" data-id="${folder.id}" class="ghost">立即扫描</button>
          <button data-action="edit-folder" data-id="${folder.id}" class="ghost">编辑</button>
          <button data-action="delete-folder" data-id="${folder.id}" class="ghost">删除</button>
        </div>
      </article>
    `;
    }).join("")
    : `<p class="muted">还没有监控目录。</p>`;
  if (folderListMarkup !== settingsMarkupCache.folderList) {
    setHtmlIfChanged("folder-list", folderListMarkup);
    settingsMarkupCache.folderList = folderListMarkup;
  }

  window.dispatchEvent(new CustomEvent("layout:topbar-sync"));
}

function renderBotApiAccountList(accounts) {
  const list = document.getElementById("bot-api-account-list");
  if (!list) return;
  const runtimeItems = state.settings?.bot_api_runtime_status?.items || [];
  const markup = accounts.length
    ? accounts.map((account) => {
      const runtime = runtimeItems.find((item) => item.id === account.id) || {};
      return `
      <article class="item">
        <div class="item-top">
          <div>
            <h3>${escapeHtml(account.name)}</h3>
            <p class="muted">${escapeHtml(account.bot_token || "")}</p>
            <p class="muted">限频：${escapeHtml(String(account.send_rate_limit_per_minute || 20))} 次/分钟</p>
            <p class="muted">单频道限频：${escapeHtml(String(account.send_rate_limit_per_channel_per_minute || 10))} 次/分钟</p>
            <p class="muted">抖动：${escapeHtml(String(account.send_jitter_min_ms ?? 300))}-${escapeHtml(String(account.send_jitter_max_ms ?? 1200))} ms</p>
            <p class="muted">429 自动降速：${account.auto_slowdown_enabled ? `开（${escapeHtml(String(account.auto_slowdown_factor_percent ?? 50))}% / ${escapeHtml(String(account.auto_slowdown_duration_seconds ?? 600))} 秒）` : "关"}</p>
            <p class="muted">最近 1 分钟：已发 ${escapeHtml(String(runtime.recent_send_count || 0))} 次 / 剩余 ${escapeHtml(String(runtime.remaining_quota ?? account.send_rate_limit_per_minute ?? 20))} 次</p>
            ${(runtime.slowdown_active) ? `<p class="warning-text">429 自动降速生效中：限频已收紧，剩余约 ${escapeHtml(String(runtime.slowdown_wait_seconds || 0))} 秒</p>` : ""}
            ${Number(runtime.wait_seconds || 0) > 0 ? `<p class="warning-text">${renderWaitReason(runtime.last_wait_reason)}：约 ${escapeHtml(String(runtime.wait_seconds))} 秒</p>` : ""}
          </div>
          ${labeledBadge(account.enabled ? "enabled" : "disabled")}
        </div>
        <div class="item-actions">
          <button data-action="edit-bot-api-account" data-id="${account.id}" class="ghost">编辑</button>
          <button data-action="test-bot-api-account" data-id="${account.id}" class="ghost">测试</button>
          <button data-action="delete-bot-api-account" data-id="${account.id}" class="ghost">删除</button>
        </div>
      </article>
    `;
    }).join("")
    : `<p class="muted">还没有 Bot API 账号。</p>`;
  if (markup !== settingsMarkupCache.botAccountList) {
    list.innerHTML = markup;
    settingsMarkupCache.botAccountList = markup;
  }
}

function renderWaitReason(reason) {
  if (reason === "channel") {
    return "单频道限频等待中";
  }
  if (reason === "global+channel") {
    return "全局与单频道限频等待中";
  }
  if (reason === "auto_slowdown") {
    return "429 自动降速等待中";
  }
  return "全局限频等待中";
}

export function fillBotApiAccountForm(accountId) {
  const account = (state.settings?.bot_api_accounts || []).find((item) => item.id === accountId);
  if (!account) return;
  const title = document.getElementById("bot-api-dialog-title");
  if (title) title.textContent = "编辑 Bot 账号";
  document.getElementById("bot-api-account-id").value = account.id;
  document.getElementById("bot-api-account-name").value = account.name || "";
  document.getElementById("bot-api-token").value = "";
  document.getElementById("bot-api-rate-limit").value = String(account.send_rate_limit_per_minute || 20);
  document.getElementById("bot-api-channel-rate-limit").value = String(account.send_rate_limit_per_channel_per_minute || 10);
  document.getElementById("bot-api-jitter-min").value = String(account.send_jitter_min_ms ?? 300);
  document.getElementById("bot-api-jitter-max").value = String(account.send_jitter_max_ms ?? 1200);
  document.getElementById("bot-api-auto-slowdown-enabled").checked = account.auto_slowdown_enabled !== false;
  document.getElementById("bot-api-auto-slowdown-factor").value = String(account.auto_slowdown_factor_percent ?? 50);
  document.getElementById("bot-api-auto-slowdown-duration").value = String(account.auto_slowdown_duration_seconds ?? 600);
  document.getElementById("bot-api-enabled").checked = !!account.enabled;
  document.getElementById("bot-api-token").placeholder = account.bot_token_saved
    ? account.bot_token || "已保存 Token，留空则沿用"
    : "输入 Bot Token";
  document.getElementById("bot-api-test-result").textContent = "";
  setSettingsFormDirty("botApi", true);
}

export function fillChannelForm(channelId) {
  const channel = state.settings.channels.find((item) => item.id === channelId);
  if (!channel) return;
  setSettingsFormDirty("channel", false);
  document.getElementById("channel-dialog-title").textContent = "编辑频道";
  document.getElementById("channel-id").value = channel.id;
  document.getElementById("channel-name").value = channel.name;
  document.getElementById("channel-target").value = channel.target;
  document.getElementById("channel-enabled").checked = channel.enabled;
  document.getElementById("channel-bot-api-account").value = channel.bot_api_account_id || "";
  syncBotDispatchControls();
}

export function fillFolderForm(folderId) {
  const folder = state.settings.folders.find((item) => item.id === folderId);
  if (!folder) return;
  setSettingsFormDirty("folder", false);
  document.getElementById("folder-dialog-title").textContent = "编辑目录";
  document.getElementById("folder-id").value = folder.id;
  document.getElementById("folder-name").value = folder.name;
  document.getElementById("folder-path").value = folder.path;
  document.getElementById("folder-channel").value = folder.channel_id;
  document.getElementById("folder-interval").value = folder.scan_interval_seconds;
  document.getElementById("folder-min-stable-seconds").value = folder.min_stable_seconds ?? 30;
  document.getElementById("folder-action").value = folder.post_upload_action;
  document.getElementById("folder-move-target").value = folder.move_target_path || "";
  document.getElementById("folder-excluded-subdirs").value = (folder.excluded_subdirs || []).join("\n");
  document.getElementById("folder-auto").checked = folder.auto_upload;
  document.getElementById("folder-media-group").checked = !!folder.media_group_upload;
  document.getElementById("folder-media-group-similarity").checked = !!folder.media_group_filename_similarity;
  document.getElementById("folder-media-group-threshold").value = folder.media_group_similarity_threshold || 80;
  document.getElementById("folder-split-large-video").checked = !!folder.split_large_video_upload;
  document.getElementById("folder-upload-limit").value = folder.upload_size_limit_mb || 2048;
  document.getElementById("folder-segment-target").value = folder.segment_target_size_mb || 1900;
  document.getElementById("folder-enabled").checked = folder.enabled;
  const warning = folderEngineWarning(folder.id);
  const warningNode = document.getElementById("folder-engine-warning");
  if (warningNode) {
    warningNode.textContent = warning ? warning.message : "";
    warningNode.classList.toggle("hidden", !warning);
  }
  syncFolderUploadLimitControls();
  syncFolderMediaGroupControls();
}

export function resetChannelForm() {
  document.getElementById("channel-form").reset();
  document.getElementById("channel-dialog-title").textContent = "新增频道";
  document.getElementById("channel-id").value = "";
  document.getElementById("channel-enabled").checked = true;
  document.getElementById("channel-bot-api-account").value = "";
  setSettingsFormDirty("channel", false);
}

export function resetBotApiAccountForm(markDirty = false) {
  const title = document.getElementById("bot-api-dialog-title");
  if (title) title.textContent = "新增 Bot 账号";
  document.getElementById("bot-api-account-id").value = "";
  document.getElementById("bot-api-account-name").value = "";
  document.getElementById("bot-api-token").value = "";
  document.getElementById("bot-api-token").placeholder = "输入 Bot Token";
  document.getElementById("bot-api-rate-limit").value = "20";
  document.getElementById("bot-api-channel-rate-limit").value = "10";
  document.getElementById("bot-api-jitter-min").value = "300";
  document.getElementById("bot-api-jitter-max").value = "1200";
  document.getElementById("bot-api-auto-slowdown-enabled").checked = true;
  document.getElementById("bot-api-auto-slowdown-factor").value = "50";
  document.getElementById("bot-api-auto-slowdown-duration").value = "600";
  document.getElementById("bot-api-enabled").checked = true;
  document.getElementById("bot-api-test-result").textContent = "";
  setSettingsFormDirty("botApi", markDirty);
}

export function resetFolderForm() {
  document.getElementById("folder-form").reset();
  document.getElementById("folder-dialog-title").textContent = "新增目录";
  document.getElementById("folder-id").value = "";
  document.getElementById("folder-auto").checked = true;
  document.getElementById("folder-media-group").checked = false;
  document.getElementById("folder-media-group-similarity").checked = false;
  document.getElementById("folder-media-group-threshold").value = 80;
  document.getElementById("folder-split-large-video").checked = false;
  document.getElementById("folder-enabled").checked = true;
  document.getElementById("folder-interval").value = 30;
  document.getElementById("folder-min-stable-seconds").value = 30;
  document.getElementById("folder-upload-limit").value = state.settings?.engine_limits?.default_upload_size_mb || 2048;
  document.getElementById("folder-segment-target").value = state.settings?.engine_limits?.default_segment_target_mb || 1900;
  document.getElementById("folder-action").value = "keep";
  document.getElementById("folder-excluded-subdirs").value = "";
  const warningNode = document.getElementById("folder-engine-warning");
  if (warningNode) {
    warningNode.textContent = "";
    warningNode.classList.add("hidden");
  }
  syncFolderUploadLimitControls();
  syncFolderMediaGroupControls();
  setSettingsFormDirty("folder", false);
}
