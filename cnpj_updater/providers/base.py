"""Contrato comum dos provedores."""

import requests

from ..modelo import Dados


class ErroProvedor(Exception):
    """Falha recuperavel: vale tentar outro provedor ou repetir depois."""


class LimiteExcedido(ErroProvedor):
    """429. `retry_after` em segundos, quando o servidor informa."""

    def __init__(self, mensagem="limite excedido", retry_after: float | None = None):
        super().__init__(mensagem)
        self.retry_after = retry_after


class NaoEncontrado(ErroProvedor):
    """CNPJ inexistente na base da Receita. Nao adianta tentar outro provedor."""


class Provedor:
    nome = "base"
    # Se este provedor entrega e-mail. BrasilAPI e MinhaReceita retornam
    # sempre null neste campo (verificado), por isso ficam de fora da fase 2.
    fornece_email = False

    def __init__(self, sessao: requests.Session, timeout: float = 20.0):
        self.sessao = sessao
        self.timeout = timeout

    def url(self, cnpj: str) -> str:
        raise NotImplementedError

    def analisar(self, payload: dict) -> Dados:
        raise NotImplementedError

    def consultar(self, cnpj: str) -> Dados:
        try:
            resp = self.sessao.get(self.url(cnpj), timeout=self.timeout)
        except requests.Timeout as e:
            raise ErroProvedor(f"timeout: {e}") from e
        except requests.RequestException as e:
            raise ErroProvedor(f"rede: {e}") from e

        if resp.status_code == 429:
            cab = resp.headers.get("Retry-After")
            espera = None
            if cab:
                try:
                    espera = float(cab)
                except ValueError:
                    pass
            raise LimiteExcedido(retry_after=espera)
        if resp.status_code == 404:
            raise NaoEncontrado(f"{self.nome}: CNPJ nao encontrado")
        if resp.status_code >= 500:
            raise ErroProvedor(f"{self.nome}: HTTP {resp.status_code}")
        if resp.status_code != 200:
            raise ErroProvedor(f"{self.nome}: HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as e:
            raise ErroProvedor(f"{self.nome}: resposta nao-JSON") from e

        return self.analisar(payload)
