import '../runs/mock_run_repository.dart';
import '../runs/run_models.dart';
import 'card_models.dart';
import 'card_repository.dart';
import 'collection_export.dart';

/// Simulated server data, so the screens work without a backend. The
/// collection is made of the identified cards of the sample runs
/// ([MockRunRepository.sampleRuns]), so the whole collection and each run
/// agree with the run results.
///
/// - Search ignores upper/lower case and matches any part of the name.
/// - Searching for `fail` simulates a server error.
class MockCardRepository implements CardRepository {
  MockCardRepository({this.latency = const Duration(milliseconds: 500)});

  /// Simulated network time. Tests use [Duration.zero].
  final Duration latency;

  static const String failingQuery = 'fail';

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
    String? runId,
    ColorGroup? color,
  }) async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);

    final needle = query.trim().toLowerCase();
    if (needle == failingQuery) {
      throw const CardsException(CardsFailure.serverError);
    }

    final cards = [
      for (final card in mockScopeCards(runId))
        if (needle.isEmpty || (card.name ?? '').toLowerCase().contains(needle))
          card,
    ];
    final counts = {for (final g in ColorGroup.values) g: 0};
    final byName = <String, List<RunCard>>{};
    var notIdentified = 0;
    for (final card in cards) {
      final group = card.color;
      if (card.outcome != ScanOutcome.identified || group == null) {
        notIdentified++;
        continue;
      }
      counts[group] = counts[group]! + 1;
      if (color == null || group == color) {
        byName.putIfAbsent(card.name!, () => []).add(card);
      }
    }
    final names = byName.keys.toList()
      ..sort((a, b) => a.toLowerCase().compareTo(b.toLowerCase()));
    final matches = [
      for (final name in names)
        CollectionCard(
          id: 'mock-$name',
          name: name,
          quantity: byName[name]!.length,
          color: byName[name]!.first.color!,
          colorIdentity: _identity(byName[name]!.first.color!),
          sets: ({
            for (final c in byName[name]!)
              if (c.setCode != null) c.setCode!.toUpperCase()
          }.toList()
                ..sort())
              .join(', '),
        ),
    ];
    final end = (offset + limit).clamp(0, matches.length);
    final start = offset.clamp(0, end);
    return CardsPage(
      items: matches.sublist(start, end),
      total: matches.length,
      colorCounts: Map.unmodifiable(counts),
      notIdentified: needle.isEmpty ? notIdentified : 0,
    );
  }
}

/// Every card of one sample run, or of all of them (null).
List<RunCard> mockScopeCards(String? runId) => [
      for (final run in MockRunRepository.sampleRuns)
        if (runId == null || run.run.id == runId) ...run.cards,
    ];

String _identity(ColorGroup group) => switch (group) {
      ColorGroup.white => 'W',
      ColorGroup.blue => 'U',
      ColorGroup.black => 'B',
      ColorGroup.red => 'R',
      ColorGroup.green => 'G',
      ColorGroup.colorless => '',
      // The sample runs have no multicolor card.
      ColorGroup.multicolor => 'WU',
    };

/// Simulated `GET /cards/export`: the same cards as [MockCardRepository],
/// one row per print, plus the cards the machine could not identify.
class MockCollectionExportSource implements CollectionExportSource {
  MockCollectionExportSource({this.latency = const Duration(milliseconds: 500)});

  /// Simulated network time. Tests use [Duration.zero].
  final Duration latency;

  @override
  Future<List<CollectionRow>> fetchRows({String? runId}) async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);
    final rows = <String, CollectionRow>{};
    for (final card in mockScopeCards(runId)) {
      final status = switch (card.outcome) {
        ScanOutcome.identified => 'identified',
        ScanOutcome.unidentified => 'unidentified',
        ScanOutcome.faceDown => 'face_down',
      };
      final key = '$status|${card.name}|${card.setCode}|${card.collectorNumber}';
      final previous = rows[key];
      rows[key] = CollectionRow(
        quantity: (previous?.quantity ?? 0) + 1,
        status: status,
        name: card.name,
        setCode: card.setCode,
        collectorNumber: card.collectorNumber,
        color: card.color,
        colorIdentity: card.color == null ? null : _identity(card.color!),
        scryfallId: card.name == null ? null : 'mock-${card.name}',
      );
    }
    return rows.values.toList();
  }
}
