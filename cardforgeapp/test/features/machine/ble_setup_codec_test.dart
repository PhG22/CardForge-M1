import 'dart:async';
import 'dart:convert';

import 'package:cardforge/features/machine/ble/ble_setup_codec.dart';
import 'package:flutter_test/flutter_test.dart';

/// A fake Raspberry Pi on the other side of the two characteristics: it
/// joins the chunks the app writes and answers in 20-byte notifications, like
/// pi_ble_setup does.
class _FakePi {
  _FakePi(this.answer);

  final Map<String, Object?>? Function(Map<String, dynamic> request) answer;
  final SetupFrameDecoder _decoder = SetupFrameDecoder();
  final List<List<int>> written = [];
  late SetupRpc rpc;

  Future<void> write(List<int> chunk) async {
    written.add(chunk);
    for (final message in _decoder.feed(chunk)) {
      final request = jsonDecode(message) as Map<String, dynamic>;
      final reply = answer(request);
      if (reply == null) continue; // stay silent
      final bytes = utf8.encode(jsonEncode({'id': request['id'], ...reply}));
      scheduleMicrotask(() {
        for (final c in splitSetupMessage(bytes, BleSetupProtocol.minChunk)) {
          rpc.addIncoming(c);
        }
      });
    }
  }
}

void main() {
  group('framing', () {
    test('chunks are joined back into messages', () {
      final decoder = SetupFrameDecoder();
      final data = utf8.encode('{"id":1,"ok":true}\n{"id":2,"ok":false}\n');
      final messages = <String>[];
      for (var i = 0; i < data.length; i += 7) {
        messages.addAll(decoder
            .feed(data.sublist(i, i + 7 > data.length ? data.length : i + 7)));
      }
      expect(messages, ['{"id":1,"ok":true}', '{"id":2,"ok":false}']);
    });

    test('an oversized message is dropped without breaking the next one', () {
      final decoder = SetupFrameDecoder();
      expect(decoder.feed(List.filled(2000, 0x41)), isEmpty);
      expect(decoder.feed(utf8.encode('\n{}\n')), ['{}']);
    });

    test('split adds the newline and respects the chunk size', () {
      final chunks = splitSetupMessage(utf8.encode('abcdefghij'), 4);
      expect(chunks.map(utf8.decode), ['abcd', 'efgh', 'ij\n']);
    });

    test('chunk size follows the MTU within limits', () {
      expect(BleSetupProtocol.chunkSizeFor(23), 20);
      expect(BleSetupProtocol.chunkSizeFor(100), 97);
      expect(BleSetupProtocol.chunkSizeFor(517), 180);
    });
  });

  group('SetupRpc', () {
    SetupRpc rpcFor(_FakePi pi,
        {Duration timeout = const Duration(seconds: 1)}) {
      final rpc =
          SetupRpc(chunkSize: 20, writeChunk: pi.write, timeout: timeout);
      pi.rpc = rpc;
      addTearDown(rpc.close);
      return rpc;
    }

    test('matches replies to requests, one request at a time', () async {
      final pi = _FakePi((r) => {
            'ok': true,
            'echo': r['cmd'],
            'wifi': {'state': 'connected'}
          });
      final rpc = rpcFor(pi);

      final replies = await Future.wait([
        rpc.call('info'),
        rpc.call('status'),
        rpc.call('auth', args: {'password': 'x' * 100}),
      ]);

      expect(replies.map((r) => r.field<String>('echo')),
          ['info', 'status', 'auth']);
      expect(replies[1].object('wifi')?['state'], 'connected');
      expect(pi.written.every((c) => c.length <= 20), isTrue);
    });

    test('error replies keep their code and extra fields', () async {
      final pi = _FakePi(
          (_) => {'ok': false, 'error': 'WRONG_PASSWORD', 'attempts_left': 4});
      final reply = await rpcFor(pi).call('auth', args: {'password': 'nope'});

      expect(reply.ok, isFalse);
      expect(reply.error, 'WRONG_PASSWORD');
      expect(reply.field<int>('attempts_left'), 4);
    });

    test('a reply without id goes to the request in flight', () async {
      final pi = _FakePi((_) => {'id': null, 'ok': false, 'error': 'BAD_JSON'});
      // The fake adds the request id first; the spread overrides it with null.
      final reply = await rpcFor(pi).call('info');

      expect(reply.error, 'BAD_JSON');
    });

    test('times out when the machine does not answer, then keeps working',
        () async {
      var silent = true;
      final pi = _FakePi((_) => silent ? null : {'ok': true});
      final rpc = rpcFor(pi, timeout: const Duration(milliseconds: 50));

      await expectLater(
        rpc.call('ping'),
        throwsA(isA<SetupRpcException>()
            .having((e) => e.reason, 'reason', SetupRpcFailure.timeout)),
      );
      silent = false;
      expect((await rpc.call('ping')).ok, isTrue);
    });

    test('closing (disconnect) fails pending and later calls', () async {
      final pi = _FakePi((_) => null);
      final rpc = rpcFor(pi);

      final pending = rpc.call('status');
      rpc.close();

      await expectLater(
        pending,
        throwsA(isA<SetupRpcException>()
            .having((e) => e.reason, 'reason', SetupRpcFailure.closed)),
      );
      await expectLater(rpc.call('ping'), throwsA(isA<SetupRpcException>()));
    });
  });
}
