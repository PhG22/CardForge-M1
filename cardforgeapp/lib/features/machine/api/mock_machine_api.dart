import 'dart:async';

import 'machine_api.dart';
import 'machine_api_models.dart';

/// A simulated machine inside the app (`USE_MOCK_MACHINE=true`), so the
/// machine control screen works with no Raspberry Pi and no computer.
///
/// Every run sorts [stackSize] cards, one every [cardInterval], into trays
/// 1 to 6, then finishes with "Stack is empty". With [jamAtCard] set, the
/// first run pauses with a feed jam before that card, to try the fault alert
/// and Resume. For the real thing (or the API's own simulator:
/// `python -m sorter_api --simulate`) use `USE_MOCK_MACHINE=false`.
///
/// Its card database has an update available, which downloads in
/// [cardDbUpdateTicks] steps of [cardDbTick], to try the progress bar.
class MockMachineApi implements MachineApi {
  MockMachineApi({
    this.stackSize = 12,
    this.cardInterval = const Duration(milliseconds: 1500),
    this.latency = const Duration(milliseconds: 300),
    this.jamAtCard = 5,
    this.cardDbTick = const Duration(milliseconds: 500),
    this.cardDbUpdateTicks = 40,
  });

  final Duration cardDbTick;
  final int cardDbUpdateTicks;

  final int stackSize;
  final Duration cardInterval;
  final Duration latency;

  /// The first run pauses with `FEED_TIMEOUT` before this card (null = never).
  final int? jamAtCard;
  bool _jammed = false;

  final StreamController<MachineEvent> _events =
      StreamController<MachineEvent>.broadcast();
  ControllerState _state = ControllerState.idle;
  RunInfo? _run;
  Timer? _timer;
  int _runs = 0;
  int _basePosition = 3;

  Future<void> _wait([int times = 1]) => Future<void>.delayed(latency * times);

  MachineSnapshot get _snapshot =>
      MachineSnapshot(state: _state, run: _run, cardDb: _cardDb);

  // --------------------------------------------------------- card database

  static const double _downloadMb = 489;
  CardDbPhase _dbPhase = CardDbPhase.idle;
  bool _dbAvailable = true;
  CardDbResult? _dbLastUpdate;
  int _dbTick = 0;
  Timer? _dbTimer;

  CardDbStatus get _cardDb => CardDbStatus(
        phase: _dbPhase,
        updateAvailable: _dbAvailable,
        lastCheck: CardDbResult(
          result: _dbAvailable ? 'update_available' : 'up_to_date',
          reason: _dbAvailable ? 'new cards or sets on Scryfall' : null,
          downloadMb: _downloadMb,
          remoteUpdatedAt: '2026-11-18T10:00:00+00:00',
        ),
        lastUpdate: _dbLastUpdate,
        progress: _dbPhase == CardDbPhase.updating ? _dbProgress() : null,
        localBuiltAt: DateTime.utc(2026, 11, 17, 18, 26),
        localCards: 110960,
      );

  CardDbProgress _dbProgress() {
    final fraction = _dbTick / cardDbUpdateTicks;
    final stage = switch (fraction) {
      < 0.05 => 'checking',
      < 0.55 => 'all_cards',
      < 0.65 => 'default_cards',
      < 0.95 => 'building',
      _ => 'finishing',
    };
    final downloaded = (fraction / 0.65).clamp(0.0, 1.0) * _downloadMb;
    final left = (cardDbUpdateTicks - _dbTick) * cardDbTick.inMilliseconds;
    return CardDbProgress(
      stage: stage,
      fraction: fraction,
      downloadedMb: downloaded,
      downloadMb: _downloadMb,
      etaSeconds: (left / 1000).round(),
      elapsedSeconds: (_dbTick * cardDbTick.inMilliseconds / 1000).round(),
    );
  }

  void _publishCardDb() => _events.add(CardDbEvent(_cardDb));

  @override
  Future<CardDbStatus> cardDbStatus() async {
    await _wait();
    return _cardDb;
  }

