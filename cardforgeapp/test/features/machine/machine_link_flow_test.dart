import 'dart:async';

import 'package:cardforge/features/machine/machine_link_flow.dart';
import 'package:cardforge/features/machine/machine_link_service.dart';
import 'package:cardforge/features/machine/machine_models.dart';
import 'package:cardforge/features/machine/mock_machine_link_service.dart';
import 'package:flutter_test/flutter_test.dart';

const _machine = DiscoveredMachine(
  id: 'B8:27:EB:00:A1:B2',
  name: 'CardForge-A1B2',
  signalDbm: -48,
);

const _address = MachineAddress(host: '192.168.0.42');

/// A machine that accepts the password but never confirms the link.
class _HangingService extends MockMachineLinkService {
  _HangingService() : super(stepDelay: Duration.zero);

  @override
  Stream<List<DiscoveredMachine>> scan() => Stream.value(const [_machine]);

  @override
  Stream<LinkProgress> link({
    required DiscoveredMachine machine,
    WifiCredentials? wifi,
  }) =>
      StreamController<LinkProgress>().stream; // never emits
}

/// Bluetooth is off.
class _NoBluetoothService extends MockMachineLinkService {
  _NoBluetoothService() : super(stepDelay: Duration.zero);

  @override
  Stream<List<DiscoveredMachine>> scan() => Stream.error(
      const MachineLinkException(MachineLinkFailure.bluetoothUnavailable));
}

/// Counts disconnects, to check that Bluetooth is released.
class _CountingService extends MockMachineLinkService {
  _CountingService() : super(stepDelay: Duration.zero, address: _address);

  int disconnects = 0;

  @override
  Future<void> disconnect() async => disconnects++;
}

/// Waits (in real time) until [condition] is true.
Future<void> settle(bool Function() condition) async {
  for (var i = 0; i < 400 && !condition(); i++) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
  expect(condition(), isTrue, reason: 'condition was not met in time');
}

