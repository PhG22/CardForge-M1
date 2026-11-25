import 'package:cardforge/features/alerts/alerts_controller.dart';
import 'package:cardforge/features/alerts/machine_alert.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late AlertsController controller;

  setUp(() => controller = AlertsController(clock: () => DateTime(2026, 1, 1)));
  tearDown(() => controller.dispose());

  test('add puts the newest alert first and counts it as unread', () {
    controller.add(AlertType.wifiConnected);
    controller.add(AlertType.batchFinished, cardCount: 50);

    expect(controller.alerts.map((a) => a.type),
        [AlertType.batchFinished, AlertType.wifiConnected]);
    expect(controller.unreadCount, 2);
    expect(controller.alerts.first.cardCount, 50);
  });

  test('onNewAlert emits each alert as it arrives', () async {
    final next = controller.onNewAlert.first;

    controller.add(AlertType.jamDetected, machineName: 'CardForge-A1B2');

    final alert = await next;
    expect(alert.type, AlertType.jamDetected);
    expect(alert.machineName, 'CardForge-A1B2');
  });

  test('notifies listeners when something changes', () {
    var notifications = 0;
    controller.addListener(() => notifications++);

    controller.add(AlertType.doubleFeed);
    controller.markAllRead();
    controller.clear();

    expect(notifications, 3);
  });

  test('markRead marks only that alert', () {
    final first = controller.add(AlertType.wifiConnected);
    controller.add(AlertType.connectionLost);

    controller.markRead(first.id);

    expect(controller.unreadCount, 1);
    expect(controller.alerts.last.read, isTrue);
    expect(controller.alerts.first.read, isFalse);
  });

  test('markAllRead and clear', () {
    controller.add(AlertType.wifiConnected);
    controller.add(AlertType.wifiDisconnected);

    controller.markAllRead();
    expect(controller.unreadCount, 0);
    expect(controller.alerts, hasLength(2));

    controller.clear();
    expect(controller.alerts, isEmpty);
  });

  test('every alert type has a severity', () {
    for (final type in AlertType.values) {
      expect(AlertSeverity.values, contains(type.severity));
    }
    expect(AlertType.jamDetected.severity, AlertSeverity.error);
    expect(AlertType.batchFinished.severity, AlertSeverity.success);
  });
}
