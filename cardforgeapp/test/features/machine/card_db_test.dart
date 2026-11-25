import 'package:cardforge/features/alerts/alerts_controller.dart';
import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:cardforge/features/machine/api/machine_api_models.dart';
import 'package:cardforge/features/machine/machine_controller.dart';
import 'package:cardforge/features/machine/machine_store.dart';
import 'package:cardforge/features/machine/mock_machine_link_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../helpers/fake_machine_api.dart';
import '../../helpers/test_helpers.dart';

/// The machine's card database as sorter_api/card_db_update.py sends it.
const Map<String, Object?> _available = {
  'phase': 'idle',
  'update_available': true,
  'last_check': {
    'result': 'update_available',
    'reason': 'new cards or sets on Scryfall',
    'message': 'update available: new cards or sets on Scryfall',
    'at': '2026-11-18T12:00:00+00:00',
    'seconds': 1.2,
    'download_mb': 489,
    'remote_updated_at': '2026-11-18T10:06:30.623+00:00',
  },
  'last_update': null,
  'progress': null,
  'local': {
    'bulk_updated_at': '2026-11-17T10:06:30.623+00:00',
    'built_at': '2026-11-17T18:26:02Z',
    'printed_langs': 'pt',
    'schema_version': '2',
    'cards': 110960,
  },
};

Map<String, Object?> _updating({double fraction = 0.42}) => {
      ..._available,
      'phase': 'updating',
      'progress': {
        'stage': 'all_cards',
        'fraction': fraction,
        'stage_fraction': 0.5,
        'downloaded_mb': 204.0,
        'download_mb': 489.0,
        'speed_mb_s': 2.1,
        'eta_s': 130,
        'elapsed_s': 95,
      },
    };

Map<String, Object?> _finished(String result) => {
      ..._available,
      'update_available': result != 'updated',
      'last_update': {
        'result': result,
        'message': 'machine message',
        'at': '2026-11-18T12:10:00+00:00',
        'seconds': 212.4,
      },
    };

CardDbStatus _status(Map<String, Object?> json) => CardDbStatus.fromJson(json);

/// WebSocket events arrive in a microtask: the first pump delivers them, the
/// second one draws the result.
Future<void> _deliver(WidgetTester tester) async {
  await tester.pump();
  await tester.pump();
}

