import 'package:cardforge/core/services/server_health_service.dart';
import 'package:cardforge/features/auth/auth_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

/// Fake server: fails the first [failures] calls, then answers.
class _FlakyServer extends ServerHealthService {
  _FlakyServer({required this.failures});

  final int failures;
  int calls = 0;

  @override
  Future<bool> isServerReachable() async {
    calls++;
    return calls > failures;
  }
}

const _loginSubtitle = 'Sign in to manage your sorter';

void main() {
  setUpAll(configureTestEnvironment);

  testWidgets('without a saved session, goes to Login', (tester) async {
    await resetAuthState();
    await pumpApp(tester);

    expect(find.text('Connecting to server…'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text(_loginSubtitle), findsOneWidget);
  });

  testWidgets('with a saved session, goes straight to Home', (tester) async {
    await resetAuthState(stored: {'access_token': 'abc'});
    await pumpApp(tester);

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text('No machine linked'), findsOneWidget);
    expect(await AuthService.instance.hasSession(), isTrue);
  });

  testWidgets('shows an error when the server does not answer', (tester) async {
    await resetAuthState();
    await pumpApp(tester, healthService: _FlakyServer(failures: 99));

    await tester.pump(const Duration(seconds: 2));
    await tester.pump();

    expect(find.text('Could not reach the server'), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text(_loginSubtitle), findsNothing);
  });

  testWidgets('"Try again" checks the server again and continues',
      (tester) async {
    await resetAuthState();
    final server = _FlakyServer(failures: 1);
    await pumpApp(tester, healthService: server);

    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.text('Try again'), findsOneWidget);

    await tester.tap(find.text('Try again'));
    await tester.pump();
    expect(find.text('Connecting to server…'), findsOneWidget);

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(find.text(_loginSubtitle), findsOneWidget);
    expect(server.calls, 2);
  });

  testWidgets('shows the splash texts in Portuguese when saved',
      (tester) async {
    await resetAuthState();
    await pumpApp(tester, preferences: {'language_code': 'pt'});

    expect(find.text('Conectando ao servidor…'), findsOneWidget);
    expect(find.text('Organize suas cartas de Magic'), findsOneWidget);

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();
  });
}
