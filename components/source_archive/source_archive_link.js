// Keep in sync with the source_archive component's `download_path` option.
const DOWNLOAD_PATH = "/source.zip";

const LABEL = "Download source";
const TITLE = "Download the ESPHome source archive";
const ERROR_LABEL = "Source unavailable";
const ERROR_RESET_MS = 5000;

const filenameFrom = (response) => {
  const match = (response.headers.get("Content-Disposition") || "").match(
    /filename="([^"]+)"/
  );
  return match ? match[1] : DOWNLOAD_PATH.replace(/^.*\//, "") || "source.zip";
};

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

    .source-archive-link--error {
      background: var(--error-color, #db4437);
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
  link.textContent = LABEL;
  link.title = TITLE;
  document.body.append(link);

  let resetTimer;
  const fail = (message, error) => {
    console.error("source_archive:", message, error || "");
    clearTimeout(resetTimer);
    link.classList.add("source-archive-link--error");
    link.textContent = ERROR_LABEL;
    link.title = message;
    resetTimer = setTimeout(() => {
      link.classList.remove("source-archive-link--error");
      link.textContent = LABEL;
      link.title = TITLE;
    }, ERROR_RESET_MS);
  };

  link.addEventListener("click", async (event) => {
    event.preventDefault();

    let response;
    try {
      response = await fetch(DOWNLOAD_PATH, { cache: "no-store" });
    } catch (error) {
      fail("Could not reach the device to download the archive.", error);
      return;
    }

    if (!response.ok) {
      fail(
        `The device answered ${response.status} for ${DOWNLOAD_PATH}. Check that the ` +
          `source_archive component is configured and that its download_path matches.`
      );
      return;
    }

    const blob = await response.blob();
    if (blob.size === 0) {
      fail("The device returned an empty archive.");
      return;
    }

    const url = URL.createObjectURL(blob);
    const download = document.createElement("a");
    download.href = url;
    download.download = filenameFrom(response);
    download.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", addSourceArchiveLink, { once: true });
} else {
  addSourceArchiveLink();
}