"""Limitador de vazao por provedor: token bucket + cooldown apos 429."""

import time


class Limitador:
    """Token bucket simples.

    Cada provedor tem o seu. `rpm` e a vazao permitida por minuto; o balde
    comeca cheio para nao penalizar o primeiro lote, e recarrega
    continuamente (nao em janelas fixas), o que evita a rajada de 429 que
    acontece quando varios provedores viram a janela ao mesmo tempo.

    Um 429 coloca o provedor em cooldown, dobrando a espera a cada falha
    consecutiva ate o teto. Um sucesso zera a escalada.
    """

    def __init__(self, nome: str, rpm: int, cooldown_base: float = 60.0,
                 cooldown_max: float = 900.0):
        self.nome = nome
        self.rpm = max(1, rpm)
        self.capacidade = float(self.rpm)
        self.tokens = float(self.rpm)
        self.taxa = self.rpm / 60.0  # tokens por segundo
        self.cooldown_base = cooldown_base
        self.cooldown_max = cooldown_max
        self.falhas_seguidas = 0
        self.bloqueado_ate = 0.0
        self._ultimo = time.monotonic()

    def _recarregar(self) -> None:
        agora = time.monotonic()
        decorrido = agora - self._ultimo
        self._ultimo = agora
        self.tokens = min(self.capacidade, self.tokens + decorrido * self.taxa)

    def espera(self) -> float:
        """Segundos até este provedor poder ser usado. 0.0 = livre agora."""
        self._recarregar()
        agora = time.monotonic()
        if agora < self.bloqueado_ate:
            return self.bloqueado_ate - agora
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.taxa

    def disponivel(self) -> bool:
        return self.espera() <= 0.0

    def consumir(self) -> None:
        """Gasta um token. Chame imediatamente antes da requisicao."""
        self._recarregar()
        self.tokens = max(0.0, self.tokens - 1.0)

    def penalizar(self, retry_after: float | None = None) -> None:
        """Registra 429/erro e poe o provedor de molho."""
        self.falhas_seguidas += 1
        if retry_after and retry_after > 0:
            espera = retry_after
        else:
            espera = min(
                self.cooldown_max,
                self.cooldown_base * (2 ** (self.falhas_seguidas - 1)),
            )
        self.bloqueado_ate = time.monotonic() + espera
        return espera

    def premiar(self) -> None:
        """Sucesso: encerra a escalada de cooldown."""
        self.falhas_seguidas = 0
