import 'dart:async';

import 'package:flutter/foundation.dart';

import 'machine_link_service.dart';
import 'machine_models.dart';

enum LinkPhase {
  deviceList,
  password,
  wifiQuestion,
  wifiForm,
  linking,
  success,
  failure,
}

typedef LinkedCallback = void Function(
  LinkedMachine machine, {
  required bool wifiSent,
});

/// Whether the phone can reach the machine's REST API at [address] (over
/// Wi-Fi, not Bluetooth).
typedef ReachabilityCheck = Future<bool> Function(MachineAddress address);

/// State machine of the "Link machine" screen (no widgets, easy to test):
///
/// scan -> pick a machine -> password -> send Wi-Fi? -> (Wi-Fi form) ->
/// wait for the machine to join the network -> check that the phone reaches
/// its API over Wi-Fi -> success or failure.
class MachineLinkFlow extends ChangeNotifier {
  MachineLinkFlow(
    this._service, {
    required this.onLinked,
    required this.checkReachable,
    this.linkTimeout = const Duration(seconds: 90),
    this.reachabilityAttempts = 3,
    this.reachabilityRetryDelay = const Duration(seconds: 2),
  });

  final MachineLinkService _service;
  final LinkedCallback onLinked;
  final ReachabilityCheck checkReachable;
  final Duration linkTimeout;

  /// Right after joining a network the machine may need a moment before the
  /// phone can reach it (DHCP, ARP), so the check is retried a few times.
  final int reachabilityAttempts;
  final Duration reachabilityRetryDelay;

  LinkPhase phase = LinkPhase.deviceList;

  // Scan
  bool scanning = false;
  List<DiscoveredMachine> machines = const [];
  MachineLinkFailure? scanFailure;

  // Password
  DiscoveredMachine? selected;
  bool checkingPassword = false;
  MachineLinkFailure? passwordFailure;

  // Linking
  WifiCredentials? _wifi;
  LinkStep? step;
  MachineLinkFailure? failure;
  LinkedMachine? linked;

  StreamSubscription<List<DiscoveredMachine>>? _scanSub;
  StreamSubscription<LinkProgress>? _linkSub;
  int _attempt = 0; // invalidates async work from a previous attempt
  bool _disposed = false;

  bool get sentWifi => _wifi != null;

  /// True while going back or closing the screen would interrupt something.
  bool get isBusy => checkingPassword || phase == LinkPhase.linking;

  /// Starts (or restarts) the whole flow with a fresh scan.
  void startScan() {
    _attempt++;
    _scanSub?.cancel();
    _linkSub?.cancel();
    unawaited(_service.disconnect());
    phase = LinkPhase.deviceList;
    selected = null;
    machines = const [];
    scanFailure = null;
    passwordFailure = null;
    checkingPassword = false;
    failure = null;
    linked = null;
    step = null;
    _wifi = null;
    scanning = true;
    _notify();

    _scanSub = _service.scan().listen(
      (found) {
        machines = found;
        _notify();
      },
      onError: (Object error) {
        scanFailure = _failureOf(error);
        scanning = false;
        _notify();
      },
      onDone: () {
        scanning = false;
        _notify();
      },
    );
  }

  void select(DiscoveredMachine machine) {
    _scanSub?.cancel();
    scanning = false;
    selected = machine;
    passwordFailure = null;
    phase = LinkPhase.password;
    _notify();
  }

  void backToList() {
    unawaited(_service.disconnect());
    selected = null;
    passwordFailure = null;
    phase = LinkPhase.deviceList;
    _notify();
  }

  void clearPasswordFailure() {
    if (passwordFailure == null) return;
    passwordFailure = null;
    _notify();
  }

