import 'dart:async';

import 'package:flutter/foundation.dart';

import '../alerts/alerts_controller.dart';
import '../alerts/machine_alert.dart';
import '../notifications/background_alerts.dart';
import 'api/machine_api.dart';
import 'api/machine_api_models.dart';
import 'machine_link_service.dart';
import 'machine_models.dart';
import 'machine_store.dart';

/// How the app is connected to the linked machine's REST API.
enum MachineConnection {
  /// No machine linked.
  none,

  /// Linked, but the phone doesn't know its IP address (older link).
  noAddress,
  connecting,
  online,

  /// No answer. The controller retries on its own (2 s, 5 s, 10 s, 30 s...).
  offline,
}

/// A card the machine just dropped, for the "Recently sorted" list.
class SortedCard {
  const SortedCard({required this.number, this.tray, this.cleared = false});

  final int number;
  final int? tray;

  /// Dropped by a stop instead of being sorted.
  final bool cleared;
}

/// The live connection to the linked machine, for the whole app.
///
/// - Connects as soon as a machine with an address is linked, and keeps the
///   WebSocket (`/ws/events`) open, reconnecting when it drops.
/// - Keeps the run state up to date and raises alerts (run finished, a fault
///   that paused the run, connection lost) whatever screen is open (RF27).
/// - While a run is active, keeps [BackgroundAlerts] watching it, so its
///   alerts also arrive as notifications with the app closed (RF38).
/// - Runs the commands of the Machine control screen.
class MachineController extends ChangeNotifier {
  MachineController({
    required MachineStore store,
    required MachineApi Function(MachineAddress address) apiFor,
    required AlertsController alerts,
    required MachineLinkService linkService,
    BackgroundAlerts backgroundAlerts = const NoBackgroundAlerts(),
    this.retryDelays = const [
      Duration(seconds: 2),
      Duration(seconds: 5),
      Duration(seconds: 10),
      Duration(seconds: 30),
    ],
  })  : _store = store,
        _apiFor = apiFor,
        _alerts = alerts,
        _linkService = linkService,
        _backgroundAlerts = backgroundAlerts;

  final BackgroundAlerts _backgroundAlerts;
  final MachineStore _store;
  final MachineApi Function(MachineAddress address) _apiFor;
  final AlertsController _alerts;
  final MachineLinkService _linkService;
  final List<Duration> retryDelays;

  static const int recentCardsLimit = 10;

  MachineConnection connection = MachineConnection.none;
  MachineHealth? health;
  MachineSnapshot? snapshot;
  List<SortedCard> recentCards = const [];

  bool isStarting = false;
  bool isResuming = false;
  bool isStopping = false;
  List<StartProblem> startProblems = const [];
  MachineApiException? actionError;

  bool isFindingAddress = false;
  MachineLinkFailure? findFailure;

  HardwareStatus? hardware;
  bool loadingHardware = false;
  MachineApiException? hardwareError;
  bool isMovingBase = false;
  BaseMoveResult? baseResult;
  MachineApiException? baseError;

  MachineApi? _api;
  MachineAddress? _address;
  StreamSubscription<MachineEvent>? _eventsSub;
  Timer? _retryTimer;
  int _retryIndex = 0;
  int _generation = 0; // invalidates callbacks from an older connection
  bool _started = false;
  bool _disposed = false;
  String? _runningRunId; // to notice a run that ended while we were offline
  final Set<String> _alertedRuns = {};
  final Set<String> _alertedFaults = {}; // "<run id>@<fault time>": one alert per fault
  bool _reportedOnline = false; // last value given to BackgroundAlerts.setAppConnected

  LinkedMachine? get machine => _store.machine;

  ControllerState get state => snapshot?.state ?? ControllerState.unknown;
  RunInfo? get run => snapshot?.run;

  bool get isOnline => connection == MachineConnection.online;

  /// A run is starting, running or stopping on the machine.
  bool get isBusy =>
      isStarting ||
      state == ControllerState.starting ||
      state == ControllerState.running ||
      state == ControllerState.stopping;

