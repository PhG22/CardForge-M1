import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/runs/run_models.dart';
import 'package:cardforge/features/runs/run_repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

class _ExpiredSession implements RunRepository {
  @override
  Future<RunsPage> fetchRuns({int offset = 0, int limit = 30}) async =>
      throw const CardsException(CardsFailure.unauthorized);

  @override
  Future<RunDetails> fetchRun(String id) async =>
      throw const CardsException(CardsFailure.unauthorized);
}

class _NoRuns implements RunRepository {
  @override
  Future<RunsPage> fetchRuns({int offset = 0, int limit = 30}) async =>
      const RunsPage(runs: [], total: 0);

  @override
  Future<RunDetails> fetchRun(String id) async =>
      throw const CardsException(CardsFailure.serverError);
}

void main() {
  setUpAll(configureTestEnvironment);
  setUp(() => resetAuthState(stored: {'access_token': 'abc'}));

  testWidgets('lists the runs, newest first, with how they ended',
      (tester) async {
    await pumpApp(tester, initialLocation: '/runs');
    await tester.pumpAndSettle();

    expect(find.text('2026-11-10 19:42'), findsOneWidget);
    expect(find.text('Finished · 9 cards, 7 identified'), findsOneWidget);
    expect(find.text('Stopped · 3 cards, 3 identified'), findsOneWidget);
    expect(find.text('Ended early · 1 card, 1 identified'), findsOneWidget);
  });

  testWidgets('a run opens its results: colors, counts and every card',
      (tester) async {
    await pumpApp(tester, initialLocation: '/runs');
    await tester.pumpAndSettle();

    await tester.tap(find.text('2026-11-10 19:42'));
    await tester.pumpAndSettle();

    expect(find.text('Run results'), findsOneWidget);
    expect(find.text('Finished with the stack empty: 9 cards sorted.'),
        findsOneWidget);
    expect(find.text('Cards per color'), findsOneWidget);
    expect(find.text('All colors  9'), findsOneWidget);
    expect(find.text('Red  2'), findsOneWidget);
    expect(find.text('Colorless  1'), findsOneWidget);
    expect(find.text('Not identified  2'), findsOneWidget);
    expect(find.textContaining('Folder'), findsNothing);
    // The card list is below the color panel: scroll down to it.
    await tester.scrollUntilVisible(find.text('Swords to Plowshares'), 200);
    expect(find.text('Swords to Plowshares'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Unidentified card'), 200);
    expect(find.text('Unidentified card'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Face-down card'), 200);
    expect(find.text('Face-down card'), findsOneWidget);
  });

  testWidgets('a color chip shows only the cards of that color',
      (tester) async {
    await pumpApp(tester, initialLocation: '/runs/3');
    await tester.pumpAndSettle();

    // A filtered list is short: scrolled to the end, all of it is on screen.
    Future<void> toBottom() async {
      await tester.drag(find.byType(ListView), const Offset(0, -2000));
      await tester.pumpAndSettle();
    }

    Future<void> toTop() async {
      await tester.drag(find.byType(ListView), const Offset(0, 2000));
      await tester.pumpAndSettle();
    }

    // Bring each chip on screen before tapping it: the chip panel is taller
    // than the 844 px test screen.
    Future<void> tapChip(String key) async {
      final chip = find.byKey(ValueKey(key));
      await tester.ensureVisible(chip);
      await tester.pumpAndSettle();
      await tester.tap(chip);
      await tester.pumpAndSettle();
    }

    await tapChip('color-red');
    await toBottom();
    expect(find.text('Lightning Bolt'), findsNWidgets(2));
    expect(find.text('Swords to Plowshares'), findsNothing);

    await toTop();
    await tapChip('color-not-identified');
    await toBottom();
    expect(find.text('Unidentified card'), findsOneWidget);
    expect(find.text('Face-down card'), findsOneWidget);
    expect(find.text('Lightning Bolt'), findsNothing);
  });

  testWidgets('the results link to the run\'s cards and its export',
      (tester) async {
    await pumpApp(tester, initialLocation: '/runs/2');
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Export this run'));
    await tester.pumpAndSettle();
    expect(find.text('Export cards'), findsOneWidget);
    expect(find.text('2026-11-08 15:05 · 3 cards'), findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('My cards'));
    await tester.pumpAndSettle();
    expect(find.text('Cards of this run'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Wall of Omens'),
      200,
      scrollable: find
          .descendant(
              of: find.byType(ListView), matching: find.byType(Scrollable))
          .first,
    );
    expect(find.text('Wall of Omens'), findsOneWidget);
  });

  testWidgets('an expired session offers to sign in again', (tester) async {
    await pumpApp(tester,
        initialLocation: '/runs', runRepository: _ExpiredSession());
    await tester.pumpAndSettle();

    expect(find.text('Could not load your runs'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in again'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
  });

  testWidgets('no runs yet', (tester) async {
    await pumpApp(tester, initialLocation: '/runs', runRepository: _NoRuns());
    await tester.pumpAndSettle();

    expect(find.text('No runs yet'), findsOneWidget);
  });
}
