# Native OAuth interception test (no app build)

De-risks the native-shell onboarding approach before building the Android/iOS apps.
It proves the single unknown: **does a native WebView fire a navigation event for
the unreachable `http://localhost:3334` redirect** so we can read the auth code
automatically (no copy-paste)? Everything else in the flow is already proven.

`react-native-webview` (Expo Go) uses the same WebView engines Capacitor wraps
(WKWebView / Android WebView), so a pass here means the Capacitor app will work.

## Run it (≈5 minutes, real phone, no build/signing)

1. Open <https://snack.expo.dev> in a desktop browser.
2. Paste the entire contents of `App.js` into the Snack's `App.js`.
3. Snack will detect the `react-native-webview` import — click to add the dependency.
4. Install **Expo Go** on your phone (App Store / Play Store), then scan the Snack's QR.
5. **Mechanism test:** tap "1 · Mechanism test (no login)". It should jump straight to
   a green **✅ INTERCEPTED** screen showing the localhost URL and which event caught
   it. That alone proves the WebView intercepts the dead localhost redirect.
6. **Full real test:** paste a fresh authorize URL (mint one — see below) into the box,
   tap "2 · Real provider test", and log in to Zepto with your OTP. After login the
   WebView hits the dead `localhost:3334` page; the harness should show ✅ INTERCEPTED
   with the real `?code=...` URL. Copy that URL and send it back to complete the
   connection (see "Finish" below).

The screen reports `caught via onShouldStartLoadWithRequest` vs `onNavigationStateChange`
— note which one(s) fire on iOS vs Android; that tells the Capacitor build which
InAppBrowser event to rely on (`urlChangeEvent`/loadstart vs nav-state).

## Mint a fresh authorize URL

Authorize URLs are valid ~15 min and store one-time PKCE state server-side, so mint a
fresh one right before testing (the deployed app still serves the old flow, so this is
generated locally against live secrets):

```bash
cd <repo> && AWS_PROFILE=companion AWS_REGION=ap-south-1 PYTHONPATH=. .venv/bin/python -c "
from core.providers.mcp.oauth import start_web_oauth
r = start_web_oauth(mcp_url='https://mcp.zepto.co.in/mcp',
    callback_url='https://houzzat.in/api/mcp/oauth/callback',
    user_id='admin_bhavesh', provider_label='Zepto MCP', provider_id='zepto')
print(r['authorization_url'])
"
```

Swap `zepto`/`mcp.zepto.co.in` for `district` (`https://mcp-server.district.in/mcp`) to
test District instead. Zomato/Swiggy return `completion: redirect` (their callback is
our hosted URL, not localhost) — for those the WebView intercepts `houzzat.in/...?mcp_oauth=success`.

## Finish (turn the captured URL into vaulted tokens)

This is the already-proven half; run it on the captured URL to confirm end-to-end:

```bash
cd <repo> && AWS_PROFILE=companion AWS_REGION=ap-south-1 PYTHONPATH=. .venv/bin/python -c "
from core.providers.mcp.oauth_complete import complete_web_oauth_from_text
print(complete_web_oauth_from_text(text='<PASTE CAPTURED URL>', user_id='admin_bhavesh'))
"
```

A `connected_pending_test` result + a working Zepto query for `admin_bhavesh` means the
full native flow is validated end-to-end without ever building the app.