  /// A fault holds the run: the motors are stopped until Resume or Stop.
  bool get isPaused => state == ControllerState.paused;

  /// The base is moving outside a run (a test move, or the homing right
  /// after the machine is switched on).
  bool get isMachineMovingBase => state == ControllerState.manual;

  bool get canStart =>
      isOnline &&
      !isBusy &&
      !isPaused &&
      !isMachineMovingBase &&
      !isStopping &&
      !isMovingBase;

  /// RF26: continue the paused run once the problem is fixed.
  bool get canResume => isOnline && isPaused && !isResuming && !isStopping;

  /// Stopping is allowed while starting too (the API cancels the start), and
  /// while paused (it ends the run).
  bool get canStop =>
      isOnline && (isBusy || isPaused) && !isStopping && !isResuming;

  /// False in top-only mode: no base, no trays.
  bool get bottomEnabled => health?.bottomEnabled ?? true;

  bool get canMoveBase =>
      isOnline &&
      bottomEnabled &&
      !isBusy &&
      !isPaused &&
      !isMachineMovingBase &&
      !isMovingBase;

  /// Starts following the linked machine. Called once by the app.
  void start() {
    if (_started) return;
    _started = true;
    _store.addListener(_onMachineChanged);
    _onMachineChanged();
  }

  void _onMachineChanged() {
    final current = machine;
    final address = current?.address;
    if (_api != null && address == _address) return;
    _disconnect();
    _address = address;
    hardware = null;
    if (current == null) {
      connection = MachineConnection.none;
      health = null;
      snapshot = null;
      recentCards = const [];
    } else if (address == null) {
      connection = MachineConnection.noAddress;
    } else {
      _api = _apiFor(address);
      unawaited(_connect());
    }
    _notify();
  }

  /// "Try again" when offline.
  Future<void> reconnect() async {
    if (_api == null) return;
    _retryTimer?.cancel();
    _eventsSub?.cancel();
    _eventsSub = null;
    _retryIndex = 0;
    await _connect();
  }

  Future<void> _connect() async {
    final api = _api;
    if (api == null) return;
    final generation = ++_generation;
    connection = MachineConnection.connecting;
    _notify();
    try {
      final h = await api.health();
      final s = await api.runStatus();
      if (generation != _generation) return;
      health = h;
      _applySnapshot(s);
    } on MachineApiException {
      if (generation == _generation) _goOffline(generation, wasRunning: false);
      return;
    }
    connection = MachineConnection.online;
    findFailure = null;
    _retryIndex = 0;
    _notify();

    _eventsSub = api.events().listen(
      (event) {
        if (generation == _generation) _onEvent(event);
      },
      onError: (Object _) => _onEventsClosed(generation),
      onDone: () => _onEventsClosed(generation),
      cancelOnError: true,
    );
  }

  void _onEventsClosed(int generation) {
    if (generation != _generation || _disposed) return;
    _goOffline(generation, wasRunning: isBusy || isPaused);
  }

  void _goOffline(int generation, {required bool wasRunning}) {
    _eventsSub = null;
    connection = MachineConnection.offline;
    if (wasRunning) {
      _alerts.add(AlertType.connectionLost, machineName: machine?.name);
    }
    _notify();
    final delay = retryDelays.isEmpty
        ? const Duration(seconds: 30)
        : retryDelays[_retryIndex.clamp(0, retryDelays.length - 1)];
    _retryIndex++;
    _retryTimer?.cancel();
    _retryTimer = Timer(delay, () {
      if (generation == _generation && !_disposed) unawaited(_connect());
    });
  }

  void _disconnect() {
    _generation++;
    _retryTimer?.cancel();
    _retryTimer = null;
    _eventsSub?.cancel();
    _eventsSub = null;
    _api?.close();
    _api = null;
    _retryIndex = 0;
  }

  // -------------------------------------------------------------- events

