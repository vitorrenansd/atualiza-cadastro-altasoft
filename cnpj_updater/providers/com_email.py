"""Provedores que entregam e-mail: CNPJa Open, CNPJ.ws Publica e ReceitaWS.

Limites baixos (3-5/min), por isso sao usados na fase 2 e como reforco da
fase 1 quando ha token sobrando.
"""

from ..cnpj import formatar_cnae
from ..modelo import Dados, juntar_telefone, normalizar_situacao, separar_telefone
from .base import ErroProvedor, LimiteExcedido, Provedor


class CNPJa(Provedor):
    """open.cnpja.com — telefones e e-mails vem como listas."""

    nome = "cnpja"
    fornece_email = True

    def url(self, cnpj: str) -> str:
        return f"https://open.cnpja.com/office/{cnpj}"

    def analisar(self, payload: dict) -> Dados:
        telefones = [
            juntar_telefone(t.get("area"), t.get("number"))
            for t in payload.get("phones") or []
        ]
        telefones = [t for t in telefones if t]
        emails = [
            e.get("address") for e in payload.get("emails") or [] if e.get("address")
        ]
        principal = (payload.get("mainActivity") or {})
        secundarios = [
            (formatar_cnae(a.get("id")), a.get("text") or "")
            for a in payload.get("sideActivities") or []
            if a.get("id")
        ]
        empresa = payload.get("company") or {}
        return Dados(
            razao_social=empresa.get("name") or "",
            nome_fantasia=payload.get("alias") or "",
            situacao_cadastral=normalizar_situacao(
                (payload.get("status") or {}).get("text")
            ),
            data_situacao=payload.get("statusDate") or "",
            telefone_1=telefones[0] if telefones else "",
            telefone_2=telefones[1] if len(telefones) > 1 else "",
            email=emails[0] if emails else "",
            cnae_principal_codigo=formatar_cnae(principal.get("id")),
            cnae_principal_descricao=principal.get("text") or "",
            cnaes_secundarios=secundarios,
        )


class CNPJws(Provedor):
    """publica.cnpj.ws — dados do estabelecimento vem aninhados."""

    nome = "cnpjws"
    fornece_email = True

    def url(self, cnpj: str) -> str:
        return f"https://publica.cnpj.ws/cnpj/{cnpj}"

    def analisar(self, payload: dict) -> Dados:
        est = payload.get("estabelecimento") or {}
        principal = est.get("atividade_principal") or {}
        secundarios = [
            (formatar_cnae(a.get("id")), a.get("descricao") or "")
            for a in est.get("atividades_secundarias") or []
            if a.get("id")
        ]
        return Dados(
            razao_social=payload.get("razao_social") or "",
            nome_fantasia=est.get("nome_fantasia") or "",
            situacao_cadastral=normalizar_situacao(est.get("situacao_cadastral")),
            data_situacao=est.get("data_situacao_cadastral") or "",
            telefone_1=juntar_telefone(est.get("ddd1"), est.get("telefone1")),
            telefone_2=juntar_telefone(est.get("ddd2"), est.get("telefone2")),
            email=est.get("email") or "",
            cnae_principal_codigo=formatar_cnae(principal.get("id")),
            cnae_principal_descricao=principal.get("descricao") or "",
            cnaes_secundarios=secundarios,
        )


class ReceitaWS(Provedor):
    """receitaws.com.br — sinaliza erro no corpo, com HTTP 200."""

    nome = "receitaws"
    fornece_email = True

    def url(self, cnpj: str) -> str:
        return f"https://receitaws.com.br/v1/cnpj/{cnpj}"

    def analisar(self, payload: dict) -> Dados:
        # Esta API devolve 200 com {"status":"ERROR","message":...} tanto para
        # CNPJ invalido quanto para excesso de consultas; sem tratar isso o
        # worker registraria sucesso com todos os campos vazios.
        if str(payload.get("status", "")).upper() == "ERROR":
            msg = str(payload.get("message") or "erro desconhecido")
            if "excedeu" in msg.lower() or "limite" in msg.lower():
                raise LimiteExcedido(f"receitaws: {msg}")
            raise ErroProvedor(f"receitaws: {msg}")

        atividade = payload.get("atividade_principal") or []
        principal = atividade[0] if atividade else {}
        secundarios = []
        for a in payload.get("atividades_secundarias") or []:
            codigo = a.get("code") or ""
            # Marcador de "sem atividade secundaria".
            if not codigo or codigo.startswith("00.00"):
                continue
            secundarios.append((formatar_cnae(codigo), a.get("text") or ""))

        return Dados(
            razao_social=payload.get("nome") or "",
            nome_fantasia=payload.get("fantasia") or "",
            situacao_cadastral=normalizar_situacao(payload.get("situacao")),
            data_situacao=payload.get("data_situacao") or "",
            telefone_1=separar_telefone(payload.get("telefone")),
            telefone_2="",
            email=payload.get("email") or "",
            cnae_principal_codigo=formatar_cnae(principal.get("code", "")),
            cnae_principal_descricao=principal.get("text") or "",
            cnaes_secundarios=secundarios,
        )
