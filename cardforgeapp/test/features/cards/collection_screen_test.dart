import 'package:cardforge/core/widgets/magic_backdrop.dart';
import 'package:cardforge/core/widgets/magic_panel.dart';
import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/card_repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/test_helpers.dart';

class _UnauthorizedRepository implements CardRepository {
  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
    String? runId,
    ColorGroup? color,
  }) =>
      Future<CardsPage>.error(const CardsException(CardsFailure.unauthorized));
}

void main() {
  setUpAll(configureTestEnvironment);
  setUp(() => resetAuthState(stored: {'access_token': 'abc'}));

  Future<void> openCards(
    WidgetTester tester, {
    String location = '/cards',
    CardRepository? repository,
    Map<String, Object> preferences = const {},
  }) async {
    await pumpApp(
      tester,
      initialLocation: location,
      cardRepository: repository,
      preferences: preferences,
    );
    await tester.pumpAndSettle();
  }

  Future<void> search(WidgetTester tester, String text) async {
    await tester.enterText(find.byType(TextField), text);
    await tester.pump(const Duration(milliseconds: 500)); // debounce
    await tester.pumpAndSettle();
  }

  Future<void> pickColor(WidgetTester tester, String key) async {
    await tester.tap(find.byKey(ValueKey('color-$key')));
    await tester.pumpAndSettle();
  }

  Future<void> pickScope(WidgetTester tester, String label) async {
    await tester.tap(find.byKey(const ValueKey('scope-picker')));
    await tester.pumpAndSettle();
    await tester.tap(find.text(label).last);
    await tester.pumpAndSettle();
  }

  /// Scrolls the card list (not the search field) until [text] is on screen.
  Future<void> reveal(WidgetTester tester, String text) =>
      tester.scrollUntilVisible(
        find.text(text),
        200,
        scrollable: find
            .descendant(
                of: find.byType(ListView), matching: find.byType(Scrollable))
            .first,
      );

  /// The panel (card tile) that holds [cardName].
  Finder tileOf(String cardName) => find.ancestor(
        of: find.text(cardName),
        matching: find.byType(MagicPanel),
      );

  Finder inTile(String cardName, String text) =>
      find.descendant(of: tileOf(cardName), matching: find.text(text));

  testWidgets('lists the whole collection by default, with no folders',
      (tester) async {
    await openCards(tester);

    expect(find.text('My cards'), findsOneWidget);
    expect(find.text('All my runs (whole collection)'), findsOneWidget);
    expect(find.text('Search a card by name'), findsOneWidget);
    expect(find.text('All your cards'), findsOneWidget);
    expect(find.text('10 cards'), findsOneWidget);
    expect(inTile('Counterspell', '×1'), findsOneWidget);
    expect(inTile('Counterspell', 'Blue · MH2'), findsOneWidget);
    expect(find.textContaining('Folder'), findsNothing);
    expect(find.textContaining('2 cards were not identified'), findsOneWidget);
  });

  testWidgets('the color chips show their counts and filter the list',
      (tester) async {
    await openCards(tester);

    expect(find.text('All colors  11'), findsOneWidget);
    expect(find.text('Red  3'), findsOneWidget);
    expect(find.text('Colorless  1'), findsOneWidget);
    expect(find.text('Multicolor  0'), findsOneWidget);

    await pickColor(tester, 'red');
    expect(find.text('2 cards'), findsOneWidget);
    expect(find.text('Lightning Bolt'), findsOneWidget);
    expect(find.text('Shock'), findsOneWidget);
    expect(find.text('Counterspell'), findsNothing);

    await pickColor(tester, 'colorless');
    expect(find.text('1 card'), findsOneWidget);
    expect(find.text('Sol Ring'), findsOneWidget);

    await pickColor(tester, 'multicolor');
    expect(find.text('No cards found'), findsOneWidget);

    await pickColor(tester, 'all');
    expect(find.text('10 cards'), findsOneWidget);
  });

  testWidgets('picking a run shows only the cards of that run',
      (tester) async {
    await openCards(tester);

    await pickScope(tester, '2026-11-08 15:05 · 3 cards');

    expect(find.text('Cards of this run'), findsOneWidget);
    expect(find.text('3 cards'), findsOneWidget);
    await reveal(tester, 'Wall of Omens');
    expect(find.text('Opt'), findsOneWidget);
    expect(find.text('Wall of Omens'), findsOneWidget);
    expect(find.text('Lightning Bolt'), findsNothing);

    await pickScope(tester, 'All my runs (whole collection)');
    expect(find.text('10 cards'), findsOneWidget);
  });

  testWidgets('opens on one run from /cards?run=<id>', (tester) async {
    await openCards(tester, location: '/cards?run=1');

    expect(find.text('Cards of this run'), findsOneWidget);
    expect(find.text('Giant Growth'), findsOneWidget);
    expect(find.text('1 card'), findsOneWidget);
  });

  testWidgets('shows the symbols at the top instead of the colored circles',
      (tester) async {
    await openCards(tester);

    expect(find.byKey(const ValueKey('mana-symbols')), findsOneWidget);
    expect(find.byType(MagicBackdrop), findsNothing);
    final symbolsBottom =
        tester.getRect(find.byKey(const ValueKey('mana-symbols'))).bottom;
    expect(tester.getTopLeft(find.byType(TextField)).dy,
        greaterThanOrEqualTo(symbolsBottom));
  });

  testWidgets('a search narrows the list', (tester) async {
    await openCards(tester);

    await search(tester, 'lightning bolt');

    expect(find.text('Results for “lightning bolt”'), findsOneWidget);
    expect(find.text('1 card'), findsOneWidget);
    expect(inTile('Lightning Bolt', '×2'), findsOneWidget);
    expect(inTile('Lightning Bolt', 'Red · 2XM'), findsOneWidget);
    expect(find.text('Counterspell'), findsNothing);
  });

  testWidgets('says so when no card matches', (tester) async {
    await openCards(tester);

    await search(tester, 'zzz');

    expect(find.text('No cards found'), findsOneWidget);
    expect(find.text('Check the spelling, or try another name or color.'),
        findsOneWidget);
  });

  testWidgets('the clear button brings back the full list', (tester) async {
    await openCards(tester);
    await search(tester, 'opt');
    expect(find.text('Results for “opt”'), findsOneWidget);

    await tester.tap(find.byTooltip('Clear search'));
    await tester.pumpAndSettle();

    expect(find.text('All your cards'), findsOneWidget);
    expect(find.byTooltip('Clear search'), findsNothing);
    expect(tester.widget<TextField>(find.byType(TextField)).controller?.text,
        isEmpty);
  });

  testWidgets('shows a server error and recovers when the search changes',
      (tester) async {
    await openCards(tester);

    await search(tester, 'fail');

    expect(find.text('Could not load the cards'), findsOneWidget);
    expect(find.text('The server had a problem. Please try again.'),
        findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Try again'), findsOneWidget);

    await tester.tap(find.byTooltip('Clear search'));
    await tester.pumpAndSettle();

    expect(find.text('All your cards'), findsOneWidget);
  });

  testWidgets('an expired session offers to sign in again', (tester) async {
    await openCards(tester, repository: _UnauthorizedRepository());

    expect(find.text('Your session expired. Please sign in again.'),
        findsOneWidget);

    await tester.tap(find.widgetWithText(FilledButton, 'Sign in again'));
    await tester.pumpAndSettle();

    expect(find.text('Sign in to manage your sorter'), findsOneWidget);
  });

  testWidgets('works in Portuguese', (tester) async {
    await openCards(tester, preferences: {'language_code': 'pt'});

    expect(find.text('Minhas cartas'), findsOneWidget);
    expect(find.text('Pesquisar carta pelo nome'), findsOneWidget);
    expect(find.text('Todas as suas cartas'), findsOneWidget);
    expect(find.text('10 cartas'), findsOneWidget);
    expect(find.text('Vermelho  3'), findsOneWidget);
    expect(find.text('Incolor  1'), findsOneWidget);

    await search(tester, 'lightning bolt');
    expect(find.text('Resultados para “lightning bolt”'), findsOneWidget);
    expect(find.text('1 carta'), findsOneWidget);
  });
}
