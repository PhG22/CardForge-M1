import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../../core/l10n/app_strings.dart';
import 'alert_watcher.dart';
import 'server_alert.dart';

/// Keys of what the app saves for the background service before starting it
/// (`FlutterForegroundTask.saveData`). The service runs in its own isolate:
/// this is how it gets the session and the run to watch.
abstract final class AlertTaskData {
  static const token = 'alerts.token';
  static const runId = 'alerts.run_id';
  static const lastId = 'alerts.last_id';
  static const language = 'alerts.language';
  static const machineName = 'alerts.machine_name';

  /// Message from the app to the service: `{appConnected: true}` while the app
  /// is open and connected to the machine (it shows the alerts itself then).
  static const appConnected = 'appConnected';
}

/// Android notification channel of the alerts (high importance: heads-up,
/// sound, shown on the lock screen).
const String alertChannelId = 'cardforge_alerts';

/// Entry point of the background service (a separate isolate started by
/// flutter_foreground_task). Must stay a top-level function.
@pragma('vm:entry-point')
void startAlertTask() {
  FlutterForegroundTask.setTaskHandler(AlertTaskHandler());
}

/// RF38: while a run is active, waits for its alerts on the CardForge server
/// and shows each one as a notification, also when the app is in the
/// background or the phone is locked. Stops the service when the run ends.
class AlertTaskHandler extends TaskHandler {
  final FlutterLocalNotificationsPlugin _notifications =
      FlutterLocalNotificationsPlugin();
  AlertWatcher? _watcher;
  bool _appConnected = false;

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    final token =
        await FlutterForegroundTask.getData<String>(key: AlertTaskData.token);
    final runId =
        await FlutterForegroundTask.getData<String>(key: AlertTaskData.runId);
    if (token == null || runId == null) {
      await FlutterForegroundTask.stopService();
      return;
    }
    final language = await FlutterForegroundTask.getData<String>(
            key: AlertTaskData.language) ??
        'en';
    final machineName = await FlutterForegroundTask.getData<String>(
        key: AlertTaskData.machineName);
    final lastId =
        await FlutterForegroundTask.getData<int>(key: AlertTaskData.lastId) ??
            0;
    final strings = AppStrings.forLocale(Locale(language));

    await _notifications.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ),
    );

    final watcher = AlertWatcher(
      feed: HttpAlertFeed(token: token),
      runId: runId,
      startAfter: lastId,
      show: (alert) => _show(alert, strings, machineName),
    );
    _watcher = watcher;
    unawaited(_watch(watcher));
  }

  Future<void> _watch(AlertWatcher watcher) async {
    final end = await watcher.run();
    if (end != WatchEnd.stopped) {
      await FlutterForegroundTask.removeData(key: AlertTaskData.token);
      await FlutterForegroundTask.stopService();
    }
  }

  Future<void> _show(
      ServerAlert alert, AppStrings strings, String? machineName) async {
    await FlutterForegroundTask.saveData(
        key: AlertTaskData.lastId, value: alert.id);
    final machineAlert = alert.toMachineAlert(machineName: machineName);
    if (machineAlert == null) return; // e.g. the user stopped the run
    // The app is on screen and connected: it already shows this alert (RF27).
    if (_appConnected && await FlutterForegroundTask.isAppOnForeground) return;
    final body = strings.alertMessage(machineAlert);
    await _notifications.show(
      id: alert.id,
      title: strings.alertTitle(machineAlert.type),
      body: body,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          alertChannelId,
          strings.alertsChannelName,
          channelDescription: strings.alertsChannelDescription,
          importance: Importance.high,
          priority: Priority.high,
          // RF38: the time it happened on the machine, not when it arrived.
          when: alert.time.millisecondsSinceEpoch,
          showWhen: true,
          category: AndroidNotificationCategory.error,
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
    );
  }

  @override
  void onRepeatEvent(DateTime timestamp) {}

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    _watcher?.stop();
  }

  @override
  void onReceiveData(Object data) {
    if (data is Map && data[AlertTaskData.appConnected] is bool) {
      _appConnected = data[AlertTaskData.appConnected] as bool;
    }
  }

  @override
  void onNotificationPressed() {
    FlutterForegroundTask.launchApp();
  }
}
