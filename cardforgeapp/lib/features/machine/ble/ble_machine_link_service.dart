import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import '../machine_link_service.dart';
import '../machine_models.dart';
import 'ble_setup_codec.dart';

/// Real Bluetooth Low Energy transport for the first setup, talking to the
/// `cardforge_setup` service on the Raspberry Pi (docs/ble-setup-protocol.md).
///
/// flutter_blue_plus is free for personal, educational and nonprofit use
/// ([License.nonprofit]); for-profit use needs a commercial license.
class BleMachineLinkService implements MachineLinkService {
  BleMachineLinkService({
    this.scanTimeout = const Duration(seconds: 8),
    this.pollInterval = const Duration(milliseconds: 1500),
  });

  final Duration scanTimeout;

  /// How often to ask the machine whether it joined the Wi-Fi network.
  final Duration pollInterval;

  static final Guid _serviceGuid = Guid(BleSetupProtocol.serviceUuid);
  static final Guid _rxGuid = Guid(BleSetupProtocol.rxUuid);
  static final Guid _txGuid = Guid(BleSetupProtocol.txUuid);

  _Connection? _connection;
  String? _session;

  // ------------------------------------------------------------------ scan

  @override
  Stream<List<DiscoveredMachine>> scan() {
    StreamSubscription<List<ScanResult>>? results;
    late final StreamController<List<DiscoveredMachine>> controller;
    controller = StreamController<List<DiscoveredMachine>>(
      onListen: () async {
        final found = <String, DiscoveredMachine>{};
        try {
          await _ensureBluetoothReady();
          results = FlutterBluePlus.onScanResults.listen((list) {
            for (final result in list.where(_isCardForge)) {
              final id = result.device.remoteId.str;
              found[id] = DiscoveredMachine(
                id: id,
                name: _displayName(result),
                signalDbm: result.rssi,
              );
            }
            if (!controller.isClosed) {
              controller.add(found.values.toList()
                ..sort((a, b) => b.signalDbm.compareTo(a.signalDbm)));
            }
          });
          // No hardware filter: macOS (and some BlueZ setups) leave the
          // service UUID out of the advertisement when the name fills it, so
          // machines are picked by UUID *or* name in [_isCardForge].
          await FlutterBluePlus.startScan(timeout: scanTimeout);
          await FlutterBluePlus.isScanning.where((scanning) => !scanning).first;
        } catch (error) {
          if (!controller.isClosed) {
            controller.addError(_toLinkException(error));
          }
        } finally {
          await results?.cancel();
          if (!controller.isClosed) unawaited(controller.close());
        }
      },
      onCancel: () async {
        await results?.cancel();
        if (FlutterBluePlus.isScanningNow) await FlutterBluePlus.stopScan();
      },
    );
    return controller.stream;
  }

  /// A CardForge machine advertises our service UUID, or a name starting
  /// with "CardForge" (when the UUID did not fit in the advertisement).
  /// Connecting checks the service anyway (else: incompatibleMachine).
  static bool _isCardForge(ScanResult result) {
    if (result.advertisementData.serviceUuids.contains(_serviceGuid)) {
      return true;
    }
    final name = result.advertisementData.advName.isNotEmpty
        ? result.advertisementData.advName
        : result.device.platformName;
    return name.toLowerCase().startsWith('cardforge');
  }

  static String _displayName(ScanResult result) {
    final advertised = result.advertisementData.advName;
    if (advertised.isNotEmpty) return advertised;
    final platform = result.device.platformName;
    if (platform.isNotEmpty) return platform;
    final id = result.device.remoteId.str.replaceAll(':', '');
    final suffix = id.length >= 4 ? id.substring(id.length - 4) : id;
    return 'CardForge-${suffix.toUpperCase()}';
  }

  // ------------------------------------------------------------ commands

  @override
  Future<void> authenticate({
    required String machineId,
    required String password,
  }) async {
    _session = null;
    final reply = await _call(
      machineId,
      'auth',
      args: {'password': password},
      timeout: const Duration(seconds: 25), // the Pi hashes slowly on purpose
    );
    if (reply.ok) {
      _session = reply.field<String>('session');
      return;
    }
    throw MachineLinkException(switch (reply.error) {
      'WRONG_PASSWORD' => MachineLinkFailure.wrongPassword,
      'LOCKED' => MachineLinkFailure.tooManyAttempts,
      _ => MachineLinkFailure.unknown,
    });
  }

