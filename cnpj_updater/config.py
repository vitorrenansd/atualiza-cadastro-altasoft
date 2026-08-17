"""Leitura do config.toml (tomllib e stdlib a partir do Python 3.11)."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProvedorCfg:
    nome: str
    rpm: int = 3
    ativo: bool = True


@dataclass
class Config:
    entrada: Path = Path("dados/cadastro.xlsx")
    saida: Path = Path("dados/cadastro_atualizado.xlsx")
    aba: str = ""
    linha_cabecalho: int = 1
    coluna_cnpj: str = ""
    aba_somente_ativas: bool = True
    comparar_contato: bool = True
    coluna_email_atual: str = ""
    colunas_telefone_atuais: list[str] = field(default_factory=list)
    banco: Path = Path("dados/consultas.sqlite3")
    max_tentativas: int = 4
    cooldown_base: float = 60.0
    timeout: float = 20.0
    buscar_email: bool = True
    email_somente_situacoes: list[str] = field(default_factory=lambda: ["Ativa"])
    provedores: list[ProvedorCfg] = field(default_factory=list)


def carregar(caminho: str | Path = "config.toml") -> Config:
    caminho = Path(caminho)
    if not caminho.exists():
        raise SystemExit(
            f"Config nao encontrado: {caminho}\n"
            "Copie o config.toml de exemplo do repositorio e ajuste os caminhos."
        )
    with caminho.open("rb") as fh:
        bruto = tomllib.load(fh)

    pl = bruto.get("planilha", {})
    bc = bruto.get("banco", {})
    wk = bruto.get("worker", {})
    base = caminho.parent

    def rel(valor: str) -> Path:
        p = Path(valor)
        return p if p.is_absolute() else base / p

    cfg = Config(
        entrada=rel(pl.get("entrada", "dados/cadastro.xlsx")),
        saida=rel(pl.get("saida", "dados/cadastro_atualizado.xlsx")),
        aba=pl.get("aba", ""),
        linha_cabecalho=int(pl.get("linha_cabecalho", 1)),
        coluna_cnpj=pl.get("coluna_cnpj", ""),
        aba_somente_ativas=bool(pl.get("aba_somente_ativas", True)),
        comparar_contato=bool(pl.get("comparar_contato", True)),
        coluna_email_atual=pl.get("coluna_email_atual", ""),
        colunas_telefone_atuais=list(pl.get("colunas_telefone_atuais", [])),
        banco=rel(bc.get("caminho", "dados/consultas.sqlite3")),
        max_tentativas=int(wk.get("max_tentativas", 4)),
        cooldown_base=float(wk.get("cooldown_base", 60)),
        timeout=float(wk.get("timeout", 20)),
        buscar_email=bool(wk.get("buscar_email", True)),
        email_somente_situacoes=list(wk.get("email_somente_situacoes", ["Ativa"])),
        provedores=[
            ProvedorCfg(
                nome=p["nome"],
                rpm=int(p.get("rpm", 3)),
                ativo=bool(p.get("ativo", True)),
            )
            for p in bruto.get("provedores", [])
        ],
    )
    if not cfg.provedores:
        raise SystemExit("Nenhum provedor configurado em config.toml.")
    return cfg
