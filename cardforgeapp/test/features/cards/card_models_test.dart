import 'package:cardforge/features/cards/card_models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('there are six folders', () {
    expect(folderCount, 6);
  });

  test('reads a card and sorts its folders by number', () {
    final card = FolderedCard.fromJson({
      'id': 42,
      'name': 'Lightning Bolt',
      'folders': [
        {'folder': 6, 'quantity': 1},
        {'folder': 4, 'quantity': 4},
      ],
    });

    expect(card.id, '42');
    expect(card.name, 'Lightning Bolt');
    expect(card.folders.map((f) => f.folder), [4, 6]);
    expect(card.folders.first.quantity, 4);
  });

  test('ignores folders outside 1 to 6 and invalid quantities', () {
    final card = FolderedCard.fromJson({
      'id': 'a',
      'name': 'Opt',
      'folders': [
        {'folder': 0, 'quantity': 1},
        {'folder': 7, 'quantity': 1},
        {'folder': 2, 'quantity': 0},
        {'folder': 3, 'quantity': 2},
      ],
    });

    expect(card.folders.map((f) => f.folder), [3]);
  });

  test('a quantity is 1 when the server leaves it out, and no folders is fine',
      () {
    final withFolder = FolderedCard.fromJson({
      'id': 'a',
      'name': 'Opt',
      'folders': [
        {'folder': 2},
      ],
    });
    final without = FolderedCard.fromJson({'id': 'b', 'name': 'Shock'});

    expect(withFolder.folders.single.quantity, 1);
    expect(without.folders, isEmpty);
  });

  test('a page keeps the server total, or falls back to the item count', () {
    final withTotal = CardsPage.fromJson({
      'items': [
        {'id': '1', 'name': 'A'},
      ],
      'total': 40,
    });
    final withoutTotal = CardsPage.fromJson({
      'items': [
        {'id': '1', 'name': 'A'},
        {'id': '2', 'name': 'B'},
      ],
    });

    expect(withTotal.total, 40);
    expect(withTotal.items, hasLength(1));
    expect(withoutTotal.total, 2);
  });
}
