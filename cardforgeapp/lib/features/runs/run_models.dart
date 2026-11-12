// The user's sorting runs, as the CardForge server returns them
// (GET /runs and GET /runs/{id}). Pure Dart, no Flutter.
//
// A run reaches the server only after it finished (the machine uploads it), so
// every run here is completed, cancelled or failed (RF22 / RF23).

import '../machine/api/machine_api_models.dart' show RunStatus;

int? _int(Object? v) => v is num ? v.toInt() : null;
String? _str(Object? v) => v is String && v.isNotEmpty ? v : null;
DateTime? _date(Object? v) => v is String ? DateTime.tryParse(v)?.toLocal() : null;
Map<String, Object?> _map(Object? v) =>
    v is Map ? v.cast<String, Object?>() : const <String, Object?>{};

RunStatus _status(Object? v) => switch (v) {
      'running' => RunStatus.running,
      'paused' => RunStatus.paused,
      'completed' => RunStatus.completed,
      'failed' => RunStatus.failed,
      'cancelled' => RunStatus.cancelled,
      _ => RunStatus.unknown,
    };

/// How a card was read (RF25 unidentified, RF39 face-down).
enum ScanOutcome { identified, unidentified, faceDown }

ScanOutcome _outcome(Object? v) => switch (v) {
      'identified' => ScanOutcome.identified,
      'face_down' => ScanOutcome.faceDown,
      _ => ScanOutcome.unidentified,
    };

/// One run in the history list.
class RunSummary {
  const RunSummary({
    required this.id,
    required this.status,
    this.startedAt,
    this.stoppedAt,
    this.cardsProcessed = 0,
    this.identified = 0,
    this.notIdentified = 0,
    this.errorCode,
    this.message,
  });

  factory RunSummary.fromJson(Map<String, Object?> json) => RunSummary(
        id: (json['run_id'] ?? '').toString(),
        status: _status(json['status']),
        startedAt: _date(json['started_at']),
        stoppedAt: _date(json['stopped_at']),
        cardsProcessed: _int(json['cards_processed']) ?? 0,
        identified: _int(json['successful_scans']) ?? 0,
        notIdentified: _int(json['failed_scans']) ?? 0,
        errorCode: _str(json['error_code']),
        message: _str(json['message']),
      );

  /// Server id, used in GET /runs/{id}.
  final String id;
  final RunStatus status;
  final DateTime? startedAt;
  final DateTime? stoppedAt;
  final int cardsProcessed;
  final int identified;

  /// Unidentified + face-down (they went to folder 6).
  final int notIdentified;

  /// Why it ended early, e.g. `MODULE_OFFLINE`, `BATCH_LIMIT`.
  final String? errorCode;

  /// The machine's own message (English).
  final String? message;
}

/// One card of a run, in the order it was sorted.
class RunCard {
  const RunCard({
    required this.number,
    required this.outcome,
    this.folder,
    this.name,
    this.setCode,
    this.collectorNumber,
    this.rarity,
  });

  factory RunCard.fromJson(Map<String, Object?> json) => RunCard(
        number: _int(json['card_number']) ?? 0,
        outcome: _outcome(json['status']),
        folder: _int(json['bin']),
        name: _str(json['card_name']),
        setCode: _str(json['set_code']),
        collectorNumber: _str(json['collector_number']),
        rarity: _str(json['rarity']),
      );

  final int number;
  final ScanOutcome outcome;

  /// Folder 1-6 it fell into (null on the top-only test bench).
  final int? folder;
  final String? name;
  final String? setCode;
  final String? collectorNumber;
  final String? rarity;
}

/// GET /runs/{id}: per-folder counts and every card (RF22).
class RunDetails {
  const RunDetails({
    required this.run,
    required this.folderCounts,
    required this.identified,
    required this.unidentified,
    required this.faceDown,
    required this.cards,
  });

  factory RunDetails.fromJson(Map<String, Object?> json) {
    final folders = <int, int>{};
    _map(json['bin_counts']).forEach((key, value) {
      final folder = int.tryParse(key);
      final count = _int(value);
      if (folder != null && count != null) folders[folder] = count;
    });
    final scans = _map(json['scan_counts']);
    final cards = json['cards'] is List
        ? (json['cards'] as List)
            .whereType<Map>()
            .map((c) => RunCard.fromJson(c.cast<String, Object?>()))
            .toList()
        : const <RunCard>[];
    return RunDetails(
      run: RunSummary.fromJson(_map(json['run'])),
      folderCounts: Map.unmodifiable(folders),
      identified: _int(scans['identified']) ?? 0,
      unidentified: _int(scans['unidentified']) ?? 0,
      faceDown: _int(scans['face_down']) ?? 0,
      cards: List.unmodifiable(cards),
    );
  }

  final RunSummary run;

  /// Folder (1-6) -> cards that fell into it. Folders with no card are absent.
  final Map<int, int> folderCounts;
  final int identified;
  final int unidentified;
  final int faceDown;
  final List<RunCard> cards;
}

/// One page of GET /runs. [total] counts every run of the user.
class RunsPage {
  const RunsPage({required this.runs, required this.total});

  factory RunsPage.fromJson(Map<String, Object?> json) {
    final runs = json['runs'] is List
        ? (json['runs'] as List)
            .whereType<Map>()
            .map((r) => RunSummary.fromJson(r.cast<String, Object?>()))
            .toList()
        : const <RunSummary>[];
    return RunsPage(runs: runs, total: _int(json['total']) ?? runs.length);
  }

  final List<RunSummary> runs;
  final int total;
}
