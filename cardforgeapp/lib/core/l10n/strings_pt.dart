import '../../features/alerts/machine_alert.dart';
import '../../features/auth/auth_service.dart';
import '../../features/cards/card_models.dart';
import '../../features/machine/api/machine_api_models.dart';
import '../../features/machine/machine_controller.dart';
import '../../features/machine/machine_models.dart';
import '../../features/runs/run_models.dart';
import 'app_strings.dart';

class PtStrings extends AppStrings {
  const PtStrings();

  // Common
  @override
  String get tryAgain => 'Tentar novamente';
  @override
  String get back => 'Voltar';
  @override
  String get view => 'Ver';
  @override
  String get done => 'Concluir';
  @override
  String get close => 'Fechar';
  @override
  String comingSoon(String feature) => '$feature em breve.';

  // Splash
  @override
  String get appTagline => 'Organize suas cartas de Magic';
  @override
  String get splashConnecting => 'Conectando ao servidor…';
  @override
  String get splashCheckingSession => 'Verificando sessão…';
  @override
  String get serverUnreachableTitle => 'Não foi possível conectar ao servidor';
  @override
  String get serverUnreachableHint =>
      'Verifique sua internet e tente novamente.';

  // Login
  @override
  String get loginSubtitle => 'Entre para gerenciar seu organizador';
  @override
  String get usernameOrEmail => 'Usuário ou e-mail';
  @override
  String get password => 'Senha';
  @override
  String get showPassword => 'Mostrar senha';
  @override
  String get hidePassword => 'Ocultar senha';
  @override
  String get rememberMe => 'Lembrar de mim';
  @override
  String get signIn => 'Entrar';
  @override
  String get forgotPassword => 'Esqueci minha senha';
  @override
  String get orDivider => 'ou';
  @override
  String get continueWithGoogle => 'Continuar com Google';
  @override
  String get newHere => 'Novo por aqui?';
  @override
  String get createAccount => 'Criar conta';
  @override
  String get passwordRecoverySoon => 'Recuperação de senha em breve.';
  @override
  String get usernameRequired => 'Informe seu usuário ou e-mail';
  @override
  String get invalidEmail => 'E-mail inválido';
  @override
  String get passwordRequired => 'Informe sua senha';
  @override
  String get passwordTooShort => 'A senha deve ter ao menos 6 caracteres';
  @override
  String authError(AuthFailure failure) => switch (failure) {
        AuthFailure.invalidCredentials => 'Usuário ou senha inválidos.',
        AuthFailure.serverUnreachable =>
          'Sem conexão com o servidor. Tente novamente.',
        AuthFailure.serverError => 'Erro no servidor. Tente novamente.',
        AuthFailure.googleCancelled => 'Login com Google cancelado.',
        AuthFailure.googleTokenMissing =>
          'Não foi possível obter o token do Google.',
        AuthFailure.usernameTaken =>
          'Este nome de usuário já está em uso. Escolha outro.',
        AuthFailure.emailTaken =>
          'Já existe uma conta com este e-mail. Entre com ela.',
        AuthFailure.invalidSignUp =>
          'O servidor não aceitou esses dados. Confira e tente de novo.',
        AuthFailure.accountDisabled => 'Esta conta está desativada.',
      };

  // Sign-up
  @override
  String get signUpSubtitle =>
      'As cartas que você separar ficam salvas na sua conta.';
  @override
  String get username => 'Nome de usuário';
  @override
  String get email => 'E-mail';
  @override
  String get confirmPassword => 'Confirme a senha';
  @override
  String get chooseUsername => 'Escolha um nome de usuário';
  @override
  String get usernameRules =>
      "De 3 a 32 letras, números, '.', '_' ou '-' (sem espaços)";
  @override
  String get emailRequired => 'Informe seu e-mail';
  @override
  String get newPasswordTooShort => 'A senha deve ter ao menos 8 caracteres';
  @override
  String get passwordsDoNotMatch => 'As senhas não conferem';
  @override
  String get alreadyHaveAccount => 'Já tem uma conta?';

