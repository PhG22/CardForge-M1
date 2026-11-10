import 'dart:async';

import 'package:flutter/foundation.dart';

import 'card_models.dart';
import 'card_repository.dart';

/// State of the "Cards in folders" screen (no widgets, easy to test).
///
/// - Empty search: lists the cards that are in the folders.
/// - With a search: lists the matching cards and the folders each one is in.
/// - Results come from the server in pages; [loadMore] fetches the next page.
class CardsInFoldersController extends ChangeNotifier {
  CardsInFoldersController(
    this._repository, {
    this.pageSize = 30,
    this.debounce = const Duration(milliseconds: 400),
  });

  final CardRepository _repository;
  final int pageSize;

  /// How long to wait after the last keystroke before asking the server.
  final Duration debounce;

  /// The search that was last requested (empty means "all cards").
  String query = '';
  List<FolderedCard> items = const [];

  /// Number of cards matching [query] on the server.
  int total = 0;

  /// True while the first page is loading.
  bool loading = false;
  bool loadingMore = false;
  CardsFailure? failure;

  Timer? _debounceTimer;
  int _requestId = 0;
  bool _disposed = false;

  bool get isSearching => query.isNotEmpty;
  bool get hasMore => items.length < total;

  /// First load.
  Future<void> load() => _loadFirstPage();

  /// Pull to refresh: keeps the current list on screen while it reloads.
  Future<void> refresh() => _loadFirstPage(keepItems: true);

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

  /// Tries again after a failure.
  Future<void> retry() {
    failure = null;
    return items.isEmpty ? _loadFirstPage() : loadMore();
  }

  void _apply(String trimmed) {
    query = trimmed;
    _loadFirstPage();
  }

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
      final page = await _repository.fetchCards(
        query: query,
        offset: 0,
        limit: pageSize,
      );
      if (id != _requestId) return; // a newer search replaced this one
      items = page.items;
      total = page.total;
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
      final page = await _repository.fetchCards(
        query: query,
        offset: items.length,
        limit: pageSize,
      );
      if (id != _requestId) return;
      items = [...items, ...page.items];
      total = page.total;
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
