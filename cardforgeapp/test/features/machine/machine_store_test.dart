import 'package:cardforge/features/machine/machine_models.dart';
import 'package:cardforge/features/machine/machine_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<SharedPreferences> prefsWith(Map<String, Object> values) async {
    SharedPreferences.setMockInitialValues(values);
    return SharedPreferences.getInstance();
  }

  test('starts without a machine', () async {
    final store = MachineStore(await prefsWith({}));

    expect(store.machine, isNull);
  });

  test('save remembers the machine and notifies listeners', () async {
    final prefs = await prefsWith({});
    final store = MachineStore(prefs);
    var notified = 0;
    store.addListener(() => notified++);

    await store.save(const LinkedMachine(id: 'AA', name: 'CardForge-A1B2'));

    expect(store.machine?.name, 'CardForge-A1B2');
    expect(notified, 1);
    // A new store (a new app launch) reads it back.
    expect(MachineStore(prefs).machine?.id, 'AA');
  });

  test('remembers where the machine answers and updates it', () async {
    final prefs = await prefsWith({});
    final store = MachineStore(prefs);
    await store.save(const LinkedMachine(
      id: 'AA',
      name: 'CardForge-A1B2',
      address: MachineAddress(host: '192.168.0.42'),
    ));

    expect(MachineStore(prefs).machine?.address?.label, '192.168.0.42:8000');

    var notified = 0;
    store.addListener(() => notified++);
    await store.updateAddress(const MachineAddress(host: '192.168.0.42'));
    expect(notified, 0, reason: 'same address: nothing to do');

    await store.updateAddress(const MachineAddress(host: '10.0.0.7', port: 8080));
    expect(notified, 1);
    final reloaded = MachineStore(prefs).machine;
    expect(reloaded?.address, const MachineAddress(host: '10.0.0.7', port: 8080));
    expect(reloaded?.name, 'CardForge-A1B2');
  });

  test('a machine saved by an older version has no address', () async {
    final store = MachineStore(
        await prefsWith({'machine_id': 'AA', 'machine_name': 'X'}));

    expect(store.machine?.name, 'X');
    expect(store.machine?.address, isNull);
  });

  test('clear forgets the machine', () async {
    final prefs = await prefsWith({
      'machine_id': 'AA',
      'machine_name': 'X',
      'machine_host': '192.168.0.42',
      'machine_port': 8000,
    });
    final store = MachineStore(prefs);
    expect(store.machine, isNotNull);

    await store.clear();

    expect(store.machine, isNull);
    expect(MachineStore(prefs).machine, isNull);
    expect(prefs.getString('machine_host'), isNull);
  });
}
