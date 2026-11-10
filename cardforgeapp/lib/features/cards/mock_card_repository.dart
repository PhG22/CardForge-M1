import 'card_models.dart';
import 'card_repository.dart';

/// Simulated server data, so the screen works without a backend.
///
/// - Search ignores upper/lower case and matches any part of the name.
/// - Searching for `fail` simulates a server error.
class MockCardRepository implements CardRepository {
  MockCardRepository({this.latency = const Duration(milliseconds: 500)});

  /// Simulated network time. Tests use [Duration.zero].
  final Duration latency;

  static const String failingQuery = 'fail';

  // (name, [(folder, quantity), ...]). Some cards are in more than one folder.
  static const List<(String, List<(int, int)>)> _data = [
    ('Arcane Signet', [(6, 2)]),
    ('Birds of Paradise', [(5, 2)]),
    ('Cancel', [(2, 2)]),
    ('Chain Lightning', [(4, 2)]),
    ('Command Tower', [(6, 3)]),
    ('Counterspell', [(2, 3), (3, 1)]),
    ('Dark Ritual', [(3, 2)]),
    ('Divination', [(2, 1)]),
    ('Doom Blade', [(3, 3)]),
    ('Duress', [(3, 2)]),
    ('Elvish Mystic', [(5, 3)]),
    ('Fireblast', [(4, 1)]),
    ('Force of Will', [(2, 1)]),
    ('Giant Growth', [(5, 3), (2, 1)]),
    ('Goblin Guide', [(4, 2)]),
    ('Gray Merchant of Asphodel', [(3, 1)]),
    ('Lightning Bolt', [(4, 4), (6, 1)]),
    ('Lightning Helix', [(6, 1)]),
    ('Llanowar Elves', [(5, 4)]),
    ('Mana Leak', [(2, 2)]),
    ('Mind Stone', [(6, 2)]),
    ('Murder', [(3, 2)]),
    ('Naturalize', [(5, 1)]),
    ('Opt', [(2, 5), (6, 1)]),
    ('Path to Exile', [(1, 3)]),
    ('Rampant Growth', [(5, 2)]),
    ('Savannah Lions', [(1, 4)]),
    ('Serra Angel', [(1, 1)]),
    ('Shivan Dragon', [(4, 1)]),
    ('Shock', [(4, 3)]),
    ('Sol Ring', [(6, 1), (1, 1)]),
    ('Swords to Plowshares', [(1, 2)]),
    ('Terminate', [(6, 2)]),
    ('Vampire Nighthawk', [(3, 1)]),
    ('Wall of Omens', [(1, 2)]),
    ('Wrath of God', [(1, 1)]),
  ];

  static final List<FolderedCard> _cards = [
    for (var i = 0; i < _data.length; i++)
      FolderedCard(
        id: 'mock-${i + 1}',
        name: _data[i].$1,
        folders: [
          for (final (folder, quantity) in (_data[i].$2.toList()
            ..sort((a, b) => a.$1.compareTo(b.$1))))
            CardFolderEntry(folder: folder, quantity: quantity),
        ],
      ),
  ];

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
  }) async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);

    final needle = query.trim().toLowerCase();
    if (needle == failingQuery) {
      throw const CardsException(CardsFailure.serverError);
    }

    final matches = needle.isEmpty
        ? _cards
        : _cards.where((c) => c.name.toLowerCase().contains(needle)).toList();
    final end = (offset + limit).clamp(0, matches.length);
    final start = offset.clamp(0, end);
    return CardsPage(items: matches.sublist(start, end), total: matches.length);
  }
}
