// Keep in sync with the source_archive component's `download_path` option.
const DOWNLOAD_PATH = "/source.zip";

const addSourceArchiveLink = () => {
  if (document.querySelector("[data-source-archive-link]")) {
    return;
  }

  const style = document.createElement("style");
  style.textContent = `
    .source-archive-link {
      position: fixed;
      right: 16px;
      bottom: calc(16px + env(safe-area-inset-bottom, 0px));
      z-index: 1000;
      display: inline-flex;
      align-items: center;
      min-height: 40px;
      padding: 0 14px;
      border-radius: 4px;
      background: var(--primary-color, #03a9f4);
      box-shadow: 0 2px 6px rgb(0 0 0 / 30%);
      color: var(--text-primary-color, #fff);
      font: 500 14px/1 sans-serif;
      letter-spacing: 0;
      text-decoration: none;
    }

    .source-archive-link:hover {
      filter: brightness(0.92);
    }

    .source-archive-link:focus-visible {
      outline: 3px solid var(--primary-color, #03a9f4);
      outline-offset: 3px;
    }

    @media (max-width: 480px) {
      .source-archive-link {
        right: 12px;
        bottom: calc(12px + env(safe-area-inset-bottom, 0px));
        min-height: 44px;
      }
    }
  `;
  document.head.append(style);

  const link = document.createElement("a");
  link.className = "source-archive-link";
  link.dataset.sourceArchiveLink = "";
  link.href = DOWNLOAD_PATH;
  link.download = "";
  link.textContent = "Download source";
  link.title = "Download the ESPHome source archive";
  document.body.append(link);
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", addSourceArchiveLink, { once: true });
} else {
  addSourceArchiveLink();
}