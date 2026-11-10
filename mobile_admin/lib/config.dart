/// Base URL for the Umoja Hardware Django backend.
///
/// **The default is production**, deliberately — a release build with no
/// `--dart-define` is the one installed on a real phone, and a phone cannot
/// reach a developer's localhost. Override it for local work:
///
///   flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000     # emulator
///   flutter run --dart-define=API_BASE_URL=http://127.0.0.1:8000    # desktop
///
/// `http://` is blocked on Android 9+ unless cleartext is allowed, so a local
/// override needs the emulator or a debug build.
const String apiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'https://umoja.ehub.co.tz',
);
