import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../helpers/test_helpers.dart';

void main() {
  setUpAll(configureTestEnvironment);

  testWidgets('English is the default language', (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/settings');

    expect(deps.locale.locale.languageCode, 'en');
    expect(find.text('Language'), findsOneWidget);
    expect(find.text('Settings'), findsOneWidget);

    // The check mark is on English.
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('language-en')),
        matching: find.byIcon(Icons.check_rounded),
      ),
      findsOneWidget,
    );
  });

  testWidgets('switches the whole app to Portuguese and remembers the choice',
      (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/settings');

    await tester.tap(find.byKey(const ValueKey('language-pt')));
    await tester.pumpAndSettle();

    expect(find.text('Idioma'), findsOneWidget);
    expect(find.text('Configurações'), findsOneWidget);
    expect(find.text('Language'), findsNothing);
    expect(deps.locale.locale.languageCode, 'pt');

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('language_code'), 'pt');
  });

  testWidgets('can switch back to English', (tester) async {
    await pumpApp(
      tester,
      initialLocation: '/settings',
      preferences: {'language_code': 'pt'},
    );
    expect(find.text('Idioma'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('language-en')));
    await tester.pumpAndSettle();

    expect(find.text('Language'), findsOneWidget);
    expect(find.text('Idioma'), findsNothing);
  });

  testWidgets('developer tools simulate an alert and show the banner',
      (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/settings');
    expect(find.text('Developer tools'), findsOneWidget);

    await tester.tap(find.widgetWithText(ActionChip, 'Sorting finished'));
    await tester.pump();

    expect(deps.alerts.alerts.single.type, AlertType.batchFinished);

    final banner = find.byType(SnackBar);
    expect(find.descendant(of: banner, matching: find.text('Sorting finished')),
        findsOneWidget);
    expect(
      find.descendant(
          of: banner, matching: find.text('CardForge-A1B2 processed 128 cards.')),
      findsOneWidget,
    );
  });
}