void main() {
  group('machine JSON', () {
    test('card_db in the snapshot and as its own event', () {
      final db =
          MachineSnapshot.fromJson({'state': 'idle', 'card_db': _available})
              .cardDb!;
      expect(db.phase, CardDbPhase.idle);
      expect(db.updateAvailable, isTrue);
      expect(db.lastCheck!.downloadMb, 489);
      expect(db.lastCheck!.reason, 'new cards or sets on Scryfall');
      expect(db.localCards, 110960);
      expect(db.localBuiltAt, DateTime.utc(2026, 11, 17, 18, 26, 2));

      final event =
          MachineEvent.fromJson({'type': 'card_db', 'card_db': _updating()});
      expect(event, isA<CardDbEvent>());
      final progress = (event as CardDbEvent).status.progress!;
      expect(progress.stage, 'all_cards');
      expect(progress.fraction, 0.42);
      expect(progress.etaSeconds, 130);
      expect(progress.downloadMb, 489);
    });

    test('older machines and odd values do not break it', () {
      expect(MachineSnapshot.fromJson({'state': 'idle'}).cardDb, isNull);
      final db = CardDbStatus.fromJson({
        'phase': 'rebuilding',
        'progress': {'fraction': 3},
      });
      expect(db.phase, CardDbPhase.unknown);
      expect(db.progress!.fraction, 1.0);
      expect(db.updateAvailable, isNull);
      expect(MachineEvent.fromJson({'type': 'card_db'}),
          isA<UnknownMachineEvent>());
    });
  });

  group('controller', () {
    TestWidgetsFlutterBinding.ensureInitialized();

    late FakeMachineApi api;
    late AlertsController alerts;

    Future<MachineController> start() async {
      SharedPreferences.setMockInitialValues(
          Map<String, Object>.of(linkedMachinePrefs));
      final controller = MachineController(
        store: MachineStore(await SharedPreferences.getInstance()),
        apiFor: (_) => api,
        alerts: alerts,
        linkService: MockMachineLinkService(stepDelay: Duration.zero),
        retryDelays: const [Duration(milliseconds: 10)],
      );
      addTearDown(controller.dispose);
      controller.start();
      await pumpEventQueue();
      return controller;
    }

    List<AlertType> alertTypes() => alerts.alerts.map((a) => a.type).toList();

    setUp(() {
      api = FakeMachineApi(
        snapshot: MachineSnapshot(
            state: ControllerState.idle, cardDb: _status(_available)),
      );
      alerts = AlertsController();
    });

    test('tells the user once that an update is available', () async {
      final controller = await start();

      expect(controller.cardDb!.updateAvailable, isTrue);
      expect(alertTypes(), [AlertType.cardDbUpdateAvailable]);
      expect(controller.canUpdateCardDb, isTrue);

      // The same Scryfall data again (a reconnect, a manual check): no repeat.
      api.emit(CardDbEvent(_status(_available)));
      await pumpEventQueue();
      expect(alertTypes(), [AlertType.cardDbUpdateAvailable]);
    });

    test('follows the update it started and tells when it ends', () async {
      final controller = await start();

      await controller.updateCardDb();
      expect(api.calls.last, 'updateCardDb');
      expect(controller.isUpdatingCardDb, isTrue);
      expect(controller.canStart, isFalse); // the machine can't sort now
      expect(controller.canCancelCardDbUpdate, isTrue);

      api.emit(CardDbEvent(_status(_updating())));
      await pumpEventQueue();
      expect(controller.cardDb!.progress!.fraction, 0.42);

      api.emit(CardDbEvent(_status(_finished('updated'))));
      await pumpEventQueue();
      expect(controller.isUpdatingCardDb, isFalse);
      expect(controller.canStart, isTrue);
      expect(alertTypes().first, AlertType.cardDbUpdated);
    });

    test('a failed update is reported, a cancelled one is not', () async {
      final controller = await start();

      api.emit(CardDbEvent(_status(_updating())));
      await pumpEventQueue();
      api.emit(CardDbEvent(_status(_finished('failed'))));
      await pumpEventQueue();
      expect(alertTypes().first, AlertType.cardDbUpdateFailed);

      api.emit(CardDbEvent(_status(_updating())));
      await pumpEventQueue();
      await controller.cancelCardDbUpdate();
      expect(api.calls.last, 'cancelCardDbUpdate');
      expect(controller.cardDb!.lastUpdate!.result, 'cancelled');
      expect(alertTypes().first, AlertType.cardDbUpdateFailed); // no new one
    });

    test('check, update anyway, and a refusal', () async {
      api.snapshot = const MachineSnapshot(
        state: ControllerState.idle,
        cardDb: CardDbStatus(updateAvailable: false),
      );
      final controller = await start();
      expect(alerts.alerts, isEmpty);

      await controller.checkCardDb();
      expect(api.calls.last, 'checkCardDb');
      expect(controller.cardDb!.isChecking, isTrue);
      expect(controller.canCheckCardDb, isFalse);

      api.cardDb = const CardDbStatus(updateAvailable: false);
      api.emit(CardDbEvent(api.cardDb));
      await pumpEventQueue();
      api.cardDbError = const MachineApiException(
        MachineApiFailure.conflict,
        statusCode: 409,
        code: 'RUN_ACTIVE',
      );
      await controller.updateCardDb(force: true);
      expect(api.calls.last, 'updateCardDb(force)');
      expect(controller.cardDbError!.code, 'RUN_ACTIVE');
    });

    test('no update while a run is active', () async {
      api.snapshot = MachineSnapshot(
        state: ControllerState.running,
        run: FakeMachineApi.runningRun(),
        cardDb: _status(_available),
      );
      final controller = await start();

      expect(controller.canCheckCardDb, isTrue);
      expect(controller.canUpdateCardDb, isFalse);
    });

    test('a machine without a card database shows nothing', () async {
      api.snapshot = const MachineSnapshot(state: ControllerState.idle);
      final controller = await start();

      expect(controller.cardDb, isNull);
      expect(controller.canCheckCardDb, isFalse);
      expect(alerts.alerts, isEmpty);
    });
  });

  group('screens', () {
    setUpAll(configureTestEnvironment);

    Future<FakeMachineApi> open(
      WidgetTester tester,
      Map<String, Object?> cardDb, {
      String location = '/machine',
      bool portuguese = false,
    }) async {
      final api = FakeMachineApi(
        snapshot: MachineSnapshot(
            state: ControllerState.idle, cardDb: _status(cardDb)),
      )..cardDb = _status(cardDb);
      await pumpApp(
        tester,
        initialLocation: location,
        machineApi: api,
        preferences: {
          ...linkedMachinePrefs,
          if (portuguese) 'language_code': 'pt',
        },
      );
      await tester.pumpAndSettle();
      // The "update available" banner (a SnackBar with an action, which newer
      // Flutter keeps until dismissed) would cover the panel's buttons.
      final banner = find.byType(SnackBar);
      if (banner.evaluate().isNotEmpty) {
        ScaffoldMessenger.of(tester.element(banner.first))
            .removeCurrentSnackBar();
        await tester.pumpAndSettle();
      }
      return api;
    }

    Future<void> show(WidgetTester tester, String key) => tester.scrollUntilVisible(
          find.byKey(ValueKey(key)),
          200,
          scrollable: find.byType(Scrollable).first,
        );

    testWidgets('update available: confirm, follow the progress, cancel',
        (tester) async {
      final api = await open(tester, _available);
      await show(tester, 'card-db-update');

      expect(find.text('Card database'), findsOneWidget);
      expect(
        find.text('Update available: Scryfall has new cards (about 489 MB '
            'to download).'),
        findsOneWidget,
      );
      expect(find.textContaining('110,960 printings'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('card-db-update')));
      await tester.pumpAndSettle();
      expect(find.text('Update the card database?'), findsOneWidget);
      expect(find.textContaining('about 489 MB'), findsWidgets);
      await tester.tap(find.byKey(const ValueKey('card-db-confirm')));
      // The bar is indeterminate until the first progress: it never settles.
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(api.calls, contains('updateCardDb'));
      expect(find.byKey(const ValueKey('card-db-cancel')), findsOneWidget);

      api.emit(CardDbEvent(_status(_updating())));
      await _deliver(tester);
      await show(tester, 'card-db-cancel');
      final bar = tester.widget<LinearProgressIndicator>(
          find.byKey(const ValueKey('card-db-progress')));
      expect(bar.value, 0.42);
      expect(
        find.text('Downloading the Portuguese names… · 42% · 204 of 489 MB · '
            'about 2 minutes left'),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const ValueKey('card-db-cancel')));
      await tester.pumpAndSettle();
      expect(api.calls.last, 'cancelCardDbUpdate');
      expect(
        find.text('The last update was cancelled. The machine kept its cards.'),
        findsOneWidget,
      );
    });

    testWidgets('up to date: check for updates', (tester) async {
      final api = await open(tester, {
        ..._available,
        'update_available': false,
        'last_check': {'result': 'up_to_date'},
      });
      await show(tester, 'card-db-check');

      expect(find.text('Up to date.'), findsOneWidget);
      expect(find.byKey(const ValueKey('card-db-update')), findsNothing);
      await tester.tap(find.byKey(const ValueKey('card-db-check')));
      await tester.pump();
      expect(api.calls.last, 'checkCardDb');
      expect(find.text('Checking Scryfall for new cards…'), findsOneWidget);
    });

    testWidgets('in Portuguese', (tester) async {
      final api = await open(tester, _available, portuguese: true);
      api.emit(CardDbEvent(_status(_updating())));
      await _deliver(tester);
      await show(tester, 'card-db-cancel');

      expect(find.text('Base de cartas'), findsOneWidget);
      expect(
        find.text('Baixando os nomes em português… · 42% · 204 de 489 MB · '
            'cerca de 2 minutos restantes'),
        findsOneWidget,
      );
      expect(find.text('Cancelar atualização'), findsOneWidget);
    });

    testWidgets('Home says an update is available', (tester) async {
      await open(tester, _available, location: '/home');

      expect(find.byKey(const ValueKey('home-card-db')), findsOneWidget);
      expect(find.text('A card database update is available. Tap to update.'),
          findsOneWidget);
    });
  });
}
