import 'package:cardforge/features/alerts/alerts_controller.dart';
import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:cardforge/features/machine/api/machine_api_models.dart';
import 'package:cardforge/features/machine/machine_controller.dart';
import 'package:cardforge/features/machine/machine_models.dart';
import 'package:cardforge/features/machine/machine_store.dart';
import 'package:cardforge/features/machine/mock_machine_link_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../helpers/fake_machine_api.dart';
import '../../helpers/test_helpers.dart';

const _newAddress = MachineAddress(host: '192.168.0.77');

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late FakeMachineApi api;
  late AlertsController alerts;
  late MachineStore store;
  late List<MachineAddress> apiAddresses;

  Future<MachineController> start({
    Map<String, Object> prefs = linkedMachinePrefs,
  }) async {
    SharedPreferences.setMockInitialValues(Map<String, Object>.of(prefs));
    store = MachineStore(await SharedPreferences.getInstance());
    final controller = MachineController(
      store: store,
      apiFor: (address) {
        apiAddresses.add(address);
        return api;
      },
      alerts: alerts,
      linkService: MockMachineLinkService(
        stepDelay: Duration.zero,
        address: _newAddress,
      ),
      retryDelays: const [Duration(milliseconds: 10)],
    );
    addTearDown(controller.dispose);
    controller.start();
    await pumpEventQueue();
    return controller;
  }

  List<AlertType> alertTypes() => alerts.alerts.map((a) => a.type).toList();

  setUp(() {
    api = FakeMachineApi();
    alerts = AlertsController();
    apiAddresses = [];
  });

  test('without a linked machine it does nothing', () async {
    final controller = await start(prefs: {});

    expect(controller.connection, MachineConnection.none);
    expect(api.calls, isEmpty);
  });

  test('a machine linked by an older version has no address', () async {
    final controller =
        await start(prefs: {'machine_id': 'AA', 'machine_name': 'X'});

    expect(controller.connection, MachineConnection.noAddress);
    expect(api.calls, isEmpty);
  });

  test('connects to the linked machine and follows its events', () async {
    final controller = await start();

    expect(controller.connection, MachineConnection.online);
    expect(apiAddresses.single.label, '192.168.0.42:8000');
    expect(api.calls, ['health', 'runStatus', 'events']);
    expect(controller.state, ControllerState.idle);
    expect(controller.canStart, isTrue);
    expect(api.hasEventListener, isTrue);
  });

  test('run events update the progress and end with one alert', () async {
    final controller = await start();

    api.emit(RunStartedEvent(FakeMachineApi.runningRun()));
    api.emit(const CardSortedEvent(runId: 'run-1', cardNumber: 1, tray: 4));
    api.emit(const CardSortedEvent(runId: 'run-1', cardNumber: 2, tray: 6));
    await pumpEventQueue();

    expect(controller.isBusy, isTrue);
    expect(controller.canStart, isFalse);
    expect(controller.run?.cardsProcessed, 2);
    expect(controller.run?.currentTray, 6);
    expect(controller.recentCards.map((c) => c.tray), [6, 4]);

    const finished = RunInfo(
      runId: 'run-1',
      status: RunStatus.completed,
      cardsProcessed: 2,
    );
    api.emit(const RunFinishedEvent(
        finished, 'Stack is empty: 2 card(s) processed.'));
    // The same news again in a snapshot must not alert twice.
    api.emit(const SnapshotEvent(
        MachineSnapshot(state: ControllerState.idle, run: finished)));
    await pumpEventQueue();

    expect(controller.isBusy, isFalse);
    expect(alertTypes(), [AlertType.batchFinished]);
    expect(alerts.alerts.single.cardCount, 2);
    expect(alerts.alerts.single.machineName, 'CardForge-A1B2');
  });

  test('a jam and other failures raise different alerts; a stop raises none',
      () async {
    await start();

    for (final (id, status, code) in [
      ('a', RunStatus.failed, 'FEED_TIMEOUT'),
      ('b', RunStatus.failed, 'MODULE_OFFLINE'),
      ('c', RunStatus.cancelled, null),
    ]) {
      api.emit(RunStartedEvent(RunInfo(runId: id, status: RunStatus.running)));
      api.emit(RunFinishedEvent(
          RunInfo(runId: id, status: status, errorCode: code), null));
    }
    await pumpEventQueue();

    expect(alertTypes(), [AlertType.runFailed, AlertType.jamDetected]);
  });

  test('losing the connection during a run alerts and reconnects', () async {
    final controller = await start();
    api.emit(RunStartedEvent(FakeMachineApi.runningRun()));
    await pumpEventQueue();

    api.reachable = false;
    await api.dropEvents();
    await pumpEventQueue();

    expect(controller.connection, MachineConnection.offline);
    expect(alertTypes(), [AlertType.connectionLost]);

    api.reachable = true;
    await Future<void>.delayed(const Duration(milliseconds: 50));
    await pumpEventQueue();

    expect(controller.connection, MachineConnection.online);
  });

  test('a run that ended while offline is announced on reconnect', () async {
    final controller = await start();
    api.emit(RunStartedEvent(FakeMachineApi.runningRun(cards: 3)));
    await pumpEventQueue();

    api.reachable = false;
    await api.dropEvents();
    await pumpEventQueue();
    api.snapshot = const MachineSnapshot(
      state: ControllerState.idle,
      run: RunInfo(
          runId: 'run-1', status: RunStatus.completed, cardsProcessed: 9),
    );
    api.reachable = true;
    await controller.reconnect();
    await pumpEventQueue();

    expect(alertTypes(), [AlertType.batchFinished, AlertType.connectionLost]);
    expect(alerts.alerts.first.cardCount, 9);
  });

  test('an unreachable machine is offline until it answers', () async {
    api.reachable = false;
    final controller = await start();

    expect(controller.connection, MachineConnection.offline);
    expect(alerts.alerts, isEmpty, reason: 'nothing was running');

    api.reachable = true;
    await controller.reconnect();

    expect(controller.connection, MachineConnection.online);
  });

  test('start reports every problem at once', () async {
    final controller = await start();
    api.startError = const MachineApiException(
      MachineApiFailure.conflict,
      statusCode: 409,
      problems: [
        StartProblem(code: 'STACK_EMPTY', message: ''),
        StartProblem(code: 'TRAYS_NOT_EMPTY', message: '', trays: [2, 5]),
      ],
    );

    await controller.startRun();

    expect(controller.startProblems.map((p) => p.code),
        ['STACK_EMPTY', 'TRAYS_NOT_EMPTY']);
    expect(controller.isStarting, isFalse);
    expect(controller.canStart, isTrue);

    await controller.startRun();
    expect(controller.startProblems, isEmpty);
    expect(controller.state, ControllerState.running);
    expect(controller.canStop, isTrue);

    await controller.stopRun();
    expect(controller.state, ControllerState.idle);
    expect(controller.run?.status, RunStatus.cancelled);

    await controller.stopRun(immediate: true);
    expect(api.calls, contains('stopRun(immediate)'));
  });

  test('hardware status and base moves', () async {
    final controller = await start();

    await controller.loadHardware();
    expect(controller.hardware?.top.flapAngle, 80);

    await controller.gotoTray(5);
    expect(controller.baseResult?.base.position, 5);
    await controller.homeBase();
    expect(controller.baseResult?.base.drift, -2);
    expect(api.calls, containsAllInOrder(['gotoTray(5)', 'homeBase']));
  });

  test('"Find over Bluetooth" saves the new address and reconnects', () async {
    api.reachable = false;
    final controller = await start();
    expect(controller.connection, MachineConnection.offline);

    api.reachable = true;
    await controller.findAgain();
    await pumpEventQueue();

    expect(store.machine?.address, _newAddress);
    expect(apiAddresses.last, _newAddress);
    expect(controller.connection, MachineConnection.online);
    expect(controller.isFindingAddress, isFalse);
  });

  test('forgetting the machine disconnects', () async {
    final controller = await start();

    await controller.forget();

    expect(controller.connection, MachineConnection.none);
    expect(store.machine, isNull);
    expect(api.closed, 1);
  });
}
