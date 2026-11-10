import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Keeps the DRF auth token in the platform's secure store.
class TokenStorage {
  TokenStorage([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'umoja_admin_token';
  final FlutterSecureStorage _storage;

  Future<String?> read() => _storage.read(key: _key);
  Future<void> save(String token) => _storage.write(key: _key, value: token);
  Future<void> clear() => _storage.delete(key: _key);
}
