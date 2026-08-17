"""Normalizacao e validacao de CNPJ."""

import re

_NAO_DIGITO = re.compile(r"\D")


def normalizar(valor) -> str | None:
    """Devolve o CNPJ com 14 digitos, ou None se nao houver digitos.

    Aceita texto formatado ("33.000.167/0001-01"), numero vindo do Excel
    (33000167000101.0) e valores curtos por perda de zero a esquerda, que
    e o defeito mais comum quando a planilha guarda CNPJ como numero.
    """
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        texto = str(int(valor))
    else:
        texto = str(valor)
    digitos = _NAO_DIGITO.sub("", texto)
    if not digitos:
        return None
    if len(digitos) > 14:
        # Sistemas legados preenchem o campo com zeros a esquerda ate um
        # tamanho fixo (na base de origem desta planilha, 15 posicoes).
        # Descarta o excesso apenas se for tudo zero; digito significativo
        # sobrando e erro de cadastro, nao preenchimento.
        excedente = len(digitos) - 14
        if digitos[:excedente].strip("0"):
            return None
        digitos = digitos[excedente:]
    return digitos.zfill(14)


def _digito(base: str, pesos: list[int]) -> int:
    soma = sum(int(d) * p for d, p in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def valido(cnpj: str) -> bool:
    """Confere os dois digitos verificadores."""
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    return (
        _digito(cnpj[:12], p1) == int(cnpj[12])
        and _digito(cnpj[:13], p2) == int(cnpj[13])
    )


def formatar(cnpj: str) -> str:
    """00000000000000 -> 00.000.000/0000-00"""
    if len(cnpj) != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def formatar_cnae(codigo) -> str:
    """Codigo de subclasse CNAE no formato oficial: 600001 -> 0600-0/01.

    Faz o zero-fill internamente: as APIs entregam o codigo como inteiro
    (600001) ou string ("0600001"), e deixar o preenchimento para quem chama
    e' convite a bug silencioso.
    """
    if codigo is None:
        return ""
    digitos = _NAO_DIGITO.sub("", str(codigo))
    if not digitos or digitos.strip("0") == "":
        return ""
    if len(digitos) < 7:
        digitos = digitos.zfill(7)
    if len(digitos) != 7:
        return str(codigo)
    return f"{digitos[:4]}-{digitos[4]}/{digitos[5:]}"
