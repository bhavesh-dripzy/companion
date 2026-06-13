import type { CapacitorConfig } from '@capacitor/cli';

/**
 * The web build (`dist/`) is bundled into the app and its API calls target
 * app.jatayulabs.com (see src/api.ts). The provider OAuth login opens in a
 * cordova-plugin-inappbrowser WebView whose `loadstart` event intercepts the
 * localhost redirect — that is the whole reason for the native shell.
 */
const config: CapacitorConfig = {
  appId: 'com.jatayulabs.companion',
  appName: 'Companion',
  webDir: 'dist',
  plugins: {
    SystemBars: {
      insetsHandling: 'css',
      style: 'DEFAULT',
      hidden: false,
    },
  },
};

export default config;
