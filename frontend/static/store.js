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
  taskFolderFilter: "all",
  taskStatusFilter: "all",
  taskSearch: "",
  taskSort: "updated_desc",
  collapsedTaskGroups: {},
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
  },
  previewRelativePath: "",
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