  void _onEvent(MachineEvent event) {
    switch (event) {
      case SnapshotEvent(:final snapshot):
        _applySnapshot(snapshot);
      case RunStartedEvent(:final run):
        recentCards = const [];
        _applySnapshot(
            MachineSnapshot(state: ControllerState.running, run: run));
      case final CardSortedEvent card:
        final current = run;
        if (current != null) {
          snapshot = MachineSnapshot(
            state: state,
            run: current.withCard(
                cardsProcessed: card.cardNumber, tray: card.tray),
          );
        }
        recentCards = [
          SortedCard(
              number: card.cardNumber, tray: card.tray, cleared: card.cleared),
          ...recentCards,
        ].take(recentCardsLimit).toList();
      case RunFinishedEvent(:final run):
        _applySnapshot(MachineSnapshot(state: ControllerState.idle, run: run));
      case final FaultEvent fault:
        final current = run;
        final paused = fault.run ??
            (current == null
                ? null
                : RunInfo(
                    runId: current.runId,
                    status: RunStatus.paused,
                    startedAt: current.startedAt,
                    cardsProcessed: current.cardsProcessed,
                    errorCode: fault.code,
                    message: fault.message,
                    faultAt: fault.time,
                  ));
        _applySnapshot(
            MachineSnapshot(state: ControllerState.paused, run: paused));
        if (paused == null) _alertFault(null, fault.code, fault.time);
      case RunResumedEvent(:final run):
        _applySnapshot(MachineSnapshot(state: ControllerState.running, run: run));
      case UnknownMachineEvent():
        return;
    }
    _notify();
  }

  void _applySnapshot(MachineSnapshot next) {
    snapshot = next;
    final run = next.run;
    // RF38: an active run is watched in the background too; none = stop watching.
    if (run != null &&
        (run.status == RunStatus.running || run.status == RunStatus.paused)) {
      unawaited(
          _backgroundAlerts.watchRun(run.runId, machineName: machine?.name));
    } else if (next.state == ControllerState.idle) {
      unawaited(_backgroundAlerts.noActiveRun());
    }
    if (run == null) return;
    if (run.status == RunStatus.running || run.status == RunStatus.paused) {
      _runningRunId = run.runId;
      // A pause seen live (fault event) or on reconnect (snapshot): one alert.
      if (run.status == RunStatus.paused) {
        _alertFault(run.runId, run.errorCode, run.faultAt);
      }
    } else if (_runningRunId == run.runId) {
      _runningRunId = null;
      _alertRunEnd(run);
    }
  }

  /// RF27: alert a fault that paused the run, with its type (the code) and
  /// time (the alert's). A feed jam has its own alert type.
  void _alertFault(String? runId, String? code, DateTime? at) {
    final key = '${runId ?? '?'}@${at?.toIso8601String() ?? code}';
    if (!_alertedFaults.add(key)) return;
    final jam = code == 'FEED_TIMEOUT' || code == 'FEED_FAILED';
    _alerts.add(
      jam ? AlertType.jamDetected : AlertType.machineFault,
      machineName: machine?.name,
      cardCount: run?.cardsProcessed,
      faultCode: code,
    );
  }

  /// One alert per finished run, even if both the event and a snapshot say so.
  void _alertRunEnd(RunInfo run) {
    if (!_alertedRuns.add(run.runId)) return;
    final name = machine?.name;
    switch (run.status) {
      case RunStatus.completed:
        _alerts.add(AlertType.batchFinished,
            machineName: name, cardCount: run.cardsProcessed);
      case RunStatus.failed:
        _alerts.add(
          run.errorCode == 'FEED_TIMEOUT'
              ? AlertType.jamDetected
              : AlertType.runFailed,
          machineName: name,
          cardCount: run.cardsProcessed,
        );
      case RunStatus.cancelled ||
            RunStatus.running ||
            RunStatus.paused ||
            RunStatus.unknown:
        break; // the user stopped it: no alert needed
    }
  }

  // ------------------------------------------------------------ commands

