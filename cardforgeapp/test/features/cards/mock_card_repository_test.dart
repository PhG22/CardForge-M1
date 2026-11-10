import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/mock_card_repository.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final repo = MockCardRepository(latency: Duration.zero);

  test('lists every card alphabetically when there is no search', () async {
    final page = await repo.fetchCards(limit: 100);

    expect(page.total, 36);
    expect(page.items, hasLength(36));
    final names = page.items.map((c) => c.name).toList();
    expect(names, [...names]..sort());
  });

  test('every card is in folders numbered 1 to 6', () async {
    final page = await repo.fetchCards(limit: 100);

    for (final card in page.items) {
      expect(card.folders, isNotEmpty, reason: card.name);
      for (final entry in card.folders) {
        expect(entry.folder, inInclusiveRange(1, folderCount),
            reason: card.name);
      }
    }
    // All six folders hold cards.
    final used = {
      for (final c in page.items) for (final f in c.folders) f.folder
    };
    expect(used, {1, 2, 3, 4, 5, 6});
  });

  test('search ignores case and matches part of the name', () async {
    final page = await repo.fetchCards(query: 'LIGHTNING');

    expect(page.items.map((c) => c.name),
        ['Chain Lightning', 'Lightning Bolt', 'Lightning Helix']);
    expect(page.total, 3);
  });

  test('a card can be in more than one folder', () async {
    final page = await repo.fetchCards(query: 'lightning bolt');

    final card = page.items.single;
    expect(card.folders.map((f) => (f.folder, f.quantity)), [(4, 4), (6, 1)]);
  });

  test('a search with no match returns an empty page', () async {
    final page = await repo.fetchCards(query: 'zzz');

    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  test('pages follow each other without gaps and report the full total',
      () async {
    final first = await repo.fetchCards(offset: 0, limit: 30);
    final second = await repo.fetchCards(offset: 30, limit: 30);
    final beyond = await repo.fetchCards(offset: 90, limit: 30);

    expect(first.items, hasLength(30));
    expect(second.items, hasLength(6));
    expect(first.total, 36);
    expect(second.total, 36);
    expect(second.items.last.name, 'Wrath of God');
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
