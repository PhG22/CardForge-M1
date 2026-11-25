import '../../core/config/app_config.dart';
import 'machine_link_service.dart';
import 'machine_models.dart';

/// Simulated machines, so the whole linking flow works in the emulator
/// (which has no Bluetooth). The same rules as the Pi's `--simulate-wifi`:
///
/// - machine password `0000` is rejected;
/// - a Wi-Fi network named `fail` is not found;
/// - the Wi-Fi password `wrongpass` is rejected.
///
/// The "linked" machine answers at [address]: by default `MOCK_MACHINE_HOST`
/// (10.0.2.2 = the computer running the Android emulator), so you can run
/// `python -m sorter_api --simulate` on your computer and control it for real
/// with `USE_MOCK_MACHINE=false`.
class MockMachineLinkService implements MachineLinkService {
  MockMachineLinkService({
    this.stepDelay = const Duration(milliseconds: 900),
    MachineAddress? address,
  }) : address = address ??
            const MachineAddress(
              host: AppConfig.mockMachineHost,
              port: AppConfig.mockMachinePort,
            );

  /// Pause between simulated events. Tests use [Duration.zero].
  final Duration stepDelay;

  final MachineAddress address;

  static const String rejectedPassword = '0000';
  static const String failingNetwork = 'fail';
  static const String rejectedWifiPassword = 'wrongpass';

  static const List<DiscoveredMachine> _machines = [
    DiscoveredMachine(
      id: 'B8:27:EB:00:A1:B2',
      name: 'CardForge-A1B2',
      signalDbm: -48,
    ),
    DiscoveredMachine(
      id: 'B8:27:EB:00:9F:3C',
      name: 'CardForge-9F3C',
      signalDbm: -72,
    ),
  ];

  Future<void> _wait([int times = 1]) =>
      Future<void>.delayed(stepDelay * times);

  @override
  Stream<List<DiscoveredMachine>> scan() async* {
    final found = <DiscoveredMachine>[];
    for (final machine in _machines) {
      await _wait();
      found.add(machine);
      yield List<DiscoveredMachine>.of(found);
    }
    await _wait();
  }

  @override
  Future<void> authenticate({
    required String machineId,
    required String password,
  }) async {
    await _wait();
    if (password == rejectedPassword) {
      throw const MachineLinkException(MachineLinkFailure.wrongPassword);
    }
  }

  @override
  Stream<LinkProgress> link({
    required DiscoveredMachine machine,
    WifiCredentials? wifi,
  }) async* {
    if (wifi != null) {
      yield const LinkProgress(LinkStep.sendingWifi);
      await _wait();
      yield const LinkProgress(LinkStep.waitingForWifi);
      await _wait(3); // the machine takes a while to join the network
      if (wifi.ssid.toLowerCase() == failingNetwork) {
        throw const MachineLinkException(MachineLinkFailure.wifiNotFound);
      }
      if (wifi.password == rejectedWifiPassword) {
        throw const MachineLinkException(MachineLinkFailure.wifiWrongPassword);
      }
    }
    yield const LinkProgress(LinkStep.checkingConnection);
    await _wait();
    yield LinkProgress(LinkStep.done, address: address);
  }

  @override
  Future<MachineAddress> findAddress({required String machineId}) async {
    await _wait(2);
    return address;
  }

  @override
  Future<void> disconnect() async {}
}