  @override
  Stream<LinkProgress> link({
    required DiscoveredMachine machine,
    WifiCredentials? wifi,
  }) async* {
    if (wifi != null) {
      yield const LinkProgress(LinkStep.sendingWifi);
      final started = await _call(machine.id, 'wifi', args: {
        'session': _session,
        'ssid': wifi.ssid,
        'password': wifi.password,
      });
      if (!started.ok) {
        throw MachineLinkException(switch (started.error) {
          // The setup session lasts 10 minutes: start again.
          'NOT_AUTHENTICATED' => MachineLinkFailure.unknown,
          'BAD_ARG' => started.field<String>('field') == 'password'
              ? MachineLinkFailure.wifiWrongPassword
              : MachineLinkFailure.wifiNotFound,
          _ => MachineLinkFailure.wifiFailed,
        });
      }
      yield const LinkProgress(LinkStep.waitingForWifi);
      await _waitForWifi(machine.id);
    }
    yield const LinkProgress(LinkStep.checkingConnection);
    final status = await _call(machine.id, 'status');
    yield LinkProgress(LinkStep.done, address: _addressFrom(status));
  }

  @override
  Future<MachineAddress> findAddress({required String machineId}) async {
    try {
      await _waitUntilSeen(machineId);
      return _addressFrom(await _call(machineId, 'status'));
    } finally {
      await disconnect();
    }
  }

  @override
  Future<void> disconnect() async {
    final connection = _connection;
    _connection = null;
    await connection?.close();
  }

  /// Polls `status` until the Pi reports the Wi-Fi result.
  Future<void> _waitForWifi(String machineId) async {
    var reconnects = 0;
    while (true) {
      await Future<void>.delayed(pollInterval);
      final SetupReply status;
      try {
        status = await _call(machineId, 'status');
      } on MachineLinkException catch (e) {
        // The Pi 3 has one radio chip for Wi-Fi and Bluetooth: the Bluetooth
        // link can drop while it changes network. Reconnect and keep asking.
        if (e.failure == MachineLinkFailure.connectionLost && reconnects < 3) {
          reconnects++;
          await disconnect();
          continue;
        }
        rethrow;
      }
      final wifi = status.object('wifi');
      switch (wifi?['state']) {
        case 'connected':
          return;
        case 'failed':
          throw MachineLinkException(switch (wifi?['error']) {
            'NETWORK_NOT_FOUND' => MachineLinkFailure.wifiNotFound,
            'WRONG_PASSWORD' => MachineLinkFailure.wifiWrongPassword,
            _ => MachineLinkFailure.wifiFailed,
          });
      }
    }
  }

  MachineAddress _addressFrom(SetupReply status) {
    if (!status.ok) {
      throw const MachineLinkException(MachineLinkFailure.unknown);
    }
    final ip = status.field<String>('ip');
    if (ip == null || ip.isEmpty) {
      throw const MachineLinkException(MachineLinkFailure.noNetwork);
    }
    if (status.field<bool>('api_ok') == false) {
      throw const MachineLinkException(MachineLinkFailure.machineApiDown);
    }
    return MachineAddress(
      host: ip,
      port: status.field<int>('api_port') ?? MachineAddress.defaultPort,
    );
  }

  /// One request, connecting first if needed. Errors become
  /// [MachineLinkException]s.
  Future<SetupReply> _call(
    String machineId,
    String cmd, {
    Map<String, Object?> args = const {},
    Duration? timeout,
  }) async {
    try {
      final rpc = await _rpcFor(machineId);
      return await rpc.call(cmd, args: args, timeout: timeout);
    } catch (error) {
      throw _toLinkException(error);
    }
  }

  // ---------------------------------------------------------- connection

  Future<SetupRpc> _rpcFor(String machineId) async {
    final current = _connection;
    if (current != null &&
        current.device.remoteId.str == machineId &&
        current.device.isConnected &&
        !current.rpc.isClosed) {
      return current.rpc;
    }
    await disconnect();
    await _ensureBluetoothReady();

    final device = BluetoothDevice.fromId(machineId);
    final subscriptions = <StreamSubscription<Object?>>[];
    try {
      // On Android this also asks for a 512-byte MTU; iOS negotiates its own.
      await device.connect(
        license: License.nonprofit,
        timeout: const Duration(seconds: 15),
      );
      final services = await device.discoverServices();
      final service = services.where((s) => s.uuid == _serviceGuid).firstOrNull;
      final rx =
          service?.characteristics.where((c) => c.uuid == _rxGuid).firstOrNull;
      final tx =
          service?.characteristics.where((c) => c.uuid == _txGuid).firstOrNull;
      if (rx == null || tx == null) {
        throw const MachineLinkException(
            MachineLinkFailure.incompatibleMachine);
      }

      final rpc = SetupRpc(
        chunkSize: BleSetupProtocol.chunkSizeFor(device.mtuNow),
        writeChunk: (chunk) => rx.write(chunk),
      );
      subscriptions
        ..add(tx.onValueReceived.listen(rpc.addIncoming))
        ..add(device.connectionState.listen((state) {
          if (state == BluetoothConnectionState.disconnected) rpc.close();
        }));
      await tx.setNotifyValue(true);
      _connection = _Connection(device, rpc, subscriptions);

      final info = await rpc.call('info');
      if (!info.ok || info.field<int>('protocol') != BleSetupProtocol.version) {
        throw const MachineLinkException(
            MachineLinkFailure.incompatibleMachine);
      }
      return rpc;
    } catch (_) {
      if (_connection?.device == device) {
        await disconnect();
      } else {
        for (final sub in subscriptions) {
          await sub.cancel();
        }
        await _quietDisconnect(device);
      }
      rethrow;
    }
  }

