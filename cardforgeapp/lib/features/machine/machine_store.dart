import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'machine_models.dart';

/// Remembers which machine is linked on this phone, and where its API is.
class MachineStore extends ChangeNotifier {
  MachineStore(this._prefs) {
    final id = _prefs.getString(_idKey);
    final name = _prefs.getString(_nameKey);
    if (id != null && name != null) {
      final host = _prefs.getString(_hostKey);
      _machine = LinkedMachine(
        id: id,
        name: name,
        address: host == null
            ? null
            : MachineAddress(
                host: host,
                port: _prefs.getInt(_portKey) ?? MachineAddress.defaultPort,
              ),
      );
    }
  }

  static const String _idKey = 'machine_id';
  static const String _nameKey = 'machine_name';
  static const String _hostKey = 'machine_host';
  static const String _portKey = 'machine_port';

  final SharedPreferences _prefs;
  LinkedMachine? _machine;

  LinkedMachine? get machine => _machine;

  Future<void> save(LinkedMachine machine) async {
    _machine = machine;
    notifyListeners();
    await _prefs.setString(_idKey, machine.id);
    await _prefs.setString(_nameKey, machine.name);
    final address = machine.address;
    if (address == null) {
      await _prefs.remove(_hostKey);
      await _prefs.remove(_portKey);
    } else {
      await _prefs.setString(_hostKey, address.host);
      await _prefs.setInt(_portKey, address.port);
    }
  }

  /// The machine got a new IP address (e.g. after a router restart).
  Future<void> updateAddress(MachineAddress address) async {
    final machine = _machine;
    if (machine == null || machine.address == address) return;
    await save(machine.withAddress(address));
  }

  Future<void> clear() async {
    _machine = null;
    notifyListeners();
    await _prefs.remove(_idKey);
    await _prefs.remove(_nameKey);
    await _prefs.remove(_hostKey);
    await _prefs.remove(_portKey);
  }
}
