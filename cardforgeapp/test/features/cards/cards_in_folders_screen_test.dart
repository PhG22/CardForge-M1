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
  }) =>
      Future<CardsPage>.error(const CardsException(CardsFailure.unauthorized));
}

void main() {
  setUpAll(configureTestEnvironment);
  setUp(() => resetAuthState(stored: {'access_token': 'abc'}));

  Future<void> openCards(
    WidgetTester tester, {
    CardRepository? repository,
    Map<String, Object> preferences = const {},
  }) async {
    await pumpApp(
      tester,
      initialLocation: '/cards',
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

  /// The panel (card tile) that holds [cardName].
  Finder tileOf(String cardName) => find.ancestor(
        of: find.text(cardName),
        matching: find.byType(MagicPanel),
      );

  Finder inTile(String cardName, String text) =>
      find.descendant(of: tileOf(cardName), matching: find.text(text));

  testWidgets('with nothing typed, lists the cards that are in the folders',
      (tester) async {
    await openCards(tester);

    expect(find.text('Search a card by name'), findsOneWidget);
    expect(find.text('Cards in your folders'), findsOneWidget);
    expect(find.text('36 cards'), findsOneWidget);
    expect(find.text('Arcane Signet'), findsOneWidget);
    expect(inTile('Birds of Paradise', 'Folder 5'), findsOneWidget);
    expect(inTile('Birds of Paradise', '×2'), findsOneWidget);
  });

  testWidgets('shows the symbols at the top instead of the colored circles',
      (tester) async {
    await openCards(tester);

    expect(find.byKey(const ValueKey('mana-symbols')), findsOneWidget);
    expect(find.byType(MagicBackdrop), findsNothing);
    // The search bar sits below the symbols.
    final symbolsBottom =
        tester.getRect(find.byKey(const ValueKey('mana-symbols'))).bottom;
    expect(tester.getTopLeft(find.byType(TextField)).dy,
        greaterThanOrEqualTo(symbolsBottom));
  });

  testWidgets('shows the folders a searched card is in', (tester) async {
    await openCards(tester);

    await search(tester, 'lightning bolt');

    expect(find.text('Results for “lightning bolt”'), findsOneWidget);
    expect(find.text('1 card'), findsOneWidget);
    expect(inTile('Lightning Bolt', 'Folder 4'), findsOneWidget);
    expect(inTile('Lightning Bolt', '×4'), findsOneWidget);
    expect(inTile('Lightning Bolt', 'Folder 6'), findsOneWidget);
    expect(inTile('Lightning Bolt', '×1'), findsOneWidget);
    expect(find.text('Counterspell'), findsNothing);
  });

  testWidgets('a search can match several cards', (tester) async {
    await openCards(tester);

    await search(tester, 'growth');

    expect(find.text('2 cards'), findsOneWidget);
    expect(find.text('Giant Growth'), findsOneWidget);
    expect(find.text('Rampant Growth'), findsOneWidget);
    // Giant Growth is in two folders.
    expect(inTile('Giant Growth', 'Folder 2'), findsOneWidget);
    expect(inTile('Giant Growth', 'Folder 5'), findsOneWidget);
  });

  testWidgets('says so when no card matches', (tester) async {
    await openCards(tester);

    await search(tester, 'zzz');

    expect(find.text('No cards found'), findsOneWidget);
    expect(find.text('Check the spelling or try another name.'),
        findsOneWidget);
  });

  testWidgets('the clear button brings back the full list', (tester) async {
    await openCards(tester);
    await search(tester, 'opt');
    expect(find.text('Results for “opt”'), findsOneWidget);

    await tester.tap(find.byTooltip('Clear search'));
    await tester.pumpAndSettle();

    expect(find.text('Cards in your folders'), findsOneWidget);
    expect(find.byTooltip('Clear search'), findsNothing);
    expect(tester.widget<TextField>(find.byType(TextField)).controller?.text,
        isEmpty);
  });

  testWidgets('loads more cards while scrolling', (tester) async {
    await openCards(tester);
    expect(find.text('Wrath of God'), findsNothing);

    await tester.scrollUntilVisible(
      find.text('Wrath of God'),
      600,
      scrollable: find
          .descendant(
              of: find.byType(ListView), matching: find.byType(Scrollable))
          .first,
    );

    expect(find.text('Wrath of God'), findsOneWidget);
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

    expect(find.text('Cards in your folders'), findsOneWidget);
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

    expect(find.text('Pesquisar carta pelo nome'), findsOneWidget);
    expect(find.text('Cartas nas suas pastas'), findsOneWidget);
    expect(find.text('36 cartas'), findsOneWidget);
    expect(inTile('Birds of Paradise', 'Pasta 5'), findsOneWidget);

    await search(tester, 'lightning bolt');
    expect(find.text('Resultados para “lightning bolt”'), findsOneWidget);
    expect(find.text('1 carta'), findsOneWidget);
  });
}
