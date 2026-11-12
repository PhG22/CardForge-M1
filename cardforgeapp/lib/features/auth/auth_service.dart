import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:http/http.dart' as http;

import '../../core/config/app_config.dart';

/// Why a sign-in failed. The UI turns each one into a translated message.
enum AuthFailure {
  invalidCredentials,
  serverUnreachable,
  serverError,
  googleCancelled,
  googleTokenMissing,

  /// Sign-up: another account already uses this username.
  usernameTaken,

  /// Sign-up: another account already uses this email.
  emailTaken,

  /// Sign-up: the server refused a field (username, email or password rules).
  invalidSignUp,

  /// The account exists but was disabled on the server.
  accountDisabled,
}

class AuthException implements Exception {
  const AuthException(this.failure);

  final AuthFailure failure;

  @override
  String toString() => 'AuthException($failure)';
}

class AuthService {
  AuthService._();
  static final AuthService instance = AuthService._();

  static const _tokenKey = 'access_token';
  final _storage = const FlutterSecureStorage();
  final _google = GoogleSignIn(
    scopes: ['email', 'profile'],
    serverClientId: AppConfig.googleServerClientId.isEmpty
        ? null
        : AppConfig.googleServerClientId,
  );

  /// Token of the current session when the user did NOT check "Remember me"
  /// (it only lasts until the app is closed).
  String? _memoryToken;

  Future<bool> hasSession() async =>
      _memoryToken != null || (await _storage.read(key: _tokenKey)) != null;

  /// The token to send to the API (`Authorization: Bearer ...`), if any.
  Future<String?> accessToken() async =>
      _memoryToken ?? await _storage.read(key: _tokenKey);

  /// Sign in with username or email + password.
  Future<void> signInWithPassword({
    required String identifier,
    required String password,
    bool remember = true,
  }) async {
    if (AppConfig.useMockBackend) {
      await Future<void>.delayed(const Duration(milliseconds: 800));
      await _persist('mock-token', remember);
      return;
    }
    final res = await _post('/auth/login', {
      'identifier': identifier,
      'password': password,
    });
    await _saveToken(res, remember);
  }

  /// With the simulated backend, signing up with this username fails as
  /// "username taken" (to try the error message).
  static const String mockTakenUsername = 'taken';

  /// Creates an account on the server (`POST /auth/signup`). On success the
  /// user is signed in, like after [signInWithPassword].
  Future<void> signUp({
    required String username,
    required String email,
    required String password,
    bool remember = true,
  }) async {
    if (AppConfig.useMockBackend) {
      await Future<void>.delayed(const Duration(milliseconds: 800));
      if (username.trim().toLowerCase() == mockTakenUsername) {
        throw const AuthException(AuthFailure.usernameTaken);
      }
      await _persist('mock-token', remember);
      return;
    }
    final res = await _post('/auth/signup', {
      'username': username,
      'email': email,
      'password': password,
    });
    await _saveToken(res, remember);
  }

  /// Google sign-in: the app gets the idToken, the backend validates it and
  /// returns the session token.
  Future<void> signInWithGoogle() async {
    if (AppConfig.useMockBackend) {
      await Future<void>.delayed(const Duration(milliseconds: 800));
      await _persist('mock-token', true);
      return;
    }
    final account = await _google.signIn();
    if (account == null) throw const AuthException(AuthFailure.googleCancelled);
    final idToken = (await account.authentication).idToken;
    if (idToken == null) {
      throw const AuthException(AuthFailure.googleTokenMissing);
    }
    final res = await _post('/auth/google', {'id_token': idToken});
    await _saveToken(res, true);
  }

  Future<void> signOut() async {
    // The simulated backend never signs in with Google, so there is nothing to
    // sign out of. (Calling the plugin anyway never completes inside widget
    // tests, where there is no Android/iOS side to answer.)
    if (!AppConfig.useMockBackend) {
      try {
        await _google.signOut();
      } on Exception {
        // no active Google session: ignore
      }
    }
    _memoryToken = null;
    await _storage.delete(key: _tokenKey);
  }

  Future<void> _persist(String token, bool remember) async {
    if (remember) {
      _memoryToken = null;
      await _storage.write(key: _tokenKey, value: token);
    } else {
      await _storage.delete(key: _tokenKey);
      _memoryToken = token;
    }
  }

  Future<http.Response> _post(String path, Map<String, dynamic> body) async {
    try {
      return await http
          .post(
            Uri.parse('${AppConfig.apiBaseUrl}$path'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(AppConfig.healthTimeout);
    } on Exception {
      throw const AuthException(AuthFailure.serverUnreachable);
    }
  }

  Future<void> _saveToken(http.Response res, bool remember) async {
    if (res.statusCode == 200 || res.statusCode == 201) {
      final data = jsonDecode(res.body) as Map<String, dynamic>;
      await _persist(data['access_token'] as String, remember);
      return;
    }
    throw AuthException(switch (res.statusCode) {
      401 => AuthFailure.invalidCredentials,
      403 => AuthFailure.accountDisabled,
      409 => _errorCode(res) == 'EMAIL_TAKEN'
          ? AuthFailure.emailTaken
          : AuthFailure.usernameTaken,
      400 || 422 => AuthFailure.invalidSignUp,
      _ => AuthFailure.serverError,
    });
  }

  /// The `code` field of an error body, e.g. `EMAIL_TAKEN`.
  static String? _errorCode(http.Response res) {
    try {
      final body = jsonDecode(res.body);
      if (body is Map && body['code'] is String) return body['code'] as String;
    } on FormatException {
      // not JSON
    }
    return null;
  }
}