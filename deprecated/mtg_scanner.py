import os
import cv2
import numpy as np
import re
import json
import time
import sys
from rapidocr_onnxruntime import RapidOCR
from thefuzz import process

ocr = RapidOCR()

# CARREGAMENTO DOS BANCOS DE DADOS LOCAIS
caminho_sets = "sets_oficiais.json"
if os.path.exists(caminho_sets):
    with open(caminho_sets, "r", encoding="utf-8") as f:
        SETS_OFICIAIS = json.load(f)
else:
    print(f"AVISO: Arquivo '{caminho_sets}' não encontrado.")
    sys.exit(1)

caminho_db = "mini_scryfall.json"
SCRYFALL_DB = {"prints": {}, "oracle": {}}
if os.path.exists(caminho_db):
    print("Carregando Banco de Dados Offline...")
    with open(caminho_db, "r", encoding="utf-8") as f:
        SCRYFALL_DB = json.load(f)
else:
    print(f"AVISO: Arquivo '{caminho_db}' não encontrado.")
    sys.exit(2)


# FUNÇÕES DE GEOMETRIA E RECORTE
def auto_canny(image, sigma=0.33):
    """
    Calcula automaticamente os limiares inferior e superior para a detecção de bordas
    usando o algoritmo de Canny, baseado na intensidade mediana dos pixels da imagem.
    """
    v = np.median(image)
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))
    return cv2.Canny(image, lower, upper)

def order_points(pts):
    """
    Ordena 4 coordenadas geográficas de um polígono na seguinte ordem:
    Topo-Esquerda, Topo-Direita, Fundo-Direita, Fundo-Esquerda.
    Essencial para a correção de perspectiva.
    """
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)] # Topo-Esquerda tem a menor soma x+y
    rect[2] = pts[np.argmax(s)] # Fundo-Direita tem a maior soma x+y
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)] # Topo-Direita tem a menor diferença
    rect[3] = pts[np.argmax(diff)] # Fundo-Esquerda tem a maior diferença
    return rect