  // Home and menu
  @override
  String get noMachineTitle => 'Nenhuma máquina vinculada';
  @override
  String get noMachineBody =>
      'Vincule seu organizador para acompanhar e controlar a separação das cartas.';
  @override
  String get machineLinkedBody => 'Vinculada à sua conta';
  @override
  String get menuMachineControl => 'Controle da máquina';
  @override
  String get menuCardsInFolders => 'Cartas nas pastas';
  @override
  String get menuRuns => 'Ordenações';
  @override
  String get menuCsvReport => 'Relatório CSV';
  @override
  String get menuSortingRules => 'Regras de ordenação';
  @override
  String get linkMachine => 'Vincular máquina';
  @override
  String get alerts => 'Alertas';
  @override
  String get settings => 'Configurações';
  @override
  String get signOut => 'Sair';

  // Link machine
  @override
  String get selectMachine => 'Selecione sua máquina';
  @override
  String get scanningForMachines => 'Procurando máquinas CardForge por perto…';
  @override
  String get scanAgain => 'Buscar novamente';
  @override
  String get noMachinesFound => 'Nenhuma máquina encontrada';
  @override
  String get noMachinesHint =>
      'Confirme que a máquina está ligada e por perto e busque de novo.';
  @override
  String get scanFailedTitle => 'Falha na busca por Bluetooth';
  @override
  String get enterMachinePassword => 'Senha da máquina';
  @override
  String machinePasswordHint(String machineName) =>
      'Informe a senha de $machineName para permitir a vinculação.';
  @override
  String get machinePassword => 'Senha da máquina';
  @override
  String get machinePasswordRequired => 'Informe a senha da máquina';
  @override
  String get verifyPassword => 'Verificar senha';
  @override
  String get chooseAnotherMachine => 'Escolher outra máquina';
  @override
  String get wifiQuestionTitle => 'Enviar dados do Wi-Fi?';
  @override
  String get wifiQuestionBody =>
      'O app controla sua máquina pelo Wi-Fi. Enviar agora o nome e a senha '
      'da sua rede? Pule só se a máquina já estiver na sua rede.';
  @override
  String get sendWifi => 'Sim, enviar Wi-Fi';
  @override
  String get skipWifi => 'Pular por enquanto';
  @override
  String get wifiFormTitle => 'Dados do Wi-Fi';
  @override
  String get networkName => 'Nome da rede (SSID)';
  @override
  String get networkNameRequired => 'Informe o nome da rede';
  @override
  String get wifiPassword => 'Senha do Wi-Fi';
  @override
  String get wifiPasswordHint => 'Deixe vazio para redes abertas';
  @override
  String get wifiTip =>
      'Use a mesma rede do seu celular: é por ela que o app fala com a máquina. '
      'Dica: o Raspberry Pi 3 Model B só enxerga redes de 2,4 GHz.';
  @override
  String get sendAndLink => 'Enviar e vincular';
  @override
  String get linkingTitle => 'Vinculando sua máquina';
  @override
  String get linkingHint =>
      'A máquina vai confirmar quando tudo estiver pronto. '
      'Isso pode levar até um minuto.';
  @override
  String linkStep(LinkStep step) => switch (step) {
        LinkStep.sendingWifi => 'Enviando os dados do Wi-Fi…',
        LinkStep.waitingForWifi => 'Aguardando a máquina conectar ao Wi-Fi…',
        LinkStep.checkingConnection => 'Verificando a conexão pelo Wi-Fi…',
        LinkStep.done => 'Concluído',
      };
  @override
  String get linkSuccessTitle => 'Máquina vinculada!';
  @override
  String linkSuccessBody(String machineName) =>
      '$machineName está pronta. Inicie uma ordenação na tela de controle da máquina.';
  @override
  String get linkFailureTitle => 'Não foi possível vincular a máquina';
  @override
  String linkFailure(MachineLinkFailure failure) => switch (failure) {
        MachineLinkFailure.bluetoothUnavailable =>
          'O Bluetooth está indisponível. Ative-o e tente de novo.',
        MachineLinkFailure.bluetoothPermission =>
          'O CardForge precisa da permissão de Bluetooth (dispositivos '
              'próximos). Permita nas configurações do celular e tente de novo.',
        MachineLinkFailure.machineNotFound =>
          'Máquina não encontrada. Confirme que ela está ligada e perto do celular.',
        MachineLinkFailure.incompatibleMachine =>
          'Esta máquina usa outra versão da configuração. Atualize a máquina '
              'ou o app.',
        MachineLinkFailure.wrongPassword => 'Senha da máquina incorreta.',
        MachineLinkFailure.tooManyAttempts =>
          'Muitas senhas erradas. Aguarde um minuto e tente de novo.',
        MachineLinkFailure.connectionLost =>
          'A conexão com a máquina foi perdida.',
        MachineLinkFailure.wifiNotFound =>
          'A máquina não encontrou essa rede Wi-Fi. Confira o nome '
              '(o Raspberry Pi 3 só enxerga redes de 2,4 GHz).',
        MachineLinkFailure.wifiWrongPassword =>
          'A rede Wi-Fi recusou a senha. Confira e tente de novo.',
        MachineLinkFailure.wifiFailed =>
          'A máquina não conseguiu conectar a essa rede Wi-Fi. '
              'Confira o nome e a senha.',
        MachineLinkFailure.noNetwork =>
          'A máquina não está conectada a nenhuma rede. Envie os dados do Wi-Fi.',
        MachineLinkFailure.machineApiDown =>
          'A máquina está na rede, mas o serviço de controle dela não está '
              'rodando. Reinicie a máquina e tente de novo.',
        MachineLinkFailure.apiUnreachable =>
          'A máquina entrou na rede, mas este celular não consegue alcançá-la. '
              'Confirme que o celular está no mesmo Wi-Fi (e não nos dados móveis).',
        MachineLinkFailure.timeout =>
          'A máquina não confirmou a tempo. Tente novamente.',
        MachineLinkFailure.unknown => 'Algo deu errado. Tente novamente.',
      };
  @override
  String get wifiPasswordInvalid =>
      'Use de 8 a 63 caracteres, ou deixe vazio para uma rede aberta';
  @override
  String get openMachineControl => 'Abrir controle da máquina';

