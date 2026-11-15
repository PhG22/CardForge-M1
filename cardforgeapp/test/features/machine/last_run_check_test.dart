import 'package:cardforge/features/machine/api/machine_api_models.dart';
import 'package:flutter_test/flutter_test.dart';

/// RNF19: `last_run_check` as sorter_api sends it (store.py, last_run_check).
void main() {
  test('reads the check from GET /run/status', () {
    final snapshot = MachineSnapshot.fromJson({
      'state': 'idle',
      'run': {'run_id': 'r1', 'status': 'failed', 'error_code': 'INTERRUPTED'},
      'last_run_check': {
        'ok': false,
        'reason': 'INTERRUPTED',
        'message': 'The last run did not finish correctly: …',
        'finished': false,
        'records_ok': true,
        'database_ok': true,
        'cards_saved': 2,
        'run': {
          'run_id': 'r1',
          'status': 'failed',
          'cards_processed': 2,
          'error_code': 'INTERRUPTED',
        },
      },
    });

    final check = snapshot.lastRunCheck!;
    expect(check.ok, isFalse);
    expect(check.reason, 'INTERRUPTED');
    expect(check.finished, isFalse);
    expect(check.recordsOk, isTrue);
    expect(check.databaseOk, isTrue);
    expect(check.cardsSaved, 2);
    expect(check.run?.runId, 'r1');
    expect(check.run?.status, RunStatus.failed);
  });

  test('no check before the first run, during a run, or from an older API',
      () {
    expect(MachineSnapshot.fromJson({'state': 'idle'}).lastRunCheck, isNull);
    expect(
      MachineSnapshot.fromJson({'state': 'running', 'last_run_check': null})
          .lastRunCheck,
      isNull,
    );
  });

  test('a check it cannot read never warns', () {
    final check = LastRunCheck.fromJson({'reason': 42});
    expect(check.ok, isTrue);
    expect(check.reason, isNull);
  });

  test('run_finished carries the check of the run that just ended', () {
    final event = MachineEvent.fromJson({
      'type': 'run_finished',
      'run': {'run_id': 'r1', 'status': 'completed', 'cards_processed': 5},
      'message': 'Stack is empty: 5 card(s) processed.',
      'last_run_check': {'ok': true, 'reason': null, 'cards_saved': 5},
    });
    expect(event, isA<RunFinishedEvent>());
    final check = (event as RunFinishedEvent).lastRunCheck!;
    expect(check.ok, isTrue);
    expect(check.cardsSaved, 5);
  });
}
