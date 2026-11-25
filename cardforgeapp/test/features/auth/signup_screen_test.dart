import 'package:cardforge/features/auth/auth_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

void main() {
  setUpAll(configureTestEnvironment);
  setUp(() => resetAuthState());

  final usernameField = find.byType(TextFormField).at(0);
  final emailField = find.byType(TextFormField).at(1);
  final passwordField = find.byType(TextFormField).at(2);
  final confirmField = find.byType(TextFormField).at(3);
  final createButton = find.widgetWithText(FilledButton, 'Create account');

  Future<void> openSignUp(WidgetTester tester) =>
      pumpApp(tester, initialLocation: '/signup');

  Future<void> fill(
    WidgetTester tester, {
    String username = 'luca',
    String email = 'luca@example.com',
    String password = 'correct-horse',
    String? confirm,
  }) async {
    await tester.enterText(usernameField, username);
    await tester.enterText(emailField, email);
    await tester.enterText(passwordField, password);
    await tester.enterText(confirmField, confirm ?? password);
  }

  testWidgets('asks for every field', (tester) async {
    await openSignUp(tester);

    await tester.tap(createButton);
    await tester.pump();

    expect(find.text('Choose a username'), findsOneWidget);
    expect(find.text('Enter your email'), findsOneWidget);
    expect(find.text('Enter your password'), findsOneWidget);
  });

  testWidgets('checks the username, email and password rules',
      (tester) async {
    await openSignUp(tester);

    await fill(tester,
        username: 'a b',
        email: 'luca@',
        password: 'short',
        confirm: 'different');
    await tester.tap(createButton);
    await tester.pump();

    expect(find.text("3 to 32 letters, digits, '.', '_' or '-' (no spaces)"),
        findsOneWidget);
    expect(find.text('Invalid email'), findsOneWidget);
    expect(find.text('Password must have at least 8 characters'),
        findsOneWidget);
    expect(find.text('The passwords do not match'), findsOneWidget);
  });

  testWidgets('creates the account, signs in and opens Home', (tester) async {
    await openSignUp(tester);

    await fill(tester);
    await tester.tap(createButton);
    await tester.pump(const Duration(seconds: 1)); // simulated server
    await tester.pumpAndSettle();

    expect(await AuthService.instance.hasSession(), isTrue);
    expect(find.text('No machine linked'), findsOneWidget);
  });

  testWidgets('shows why the server refused the account', (tester) async {
    await openSignUp(tester);

    await fill(tester, username: AuthService.mockTakenUsername);
    await tester.tap(createButton);
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();

    expect(find.text('This username is taken. Choose another one.'),
        findsOneWidget);
    expect(await AuthService.instance.hasSession(), isFalse);
  });

  testWidgets('"Sign in" goes back to the login screen', (tester) async {
    await openSignUp(tester);

    await tester.tap(find.widgetWithText(TextButton, 'Sign in'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
  });
}
