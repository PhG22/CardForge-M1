import 'dart:async';

import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/card_repository.dart';
import 'package:cardforge/features/cards/cards_in_folders_controller.dart';
import 'package:cardforge/features/cards/mock_card_repository.dart';
import 'package:flutter_test/flutter_test.dart';

/// Wraps another repository and remembers every request.
class _RecordingRepository implements CardRepository {
  _RecordingRepository(this.inner);

  final CardRepository inner;
  final List<({String query, int offset})> calls = [];

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
  }) {
    calls.add((query: query, offset: offset));
    return inner.fetchCards(query: query, offset: offset, limit: limit);
  }
}

/// Answers only when the test says so, to control the order of responses.
class _ControlledRepository implements CardRepository {
  final Map<String, Completer<CardsPage>> pending = {};

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
  }) {
    final completer = Completer<CardsPage>();
    pending[query] = completer;
    return completer.future;
  }
}

/// Fails once, then behaves like the wrapped repository.
class _FailsOnce implements CardRepository {
  _FailsOnce(this.inner, {required this.when});

  final CardRepository inner;
  final bool Function(int offset) when;
  bool _failed = false;

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
  }) {
    if (!_failed && when(offset)) {
      _failed = true;
      return Future<CardsPage>.error(
          const CardsException(CardsFailure.serverUnreachable));
    }
    return inner.fetchCards(query: query, offset: offset, limit: limit);
  }
}

FolderedCard _card(String name) => FolderedCard(
      id: name,
      name: name,
      folders: const [CardFolderEntry(folder: 1, quantity: 1)],
    );

void main() {
  MockCardRepository fastMock() => MockCardRepository(latency: Duration.zero);

  CardsInFoldersController make(
    CardRepository repo, {
    int pageSize = 10,
    Duration debounce = const Duration(milliseconds: 20),
  }) {
    final controller =
        CardsInFoldersController(repo, pageSize: pageSize, debounce: debounce);
    addTearDown(controller.dispose);
    return controller;
  }

  test('load shows the first page', () async {
    final c = make(fastMock());

    final future = c.load();
    expect(c.loading, isTrue);
    await future;

    expect(c.loading, isFalse);
    expect(c.items, hasLength(10));
    expect(c.total, 36);
    expect(c.hasMore, isTrue);
    expect(c.isSearching, isFalse);
    expect(c.failure, isNull);
  });

  test('loadMore appends pages until everything is loaded', () async {
    final repo = _RecordingRepository(fastMock());
    final c = make(repo);
    await c.load();

    await c.loadMore();
    await c.loadMore();
    await c.loadMore();

    expect(c.items, hasLength(36));
    expect(c.hasMore, isFalse);
    final callsBefore = repo.calls.length;

    await c.loadMore(); // nothing left to load
    expect(repo.calls.length, callsBefore);
    expect(repo.calls.map((call) => call.offset), [0, 10, 20, 30]);
  });

  test('a search narrows the list to the matching cards', () async {
    final c = make(fastMock());
    await c.load();

    c.searchNow('lightning');
    await pumpEventQueue();

    expect(c.query, 'lightning');
    expect(c.isSearching, isTrue);
    expect(c.items.map((card) => card.name),
        ['Chain Lightning', 'Lightning Bolt', 'Lightning Helix']);
    expect(c.total, 3);
    expect(c.hasMore, isFalse);
  });

  test('a blank search lists all the cards again', () async {
    final c = make(fastMock());
    c.searchNow('opt');
    await pumpEventQueue();
    expect(c.total, 1);

    c.searchNow('   ');
    await pumpEventQueue();

    expect(c.query, '');
    expect(c.isSearching, isFalse);
    expect(c.total, 36);
  });

  test('typing quickly asks the server only once, after the pause', () async {
    final repo = _RecordingRepository(fastMock());
    final c = make(repo);

    c.onQueryChanged('b');
    c.onQueryChanged('bo');
    c.onQueryChanged('bolt');
    expect(repo.calls, isEmpty);

    await Future<void>.delayed(const Duration(milliseconds: 100));

    expect(repo.calls.map((call) => call.query), ['bolt']);
    expect(c.items.single.name, 'Lightning Bolt');
  });

  test('typing the search that is already shown does not ask again', () async {
    final repo = _RecordingRepository(fastMock());
    final c = make(repo);
    c.searchNow('opt');
    await pumpEventQueue();

    c.onQueryChanged('opt ');
    await Future<void>.delayed(const Duration(milliseconds: 80));

    expect(repo.calls, hasLength(1));
  });

  test('a slow answer to an old search never overwrites the newer one',
      () async {
    final repo = _ControlledRepository();
    final c = make(repo);

    c.searchNow('a');
    c.searchNow('ab');
    await pumpEventQueue();

    repo.pending['ab']!.complete(CardsPage(items: [_card('AB')], total: 1));
    await pumpEventQueue();
    repo.pending['a']!.complete(CardsPage(items: [_card('A')], total: 1));
    await pumpEventQueue();

    expect(c.query, 'ab');
    expect(c.items.single.name, 'AB');
    expect(c.loading, isFalse);
  });

  test('a failure on the first page is reported and retry recovers',
      () async {
    final c = make(_FailsOnce(fastMock(), when: (offset) => offset == 0));

    await c.load();
    expect(c.failure, CardsFailure.serverUnreachable);
    expect(c.items, isEmpty);
    expect(c.loading, isFalse);

    await c.retry();
    expect(c.failure, isNull);
    expect(c.items, isNotEmpty);
  });

  test('a failure while loading more keeps the list, and retry loads the page',
      () async {
    final c = make(_FailsOnce(fastMock(), when: (offset) => offset > 0));
    await c.load();

    await c.loadMore();
    expect(c.failure, CardsFailure.serverUnreachable);
    expect(c.items, hasLength(10));

    await c.retry();
    expect(c.failure, isNull);
    expect(c.items, hasLength(20));
  });

  test('refresh keeps the current list while it reloads', () async {
    final c = make(fastMock());
    await c.load();

    final future = c.refresh();
    expect(c.items, isNotEmpty);
    expect(c.loading, isTrue);
    await future;

    expect(c.loading, isFalse);
    expect(c.items, hasLength(10));
  });
}
