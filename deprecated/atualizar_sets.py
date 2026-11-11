import requests
import json
import os

def baixar_sets_scryfall():
    """
    Conecta-se à API pública da Scryfall para buscar a lista completa de coleções (sets) 
    de Magic: The Gathering e extrai os seus respectivos códigos (ex: 'FIN', 'MH3').
    
    O resultado é filtrado para evitar duplicatas, ordenado alfabeticamente e salvo 
    localmente no arquivo 'sets_oficiais.json' para ser utilizado como Whitelist de 
    validação pelo scanner OCR.
    """
    print("A estabelecer ligação com a API do Scryfall...")
    
    url = "https://api.scryfall.com/sets"

    headers = {
        "User-Agent": "MTGScanner/1.0",
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        
        response.raise_for_status() 
        
        dados = response.json()
        
        codigos_edicoes = []

        for colecao in dados.get('data', []):
            codigo = colecao.get('code', '').upper()
            
            if codigo:
                codigos_edicoes.append(codigo)
                
        codigos_edicoes = sorted(list(set(codigos_edicoes)))
        
        caminho_arquivo = "sets_oficiais.json"
        
        with open(caminho_arquivo, "w", encoding="utf-8") as f:
            json.dump(codigos_edicoes, f, indent=4)
            
        print(f"Sucesso! {len(codigos_edicoes)} códigos de edição foram gravados em '{caminho_arquivo}'.")
        
    except requests.exceptions.RequestException as e:
        print(f"Erro ao descarregar dados do Scryfall: {e}")

if __name__ == "__main__":
    baixar_sets_scryfall()