import os
from pedalboard import Pedalboard, Reverb
from pedalboard.io import AudioFile

PASTA_ORIGEM  = r"C:\Users\l4nce\OneDrive\Documentos\Python\rpg_ecs\assets\sounds\sfx"
PASTA_DESTINO = r"C:\Users\l4nce\OneDrive\Documentos\Python\rpg_ecs\assets\sounds\sfx\cave"

# Liste aqui os arquivos que deseja processar (apenas o nome, sem o caminho).
# Deixe a lista vazia para processar todos os .ogg da pasta.
ARQUIVOS = [
    "mob_morto-vivo_death.ogg",
    "mob_morto_vivo_get_crit_1.ogg",
    "mob_morto-vivo_aggro.ogg",
    "mob_morto-vivo_emote_attack_1.ogg",
    "mob_morto-vivo_emote_attack_2.ogg",
    "mob_morto-vivo_emote_attack_3.ogg",
    "mob_morto-vivo_emote_attack_4.ogg"
]


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

            efeito = board(audio, samplerate)

            with AudioFile(caminho_out, "w", samplerate, num_channels) as f:
                f.write(efeito)

            print(f"OK -> {caminho_out}")
            ok += 1

        except Exception as e:
            print(f"ERRO: {e}")

    print(f"\nProcessamento finalizado. {ok}/{len(fila)} arquivo(s) gerado(s).")


gerar_sons_caverna(PASTA_ORIGEM, PASTA_DESTINO, ARQUIVOS)

input("Pressione Enter para sair...")