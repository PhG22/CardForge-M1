import 'package:cardforge/features/auth/auth_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

// These tests run on the simulated backend (USE_MOCK=true, the default).
// 'access_token' is the key AuthService uses to store the token.
const _storage = FlutterSecureStorage();

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => resetAuthState());

  test('without a sign-in there is no session', () async {
    expect(await AuthService.instance.hasSession(), isFalse);
  });

  test('sign-in with "Remember me" stores the token in secure storage',
      () async {
    await AuthService.instance
        .signInWithPassword(identifier: 'fernando', password: '123456');

    expect(await _storage.read(key: 'access_token'), isNotNull);
    expect(await AuthService.instance.hasSession(), isTrue);
  });

  test('sign-in without "Remember me" keeps the session in memory only',
      () async {
    await AuthService.instance.signInWithPassword(
      identifier: 'fernando',
      password: '123456',
      remember: false,
    );

    expect(await _storage.read(key: 'access_token'), isNull);
    expect(await AuthService.instance.hasSession(), isTrue);
  });

  test('Google sign-in (simulated) stores the token', () async {
    await AuthService.instance.signInWithGoogle();

    expect(await _storage.read(key: 'access_token'), isNotNull);
    expect(await AuthService.instance.hasSession(), isTrue);
  });

  test('signOut clears both the stored and the in-memory session', () async {
    await AuthService.instance
        .signInWithPassword(identifier: 'a@b.com', password: '123456');
    await AuthService.instance.signOut();
    expect(await AuthService.instance.hasSession(), isFalse);

    await AuthService.instance.signInWithPassword(
      identifier: 'a@b.com',
      password: '123456',
      remember: false,
    );
    await AuthService.instance.signOut();
    expect(await AuthService.instance.hasSession(), isFalse);
    expect(await _storage.read(key: 'access_token'), isNull);
  });
}
