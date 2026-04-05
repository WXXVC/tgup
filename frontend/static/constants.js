export const STATUS_LABELS = {
  pending: "未上传",
  uploading: "上传中",
  uploaded: "已上传",
  failed: "失败",
  locked: "占用中",
  enabled: "启用",
  disabled: "禁用",
  logged_out: "未登录",
  code_required: "等待验证码",
  password_required: "等待二次验证",
  authorized: "已登录",
};

export const FILE_TYPE_LABELS = {
  video: "视频",
  image: "图片",
  music: "音乐",
  document: "文档",
  other: "其他",
};

export const TASK_STATUS_ORDER = ["uploading", "pending", "failed", "locked", "uploaded"];
export const RETRYABLE_TASK_STATUSES = new Set(["failed", "locked"]);
export const TERMINAL_TASK_STATUSES = new Set(["failed", "locked", "uploaded"]);
export const TOAST_DURATION_MS = 2600;
export const SEARCH_DEBOUNCE_MS = 220;
