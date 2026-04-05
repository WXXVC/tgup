import {
  RETRYABLE_TASK_STATUSES,
  TERMINAL_TASK_STATUSES,
} from "./constants.js";

export const state = {
  settings: null,
  login: null,
  uploads: [],
  uploadStats: null,
  files: [],
  selectedFolderId: "",
  selectedFiles: new Set(),
  selectedUploadTaskIds: new Set(),
  currentSubdir: "",
  fileScopeFilter: "recursive",
  fileTypeFilter: "all",
  fileStatusFilter: "all",
  fileSearch: "",
  fileColumns: 4,
  taskColumns: 3,
  taskFolderFilter: "all",
  taskStatusFilter: "all",
  taskSearch: "",
  taskSort: "updated_desc",
  routePath: "/",
  activeTab: "settings",
  activeSettingsTab: "access",
  collapsedTaskGroups: {},
  collapsedDirs: {},
  access: {
    enabled: false,
    authorized: true,
    checked: false,
  },
  ui: {
    loading: {
      settings: true,
      files: false,
      uploads: true,
    },
    errors: {
      settings: "",
      files: "",
      uploads: "",
    },
    dirtyForms: {
      api: false,
      channel: false,
      folder: false,
      access: false,
    },
  },
  previewRelativePath: "",
  previewSize: "medium",
  activeTaskDetailId: "",
};

export function getTaskById(taskId) {
  return state.uploads.find((item) => item.id === taskId);
}

export function isRetryableTask(task) {
  return !!task && RETRYABLE_TASK_STATUSES.has(task.status);
}

export function isTerminalTask(task) {
  return !!task && TERMINAL_TASK_STATUSES.has(task.status);
}
