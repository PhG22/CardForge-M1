import 'dart:async';

import 'package:flutter/foundation.dart';

import 'machine_alert.dart';

/// Keeps the list of alerts and announces each new one.
///
/// Today alerts are created inside the app (and by the debug simulator). Later,
/// the server connection (push, WebSocket or polling) just calls [add].
class AlertsController extends ChangeNotifier {
  AlertsController({DateTime Function()? clock})
      : _clock = clock ?? DateTime.now;

  final DateTime Function() _clock;
  final List<MachineAlert> _alerts = [];
  final StreamController<MachineAlert> _incoming =
      StreamController<MachineAlert>.broadcast();
  int _nextId = 1;

  /// Newest first.
  List<MachineAlert> get alerts => List.unmodifiable(_alerts);

  int get unreadCount => _alerts.where((a) => !a.read).length;

  /// Emits every alert as it arrives (the app shows it as a banner).
  Stream<MachineAlert> get onNewAlert => _incoming.stream;

  MachineAlert add(AlertType type, {String? machineName, int? cardCount}) {
    final alert = MachineAlert(
      id: _nextId++,
      type: type,
      createdAt: _clock(),
      machineName: machineName,
      cardCount: cardCount,
    );
    _alerts.insert(0, alert);
    notifyListeners();
    _incoming.add(alert);
    return alert;
  }

  void markRead(int id) {
    final index = _alerts.indexWhere((a) => a.id == id);
    if (index < 0 || _alerts[index].read) return;
    _alerts[index] = _alerts[index].markRead();
    notifyListeners();
  }

  void markAllRead() {
    if (unreadCount == 0) return;
    for (var i = 0; i < _alerts.length; i++) {
      _alerts[i] = _alerts[i].markRead();
    }
    notifyListeners();
  }

  void clear() {
    if (_alerts.isEmpty) return;
    _alerts.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    _incoming.close();
    super.dispose();
  }
}
