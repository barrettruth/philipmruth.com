(() => {
  const rootSelector = "[data-vinyl-detail-root]";

  const getDetailRoots = (scope) => {
    if (!scope) return [];
    if (scope instanceof Element && scope.matches(rootSelector)) {
      return [scope];
    }
    return Array.from(scope.querySelectorAll?.(rootSelector) ?? []);
  };

  const initializeDetailRoot = (root) => {
    if (
      !(root instanceof HTMLElement) ||
      root.dataset.detailInitialized === "true"
    )
      return;
    root.dataset.detailInitialized = "true";

    const hero = root.querySelector("[data-detail-hero]");
    const currentLabel = root.querySelector("[data-detail-current-label]");
    const thumbnails = Array.from(
      root.querySelectorAll("[data-detail-thumbnail]"),
    );

    if (!(hero instanceof HTMLImageElement) || thumbnails.length === 0) return;

    let activeIndex = Math.max(
      thumbnails.findIndex((thumbnail) =>
        thumbnail.classList.contains("is-active"),
      ),
      0,
    );

    const setActiveThumbnail = (nextIndex, { focus = false } = {}) => {
      const normalizedIndex =
        ((nextIndex % thumbnails.length) + thumbnails.length) %
        thumbnails.length;
      const nextThumbnail = thumbnails[normalizedIndex];

      if (!(nextThumbnail instanceof HTMLButtonElement)) return;

      activeIndex = normalizedIndex;
      hero.src = nextThumbnail.dataset.detailSrc ?? hero.src;
      hero.alt = nextThumbnail.dataset.detailAlt ?? hero.alt;

      const width = nextThumbnail.dataset.detailWidth;
      const height = nextThumbnail.dataset.detailHeight;

      if (width) {
        hero.setAttribute("width", width);
      }

      if (height) {
        hero.setAttribute("height", height);
      }

      if (currentLabel instanceof HTMLElement) {
        currentLabel.textContent = nextThumbnail.dataset.detailLabel ?? "";
      }

      thumbnails.forEach((thumbnail, thumbnailIndex) => {
        const isActive = thumbnailIndex === normalizedIndex;
        thumbnail.classList.toggle("is-active", isActive);
        thumbnail.setAttribute("aria-pressed", isActive ? "true" : "false");
      });

      if (focus) {
        nextThumbnail.focus();
      }
    };

    thumbnails.forEach((thumbnail, thumbnailIndex) => {
      if (!(thumbnail instanceof HTMLButtonElement)) return;
      thumbnail.addEventListener("click", () =>
        setActiveThumbnail(thumbnailIndex),
      );
    });

    root.addEventListener("keydown", (event) => {
      if (
        event.defaultPrevented ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey
      )
        return;
      if (!root.contains(document.activeElement)) return;

      if (event.key === "ArrowRight") {
        event.preventDefault();
        setActiveThumbnail(activeIndex + 1, { focus: true });
      }

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setActiveThumbnail(activeIndex - 1, { focus: true });
      }
    });

    setActiveThumbnail(activeIndex);
  };

  const initialize = (scope = document) => {
    getDetailRoots(scope).forEach(initializeDetailRoot);
  };

  window.VinylDetail = {
    initialize,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initialize(document));
  } else {
    initialize(document);
  }
})();
