import 'dart:async';
import 'dart:convert';

// Pure Dart (no Flutter, no Bluetooth plugin), so it is easy to unit test.
// The protocol is described in docs/ble-setup-protocol.md.

/// UUIDs and limits of the BLE setup protocol. Keep them in sync with the Pi
/// (`pi_ble_setup/cardforge_setup/__init__.py`).
abstract final class BleSetupProtocol {
  static const int version = 1;
  static const String serviceUuid = 'cdaa0001-bc5d-45ab-8926-3aa83f2ab5c0';

  /// App -> Pi (write).
  static const String rxUuid = 'cdaa0002-bc5d-45ab-8926-3aa83f2ab5c0';

  /// Pi -> app (notify).
  static const String txUuid = 'cdaa0003-bc5d-45ab-8926-3aa83f2ab5c0';

  static const int maxMessageBytes = 1024;

  /// Fits in the minimum BLE MTU (23 - 3 bytes of header).
  static const int minChunk = 20;

  /// Upper bound for one write, even when the MTU is bigger.
  static const int maxChunk = 180;

  /// Chunk size for writes with a negotiated [mtu].
  static int chunkSizeFor(int mtu) {
    final size = mtu - 3;
    if (size < minChunk) return minChunk;
    if (size > maxChunk) return maxChunk;
    return size;
  }
}

/// Joins notification chunks into complete messages (one JSON per line).
class SetupFrameDecoder {
  final List<int> _buffer = [];
  bool _overflow = false;

  List<String> feed(List<int> chunk) {
    final messages = <String>[];
    for (final byte in chunk) {
      if (byte == 0x0A) {
        if (!_overflow && _buffer.isNotEmpty) {
          messages.add(utf8.decode(_buffer, allowMalformed: true));
        }
        _buffer.clear();
        _overflow = false;
      } else if (!_overflow) {
        _buffer.add(byte);
        if (_buffer.length > BleSetupProtocol.maxMessageBytes) {
          _buffer.clear();
          _overflow = true;
        }
      }
    }
    return messages;
  }

  void reset() {
    _buffer.clear();
    _overflow = false;
  }
}

/// Adds the final newline and splits [message] into chunks of [chunkSize].
List<List<int>> splitSetupMessage(List<int> message, int chunkSize) {
  if (chunkSize < 1) throw ArgumentError.value(chunkSize, 'chunkSize');
  final data = [...message, 0x0A];
  return [
    for (var start = 0; start < data.length; start += chunkSize)
      data.sublist(
        start,
        start + chunkSize > data.length ? data.length : start + chunkSize,
      ),
  ];
}

/// One reply from the machine: `{"id": 7, "ok": true, ...}`.
class SetupReply {
  const SetupReply(this.json);

  final Map<String, Object?> json;

  int? get id => json['id'] is int ? json['id'] as int : null;
  bool get ok => json['ok'] == true;

  /// Error code when [ok] is false, e.g. `WRONG_PASSWORD`.
  String? get error => json['error'] is String ? json['error'] as String : null;

  /// The field when it exists and has type [T], else null.
  T? field<T>(String key) {
    final value = json[key];
    return value is T ? value : null;
  }

  Map<String, Object?>? object(String key) {
    final value = json[key];
    return value is Map ? value.cast<String, Object?>() : null;
  }

  @override
  String toString() => 'SetupReply(id: $id, ok: $ok, error: $error)';
}

enum SetupRpcFailure { timeout, closed }

class SetupRpcException implements Exception {
  const SetupRpcException(this.reason);

  final SetupRpcFailure reason;

  @override
  String toString() => 'SetupRpcException($reason)';
}

/// Request/reply on top of the two characteristics: [call] writes one
/// request and waits for the reply with the same `id`. Requests go out one at
/// a time, as the protocol requires.
class SetupRpc {
  SetupRpc({
    required this.writeChunk,
    required this.chunkSize,
    this.timeout = const Duration(seconds: 10),
  });

  /// Writes one chunk to the RX characteristic.
  final Future<void> Function(List<int> chunk) writeChunk;
  final int chunkSize;
  final Duration timeout;

  final SetupFrameDecoder _decoder = SetupFrameDecoder();
  final Map<int, Completer<SetupReply>> _pending = {};
  Future<void> _queue = Future<void>.value();
  int _nextId = 1;
  bool _closed = false;

  bool get isClosed => _closed;

  /// Feed every TX notification here.
  void addIncoming(List<int> chunk) {
    for (final text in _decoder.feed(chunk)) {
      final Object? decoded;
      try {
        decoded = jsonDecode(text);
      } on FormatException {
        continue;
      }
      if (decoded is! Map) continue;
      final reply = SetupReply(decoded.cast<String, Object?>());
      final id = reply.id;
      Completer<SetupReply>? completer;
      if (id != null) {
        completer = _pending.remove(id);
      } else if (_pending.length == 1) {
        // BAD_JSON / TOO_LONG come back without an id: it can only be the
        // request in flight.
        completer = _pending.remove(_pending.keys.first);
      }
      if (completer != null && !completer.isCompleted) {
        completer.complete(reply);
      }
    }
  }

  Future<SetupReply> call(
    String cmd, {
    Map<String, Object?> args = const {},
    Duration? timeout,
  }) {
    final result =
        _queue.then((_) => _send(cmd, args, timeout ?? this.timeout));
    _queue = result.then<void>((_) {}, onError: (Object _) {});
    return result;
  }

  Future<SetupReply> _send(
    String cmd,
    Map<String, Object?> args,
    Duration timeout,
  ) async {
    if (_closed) throw const SetupRpcException(SetupRpcFailure.closed);
    final id = _nextId;
    _nextId = _nextId >= 65535 ? 1 : _nextId + 1;
    final completer = Completer<SetupReply>();
    // close() may fail it while a chunk is still being written, before
    // anyone listens: don't report that as an unhandled error.
    completer.future.ignore();
    _pending[id] = completer;
    try {
      final bytes = utf8.encode(jsonEncode(<String, Object?>{
        'id': id,
        'cmd': cmd,
        ...args,
      }));
      for (final chunk in splitSetupMessage(bytes, chunkSize)) {
        await writeChunk(chunk);
      }
      return await completer.future.timeout(
        timeout,
        onTimeout: () => throw const SetupRpcException(SetupRpcFailure.timeout),
      );
    } finally {
      _pending.remove(id);
    }
  }

  /// Fails every pending call (the device disconnected).
  void close() {
    if (_closed) return;
    _closed = true;
    for (final completer in _pending.values) {
      if (!completer.isCompleted) {
        completer
            .completeError(const SetupRpcException(SetupRpcFailure.closed));
      }
    }
    _pending.clear();
    _decoder.reset();
  }
}
