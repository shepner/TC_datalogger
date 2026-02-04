/**
 * Redirect to login on 401 so the auth prompt is shown from any page when session is missing/expired.
 * Wraps fetch() so API calls from any in-app page trigger a full navigation to the login page.
 */
(function () {
    var orig = window.fetch;
    if (!orig) return;
    window.fetch = function () {
        var p = orig.apply(this, arguments);
        return p.then(function (res) {
            if (res.status === 401) {
                var loginUrl = '/login';
                res.clone().json().then(function (d) {
                    if (d && d.login_url) loginUrl = d.login_url;
                    window.location.href = loginUrl;
                }).catch(function () {
                    window.location.href = loginUrl;
                });
            }
            return res;
        });
    };
})();
