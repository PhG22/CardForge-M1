import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../core/config/app_config.dart';
import '../machine_models.dart';
import 'machine_api_models.dart';

/// The machine's REST API (sorter_api on the Raspberry Pi), reached over
/// Wi-Fi once the machine is linked. See raspberry-api/rest-api.md.
///
/// Every method throws [MachineApiException] on failure.
abstract class MachineApi {
  /// GET /health
  Future<MachineHealth> health();

  /// GET /run/status
  Future<MachineSnapshot> runStatus();

  /// POST /run/start. Blocks while the base homes (up to ~40 s). A 409
  /// carries every [StartProblem] at once.
  Future<RunInfo> startRun();

  /// POST /run/stop (or `?immediate=true`, the emergency stop).
  Future<RunInfo?> stopRun({bool immediate = false});

  /// GET /hardware/status
  Future<HardwareStatus> hardwareStatus();

  /// POST /base/home
  Future<BaseMoveResult> homeBase();

  /// POST /base/goto/{tray}
  Future<BaseMoveResult> gotoTray(int tray);

  /// WS /ws/events. The first event is a [SnapshotEvent]. The stream ends
  /// (or errors) when the connection drops; listen again to reconnect.
  Stream<MachineEvent> events();

  void close();
}

/// Whether [api] answers GET /health.
Future<bool> isMachineReachable(MachineApi api) async {
  try {
    return (await api.health()).ok;
  } on MachineApiException {
    return false;
  }
}

class HttpMachineApi implements MachineApi {
  HttpMachineApi(
    this.address, {
    http.Client? client,
    WebSocketChannel Function(Uri uri)? connectSocket,
  })  : _client = client ?? http.Client(),
        _connectSocket = connectSocket ?? WebSocketChannel.connect;

  final MachineAddress address;
  final http.Client _client;
  final WebSocketChannel Function(Uri uri) _connectSocket;

  Uri _uri(String path, [Map<String, String>? query]) =>
      address.baseUri.replace(path: path, queryParameters: query);

  @override
  Future<MachineHealth> health() async => MachineHealth.fromJson(
        await _request('GET', '/health', timeout: AppConfig.healthTimeout),
      );

  @override
  Future<MachineSnapshot> runStatus() async =>
      MachineSnapshot.fromJson(await _request('GET', '/run/status'));

  @override
  Future<RunInfo> startRun() async {
    final json = await _request(
      'POST',
      '/run/start',
      timeout: AppConfig.machineStartTimeout,
    );
    final run = json['run'];
    if (run is! Map) {
      throw const MachineApiException(MachineApiFailure.badResponse);
    }
    return RunInfo.fromJson(run.cast<String, Object?>());
  }

  @override
  Future<RunInfo?> stopRun({bool immediate = false}) async {
    final json = await _request(
      'POST',
      '/run/stop',
      query: immediate ? const {'immediate': 'true'} : null,
      timeout: AppConfig.machineMoveTimeout,
    );
    final run = json['run'];
    return run is Map ? RunInfo.fromJson(run.cast<String, Object?>()) : null;
  }

  @override
  Future<HardwareStatus> hardwareStatus() async =>
      HardwareStatus.fromJson(await _request('GET', '/hardware/status'));

  @override
  Future<BaseMoveResult> homeBase() async => BaseMoveResult.fromJson(
        await _request('POST', '/base/home',
            timeout: AppConfig.machineMoveTimeout),
      );

  @override
  Future<BaseMoveResult> gotoTray(int tray) async => BaseMoveResult.fromJson(
        await _request('POST', '/base/goto/$tray',
            timeout: AppConfig.machineMoveTimeout),
      );

  @override
  Stream<MachineEvent> events() {
    // A plain StreamController (not async*) so that cancelling the
    // subscription closes the socket right away instead of waiting for it.
    WebSocketChannel? channel;
    StreamSubscription<Object?>? socketSub;
    var cancelled = false;
    late final StreamController<MachineEvent> controller;

    void fail() {
      if (cancelled || controller.isClosed) return;
      controller
          .addError(const MachineApiException(MachineApiFailure.unreachable));
      unawaited(controller.close());
    }

    controller = StreamController<MachineEvent>(
      onListen: () async {
        final uri = address.baseUri.replace(scheme: 'ws', path: '/ws/events');
        try {
          final opened = channel = _connectSocket(uri);
          await opened.ready.timeout(AppConfig.healthTimeout);
        } catch (_) {
          unawaited(channel?.sink.close());
          fail();
          return;
        }
        if (cancelled) {
          unawaited(channel?.sink.close());
          return;
        }
        socketSub = channel!.stream.listen(
          (message) {
            if (message is! String) return;
            final Object? decoded;
            try {
              decoded = jsonDecode(message);
            } on FormatException {
              return;
            }
            if (decoded is Map && !controller.isClosed) {
              controller
                  .add(MachineEvent.fromJson(decoded.cast<String, Object?>()));
            }
          },
          onError: (Object _) => fail(),
          onDone: () {
            if (!controller.isClosed) unawaited(controller.close());
          },
          cancelOnError: true,
        );
      },
      onCancel: () {
        cancelled = true;
        unawaited(socketSub?.cancel());
        unawaited(channel?.sink.close());
      },
    );
    return controller.stream;
  }

  Future<Map<String, Object?>> _request(
    String method,
    String path, {
    Map<String, String>? query,
    Duration? timeout,
  }) async {
    final request = http.Request(method, _uri(path, query))
      ..headers['Accept'] = 'application/json';
    final http.Response res;
    try {
      res = await http.Response.fromStream(
        await _client.send(request).timeout(
              timeout ?? AppConfig.machineRequestTimeout,
            ),
      ).timeout(timeout ?? AppConfig.machineRequestTimeout);
    } on Exception {
      // timeout, no route to host, connection refused, ...
      throw const MachineApiException(MachineApiFailure.unreachable);
    }

    Object? body;
    try {
      body =
          res.bodyBytes.isEmpty ? null : jsonDecode(utf8.decode(res.bodyBytes));
    } on FormatException {
      body = null;
    }
    if (res.statusCode >= 200 && res.statusCode < 300) {
      if (body is Map) return body.cast<String, Object?>();
      throw const MachineApiException(MachineApiFailure.badResponse);
    }
    throw MachineApiException.fromResponse(res.statusCode, body);
  }

  @override
  void close() => _client.close();
}
