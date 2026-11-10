import 'dart:async';

import 'package:cardforge/features/machine/api/machine_api.dart';
import 'package:cardforge/features/machine/api/machine_api_models.dart';

/// A machine REST API fully controlled by the test: set the next answers,
/// push WebSocket events with [emit], drop the connection with [dropEvents].
/// Every call answers immediately (no timers), so tests stay deterministic.
class FakeMachineApi implements MachineApi {
  FakeMachineApi({
    this.reachable = true,
    this.bottomEnabled = true,
    MachineSnapshot? snapshot,
  }) : snapshot =
            snapshot ?? const MachineSnapshot(state: ControllerState.idle);

  bool reachable;
  bool bottomEnabled;
  MachineSnapshot snapshot;

  /// When set, the next startRun throws it.
  MachineApiException? startError;

  /// When set, startRun waits for it (to test the "Preparing…" state).
  Completer<void>? startGate;

  HardwareStatus hardware = const HardwareStatus(
    top: TopModuleStatus(
      online: true,
      state: 'IDLE',
      stackHasCard: true,
      platformHasCard: false,
      flapAngle: 80,
      rollerRunning: false,
    ),
    bottom: BaseStatus(online: true, state: 'IDLE', homed: true, position: 3),
  );

  final List<String> calls = [];
  int closed = 0;
  StreamController<MachineEvent>? _events;

  static const MachineApiException _offline =
      MachineApiException(MachineApiFailure.unreachable);

  static RunInfo runningRun({int cards = 0, int? tray}) => RunInfo(
        runId: 'run-1',
        status: RunStatus.running,
        cardsProcessed: cards,
        currentTray: tray,
      );

  void _check(String call) {
    calls.add(call);
    if (!reachable) throw _offline;
  }

  @override
  Future<MachineHealth> health() async {
    _check('health');
    return MachineHealth(ok: true, bottomEnabled: bottomEnabled);
  }

  @override
  Future<MachineSnapshot> runStatus() async {
    _check('runStatus');
    return snapshot;
  }

  @override
  Future<RunInfo> startRun() async {
    _check('startRun');
    await startGate?.future;
    final error = startError;
    if (error != null) {
      startError = null;
      throw error;
    }
    final run = runningRun();
    snapshot = MachineSnapshot(state: ControllerState.running, run: run);
    return run;
  }

  @override
  Future<RunInfo?> stopRun({bool immediate = false}) async {
    _check(immediate ? 'stopRun(immediate)' : 'stopRun');
    final run = RunInfo(
      runId: snapshot.run?.runId ?? 'run-1',
      status: RunStatus.cancelled,
      cardsProcessed: snapshot.run?.cardsProcessed ?? 0,
    );
    snapshot = MachineSnapshot(state: ControllerState.idle, run: run);
    return run;
  }

  @override
  Future<HardwareStatus> hardwareStatus() async {
    _check('hardwareStatus');
    return hardware;
  }

  @override
  Future<BaseMoveResult> homeBase() async {
    _check('homeBase');
    return const BaseMoveResult(
      base: BaseStatus(online: true, homed: true, position: 3, drift: -2),
    );
  }

  @override
  Future<BaseMoveResult> gotoTray(int tray) async {
    _check('gotoTray($tray)');
    return BaseMoveResult(
      base: BaseStatus(online: true, homed: true, position: tray),
    );
  }

  @override
  Stream<MachineEvent> events() {
    calls.add('events');
    if (!reachable) return Stream<MachineEvent>.error(_offline);
    final controller = StreamController<MachineEvent>();
    _events = controller;
    controller.add(SnapshotEvent(snapshot));
    return controller.stream;
  }

  /// Pushes a WebSocket event to the app.
  void emit(MachineEvent event) => _events?.add(event);

  /// The WebSocket drops (Wi-Fi lost, machine turned off).
  Future<void> dropEvents() async {
    final events = _events;
    _events = null;
    await events?.close();
  }

  bool get hasEventListener => _events?.hasListener ?? false;

  @override
  void close() => closed++;
}