void main() {
  LinkedMachine? linked;
  bool? lastWifiSent;
  final checked = <MachineAddress>[];

  MachineLinkFlow makeFlow(
    MachineLinkService service, {
    Duration timeout = const Duration(seconds: 5),
    bool reachable = true,
  }) {
    final flow = MachineLinkFlow(
      service,
      linkTimeout: timeout,
      reachabilityRetryDelay: Duration.zero,
      checkReachable: (address) async {
        checked.add(address);
        return reachable;
      },
      onLinked: (machine, {required wifiSent}) {
        linked = machine;
        lastWifiSent = wifiSent;
      },
    );
    addTearDown(flow.dispose);
    return flow;
  }

  MockMachineLinkService fastMock() =>
      MockMachineLinkService(stepDelay: Duration.zero, address: _address);

  /// Runs the flow up to the "Send Wi-Fi details?" question.
  Future<MachineLinkFlow> flowAtWifiQuestion({
    MachineLinkService? service,
    bool reachable = true,
  }) async {
    final flow = makeFlow(service ?? fastMock(), reachable: reachable);
    flow.startScan();
    await settle(() => flow.machines.isNotEmpty);
    flow.select(flow.machines.first);
    await flow.submitPassword('1234');
    expect(flow.phase, LinkPhase.wifiQuestion);
    return flow;
  }

  setUp(() {
    linked = null;
    lastWifiSent = null;
    checked.clear();
  });

  test('the scan lists the machines found and then stops scanning', () async {
    final flow = makeFlow(fastMock());

    flow.startScan();
    expect(flow.scanning, isTrue);

    await settle(() => !flow.scanning);
    expect(
        flow.machines.map((m) => m.name), ['CardForge-A1B2', 'CardForge-9F3C']);
    expect(flow.scanFailure, isNull);
  });

  test('selecting a machine moves to the password step', () async {
    final flow = makeFlow(fastMock());
    flow.startScan();
    await settle(() => flow.machines.isNotEmpty);

    flow.select(flow.machines.first);

    expect(flow.phase, LinkPhase.password);
    expect(flow.selected?.name, 'CardForge-A1B2');
    expect(flow.scanning, isFalse);
  });

  test('a wrong password keeps the step and reports the failure', () async {
    final flow = makeFlow(fastMock());
    flow.startScan();
    await settle(() => flow.machines.isNotEmpty);
    flow.select(flow.machines.first);

    await flow.submitPassword(MockMachineLinkService.rejectedPassword);

    expect(flow.phase, LinkPhase.password);
    expect(flow.passwordFailure, MachineLinkFailure.wrongPassword);
    expect(flow.checkingPassword, isFalse);

    flow.clearPasswordFailure();
    expect(flow.passwordFailure, isNull);

    await flow.submitPassword('1234');
    expect(flow.phase, LinkPhase.wifiQuestion);
  });

  test('going back from the password step returns to the list', () async {
    final flow = makeFlow(fastMock());
    flow.startScan();
    await settle(() => flow.machines.isNotEmpty);
    flow.select(flow.machines.first);

    flow.backToList();

    expect(flow.phase, LinkPhase.deviceList);
    expect(flow.selected, isNull);
  });

  test('skipping Wi-Fi links the machine with the address it reports',
      () async {
    final flow = await flowAtWifiQuestion();

    flow.answerWifiQuestion(send: false);
    expect(flow.phase, LinkPhase.linking);
    await settle(() => flow.phase == LinkPhase.success);

    expect(flow.sentWifi, isFalse);
    expect(linked?.name, 'CardForge-A1B2');
    expect(linked?.address, _address);
    expect(lastWifiSent, isFalse);
    expect(checked, [_address]); // reached over Wi-Fi before saying "linked"
  });

  test('sending Wi-Fi goes through every step and links the machine', () async {
    final flow = await flowAtWifiQuestion();
    final seen = <LinkStep>[];
    flow.addListener(() {
      final step = flow.step;
      if (step != null && (seen.isEmpty || seen.last != step)) seen.add(step);
    });

    flow.answerWifiQuestion(send: true);
    expect(flow.phase, LinkPhase.wifiForm);

    flow.submitWifi(
        const WifiCredentials(ssid: 'HomeWiFi', password: 'secret123'));
    await settle(() => flow.phase == LinkPhase.success);

    expect(seen, [
      LinkStep.sendingWifi,
      LinkStep.waitingForWifi,
      LinkStep.checkingConnection,
      LinkStep.done,
    ]);
    expect(flow.sentWifi, isTrue);
    expect(lastWifiSent, isTrue);
    expect(linked?.id, 'B8:27:EB:00:A1:B2');
    expect(linked?.address?.label, '192.168.0.42:8000');
  });

  test('the Wi-Fi form can go back to the question', () async {
    final flow = await flowAtWifiQuestion();
    flow.answerWifiQuestion(send: true);

    flow.backToWifiQuestion();

    expect(flow.phase, LinkPhase.wifiQuestion);
  });

  test('a network the machine cannot find ends in a failure that can be fixed',
      () async {
    final flow = await flowAtWifiQuestion();
    flow.answerWifiQuestion(send: true);

    flow.submitWifi(const WifiCredentials(
      ssid: MockMachineLinkService.failingNetwork,
      password: '',
    ));
    await settle(() => flow.phase == LinkPhase.failure);

    expect(flow.failure, MachineLinkFailure.wifiNotFound);
    expect(linked, isNull);
    expect(flow.canEditWifi, isTrue);

    // Fix the details without scanning and typing the password again.
    flow.editWifi();
    expect(flow.phase, LinkPhase.wifiForm);
    flow.submitWifi(const WifiCredentials(ssid: 'HomeWiFi', password: ''));
    await settle(() => flow.phase == LinkPhase.success);
  });

  test('a rejected Wi-Fi password is reported as such', () async {
    final flow = await flowAtWifiQuestion();
    flow.submitWifi(const WifiCredentials(
      ssid: 'HomeWiFi',
      password: MockMachineLinkService.rejectedWifiPassword,
    ));
    await settle(() => flow.phase == LinkPhase.failure);

    expect(flow.failure, MachineLinkFailure.wifiWrongPassword);
  });

  test('retry from a failure restarts with a fresh scan', () async {
    final flow = await flowAtWifiQuestion();
    flow.submitWifi(const WifiCredentials(
      ssid: MockMachineLinkService.failingNetwork,
      password: '',
    ));
    await settle(() => flow.phase == LinkPhase.failure);

    flow.startScan();
    expect(flow.phase, LinkPhase.deviceList);
    expect(flow.failure, isNull);
    await settle(() => flow.machines.isNotEmpty);
  });

  test('fails when the phone cannot reach the machine over Wi-Fi', () async {
    final flow = await flowAtWifiQuestion(reachable: false);

    flow.answerWifiQuestion(send: false);
    await settle(() => flow.phase == LinkPhase.failure);

    expect(flow.failure, MachineLinkFailure.apiUnreachable);
    expect(checked, hasLength(3)); // retried before giving up
    expect(linked, isNull);
    expect(flow.canEditWifi, isFalse);
  });

  test('Bluetooth is released once the machine is linked', () async {
    final service = _CountingService();
    final flow = await flowAtWifiQuestion(service: service);
    final before = service.disconnects;

    flow.answerWifiQuestion(send: false);
    await settle(() => flow.phase == LinkPhase.success);

    expect(service.disconnects, greaterThan(before));
  });

  test('fails with a timeout when the machine never confirms', () async {
    final flow = makeFlow(
      _HangingService(),
      timeout: const Duration(milliseconds: 50),
    );
    flow.startScan();
    await settle(() => flow.machines.isNotEmpty);
    flow.select(flow.machines.first);
    await flow.submitPassword('1234');

    flow.answerWifiQuestion(send: false);
    expect(flow.isBusy, isTrue);

    await settle(() => flow.phase == LinkPhase.failure);
    expect(flow.failure, MachineLinkFailure.timeout);
    expect(flow.isBusy, isFalse);
  });

  test('a scan error (Bluetooth off) is reported', () async {
    final flow = makeFlow(_NoBluetoothService());

    flow.startScan();
    await settle(() => !flow.scanning);

    expect(flow.scanFailure, MachineLinkFailure.bluetoothUnavailable);
    expect(flow.machines, isEmpty);
  });

  test('signal level goes from weak to strong', () {
    DiscoveredMachine at(int dbm) =>
        DiscoveredMachine(id: 'x', name: 'x', signalDbm: dbm);

    expect(at(-48).signalLevel, 3);
    expect(at(-70).signalLevel, 2);
    expect(at(-90).signalLevel, 1);
  });

  test('Wi-Fi password rules match the machine', () {
    expect(WifiCredentials.isValidPassword(''), isTrue);
    expect(WifiCredentials.isValidPassword('1234567'), isFalse);
    expect(WifiCredentials.isValidPassword('12345678'), isTrue);
    expect(WifiCredentials.isValidPassword('a' * 63), isTrue);
    expect(WifiCredentials.isValidPassword('a' * 64), isTrue); // hex key
    expect(WifiCredentials.isValidPassword('g' * 64), isFalse);
  });
}
