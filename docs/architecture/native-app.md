# Companion native app (Capacitor)

A thin Capacitor shell wraps the existing React/Vite frontend as installable
Android + iOS apps. Its one job beyond "the website in an app" is **OAuth
redirect interception**: the provider login opens in a `cordova-plugin-inappbrowser`
WebView whose `loadstart` event catches the `http://localhost:3334` redirect
before the dead load — so the allowlist providers (Zepto/District/Groww) connect
with no copy-paste. See [mcp-phone-onboarding.md](./mcp-phone-onboarding.md).

## Layout

- `frontend/capacitor.config.ts` — appId `com.jatayulabs.companion`, `webDir: dist` (the
  web build is **bundled**, not remote-loaded).
- `frontend/src/api.ts` — when `Capacitor.isNativePlatform()`, API calls target
  `https://app.jatayulabs.com` (override with `VITE_API_BASE`). CORS is open and auth is a
  Bearer header, so this needs no backend change.
- `frontend/src/native.ts` — `runNativeMcpConnect(provider)`: negotiate OAuth, open the
  login in the intercepting WebView, catch the localhost redirect (`loadstart`/`loaderror`
  backstop) or the `mcp_oauth=success` bounce for redirect-mode providers, then finish via
  `POST /api/mcp/oauth/complete/`.
- `frontend/src/SettingsModal.tsx` — Connect runs the native path when `isNative`; the web
  paste flow is untouched.
- `frontend/android/`, `frontend/ios/` — generated native projects (Capacitor 8; iOS uses
  Swift Package Manager, so **no CocoaPods needed**).

## Build & run on a device

All commands run from `frontend/`. Rebuild + sync after any web change:

```bash
npm run build && npx cap sync
```

### Android (lowest friction — do this first)

1. Install **Android Studio** + a JDK (Capacitor 8 wants JDK 21).
2. `npx cap open android` → opens Android Studio.
3. Plug in an Android phone with USB debugging on (or start an emulator), press **Run**.
   The app installs and launches. To share a build, Build → Build APK and sideload it.

### iOS

1. Install **Xcode** (Mac App Store). No CocoaPods required — SPM resolves on open.
2. `npx cap open ios` → opens Xcode.
3. Select your iPhone (or a simulator), set a Signing Team (a free Apple ID works for a
   7-day install), press **Run**. TestFlight/longer installs need the Apple Developer Program.

> This machine has no Xcode/Android SDK/Java, so the apps must be built on a machine that does.

## What's verified vs. what to confirm on device

- **Verified here:** web build typechecks with the native code; `cap add`/`cap sync` wire
  `cordova-plugin-inappbrowser` into both platforms; the full OAuth + completion chain works
  end-to-end (the `loopback_test.py` PASS — real Zepto login, 23 tools).
- **Confirm on first device run:** that `loadstart` fires for the dead `localhost:3334`
  redirect so the code is captured. This is what `loadstart` is designed for (it fires at
  navigation *start*, before the network attempt — so Android cleartext blocking doesn't
  matter either), but it's the one behavior that can only be seen on a real WebView. The
  `tools/native-oauth-test/` Expo harness logs which event fires if you want to confirm
  independently first.

## Prerequisite for the wired connect flow

`runNativeMcpConnect` calls `GET /api/mcp/oauth/start` (completion-mode negotiation) and
`POST /api/mcp/oauth/complete/` — **both are new backend endpoints not yet deployed**. Until
the backend is deployed to app.jatayulabs.com, the in-app Connect button will fail (the pure
interception mechanism still tests fine via the Expo harness, which needs no backend). Deploy
the `dev` changes, then the in-app flow works against live.

## Note on the native projects in git

`android/` and `ios/` are generated and regenerable (`npx cap add ...`). Either commit them
(Capacitor's recommendation, keeps signing/config stable) or gitignore them and regenerate.
They are currently untracked.
