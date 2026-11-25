import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/mock_card_repository.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final repo = MockCardRepository(latency: Duration.zero);

  test('the collection is every identified card of the sample runs', () async {
    final page = await repo.fetchCards(limit: 100);

    expect(page.items.map((c) => c.name), [
      'Counterspell',
      'Doom Blade',
      'Giant Growth',
      'Lightning Bolt',
      'Llanowar Elves',
      'Opt',
      'Shock',
      'Sol Ring',
      'Swords to Plowshares',
      'Wall of Omens',
    ]);
    expect(page.total, 10);
    expect(page.items.firstWhere((c) => c.name == 'Lightning Bolt').quantity,
        2);
    expect(page.notIdentified, 2);
  });

  test('counts the copies per color', () async {
    final page = await repo.fetchCards(limit: 100);

    expect(page.colorCounts, {
      ColorGroup.white: 2,
      ColorGroup.blue: 2,
      ColorGroup.black: 1,
      ColorGroup.red: 3,
      ColorGroup.green: 2,
      ColorGroup.colorless: 1,
      ColorGroup.multicolor: 0,
    });
  });

  test('a color keeps only that group, and the counts stay the same',
      () async {
    final red = await repo.fetchCards(color: ColorGroup.red);
    final colorless = await repo.fetchCards(color: ColorGroup.colorless);

    expect(red.items.map((c) => c.name), ['Lightning Bolt', 'Shock']);
    expect(red.total, 2);
    expect(red.colorCounts[ColorGroup.white], 2);
    expect(colorless.items.single.name, 'Sol Ring');
  });

  test('a run keeps only its own cards', () async {
    final run2 = await repo.fetchCards(runId: '2');
    final run1 = await repo.fetchCards(runId: '1');

    expect(run2.items.map((c) => c.name), ['Opt', 'Shock', 'Wall of Omens']);
    expect(run2.notIdentified, 0);
    expect(run1.items.single.name, 'Giant Growth');
  });

  test('search ignores case and matches part of the name', () async {
    final page = await repo.fetchCards(query: 'LL');

    expect(page.items.map((c) => c.name),
        ['Counterspell', 'Llanowar Elves', 'Wall of Omens']);
    expect(page.total, 3);
  });

  test('pages follow each other without gaps and report the full total',
      () async {
    final first = await repo.fetchCards(offset: 0, limit: 6);
    final second = await repo.fetchCards(offset: 6, limit: 6);
    final beyond = await repo.fetchCards(offset: 30, limit: 6);

    expect(first.items, hasLength(6));
    expect(second.items, hasLength(4));
    expect(second.total, 10);
    expect(second.items.last.name, 'Wall of Omens');
    expect(beyond.items, isEmpty);
  });

  test('searching for "fail" simulates a server error', () async {
    expect(
      () => repo.fetchCards(query: MockCardRepository.failingQuery),
      throwsA(isA<CardsException>()
          .having((e) => e.failure, 'failure', CardsFailure.serverError)),
    );
  });
}
