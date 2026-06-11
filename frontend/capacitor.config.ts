import type { CapacitorConfig } from '@capacitor/cli';

/**
 * The web build (`dist/`) is bundled into the app and its API calls target
 * houzzat.in (see src/api.ts). The provider OAuth login opens in a
 * cordova-plugin-inappbrowser WebView whose `loadstart` event intercepts the
 * localhost redirect — that is the whole reason for the native shell.
 */
const config: CapacitorConfig = {
  appId: 'in.houzzat.companion',
  appName: 'Companion',
  webDir: 'dist',
};

export default config;
