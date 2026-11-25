/// Why linking a machine (or searching for one) failed.
enum MachineLinkFailure {
  bluetoothUnavailable,
  bluetoothPermission,
  machineNotFound,
  incompatibleMachine,
  wrongPassword,
  tooManyAttempts,
  connectionLost,
  wifiNotFound,
  wifiWrongPassword,
  wifiFailed,
  noNetwork,
  machineApiDown,
  apiUnreachable,
  timeout,
  unknown,
}

class MachineLinkException implements Exception {
  const MachineLinkException(this.failure);

  final MachineLinkFailure failure;

  @override
  String toString() => 'MachineLinkException($failure)';
}

/// Progress of the link, as shown on the "Linking your machine" step.
enum LinkStep { sendingWifi, waitingForWifi, checkingConnection, done }

/// One event of [MachineLinkService.link]. The last one is [LinkStep.done]
/// and carries the address where the machine's REST API answers.
class LinkProgress {
  const LinkProgress(this.step, {this.address});

  final LinkStep step;
  final MachineAddress? address;
}

/// Where the machine's REST API (sorter_api on the Raspberry Pi) answers.
class MachineAddress {
  const MachineAddress({required this.host, this.port = defaultPort});

  static const int defaultPort = 8000;

  final String host;
  final int port;

  /// `http://<host>:<port>`. The API is plain HTTP on the local network.
  Uri get baseUri => Uri(scheme: 'http', host: host, port: port);

  String get label => '$host:$port';

  @override
  bool operator ==(Object other) =>
      other is MachineAddress && other.host == host && other.port == port;

  @override
  int get hashCode => Object.hash(host, port);

  @override
  String toString() => 'MachineAddress($label)';
}

/// A CardForge machine found by the Bluetooth scan.
class DiscoveredMachine {
  const DiscoveredMachine({
    required this.id,
    required this.name,
    required this.signalDbm,
  });

  /// Bluetooth id: the MAC address on Android, a per-phone UUID on iOS.
  final String id;
  final String name;
  final int signalDbm;

  /// 3 = strong, 1 = weak.
  int get signalLevel => signalDbm >= -60
      ? 3
      : signalDbm >= -75
          ? 2
          : 1;
}

class WifiCredentials {
  const WifiCredentials({required this.ssid, required this.password});

  final String ssid;

  /// Empty for open networks.
  final String password;

  /// What the machine accepts: empty (open network), 8 to 63 characters
  /// (WPA passphrase) or 64 hexadecimal digits.
  static bool isValidPassword(String password) {
    if (password.isEmpty) return true;
    if (password.length == 64) {
      return RegExp(r'^[0-9a-fA-F]{64}$').hasMatch(password);
    }
    return password.length >= 8 && password.length <= 63;
  }

  @override
  String toString() =>
      'WifiCredentials(ssid: $ssid)'; // never print the password
}

class LinkedMachine {
  const LinkedMachine({required this.id, required this.name, this.address});

  /// Bluetooth id, used to find the machine again over Bluetooth.
  final String id;
  final String name;

  /// Null for machines linked by older versions of the app.
  final MachineAddress? address;

  LinkedMachine withAddress(MachineAddress address) =>
      LinkedMachine(id: id, name: name, address: address);
}
