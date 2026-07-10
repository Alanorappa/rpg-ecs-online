                Talentos — Cavaleiro
         (superscrito = pontos máximos do nó)

     _________        _________       _________
    |        ³|      |        ⁵|     |        ¹|
    |    1    |      |    2    |     |    3    |
    |_________|      |_________|     |_________|
         |                |               |
     ____|____        ____|____       ____|____
    |        ¹|      |        ⁵|     |        ⁵|
    |    4    |      |    5    |     |    6    |
    |_________|      |_________|     |_________|
         |                |
     ____|____        ____|____       _________
    |        ¹|      |        ⁵|     |        ¹|
    |    7    |      |    8    |_____|    9    |
    |_________|      |_________|     |_________|
         |                                |
     ____|____        _________       ____|____
    |        ¹|      |        ³|     |        ¹|
    |   10    |______|   11    |     |   12    |
    |_________|      |_________|     |_________|
                          |               |
                      ____|____       ____|____
                     |        ¹|     |        ¹|
                     |   13    |     |   14    |
                     |_________|     |_________|
                          |
                      ____|____
                     |        ¹|
                     |   15    |
                     |_________|


── Descrições ──────────────────────────────────────────────────────────────

 1) Reflexos Apurados      [col 0, row 0 | max 3 pts]
    +1% de chance de aparar um golpe por ponto.
    Requer: —

 2) Veterano               [col 1, row 0 | max 5 pts]
    Reduz 1 de custo de Raiva do Golpe Poderoso por ponto (mínimo 10).
    Requer: —

 3) Vontade                [col 2, row 0 | max 1 pt]
    Interceptar gera +10 de Raiva ao atingir o alvo.
    Requer: —

 4) Máquina de Matar       [col 0, row 1 | max 1 pt]
    Impacto causa +15% de dano adicional por inimigo presente no raio de 3 tiles.
    Requer: Reflexos Apurados (1)

 5) Embalo                 [col 1, row 1 | max 5 pts]
    +10% de dano no Golpe Poderoso por ponto quando usado após um crítico.
    Cada crítico concede 1 carga de Embalo (não acumula).
    Requer: Veterano (1)

 6) Sede de Batalha        [col 2, row 1 | max 5 pts]
    Reduz 2s do cooldown de Interceptar por ponto (base 22s, mínimo 12s).
    Requer: Vontade (1)

 7) Assassino              [col 0, row 2 | max 1 pt]
    Impacto tem 5% de chance por alvo golpeado de ativar uma carga de Executar,
    mesmo que nenhum alvo esteja abaixo de 30% de HP.
    Requer: Máquina de Matar (1)

 8) Alvo Confirmado        [col 1, row 2 | max 5 pts]
    Interceptar atordoa o alvo por +0.3s por ponto (ex.: 5 pts = 1.5s de stun).
    Requer: Embalo (1)

 9) Horrorizante           [col 2, row 2 | max 1 pt]
    Executar que não finaliza o oponente faz o alvo fugir de medo por 1s.
    Requer: Alvo Confirmado (1)

10) Golpe Debilitante      [col 0, row 3 | max 1 pt — DESBLOQUEIA HABILIDADE]
    Habilidade: golpe certeiro causando 50% do dano de ataque + dano da arma.
    Reduz a velocidade do alvo em 50% por 5s. Custo: 5 Raiva.
    Requer: Assassino (1)

11) Explorador de Fraquezas [col 1, row 3 | max 3 pts]
    +15% de chance de crítico por ponto contra alvos com slow ativo
    (ex.: 3 pts = +45% crit ao atacar um alvo debilitado pelo Golpe Debilitante).
    Requer: Golpe Debilitante (1)

12) Brado Provocativo      [col 2, row 3 | max 1 pt — DESBLOQUEIA HABILIDADE]
    Habilidade: provoca todos os inimigos em raio de 3 tiles, enlouquecendo-os
    por 10s. Enlouquecidos causam +5% de dano mas recebem +10% de dano.
    Cooldown: 45s.
    Requer: Horrorizante (1)

13) Foco Mortal            [col 1, row 4 | max 1 pt]
    +8% de dano por segundo contínuo que o alvo permanece debilitado (slow ativo),
    acumulando até +40% (máximo em 5s). O acúmulo reseta quando o slow expira.
    Requer: Explorador de Fraquezas (1)