  // Machine control
  @override
  String connectionLabel(MachineConnection connection) => switch (connection) {
        MachineConnection.none => 'Nenhuma máquina',
        MachineConnection.noAddress => 'Endereço desconhecido',
        MachineConnection.connecting => 'Conectando…',
        MachineConnection.online => 'Online',
        MachineConnection.offline => 'Sem conexão',
      };
  @override
  String get noMachineControlBody =>
      'Vincule sua máquina primeiro. O Bluetooth só é usado nessa etapa; '
      'depois o app controla a máquina pelo Wi-Fi.';
  @override
  String get noAddressTitle => 'Endereço da máquina desconhecido';
  @override
  String get noAddressBody =>
      'Este celular não sabe onde encontrar a máquina no Wi-Fi. '
      'Fique perto dela e procure pelo Bluetooth.';
  @override
  String get machineOfflineTitle => 'Não foi possível falar com a máquina';
  @override
  String machineOfflineBody(String address) =>
      'Sem resposta de $address. Confirme que a máquina está ligada e que o '
      'celular está no mesmo Wi-Fi. Se o roteador deu um novo endereço a ela, '
      'procure pelo Bluetooth.';
  @override
  String get findOverBluetooth => 'Procurar pelo Bluetooth';
  @override
  String get findingMachine => 'Procurando a máquina pelo Bluetooth…';
  @override
  String get simulatedMachine => 'Máquina simulada (sem hardware)';
  @override
  String get topOnlyMode => 'Só o módulo superior: sem base giratória';
  @override
  String runStateLabel(ControllerState state) => switch (state) {
        ControllerState.idle => 'Pronta',
        ControllerState.starting => 'Preparando…',
        ControllerState.running => 'Ordenando',
        ControllerState.stopping => 'Parando…',
        ControllerState.paused => 'Pausada',
        ControllerState.manual => 'Movendo a base…',
        ControllerState.unknown => 'Desconhecido',
      };
  @override
  String get cardsProcessed => 'Cartas processadas';
  @override
  String get currentFolder => 'Pasta atual';
  @override
  String get startSorting => 'Iniciar ordenação';
  @override
  String get startingHint =>
      'Preparando a máquina: verificando os sensores e levando a base à '
      'posição inicial. Isso leva até 40 segundos.';
  @override
  String get stopSorting => 'Parar';
  @override
  String get resumeSorting => 'Retomar';
  @override
  String get resumingHint =>
      'Fazendo o homing da base de novo; depois a ordenação continua. '
      'Leva até 40 segundos.';
  @override
  String get resumeProblemsTitle => 'Ainda não dá para retomar';
  @override
  String get pausedHint =>
      'Resolva o problema na máquina e toque em Retomar. Parar encerra a '
      'ordenação aqui.';
  @override
  String faultDescription(String? code) => switch (code) {
        'FEED_TIMEOUT' =>
          'nenhuma carta chegou à plataforma (atolamento ou pilha vazia).',
        'FEED_FAILED' =>
          'a alimentação terminou, mas não há carta na plataforma.',
        'DROP_FAILED' => 'uma carta não caiu da plataforma.',
        'MOVE_TIMEOUT' => 'a base não chegou à pasta.',
        'HOME_TIMEOUT' ||
        'HOME_FAILED' =>
          'a base não encontrou a posição inicial.',
        'NOT_HOMED' => 'a base perdeu a posição.',
        null => 'a máquina informou uma falha.',
        _ => 'a máquina informou $code.',
      };
  String _hms(DateTime t) {
    final local = t.toLocal();
    return '${_two(local.hour)}:${_two(local.minute)}:${_two(local.second)}';
  }
  @override
  String get emergencyStop => 'Parada de emergência';
  @override
  String get startProblemsTitle => 'Ainda não dá para iniciar';
  @override
  String startProblem(StartProblem problem) => switch (problem.code) {
        'STACK_EMPTY' => 'Não há cartas na pilha. Coloque cartas e tente de novo.',
        'TRAYS_NOT_EMPTY' when problem.trays.length == 1 =>
          'A pasta ${problem.trays.first} não está vazia. Esvazie-a e tente de novo.',
        'TRAYS_NOT_EMPTY' when problem.trays.isNotEmpty =>
          'As pastas ${problem.trays.join(', ')} não estão vazias. '
              'Esvazie-as e tente de novo.',
        'PLATFORM_NOT_CLEAR' =>
          'Ficou uma carta na plataforma de leitura. Retire-a e tente de novo.',
        'RUN_ACTIVE' => 'Já existe uma ordenação em andamento.',
        'START_CANCELLED' => 'O início foi cancelado.',
        'MODULE_OFFLINE' => '${_moduleName(problem.module)} não está respondendo. '
            'Verifique o cabo e a alimentação.',
        'HOME_TIMEOUT' => 'A base não chegou à posição inicial a tempo.',
        'HOMING' => 'A base está fazendo o homing depois de ligar. '
            'Tente de novo em alguns segundos.',
        'SIGN_IN_REQUIRED' =>
          'Sua sessão expirou. Entre novamente para iniciar uma ordenação.',
        'SERVER_UNREACHABLE' =>
          'A máquina não consegue falar com o servidor CardForge, então a '
              'ordenação não pode ser salva na sua conta. Verifique a rede dela.',
        'SERVER_ERROR' => 'O servidor CardForge teve um problema. Tente de novo.',
        _ => problem.message.isNotEmpty
            ? problem.message
            : 'A máquina não conseguiu iniciar (${problem.code}).',
      };
  String _moduleName(String? module) => switch (module) {
        'A1' => 'O módulo superior (A1)',
        'A2' => 'A base (A2)',
        _ => 'Um módulo',
      };
  @override
  String machineApiError(MachineApiException error) => switch (error.failure) {
        MachineApiFailure.unreachable =>
          'Não foi possível falar com a máquina. Confirme que ela está ligada '
              'e que o celular está no mesmo Wi-Fi.',
        MachineApiFailure.conflict => switch (error.code) {
            'NOT_HOMED' => 'Leve a base à posição inicial primeiro.',
            'RUN_ACTIVE' => 'Não é possível durante uma ordenação.',
            'BOTTOM_DISABLED' => 'A base está desativada (só módulo superior).',
            _ => error.message ?? 'Não é possível agora.',
          },
        MachineApiFailure.moduleOffline =>
          'Um módulo não está respondendo. Verifique os cabos e a alimentação.',
        MachineApiFailure.hardware => switch (error.code) {
            'HOME_TIMEOUT' => 'A base não chegou à posição inicial a tempo.',
            'HOME_FAILED' => 'A base não encontrou a faixa refletiva.',
            _ => 'Problema no hardware${error.code == null ? '' : ' (${error.code})'}. '
                'Verifique a máquina.',
          },
        MachineApiFailure.badResponse => 'Resposta inesperada da máquina.',
      };
  @override
  String get lastRun => 'Última ordenação';
  @override
  String runResult(RunInfo run) => switch (run.status) {
        RunStatus.running => 'Ordenação em andamento.',
        RunStatus.paused =>
          'Pausada depois de ${cardsCount(run.cardsProcessed)}: '
              '${faultDescription(run.errorCode)}',
        RunStatus.completed => run.cardsProcessed == 1
            ? 'Concluída: 1 carta ordenada.'
            : 'Concluída: ${run.cardsProcessed} cartas ordenadas.',
        RunStatus.cancelled =>
          'Parada por você depois de ${cardsCount(run.cardsProcessed)}.',
        RunStatus.failed => switch (run.errorCode) {
            'FEED_TIMEOUT' =>
              'Parou: o alimentador não conseguiu mover uma carta '
                  '(atolamento ou pilha vazia).',
            'MODULE_OFFLINE' => 'Parou: um módulo deixou de responder.',
            _ => 'Parou com um erro'
                '${run.errorCode == null ? '' : ' (${run.errorCode})'}.',
          },
        RunStatus.unknown => run.message ?? '',
      };
  @override
  String get recentlySorted => 'Ordenadas há pouco';
  @override
  String sortedCard(SortedCard card) {
    final folder = card.tray == null ? '' : ' → ${folderLabel(card.tray!)}';
    return card.cleared
        ? 'Carta nº ${card.number} descartada pela parada$folder'
        : 'Carta nº ${card.number}$folder';
  }
  @override
  String get diagnostics => 'Diagnóstico';
  @override
  String get refresh => 'Atualizar';
  @override
  String get topModule => 'Módulo superior (A1)';
  @override
  String get bottomModule => 'Base (A2)';
  @override
  String get moduleOnline => 'Online';
  @override
  String get moduleOffline => 'Sem resposta';
  @override
  String get stackHasCards => 'Cartas na pilha';
  @override
  String get platformHasCard => 'Carta na plataforma';
  @override
  String get flap => 'Aba';
  @override
  String flapValue(int angle) => switch (angle) {
        80 => 'Fechada (80°)',
        0 => 'Aberta (0°)',
        _ => '$angle°',
      };
  @override
  String get roller => 'Rolo girando';
  @override
  String get homed => 'Na referência';
  @override
  String get position => 'Posição';
  @override
  String positionValue(int? position) =>
      position == null || position == 0 ? 'Entre pastas' : folderLabel(position);
  @override
  String get drift => 'Desvio (passos)';
  @override
  String get foldersWithCards => 'Pastas com cartas';
  @override
  String get none => 'Nenhuma';
  @override
  String get yes => 'Sim';
  @override
  String get no => 'Não';
  @override
  String get baseTest => 'Teste da base';
  @override
  String get baseTestHint =>
      'Move a base fora de uma ordenação, para conferir a posição das pastas.';
  @override
  String get homeBase => 'Referência';
  @override
  String get forgetMachine => 'Esquecer esta máquina';
  @override
  String forgetMachineConfirm(String machineName) =>
      'Esquecer $machineName neste celular? Para usá-la de novo será preciso '
      'vinculá-la pelo Bluetooth.';
  @override
  String get cancel => 'Cancelar';
  @override
  String get forget => 'Esquecer';