def four_point_transform(image, pts):
    """
    Aplica uma transformação de perspectiva (Warp) para "achatar" a carta,
    corrigindo qualquer inclinação da câmera em relação à mesa.
    """
    rect = order_points(pts)
    (tl, tr, br, bl) = rect
    # Calcula a largura e altura máximas para a nova imagem plana
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    
    # Mapeia os pontos originais para um retângulo perfeito
    dst = np.array([[0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxWidth, maxHeight))

def isolate_and_standardize_card(image_path):
    """
    Localiza a carta na fotografia original, recorta-a e padroniza a sua resolução
    para 630x880 pixels. Caso não encontre a carta (falha de contraste),
    retorna uma imagem preta de segurança para evitar lentidão extrema.
    """
    original_image = cv2.imread(image_path)
    if original_image is None:
        raise ValueError(f"Não foi possível ler a imagem.")

    # Redimensionamento inicial para acelerar o processamento geométrico
    altura_original = original_image.shape[0]
    ratio = altura_original / 800.0
    largura_redimensionada = int(original_image.shape[1] / ratio)
    
    image_processing = cv2.resize(original_image, (largura_redimensionada, 800))
    gray = cv2.cvtColor(image_processing, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = auto_canny(blur)
    
    # Fecha pequenos buracos nas bordas detectadas
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed_edges = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed_edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    
    # Limita a pesquisa apenas aos 15 maiores contornos.
    # Impede que o programa congele tentando analisar ruídos da superfície.
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:15]

    card_contour = None
    img_area = image_processing.shape[0] * image_processing.shape[1]

    for c in contours:
        area = cv2.contourArea(c)
        if area < img_area * 0.10: # Ignora objetos muito pequenos
            continue
        hull = cv2.convexHull(c)
        peri = cv2.arcLength(hull, True)
        
        # Tenta aproximar o contorno a um polígono de 4 lados
        for eps_multiplier in np.linspace(0.01, 0.10, 10):
            approx = cv2.approxPolyDP(hull, eps_multiplier * peri, True)
            if len(approx) == 4:
                x, y, w, h = cv2.boundingRect(approx)
                # Verifica se a proporção corresponde a uma carta de Magic
                if 1.20 <= float(max(w, h)) / min(w, h) <= 1.70:
                    card_contour = approx
                    break 
        if card_contour is not None: break 

    if card_contour is not None:
        card_contour_original = (card_contour.astype("float") * ratio).astype("int")
        warped = four_point_transform(original_image, card_contour_original.reshape(4, 2))
        
        # Garante que a carta fique na vertical
        if warped.shape[1] > warped.shape[0]:
            warped = cv2.rotate(warped, cv2.ROTATE_90_CLOCKWISE)
        return cv2.resize(warped, (630, 880)), True

    # Se falhar, devolve uma imagem preta de aviso
    warped = np.zeros((880, 630, 3), dtype=np.uint8) 
    cv2.putText(warped, "FALHA", (250, 440), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    return warped, False


# OCR E EXTRAÇÃO
def executar_ocr_simples(roi_img):
    """
    Função auxiliar que executa o OCR numa pequena Região de Interesse (ROI) 
    e devolve o texto extraído em formato de string limpa.
    """
    result, _ = ocr(roi_img)
    return " ".join([res[1] for res in result]).strip() if result else ""

def classificar_layout(card_image):
    """
    Analisa posições específicas da carta para determinar o seu layout visual 
    (Padrão, Saga, Full Art, Token). Isto dita onde as caixas de OCR vão procurar texto.
    """
    if "saga" in executar_ocr_simples(card_image[670:740, 30:490]).lower(): return "SAGA"
    
    texto_rodape = executar_ocr_simples(card_image[735:795, 30:490]).lower()
    if "land" in texto_rodape or "terreno" in texto_rodape: return "FULL_ART"
    if "token" in texto_rodape or "ficha" in texto_rodape: return "TOKEN_FULL"
        
    texto_token_classico = executar_ocr_simples(card_image[565:640, 30:490]).lower()
    if "token" in texto_token_classico or "ficha" in texto_token_classico: return "TOKEN_CLASSICO"
        
    return "PADRAO"

def extrair_textos_batch(img_nome, img_tipo, img_rodape):
    """
    Em vez de invocar o OCR três vezes, esta função empilha as imagens do Nome, Tipo e Rodapé numa única coluna, 
    forçando o OCR a ler tudo de uma só vez. 
    Os resultados são depois separados com base na coordenada Y de cada leitura.
    """
    h_n, w_n = img_nome.shape[:2]
    h_t, w_t = img_tipo.shape[:2]
    h_r, w_r = img_rodape.shape[:2]
    max_w = max(w_n, w_t, w_r)

    # Adiciona bordas pretas para que todas as imagens tenham a mesma largura e um espaçamento entre elas
    n_pad = cv2.copyMakeBorder(img_nome, 0, 20, 0, max_w - w_n, cv2.BORDER_CONSTANT, value=(0,0,0))
    t_pad = cv2.copyMakeBorder(img_tipo, 0, 20, 0, max_w - w_t, cv2.BORDER_CONSTANT, value=(0,0,0))
    r_pad = cv2.copyMakeBorder(img_rodape, 0, 0, 0, max_w - w_r, cv2.BORDER_CONSTANT, value=(0,0,0))

    # Concatena as imagens verticalmente
    batch_img = cv2.vconcat([n_pad, t_pad, r_pad])

    gray = cv2.cvtColor(batch_img, cv2.COLOR_BGR2GRAY)
    scaled = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_LINEAR)
    bgr = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGR)

    result, _ = ocr(bgr)
    t_nome, t_tipo, t_rodape = [], [], []

    if result:
        for res in result:
            box = res[0]
            texto = res[1]
            # Calcula o centro vertical da caixa lida e ajusta a escala
            y_center = (box[0][1] + box[2][1]) / 2.0 / 1.5 
            
            # Distribui o texto para a variável correta baseado na altura em que foi lido
            if y_center < (h_n + 10):
                t_nome.append(texto)
            elif y_center < (h_n + 20 + h_t + 10):
                t_tipo.append(texto)
            else:
                t_rodape.append(texto)

    return " ".join(t_nome).strip(), " ".join(t_tipo).strip(), " ".join(t_rodape).strip()


