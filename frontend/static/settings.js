import { state } from "./store.js";
import {
  api,
  escapeHtml,
  labeledBadge,
  setGlobalBanner,
  setText,
  statusLabel,
} from "./utils.js";

export async function submitJson(url, payload, method = "POST") {
  return api(url, { method, body: JSON.stringify(payload) });
}

export function setSettingsFormDirty(formName, dirty = true) {
  if (Object.hasOwn(state.ui.dirtyForms, formName)) {
    state.ui.dirtyForms[formName] = dirty;
  }
}

function shouldHydrateSettingsForm(formName) {
  return !state.ui.dirtyForms[formName];
}

export async function loadSettings() {
  state.ui.loading.settings = true;
  state.ui.errors.settings = "";
  renderSettings();
  try {
    const payload = await api("/api/settings");
    state.settings = payload.settings;
    state.login = payload.login;
  } catch (error) {
    state.ui.errors.settings = error.message;
    setGlobalBanner(`基础配置加载失败：${error.message}`, "error");
  } finally {
    state.ui.loading.settings = false;
    renderSettings();
  }
}

export function renderSettings() {
  if (state.ui.loading.settings && !state.settings) {
    setText("login-stage", "加载中");
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

  setText("access-password-status", settings.access_password_enabled ? "已启用访问密码" : "未设置访问密码");
  setText("login-stage", statusLabel(login.stage));
  setText("login-error", state.ui.errors.settings || login.last_error || "");
  document.getElementById("code-form").style.display = login.stage === "code_required" ? "flex" : "none";
  document.getElementById("password-form").style.display = login.stage === "password_required" ? "flex" : "none";

  const channelOptions = settings.channels
    .map((channel) => `<option value="${channel.id}">${escapeHtml(channel.name)}</option>`)
    .join("");

  document.getElementById("folder-channel").innerHTML = `<option value="">请选择频道</option>${channelOptions}`;
  document.getElementById("browser-folder").innerHTML = `<option value="">请选择目录</option>${settings.folders
    .map((folder) => `<option value="${folder.id}" ${folder.id === state.selectedFolderId ? "selected" : ""}>${escapeHtml(folder.name)}</option>`)
    .join("")}`;
  document.getElementById("task-folder-filter").innerHTML = `<option value="all">全部目录</option>${settings.folders
    .map((folder) => `<option value="${folder.id}" ${folder.id === state.taskFolderFilter ? "selected" : ""}>${escapeHtml(folder.name)}</option>`)
    .join("")}`;

  document.getElementById("channel-list").innerHTML = settings.channels.length
    ? settings.channels.map((channel) => `
      <article class="item">
        <div class="item-top">
          <div>
            <h3>${escapeHtml(channel.name)}</h3>
            <p class="muted">${escapeHtml(channel.target)}</p>
          </div>
          ${labeledBadge(channel.enabled ? "enabled" : "disabled")}
        </div>
        <div class="item-actions">
          <button data-action="edit-channel" data-id="${channel.id}" class="ghost">编辑</button>
          <button data-action="delete-channel" data-id="${channel.id}" class="ghost">删除</button>
        </div>
      </article>
    `).join("")
    : `<p class="muted">还没有频道配置。</p>`;

  document.getElementById("folder-list").innerHTML = settings.folders.length
    ? settings.folders.map((folder) => {
      const channel = settings.channels.find((item) => item.id === folder.channel_id);
      return `
      <article class="item">
        <div class="item-top">
          <div>
            <h3>${escapeHtml(folder.name)}</h3>
            <p class="muted">${escapeHtml(folder.path)}</p>
          </div>
          ${labeledBadge(folder.enabled ? "enabled" : "disabled")}
        </div>
        <div class="meta">
          <span>频道: ${escapeHtml(channel ? channel.name : "未找到")}</span>
          <span>自动上传: ${folder.auto_upload ? "开" : "关"}</span>
          <span>扫描: ${folder.scan_interval_seconds}s</span>
          <span>处理: ${escapeHtml(folder.post_upload_action)}</span>
        </div>
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
}

export function fillChannelForm(channelId) {
  const channel = state.settings.channels.find((item) => item.id === channelId);
  if (!channel) return;
  setSettingsFormDirty("channel", false);
  document.getElementById("channel-id").value = channel.id;
  document.getElementById("channel-name").value = channel.name;
  document.getElementById("channel-target").value = channel.target;
  document.getElementById("channel-enabled").checked = channel.enabled;
}

export function fillFolderForm(folderId) {
  const folder = state.settings.folders.find((item) => item.id === folderId);
  if (!folder) return;
  setSettingsFormDirty("folder", false);
  document.getElementById("folder-id").value = folder.id;
  document.getElementById("folder-name").value = folder.name;
  document.getElementById("folder-path").value = folder.path;
  document.getElementById("folder-channel").value = folder.channel_id;
  document.getElementById("folder-interval").value = folder.scan_interval_seconds;
  document.getElementById("folder-action").value = folder.post_upload_action;
  document.getElementById("folder-move-target").value = folder.move_target_path || "";
  document.getElementById("folder-auto").checked = folder.auto_upload;
  document.getElementById("folder-enabled").checked = folder.enabled;
}

export function resetChannelForm() {
  document.getElementById("channel-form").reset();
  document.getElementById("channel-id").value = "";
  document.getElementById("channel-enabled").checked = true;
  setSettingsFormDirty("channel", false);
}

export function resetFolderForm() {
  document.getElementById("folder-form").reset();
  document.getElementById("folder-id").value = "";
  document.getElementById("folder-auto").checked = true;
  document.getElementById("folder-enabled").checked = true;
  document.getElementById("folder-interval").value = 30;
  document.getElementById("folder-action").value = "keep";
  setSettingsFormDirty("folder", false);
}
