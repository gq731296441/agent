// 文件预览与工作区逻辑
export function addFileToList(fileData) {
  const list = document.getElementById("file-list");
  if (!list) return;
  if (list.querySelector(".empty-state"))
    list.querySelector(".empty-state").remove();
  const item = document.createElement("div");
  item.className = "file-item";
  item.innerHTML = `<span class="file-icon">📄</span> ${fileData.name}`;
  item.onclick = () => openFileTab(fileData);
  list.prepend(item);
}

export function switchTab(tabId) {
  document
    .querySelectorAll(".tab-item")
    .forEach((el) => el.classList.remove("active"));
  const activeHeader =
    document.querySelector(`.tab-item[data-target="${tabId}"]`) ||
    (tabId === "tool-logs-tab"
      ? document.querySelector(".tab-item:first-child")
      : null);
  if (activeHeader) activeHeader.classList.add("active");
  document
    .querySelectorAll(".tab-pane")
    .forEach((el) => el.classList.remove("active"));
  const el = document.getElementById(tabId);
  if (el) el.classList.add("active");
}

export function closeTab(e, tabId) {
  e.stopPropagation();
  const header = document.querySelector(`.tab-item[data-target="${tabId}"]`);
  const pane = document.getElementById(tabId);
  if (header) header.remove();
  if (pane) pane.remove();
  switchTab("tool-logs-tab");
}

export function openFileTab(fileData) {
  const tabId = `file-${fileData.name.replace(/[^a-zA-Z0-9]/g, "-")}`;
  if (document.getElementById(tabId)) {
    switchTab(tabId);
    return;
  }
  const headerContainer = document.getElementById("tabs-header");
  const header = document.createElement("div");
  header.className = "tab-item active";
  header.dataset.target = tabId;
  header.innerHTML = `${fileData.name} <span class="tab-close" onclick="closeTab(event, '${tabId}')">×</span>`;
  header.onclick = () => switchTab(tabId);
  document
    .querySelectorAll(".tab-item")
    .forEach((el) => el.classList.remove("active"));
  headerContainer.appendChild(header);
  const contentContainer = document.getElementById("tabs-content");
  const pane = document.createElement("div");
  pane.id = tabId;
  pane.className = "tab-pane active";
  document
    .querySelectorAll(".tab-pane")
    .forEach((el) => el.classList.remove("active"));
  renderFileContent(pane, fileData);
  contentContainer.appendChild(pane);
}

export function renderFileContent(container, fileData) {
  const ext = fileData.type.toLowerCase();
  const url = fileData.url;
  container.innerHTML = '<div style="padding:20px;">正在加载文件...</div>';
  if (ext === "html") {
    container.innerHTML = `<iframe src="${url}" class="file-preview-frame"></iframe>`;
  } else if (ext === "docx" || ext === "doc") {
    fetch(url)
      .then((r) => r.arrayBuffer())
      .then((buf) => mammoth.convertToHtml({ arrayBuffer: buf }))
      .then((result) => (container.innerHTML = result.value))
      .catch((e) => (container.innerHTML = `无法预览 Word 文件: ${e}`));
  } else if (ext === "xlsx" || ext === "xls") {
    fetch(url)
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        const wb = XLSX.read(buf, { type: "array" });
        const html = XLSX.utils.sheet_to_html(wb.Sheets[wb.SheetNames[0]]);
        container.innerHTML = html;
      })
      .catch((e) => (container.innerHTML = `无法预览 表格: ${e}`));
  } else
    container.innerHTML = `<a href="${url}" target="_blank">下载 ${fileData.name}</a>`;
}
