"""O pool de consultas.

Estrategia em duas fases, desenhada em cima do que cada API entrega de fato:

Fase 1 - dados cadastrais (CNAE principal + secundarios, situacao, telefone).
    Todos os provedores servem. BrasilAPI e Minha Receita aguentam vazao alta,
    entao a fase 1 e rapida. Mas quando um provedor com e-mail tem token
    sobrando, ele e preferido: a requisicao ia acontecer de um jeito ou de
    outro, e assim o e-mail vem de graca e a fila da fase 2 encurta. Isso nao
    atrasa a fase 1, porque so escolhemos um provedor que esta livre agora.

Fase 2 - e-mail das empresas que interessam (por padrao, so as Ativas).
    Somente os provedores que retornam e-mail, a 3-5 req/min cada.

Interromper com Ctrl+C e seguro: o progresso esta no SQLite, e retomar nao
gasta consulta repetida.
"""

import signal
import time

import requests

from .config import Config
from .modelo import Dados
from .providers import REGISTRO, ErroProvedor, LimiteExcedido, NaoEncontrado, Provedor
from .ratelimit import Limitador
from .store import Store

UA = "atualiza-cadastro-altasoft/0.1 (uso interno; contato: ti@altasoft.com.br)"


class Worker:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.parar = False

        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": UA, "Accept": "application/json"})

        self.provedores: list[Provedor] = []
        self.limitadores: dict[str, Limitador] = {}
        for pcfg in cfg.provedores:
            if not pcfg.ativo:
                continue
            classe = REGISTRO.get(pcfg.nome)
            if classe is None:
                print(f"  aviso: provedor desconhecido '{pcfg.nome}', ignorando")
                continue
            self.provedores.append(classe(self.sessao, cfg.timeout))
            self.limitadores[pcfg.nome] = Limitador(
                pcfg.nome, pcfg.rpm, cfg.cooldown_base
            )

        if not self.provedores:
            raise SystemExit("Nenhum provedor ativo.")

        # Prioridade declarada no config, usada como desempate.
        self.prioridade = {p.nome: i for i, p in enumerate(self.provedores)}

        signal.signal(signal.SIGINT, self._interromper)

    def _interromper(self, *_):
        if self.parar:
            raise KeyboardInterrupt
        self.parar = True
        print("\n  interrompendo apos a consulta atual... (Ctrl+C de novo forca)")

    # -- escolha de provedor ----------------------------------------------

    def _ordenar(self, candidatos: list[Provedor], preferir_email: bool):
        """Disponiveis primeiro; entre eles, os com e-mail na frente na fase 1."""
        livres = [p for p in candidatos if self.limitadores[p.nome].disponivel()]
        if preferir_email:
            livres.sort(key=lambda p: (not p.fornece_email, self.prioridade[p.nome]))
        else:
            livres.sort(key=lambda p: self.prioridade[p.nome])
        return livres

    def _espera_minima(self, candidatos: list[Provedor]) -> float:
        return min(self.limitadores[p.nome].espera() for p in candidatos)

    def _tentar(self, cnpj: str, ordenados: list[Provedor]):
        """Percorre provedores livres ate um responder.

        Devolve (provedor, Dados) no sucesso, ou (None, (erro, n_nao_encontrado)).
        """
        ultimo_erro = None
        nao_encontrado = 0
        for prov in ordenados:
            lim = self.limitadores[prov.nome]
            if not lim.disponivel():
                continue
            lim.consumir()
            try:
                dados = prov.consultar(cnpj)
                lim.premiar()
                return prov, dados
            except LimiteExcedido as e:
                espera = lim.penalizar(e.retry_after)
                print(f"  [{prov.nome}] limite excedido, pausando {espera:.0f}s")
                ultimo_erro = str(e)
            except NaoEncontrado as e:
                # Nao e culpa do provedor: nao penaliza a vazao dele.
                lim.premiar()
                nao_encontrado += 1
                ultimo_erro = str(e)
            except ErroProvedor as e:
                lim.penalizar()
                print(f"  [{prov.nome}] falhou: {e}")
                ultimo_erro = str(e)
        return None, (ultimo_erro, nao_encontrado)

    # -- fases -------------------------------------------------------------

    def rodar_fase1(self) -> None:
        print("\n=== Fase 1: dados cadastrais (CNAE, situacao, telefone) ===")
        feitos = 0
        inicio = time.monotonic()
        while not self.parar:
            fila = self.store.pendentes(self.cfg.max_tentativas)
            if not fila:
                break
            for cnpj in fila:
                if self.parar:
                    break
                ordenados = self._ordenar(self.provedores, preferir_email=True)
                if not ordenados:
                    self._dormir(self._espera_minima(self.provedores))
                    ordenados = self._ordenar(self.provedores, preferir_email=True)
                    if not ordenados:
                        continue

                prov, resultado = self._tentar(cnpj, ordenados)
                if prov is not None:
                    dados: Dados = resultado
                    self.store.salvar(cnpj, dados, prov.nome)
                    feitos += 1
                    self._progresso(cnpj, dados, prov.nome, feitos, inicio)
                else:
                    erro, n_nao_encontrado = resultado
                    # Dois provedores independentes negando: e ausencia real
                    # na base da Receita, nao instabilidade.
                    definitivo = n_nao_encontrado >= 2
                    self.store.registrar_falha(cnpj, erro or "sem provedor",
                                               definitivo)
        print(f"\n  fase 1: {feitos} consultas concluidas nesta execucao")

    def rodar_fase2(self) -> None:
        com_email = [p for p in self.provedores if p.fornece_email]
        if not com_email:
            print("\n  fase 2 ignorada: nenhum provedor com e-mail ativo")
            return
        situacoes = self.cfg.email_somente_situacoes
        print(f"\n=== Fase 2: e-mail (situacao em {situacoes}) ===")
        nomes = ", ".join(f"{p.nome}@{self.limitadores[p.nome].rpm}/min"
                          for p in com_email)
        print(f"  provedores: {nomes}")

        feitos = 0
        inicio = time.monotonic()
        while not self.parar:
            fila = self.store.pendentes_email(self.cfg.max_tentativas, situacoes)
            if not fila:
                break
            for cnpj in fila:
                if self.parar:
                    break
                ordenados = self._ordenar(com_email, preferir_email=False)
                if not ordenados:
                    self._dormir(self._espera_minima(com_email))
                    continue

                prov, resultado = self._tentar(cnpj, ordenados)
                if prov is not None:
                    dados: Dados = resultado
                    # Sem e-mail aqui significa que a Receita nao tem e-mail
                    # cadastrado: marca como 'vazio' para nao reconsultar.
                    self.store.salvar_email(cnpj, dados.email, prov.nome)
                    feitos += 1
                    marca = dados.email or "(sem e-mail na Receita)"
                    print(f"  [{feitos}] {cnpj} {marca}  <{prov.nome}>")
                else:
                    erro, n_nao_encontrado = resultado
                    self.store.registrar_falha(
                        cnpj, erro or "sem provedor",
                        definitivo=n_nao_encontrado >= 2, campo="email",
                    )
        print(f"\n  fase 2: {feitos} consultas de e-mail nesta execucao")

    # -- utilitarios -------------------------------------------------------

    def _dormir(self, segundos: float) -> None:
        """Espera respeitando Ctrl+C, em fatias de 1s."""
        if segundos <= 0:
            return
        fim = time.monotonic() + segundos
        if segundos > 5:
            print(f"  todos os provedores em espera; aguardando {segundos:.0f}s")
        while time.monotonic() < fim and not self.parar:
            time.sleep(min(1.0, fim - time.monotonic()))

    def _progresso(self, cnpj, dados: Dados, fonte, feitos, inicio) -> None:
        sec = len(dados.cnaes_secundarios)
        linha = (
            f"  [{feitos}] {cnpj} {dados.situacao_cadastral or '?':9s}"
            f" cnae={dados.cnae_principal_codigo or '-':11s} sec={sec:<2d}"
            f" tel={'S' if dados.telefone_1 else '-'}"
            f" mail={'S' if dados.email else '-'}  <{fonte}>"
        )
        print(linha)
        if feitos % 50 == 0:
            decorrido = time.monotonic() - inicio
            taxa = feitos / decorrido * 60 if decorrido else 0
            restam = len(self.store.pendentes(self.cfg.max_tentativas, limite=100000))
            eta = restam / taxa if taxa else 0
            print(f"  --- {taxa:.1f} consultas/min | restam {restam}"
                  f" | ~{eta:.0f} min ---")

    def rodar(self) -> None:
        self.rodar_fase1()
        if self.cfg.buscar_email and not self.parar:
            self.rodar_fase2()
