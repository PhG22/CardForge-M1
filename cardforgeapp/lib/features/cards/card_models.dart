/// The color filter of the collection, the run results and the export: a
/// card's color identity, the same groups the machine sorts by (RF20).
/// Every identified card is in exactly one group.
enum ColorGroup {
  white('W'),
  blue('U'),
  black('B'),
  red('R'),
  green('G'),
  colorless('colorless'),
  multicolor('multicolor');

  const ColorGroup(this.apiValue);

  /// What the server uses (`?color=` and the `color` field).
  final String apiValue;

  /// From the server's `color` field. Unknown or missing values give null.
  static ColorGroup? fromApi(Object? value) {
    if (value is! String) return null;
    for (final group in values) {
      if (group.apiValue.toLowerCase() == value.toLowerCase()) return group;
    }
    return null;
  }

  /// From a Scryfall color identity such as `"W"`, `"UR"` or `""`.
  /// Only the WUBRG letters count.
  static ColorGroup fromIdentity(String? identity) {
    final colors = <String>{
      for (final ch in (identity ?? '').toUpperCase().split(''))
        if ('WUBRG'.contains(ch) && ch.isNotEmpty) ch,
    };
    if (colors.isEmpty) return ColorGroup.colorless;
    if (colors.length > 1) return ColorGroup.multicolor;
    return switch (colors.first) {
      'W' => ColorGroup.white,
      'U' => ColorGroup.blue,
      'B' => ColorGroup.black,
      'R' => ColorGroup.red,
      _ => ColorGroup.green,
    };
  }
}

/// Reads `{"W": 3, "U": 0, ...}`; groups that are missing count 0.
Map<ColorGroup, int> colorCountsFromJson(Object? json) {
  final counts = {for (final g in ColorGroup.values) g: 0};
  if (json is Map) {
    json.forEach((key, value) {
      final group = ColorGroup.fromApi(key);
      if (group != null && value is num) counts[group] = value.toInt();
    });
  }
  return Map.unmodifiable(counts);
}

/// One card of the collection (by name): how many copies the user has, in
/// the whole collection or in one run.
class CollectionCard {
  const CollectionCard({
    required this.id,
    required this.name,
    required this.quantity,
    required this.color,
    this.colorIdentity = '',
    this.typeLine,
    this.manaCost,
    this.rarity,
    this.sets = '',
  });

  /// Reads one card from the server response.
  factory CollectionCard.fromJson(Map<String, dynamic> json) {
    String? str(String key) =>
        json[key] is String && (json[key] as String).isNotEmpty
            ? json[key] as String
            : null;
    final identity = str('color_identity') ?? '';
    return CollectionCard(
      id: json['id'].toString(),
      name: json['name'] as String,
      quantity: (json['quantity'] as num? ?? 1).toInt(),
      color: ColorGroup.fromApi(json['color']) ??
          ColorGroup.fromIdentity(identity),
      colorIdentity: identity,
      typeLine: str('type_line'),
      manaCost: str('mana_cost'),
      rarity: str('rarity'),
      sets: str('sets') ?? '',
    );
  }

  final String id;
  final String name;
  final int quantity;
  final ColorGroup color;

  /// e.g. `"WU"`; empty for a colorless card.
  final String colorIdentity;
  final String? typeLine;
  final String? manaCost;
  final String? rarity;

  /// The sets the copies come from, e.g. `"FIN, M10"`.
  final String sets;
}

/// One page of results. [total] is the number of cards (names) matching the
/// search and filters, not just the ones in this page. [colorCounts] counts
/// the copies per color group for the same scope and search, ignoring the
/// color filter, so every filter chip can show its number.
class CardsPage {
  const CardsPage({
    required this.items,
    required this.total,
    this.colorCounts = const {},
    this.notIdentified = 0,
  });

  final List<CollectionCard> items;
  final int total;
  final Map<ColorGroup, int> colorCounts;

  /// Unidentified and face-down cards in the scope (not in [items]).
  final int notIdentified;

  factory CardsPage.fromJson(Map<String, dynamic> json) {
    final items = (json['items'] as List<dynamic>)
        .map((e) => CollectionCard.fromJson(e as Map<String, dynamic>))
        .toList();
    return CardsPage(
      items: items,
      total: (json['total'] as num?)?.toInt() ?? items.length,
      colorCounts: colorCountsFromJson(json['color_counts']),
      notIdentified: (json['not_identified'] as num?)?.toInt() ?? 0,
    );
  }
}

enum CardsFailure { unauthorized, serverUnreachable, serverError }

class CardsException implements Exception {
  const CardsException(this.failure);

  final CardsFailure failure;

  @override
  String toString() => 'CardsException($failure)';
}