# INTEGRAÇÃO COM BANCO DE DADOS LOCAL
def consultar_dados_oficiais(nome_ocr, tipo_ocr, set_code, numero_colecionador, layout):
    """
    Cruza as informações imperfeitas do OCR com o banco de dados oficial (Mini Scryfall)
    em três etapas de segurança para garantir precisão nos metadados da carta.
    """
    dados_oficiais = {
        "Nome_Oficial": nome_ocr,
        "Tipo_Oficial": tipo_ocr,
        "Custo_Mana_Real": "N/A",
        "Raridade": "N/A",
        "Identidade_Cor_Real": "N/A"
    }

    set_query = set_code.lower()
    # Scryfall exige o prefixo 't' para edições de Tokens
    if "TOKEN" in layout and set_query != "n/a":
        if not set_query.startswith('t'): set_query = f"t{set_query}"

    chave_exata = f"{set_query}-{numero_colecionador.lower()}"
    nome_lower = nome_ocr.lower().strip()

    # Busca exata por Set e Número da Coleção
    if chave_exata in SCRYFALL_DB.get("prints", {}):
        dados_oficiais.update(SCRYFALL_DB["prints"][chave_exata])
        if not dados_oficiais["Tipo_Oficial"]: dados_oficiais["Tipo_Oficial"] = tipo_ocr
        return dados_oficiais

    # Fallback exato pelo Nome da carta (Para cartas antigas sem número)
    if nome_lower in SCRYFALL_DB.get("oracle", {}):
        dados_oficiais.update(SCRYFALL_DB["oracle"][nome_lower])
        if not dados_oficiais["Tipo_Oficial"]: dados_oficiais["Tipo_Oficial"] = tipo_ocr
        return dados_oficiais

    # Fallback Fuzzy (Corrige pequenos erros de leitura do OCR)
    if len(nome_lower) > 3:
        nomes_possiveis = list(SCRYFALL_DB.get("oracle", {}).keys())
        if nomes_possiveis:
            melhor_match, pontuacao = process.extractOne(nome_lower, nomes_possiveis)
            if pontuacao >= 85:
                dados_oficiais.update(SCRYFALL_DB["oracle"][melhor_match])
                if not dados_oficiais["Tipo_Oficial"]: dados_oficiais["Tipo_Oficial"] = tipo_ocr

    return dados_oficiais


