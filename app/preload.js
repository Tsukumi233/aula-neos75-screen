// Bridge between the window and the main process (no Node in the page).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("neos", {
  info: () => ipcRenderer.invoke("info"),
  pickImage: () => ipcRenderer.invoke("pick-image"),
  pickFile: extensions => ipcRenderer.invoke("pick-file", extensions),
  pickFolder: () => ipcRenderer.invoke("pick-folder"),
  appSettings: () => ipcRenderer.invoke("app-settings"),
  setAppSettings: changes => ipcRenderer.invoke("set-app-settings", changes),
  showItem: p => ipcRenderer.invoke("show-item", p),
  openPath: p => ipcRenderer.invoke("open-path", p),
  restartService: () => ipcRenderer.invoke("restart-service"),
  onServiceExit: cb => ipcRenderer.on("service-exit", (_e, code) => cb(code)),
  onServiceRestarted: cb => ipcRenderer.on("service-restarted", (_e, info) => cb(info)),
});