  Future<void> startRun() async {
    final api = _api;
    if (api == null || !canStart) return;
    isStarting = true;
    startProblems = const [];
    actionError = null;
    _notify();
    try {
      final run = await api.startRun();
      recentCards = const [];
      _applySnapshot(MachineSnapshot(state: ControllerState.running, run: run));
    } on MachineApiException catch (e) {
      if (e.problems.isNotEmpty) {
        startProblems = e.problems;
      } else {
        actionError = e;
      }
    } finally {
      isStarting = false;
      _notify();
    }
  }

  /// RF26: continue a run paused by a fault. The machine homes the base again
  /// first; a refusal (e.g. a card still on the platform) lists its reasons.
  Future<void> resumeRun() async {
    final api = _api;
    if (api == null || !canResume) return;
    isResuming = true;
    startProblems = const [];
    actionError = null;
    _notify();
    try {
      final run = await api.resumeRun();
      _applySnapshot(MachineSnapshot(state: ControllerState.running, run: run));
    } on MachineApiException catch (e) {
      if (e.problems.isNotEmpty) {
        startProblems = e.problems;
      } else {
        actionError = e;
      }
    } finally {
      isResuming = false;
      _notify();
    }
  }

  Future<void> stopRun({bool immediate = false}) async {
    final api = _api;
    if (api == null || (!immediate && !canStop)) return;
    isStopping = true;
    actionError = null;
    _notify();
    try {
      final run = await api.stopRun(immediate: immediate);
      if (run != null) {
        _applySnapshot(MachineSnapshot(state: ControllerState.idle, run: run));
      }
    } on MachineApiException catch (e) {
      actionError = e;
    } finally {
      isStopping = false;
      _notify();
    }
  }

  void clearStartProblems() {
    if (startProblems.isEmpty && actionError == null) return;
    startProblems = const [];
    actionError = null;
    _notify();
  }

  Future<void> loadHardware() async {
    final api = _api;
    if (api == null) return;
    loadingHardware = true;
    hardwareError = null;
    _notify();
    try {
      hardware = await api.hardwareStatus();
    } on MachineApiException catch (e) {
      hardwareError = e;
    } finally {
      loadingHardware = false;
      _notify();
    }
  }

  Future<void> homeBase() => _moveBase((api) => api.homeBase());

  Future<void> gotoTray(int tray) => _moveBase((api) => api.gotoTray(tray));

  Future<void> _moveBase(
      Future<BaseMoveResult> Function(MachineApi api) move) async {
    final api = _api;
    if (api == null || !canMoveBase) return;
    isMovingBase = true;
    baseError = null;
    baseResult = null;
    _notify();
    try {
      baseResult = await move(api);
    } on MachineApiException catch (e) {
      baseError = e;
    } finally {
      isMovingBase = false;
      _notify();
    }
    unawaited(loadHardware());
  }

  /// Asks the machine for its current IP over Bluetooth (no password), saves
  /// it and reconnects. For when the router gave the machine a new address.
  Future<void> findAgain() async {
    final current = machine;
    if (current == null || isFindingAddress) return;
    isFindingAddress = true;
    findFailure = null;
    _notify();
    try {
      final address = await _linkService.findAddress(machineId: current.id);
      if (address == _address) {
        await reconnect();
      } else {
        await _store.updateAddress(address); // reconnects via _onMachineChanged
      }
    } on MachineLinkException catch (e) {
      findFailure = e.failure;
    } finally {
      isFindingAddress = false;
      _notify();
    }
  }

  /// Unlinks the machine on this phone.
  Future<void> forget() => _store.clear();

  void _notify() {
    if (_disposed) return;
    if (isOnline != _reportedOnline) {
      _reportedOnline = isOnline;
      _backgroundAlerts.setAppConnected(isOnline);
    }
    notifyListeners();
  }

  /// Stops following the machine (app closing, tests).
  void stop() {
    if (_started) _store.removeListener(_onMachineChanged);
    _started = false;
    _disconnect();
  }

  @override
  void dispose() {
    stop();
    _disposed = true;
    super.dispose();
  }
}
