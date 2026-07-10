import os
import numpy as np
from pedalboard import Pedalboard, Reverb
from pedalboard.io import AudioFile

PASTA_ORIGEM  = r"C:\dev\rpg_ecs_online\assets\sounds\sfx"
PASTA_DESTINO = r"C:\dev\rpg_ecs_online\assets\sounds\sfx\cave"

# Liste aqui os arquivos que deseja processar (apenas o nome, sem o caminho).
# Deixe a lista vazia para processar todos os .ogg da pasta.
ARQUIVOS = [

]

# Segundos de silêncio adicionados ao FIM do áudio antes do efeito — espaço
# pra cauda do reverb soar. Sem isso, o efeito só processa as amostras do
# arquivo original e a cauda do eco é truncada no fim (o "corte seco").
CAUDA_S = 2.0

# Apara o silêncio sobrando no fim do arquivo processado: mantém tudo até a
# última amostra acima deste nível (~-60 dB) + uma margem de segurança.
LIMIAR_SILENCIO = 0.001
MARGEM_S        = 0.1


# ---------------------------------------------------------------------------

def gerar_sons_caverna(pasta_origem, pasta_destino, arquivos):
    print("Iniciando processamento...")

    board = Pedalboard([
        Reverb(
            room_size=0.75,
            wet_level=0.4,
            dry_level=0.6
        )
    ])

    if not os.path.exists(pasta_destino):
        os.makedirs(pasta_destino)
        print(f"Pasta criada: {pasta_destino}")

    # Se a lista estiver vazia, processa tudo
    if arquivos:
        fila = arquivos
    else:
        fila = [f for f in os.listdir(pasta_origem) if f.lower().endswith(".ogg")]

    print(f"Arquivos a processar: {fila}")

    ok = 0
    for arquivo in fila:
        if not arquivo.lower().endswith(".ogg"):
            print(f"Ignorado (não é .ogg): {arquivo}")
            continue
        try:
            caminho_in = os.path.join(pasta_origem, arquivo)
            if not os.path.isfile(caminho_in):
                print(f"Arquivo não encontrado: {caminho_in}")
                continue

            nome_base   = os.path.splitext(arquivo)[0]
            caminho_out = os.path.join(pasta_destino, f"{nome_base}.ogg")

            print(f"Processando: {arquivo} ...", end=" ")

            with AudioFile(caminho_in, "r") as f:
                audio        = f.read(f.frames)
                samplerate   = f.samplerate
                num_channels = f.num_channels

            # Acolchoa o fim com silêncio — dá espaço pra cauda do reverb
            pad = np.zeros((audio.shape[0], int(CAUDA_S * samplerate)),
                           dtype=audio.dtype)
            audio_padded = np.concatenate([audio, pad], axis=1)

            efeito = board(audio_padded, samplerate)

            # Apara o silêncio excedente: corta após a última amostra audível
            # (+ margem), sem nunca cortar antes do fim do áudio original
            audivel = np.where(np.abs(efeito).max(axis=0) > LIMIAR_SILENCIO)[0]
            fim = (audivel[-1] + 1 + int(MARGEM_S * samplerate)
                   if audivel.size else efeito.shape[1])
            fim = min(efeito.shape[1], max(fim, audio.shape[1]))
            efeito = efeito[:, :fim]

            with AudioFile(caminho_out, "w", samplerate, num_channels) as f:
                f.write(efeito)

            print(f"OK -> {caminho_out}")
            ok += 1

        except Exception as e:
            print(f"ERRO: {e}")

    print(f"\nProcessamento finalizado. {ok}/{len(fila)} arquivo(s) gerado(s).")


gerar_sons_caverna(PASTA_ORIGEM, PASTA_DESTINO, ARQUIVOS)

input("Pressione Enter para sair...")