  /// Short scan so the phone sees the machine before connecting by id: iOS
  /// only connects to devices it has seen, and it tells us if it's nearby.
  Future<void> _waitUntilSeen(String machineId) async {
    await _ensureBluetoothReady();
    final seen = Completer<void>();
    final sub = FlutterBluePlus.onScanResults.listen((results) {
      if (!seen.isCompleted &&
          results.any((r) => r.device.remoteId.str == machineId)) {
        seen.complete();
      }
    });
    try {
      await FlutterBluePlus.startScan(timeout: scanTimeout);
      final scanEnded =
          FlutterBluePlus.isScanning.where((scanning) => !scanning).first;
      await Future.any([seen.future, scanEnded]);
      if (!seen.isCompleted) {
        throw const MachineLinkException(MachineLinkFailure.machineNotFound);
      }
    } catch (error) {
      throw _toLinkException(error);
    } finally {
      await sub.cancel();
      if (FlutterBluePlus.isScanningNow) await FlutterBluePlus.stopScan();
    }
  }

  Future<void> _ensureBluetoothReady() async {
    if (!await FlutterBluePlus.isSupported) {
      throw const MachineLinkException(MachineLinkFailure.bluetoothUnavailable);
    }
    // iOS starts in `unknown` for a moment.
    var state = await FlutterBluePlus.adapterState
        .where((s) => s != BluetoothAdapterState.unknown)
        .first
        .timeout(
          const Duration(seconds: 3),
          onTimeout: () => FlutterBluePlus.adapterStateNow,
        );
    if (state == BluetoothAdapterState.off &&
        defaultTargetPlatform == TargetPlatform.android) {
      try {
        await FlutterBluePlus.turnOn(); // Android shows "Allow Bluetooth?"
        state = BluetoothAdapterState.on;
      } on Exception {
        // the user said no: reported below
      }
    }
    if (state == BluetoothAdapterState.unauthorized) {
      throw const MachineLinkException(MachineLinkFailure.bluetoothPermission);
    }
    if (state != BluetoothAdapterState.on) {
      throw const MachineLinkException(MachineLinkFailure.bluetoothUnavailable);
    }
  }

  static MachineLinkException _toLinkException(Object error) {
    if (error is MachineLinkException) return error;
    if (error is SetupRpcException) {
      return MachineLinkException(error.reason == SetupRpcFailure.timeout
          ? MachineLinkFailure.timeout
          : MachineLinkFailure.connectionLost);
    }
    if (error is FlutterBluePlusException) {
      final text = '${error.function} ${error.description}'.toLowerCase();
      if (text.contains('permission')) {
        return const MachineLinkException(
            MachineLinkFailure.bluetoothPermission);
      }
      if (text.contains('adapter') ||
          text.contains('bluetooth must be turned on')) {
        return const MachineLinkException(
            MachineLinkFailure.bluetoothUnavailable);
      }
      if (text.contains('timeout') || text.contains('timed out')) {
        return const MachineLinkException(MachineLinkFailure.machineNotFound);
      }
      return const MachineLinkException(MachineLinkFailure.connectionLost);
    }
    if (error is TimeoutException) {
      return const MachineLinkException(MachineLinkFailure.timeout);
    }
    debugPrint('Unexpected Bluetooth error: $error');
    return const MachineLinkException(MachineLinkFailure.unknown);
  }

  static Future<void> _quietDisconnect(BluetoothDevice device) async {
    try {
      await device.disconnect();
    } catch (_) {
      // already gone
    }
  }
}

class _Connection {
  _Connection(this.device, this.rpc, this._subscriptions);

  final BluetoothDevice device;
  final SetupRpc rpc;
  final List<StreamSubscription<Object?>> _subscriptions;

  Future<void> close() async {
    rpc.close();
    for (final sub in _subscriptions) {
      await sub.cancel();
    }
    await BleMachineLinkService._quietDisconnect(device);
  }
}