  Future<void> submitPassword(String password) async {
    final machine = selected;
    if (machine == null || checkingPassword) return;
    final attempt = _attempt;
    checkingPassword = true;
    passwordFailure = null;
    _notify();
    try {
      await _service.authenticate(machineId: machine.id, password: password);
      if (attempt == _attempt) phase = LinkPhase.wifiQuestion;
    } on MachineLinkException catch (e) {
      if (attempt == _attempt) passwordFailure = e.failure;
    } finally {
      checkingPassword = false;
      _notify();
    }
  }

  /// Answers "Send Wi-Fi details?". `send: false` skips straight to linking.
  void answerWifiQuestion({required bool send}) {
    if (send) {
      phase = LinkPhase.wifiForm;
      _notify();
    } else {
      _startLinking(null);
    }
  }

  void backToWifiQuestion() {
    phase = LinkPhase.wifiQuestion;
    _notify();
  }

  void submitWifi(WifiCredentials credentials) => _startLinking(credentials);

  static const Set<MachineLinkFailure> _wifiFailures = {
    MachineLinkFailure.wifiNotFound,
    MachineLinkFailure.wifiWrongPassword,
    MachineLinkFailure.wifiFailed,
    MachineLinkFailure.noNetwork,
  };

  /// After a Wi-Fi problem the user can fix the details without starting over
  /// (the machine password stays valid for 10 minutes).
  bool get canEditWifi =>
      phase == LinkPhase.failure &&
      selected != null &&
      _wifiFailures.contains(failure);

  void editWifi() {
    if (!canEditWifi) return;
    failure = null;
    phase = LinkPhase.wifiForm;
    _notify();
  }

  void _startLinking(WifiCredentials? wifi) {
    final machine = selected;
    if (machine == null) return;
    final attempt = ++_attempt;
    _wifi = wifi;
    step = null;
    failure = null;
    phase = LinkPhase.linking;
    _notify();

    _linkSub = _service.link(machine: machine, wifi: wifi).timeout(
      linkTimeout,
      onTimeout: (sink) {
        sink.addError(
          const MachineLinkException(MachineLinkFailure.timeout),
        );
        sink.close();
      },
    ).listen(
      (event) {
        if (event.step == LinkStep.done) {
          final address = event.address;
          if (address == null) {
            _fail(MachineLinkFailure.unknown);
          } else {
            // The machine is ready: the last check happens over Wi-Fi.
            step = LinkStep.checkingConnection;
            _notify();
            unawaited(
                _finish(attempt, machine, address, wifiSent: wifi != null));
          }
          return;
        }
        step = event.step;
        _notify();
      },
      onError: (Object error) {
        if (attempt == _attempt) _fail(_failureOf(error));
      },
    );
  }

  Future<void> _finish(
    int attempt,
    DiscoveredMachine machine,
    MachineAddress address, {
    required bool wifiSent,
  }) async {
    unawaited(_service.disconnect()); // Bluetooth is no longer needed
    var reachable = false;
    for (var i = 0; i < reachabilityAttempts && !reachable; i++) {
      if (i > 0) await Future<void>.delayed(reachabilityRetryDelay);
      if (attempt != _attempt || _disposed) return;
      try {
        reachable = await checkReachable(address);
      } on Exception {
        reachable = false;
      }
    }
    if (attempt != _attempt || _disposed) return;
    if (!reachable) {
      _fail(MachineLinkFailure.apiUnreachable);
      return;
    }
    final result =
        LinkedMachine(id: machine.id, name: machine.name, address: address);
    linked = result;
    step = LinkStep.done;
    phase = LinkPhase.success;
    onLinked(result, wifiSent: wifiSent);
    _notify();
  }

  void _fail(MachineLinkFailure reason) {
    unawaited(_service.disconnect());
    failure = reason;
    phase = LinkPhase.failure;
    _notify();
  }

  MachineLinkFailure _failureOf(Object error) => error is MachineLinkException
      ? error.failure
      : MachineLinkFailure.unknown;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _attempt++;
    _scanSub?.cancel();
    _linkSub?.cancel();
    unawaited(_service.disconnect());
    super.dispose();
  }
}
