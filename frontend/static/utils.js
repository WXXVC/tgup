import {
  FILE_TYPE_LABELS,
  SEARCH_DEBOUNCE_MS,
  STATUS_LABELS,
  TOAST_DURATION_MS,
} from "./constants.js";

export async function api(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("app-unauthorized"));
    }
    throw new Error(payload.detail || "请求失败");
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response;
}

export function setText(id, value) {
  document.getElementById(id).textContent = value || "";
}

export function debounce(fn, delay = SEARCH_DEBOUNCE_MS) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
}

export function formatBytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function labeledBadge(status) {
  return `<span class="badge ${status}">${STATUS_LABELS[status] || status}</span>`;
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status;
}

export function fileTypeLabel(type) {
  return FILE_TYPE_LABELS[type] || type;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function setGlobalBanner(message = "", type = "info") {
  const banner = document.getElementById("global-banner");
  if (!message) {
    banner.className = "global-banner hidden";
    banner.textContent = "";
    return;
  }
  banner.className = `global-banner ${type}`;
  banner.textContent = message;
}

export function pushToast(message, type = "info") {
  if (!message) return;
  const stack = document.getElementById("toast-stack");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  stack.prepend(toast);
  window.setTimeout(() => {
    toast.remove();
  }, TOAST_DURATION_MS);
}

export function setPanelFeedback(id, options = {}) {
  const panel = document.getElementById(id);
  const { title = "", message = "", actionLabel = "", actionId = "", tone = "info", visible = false } = options;
  if (!visible) {
    panel.className = "panel-feedback hidden";
    panel.innerHTML = "";
    return;
  }
  panel.className = `panel-feedback ${tone}`;
  panel.innerHTML = `
    <div class="feedback-row">
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(message)}</span>
      </div>
      ${actionLabel ? `<button class="ghost" type="button" id="${actionId}">${escapeHtml(actionLabel)}</button>` : ""}
    </div>
  `;
}

export function fileSkeleton(count = 6) {
  return Array.from({ length: count }, (_, index) => `
    <article class="skeleton-card" aria-hidden="true" key="${index}">
      <div class="skeleton skeleton-preview"></div>
      <div class="skeleton skeleton-line medium"></div>
      <div class="skeleton skeleton-line short"></div>
      <div class="skeleton skeleton-line medium"></div>
    </article>
  `).join("");
}

export function taskSkeleton(count = 4) {
  return Array.from({ length: count }, (_, index) => `
    <article class="skeleton-task" aria-hidden="true" key="${index}">
      <div class="skeleton skeleton-line medium"></div>
      <div class="skeleton skeleton-line short"></div>
      <div class="skeleton skeleton-line"></div>
      <div class="skeleton skeleton-line medium"></div>
    </article>
  `).join("");
}

export function formatDateTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString();
}
