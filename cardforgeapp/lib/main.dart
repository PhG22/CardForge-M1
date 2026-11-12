import 'package:flutter/material.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
import 'core/app_scope.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Messages between the app and the background alert service (RF38).
  FlutterForegroundTask.initCommunicationPort();
  final prefs = await SharedPreferences.getInstance();
  runApp(CardForgeApp(deps: AppDependencies.create(prefs)));
}