  @override
  Future<CardDbStatus> checkCardDb() async {
    await _wait();
    if (_dbPhase != CardDbPhase.idle) return _cardDb;
    _dbPhase = CardDbPhase.checking;
    Timer(latency * 4, () {
      _dbPhase = CardDbPhase.idle;
      _publishCardDb();
    });
    return _cardDb;
  }

  @override
  Future<CardDbStatus> updateCardDb({bool force = false}) async {
    await _wait();
    if (_dbPhase == CardDbPhase.updating) {
      throw const MachineApiException(MachineApiFailure.conflict,
          statusCode: 409, code: 'CARD_DB_UPDATING');
    }
    if (_state != ControllerState.idle) {
      throw const MachineApiException(MachineApiFailure.conflict,
          statusCode: 409, code: 'RUN_ACTIVE');
    }
    _dbPhase = CardDbPhase.updating;
    _dbTick = 0;
    _dbTimer = Timer.periodic(cardDbTick, (_) {
      _dbTick++;
      if (_dbTick >= cardDbUpdateTicks) {
        _dbTimer?.cancel();
        _dbPhase = CardDbPhase.idle;
        _dbAvailable = false;
        _dbLastUpdate = CardDbResult(
            result: 'updated', at: DateTime.now().toUtc());
      }
      _publishCardDb();
    });
    return _cardDb;
  }

  @override
  Future<CardDbStatus> cancelCardDbUpdate() async {
    await _wait();
    if (_dbPhase != CardDbPhase.updating) {
      throw const MachineApiException(MachineApiFailure.conflict,
          statusCode: 409, code: 'NOT_UPDATING');
    }
    _dbTimer?.cancel();
    _dbPhase = CardDbPhase.idle;
    _dbLastUpdate =
        CardDbResult(result: 'cancelled', at: DateTime.now().toUtc());
    _publishCardDb();
    return _cardDb;
  }

  @override
  Future<MachineHealth> health() async {
    await _wait();
    return const MachineHealth(ok: true, simulate: true);
  }

  @override
  Future<MachineSnapshot> runStatus() async {
    await _wait();
    return _snapshot;
  }

  @override
  Future<RunInfo> startRun() async {
    if (_dbPhase == CardDbPhase.updating) {
      throw const MachineApiException(
        MachineApiFailure.conflict,
        statusCode: 409,
        code: 'CARD_DB_UPDATING',
        problems: [
          StartProblem(
              code: 'CARD_DB_UPDATING',
              message: 'The machine is updating its card database.'),
        ],
      );
    }
    if (_state != ControllerState.idle) {
      throw const MachineApiException(
        MachineApiFailure.conflict,
        statusCode: 409,
        code: 'RUN_ACTIVE',
        problems: [
          StartProblem(
              code: 'RUN_ACTIVE', message: 'A run is already in progress.'),
        ],
      );
    }
    _state = ControllerState.starting;
    await _wait(4); // homing the base
    _runs++;
    final run = RunInfo(
      runId: 'mock-run-$_runs',
      status: RunStatus.running,
      startedAt: DateTime.now().toUtc(),
      message: 'Run started.',
    );
    _run = run;
    _state = ControllerState.running;
    _events.add(RunStartedEvent(run));
    _timer = Timer.periodic(cardInterval, (_) => _sortNextCard());
    return run;
  }

  void _sortNextCard() {
    final run = _run;
    if (run == null || _state != ControllerState.running) return;
    final number = run.cardsProcessed + 1;
    if (!_jammed && number == jamAtCard) {
      _jammed = true;
      _timer?.cancel();
      const message = 'Feed jam: no card reached the platform after 2 pull '
          'attempts (jam or empty stack). Fix it, then press Resume.';
      final now = DateTime.now().toUtc();
      final paused = RunInfo(
        runId: run.runId,
        status: RunStatus.paused,
        startedAt: run.startedAt,
        cardsProcessed: run.cardsProcessed,
        errorCode: 'FEED_TIMEOUT',
        message: message,
        faultAt: now,
      );
      _run = paused;
      _state = ControllerState.paused;
      _events.add(FaultEvent(
          code: 'FEED_TIMEOUT', time: now, message: message, run: paused));
      return;
    }
    final tray = (number - 1) % 6 + 1;
    _run = run.withCard(cardsProcessed: number, tray: tray);
    _events
        .add(CardSortedEvent(runId: run.runId, cardNumber: number, tray: tray));
    if (number >= stackSize) {
      _finish(
          RunStatus.completed, 'Stack is empty: $number card(s) processed.');
    }
  }