  // Cards in folders
  @override
  String get searchCardsHint => 'Pesquisar carta pelo nome';
  @override
  String get clearSearch => 'Limpar pesquisa';
  @override
  String get allCardsHeader => 'Cartas nas suas pastas';
  @override
  String resultsFor(String query) => 'Resultados para “$query”';
  @override
  String cardsCount(int count) => count == 1 ? '1 carta' : '$count cartas';
  @override
  String copiesCount(int quantity) =>
      quantity == 1 ? '1 cópia' : '$quantity cópias';
  @override
  String folderLabel(int folder) => 'Pasta $folder';
  @override
  String get cardsEmptyTitle => 'Nenhuma carta nas pastas ainda';
  @override
  String get cardsEmptyHint =>
      'As cartas aparecem aqui depois de uma ordenação.';
  @override
  String get noCardsFoundTitle => 'Nenhuma carta encontrada';
  @override
  String get noCardsFoundHint => 'Confira a grafia ou tente outro nome.';
  @override
  String get cardsLoadFailedTitle => 'Não foi possível carregar as cartas';
  @override
  String cardsFailure(CardsFailure failure) => switch (failure) {
        CardsFailure.unauthorized => 'Sua sessão expirou. Entre novamente.',
        CardsFailure.serverUnreachable =>
          'Sem conexão com o servidor. Verifique sua internet e tente de novo.',
        CardsFailure.serverError =>
          'O servidor teve um problema. Tente novamente.',
      };
  @override
  String get signInAgain => 'Entrar novamente';

