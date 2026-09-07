/// Base URL for the Umoja Hardware Django backend.
///
/// **The default is production**, deliberately. A build with no `--dart-define`
/// is the one that gets uploaded to the portal and installed on a real phone,
/// and a phone cannot reach a developer's localhost. The default used to be
/// `http://10.0.2.2:8765` — the Android *emulator's* alias for the host machine
/// — which built an APK that could not talk to anything once installed, and
/// that is exactly the APK that shipped.
///
/// Override it for local work instead, where you know what you are pointing at:
///
///   flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8765     # emulator
///   flutter run --dart-define=API_BASE_URL=http://127.0.0.1:8000    # desktop/web
///
/// Note that `http://` is blocked on Android 9+ unless cleartext is explicitly
/// allowed, so a local override needs the emulator or a debug build.
const String apiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'https://umoja.ehub.co.tz',
);
