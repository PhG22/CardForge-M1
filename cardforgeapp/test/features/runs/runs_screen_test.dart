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

  testWidgets('a run opens its results: folders, counts and every card',
      (tester) async {
    await pumpApp(tester, initialLocation: '/runs');
    await tester.pumpAndSettle();

    await tester.tap(find.text('2026-11-10 19:42'));
    await tester.pumpAndSettle();

    expect(find.text('Run results'), findsOneWidget);
    expect(find.text('Finished with the stack empty: 9 cards sorted.'),
        findsOneWidget);
    expect(find.text('Folder 4  ×2'), findsOneWidget);
    expect(find.text('Folder 6  ×3'), findsOneWidget);
    expect(find.text('Swords to Plowshares'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Face-down card'), 200);
    expect(find.text('Face-down card'), findsOneWidget);
    expect(find.text('Unidentified card'), findsOneWidget);
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