  // Run history and results
  @override
  String get runsLoadFailedTitle => 'Não foi possível carregar suas ordenações';
  @override
  String get runsEmptyTitle => 'Nenhuma ordenação ainda';
  @override
  String get runsEmptyHint =>
      'As ordenações iniciadas nesta conta aparecem aqui quando terminam.';
  @override
  String get loadMore => 'Carregar mais';
  @override
  String runStatusLabel(RunStatus status) => switch (status) {
        RunStatus.completed => 'Concluída',
        RunStatus.cancelled => 'Interrompida',
        RunStatus.failed => 'Terminou antes',
        RunStatus.running => 'Em andamento',
        RunStatus.paused => 'Pausada',
        RunStatus.unknown => 'Ordenação',
      };
  @override
  String dateTime(DateTime time) => '${_two(time.day)}/${_two(time.month)}/'
      '${time.year} ${_two(time.hour)}:${_two(time.minute)}';
  String _two(int n) => n.toString().padLeft(2, '0');
  @override
  String runCardsSummary(int cards, int identified) =>
      '${cardsCount(cards)}, $identified identificadas';
  @override
  String get runResultsTitle => 'Resultado da ordenação';
  @override
  String get runLoadFailedTitle => 'Não foi possível carregar esta ordenação';
  @override
  String runOutcome(RunSummary run) => switch (run.status) {
        RunStatus.completed when run.errorCode == 'BATCH_LIMIT' =>
          'Terminou no limite do lote: ${cardsCount(run.cardsProcessed)} separadas.',
        RunStatus.completed =>
          'Concluída com a pilha vazia: ${cardsCount(run.cardsProcessed)} separadas.',
        RunStatus.cancelled =>
          'Interrompida por você depois de ${cardsCount(run.cardsProcessed)}.',
        RunStatus.failed => switch (run.errorCode) {
            'MODULE_OFFLINE' => 'Terminou antes: um módulo parou de responder.',
            'INTERRUPTED' =>
              'Terminou antes: a máquina reiniciou durante a ordenação.',
            _ => 'Terminou antes por um erro'
                '${run.errorCode == null ? '' : ' (${run.errorCode})'}.',
          },
        RunStatus.running ||
        RunStatus.paused ||
        RunStatus.unknown =>
          run.message ?? '',
      };
  @override
  String get identifiedLabel => 'Identificadas';
  @override
  String get unidentifiedLabel => 'Não identificadas';
  @override
  String get faceDownLabel => 'Viradas';
  @override
  String get cardsPerFolder => 'Cartas por pasta';
  @override
  String get cardsInThisRun => 'Cartas desta ordenação';
  @override
  String get noCardsInRun => 'Nenhuma carta foi separada nesta ordenação.';
  @override
  String scanOutcome(ScanOutcome outcome) => switch (outcome) {
        ScanOutcome.identified => 'Identificada',
        ScanOutcome.unidentified => 'Carta não identificada',
        ScanOutcome.faceDown => 'Carta virada',
      };
  @override
  String runCardDetails(RunCard card) => [
        if (card.setCode != null)
          '${card.setCode!.toUpperCase()} ${card.collectorNumber ?? ''}'.trim(),
        if (card.folder != null) folderLabel(card.folder!),
      ].join(' · ');

