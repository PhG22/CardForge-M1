import 'machine_models.dart';

/// Talks to the machine over Bluetooth, during the first setup only.
///
/// The UI only depends on this interface. The real transport is
/// `BleMachineLinkService` (ble/ble_machine_link_service.dart); the emulator
/// uses `MockMachineLinkService`.
abstract class MachineLinkService {
  /// Emits the growing list of machines found nearby and completes when the
  /// scan ends. Errors are [MachineLinkException]s.
  Stream<List<DiscoveredMachine>> scan();

  /// Connects to the machine and checks its password. Throws
  /// [MachineLinkException] (for example `wrongPassword`) on failure.
  Future<void> authenticate({
    required String machineId,
    required String password,
  });

  /// Sends the Wi-Fi details (when given), waits for the machine to join the
  /// network and asks for its address. Emits each [LinkStep] and finishes
  /// with [LinkStep.done], whose event carries the [MachineAddress].
  /// Errors are [MachineLinkException]s.
  Stream<LinkProgress> link({
    required DiscoveredMachine machine,
    WifiCredentials? wifi,
  });

  /// Connects to an already linked machine and asks for its current address
  /// (no password needed). Used when the saved IP address stops answering.
  Future<MachineAddress> findAddress({required String machineId});

  /// Ends the Bluetooth connection, if there is one.
  Future<void> disconnect();
}