# ORQUESTRAÇÃO PRINCIPAL
def extract_and_annotate_data(card_image, sucesso_recorte):
    """
    Controla o fluxo da extração. Lida com cartas invertidas de forma segura, 
    recorta as caixas de interesse com base no layout da carta, invoca o OCR e triangula 
    os dados com o dicionário local.
    """
    # Aborta o processo se o OpenCV tiver falhado na fase anterior
    if not sucesso_recorte:
        return {
            "Nome": "N/A", "Tipo": "N/A", "Numero_Colecionador": "N/A", 
            "Codigo_Edicao_Texto": "N/A", "Nome_Oficial": "FALHA NO RECORTE",
            "Custo_Mana_Real": "N/A", "Raridade": "N/A", "Identidade_Cor_Real": "N/A", "Tipo_Oficial": "N/A"
        }, card_image

    # Evita girar a carta cegamente devido a reflexos
    roi_teste_topo = card_image[30:85, 35:460]
    texto_topo = executar_ocr_simples(roi_teste_topo)

    if len(texto_topo) < 3:
        carta_virada = cv2.rotate(card_image, cv2.ROTATE_180)
        roi_teste_baixo = carta_virada[30:85, 35:460]
        texto_baixo = executar_ocr_simples(roi_teste_baixo)
        
        if len(texto_baixo) > len(texto_topo):
            card_image = carta_virada

    layout_detectado = classificar_layout(card_image)
    
    # Coordenadas estáticas (Y1, Y2, X1, X2) ajustadas por layout
    rois_layouts = {
        "PADRAO": {"Nome": (30, 85, 35, 460), "Tipo": (485, 555, 35, 490), "Info": (810, 870, 30, 300)},
        "SAGA": {"Nome": (30, 85, 35, 460), "Tipo": (675, 735, 35, 490), "Info": (810, 870, 30, 300)},
        "FULL_ART": {"Nome": (30, 95, 35, 460), "Tipo": (735, 795, 50, 490), "Info": (810, 870, 30, 300)},
        "TOKEN_FULL": {"Nome": (30, 95, 35, 460), "Tipo": (735, 795, 35, 490), "Info": (810, 870, 30, 300)},
        "TOKEN_CLASSICO": {"Nome": (30, 95, 35, 460), "Tipo": (575, 635, 35, 490), "Info": (810, 870, 30, 300)}
    }

    rc = rois_layouts[layout_detectado]
    img_nome = card_image[rc["Nome"][0]:rc["Nome"][1], rc["Nome"][2]:rc["Nome"][3]]
    img_tipo = card_image[rc["Tipo"][0]:rc["Tipo"][1], rc["Tipo"][2]:rc["Tipo"][3]]
    img_info = card_image[rc["Info"][0]:rc["Info"][1], rc["Info"][2]:rc["Info"][3]]

    nome_ocr, tipo_ocr, info_ocr = extrair_textos_batch(img_nome, img_tipo, img_info)

    # Anotações visuais para debug do relatório
    annotated_image = card_image.copy()
    cv2.putText(annotated_image, f"LAYOUT: {layout_detectado}", (430, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)
    cv2.rectangle(annotated_image, (rc["Nome"][2], rc["Nome"][0]), (rc["Nome"][3], rc["Nome"][1]), (0, 255, 0), 2)
    cv2.rectangle(annotated_image, (rc["Tipo"][2], rc["Tipo"][0]), (rc["Tipo"][3], rc["Tipo"][1]), (0, 255, 255), 2)
    cv2.rectangle(annotated_image, (rc["Info"][2], rc["Info"][0]), (rc["Info"][3], rc["Info"][1]), (255, 0, 255), 2)

    card_data = {"Nome": nome_ocr, "Tipo": tipo_ocr}
    
    # Tratamento via Regex para encontrar o número do colecionador
    info_raw = info_ocr.upper()
    match_numero = re.search(r'(\d+)(?:/\d+)?', info_raw)
    card_data["Numero_Colecionador"] = match_numero.group(1) if match_numero else "Desconhecido"

    # Isolamento do código da coleção
    palavras_rodape = re.split(r'[^A-Z0-9]', info_raw)
    idiomas_oficiais = ["ENG", "EN", "PT", "ES", "FR", "DE", "IT", "JP", "KO", "RU", "ZHT", "ZHS"]
    melhor_set = "N/A"
    
    # Tenta um "Match" exato na Whitelist
    for p in palavras_rodape:
        if 3 <= len(p) <= 6 and not p.isdigit() and p not in idiomas_oficiais:
            if p in SETS_OFICIAIS:
                melhor_set = p
                break 

    # Se falhar, tenta um "Match" Fuzzy tolerante a falhas (ex: o OCR leu "FIN" como "F1N")
    if melhor_set == "N/A":
        maior_pontuacao = 0
        for p in palavras_rodape:
            if 3 <= len(p) <= 6 and not p.isdigit() and p not in idiomas_oficiais:
                melhor_match, pontuacao = process.extractOne(p, SETS_OFICIAIS)
                if pontuacao >= 85 and pontuacao > maior_pontuacao:
                    maior_pontuacao = pontuacao
                    melhor_set = melhor_match 

    card_data["Codigo_Edicao_Texto"] = melhor_set

    # Injeta a Verdade Absoluta do Scryfall
    dados_api = consultar_dados_oficiais(nome_ocr, tipo_ocr, melhor_set, card_data["Numero_Colecionador"], layout_detectado)
    card_data.update(dados_api)

    return card_data, annotated_image


# GERAÇÃO DE RELATÓRIO
def gerar_relatorio(caminho_imagem, dados, imagem_anotada):
    """
    Gera as saídas finais na pasta 'relatorios': uma imagem JPG com as caixas delimitadoras
    desenhadas e um arquivo TXT de texto puro contendo a estrutura dos dados lidos.
    """
    pasta_saida = "relatorios"
    if not os.path.exists(pasta_saida): os.makedirs(pasta_saida)
        
    nome_base = os.path.splitext(os.path.basename(caminho_imagem))[0]
    caminho_imagem_saida = os.path.join(pasta_saida, f"{nome_base}_processada.jpg")
    cv2.imwrite(caminho_imagem_saida, imagem_anotada)
    
    caminho_txt = os.path.join(pasta_saida, f"{nome_base}_dados.txt")
    with open(caminho_txt, "w", encoding="utf-8") as f:
        f.write("=======================================\n")
        f.write(f"RELATÓRIO DE EXTRAÇÃO - OCR + SCRYFALL LOCAL\n")
        f.write(f"Arquivo Original: {os.path.basename(caminho_imagem)}\n")
        f.write("=======================================\n\n")
        f.write("--- DADOS LIDOS PELA CÂMARA (OCR) ---\n")
        f.write(f"Nome Lido: {dados.get('Nome', 'N/A')}\n")
        f.write(f"Tipo Lido: {dados.get('Tipo', 'N/A')}\n")
        f.write(f"Set Detectado: {dados.get('Codigo_Edicao_Texto', 'N/A')}\n")
        f.write(f"Número Extraído: {dados.get('Numero_Colecionador', 'N/A')}\n\n")
        
        f.write("--- VERDADE ABSOLUTA (SCRYFALL) ---\n")
        f.write(f"Nome Oficial: {dados.get('Nome_Oficial', 'N/A')}\n")
        f.write(f"Custo de Mana: {dados.get('Custo_Mana_Real', 'N/A')}\n")
        f.write(f"Identidade de Cor: {dados.get('Identidade_Cor_Real', 'N/A')}\n")
        f.write(f"Tipo da Carta: {dados.get('Tipo_Oficial', 'N/A')}\n")
        f.write(f"Raridade: {dados.get('Raridade', 'N/A').capitalize()}\n")
        
        f.write("\n=======================================\n")
        f.write(f"Imagem anotada salva em: {caminho_imagem_saida}")

if __name__ == "__main__":
    pasta_entrada = "input_cartas"
    if not os.path.exists(pasta_entrada):
        os.makedirs(pasta_entrada)
        print(f"Pasta '{pasta_entrada}' criada. Coloque as imagens lá.")
        sys.exit(0)

    arquivos = [arq for arq in os.listdir(pasta_entrada) if arq.lower().endswith(('.png', '.jpg', '.jpeg'))]
    total = len(arquivos)
    if total == 0:
        print(f"Não há imagens a serem processadas. Coloque as imagens na pasta de entrada.\n") 
        sys.exit(0)

    print(f"Iniciando processamento Otimizado de {total} cartas...\n")
    
    sucessos, erros = [], []
    tempo_inicio = time.time()

    for arq in arquivos:
        t_carta = time.time()
        caminho = os.path.join(pasta_entrada, arq)
        print(f"Processando: {arq}...")
        
        try:
            carta, recortou = isolate_and_standardize_card(caminho)
            dados, img = extract_and_annotate_data(carta, recortou)
            gerar_relatorio(caminho, dados, img)
            
            t_dec = time.time() - t_carta
            if recortou:
                sucessos.append(arq)
                print(f"  -> Sucesso! ({t_dec:.2f}s)")
            else:
                erros.append(arq)
                print(f"  -> Falha no recorte. ({t_dec:.2f}s)")
        except Exception as e:
            t_dec = time.time() - t_carta
            erros.append(arq)
            print(f"  -> Erro: {e} ({t_dec:.2f}s)")
            
    t_total = time.time() - tempo_inicio
    t_medio = t_total / total if total > 0 else 0
    p_erro = (len(erros) / total) * 100

    print("\n=======================================")
    print(f"PROCESSAMENTO CONCLUÍDO!")
    print(f"Sucessos: {len(sucessos)} ({100-p_erro:.1f}%) | Erros: {len(erros)} ({p_erro:.1f}%)")
    print(f"Tempo Total: {t_total:.2f}s | Tempo Médio: {t_medio:.2f}s")
    print("=======================================")