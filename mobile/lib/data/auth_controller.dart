import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'api_client.dart';

enum AuthStatus { unknown, signedOut, signedIn }

class AuthState {
  const AuthState({required this.status, this.username, this.error});

  final AuthStatus status;
  final String? username;
  final String? error;

  AuthState copyWith({AuthStatus? status, String? username, String? error}) {
    return AuthState(
      status: status ?? this.status,
      username: username ?? this.username,
      error: error,
    );
  }
}

class AuthController extends AsyncNotifier<AuthState> {
  @override
  Future<AuthState> build() async {
    final storage = ref.read(tokenStorageProvider);
    final token = await storage.read();
    return AuthState(
      status: (token == null || token.isEmpty)
          ? AuthStatus.signedOut
          : AuthStatus.signedIn,
    );
  }

  Future<void> signIn(String username, String password) async {
    state = const AsyncValue.loading();
    try {
      final api = ref.read(apiClientProvider);
      await api.login(username, password);
      state = AsyncValue.data(
        AuthState(status: AuthStatus.signedIn, username: username),
      );
    } catch (e, st) {
      state = AsyncValue.data(
        AuthState(status: AuthStatus.signedOut, error: describeDioError(e)),
      );
      // Keep stack trace handy for debug builds without re-throwing.
      assert(() {
        // ignore: avoid_print
        print('signIn failed: $e\n$st');
        return true;
      }());
    }
  }

  Future<void> signOut() async {
    await ref.read(apiClientProvider).logout();
    state = const AsyncValue.data(AuthState(status: AuthStatus.signedOut));
  }
}

final authControllerProvider =
    AsyncNotifierProvider<AuthController, AuthState>(AuthController.new);
