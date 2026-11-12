import '../cards/card_models.dart';
import '../machine/api/machine_api_models.dart' show RunStatus;
import 'run_models.dart';
import 'run_repository.dart';

/// Simulated run history (three runs), so the screens work without a server.
class MockRunRepository implements RunRepository {
  MockRunRepository({this.latency = const Duration(milliseconds: 500)});

  /// Simulated network time. Tests use [Duration.zero].
  final Duration latency;

  // (name, set, number, folder); null name = unidentified, '*' = face-down.
  static const List<(String?, String?, String?, int)> _firstRunCards = [
    ('Swords to Plowshares', 'STA', '10', 1),
    ('Counterspell', 'MH2', '267', 2),
    ('Lightning Bolt', '2XM', '129', 4),
    (null, null, null, 6),
    ('Llanowar Elves', 'DOM', '168', 5),
    ('Lightning Bolt', '2XM', '129', 4),
    ('Doom Blade', 'M14', '95', 3),
    ('*', null, null, 6),
    ('Sol Ring', 'C21', '263', 6),
  ];

  static final List<RunDetails> _runs = [
    _details(
      id: '3',
      status: RunStatus.completed,
      startedAt: DateTime(2026, 11, 10, 19, 42),
      minutes: 2,
      message: 'Stack is empty: 9 card(s) processed.',
      cards: _firstRunCards,
    ),
    _details(
      id: '2',
      status: RunStatus.cancelled,
      startedAt: DateTime(2026, 11, 8, 15, 5),
      minutes: 1,
      message: 'Run stopped by user after 3 card(s).',
      cards: const [
        ('Opt', 'ELD', '59', 2),
        ('Shock', 'M19', '156', 4),
        ('Wall of Omens', 'ROE', '53', 1),
      ],
    ),
    _details(
      id: '1',
      status: RunStatus.failed,
      startedAt: DateTime(2026, 11, 2, 10, 30),
      minutes: 1,
      errorCode: 'MODULE_OFFLINE',
      message: 'The bottom module (A2) stopped responding.',
      cards: const [('Giant Growth', 'M12', '176', 5)],
    ),
  ];

  static RunDetails _details({
    required String id,
    required RunStatus status,
    required DateTime startedAt,
    required int minutes,
    required List<(String?, String?, String?, int)> cards,
    String? errorCode,
    String? message,
  }) {
    final runCards = [
      for (var i = 0; i < cards.length; i++)
        RunCard(
          number: i + 1,
          outcome: switch (cards[i].$1) {
            null => ScanOutcome.unidentified,
            '*' => ScanOutcome.faceDown,
            _ => ScanOutcome.identified,
          },
          name: cards[i].$1 == '*' ? null : cards[i].$1,
          setCode: cards[i].$2,
          collectorNumber: cards[i].$3,
          folder: cards[i].$4,
        ),
    ];
    final folders = <int, int>{};
    for (final card in runCards) {
      if (card.folder != null) {
        folders[card.folder!] = (folders[card.folder!] ?? 0) + 1;
      }
    }
    int count(ScanOutcome outcome) =>
        runCards.where((c) => c.outcome == outcome).length;
    return RunDetails(
      run: RunSummary(
        id: id,
        status: status,
        startedAt: startedAt,
        stoppedAt: startedAt.add(Duration(minutes: minutes)),
        cardsProcessed: runCards.length,
        identified: count(ScanOutcome.identified),
        notIdentified: runCards.length - count(ScanOutcome.identified),
        errorCode: errorCode,
        message: message,
      ),
      folderCounts: folders,
      identified: count(ScanOutcome.identified),
      unidentified: count(ScanOutcome.unidentified),
      faceDown: count(ScanOutcome.faceDown),
      cards: runCards,
    );
  }

  @override
  Future<RunsPage> fetchRuns({int offset = 0, int limit = 30}) async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);
    final page = _runs.skip(offset).take(limit).map((d) => d.run).toList();
    return RunsPage(runs: page, total: _runs.length);
  }

  @override
  Future<RunDetails> fetchRun(String id) async {
    if (latency > Duration.zero) await Future<void>.delayed(latency);
    for (final details in _runs) {
      if (details.run.id == id) return details;
    }
    throw const CardsException(CardsFailure.serverError);
  }
}
