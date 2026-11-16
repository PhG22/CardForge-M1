import 'package:cardforge/features/cards/card_models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('color groups follow the machine: one color, colorless or multicolor',
      () {
    expect(ColorGroup.fromIdentity('W'), ColorGroup.white);
    expect(ColorGroup.fromIdentity('u'), ColorGroup.blue);
    expect(ColorGroup.fromIdentity('B'), ColorGroup.black);
    expect(ColorGroup.fromIdentity('R'), ColorGroup.red);
    expect(ColorGroup.fromIdentity('G'), ColorGroup.green);
    expect(ColorGroup.fromIdentity(''), ColorGroup.colorless);
    expect(ColorGroup.fromIdentity(null), ColorGroup.colorless);
    expect(ColorGroup.fromIdentity('RW'), ColorGroup.multicolor);
    expect(ColorGroup.fromIdentity('{W}{U}'), ColorGroup.multicolor);
  });

  test('color groups read and write the server values', () {
    expect(ColorGroup.values.map((g) => g.apiValue),
        ['W', 'U', 'B', 'R', 'G', 'colorless', 'multicolor']);
    expect(ColorGroup.fromApi('R'), ColorGroup.red);
    expect(ColorGroup.fromApi('Multicolor'), ColorGroup.multicolor);
    expect(ColorGroup.fromApi('purple'), isNull);
    expect(ColorGroup.fromApi(null), isNull);
  });

  test('reads a card of the collection, without folders', () {
    final card = CollectionCard.fromJson({
      'id': 42,
      'name': 'Lightning Bolt',
      'quantity': 3,
      'color': 'R',
      'color_identity': 'R',
      'type_line': 'Instant',
      'sets': 'FIN, M10',
    });

    expect(card.id, '42');
    expect(card.name, 'Lightning Bolt');
    expect(card.quantity, 3);
    expect(card.color, ColorGroup.red);
    expect(card.sets, 'FIN, M10');
    expect(card.typeLine, 'Instant');
  });

  test('without a color field, the color comes from the identity', () {
    final card = CollectionCard.fromJson(
        {'id': 'a', 'name': 'Lightning Helix', 'color_identity': 'RW'});

    expect(card.color, ColorGroup.multicolor);
    expect(card.quantity, 1);
    expect(card.sets, '');
  });

  test('a page keeps the totals, the color counts and the hidden cards', () {
    final page = CardsPage.fromJson({
      'items': [
        {'id': '1', 'name': 'A', 'color': 'W'},
      ],
      'total': 40,
      'color_counts': {'W': 3, 'multicolor': 1, 'nonsense': 9},
      'not_identified': 2,
    });
    final bare = CardsPage.fromJson({
      'items': [
        {'id': '1', 'name': 'A'},
        {'id': '2', 'name': 'B'},
      ],
    });

    expect(page.total, 40);
    expect(page.items, hasLength(1));
    expect(page.colorCounts[ColorGroup.white], 3);
    expect(page.colorCounts[ColorGroup.multicolor], 1);
    expect(page.colorCounts[ColorGroup.blue], 0);
    expect(page.colorCounts, hasLength(7));
    expect(page.notIdentified, 2);
    expect(bare.total, 2);
    expect(bare.notIdentified, 0);
  });
}
