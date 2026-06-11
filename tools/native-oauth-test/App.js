/**
 * Companion — native OAuth redirect-interception test harness.
 *
 * Proves the one unknown in the native-shell onboarding approach: that a native
 * WebView fires a navigation event for the unreachable `http://localhost:3334`
 * redirect, so we can read the auth code without the user copy-pasting it.
 *
 * Run with NO app build:
 *   1. Go to https://snack.expo.dev, paste this whole file into App.js.
 *   2. When prompted, add the `react-native-webview` dependency.
 *   3. Open the Snack on your phone via the Expo Go app (scan the QR).
 *   4. Tap "1 · Mechanism test" — should immediately show INTERCEPTED. That alone
 *      proves the WebView catches a localhost redirect.
 *   5. For the full proof: paste a fresh authorize URL (ask Claude to mint one),
 *      tap "2 · Real provider test", log in with your OTP. The dead localhost page
 *      should be intercepted and its URL shown — copy it back to Claude to finish.
 *
 * react-native-webview uses WKWebView (iOS) / Android WebView — the same engines
 * Capacitor's InAppBrowser wraps — so a pass here means Capacitor will work too.
 * The screen reports WHICH event caught the redirect, which tells the Capacitor
 * build whether to rely on `urlChangeEvent` (loadstart) or the nav-state change.
 */
import React, { useState } from 'react';
import { View, Text, TextInput, Button, ScrollView, StyleSheet } from 'react-native';
import { WebView } from 'react-native-webview';

const CALLBACK_PREFIX = 'http://localhost:3334/oauth/callback';

// A trivial page that, once loaded, navigates to the localhost callback. This is
// a *navigation* (not the initial load), mirroring the real provider 302 — so it
// exercises the same delegate path the real redirect will.
const MECH_HTML = `<!doctype html><meta name="viewport" content="width=device-width">
<body style="font:17px -apple-system;padding:24px">Redirecting to the dead localhost callback…
<script>setTimeout(function(){location.href="${CALLBACK_PREFIX}?code=MECH_TEST_CODE&state=mech";},400)</script>`;

export default function App() {
  const [mode, setMode] = useState(null); // null | 'mech' | 'real'
  const [input, setInput] = useState('');
  const [captured, setCaptured] = useState(null);
  const [via, setVia] = useState('');

  const onNav = (url, source) => {
    if (typeof url === 'string' && url.startsWith(CALLBACK_PREFIX)) {
      if (!captured) {
        setCaptured(url);
        setVia(source);
      }
      return false; // block the dead load (honored by onShouldStartLoadWithRequest)
    }
    return true;
  };

  if (captured) {
    return (
      <ScrollView contentContainerStyle={styles.center}>
        <Text style={styles.ok}>✅ INTERCEPTED</Text>
        <Text style={styles.meta}>caught via {via}</Text>
        <Text style={styles.label}>Captured redirect URL (copy this):</Text>
        <Text selectable style={styles.url}>{captured}</Text>
        <View style={styles.gap} />
        <Button title="Reset" onPress={() => { setCaptured(''); setCaptured(null); setMode(null); }} />
      </ScrollView>
    );
  }

  if (mode === 'mech') {
    return (
      <WebView
        source={{ html: MECH_HTML }}
        onShouldStartLoadWithRequest={(r) => onNav(r.url, 'onShouldStartLoadWithRequest')}
        onNavigationStateChange={(n) => onNav(n.url, 'onNavigationStateChange')}
      />
    );
  }

  if (mode === 'real') {
    return (
      <WebView
        source={{ uri: input.trim() }}
        incognito
        onShouldStartLoadWithRequest={(r) => onNav(r.url, 'onShouldStartLoadWithRequest')}
        onNavigationStateChange={(n) => onNav(n.url, 'onNavigationStateChange')}
      />
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.form}>
      <Text style={styles.h1}>Companion OAuth interception test</Text>
      <Text style={styles.p}>
        Tap (1) for an instant, no-login proof the WebView catches the localhost
        redirect. Tap (2) after pasting a fresh authorize URL for the real end-to-end run.
      </Text>
      <View style={styles.gap} />
      <Button title="1 · Mechanism test (no login)" onPress={() => setMode('mech')} />
      <View style={styles.gap} />
      <Text style={styles.label}>Authorize URL (paste, ~15 min validity):</Text>
      <TextInput
        style={styles.input}
        value={input}
        onChangeText={setInput}
        autoCapitalize="none"
        autoCorrect={false}
        multiline
        placeholder="https://auth.zepto.co.in/authorize?response_type=code&..."
      />
      <Button title="2 · Real provider test" onPress={() => input.trim() && setMode('real')} />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  form: { padding: 24, paddingTop: 64 },
  center: { padding: 24, paddingTop: 80, alignItems: 'center' },
  h1: { fontSize: 22, fontWeight: '800', marginBottom: 10 },
  p: { fontSize: 14, color: '#444', lineHeight: 20 },
  label: { fontSize: 12, fontWeight: '700', color: '#666', marginTop: 12, marginBottom: 6 },
  meta: { fontSize: 13, color: '#2563eb', fontWeight: '700', marginBottom: 18 },
  ok: { fontSize: 26, fontWeight: '900', color: '#047857', marginBottom: 8 },
  url: { fontFamily: 'Courier', fontSize: 13, color: '#111', backgroundColor: '#f1f5f9', padding: 12, borderRadius: 8 },
  input: { borderWidth: 1, borderColor: '#cbd5e1', borderRadius: 10, padding: 12, minHeight: 90, fontFamily: 'Courier', fontSize: 12, marginBottom: 14, textAlignVertical: 'top' },
  gap: { height: 16 },
});
