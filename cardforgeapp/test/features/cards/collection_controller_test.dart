import 'dart:async';

import 'package:cardforge/features/cards/card_models.dart';
import 'package:cardforge/features/cards/card_repository.dart';
import 'package:cardforge/features/cards/collection_controller.dart';
import 'package:cardforge/features/cards/mock_card_repository.dart';
import 'package:cardforge/features/runs/mock_run_repository.dart';
import 'package:flutter_test/flutter_test.dart';

typedef _Call = ({String query, int offset, String? runId, ColorGroup? color});

/// Wraps another repository and remembers every request.
class _RecordingRepository implements CardRepository {
  _RecordingRepository(this.inner);

  final CardRepository inner;
  final List<_Call> calls = [];

  @override
  Future<CardsPage> fetchCards({
    String query = '',
    int offset = 0,
    int limit = 30,
    String? runId,
    ColorGroup? color,
  }) {
    calls.add((query: query, offset: offset, runId: runId, color: color));
    return inner.fetchCards(
        query: query, offset: offset, limit: limit, runId: runId, color: color);
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
    String? runId,
    ColorGroup? color,
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
    String? runId,
    ColorGroup? color,
  }) {
    if (!_failed && when(offset)) {
      _failed = true;
      return Future<CardsPage>.error(
          const CardsException(CardsFailure.serverUnreachable));
    }
    return inner.fetchCards(
        query: query, offset: offset, limit: limit, runId: runId, color: color);
  }
}

CollectionCard _card(String name) =>
    CollectionCard(id: name, name: name, quantity: 1, color: ColorGroup.white);

void main() {
  MockCardRepository fastMock() => MockCardRepository(latency: Duration.zero);

  CollectionController make(
    CardRepository repo, {
    int pageSize = 4,
    Duration debounce = const Duration(milliseconds: 20),
    bool withRuns = false,
  }) {
    final controller = CollectionController(
      repo,
      runs: withRuns ? MockRunRepository(latency: Duration.zero) : null,
      pageSize: pageSize,
      debounce: debounce,
    );
    addTearDown(controller.dispose);
    return controller;
  }

  test('load shows the first page of the whole collection', () async {
    final c = make(fastMock());

    final future = c.load();
    expect(c.loading, isTrue);
    await future;

    expect(c.loading, isFalse);
    expect(c.items, hasLength(4));
    expect(c.total, 10);
    expect(c.hasMore, isTrue);
    expect(c.runId, isNull);
    expect(c.color, isNull);
    expect(c.isFiltered, isFalse);
    expect(c.colorCounts[ColorGroup.red], 3);
    expect(c.allColorsCount, 11);
    expect(c.notIdentified, 2);
    expect(c.failure, isNull);
  });

  test('load also fetches the runs for the scope picker', () async {
    final c = make(fastMock(), withRuns: true);
    await c.load();
    await pumpEventQueue();

    expect(c.runs.map((r) => r.id), ['3', '2', '1']);
    expect(c.selectedRun, isNull);
  });

  test('loadMore appends pages until everything is loaded', () async {
    final repo = _RecordingRepository(fastMock());
    final c = make(repo);
    await c.load();

    await c.loadMore();
    await c.loadMore();

    expect(c.items, hasLength(10));
    expect(c.hasMore, isFalse);
    final callsBefore = repo.calls.length;

    await c.loadMore(); // nothing left to load
    expect(repo.calls.length, callsBefore);
    expect(repo.calls.map((call) => call.offset), [0, 4, 8]);
  });

  test('picking a color shows only that group and keeps every count',
      () async {
    final repo = _RecordingRepository(fastMock());
    final c = make(repo);
    await c.load();

    c.selectColor(ColorGroup.red);
    await pumpEventQueue();

    expect(c.color, ColorGroup.red);
    expect(c.isFiltered, isTrue);
    expect(c.items.map((card) => card.name), ['Lightning Bolt', 'Shock']);
    expect(c.total, 2);
    expect(c.colorCounts[ColorGroup.white], 2);
    expect(repo.calls.last.color, ColorGroup.red);

    c.selectColor(null);
    await pumpEventQueue();
    expect(c.total, 10);
  });

  test('picking a run shows only its cards, and the color still applies',
      () async {
    final repo = _RecordingRepository(fastMock());
    final c = make(repo, withRuns: true);
    await c.load();

    c.selectRun('2');
    await pumpEventQueue();
    expect(c.items.map((card) => card.name), ['Opt', 'Shock', 'Wall of Omens']);
    expect(c.selectedRun?.cardsProcessed, 3);

    c.selectColor(ColorGroup.white);
    await pumpEventQueue();
    expect(c.items.single.name, 'Wall of Omens');
    expect(repo.calls.last.runId, '2');

    c.selectRun(null);
    await pumpEventQueue();
    expect(c.items.map((card) => card.name),
        ['Swords to Plowshares', 'Wall of Omens']);
  });

  test('a search narrows the list to the matching cards', () async {
    final c = make(fastMock());
    await c.load();

    c.searchNow('LL');
    await pumpEventQueue();

    expect(c.query, 'LL');
    expect(c.isSearching, isTrue);
    expect(c.items.map((card) => card.name),
        ['Counterspell', 'Llanowar Elves', 'Wall of Omens']);
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
    expect(c.total, 10);
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
    expect(c.items, hasLength(4));

    await c.retry();
    expect(c.failure, isNull);
    expect(c.items, hasLength(8));
  });

  test('refresh keeps the current list while it reloads', () async {
    final c = make(fastMock());
    await c.load();

    final future = c.refresh();
    expect(c.items, isNotEmpty);
    expect(c.loading, isTrue);
    await future;

    expect(c.loading, isFalse);
    expect(c.items, hasLength(4));
  });
}
