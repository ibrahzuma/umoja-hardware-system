/// Base URL for the Umoja Hardware Django backend.
///
/// Override at build/run time with:
///   flutter run --dart-define=API_BASE_URL=https://umoja.ehub.co.tz
///
/// Defaults are:
///   - Android emulator: 10.0.2.2 reaches the host's 127.0.0.1
///   - Web/desktop:      127.0.0.1
const String _defaultDevHost =
    bool.fromEnvironment('dart.library.io') ? '10.0.2.2' : '127.0.0.1';

const String apiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://$_defaultDevHost:8765',
);
