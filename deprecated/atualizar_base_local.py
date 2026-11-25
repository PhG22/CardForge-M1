import requests 
import json 
import gzip 
import os 

def descarregar_mini_scryfall():
    """
    Descarrega a base de dados em massa da API da Scryfall, extrai e otimiza 
    os dados estritamente necessários para o funcionamento offline do scanner MTG.
    
    O processo funciona em três etapas principais:
    1. Consulta o catálogo de pacotes da Scryfall para encontrar o URI de download do arquivo de cartas padrão.
    2. Faz o download do arquivo compactado (.jsonl.gz) num fluxo contínuo (em pedaços de 8KB) para evitar sobrecarga na memória.
    3. Lê o arquivo de texto compactado linha por linha (formato JSON Lines), extrai apenas as chaves relevantes (nome, custo, tipo, cores, raridade) e constrói um JSON.
    """

    print("A estabelecer ligação com a Scryfall...")
    
    headers = {
        "User-Agent": "MTGScanner/1.0", 
        "Accept": "application/json"
    }
    
    try:
        # Pede a lista principal de todos os dados disponíveis na plataforma
        url_bulk = "https://api.scryfall.com/bulk-data"
        response = requests.get(url_bulk, headers=headers)
        response.raise_for_status()
        
        download_uri = None
        
        # Percorre a resposta em busca do pacote "default_cards", que engloba todas as cartas de Magic normais
        for pacote in response.json().get("data", []):
            if pacote.get("type") == "default_cards":
                # Captura a URI atualizada para o formato JSONL
                download_uri = pacote.get("jsonl_download_uri")
                break
                
        if not download_uri:
            print("Erro: Link de download 'default_cards' não encontrado.")
            return
        
        caminho_gz = "scryfall_default_cards.jsonl.gz"
        print("A descarregar a base de dados oficial compactada (.gz)...")
        
        # Inicia a transferência do ficheiro utilizando stream=True para não travar a aplicação
        with requests.get(download_uri, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(caminho_gz, 'wb') as f: 
                # Grava no disco rígido em blocos de 8192 bytes (8KB)
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
        # Inicializa as duas estruturas chave-valor para as buscas no script principal
        mini_db = {"prints": {}, "oracle": {}}
        
        # Dicionário fixo para padronizar o idioma da identidade de cores para português
        mapa_cores = {"W": "Branco", "U": "Azul", "B": "Preto", "R": "Vermelho", "G": "Verde"}
        
        print("A extrair e a destilar os dados (com injeção de Raridade Oracle)...")
        
        with gzip.open(caminho_gz, 'rt', encoding='utf-8') as f:
            for linha in f: 
                carta = json.loads(linha) 
                nome_lower = carta.get("name", "Desconhecido").lower() 
                
                # Traduz as letras oficiais (W, U, B, R, G) na estrutura pretendida
                cores_brutas = carta.get("color_identity", []) 
                cores_traduzidas = [mapa_cores.get(c, c) for c in cores_brutas] 
                identidade = ", ".join(cores_traduzidas) if cores_traduzidas else "Incolor" 
                
                # Resgata a raridade atual, definindo "comum" como contingência básica
                raridade = carta.get("rarity", "comum") 
                
                # Gera o bloco consolidado apenas com os nós úteis à apresentação do scanner
                dados_base = {
                    "Nome_Oficial": carta.get("name", "Desconhecido"), 
                    "Custo_Mana_Real": carta.get("mana_cost", ""), 
                    "Tipo_Oficial": carta.get("type_line", ""), 
                    "Identidade_Cor_Real": identidade, 
                    "Raridade": raridade 
                }
                
                # Atualiza ou cria a entrada genérica pela busca de nome (incluindo a injeção da raridade mais recente)
                mini_db["oracle"][nome_lower] = dados_base 
                    
                set_code = carta.get("set", "").lower() 
                col_num = str(carta.get("collector_number", "")).lower() 
                
                # Constroi o ID exato (ex: 'fin-0092') caso a carta possua metadados de impressão física
                if set_code and col_num: 
                    mini_db["prints"][f"{set_code}-{col_num}"] = dados_base.copy() 
            
        # Despeja a versão "destilada" na mesma diretoria do script para consumo instantâneo pelo scanner OCR
        with open("mini_scryfall.json", "w", encoding="utf-8") as f: 
            json.dump(mini_db, f) 
            
        # Apaga a origem gigante compressa (aprox. 100MB+) após concluir o processamento, mantendo o disco limpo
        os.remove(caminho_gz) 
        print("Sucesso! Base local atualizada.") 
        
    except Exception as e: 
        print(f"Erro crítico: {e}") 

if __name__ == "__main__": 
    descarregar_mini_scryfall() 