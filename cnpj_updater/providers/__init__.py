"""Registro de provedores disponiveis."""

from .base import ErroProvedor, LimiteExcedido, NaoEncontrado, Provedor
from .com_email import CNPJa, CNPJws, ReceitaWS
from .receita_dump import BrasilAPI, MinhaReceita

REGISTRO: dict[str, type[Provedor]] = {
    BrasilAPI.nome: BrasilAPI,
    MinhaReceita.nome: MinhaReceita,
    CNPJa.nome: CNPJa,
    CNPJws.nome: CNPJws,
    ReceitaWS.nome: ReceitaWS,
}

__all__ = [
    "REGISTRO", "Provedor", "ErroProvedor", "LimiteExcedido", "NaoEncontrado",
    "BrasilAPI", "MinhaReceita", "CNPJa", "CNPJws", "ReceitaWS",
]