14) Punho no Queixo        [col 2, row 4 | max 1 pt — DESBLOQUEIA HABILIDADE]
    Habilidade: após 3 golpes bem-sucedidos, o cavaleiro acumula 1 carga.
    Ao usar, desfere um soco causando 45% do poder de ataque e atordoa o alvo
    por 3s. Sem cooldown fixo — dependente de cargas.
    Requer: Brado Provocativo (1)

15) Fatiador de Corpos     [col 1, row 5 | max 1 pt — DESBLOQUEIA HABILIDADE]
    Habilidade: o cavaleiro gira desferindo golpes a todos ao redor, causando
    45% do dano de ataque + dano da arma a cada 1s durante 5s (AoE raio 2 tiles).
    Cooldown: 45s. Bloqueia outras habilidades durante o channel.
    Requer: Foco Mortal (1)


── Habilidades desbloqueadas por talentos ──────────────────────────────────

  Slot 6 — Golpe Debilitante  (talento 10)
  Slot 7 — Brado Provocativo  (talento 12)  ← era "Provocação", renomeado
  Slot 8 — Punho no Queixo    (talento 14)
  Slot 7 — Fatiador de Corpos (talento 15)  ← empurra Brado para slot anterior

  Nota: a ordem dos slots depende da ordem de alocação dos talentos.


── Caminhos principais ─────────────────────────────────────────────────────

  Controle:  1→4→7→10→11→13→15   (Golpe Debilitante + Foco Mortal + Fatiador)
  Mobilidade: 3→6 + 2→5→8        (Sede de Batalha + Alvo Confirmado)
  Executar:  4→7 + 2→5→8→9→12→14 (Assassino + Horrorizante + Punho no Queixo)



                Talentos — Piromania
         (superscrito = pontos máximos do nó)

                      _________       
                     |        ⁵|        
                     |    1    |      
                     |_________|      
                          |                
     _________        ____|____       _________
    |        ⁵|      |        ⁵|     |        ⁵|
    |    2    |______|    3    |_____|    4    |
    |_________|      |_________|     |_________|
         |                |               |
     ____|____        ____|____       ____|____
    |        ¹|      |        ⁵|     |        ¹|
    |    5    |      |    6    |     |    7    |
    |_________|      |_________|     |_________|
                          |                
     _________        ____|____       _________
    |        ³|      |        ³|     |        ¹|
    |    8    |______|    9    |_____|   10    |
    |_________|      |_________|     |_________|
         |                |               |     
     ____|_____       ____|____       ____|____ 
    |         ¹|     |        ¹|     |        ¹|
    |    11    |     |   12    |     |   13    |
    |__________|     |_________|     |_________|
                          |
                      ____|____
                     |        ¹|
                     |   14    |
                     |_________|


── Descrições ──────────────────────────────────────────────────────────────

1) Frieza - Reduz o custo de mana de sua bola de fogo em 3.

2) Bola de fogo aperfeiçoada - Reduz o tempo de lançamento de bola de fogo em 0.1 segundos.

3) Queimaduras profundas - Acertos críticos de Bola de fogo fazem o alvo arder em chamas por 3 segundos.

4) Precisão elemental - Reduz o tempo de lançamento de sua nova congelante em 0.2 segundos.

5) Escudo de fogo - Desbloqueia a habilidade Escudo de Fogo, Um escudo que envolve seu corpo fazendo com que quando um oponente te atacar recebe 10 de dano mais 20% do spell power do mago.

6) Chama interna - Suas habilidades da escola de magia de fogo tem 2% de chance de fazer com que sua Bola de fogo possa ser lançada instantâneamente e não tenha custo de mana.

7) Choque térmico - Enquanto o alvo está sob efeito da nova congelante, habilidades da escola de magia de fogo causam 100% mais dano.

8) Piromaníaco - Suas skills da escola de magia de fogo custam 5% menos mana e causam 5% mais dano.

9) Lapso Elemental - Por um instante você esquece sua identidade e se torna totalmente instável, aumentando 5% a chance de crítico por 3 segundos, e seu corpo arde em chamas consumindo 1% de sua vida por segundo enquanto estiver sobre o efeito de instabilidade.

10) Exaustão - Suas bolas de fogo consecutivas fazem o alvo ficar exausto, reduzindo a velocidade de movimento em 5% cada acerto subsequente, acumulando até 5.

