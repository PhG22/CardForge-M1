import 'dart:async';

import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:cardforge/features/machine/api/machine_api_models.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../helpers/fake_machine_api.dart';
import '../../helpers/test_helpers.dart';

/// WebSocket events arrive in a microtask: the first pump delivers them, the
/// second one draws the result.
Future<void> deliver(WidgetTester tester) async {
  await tester.pump();
  await tester.pump();
}

void main() {
  setUpAll(configureTestEnvironment);

  Future<FakeMachineApi> openControl(
    WidgetTester tester, {
    FakeMachineApi? api,
    Map<String, Object> preferences = linkedMachinePrefs,
  }) async {
    final machineApi = api ?? FakeMachineApi();
    await pumpApp(
      tester,
      initialLocation: '/machine',
      machineApi: machineApi,
      preferences: preferences,
    );
    await tester.pumpAndSettle();
    return machineApi;
  }

  testWidgets('shows the machine online and ready', (tester) async {
    await openControl(tester);

    expect(find.text('CardForge-A1B2'), findsOneWidget);
    expect(find.text('192.168.0.42:8000'), findsOneWidget);
    expect(find.text('Online'), findsOneWidget);
    expect(find.text('Ready'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Start sorting'), findsOneWidget);
  });

  testWidgets('start, live progress, and the finished run', (tester) async {
    final api = await openControl(tester);
    final gate = api.startGate = Completer<void>();

    await tester.tap(find.text('Start sorting'));
    await tester.pump();
    expect(find.text('Preparing…'), findsOneWidget);
    expect(find.textContaining('homing the base'), findsOneWidget);

    gate.complete();
    await tester.pumpAndSettle();
    expect(find.text('Sorting'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Stop'), findsOneWidget);
    expect(find.text('Emergency stop'), findsOneWidget);

    api.emit(const CardSortedEvent(runId: 'run-1', cardNumber: 1, tray: 4));
    api.emit(const CardSortedEvent(runId: 'run-1', cardNumber: 2, tray: 2));
    await deliver(tester);
    expect(find.text('Recently sorted'), findsOneWidget);
    expect(find.text('Card #2 → Folder 2'), findsOneWidget);
    expect(find.text('Card #1 → Folder 4'), findsOneWidget);

    api.emit(const RunFinishedEvent(
      RunInfo(runId: 'run-1', status: RunStatus.completed, cardsProcessed: 2),
      'Stack is empty: 2 card(s) processed.',
    ));
    await deliver(tester);
    expect(find.text('Ready'), findsOneWidget);
    expect(find.text('Finished: 2 cards sorted.'), findsOneWidget);
    // The alert banner shows up on top of the screen.
    expect(find.text('Sorting finished'), findsOneWidget);
  });

  testWidgets('lists every reason the machine cannot start', (tester) async {
    final api = await openControl(tester);
    api.startError = const MachineApiException(
      MachineApiFailure.conflict,
      statusCode: 409,
      problems: [
        StartProblem(code: 'STACK_EMPTY', message: ''),
        StartProblem(code: 'TRAYS_NOT_EMPTY', message: '', trays: [2, 5]),
      ],
    );

    await tester.tap(find.text('Start sorting'));
    await tester.pumpAndSettle();

    expect(find.text('Cannot start yet'), findsOneWidget);
    expect(find.text('• No cards on the stack. Load cards and try again.'),
        findsOneWidget);
    expect(find.text('• Folders 2, 5 are not empty. Empty them and try again.'),
        findsOneWidget);
  });

  testWidgets('stop sends the stop and shows the result', (tester) async {
    final api = FakeMachineApi(
      snapshot: MachineSnapshot(
        state: ControllerState.running,
        run: FakeMachineApi.runningRun(cards: 5, tray: 1),
      ),
    );
    await openControl(tester, api: api);
    expect(find.text('Sorting'), findsOneWidget);
    expect(find.text('5'), findsOneWidget); // cards processed

    await tester.tap(find.widgetWithText(FilledButton, 'Stop'));
    await tester.pumpAndSettle();

    expect(api.calls, contains('stopRun'));
    expect(find.text('Stopped by you after 5 cards.'), findsOneWidget);
  });

  testWidgets('a paused run shows the fault, Resume and Stop (RF26, RF27)',
      (tester) async {
    final api = FakeMachineApi(
      snapshot: MachineSnapshot(
        state: ControllerState.paused,
        run: FakeMachineApi.pausedRun(cards: 3, code: 'DROP_FAILED'),
      ),
    );
    await openControl(tester, api: api);

    expect(find.text('Paused'), findsOneWidget);
    expect(find.text('A card did not fall from the platform.'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Resume'), findsOneWidget);
    expect(find.widgetWithText(OutlinedButton, 'Stop'), findsOneWidget);
    expect(find.text('Start sorting'), findsNothing);

    await tester.tap(find.widgetWithText(FilledButton, 'Resume'));
    await tester.pumpAndSettle();

    expect(api.calls, contains('resumeRun'));
    expect(find.text('Sorting'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Stop'), findsOneWidget);
  });

  testWidgets('a fault during a run shows a banner with its type and time',
      (tester) async {
    final api = FakeMachineApi(
      snapshot: MachineSnapshot(
        state: ControllerState.running,
        run: FakeMachineApi.runningRun(cards: 2),
      ),
    );
    await openControl(tester, api: api);

    final paused = FakeMachineApi.pausedRun(cards: 2);
    api.emit(FaultEvent(code: 'FEED_TIMEOUT', time: paused.faultAt, run: paused));
    await deliver(tester);

    expect(find.text('Card jam detected'), findsOneWidget);
    expect(find.textContaining(RegExp(r'paused at \d\d:\d\d:\d\d')),
        findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Resume'), findsOneWidget);
  });

  testWidgets('offline: explains and offers to retry or find over Bluetooth',
      (tester) async {
    final api = FakeMachineApi(reachable: false);
    await openControl(tester, api: api);

    expect(find.text('Offline'), findsOneWidget);
    expect(find.text('Cannot reach the machine'), findsOneWidget);
    expect(find.textContaining('No answer from 192.168.0.42:8000'),
        findsOneWidget);

    // The machine got a new address: find it over (simulated) Bluetooth.
    api.reachable = true;
    await tester.tap(find.text('Find over Bluetooth'));
    await tester.pumpAndSettle();

    expect(find.text('Online'), findsOneWidget);
    expect(find.text('10.0.2.2:8000'), findsOneWidget);
  });

  testWidgets('a lost connection during a run raises an alert', (tester) async {
    final api = await openControl(tester);
    api.emit(RunStartedEvent(FakeMachineApi.runningRun()));
    await deliver(tester);

    api.reachable = false;
    await api.dropEvents();
    await deliver(tester);

    expect(find.text('Offline'), findsOneWidget);
    expect(find.text('Connection lost'), findsOneWidget); // banner
  });

  testWidgets('diagnostics show the modules and move the base', (tester) async {
    final api = await openControl(tester);

    final list = find.byType(Scrollable).first;
    await tester.scrollUntilVisible(find.text('Diagnostics'), 200,
        scrollable: list);
    await tester.pumpAndSettle(); // draw the scrolled position before tapping
    await tester.tap(find.byIcon(Icons.expand_more_rounded));
    await tester.pumpAndSettle();

    expect(find.text('Top module (A1)'), findsOneWidget);
    expect(find.text('Closed (80°)'), findsOneWidget);
    expect(find.text('Base (A2)'), findsOneWidget);

    await tester.scrollUntilVisible(find.widgetWithText(ActionChip, '5'), 200,
        scrollable: list);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(ActionChip, '5'));
    await tester.pumpAndSettle();
    expect(api.calls, contains('gotoTray(5)'));
    expect(find.text('Position: Folder 5'), findsOneWidget);
  });

  testWidgets('top-only mode hides the base', (tester) async {
    await openControl(tester, api: FakeMachineApi(bottomEnabled: false));

    expect(find.text('Top module only: no rotating base'), findsOneWidget);
    expect(find.text('Current folder'), findsNothing);
  });

  testWidgets('forget the machine after confirming', (tester) async {
    await openControl(tester);

    await tester.tap(find.byType(PopupMenuButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Forget this machine'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Forget CardForge-A1B2 on this phone?'),
        findsOneWidget);

    await tester.tap(find.text('Forget'));
    await tester.pumpAndSettle();

    expect(find.text('No machine linked'), findsOneWidget); // back on Home
  });

  testWidgets('without a machine it offers to link one', (tester) async {
    await openControl(tester, preferences: const {});

    expect(find.text('No machine linked'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Link machine'));
    await tester.pumpAndSettle();
    expect(find.text('Select your machine'), findsOneWidget);
  });

  testWidgets('works in Portuguese', (tester) async {
    await openControl(
      tester,
      preferences: {...linkedMachinePrefs, 'language_code': 'pt'},
    );

    expect(find.text('Controle da máquina'), findsOneWidget);
    expect(find.text('Pronta'), findsOneWidget);
    expect(find.text('Iniciar ordenação'), findsOneWidget);
  });

  test('every alert type used by the controller exists', () {
    expect(AlertType.values, contains(AlertType.runFailed));
  });
}