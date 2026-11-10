import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../features/alerts/alerts_controller.dart';
import '../features/cards/card_repository.dart';
import '../features/cards/mock_card_repository.dart';
import '../features/machine/api/machine_api.dart';
import '../features/machine/api/mock_machine_api.dart';
import '../features/machine/ble/ble_machine_link_service.dart';
import '../features/machine/machine_controller.dart';
import '../features/machine/machine_link_service.dart';
import '../features/machine/machine_models.dart';
import '../features/machine/machine_store.dart';
import '../features/machine/mock_machine_link_service.dart';
import 'config/app_config.dart';
import 'l10n/locale_controller.dart';

/// Builds the client for the machine's REST API at an address.
typedef MachineApiFactory = MachineApi Function(MachineAddress address);

/// Objects shared by the whole app (language, alerts, linked machine,
/// Bluetooth setup, machine connection).
class AppDependencies {
  AppDependencies({
    required this.locale,
    required this.alerts,
    required this.machineStore,
    required this.linkService,
    required this.cardRepository,
    required this.machineApiFor,
    required this.machine,
    GlobalKey<ScaffoldMessengerState>? messengerKey,
  }) : messengerKey = messengerKey ?? GlobalKey<ScaffoldMessengerState>();

  factory AppDependencies.create(
    SharedPreferences prefs, {
    MachineLinkService? linkService,
    CardRepository? cardRepository,
    MachineApiFactory? machineApiFor,
    List<Duration>? machineRetryDelays,
  }) {
    final alerts = AlertsController();
    final machineStore = MachineStore(prefs);
    final link = linkService ??
        (AppConfig.useMockBluetooth
            ? MockMachineLinkService()
            : BleMachineLinkService());
    final MachineApiFactory apiFor = machineApiFor ?? _defaultMachineApi();
    return AppDependencies(
      locale: LocaleController(prefs),
      alerts: alerts,
      machineStore: machineStore,
      linkService: link,
      cardRepository: cardRepository ??
          (AppConfig.useMockBackend
              ? MockCardRepository()
              : ApiCardRepository()),
      machineApiFor: apiFor,
      machine: MachineController(
        store: machineStore,
        apiFor: apiFor,
        alerts: alerts,
        linkService: link,
        retryDelays: machineRetryDelays ??
            const [
              Duration(seconds: 2),
              Duration(seconds: 5),
              Duration(seconds: 10),
              Duration(seconds: 30),
            ],
      ),
    );
  }

  static MachineApiFactory _defaultMachineApi() {
    if (AppConfig.useMockMachine) {
      final simulated = MockMachineApi(); // one machine, whatever the address
      return (_) => simulated;
    }
    return HttpMachineApi.new;
  }

  final LocaleController locale;
  final AlertsController alerts;
  final MachineStore machineStore;
  final MachineLinkService linkService;
  final CardRepository cardRepository;
  final MachineApiFactory machineApiFor;

  /// Live connection to the linked machine (run state, commands).
  final MachineController machine;

  /// Lets the app show alert banners from anywhere, on top of any screen.
  final GlobalKey<ScaffoldMessengerState> messengerKey;
}

/// Gives every screen access to [AppDependencies]:
/// `AppScope.of(context).alerts`.
class AppScope extends InheritedWidget {
  const AppScope({super.key, required this.deps, required super.child});

  final AppDependencies deps;

  /// Does not register a dependency, so it is safe to call in `initState`.
  /// Widgets that need updates listen to the specific controller instead.
  static AppDependencies of(BuildContext context) {
    final scope = context.getInheritedWidgetOfExactType<AppScope>();
    assert(scope != null, 'AppScope not found above this widget.');
    return scope!.deps;
  }

  @override
  bool updateShouldNotify(AppScope oldWidget) => deps != oldWidget.deps;
}
