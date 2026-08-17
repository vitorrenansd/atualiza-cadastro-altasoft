"""Interface de linha de comando.

Fluxo normal:
    python -m cnpj_updater importar   # le a planilha -> fila no banco
    python -m cnpj_updater rodar      # consulta as APIs (pode parar/retomar)
    python -m cnpj_updater exportar   # gera a planilha com as colunas novas
"""

import argparse
import sys

from . import cnpj as cnpj_util
from . import config as config_mod
from . import excel_io
from .store import Store
from .worker import Worker


def _abrir(args):
    cfg = config_mod.carregar(args.config)
    return cfg, Store(cfg.banco)


def cmd_inspecionar(args) -> int:
    """Mostra como o programa esta lendo a planilha, sem gravar nada."""
    cfg = config_mod.carregar(args.config)
    linhas, aba, col = excel_io.ler_linhas(cfg)
    from openpyxl.utils import get_column_letter

    print(f"Arquivo : {cfg.entrada}")
    print(f"Aba     : {aba}")
    print(f"Coluna  : {get_column_letter(col)} (cabecalho na linha "
          f"{cfg.linha_cabecalho})")
    print(f"Linhas  : {len(linhas)} com CNPJ preenchido")

    invalidos = [l for l in linhas if not l.valido]
    unicos = {l.cnpj for l in linhas if l.valido}
    print(f"Validos : {len(linhas) - len(invalidos)} ({len(unicos)} CNPJs distintos)")
    print(f"Invalidos: {len(invalidos)}")

    print("\nPrimeiras 10 linhas lidas:")
    for l in linhas[:10]:
        marca = "ok " if l.valido else "INV"
        print(f"  linha {l.numero:<6d} {marca} {repr(l.bruto):>24s} -> "
              f"{cnpj_util.formatar(l.cnpj) if l.cnpj else '-'}")
    if invalidos:
        print("\nPrimeiros invalidos (confira se e erro de digitacao ou "
              "perda de zero a esquerda):")
        for l in invalidos[:10]:
            print(f"  linha {l.numero:<6d} {repr(l.bruto)}")
    return 0


def cmd_importar(args) -> int:
    cfg, store = _abrir(args)
    linhas, aba, _ = excel_io.ler_linhas(cfg)

    validos = sorted({l.cnpj for l in linhas if l.valido})
    novos = store.inserir_pendentes(validos)
    for l in linhas:
        if not l.valido:
            alvo = l.cnpj or f"linha{l.numero}"
            store.marcar_invalido(alvo, f"CNPJ invalido na linha {l.numero}")

    print(f"Aba '{aba}': {len(linhas)} linhas com CNPJ")
    print(f"  {len(validos)} CNPJs validos distintos")
    print(f"  {novos} novos adicionados a fila (o resto ja estava no banco)")
    invalidos = sum(1 for l in linhas if not l.valido)
    if invalidos:
        print(f"  {invalidos} invalidos marcados; rode 'inspecionar' para ver quais")
    store.fechar()
    return 0


def cmd_rodar(args) -> int:
    cfg, store = _abrir(args)
    if args.sem_email:
        cfg.buscar_email = False
    if args.reprocessar_erros:
        n = store.reabrir_erros()
        print(f"  {n} linhas em erro reabertas para nova tentativa")
    resumo = store.resumo(cfg.email_somente_situacoes, cfg.max_tentativas)
    if resumo["total"] == 0:
        print("Fila vazia. Rode 'importar' primeiro.")
        store.fechar()
        return 1
    print(f"Fila: {resumo['total']} CNPJs no banco")
    try:
        Worker(cfg, store).rodar()
    except KeyboardInterrupt:
        print("\n  interrompido pelo usuario")
    _imprimir_resumo(store.resumo(cfg.email_somente_situacoes,
                                  cfg.max_tentativas))
    store.fechar()
    return 0


def cmd_exportar(args) -> int:
    cfg, store = _abrir(args)
    print(f"Lendo   : {cfg.entrada}")
    contagem = excel_io.exportar(cfg, store)
    print(f"Gerado  : {cfg.saida}")
    print(f"  {contagem['total_linhas']} linhas processadas")
    print(f"  {contagem['preenchidas']} com dados da Receita")
    print(f"  {contagem['ativas']} ATIVAS")
    print(f"  {contagem['sem_dados']} ainda sem dados (worker nao chegou nelas)")
    if contagem["invalidas"]:
        print(f"  {contagem['invalidas']} com CNPJ invalido na planilha")
    if cfg.aba_somente_ativas:
        print("  aba extra 'Somente Ativas' incluida")
    store.fechar()
    return 0


def cmd_status(args) -> int:
    cfg, store = _abrir(args)
    _imprimir_resumo(store.resumo(cfg.email_somente_situacoes,
                                  cfg.max_tentativas))
    store.fechar()
    return 0


def _imprimir_resumo(r: dict) -> None:
    base = r.get("consultaveis", 0) or 1
    print("\n--- Situacao da fila ---")
    for chave in ("pendente", "ok", "erro", "nao_encontrado", "invalido"):
        if chave in r:
            print(f"  {chave:16s} {r[chave]:6d}")
    print(f"  {'-' * 24}")
    print(f"  (percentuais sobre {base} CNPJs validos)")
    print(f"  {'com CNAE':16s} {r['com_cnae']:6d} ({r['com_cnae']/base:.0%})")
    print(f"  {'com telefone':16s} {r['com_telefone']:6d} "
          f"({r['com_telefone']/base:.0%})")
    print(f"  {'com e-mail':16s} {r['com_email']:6d} ({r['com_email']/base:.0%})")
    print(f"  {'ATIVAS':16s} {r['ativas']:6d} ({r['ativas']/base:.0%})")
    if r.get("email_sem_cadastro"):
        print(f"  {'sem e-mail na Receita':16s} {r['email_sem_cadastro']:6d}"
              f" (ja consultado, nao tem)")
    if r.get("email_pendente"):
        # Fila real da fase 2, ja descontando quem nao sera consultado.
        horas = r["email_pendente"] / 11 / 60
        print(f"  fase 2 pendente: {r['email_pendente']} Ativas sem e-mail"
              f" (~{horas:.1f} h a 11/min)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cnpj_updater",
        description="Enriquece a planilha de clientes com CNAE, situacao "
                    "cadastral e contato, consultando APIs publicas de CNPJ.",
    )
    p.add_argument("--config", default="config.toml", help="caminho do config.toml")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("inspecionar", help="mostra como a planilha esta sendo lida")
    sub.add_parser("importar", help="carrega os CNPJs da planilha para a fila")
    r = sub.add_parser("rodar", help="consulta as APIs (interrompivel/retomavel)")
    r.add_argument("--sem-email", action="store_true",
                   help="so a fase 1; nao gasta as APIs lentas com e-mail")
    r.add_argument("--reprocessar-erros", action="store_true",
                   help="zera o contador das linhas que pararam em erro e "
                        "tenta de novo")
    sub.add_parser("exportar", help="grava a planilha de saida")
    sub.add_parser("status", help="resumo do que ja foi coletado")

    args = p.parse_args(argv)
    return {
        "inspecionar": cmd_inspecionar,
        "importar": cmd_importar,
        "rodar": cmd_rodar,
        "exportar": cmd_exportar,
        "status": cmd_status,
    }[args.comando](args)


if __name__ == "__main__":
    sys.exit(main())
