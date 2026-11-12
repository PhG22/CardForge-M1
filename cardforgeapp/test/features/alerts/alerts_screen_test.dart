import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

void main() {
  setUpAll(configureTestEnvironment);

  Finder inList(String text) =>
      find.descendant(of: find.byType(ListView), matching: find.text(text));

  testWidgets('shows an empty state and disables the actions', (tester) async {
    await pumpApp(tester, initialLocation: '/alerts');

    expect(find.text('No alerts yet'), findsOneWidget);
    expect(find.text('Machine events will show up here.'), findsOneWidget);

    final markAll = tester.widget<IconButton>(
        find.widgetWithIcon(IconButton, Icons.done_all_rounded));
    final clear = tester.widget<IconButton>(
        find.widgetWithIcon(IconButton, Icons.delete_outline_rounded));
    expect(markAll.onPressed, isNull);
    expect(clear.onPressed, isNull);
  });

  testWidgets('lists the alerts, newest first, with translated text',
      (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/alerts');

    deps.alerts.add(AlertType.wifiConnected, machineName: 'CardForge-A1B2');
    deps.alerts.add(AlertType.jamDetected, machineName: 'CardForge-A1B2');
    await tester.pump();

    expect(inList('Card jam detected'), findsOneWidget);
    expect(
        find.descendant(
          of: find.byType(ListView),
          matching: find.textContaining(
              'no card reached the platform. Clear the jam, then press Resume.'),
        ),
        findsOneWidget);
    expect(inList('Machine connected to Wi-Fi'), findsOneWidget);

    final jamY = tester.getTopLeft(inList('Card jam detected')).dy;
    final wifiY = tester.getTopLeft(inList('Machine connected to Wi-Fi')).dy;
    expect(jamY, lessThan(wifiY));
  });

  testWidgets('alerts follow the app language', (tester) async {
    final deps = await pumpApp(
      tester,
      initialLocation: '/alerts',
      preferences: {'language_code': 'pt'},
    );

    deps.alerts.add(AlertType.batchFinished,
        machineName: 'CardForge-A1B2', cardCount: 12);
    await tester.pump();

    expect(inList('Ordenação concluída'), findsOneWidget);
    expect(inList('CardForge-A1B2 processou 12 cartas.'), findsOneWidget);
  });

  testWidgets('tapping an alert marks it as read', (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/alerts');
    deps.alerts.add(AlertType.wifiConnected);
    deps.alerts.add(AlertType.connectionLost);
    await tester.pump();
    expect(find.byKey(const ValueKey('unread-dot')), findsNWidgets(2));

    await tester.tap(inList('Connection lost'));
    await tester.pump();

    expect(find.byKey(const ValueKey('unread-dot')), findsOneWidget);
    expect(deps.alerts.unreadCount, 1);
  });

  testWidgets('"Mark all as read" and "Clear all" work', (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/alerts');
    deps.alerts.add(AlertType.wifiConnected);
    deps.alerts.add(AlertType.doubleFeed);
    await tester.pump();

    await tester.tap(find.byTooltip('Mark all as read'));
    await tester.pump();
    expect(find.byKey(const ValueKey('unread-dot')), findsNothing);
    expect(deps.alerts.unreadCount, 0);

    await tester.tap(find.byTooltip('Clear all'));
    await tester.pump();
    expect(find.text('No alerts yet'), findsOneWidget);
  });

  testWidgets('a new alert shows a banner on any screen, with a View action',
      (tester) async {
    final deps = await pumpApp(tester, initialLocation: '/home');

    deps.alerts.add(AlertType.batchFinished,
        machineName: 'CardForge-A1B2', cardCount: 128);
    await tester.pump();
    // Let the banner finish growing in (Material 3 animates its height from
    // zero), so "View" can be tapped.
    await tester.pump(const Duration(milliseconds: 500));

    final banner = find.byType(SnackBar);
    expect(find.descendant(of: banner, matching: find.text('Sorting finished')),
        findsOneWidget);
    expect(
      find.descendant(
          of: banner, matching: find.text('CardForge-A1B2 processed 128 cards.')),
      findsOneWidget,
    );

    await tester.tap(find.text('View'));
    await tester.pumpAndSettle();

    // Now on the alerts screen, where the alert is listed.
    expect(inList('Sorting finished'), findsOneWidget);
  });
}