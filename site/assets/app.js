"use strict";

// 本站不读取剪贴板、不持久化设置，也不发送网络请求。
const themeButton = document.querySelector(".theme-toggle");
if (themeButton) {
  const themes = ["system", "light", "dark"];
  const labels = { system: "跟随系统", light: "浅色", dark: "深色" };
  let current = 0;
  themeButton.hidden = false;
  themeButton.addEventListener("click", () => {
    current = (current + 1) % themes.length;
    const theme = themes[current];
    if (theme === "system") {
      delete document.documentElement.dataset.theme;
    } else {
      document.documentElement.dataset.theme = theme;
    }
    themeButton.textContent = `主题：${labels[theme]}`;
    themeButton.setAttribute("aria-label", `当前主题：${labels[theme]}，点击切换`);
  });
}

const announcement = document.getElementById("announcement");
for (const block of document.querySelectorAll("pre[data-copy]")) {
  const code = block.querySelector("code");
  if (!code || !navigator.clipboard || !window.isSecureContext) continue;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button";
  button.textContent = "复制命令";
  button.setAttribute("aria-label", `复制${block.dataset.copy}公开示例命令`);
  block.append(button);
  let timer;
  button.addEventListener("click", async () => {
    try {
      // 仅复制页面中固定的公开命令；不读取用户输入或剪贴板内容。
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = "已复制";
      if (announcement) announcement.textContent = "公开示例命令已复制，请先检查并替换占位 ID。";
    } catch {
      button.textContent = "请手动选择复制";
      if (announcement) announcement.textContent = "浏览器未允许写入剪贴板，请手动选择命令复制。";
    }
    clearTimeout(timer);
    timer = setTimeout(() => { button.textContent = "复制命令"; }, 3000);
  });
}
