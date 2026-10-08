// SPDX-FileCopyrightText: 2026 calendaring-jmap contributors
// SPDX-License-Identifier: AGPL-3.0-or-later

// pydata-sphinx-theme toggles light/dark via the html element's data-theme
// attribute; the Bootstrap CSS vendored inside the theme's own stylesheet
// keys its dark-mode variable block off data-bs-theme instead, which this
// theme never sets. Left alone, every Bootstrap component (dropdown menus,
// tooltips, and anything else using --bs-* variables instead of the
// theme's own --pst-color-* ones) stays stuck in light colors even when
// the page is in dark mode. Mirroring the attribute is the only fix that
// covers every such component, including ones added later, instead of
// hand-overriding each affected --bs-* variable one at a time.
(function () {
  var html = document.documentElement;

  function sync() {
    var mode = html.getAttribute("data-theme");
    if (mode) {
      html.setAttribute("data-bs-theme", mode);
    }
  }

  sync();
  new MutationObserver(sync).observe(html, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
})();