  // CSV export
  @override
  String get exportTitle => 'Exportar sua coleção';
  @override
  String get exportHint =>
      'Todas as suas cartas, de todas as ordenações, em um arquivo CSV: nome, '
      'edição, número de coleção, pasta e quantidade. Abre no Excel, no '
      'Google Planilhas e na maioria dos sites de coleção.';
  @override
  String get exportCsv => 'Exportar CSV';
  @override
  String get exportNothing =>
      'Sua coleção está vazia: separe algumas cartas primeiro.';
  @override
  String get exportFileFailed => 'Não foi possível criar ou compartilhar o arquivo.';
  @override
  String get exportFailedTitle => 'Não foi possível exportar a coleção';
  @override
  String exportDone(int cards, String fileName) =>
      '${cardsCount(cards)} exportadas para $fileName.';

  // Settings
  @override
  String get languageSection => 'Idioma';
  @override
  String get developerTools => 'Ferramentas de desenvolvimento';
  @override
  String get simulateAlerts => 'Simular alertas';

  // Alerts
  @override
  String get noAlerts => 'Nenhum alerta ainda';
  @override
  String get noAlertsHint => 'Os eventos da máquina vão aparecer aqui.';
  @override
  String get markAllRead => 'Marcar tudo como lido';
  @override
  String get clearAll => 'Limpar tudo';
  @override
  String get yourMachine => 'Sua máquina';
  @override
  String alertTitle(AlertType type) => switch (type) {
        AlertType.wifiConnected => 'Máquina conectada ao Wi-Fi',
        AlertType.wifiDisconnected => 'Máquina sem Wi-Fi',
        AlertType.batchFinished => 'Ordenação concluída',
        AlertType.runFailed => 'Ordenação interrompida',
        AlertType.jamDetected => 'Atolamento detectado',
        AlertType.machineFault => 'Ordenação pausada',
        AlertType.doubleFeed => 'Alimentação dupla detectada',
        AlertType.connectionLost => 'Conexão perdida',
        AlertType.machineLinked => 'Máquina vinculada',
      };
  @override
  String alertMessage(MachineAlert alert) {
    final name = alert.machineName ?? yourMachine;
    return switch (alert.type) {
      AlertType.wifiConnected => '$name está online.',
      AlertType.wifiDisconnected =>
        '$name está offline. Verifique sua rede Wi-Fi.',
      AlertType.batchFinished =>
        '$name processou ${alert.cardCount ?? 0} cartas.',
      AlertType.runFailed =>
        '$name parou com um erro. Abra o controle da máquina para ver os detalhes.',
      AlertType.jamDetected => '$name pausou às ${_hms(alert.createdAt)}: '
          'nenhuma carta chegou à plataforma. Remova o atolamento e toque em '
          'Retomar.',
      AlertType.machineFault => '$name pausou às ${_hms(alert.createdAt)}: '
          '${faultDescription(alert.faultCode)} Resolva e toque em Retomar.',
      AlertType.doubleFeed => '$name puxou duas cartas ao mesmo tempo.',
      AlertType.connectionLost =>
        'Contato com $name perdido. O app continua tentando reconectar.',
      AlertType.machineLinked => '$name agora está vinculada à sua conta.',
    };
  }
}
