import 'package:cardforge/features/auth/auth_service.dart';
import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

const _menuItems = [
  'Machine control',
  'Cards in folders',
  'CSV report',
  'Sorting rules',
  'Link machine',
  'Alerts',
  'Settings',
  'Sign out',
];

void main() {
  setUpAll(configureTestEnvironment);
  setUp(() => resetAuthState(stored: {'access_token': 'abc'}));

  Future<void> openDrawer(WidgetTester tester) async {
    await tester.tap(find.byType(DrawerButton));
    await tester.pumpAndSettle();
  }

  Finder inDrawer(String text) =>
      find.descendant(of: find.byType(Drawer), matching: find.text(text));

  testWidgets('shows the "no machine linked" state', (tester) async {
    await pumpApp(tester, initialLocation: '/home');

    expect(find.text('CARDFORGE'), findsOneWidget); // app bar title
    expect(find.text('No machine linked'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Link machine'), findsOneWidget);
  });

  testWidgets('shows the linked machine instead of the link button',
      (tester) async {
    await pumpApp(
      tester,
      initialLocation: '/home',
      preferences: {
        'machine_id': 'B8:27:EB:00:A1:B2',
        'machine_name': 'CardForge-A1B2',
      },
    );

    expect(find.text('CardForge-A1B2'), findsOneWidget);
    expect(find.text('Linked to your account'), findsOneWidget);
    expect(find.text('No machine linked'), findsNothing);
  });

  testWidgets('the side menu lists every planned screen', (tester) async {
    await pumpApp(tester, initialLocation: '/home');
    await openDrawer(tester);

    for (final item in _menuItems) {
      expect(inDrawer(item), findsOneWidget, reason: item);
    }
  });

  testWidgets('a menu item without a screen closes the menu and says "coming soon"',
      (tester) async {
    await pumpApp(tester, initialLocation: '/home');
    await openDrawer(tester);

    await tester.tap(inDrawer('CSV report'));
    await tester.pumpAndSettle();

    expect(find.byType(Drawer), findsNothing);
    expect(find.text('CSV report is coming soon.'), findsOneWidget);
  });

  testWidgets('"Cards in folders" in the menu opens the cards screen',
      (tester) async {
    await pumpApp(tester, initialLocation: '/home');
    await openDrawer(tester);

    await tester.tap(inDrawer('Cards in folders'));
    await tester.pumpAndSettle();

    expect(find.text('Search a card by name'), findsOneWidget);
  });

  testWidgets('"Machine control" in the menu opens the control screen',
      (tester) async {
    await pumpApp(tester, initialLocation: '/home');
    await openDrawer(tester);

    await tester.tap(inDrawer('Machine control'));
    await tester.pumpAndSettle();

    // No machine yet: the screen offers to link one.
    expect(find.text('No machine linked'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Link machine'), findsOneWidget);
  });

  testWidgets('the linked machine shows its connection and opens control',
      (tester) async {
    await pumpApp(tester,
        initialLocation: '/home', preferences: linkedMachinePrefs);
    await tester.pumpAndSettle();

    expect(find.text('Online'), findsOneWidget);

    await tester.tap(find.text('CardForge-A1B2'));
    await tester.pumpAndSettle();

    expect(find.text('Start sorting'), findsOneWidget);
    expect(find.text('192.168.0.42:8000'), findsOneWidget);
  });

  testWidgets('"Settings" in the menu opens the settings screen',
      (tester) async {
    await pumpApp(tester, initialLocation: '/home');
    await openDrawer(tester);

    await tester.tap(inDrawer('Settings'));
    await tester.pumpAndSettle();

    expect(find.text('Language'), findsOneWidget);
  });

  testWidgets('the bell shows the unread count and opens the alerts screen',
      (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/home');
    expect(find.descendant(of: find.byType(Badge), matching: find.text('1')),
        findsNothing);

    deps.alerts.add(AlertType.wifiConnected, machineName: 'CardForge-A1B2');
    await tester.pump();

    expect(find.descendant(of: find.byType(Badge), matching: find.text('1')),
        findsOneWidget);

    await tester.tap(find.byIcon(Icons.notifications_outlined));
    await tester.pumpAndSettle();

    expect(find.text('No alerts yet'), findsNothing);
    expect(find.text('Machine connected to Wi-Fi'), findsWidgets);
  });

  testWidgets('"Sign out" ends the session and goes back to Login',
      (tester) async {
    await pumpApp(tester, initialLocation: '/home');
    expect(await AuthService.instance.hasSession(), isTrue);

    await openDrawer(tester);
    await tester.tap(inDrawer('Sign out'));
    await tester.pump(const Duration(milliseconds: 100));
    await tester.pumpAndSettle();

    expect(find.text('Sign in to manage your sorter'), findsOneWidget);
    expect(await AuthService.instance.hasSession(), isFalse);
  });
}
