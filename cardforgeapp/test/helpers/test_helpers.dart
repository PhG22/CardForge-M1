import 'package:cardforge/app.dart';
import 'package:cardforge/core/app_scope.dart';
import 'package:cardforge/core/services/server_health_service.dart';
import 'package:cardforge/features/auth/auth_service.dart';
import 'package:cardforge/features/cards/card_repository.dart';
import 'package:cardforge/features/cards/collection_export.dart';
import 'package:cardforge/features/cards/mock_card_repository.dart';
import 'package:cardforge/features/machine/api/machine_api.dart';
import 'package:cardforge/features/machine/machine_link_service.dart';
import 'package:cardforge/features/machine/mock_machine_link_service.dart';
import 'package:cardforge/features/runs/mock_run_repository.dart';
import 'package:cardforge/features/runs/run_repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'fake_machine_api.dart';

/// Call in `setUpAll`. Stops google_fonts from downloading fonts during tests
/// (without the font, Flutter uses a fallback and the test runs normally).
void configureTestEnvironment() {
  GoogleFonts.config.allowRuntimeFetching = false;
}

/// Clears the session and the simulated secure storage before each test.
/// Use [stored] to start with saved values
/// (e.g. `{'access_token': 'abc'}` simulates a user who is already signed in).
Future<void> resetAuthState({Map<String, String> stored = const {}}) async {
  FlutterSecureStorage.setMockInitialValues(<String, String>{});
  await AuthService.instance.signOut();
  FlutterSecureStorage.setMockInitialValues(Map<String, String>.of(stored));
}

/// Saved preferences of a linked machine that answers at 192.168.0.42:8000.
const Map<String, Object> linkedMachinePrefs = {
  'machine_id': 'B8:27:EB:00:A1:B2',
  'machine_name': 'CardForge-A1B2',
  'machine_host': '192.168.0.42',
  'machine_port': 8000,
};

/// Opens the whole app (theme, routes, language, alerts) on a 390x844 screen
/// and returns the app dependencies so tests can inspect them.
///
/// - [preferences]: starting SharedPreferences, e.g. `{'language_code': 'pt'}`
///   or [linkedMachinePrefs].
/// - [linkService]: the Bluetooth service (a fast simulated one by default).
/// - [cardRepository]: where the cards come from (a fast simulated one by default).
/// - [machineApi]: the machine's REST API ([FakeMachineApi] by default).
/// - [runRepository] / [collectionExport]: run history and CSV export rows
///   (fast simulated ones by default). [shareCsv] replaces the share sheet
///   (by default it does nothing).
Future<AppDependencies> pumpApp(
  WidgetTester tester, {
  String initialLocation = '/splash',
  ServerHealthService? healthService,
  MachineLinkService? linkService,
  CardRepository? cardRepository,
  MachineApi? machineApi,
  RunRepository? runRepository,
  CollectionExportSource? collectionExport,
  CsvSharer? shareCsv,
  Map<String, Object> preferences = const {},
}) async {
  tester.view.physicalSize = const Size(1170, 2532);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.reset);

  SharedPreferences.setMockInitialValues(Map<String, Object>.of(preferences));
  final prefs = await SharedPreferences.getInstance();
  final api = machineApi ?? FakeMachineApi();
  final deps = AppDependencies.create(
    prefs,
    linkService: linkService ?? MockMachineLinkService(stepDelay: Duration.zero),
    cardRepository:
        cardRepository ?? MockCardRepository(latency: Duration.zero),
    runRepository: runRepository ?? MockRunRepository(latency: Duration.zero),
    collectionExport: collectionExport ??
        MockCollectionExportSource(latency: Duration.zero),
    shareCsv: shareCsv ?? (_, __, ___) async {},
    machineApiFor: (_) => api,
  );

  await tester.pumpWidget(
    CardForgeApp(
      deps: deps,
      initialLocation: initialLocation,
      healthService: healthService,
    ),
  );
  await tester.pump();
  return deps;
}
