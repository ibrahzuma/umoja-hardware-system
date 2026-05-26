import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config.dart';
import 'token_storage.dart';

/// Dio-based HTTP client that injects the Token auth header.
class ApiClient {
  ApiClient(this._tokenStorage)
    : dio = Dio(
        BaseOptions(
          baseUrl: apiBaseUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 30),
          headers: {'Accept': 'application/json'},
        ),
      ) {
    dio.interceptors.add(_AuthInterceptor(_tokenStorage));
    if (kDebugMode) {
      dio.interceptors.add(
        LogInterceptor(requestBody: false, responseBody: false, error: true),
      );
    }
  }

  final TokenStorage _tokenStorage;
  final Dio dio;

  /// POST /api-token-auth/ — exchange username/password for a token.
  Future<String> login(String username, String password) async {
    final resp = await dio.post(
      '/api-token-auth/',
      data: {'username': username, 'password': password},
    );
    final token = resp.data['token'] as String?;
    if (token == null || token.isEmpty) {
      throw const ApiException('Login response missing token');
    }
    await _tokenStorage.save(token);
    return token;
  }

  Future<void> logout() => _tokenStorage.clear();

  /// GET a paginated DRF list. Returns the `results` array if present,
  /// otherwise the raw list response.
  Future<List<dynamic>> list(String path, {Map<String, dynamic>? query}) async {
    final resp = await dio.get(path, queryParameters: query);
    final data = resp.data;
    if (data is Map<String, dynamic> && data.containsKey('results')) {
      return List<dynamic>.from(data['results'] as List);
    }
    if (data is List) return List<dynamic>.from(data);
    return const [];
  }

  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
  }) async {
    final resp = await dio.get(path, queryParameters: query);
    return Map<String, dynamic>.from(resp.data as Map);
  }

  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
  ) async {
    final resp = await dio.post(path, data: body);
    final data = resp.data;
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }
}

class _AuthInterceptor extends Interceptor {
  _AuthInterceptor(this._tokenStorage);
  final TokenStorage _tokenStorage;

  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    final token = await _tokenStorage.read();
    if (token != null && token.isNotEmpty) {
      options.headers['Authorization'] = 'Token $token';
    }
    handler.next(options);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    if (err.response?.statusCode == 401) {
      // Fire-and-forget: drop the bad token so the auth gate kicks in.
      _tokenStorage.clear();
    }
    handler.next(err);
  }
}

class ApiException implements Exception {
  const ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Translate a DioException into a human-friendly message.
String describeDioError(Object error) {
  if (error is DioException) {
    final data = error.response?.data;
    if (data is Map) {
      // DRF token-auth returns {"non_field_errors": ["..."]}.
      if (data['non_field_errors'] is List &&
          (data['non_field_errors'] as List).isNotEmpty) {
        return (data['non_field_errors'] as List).first.toString();
      }
      if (data['detail'] is String) return data['detail'] as String;
    }
    if (error.type == DioExceptionType.connectionTimeout ||
        error.type == DioExceptionType.connectionError) {
      return 'Cannot reach server. Check your connection.';
    }
    return 'Request failed (${error.response?.statusCode ?? 'no status'})';
  }
  return error.toString();
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final tokenStorageProvider = Provider<TokenStorage>((ref) => TokenStorage());

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(ref.watch(tokenStorageProvider));
});
