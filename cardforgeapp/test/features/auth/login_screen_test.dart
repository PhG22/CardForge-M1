import 'package:cardforge/features/auth/auth_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

void main() {
  setUpAll(configureTestEnvironment);
  setUp(() => resetAuthState());

  final identifierField = find.byType(TextFormField).at(0);
  final passwordField = find.byType(TextFormField).at(1);
  final signInButton = find.widgetWithText(FilledButton, 'Sign in');

  Future<void> openLogin(WidgetTester tester) =>
      pumpApp(tester, initialLocation: '/login');

  group('validation', () {
    testWidgets('shows errors when the fields are empty', (tester) async {
      await openLogin(tester);

      await tester.tap(signInButton);
      await tester.pump();

      expect(find.text('Enter your username or email'), findsOneWidget);
      expect(find.text('Enter your password'), findsOneWidget);
    });

    testWidgets('rejects a malformed email', (tester) async {
      await openLogin(tester);

      await tester.enterText(identifierField, 'fernando@');
      await tester.enterText(passwordField, '123456');
      await tester.tap(signInButton);
      await tester.pump();

      expect(find.text('Invalid email'), findsOneWidget);
      expect(find.text('Enter your password'), findsNothing);
    });

    testWidgets('rejects a password shorter than 6 characters',
        (tester) async {
      await openLogin(tester);

      await tester.enterText(identifierField, 'fernando');
      await tester.enterText(passwordField, '123');
      await tester.tap(signInButton);
      await tester.pump();

      expect(find.text('Password must have at least 6 characters'),
          findsOneWidget);
      expect(find.text('Enter your username or email'), findsNothing);
    });
  });

  group('fields and controls', () {
    testWidgets('shows and hides the password', (tester) async {
      await openLogin(tester);

      bool obscured() => tester
          .widget<TextField>(find.descendant(
              of: passwordField, matching: find.byType(TextField)))
          .obscureText;

      expect(obscured(), isTrue);

      await tester.tap(find.byIcon(Icons.visibility_outlined));
      await tester.pump();

      expect(obscured(), isFalse);
      expect(find.byIcon(Icons.visibility_off_outlined), findsOneWidget);
    });

    testWidgets('"Remember me" starts checked and can be unchecked',
        (tester) async {
      await openLogin(tester);

      expect(tester.widget<Checkbox>(find.byType(Checkbox)).value, isTrue);

      await tester.tap(find.byType(Checkbox));
      await tester.pump();

      expect(tester.widget<Checkbox>(find.byType(Checkbox)).value, isFalse);
    });

    testWidgets('"Forgot password?" and "Create account" show a notice',
        (tester) async {
      await openLogin(tester);

      await tester.tap(find.text('Forgot password?'));
      await tester.pump();
      expect(find.text('Password recovery is coming soon.'), findsOneWidget);

      await tester.tap(find.text('Create account'));
      await tester.pump();
      expect(find.text('Sign-up is coming soon.'), findsOneWidget);
    });

    testWidgets('shows the screen in Portuguese when that language is saved',
        (tester) async {
      await pumpApp(
        tester,
        initialLocation: '/login',
        preferences: {'language_code': 'pt'},
      );

      expect(find.widgetWithText(FilledButton, 'Entrar'), findsOneWidget);
      expect(find.text('Esqueci minha senha'), findsOneWidget);
      expect(find.text('Sign in'), findsNothing);

      await tester.tap(find.widgetWithText(FilledButton, 'Entrar'));
      await tester.pump();
      expect(find.text('Informe seu usuário ou e-mail'), findsOneWidget);
    });
  });

  group('sign-in (simulated backend)', () {
    testWidgets('valid credentials go to Home and save the session',
        (tester) async {
      await openLogin(tester);

      await tester.enterText(identifierField, 'fernando@email.com');
      await tester.enterText(passwordField, '123456');
      await tester.tap(signInButton);
      await tester.pump();

      // While sending: spinner and disabled button.
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      expect(tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
          isNull);

      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(find.text('No machine linked'), findsOneWidget);
      expect(await AuthService.instance.hasSession(), isTrue);
      expect(await const FlutterSecureStorage().read(key: 'access_token'),
          isNotNull);
    });

    testWidgets('a username (without @) is also accepted', (tester) async {
      await openLogin(tester);

      await tester.enterText(identifierField, 'fernando');
      await tester.enterText(passwordField, '123456');
      await tester.tap(signInButton);
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(find.text('No machine linked'), findsOneWidget);
    });

    testWidgets('without "Remember me" the token is not saved on the device',
        (tester) async {
      await openLogin(tester);

      await tester.tap(find.byType(Checkbox));
      await tester.enterText(identifierField, 'fernando');
      await tester.enterText(passwordField, '123456');
      await tester.tap(signInButton);
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(find.text('No machine linked'), findsOneWidget);
      expect(await const FlutterSecureStorage().read(key: 'access_token'),
          isNull);
      expect(await AuthService.instance.hasSession(), isTrue);
    });

    testWidgets('"Continue with Google" also goes to Home', (tester) async {
      await openLogin(tester);

      await tester.tap(find.text('Continue with Google'));
      await tester.pump(const Duration(milliseconds: 900));
      await tester.pumpAndSettle();

      expect(find.text('No machine linked'), findsOneWidget);
    });
  });
}
