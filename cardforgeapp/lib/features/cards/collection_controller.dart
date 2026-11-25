import 'dart:async';

import 'package:flutter/foundation.dart';

import '../runs/run_models.dart';
import '../runs/run_repository.dart';
import 'card_models.dart';
import 'card_repository.dart';

/// State of the "My cards" screen (no widgets, easy to test).
///
/// - Scope: the whole collection ([runId] null) or the cards of one run.
/// - Color: every card ([color] null) or one color group (white, blue, black,
///   red, green, colorless, multicolor). [colorCounts] has the number of each
///   group for the current scope and search, for the filter chips.
/// - Search: empty lists every card; otherwise only the matching names.
/// - Results come from the server in pages; [loadMore] fetches the next page.
class CollectionController extends ChangeNotifier {
  CollectionController(
    this._repository, {
    RunRepository? runs,
    this.runId,
    this.pageSize = 30,
    this.debounce = const Duration(milliseconds: 400),
  }) : _runs = runs;

  final CardRepository _repository;
  final RunRepository? _runs;
  final int pageSize;

  /// How long to wait after the last keystroke before asking the server.
  final Duration debounce;

  /// The search that was last requested (empty means "all cards").
  String query = '';

  /// The run whose cards are listed; null = the whole collection.
  String? runId;

  /// The color group that is shown; null = every color.
  ColorGroup? color;

  List<CollectionCard> items = const [];

  /// Number of cards (names) matching the scope, search and color.
  int total = 0;

  /// Copies per color group in the scope and search (ignores [color]).
  Map<ColorGroup, int> colorCounts = const {};

  /// Unidentified and face-down cards in the scope.
  int notIdentified = 0;

  /// The user's runs, newest first, for the scope picker. Empty until
  /// [loadRuns] finished (or when the server has none).
  List<RunSummary> runs = const [];

  /// True while the first page is loading.
  bool loading = false;
  bool loadingMore = false;
  CardsFailure? failure;

  Timer? _debounceTimer;
  int _requestId = 0;
  bool _disposed = false;

  bool get isSearching => query.isNotEmpty;
  bool get isFiltered => isSearching || color != null;
  bool get hasMore => items.length < total;

  /// Copies of every color, in the current scope and search.
  int get allColorsCount => colorCounts.values.fold(0, (sum, n) => sum + n);

  /// The run picked as scope, when it is in [runs].
  RunSummary? get selectedRun {
    for (final run in runs) {
      if (run.id == runId) return run;
    }
    return null;
  }

  /// First load: the cards and, in the background, the runs for the picker.
  Future<void> load() {
    unawaited(loadRuns());
    return _loadFirstPage();
  }

  /// Pull to refresh: keeps the current list on screen while it reloads.
  Future<void> refresh() => _loadFirstPage(keepItems: true);

  /// Loads the runs of the scope picker. A failure only leaves the picker
  /// with "All my cards"; the card list shows its own errors.
  Future<void> loadRuns() async {
    final repo = _runs;
    if (repo == null) return;
    try {
      final page = await repo.fetchRuns(limit: 100);
      runs = page.runs;
      _notify();
    } catch (_) {
      // keep the picker as it is
    }
  }

  /// Called on every keystroke; asks the server only after the user pauses.
  void onQueryChanged(String text) {
    final trimmed = text.trim();
    _debounceTimer?.cancel();
    if (trimmed == query) return;
    _debounceTimer = Timer(debounce, () => _apply(trimmed));
  }

  /// Searches right away (keyboard "search" key, clear button).
  void searchNow(String text) {
    _debounceTimer?.cancel();
    _apply(text.trim());
  }

  /// Shows one color group, or every color with null.
  void selectColor(ColorGroup? group) {
    if (group == color) return;
    color = group;
    _loadFirstPage();
  }

  /// Shows the cards of one run, or the whole collection with null.
  void selectRun(String? id) {
    if (id == runId) return;
    runId = id;
    _loadFirstPage();
  }

  /// Tries again after a failure.
  Future<void> retry() {
    failure = null;
    return items.isEmpty ? _loadFirstPage() : loadMore();
  }

  void _apply(String trimmed) {
    query = trimmed;
    _loadFirstPage();
  }

  Future<CardsPage> _fetch(int offset) => _repository.fetchCards(
        query: query,
        offset: offset,
        limit: pageSize,
        runId: runId,
        color: color,
      );

  Future<void> _loadFirstPage({bool keepItems = false}) async {
    final id = ++_requestId;
    loading = true;
    loadingMore = false;
    failure = null;
    if (!keepItems) {
      items = const [];
      total = 0;
    }
    _notify();

    try {
      final page = await _fetch(0);
      if (id != _requestId) return; // a newer request replaced this one
      _applyPage(page, append: false);
    } on CardsException catch (e) {
      if (id != _requestId) return;
      failure = e.failure;
    } catch (_) {
      if (id != _requestId) return;
      failure = CardsFailure.serverError;
    }
    loading = false;
    _notify();
  }

  /// Fetches the next page, when there is one.
  Future<void> loadMore() async {
    if (loading || loadingMore || !hasMore || failure != null) return;
    final id = _requestId;
    loadingMore = true;
    _notify();

    try {
      final page = await _fetch(items.length);
      if (id != _requestId) return;
      _applyPage(page, append: true);
    } on CardsException catch (e) {
      if (id != _requestId) return;
      failure = e.failure;
    } catch (_) {
      if (id != _requestId) return;
      failure = CardsFailure.serverError;
    }
    loadingMore = false;
    _notify();
  }

  void _applyPage(CardsPage page, {required bool append}) {
    items = append ? [...items, ...page.items] : page.items;
    total = page.total;
    colorCounts = page.colorCounts;
    notIdentified = page.notIdentified;
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _debounceTimer?.cancel();
    super.dispose();
  }
}