  void _finish(RunStatus status, String message) {
    _timer?.cancel();
    final run = _run;
    if (run == null) return;
    final finished = RunInfo(
      runId: run.runId,
      status: status,
      startedAt: run.startedAt,
      stoppedAt: DateTime.now().toUtc(),
      cardsProcessed: run.cardsProcessed,
      currentTray: run.currentTray,
      message: message,
    );
    _run = finished;
    _state = ControllerState.idle;
    _events.add(RunFinishedEvent(finished, message));
  }

  @override
  Future<RunInfo> resumeRun() async {
    final run = _run;
    if (_state != ControllerState.paused || run == null) {
      throw const MachineApiException(
        MachineApiFailure.conflict,
        statusCode: 409,
        message: 'No paused run to resume.',
      );
    }
    _state = ControllerState.starting;
    await _wait(4); // homing the base again
    final resumed = RunInfo(
      runId: run.runId,
      status: RunStatus.running,
      startedAt: run.startedAt,
      cardsProcessed: run.cardsProcessed,
      currentTray: run.currentTray,
      message: 'Run resumed.',
    );
    _run = resumed;
    _state = ControllerState.running;
    _events.add(RunResumedEvent(resumed));
    _timer = Timer.periodic(cardInterval, (_) => _sortNextCard());
    return resumed;
  }

  @override
  Future<RunInfo?> stopRun({bool immediate = false}) async {
    if (_state != ControllerState.running &&
        _state != ControllerState.paused) {
      throw const MachineApiException(
        MachineApiFailure.conflict,
        statusCode: 409,
        message: 'No run in progress.',
      );
    }
    _state = ControllerState.stopping;
    _timer?.cancel();
    await _wait(immediate ? 1 : 3);
    final count = _run?.cardsProcessed ?? 0;
    _finish(
      RunStatus.cancelled,
      'Run stopped by user: $count card(s) processed. Flap closed, motors stopped.',
    );
    return _run;
  }

  @override
  Future<HardwareStatus> hardwareStatus() async {
    await _wait();
    final running = _state == ControllerState.running;
    return HardwareStatus(
      top: TopModuleStatus(
        online: true,
        state: running ? 'BUSY' : 'IDLE',
        stackHasCard: true,
        platformHasCard: false,
        flapAngle: 80,
        rollerRunning: running,
      ),
      bottom: BaseStatus(
        online: true,
        state: 'IDLE',
        homed: true,
        position: _run?.currentTray ?? _basePosition,
        zeroSensor: _basePosition == 3,
      ),
    );
  }

  @override
  Future<BaseMoveResult> homeBase() async {
    await _base();
    await _wait(5);
    _basePosition = 3;
    return const BaseMoveResult(
      base: BaseStatus(
          online: true,
          state: 'IDLE',
          homed: true,
          position: 3,
          steps: 0,
          drift: 0,
          zeroSensor: true),
      message: 'Base homed (tray 3).',
    );
  }

  @override
  Future<BaseMoveResult> gotoTray(int tray) async {
    await _base();
    await _wait(3);
    _basePosition = tray;
    return BaseMoveResult(
      base:
          BaseStatus(online: true, state: 'IDLE', homed: true, position: tray),
      message: 'Base at tray $tray.',
    );
  }

  Future<void> _base() async {
    if (_state != ControllerState.idle) {
      throw const MachineApiException(
        MachineApiFailure.conflict,
        statusCode: 409,
        code: 'RUN_ACTIVE',
      );
    }
  }

  @override
  Stream<MachineEvent> events() async* {
    yield SnapshotEvent(_snapshot);
    yield* _events.stream;
  }

  @override
  void close() {}
}
