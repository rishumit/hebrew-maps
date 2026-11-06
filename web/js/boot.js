// Loaded first by web/map.html (a classic script, before the CSS and the modules):
//  - the saved day/night theme, so a night-mode map does not flash white;
//  - the first API calls, which then run while the modules download (web/js/api.js picks the responses up).
(function () {
  try {
    document.documentElement.dataset.theme = JSON.parse(localStorage.getItem('hmaps:uitheme') || '"auto"');
  } catch (e) {
    document.documentElement.dataset.theme = 'auto';
  }
  var early = (window.__early = {});
  ['/api/meta', '/api/categories'].forEach(function (u) {
    var r = fetch(u);
    r.catch(function () {});
    early[u] = r;
  });
})();
