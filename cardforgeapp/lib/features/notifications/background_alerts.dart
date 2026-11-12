import 'dart:io' show Platform;

import 'package:flutter_foreground_task/flutter_foreground_task.dart';

import '../../core/l10n/app_strings.dart';
import 'alert_task.dart';

/// RF38 from the app's side: while a run is active, a background service
/// waits for the run's alerts on the CardForge server and shows them as
/// notifications, also when the app is in the background or the phone is
/// locked. The machine sends each alert to the server when it happens
/// (a fault that paused the run, the end of the run).
abstract class BackgroundAlerts {
  /// A run is running or paused: make sure its alerts reach the phone.
  /// Called on every update of the run; does nothing when already watching it.
  Future<void> watchRun(String runId, {String? machineName});

  /// The machine has no active run (it ended, or the app reconnected to an
  /// idle machine). While the app is on screen the service is stopped; in the
  /// background it stops by itself after showing the end of the run.
  Future<void> noActiveRun();

  /// The app is connected to the machine. While it is also on screen it shows
  /// the alerts itself (RF27), so the service does not repeat them.
  void setAppConnected(bool connected);
}

/// No background alerts (simulated backend, tests, platforms without the
/// service).
class NoBackgroundAlerts implements BackgroundAlerts {
  const NoBackgroundAlerts();

  @override
  Future<void> watchRun(String runId, {String? machineName}) async {}

  @override
  Future<void> noActiveRun() async {}

  @override
  void setAppConnected(bool connected) {}
}

/// Android foreground service (flutter_foreground_task) running
/// [AlertTaskHandler]. The service shows a small "watching your run"
/// notification while it runs; Android keeps it alive with the screen off.
class ForegroundServiceAlerts implements BackgroundAlerts {
  ForegroundServiceAlerts({
    required Future<String?> Function() token,
    required AppStrings Function() strings,
    required String Function() languageCode,
  })  : _token = token,
        _strings = strings,
        _languageCode = languageCode;

  static const int _serviceId = 4238;
  static const String _askedBatteryKey = 'alerts.asked_battery';

  final Future<String?> Function() _token;
  final AppStrings Function() _strings;
  final String Function() _languageCode;

  Future<void> _queue = Future<void>.value();
  bool _initialized = false;
  bool _askedNotifications = false;
  bool _connected = false;
  String? _watching;

  /// One command at a time: watchRun is called on every run update.
  Future<void> _serial(Future<void> Function() action) {
    final next = _queue.then((_) => action()).catchError((Object _) {});
    _queue = next;
    return next;
  }

  @override
  Future<void> watchRun(String runId, {String? machineName}) {
    if (!Platform.isAndroid) return Future<void>.value();
    return _serial(() => _watch(runId, machineName));
  }

  Future<void> _watch(String runId, String? machineName) async {
    if (_watching == runId && await FlutterForegroundTask.isRunningService) {
      return;
    }
    final token = await _token();
    if (token == null) return; // signed out: the server would refuse anyway
    _init();
    await _askPermissions();

    final savedRun =
        await FlutterForegroundTask.getData<String>(key: AlertTaskData.runId);
    if (savedRun != runId) {
      await FlutterForegroundTask.saveData(key: AlertTaskData.lastId, value: 0);
    }
    await FlutterForegroundTask.saveData(key: AlertTaskData.token, value: token);
    await FlutterForegroundTask.saveData(key: AlertTaskData.runId, value: runId);
    await FlutterForegroundTask.saveData(
        key: AlertTaskData.language, value: _languageCode());
    if (machineName != null) {
      await FlutterForegroundTask.saveData(
          key: AlertTaskData.machineName, value: machineName);
    }

    final strings = _strings();
    final ServiceRequestResult result;
    if (await FlutterForegroundTask.isRunningService) {
      result = await FlutterForegroundTask.restartService(); // reads the new run
    } else {
      result = await FlutterForegroundTask.startService(
        serviceId: _serviceId,
        serviceTypes: [ForegroundServiceTypes.remoteMessaging],
        notificationTitle: strings.watchingRunTitle,
        notificationText: strings.watchingRunText(machineName),
        callback: startAlertTask,
      );
    }
    if (result is ServiceRequestSuccess) {
      _watching = runId;
      _sendConnected();
    }
  }

  @override
  Future<void> noActiveRun() {
    if (!Platform.isAndroid) return Future<void>.value();
    return _serial(() async {
      if (!await FlutterForegroundTask.isRunningService) {
        _watching = null;
        return;
      }
      // In the background the service still has to show the end of the run.
      if (!await FlutterForegroundTask.isAppOnForeground) return;
      _watching = null;
      await FlutterForegroundTask.stopService();
      await FlutterForegroundTask.removeData(key: AlertTaskData.token);
    });
  }

  @override
  void setAppConnected(bool connected) {
    _connected = connected;
    if (Platform.isAndroid && _watching != null) _sendConnected();
  }

  void _sendConnected() {
    try {
      FlutterForegroundTask.sendDataToTask(
          {AlertTaskData.appConnected: _connected});
    } on Object {
      // the service is not running: nothing to tell
    }
  }

  void _init() {
    if (_initialized) return;
    _initialized = true;
    final strings = _strings();
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'cardforge_watch',
        channelName: strings.watchChannelName,
        channelDescription: strings.watchChannelDescription,
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.nothing(),
        allowWakeLock: true,
        allowWifiLock: true,
        stopWithTask: false, // keep watching after the app is swiped away
      ),
    );
  }

  /// Android 13+: notifications need the user's permission. The battery
  /// optimisation exemption keeps the service's network access with the
  /// screen off (Doze); asked once.
  Future<void> _askPermissions() async {
    if (!_askedNotifications) {
      _askedNotifications = true;
      final permission =
          await FlutterForegroundTask.checkNotificationPermission();
      if (permission != NotificationPermission.granted) {
        await FlutterForegroundTask.requestNotificationPermission();
      }
    }
    final asked =
        await FlutterForegroundTask.getData<bool>(key: _askedBatteryKey) ??
            false;
    if (!asked && !await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
      await FlutterForegroundTask.saveData(key: _askedBatteryKey, value: true);
      await FlutterForegroundTask.requestIgnoreBatteryOptimization();
    }
  }
}