11) Calamidade Flamejante - Desbloqueia a skill Calamidade Flamejante, seleciona uma área que será alvejada por bolas de fogo caídas do ceu, causando 50 de dano mais 50% do spell power a cada 1 segundo e reduzindo a velocidade de movimento dos alvos em 50% por 5 segundos.

12) Crematória - Aumenta 25% o dano a alvos com menos de 20% de vida.

13) Combustão - Desbloqueia a habilidade Calcinar, Uma habilidade que pode ser lançada em movimento, causa 50 de dano mais 25% do spell power.

14) Pirofagia - Desbloqueia a habilidade Pirofagia, o mago cospe fogo causando 150 de dano mais 150% do spell power e faz os alvos ficarem desorientados por 3 segundos.


                Talentos — Bardo
         (superscrito = pontos máximos do nó)

              
     _________        _________       _________
    |        ⁵|      |        ⁵|     |        ⁵|
    |    1    |______|    2    |_____|    3    |
    |_________|      |_________|     |_________|
         |                |               |
     ____|____        ____|____       ____|____
    |        ¹|      |        ¹|     |        ⁵|
    |    4    |      |    5    |     |    6    |
    |_________|      |_________|     |_________|
                          |                
     _________        ____|____       _________
    |        ³|      |        ¹|     |        ¹|
    |    7    |──────|    8    |_____|    9    |
    |_________|      |_________|     |_________|
         |                |               |     
     ____|____        ____|____       ____|____ 
    |        ¹|      |        ¹|     |        ¹|
    |    10   |      |   11    |     |   12    |
    |_________|      |_________|     |_________|
                          |
     __________       ____|____ 
    |         ¹|     |        ¹|
    |    13    |─────|   14    |
    |__________|     |_________|
                          |
                      ____|____ 
                     |        ³|
                     |   15    |
                     |_________|
                                          
                     
                     
                     

                     

─────────────────────────────────────── Descrições ───────────────────────────────────────

1) Consistência - O Arqueiro é consistente em seu treinamento, aumentando sua taxa de acerto em 2%.

2) Parado e concentrado - O Arqueiro consegue se concentrar melhor parado, portanto restaurará mais 1 de concentração quando estiver parado.

3) Briguento - Brigas de bar aumentaram os reflexos do arqueiro, aumentando sua esquiva em 1%.

4) Só um gole - Desbloqueia a habilidade Só um gole, contra todas as estatísticas, o arqueiro fica mais afiado quando bebe, suas habilidades que consomem concentração ficam grátis, e aumenta a taxa de acerto para 100%.

5) Prático - A prática leva a perfeição, o arqueiro agora consegue recarregar a aljava em movimento.

6) Calmo e certeiro - Enquanto estiver parado, cada segundo dará um bônus de 1% de taxa de acerto.

7) Alvo fácil - Enquanto o alvo estiver sob efeitos que prejudiquem seu movimento, a taxa de acerto contra eles é aumentada em 2% e a chance de causar um dano crítico é aumentada em 5%.

8) Sequência final - Quando o alvo está com menos de 50% de vida, a habilidade Flecha Reiterada dispara uma terceira flecha.

9) Reciclagem - Após abater alvos, há uma chance de reaproveitar uma boa parte das flechas gastas no combate.

10) Canção da Inspiração - Desbloqueia a habilidade Canção da Inspiração, o arqueiro usa sua flauta para tocar uma música épica, inspirando todos em um raio de 5 tiles em 30% o poder de ataque por 20 segundos, cooldown de 360 segundos.

11) Flechas despadronizadas - Algumas flechas fora do padrão são consertadas pelo arqueiro, essas flechas são especiais, e causam 50% a mais de dano. Proc de 15%.

12) Tiro repulsivo - Desbloqueia a habilidade Tiro repulsivo, o arqueiro usa toda sua concentração em um ponto fixo do alvo, o repelindo.

13) Camuflagem - Desbloqueia a habilidade Camuflagem, o arqueiro usa sua capa para criar uma ilusão, enganando os adversários.

14) Na mosca - Quando o arqueiro acerta um dano crítico no alvo, ele ganha confiança, que aumenta o dano da sua próxima flecha em 25%.

15) Tiro múltiplo - Desbloqueia uma habilidade Tiro múltiplo, uma habilidade que faz com que o arqueiro dispare múltiplas flechas podendo acertar até 2 alvos além do alvo selecionado, causando 100% do dano da arma e 300% do poder de ataque.
