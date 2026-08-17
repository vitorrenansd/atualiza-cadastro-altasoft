"""Formato unico em que todos os provedores entregam os dados."""

from dataclasses import dataclass, field

# Situacoes cadastrais canonicas. Cada provedor escreve de um jeito
# ("ATIVA", "Ativa", {"text": "Ativa"}); tudo converge para estes valores.
SITUACOES = {
    "ativa": "Ativa",
    "baixada": "Baixada",
    "suspensa": "Suspensa",
    "inapta": "Inapta",
    "nula": "Nula",
}


def normalizar_situacao(valor) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip()
    return SITUACOES.get(texto.lower(), texto.title())


@dataclass
class Dados:
    """Resultado de uma consulta, ja normalizado."""

    razao_social: str = ""
    nome_fantasia: str = ""
    situacao_cadastral: str = ""
    data_situacao: str = ""
    telefone_1: str = ""
    telefone_2: str = ""
    email: str = ""
    cnae_principal_codigo: str = ""
    cnae_principal_descricao: str = ""
    # Lista de (codigo, descricao).
    cnaes_secundarios: list[tuple[str, str]] = field(default_factory=list)


def juntar_telefone(ddd, numero) -> str:
    """Monta "(11) 3684-5122" a partir de ddd e numero separados."""
    ddd = "".join(c for c in str(ddd or "") if c.isdigit())
    numero = "".join(c for c in str(numero or "") if c.isdigit())
    if not numero:
        return ""
    if len(numero) == 8:
        numero = f"{numero[:4]}-{numero[4:]}"
    elif len(numero) == 9:
        numero = f"{numero[:5]}-{numero[5:]}"
    return f"({ddd}) {numero}" if ddd else numero


def separar_telefone(bruto) -> str:
    """Trata o formato colado do BrasilAPI/MinhaReceita: "1136845122"."""
    digitos = "".join(c for c in str(bruto or "") if c.isdigit())
    if not digitos:
        return ""
    if len(digitos) in (10, 11):
        return juntar_telefone(digitos[:2], digitos[2:])
    return digitos
